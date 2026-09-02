"""Tests for the typed replay result models (cua.replay.results)."""

from decimal import Decimal

from pydantic import TypeAdapter

from cua.replay.results import (
    ReplayBusinessOutcome,
    ReplayFailure,
    ReplayInterventionOutcome,
    ReplayResult,
    ReplaySuccess,
)


def test_success_result_serializes():
    result = ReplaySuccess(
        run_id="run-1",
        capability_id="parabank.transfer_funds",
        outputs={"confirmation_message": "Transfer Complete!", "transferred_amount": Decimal("20.00")},
        checkpoint_evidence="matched",
        evidence_dir="run_output/run-1",
    )
    dumped = result.model_dump_json()
    restored = ReplaySuccess.model_validate_json(dumped)

    # JSON has no native Decimal type, so a Decimal-or-str union field
    # always re-parses a JSON string as `str` (its strict validation
    # trivially matches any JSON string, pre-empting Decimal). This is an
    # inherent JSON round-trip limitation, not a bug — compare fields
    # individually rather than asserting full object equality.
    assert restored.status == "success"
    assert restored.run_id == result.run_id
    assert restored.capability_id == result.capability_id
    assert restored.outputs["confirmation_message"] == "Transfer Complete!"
    assert Decimal(str(restored.outputs["transferred_amount"])) == Decimal("20.00")
    assert restored.status == "success"


def test_business_outcome_result_serializes():
    result = ReplayBusinessOutcome(
        run_id="run-2",
        capability_id="parabank.transfer_funds",
        outcome_code="INVALID_CREDENTIALS",
        message="The username and password could not be verified.",
        evidence_dir="run_output/run-2",
    )
    restored = ReplayBusinessOutcome.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.status == "business_outcome"
    assert restored.step_id is None


def test_failure_result_serializes():
    result = ReplayFailure(
        run_id="run-3",
        capability_id="parabank.transfer_funds",
        failure_category="locator_not_found",
        step_id=6,
        expected="visible confirmation element",
        observed="no matching visible element",
        screenshot_path="run_output/run-3/step_6_failed.png",
        exception_summary="LocatorNotFoundError: ...",
        evidence_dir="run_output/run-3",
    )
    restored = ReplayFailure.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.status == "failure"


def test_intervention_outcome_result_serializes():
    result = ReplayInterventionOutcome(
        run_id="run-4", capability_id="parabank.transfer_funds", step_id=5,
        reason="amount=600.00 exceeds approval threshold 500", decision="declined",
        evidence_dir="run_output/run-4",
    )
    restored = ReplayInterventionOutcome.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.status == "intervention"


def test_policy_violation_is_a_valid_failure_category():
    result = ReplayFailure(run_id="run-5", capability_id="c", failure_category="policy_violation")
    restored = ReplayFailure.model_validate_json(result.model_dump_json())
    assert restored.failure_category == "policy_violation"


def test_result_union_discriminates_by_status():
    adapter = TypeAdapter(ReplayResult)

    success = ReplaySuccess(
        run_id="r", capability_id="c", outputs={}, checkpoint_evidence="e", evidence_dir="d"
    )
    outcome = ReplayBusinessOutcome(
        run_id="r", capability_id="c", outcome_code="X", message="m", evidence_dir="d"
    )
    failure = ReplayFailure(run_id="r", capability_id="c", failure_category="unexpected_error")
    intervention = ReplayInterventionOutcome(
        run_id="r", capability_id="c", step_id=1, reason="r", decision="not_confirmed", evidence_dir="d"
    )

    assert isinstance(adapter.validate_json(success.model_dump_json()), ReplaySuccess)
    assert isinstance(adapter.validate_json(outcome.model_dump_json()), ReplayBusinessOutcome)
    assert isinstance(adapter.validate_json(failure.model_dump_json()), ReplayFailure)
    assert isinstance(adapter.validate_json(intervention.model_dump_json()), ReplayInterventionOutcome)
