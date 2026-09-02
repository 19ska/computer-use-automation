"""Writes structured replay evidence: a JSONL event log plus screenshots.

Hard rule enforced by CALLING CONVENTION, not just documentation: nothing
that calls `record_event` may ever pass a credential, or the concrete
`value` typed/selected during a `type`/`select_option` step, as an
argument. Only step metadata (action, locator strategy used, pass/fail,
short diagnostic text) belongs here. This directory is temporary run
output (Milestone 3), not the final /evidence/ submission structure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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

    def record_event(
        self,
        *,
        step_id: int | None,
        action: str,
        locator_strategy: str | None,
        outcome: str,
        detail: str | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "run_id": self.run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "step_id": step_id,
            "action": action,
            "locator_strategy": locator_strategy,
            "outcome": outcome,
            "detail": detail,
        }
        with self.events_path.open("a") as f:
            f.write(json.dumps(event) + "\n")

    def save_screenshot(self, page: Any, name: str) -> str:
        path = self.run_dir / f"{name}.png"
        page.screenshot(path=str(path), full_page=True)
        return str(path)
