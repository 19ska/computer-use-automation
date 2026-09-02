"""Tests for GeminiProvider — the only module allowed to know about
google.genai. No real network access: the client's own
`generate_content` method is replaced with a scripted fake, so
everything else (Tool/Content construction, function-call extraction,
threading) exercises real code.
"""

from google.genai import errors

from cua.discovery.llm import LLMActionCall, LLMDecisionError, LLMProviderError
from cua.discovery.llm.gemini_provider import GeminiProvider

from .fakes import (
    FakeGeminiCandidate,
    FakeGeminiContent,
    FakeGeminiFunctionCall,
    FakeGeminiResponse,
    ScriptedGenerateContent,
)


def _make_provider(monkeypatch, script):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-tests")
    provider = GeminiProvider("gemini-2.5-flash", system_prompt="You are a test system prompt.")
    provider._client.models.generate_content = ScriptedGenerateContent(script)
    return provider


def test_missing_api_key_raises_llm_provider_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    try:
        GeminiProvider("gemini-2.5-flash", system_prompt="x")
        raised = False
    except LLMProviderError:
        raised = True
    assert raised


def test_start_seeds_conversation_with_goal_and_params(monkeypatch):
    provider = _make_provider(monkeypatch, script=[])
    provider.start(
        goal="Transfer 20.00 from account 15009 to account 15120.",
        declared_params={"amount": "20.00"},
        observation_text="URL: https://x/transfer.htm",
    )
    assert len(provider._contents) == 1
    seeded_text = provider._contents[0].parts[0].text
    assert "Transfer 20.00" in seeded_text
    assert "amount=20.00" in seeded_text
    assert "transfer.htm" in seeded_text


def test_propose_action_extracts_single_actionable_call(monkeypatch):
    response = FakeGeminiResponse(
        function_calls=[FakeGeminiFunctionCall(name="click", args={"target_description": "Transfer button"})],
        candidates=[FakeGeminiCandidate(content=FakeGeminiContent(role="model", parts=[]))],
    )
    provider = _make_provider(monkeypatch, script=[response])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    decision = provider.propose_action()

    assert isinstance(decision, LLMActionCall)
    assert decision.name == "click"
    assert decision.args == {"target_description": "Transfer button"}


def test_propose_action_zero_calls_returns_decision_error(monkeypatch):
    response = FakeGeminiResponse(function_calls=[], candidates=[FakeGeminiCandidate()])
    provider = _make_provider(monkeypatch, script=[response])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    decision = provider.propose_action()
    assert isinstance(decision, LLMDecisionError)


def test_propose_action_multiple_calls_returns_decision_error(monkeypatch):
    response = FakeGeminiResponse(
        function_calls=[
            FakeGeminiFunctionCall(name="click", args={}),
            FakeGeminiFunctionCall(name="type_text", args={}),
        ],
        candidates=[FakeGeminiCandidate()],
    )
    provider = _make_provider(monkeypatch, script=[response])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    decision = provider.propose_action()
    assert isinstance(decision, LLMDecisionError)


def test_propose_action_wraps_api_error_as_llm_provider_error(monkeypatch):
    # Retries disabled (max attempts = 1) so a single scripted 503 raises
    # immediately — retry behavior itself is covered separately below.
    monkeypatch.setenv("GEMINI_MAX_TRANSIENT_ATTEMPTS", "1")
    provider = _make_provider(monkeypatch, script=[errors.APIError(503, {"error": {"message": "overloaded"}})])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    try:
        provider.propose_action()
        raised = False
    except LLMProviderError as exc:
        raised = True
        assert "overloaded" in str(exc)
    assert raised


def test_api_error_message_includes_safe_diagnostics_but_no_secret(monkeypatch):
    fake_key = "sk-totally-fake-gemini-key-do-not-log"
    monkeypatch.setenv("GEMINI_API_KEY", fake_key)
    provider = GeminiProvider("gemini-3.6-flash", system_prompt="x")
    provider._client.models.generate_content = ScriptedGenerateContent(
        [errors.APIError(400, {"error": {"message": "Request contains an invalid argument."}})]
    )
    provider.start(goal="g", declared_params={}, observation_text="obs")

    try:
        provider.propose_action()
        raised = False
    except LLMProviderError as exc:
        raised = True
        message = str(exc)
        assert "400" in message
        assert "invalid argument" in message
        assert "gemini-3.6-flash" in message
        assert "phase=initial_decision" in message
        assert fake_key not in message
    assert raised


def test_second_turn_api_error_is_tagged_tool_result_turn(monkeypatch):
    first_response = FakeGeminiResponse(
        function_calls=[FakeGeminiFunctionCall(name="click", args={})],
        candidates=[FakeGeminiCandidate(content=FakeGeminiContent(role="model", parts=[]))],
    )
    provider = _make_provider(
        monkeypatch,
        script=[first_response, errors.APIError(400, {"error": {"message": "bad turn"}})],
    )
    provider.start(goal="g", declared_params={}, observation_text="obs")
    provider.propose_action()
    provider.record_tool_result(result_text="ok", is_error=False)

    try:
        provider.propose_action()
        raised = False
    except LLMProviderError as exc:
        raised = True
        assert "phase=tool_result_turn" in str(exc)
    assert raised


def test_generate_content_never_receives_a_thinking_config(monkeypatch):
    # Regression test: thinking_budget is rejected as a user error by
    # Gemini 3.5+ models ("the old thinking_budget will no longer be
    # supported and will result in a user error if set" — per the
    # installed google-genai SDK's own docstring), which was the exact
    # cause of a real "400 Request contains an invalid argument." failure
    # against gemini-3.6-flash. This milestone needs no custom sampling
    # config at all, so the smallest safe fix is to never set one.
    response = FakeGeminiResponse(function_calls=[], candidates=[FakeGeminiCandidate()])
    recorder = ScriptedGenerateContent([response])
    provider = _make_provider(monkeypatch, script=[])
    provider._client.models.generate_content = recorder
    provider.start(goal="g", declared_params={}, observation_text="obs")

    provider.propose_action()

    assert len(recorder.calls) == 1
    config = recorder.calls[0]["config"]
    assert config.thinking_config is None


def test_record_tool_result_threads_function_response(monkeypatch):
    response = FakeGeminiResponse(
        function_calls=[FakeGeminiFunctionCall(name="click", args={}, id="call_42")],
        candidates=[FakeGeminiCandidate(content=FakeGeminiContent(role="model", parts=[]))],
    )
    provider = _make_provider(monkeypatch, script=[response])
    provider.start(goal="g", declared_params={}, observation_text="obs")
    provider.propose_action()

    provider.record_tool_result(result_text="clicked ok", is_error=False)

    # Content history: [initial user turn, model's function-call turn, tool response turn].
    # Gemini's Content.role only accepts "user"/"model" — there is no "tool"
    # role in the API. Function responses must be threaded back as "user"
    # (this mirrors what the installed google-genai SDK's own chat
    # implementation does internally). Regression test for a real bug that
    # sent role="tool" and would have caused a 400 on any second turn.
    assert len(provider._contents) == 3
    assert provider._contents[-1].role == "user"


def test_record_invalid_decision_after_text_only_response_appends_plain_corrective_turn(monkeypatch):
    response = FakeGeminiResponse(
        function_calls=[],
        candidates=[FakeGeminiCandidate(content=FakeGeminiContent(role="model", parts=[]))],
    )
    provider = _make_provider(monkeypatch, script=[response])
    provider.start(goal="g", declared_params={}, observation_text="obs")
    decision = provider.propose_action()
    assert isinstance(decision, LLMDecisionError)

    provider.record_invalid_decision("This discovery loop requires exactly one action per turn.")

    # [initial user turn, model's text-only turn, corrective user turn]
    assert len(provider._contents) == 3
    corrective = provider._contents[-1]
    assert corrective.role == "user"
    assert "requires exactly one action" in corrective.parts[0].text


def test_record_invalid_decision_after_multiple_calls_responds_to_each_raw_call(monkeypatch):
    response = FakeGeminiResponse(
        function_calls=[
            FakeGeminiFunctionCall(name="click", args={}, id="call_1"),
            FakeGeminiFunctionCall(name="type_text", args={}, id="call_2"),
        ],
        candidates=[FakeGeminiCandidate(content=FakeGeminiContent(role="model", parts=[]))],
    )
    provider = _make_provider(monkeypatch, script=[response])
    provider.start(goal="g", declared_params={}, observation_text="obs")
    decision = provider.propose_action()
    assert isinstance(decision, LLMDecisionError)

    provider.record_invalid_decision("pick exactly one")

    corrective = provider._contents[-1]
    assert corrective.role == "user"
    # 2 function_response parts (one per raw call) + 1 trailing text part
    assert len(corrective.parts) == 3


def test_record_invalid_decision_does_not_leave_a_pending_call(monkeypatch):
    response = FakeGeminiResponse(function_calls=[], candidates=[FakeGeminiCandidate()])
    provider = _make_provider(monkeypatch, script=[response])
    provider.start(goal="g", declared_params={}, observation_text="obs")
    provider.propose_action()

    provider.record_invalid_decision("try again")

    assert provider._pending_function_call is None
    assert provider._last_raw_function_calls == []


def _patch_sleep(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("cua.discovery.llm.gemini_provider.time.sleep", sleeps.append)
    return sleeps


def _finish_response():
    return FakeGeminiResponse(
        function_calls=[FakeGeminiFunctionCall(name="finish", args={"rationale": "done"})],
        candidates=[FakeGeminiCandidate(content=FakeGeminiContent(role="model", parts=[]))],
    )


def _transient_503():
    return errors.APIError(503, {"error": {"message": "high demand"}})


def test_503_then_success_retries_once_and_succeeds(monkeypatch):
    sleeps = _patch_sleep(monkeypatch)
    provider = _make_provider(monkeypatch, script=[_transient_503(), _finish_response()])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    decision = provider.propose_action()

    assert isinstance(decision, LLMActionCall)
    assert decision.name == "finish"
    assert sleeps == [5.0]


def test_503_503_success_succeeds_on_final_allowed_attempt(monkeypatch):
    sleeps = _patch_sleep(monkeypatch)
    provider = _make_provider(monkeypatch, script=[_transient_503(), _transient_503(), _finish_response()])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    decision = provider.propose_action()

    assert isinstance(decision, LLMActionCall)
    assert sleeps == [5.0, 10.0]  # bounded exponential backoff: base * 2**(attempt-1)


def test_repeated_503_beyond_the_limit_raises_structured_llm_provider_error(monkeypatch):
    sleeps = _patch_sleep(monkeypatch)
    provider = _make_provider(monkeypatch, script=[_transient_503(), _transient_503(), _transient_503()])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    try:
        provider.propose_action()
        raised = False
    except LLMProviderError as exc:
        raised = True
        message = str(exc)
        assert "attempt=3" in message
        assert "code=503" in message
    assert raised
    assert sleeps == [5.0, 10.0]  # exactly 3 attempts total: 2 waits, then give up, no 4th attempt


def test_400_is_never_retried(monkeypatch):
    sleeps = _patch_sleep(monkeypatch)
    provider = _make_provider(monkeypatch, script=[errors.APIError(400, {"error": {"message": "bad request"}})])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    try:
        provider.propose_action()
        raised = False
    except LLMProviderError:
        raised = True
    assert raised
    assert sleeps == []


def test_429_with_retry_after_is_retried_once(monkeypatch):
    sleeps = _patch_sleep(monkeypatch)
    rate_limited = errors.APIError(429, {"error": {"message": "rate limited", "details": [{"retryDelay": "3s"}]}})
    provider = _make_provider(monkeypatch, script=[rate_limited, _finish_response()])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    decision = provider.propose_action()

    assert isinstance(decision, LLMActionCall)
    assert sleeps == [3.0]


def test_429_without_a_stated_retry_delay_is_not_retried(monkeypatch):
    sleeps = _patch_sleep(monkeypatch)
    rate_limited = errors.APIError(429, {"error": {"message": "rate limited"}})
    provider = _make_provider(monkeypatch, script=[rate_limited])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    try:
        provider.propose_action()
        raised = False
    except LLMProviderError:
        raised = True
    assert raised
    assert sleeps == []


def test_429_is_never_retried_more_than_once_even_if_it_recurs(monkeypatch):
    rate_limited = errors.APIError(429, {"error": {"message": "rate limited", "details": [{"retryDelay": "1s"}]}})
    sleeps = _patch_sleep(monkeypatch)
    provider = _make_provider(monkeypatch, script=[rate_limited, rate_limited])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    try:
        provider.propose_action()
        raised = False
    except LLMProviderError:
        raised = True
    assert raised
    assert sleeps == [1.0]  # not two — 429 gets at most one bounded retry, not unlimited


def test_failed_transient_attempts_do_not_mutate_conversation_state(monkeypatch):
    _patch_sleep(monkeypatch)
    provider = _make_provider(monkeypatch, script=[_transient_503(), _finish_response()])
    provider.start(goal="g", declared_params={}, observation_text="obs")
    assert len(provider._contents) == 1  # just the seeded initial turn

    provider.propose_action()

    # Exactly one new entry — the eventual successful response's own
    # content — never one per failed attempt in between.
    assert len(provider._contents) == 2


def test_fully_exhausted_retries_leave_history_completely_unchanged(monkeypatch):
    """A 503 retry must retry the SAME decision request: it must not
    execute a Playwright action, duplicate a tool_result, or modify
    conversation history as though Gemini had responded. When every
    attempt fails, nothing about the conversation should have moved at
    all — same as if propose_action() had never been called.
    """
    _patch_sleep(monkeypatch)
    provider = _make_provider(monkeypatch, script=[_transient_503(), _transient_503(), _transient_503()])
    provider.start(goal="g", declared_params={}, observation_text="obs")
    assert len(provider._contents) == 1

    try:
        provider.propose_action()
    except LLMProviderError:
        pass

    assert len(provider._contents) == 1
    assert provider._pending_function_call is None


def test_retry_config_is_configurable_via_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_MAX_TRANSIENT_ATTEMPTS", "2")
    monkeypatch.setenv("GEMINI_RETRY_BASE_SECONDS", "1")
    sleeps = _patch_sleep(monkeypatch)
    provider = _make_provider(monkeypatch, script=[_transient_503(), _transient_503()])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    try:
        provider.propose_action()
        raised = False
    except LLMProviderError:
        raised = True
    assert raised
    assert sleeps == [1.0]  # only 1 wait allowed for max_attempts=2, at base_seconds=1


def test_api_key_never_appears_on_provider_public_state(monkeypatch):
    fake_key = "sk-totally-fake-gemini-key-do-not-log"
    monkeypatch.setenv("GEMINI_API_KEY", fake_key)
    provider = GeminiProvider("gemini-2.5-flash", system_prompt="x")

    assert fake_key not in repr(provider.__dict__)
    assert not any(fake_key in str(v) for k, v in vars(provider).items() if k != "_client")
