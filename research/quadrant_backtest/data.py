"""Price data for the quadrant-tilt backtest (docs/specs/Quadrant_Backtest_Preregistration.md). Read-only public downloads; never 2026.

    PYTHONPATH=src python -m research.quadrant_backtest.run download [--refresh]

`yfinance` daily history (`auto_adjust=False`; Yahoo's dividend-and-split adjusted close is the total-return series) for every basket member,
every proxy named by the pre-registration's asset-class rule, and SPY, from 2009-01-01 with `end` exclusive at 2026-01-01. Cache:
`data/reference/quadrant_backtest/` (gitignored). Reuses the Core-candidates study's Yahoo normaliser and cache writer/reader unchanged
(they refuse any row dated on/after 2026-01-01). DTB3 and BAA10Y (for the Sharpe ratio and C5) are read from the Core-candidates cache.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest.data.config import REPO_ROOT
from research.core_candidates import data as cc

CACHE_DIR = REPO_ROOT / "data" / "reference" / "quadrant_backtest"
DATA_FROM = "2009-01-01"
YAHOO_END = "2026-01-01"                       # exclusive
MEMBERS = ("SPY", "QQQ", "SMH", "XLI", "XLF", "COWZ", "VDE", "PDBC", "VTIP", "GLD", "XLP", "XLV", "KMLM", "TLT", "IEF", "USMV")
PROXIES = ("IWD", "SPLV", "DBC", "STIP", "SOXX")   # SOXX only as the semiconductor fallback if SMH lacks history
SYMBOLS = tuple(dict.fromkeys(MEMBERS + PROXIES))


def download(symbol: str, *, root: Path = CACHE_DIR, fetch=None) -> dict:
    df = cc.normalise_yahoo((fetch or cc.yahoo_history)(symbol, DATA_FROM, YAHOO_END))
    if not len(df):
        raise ValueError(f"Yahoo returned no rows for {symbol}; nothing was cached.")
    return cc._write(df, {"symbol": symbol, "downloaded_utc": cc._now(), "request": {"start": DATA_FROM, "end_exclusive": YAHOO_END}},
                     "yahoo", symbol, root)


def read(symbol: str, root: Path = CACHE_DIR) -> pd.DataFrame:
    return cc.read("yahoo", symbol, root)[0]


def ensure_cache(*, root: Path = CACHE_DIR, refresh: bool = False, log=None) -> None:
    for s in SYMBOLS:
        if refresh or not (root / "yahoo" / f"{s}.parquet").is_file():
            m = download(s, root=root)
            if log:
                log(f"{s}: {m['rows']} rows {m['first_date']}..{m['last_date']}")


def first_dates(root: Path = CACHE_DIR) -> dict[str, str]:
    """Each symbol's first adjusted-close date in the cache (the request starts 2009-01-01, so an older fund shows 2009-01-02)."""
    return {s: read(s, root)["date"].min().date().isoformat() for s in SYMBOLS}
