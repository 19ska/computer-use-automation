"""Shared fakes for discovery tests — a fake LLMProvider (for engine
tests) and minimal fakes mimicking the real Gemini/Groq SDK response
shapes (for testing GeminiProvider's/GroqProvider's own extraction logic
in isolation). No real network access, no real browser, no real Gemini
or Groq API calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import groq
import httpx

from cua.discovery.llm import LLMActionCall, LLMDecisionError, LLMProviderError


class FakeLLMProvider:
    """Implements the LLMProvider Protocol directly — returns/raises the
    next scripted item per `propose_action()` call. Used by engine tests
    so no provider-specific response shape needs to be faked at all."""

    provider_name = "gemini"

    def __init__(self, model: str, script: list[Any]):
        self.model = model
        self._script = list(script)
        self.start_calls: list[tuple[str, dict[str, str], str]] = []
        self.record_tool_result_calls: list[tuple[str, bool]] = []
        self.record_invalid_decision_calls: list[str] = []

    def start(self, *, goal: str, declared_params: dict[str, str], observation_text: str) -> None:
        self.start_calls.append((goal, declared_params, observation_text))

    def propose_action(self) -> LLMActionCall | LLMDecisionError:
        if not self._script:
            raise AssertionError("FakeLLMProvider.propose_action() called more times than scripted")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def record_tool_result(self, *, result_text: str, is_error: bool) -> None:
        self.record_tool_result_calls.append((result_text, is_error))

    def record_invalid_decision(self, message: str) -> None:
        self.record_invalid_decision_calls.append(message)


def action_call(name: str, args: dict[str, Any]) -> LLMActionCall:
    return LLMActionCall(name=name, args=args)


class ScriptedCallable:
    """A callable that pops and returns/raises the next scripted item per
    call, ignoring whatever arguments it's invoked with. Used to stand in
    for execute_action_fn / evaluate_checkpoint_fn in engine tests — these
    are unrelated to the LLM provider and unaffected by this migration."""

    def __init__(self, script: list[Any]):
        self._script = list(script)
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        if not self._script:
            raise AssertionError("ScriptedCallable called more times than scripted")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class RepeatingCallable:
    """Always returns the same scripted value/behavior, for dependencies
    that don't need to vary per call in a given test."""

    def __init__(self, value: Any):
        self._value = value
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        return self._value


# --- Minimal fakes mimicking the REAL google-genai response shape, used
# only by test_gemini_provider.py to test GeminiProvider's own
# response-extraction logic in isolation. ---


@dataclass
class FakeGeminiFunctionCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    id: str = "call_1"


@dataclass
class FakeGeminiContent:
    role: str = "model"
    parts: list[Any] = field(default_factory=list)


@dataclass
class FakeGeminiCandidate:
    content: FakeGeminiContent = field(default_factory=FakeGeminiContent)


@dataclass
class FakeGeminiResponse:
    function_calls: list[FakeGeminiFunctionCall] = field(default_factory=list)
    candidates: list[FakeGeminiCandidate] = field(default_factory=list)


class ScriptedGenerateContent:
    """Stands in for client.models.generate_content — pops/raises the
    next scripted response per call."""

    def __init__(self, script: list[Any]):
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> FakeGeminiResponse:
        self.calls.append(kwargs)
        if not self._script:
            raise AssertionError("generate_content called more times than scripted")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# --- Minimal fakes mimicking the REAL groq (OpenAI-Chat-Completions-
# shaped) response shape, used only by test_groq_provider.py to test
# GroqProvider's own response-extraction logic in isolation. ---


@dataclass
class FakeGroqToolCallFunction:
    name: str
    arguments: str = "{}"  # JSON string, exactly as the real SDK returns it


@dataclass
class FakeGroqToolCall:
    id: str
    function: FakeGroqToolCallFunction
    type: str = "function"


@dataclass
class FakeGroqMessage:
    content: str | None = None
    tool_calls: list[FakeGroqToolCall] = field(default_factory=list)


@dataclass
class FakeGroqChoice:
    message: FakeGroqMessage = field(default_factory=FakeGroqMessage)


@dataclass
class FakeGroqResponse:
    choices: list[FakeGroqChoice] = field(default_factory=list)


def make_groq_api_status_error(status_code: int, message: str) -> groq.APIStatusError:
    """groq.APIStatusError requires a real httpx.Response (unlike
    google-genai's errors.APIError, which accepts a plain dict) — this
    builds the minimal real one needed to construct it in tests."""
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(status_code, request=request, json={"error": {"message": message}})
    return groq.APIStatusError(message, response=response, body={"error": {"message": message}})


class ScriptedGroqCreate:
    """Stands in for client.chat.completions.create — pops/raises the
    next scripted response per call."""

    def __init__(self, script: list[Any]):
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> FakeGroqResponse:
        self.calls.append(kwargs)
        if not self._script:
            raise AssertionError("chat.completions.create called more times than scripted")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
