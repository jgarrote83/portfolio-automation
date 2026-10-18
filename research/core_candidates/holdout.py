"""The ONE allowance for reading 2026 in `research/core_candidates` (docs/specs/C5_Holdout_Preregistration.md, item 4).

Every 2026 refusal in this package (`engine.refuse_holdout`, `data.assert_not_holdout`, the cache writer and reader) asks `permits(d)`.
It is False unless the scoped context `allow(date(2026, 5, 19))` is active, and then True only for 2026-01-01..2026-05-19. No other end
date is accepted. `backtest.data`'s own research guard is a different mechanism and is not touched.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date

HOLDOUT_START = date(2026, 1, 1)
HOLDOUT_END = date(2026, 5, 19)
_active_end: date | None = None


def permits(d: date) -> bool:
    return _active_end is not None and HOLDOUT_START <= d <= _active_end


@contextmanager
def allow(end: date):
    global _active_end
    if end != HOLDOUT_END:
        raise ValueError(f"the only holdout allowance is through {HOLDOUT_END}; {end} refused")
    prev, _active_end = _active_end, end
    try:
        yield
    finally:
        _active_end = prev
