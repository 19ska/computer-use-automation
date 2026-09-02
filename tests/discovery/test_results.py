"""Tests for typed discovery result models (cua.discovery.results)."""

from pydantic import TypeAdapter

from cua.discovery.results import (
    DiscoveryBusinessOutcome,
    DiscoveryFailure,
    DiscoveryResult,
    DiscoverySuccess,
)


def test_success_result_serializes_and_records_provider_and_model():
    result = DiscoverySuccess(
        run_id="r1",
        goal="Transfer 20.00 from account 15009 to account 15120.",
        declared_parameters={"amount": "20.00", "from_account_id": "15009", "to_account_id": "15120"},
        final_checkpoint_evidence="Transfer Complete! ...",
        evidence_dir="discovery_output/r1",
        step_count=8,
        provider="gemini",
        model="gemini-2.5-flash",
    )
    restored = DiscoverySuccess.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.provider == "gemini"
    assert restored.model == "gemini-2.5-flash"


def test_business_outcome_result_serializes():
    result = DiscoveryBusinessOutcome(
        run_id="r2", outcome_code="INVALID_CREDENTIALS",
        message="The username and password could not be verified.",
        evidence_dir="discovery_output/r2", provider="gemini", model="gemini-2.5-flash",
    )
    restored = DiscoveryBusinessOutcome.model_validate_json(result.model_dump_json())
    assert restored == result


def test_failure_result_serializes():
    result = DiscoveryFailure(
        run_id="r3", failure_category="llm_api_error", last_step=15,
        reason="simulated Gemini API failure", screenshot_path="discovery_output/r3/screenshots/x.png",
        evidence_dir="discovery_output/r3", provider="gemini", model="gemini-2.5-flash",
    )
    restored = DiscoveryFailure.model_validate_json(result.model_dump_json())
    assert restored == result


def test_result_union_discriminates_by_status():
    adapter = TypeAdapter(DiscoveryResult)
    success = DiscoverySuccess(
        run_id="r", goal="g", declared_parameters={}, final_checkpoint_evidence="e",
        evidence_dir="d", step_count=1, provider="gemini", model="m",
    )
    outcome = DiscoveryBusinessOutcome(run_id="r", outcome_code="X", message="m", evidence_dir="d")
    failure = DiscoveryFailure(run_id="r", failure_category="give_up", reason="r")

    assert isinstance(adapter.validate_json(success.model_dump_json()), DiscoverySuccess)
    assert isinstance(adapter.validate_json(outcome.model_dump_json()), DiscoveryBusinessOutcome)
    assert isinstance(adapter.validate_json(failure.model_dump_json()), DiscoveryFailure)
