"""Tests for cua.compiler.events — parsing events.jsonl and isolating the
successful winning executed-action path.
"""

import json

import pytest

from cua.compiler.events import CompilationError, assert_run_succeeded, load_events, winning_path

from .fakes import build_incomplete_run, build_run_with_corrections_and_failures, build_successful_transfer_run


def test_load_events_parses_a_successful_run(tmp_path):
    run_dir = build_successful_transfer_run(tmp_path)
    events = load_events(run_dir)
    assert len(events) == 6  # navigate, select, select, type, click, finish


def test_load_events_skips_session_establish_lines(tmp_path):
    run_dir = build_successful_transfer_run(tmp_path)
    events_path = run_dir / "events.jsonl"
    # session_establish lines predate evidence_schema_version and must be skipped, not rejected
    prior = events_path.read_text()
    events_path.write_text(
        json.dumps({"action": "session_establish", "outcome": "authenticated", "step_number": None}) + "\n" + prior
    )
    events = load_events(run_dir)
    assert len(events) == 6


def test_load_events_rejects_unsupported_evidence_schema_version(tmp_path):
    run_dir = build_successful_transfer_run(tmp_path)
    events_path = run_dir / "events.jsonl"
    lines = events_path.read_text().splitlines()
    mutated = json.loads(lines[0])
    mutated["evidence_schema_version"] = "0.9"
    lines[0] = json.dumps(mutated)
    events_path.write_text("\n".join(lines) + "\n")

    with pytest.raises(CompilationError, match="evidence_schema_version"):
        load_events(run_dir)


def test_load_events_rejects_missing_evidence_schema_version(tmp_path):
    run_dir = build_successful_transfer_run(tmp_path)
    events_path = run_dir / "events.jsonl"
    lines = events_path.read_text().splitlines()
    mutated = json.loads(lines[0])
    del mutated["evidence_schema_version"]
    lines[0] = json.dumps(mutated)
    events_path.write_text("\n".join(lines) + "\n")

    with pytest.raises(CompilationError, match="evidence_schema_version"):
        load_events(run_dir)


def test_load_events_rejects_missing_run_directory(tmp_path):
    with pytest.raises(CompilationError, match="no events.jsonl"):
        load_events(tmp_path / "does-not-exist")


def test_assert_run_succeeded_passes_for_a_finished_run(tmp_path):
    run_dir = build_successful_transfer_run(tmp_path)
    events = load_events(run_dir)
    assert_run_succeeded(events)  # must not raise


def test_assert_run_succeeded_rejects_a_run_with_no_finish_event(tmp_path):
    run_dir = build_incomplete_run(tmp_path)
    events = load_events(run_dir)
    with pytest.raises(CompilationError, match="successful finish event"):
        assert_run_succeeded(events)


def test_winning_path_excludes_corrections_blocked_and_failed_events(tmp_path):
    run_dir = build_run_with_corrections_and_failures(tmp_path)
    events = load_events(run_dir)
    winning = winning_path(events)

    assert [e.action for e in winning] == ["navigate", "click"]
    assert all(e.outcome == "ok" for e in winning)


def test_winning_path_excludes_finish_and_give_up(tmp_path):
    run_dir = build_successful_transfer_run(tmp_path)
    events = load_events(run_dir)
    winning = winning_path(events)
    assert "finish" not in [e.action for e in winning]
    assert "give_up" not in [e.action for e in winning]


def test_winning_path_is_ordered_by_step_number(tmp_path):
    run_dir = build_successful_transfer_run(tmp_path)
    events = load_events(run_dir)
    winning = winning_path(events)
    step_numbers = [e.step_number for e in winning]
    assert step_numbers == sorted(step_numbers)
    assert step_numbers == [1, 2, 3, 4, 5]
