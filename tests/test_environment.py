"""Milestone 1 sanity checks.

Confirms the dependency set (playwright, pydantic) is installed and
importable, and that Pydantic v2 model validation/serialization works as
expected. Deliberately does not launch a browser or hit the network — this
is meant to run in a second, as a fast environment check independent of the
manual exploration script.
"""

from decimal import Decimal

import pydantic
from playwright.sync_api import sync_playwright


def test_playwright_importable() -> None:
    # Just confirm the sync API entry point exists and is callable as a
    # context manager factory — doesn't launch a browser.
    assert callable(sync_playwright)


def test_pydantic_v2_is_installed() -> None:
    major_version = int(pydantic.VERSION.split(".")[0])
    assert major_version >= 2


def test_throwaway_model_round_trip() -> None:
    class ExampleAmount(pydantic.BaseModel):
        amount: Decimal
        label: str

    original = ExampleAmount(amount=Decimal("20.00"), label="test transfer")
    round_tripped = ExampleAmount.model_validate_json(original.model_dump_json())

    assert round_tripped == original
    assert round_tripped.amount == Decimal("20.00")
