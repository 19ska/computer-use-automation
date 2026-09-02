"""Parses discovery evidence (events.jsonl) into typed events and isolates
the successful "winning path" a capability artifact should be compiled
from.

Deliberately dependency-light: consumes exactly the compile-ready fields
Milestone 4's evidence recorder now emits (evidence_schema_version,
action, target_description, accessible_role, accessible_name,
value_source, resolved_locator, url_path, outcome, provider, model) and
nothing else. Never reconstructs a locator, never re-derives navigation
intent from resulting_url, never replays discovery's own resolution
algorithm — that data is either present in the evidence or compilation
fails closed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cua.discovery.evidence import EVIDENCE_SCHEMA_VERSION

# Actions that represent a real, executed browser action. "finish" and
# "give_up" are model decisions, not browser actions, and never belong in
# a compiled artifact's step list.
EXECUTED_ACTIONS = frozenset({"navigate", "click", "type_text", "select_option"})


class CompilationError(Exception):
    """Raised for any condition that should make compilation fail
    closed — missing/incompatible evidence, an unrepresentable locator,
    an unresolved parameter reference, or a failed final artifact
    validation. Never caught silently; the CLI surfaces this message
    directly and writes no output file.
    """


@dataclass(frozen=True)
class DiscoveryEvent:
    step_number: int | None
    action: str
    outcome: str
    provider: str | None
    model: str | None
    target_description: str | None
    accessible_role: str | None
    accessible_name: str | None
    value_source: dict[str, Any] | None
    resolved_locator: dict[str, Any] | None
    url_path: str | None


def _parse_line(line: str, *, run_dir: Path, line_number: int) -> DiscoveryEvent | None:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise CompilationError(f"{run_dir}: events.jsonl line {line_number} is not valid JSON: {exc}") from exc

    # Session-establishment log lines (from evidence.record_event, shared
    # with deterministic replay) predate the discovery loop and have a
    # different, older shape with no evidence_schema_version at all —
    # they are not part of the compiled action path and are skipped, not
    # treated as a version violation.
    if raw.get("action") == "session_establish":
        return None

    version = raw.get("evidence_schema_version")
    if version != EVIDENCE_SCHEMA_VERSION:
        raise CompilationError(
            f"{run_dir}: events.jsonl line {line_number} has evidence_schema_version={version!r}, "
            f"but this compiler only supports {EVIDENCE_SCHEMA_VERSION!r}. "
            "Older discovery evidence is not supported through compatibility hacks — "
            "re-run discovery to produce compile-ready evidence."
        )

    action = raw.get("action")
    outcome = raw.get("outcome")
    if not action or not outcome:
        raise CompilationError(
            f"{run_dir}: events.jsonl line {line_number} is missing required field 'action' or 'outcome'"
        )

    return DiscoveryEvent(
        step_number=raw.get("step_number"),
        action=action,
        outcome=outcome,
        provider=raw.get("provider"),
        model=raw.get("model"),
        target_description=raw.get("target_description"),
        accessible_role=raw.get("accessible_role"),
        accessible_name=raw.get("accessible_name"),
        value_source=raw.get("value_source"),
        resolved_locator=raw.get("resolved_locator"),
        url_path=raw.get("url_path"),
    )


def load_events(run_dir: Path) -> list[DiscoveryEvent]:
    events_path = run_dir / "events.jsonl"
    if not events_path.is_file():
        raise CompilationError(f"{run_dir}: no events.jsonl found — not a discovery run directory")

    events: list[DiscoveryEvent] = []
    for line_number, line in enumerate(events_path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        event = _parse_line(line, run_dir=run_dir, line_number=line_number)
        if event is not None:
            events.append(event)

    if not events:
        raise CompilationError(f"{run_dir}: events.jsonl contains no compile-relevant events")

    return events


def assert_run_succeeded(events: list[DiscoveryEvent]) -> None:
    """A run is only compilable if the model's own "finish" claim was
    independently verified — the exact same success condition discovery
    itself required before returning DiscoverySuccess."""
    if not any(e.action == "finish" and e.outcome == "finished" for e in events):
        raise CompilationError(
            "discovery run has no independently-verified successful finish event — "
            "only a successful discovery run can be compiled"
        )


def winning_path(events: list[DiscoveryEvent]) -> list[DiscoveryEvent]:
    """The successfully executed browser-action sequence, in execution
    order. Excludes finish/give_up, invalid-model-response corrections,
    policy-blocked actions, and failed/repeated-failure attempts — all of
    those never have outcome == "ok"."""
    executed_ok = [e for e in events if e.action in EXECUTED_ACTIONS and e.outcome == "ok"]
    return sorted(executed_ok, key=lambda e: (e.step_number is None, e.step_number))
