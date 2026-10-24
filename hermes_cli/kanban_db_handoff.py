"""The machine-readable half of a completion handoff: ``verdict`` and ``coverage``.

A completed run says the worker did its job; it does not say what the worker concluded about
its subject, or how much of the subject it examined. A validator that finds the artifact bad,
and a gate that ran 78 of 168 checks, both land as ``completed``. These two fields let a
downstream worker (and any program reading ``task_runs``) tell those apart from a full pass
without parsing prose (#121583):

- ``verdict``: ``pass`` | ``fail`` | ``unknown`` — what the run concluded about its subject.
  NULL means the producer concludes nothing (most non-check work).
- ``coverage``: the denominator behind the verdict — ``asserted`` / ``skipped`` / ``total``
  counts and, for check-style runs, per-check ``compared`` / ``uncompared`` field lists, so
  "no findings" is never read as "no problems" when half the subject went unexamined.

Retryability is NOT here: a worker that cannot finish routes through ``kanban_block`` kinds
(``transient`` retries, ``dependency`` / ``needs_input`` park), which the dispatcher already
acts on.
"""

from __future__ import annotations

from typing import Any, Optional

import hermes_cli.kanban_db as _kb

VALID_VERDICTS = ("pass", "fail", "unknown")
VALID_CHECK_RESULTS = ("pass", "fail", "skip", "error")
_COUNT_KEYS = ("asserted", "skipped", "total")
_COVERAGE_KEYS = (*_COUNT_KEYS, "checks")
_CHECK_KEYS = ("name", "ran", "result", "compared", "uncompared")
# Rendering cap for the "unexamined" line in a worker's context.
_MAX_GAPS_SHOWN = 10


def normalize_verdict(verdict: Optional[str]) -> Optional[str]:
    """Lower-cased ``VALID_VERDICTS`` member; None/blank stays None. Anything else raises —
    a typo'd verdict must not land as a silent NULL that reads as "concluded nothing"."""
    if verdict is None or not str(verdict).strip():
        return None
    value = str(verdict).strip().lower()
    if value not in VALID_VERDICTS:
        raise ValueError(f"verdict must be one of {list(VALID_VERDICTS)} (or omitted), got {verdict!r}")
    return value


def _count(cov: dict, key: str) -> Optional[int]:
    value = cov.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"coverage.{key} must be a non-negative integer, got {value!r}")
    return value


def _str_list(check: dict, key: str, where: str) -> list[str]:
    value = check.get(key)
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"{where}.{key} must be a list of non-empty strings")
    return [v.strip() for v in value]


def _normalize_check(check: Any, index: int) -> dict:
    where = f"coverage.checks[{index}]"
    if not isinstance(check, dict):
        raise ValueError(f"{where} must be an object")
    unknown = sorted(set(check) - set(_CHECK_KEYS))
    if unknown:
        raise ValueError(f"{where} has unknown keys {unknown}; allowed: {list(_CHECK_KEYS)}")
    name = check.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{where}.name must be a non-empty string")
    out: dict = {"name": name.strip()}
    if "ran" in check:
        if not isinstance(check["ran"], bool):
            raise ValueError(f"{where}.ran must be a boolean")
        out["ran"] = check["ran"]
    if check.get("result") is not None:
        result = str(check["result"]).strip().lower()
        if result not in VALID_CHECK_RESULTS:
            raise ValueError(f"{where}.result must be one of {list(VALID_CHECK_RESULTS)}")
        out["result"] = result
    for key in ("compared", "uncompared"):
        values = _str_list(check, key, where)
        if values:
            out[key] = values
    return out


def normalize_coverage(coverage: Any) -> Optional[dict]:
    """Validated copy of a ``coverage`` object; None/empty stays None. Unknown keys raise so a
    misspelt ``totl`` cannot silently drop the denominator a consumer would route on."""
    if coverage is None or coverage == {}:
        return None
    if not isinstance(coverage, dict):
        raise ValueError("coverage must be an object")
    unknown = sorted(set(coverage) - set(_COVERAGE_KEYS))
    if unknown:
        raise ValueError(f"coverage has unknown keys {unknown}; allowed: {list(_COVERAGE_KEYS)}")
    out: dict = {}
    for key in _COUNT_KEYS:
        value = _count(coverage, key)
        if value is not None:
            out[key] = value
    total = out.get("total")
    if total is not None and out.get("asserted", 0) + out.get("skipped", 0) > total:
        raise ValueError("coverage.asserted + coverage.skipped cannot exceed coverage.total")
    checks = coverage.get("checks")
    if checks is not None:
        if not isinstance(checks, (list, tuple)):
            raise ValueError("coverage.checks must be a list")
        normalized = [_normalize_check(c, i) for i, c in enumerate(checks)]
        if normalized:
            out["checks"] = normalized
    return out or None


def _check_gap(check: dict) -> Optional[str]:
    if check.get("ran") is False:
        return f"{check['name']} did not run"
    if check.get("uncompared"):
        return f"{check['name']} did not compare {', '.join(check['uncompared'])}"
    return None


def coverage_gaps(coverage: Optional[dict]) -> list[str]:
    """What the run declared it did NOT examine: unasserted counts, checks that did not run,
    fields a check did not compare. Empty when nothing is declared missing."""
    if not coverage:
        return []
    gaps: list[str] = []
    total, asserted = coverage.get("total"), coverage.get("asserted")
    if total is not None and asserted is not None and asserted < total:
        gaps.append(f"{total - asserted} of {total} not asserted")
    gaps.extend(g for g in map(_check_gap, coverage.get("checks", ())) if g)
    return gaps


def _counts_text(coverage: dict) -> Optional[str]:
    asserted, skipped, total = (coverage.get(k) for k in _COUNT_KEYS)
    if asserted is None and total is None:
        return None
    text = f"{'?' if asserted is None else asserted}/{'?' if total is None else total} asserted"
    return f"{text}, {skipped} skipped" if skipped else text


def verdict_lines(verdict: Optional[str], coverage: Optional[dict]) -> list[str]:
    """Worker-context lines for a run's verdict + coverage. A declared gap is rendered as
    PARTIAL so a downstream reader cannot take an unexamined pass for a full one."""
    lines: list[str] = []
    if verdict:
        lines.append(f"_verdict_: **{verdict}**")
    if not coverage:
        return lines
    gaps = coverage_gaps(coverage)
    counts = _counts_text(coverage)
    label = "PARTIAL" if gaps else "complete"
    lines.append(f"_coverage_: {label}" + (f" ({counts})" if counts else ""))
    if gaps:
        shown = gaps[:_MAX_GAPS_SHOWN]
        more = len(gaps) - len(shown)
        lines.append("_unexamined_: " + "; ".join(shown) + (f"; … {more} more" if more else ""))
    return lines


def completed_event_payload(
    result: Optional[str], event_summary: Optional[str], verified_cards: list[str], metadata: Any,
    *, verdict: Optional[str] = None, coverage: Optional[dict] = None,
) -> dict:
    """``completed`` event payload: first summary line (400 chars) so gateway notifiers /
    dashboard WS render without a second round-trip; verified cards;
    ``metadata["artifacts"]`` promoted so the notifier can upload them as native attachments
    without fetching the run row; and the verdict + coverage counts, so an event consumer can
    route on the conclusion without a second read."""
    payload: dict = {
        "result_len": len(result) if result else 0,
        "summary": _kb._first_line(event_summary, 400) or None,
    }
    if verified_cards:
        payload["verified_cards"] = verified_cards
    if isinstance(metadata, dict):
        cleaned = _kb._cleaned_artifact_paths(metadata)
        if cleaned:
            payload["artifacts"] = cleaned
    if verdict:
        payload["verdict"] = verdict
    if coverage:
        counts = {k: coverage[k] for k in _COUNT_KEYS if k in coverage}
        if counts:
            payload["coverage"] = counts
        if coverage_gaps(coverage):
            payload["coverage_partial"] = True
    return payload
