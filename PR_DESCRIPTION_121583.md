# feat(kanban): record verdict and coverage on completed runs

Closes #121583 (parts **a** and **c**; part **b** is answered below with what already exists).

A Kanban run that completes only says *the worker did its job*. A program reading the board — a
dispatcher, a downstream worker, fleet tooling — could not tell these three apart, because they all
landed as the same `completed` record:

1. **The check worked and the subject is good.**
2. **The check worked and the subject is bad.** A validator that finds 12 failures did its job perfectly.
3. **The check ran and examined almost nothing.** `ALL CHECKS PASS` over 78 of 168 checks, or a
   header-consistency check that compared `n_levels`/`n_boards` but never the two fields upstream
   actually mutates.

A person can usually tell these apart from the summary text. A program cannot. This PR adds two
optional, validated fields to the completion record so it can.

## What lands

`task_runs` gets two nullable columns (an additive migration; old boards upgrade on first `connect()`):

```
verdict   pass | fail | unknown | NULL      what the run concluded about its subject
coverage  JSON                              how much it actually examined
```

```json
{
  "asserted": 78, "skipped": 90, "total": 168,
  "checks": [
    {"name": "header_data_consistency", "ran": true, "result": "pass",
     "compared":   ["n_levels", "n_boards"],
     "uncompared": ["alt_boards", "data_version"]}
  ]
}
```

Every key is optional. Pass only `checks` and the counts are worked out from the list (`ran: false`
counts as skipped), so a list of checks on its own still states its denominator.

| Where | What it does |
|---|---|
| `hermes_cli/kanban_db_verdict.py` (new) | Pure validation and rendering: `normalize_verdict`, `normalize_coverage`, `coverage_is_partial`, `coverage_counts`, `render_outcome_lines` |
| `hermes_cli/kanban_db.py` | Schema columns; `Run.verdict` / `Run.coverage`; `complete_task(..., verdict=, coverage=)` validates **before** any write; values go through `_end_run` / `_synthesize_ended_run`; `completed` event payload carries `verdict` + the bare denominator (`asserted/skipped/total/partial`) |
| `hermes_cli/kanban_db_connect.py` | Additive migration in `_TASK_RUN_COLUMNS` + the canonical `task_runs` rebuild spec stays in sync with `SCHEMA_SQL` |
| `hermes_cli/kanban_db_context.py` | A child's worker context shows each parent's verdict and coverage under that parent's handoff |
| `tools/kanban_tools_schemas.py`, `tools/kanban_tools.py` | `kanban_complete` accepts `verdict` (enum) and `coverage` (object); both show up in `kanban_show` run dicts |
| `hermes_cli/kanban_parser.py`, `hermes_cli/kanban.py`, `hermes_cli/kanban_output.py` | `hermes kanban complete --verdict --coverage`; `show` / `runs` render it, and `--json` includes both fields |
| `website/docs/user-guide/features/kanban.md` | Structured-handoff section + CLI reference |

### What a downstream worker now sees

```
## Parent task results
### t_d307165b (completed just now)
ALL CHECKS PASS
_outcome_: verdict=fail; coverage=78/168 asserted, 90 skipped (PARTIAL: not everything was examined)
  - header_data_consistency: NOT compared: alt_boards, data_version
```

Before this PR, that same context said only `ALL CHECKS PASS`.

## Design decisions

**Routing is unchanged.** A `fail` verdict still completes the task and still releases its children,
exactly as today. The issue's own framing is "the dispatcher routes on status; the parent wakes on
verdict". Making `fail` gate child promotion would be a new routing rule, and it would surprise every
board that exists today. That needs a maintainer decision (`needs-decision` is on the issue). The
fields are things consumers can read. Gating on them later is an easy follow-up, and this PR doesn't
commit the board to any particular gating rule.

**"Status" maps onto what we already have, not a second status column.** The issue's
`status ok | partial | error | skipped` is already covered by existing fields:

- `ok` is `outcome = completed`.
- `error` is `crashed` / `timed_out` / `spawn_failed` / `gave_up`.
- `skipped` with a reason is a typed block (below).
- `partial` is something coverage now states directly: `asserted < total`, a non-zero `skipped`, a
  check with `ran: false`, or any `uncompared` field. `coverage_is_partial` holds that rule in one place.

Adding a parallel status vocabulary would mean two ledgers to keep in sync, which the issue itself
rejects ("a separate result table … creates a second ledger to reconcile").

**Columns, not a `metadata` convention.** `metadata` is free-form, so every operator would invent
their own keys and none of them would interoperate (the issue's point about conventions). Real columns
can be queried (`SELECT … WHERE verdict = 'fail'`). They are validated, so a malformed record is
refused instead of stored, and they render the same way everywhere.

**Validated up front, nothing half-written.** `complete_task` normalizes both fields before the
parent check, the artifact staging, or the write transaction runs. A bad record (an unknown verdict,
`asserted + skipped > total`, `uncompared` as a string, invalid JSON, unknown keys) raises `ValueError`.
The task stays `running`, its run stays open, and no `completed` event is written. The tool turns that
into a retryable `tool_error`; the CLI exits with rc=2.

**Footprint.** No new core tool and no new toolset. The two parameters are added to `kanban_complete`,
which only exists inside the `kanban` toolset (dispatcher workers + opted-in platforms), so the
schema outside a kanban task doesn't change. Workers with no model use the CLI flags.

## Part (b) — retryability: already typed, deliberately not extended here

The issue asks for a way to tell *a lock collision (retry soon)* apart from *an unmet precondition
(don't retry, there's a missing dependency edge)*, and asks for maintainer input before anyone builds it.
The kernel already has that distinction as block kinds (`VALID_BLOCK_KINDS`):

```bash
hermes kanban block <id> "lock held by run 4411" --kind transient     # maybe-flaky; counts toward the loop breaker
hermes kanban block <id> "fixture DB not seeded" --kind dependency    # waits in todo on an open parent, never respawns context-free
```

`dependency` with no open parent is re-kinded to `needs_input` (sticky), so it can't loop forever. A
shell worker can call these today. What is still open is whether `transient` should **auto-retry**
after a backoff instead of waiting in `blocked`. That changes dispatcher retry policy, the part the
issue wants an opinion on before it's written, so this PR documents the existing kinds and stops there.

## Tests

Two behaviour-contract tests in `tests/hermes_cli/test_kanban_db_verdict.py`. Both were **red on base**
(`TypeError: complete_task() got an unexpected keyword argument 'verdict'`) and are green here:

- `test_partial_failing_check_reaches_downstream_worker_without_changing_routing`: a gate completes
  with `verdict="FAIL"` and partial coverage. The task is `done` and its child is `ready` (routing
  unchanged). The run stores `verdict == "fail"` and the denominator. The `completed` event carries
  `partial: true`. The child's `build_worker_context` shows the verdict, `78/168 asserted`, and the
  uncompared fields.
- `test_malformed_outcome_record_is_refused_before_anything_is_written` (4 cases): a bad record raises
  `ValueError`, and afterwards the task is still `running`, the run is still open, and there is no
  `completed` event.

```
scripts/run_tests.sh tests/hermes_cli/test_kanban*.py tests/tools/test_kanban*.py tests/gateway/*kanban*.py
=== Summary: 112 files, 784 tests passed, 0 failed, 4 skipped ===
```

`ruff check` is clean on every touched file.

### E2E against a real temp `HERMES_HOME`

1. **Legacy board.** Created the board and two linked tasks with the **pre-change** code
   (`task_runs` ends at `summary, metadata, error`), then switched to this branch and ran the real CLI.
   The migration added both columns on first connect.
2. `hermes kanban complete <gate> --verdict pass --coverage '{"asserted":5,"skipped":1,"total":4}'`
   gives `kanban: --coverage: coverage: asserted + skipped exceeds total` with rc=2, and the task is
   unchanged.
3. `hermes kanban complete <gate> --summary "ALL CHECKS PASS" --verdict fail --coverage '<the 78/168 record>'`
   gives `Completed`.
4. `hermes kanban show` / `runs --json` / `context <child>` show the verdict, the partial flag and the
   uncompared fields (the output is quoted above).
5. **Worker tool path.** With `HERMES_KANBAN_TASK` / `HERMES_KANBAN_RUN_ID` pinned,
   `handle_function_call("kanban_complete", {...verdict: "nope"})` returns
   `verdict must be one of pass, fail, unknown`. A valid call with
   `coverage: {checks: [{a, ran: true}, {b, ran: false}]}` completes, and the stored denominator is
   derived as `asserted=1, skipped=1, total=2`.

## Not in this PR

- Gating child promotion or notifications on `verdict = fail` (a routing change; needs a maintainer call).
- Auto-retry for `transient` blocks (the issue's part b; same reason).
- Dashboard rendering of the two fields. They are already in the run dicts the dashboard reads, so
  displaying them is a UI follow-up.
- The `workflow_template_id` / `current_step_key` loader the issue mentions. That's a separate
  convention question and is left for the maintainers to answer.
