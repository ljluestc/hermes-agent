"""Machine-readable outcome of a completed run: ``verdict`` and ``coverage``.

A completed run only says the worker did its job. Two reserved
``metadata`` keys say what it concluded and how much it examined, so a
program consuming the handoff can tell "the subject passed" from "the
subject failed" from "almost nothing was checked" (#121583):

- ``verdict``: ``pass`` | ``fail`` | ``unknown`` — the worker's conclusion
  about its subject. Absent/``None`` = the run concludes nothing.
- ``coverage``: ``{"asserted": int, "total": int, "skipped"?: int,
  "compared"?: [str], "uncompared"?: [str]}`` — the denominator behind
  that verdict. ``compared``/``uncompared`` name what a check did and did
  not look at.

They ride inside ``metadata`` (like ``artifacts``) so they reach the run
row, downstream worker context, and ``--json`` output without a schema
change; the kernel only validates the shape and promotes them onto the
``completed`` event.
"""

from __future__ import annotations

from typing import Any

VALID_VERDICTS = ("pass", "fail", "unknown")
_COVERAGE_COUNTS = ("asserted", "skipped", "total")
_COVERAGE_NAMES = ("compared", "uncompared")


class InvalidOutcomeError(ValueError):
    """``metadata.verdict`` / ``metadata.coverage`` is malformed. Raised before
    any write, so the caller can fix the handoff and retry."""

    def __init__(self, detail: str):
        super().__init__(f"invalid completion outcome: {detail} (nothing changed)")


def _count(coverage: dict, key: str) -> int | None:
    value = coverage.get(key)
    if value is None:
        return None
    # bool is an int subclass; True is not a count.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidOutcomeError(f"coverage.{key} must be a non-negative integer, got {value!r}")
    return value


def validate_outcome(metadata: Any) -> None:
    """Raise :class:`InvalidOutcomeError` when the reserved keys are malformed."""
    if not isinstance(metadata, dict):
        return
    verdict = metadata.get("verdict")
    if verdict is not None and verdict not in VALID_VERDICTS:
        raise InvalidOutcomeError(
            f"verdict must be one of {list(VALID_VERDICTS)} or null, got {verdict!r}")
    coverage = metadata.get("coverage")
    if coverage is None:
        return
    if not isinstance(coverage, dict):
        raise InvalidOutcomeError(f"coverage must be an object, got {type(coverage).__name__}")
    asserted, skipped, total = (_count(coverage, key) for key in _COVERAGE_COUNTS)
    if asserted is None or total is None:
        raise InvalidOutcomeError("coverage needs both asserted and total")
    if asserted + (skipped or 0) > total:
        raise InvalidOutcomeError(
            f"coverage.asserted + coverage.skipped ({asserted + (skipped or 0)}) exceeds total ({total})")
    for key in _COVERAGE_NAMES:
        names = coverage.get(key)
        if names is not None and not (
            isinstance(names, list) and all(isinstance(n, str) and n.strip() for n in names)
        ):
            raise InvalidOutcomeError(f"coverage.{key} must be a list of non-empty strings")


def outcome_event_fields(metadata: Any) -> dict:
    """``verdict`` and the coverage counts for the ``completed`` event payload,
    so notifiers and event consumers route without fetching the run row.
    Name lists stay on the run: they can be long."""
    if not isinstance(metadata, dict):
        return {}
    fields: dict = {}
    if metadata.get("verdict") is not None:
        fields["verdict"] = metadata["verdict"]
    coverage = metadata.get("coverage")
    if isinstance(coverage, dict):
        fields["coverage"] = {k: coverage[k] for k in _COVERAGE_COUNTS if coverage.get(k) is not None}
    return fields
