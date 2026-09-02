"""Writes structured discovery evidence: a JSONL event log, one saved
Observation per step, and screenshots.

Hard rule enforced by CALLING CONVENTION, same as cua.replay.evidence:
nothing that reaches `record_event`/`record_step` may ever be a
credential, an API key, or hidden chain-of-thought — only the bounded,
structured fields the discovery loop already computes (action, target
description, resolved locator, rationale, outcome). This directory is
temporary discovery output (Milestone 4), not the final /evidence/
submission structure, and not the reusable capability artifact (that's
Milestone 5).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cua.artifact.schema import LiteralRef, ParamRef

from .observation import Observation


def _value_source_to_dict(value_source: ParamRef | LiteralRef | None) -> dict[str, Any] | None:
    if value_source is None:
        return None
    return value_source.model_dump()


@dataclass
class DiscoveryEvidenceWriter:
    run_id: str
    base_dir: str | Path = field(default_factory=lambda: Path("discovery_output"))
    run_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.base_dir = Path(self.base_dir)
        self.run_dir = self.base_dir / self.run_id
        (self.run_dir / "observations").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "screenshots").mkdir(parents=True, exist_ok=True)

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
        """Matches the same shape cua.replay.session.SupportsRecordEvent
        expects, so establish_session() works unchanged against this
        writer during discovery's session-establishment phase."""
        self._append(
            {
                "run_id": self.run_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "step_number": step_id,
                "action": action,
                "locator_strategy": locator_strategy,
                "outcome": outcome,
                "detail": detail,
            }
        )

    def record_step(
        self,
        *,
        step_number: int,
        provider: str | None = None,
        model: str,
        action: str,
        target_description: str | None = None,
        accessible_role: str | None = None,
        accessible_name: str | None = None,
        value_source: ParamRef | LiteralRef | None = None,
        resolved_locator_strategy: str | None = None,
        rationale: str | None = None,
        outcome: str,
        resulting_url: str | None = None,
        observation: Observation | None = None,
        checkpoint_result: dict[str, Any] | None = None,
        correction_attempt: int | None = None,
    ) -> str | None:
        observation_ref = None
        if observation is not None:
            observation_ref = f"observations/step_{step_number:03d}.json"
            (self.run_dir / observation_ref).write_text(json.dumps(asdict(observation), indent=2))

        self._append(
            {
                "run_id": self.run_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "step_number": step_number,
                "provider": provider,
                "model": model,
                "action": action,
                "target_description": target_description,
                "accessible_role": accessible_role,
                "accessible_name": accessible_name,
                "value_source": _value_source_to_dict(value_source),
                "resolved_locator_strategy": resolved_locator_strategy,
                "rationale": rationale,
                "outcome": outcome,
                "resulting_url": resulting_url,
                "observation_ref": observation_ref,
                "checkpoint_result": checkpoint_result,
                "correction_attempt": correction_attempt,
            }
        )
        return observation_ref

    def save_screenshot(self, page: Any, name: str) -> str:
        path = self.run_dir / "screenshots" / f"{name}.png"
        page.screenshot(path=str(path), full_page=True)
        return str(path)

    def _append(self, event: dict[str, Any]) -> None:
        with self.events_path.open("a") as f:
            f.write(json.dumps(event) + "\n")
