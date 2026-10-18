"""Reference data for the out-of-sample validation (docs/specs/Core_Candidates_OOS_Preregistration.md). Read-only public downloads.
**Nothing dated after 2004-12-31 is ever requested, kept or read** (2005-2025 is spent; 2026 is reserved).

    PYTHONPATH=src python -m research.core_candidates.oos download [--refresh]

Sources:
  * `yfinance` `^SP500TR` (S&P 500 Total Return index) from 1988-01-04, and SPY from 1993-01-01 (for the tracking check only),
    `auto_adjust=False`, `end` exclusive at 2005-01-01.
  * FRED CSV `DTB3` and `BAA10Y` with `cosd=1988-01-01&coed=2004-12-31`.
Cache: `data/reference/core_candidates_oos/` (gitignored), separate from the 2005-2025 study's cache. Reuses the parent study's Yahoo
normaliser, FRED parser and cache writer unchanged.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd

from backtest.data.config import REPO_ROOT
from backtest.wheel_data import http_text, parse_fred_csv

from . import data as parent

CACHE_DIR = REPO_ROOT / "data" / "reference" / "core_candidates_oos"
LAST_ALLOWED = date(2004, 12, 31)
YAHOO_END = "2005-01-01"                      # exclusive
INDEX = "^SP500TR"
YAHOO = {INDEX: "1988-01-01", "SPY": "1993-01-01"}
FRED_SERIES = ("DTB3", "BAA10Y")
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd=1988-01-01&coed=2004-12-31"


class OutOfWindow(ValueError):
    """A date after 2004-12-31 was requested or found."""


def refuse_after_2004(*days) -> None:
    for d in days:
        d = d.date() if isinstance(d, (datetime, pd.Timestamp)) else d
        if d > LAST_ALLOWED:
            raise OutOfWindow(f"{d} is after {LAST_ALLOWED}: this validation reads no date after 2004 (2005-2025 is spent, 2026 reserved).")


def _check(df: pd.DataFrame, name: str) -> pd.DataFrame:
    if len(df):
        refuse_after_2004(df["date"].max())
    return df


def _name(sym: str) -> str:
    return sym.replace("^", "IDX_")


def download_yahoo(symbol: str, *, root: Path = CACHE_DIR, fetch=None) -> dict:
    df = parent.normalise_yahoo((fetch or parent.yahoo_history)(symbol, YAHOO[symbol], YAHOO_END))
    if not len(df):
        raise ValueError(f"Yahoo returned no rows for {symbol}; nothing was cached.")
    _check(df, symbol)
    return parent._write(df, {"symbol": symbol, "downloaded_utc": parent._now(),
                              "request": {"start": YAHOO[symbol], "end_exclusive": YAHOO_END}}, "yahoo", _name(symbol), root)


def download_fred(series: str, *, root: Path = CACHE_DIR, fetch=None) -> dict:
    df = parse_fred_csv((fetch or http_text)(FRED_URL.format(series=series)), series)
    if not len(df) or df["value"].notna().sum() == 0:
        raise ValueError(f"FRED returned no usable rows for {series}; nothing was cached.")
    _check(df, series)
    return parent._write(df, {"series": series, "source": FRED_URL.format(series=series), "downloaded_utc": parent._now()},
                         "fred", series, root)


def read(kind: str, name: str, root: Path = CACHE_DIR) -> pd.DataFrame:
    df, _ = parent.read(kind, _name(name) if kind == "yahoo" else name, root)
    return _check(df, name)


def ensure_cache(*, root: Path = CACHE_DIR, refresh: bool = False, log=None) -> None:
    for sym in YAHOO:
        if refresh or not (root / "yahoo" / f"{_name(sym)}.parquet").is_file():
            m = download_yahoo(sym, root=root)
            if log:
                log(f"{sym}: {m['rows']} rows {m['first_date']}..{m['last_date']}")
    for s in FRED_SERIES:
        if refresh or not (root / "fred" / f"{s}.parquet").is_file():
            m = download_fred(s, root=root)
            if log:
                log(f"{s}: {m['rows']} rows {m['first_date']}..{m['last_date']}")
