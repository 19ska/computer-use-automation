"""Tests for output extraction (cua.replay.extraction)."""

from decimal import Decimal

import pytest

from cua.artifact.schema import CssLocator, ElementTarget, ExtractionSpec, OutputField
from cua.replay.extraction import ExtractionError, extract_output

from .fakes import FakeElement, FakeLocator, FakePage

CONFIRMATION_TEXT = "Transfer Complete! $20.00 has been transferred from account #111 to account #222."
PATTERN = r"\$([0-9,]+\.\d{2}) has been transferred from account #(\d+) to account #(\d+)\."


def _target() -> ElementTarget:
    return ElementTarget(description="body", strategies=[CssLocator(selector="body")])


def test_extract_text_without_pattern():
    page = FakePage()
    page.css_locators["body"] = FakeLocator([FakeElement(text=CONFIRMATION_TEXT)])

    output = OutputField(
        name="confirmation_message",
        type="string",
        description="d",
        extraction=ExtractionSpec(target=_target(), source="text"),
    )
    assert extract_output(page, output) == CONFIRMATION_TEXT


def test_extract_with_regex_and_capture_group():
    page = FakePage()
    page.css_locators["body"] = FakeLocator([FakeElement(text=CONFIRMATION_TEXT)])

    output = OutputField(
        name="from_account_id",
        type="string",
        description="d",
        extraction=ExtractionSpec(target=_target(), source="text", pattern=PATTERN, capture_group=2),
    )
    assert extract_output(page, output) == "111"


def test_extract_decimal_conversion():
    page = FakePage()
    page.css_locators["body"] = FakeLocator([FakeElement(text=CONFIRMATION_TEXT)])

    output = OutputField(
        name="transferred_amount",
        type="decimal",
        description="d",
        extraction=ExtractionSpec(target=_target(), source="text", pattern=PATTERN, capture_group=1),
    )
    value = extract_output(page, output)
    assert value == Decimal("20.00")
    assert isinstance(value, Decimal)


def test_extract_attribute_source():
    page = FakePage()
    page.css_locators["a#logout"] = FakeLocator(
        [FakeElement(text="Log Out", attributes={"href": "/parabank/logout.htm"})]
    )

    output = OutputField(
        name="logout_href",
        type="string",
        description="d",
        extraction=ExtractionSpec(
            target=ElementTarget(description="d", strategies=[CssLocator(selector="a#logout")]),
            source="attribute",
            attribute_name="href",
        ),
    )
    assert extract_output(page, output) == "/parabank/logout.htm"


def test_extract_finds_confirmation_sentence_inside_a_noisy_larger_page():
    """Regression test for the Milestone 3 bug: the real page body is not
    just the confirmation sentence — it's surrounded by nav, headings, and
    footer text. The pattern must be unanchored (no ^/$) so it can find
    the sentence anywhere inside that larger text, for all three
    numeric/id outputs.
    """
    noisy_body = (
        "Experience the difference\n"
        "Solutions About Us Services Products Locations Admin Page\n"
        "Welcome Some User\n"
        "Transfer Complete!\n"
        "$20.00 has been transferred from account #111 to account #222.\n"
        "Home About Us Services Products Locations Forum Site Map Contact Us"
    )
    page = FakePage()
    page.css_locators["body"] = FakeLocator([FakeElement(text=noisy_body)])

    amount_output = OutputField(
        name="transferred_amount",
        type="decimal",
        description="d",
        extraction=ExtractionSpec(target=_target(), source="text", pattern=PATTERN, capture_group=1),
    )
    from_output = OutputField(
        name="from_account_id",
        type="string",
        description="d",
        extraction=ExtractionSpec(target=_target(), source="text", pattern=PATTERN, capture_group=2),
    )
    to_output = OutputField(
        name="to_account_id",
        type="string",
        description="d",
        extraction=ExtractionSpec(target=_target(), source="text", pattern=PATTERN, capture_group=3),
    )

    amount = extract_output(page, amount_output)
    assert amount == Decimal("20.00")
    assert isinstance(amount, Decimal)
    assert extract_output(page, from_output) == "111"
    assert extract_output(page, to_output) == "222"


def test_extract_pattern_no_match_raises():
    page = FakePage()
    page.css_locators["body"] = FakeLocator([FakeElement(text="something unrelated")])

    output = OutputField(
        name="transferred_amount",
        type="decimal",
        description="d",
        extraction=ExtractionSpec(target=_target(), source="text", pattern=PATTERN, capture_group=1),
    )
    with pytest.raises(ExtractionError, match="pattern did not match"):
        extract_output(page, output)


def test_extract_invalid_decimal_raises():
    page = FakePage()
    page.css_locators["body"] = FakeLocator([FakeElement(text="not-a-decimal")])

    output = OutputField(
        name="transferred_amount",
        type="decimal",
        description="d",
        extraction=ExtractionSpec(target=_target(), source="text"),
    )
    with pytest.raises(ExtractionError, match="could not convert"):
        extract_output(page, output)
