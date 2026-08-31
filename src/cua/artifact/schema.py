"""Typed, versioned capability artifact schema (Milestone 2).

This module defines the CONTRACT that a future discovery agent will
generate and a future deterministic replay engine will consume. Nothing in
this module executes anything — no Playwright, no LLM calls, no browser
actions. It is pure data modeling plus validation.

Security boundary (read before adding new fields):
    Authentication credentials, tokens, API keys, passwords, and other
    secrets must NEVER be represented inside a CapabilityArtifact. The
    only sanctioned way to express "this capability needs an
    authenticated session" is `SessionRequirement.auth_profile` — an
    OPAQUE reference (e.g. "parabank_demo") that a later runtime
    milestone resolves against environment/secret configuration before
    invoking this capability's steps. `auth_profile` is validated as a
    plain lowercase slug specifically so it cannot structurally hold an
    email address, password, or token.

    `LiteralRef` remains in the schema for genuinely non-sensitive fixed
    UI values (e.g. a constant dropdown option that never varies per
    call). This is enforced by CONTRACT and code review, not by content
    sniffing: a heuristic that pattern-matches literal strings for
    "looks like a secret" produces both false positives (rejecting
    legitimate non-sensitive text) and false negatives (real secrets that
    don't happen to contain a flagged word), so no such check is
    implemented here. Never put a credential, token, or password in a
    LiteralRef — if a capability needs to authenticate, express that via
    `SessionRequirement` instead.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Union
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator

# --------------------------------------------------------------------------
# Session / auth requirement — see the security boundary note above.
# --------------------------------------------------------------------------

_SLUG_PATTERN = r"^[a-z][a-z0-9_]*$"


class SessionRequirement(BaseModel):
    """Declares that a capability needs an authenticated session, without
    ever holding the credentials themselves.

    `auth_profile` is an opaque reference (e.g. "parabank_demo") that a
    later runtime milestone resolves against environment/secret
    configuration to establish a session before this capability's steps
    run. It is validated as a plain slug so it cannot structurally carry
    an email, password, token, or other secret value.
    """

    authenticated: bool
    auth_profile: str | None = Field(default=None, pattern=_SLUG_PATTERN)


# --------------------------------------------------------------------------
# Locator strategies — deliberately small: role/name, label/text, css.
# --------------------------------------------------------------------------


class RoleLocator(BaseModel):
    kind: Literal["role"] = "role"
    role: str
    name: str
    exact: bool = False


class LabelTextLocator(BaseModel):
    kind: Literal["label_text"] = "label_text"
    text: str
    exact: bool = False


class CssLocator(BaseModel):
    kind: Literal["css"] = "css"
    selector: str


LocatorStrategy = Annotated[
    Union[RoleLocator, LabelTextLocator, CssLocator], Field(discriminator="kind")
]


class ElementTarget(BaseModel):
    """An element to act on or read from, described as an ORDERED fallback
    chain of locator strategies. Replay tries strategies[0] first, then
    strategies[1], and so on.
    """

    description: str
    strategies: list[LocatorStrategy] = Field(min_length=1)
    notes: str | None = None


# --------------------------------------------------------------------------
# Symbolic value references.
# --------------------------------------------------------------------------


class ParamRef(BaseModel):
    """A symbolic reference to a declared InputParameter. Steps never
    persist the concrete value used during discovery — only this
    reference by name."""

    kind: Literal["param"] = "param"
    name: str


class LiteralRef(BaseModel):
    """A fixed, non-symbolic value baked into the artifact.

    Reserved for genuinely non-sensitive fixed UI values only (e.g. a
    dropdown option that never varies per call). NEVER use this for
    credentials, tokens, API keys, or any other secret — see the module
    docstring for why this boundary is enforced by contract/code-review
    rather than by a content heuristic.
    """

    kind: Literal["literal"] = "literal"
    value: str


ValueRef = Annotated[Union[ParamRef, LiteralRef], Field(discriminator="kind")]


# --------------------------------------------------------------------------
# Inputs / outputs.
# --------------------------------------------------------------------------

ValueType = Literal["string", "number", "decimal", "boolean"]


class InputParameter(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    type: ValueType
    required: bool = True
    description: str
    example: str | float | bool | None = None
    redact_in_logs: bool = False


class ExtractionSpec(BaseModel):
    """Declares HOW and WHERE replay reads an output's value from the page.

    `pattern`/`capture_group` let one visible string (e.g. a confirmation
    sentence) be parsed into several typed outputs without the replay
    engine needing bespoke per-capability code — the parsing rule itself
    is data, validated here for well-formedness, not executed here.
    """

    target: ElementTarget
    source: Literal["text", "attribute"] = "text"
    attribute_name: str | None = None
    pattern: str | None = None
    capture_group: int | None = None

    @model_validator(mode="after")
    def _check_extraction_fields(self) -> "ExtractionSpec":
        if self.source == "attribute" and not self.attribute_name:
            raise ValueError("source='attribute' requires 'attribute_name'")
        if self.source == "text" and self.attribute_name is not None:
            raise ValueError("attribute_name is only valid when source='attribute'")

        if self.pattern is not None:
            if not self.pattern.strip():
                raise ValueError("pattern must be non-empty when provided")
            try:
                compiled = re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"pattern is not a valid regex: {exc}") from exc
            if self.capture_group is None:
                raise ValueError("capture_group is required when pattern is set")
            if not (0 <= self.capture_group <= compiled.groups):
                raise ValueError(
                    f"capture_group {self.capture_group} is out of range for "
                    f"pattern with {compiled.groups} group(s)"
                )
        elif self.capture_group is not None:
            raise ValueError("capture_group requires 'pattern' to be set")

        return self


class OutputField(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    type: ValueType
    description: str
    extraction: ExtractionSpec


# --------------------------------------------------------------------------
# Retry policy.
# --------------------------------------------------------------------------


class RetryPolicy(BaseModel):
    max_attempts: int = Field(default=1, ge=1, le=10)
    backoff_ms: int = Field(default=0, ge=0)
    retry_on: list[Literal["timeout", "detached_element", "navigation_pending"]] = []


# --------------------------------------------------------------------------
# Checkpoints.
# --------------------------------------------------------------------------


class Checkpoint(BaseModel):
    description: str
    assertion: Literal["url_matches", "element_visible", "element_hidden", "text_contains"]
    target: ElementTarget | None = None
    expected_url_pattern: str | None = None
    expected_literal_text: list[str] = []
    expected_value_refs: list[ValueRef] = []

    @model_validator(mode="after")
    def _check_assertion_fields(self) -> "Checkpoint":
        if self.assertion == "url_matches" and not self.expected_url_pattern:
            raise ValueError("url_matches checkpoints require 'expected_url_pattern'")
        if self.assertion in ("element_visible", "element_hidden") and self.target is None:
            raise ValueError(f"{self.assertion} checkpoints require 'target'")
        if self.assertion == "text_contains" and not (
            self.expected_literal_text or self.expected_value_refs
        ):
            raise ValueError(
                "text_contains checkpoints require at least one of "
                "'expected_literal_text' or 'expected_value_refs'"
            )
        return self


# --------------------------------------------------------------------------
# Steps.
# --------------------------------------------------------------------------

ActionType = Literal["navigate", "click", "type", "select_option", "wait_for", "extract"]


class ActionStep(BaseModel):
    step_id: int = Field(ge=1)
    action: ActionType
    target: ElementTarget | None = None
    url: str | None = None
    value: ValueRef | None = None
    output_ref: str | None = None
    checkpoint: Checkpoint | None = None
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    risk: Literal["safe", "risky"] = "safe"

    @model_validator(mode="after")
    def _check_action_fields(self) -> "ActionStep":
        action = self.action
        has_target = self.target is not None
        has_url = self.url is not None
        has_value = self.value is not None
        has_output_ref = self.output_ref is not None

        if action == "navigate":
            if not has_url:
                raise ValueError("'navigate' steps require 'url'")
            if has_target or has_value or has_output_ref:
                raise ValueError("'navigate' steps must only set 'url'")
        elif action == "extract":
            if not has_output_ref:
                raise ValueError("'extract' steps require 'output_ref'")
            if has_target or has_url or has_value:
                raise ValueError(
                    "'extract' steps must only set 'output_ref' — the "
                    "extraction target/source live on OutputField.extraction"
                )
        elif action in ("click", "wait_for"):
            if not has_target:
                raise ValueError(f"'{action}' steps require 'target'")
            if has_url or has_value or has_output_ref:
                raise ValueError(f"'{action}' steps must only set 'target'")
        elif action in ("type", "select_option"):
            if not (has_target and has_value):
                raise ValueError(f"'{action}' steps require both 'target' and 'value'")
            if has_url or has_output_ref:
                raise ValueError(f"'{action}' steps must not set 'url' or 'output_ref'")

        return self


# --------------------------------------------------------------------------
# Business outcome detectors.
# --------------------------------------------------------------------------


class BusinessOutcomeDetector(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    description: str
    target: ElementTarget
    contains_text: str
    origin: Literal["session_establishment", "capability_execution"] = "capability_execution"


# --------------------------------------------------------------------------
# Policy metadata.
# --------------------------------------------------------------------------


class PolicyMetadata(BaseModel):
    allowed_domains: list[str] = Field(min_length=1)
    allowed_actions: list[ActionType] = Field(min_length=1)
    requires_approval: bool = False
    approval_threshold_param: str | None = None
    approval_threshold_value: Decimal | None = None
    data_classification: Literal["none", "internal", "regulated_pii"] = "none"

    @model_validator(mode="after")
    def _check_threshold_pair(self) -> "PolicyMetadata":
        has_param = self.approval_threshold_param is not None
        has_value = self.approval_threshold_value is not None
        if has_param != has_value:
            raise ValueError(
                "approval_threshold_param and approval_threshold_value must "
                "be set together"
            )
        return self


# --------------------------------------------------------------------------
# Root artifact.
# --------------------------------------------------------------------------


class CapabilityArtifact(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
    capability_version: int = Field(ge=1)
    display_name: str
    description: str
    target_app: str
    vendor_product: str | None = None

    session_requirement: SessionRequirement

    inputs: list[InputParameter] = Field(min_length=1)
    outputs: list[OutputField] = Field(min_length=1)
    steps: list[ActionStep] = Field(min_length=1)
    success_checkpoint: Checkpoint
    business_outcomes: list[BusinessOutcomeDetector] = []

    policy: PolicyMetadata
    created_at: datetime
    created_by: Literal["discovery_agent", "human_edited"]
    discovery_run_id: str
    notes: str | None = None

    # -- cross-field integrity checks --------------------------------

    @model_validator(mode="after")
    def _check_unique_names_and_ids(self) -> "CapabilityArtifact":
        input_names = [i.name for i in self.inputs]
        if len(input_names) != len(set(input_names)):
            raise ValueError("input names must be unique")

        output_names = [o.name for o in self.outputs]
        if len(output_names) != len(set(output_names)):
            raise ValueError("output names must be unique")

        step_ids = [s.step_id for s in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step_id values must be unique")

        outcome_codes = [b.code for b in self.business_outcomes]
        if len(outcome_codes) != len(set(outcome_codes)):
            raise ValueError("business_outcomes codes must be unique")

        return self

    @model_validator(mode="after")
    def _check_param_refs_resolve(self) -> "CapabilityArtifact":
        input_names = {i.name for i in self.inputs}
        for ref in self._all_value_refs():
            if isinstance(ref, ParamRef) and ref.name not in input_names:
                raise ValueError(
                    f"step or checkpoint references unknown input parameter '{ref.name}'"
                )
        return self

    @model_validator(mode="after")
    def _check_extract_steps_match_outputs(self) -> "CapabilityArtifact":
        output_names = {o.name for o in self.outputs}
        extract_refs = [s.output_ref for s in self.steps if s.action == "extract"]

        unknown = set(extract_refs) - output_names
        if unknown:
            raise ValueError(f"extract steps reference unknown outputs: {sorted(unknown)}")

        counts: dict[str, int] = {}
        for ref in extract_refs:
            counts[ref] = counts.get(ref, 0) + 1

        missing = output_names - set(extract_refs)
        if missing:
            raise ValueError(f"outputs with no extract step: {sorted(missing)}")

        duplicated = [name for name, count in counts.items() if count > 1]
        if duplicated:
            raise ValueError(f"outputs extracted by more than one step: {sorted(duplicated)}")

        return self

    @model_validator(mode="after")
    def _check_target_domain_allowed(self) -> "CapabilityArtifact":
        host = urlparse(self.target_app).netloc or self.target_app
        allowed = self.policy.allowed_domains
        if not any(host == d or host.endswith(f".{d}") for d in allowed):
            raise ValueError(
                f"target_app host '{host}' is not in policy.allowed_domains {allowed}"
            )
        return self

    def _all_value_refs(self) -> list[ParamRef | LiteralRef]:
        refs: list[ParamRef | LiteralRef] = []
        for step in self.steps:
            if step.value is not None:
                refs.append(step.value)
            if step.checkpoint is not None:
                refs.extend(step.checkpoint.expected_value_refs)
        refs.extend(self.success_checkpoint.expected_value_refs)
        return refs
