"""Runtime input validation, checked BEFORE any browser action begins.

Given the artifact's declared `InputParameter`s and raw string values
(e.g. from the CLI), validates and converts them into typed Python values
that the executor can hand to Playwright.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from cua.artifact.schema import InputParameter


class InputValidationError(Exception):
    """Raised when runtime inputs don't satisfy the artifact's contract."""


def validate_inputs(
    declared: list[InputParameter], raw_inputs: dict[str, str]
) -> dict[str, Any]:
    declared_by_name = {p.name: p for p in declared}

    unknown = set(raw_inputs) - set(declared_by_name)
    if unknown:
        raise InputValidationError(f"unknown input(s) provided: {sorted(unknown)}")

    missing = [p.name for p in declared if p.required and p.name not in raw_inputs]
    if missing:
        raise InputValidationError(f"missing required input(s): {sorted(missing)}")

    resolved: dict[str, Any] = {}
    for name, raw_value in raw_inputs.items():
        resolved[name] = _coerce_input(raw_value, declared_by_name[name])
    return resolved


def _coerce_input(raw_value: str, param: InputParameter) -> Any:
    if param.type == "string":
        return raw_value

    if param.type == "decimal":
        try:
            return Decimal(raw_value)
        except InvalidOperation as exc:
            raise InputValidationError(
                f"input '{param.name}' must be a valid decimal, got {raw_value!r}"
            ) from exc

    if param.type == "number":
        try:
            return float(raw_value)
        except ValueError as exc:
            raise InputValidationError(
                f"input '{param.name}' must be numeric, got {raw_value!r}"
            ) from exc

    if param.type == "boolean":
        lowered = raw_value.strip().lower()
        if lowered not in ("true", "false"):
            raise InputValidationError(
                f"input '{param.name}' must be 'true' or 'false', got {raw_value!r}"
            )
        return lowered == "true"

    raise AssertionError(f"unhandled input type: {param.type}")  # pragma: no cover
