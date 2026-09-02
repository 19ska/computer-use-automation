"""Tests for cua.replay.operator.TerminalOperator — the minimal real
same-session control-transfer mechanism (blocking stdin prompt)."""

import builtins

from cua.replay.operator import InterventionRequest, TerminalOperator

_REQUEST = InterventionRequest(
    run_id="r1", capability_id="parabank.transfer_funds", step_id=5,
    reason="amount=600.00 exceeds approval threshold 500",
    current_url="https://parabank.parasoft.com/parabank/transfer.htm",
    screenshot_path="run_output/r1/intervention_step_5.png",
    timestamp="2026-01-01T00:00:00+00:00",
    pending_action="click on 'Transfer submit button'",
    context={"amount": "600.00"},
)


def _scripted_input(monkeypatch, responses: list[str]):
    responses = list(responses)

    def fake_input(prompt: str = "") -> str:
        return responses.pop(0)

    monkeypatch.setattr(builtins, "input", fake_input)


def test_resume_is_recognized(monkeypatch):
    _scripted_input(monkeypatch, ["resume"])
    assert TerminalOperator().request_intervention(_REQUEST) == "resume"


def test_approve_is_treated_as_resume(monkeypatch):
    _scripted_input(monkeypatch, ["approve"])
    assert TerminalOperator().request_intervention(_REQUEST) == "resume"


def test_decline_is_recognized(monkeypatch):
    _scripted_input(monkeypatch, ["decline"])
    assert TerminalOperator().request_intervention(_REQUEST) == "decline"


def test_cancel_is_treated_as_decline(monkeypatch):
    _scripted_input(monkeypatch, ["cancel"])
    assert TerminalOperator().request_intervention(_REQUEST) == "decline"


def test_case_insensitive_and_whitespace_trimmed(monkeypatch):
    _scripted_input(monkeypatch, ["  RESUME  "])
    assert TerminalOperator().request_intervention(_REQUEST) == "resume"


def test_reprompts_on_invalid_input(monkeypatch):
    _scripted_input(monkeypatch, ["banana", "whatever", "decline"])
    assert TerminalOperator().request_intervention(_REQUEST) == "decline"
