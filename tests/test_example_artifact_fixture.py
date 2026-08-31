"""Loads and validates the example ParaBank capability artifact fixture.

This is a real, human-reviewable artifact (not synthetic test data) — it
exercises the schema against actual observed ParaBank behavior from
Milestone 1, without running any browser or LLM code.
"""

import json
import re
from pathlib import Path

from cua.artifact import CapabilityArtifact, LiteralRef

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "examples"
    / "capabilities"
    / "parabank_transfer_funds.json"
)


def _load() -> CapabilityArtifact:
    raw = json.loads(FIXTURE_PATH.read_text())
    return CapabilityArtifact.model_validate(raw)


def test_fixture_loads_and_validates():
    artifact = _load()
    assert artifact.capability_id == "parabank.transfer_funds"
    assert artifact.schema_version == "1.0"


def test_fixture_declares_session_requirement_without_credentials():
    artifact = _load()
    assert artifact.session_requirement.authenticated is True
    assert artifact.session_requirement.auth_profile == "parabank_demo"


def test_fixture_contains_no_literal_ref_anywhere():
    """Structural regression test proving credentials are genuinely absent,
    not merely absent by accident. This fixture's only fixed values
    (amount/from/to account) are all symbolic ParamRefs — no step should
    carry a LiteralRef at all, since authentication is intentionally kept
    out of this artifact (see session_requirement / schema.py docstring).
    """
    artifact = _load()
    literal_values = [
        step.value for step in artifact.steps if isinstance(step.value, LiteralRef)
    ]
    assert literal_values == []


def test_fixture_invalid_credentials_outcome():
    artifact = _load()
    outcomes = {o.code: o for o in artifact.business_outcomes}
    assert "INVALID_CREDENTIALS" in outcomes
    outcome = outcomes["INVALID_CREDENTIALS"]
    assert outcome.origin == "session_establishment"
    assert outcome.contains_text == "The username and password could not be verified."


def test_fixture_success_checkpoint_mentions_transfer_complete():
    artifact = _load()
    assert "Transfer Complete!" in artifact.success_checkpoint.expected_literal_text


def test_fixture_output_extraction_patterns_compile_and_capture_groups_in_range():
    artifact = _load()
    for output in artifact.outputs:
        spec = output.extraction
        if spec.pattern is not None:
            compiled = re.compile(spec.pattern)
            assert 0 <= spec.capture_group <= compiled.groups


def test_fixture_confirmation_pattern_parses_the_observed_message():
    """Sanity-checks the fixture's regex against the ACTUAL message observed
    during Milestone 1 — proves the pattern is not just syntactically valid
    but would really parse real ParaBank output. This does not exercise
    any replay code; the assertions below ARE the check.
    """
    artifact = _load()
    by_name = {o.name: o for o in artifact.outputs}
    pattern = by_name["transferred_amount"].extraction.pattern
    observed = (
        "$999999999.00 has been transferred from account #16785 "
        "to account #16896."
    )

    match = re.match(pattern, observed)
    assert match is not None
    assert match.group(by_name["transferred_amount"].extraction.capture_group) == "999999999.00"
    assert match.group(by_name["from_account_id"].extraction.capture_group) == "16785"
    assert match.group(by_name["to_account_id"].extraction.capture_group) == "16896"
