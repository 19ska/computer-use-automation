"""Minimal same-session human-in-the-loop operator interface.

The SAME live Page/BrowserContext stays open and untouched by automation
while a human reviews/acts in it (see executor._handle_intervention) —
this module only defines HOW automation asks a human for a decision. A
blocking terminal prompt is the smallest REAL control-transfer mechanism:
no dashboard, no websocket, no remote co-browsing. Automation issues no
further Playwright calls for as long as `request_intervention` has not
returned.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class InterventionRequest:
    run_id: str
    capability_id: str
    step_id: int
    reason: str
    current_url: str
    screenshot_path: str | None
    timestamp: str
    pending_action: str
    context: dict[str, str]


OperatorDecision = Literal["resume", "decline"]


class OperatorInterface(Protocol):
    def request_intervention(self, request: InterventionRequest) -> OperatorDecision: ...


class TerminalOperator:
    """Real control transfer: blocks this process on stdin while a human
    works in the SAME live browser window."""

    def request_intervention(self, request: InterventionRequest) -> OperatorDecision:
        print("\n=== HUMAN INTERVENTION REQUESTED ===")
        print(json.dumps(asdict(request), indent=2))
        print(
            "\nThe browser is paused for you. If you approve, perform the pending action "
            "yourself in the open window, then type 'resume'. Type 'decline' to cancel "
            "without performing it."
        )
        while True:
            choice = input("[resume/decline] > ").strip().lower()
            if choice in ("resume", "approve"):
                return "resume"
            if choice in ("decline", "cancel"):
                return "decline"
            print("Please type 'resume' or 'decline'.")
