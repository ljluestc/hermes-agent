"""Verdict + coverage on a completed run: what a finished worker concluded, and over how much.

A ``completed`` run only says the worker did its job. Programs consuming the
board (dispatchers, downstream workers, fleet tooling) also need to tell
"the subject passed" from "the subject failed" from "almost nothing was
examined" without parsing prose (#121583). Two optional ``task_runs``
columns carry that:

* ``verdict`` -- ``pass | fail | unknown``; NULL when the worker concludes
  nothing about a subject (most producers).
* ``coverage`` -- JSON denominator ``{asserted, skipped, total}`` plus an
  optional ``checks`` list naming, per check, the fields it ``compared`` and
  the ones it left ``uncompared``. The presence of a check must not read as
  the subject having been checked.

Routing is unchanged: a ``fail`` verdict still completes the task. Both
fields are reporting surfaces for consumers, validated here so a malformed
record is refused before the completion transaction instead of stored.
"""

from __future__ import annotations

import json
from typing import Any, Optional

VALID_VERDICTS = ("pass", "fail", "unknown")
VALID_CHECK_RESULTS = ("pass", "fail", "unknown", "skipped")
_COUNT_KEYS = ("asserted", "skipped", "total")
_CHECK_KEYS = {"name", "ran", "result", "compared", "uncompared"}
_MAX_CHECKS = 500
_RENDER_UNCOMPARED_CHECKS = 10


def normalize_verdict(value: Any) -> Optional[str]:
    """``None``/blank -> ``None``; a case-insensitive member of ``VALID_VERDICTS``; else ValueError."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"verdict must be one of {', '.join(VALID_VERDICTS)} (got {type(value).__name__})")
    text = value.strip().lower()
    if not text:
        return None
    if text not in VALID_VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VALID_VERDICTS)} (got {value!r})")
    return text


def _count(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"coverage.{key} must be a non-negative integer (got {value!r})")
    return value


def _name_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"{where} must be a list of non-empty strings")
    return [v.strip() for v in value]


def _normalize_check(raw: Any, idx: int) -> dict:
    where = f"coverage.checks[{idx}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object")
    unknown = sorted(set(raw) - _CHECK_KEYS)
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(sorted(_CHECK_KEYS))}")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{where}.name must be a non-empty string")
    check: dict = {"name": name.strip()}
    if "ran" in raw:
        if not isinstance(raw["ran"], bool):
            raise ValueError(f"{where}.ran must be true or false")
        check["ran"] = raw["ran"]
    if raw.get("result") is not None:
        result = str(raw["result"]).strip().lower()
        if result not in VALID_CHECK_RESULTS:
            raise ValueError(f"{where}.result must be one of {', '.join(VALID_CHECK_RESULTS)}")
        check["result"] = result
    for key in ("compared", "uncompared"):
        if raw.get(key) is not None:
            check[key] = _name_list(raw[key], f"{where}.{key}")
    return check


def normalize_coverage(value: Any) -> Optional[dict]:
    """Validate a coverage record (dict or JSON string); ``None``/empty -> ``None``.

    With ``checks`` but no counts, the denominator is derived from the list
    (``ran: false`` counts as skipped) so a check manifest alone still states
    how much of it was asserted. Explicit counts are kept as given and must be
    consistent: ``asserted + skipped <= total``.
    """
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"coverage is not valid JSON: {exc}") from None
    if not isinstance(value, dict):
        raise ValueError("coverage must be an object")
    if not value:
        return None
    unknown = sorted(set(value) - set(_COUNT_KEYS) - {"checks"})
    if unknown:
        raise ValueError(
            f"coverage: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(_COUNT_KEYS)}, checks")
    out: dict = {key: _count(value[key], key) for key in _COUNT_KEYS if value.get(key) is not None}
    raw_checks = value.get("checks")
    if raw_checks is not None:
        if not isinstance(raw_checks, (list, tuple)):
            raise ValueError("coverage.checks must be a list")
        if len(raw_checks) > _MAX_CHECKS:
            raise ValueError(f"coverage.checks holds at most {_MAX_CHECKS} entries")
        checks = [_normalize_check(c, i) for i, c in enumerate(raw_checks)]
        if checks:
            out["checks"] = checks
            if not any(key in out for key in _COUNT_KEYS):
                skipped = sum(1 for c in checks if c.get("ran") is False)
                out.update(asserted=len(checks) - skipped, skipped=skipped, total=len(checks))
    if not out:
        return None
    total = out.get("total")
    if total is not None and out.get("asserted", 0) + out.get("skipped", 0) > total:
        raise ValueError("coverage: asserted + skipped exceeds total")
    return out


def coverage_is_partial(coverage: Optional[dict]) -> bool:
    """True when the record itself admits something went unexamined."""
    if not coverage:
        return False
    total = coverage.get("total")
    if total is not None and coverage.get("asserted") is not None and coverage["asserted"] < total:
        return True
    if coverage.get("skipped"):
        return True
    return any(c.get("ran") is False or c.get("uncompared") for c in coverage.get("checks") or ())


def coverage_counts(coverage: Optional[dict]) -> Optional[dict]:
    """The bare denominator (no per-check detail) for compact surfaces like events."""
    if not coverage:
        return None
    counts = {key: coverage[key] for key in _COUNT_KEYS if key in coverage}
    counts["partial"] = coverage_is_partial(coverage)
    return counts


def render_outcome_lines(verdict: Optional[str], coverage: Optional[dict]) -> list[str]:
    """Human/LLM-readable lines for handoff context and ``kanban show``; ``[]`` when neither is set."""
    lines: list[str] = []
    head = []
    if verdict:
        head.append(f"verdict={verdict}")
    if coverage:
        asserted, skipped, total = (coverage.get(k) for k in _COUNT_KEYS)
        if asserted is not None and total is not None:
            text = f"coverage={asserted}/{total} asserted"
        elif asserted is not None:
            text = f"coverage={asserted} asserted (no total reported)"
        else:
            text = f"coverage total={total}" if total is not None else "coverage reported"
        if skipped:
            text += f", {skipped} skipped"
        if coverage_is_partial(coverage):
            text += " (PARTIAL: not everything was examined)"
        head.append(text)
    if head:
        lines.append("_outcome_: " + "; ".join(head))
    uncovered = [
        c for c in (coverage or {}).get("checks") or () if c.get("ran") is False or c.get("uncompared")
    ]
    for check in uncovered[:_RENDER_UNCOMPARED_CHECKS]:
        if check.get("ran") is False:
            lines.append(f"  - {check['name']}: did not run")
        else:
            lines.append(f"  - {check['name']}: NOT compared: {', '.join(check['uncompared'])}")
    if len(uncovered) > _RENDER_UNCOMPARED_CHECKS:
        lines.append(f"  - ({len(uncovered) - _RENDER_UNCOMPARED_CHECKS} more checks with gaps omitted)")
    return lines
