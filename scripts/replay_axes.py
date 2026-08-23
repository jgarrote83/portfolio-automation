"""ALFRED backtest harness (2026-08-21, `feat/20260821-alfred-backtest-harness`),
Task C — stateful day-by-day replay of the REAL production classifier.

Calls the actual production functions (`_build_growth_axis`,
`_build_inflation_axis`, `_confirm_axis_direction`, `active_quadrant`) against
`point_in_time.macro_data_as_of`-reconstructed inputs -- no parallel
implementation of any classification logic exists here or anywhere in this
harness (doctrine: "if you find yourself copying classification logic into
scripts/, stop").

**The hysteresis is stateful and MUST be chained.** `_confirm_axis_direction`
takes the prior day's confirmed state; evaluating dates independently would
silently erase the N=2 confirmation delay that is exactly what this harness
exists to measure (a lag number computed from independently-evaluated dates
would understate real lag by however many days the confirmation gate adds).
This module's `replay` function is a single forward walk, date by date,
feeding each day's confirmation output as the next day's `prev`.

D-A2 (production's "first run / no persisted state" rule) needs no special
handling here: `_confirm_axis_direction(raw, prev=None, today)` already adopts
the raw value immediately with streak seeded at 2 when `prev` is `None` --
the replay's first date is a genuine cold start by construction, since
`prev_growth_state`/`prev_inflation_state` start at `None`.

Business dates: weekdays only (Mon-Fri), mirroring the actual production
collector's cron (`0 0 9 * * 1-5`) exactly -- production has no
market-holiday awareness either, so this is not a simplification, it is
parity with what the live system does.

Offline only; never imported by collector/analyzer/flex runtime code.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from collector.handler import (  # noqa: E402
    _build_growth_axis,
    _build_inflation_axis,
    _confirm_axis_direction,
)
from shared.quadrants import active_quadrant  # noqa: E402

from point_in_time import macro_data_as_of, quarter_bounds_for  # noqa: E402

__all__ = ["replay", "business_dates"]

# The realized-core series the inflation axis needs to produce anything other
# than 'indeterminate' -- mirrors _build_inflation_axis's own PCE-first,
# CPI-fallback preference.
_INFLATION_CORE_SERIES = ("PCEPILFE", "CPILFESL")


def business_dates(start: str, end: str) -> list[str]:
    """Weekdays (Mon-Fri) from ``start`` to ``end`` inclusive, ISO strings."""
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    d = d0
    while d <= d1:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _reconstructable_growth(macro_data: dict) -> tuple[bool, str | None]:
    """Whether ANY GDPNow-derived input existed for this date at all --
    independent of whatever `_build_growth_axis` decided to do with it (a
    within-quarter-but-thin read is still reconstructable; zero raw data is
    not). This is what lets Task D's turn-coverage check tell 'the primary
    input does not exist for this whole era' apart from an ordinary
    single-day indeterminate read within an otherwise-covered period."""
    has_data = bool(
        macro_data.get("GDPNOW_VINTAGES")
        or macro_data.get("GDPNOW_VINTAGES_PRIOR")
        or macro_data.get("GDPNOW")
    )
    return (True, None) if has_data else (False, "no_gdpnow_data_in_cache_window")


def _reconstructable_inflation(macro_data: dict) -> tuple[bool, str | None]:
    has_data = any(macro_data.get(sid) for sid in _INFLATION_CORE_SERIES)
    return (True, None) if has_data else (False, "no_realized_core_data_in_cache_window")


def replay(start: str, end: str, cache: dict[str, list[dict]]) -> list[dict]:
    """Walk every business date from ``start`` to ``end``, reconstructing
    `macro_data` as of that date and calling the real production classifier,
    chaining the N=2 confirmation hysteresis forward day by day.

    Per date, returns:
    ``{date, growth: {raw_direction, direction, raw_streak, direction_pending,
    confirmed_as_of, basis, confidence, rollover}, inflation: {raw_direction,
    direction, raw_streak, direction_pending, confirmed_as_of},
    active_quadrant, reconstructable: {growth, inflation},
    reconstructable_reason: {growth, inflation}}``.

    ``active_quadrant`` is computed from the CONFIRMED directions (never the
    raw ones) -- exactly what production's own consumers read.
    """
    out: list[dict] = []
    prev_growth_state: dict | None = None
    prev_inflation_state: dict | None = None

    for as_of in business_dates(start, end):
        q_start, prior_q_start = quarter_bounds_for(as_of)
        macro_data = macro_data_as_of(as_of, cache, q_start, prior_q_start)

        growth_raw = _build_growth_axis(macro_data)
        inflation_raw = _build_inflation_axis(macro_data, None, as_of)

        growth_confirm = _confirm_axis_direction(growth_raw["direction"], prev_growth_state, as_of)
        inflation_confirm = _confirm_axis_direction(inflation_raw["direction"], prev_inflation_state, as_of)

        g_ok, g_reason = _reconstructable_growth(macro_data)
        i_ok, i_reason = _reconstructable_inflation(macro_data)

        out.append({
            "date": as_of,
            "growth": {
                **growth_confirm,
                "basis": growth_raw.get("basis"),
                "confidence": growth_raw.get("confidence"),
                "rollover": growth_raw.get("rollover"),
            },
            "inflation": {**inflation_confirm},
            "active_quadrant": active_quadrant(growth_confirm["direction"], inflation_confirm["direction"]),
            "reconstructable": {"growth": g_ok, "inflation": i_ok},
            "reconstructable_reason": {"growth": g_reason, "inflation": i_reason},
        })

        prev_growth_state = growth_confirm
        prev_inflation_state = inflation_confirm

    return out


if __name__ == "__main__":
    import argparse

    import alfred_cache

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    args = ap.parse_args()

    cache = alfred_cache.load_all_cached()
    if not cache:
        print(f"No ALFRED cache found in {alfred_cache.CACHE_DIR} -- run alfred_cache.py first.")
        sys.exit(2)

    records = replay(args.start, args.end, cache)
    print(f"Replayed {len(records)} business dates, {args.start}..{args.end}")
    changes = [
        (a, b) for a, b in zip(records, records[1:])
        if a["active_quadrant"] != b["active_quadrant"]
    ]
    print(f"{len(changes)} active_quadrant change(s):")
    for a, b in changes:
        print(f"  {b['date']}: {a['active_quadrant'] or '(none)'} -> {b['active_quadrant'] or '(none)'}")
