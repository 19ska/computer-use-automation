"""Groq implementation of the LLMProvider protocol.

This is the ONLY module in the codebase that imports groq. Everything
Groq-specific — OpenAI-Chat-Completions-shaped message history, tool
schema wrapping, tool-call parsing, tool_call_id threading, provider
errors — is fully encapsulated here. The engine only ever calls
start()/propose_action()/record_tool_result().

Secret handling: GROQ_API_KEY is read once from the environment in
__init__ and passed straight to groq.Groq(api_key=...). It is never
stored on any attribute other than the client's own internal state, never
logged, never included in an exception message, and never appears in any
prompt/tool-input content this module constructs.

Retry policy: the official groq SDK already performs bounded, internal
retries (its own `max_retries`, default 2) for transient 429/5xx/
connection failures at the HTTP layer before ever raising an exception to
this module. There is no second, hand-written backoff loop here — that
would just be an unbounded-feeling duplicate of behavior the SDK already
guarantees is finite. Whatever still fails after the SDK's own retries
are exhausted is wrapped as the existing LLMProviderError, exactly like
any other error.
"""

from __future__ import annotations

import json
import os

import groq

from ..tools import TOOLS
from .base import LLMActionCall, LLMDecision, LLMProviderError, validate_single_call

_ENV_API_KEY = "GROQ_API_KEY"
_ENV_MAX_RETRIES = "GROQ_MAX_RETRIES"
_DEFAULT_MAX_RETRIES = 2


def _read_non_negative_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def _build_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in TOOLS
    ]


def _tool_call_param(tool_call) -> dict:  # noqa: ANN001 - real groq ChatCompletionMessageToolCall
    """The minimal, explicit shape groq/OpenAI-style APIs accept back as
    an assistant message's `tool_calls` entry. Built field-by-field
    rather than dumping the whole response object, since the response
    message carries extra output-only fields (e.g. `reasoning`,
    `executed_tools`) that are not valid request-side fields.
    """
    return {
        "id": tool_call.id,
        "type": "function",
        "function": {"name": tool_call.function.name, "arguments": tool_call.function.arguments},
    }


class GroqProvider:
    provider_name = "groq"

    def __init__(self, model: str, *, system_prompt: str):
        api_key = os.environ.get(_ENV_API_KEY)
        if not api_key:
            raise LLMProviderError(f"{_ENV_API_KEY} is not set in the environment")

        self.model = model
        self._system_prompt = system_prompt
        max_retries = _read_non_negative_int_env(_ENV_MAX_RETRIES, _DEFAULT_MAX_RETRIES)
        self._client = groq.Groq(api_key=api_key, max_retries=max_retries)
        self._tools = _build_tools()
        self._messages: list[dict] = []
        self._pending_tool_call_id: str | None = None
        self._last_raw_tool_calls: list = []
        self._request_count = 0

    def start(self, *, goal: str, declared_params: dict[str, str], observation_text: str) -> None:
        params_text = ", ".join(f"{k}={v}" for k, v in declared_params.items())
        initial_text = f"Goal: {goal}\nDeclared parameters: {params_text}\n\nCurrent state:\n{observation_text}"
        self._messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": initial_text},
        ]

    def propose_action(self) -> LLMDecision:
        self._request_count += 1
        phase = "initial_decision" if self._request_count == 1 else "tool_result_turn"

        try:
            # "auto" (not "required"): with tool_choice="required", the API
            # itself 400s ("Tool choice is required, but model did not call
            # a tool") whenever the model's natural response to a completed
            # tool result is plain text rather than another tool call — a
            # real, expected shape of response, not a provider failure.
            # Zero-tool-call responses are turned into the ordinary
            # LLMDecisionError below via validate_single_call(), the same
            # provider-neutral path used for every other invalid decision
            # shape. Exactly-one-call is still enforced by us, never by
            # this setting.
            response = self._client.chat.completions.create(
                model=self.model,
                messages=self._messages,
                tools=self._tools,
                tool_choice="auto",
                parallel_tool_calls=False,
            )
        except groq.APIStatusError as exc:
            raise LLMProviderError(
                f"Groq API error (phase={phase}, model={self.model}): "
                f"code={exc.status_code} message={exc.message}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - network/transport errors, malformed responses, etc.
            raise LLMProviderError(
                f"Groq request failed (phase={phase}, model={self.model}): {type(exc).__name__}: {exc}"
            ) from exc

        message = response.choices[0].message
        raw_tool_calls = message.tool_calls or []
        proposed: list[LLMActionCall] = []
        for tc in raw_tool_calls:
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {}
            proposed.append(LLMActionCall(name=tc.function.name, args=args))

        decision = validate_single_call(proposed)

        # Preserve the assistant's own tool-call message so the follow-up
        # turn threads correctly, and remember which raw tool_call_id to
        # respond to (only meaningful when validation succeeded).
        assistant_message: dict = {"role": "assistant", "content": message.content}
        if raw_tool_calls:
            assistant_message["tool_calls"] = [_tool_call_param(tc) for tc in raw_tool_calls]
        self._messages.append(assistant_message)

        # Kept regardless of validity — record_invalid_decision() must
        # respond to every raw tool_call the assistant message declared
        # (OpenAI-style APIs require a matching tool-role response for
        # each one before any further turn), even if none were valid.
        self._last_raw_tool_calls = raw_tool_calls

        if isinstance(decision, LLMActionCall) and raw_tool_calls:
            # There may be more than one raw tool_call even though
            # validate_single_call only accepts responses with exactly
            # one ACTIONABLE (known-name) call; find the one that matched.
            self._pending_tool_call_id = next(
                tc.id for tc in raw_tool_calls if tc.function.name == decision.name
            )
        else:
            self._pending_tool_call_id = None

        return decision

    def record_tool_result(self, *, result_text: str, is_error: bool) -> None:
        if self._pending_tool_call_id is None:
            # No valid pending call to respond to (e.g. validation failed) —
            # nothing Groq-specific to thread; the engine treats this as a
            # terminal condition and won't call propose_action() again.
            return

        content = f"ERROR: {result_text}" if is_error else result_text
        self._messages.append({"role": "tool", "tool_call_id": self._pending_tool_call_id, "content": content})
        self._pending_tool_call_id = None

    def record_invalid_decision(self, message: str) -> None:
        # Every raw tool_call from the rejected assistant message still
        # needs a matching tool-role response — OpenAI-style APIs reject a
        # request where an assistant message's tool_calls aren't each
        # followed by a tool response — even though none of them were
        # valid. A plain text-only response has no such calls, so this is
        # just the corrective user turn.
        for tc in self._last_raw_tool_calls:
            self._messages.append({"role": "tool", "tool_call_id": tc.id, "content": f"ERROR: {message}"})
        self._messages.append({"role": "user", "content": message})
        self._last_raw_tool_calls = []
        self._pending_tool_call_id = None
