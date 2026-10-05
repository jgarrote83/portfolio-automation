"""Opening-range direction, entry/stop levels and the bar-level fill rules. Pure Python.

The geometry here is the strategy; the loops that call it (the backtest's minute-by-minute engine
today, the live 1-minute reconcile loop in Phase 4) own the I/O and the state.

Fill rules (Phase 2 pre-registration, section 5):
  * A long's entry stop-order triggers in a bar whose HIGH >= the trigger; a short's in a bar whose
    LOW <= the trigger. It fills at the trigger, or at the bar's OPEN if the open is already
    beyond it (a gap).
  * The stop is trigger -/+ 10% of ATR, rounded to the cent AWAY from the entry (never tighter than
    the rule). It triggers in a bar whose LOW <= the stop (long) / HIGH >= the stop (short) and fills
    at the stop, or at the bar's OPEN if the open is already beyond it.
  * Entry and stop in the SAME bar => the stop is hit (for the entry bar the stop fills at the
    stop level: the open predates the entry, so a gap fill at the open is impossible there).
"""
from __future__ import annotations

import math
from typing import NamedTuple

from .config import OrbConfig

LONG = "long"
SHORT = "short"


class EntryPlan(NamedTuple):
    side: str
    trigger: float          # the 5-minute high (long) / low (short)
    stop: float             # protective stop level, rounded away from the entry
    stop_distance: float    # |trigger - stop|, the R per share used for sizing


def direction_from_bar(open_price: float, close_price: float) -> str | None:
    """First 5-minute bar: close above open -> long; below -> short; equal (doji) -> None."""
    if close_price > open_price:
        return LONG
    if close_price < open_price:
        return SHORT
    return None


def round_stop(price: float, side: str, tick: float) -> float:
    """Round a stop to the tick AWAY from the entry: down for a long's stop, up for a short's."""
    n = round(price / tick, 6)                       # kill float noise (10079.999999999998 -> 10080.0)
    n = math.floor(n) if side == LONG else math.ceil(n)
    return round(n * tick, 6)


def entry_plan(side: str, bar_high: float, bar_low: float, atr: float, cfg: OrbConfig) -> EntryPlan:
    """Trigger and stop for one pick, from its first 5-minute bar and its ATR."""
    if side not in (LONG, SHORT):
        raise ValueError(f"side must be {LONG!r} or {SHORT!r}, not {side!r}")
    trigger = bar_high if side == LONG else bar_low
    raw_stop = trigger - cfg.stop_atr_fraction * atr if side == LONG else trigger + cfg.stop_atr_fraction * atr
    stop = round_stop(raw_stop, side, cfg.tick_size)
    return EntryPlan(side, trigger, stop, round(abs(trigger - stop), 6))


def trigger_fill(side: str, bar_open: float, bar_high: float, bar_low: float,
                 trigger: float) -> float | None:
    """Entry fill price if the bar triggers the entry order, else None."""
    if side == LONG:
        if bar_high < trigger:
            return None
        return bar_open if bar_open > trigger else trigger
    if bar_low > trigger:
        return None
    return bar_open if bar_open < trigger else trigger


def stop_fill(side: str, bar_open: float, bar_high: float, bar_low: float,
              stop: float) -> float | None:
    """Stop-out fill price for a position that was already open BEFORE this bar, else None."""
    if side == LONG:
        if bar_low > stop:
            return None
        return bar_open if bar_open < stop else stop
    if bar_high < stop:
        return None
    return bar_open if bar_open > stop else stop


def stop_hit_in_entry_bar(side: str, bar_high: float, bar_low: float, stop: float) -> bool:
    """Entry and stop in the same minute bar: assume the stop was hit (pre-registered rule).

    The fill is at the stop level -- the bar's open cannot be the exit because it predates the
    entry (a gap entry fills at the open, which is already on the profit side of the stop)."""
    return bar_low <= stop if side == LONG else bar_high >= stop
