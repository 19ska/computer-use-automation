"""Tests for cua.replay.policy — the deterministic replay-time policy gate."""

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
from cua.replay.policy import check_policy

from .fakes import FakePage


def _artifact(*, policy: PolicyMetadata, steps: list[ActionStep]) -> CapabilityArtifact:
    output = OutputField(
        name="out", type="string", description="d",
        extraction=ExtractionSpec(target=ElementTarget(description="body", strategies=[CssLocator(selector="body")]), source="text"),
    )
    next_step_id = max((s.step_id for s in steps), default=0) + 1
    steps = [*steps, ActionStep(step_id=next_step_id, action="extract", output_ref="out")]
    return CapabilityArtifact(
        capability_id="test.policy_capability",
        capability_version=1,
        display_name="d", description="d",
        target_app="https://parabank.parasoft.com/parabank",
        session_requirement=SessionRequirement(authenticated=False),
        inputs=[InputParameter(name="amount", type="decimal", description="d")],
        outputs=[output],
        steps=steps,
        success_checkpoint=Checkpoint(description="d", assertion="text_contains", expected_literal_text=["ok"]),
        policy=policy,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        created_by="human_edited",
        discovery_run_id="test-run",
    )


_ALLOWED_POLICY = PolicyMetadata(
    allowed_domains=["parabank.parasoft.com"],
    allowed_actions=["navigate", "click", "type", "select_option", "wait_for", "extract"],
)


def test_allowed_domain_passes():
    page = FakePage()
    page.url = "https://parabank.parasoft.com/parabank/transfer.htm"
    step = ActionStep(step_id=1, action="navigate", url="/transfer.htm")
    decision = check_policy(page, step, _artifact(policy=_ALLOWED_POLICY, steps=[step]), {})
    assert decision.allowed is True
    assert decision.requires_approval is False


def test_disallowed_navigation_destination_is_blocked():
    policy = PolicyMetadata(allowed_domains=["parabank.parasoft.com"], allowed_actions=["navigate"])
    page = FakePage()
    page.url = "https://parabank.parasoft.com/parabank/overview.htm"
    # target_app itself would need to be evil.example.com for this to be
    # reachable in a real artifact (schema validates target_app against
    # allowed_domains at load time) — this proves the runtime check is a
    # real independent guard, not just relying on that load-time check.
    step = ActionStep(step_id=1, action="navigate", url="/leave")
    artifact = _artifact(policy=policy, steps=[step]).model_copy(
        update={"target_app": "https://evil.example.com"}
    )
    decision = check_policy(page, step, artifact, {})
    assert decision.allowed is False
    assert "evil.example.com" in decision.reason


def test_current_page_drifted_to_unapproved_origin_blocks_interacting_actions():
    step = ActionStep(
        step_id=1, action="click",
        target=ElementTarget(description="x", strategies=[CssLocator(selector="#x")]),
    )
    page = FakePage()
    page.url = "https://evil.example.com/phish"
    decision = check_policy(page, step, _artifact(policy=_ALLOWED_POLICY, steps=[step]), {})
    assert decision.allowed is False
    assert "evil.example.com" in decision.reason


def test_current_page_drifted_blocks_navigate_too():
    step = ActionStep(step_id=1, action="navigate", url="/transfer.htm")
    page = FakePage()
    page.url = "https://evil.example.com/phish"
    decision = check_policy(page, step, _artifact(policy=_ALLOWED_POLICY, steps=[step]), {})
    assert decision.allowed is False


def test_fresh_blank_page_does_not_block_first_navigate():
    step = ActionStep(step_id=1, action="navigate", url="/index.htm")
    page = FakePage()
    page.url = ""  # e.g. about:blank has no meaningful host
    decision = check_policy(page, step, _artifact(policy=_ALLOWED_POLICY, steps=[step]), {})
    assert decision.allowed is True


def test_exact_domain_match_only_no_subdomain_leniency():
    step = ActionStep(step_id=1, action="navigate", url="/x")
    page = FakePage()
    page.url = "https://sub.parabank.parasoft.com/x"
    decision = check_policy(page, step, _artifact(policy=_ALLOWED_POLICY, steps=[step]), {})
    assert decision.allowed is False


def test_disallowed_action_type_is_blocked():
    policy = PolicyMetadata(allowed_domains=["parabank.parasoft.com"], allowed_actions=["navigate"])
    step = ActionStep(
        step_id=1, action="click",
        target=ElementTarget(description="x", strategies=[CssLocator(selector="#x")]),
    )
    page = FakePage()
    page.url = "https://parabank.parasoft.com/parabank/x"
    decision = check_policy(page, step, _artifact(policy=policy, steps=[step]), {})
    assert decision.allowed is False
    assert "click" in decision.reason


def test_allowed_action_type_passes():
    step = ActionStep(
        step_id=1, action="click",
        target=ElementTarget(description="x", strategies=[CssLocator(selector="#x")]),
    )
    page = FakePage()
    page.url = "https://parabank.parasoft.com/parabank/x"
    decision = check_policy(page, step, _artifact(policy=_ALLOWED_POLICY, steps=[step]), {})
    assert decision.allowed is True


def test_amount_below_threshold_does_not_require_approval():
    policy = _ALLOWED_POLICY.model_copy(
        update={"approval_threshold_param": "amount", "approval_threshold_value": Decimal("500")}
    )
    step = ActionStep(
        step_id=1, action="click", risk="risky",
        target=ElementTarget(description="Transfer", strategies=[CssLocator(selector="#submit")]),
    )
    page = FakePage()
    page.url = "https://parabank.parasoft.com/parabank/x"
    decision = check_policy(page, step, _artifact(policy=policy, steps=[step]), {"amount": Decimal("20.00")})
    assert decision.allowed is True
    assert decision.requires_approval is False


def test_amount_above_threshold_requires_approval():
    policy = _ALLOWED_POLICY.model_copy(
        update={"approval_threshold_param": "amount", "approval_threshold_value": Decimal("500")}
    )
    step = ActionStep(
        step_id=1, action="click", risk="risky",
        target=ElementTarget(description="Transfer", strategies=[CssLocator(selector="#submit")]),
    )
    page = FakePage()
    page.url = "https://parabank.parasoft.com/parabank/x"
    decision = check_policy(page, step, _artifact(policy=policy, steps=[step]), {"amount": Decimal("600.00")})
    assert decision.allowed is True
    assert decision.requires_approval is True
    assert decision.threshold_context == {"param": "amount", "value": "600.00", "threshold": "500"}


def test_safe_step_never_requires_approval_regardless_of_amount():
    policy = _ALLOWED_POLICY.model_copy(
        update={"approval_threshold_param": "amount", "approval_threshold_value": Decimal("500")}
    )
    step = ActionStep(
        step_id=1, action="click",  # risk defaults to "safe"
        target=ElementTarget(description="Something else", strategies=[CssLocator(selector="#x")]),
    )
    page = FakePage()
    page.url = "https://parabank.parasoft.com/parabank/x"
    decision = check_policy(page, step, _artifact(policy=policy, steps=[step]), {"amount": Decimal("999999")})
    assert decision.requires_approval is False
