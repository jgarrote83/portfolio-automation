"""Load TLT, IEF and SPY bars for the bonds-vs-SPY analysis. Alpaca market-data GETs go ONLY through `backtest.data.get_bars` (SIP, raw), so the
research data layer's refusal of any date on or after 2026-01-01 stays in force; nothing here widens it.

Daily bars: 2023-12-01 to 2025-12-31 (the first window day needs a prior close). Minute bars: 2024-01-02 to 2025-12-31 under the window
09:30-16:00, the same cache key the earlier SPY studies used, so the SPY minute bars are reused and TLT / IEF are fetched once.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

SYMBOLS = ("TLT", "IEF", "SPY")
DAILY_FROM, FIRST_DAY, LAST_DAY = date(2023, 12, 1), date(2024, 1, 2), date(2025, 12, 31)
WINDOW = ("09:30", "16:00")
GRID_START, GRID_END = 9 * 60 + 30, 11 * 60                 # minutes of day: bars stamped 09:30 ... 11:00 inclusive
GRID_LEN = GRID_END - GRID_START + 1                         # 91 bars -> 90 one-minute returns (stamped 09:31 ... 11:00)
OPEN10 = 10 * 60 - GRID_START                                # index of the 10:00 bar on the grid


def load_frames(layer=None, *, offline: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(daily, minute) frames from the data layer (cache first; read-only API fetches only for what is missing)."""
    if LAST_DAY >= date(2026, 1, 1):
        raise ValueError("the analysis window must end before 2026-01-01")
    if layer is None:
        from backtest.data import DataLayer
        layer = DataLayer()
    daily = layer.get_bars(list(SYMBOLS), DAILY_FROM, LAST_DAY, "1Day", "sip", offline=offline)
    minute = layer.get_bars(list(SYMBOLS), FIRST_DAY, LAST_DAY, "1Min", "sip", window=WINDOW, offline=offline)
    return daily, minute


def daily_maps(daily: pd.DataFrame) -> dict[str, dict[date, tuple[float, float]]]:
    """{symbol: {session date: (open, close)}} from daily bars (their stamp is midnight New York time)."""
    out: dict[str, dict[date, tuple[float, float]]] = {s: {} for s in SYMBOLS}
    d = daily.assign(day=daily["ts"].dt.date)
    for sym, day, o, c in zip(d["symbol"], d["day"], d["open"], d["close"]):
        out[sym][day] = (float(o), float(c))
    return out


def minute_maps(minute: pd.DataFrame) -> tuple[dict[str, dict[date, np.ndarray]], dict[str, dict[date, float]]]:
    """({symbol: {date: 91 minute CLOSES 09:30..11:00, NaN where the bar is absent}}, {symbol: {date: open of the 10:00 bar}}).
    A day with no 09:30-11:00 bars at all has no entry; a missing 10:00 bar has no `open10` entry. Nothing is filled."""
    closes: dict[str, dict[date, np.ndarray]] = {s: {} for s in SYMBOLS}
    open10: dict[str, dict[date, float]] = {s: {} for s in SYMBOLS}
    m = minute["ts"].dt.hour * 60 + minute["ts"].dt.minute
    sel = minute[(m >= GRID_START) & (m <= GRID_END)].assign(m=m[(m >= GRID_START) & (m <= GRID_END)], day=lambda f: f["ts"].dt.date)
    for (sym, day), g in sel.groupby(["symbol", "day"], sort=False):
        arr = np.full(GRID_LEN, np.nan)
        arr[(g["m"] - GRID_START).to_numpy()] = g["close"].to_numpy()
        closes[sym][day] = arr
        row = g[g["m"] == GRID_START + OPEN10]
        if len(row):
            open10[sym][day] = float(row["open"].iloc[0])
    return closes, open10
