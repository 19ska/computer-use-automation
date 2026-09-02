"""Tests for the generic locator resolver (cua.replay.locators)."""

import pytest

from cua.artifact.schema import CssLocator, ElementTarget, LabelTextLocator, RoleLocator
from cua.replay.locators import LocatorAmbiguousError, LocatorNotFoundError, resolve_target

from .fakes import FakeElement, FakeLocator, FakePage


def _target(*strategies) -> ElementTarget:
    return ElementTarget(description="test target", strategies=list(strategies))


def test_resolve_prefers_first_matching_strategy():
    page = FakePage()
    page.role_locators[("button", "Transfer")] = FakeLocator([FakeElement(text="Transfer")])
    page.css_locators["input[value='Transfer']"] = FakeLocator([FakeElement(text="Transfer")])

    target = _target(
        RoleLocator(role="button", name="Transfer"), CssLocator(selector="input[value='Transfer']")
    )
    resolution = resolve_target(page, target)

    assert resolution.strategy_index == 0
    assert resolution.strategy.kind == "role"


def test_resolve_falls_back_to_second_strategy_when_first_finds_nothing():
    page = FakePage()
    page.css_locators["#fromAccountId"] = FakeLocator([FakeElement(text="16785")])
    # role locator intentionally not registered -> resolves to zero elements

    target = _target(
        RoleLocator(role="combobox", name="From Account"), CssLocator(selector="#fromAccountId")
    )
    resolution = resolve_target(page, target)

    assert resolution.strategy_index == 1
    assert resolution.strategy.kind == "css"


def test_resolve_falls_back_when_first_strategy_is_ambiguous():
    page = FakePage()
    page.text_locators["Submit"] = FakeLocator(
        [FakeElement(text="Submit"), FakeElement(text="Submit")]
    )
    page.css_locators["button#submit"] = FakeLocator([FakeElement(text="Submit")])

    target = _target(LabelTextLocator(text="Submit"), CssLocator(selector="button#submit"))
    resolution = resolve_target(page, target)

    assert resolution.strategy_index == 1
    assert resolution.strategy.kind == "css"


def test_resolve_raises_ambiguous_when_every_matching_strategy_is_ambiguous():
    page = FakePage()
    page.css_locators["div"] = FakeLocator([FakeElement(), FakeElement()])

    target = _target(CssLocator(selector="div"))
    with pytest.raises(LocatorAmbiguousError):
        resolve_target(page, target)


def test_resolve_raises_not_found_when_no_strategy_matches_anything():
    page = FakePage()  # nothing registered anywhere

    target = _target(
        RoleLocator(role="button", name="Ghost"), CssLocator(selector="#does-not-exist")
    )
    with pytest.raises(LocatorNotFoundError):
        resolve_target(page, target)


def test_resolve_does_not_fall_back_to_body_when_target_declares_no_such_strategy():
    """Regression test for the Milestone 3 bug: a completion-state target
    like `label_text: "Transfer Complete!"` must fail to resolve when that
    text isn't present, even though `body` trivially exists on the page.
    The resolver must only try what the target actually declares — it has
    no implicit "the document exists" fallback of its own.
    """
    page = FakePage()  # FakePage always has a default 'body' locator registered
    # Confirmation text is NOT registered anywhere -> page.text_locators is empty.

    target = _target(LabelTextLocator(text="Transfer Complete!"))
    with pytest.raises(LocatorNotFoundError):
        resolve_target(page, target)


def test_resolve_skips_invisible_elements():
    page = FakePage()
    page.css_locators["#hidden"] = FakeLocator([FakeElement(text="x", visible=False)])
    page.css_locators["#visible"] = FakeLocator([FakeElement(text="y", visible=True)])

    target = _target(CssLocator(selector="#hidden"), CssLocator(selector="#visible"))
    resolution = resolve_target(page, target)

    assert resolution.strategy_index == 1
    assert resolution.locator.inner_text() == "y"
