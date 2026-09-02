"""Typed, structured replay results.

Three shapes only, matching the three outcomes replay can actually
produce right now: a clean success, a legitimate typed business outcome
(not a crash), or a failure with enough context to debug. This is
intentionally NOT the full expected/recoverable/hard-failure taxonomy —
`failure_category` is a small, closed set covering only what Milestone 3
itself can produce.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

OutputValue = Union[str, Decimal, float, bool]

FailureCategory = Literal[
    "artifact_load_error",
    "input_validation_error",
    "session_establishment_error",
    "locator_not_found",
    "locator_ambiguous",
    "checkpoint_failed",
    "extraction_error",
    "unexpected_error",
]


class ReplaySuccess(BaseModel):
    status: Literal["success"] = "success"
    run_id: str
    capability_id: str
    outputs: dict[str, OutputValue]
    checkpoint_evidence: str
    evidence_dir: str


class ReplayBusinessOutcome(BaseModel):
    status: Literal["business_outcome"] = "business_outcome"
    run_id: str
    capability_id: str
    outcome_code: str
    message: str
    step_id: int | None = None
    evidence_dir: str


class ReplayFailure(BaseModel):
    status: Literal["failure"] = "failure"
    run_id: str
    capability_id: str
    failure_category: FailureCategory
    step_id: int | None = None
    expected: str | None = None
    observed: str | None = None
    screenshot_path: str | None = None
    exception_summary: str | None = None
    evidence_dir: str | None = None


ReplayResult = Annotated[
    Union[ReplaySuccess, ReplayBusinessOutcome, ReplayFailure], Field(discriminator="status")
]
