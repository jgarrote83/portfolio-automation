"""Regular-session close times for the 2024-2025 window.

The Phase 2 spec says half-days "use the calendar close". The calendar endpoint is a trading API
(never called by the backtest), so the NYSE early closes (13:00 ET) are listed here, as locked in
the pre-registration (section 5, item 11). `check_early_closes` cross-checks the table against
SPY's own one-minute bars on a real run and REPORTS any mismatch; it never edits the table.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date

FULL_CLOSE_MIN = 16 * 60
EARLY_CLOSE_MIN = 13 * 60

EARLY_CLOSE_DATES = frozenset({
    date(2024, 7, 3), date(2024, 11, 29), date(2024, 12, 24),
    date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24),
})


def close_minute(day: date) -> int:
    """Calendar close of `day`, minutes since midnight ET (960 = 16:00, 780 = 13:00)."""
    return EARLY_CLOSE_MIN if day in EARLY_CLOSE_DATES else FULL_CLOSE_MIN


def check_early_closes(days: Iterable[date],
                       last_bar_minute: Callable[[date], int | None]) -> list[dict]:
    """Compare the table with the market: `last_bar_minute(day)` is the stamp (minutes since
    midnight ET) of SPY's last regular-session one-minute bar that day, or None if it has none.

    A full day should end at 15:59 (959) and an early-close day at 12:59 (779). Returns one dict per
    mismatch: {day, expected_close_min, last_bar_min}. A day with no SPY bar at all is a mismatch."""
    bad = []
    for d in days:
        exp = close_minute(d)
        last = last_bar_minute(d)
        if last is None or last != exp - 1:
            bad.append({"day": d.isoformat(), "expected_close_min": exp, "last_bar_min": last})
    return bad
