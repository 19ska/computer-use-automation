"""Tests for the generic action executor (cua.replay.executor)."""

import json
from datetime import datetime, timezone
from decimal import Decimal

from cua.artifact.schema import (
    ActionStep,
    CapabilityArtifact,
    Checkpoint,
    CssLocator,
    ElementTarget,
    ExtractionSpec,
    InputParameter,
    OutputField,
    ParamRef,
    PolicyMetadata,
    RetryPolicy,
    SessionRequirement,
)
from cua.replay.evidence import ReplayEvidenceWriter
from cua.replay.executor import FirstEligibleStepOnceInjector, run_steps

from .fakes import FakeElement, FakeLocator, FakePage


class FakeOperator:
    def __init__(self, decision: str):
        self.decision = decision
        self.calls: list = []

    def request_intervention(self, request):
        self.calls.append(request)
        return self.decision


def _artifact(
    steps: list[ActionStep], outputs: list[OutputField] | None = None, *, policy: PolicyMetadata | None = None
) -> CapabilityArtifact:
    if outputs is None:
        # Schema requires at least one output with exactly one matching
        # extract step. Tests that aren't exercising extraction get a
        # harmless placeholder appended after their real steps.
        placeholder_target = ElementTarget(
            description="placeholder", strategies=[CssLocator(selector="body")]
        )
        outputs = [
            OutputField(
                name="placeholder_output",
                type="string",
                description="d",
                extraction=ExtractionSpec(target=placeholder_target, source="text"),
            )
        ]
        next_step_id = max((s.step_id for s in steps), default=0) + 1
        steps = [*steps, ActionStep(step_id=next_step_id, action="extract", output_ref="placeholder_output")]

    return CapabilityArtifact(
        capability_id="test.executor_capability",
        capability_version=1,
        display_name="d",
        description="d",
        target_app="https://example.test/app",
        session_requirement=SessionRequirement(authenticated=False),
        inputs=[
            InputParameter(name="amount", type="decimal", description="d"),
            InputParameter(name="from_account_id", type="string", description="d"),
        ],
        outputs=outputs or [],
        steps=steps,
        success_checkpoint=Checkpoint(description="d", assertion="text_contains", expected_literal_text=["ok"]),
        policy=policy
        or PolicyMetadata(
            allowed_domains=["example.test"],
            allowed_actions=["navigate", "click", "type", "select_option", "wait_for", "extract"],
        ),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        created_by="human_edited",
        discovery_run_id="test-run",
    )


def test_param_ref_resolves_into_typed_action(tmp_path):
    page = FakePage()
    element = FakeElement(text="")
    page.css_locators["#amount"] = FakeLocator([element])

    steps = [
        ActionStep(
            step_id=1,
            action="type",
            target=ElementTarget(description="amount field", strategies=[CssLocator(selector="#amount")]),
            value=ParamRef(name="amount"),
        )
    ]
    resolved_inputs = {"amount": Decimal("20.00"), "from_account_id": "111"}
    evidence = ReplayEvidenceWriter(run_id="exec-1", base_dir=tmp_path)

    outputs, failure, intervention = run_steps(page, _artifact(steps), resolved_inputs, evidence)

    assert intervention is None

    assert failure is None
    assert element.filled_value == "20.00"


def test_extract_step_populates_outputs(tmp_path):
    page = FakePage()
    page.css_locators["body"] = FakeLocator([FakeElement(text="Transfer Complete!")])

    output = OutputField(
        name="confirmation_message",
        type="string",
        description="d",
        extraction=ExtractionSpec(
            target=ElementTarget(description="body", strategies=[CssLocator(selector="body")]),
            source="text",
        ),
    )
    steps = [ActionStep(step_id=1, action="extract", output_ref="confirmation_message")]
    evidence = ReplayEvidenceWriter(run_id="exec-2", base_dir=tmp_path)

    outputs, failure, intervention = run_steps(page, _artifact(steps, [output]), {}, evidence)

    assert intervention is None

    assert failure is None
    assert outputs["confirmation_message"] == "Transfer Complete!"


def test_locator_not_found_retries_up_to_max_attempts_then_fails(tmp_path):
    page = FakePage()  # target never registered -> always not found

    steps = [
        ActionStep(
            step_id=1,
            action="wait_for",
            target=ElementTarget(description="never appears", strategies=[CssLocator(selector="#ghost")]),
            retry=RetryPolicy(max_attempts=3, backoff_ms=10, retry_on=["timeout"]),
        )
    ]
    evidence = ReplayEvidenceWriter(run_id="exec-3", base_dir=tmp_path)

    outputs, failure, intervention = run_steps(page, _artifact(steps), {}, evidence)

    assert intervention is None

    assert failure is not None
    assert failure.step_id == 1
    assert failure.category == "locator_not_found"
    # backoff was applied between attempt 1->2 and 2->3, but not after the
    # final (3rd) failed attempt.
    assert page.wait_for_timeout_calls == [10, 10]


def test_wait_for_succeeds_once_element_becomes_visible_within_retries(tmp_path):
    page = FakePage()
    # Registering it up front is equivalent to "it becomes visible before
    # the first attempt" — proves the happy path within a retry-capable step.
    page.css_locators["#confirm"] = FakeLocator([FakeElement(text="Transfer Complete!", visible=True)])

    steps = [
        ActionStep(
            step_id=1,
            action="wait_for",
            target=ElementTarget(description="confirmation", strategies=[CssLocator(selector="#confirm")]),
            retry=RetryPolicy(max_attempts=3, backoff_ms=10, retry_on=["timeout"]),
        )
    ]
    evidence = ReplayEvidenceWriter(run_id="exec-4", base_dir=tmp_path)

    outputs, failure, intervention = run_steps(page, _artifact(steps), {}, evidence)

    assert intervention is None

    assert failure is None
    assert page.wait_for_timeout_calls == []


def test_locator_ambiguous_never_retries(tmp_path):
    page = FakePage()
    page.css_locators["#dup"] = FakeLocator([FakeElement(), FakeElement()])

    steps = [
        ActionStep(
            step_id=1,
            action="click",
            target=ElementTarget(description="ambiguous", strategies=[CssLocator(selector="#dup")]),
            retry=RetryPolicy(max_attempts=5, backoff_ms=10, retry_on=["timeout"]),
        )
    ]
    evidence = ReplayEvidenceWriter(run_id="exec-5", base_dir=tmp_path)

    outputs, failure, intervention = run_steps(page, _artifact(steps), {}, evidence)

    assert intervention is None

    assert failure is not None
    assert failure.category == "locator_ambiguous"
    # No backoff sleeps at all — ambiguity is never retried.
    assert page.wait_for_timeout_calls == []


# --- Milestone 6: policy gate / human handoff / transient injection ---

_THRESHOLD_POLICY = PolicyMetadata(
    allowed_domains=["example.test"],
    allowed_actions=["navigate", "click", "type", "select_option", "wait_for", "extract"],
    approval_threshold_param="amount",
    approval_threshold_value=Decimal("500"),
)


def _risky_click_step(step_id: int = 1) -> ActionStep:
    return ActionStep(
        step_id=step_id, action="click", risk="risky",
        target=ElementTarget(description="Transfer submit button", strategies=[CssLocator(selector="#submit")]),
    )


def test_disallowed_domain_blocks_before_any_dispatch(tmp_path):
    # Constructed via model_copy (bypassing pydantic validation, which
    # already prevents target_app from mismatching policy.allowed_domains
    # at load time) to prove the RUNTIME check is a real, independent
    # guard against the current page having drifted, not just relying on
    # that load-time check.
    page = FakePage()
    page.url = "https://evil.example.com/phish"
    steps = [ActionStep(step_id=1, action="navigate", url="/x")]
    artifact = _artifact(steps).model_copy(update={"target_app": "https://evil.example.com"})
    evidence = ReplayEvidenceWriter(run_id="exec-policy-1", base_dir=tmp_path)

    outputs, failure, intervention = run_steps(page, artifact, {}, evidence)

    assert intervention is None
    assert failure is not None
    assert failure.category == "policy_violation"
    assert page.goto_calls == []  # never even attempted


def test_disallowed_action_blocks_before_dispatch(tmp_path):
    policy = PolicyMetadata(allowed_domains=["example.test"], allowed_actions=["navigate", "extract"])
    page = FakePage()
    element = FakeElement(text="", tag_name="input")
    page.css_locators["#submit"] = FakeLocator([element])
    steps = [_risky_click_step()]
    evidence = ReplayEvidenceWriter(run_id="exec-policy-2", base_dir=tmp_path)

    outputs, failure, intervention = run_steps(page, _artifact(steps, policy=policy), {}, evidence)

    assert intervention is None
    assert failure is not None
    assert failure.category == "policy_violation"
    assert element.clicked is False


def test_amount_below_threshold_executes_normally_no_intervention(tmp_path):
    page = FakePage()
    element = FakeElement(text="", tag_name="input")
    page.css_locators["#submit"] = FakeLocator([element])
    steps = [_risky_click_step()]
    evidence = ReplayEvidenceWriter(run_id="exec-policy-3", base_dir=tmp_path)

    outputs, failure, intervention = run_steps(
        page, _artifact(steps, policy=_THRESHOLD_POLICY), {"amount": Decimal("20.00")}, evidence,
        operator=FakeOperator("resume"),
    )

    assert intervention is None
    assert failure is None
    assert element.clicked is True  # automation performed it itself — no handoff needed


def test_amount_above_threshold_requests_intervention_before_risky_click(tmp_path):
    page = FakePage()
    element = FakeElement(text="", tag_name="input")
    page.css_locators["#submit"] = FakeLocator([element])
    steps = [_risky_click_step()]
    operator = FakeOperator("decline")
    evidence = ReplayEvidenceWriter(run_id="exec-policy-4", base_dir=tmp_path)

    outputs, failure, intervention = run_steps(
        page, _artifact(steps, policy=_THRESHOLD_POLICY), {"amount": Decimal("600.00")}, evidence,
        operator=operator,
    )

    assert failure is None
    assert element.clicked is False  # the risky action executor was NEVER called before handoff
    assert intervention is not None
    assert intervention.decision == "declined"
    assert len(operator.calls) == 1
    assert operator.calls[0].step_id == 1
    assert operator.calls[0].context == {"param": "amount", "value": "600.00", "threshold": "500"}


def test_decline_returns_clean_structured_outcome(tmp_path):
    page = FakePage()
    page.css_locators["#submit"] = FakeLocator([FakeElement(text="", tag_name="input")])
    steps = [_risky_click_step()]
    evidence = ReplayEvidenceWriter(run_id="exec-policy-5", base_dir=tmp_path)

    outputs, failure, intervention = run_steps(
        page, _artifact(steps, policy=_THRESHOLD_POLICY), {"amount": Decimal("600.00")}, evidence,
        operator=FakeOperator("decline"),
    )

    assert failure is None
    assert intervention.decision == "declined"
    assert intervention.step_id == 1


def test_resume_without_expected_checkpoint_does_not_continue(tmp_path):
    page = FakePage()
    element = FakeElement(text="", tag_name="input")
    page.css_locators["#submit"] = FakeLocator([element])
    page.body_text = "still on the transfer form"  # human did NOT actually click Transfer
    page.css_locators["body"] = FakeLocator([FakeElement(text=page.body_text)])
    steps = [_risky_click_step()]
    artifact = _artifact(steps, policy=_THRESHOLD_POLICY).model_copy(
        update={
            "success_checkpoint": Checkpoint(
                description="d", assertion="text_contains", expected_literal_text=["Transfer Complete!"]
            )
        }
    )
    evidence = ReplayEvidenceWriter(run_id="exec-policy-6", base_dir=tmp_path)

    outputs, failure, intervention = run_steps(
        page, artifact, {"amount": Decimal("600.00")}, evidence, operator=FakeOperator("resume"),
    )

    assert failure is None
    assert element.clicked is False  # automation still never clicked it
    assert intervention is not None
    assert intervention.decision == "not_confirmed"


def test_resume_with_expected_checkpoint_skips_risky_step_and_continues(tmp_path):
    page = FakePage()
    element = FakeElement(text="", tag_name="input")
    page.css_locators["#submit"] = FakeLocator([element])
    page.body_text = "Transfer Complete! $600.00 has been transferred from account #1 to account #2."
    page.css_locators["body"] = FakeLocator([FakeElement(text=page.body_text)])
    steps = [_risky_click_step()]
    artifact = _artifact(steps, policy=_THRESHOLD_POLICY).model_copy(
        update={
            "success_checkpoint": Checkpoint(
                description="d", assertion="text_contains", expected_literal_text=["Transfer Complete!"]
            )
        }
    )
    evidence = ReplayEvidenceWriter(run_id="exec-policy-7", base_dir=tmp_path)

    outputs, failure, intervention = run_steps(
        page, artifact, {"amount": Decimal("600.00")}, evidence, operator=FakeOperator("resume"),
    )

    assert intervention is None
    assert failure is None
    assert element.clicked is False  # skipped — the human already performed it


def test_manual_capture_installed_and_disabled_around_the_blocking_wait(tmp_path):
    page = FakePage()
    page.css_locators["#submit"] = FakeLocator([FakeElement(text="", tag_name="input")])
    steps = [_risky_click_step()]
    evidence = ReplayEvidenceWriter(run_id="exec-policy-8", base_dir=tmp_path)

    run_steps(
        page, _artifact(steps, policy=_THRESHOLD_POLICY), {"amount": Decimal("600.00")}, evidence,
        operator=FakeOperator("decline"),
    )

    assert "__cuaReportManualEvent__" in page.exposed_functions
    assert any("addEventListener" in call for call in page.evaluate_calls)
    assert any("Enabled__ = false" in call for call in page.evaluate_calls)


def test_control_transitions_recorded_automation_human_automation(tmp_path):
    page = FakePage()
    page.css_locators["#submit"] = FakeLocator([FakeElement(text="", tag_name="input")])
    steps = [_risky_click_step()]
    evidence = ReplayEvidenceWriter(run_id="exec-policy-9", base_dir=tmp_path)

    run_steps(
        page, _artifact(steps, policy=_THRESHOLD_POLICY), {"amount": Decimal("600.00")}, evidence,
        operator=FakeOperator("decline"),
    )

    events = [json.loads(line) for line in evidence.events_path.read_text().splitlines()]
    transitions = [e["control_owner"] for e in events if e["action"] == "control_transition"]
    assert transitions == ["human", "automation"]


def test_manual_event_evidence_recorded_without_input_values(tmp_path):
    page = FakePage()
    page.css_locators["#submit"] = FakeLocator([FakeElement(text="", tag_name="input")])
    steps = [_risky_click_step()]
    evidence = ReplayEvidenceWriter(run_id="exec-policy-10", base_dir=tmp_path)

    class ClickingOperator:
        def request_intervention(self, request):
            # Simulate the human clicking Transfer: invoke the exposed
            # callback exactly as the browser-side JS would.
            page.exposed_functions["__cuaReportManualEvent__"](
                {"type": "click", "tag": "input", "id": None, "name": None, "text": "Transfer"}
            )
            return "decline"

    run_steps(
        page, _artifact(steps, policy=_THRESHOLD_POLICY), {"amount": Decimal("600.00")}, evidence,
        operator=ClickingOperator(),
    )

    events = [json.loads(line) for line in evidence.events_path.read_text().splitlines()]
    manual_events = [e for e in events if e["action"] == "manual_event"]
    assert len(manual_events) == 1
    assert manual_events[0]["event_type"] == "click"
    assert manual_events[0]["text"] == "Transfer"
    assert manual_events[0]["control_owner"] == "human"
    # no field anywhere carries a typed/selected value
    assert "value" not in manual_events[0]


def test_policy_evaluated_and_logged_for_every_step(tmp_path):
    page = FakePage()
    page.css_locators["#submit"] = FakeLocator([FakeElement(text="", tag_name="input")])
    steps = [_risky_click_step()]
    evidence = ReplayEvidenceWriter(run_id="exec-policy-11", base_dir=tmp_path)

    run_steps(page, _artifact(steps, policy=_THRESHOLD_POLICY), {"amount": Decimal("600.00")}, evidence,
              operator=FakeOperator("decline"))

    events = [json.loads(line) for line in evidence.events_path.read_text().splitlines()]
    policy_events = [e for e in events if e["action"] == "policy_check"]
    assert len(policy_events) >= 1
    assert policy_events[0]["outcome"] == "approval_required"


def test_injected_transient_condition_then_real_success(tmp_path):
    page = FakePage()
    page.css_locators["#confirm"] = FakeLocator([FakeElement(text="Transfer Complete!", visible=True)])
    steps = [
        ActionStep(
            step_id=1, action="wait_for",
            target=ElementTarget(description="confirmation", strategies=[CssLocator(selector="#confirm")]),
            retry=RetryPolicy(max_attempts=3, backoff_ms=10, retry_on=["timeout"]),
        )
    ]
    evidence = ReplayEvidenceWriter(run_id="exec-inject-1", base_dir=tmp_path)

    outputs, failure, intervention = run_steps(
        page, _artifact(steps), {}, evidence, transient_injector=FirstEligibleStepOnceInjector(),
    )

    assert intervention is None
    assert failure is None
    assert page.wait_for_timeout_calls == [10]  # exactly one backoff: injected attempt 1, real attempt 2

    events = [json.loads(line) for line in evidence.events_path.read_text().splitlines()]
    wait_events = [e for e in events if e["action"] == "wait_for"]
    assert [e["outcome"] for e in wait_events] == ["injected_transient", "ok"]


def test_injection_never_affects_a_risky_step(tmp_path):
    page = FakePage()
    element = FakeElement(text="", tag_name="input")
    page.css_locators["#submit"] = FakeLocator([element])
    steps = [
        ActionStep(
            step_id=1, action="click", risk="risky",
            target=ElementTarget(description="Transfer submit button", strategies=[CssLocator(selector="#submit")]),
            retry=RetryPolicy(max_attempts=3, backoff_ms=10, retry_on=["timeout"]),
        )
    ]
    evidence = ReplayEvidenceWriter(run_id="exec-inject-2", base_dir=tmp_path)

    outputs, failure, intervention = run_steps(
        page, _artifact(steps), {"amount": Decimal("20.00")}, evidence,
        operator=FakeOperator("resume"), transient_injector=FirstEligibleStepOnceInjector(),
    )

    assert intervention is None
    assert failure is None
    assert element.clicked is True
    assert page.wait_for_timeout_calls == []  # injector skipped the risky step entirely


def test_injected_retry_exhaustion_still_fails_cleanly(tmp_path):
    page = FakePage()  # target never registered -> the real attempt(s) fail too
    steps = [
        ActionStep(
            step_id=1, action="wait_for",
            target=ElementTarget(description="never appears", strategies=[CssLocator(selector="#ghost")]),
            retry=RetryPolicy(max_attempts=2, backoff_ms=10, retry_on=["timeout"]),
        )
    ]
    evidence = ReplayEvidenceWriter(run_id="exec-inject-4", base_dir=tmp_path)

    outputs, failure, intervention = run_steps(
        page, _artifact(steps), {}, evidence, transient_injector=FirstEligibleStepOnceInjector(),
    )

    assert intervention is None
    assert failure is not None
    assert failure.category == "locator_not_found"

    events = [json.loads(line) for line in evidence.events_path.read_text().splitlines()]
    wait_events = [e for e in events if e["action"] == "wait_for"]
    # attempt 1: injected; attempt 2: a REAL attempt that also genuinely
    # fails (target never registered) -> exhausted, clean structured failure.
    assert [e["outcome"] for e in wait_events] == ["injected_transient", "failed"]


def test_injection_disabled_by_default_when_no_injector_given(tmp_path):
    page = FakePage()
    page.css_locators["#confirm"] = FakeLocator([FakeElement(text="Transfer Complete!", visible=True)])
    steps = [
        ActionStep(
            step_id=1, action="wait_for",
            target=ElementTarget(description="confirmation", strategies=[CssLocator(selector="#confirm")]),
            retry=RetryPolicy(max_attempts=3, backoff_ms=10, retry_on=["timeout"]),
        )
    ]
    evidence = ReplayEvidenceWriter(run_id="exec-inject-5", base_dir=tmp_path)

    outputs, failure, intervention = run_steps(page, _artifact(steps), {}, evidence)  # no transient_injector passed

    assert intervention is None
    assert failure is None
    assert page.wait_for_timeout_calls == []  # no injected failure at all — disabled by default
