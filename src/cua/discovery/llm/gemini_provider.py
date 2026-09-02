"""Gemini implementation of the LLMProvider protocol.

This is the ONLY module in the codebase that imports google.genai.
Everything Gemini-specific — Content/Part construction, function-response
threading, request config, transient-failure retry/backoff — is fully
encapsulated here. The engine only ever calls
start()/propose_action()/record_tool_result() and never sees a retry
happen; a retry either resolves silently inside a single propose_action()
call or the call raises the same LLMProviderError it always could.

Secret handling: GEMINI_API_KEY is read once from the environment in
__init__ and passed straight to genai.Client(api_key=...). It is never
stored on any attribute other than the client's own internal state, never
logged, never included in an exception message, and never appears in any
prompt/tool-input content this module constructs.
"""

from __future__ import annotations

import logging
import os
import time

from google import genai
from google.genai import errors, types

from ..tools import TOOLS
from .base import LLMActionCall, LLMDecision, LLMProviderError, validate_single_call

_ENV_API_KEY = "GEMINI_API_KEY"
_ENV_MAX_TRANSIENT_ATTEMPTS = "GEMINI_MAX_TRANSIENT_ATTEMPTS"
_ENV_RETRY_BASE_SECONDS = "GEMINI_RETRY_BASE_SECONDS"

_DEFAULT_MAX_TRANSIENT_ATTEMPTS = 3
_DEFAULT_RETRY_BASE_SECONDS = 5.0
_RETRY_BACKOFF_FACTOR = 2.0

_TRANSIENT_CODE = 503  # "high demand" / service unavailable — the only auto-retried code
_RATE_LIMITED_CODE = 429  # retried at most once, and only if the API states a retry delay
_MAX_RATE_LIMIT_WAIT_SECONDS = 60.0

_logger = logging.getLogger(__name__)


def _read_positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 1 else default


def _read_positive_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _retry_after_seconds(exc: errors.APIError) -> float | None:
    """Best-effort extraction of a server-provided retry delay from a 429
    response's structured error details (the standard
    `google.rpc.RetryInfo` shape: `{"retryDelay": "35s"}`). Returns None
    if no such delay is present — callers must NOT invent a retry in that
    case, per the "at most one bounded wait, only if the API states a
    duration" requirement for 429s.
    """
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        return None
    error_details = details.get("error", {}).get("details", [])
    if not isinstance(error_details, list):
        return None
    for item in error_details:
        if not isinstance(item, dict):
            continue
        retry_delay = item.get("retryDelay")
        if isinstance(retry_delay, str) and retry_delay.endswith("s"):
            try:
                return min(float(retry_delay[:-1]), _MAX_RATE_LIMIT_WAIT_SECONDS)
            except ValueError:
                continue
    return None


def _build_tool() -> types.Tool:
    declarations = [
        types.FunctionDeclaration(
            name=t["name"], description=t["description"], parameters_json_schema=t["input_schema"]
        )
        for t in TOOLS
    ]
    return types.Tool(function_declarations=declarations)


class GeminiProvider:
    provider_name = "gemini"

    def __init__(self, model: str, *, system_prompt: str):
        api_key = os.environ.get(_ENV_API_KEY)
        if not api_key:
            raise LLMProviderError(f"{_ENV_API_KEY} is not set in the environment")

        self.model = model
        self._system_prompt = system_prompt
        self._client = genai.Client(api_key=api_key)
        self._tool = _build_tool()
        self._contents: list[types.Content] = []
        self._pending_function_call: types.FunctionCall | None = None
        self._last_raw_function_calls: list[types.FunctionCall] = []
        self._request_count = 0
        self._max_transient_attempts = _read_positive_int_env(
            _ENV_MAX_TRANSIENT_ATTEMPTS, _DEFAULT_MAX_TRANSIENT_ATTEMPTS
        )
        self._retry_base_seconds = _read_positive_float_env(
            _ENV_RETRY_BASE_SECONDS, _DEFAULT_RETRY_BASE_SECONDS
        )

    def start(self, *, goal: str, declared_params: dict[str, str], observation_text: str) -> None:
        params_text = ", ".join(f"{k}={v}" for k, v in declared_params.items())
        initial_text = f"Goal: {goal}\nDeclared parameters: {params_text}\n\nCurrent state:\n{observation_text}"
        self._contents = [types.Content(role="user", parts=[types.Part.from_text(text=initial_text)])]

    def _generate_content_once(self) -> types.GenerateContentResponse:
        return self._client.models.generate_content(
            model=self.model,
            contents=self._contents,
            config=types.GenerateContentConfig(
                system_instruction=self._system_prompt,
                tools=[self._tool],
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode=types.FunctionCallingConfigMode.ANY
                    )
                ),
            ),
        )

    def _generate_with_retry(self, phase: str) -> types.GenerateContentResponse:
        """Bounded retry for transient provider failures ONLY. This is the
        one place in the whole system that sleeps and re-sends a request —
        it never touches self._contents, never signals the engine, and
        never advances self._request_count itself (the caller already did
        that once, before any attempt). A retry here is invisible to
        everything outside this method: either it eventually returns a
        real response, or it raises the same LLMProviderError
        propose_action() could always raise.

        - 503 (transient "high demand"): retried with bounded exponential
          backoff, up to self._max_transient_attempts total attempts.
        - 429 (rate limited): retried AT MOST ONCE, and only if the API's
          own error details state a retry delay — never an unlimited or
          guessed backoff.
        - Everything else (400, auth errors, malformed responses, etc.):
          never retried; raised immediately as today.
        """
        rate_limit_retry_used = False
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._generate_content_once()
            except errors.APIError as exc:
                if exc.code == _TRANSIENT_CODE and attempt < self._max_transient_attempts:
                    delay = self._retry_base_seconds * (_RETRY_BACKOFF_FACTOR ** (attempt - 1))
                    self._log_retry(phase=phase, attempt=attempt, code=exc.code, delay=delay, model=self.model)
                    time.sleep(delay)
                    continue
                if exc.code == _RATE_LIMITED_CODE and not rate_limit_retry_used:
                    delay = _retry_after_seconds(exc)
                    if delay is not None:
                        rate_limit_retry_used = True
                        self._log_retry(phase=phase, attempt=attempt, code=exc.code, delay=delay, model=self.model)
                        time.sleep(delay)
                        continue
                raise LLMProviderError(
                    f"Gemini API error (phase={phase}, model={self.model}, attempt={attempt}): "
                    f"code={exc.code} message={exc.message}"
                ) from exc
            except Exception as exc:  # noqa: BLE001 - network/transport errors etc., never retried
                raise LLMProviderError(
                    f"Gemini request failed (phase={phase}, model={self.model}, attempt={attempt}): "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

    @staticmethod
    def _log_retry(*, phase: str, attempt: int, code: int, delay: float, model: str) -> None:
        # Safe diagnostics only — provider, model, attempt number, error
        # code, backoff duration. Never the request/response payload,
        # never the API key, never any credential.
        _logger.warning(
            "Gemini transient error, retrying "
            "(provider=gemini model=%s phase=%s attempt=%d code=%d backoff=%.1fs)",
            model,
            phase,
            attempt,
            code,
            delay,
        )

    def propose_action(self) -> LLMDecision:
        self._request_count += 1
        phase = "initial_decision" if self._request_count == 1 else "tool_result_turn"
        response = self._generate_with_retry(phase)

        function_calls = response.function_calls or []
        proposed = [LLMActionCall(name=fc.name, args=dict(fc.args or {})) for fc in function_calls]

        decision = validate_single_call(proposed)

        # Remember the model's own content block so the follow-up turn can
        # be threaded correctly, and which raw FunctionCall to respond to
        # (only meaningful when validation succeeded).
        candidate_content = response.candidates[0].content if response.candidates else None
        if candidate_content is not None:
            self._contents.append(candidate_content)

        # Kept regardless of validity — record_invalid_decision() needs to
        # respond to every raw call the model actually made (even an
        # unknown-named or extra one) before a corrective turn can follow.
        self._last_raw_function_calls = function_calls

        if isinstance(decision, LLMActionCall) and function_calls:
            # There may be more than one raw function_call even though
            # validate_single_call only accepts responses with exactly
            # one ACTIONABLE (known-name) call; find the one that matched.
            self._pending_function_call = next(
                fc for fc in function_calls if fc.name == decision.name
            )
        else:
            self._pending_function_call = None

        return decision

    def record_tool_result(self, *, result_text: str, is_error: bool) -> None:
        if self._pending_function_call is None:
            # No valid pending call to respond to (e.g. validation failed) —
            # nothing Gemini-specific to thread; the engine treats this as
            # a terminal condition and won't call propose_action() again.
            return

        response_payload: dict[str, str] = {"error": result_text} if is_error else {"result": result_text}
        function_response_part = types.Part.from_function_response(
            name=self._pending_function_call.name, response=response_payload
        )
        # Gemini's Content.role only accepts "user" or "model" — function
        # responses are threaded back as a "user" turn, exactly as the
        # installed google-genai SDK's own chat implementation does
        # internally (see chats.py: `role="user", parts=func_response_parts`).
        # There is no "tool" role in the Gemini API.
        self._contents.append(types.Content(role="user", parts=[function_response_part]))
        self._pending_function_call = None

    def record_invalid_decision(self, message: str) -> None:
        # If the rejected response contained any raw function calls (e.g.
        # multiple calls, or an unknown tool name), each one still needs a
        # function_response to keep the conversation structurally valid,
        # even though none of them were accepted. A plain text-only
        # response has no such calls, so this is just the corrective text.
        parts = [
            types.Part.from_function_response(name=fc.name, response={"error": message})
            for fc in self._last_raw_function_calls
        ]
        parts.append(types.Part.from_text(text=message))
        self._contents.append(types.Content(role="user", parts=parts))
        self._last_raw_function_calls = []
        self._pending_function_call = None
