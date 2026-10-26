"""A completed run separates "the tool worked" from "the subject passed" and
"how much was examined" (``metadata.verdict`` / ``metadata.coverage``)."""
import json

import pytest


@pytest.fixture
def running_task(monkeypatch, tmp_path):
    """A claimed task owned by this worker, plus a dependent child. Returns ids."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "test-worker")
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    from pathlib import Path as _Path
    monkeypatch.setattr(_Path, "home", lambda: tmp_path)

    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    conn = kbc.connect()
    try:
        task_id = kb.create_task(conn, title="validate artifact", assignee="test-worker")
        child_id = kb.create_task(conn, title="publish artifact", assignee="op", parents=[task_id])
        assert kb.claim_task(conn, task_id) is not None
        run_id = kb._current_run_id(conn, task_id)
    finally:
        conn.close()
    monkeypatch.setenv("HERMES_KANBAN_TASK", task_id)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id))
    return task_id, child_id


_PARTIAL_FAIL = {
    "verdict": "fail",
    "coverage": {
        "asserted": 78, "skipped": 90, "total": 168,
        "compared": ["n_levels", "n_boards"], "uncompared": ["alt_boards", "data_version"],
    },
}


def test_outcome_reaches_run_event_and_downstream_context(running_task):
    """A successful run that found the subject bad stays distinguishable all the way down."""
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    build_worker_context = kb.build_worker_context
    from tools import kanban_tools as kt

    task_id, child_id = running_task
    out = json.loads(kt._handle_complete(
        {"task_id": task_id, "summary": "validator ran", "metadata": _PARTIAL_FAIL}))
    assert not out.get("error"), out
    conn = kbc.connect()
    try:
        run = kb.latest_run(conn, task_id)
        assert run.outcome == "completed"
        assert run.metadata["verdict"] == "fail"
        assert run.metadata["coverage"] == _PARTIAL_FAIL["coverage"]
        completed = [e for e in kb.list_events(conn, task_id) if e.kind == "completed"]
        assert completed[-1].payload["verdict"] == "fail"
        # Counts are promoted for routing; name lists stay on the run row.
        assert completed[-1].payload["coverage"] == {"asserted": 78, "skipped": 90, "total": 168}
        context = build_worker_context(conn, child_id)
    finally:
        conn.close()
    assert '"verdict": "fail"' in context
    assert "data_version" in context


@pytest.mark.parametrize("metadata", [
    {"verdict": "ok"},
    {"coverage": {"asserted": 78, "skipped": 91, "total": 168}},
    {"coverage": {"asserted": True, "total": 1}},
    {"coverage": {"skipped": 3, "total": 10}},
    {"coverage": {"asserted": 1, "total": 2, "uncompared": "alt_boards"}},
    {"coverage": [78, 168]},
])
def test_malformed_outcome_refused_without_mutation(running_task, metadata):
    """A malformed outcome is a retryable tool error; the run stays open."""
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from tools import kanban_tools as kt

    task_id, _ = running_task
    out = json.loads(kt._handle_complete(
        {"task_id": task_id, "summary": "validator ran", "metadata": metadata}))
    assert "invalid completion outcome" in out.get("error", "")
    conn = kbc.connect()
    try:
        assert kb.get_task(conn, task_id).status == "running"
        assert kb.latest_run(conn, task_id).ended_at is None
        assert not [e for e in kb.list_events(conn, task_id) if e.kind.startswith("complet")]
    finally:
        conn.close()


def test_run_without_outcome_keeps_existing_payload(running_task):
    """Workers that never set the reserved keys see no new event fields."""
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from tools import kanban_tools as kt

    task_id, _ = running_task
    out = json.loads(kt._handle_complete(
        {"task_id": task_id, "summary": "done", "metadata": {"verdict": None, "tests_run": 3}}))
    assert not out.get("error"), out
    conn = kbc.connect()
    try:
        payload = [e for e in kb.list_events(conn, task_id) if e.kind == "completed"][-1].payload
    finally:
        conn.close()
    assert "verdict" not in payload and "coverage" not in payload
