"""Tests for cua.replay.engine._run_against_page — exercises the
orchestration layer directly with fakes, so no real browser/artifact
file is needed. Confirms the new ReplayInterventionOutcome conversion
and that the existing below-threshold path is completely unaffected.
"""

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
    PolicyMetadata,
    SessionRequirement,
)
from cua.replay import engine
from cua.replay.evidence import ReplayEvidenceWriter

from .fakes import FakeElement, FakeLocator, FakePage
from .test_executor import FakeOperator


def _artifact(*, risky_amount_threshold: Decimal | None = None) -> CapabilityArtifact:
    output = OutputField(
        name="confirmation_message", type="string", description="d",
        extraction=ExtractionSpec(
            target=ElementTarget(description="body", strategies=[CssLocator(selector="body")]), source="text"
        ),
    )
    policy = PolicyMetadata(
        allowed_domains=["example.test"],
        allowed_actions=["navigate", "click", "type", "select_option", "wait_for", "extract"],
        approval_threshold_param="amount" if risky_amount_threshold else None,
        approval_threshold_value=risky_amount_threshold,
    )
    steps = [
        ActionStep(
            step_id=1, action="click", risk="risky" if risky_amount_threshold else "safe",
            target=ElementTarget(description="Transfer submit button", strategies=[CssLocator(selector="#submit")]),
        ),
        ActionStep(step_id=2, action="extract", output_ref="confirmation_message"),
    ]
    return CapabilityArtifact(
        capability_id="test.engine_capability",
        capability_version=1,
        display_name="d", description="d",
        target_app="https://example.test/app",
        session_requirement=SessionRequirement(authenticated=False),
        inputs=[InputParameter(name="amount", type="decimal", description="d")],
        outputs=[output],
        steps=steps,
        success_checkpoint=Checkpoint(description="d", assertion="text_contains", expected_literal_text=["Done!"]),
        policy=policy,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        created_by="human_edited",
        discovery_run_id="test-run",
    )


def _page_with_confirmation(text: str) -> FakePage:
    page = FakePage()
    page.url = "https://example.test/app"
    page.css_locators["#submit"] = FakeLocator([FakeElement(text="", tag_name="input")])
    page.css_locators["body"] = FakeLocator([FakeElement(text=text)])
    page.body_text = text
    return page


def test_below_threshold_replay_still_returns_success_unaffected(tmp_path):
    page = _page_with_confirmation("Done!")
    evidence = ReplayEvidenceWriter(run_id="engine-1", base_dir=tmp_path)

    result = engine._run_against_page(
        page, _artifact(risky_amount_threshold=None), {"amount": Decimal("20.00")}, "engine-1", evidence,
        operator=FakeOperator("resume"), transient_injector=None,
    )

    assert result.status == "success"


def test_declined_intervention_surfaces_as_intervention_outcome(tmp_path):
    page = _page_with_confirmation("still on the form")
    evidence = ReplayEvidenceWriter(run_id="engine-2", base_dir=tmp_path)

    result = engine._run_against_page(
        page, _artifact(risky_amount_threshold=Decimal("500")), {"amount": Decimal("600.00")}, "engine-2", evidence,
        operator=FakeOperator("decline"), transient_injector=None,
    )

    assert result.status == "intervention"
    assert result.decision == "declined"
    assert result.step_id == 1
    assert result.evidence_dir == str(evidence.run_dir)


def test_resume_with_confirmed_checkpoint_reaches_success(tmp_path):
    page = _page_with_confirmation("Done!")
    evidence = ReplayEvidenceWriter(run_id="engine-3", base_dir=tmp_path)

    result = engine._run_against_page(
        page, _artifact(risky_amount_threshold=Decimal("500")), {"amount": Decimal("600.00")}, "engine-3", evidence,
        operator=FakeOperator("resume"), transient_injector=None,
    )

    assert result.status == "success"
