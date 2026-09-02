"""ParaBank-specific session establishment.

This is the one module allowed to know concrete ParaBank login selectors —
there's no artifact-level home for login form controls, since login is
deliberately kept OUT of the capability artifact (see SessionRequirement in
schema.py). Isolated here behind `establish_session()` so a different
vendor app's login flow could be substituted later without touching the
generic engine/executor.

Deliberately independent of CapabilityArtifact: both deterministic replay
and LLM discovery need to establish a session before their own loop
starts, but discovery runs before any artifact exists. This function takes
only the pieces it actually needs (base_url, session_requirement,
business_outcomes) so both callers can use it without either constructing
an artifact or duplicating this logic.

Credential handling: `PARABANK_USERNAME`/`PARABANK_PASSWORD` are read from
the environment and used ONLY as arguments to `page.fill(...)`. They are
never passed to `evidence.record_event`, never included in an exception
message, and never appear in a return value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from cua.artifact.schema import BusinessOutcomeDetector, SessionRequirement

from . import locators
from .locators import LocatorAmbiguousError, LocatorNotFoundError, SupportsPageProtocol

# Maps an opaque auth_profile name to the environment variables that hold
# its credentials. Adding a new profile means adding one entry here — no
# credential value is ever stored in code or in an artifact.
_PROFILE_ENV_VARS: dict[str, tuple[str, str]] = {
    "parabank_demo": ("PARABANK_USERNAME", "PARABANK_PASSWORD"),
}

_USERNAME_SELECTOR = "input[name='username']"
_PASSWORD_SELECTOR = "input[name='password']"
_SUBMIT_SELECTOR = "input[type='submit'][value='Log In']"
_LOGOUT_LINK_SELECTOR = "a[href*='logout.htm']"

# ParaBank is an old demo app that can keep background network activity
# alive indefinitely — "networkidle" is not a reliable readiness signal
# for it and can hang well past any reasonable navigation budget. DOM
# load plus an explicit wait for the actual login controls is a stronger,
# bounded readiness check for this specific page.
_NAVIGATION_TIMEOUT_MS = 15_000
_LOGIN_READY_TIMEOUT_MS = 15_000


class SupportsRecordEvent(Protocol):
    """The subset of an evidence writer's interface establish_session
    needs. Both ReplayEvidenceWriter and DiscoveryEvidenceWriter satisfy
    this structurally."""

    def record_event(
        self,
        *,
        step_id: int | None,
        action: str,
        locator_strategy: str | None,
        outcome: str,
        detail: str | None = None,
    ) -> None: ...


@dataclass
class SessionEstablished:
    pass


@dataclass
class SessionBusinessOutcome:
    outcome_code: str
    message: str


@dataclass
class SessionFailure:
    category: str
    expected: str
    observed: str


SessionResult = SessionEstablished | SessionBusinessOutcome | SessionFailure


def establish_session(
    page: SupportsPageProtocol,
    *,
    base_url: str,
    session_requirement: SessionRequirement,
    business_outcomes: list[BusinessOutcomeDetector],
    evidence: SupportsRecordEvent,
) -> SessionResult:
    if not session_requirement.authenticated:
        return SessionEstablished()

    if session_requirement.auth_profile not in _PROFILE_ENV_VARS:
        return SessionFailure(
            category="session_establishment_error",
            expected=f"a known auth_profile (one of {sorted(_PROFILE_ENV_VARS)})",
            observed=f"auth_profile={session_requirement.auth_profile!r}",
        )

    username_var, password_var = _PROFILE_ENV_VARS[session_requirement.auth_profile]
    username = os.environ.get(username_var)
    password = os.environ.get(password_var)
    if not username or not password:
        return SessionFailure(
            category="session_establishment_error",
            expected=f"{username_var} and {password_var} to be set in the environment",
            observed="one or both environment variables are unset or empty",
        )

    try:
        page.goto(f"{base_url}/index.htm", wait_until="domcontentloaded", timeout=_NAVIGATION_TIMEOUT_MS)  # type: ignore[attr-defined]
        # Wait for the real login controls (verified during Milestone 1
        # manual exploration) rather than trusting DOM-load timing alone —
        # a stronger, application-specific readiness signal than global
        # network idleness, and one that won't hang on ParaBank's
        # persistent background activity.
        page.wait_for_selector(_USERNAME_SELECTOR, timeout=_LOGIN_READY_TIMEOUT_MS)  # type: ignore[attr-defined]
        page.wait_for_selector(_PASSWORD_SELECTOR, timeout=_LOGIN_READY_TIMEOUT_MS)  # type: ignore[attr-defined]
        page.wait_for_selector(_SUBMIT_SELECTOR, timeout=_LOGIN_READY_TIMEOUT_MS)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - navigation/readiness failure is a structured session failure, not a crash
        evidence.record_event(
            step_id=None, action="session_establish", locator_strategy=None, outcome="failed"
        )
        return SessionFailure(
            category="session_establishment_error",
            expected="the ParaBank login page to load and its login controls to become visible",
            observed=f"{type(exc).__name__}: {exc}",
        )

    page.fill(_USERNAME_SELECTOR, username, timeout=5_000)  # type: ignore[attr-defined]
    page.fill(_PASSWORD_SELECTOR, password, timeout=5_000)  # type: ignore[attr-defined]
    page.click(_SUBMIT_SELECTOR, timeout=5_000)  # type: ignore[attr-defined]
    page.wait_for_load_state("networkidle", timeout=10_000)  # type: ignore[attr-defined]

    evidence.record_event(
        step_id=None, action="session_establish", locator_strategy=None, outcome="attempted"
    )

    if page.query_selector(_LOGOUT_LINK_SELECTOR):  # type: ignore[attr-defined]
        evidence.record_event(
            step_id=None,
            action="session_establish",
            locator_strategy=_LOGOUT_LINK_SELECTOR,
            outcome="authenticated",
        )
        return SessionEstablished()

    # Reuse the SAME generic locator resolver the executor uses, checking
    # only the detectors declared as belonging to session establishment
    # (origin="session_establishment").
    for outcome in business_outcomes:
        if outcome.origin != "session_establishment":
            continue
        try:
            resolution = locators.resolve_target(page, outcome.target)
        except (LocatorNotFoundError, LocatorAmbiguousError):
            continue
        if outcome.contains_text in resolution.locator.inner_text():
            evidence.record_event(
                step_id=None,
                action="session_establish",
                locator_strategy=resolution.strategy_description,
                outcome="business_outcome",
                detail=outcome.code,
            )
            return SessionBusinessOutcome(outcome_code=outcome.code, message=outcome.contains_text)

    evidence.record_event(
        step_id=None, action="session_establish", locator_strategy=None, outcome="failed"
    )
    return SessionFailure(
        category="session_establishment_error",
        expected="an authenticated session (Log Out link) or a known business outcome",
        observed="neither an authenticated state nor a known business outcome was detected",
    )
