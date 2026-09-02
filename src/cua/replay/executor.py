"""Generic action executor — dispatches ActionStep data to Playwright.

Contains NO capability-specific flow logic: every decision (which
element, what value, what to check) comes from the ActionStep/artifact
data passed in.

Retry behavior is intentionally minimal for Milestone 3: only exceptions
this milestone actually understands are retried, and only when the
step's own RetryPolicy.retry_on declares a condition we understand
("timeout" or "navigation_pending" — both currently mapped onto the same
retry loop, since M3 has no way to distinguish them). LocatorAmbiguousError
is never retried — ambiguity does not resolve by waiting, so it always
fails immediately regardless of policy. Richer recovery conditions
("detached_element", transient-failure injection) are deferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from playwright.sync_api import TimeoutError as PWTimeoutError

from cua.artifact.schema import ActionStep, CapabilityArtifact, LiteralRef, OutputField, ParamRef, ValueRef

from . import checkpoints, extraction, locators
from .evidence import ReplayEvidenceWriter
from .extraction import ExtractionError
from .locators import LocatorAmbiguousError, LocatorNotFoundError, SupportsPageProtocol

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


def run_steps(
    page: SupportsPageProtocol,
    artifact: CapabilityArtifact,
    resolved_inputs: dict[str, Any],
    evidence: ReplayEvidenceWriter,
) -> tuple[dict[str, Any], StepFailure | None]:
    outputs: dict[str, Any] = {}
    outputs_by_name = {o.name: o for o in artifact.outputs}

    for step in artifact.steps:
        failure = _run_one_step(page, step, artifact, resolved_inputs, outputs, outputs_by_name, evidence)
        if failure is not None:
            return outputs, failure

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
                return outputs, StepFailure(
                    step_id=step.step_id,
                    category="checkpoint_failed",
                    expected=result.expected,
                    observed=result.observed,
                    exception_summary="step checkpoint did not pass",
                )

    return outputs, None


def _run_one_step(
    page: SupportsPageProtocol,
    step: ActionStep,
    artifact: CapabilityArtifact,
    resolved_inputs: dict[str, Any],
    outputs: dict[str, Any],
    outputs_by_name: dict[str, OutputField],
    evidence: ReplayEvidenceWriter,
) -> StepFailure | None:
    understands_retry = bool(_UNDERSTOOD_RETRY_CONDITIONS & set(step.retry.retry_on))
    attempts = step.retry.max_attempts

    for attempt in range(attempts):
        try:
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
