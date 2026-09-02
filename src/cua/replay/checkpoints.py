"""Evaluates a Checkpoint against live page state.

Generic over the four assertion types the schema supports. Nothing here
is ParaBank-specific — the substrings/URLs/targets all come from the
artifact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from cua.artifact.schema import Checkpoint, LiteralRef, ParamRef, ValueRef

from . import locators
from .locators import LocatorAmbiguousError, LocatorNotFoundError, SupportsPageProtocol


@dataclass
class CheckpointResult:
    passed: bool
    expected: str
    observed: str


def evaluate_checkpoint(
    page: SupportsPageProtocol, checkpoint: Checkpoint, resolved_inputs: dict[str, Any]
) -> CheckpointResult:
    if checkpoint.assertion == "url_matches":
        return _evaluate_url_matches(page, checkpoint)
    if checkpoint.assertion in ("element_visible", "element_hidden"):
        return _evaluate_element_presence(page, checkpoint)
    if checkpoint.assertion == "text_contains":
        return _evaluate_text_contains(page, checkpoint, resolved_inputs)
    raise AssertionError(f"unhandled assertion type: {checkpoint.assertion}")  # pragma: no cover


def _evaluate_url_matches(page: SupportsPageProtocol, checkpoint: Checkpoint) -> CheckpointResult:
    observed_url = page.url  # type: ignore[attr-defined]
    passed = bool(re.search(checkpoint.expected_url_pattern, observed_url))
    return CheckpointResult(passed=passed, expected=checkpoint.expected_url_pattern, observed=observed_url)


def _evaluate_element_presence(page: SupportsPageProtocol, checkpoint: Checkpoint) -> CheckpointResult:
    try:
        resolution = locators.resolve_target(page, checkpoint.target)
        is_visible = resolution.locator.is_visible()
    except (LocatorNotFoundError, LocatorAmbiguousError):
        is_visible = False

    expected_visible = checkpoint.assertion == "element_visible"
    passed = is_visible == expected_visible
    return CheckpointResult(
        passed=passed,
        expected=f"{checkpoint.assertion} (target should be visible={expected_visible})",
        observed=f"visible={is_visible}",
    )


def _evaluate_text_contains(
    page: SupportsPageProtocol, checkpoint: Checkpoint, resolved_inputs: dict[str, Any]
) -> CheckpointResult:
    page_text = _scoped_text(page, checkpoint.target)

    expected_strings = list(checkpoint.expected_literal_text)
    for ref in checkpoint.expected_value_refs:
        expected_strings.append(_render_value_ref(ref, resolved_inputs))

    missing = [s for s in expected_strings if s not in page_text]
    passed = not missing
    return CheckpointResult(
        passed=passed,
        expected=f"text containing all of: {expected_strings} (missing: {missing})",
        observed=page_text[:1000],
    )


def _scoped_text(page: SupportsPageProtocol, target) -> str:  # noqa: ANN001
    if target is None:
        return page.inner_text("body")  # type: ignore[attr-defined]
    resolution = locators.resolve_target(page, target)
    return resolution.locator.inner_text()


def _render_value_ref(ref: ValueRef, resolved_inputs: dict[str, Any]) -> str:
    if isinstance(ref, ParamRef):
        return str(resolved_inputs[ref.name])
    if isinstance(ref, LiteralRef):
        return ref.value
    raise AssertionError(f"unhandled value ref: {ref!r}")  # pragma: no cover
