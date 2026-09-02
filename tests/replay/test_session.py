"""Tests for ParaBank session establishment (cua.replay.session).

Uses a fake Page — no real browser or network access. Exercises the
authenticated / INVALID_CREDENTIALS / config-error paths, and includes a
regression test proving credentials never reach the evidence log.
"""

from datetime import datetime, timezone

import pytest

from cua.artifact.schema import (
    ActionStep,
    BusinessOutcomeDetector,
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
from cua.replay.evidence import ReplayEvidenceWriter
from cua.replay.session import SessionBusinessOutcome, SessionEstablished, SessionFailure, establish_session

from .fakes import FakeElement, FakeLocator, FakePage

FAKE_USERNAME = "cua_test_user"
FAKE_PASSWORD = "s3cr3t-fake-password-do-not-log-me"


def _artifact(*, auth_profile: str | None = "parabank_demo") -> CapabilityArtifact:
    target = ElementTarget(description="body", strategies=[CssLocator(selector="body")])
    return CapabilityArtifact(
        capability_id="test.session_capability",
        capability_version=1,
        display_name="d",
        description="d",
        target_app="https://parabank.parasoft.com/parabank",
        session_requirement=SessionRequirement(authenticated=True, auth_profile=auth_profile),
        inputs=[InputParameter(name="amount", type="decimal", description="d")],
        outputs=[
            OutputField(
                name="x",
                type="string",
                description="d",
                extraction=ExtractionSpec(target=target, source="text"),
            )
        ],
        steps=[
            ActionStep(step_id=1, action="navigate", url="/transfer.htm"),
            ActionStep(step_id=2, action="extract", output_ref="x"),
        ],
        success_checkpoint=Checkpoint(description="d", assertion="text_contains", expected_literal_text=["ok"]),
        business_outcomes=[
            BusinessOutcomeDetector(
                code="INVALID_CREDENTIALS",
                description="d",
                origin="session_establishment",
                target=target,
                contains_text="The username and password could not be verified.",
            )
        ],
        policy=PolicyMetadata(
            allowed_domains=["parabank.parasoft.com"], allowed_actions=["navigate", "extract"]
        ),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        created_by="human_edited",
        discovery_run_id="test-run",
    )


def _page_with_body(text: str) -> FakePage:
    page = FakePage()
    page.body_text = text
    page.css_locators["body"] = FakeLocator([FakeElement(text=text)])
    return page


def _establish(page, artifact, evidence):
    """Thin helper mirroring how replay/engine.py calls establish_session
    — unpacks the pieces from a CapabilityArtifact, since the function
    itself no longer takes an artifact directly."""
    return establish_session(
        page,
        base_url=artifact.target_app,
        session_requirement=artifact.session_requirement,
        business_outcomes=artifact.business_outcomes,
        evidence=evidence,
    )


def test_establish_session_returns_established_when_logout_link_present(monkeypatch, tmp_path):
    monkeypatch.setenv("PARABANK_USERNAME", FAKE_USERNAME)
    monkeypatch.setenv("PARABANK_PASSWORD", FAKE_PASSWORD)

    page = _page_with_body("Welcome!")
    page.query_selector_result = object()  # simulates the Log Out link being found

    evidence = ReplayEvidenceWriter(run_id="r1", base_dir=tmp_path)
    result = _establish(page, _artifact(), evidence)

    assert isinstance(result, SessionEstablished)
    assert page.fill_calls == [
        ("input[name='username']", FAKE_USERNAME),
        ("input[name='password']", FAKE_PASSWORD),
    ]


def test_establish_session_navigates_without_networkidle(monkeypatch, tmp_path):
    """Regression test for the real bug: ParaBank can keep background
    network activity alive indefinitely, so `wait_until="networkidle"`
    hung well past any reasonable navigation budget. Initial navigation
    must use "domcontentloaded" instead.
    """
    monkeypatch.setenv("PARABANK_USERNAME", FAKE_USERNAME)
    monkeypatch.setenv("PARABANK_PASSWORD", FAKE_PASSWORD)

    page = _page_with_body("Welcome!")
    page.query_selector_result = object()

    evidence = ReplayEvidenceWriter(run_id="r-nav", base_dir=tmp_path)
    result = _establish(page, _artifact(), evidence)

    assert isinstance(result, SessionEstablished)
    assert len(page.goto_details) == 1
    assert page.goto_details[0]["wait_until"] == "domcontentloaded"
    assert page.goto_details[0]["wait_until"] != "networkidle"


def test_establish_session_waits_for_login_controls_before_filling(monkeypatch, tmp_path):
    """DOM load + the three real login controls (verified in Milestone 1)
    is the readiness signal now, not global network idleness."""
    monkeypatch.setenv("PARABANK_USERNAME", FAKE_USERNAME)
    monkeypatch.setenv("PARABANK_PASSWORD", FAKE_PASSWORD)

    page = _page_with_body("Welcome!")
    page.query_selector_result = object()

    evidence = ReplayEvidenceWriter(run_id="r-ready", base_dir=tmp_path)
    result = _establish(page, _artifact(), evidence)

    assert isinstance(result, SessionEstablished)
    waited_selectors = [selector for selector, _timeout in page.wait_for_selector_calls]
    assert waited_selectors == [
        "input[name='username']",
        "input[name='password']",
        "input[type='submit'][value='Log In']",
    ]
    # bounded, not unbounded
    assert all(timeout is not None and timeout <= 20_000 for _selector, timeout in page.wait_for_selector_calls)


def test_establish_session_fails_cleanly_when_login_controls_never_appear(monkeypatch, tmp_path):
    """If navigation/readiness fails (e.g. the login controls never show
    up), this must become the existing structured session failure — never
    an unhandled crash — and must never attempt to fill the login form."""
    monkeypatch.setenv("PARABANK_USERNAME", FAKE_USERNAME)
    monkeypatch.setenv("PARABANK_PASSWORD", FAKE_PASSWORD)

    page = _page_with_body("")
    page.wait_for_selector_error = TimeoutError("Timeout 15000ms exceeded waiting for selector")

    evidence = ReplayEvidenceWriter(run_id="r-timeout", base_dir=tmp_path)
    result = _establish(page, _artifact(), evidence)

    assert isinstance(result, SessionFailure)
    assert result.category == "session_establishment_error"
    assert page.fill_calls == []  # never attempted to fill a form that was never confirmed ready


def test_establish_session_returns_business_outcome_on_invalid_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("PARABANK_USERNAME", FAKE_USERNAME)
    monkeypatch.setenv("PARABANK_PASSWORD", FAKE_PASSWORD)

    page = _page_with_body("The username and password could not be verified.")
    page.query_selector_result = None  # no Log Out link -> not authenticated

    evidence = ReplayEvidenceWriter(run_id="r2", base_dir=tmp_path)
    result = _establish(page, _artifact(), evidence)

    assert isinstance(result, SessionBusinessOutcome)
    assert result.outcome_code == "INVALID_CREDENTIALS"


def test_establish_session_fails_when_env_vars_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("PARABANK_USERNAME", raising=False)
    monkeypatch.delenv("PARABANK_PASSWORD", raising=False)

    page = FakePage()
    evidence = ReplayEvidenceWriter(run_id="r3", base_dir=tmp_path)
    result = _establish(page, _artifact(), evidence)

    assert isinstance(result, SessionFailure)
    assert page.goto_calls == []  # never even opened the login page


def test_establish_session_fails_for_unknown_auth_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("PARABANK_USERNAME", FAKE_USERNAME)
    monkeypatch.setenv("PARABANK_PASSWORD", FAKE_PASSWORD)

    page = FakePage()
    evidence = ReplayEvidenceWriter(run_id="r4", base_dir=tmp_path)
    result = _establish(page, _artifact(auth_profile="some_other_profile"), evidence)

    assert isinstance(result, SessionFailure)


def test_establish_session_short_circuits_when_not_required(tmp_path):
    artifact = _artifact()
    artifact = artifact.model_copy(
        update={"session_requirement": SessionRequirement(authenticated=False)}
    )
    page = FakePage()
    evidence = ReplayEvidenceWriter(run_id="r5", base_dir=tmp_path)

    result = _establish(page, artifact, evidence)

    assert isinstance(result, SessionEstablished)
    assert page.goto_calls == []


def test_establish_session_never_logs_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("PARABANK_USERNAME", FAKE_USERNAME)
    monkeypatch.setenv("PARABANK_PASSWORD", FAKE_PASSWORD)

    page = _page_with_body("Welcome!")
    page.query_selector_result = object()

    evidence = ReplayEvidenceWriter(run_id="r6", base_dir=tmp_path)
    _establish(page, _artifact(), evidence)

    log_contents = evidence.events_path.read_text()
    assert FAKE_USERNAME not in log_contents
    assert FAKE_PASSWORD not in log_contents
