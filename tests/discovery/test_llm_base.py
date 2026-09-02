"""Tests for provider-neutral response-shape validation
(cua.discovery.llm.base.validate_single_call).

Operates on plain LLMActionCall lists — no provider-specific response
object involved at all, since this validation logic has no knowledge of
any particular SDK's shape. This is the same logic that previously lived
in cua.discovery.tools.extract_single_tool_call (Anthropic-specific);
it's unchanged in behavior, just now provider-neutral.
"""

from cua.discovery.llm import LLMActionCall, LLMDecisionError, validate_single_call


def test_exactly_one_actionable_call_is_accepted():
    result = validate_single_call([LLMActionCall(name="click", args={"target_description": "x"})])
    assert isinstance(result, LLMActionCall)
    assert result.name == "click"


def test_zero_calls_is_rejected():
    result = validate_single_call([])
    assert isinstance(result, LLMDecisionError)


def test_multiple_actionable_calls_are_rejected():
    result = validate_single_call(
        [LLMActionCall(name="click", args={}), LLMActionCall(name="type_text", args={})]
    )
    assert isinstance(result, LLMDecisionError)


def test_unknown_tool_name_does_not_count_as_actionable():
    result = validate_single_call([LLMActionCall(name="execute_script", args={"code": "..."})])
    assert isinstance(result, LLMDecisionError)


def test_one_actionable_plus_one_unknown_is_still_accepted():
    """An unknown/non-actionable call alongside exactly one real one is
    fine — only ACTIONABLE (known-name) calls count toward the "exactly
    one" requirement."""
    result = validate_single_call(
        [LLMActionCall(name="execute_script", args={}), LLMActionCall(name="click", args={})]
    )
    assert isinstance(result, LLMActionCall)
    assert result.name == "click"
