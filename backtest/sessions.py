"""Regular-session close times for the 2024-2025 window.

The Phase 2 spec says half-days "use the calendar close". The calendar endpoint is a trading API
(never called by the backtest), so the NYSE early closes (13:00 ET) are listed here, as locked in
the pre-registration (section 5, item 11). `backtest.verify_run` checks the table after a real run against the
volume collapse that follows a 13:00 close, and REPORTS any mismatch; it never edits the table. (A
"last SPY bar" comparison cannot work: extended-hours bars exist after an early close.)
"""
from __future__ import annotations

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
