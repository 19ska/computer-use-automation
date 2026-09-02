"""Provider-neutral LLM seam.

The discovery engine talks ONLY to this module's types and the
`LLMProvider` Protocol — it never imports a specific provider's SDK.
Swapping providers (Gemini today, potentially something else later)
means writing a new module that implements this Protocol; nothing in
engine.py, tools.py's schema, or anywhere else in the discovery package
needs to change.

`validate_single_call` is the harness-side response-shape validation
required regardless of provider: exactly one actionable call, never
zero, never more than one, never an unknown tool name. It operates on
the normalized `LLMActionCall` list a provider adapter already extracted
from its own SDK's response — this logic itself has no provider-specific
knowledge at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..tools import TOOL_NAMES


@dataclass
class LLMActionCall:
    name: str
    args: dict[str, Any]


@dataclass
class LLMDecisionError:
    reason: str


LLMDecision = LLMActionCall | LLMDecisionError


class LLMProviderError(Exception):
    """Raised by a provider on API/network/rate-limit/invalid-response
    failure. The engine catches this ONE exception type regardless of
    which provider is in use."""


class LLMProvider(Protocol):
    model: str
    provider_name: str

    def start(self, *, goal: str, declared_params: dict[str, str], observation_text: str) -> None: ...

    def propose_action(self) -> LLMDecision: ...

    def record_tool_result(self, *, result_text: str, is_error: bool) -> None: ...

    def record_invalid_decision(self, message: str) -> None:
        """Threads a corrective message back into the conversation after a
        rejected decision (zero calls, multiple calls, or an unknown tool
        name) — used by the engine's bounded correction loop to ask the
        SAME discovery state for another decision, without executing
        anything. Distinct from record_tool_result(), which always
        responds to a specific successfully-identified tool call; this
        method has no such call to respond to, so each provider threads
        the message in whatever shape its own API requires (e.g. an
        OpenAI-style API still expects a tool-role response for any raw
        tool_calls the rejected response contained, even if none of them
        were valid, before a plain corrective turn can follow).
        """
        ...


def validate_single_call(proposed: list[LLMActionCall]) -> LLMActionCall | LLMDecisionError:
    actionable = [c for c in proposed if c.name in TOOL_NAMES]

    if len(actionable) == 0:
        if proposed:
            return LLMDecisionError(
                "expected exactly one actionable function call, got 0 valid calls; "
                f"received unsupported/unknown tool name(s): {[c.name for c in proposed]}"
            )
        return LLMDecisionError("expected exactly one actionable function call, got 0 (no tool call in response)")
    if len(actionable) > 1:
        return LLMDecisionError(
            f"expected exactly one actionable function call, got {len(actionable)}: "
            f"{[c.name for c in actionable]}"
        )
    return actionable[0]
