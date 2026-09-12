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
from flex.indicators import atr14

# N2 (2026-09-12): `scale_out` and `trail` are RETIRED — both exits rest at the
# broker as a native OCO bracket. `time_stop` is the only engine-managed exit
# left. The retired values stay in this tuple so a historical `flex_state` blob
# still validates against it; nothing produces them any more.
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
    qty_current = int(ledger_entry.get("qty_current") or 0)
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

    out: dict = {
        "symbol": str(ledger_entry.get("symbol") or "").upper(),
        "current_price": current_price,
        "atr14": atr,
        "r_multiple": r_multiple,
        "time_in_trade_days": tdays,
        "qty_current": qty_current,
        # N2 (session 2026-09-12): no trail, no scale-out. Both exits (+take_profit
        # / -stop) now REST AT THE BROKER as a native OCO bracket placed at entry,
        # so neither is engine-managed and neither is 15-minute-quantized. These
        # keys are retained at None so `flex_state` consumers (and the report's
        # per-name echo) keep a stable shape across the change.
        "trail_stop": None,
        "target_stop": None,
        "scale_out_qty": None,
        "stop_move_needed": False,
        "next_action": "unknown",
    }

    # Insufficient data → unknown (never a forced trade).
    if current_price is None or atr is None or qty_current < 1:
        return out

    # The ONLY engine-managed exit left: the time stop. A catalyst has a shelf
    # life, and the bracket legs cannot express "N days elapsed". Task E
    # (2026-08-14): the conviction path has NO calendar clock at all — its shelf
    # life is the collector-side release_sessions hysteresis decay instead (see
    # `_release_pending_exit`/flex/handler.py). A conviction entry skips this rule
    # entirely, never falling through to a phantom time_stop.
    #
    # NOTE (FOLLOWUPS #107): the conviction path's confirm/release hysteresis was
    # inherited from a multi-week overlay and may be incompatible with a ~2-day
    # horizon. Unresolved; the catalyst path below is unaffected.
    is_conviction = str(ledger_entry.get("path") or "catalyst") == "conviction"
    if not is_conviction and tdays is not None and tdays >= cfg.time_stop_days:
        out["next_action"] = "time_stop"
        out["scale_out_qty"] = qty_current
        return out

    out["next_action"] = "hold"
    return out


def _num(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
