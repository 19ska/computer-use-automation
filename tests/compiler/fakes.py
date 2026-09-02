"""Builds synthetic discovery run directories using the REAL
DiscoveryEvidenceWriter — not hand-typed JSON — so compiler tests exercise
the exact event shape Milestone 4 actually produces.
"""

from __future__ import annotations

from pathlib import Path

from cua.artifact.schema import CssLocator, ParamRef, RoleLocator
from cua.discovery.evidence import DiscoveryEvidenceWriter

PROVIDER = "groq"
MODEL = "qwen/qwen3.6-27b"
BASE_URL = "https://parabank.parasoft.com/parabank"


def build_successful_transfer_run(tmp_path: Path, run_id: str = "discovery-test-run") -> Path:
    """A realistic successful run: navigate, select_option (from/to,
    ParamRef), type_text (amount, ParamRef), click (submit), finish
    (finished). Mirrors the real Groq/Qwen run structure.
    """
    evidence = DiscoveryEvidenceWriter(run_id=run_id, base_dir=tmp_path)

    evidence.record_step(
        step_number=1, provider=PROVIDER, model=MODEL, action="navigate",
        url_path="/transfer.htm", rationale="Go to transfer page.",
        outcome="ok", resulting_url=f"{BASE_URL}/transfer.htm",
    )
    evidence.record_step(
        step_number=2, provider=PROVIDER, model=MODEL, action="select_option",
        target_description="From account dropdown", accessible_role="combobox", accessible_name="From account #",
        value_source=ParamRef(name="from_account_id"),
        resolved_locator_strategy="css#1", resolved_locator=CssLocator(selector="#fromAccountId"),
        rationale="Select source account.", outcome="ok", resulting_url=f"{BASE_URL}/transfer.htm",
    )
    evidence.record_step(
        step_number=3, provider=PROVIDER, model=MODEL, action="select_option",
        target_description="To account dropdown", accessible_role="combobox", accessible_name="to account #",
        value_source=ParamRef(name="to_account_id"),
        resolved_locator_strategy="css#1", resolved_locator=CssLocator(selector="#toAccountId"),
        rationale="Select destination account.", outcome="ok", resulting_url=f"{BASE_URL}/transfer.htm",
    )
    evidence.record_step(
        step_number=4, provider=PROVIDER, model=MODEL, action="type_text",
        target_description="Amount field", accessible_role="textbox", accessible_name="Amount",
        value_source=ParamRef(name="amount"),
        resolved_locator_strategy="css#1", resolved_locator=CssLocator(selector="#amount"),
        rationale="Enter amount.", outcome="ok", resulting_url=f"{BASE_URL}/transfer.htm",
    )
    evidence.record_step(
        step_number=5, provider=PROVIDER, model=MODEL, action="click",
        target_description="Transfer submit button", accessible_role="button", accessible_name="Transfer",
        resolved_locator_strategy="role#0", resolved_locator=RoleLocator(role="button", name="Transfer"),
        rationale="Submit transfer.", outcome="ok", resulting_url=f"{BASE_URL}/transfer.htm",
    )
    evidence.record_step(
        step_number=6, provider=PROVIDER, model=MODEL, action="finish",
        outcome="finished", rationale="Transfer confirmed.",
        checkpoint_result={"passed": True, "observed": "Transfer Complete! $20.00 ..."},
    )

    return evidence.run_dir


def build_run_with_corrections_and_failures(tmp_path: Path, run_id: str = "discovery-messy-run") -> Path:
    """A successful run that ALSO contains a correction attempt, a
    policy-blocked action, and a failed (then retried) action — all of
    which must be excluded from the compiled winning path.
    """
    evidence = DiscoveryEvidenceWriter(run_id=run_id, base_dir=tmp_path)

    evidence.record_step(
        step_number=1, provider=PROVIDER, model=MODEL, action="navigate",
        url_path="/transfer.htm", outcome="ok", resulting_url=f"{BASE_URL}/transfer.htm",
    )
    # correction attempt: text-only response, excluded
    evidence.record_step(
        step_number=2, provider=PROVIDER, model=MODEL, action="invalid_response",
        outcome="invalid_model_response", rationale="expected exactly one actionable function call, got 0",
        correction_attempt=0,
    )
    # policy-blocked attempt, excluded
    evidence.record_step(
        step_number=2, provider=PROVIDER, model=MODEL, action="navigate",
        outcome="blocked", rationale="tried to leave the domain",
    )
    # a failed attempt at the same step, excluded
    evidence.record_step(
        step_number=2, provider=PROVIDER, model=MODEL, action="click",
        target_description="Ghost button", accessible_role="button", accessible_name="Ghost",
        outcome="failed", rationale="not found",
    )
    # the actual successful action for step 2
    evidence.record_step(
        step_number=2, provider=PROVIDER, model=MODEL, action="click",
        target_description="Transfer submit button", accessible_role="button", accessible_name="Transfer",
        resolved_locator_strategy="role#0", resolved_locator=RoleLocator(role="button", name="Transfer"),
        outcome="ok", resulting_url=f"{BASE_URL}/transfer.htm",
    )
    evidence.record_step(
        step_number=3, provider=PROVIDER, model=MODEL, action="give_up",
        outcome="given_up", rationale="unused in this fixture",
    )
    evidence.record_step(
        step_number=4, provider=PROVIDER, model=MODEL, action="finish",
        outcome="finished", rationale="done",
        checkpoint_result={"passed": True, "observed": "Transfer Complete! ..."},
    )

    return evidence.run_dir


def build_incomplete_run(tmp_path: Path, run_id: str = "discovery-incomplete-run") -> Path:
    """A run with no successful finish event at all."""
    evidence = DiscoveryEvidenceWriter(run_id=run_id, base_dir=tmp_path)
    evidence.record_step(
        step_number=1, provider=PROVIDER, model=MODEL, action="navigate",
        url_path="/transfer.htm", outcome="ok", resulting_url=f"{BASE_URL}/transfer.htm",
    )
    evidence.record_step(
        step_number=2, provider=PROVIDER, model=MODEL, action="give_up",
        outcome="given_up", rationale="could not find the form",
    )
    return evidence.run_dir
