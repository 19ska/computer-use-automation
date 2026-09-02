"""Tests for cua.discovery.executor — reuses the same FakePage/FakeLocator
fixtures as the replay test suite, since the executor resolves targets
through the identical cua.replay.locators.resolve_target function."""

from cua.artifact.schema import CssLocator, RoleLocator
from cua.discovery.executor import execute_action
from cua.discovery.target_resolution import _associated_control_xpath, _EDITABLE_TAGS, _SELECT_TAGS

from ..replay.fakes import FakeElement, FakeLocator, FakePage

BASE_URL = "https://parabank.parasoft.com/parabank"
DECLARED_PARAMS = {"amount": "20.00", "from_account_id": "15009", "to_account_id": "15120"}


def test_navigate_executes_and_reports_resulting_url():
    page = FakePage()
    outcome = execute_action(
        page, "navigate", {"url_path": "/transfer.htm"}, base_url=BASE_URL, declared_params={}
    )
    assert outcome.ok is True
    assert page.goto_calls == [f"{BASE_URL}/transfer.htm"]


def test_click_resolves_target_and_clicks():
    page = FakePage()
    element = FakeElement(text="Transfer")
    page.role_locators[("button", "Transfer")] = FakeLocator([element])

    outcome = execute_action(
        page,
        "click",
        {"target_description": "Transfer button", "accessible_role": "button", "accessible_name": "Transfer"},
        base_url=BASE_URL,
        declared_params=DECLARED_PARAMS,
    )
    assert outcome.ok is True
    assert element.clicked is True
    assert outcome.resolved_locator_strategy == "role#0"
    assert outcome.resolved_locator == RoleLocator(role="button", name="Transfer")


def test_click_falls_back_to_label_text_and_is_unaffected_by_the_type_text_fix():
    page = FakePage()
    element = FakeElement(text="Transfer Funds")
    page.text_locators["Transfer Funds"] = FakeLocator([element])

    outcome = execute_action(
        page,
        "click",
        {"target_description": "Transfer Funds link", "accessible_role": None, "accessible_name": "Transfer Funds"},
        base_url=BASE_URL,
        declared_params=DECLARED_PARAMS,
    )
    assert outcome.ok is True
    assert element.clicked is True
    assert outcome.resolved_locator_strategy == "label_text#0"


def test_type_text_resolves_via_role_when_available():
    page = FakePage()
    element = FakeElement(text="", tag_name="input")
    page.role_locators[("textbox", "Amount")] = FakeLocator([element])

    outcome = execute_action(
        page,
        "type_text",
        {
            "target_description": "Amount field",
            "accessible_role": "textbox",
            "accessible_name": "Amount",
            "value": "20.00",
        },
        base_url=BASE_URL,
        declared_params=DECLARED_PARAMS,
    )
    assert outcome.ok is True
    assert element.filled_value == "20.00"
    assert outcome.value_source is not None
    assert outcome.value_source.kind == "param"
    assert outcome.value_source.name == "amount"
    assert outcome.resolved_locator == RoleLocator(role="textbox", name="Amount")


def test_type_text_never_fills_a_plain_label_text_node():
    """Regression test for the real bug: Gemini asked for
    accessible_role="textbox", accessible_name="Amount" but the live page
    has no accessible name wired to the input (legacy markup), so the role
    locator found nothing. The resolver used to fall back to a
    LabelTextLocator, which matched the visible <b>Amount:</b> label node
    itself, and Playwright's .fill() then raised a low-level exception.
    Now there is no LabelTextLocator candidate for type_text at all — only
    an associated-editable-control fallback — so a label-only page (no
    real input registered) must fail cleanly, not attempt to fill a label.
    """
    page = FakePage()
    label_only = FakeElement(text="Amount:", tag_name="b")
    page.text_locators["Amount"] = FakeLocator([label_only])  # only a label node registered, no input anywhere

    outcome = execute_action(
        page,
        "type_text",
        {
            "target_description": "Amount field",
            "accessible_role": "textbox",
            "accessible_name": "Amount",
            "value": "20.00",
        },
        base_url=BASE_URL,
        declared_params=DECLARED_PARAMS,
    )
    assert outcome.ok is False
    assert label_only.filled_value is None


def test_type_text_falls_back_to_associated_editable_control_when_role_is_unavailable():
    page = FakePage()
    editable = FakeElement(text="", tag_name="input")
    selector = _associated_control_xpath("Amount", _EDITABLE_TAGS, contenteditable=True)
    page.css_locators[selector] = FakeLocator([editable])

    outcome = execute_action(
        page,
        "type_text",
        {
            "target_description": "Amount field",
            "accessible_role": "textbox",
            "accessible_name": "Amount",
            "value": "20.00",
        },
        base_url=BASE_URL,
        declared_params=DECLARED_PARAMS,
    )
    assert outcome.ok is True
    assert editable.filled_value == "20.00"


def test_type_text_associated_control_fallback_compiles_to_id_based_css_never_xpath():
    """The internal xpath= fallback that just made this action succeed
    must never be persisted as the resolved_locator — it must be
    normalized into a real, artifact-compatible CSS selector derived from
    the resolved element's id.
    """
    page = FakePage()
    editable = FakeElement(text="", tag_name="input", attributes={"id": "amount"})
    selector = _associated_control_xpath("Amount", _EDITABLE_TAGS, contenteditable=True)
    page.css_locators[selector] = FakeLocator([editable])

    outcome = execute_action(
        page,
        "type_text",
        {
            "target_description": "Amount field",
            "accessible_role": "textbox",
            "accessible_name": "Amount",
            "value": "20.00",
        },
        base_url=BASE_URL,
        declared_params=DECLARED_PARAMS,
    )
    assert outcome.ok is True
    assert outcome.resolved_locator == CssLocator(selector="#amount")
    assert not outcome.resolved_locator.selector.startswith("xpath=")


def test_select_option_associated_control_fallback_compiles_to_id_based_css():
    page = FakePage()
    select_element = FakeElement(text="", tag_name="select", attributes={"id": "fromAccountId"})
    selector = _associated_control_xpath("From account #", _SELECT_TAGS)
    page.css_locators[selector] = FakeLocator([select_element])

    outcome = execute_action(
        page,
        "select_option",
        {
            "target_description": "From account dropdown",
            "accessible_role": "combobox",
            "accessible_name": "From account #",
            "value": "15009",
        },
        base_url=BASE_URL,
        declared_params=DECLARED_PARAMS,
    )
    assert outcome.ok is True
    assert outcome.resolved_locator == CssLocator(selector="#fromAccountId")


def test_associated_control_fallback_succeeds_even_when_no_artifact_locator_derivable():
    """Execution succeeding and an artifact-compatible locator being
    derivable are independent: an element with neither id nor name still
    lets the live action succeed, it just leaves resolved_locator unset
    for this step (a later compiler would fail closed on THAT run, not
    this execution).
    """
    page = FakePage()
    editable = FakeElement(text="", tag_name="input")  # no id, no name
    selector = _associated_control_xpath("Amount", _EDITABLE_TAGS, contenteditable=True)
    page.css_locators[selector] = FakeLocator([editable])

    outcome = execute_action(
        page,
        "type_text",
        {
            "target_description": "Amount field",
            "accessible_role": "textbox",
            "accessible_name": "Amount",
            "value": "20.00",
        },
        base_url=BASE_URL,
        declared_params=DECLARED_PARAMS,
    )
    assert outcome.ok is True
    assert editable.filled_value == "20.00"
    assert outcome.resolved_locator is None


def test_type_text_rejects_a_resolved_non_editable_element_before_calling_fill():
    """Even if the associated-control xpath somehow resolves to a
    non-editable node, the executor must reject it with a structured
    error instead of calling .fill() on it — the minimal actionability
    guard is the last line of defense, independent of how resolution
    happened to succeed.
    """
    page = FakePage()
    non_editable = FakeElement(text="Amount:", tag_name="b")
    selector = _associated_control_xpath("something vague", _EDITABLE_TAGS, contenteditable=True)
    page.css_locators[selector] = FakeLocator([non_editable])

    outcome = execute_action(
        page,
        "type_text",
        {"target_description": "something vague", "accessible_role": None, "accessible_name": None, "value": "x"},
        base_url=BASE_URL,
        declared_params=DECLARED_PARAMS,
    )
    assert outcome.ok is False
    assert "not an editable control" in outcome.error
    assert non_editable.filled_value is None


def test_select_option_resolves_via_role_when_available():
    page = FakePage()
    element = FakeElement(text="", tag_name="select")
    page.role_locators[("combobox", "Account Type")] = FakeLocator([element])

    outcome = execute_action(
        page,
        "select_option",
        {
            "target_description": "Account type dropdown",
            "accessible_role": "combobox",
            "accessible_name": "Account Type",
            "value": "SAVINGS",
        },
        base_url=BASE_URL,
        declared_params=DECLARED_PARAMS,
    )
    assert outcome.ok is True
    assert element.selected_value == "SAVINGS"
    assert outcome.value_source.kind == "literal"
    assert outcome.value_source.value == "SAVINGS"


def test_select_option_falls_back_to_associated_select_control():
    page = FakePage()
    select_element = FakeElement(text="", tag_name="select")
    selector = _associated_control_xpath("Account Type", _SELECT_TAGS)
    page.css_locators[selector] = FakeLocator([select_element])

    outcome = execute_action(
        page,
        "select_option",
        {
            "target_description": "Account type dropdown",
            "accessible_role": "combobox",
            "accessible_name": "Account Type",
            "value": "SAVINGS",
        },
        base_url=BASE_URL,
        declared_params=DECLARED_PARAMS,
    )
    assert outcome.ok is True
    assert select_element.selected_value == "SAVINGS"


def test_select_option_from_account_resolves_when_label_is_a_bare_text_node():
    """Regression test for the real ParaBank bug: "From account #" and
    "to account #" are bare text nodes sitting inline in the same
    paragraph as both <select>s — not wrapped in any element, and the
    selects themselves have no accessible name (role=combobox lookups
    find nothing). The fallback must still resolve the correct <select>.
    """
    page = FakePage()
    from_select = FakeElement(text="", tag_name="select")
    page.css_locators[_associated_control_xpath("From account #", _SELECT_TAGS)] = FakeLocator([from_select])

    outcome = execute_action(
        page,
        "select_option",
        {
            "target_description": "From account dropdown",
            "accessible_role": "combobox",
            "accessible_name": "From account #",
            "value": "15009",
        },
        base_url=BASE_URL,
        declared_params=DECLARED_PARAMS,
    )
    assert outcome.ok is True
    assert from_select.selected_value == "15009"
    assert outcome.value_source.kind == "param"
    assert outcome.value_source.name == "from_account_id"


def test_select_option_to_account_resolves_the_correct_distinct_select():
    """Both "from account #" and "to account #" labels exist on the same
    page — each must resolve to ITS OWN select, never the other one, and
    never ambiguously across both.
    """
    page = FakePage()
    from_select = FakeElement(text="", tag_name="select")
    to_select = FakeElement(text="", tag_name="select")
    page.css_locators[_associated_control_xpath("From account #", _SELECT_TAGS)] = FakeLocator([from_select])
    page.css_locators[_associated_control_xpath("to account #", _SELECT_TAGS)] = FakeLocator([to_select])

    outcome = execute_action(
        page,
        "select_option",
        {
            "target_description": "To account dropdown",
            "accessible_role": "combobox",
            "accessible_name": "to account #",
            "value": "15120",
        },
        base_url=BASE_URL,
        declared_params=DECLARED_PARAMS,
    )
    assert outcome.ok is True
    assert to_select.selected_value == "15120"
    assert from_select.selected_value is None


def test_select_option_label_trailing_colon_is_normalized():
    page = FakePage()
    select_element = FakeElement(text="", tag_name="select")
    # The generated selector matches "account type" with OR without a
    # trailing colon, so a label ending in ":" resolves the same select.
    page.css_locators[_associated_control_xpath("Account Type:", _SELECT_TAGS)] = FakeLocator([select_element])

    outcome = execute_action(
        page,
        "select_option",
        {
            "target_description": "Account type dropdown",
            "accessible_role": "combobox",
            "accessible_name": "Account Type:",
            "value": "SAVINGS",
        },
        base_url=BASE_URL,
        declared_params=DECLARED_PARAMS,
    )
    assert outcome.ok is True
    assert select_element.selected_value == "SAVINGS"


def test_select_option_ambiguous_associated_control_does_not_guess():
    """If the associated-control fallback matches more than one visible
    select, the resolver must fail rather than arbitrarily picking one —
    same "never guess" rule as everywhere else in this codebase.
    """
    page = FakePage()
    selector = _associated_control_xpath("Account", _SELECT_TAGS)
    page.css_locators[selector] = FakeLocator(
        [FakeElement(text="", tag_name="select"), FakeElement(text="", tag_name="select")]
    )

    outcome = execute_action(
        page,
        "select_option",
        {"target_description": "Account", "accessible_role": None, "accessible_name": "Account", "value": "x"},
        base_url=BASE_URL,
        declared_params=DECLARED_PARAMS,
    )
    assert outcome.ok is False


def test_select_option_never_resolves_to_visible_label_text():
    page = FakePage()
    label_only = FakeElement(text="Account Type:", tag_name="b")
    page.text_locators["Account Type"] = FakeLocator([label_only])  # only a label node, no select anywhere

    outcome = execute_action(
        page,
        "select_option",
        {
            "target_description": "Account type dropdown",
            "accessible_role": "combobox",
            "accessible_name": "Account Type",
            "value": "SAVINGS",
        },
        base_url=BASE_URL,
        declared_params=DECLARED_PARAMS,
    )
    assert outcome.ok is False
    assert label_only.selected_value is None


def test_select_option_rejects_a_resolved_non_select_element_before_calling_select_option():
    page = FakePage()
    non_select = FakeElement(text="Account Type:", tag_name="b")
    selector = _associated_control_xpath("something vague", _SELECT_TAGS)
    page.css_locators[selector] = FakeLocator([non_select])

    outcome = execute_action(
        page,
        "select_option",
        {"target_description": "something vague", "accessible_role": None, "accessible_name": None, "value": "x"},
        base_url=BASE_URL,
        declared_params=DECLARED_PARAMS,
    )
    assert outcome.ok is False
    assert "not a select/combobox control" in outcome.error
    assert non_select.selected_value is None


def test_unresolvable_target_returns_structured_failure_not_an_exception():
    page = FakePage()  # nothing registered anywhere

    outcome = execute_action(
        page,
        "click",
        {"target_description": "Ghost button", "accessible_role": "button", "accessible_name": "Ghost"},
        base_url=BASE_URL,
        declared_params=DECLARED_PARAMS,
    )
    assert outcome.ok is False
    assert outcome.error is not None
