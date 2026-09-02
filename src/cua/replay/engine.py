"""Top-level orchestration for a single deterministic replay run.

This is the ONLY module that knows the overall order of operations:
load -> validate inputs -> establish session -> execute steps ->
verify success checkpoint -> return a typed result. Every piece it calls
is generic and reusable for a different capability/vendor later.

Zero LLM calls anywhere in this module or anything it imports.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

from cua.artifact.loader import ArtifactLoadError, load_artifact
from cua.artifact.schema import CapabilityArtifact

from . import checkpoints, executor, session
from .evidence import ReplayEvidenceWriter
from .inputs import InputValidationError, validate_inputs
from .results import ReplayBusinessOutcome, ReplayFailure, ReplayResult, ReplaySuccess


def _generate_run_id(capability_id: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{capability_id}-{timestamp}-{uuid.uuid4().hex[:8]}"


def run_replay(
    artifact_path: str | Path,
    raw_inputs: dict[str, str],
    *,
    headless: bool = False,
    base_dir: Path = Path("run_output"),
) -> ReplayResult:
    # 1. Load + validate the artifact — no browser opened yet.
    try:
        artifact = load_artifact(artifact_path)
    except ArtifactLoadError as exc:
        return ReplayFailure(
            run_id=_generate_run_id("unknown"),
            capability_id="unknown",
            failure_category="artifact_load_error",
            exception_summary=str(exc),
        )

    run_id = _generate_run_id(artifact.capability_id)

    # 2. Validate runtime inputs — still no browser opened.
    try:
        resolved_inputs = validate_inputs(artifact.inputs, raw_inputs)
    except InputValidationError as exc:
        return ReplayFailure(
            run_id=run_id,
            capability_id=artifact.capability_id,
            failure_category="input_validation_error",
            exception_summary=str(exc),
        )

    evidence = ReplayEvidenceWriter(run_id=run_id, base_dir=base_dir)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            result = _run_against_page(page, artifact, resolved_inputs, run_id, evidence)
        except Exception as exc:  # noqa: BLE001 - last-resort safety net
            screenshot_path = _safe_screenshot(page, evidence, "unexpected_error")
            result = ReplayFailure(
                run_id=run_id,
                capability_id=artifact.capability_id,
                failure_category="unexpected_error",
                exception_summary=str(exc),
                screenshot_path=screenshot_path,
                evidence_dir=str(evidence.run_dir),
            )
        finally:
            browser.close()

    return result


def _run_against_page(
    page: Page,
    artifact: CapabilityArtifact,
    resolved_inputs: dict[str, Any],
    run_id: str,
    evidence: ReplayEvidenceWriter,
) -> ReplayResult:
    session_result = session.establish_session(
        page,
        base_url=artifact.target_app,
        session_requirement=artifact.session_requirement,
        business_outcomes=artifact.business_outcomes,
        evidence=evidence,
    )

    if isinstance(session_result, session.SessionBusinessOutcome):
        return ReplayBusinessOutcome(
            run_id=run_id,
            capability_id=artifact.capability_id,
            outcome_code=session_result.outcome_code,
            message=session_result.message,
            step_id=None,
            evidence_dir=str(evidence.run_dir),
        )
    if isinstance(session_result, session.SessionFailure):
        screenshot_path = _safe_screenshot(page, evidence, "session_establishment_failed")
        return ReplayFailure(
            run_id=run_id,
            capability_id=artifact.capability_id,
            failure_category="session_establishment_error",
            expected=session_result.expected,
            observed=session_result.observed,
            screenshot_path=screenshot_path,
            evidence_dir=str(evidence.run_dir),
        )

    outputs, step_failure = executor.run_steps(page, artifact, resolved_inputs, evidence)
    if step_failure is not None:
        screenshot_path = _safe_screenshot(page, evidence, f"step_{step_failure.step_id}_failed")
        return ReplayFailure(
            run_id=run_id,
            capability_id=artifact.capability_id,
            failure_category=step_failure.category,
            step_id=step_failure.step_id,
            expected=step_failure.expected,
            observed=step_failure.observed,
            exception_summary=step_failure.exception_summary,
            screenshot_path=screenshot_path,
            evidence_dir=str(evidence.run_dir),
        )

    final_result = checkpoints.evaluate_checkpoint(page, artifact.success_checkpoint, resolved_inputs)
    evidence.record_event(
        step_id=None,
        action="success_checkpoint",
        locator_strategy=None,
        outcome="passed" if final_result.passed else "failed",
        detail=final_result.observed[:200],
    )
    if not final_result.passed:
        screenshot_path = _safe_screenshot(page, evidence, "success_checkpoint_failed")
        return ReplayFailure(
            run_id=run_id,
            capability_id=artifact.capability_id,
            failure_category="checkpoint_failed",
            expected=final_result.expected,
            observed=final_result.observed,
            screenshot_path=screenshot_path,
            evidence_dir=str(evidence.run_dir),
        )

    return ReplaySuccess(
        run_id=run_id,
        capability_id=artifact.capability_id,
        outputs=outputs,
        checkpoint_evidence=final_result.observed,
        evidence_dir=str(evidence.run_dir),
    )


def _safe_screenshot(page: Page, evidence: ReplayEvidenceWriter, name: str) -> str | None:
    try:
        return evidence.save_screenshot(page, name)
    except Exception:  # noqa: BLE001 - never let screenshot capture mask the real failure
        return None
