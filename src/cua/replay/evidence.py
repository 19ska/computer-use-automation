"""Writes structured replay evidence: a JSONL event log plus screenshots.

Hard rule enforced by CALLING CONVENTION, not just documentation: nothing
that calls `record_event`/`record_manual_event` may ever pass a
credential, or the concrete `value` typed/selected during a
`type`/`select_option` step, as an argument. Only step metadata (action,
locator strategy used, pass/fail, short diagnostic text) belongs here.
This directory is temporary run output (Milestone 3+), not the final
/evidence/ submission structure.

Milestone 6 additions (control_transition / intervention_* /
manual_event) all append to this SAME events.jsonl — one unified,
chronological log a reviewer can read top to bottom to distinguish a
fully-automated run from a human-assisted one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .operator import InterventionRequest


@dataclass
class ReplayEvidenceWriter:
    run_id: str
    base_dir: str | Path = field(default_factory=lambda: Path("run_output"))
    run_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.base_dir = Path(self.base_dir)
        self.run_dir = self.base_dir / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)

    @property
    def events_path(self) -> Path:
        return self.run_dir / "events.jsonl"

    def _append(self, event: dict[str, Any]) -> None:
        full_event = {
            "run_id": self.run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **event,
        }
        with self.events_path.open("a") as f:
            f.write(json.dumps(full_event) + "\n")

    def record_event(
        self,
        *,
        step_id: int | None,
        action: str,
        locator_strategy: str | None,
        outcome: str,
        detail: str | None = None,
    ) -> None:
        self._append(
            {
                "step_id": step_id,
                "action": action,
                "locator_strategy": locator_strategy,
                "outcome": outcome,
                "detail": detail,
            }
        )

    def record_control_transition(self, *, owner: str, step_id: int | None) -> None:
        self._append({"step_id": step_id, "action": "control_transition", "control_owner": owner})

    def record_intervention_request(self, request: InterventionRequest) -> None:
        self._append(
            {
                "step_id": request.step_id,
                "action": "intervention_requested",
                "reason": request.reason,
                "current_url": request.current_url,
                "screenshot_path": request.screenshot_path,
                "pending_action": request.pending_action,
                "context": request.context,
            }
        )

    def record_intervention_decision(self, *, step_id: int, decision: str) -> None:
        self._append({"step_id": step_id, "action": "intervention_decision", "decision": decision})

    def record_manual_event(
        self,
        *,
        step_id: int | None,
        event_type: str,
        tag: str | None,
        id: str | None,
        name: str | None,
        text: str | None,
    ) -> None:
        self._append(
            {
                "step_id": step_id,
                "action": "manual_event",
                "event_type": event_type,
                "tag": tag,
                "id": id,
                "name": name,
                "text": text,
                "control_owner": "human",
            }
        )

    def save_screenshot(self, page: Any, name: str) -> str:
        path = self.run_dir / f"{name}.png"
        page.screenshot(path=str(path), full_page=True)
        return str(path)
