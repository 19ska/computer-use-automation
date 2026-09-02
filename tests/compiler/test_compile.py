"""Tests for cua.compiler.compile — the full pipeline, end to end,
against synthetic evidence."""

import json

import pytest

from cua.artifact.schema import CapabilityArtifact
from cua.compiler.compile import compile_and_write, compile_artifact
from cua.compiler.events import CompilationError
from cua.compiler.templates import PARABANK_TRANSFER_FUNDS_TEMPLATE, get_template

from .fakes import build_incomplete_run, build_run_with_corrections_and_failures, build_successful_transfer_run


def test_compile_artifact_produces_a_valid_capability_artifact(tmp_path):
    run_dir = build_successful_transfer_run(tmp_path)
    artifact, discovered_count = compile_artifact(run_dir, PARABANK_TRANSFER_FUNDS_TEMPLATE)

    assert discovered_count == 5  # navigate, select, select, type, click
    assert isinstance(artifact, CapabilityArtifact)
    # re-validates from its own serialized form too
    CapabilityArtifact.model_validate_json(artifact.model_dump_json())


def test_transfer_click_step_is_marked_risky_from_template_metadata(tmp_path):
    """Milestone 6: the template declares which discovered click is the
    state-changing action (risky_click_accessible_names={"Transfer"}) —
    compile.py only ever consults that set, never a hardcoded label."""
    run_dir = build_successful_transfer_run(tmp_path)
    artifact, discovered_count = compile_artifact(run_dir, PARABANK_TRANSFER_FUNDS_TEMPLATE)

    discovered = artifact.steps[:discovered_count]
    click_steps = [s for s in discovered if s.action == "click"]
    assert len(click_steps) == 1
    assert click_steps[0].risk == "risky"
    # every other discovered step remains "safe"
    assert all(s.risk == "safe" for s in discovered if s.action != "click")


def test_compiled_step_ids_are_renumbered_1_to_n(tmp_path):
    run_dir = build_successful_transfer_run(tmp_path)
    artifact, discovered_count = compile_artifact(run_dir, PARABANK_TRANSFER_FUNDS_TEMPLATE)
    step_ids = [s.step_id for s in artifact.steps]
    assert step_ids == list(range(1, len(artifact.steps) + 1))


def test_trailing_wait_and_extract_steps_are_appended(tmp_path):
    run_dir = build_successful_transfer_run(tmp_path)
    artifact, discovered_count = compile_artifact(run_dir, PARABANK_TRANSFER_FUNDS_TEMPLATE)

    trailing = artifact.steps[discovered_count:]
    assert [s.action for s in trailing] == ["wait_for", "extract", "extract", "extract", "extract"]
    assert [s.output_ref for s in trailing[1:]] == [
        "confirmation_message", "transferred_amount", "from_account_id", "to_account_id",
    ]


def test_discovered_actions_precede_trailing_steps_in_execution_order(tmp_path):
    run_dir = build_successful_transfer_run(tmp_path)
    artifact, discovered_count = compile_artifact(run_dir, PARABANK_TRANSFER_FUNDS_TEMPLATE)
    discovered = artifact.steps[:discovered_count]
    assert [s.action for s in discovered] == ["navigate", "select_option", "select_option", "type", "click"]


def test_session_requirement_business_outcome_and_policy_come_from_template(tmp_path):
    run_dir = build_successful_transfer_run(tmp_path)
    artifact, _ = compile_artifact(run_dir, PARABANK_TRANSFER_FUNDS_TEMPLATE)

    assert artifact.session_requirement.authenticated is True
    assert artifact.session_requirement.auth_profile == "parabank_demo"
    assert len(artifact.business_outcomes) == 1
    assert artifact.business_outcomes[0].code == "INVALID_CREDENTIALS"
    assert artifact.business_outcomes[0].contains_text == "The username and password could not be verified."
    assert artifact.policy.allowed_domains == ["parabank.parasoft.com"]


def test_success_checkpoint_comes_from_template(tmp_path):
    run_dir = build_successful_transfer_run(tmp_path)
    artifact, _ = compile_artifact(run_dir, PARABANK_TRANSFER_FUNDS_TEMPLATE)
    assert artifact.success_checkpoint.expected_literal_text == ["Transfer Complete!"]
    assert {r.name for r in artifact.success_checkpoint.expected_value_refs} == {
        "amount", "from_account_id", "to_account_id",
    }


def test_discovery_run_id_provenance_is_set(tmp_path):
    run_dir = build_successful_transfer_run(tmp_path, run_id="discovery-20260902T051912Z-d505f934")
    artifact, _ = compile_artifact(run_dir, PARABANK_TRANSFER_FUNDS_TEMPLATE)
    assert artifact.discovery_run_id == "discovery-20260902T051912Z-d505f934"
    assert artifact.created_by == "discovery_agent"
    assert "groq" in artifact.notes
    assert "qwen/qwen3.6-27b" in artifact.notes


def test_non_successful_discovery_run_is_rejected(tmp_path):
    run_dir = build_incomplete_run(tmp_path)
    with pytest.raises(CompilationError, match="successful finish event"):
        compile_artifact(run_dir, PARABANK_TRANSFER_FUNDS_TEMPLATE)


def test_corrections_and_failures_never_appear_in_compiled_steps(tmp_path):
    run_dir = build_run_with_corrections_and_failures(tmp_path)
    artifact, discovered_count = compile_artifact(run_dir, PARABANK_TRANSFER_FUNDS_TEMPLATE)
    discovered = artifact.steps[:discovered_count]
    assert [s.action for s in discovered] == ["navigate", "click"]


def test_get_template_rejects_unknown_capability_id():
    with pytest.raises(CompilationError, match="no CompilationTemplate registered"):
        get_template("some.other_capability")


def test_no_credentials_or_api_keys_appear_in_generated_artifact(tmp_path, monkeypatch):
    fake_username = "cua_test_user"
    fake_password = "s3cr3t-fake-password-do-not-log-me"
    fake_api_key = "gsk-totally-fake-groq-key-do-not-log"
    monkeypatch.setenv("PARABANK_USERNAME", fake_username)
    monkeypatch.setenv("PARABANK_PASSWORD", fake_password)
    monkeypatch.setenv("GROQ_API_KEY", fake_api_key)

    run_dir = build_successful_transfer_run(tmp_path)
    artifact, _ = compile_artifact(run_dir, PARABANK_TRANSFER_FUNDS_TEMPLATE)
    serialized = artifact.model_dump_json()

    for forbidden in (fake_username, fake_password, fake_api_key, "PARABANK_USERNAME=", "PARABANK_PASSWORD="):
        assert forbidden not in serialized
    # the business-outcome copy legitimately contains the word "password" —
    # that is expected static text, not a leaked credential value.
    assert "The username and password could not be verified." in serialized


def test_deterministic_compilation_modulo_created_at(tmp_path):
    run_dir = build_successful_transfer_run(tmp_path)
    artifact_1, _ = compile_artifact(run_dir, PARABANK_TRANSFER_FUNDS_TEMPLATE)
    artifact_2, _ = compile_artifact(run_dir, PARABANK_TRANSFER_FUNDS_TEMPLATE)

    dump_1 = artifact_1.model_dump(exclude={"created_at"})
    dump_2 = artifact_2.model_dump(exclude={"created_at"})
    assert dump_1 == dump_2


def test_compile_and_write_produces_a_file_at_the_expected_path(tmp_path):
    run_dir = build_successful_transfer_run(tmp_path)
    output_root = tmp_path / "generated_capabilities"

    result = compile_and_write(run_dir, "parabank.transfer_funds", output_root=output_root)

    assert result.output_path == output_root / "parabank.transfer_funds" / "v1.json"
    assert result.output_path.is_file()
    on_disk = json.loads(result.output_path.read_text())
    CapabilityArtifact.model_validate(on_disk)


def test_compile_and_write_never_writes_a_file_on_failure(tmp_path):
    run_dir = build_incomplete_run(tmp_path)
    output_root = tmp_path / "generated_capabilities"

    with pytest.raises(CompilationError):
        compile_and_write(run_dir, "parabank.transfer_funds", output_root=output_root)

    assert not output_root.exists()
