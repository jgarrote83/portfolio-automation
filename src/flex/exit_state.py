"""Deterministic flex-exit state machine (pure).

For each held flex position, compute the mechanical exit triple — ATR stop /
scale-out → breakeven / ATR-trail / time-stop — and emit the single
``next_action`` the engine should take this tick. All quantities key off
``qty_current`` (the remaining shares after any partial scale-out), never
``qty_initial``. Missing data ⟹ ``next_action == "unknown"`` (never a crash, never
a forced trade). ``"stopped"`` is decided by reconciliation (a broker fill), not
here, but is part of the action vocabulary for callers.
"""
from __future__ import annotations

from datetime import date, datetime

from flex.config import FlexConfig
from flex.indicators import atr14, session_vwap

NEXT_ACTIONS = ("hold", "scale_out", "trail", "time_stop", "stopped", "unknown")


def _to_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def trading_days_between(entry_date, now) -> int | None:
    """Weekdays elapsed since entry (entry day == 0; the next weekday == 1)."""
    start = _to_date(entry_date)
    end = _to_date(now)
    if start is None or end is None or end < start:
        return None
    count = 0
    cur = start
    while cur < end:
        cur = date.fromordinal(cur.toordinal() + 1)
        if cur.weekday() < 5:  # Mon–Fri
            count += 1
    return count


def _last_close(bars: list[dict]) -> float | None:
    for b in reversed(bars or []):
        try:
            return float(b.get("c"))
        except (TypeError, ValueError):
            continue
    return None


def build_flex_exit_state(
    ledger_entry: dict,
    intraday_bars: list[dict],
    daily_bars: list[dict],
    cfg: FlexConfig,
    now: datetime,
) -> dict:
    entry_price = _num(ledger_entry.get("entry_price"))
    initial_stop = _num(ledger_entry.get("initial_stop"))
    current_stop = _num(ledger_entry.get("current_stop")) or initial_stop
    qty_current = int(ledger_entry.get("qty_current") or 0)
    scaled_out = bool(ledger_entry.get("scaled_out"))
    risk_per_share = _num(ledger_entry.get("risk_per_share"))
    if risk_per_share is None and entry_price is not None and initial_stop is not None:
        risk_per_share = entry_price - initial_stop

    current_price = _last_close(intraday_bars)
    atr = atr14(daily_bars)
    tdays = trading_days_between(ledger_entry.get("entry_date"), now)

    r_multiple = None
    if (
        current_price is not None
        and entry_price is not None
        and risk_per_share
        and risk_per_share > 0
    ):
        r_multiple = (current_price - entry_price) / risk_per_share

    trail_stop = None
    if current_price is not None and atr is not None and atr > 0:
        atr_trail = current_price - cfg.atr_mult * atr
        # Ride VWAP support only when it sits BELOW price and is tighter than the
        # ATR trail — a stop must never be placed at/above the current price.
        raw = atr_trail
        vwap = session_vwap(intraday_bars)
        if vwap is not None and atr_trail < vwap < current_price:
            raw = vwap
        base = current_stop if current_stop is not None else raw
        trail_stop = max(base, raw)

    out: dict = {
        "symbol": str(ledger_entry.get("symbol") or "").upper(),
        "current_price": current_price,
        "atr14": atr,
        "r_multiple": r_multiple,
        "time_in_trade_days": tdays,
        "qty_current": qty_current,
        "trail_stop": trail_stop,
        "target_stop": None,
        "scale_out_qty": None,
        "stop_move_needed": False,
        "next_action": "unknown",
    }

    # Insufficient data → unknown (never a forced trade).
    if current_price is None or atr is None or qty_current < 1:
        return out

    # 1. Time stop — a catalyst has a shelf life.
    if tdays is not None and tdays >= cfg.time_stop_days:
        out["next_action"] = "time_stop"
        out["scale_out_qty"] = qty_current
        return out

    # 2. First scale-out target → bank half, move the runner's stop to breakeven.
    if (
        not scaled_out
        and qty_current >= 2
        and r_multiple is not None
        and r_multiple >= cfg.first_target_r
    ):
        qty = int(cfg.scale_out_fraction * qty_current)
        qty = max(1, min(qty, qty_current - 1))  # always leave a runner
        out["next_action"] = "scale_out"
        out["scale_out_qty"] = qty
        out["target_stop"] = entry_price  # breakeven on the remainder
        out["stop_move_needed"] = True
        return out

    # 3. Trail the stop up — monotonic, epsilon-gated.
    if (
        trail_stop is not None
        and current_stop is not None
        and (trail_stop - current_stop) >= cfg.stop_epsilon(atr)
    ):
        out["next_action"] = "trail"
        out["target_stop"] = trail_stop
        out["stop_move_needed"] = True
        return out

    out["next_action"] = "hold"
    return out


def _num(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
