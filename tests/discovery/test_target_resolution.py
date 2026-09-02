"""Tests for cua.discovery.target_resolution."""

from cua.artifact.schema import CssLocator, LabelTextLocator, LiteralRef, ParamRef, RoleLocator
from cua.discovery.target_resolution import (
    _associated_control_xpath,
    _EDITABLE_TAGS,
    _SELECT_TAGS,
    build_candidate_target,
    resolve_value_source,
)

DECLARED_PARAMS = {"amount": "20.00", "from_account_id": "15009", "to_account_id": "15120"}


def test_resolve_value_source_exact_string_match():
    ref = resolve_value_source("15009", DECLARED_PARAMS)
    assert isinstance(ref, ParamRef)
    assert ref.name == "from_account_id"


def test_resolve_value_source_numeric_equivalence_match():
    # "20" and "20.00" represent the same amount even though the strings differ.
    ref = resolve_value_source("20", DECLARED_PARAMS)
    assert isinstance(ref, ParamRef)
    assert ref.name == "amount"


def test_resolve_value_source_falls_back_to_literal():
    ref = resolve_value_source("SAVINGS", DECLARED_PARAMS)
    assert isinstance(ref, LiteralRef)
    assert ref.value == "SAVINGS"


def test_build_candidate_target_click_with_role_and_name():
    target = build_candidate_target(
        action="click", target_description="Transfer button", accessible_role="button", accessible_name="Transfer"
    )
    assert isinstance(target.strategies[0], RoleLocator)
    assert target.strategies[0].role == "button"
    assert target.strategies[0].name == "Transfer"
    assert isinstance(target.strategies[1], LabelTextLocator)
    assert target.strategies[1].text == "Transfer"


def test_build_candidate_target_click_with_name_only():
    target = build_candidate_target(
        action="click", target_description="Transfer link", accessible_role=None, accessible_name="Transfer Funds"
    )
    assert len(target.strategies) == 1
    assert isinstance(target.strategies[0], LabelTextLocator)


def test_build_candidate_target_click_with_no_hints_still_produces_a_target():
    target = build_candidate_target(
        action="click", target_description="something vague", accessible_role=None, accessible_name=None
    )
    assert len(target.strategies) == 1
    assert isinstance(target.strategies[0], LabelTextLocator)
    assert target.strategies[0].text == "something vague"


def test_build_candidate_target_type_text_never_uses_plain_label_text_locator():
    """Regression test for the real bug: type_text used to fall back to a
    LabelTextLocator on the accessible name, which resolves to the visible
    label node itself (e.g. <b>Amount:</b>) — not an editable control.
    """
    target = build_candidate_target(
        action="type_text", target_description="Amount field", accessible_role="textbox", accessible_name="Amount"
    )
    assert not any(isinstance(s, LabelTextLocator) for s in target.strategies)
    assert isinstance(target.strategies[0], RoleLocator)
    assert all(isinstance(s, CssLocator) for s in target.strategies[1:])


def test_build_candidate_target_type_text_fallback_targets_associated_editable_control():
    target = build_candidate_target(
        action="type_text", target_description="Amount", accessible_role=None, accessible_name="Amount"
    )
    assert len(target.strategies) == 1
    assert isinstance(target.strategies[0], CssLocator)
    assert target.strategies[0].selector == _associated_control_xpath("Amount", _EDITABLE_TAGS, contenteditable=True)


def test_build_candidate_target_type_text_tries_both_name_and_description_as_labels():
    target = build_candidate_target(
        action="type_text", target_description="Amount input field", accessible_role=None, accessible_name="Amount"
    )
    selectors = [s.selector for s in target.strategies]
    assert _associated_control_xpath("Amount", _EDITABLE_TAGS, contenteditable=True) in selectors
    assert _associated_control_xpath("Amount input field", _EDITABLE_TAGS, contenteditable=True) in selectors


def test_build_candidate_target_select_option_never_uses_plain_label_text_locator():
    target = build_candidate_target(
        action="select_option",
        target_description="Account Type dropdown",
        accessible_role="combobox",
        accessible_name="Account Type",
    )
    assert not any(isinstance(s, LabelTextLocator) for s in target.strategies)
    assert isinstance(target.strategies[0], RoleLocator)
    assert all(isinstance(s, CssLocator) for s in target.strategies[1:])
    assert target.strategies[1].selector == _associated_control_xpath("Account Type", _SELECT_TAGS)


def test_associated_control_xpath_matches_label_case_insensitively_with_or_without_colon():
    selector = _associated_control_xpath("amount", ("input",))
    assert '"amount"' in selector
    assert '"amount:"' in selector
    assert "following::input[1]" in selector


def test_associated_control_xpath_includes_contenteditable_branch_only_when_requested():
    without = _associated_control_xpath("Amount", ("input",), contenteditable=False)
    with_ce = _associated_control_xpath("Amount", ("input",), contenteditable=True)
    assert "contenteditable" not in without
    assert "contenteditable" in with_ce


def test_associated_control_xpath_matches_bare_text_nodes_not_just_wrapped_elements():
    """Regression test for the real bug: ParaBank's transfer page renders
    "From account #" and "to account #" as plain text sitting directly in
    a shared paragraph alongside both <select>s — not wrapped in any tag
    of their own (unlike Amount's `<b>Amount:</b>`). `//*[...]` only
    matches elements, so it could never find a bare text node; a
    `//text()[...]` branch is required.
    """
    selector = _associated_control_xpath("From account #", _SELECT_TAGS)
    assert "//text()[" in selector
    assert "following::select[1]" in selector
    # both the element-based and text-node-based branches must be present
    assert selector.count("following::select[1]") == 2
