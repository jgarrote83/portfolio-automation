"""The four return definitions of the bonds-vs-SPY analysis, and the day table built from them (pure; no I/O).

For trading day D and one symbol, with prior trading day P:

    gap      = open_D            / close_P  - 1      (daily bars: the overnight move)
    morning  = open(10:00 bar)   / open_D   - 1      (09:30 open to the open of the 1-minute bar stamped 10:00)
    rest     = close_D           / open(10:00 bar) - 1
    full     = close_D           / close_P  - 1

so (1 + gap)(1 + morning)(1 + rest) = 1 + full by construction (an identity the tests and the verifier check). TLT and IEF
RISE when yields FALL: "bonds up" means "yields down".

A day is used only when all three symbols have their daily bar on D and P and a 10:00 minute bar on D. Anything else is SKIPPED and
COUNTED with its reason; nothing is ever filled or interpolated.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

SYMBOLS = ("TLT", "IEF", "SPY")
MEASURES = ("gap", "morning", "rest", "full")


def day_returns(open_d: float, close_d: float, close_p: float, open10: float) -> dict[str, float]:
    """The four returns for one symbol-day (fractions, not percent)."""
    return {"gap": open_d / close_p - 1.0, "morning": open10 / open_d - 1.0, "rest": close_d / open10 - 1.0, "full": close_d / close_p - 1.0}


def build_day_table(calendar: list[date], daily: dict[str, dict[date, tuple[float, float]]], open10: dict[str, dict[date, float]],
                    first_day: date, last_day: date, symbols: tuple[str, ...] = SYMBOLS) -> tuple[pd.DataFrame, dict]:
    """One row per usable day D in [first_day, last_day], columns `{SYMBOL}_{measure}`.

    `calendar` is the ascending list of trading days (SPY's daily bars), so the prior trading day P is the previous element.
    `daily[symbol][date] = (open, close)`; `open10[symbol][date]` = the open of the 1-minute bar stamped 10:00.
    Returns (table, skips) where skips = {"days_in_window": n, "used": n, "skipped_days": {date: [reasons]}, "by_reason": {reason: count}}."""
    rows, skipped = [], {}
    idx = {d: i for i, d in enumerate(calendar)}
    window = [d for d in calendar if first_day <= d <= last_day]
    for d in window:
        i = idx[d]
        reasons = []
        if i == 0:
            skipped[d] = ["no_prior_trading_day"]
            continue
        p = calendar[i - 1]
        for s in symbols:
            if d not in daily.get(s, {}):
                reasons.append(f"missing_daily_bar_D:{s}")
            if p not in daily.get(s, {}):
                reasons.append(f"missing_daily_bar_prior:{s}")
            if d not in open10.get(s, {}):
                reasons.append(f"missing_1000_bar:{s}")
        if reasons:
            skipped[d] = reasons
            continue
        row: dict = {"date": d}
        for s in symbols:
            o_d, c_d = daily[s][d]
            c_p = daily[s][p][1]
            for m, v in day_returns(o_d, c_d, c_p, open10[s][d]).items():
                row[f"{s}_{m}"] = v
        rows.append(row)
    table = pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame(columns=[f"{s}_{m}" for s in symbols for m in MEASURES])
    by_reason: dict[str, int] = {}
    for rs in skipped.values():
        for r in rs:
            by_reason[r] = by_reason.get(r, 0) + 1
    return table, {"days_in_window": len(window), "used": len(table), "skipped_days": skipped, "by_reason": by_reason}


def first_trading_day_of_month(calendar: list[date]) -> set[date]:
    """Days whose calendar month differs from the previous trading day's (the crude proxy for a monthly ex-distribution date)."""
    return {b for a, b in zip(calendar, calendar[1:]) if (a.year, a.month) != (b.year, b.month)}
