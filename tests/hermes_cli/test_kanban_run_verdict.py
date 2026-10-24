"""A completed run records what it concluded and what it examined (#121583).

``verdict`` separates "the tool did its job" from "the subject passed"; ``coverage`` carries
the denominator so a pass over part of the subject is never handed downstream as a full pass.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from hermes_cli import kanban as kc
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc

# The header-consistency gate from the issue: it compared the two fields upstream recomputes
# and skipped the two it mutates, then reported a pass.
_PARTIAL_GATE = {
    "asserted": 78, "skipped": 0, "total": 168,
    "checks": [{
        "name": "header_data_consistency", "ran": True, "result": "pass",
        "compared": ["n_levels", "n_boards"], "uncompared": ["alt_boards", "data_version"],
    }],
}


@pytest.fixture
def conn(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.discard(str(kb.kanban_db_path(board="default").resolve()))
    kb.init_db()
    with kbc.connect() as c:
        yield c


def _running(conn, title="validate artifact", **kw) -> str:
    tid = kb.create_task(conn, title=title, assignee="validator", **kw)
    assert kb.claim_task(conn, tid, claimer=kb._claimer_id()) is not None
    return tid


def _completed_event(conn, tid) -> dict:
    row = conn.execute(
        "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'completed'", (tid,),
    ).fetchone()
    return json.loads(row["payload"])


def test_failing_subject_completes_with_fail_verdict_and_coverage(conn):
    tid = _running(conn)
    assert kb.complete_task(
        conn, tid, summary="30 checks ran; 4 failed", verdict="FAIL", coverage=_PARTIAL_GATE,
    ) is True

    assert kb.get_task(conn, tid).status == "done"
    run = kb.latest_run(conn, tid)
    assert run.outcome == "completed"
    assert run.verdict == "fail"
    assert run.coverage["total"] == 168
    assert run.coverage["checks"][0]["uncompared"] == ["alt_boards", "data_version"]
    event = _completed_event(conn, tid)
    assert event["verdict"] == "fail"
    assert event["coverage"] == {"asserted": 78, "skipped": 0, "total": 168}
    assert event["coverage_partial"] is True


def test_run_without_verdict_concludes_nothing(conn):
    tid = _running(conn)
    assert kb.complete_task(conn, tid, summary="wrote the draft") is True
    run = kb.latest_run(conn, tid)
    assert run.verdict is None and run.coverage is None
    assert "verdict" not in _completed_event(conn, tid)


def test_child_context_shows_parent_verdict_and_unexamined_fields(conn):
    parent = _running(conn, title="gate")
    child = kb.create_task(conn, title="publish", assignee="publisher", parents=[parent])
    assert kb.complete_task(
        conn, parent, summary="ALL CHECKS PASS", verdict="pass", coverage=_PARTIAL_GATE,
    ) is True

    ctx = kb.build_worker_context(conn, child)
    assert "_verdict_: **pass**" in ctx
    assert "_coverage_: PARTIAL (78/168 asserted)" in ctx
    assert "90 of 168 not asserted" in ctx
    assert "header_data_consistency did not compare alt_boards, data_version" in ctx


def test_full_coverage_is_not_flagged_partial(conn):
    parent = _running(conn, title="gate")
    child = kb.create_task(conn, title="publish", assignee="publisher", parents=[parent])
    kb.complete_task(conn, parent, summary="ok", verdict="pass",
                     coverage={"asserted": 168, "total": 168})
    ctx = kb.build_worker_context(conn, child)
    assert "_coverage_: complete (168/168 asserted)" in ctx
    assert "_unexamined_" not in ctx
    assert "coverage_partial" not in _completed_event(conn, parent)


@pytest.mark.parametrize("kwargs", [
    {"verdict": "passed"},
    {"coverage": {"totl": 168}},
    {"coverage": {"asserted": 170, "total": 168}},
    {"coverage": {"asserted": -1}},
    {"coverage": {"checks": [{"ran": True}]}},
    {"coverage": {"checks": [{"name": "x", "uncompared": "alt_boards"}]}},
])
def test_malformed_handoff_is_refused_without_side_effects(conn, kwargs):
    tid = _running(conn)
    with pytest.raises(ValueError):
        kb.complete_task(conn, tid, summary="done", **kwargs)
    assert kb.get_task(conn, tid).status == "running"
    assert conn.execute(
        "SELECT 1 FROM task_events WHERE task_id = ? AND kind = 'completed'", (tid,),
    ).fetchone() is None


def test_legacy_board_gains_columns_on_open(conn):
    """A board created before these columns existed upgrades in place on the next init."""
    for col in ("verdict", "coverage"):
        conn.execute(f"ALTER TABLE task_runs DROP COLUMN {col}")
    conn.commit()
    kb._INITIALIZED_PATHS.discard(str(kb.kanban_db_path(board="default").resolve()))
    kb.init_db()
    with kbc.connect() as fresh:
        cols = {r["name"] for r in fresh.execute("PRAGMA table_info(task_runs)")}
        assert {"verdict", "coverage"} <= cols
        tid = _running(fresh)
        assert kb.complete_task(fresh, tid, summary="ok", verdict="unknown") is True
        assert kb.latest_run(fresh, tid).verdict == "unknown"


def test_cli_complete_records_verdict_and_rejects_bad_coverage(conn):
    tid = _running(conn)
    bad = kc.run_slash(f"complete {tid} --summary ok --coverage {shlex.quote(json.dumps({'totl': 1}))}")
    assert "unknown keys" in bad
    assert kb.get_task(conn, tid).status == "running"

    kc.run_slash(
        f"complete {tid} --summary ok --verdict fail --coverage {shlex.quote(json.dumps(_PARTIAL_GATE))}"
    )
    run = kb.latest_run(conn, tid)
    assert run.verdict == "fail"
    assert run.coverage["asserted"] == 78
    runs_out = kc.run_slash(f"runs {tid}")
    assert "_verdict_: **fail**" in runs_out
    assert "_coverage_: PARTIAL" in runs_out
