"""Discovery-time policy gate.

Runs BEFORE any proposed action ever touches the page. Claude cannot see
or influence this configuration — it is fixed harness-side. For this
milestone, the allowed host is exact and configured explicitly; arbitrary
subdomains are NOT automatically trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

ALLOWED_ACTIONS = frozenset({"navigate", "click", "type_text", "select_option", "finish", "give_up"})


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str | None = None


def check_policy(action: str, args: dict, *, allowed_host: str, base_url: str) -> PolicyDecision:
    """`allowed_host` must match exactly (e.g. "parabank.parasoft.com") —
    this implementation does not treat subdomains as automatically
    trusted; if a subdomain needs to be allowed, it must be added
    explicitly by the caller.
    """
    if action not in ALLOWED_ACTIONS:
        return PolicyDecision(False, f"action '{action}' is not permitted")

    if action == "navigate":
        url_path = args.get("url_path", "") or ""
        if url_path.startswith(("http://", "https://")):
            host = urlparse(url_path).netloc
        else:
            host = urlparse(base_url).netloc
        if host != allowed_host:
            return PolicyDecision(
                False,
                f"navigation to host '{host}' is not permitted; only '{allowed_host}' is allowed",
            )

    return PolicyDecision(True)
