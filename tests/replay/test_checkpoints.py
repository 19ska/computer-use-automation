"""Tests for checkpoint evaluation (cua.replay.checkpoints)."""

from decimal import Decimal

from cua.artifact.schema import Checkpoint, CssLocator, ElementTarget, ParamRef
from cua.replay.checkpoints import evaluate_checkpoint

from .fakes import FakeElement, FakeLocator, FakePage


def test_text_contains_passes_with_literal_and_param_refs():
    page = FakePage()
    page.body_text = "Transfer Complete! $20.00 has been transferred from account #111 to account #222."

    checkpoint = Checkpoint(
        description="d",
        assertion="text_contains",
        expected_literal_text=["Transfer Complete!"],
        expected_value_refs=[
            ParamRef(name="amount"),
            ParamRef(name="from_account_id"),
            ParamRef(name="to_account_id"),
        ],
    )
    resolved_inputs = {
        "amount": Decimal("20.00"),
        "from_account_id": "111",
        "to_account_id": "222",
    }

    result = evaluate_checkpoint(page, checkpoint, resolved_inputs)
    assert result.passed is True


def test_text_contains_fails_when_a_value_is_missing():
    page = FakePage()
    page.body_text = "Transfer Complete! $20.00 has been transferred from account #111 to account #999."

    checkpoint = Checkpoint(
        description="d",
        assertion="text_contains",
        expected_literal_text=["Transfer Complete!"],
        expected_value_refs=[ParamRef(name="to_account_id")],
    )
    resolved_inputs = {"to_account_id": "222"}  # page actually shows 999

    result = evaluate_checkpoint(page, checkpoint, resolved_inputs)
    assert result.passed is False
    assert "222" in result.expected


def test_url_matches_checkpoint():
    page = FakePage()
    page.url = "https://parabank.parasoft.com/parabank/transfer.htm"

    checkpoint = Checkpoint(
        description="d", assertion="url_matches", expected_url_pattern=r"transfer\.htm$"
    )
    result = evaluate_checkpoint(page, checkpoint, {})
    assert result.passed is True


def test_url_matches_checkpoint_fails_on_mismatch():
    page = FakePage()
    page.url = "https://parabank.parasoft.com/parabank/overview.htm"

    checkpoint = Checkpoint(
        description="d", assertion="url_matches", expected_url_pattern=r"transfer\.htm$"
    )
    result = evaluate_checkpoint(page, checkpoint, {})
    assert result.passed is False


def test_element_visible_checkpoint():
    page = FakePage()
    page.css_locators["#confirm"] = FakeLocator([FakeElement(text="ok", visible=True)])

    checkpoint = Checkpoint(
        description="d",
        assertion="element_visible",
        target=ElementTarget(description="d", strategies=[CssLocator(selector="#confirm")]),
    )
    result = evaluate_checkpoint(page, checkpoint, {})
    assert result.passed is True


def test_element_visible_checkpoint_fails_when_not_found():
    page = FakePage()  # nothing registered

    checkpoint = Checkpoint(
        description="d",
        assertion="element_visible",
        target=ElementTarget(description="d", strategies=[CssLocator(selector="#missing")]),
    )
    result = evaluate_checkpoint(page, checkpoint, {})
    assert result.passed is False
