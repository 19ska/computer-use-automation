"""Typed, structured discovery results.

Mirrors the shape of cua.replay.results (success / business_outcome /
failure) — the same three-way outcome vocabulary applies to both a
discovery run and a replay run, since both ultimately either achieve the
goal, hit a legitimate known application outcome, or fail in a way that
needs debugging.

`provider`/`model` record exactly which LLM produced the run, without
being specific to any one provider's naming.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

DiscoveryFailureCategory = Literal[
    "session_establishment_error",
    "max_steps_exceeded",
    "timeout_exceeded",
    "repeated_action_failure",
    "give_up",
    "invalid_model_response",
    "llm_api_error",
    "unexpected_error",
]


class DiscoverySuccess(BaseModel):
    status: Literal["success"] = "success"
    run_id: str
    goal: str
    declared_parameters: dict[str, str]
    final_checkpoint_evidence: str
    evidence_dir: str
    step_count: int
    provider: str
    model: str


class DiscoveryBusinessOutcome(BaseModel):
    status: Literal["business_outcome"] = "business_outcome"
    run_id: str
    outcome_code: str
    message: str
    evidence_dir: str
    provider: str | None = None
    model: str | None = None


class DiscoveryFailure(BaseModel):
    status: Literal["failure"] = "failure"
    run_id: str
    failure_category: DiscoveryFailureCategory
    last_step: int | None = None
    reason: str
    screenshot_path: str | None = None
    evidence_dir: str | None = None
    provider: str | None = None
    model: str | None = None


DiscoveryResult = Annotated[
    Union[DiscoverySuccess, DiscoveryBusinessOutcome, DiscoveryFailure], Field(discriminator="status")
]
