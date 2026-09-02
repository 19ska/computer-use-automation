"""Loads and validates the example ParaBank capability artifact fixture.

This is a real, human-reviewable artifact (not synthetic test data) — it
exercises the schema against actual observed ParaBank behavior from
Milestone 1, without running any browser or LLM code.
"""

import json
import re
from pathlib import Path

from cua.artifact import CapabilityArtifact, CssLocator, LiteralRef

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


def test_fixture_confirmation_pattern_parses_the_observed_message_inside_a_larger_page():
    """Sanity-checks the fixture's regex against the ACTUAL confirmation
    sentence, EMBEDDED inside a much larger body of surrounding page text
    (nav links, headings, footer) — matching the real shape of
    `page.inner_text("body")` on the live confirmation page, not an
    isolated sentence in a vacuum.

    This is a regression test for the Milestone 3 bug where the fixture's
    pattern was anchored with ^/$, which can only match when the ENTIRE
    body equals the sentence — never true on a real page with a header,
    nav, and footer around it. `re.search` (not `re.match`) is used here
    because replay itself uses `re.search`.
    """
    artifact = _load()
    by_name = {o.name: o for o in artifact.outputs}
    pattern = by_name["transferred_amount"].extraction.pattern

    noisy_body = (
        "Experience the difference\n"
        "Solutions About Us Services Products Locations Admin Page\n"
        "Welcome Some User\n"
        "Transfer Complete!\n"
        "$999999999.00 has been transferred from account #16785 to account #16896.\n"
        "Home About Us Services Products Locations Forum Site Map Contact Us\n"
        "© Parasoft. All rights reserved."
    )

    match = re.search(pattern, noisy_body)
    assert match is not None
    assert match.group(by_name["transferred_amount"].extraction.capture_group) == "999999999.00"
    assert match.group(by_name["from_account_id"].extraction.capture_group) == "16785"
    assert match.group(by_name["to_account_id"].extraction.capture_group) == "16896"


def test_fixture_wait_for_target_has_no_universal_body_fallback():
    """Regression test for the Milestone 3 bug: the wait_for step's target
    must not include a css:'body' strategy. body exists on every ParaBank
    page — including the pre-submission Transfer Funds form — so it is
    never a valid signal that the confirmation state was actually reached.
    If this test starts failing, someone re-added a universal fallback
    that will make the wait step succeed instantly and incorrectly.
    """
    artifact = _load()
    wait_steps = [s for s in artifact.steps if s.action == "wait_for"]
    assert wait_steps, "expected at least one wait_for step in this fixture"

    for step in wait_steps:
        body_fallbacks = [
            s
            for s in step.target.strategies
            if isinstance(s, CssLocator) and s.selector.strip().lower() == "body"
        ]
        assert body_fallbacks == [], (
            f"wait_for step {step.step_id} must not use a css:'body' strategy: "
            f"{step.target.strategies}"
        )
