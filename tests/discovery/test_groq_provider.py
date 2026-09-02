"""Tests for GroqProvider — the only module allowed to know about groq.
No real network access: the client's own `chat.completions.create`
method is replaced with a scripted fake, so everything else (tool schema
wrapping, tool-call extraction, threading) exercises real code.
"""

from cua.discovery.llm import LLMActionCall, LLMDecisionError, LLMProviderError
from cua.discovery.llm.groq_provider import GroqProvider

from .fakes import (
    FakeGroqChoice,
    FakeGroqMessage,
    FakeGroqResponse,
    FakeGroqToolCall,
    FakeGroqToolCallFunction,
    ScriptedGroqCreate,
    make_groq_api_status_error,
)


def _make_provider(monkeypatch, script):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-tests")
    provider = GroqProvider("openai/gpt-oss-120b", system_prompt="You are a test system prompt.")
    provider._client.chat.completions.create = ScriptedGroqCreate(script)
    return provider


def _tool_call(call_id: str, name: str, args_json: str = "{}") -> FakeGroqToolCall:
    return FakeGroqToolCall(id=call_id, function=FakeGroqToolCallFunction(name=name, arguments=args_json))


def test_missing_api_key_raises_llm_provider_error(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    try:
        GroqProvider("openai/gpt-oss-120b", system_prompt="x")
        raised = False
    except LLMProviderError:
        raised = True
    assert raised


def test_start_seeds_conversation_with_system_and_user_turns(monkeypatch):
    provider = _make_provider(monkeypatch, script=[])
    provider.start(
        goal="Transfer 20.00 from account 15009 to account 15120.",
        declared_params={"amount": "20.00"},
        observation_text="URL: https://x/transfer.htm",
    )
    assert len(provider._messages) == 2
    assert provider._messages[0]["role"] == "system"
    assert provider._messages[1]["role"] == "user"
    assert "Transfer 20.00" in provider._messages[1]["content"]
    assert "amount=20.00" in provider._messages[1]["content"]
    assert "transfer.htm" in provider._messages[1]["content"]


def test_propose_action_extracts_single_actionable_call(monkeypatch):
    response = FakeGroqResponse(
        choices=[
            FakeGroqChoice(
                message=FakeGroqMessage(
                    tool_calls=[_tool_call("call_1", "click", '{"target_description": "Transfer button"}')]
                )
            )
        ]
    )
    provider = _make_provider(monkeypatch, script=[response])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    decision = provider.propose_action()

    assert isinstance(decision, LLMActionCall)
    assert decision.name == "click"
    assert decision.args == {"target_description": "Transfer button"}


def test_propose_action_zero_calls_returns_decision_error(monkeypatch):
    response = FakeGroqResponse(choices=[FakeGroqChoice(message=FakeGroqMessage(content="I'm not sure."))])
    provider = _make_provider(monkeypatch, script=[response])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    decision = provider.propose_action()
    assert isinstance(decision, LLMDecisionError)


def test_second_turn_text_only_response_after_tool_result_is_a_decision_error_not_a_crash(monkeypatch):
    """Regression test for the real bug: after a valid tool call and its
    result are threaded back, Groq's natural next response is often plain
    text (e.g. "the finish tool was called successfully..."), not another
    tool call. With tool_choice="required" this used to make the Groq API
    itself return a 400, which surfaced as a wrongly-categorized
    LLMProviderError. It must instead become the ordinary, provider-
    neutral LLMDecisionError for zero actionable calls — not a crash, not
    an API-error failure category — and the request itself must succeed
    (no 400) since tool_choice is no longer "required".
    """
    first_response = FakeGroqResponse(
        choices=[FakeGroqChoice(message=FakeGroqMessage(tool_calls=[_tool_call("call_1", "finish")]))]
    )
    second_response = FakeGroqResponse(
        choices=[
            FakeGroqChoice(
                message=FakeGroqMessage(content="The test is complete—the finish tool was called successfully.")
            )
        ]
    )
    provider = _make_provider(monkeypatch, script=[first_response, second_response])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    first_decision = provider.propose_action()
    assert isinstance(first_decision, LLMActionCall)
    provider.record_tool_result(result_text="Confirmed: goal achieved.", is_error=False)

    second_decision = provider.propose_action()
    assert isinstance(second_decision, LLMDecisionError)


def test_propose_action_multiple_calls_returns_decision_error(monkeypatch):
    response = FakeGroqResponse(
        choices=[
            FakeGroqChoice(
                message=FakeGroqMessage(
                    tool_calls=[_tool_call("call_1", "click"), _tool_call("call_2", "type_text")]
                )
            )
        ]
    )
    provider = _make_provider(monkeypatch, script=[response])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    decision = provider.propose_action()
    assert isinstance(decision, LLMDecisionError)


def test_propose_action_unknown_tool_name_returns_decision_error(monkeypatch):
    response = FakeGroqResponse(
        choices=[FakeGroqChoice(message=FakeGroqMessage(tool_calls=[_tool_call("call_1", "delete_everything")]))]
    )
    provider = _make_provider(monkeypatch, script=[response])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    decision = provider.propose_action()
    assert isinstance(decision, LLMDecisionError)


def test_propose_action_malformed_arguments_json_does_not_crash(monkeypatch):
    response = FakeGroqResponse(
        choices=[FakeGroqChoice(message=FakeGroqMessage(tool_calls=[_tool_call("call_1", "click", "{not json")]))]
    )
    provider = _make_provider(monkeypatch, script=[response])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    decision = provider.propose_action()
    assert isinstance(decision, LLMActionCall)
    assert decision.args == {}


def test_propose_action_wraps_api_status_error_as_llm_provider_error(monkeypatch):
    provider = _make_provider(monkeypatch, script=[make_groq_api_status_error(503, "overloaded")])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    try:
        provider.propose_action()
        raised = False
    except LLMProviderError as exc:
        raised = True
        message = str(exc)
        assert "overloaded" in message
        assert "code=503" in message
        assert "openai/gpt-oss-120b" in message
        assert "phase=initial_decision" in message
    assert raised


def test_generic_request_failure_wraps_as_llm_provider_error(monkeypatch):
    provider = _make_provider(monkeypatch, script=[ConnectionError("network down")])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    try:
        provider.propose_action()
        raised = False
    except LLMProviderError as exc:
        raised = True
        assert "network down" in str(exc)
    assert raised


def test_second_turn_api_error_is_tagged_tool_result_turn(monkeypatch):
    first_response = FakeGroqResponse(
        choices=[FakeGroqChoice(message=FakeGroqMessage(tool_calls=[_tool_call("call_1", "click")]))]
    )
    provider = _make_provider(
        monkeypatch, script=[first_response, make_groq_api_status_error(400, "bad turn")]
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


def test_record_tool_result_threads_assistant_and_tool_messages_correctly(monkeypatch):
    response = FakeGroqResponse(
        choices=[FakeGroqChoice(message=FakeGroqMessage(tool_calls=[_tool_call("call_42", "click")]))]
    )
    provider = _make_provider(monkeypatch, script=[response])
    provider.start(goal="g", declared_params={}, observation_text="obs")
    provider.propose_action()

    provider.record_tool_result(result_text="clicked ok", is_error=False)

    # Message history: [system, initial user turn, assistant tool-call turn, tool result turn].
    assert len(provider._messages) == 4
    assistant_message = provider._messages[2]
    assert assistant_message["role"] == "assistant"
    assert assistant_message["tool_calls"][0]["id"] == "call_42"
    assert assistant_message["tool_calls"][0]["function"]["name"] == "click"

    tool_message = provider._messages[3]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_42"
    assert tool_message["content"] == "clicked ok"


def test_record_invalid_decision_after_text_only_response_appends_plain_user_turn(monkeypatch):
    response = FakeGroqResponse(choices=[FakeGroqChoice(message=FakeGroqMessage(content="I'm done here."))])
    provider = _make_provider(monkeypatch, script=[response])
    provider.start(goal="g", declared_params={}, observation_text="obs")
    decision = provider.propose_action()
    assert isinstance(decision, LLMDecisionError)

    provider.record_invalid_decision("This discovery loop requires exactly one action per turn.")

    # [system, initial user turn, assistant text-only turn, corrective user turn]
    assert len(provider._messages) == 4
    corrective = provider._messages[-1]
    assert corrective["role"] == "user"
    assert "requires exactly one action" in corrective["content"]


def test_record_invalid_decision_after_multiple_calls_responds_to_each_raw_tool_call(monkeypatch):
    response = FakeGroqResponse(
        choices=[
            FakeGroqChoice(
                message=FakeGroqMessage(
                    tool_calls=[_tool_call("call_1", "click"), _tool_call("call_2", "type_text")]
                )
            )
        ]
    )
    provider = _make_provider(monkeypatch, script=[response])
    provider.start(goal="g", declared_params={}, observation_text="obs")
    decision = provider.propose_action()
    assert isinstance(decision, LLMDecisionError)

    provider.record_invalid_decision("pick exactly one")

    # [system, initial user turn, assistant turn, tool-response for call_1,
    #  tool-response for call_2, corrective user turn]
    assert len(provider._messages) == 6
    assert provider._messages[3] == {"role": "tool", "tool_call_id": "call_1", "content": "ERROR: pick exactly one"}
    assert provider._messages[4] == {"role": "tool", "tool_call_id": "call_2", "content": "ERROR: pick exactly one"}
    assert provider._messages[5] == {"role": "user", "content": "pick exactly one"}


def test_record_invalid_decision_does_not_leave_a_pending_call(monkeypatch):
    response = FakeGroqResponse(choices=[FakeGroqChoice(message=FakeGroqMessage(content="hmm"))])
    provider = _make_provider(monkeypatch, script=[response])
    provider.start(goal="g", declared_params={}, observation_text="obs")
    provider.propose_action()

    provider.record_invalid_decision("try again")

    assert provider._pending_tool_call_id is None
    assert provider._last_raw_tool_calls == []


def test_record_tool_result_prefixes_errors(monkeypatch):
    response = FakeGroqResponse(
        choices=[FakeGroqChoice(message=FakeGroqMessage(tool_calls=[_tool_call("call_1", "click")]))]
    )
    provider = _make_provider(monkeypatch, script=[response])
    provider.start(goal="g", declared_params={}, observation_text="obs")
    provider.propose_action()

    provider.record_tool_result(result_text="not found", is_error=True)

    assert provider._messages[-1]["content"] == "ERROR: not found"


def test_record_tool_result_does_nothing_when_no_pending_call(monkeypatch):
    response = FakeGroqResponse(choices=[FakeGroqChoice(message=FakeGroqMessage(content="hmm"))])
    provider = _make_provider(monkeypatch, script=[response])
    provider.start(goal="g", declared_params={}, observation_text="obs")
    provider.propose_action()  # zero calls -> LLMDecisionError -> no pending call
    message_count_before = len(provider._messages)

    provider.record_tool_result(result_text="irrelevant", is_error=False)

    assert len(provider._messages) == message_count_before


def test_provider_name_and_model_are_set(monkeypatch):
    provider = _make_provider(monkeypatch, script=[])
    assert provider.provider_name == "groq"
    assert provider.model == "openai/gpt-oss-120b"


def test_tool_choice_is_auto_not_required_and_parallel_tool_calls_disabled(monkeypatch):
    # Regression test for the real bug: tool_choice="required" made the
    # Groq API itself 400 ("Tool choice is required, but model did not
    # call a tool") whenever the model's natural response to a completed
    # tool result was plain text. "auto" lets the model respond either
    # way; our own validate_single_call() still enforces exactly one call
    # is required to advance the discovery loop.
    response = FakeGroqResponse(
        choices=[FakeGroqChoice(message=FakeGroqMessage(tool_calls=[_tool_call("call_1", "finish")]))]
    )
    recorder = ScriptedGroqCreate([response])
    provider = _make_provider(monkeypatch, script=[])
    provider._client.chat.completions.create = recorder
    provider.start(goal="g", declared_params={}, observation_text="obs")

    provider.propose_action()

    assert len(recorder.calls) == 1
    assert recorder.calls[0]["tool_choice"] == "auto"
    assert recorder.calls[0]["parallel_tool_calls"] is False


def test_api_key_never_appears_on_provider_public_state(monkeypatch):
    fake_key = "gsk-totally-fake-groq-key-do-not-log"
    monkeypatch.setenv("GROQ_API_KEY", fake_key)
    provider = GroqProvider("openai/gpt-oss-120b", system_prompt="x")

    assert fake_key not in repr(provider.__dict__)
    assert not any(fake_key in str(v) for k, v in vars(provider).items() if k != "_client")


def test_api_key_never_appears_in_error_message(monkeypatch):
    fake_key = "gsk-totally-fake-groq-key-do-not-log"
    monkeypatch.setenv("GROQ_API_KEY", fake_key)
    provider = GroqProvider("openai/gpt-oss-120b", system_prompt="x")
    provider._client.chat.completions.create = ScriptedGroqCreate([make_groq_api_status_error(500, "server error")])
    provider.start(goal="g", declared_params={}, observation_text="obs")

    try:
        provider.propose_action()
        raised = False
    except LLMProviderError as exc:
        raised = True
        assert fake_key not in str(exc)
    assert raised
