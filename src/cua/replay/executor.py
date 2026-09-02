"""Generic action executor — dispatches ActionStep data to Playwright.

Contains NO capability-specific flow logic: every decision (which
element, what value, what to check) comes from the ActionStep/artifact
data passed in.

Retry behavior is intentionally minimal: only exceptions this milestone
actually understands are retried, and only when the step's own
RetryPolicy.retry_on declares a condition we understand ("timeout" or
"navigation_pending"). LocatorAmbiguousError is never retried — ambiguity
does not resolve by waiting, so it always fails immediately regardless of
policy.

Milestone 6 additions, both gated by cua.replay.policy.check_policy()
immediately before every step:
- a hard policy violation (disallowed action/domain, or the current page
  having unexpectedly drifted to an unapproved origin) stops the run with
  a structured ReplayFailure(failure_category="policy_violation") —
  automation never attempts to recover or interact with the page first;
- a risky step whose resolved runtime value exceeds the artifact's
  approval threshold triggers a same-session human handoff
  (_handle_intervention) instead of being dispatched automatically.

A deterministic, disabled-by-default TransientInjector may force exactly
one synthetic timeout on an eligible step's first attempt, purely to
produce demo evidence of the existing bounded-retry recovery path — it
never touches a risky step, and the real dispatch always still runs for
real on the next attempt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from playwright.sync_api import TimeoutError as PWTimeoutError

from cua.artifact.schema import ActionStep, CapabilityArtifact, LiteralRef, OutputField, ParamRef, ValueRef

from . import checkpoints, extraction, locators, manual_capture, policy
from .evidence import ReplayEvidenceWriter
from .extraction import ExtractionError
from .locators import LocatorAmbiguousError, LocatorNotFoundError, SupportsPageProtocol
from .operator import InterventionRequest, OperatorInterface, TerminalOperator
from .policy import PolicyDecision

# Exceptions this milestone knows how to retry as a "timeout"-style
# condition. Anything else propagates immediately.
_RETRYABLE_AS_TIMEOUT: tuple[type[Exception], ...] = (LocatorNotFoundError, PWTimeoutError)

_UNDERSTOOD_RETRY_CONDITIONS = {"timeout", "navigation_pending"}


@dataclass
class StepFailure:
    step_id: int
    category: str
    expected: str | None
    observed: str | None
    exception_summary: str


@dataclass
class InterventionOutcome:
    step_id: int
    decision: Literal["declined", "not_confirmed"]
    reason: str


class TransientInjector(Protocol):
    def should_inject(self, step: ActionStep, attempt: int) -> bool: ...


@dataclass
class FirstEligibleStepOnceInjector:
    """Demo/test-only, disabled by default. Fires exactly once, on the
    first attempt of the first step that (a) is not risky — so this never
    collides with the human-handoff demo on the same step, (b) declares
    "timeout" in its own RetryPolicy.retry_on, and (c) allows more than
    one attempt. Never affects any other step or attempt.
    """

    _fired: bool = False

    def should_inject(self, step: ActionStep, attempt: int) -> bool:
        if self._fired or attempt != 0 or step.risk == "risky":
            return False
        if step.retry.max_attempts <= 1 or "timeout" not in step.retry.retry_on:
            return False
        self._fired = True
        return True


def run_steps(
    page: SupportsPageProtocol,
    artifact: CapabilityArtifact,
    resolved_inputs: dict[str, Any],
    evidence: ReplayEvidenceWriter,
    *,
    operator: OperatorInterface | None = None,
    transient_injector: TransientInjector | None = None,
) -> tuple[dict[str, Any], StepFailure | None, InterventionOutcome | None]:
    outputs: dict[str, Any] = {}
    outputs_by_name = {o.name: o for o in artifact.outputs}
    operator = operator or TerminalOperator()

    for step in artifact.steps:
        decision = policy.check_policy(page, step, artifact, resolved_inputs)
        evidence.record_event(
            step_id=step.step_id,
            action="policy_check",
            locator_strategy=None,
            outcome="blocked" if not decision.allowed else ("approval_required" if decision.requires_approval else "passed"),
            detail=decision.reason,
        )

        if not decision.allowed:
            return (
                outputs,
                StepFailure(step.step_id, "policy_violation", None, None, decision.reason or "policy violation"),
                None,
            )

        if decision.requires_approval:
            intervention = _handle_intervention(page, step, artifact, resolved_inputs, evidence, operator, decision)
            if intervention is not None:
                return outputs, None, intervention
            # Confirmed: the human already performed this risky step
            # themselves — do NOT dispatch it again, just continue.
        else:
            failure = _run_one_step(
                page, step, artifact, resolved_inputs, outputs, outputs_by_name, evidence, transient_injector
            )
            if failure is not None:
                return outputs, failure, None

        if step.checkpoint is not None:
            result = checkpoints.evaluate_checkpoint(page, step.checkpoint, resolved_inputs)
            evidence.record_event(
                step_id=step.step_id,
                action="checkpoint",
                locator_strategy=None,
                outcome="passed" if result.passed else "failed",
                detail=result.observed[:200],
            )
            if not result.passed:
                return (
                    outputs,
                    StepFailure(
                        step.step_id, "checkpoint_failed", result.expected, result.observed,
                        "step checkpoint did not pass",
                    ),
                    None,
                )

    return outputs, None, None


def _describe_step(step: ActionStep) -> str:
    if step.target is not None:
        return f"{step.action} on '{step.target.description}'"
    if step.url is not None:
        return f"{step.action} to '{step.url}'"
    return step.action


def _handle_intervention(
    page: SupportsPageProtocol,
    step: ActionStep,
    artifact: CapabilityArtifact,
    resolved_inputs: dict[str, Any],
    evidence: ReplayEvidenceWriter,
    operator: OperatorInterface,
    decision: PolicyDecision,
) -> InterventionOutcome | None:
    try:
        screenshot_path = evidence.save_screenshot(page, f"intervention_step_{step.step_id}")
    except Exception:  # noqa: BLE001 - never let screenshot capture block a real handoff
        screenshot_path = None

    request = InterventionRequest(
        run_id=evidence.run_id,
        capability_id=artifact.capability_id,
        step_id=step.step_id,
        reason=decision.reason or "approval required",
        current_url=page.url,  # type: ignore[attr-defined]
        screenshot_path=screenshot_path,
        timestamp=datetime.now(timezone.utc).isoformat(),
        pending_action=_describe_step(step),
        context=decision.threshold_context or {},
    )
    evidence.record_intervention_request(request)
    evidence.record_control_transition(owner="human", step_id=step.step_id)

    def _on_manual_event(event: dict[str, Any]) -> None:
        evidence.record_manual_event(
            step_id=step.step_id,
            event_type=event.get("type", "unknown"),
            tag=event.get("tag"),
            id=event.get("id"),
            name=event.get("name"),
            text=event.get("text"),
        )

    manual_capture.install(page, _on_manual_event)  # type: ignore[arg-type]

    op_decision = operator.request_intervention(request)

    manual_capture.disable(page)  # type: ignore[arg-type]
    evidence.record_control_transition(owner="automation", step_id=step.step_id)
    evidence.record_intervention_decision(step_id=step.step_id, decision=op_decision)

    if op_decision == "decline":
        return InterventionOutcome(step_id=step.step_id, decision="declined", reason=request.reason)

    # "resume" — never assume the human performed the expected action;
    # independently revalidate against the SAME success_checkpoint the
    # run will need to pass anyway.
    check = checkpoints.evaluate_checkpoint(page, artifact.success_checkpoint, resolved_inputs)
    evidence.record_event(
        step_id=step.step_id,
        action="intervention_resume_check",
        locator_strategy=None,
        outcome="passed" if check.passed else "failed",
        detail=check.observed[:200],
    )
    if not check.passed:
        return InterventionOutcome(
            step_id=step.step_id,
            decision="not_confirmed",
            reason=f"expected: {check.expected}; observed: {check.observed[:200]}",
        )
    return None


def _run_one_step(
    page: SupportsPageProtocol,
    step: ActionStep,
    artifact: CapabilityArtifact,
    resolved_inputs: dict[str, Any],
    outputs: dict[str, Any],
    outputs_by_name: dict[str, OutputField],
    evidence: ReplayEvidenceWriter,
    transient_injector: TransientInjector | None,
) -> StepFailure | None:
    understands_retry = bool(_UNDERSTOOD_RETRY_CONDITIONS & set(step.retry.retry_on))
    attempts = step.retry.max_attempts

    for attempt in range(attempts):
        injected_this_attempt = bool(transient_injector and transient_injector.should_inject(step, attempt))
        try:
            if injected_this_attempt:
                raise PWTimeoutError(
                    f"injected transient condition (--inject-transient-once), "
                    f"step={step.step_id} attempt={attempt + 1}"
                )
            _dispatch(page, step, artifact, resolved_inputs, outputs, outputs_by_name, evidence)
            return None
        except LocatorAmbiguousError as exc:
            evidence.record_event(
                step_id=step.step_id, action=step.action, locator_strategy=None,
                outcome="failed", detail="ambiguous",
            )
            return StepFailure(step.step_id, "locator_ambiguous", None, None, str(exc))
        except ExtractionError as exc:
            evidence.record_event(
                step_id=step.step_id, action=step.action, locator_strategy=None,
                outcome="failed", detail="extraction_error",
            )
            return StepFailure(step.step_id, "extraction_error", None, None, str(exc))
        except _RETRYABLE_AS_TIMEOUT as exc:
            if understands_retry and attempt + 1 < attempts:
                evidence.record_event(
                    step_id=step.step_id, action=step.action, locator_strategy=None,
                    outcome="injected_transient" if injected_this_attempt else "retrying",
                    detail=str(exc)[:200],
                )
                page.wait_for_timeout(step.retry.backoff_ms)  # type: ignore[attr-defined]
                continue
            evidence.record_event(
                step_id=step.step_id, action=step.action, locator_strategy=None,
                outcome="failed", detail="not_found_or_timeout",
            )
            return StepFailure(step.step_id, "locator_not_found", None, None, str(exc))
        except Exception as exc:  # noqa: BLE001 - last-resort catch
            evidence.record_event(
                step_id=step.step_id, action=step.action, locator_strategy=None,
                outcome="failed", detail="unexpected",
            )
            return StepFailure(step.step_id, "unexpected_error", None, None, str(exc))

    return None  # pragma: no cover - loop always returns or raises


def _dispatch(
    page: SupportsPageProtocol,
    step: ActionStep,
    artifact: CapabilityArtifact,
    resolved_inputs: dict[str, Any],
    outputs: dict[str, Any],
    outputs_by_name: dict[str, OutputField],
    evidence: ReplayEvidenceWriter,
) -> None:
    if step.action == "navigate":
        full_url = f"{artifact.target_app.rstrip('/')}{step.url}"
        page.goto(full_url, wait_until="networkidle", timeout=10_000)  # type: ignore[attr-defined]
        evidence.record_event(
            step_id=step.step_id, action="navigate", locator_strategy=None, outcome="ok", detail=step.url
        )
        return

    if step.action == "extract":
        output = outputs_by_name[step.output_ref]
        value = extraction.extract_output(page, output)
        outputs[output.name] = value
        evidence.record_event(
            step_id=step.step_id, action="extract", locator_strategy=None, outcome="ok",
            detail=f"output={output.name}",
        )
        return

    resolution = locators.resolve_target(page, step.target)

    if step.action == "click":
        resolution.locator.click(timeout=5_000)  # type: ignore[attr-defined]
    elif step.action == "wait_for":
        pass  # resolving successfully (a unique VISIBLE match) IS the wait condition
    elif step.action == "type":
        value = _resolve_value(step.value, resolved_inputs)
        resolution.locator.fill(str(value), timeout=5_000)  # type: ignore[attr-defined]
    elif step.action == "select_option":
        value = _resolve_value(step.value, resolved_inputs)
        resolution.locator.select_option(str(value), timeout=5_000)  # type: ignore[attr-defined]
    else:
        raise AssertionError(f"unhandled action: {step.action}")  # pragma: no cover

    evidence.record_event(
        step_id=step.step_id,
        action=step.action,
        locator_strategy=resolution.strategy_description,
        outcome="ok",
    )
    # NOTE: the resolved `value` for type/select_option is intentionally
    # never passed to evidence.record_event above.


def _resolve_value(ref: ValueRef, resolved_inputs: dict[str, Any]) -> Any:
    if isinstance(ref, ParamRef):
        return resolved_inputs[ref.name]
    if isinstance(ref, LiteralRef):
        return ref.value
    raise AssertionError(f"unhandled value ref: {ref!r}")  # pragma: no cover
