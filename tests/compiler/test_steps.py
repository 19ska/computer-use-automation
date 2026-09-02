"""Tests for cua.compiler.steps — mapping one DiscoveryEvent into one
ActionStep, consuming resolved_locator/value_source directly."""

import pytest

from cua.artifact.schema import CssLocator, LabelTextLocator, ParamRef, RoleLocator
from cua.compiler.events import CompilationError, DiscoveryEvent
from cua.compiler.steps import compile_step

KNOWN_PARAMS = frozenset({"amount", "from_account_id", "to_account_id"})


def _event(**overrides) -> DiscoveryEvent:
    base = dict(
        step_number=1, action="click", outcome="ok", provider="groq", model="qwen/qwen3.6-27b",
        target_description="Transfer submit button", accessible_role="button", accessible_name="Transfer",
        value_source=None, resolved_locator={"kind": "role", "role": "button", "name": "Transfer"},
        url_path=None,
    )
    base.update(overrides)
    return DiscoveryEvent(**base)


def test_navigate_uses_recorded_url_path():
    event = _event(action="navigate", url_path="/transfer.htm", resolved_locator=None, target_description=None)
    step = compile_step(event, 1, known_params=KNOWN_PARAMS)
    assert step.action == "navigate"
    assert step.url == "/transfer.htm"
    assert step.target is None


def test_navigate_missing_url_path_is_rejected():
    event = _event(action="navigate", url_path=None, resolved_locator=None, target_description=None)
    with pytest.raises(CompilationError, match="url_path"):
        compile_step(event, 1, known_params=KNOWN_PARAMS)


def test_click_maps_directly_and_preserves_role_locator():
    event = _event(action="click")
    step = compile_step(event, 3, known_params=KNOWN_PARAMS)
    assert step.action == "click"
    assert step.step_id == 3
    assert step.target.strategies == [RoleLocator(role="button", name="Transfer")]


def test_type_text_maps_to_type_action_and_preserves_param_ref():
    event = _event(
        action="type_text", target_description="Amount field", accessible_role="textbox", accessible_name="Amount",
        resolved_locator={"kind": "css", "selector": "#amount"},
        value_source={"kind": "param", "name": "amount"},
    )
    step = compile_step(event, 2, known_params=KNOWN_PARAMS)
    assert step.action == "type"
    assert step.value == ParamRef(name="amount")
    assert step.target.strategies == [CssLocator(selector="#amount")]


def test_select_option_preserves_param_ref():
    event = _event(
        action="select_option", target_description="From account dropdown",
        accessible_role="combobox", accessible_name="From account #",
        resolved_locator={"kind": "css", "selector": "#fromAccountId"},
        value_source={"kind": "param", "name": "from_account_id"},
    )
    step = compile_step(event, 2, known_params=KNOWN_PARAMS)
    assert step.action == "select_option"
    assert step.value == ParamRef(name="from_account_id")
    assert step.target.strategies == [CssLocator(selector="#fromAccountId")]


def test_label_text_locator_is_preserved():
    event = _event(action="click", resolved_locator={"kind": "label_text", "text": "Transfer Funds"})
    step = compile_step(event, 1, known_params=KNOWN_PARAMS)
    assert step.target.strategies == [LabelTextLocator(text="Transfer Funds")]


def test_null_resolved_locator_is_rejected():
    event = _event(action="click", resolved_locator=None)
    with pytest.raises(CompilationError, match="resolved_locator"):
        compile_step(event, 1, known_params=KNOWN_PARAMS)


def test_unsupported_locator_kind_is_rejected():
    event = _event(action="click", resolved_locator={"kind": "xpath", "selector": "//button"})
    with pytest.raises(CompilationError, match="unsupported resolved_locator kind"):
        compile_step(event, 1, known_params=KNOWN_PARAMS)


def test_xpath_disguised_as_css_is_rejected():
    """Defense in depth: even if a malformed/legacy evidence file somehow
    contained an xpath= selector under kind='css', the compiler must
    still refuse to persist it — never trust the on-disk invariant
    blindly."""
    event = _event(
        action="type_text", value_source={"kind": "param", "name": "amount"},
        resolved_locator={"kind": "css", "selector": "xpath=//input[1]"},
    )
    with pytest.raises(CompilationError, match="XPath"):
        compile_step(event, 1, known_params=KNOWN_PARAMS)


def test_unknown_param_ref_is_rejected():
    event = _event(
        action="type_text", value_source={"kind": "param", "name": "not_a_declared_param"},
        resolved_locator={"kind": "css", "selector": "#amount"},
    )
    with pytest.raises(CompilationError, match="unknown input parameter"):
        compile_step(event, 1, known_params=KNOWN_PARAMS)


def test_literal_value_source_is_preserved():
    event = _event(
        action="select_option", value_source={"kind": "literal", "value": "SAVINGS"},
        resolved_locator={"kind": "css", "selector": "#accountType"},
    )
    step = compile_step(event, 1, known_params=KNOWN_PARAMS)
    assert step.value.kind == "literal"
    assert step.value.value == "SAVINGS"


def test_missing_value_source_for_type_text_is_rejected():
    event = _event(action="type_text", value_source=None, resolved_locator={"kind": "css", "selector": "#amount"})
    with pytest.raises(CompilationError, match="value_source"):
        compile_step(event, 1, known_params=KNOWN_PARAMS)


def test_finish_and_give_up_have_no_action_mapping():
    for action in ("finish", "give_up"):
        event = _event(action=action, resolved_locator=None)
        with pytest.raises(CompilationError, match="no artifact action mapping"):
            compile_step(event, 1, known_params=KNOWN_PARAMS)
