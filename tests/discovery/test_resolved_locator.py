"""Tests for cua.discovery.resolved_locator — normalizing a successful
LocatorResolution into an artifact-compatible locator, never persisting
an internal XPath string as though it were CSS.
"""

from cua.artifact.schema import CssLocator, LabelTextLocator, RoleLocator
from cua.discovery.resolved_locator import resolve_artifact_locator
from cua.replay.locators import LocatorResolution

from ..replay.fakes import FakeElement, FakeLocator


def _resolution(strategy, locator=None, index=0):
    return LocatorResolution(locator=locator or FakeLocator([FakeElement()]), strategy_index=index, strategy=strategy)


def test_role_locator_is_preserved_directly():
    strategy = RoleLocator(role="button", name="Transfer")
    result = resolve_artifact_locator(_resolution(strategy))
    assert result is strategy


def test_label_text_locator_is_preserved_directly():
    strategy = LabelTextLocator(text="Transfer Funds")
    result = resolve_artifact_locator(_resolution(strategy))
    assert result is strategy


def test_genuine_css_locator_is_preserved_directly():
    strategy = CssLocator(selector="#amount")
    result = resolve_artifact_locator(_resolution(strategy))
    assert result is strategy


def test_internal_xpath_locator_derives_id_based_css_from_resolved_element():
    element = FakeElement(attributes={"id": "fromAccountId"})
    strategy = CssLocator(selector='xpath=//text()[.="From account #"]/following::select[1]')
    result = resolve_artifact_locator(_resolution(strategy, locator=FakeLocator([element])))
    assert isinstance(result, CssLocator)
    assert result.selector == "#fromAccountId"


def test_internal_xpath_locator_falls_back_to_name_when_no_id():
    element = FakeElement(attributes={"name": "fromAccountId"})
    strategy = CssLocator(selector="xpath=//*[.]/following::select[1]")
    result = resolve_artifact_locator(_resolution(strategy, locator=FakeLocator([element])))
    assert isinstance(result, CssLocator)
    assert result.selector == '[name="fromAccountId"]'


def test_internal_xpath_locator_escapes_quotes_and_backslashes_in_name():
    element = FakeElement(attributes={"name": 'weird"name\\here'})
    strategy = CssLocator(selector="xpath=//*[.]/following::select[1]")
    result = resolve_artifact_locator(_resolution(strategy, locator=FakeLocator([element])))
    assert isinstance(result, CssLocator)
    assert result.selector == '[name="weird\\"name\\\\here"]'


def test_internal_xpath_locator_returns_none_when_no_id_or_name():
    element = FakeElement()  # no attributes at all
    strategy = CssLocator(selector="xpath=//*[.]/following::select[1]")
    result = resolve_artifact_locator(_resolution(strategy, locator=FakeLocator([element])))
    assert result is None


def test_internal_xpath_locator_rejects_unsafe_id_and_falls_back_to_name():
    element = FakeElement(attributes={"id": "not a valid css ident!", "name": "fromAccountId"})
    strategy = CssLocator(selector="xpath=//*[.]/following::select[1]")
    result = resolve_artifact_locator(_resolution(strategy, locator=FakeLocator([element])))
    assert isinstance(result, CssLocator)
    assert result.selector == '[name="fromAccountId"]'


def test_internal_xpath_locator_rejects_unsafe_id_with_no_name_fallback():
    element = FakeElement(attributes={"id": "123-starts-with-digit"})
    strategy = CssLocator(selector="xpath=//*[.]/following::select[1]")
    result = resolve_artifact_locator(_resolution(strategy, locator=FakeLocator([element])))
    assert result is None


def test_never_returns_an_xpath_css_locator():
    """The one property that must never be violated: no returned
    CssLocator's selector may start with 'xpath='."""
    cases = [
        (CssLocator(selector="xpath=//*[.]/following::select[1]"), FakeElement(attributes={"id": "x"})),
        (CssLocator(selector="xpath=//*[.]/following::select[1]"), FakeElement(attributes={"name": "x"})),
        (CssLocator(selector="xpath=//*[.]/following::select[1]"), FakeElement()),
        (CssLocator(selector="#already-css"), FakeElement()),
        (RoleLocator(role="combobox", name="From Account"), FakeElement()),
        (LabelTextLocator(text="Amount:"), FakeElement()),
    ]
    for strategy, element in cases:
        result = resolve_artifact_locator(_resolution(strategy, locator=FakeLocator([element])))
        if isinstance(result, CssLocator):
            assert not result.selector.startswith("xpath=")
