"""Tests for the discovery orchestration loop (cua.discovery.engine).

Exercises _run_against_page() directly with a fake LLMProvider AND fakes
for the four other injectable dependencies (establish_session,
capture_observation, execute_action, evaluate_checkpoint) — no real
browser, no real API call, and no need for a heavyweight fake Page (a
plain sentinel object stands in for it, since none of the fakes actually
touch it). Because the engine only ever talks to the provider-neutral
LLMProvider Protocol, these tests don't need to know anything about
Gemini's actual response shape at all — that's exercised separately in
test_gemini_provider.py.
"""

from __future__ import annotations

import json

from cua.artifact.schema import RoleLocator
from cua.discovery import engine
from cua.discovery.evidence import DiscoveryEvidenceWriter
from cua.discovery.executor import ExecutionOutcome
from cua.discovery.llm import LLMDecisionError, LLMProviderError
from cua.discovery.observation import build_observation
from cua.replay.checkpoints import CheckpointResult
from cua.replay.session import SessionBusinessOutcome, SessionEstablished, SessionFailure

from .fakes import FakeLLMProvider, RepeatingCallable, ScriptedCallable, action_call

BASE_URL = "https://parabank.parasoft.com/parabank"
ALLOWED_HOST = "parabank.parasoft.com"
DECLARED_PARAMS = {"amount": "20.00", "from_account_id": "15009", "to_account_id": "15120"}

_PAGE = object()  # never dereferenced by any of the fakes used here


def _observation():
    return build_observation(
        step_number=0, url="u", title="t", heading_texts=[], button_names=[],
        link_names=[], text_input_names=[], select_data=[], visible_text="",
    )


def _run(
    *,
    provider_script,
    max_steps=15,
    establish_session_result=SessionEstablished(),
    execute_action_script=None,
    checkpoint_script=None,
    tmp_path,
    return_provider=False,
):
    evidence = DiscoveryEvidenceWriter(run_id="test-run", base_dir=tmp_path)
    provider = FakeLLMProvider("gemini-2.5-flash", provider_script)
    execute_action_fn = ScriptedCallable(execute_action_script or [])
    result = engine._run_against_page(
        _PAGE,
        goal="Transfer 20.00 from account 15009 to account 15120 and reach the transfer confirmation page.",
        declared_params=DECLARED_PARAMS,
        base_url=BASE_URL,
        allowed_host=ALLOWED_HOST,
        max_steps=max_steps,
        timeout_s=300.0,
        run_id="test-run",
        evidence=evidence,
        provider=provider,
        establish_session_fn=RepeatingCallable(establish_session_result),
        capture_observation_fn=RepeatingCallable(_observation()),
        execute_action_fn=execute_action_fn,
        evaluate_checkpoint_fn=ScriptedCallable(checkpoint_script or []),
    )
    if return_provider:
        return result, provider, execute_action_fn
    return result


def _events(tmp_path):
    return [json.loads(line) for line in (tmp_path / "test-run" / "events.jsonl").read_text().splitlines()]


def test_navigate_step_records_the_requested_url_path_in_evidence(tmp_path):
    result = _run(
        provider_script=[action_call("navigate", {"url_path": "/transfer.htm", "rationale": "go"})],
        execute_action_script=[ExecutionOutcome(ok=True, resulting_url=f"{BASE_URL}/transfer.htm")],
        max_steps=1,
        tmp_path=tmp_path,
    )
    assert result.status == "failure"
    assert result.failure_category == "max_steps_exceeded"
    events = [e for e in _events(tmp_path) if e.get("action") == "navigate"]
    assert len(events) == 1
    assert events[0]["url_path"] == "/transfer.htm"
    assert events[0]["resulting_url"] == f"{BASE_URL}/transfer.htm"


def test_click_step_records_resolved_locator_in_evidence(tmp_path):
    click_input = {
        "target_description": "Transfer button", "accessible_role": "button",
        "accessible_name": "Transfer", "rationale": "clicking",
    }
    resolved = RoleLocator(role="button", name="Transfer")
    result = _run(
        provider_script=[action_call("click", click_input)],
        execute_action_script=[
            ExecutionOutcome(ok=True, resolved_locator_strategy="role#0", resolved_locator=resolved)
        ],
        max_steps=1,
        tmp_path=tmp_path,
    )
    assert result.status == "failure"
    assert result.failure_category == "max_steps_exceeded"
    events = [e for e in _events(tmp_path) if e.get("action") == "click"]
    assert len(events) == 1
    assert events[0]["resolved_locator"] == {"kind": "role", "role": "button", "name": "Transfer", "exact": False}
    assert events[0]["url_path"] is None


def test_session_business_outcome_short_circuits_before_any_llm_call(tmp_path):
    result = _run(
        provider_script=[],  # if the provider were called, FakeLLMProvider would raise
        establish_session_result=SessionBusinessOutcome(
            outcome_code="INVALID_CREDENTIALS", message="The username and password could not be verified."
        ),
        tmp_path=tmp_path,
    )
    assert result.status == "business_outcome"
    assert result.outcome_code == "INVALID_CREDENTIALS"


def test_session_failure_short_circuits(tmp_path):
    result = _run(
        provider_script=[],
        establish_session_result=SessionFailure(category="x", expected="e", observed="o"),
        tmp_path=tmp_path,
    )
    assert result.status == "failure"
    assert result.failure_category == "session_establishment_error"


def test_max_steps_exceeded_stops_the_run(tmp_path):
    click_input = {
        "target_description": "Something",
        "accessible_role": "button",
        "accessible_name": "Something",
        "rationale": "clicking",
    }
    result = _run(
        provider_script=[action_call("click", click_input) for _ in range(3)],
        execute_action_script=[ExecutionOutcome(ok=True, resolved_locator_strategy="role#0")] * 3,
        max_steps=3,
        tmp_path=tmp_path,
    )
    assert result.status == "failure"
    assert result.failure_category == "max_steps_exceeded"
    assert result.last_step == 3


def test_repeated_identical_action_failure_stops_before_max_steps(tmp_path):
    click_input = {
        "target_description": "Ghost button",
        "accessible_role": "button",
        "accessible_name": "Ghost",
        "rationale": "trying",
    }
    result = _run(
        provider_script=[action_call("click", click_input) for _ in range(10)],
        execute_action_script=[ExecutionOutcome(ok=False, error="not found")] * 10,
        max_steps=10,
        tmp_path=tmp_path,
    )
    assert result.status == "failure"
    assert result.failure_category == "repeated_action_failure"
    assert result.last_step == 2  # threshold is 2 identical failures in a row


def test_finish_with_failing_checkpoint_does_not_succeed_and_continues(tmp_path):
    result = _run(
        provider_script=[
            action_call("finish", {"rationale": "I believe I am done."}),
            action_call("finish", {"rationale": "Still done."}),
        ],
        checkpoint_script=[
            CheckpointResult(passed=False, expected="Transfer Complete!", observed="not yet"),
            CheckpointResult(passed=True, expected="Transfer Complete!", observed="Transfer Complete! $20.00 ..."),
        ],
        max_steps=15,
        tmp_path=tmp_path,
    )
    assert result.status == "success"
    assert result.step_count == 2
    assert result.provider == "gemini"
    assert result.model == "gemini-2.5-flash"


def test_finish_repeatedly_failing_verification_stops_as_repeated_failure(tmp_path):
    result = _run(
        provider_script=[
            action_call("finish", {"rationale": "done"}),
            action_call("finish", {"rationale": "done again"}),
        ],
        checkpoint_script=[
            CheckpointResult(passed=False, expected="x", observed="y"),
            CheckpointResult(passed=False, expected="x", observed="y"),
        ],
        max_steps=15,
        tmp_path=tmp_path,
    )
    assert result.status == "failure"
    assert result.failure_category == "repeated_action_failure"


def test_give_up_stops_the_run(tmp_path):
    result = _run(
        provider_script=[action_call("give_up", {"reason": "cannot find the transfer form"})],
        tmp_path=tmp_path,
    )
    assert result.status == "failure"
    assert result.failure_category == "give_up"
    assert "cannot find" in result.reason


def test_policy_blocked_action_repeated_stops_as_repeated_failure(tmp_path):
    bad_navigate = {"url_path": "https://evil.example.com/x", "rationale": "trying to leave"}
    result = _run(
        provider_script=[action_call("navigate", bad_navigate) for _ in range(5)],
        max_steps=5,
        tmp_path=tmp_path,
    )
    assert result.status == "failure"
    assert result.failure_category == "repeated_action_failure"
    assert result.last_step == 2


def test_llm_api_error_becomes_structured_failure_not_a_crash(tmp_path):
    result = _run(
        provider_script=[LLMProviderError("simulated Gemini API failure")],
        tmp_path=tmp_path,
    )
    assert result.status == "failure"
    assert result.failure_category == "llm_api_error"
    assert "simulated Gemini API failure" in result.reason


def test_zero_actionable_calls_exhausting_all_corrections_is_a_structured_failure(tmp_path):
    result = _run(
        # initial attempt + 2 bounded corrections, all still invalid
        provider_script=[LLMDecisionError("expected exactly one actionable function call, got 0")] * 3,
        tmp_path=tmp_path,
    )
    assert result.status == "failure"
    assert result.failure_category == "invalid_model_response"
    assert result.last_step == 1


def test_text_only_response_then_correction_then_valid_call_succeeds(tmp_path):
    click_input = {
        "target_description": "Transfer button", "accessible_role": "button",
        "accessible_name": "Transfer", "rationale": "clicking",
    }
    result, provider, execute_action_fn = _run(
        provider_script=[
            LLMDecisionError("expected exactly one actionable function call, got 0 (no tool call in response)"),
            action_call("click", click_input),
        ],
        execute_action_script=[ExecutionOutcome(ok=True, resolved_locator_strategy="role#0")],
        max_steps=1,
        tmp_path=tmp_path,
        return_provider=True,
    )
    # max_steps=1 with exactly one real UI action executed confirms the
    # correction was accepted and the loop moved on to a normal step,
    # rather than failing with invalid_model_response.
    assert result.status == "failure"
    assert result.failure_category == "max_steps_exceeded"
    # exactly one correction was sent, and exactly one Playwright action executed
    assert provider.record_invalid_decision_calls == [engine._CORRECTIVE_MESSAGE]
    assert len(execute_action_fn.calls) == 1


def test_two_consecutive_invalid_responses_then_valid_call_succeeds(tmp_path):
    click_input = {
        "target_description": "Transfer button", "accessible_role": "button",
        "accessible_name": "Transfer", "rationale": "clicking",
    }
    result, provider, execute_action_fn = _run(
        provider_script=[
            LLMDecisionError("got 0"),
            LLMDecisionError("got 0"),
            action_call("click", click_input),
        ],
        execute_action_script=[ExecutionOutcome(ok=True, resolved_locator_strategy="role#0")],
        max_steps=1,
        tmp_path=tmp_path,
        return_provider=True,
    )
    assert result.status == "failure"
    assert result.failure_category == "max_steps_exceeded"
    assert provider.record_invalid_decision_calls == [engine._CORRECTIVE_MESSAGE, engine._CORRECTIVE_MESSAGE]
    assert len(execute_action_fn.calls) == 1


def test_multiple_tool_calls_response_is_bounded_corrected(tmp_path):
    click_input = {
        "target_description": "Transfer button", "accessible_role": "button",
        "accessible_name": "Transfer", "rationale": "clicking",
    }
    result, provider, execute_action_fn = _run(
        provider_script=[
            LLMDecisionError("expected exactly one actionable function call, got 2: ['click', 'type_text']"),
            action_call("click", click_input),
        ],
        execute_action_script=[ExecutionOutcome(ok=True, resolved_locator_strategy="role#0")],
        max_steps=1,
        tmp_path=tmp_path,
        return_provider=True,
    )
    assert result.status == "failure"
    assert result.failure_category == "max_steps_exceeded"
    assert provider.record_invalid_decision_calls == [engine._CORRECTIVE_MESSAGE]
    assert len(execute_action_fn.calls) == 1


def test_unknown_tool_name_response_is_bounded_corrected(tmp_path):
    click_input = {
        "target_description": "Transfer button", "accessible_role": "button",
        "accessible_name": "Transfer", "rationale": "clicking",
    }
    result, provider, execute_action_fn = _run(
        provider_script=[
            LLMDecisionError("received unsupported/unknown tool name(s): ['delete_everything']"),
            action_call("click", click_input),
        ],
        execute_action_script=[ExecutionOutcome(ok=True, resolved_locator_strategy="role#0")],
        max_steps=1,
        tmp_path=tmp_path,
        return_provider=True,
    )
    assert result.status == "failure"
    assert result.failure_category == "max_steps_exceeded"
    assert len(execute_action_fn.calls) == 1


def test_correction_attempts_do_not_advance_step_count_or_execute_actions(tmp_path):
    click_input = {
        "target_description": "Transfer button", "accessible_role": "button",
        "accessible_name": "Transfer", "rationale": "clicking",
    }
    result, provider, execute_action_fn = _run(
        provider_script=[
            LLMDecisionError("got 0"),
            LLMDecisionError("got 0"),
            action_call("click", click_input),
        ],
        execute_action_script=[ExecutionOutcome(ok=True, resolved_locator_strategy="role#0")],
        max_steps=1,
        tmp_path=tmp_path,
        return_provider=True,
    )
    # 3 propose_action() calls happened (2 corrections + 1 valid), but only
    # ONE Playwright action was ever executed, and max_steps=1 was enough
    # to reach a clean max_steps_exceeded stop — correction attempts never
    # became their own UI step, or step 1 would never have been reached
    # with only one action budgeted.
    assert result.status == "failure"
    assert result.failure_category == "max_steps_exceeded"
    assert result.last_step == 1
    assert len(execute_action_fn.calls) == 1


def test_provider_and_model_identifiers_are_recorded_on_every_result_type(tmp_path):
    result = _run(
        provider_script=[],
        establish_session_result=SessionFailure(category="x", expected="e", observed="o"),
        tmp_path=tmp_path,
    )
    assert result.provider == "gemini"
    assert result.model == "gemini-2.5-flash"
