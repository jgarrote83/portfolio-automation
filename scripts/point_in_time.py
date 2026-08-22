"""ALFRED backtest harness (2026-08-21, `feat/20260821-alfred-backtest-harness`),
Task B — as-of reconstruction of `macro_data` from cached ALFRED vintage rows.

FOLLOWUPS #23: "revised data makes naive backtests lie (payrolls revisions
especially)." Every FRED series is revised after its first print; production's
`get_series_latest` always returns the CURRENT (fully-revised) value, which is
exactly wrong for a historical replay -- on the date being replayed, only the
vintage known that day existed. This module reconstructs what production's own
`macro_data` dict would have contained on a past `as_of` date, from the full
vintage history `scripts/alfred_cache.py` caches locally.

Three semantics that must be exactly right (see the PR body's "headline
correctness test" for the empirical demonstration):

1. Publication lag: a row is visible on `as_of` only if `realtime_start <= as_of`.
2. Latest vintage wins: for a STANDARD series, collapse to the single most
   recent revision known by `as_of` per observation date.
3. Ordering: standard series newest-first (matches `_macro_vals`'s documented
   contract); `GDPNOW_VINTAGES`/`GDPNOW_VINTAGES_PRIOR` oldest-first (matches
   `_gdpnow_vintage_rows`'s contract) -- these are OPPOSITE conventions and
   must never be conflated (see collector/handler.py's own "two input-shape
   traps" note).

GDPNow is NOT a standard series for reconstruction purposes: each row for the
SAME observation date (the target quarter) is a genuinely distinct sequential
nowcast -- the trajectory IS the sequence of vintages, never collapsed to
"latest wins" the way a revised CPI print is. `_gdpnow_key_as_of` keeps every
vintage with `realtime_start <= as_of` and routes the result through the
PRODUCTION `_gdpnow_vintage_rows` (imported, never reimplemented) to filter to
one quarter and reshape to `{date, asof, value}`.

This module has zero I/O of its own -- it operates on already-fetched vintage
rows (whatever shape `alfred_cache.py` hands it, or a hand-built fixture in a
test). Offline only; never imported by collector/analyzer/flex runtime code.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from collector.handler import _gdpnow_vintage_rows, _quarter_bounds  # noqa: E402

__all__ = [
    "macro_data_as_of",
    "quarter_bounds_for",
]


def _latest_vintage_per_date(rows: list[dict] | None, as_of: str) -> dict[str, dict]:
    """For each observation ``date``, the row with the greatest
    ``realtime_start`` that is still ``<= as_of`` -- the most recent revision
    known on that day, never the first print. A row missing ``realtime_start``
    or ``date``, published AFTER ``as_of``, or carrying a missing/dot value is
    excluded entirely (never fabricated)."""
    best: dict[str, dict] = {}
    for r in rows or []:
        rt = r.get("realtime_start")
        d = r.get("date")
        v = r.get("value")
        if not rt or not d or rt > as_of:
            continue
        if v in (None, ".", ""):
            continue
        cur = best.get(d)
        if cur is None or rt > cur["realtime_start"]:
            best[d] = r
    return best


def _standard_series_as_of(rows: list[dict] | None, as_of: str) -> list[dict]:
    """Reconstruct a standard FRED series as of ``as_of``: newest-first
    ``{date, value}`` rows -- the SAME shape production's own
    ``get_series_latest`` returns, so ``_macro_vals`` and every axis builder
    need zero changes to consume it."""
    best = _latest_vintage_per_date(rows, as_of)
    ordered = sorted(best.values(), key=lambda r: r["date"], reverse=True)
    return [{"date": r["date"], "value": r["value"]} for r in ordered]


def _gdpnow_key_as_of(rows: list[dict] | None, as_of: str, obs_date: str) -> list[dict]:
    """``GDPNOW_VINTAGES`` / ``GDPNOW_VINTAGES_PRIOR`` as of ``as_of``: every
    vintage of quarter ``obs_date`` with ``realtime_start <= as_of``, kept in
    full (never collapsed to one), reshaped by the PRODUCTION
    ``_gdpnow_vintage_rows`` exactly as the live collector does."""
    visible = [
        r for r in (rows or [])
        if r.get("realtime_start") and r["realtime_start"] <= as_of
    ]
    return _gdpnow_vintage_rows(visible, obs_date)


def quarter_bounds_for(as_of: str) -> tuple[str, str]:
    """(quarter_start, prior_quarter_start) for the calendar quarter ``as_of``
    falls in -- the boundaries IN FORCE on that historical date, reusing
    production's own ``_quarter_bounds`` (never a second implementation of
    the same arithmetic)."""
    from datetime import date
    return _quarter_bounds(date.fromisoformat(as_of))


def macro_data_as_of(
    as_of: str,
    cache: dict[str, list[dict]],
    q_start: str,
    prior_q_start: str,
) -> dict:
    """Reconstruct ``macro_data`` -- the SAME shape ``_build_growth_axis`` /
    ``_build_inflation_axis`` expect -- as it would have looked on ``as_of``,
    using only revisions with ``realtime_start <= as_of``.

    ``cache`` maps series id -> the FULL raw ALFRED vintage-row list (as
    ``alfred_cache.py`` persists it, or a hand-built fixture in a test).
    ``q_start``/``prior_q_start`` are the ISO start dates of the quarter
    ``as_of`` falls in and the one before it -- pass ``quarter_bounds_for``'s
    result, not today's boundaries.

    A series absent from ``cache`` is simply absent from the output (never a
    fabricated empty list standing in for missing data) -- callers wanting the
    degrade-gracefully behavior every axis builder already has should rely on
    THAT builder's own missing-data handling, not on this function inventing
    placeholder rows.
    """
    out: dict[str, list[dict]] = {}
    for sid, rows in (cache or {}).items():
        if sid == "GDPNOW":
            out["GDPNOW"] = _standard_series_as_of(rows, as_of)
            out["GDPNOW_VINTAGES"] = _gdpnow_key_as_of(rows, as_of, q_start)
            out["GDPNOW_VINTAGES_PRIOR"] = _gdpnow_key_as_of(rows, as_of, prior_q_start)
        else:
            out[sid] = _standard_series_as_of(rows, as_of)
    return out


if __name__ == "__main__":
    print(__doc__)
    print("This module has no CLI of its own -- see replay_axes.py.")
    sys.exit(0)
