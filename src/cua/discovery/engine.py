"""Top-level orchestration for a single LLM-driven discovery run.

observe -> decide -> act, bounded by max_steps and a wall-clock timeout,
with a policy gate before every proposed action and independent
verification of any "finish" claim. Zero automatic artifact generation —
that is Milestone 5.

Reuses, unmodified, from cua.replay: locators.resolve_target (via
executor/target_resolution), checkpoints.evaluate_checkpoint (finish
verification), and session.establish_session (session bootstrap) — the
same generic pieces deterministic replay already relies on.

Provider-neutral: this module talks only to the LLMProvider Protocol
(cua.discovery.llm.base) — it never imports google.genai, groq, or any
other provider SDK directly, and never constructs provider-specific
message objects. The only place a concrete provider is chosen is inside
run_discovery(), via cua.discovery.llm.factory.create_llm_provider() —
which provider that resolves to (Gemini, Groq, ...) is controlled purely
by the LLM_PROVIDER environment variable; this module has no branching
of its own on provider identity.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright

from cua.artifact.schema import BusinessOutcomeDetector, Checkpoint, CssLocator, ElementTarget, ParamRef, SessionRequirement
from cua.replay import checkpoints, session

from . import executor, policy
from .evidence import DiscoveryEvidenceWriter
from .llm import LLMActionCall, LLMDecisionError, LLMProvider, LLMProviderError
from .llm.factory import DEFAULT_PROVIDER, create_llm_provider, resolve_model, resolve_provider_name
from .observation import capture_observation
from .results import DiscoveryBusinessOutcome, DiscoveryFailure, DiscoveryResult, DiscoverySuccess

DEFAULT_MAX_STEPS = 15
DEFAULT_TIMEOUT_S = 300.0
REPEATED_FAILURE_THRESHOLD = 2  # same signature failing this many times in a row -> stuck
MAX_INVALID_DECISION_CORRECTIONS = 2  # bounded corrective retries for the SAME discovery state

_CORRECTIVE_MESSAGE = (
    "This discovery loop requires exactly one action per turn. Respond by calling exactly one of: "
    "navigate, click, type_text, select_option, finish, give_up."
)

_CONFIRMATION_TEXT = "Transfer Complete!"

# There is no artifact yet at discovery time (that's Milestone 5's job),
# so the one known session-establishment outcome is declared directly
# here — kept in sync with the same detector in
# examples/capabilities/parabank_transfer_funds.json.
_SESSION_BUSINESS_OUTCOMES: list[BusinessOutcomeDetector] = [
    BusinessOutcomeDetector(
        code="INVALID_CREDENTIALS",
        description="The configured auth_profile's credentials were rejected during session establishment.",
        origin="session_establishment",
        target=ElementTarget(description="body", strategies=[CssLocator(selector="body")]),
        contains_text="The username and password could not be verified.",
    )
]

SYSTEM_PROMPT = """You are operating a live web banking application to accomplish a single, bounded goal.

You act ONLY through the provided tools. You cannot execute scripts, run shell commands, access the filesystem, or navigate outside the current application.

Every decision turn MUST call exactly one tool. Never respond with prose, an explanation, or a summary instead of a tool call — a text-only response cannot be executed and will be rejected.

Every tool call must include a short "rationale" of one sentence. Do not include extended internal reasoning anywhere in your responses — only that one short, user-visible sentence per action.

You will be told the goal and any declared parameters. Type or select exactly the parameter values you are given.

Describe the target control using "accessible_role" and "accessible_name" matching what you observe in the current state (buttons, links, text fields, dropdowns). Never guess at CSS selectors or HTML structure — you cannot see the underlying markup, only the summarized controls provided to you.

Use "finish" only when the goal is visibly achieved in the current state. Your claim will be independently verified by the harness against the live page — it is not taken on trust. If verification fails, you will be told what was missing and should continue working toward the goal.

Use "give_up" if you become stuck and no valid action can be determined, with a short reason.
"""


def _generate_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"discovery-{timestamp}-{uuid.uuid4().hex[:8]}"


def run_discovery(
    goal: str,
    declared_params: dict[str, str],
    *,
    base_url: str,
    headless: bool = False,
    max_steps: int = DEFAULT_MAX_STEPS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    base_dir: Path = Path("discovery_output"),
) -> DiscoveryResult:
    run_id = _generate_run_id()
    evidence = DiscoveryEvidenceWriter(run_id=run_id, base_dir=base_dir)
    allowed_host = urlparse(base_url).netloc

    try:
        provider_name = resolve_provider_name()
    except LLMProviderError as exc:
        # An unsupported LLM_PROVIDER value fails cleanly before discovery
        # starts. os.environ is read directly here (never through
        # resolve_provider_name(), which just raised) purely to make the
        # failure record readable — falls back to the documented default
        # if LLM_PROVIDER itself is unset.
        configured_provider = os.environ.get("LLM_PROVIDER", DEFAULT_PROVIDER)
        return DiscoveryFailure(
            run_id=run_id,
            failure_category="llm_api_error",
            reason=str(exc),
            evidence_dir=str(evidence.run_dir),
            provider=configured_provider,
            model=resolve_model(configured_provider),
        )

    model = resolve_model(provider_name)
    try:
        provider = create_llm_provider(provider_name, model, system_prompt=SYSTEM_PROMPT)
    except LLMProviderError as exc:
        return DiscoveryFailure(
            run_id=run_id,
            failure_category="llm_api_error",
            reason=str(exc),
            evidence_dir=str(evidence.run_dir),
            provider=provider_name,
            model=model,
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            result = _run_against_page(
                page,
                goal=goal,
                declared_params=declared_params,
                base_url=base_url,
                allowed_host=allowed_host,
                max_steps=max_steps,
                timeout_s=timeout_s,
                run_id=run_id,
                evidence=evidence,
                provider=provider,
            )
        except Exception as exc:  # noqa: BLE001 - last-resort safety net
            screenshot_path = _safe_screenshot(page, evidence, "unexpected_error")
            result = DiscoveryFailure(
                run_id=run_id,
                failure_category="unexpected_error",
                reason=str(exc),
                screenshot_path=screenshot_path,
                evidence_dir=str(evidence.run_dir),
                provider=provider.provider_name,
                model=model,
            )
        finally:
            browser.close()

    return result


def _run_against_page(
    page: Page,
    *,
    goal: str,
    declared_params: dict[str, str],
    base_url: str,
    allowed_host: str,
    max_steps: int,
    timeout_s: float,
    run_id: str,
    evidence: DiscoveryEvidenceWriter,
    provider: LLMProvider,
    establish_session_fn: Any = session.establish_session,
    capture_observation_fn: Any = capture_observation,
    execute_action_fn: Any = executor.execute_action,
    evaluate_checkpoint_fn: Any = checkpoints.evaluate_checkpoint,
) -> DiscoveryResult:
    """The real orchestration logic. `provider` and the four `*_fn`
    dependencies are injectable so tests can exercise stopping-condition
    logic, response validation, and finish-verification gating with fakes
    for both the LLM provider and the page — no real browser, no real API
    call, and no need for a heavyweight fake Page. `run_discovery()` is
    the only caller that supplies the real implementations.
    """
    model = provider.model
    provider_name = provider.provider_name

    session_result = establish_session_fn(
        page,
        base_url=base_url,
        session_requirement=SessionRequirement(authenticated=True, auth_profile="parabank_demo"),
        business_outcomes=_SESSION_BUSINESS_OUTCOMES,
        evidence=evidence,
    )
    if isinstance(session_result, session.SessionBusinessOutcome):
        return DiscoveryBusinessOutcome(
            run_id=run_id,
            outcome_code=session_result.outcome_code,
            message=session_result.message,
            evidence_dir=str(evidence.run_dir),
            provider=provider_name,
            model=model,
        )
    if isinstance(session_result, session.SessionFailure):
        screenshot_path = _safe_screenshot(page, evidence, "session_establishment_failed")
        return DiscoveryFailure(
            run_id=run_id,
            failure_category="session_establishment_error",
            reason=f"expected {session_result.expected}; observed {session_result.observed}",
            screenshot_path=screenshot_path,
            evidence_dir=str(evidence.run_dir),
            provider=provider_name,
            model=model,
        )

    initial_observation = capture_observation_fn(page, step_number=0)
    provider.start(
        goal=goal, declared_params=declared_params, observation_text=initial_observation.to_prompt_text()
    )

    last_failure_signature: tuple[Any, ...] | None = None
    consecutive_failures = 0
    start_time = time.monotonic()

    def note_outcome(signature: tuple[Any, ...], ok: bool) -> bool:
        """Returns True if this outcome means the run is now stuck."""
        nonlocal last_failure_signature, consecutive_failures
        if ok:
            last_failure_signature = None
            consecutive_failures = 0
            return False
        if signature == last_failure_signature:
            consecutive_failures += 1
        else:
            last_failure_signature = signature
            consecutive_failures = 1
        return consecutive_failures >= REPEATED_FAILURE_THRESHOLD

    for step_number in range(1, max_steps + 1):
        if time.monotonic() - start_time > timeout_s:
            screenshot_path = _safe_screenshot(page, evidence, "timeout")
            return DiscoveryFailure(
                run_id=run_id,
                failure_category="timeout_exceeded",
                last_step=step_number,
                reason=f"exceeded {timeout_s}s wall-clock budget",
                screenshot_path=screenshot_path,
                evidence_dir=str(evidence.run_dir),
                provider=provider_name,
                model=model,
            )

        # Bounded correction loop: an invalid decision (zero calls,
        # multiple calls, or an unknown tool name) never executes
        # anything and never advances step_number/the UI — it just asks
        # the SAME discovery state for another decision, up to
        # MAX_INVALID_DECISION_CORRECTIONS times, before this step gives
        # up entirely with the same failure_category="invalid_model_response"
        # this always had.
        correction_attempt = 0
        while True:
            try:
                decision = provider.propose_action()
            except LLMProviderError as exc:
                screenshot_path = _safe_screenshot(page, evidence, "llm_api_error")
                return DiscoveryFailure(
                    run_id=run_id,
                    failure_category="llm_api_error",
                    last_step=step_number,
                    reason=str(exc),
                    screenshot_path=screenshot_path,
                    evidence_dir=str(evidence.run_dir),
                    provider=provider_name,
                    model=model,
                )

            if isinstance(decision, LLMActionCall):
                break

            evidence.record_step(
                step_number=step_number,
                provider=provider_name,
                model=model,
                action="invalid_response",
                outcome="invalid_model_response",
                rationale=decision.reason,
                correction_attempt=correction_attempt,
            )

            if correction_attempt >= MAX_INVALID_DECISION_CORRECTIONS:
                screenshot_path = _safe_screenshot(page, evidence, "invalid_model_response")
                return DiscoveryFailure(
                    run_id=run_id,
                    failure_category="invalid_model_response",
                    last_step=step_number,
                    reason=decision.reason,
                    screenshot_path=screenshot_path,
                    evidence_dir=str(evidence.run_dir),
                    provider=provider_name,
                    model=model,
                )

            correction_attempt += 1
            provider.record_invalid_decision(_CORRECTIVE_MESSAGE)
            # loop again: same step_number, same UI state, nothing executed

        assert isinstance(decision, LLMActionCall)  # narrows for the type checker
        action = decision.name
        args = decision.args
        rationale = args.get("rationale") or args.get("reason")

        if action == "give_up":
            evidence.record_step(
                step_number=step_number, provider=provider_name, model=model, action="give_up",
                outcome="given_up", rationale=rationale,
            )
            screenshot_path = _safe_screenshot(page, evidence, "give_up")
            return DiscoveryFailure(
                run_id=run_id,
                failure_category="give_up",
                last_step=step_number,
                reason=rationale or "model gave up",
                screenshot_path=screenshot_path,
                evidence_dir=str(evidence.run_dir),
                provider=provider_name,
                model=model,
            )

        if action == "finish":
            checkpoint = Checkpoint(
                description="Independent finish verification",
                assertion="text_contains",
                expected_literal_text=[_CONFIRMATION_TEXT],
                expected_value_refs=[ParamRef(name=name) for name in declared_params],
            )
            check_result = evaluate_checkpoint_fn(page, checkpoint, declared_params)
            evidence.record_step(
                step_number=step_number, provider=provider_name, model=model, action="finish",
                outcome="finished" if check_result.passed else "finish_rejected",
                rationale=rationale,
                checkpoint_result={"passed": check_result.passed, "observed": check_result.observed[:500]},
            )
            if check_result.passed:
                _safe_screenshot(page, evidence, "final_success")
                return DiscoverySuccess(
                    run_id=run_id,
                    goal=goal,
                    declared_parameters=declared_params,
                    final_checkpoint_evidence=check_result.observed,
                    evidence_dir=str(evidence.run_dir),
                    step_count=step_number,
                    provider=provider_name,
                    model=model,
                )
            stuck = note_outcome(("finish",), ok=False)
            if stuck:
                screenshot_path = _safe_screenshot(page, evidence, "repeated_premature_finish")
                return DiscoveryFailure(
                    run_id=run_id,
                    failure_category="repeated_action_failure",
                    last_step=step_number,
                    reason="model called finish without the goal actually being achieved, repeatedly",
                    screenshot_path=screenshot_path,
                    evidence_dir=str(evidence.run_dir),
                    provider=provider_name,
                    model=model,
                )
            provider.record_tool_result(
                result_text=(
                    f"Not verified yet. Expected: {check_result.expected}. "
                    f"Observed: {check_result.observed[:500]}. Continue."
                ),
                is_error=True,
            )
            continue

        policy_decision = policy.check_policy(action, args, allowed_host=allowed_host, base_url=base_url)
        if not policy_decision.allowed:
            evidence.record_step(
                step_number=step_number, provider=provider_name, model=model, action=action,
                target_description=args.get("target_description"),
                outcome="blocked", rationale=rationale,
            )
            signature = ("blocked", action, args.get("target_description"), args.get("value"))
            stuck = note_outcome(signature, ok=False)
            if stuck:
                screenshot_path = _safe_screenshot(page, evidence, "repeated_policy_violation")
                return DiscoveryFailure(
                    run_id=run_id,
                    failure_category="repeated_action_failure",
                    last_step=step_number,
                    reason=f"policy violation repeated: {policy_decision.reason}",
                    screenshot_path=screenshot_path,
                    evidence_dir=str(evidence.run_dir),
                    provider=provider_name,
                    model=model,
                )
            provider.record_tool_result(
                result_text=f"Blocked by policy: {policy_decision.reason}. Choose a different action.",
                is_error=True,
            )
            continue

        execution = execute_action_fn(
            page, action, args, base_url=base_url, declared_params=declared_params
        )
        evidence.record_step(
            step_number=step_number,
            provider=provider_name,
            model=model,
            action=action,
            target_description=args.get("target_description"),
            accessible_role=args.get("accessible_role"),
            accessible_name=args.get("accessible_name"),
            value_source=execution.value_source,
            resolved_locator_strategy=execution.resolved_locator_strategy,
            rationale=rationale,
            outcome="ok" if execution.ok else "failed",
            resulting_url=execution.resulting_url,
        )

        signature = ("action", action, args.get("target_description"), args.get("value"))
        stuck = note_outcome(signature, ok=execution.ok)
        if not execution.ok and stuck:
            screenshot_path = _safe_screenshot(page, evidence, f"step_{step_number}_repeated_failure")
            return DiscoveryFailure(
                run_id=run_id,
                failure_category="repeated_action_failure",
                last_step=step_number,
                reason=f"action repeatedly failed: {execution.error}",
                screenshot_path=screenshot_path,
                evidence_dir=str(evidence.run_dir),
                provider=provider_name,
                model=model,
            )

        if not execution.ok:
            provider.record_tool_result(
                result_text=f"Action failed: {execution.error}. Try a different description or approach.",
                is_error=True,
            )
            continue

        new_observation = capture_observation_fn(
            page, step_number=step_number, previous_action_summary=f"{action} succeeded"
        )
        provider.record_tool_result(result_text=new_observation.to_prompt_text(), is_error=False)

    screenshot_path = _safe_screenshot(page, evidence, "max_steps_exceeded")
    return DiscoveryFailure(
        run_id=run_id,
        failure_category="max_steps_exceeded",
        last_step=max_steps,
        reason=f"exceeded {max_steps} steps without reaching the goal",
        screenshot_path=screenshot_path,
        evidence_dir=str(evidence.run_dir),
        provider=provider_name,
        model=model,
    )


def _safe_screenshot(page: Page, evidence: DiscoveryEvidenceWriter, name: str) -> str | None:
    try:
        return evidence.save_screenshot(page, name)
    except Exception:  # noqa: BLE001 - never let screenshot capture mask the real failure
        return None
