"""Completed runs carry what they concluded and how much they examined.

Regression for #121583: a check that ran, found its subject bad, or examined
only part of it must be distinguishable by a program reading the board, not
only by a human reading the summary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


@pytest.fixture
def conn(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    with kbc.connect_closing() as c:
        yield c


def test_partial_failing_check_reaches_downstream_worker_without_changing_routing(conn):
    gate = kb.create_task(conn, title="validate artifact", assignee="qa")
    consumer = kb.create_task(conn, title="publish artifact", assignee="ops", parents=[gate])
    assert kb.claim_task(conn, gate) is not None
    run_id = kb.latest_run(conn, gate).id

    coverage = {
        "total": 168,
        "checks": [
            {"name": "header_data_consistency", "ran": True, "result": "pass",
             "compared": ["n_levels", "n_boards"], "uncompared": ["alt_boards", "data_version"]},
        ],
        "asserted": 78,
        "skipped": 90,
    }
    assert kb.complete_task(
        conn, gate, summary="All checks pass", expected_run_id=run_id,
        verdict="FAIL", coverage=coverage,
    )

    # The tool did its job: the task completes and its consumer is released as before.
    assert kb.get_task(conn, gate).status == "done"
    assert kb.get_task(conn, consumer).status == "ready"

    run = kb.latest_run(conn, gate)
    assert run.outcome == "completed"
    assert run.verdict == "fail"
    assert (run.coverage["asserted"], run.coverage["total"]) == (78, 168)

    event = [e for e in kb.list_events(conn, gate) if e.kind == "completed"][-1]
    assert event.payload["verdict"] == "fail"
    assert event.payload["coverage"]["partial"] is True

    # The consumer's context states the gap, so "All checks pass" is not read as a full pass.
    context = kb.build_worker_context(conn, consumer)
    assert "verdict=fail" in context
    assert "78/168 asserted" in context
    assert "alt_boards, data_version" in context


@pytest.mark.parametrize("bad", [
    {"verdict": "maybe"},
    {"coverage": {"asserted": 5, "skipped": 1, "total": 4}},
    {"coverage": {"checks": [{"name": "x", "uncompared": "data_version"}]}},
    {"coverage": "{not json"},
])
def test_malformed_outcome_record_is_refused_before_anything_is_written(conn, bad):
    tid = kb.create_task(conn, title="validate", assignee="qa")
    assert kb.claim_task(conn, tid) is not None
    run_id = kb.latest_run(conn, tid).id

    with pytest.raises(ValueError):
        kb.complete_task(conn, tid, summary="checked", expected_run_id=run_id, **bad)

    assert kb.get_task(conn, tid).status == "running"
    run = kb.latest_run(conn, tid)
    assert run.id == run_id and run.ended_at is None
    assert not [e for e in kb.list_events(conn, tid) if e.kind == "completed"]
