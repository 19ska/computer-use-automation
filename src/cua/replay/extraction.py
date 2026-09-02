"""Turns an OutputField's ExtractionSpec into a concrete typed value.

No ParaBank-specific parsing logic lives here — the target, the source
(text/attribute), the regex pattern, and the capture group all come from
the artifact's ExtractionSpec. This same function works for any future
capability's outputs.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from cua.artifact.schema import OutputField, ValueType

from . import locators
from .locators import SupportsPageProtocol


class ExtractionError(Exception):
    """Raised when an output's declared extraction can't be satisfied."""


def extract_output(page: SupportsPageProtocol, output: OutputField) -> str | Decimal | float | bool:
    spec = output.extraction
    resolution = locators.resolve_target(page, spec.target)

    if spec.source == "text":
        raw = resolution.locator.inner_text()
    else:
        raw = resolution.locator.get_attribute(spec.attribute_name)
        if raw is None:
            raise ExtractionError(
                f"attribute '{spec.attribute_name}' not present on the resolved "
                f"element for output '{output.name}'"
            )

    if spec.pattern is not None:
        match = re.search(spec.pattern, raw)
        if match is None:
            raise ExtractionError(
                f"pattern did not match extracted text for output '{output.name}'. "
                f"Extracted text: {raw[:300]!r}"
            )
        raw_value = match.group(spec.capture_group)
    else:
        raw_value = raw.strip()

    return _coerce(raw_value, output.type, output.name)


def _coerce(raw_value: str, value_type: ValueType, output_name: str) -> str | Decimal | float | bool:
    raw_value = raw_value.strip()

    if value_type == "string":
        return raw_value

    if value_type == "decimal":
        try:
            return Decimal(raw_value.replace(",", ""))
        except InvalidOperation as exc:
            raise ExtractionError(
                f"could not convert {raw_value!r} to Decimal for output '{output_name}'"
            ) from exc

    if value_type == "number":
        try:
            return float(raw_value.replace(",", ""))
        except ValueError as exc:
            raise ExtractionError(
                f"could not convert {raw_value!r} to number for output '{output_name}'"
            ) from exc

    if value_type == "boolean":
        return raw_value.lower() in ("true", "yes", "1")

    raise AssertionError(f"unhandled output type: {value_type}")  # pragma: no cover
