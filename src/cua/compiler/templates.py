"""CompilationTemplate: capability-specific static knowledge that cannot
be learned solely from a successful discovery action path — inputs,
outputs, session requirement, business outcomes, policy, the success
checkpoint, and the trailing wait/extract steps deterministic replay
needs after the discovered UI actions.

Generic compiler code (events.py, steps.py, compile.py) must never
contain capability-specific business logic — that logic lives entirely
in a template instance like PARABANK_TRANSFER_FUNDS_TEMPLATE below,
mirroring the same structure already proven by hand-authoring
examples/capabilities/parabank_transfer_funds.json (that fixture's steps
1-5 are exactly what a discovery-run compiler generates; its steps 6-10
plus everything outside `steps` is exactly template data).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from cua.artifact.schema import (
    ActionStep,
    BusinessOutcomeDetector,
    Checkpoint,
    CssLocator,
    ElementTarget,
    InputParameter,
    LabelTextLocator,
    OutputField,
    ParamRef,
    PolicyMetadata,
    RetryPolicy,
    SessionRequirement,
)

from .events import CompilationError


@dataclass(frozen=True)
class CompilationTemplate:
    capability_id: str
    capability_version: int
    display_name: str
    description: str
    target_app: str
    vendor_product: str | None
    session_requirement: SessionRequirement
    inputs: list[InputParameter]
    outputs: list[OutputField]
    trailing_steps: list[ActionStep]
    success_checkpoint: Checkpoint
    business_outcomes: list[BusinessOutcomeDetector]
    policy: PolicyMetadata
    # Milestone 6: capability-specific knowledge of which discovered click
    # is the state-changing/risky one (e.g. "Transfer"). Generic compiler
    # code (compile.py) only ever consults this set — it contains no
    # capability-specific logic of its own.
    risky_click_accessible_names: frozenset[str] = frozenset()


_CONFIRMATION_TARGET = ElementTarget(
    description="Transfer confirmation panel",
    strategies=[CssLocator(selector="body")],
    notes=(
        "Intentionally broad: matches the whole page body because a reliable narrower "
        "confirmation-container selector has not been identified. Safe here because "
        "extraction only runs after the wait_for step has already confirmed the "
        "confirmation state was reached using a state-specific locator."
    ),
)

_CONFIRMATION_PATTERN = r"\$([0-9,]+\.\d{2}) has been transferred from account #(\d+) to account #(\d+)\."

PARABANK_TRANSFER_FUNDS_TEMPLATE = CompilationTemplate(
    capability_id="parabank.transfer_funds",
    capability_version=1,
    display_name="Transfer Funds (ParaBank)",
    description="Transfer a specified amount from one ParaBank account to another and verify the transfer confirmation.",
    target_app="https://parabank.parasoft.com/parabank",
    vendor_product="ParaBank",
    session_requirement=SessionRequirement(authenticated=True, auth_profile="parabank_demo"),
    inputs=[
        InputParameter(name="amount", type="decimal", description="Amount to transfer.", example="20.00"),
        InputParameter(name="from_account_id", type="string", description="Source account id.", example="16785"),
        InputParameter(
            name="to_account_id", type="string", description="Destination account id.", example="16896"
        ),
    ],
    outputs=[
        OutputField(
            name="confirmation_message",
            type="string",
            description="Full visible confirmation text.",
            extraction={"target": _CONFIRMATION_TARGET, "source": "text"},
        ),
        OutputField(
            name="transferred_amount",
            type="decimal",
            description="Amount ParaBank reports as transferred.",
            extraction={
                "target": _CONFIRMATION_TARGET,
                "source": "text",
                "pattern": _CONFIRMATION_PATTERN,
                "capture_group": 1,
            },
        ),
        OutputField(
            name="from_account_id",
            type="string",
            description="Source account ParaBank reports.",
            extraction={
                "target": _CONFIRMATION_TARGET,
                "source": "text",
                "pattern": _CONFIRMATION_PATTERN,
                "capture_group": 2,
            },
        ),
        OutputField(
            name="to_account_id",
            type="string",
            description="Destination account ParaBank reports.",
            extraction={
                "target": _CONFIRMATION_TARGET,
                "source": "text",
                "pattern": _CONFIRMATION_PATTERN,
                "capture_group": 3,
            },
        ),
    ],
    trailing_steps=[
        ActionStep(
            step_id=1,
            action="wait_for",
            target=ElementTarget(
                description="Transfer confirmation appears",
                strategies=[LabelTextLocator(text="Transfer Complete!")],
                notes=(
                    "Deliberately no css:'body' fallback: body exists on every ParaBank page, "
                    "including the pre-submission form itself, so it is not a valid signal that "
                    "the confirmation state was actually reached."
                ),
            ),
            retry=RetryPolicy(max_attempts=3, backoff_ms=1000, retry_on=["timeout"]),
        ),
        ActionStep(step_id=2, action="extract", output_ref="confirmation_message"),
        ActionStep(step_id=3, action="extract", output_ref="transferred_amount"),
        ActionStep(step_id=4, action="extract", output_ref="from_account_id"),
        ActionStep(step_id=5, action="extract", output_ref="to_account_id"),
    ],
    success_checkpoint=Checkpoint(
        description="Transfer completed and confirmation shows amount, source, and destination.",
        assertion="text_contains",
        expected_literal_text=["Transfer Complete!"],
        expected_value_refs=[
            ParamRef(name="amount"),
            ParamRef(name="from_account_id"),
            ParamRef(name="to_account_id"),
        ],
    ),
    business_outcomes=[
        BusinessOutcomeDetector(
            code="INVALID_CREDENTIALS",
            description="The configured auth_profile's credentials were rejected during session establishment.",
            origin="session_establishment",
            target=ElementTarget(
                description="Whole page (exact error-message container not yet isolated)",
                strategies=[CssLocator(selector="body")],
            ),
            contains_text="The username and password could not be verified.",
        )
    ],
    policy=PolicyMetadata(
        allowed_domains=["parabank.parasoft.com"],
        allowed_actions=["navigate", "click", "type", "select_option", "wait_for", "extract"],
        requires_approval=False,
        approval_threshold_param="amount",
        approval_threshold_value=Decimal("500"),
        data_classification="internal",
    ),
    risky_click_accessible_names=frozenset({"Transfer"}),
)

_TEMPLATES: dict[str, CompilationTemplate] = {
    PARABANK_TRANSFER_FUNDS_TEMPLATE.capability_id: PARABANK_TRANSFER_FUNDS_TEMPLATE,
}


def get_template(capability_id: str) -> CompilationTemplate:
    template = _TEMPLATES.get(capability_id)
    if template is None:
        raise CompilationError(
            f"no CompilationTemplate registered for capability_id={capability_id!r} "
            f"(known: {sorted(_TEMPLATES)})"
        )
    return template
