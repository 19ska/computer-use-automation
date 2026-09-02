"""Deterministic replay-time policy gate.

Runs immediately BEFORE every step dispatch — never the LLM, since
replay makes zero LLM calls at all. Enforces the artifact's own policy
metadata: allowed action types, allowed domains (checked against BOTH
the current page origin and, for navigate, the destination origin — the
current-origin check exists so automation stops immediately if the page
has unexpectedly drifted to an unapproved origin, rather than continuing
to interact with it), and a configurable per-capability risk/approval
threshold.

Distinguishes a HARD violation (stop immediately, ReplayFailure) from a
risky-but-legitimate action gated behind human approval (not a
violation — `requires_approval=True` is a normal, expected outcome for
an over-threshold amount).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from cua.artifact.schema import ActionStep, CapabilityArtifact

from .locators import SupportsPageProtocol

# Actions that touch the live page and therefore need a fresh
# current-origin check immediately before they run (not just once at the
# start of the run) — page state can drift between steps.
_BROWSER_INTERACTING_ACTIONS = frozenset({"click", "type", "select_option", "wait_for", "extract"})


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str | None = None
    requires_approval: bool = False
    threshold_context: dict[str, str] | None = None


def _host_allowed(host: str, allowed_domains: list[str]) -> bool:
    # Exact match only — no implicit subdomain leniency, matching the
    # same policy already enforced during discovery.
    return host in allowed_domains


def check_policy(
    page: SupportsPageProtocol,
    step: ActionStep,
    artifact: CapabilityArtifact,
    resolved_inputs: dict[str, Any],
) -> PolicyDecision:
    if step.action not in artifact.policy.allowed_actions:
        return PolicyDecision(allowed=False, reason=f"action '{step.action}' is not in policy.allowed_actions")

    current_host = urlparse(page.url).netloc  # type: ignore[attr-defined]

    if step.action == "navigate":
        # Current origin, "when applicable" — a fresh page (e.g. about:blank)
        # has no meaningful current host yet, so an empty host is not itself
        # a violation; but if the page IS already somewhere, that somewhere
        # must be approved before we act on it at all.
        if current_host and not _host_allowed(current_host, artifact.policy.allowed_domains):
            return PolicyDecision(
                allowed=False,
                reason=f"current page host '{current_host}' is not in policy.allowed_domains",
            )
        full_url = f"{artifact.target_app.rstrip('/')}{step.url}"
        destination_host = urlparse(full_url).netloc
        if not _host_allowed(destination_host, artifact.policy.allowed_domains):
            return PolicyDecision(
                allowed=False,
                reason=f"navigation destination host '{destination_host}' is not in policy.allowed_domains",
            )
    elif step.action in _BROWSER_INTERACTING_ACTIONS:
        if not _host_allowed(current_host, artifact.policy.allowed_domains):
            return PolicyDecision(
                allowed=False,
                reason=f"current page host '{current_host}' is not in policy.allowed_domains",
            )

    if step.risk == "risky":
        threshold_param = artifact.policy.approval_threshold_param
        threshold_value = artifact.policy.approval_threshold_value
        if threshold_param and threshold_value is not None:
            raw = resolved_inputs.get(threshold_param)
            if raw is not None:
                resolved_amount = raw if isinstance(raw, Decimal) else Decimal(str(raw))
                if resolved_amount > threshold_value:
                    return PolicyDecision(
                        allowed=True,
                        requires_approval=True,
                        reason=(
                            f"{threshold_param}={resolved_amount} exceeds approval "
                            f"threshold {threshold_value}"
                        ),
                        threshold_context={
                            "param": threshold_param,
                            "value": str(resolved_amount),
                            "threshold": str(threshold_value),
                        },
                    )

    return PolicyDecision(allowed=True)
