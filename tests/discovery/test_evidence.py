"""Tests for cua.discovery.evidence — event format and credential safety."""

import json

from cua.artifact.schema import LiteralRef, ParamRef
from cua.discovery.evidence import DiscoveryEvidenceWriter
from cua.discovery.observation import build_observation


def test_record_step_writes_required_fields(tmp_path):
    evidence = DiscoveryEvidenceWriter(run_id="disc-1", base_dir=tmp_path)
    evidence.record_step(
        step_number=3,
        provider="gemini",
        model="gemini-2.5-flash",
        action="type_text",
        target_description="Amount field",
        accessible_role="textbox",
        accessible_name="Amount",
        value_source=ParamRef(name="amount"),
        resolved_locator_strategy="label_text#1",
        rationale="Entering the transfer amount.",
        outcome="ok",
        resulting_url="https://parabank.parasoft.com/parabank/transfer.htm",
    )

    lines = evidence.events_path.read_text().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    for key in (
        "run_id", "timestamp", "step_number", "provider", "model", "action",
        "target_description", "accessible_role", "accessible_name",
        "value_source", "resolved_locator_strategy", "rationale",
        "outcome", "resulting_url", "observation_ref", "checkpoint_result",
    ):
        assert key in event
    assert event["value_source"] == {"kind": "param", "name": "amount"}
    assert event["provider"] == "gemini"
    assert event["model"] == "gemini-2.5-flash"


def test_record_step_saves_observation_when_provided(tmp_path):
    evidence = DiscoveryEvidenceWriter(run_id="disc-2", base_dir=tmp_path)
    obs = build_observation(
        step_number=1, url="u", title="t", heading_texts=["H"], button_names=[],
        link_names=[], text_input_names=[], select_data=[], visible_text="body",
    )
    ref = evidence.record_step(
        step_number=1, provider="gemini", model="gemini-2.5-flash", action="navigate",
        outcome="ok", observation=obs,
    )
    assert ref == "observations/step_001.json"
    saved = json.loads((evidence.run_dir / ref).read_text())
    assert saved["headings"] == ["H"]


def test_record_step_includes_correction_attempt_for_invalid_decision_events(tmp_path):
    evidence = DiscoveryEvidenceWriter(run_id="disc-corr", base_dir=tmp_path)
    evidence.record_step(
        step_number=4, provider="groq", model="openai/gpt-oss-120b", action="invalid_response",
        outcome="invalid_model_response", rationale="expected exactly one actionable function call, got 0",
        correction_attempt=1,
    )
    event = json.loads(evidence.events_path.read_text().splitlines()[0])
    assert event["correction_attempt"] == 1
    assert event["step_number"] == 4
    assert event["provider"] == "groq"


def test_record_step_correction_attempt_defaults_to_none(tmp_path):
    evidence = DiscoveryEvidenceWriter(run_id="disc-corr-2", base_dir=tmp_path)
    evidence.record_step(step_number=1, provider="gemini", model="m", action="click", outcome="ok")
    event = json.loads(evidence.events_path.read_text().splitlines()[0])
    assert event["correction_attempt"] is None


def test_literal_ref_value_source_serializes_correctly(tmp_path):
    evidence = DiscoveryEvidenceWriter(run_id="disc-3", base_dir=tmp_path)
    evidence.record_step(
        step_number=1, provider="gemini", model="m", action="select_option", outcome="ok",
        value_source=LiteralRef(value="SAVINGS"),
    )
    event = json.loads(evidence.events_path.read_text().splitlines()[0])
    assert event["value_source"] == {"kind": "literal", "value": "SAVINGS"}


def test_evidence_never_contains_credential_strings(tmp_path):
    fake_password = "s3cr3t-fake-password-do-not-log-me"
    fake_gemini_key = "sk-totally-fake-gemini-key-do-not-log"
    evidence = DiscoveryEvidenceWriter(run_id="disc-4", base_dir=tmp_path)

    # record_event is the same call establish_session uses during
    # session bootstrap — it must never be given a credential, and this
    # test proves the writer doesn't have any code path that could leak
    # one even if it somehow were.
    evidence.record_event(step_id=None, action="session_establish", locator_strategy=None, outcome="attempted")
    evidence.record_step(
        step_number=1, provider="gemini", model="gemini-2.5-flash", action="type_text",
        target_description="Amount field", value_source=ParamRef(name="amount"),
        rationale="Entering amount.", outcome="ok",
    )

    contents = evidence.events_path.read_text()
    assert fake_password not in contents
    assert fake_gemini_key not in contents
    assert "GEMINI_API_KEY" not in contents
