"""Validation tests for the generic capability artifact schema (Milestone 2).

These tests use synthetic, non-ParaBank data to exercise the SCHEMA's rules
in isolation. The real ParaBank fixture is validated separately in
tests/test_example_artifact_fixture.py.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from cua.artifact import (
    ActionStep,
    BusinessOutcomeDetector,
    CapabilityArtifact,
    Checkpoint,
    CssLocator,
    ElementTarget,
    ExtractionSpec,
    InputParameter,
    LabelTextLocator,
    LiteralRef,
    OutputField,
    ParamRef,
    PolicyMetadata,
    RetryPolicy,
    RoleLocator,
    SessionRequirement,
)


def _target(selector: str = "#thing", description: str = "a thing") -> ElementTarget:
    return ElementTarget(description=description, strategies=[CssLocator(selector=selector)])


def _extraction(selector: str = "#result") -> ExtractionSpec:
    return ExtractionSpec(target=_target(selector, "result"), source="text")


def _minimal_kwargs(**overrides) -> dict:
    kwargs = dict(
        capability_id="test.capability",
        capability_version=1,
        display_name="Test Capability",
        description="A minimal artifact for schema testing.",
        target_app="https://example.test/app",
        session_requirement=SessionRequirement(authenticated=True, auth_profile="test_profile"),
        inputs=[
            InputParameter(name="amount", type="decimal", description="An amount."),
        ],
        outputs=[
            OutputField(
                name="result_text", type="string", description="Result.", extraction=_extraction()
            ),
        ],
        steps=[
            ActionStep(step_id=1, action="navigate", url="/start"),
            ActionStep(
                step_id=2,
                action="type",
                target=_target("#amount"),
                value=ParamRef(name="amount"),
            ),
            ActionStep(step_id=3, action="extract", output_ref="result_text"),
        ],
        success_checkpoint=Checkpoint(
            description="Done.",
            assertion="text_contains",
            expected_literal_text=["Done!"],
        ),
        business_outcomes=[],
        policy=PolicyMetadata(
            allowed_domains=["example.test"],
            allowed_actions=["navigate", "type", "extract"],
        ),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        created_by="human_edited",
        discovery_run_id="test-run-1",
    )
    kwargs.update(overrides)
    return kwargs


def build_artifact(**overrides) -> CapabilityArtifact:
    return CapabilityArtifact(**_minimal_kwargs(**overrides))


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------


def test_minimal_valid_artifact_constructs():
    artifact = build_artifact()
    assert artifact.capability_id == "test.capability"


def test_json_round_trip():
    artifact = build_artifact()
    dumped = artifact.model_dump_json()
    restored = CapabilityArtifact.model_validate_json(dumped)
    assert restored == artifact


def test_locator_strategy_round_trip_for_each_kind():
    target = ElementTarget(
        description="multi-strategy",
        strategies=[
            RoleLocator(role="button", name="Submit"),
            LabelTextLocator(text="Submit"),
            CssLocator(selector="button[type=submit]"),
        ],
    )
    restored = ElementTarget.model_validate_json(target.model_dump_json())
    assert restored == target
    assert [s.kind for s in restored.strategies] == ["role", "label_text", "css"]


def test_value_ref_round_trip_for_each_kind():
    step_param = ActionStep(
        step_id=1, action="type", target=_target(), value=ParamRef(name="amount")
    )
    step_literal = ActionStep(
        step_id=2, action="type", target=_target(), value=LiteralRef(value="SAVINGS")
    )
    for step in (step_param, step_literal):
        restored = ActionStep.model_validate_json(step.model_dump_json())
        assert restored == step


# --------------------------------------------------------------------------
# Uniqueness rules
# --------------------------------------------------------------------------


def test_duplicate_input_names_rejected():
    with pytest.raises(ValidationError, match="input names must be unique"):
        build_artifact(
            inputs=[
                InputParameter(name="amount", type="decimal", description="a"),
                InputParameter(name="amount", type="string", description="b"),
            ]
        )


def test_duplicate_output_names_rejected():
    with pytest.raises(ValidationError, match="output names must be unique"):
        build_artifact(
            outputs=[
                OutputField(name="x", type="string", description="a", extraction=_extraction()),
                OutputField(name="x", type="string", description="b", extraction=_extraction()),
            ],
            steps=[
                ActionStep(step_id=1, action="navigate", url="/start"),
                ActionStep(step_id=2, action="extract", output_ref="x"),
            ],
        )


def test_duplicate_step_ids_rejected():
    with pytest.raises(ValidationError, match="step_id values must be unique"):
        build_artifact(
            steps=[
                ActionStep(step_id=1, action="navigate", url="/start"),
                ActionStep(step_id=1, action="extract", output_ref="result_text"),
            ]
        )


def test_duplicate_business_outcome_codes_rejected():
    outcome = BusinessOutcomeDetector(
        code="SOME_OUTCOME", description="d", target=_target(), contains_text="oops"
    )
    with pytest.raises(ValidationError, match="business_outcomes codes must be unique"):
        build_artifact(business_outcomes=[outcome, outcome])


def test_business_outcome_code_must_be_upper_snake_case():
    with pytest.raises(ValidationError):
        BusinessOutcomeDetector(
            code="not_upper", description="d", target=_target(), contains_text="oops"
        )


def test_business_outcome_origin_defaults_to_capability_execution():
    outcome = BusinessOutcomeDetector(
        code="SOME_OUTCOME", description="d", target=_target(), contains_text="oops"
    )
    assert outcome.origin == "capability_execution"


def test_business_outcome_origin_accepts_session_establishment():
    outcome = BusinessOutcomeDetector(
        code="INVALID_CREDENTIALS",
        description="d",
        target=_target(),
        contains_text="nope",
        origin="session_establishment",
    )
    assert outcome.origin == "session_establishment"


# --------------------------------------------------------------------------
# ActionStep per-action field rules
# --------------------------------------------------------------------------


def test_navigate_requires_url():
    with pytest.raises(ValidationError, match="require 'url'"):
        ActionStep(step_id=1, action="navigate")


def test_navigate_forbids_target():
    with pytest.raises(ValidationError, match="must only set 'url'"):
        ActionStep(step_id=1, action="navigate", url="/x", target=_target())


def test_click_requires_target():
    with pytest.raises(ValidationError, match="require 'target'"):
        ActionStep(step_id=1, action="click")


def test_type_requires_value():
    with pytest.raises(ValidationError, match="require both 'target' and 'value'"):
        ActionStep(step_id=1, action="type", target=_target())


def test_select_option_requires_value():
    with pytest.raises(ValidationError, match="require both 'target' and 'value'"):
        ActionStep(step_id=1, action="select_option", target=_target())


def test_extract_requires_output_ref():
    with pytest.raises(ValidationError, match="require 'output_ref'"):
        ActionStep(step_id=1, action="extract")


def test_extract_forbids_target():
    with pytest.raises(ValidationError, match="must only set 'output_ref'"):
        ActionStep(step_id=1, action="extract", output_ref="x", target=_target())


def test_extract_forbids_value():
    with pytest.raises(ValidationError, match="must only set 'output_ref'"):
        ActionStep(step_id=1, action="extract", output_ref="x", value=ParamRef(name="amount"))


def test_output_ref_only_valid_on_extract():
    with pytest.raises(ValidationError):
        ActionStep(step_id=1, action="click", target=_target(), output_ref="x")


# --------------------------------------------------------------------------
# extract <-> output linkage
# --------------------------------------------------------------------------


def test_extract_step_referencing_unknown_output_rejected():
    with pytest.raises(ValidationError, match="unknown outputs"):
        build_artifact(
            steps=[
                ActionStep(step_id=1, action="navigate", url="/start"),
                ActionStep(step_id=2, action="extract", output_ref="does_not_exist"),
            ]
        )


def test_output_with_no_extract_step_rejected():
    with pytest.raises(ValidationError, match="no extract step"):
        build_artifact(steps=[ActionStep(step_id=1, action="navigate", url="/start")])


def test_output_extracted_by_more_than_one_step_rejected():
    with pytest.raises(ValidationError, match="more than one step"):
        build_artifact(
            steps=[
                ActionStep(step_id=1, action="navigate", url="/start"),
                ActionStep(step_id=2, action="extract", output_ref="result_text"),
                ActionStep(step_id=3, action="extract", output_ref="result_text"),
            ]
        )


# --------------------------------------------------------------------------
# Checkpoint field rules
# --------------------------------------------------------------------------


def test_checkpoint_url_matches_requires_pattern():
    with pytest.raises(ValidationError, match="require 'expected_url_pattern'"):
        Checkpoint(description="d", assertion="url_matches")


def test_checkpoint_element_visible_requires_target():
    with pytest.raises(ValidationError, match="require 'target'"):
        Checkpoint(description="d", assertion="element_visible")


def test_checkpoint_text_contains_requires_text_or_refs():
    with pytest.raises(ValidationError, match="expected_literal_text"):
        Checkpoint(description="d", assertion="text_contains")


# --------------------------------------------------------------------------
# ExtractionSpec rules
# --------------------------------------------------------------------------


def test_extraction_attribute_requires_attribute_name():
    with pytest.raises(ValidationError, match="requires 'attribute_name'"):
        ExtractionSpec(target=_target(), source="attribute")


def test_extraction_attribute_name_only_valid_with_attribute_source():
    with pytest.raises(ValidationError, match="only valid when source='attribute'"):
        ExtractionSpec(target=_target(), source="text", attribute_name="href")


def test_extraction_pattern_must_be_non_empty():
    with pytest.raises(ValidationError, match="non-empty"):
        ExtractionSpec(target=_target(), pattern="   ", capture_group=0)


def test_extraction_pattern_must_be_valid_regex():
    with pytest.raises(ValidationError, match="not a valid regex"):
        ExtractionSpec(target=_target(), pattern="(unclosed", capture_group=0)


def test_extraction_capture_group_required_with_pattern():
    with pytest.raises(ValidationError, match="capture_group is required"):
        ExtractionSpec(target=_target(), pattern=r"(\d+)")


def test_extraction_capture_group_out_of_range_rejected():
    with pytest.raises(ValidationError, match="out of range"):
        ExtractionSpec(target=_target(), pattern=r"(\d+)", capture_group=5)


def test_extraction_capture_group_without_pattern_rejected():
    with pytest.raises(ValidationError, match="requires 'pattern'"):
        ExtractionSpec(target=_target(), capture_group=0)


# --------------------------------------------------------------------------
# ParamRef resolution
# --------------------------------------------------------------------------


def test_param_ref_must_resolve_to_declared_input():
    with pytest.raises(ValidationError, match="unknown input parameter"):
        build_artifact(
            steps=[
                ActionStep(step_id=1, action="navigate", url="/start"),
                ActionStep(
                    step_id=2,
                    action="type",
                    target=_target(),
                    value=ParamRef(name="does_not_exist"),
                ),
                ActionStep(step_id=3, action="extract", output_ref="result_text"),
            ]
        )


def test_param_ref_in_checkpoint_must_resolve():
    with pytest.raises(ValidationError, match="unknown input parameter"):
        build_artifact(
            success_checkpoint=Checkpoint(
                description="d",
                assertion="text_contains",
                expected_value_refs=[ParamRef(name="ghost")],
            )
        )


# --------------------------------------------------------------------------
# Policy rules
# --------------------------------------------------------------------------


def test_policy_threshold_param_and_value_must_be_set_together():
    with pytest.raises(ValidationError, match="must be set together"):
        PolicyMetadata(
            allowed_domains=["x.test"],
            allowed_actions=["navigate"],
            approval_threshold_param="amount",
        )


def test_target_app_domain_must_be_allowed():
    with pytest.raises(ValidationError, match="not in policy.allowed_domains"):
        build_artifact(
            policy=PolicyMetadata(
                allowed_domains=["somewhere-else.test"],
                allowed_actions=["navigate", "type", "extract"],
            )
        )


# --------------------------------------------------------------------------
# SessionRequirement
# --------------------------------------------------------------------------


def test_session_requirement_auth_profile_accepts_plain_slug():
    req = SessionRequirement(authenticated=True, auth_profile="parabank_demo")
    assert req.auth_profile == "parabank_demo"


@pytest.mark.parametrize(
    "bad_profile",
    ["user@example.com", "Has Spaces", "UPPERCASE", "with-dash", "s3cr3t!token", ""],
)
def test_session_requirement_auth_profile_rejects_non_slug_values(bad_profile):
    with pytest.raises(ValidationError):
        SessionRequirement(authenticated=True, auth_profile=bad_profile)


def test_session_requirement_auth_profile_optional():
    req = SessionRequirement(authenticated=False)
    assert req.auth_profile is None


# --------------------------------------------------------------------------
# RetryPolicy bounds
# --------------------------------------------------------------------------


def test_retry_policy_default():
    policy = RetryPolicy()
    assert policy.max_attempts == 1
    assert policy.backoff_ms == 0
    assert policy.retry_on == []


def test_retry_policy_max_attempts_must_be_at_least_one():
    with pytest.raises(ValidationError):
        RetryPolicy(max_attempts=0)


# --------------------------------------------------------------------------
# LiteralRef — structural, non-content-based contract (see schema.py docstring)
# --------------------------------------------------------------------------


def test_literal_ref_allows_genuinely_non_sensitive_fixed_values():
    ref = LiteralRef(value="SAVINGS")
    assert ref.value == "SAVINGS"
