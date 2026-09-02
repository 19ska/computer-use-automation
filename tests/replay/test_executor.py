"""Tests for the generic action executor (cua.replay.executor)."""

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
from cua.replay.executor import run_steps

from .fakes import FakeElement, FakeLocator, FakePage


def _artifact(steps: list[ActionStep], outputs: list[OutputField] | None = None) -> CapabilityArtifact:
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
        policy=PolicyMetadata(allowed_domains=["example.test"], allowed_actions=["navigate", "type", "extract"]),
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

    outputs, failure = run_steps(page, _artifact(steps), resolved_inputs, evidence)

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

    outputs, failure = run_steps(page, _artifact(steps, [output]), {}, evidence)

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

    outputs, failure = run_steps(page, _artifact(steps), {}, evidence)

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

    outputs, failure = run_steps(page, _artifact(steps), {}, evidence)

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

    outputs, failure = run_steps(page, _artifact(steps), {}, evidence)

    assert failure is not None
    assert failure.category == "locator_ambiguous"
    # No backoff sleeps at all — ambiguity is never retried.
    assert page.wait_for_timeout_calls == []
