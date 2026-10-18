"""Reference data for the Core-candidates study (docs/specs/Core_Candidates_Preregistration.md). Read-only public downloads; never reads 2026.

    PYTHONPATH=src python -m research.core_candidates.run download [--refresh]

Sources:
  * `yfinance` daily history for SPY, EFA, AGG, SSO and ACWX, `auto_adjust=False, actions=True`, start 2003-01-01, `end` exclusive at
    2026-01-01 (the last day requested is 2025-12-31). Kept: the raw close, Yahoo's dividend-and-split adjusted close ('Adj Close'),
    dividends and splits.
  * FRED CSV `fredgraph.csv?id=<SERIES>&cosd=2003-01-01&coed=2025-12-31` for DTB3 and BAA10Y, so nothing dated after 2025-12-31 is ever
    requested.
Cache: `data/reference/core_candidates/` (gitignored), one parquet + one JSON metadata file per series. A run reads only this cache. Any
cached or received row dated on/after 2026-01-01 is a failure (`HoldoutError`), never used.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from backtest.data.bars import HoldoutError
from backtest.data.config import REPO_ROOT
from backtest.wheel_data import http_text, parse_fred_csv

from . import holdout

CACHE_DIR = REPO_ROOT / "data" / "reference" / "core_candidates"
DATA_FROM = date(2003, 1, 1)
LAST_DAY = date(2025, 12, 31)
HOLDOUT_START = date(2026, 1, 1)
YAHOO_END = "2026-01-01"                       # exclusive
SYMBOLS = ("SPY", "EFA", "AGG", "SSO", "ACWX")
FRED_SERIES = ("DTB3", "BAA10Y")
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd=2003-01-01&coed=2025-12-31"


def assert_not_holdout(*days) -> None:
    for d in days:
        d = d.date() if isinstance(d, (datetime, pd.Timestamp)) else d
        if d >= HOLDOUT_START and not holdout.permits(d):
            raise HoldoutError(f"{d} is on/after {HOLDOUT_START}: the 2026 window is this study's holdout and is closed (no override exists).")


def yahoo_history(symbol: str, start: str, end: str) -> pd.DataFrame:
    import yfinance as yf
    return yf.Ticker(symbol).history(start=start, end=end, interval="1d", auto_adjust=False, actions=True)


def normalise_yahoo(h: pd.DataFrame) -> pd.DataFrame:
    """yfinance frame -> `date, close, adj_close, dividends, split` (session date = the row's local calendar date), sorted, deduplicated."""
    cols = ["date", "close", "adj_close", "dividends", "split"]
    if h is None or len(h) == 0:
        return pd.DataFrame({c: pd.Series(dtype="datetime64[ns]" if c == "date" else float) for c in cols})
    zero = pd.Series(0.0, index=h.index)
    out = pd.DataFrame({
        "date": pd.to_datetime([ts.date() for ts in h.index]),
        "close": pd.to_numeric(h["Close"], errors="coerce").to_numpy(dtype=float),
        "adj_close": pd.to_numeric(h["Adj Close"], errors="coerce").to_numpy(dtype=float),
        "dividends": pd.to_numeric(h["Dividends"] if "Dividends" in h else zero, errors="coerce").fillna(0.0).to_numpy(dtype=float),
        "split": pd.to_numeric(h["Stock Splits"] if "Stock Splits" in h else zero, errors="coerce").fillna(0.0).to_numpy(dtype=float),
    })
    return out.dropna(subset=["adj_close"]).drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)


def _paths(kind: str, name: str, root: Path) -> tuple[Path, Path]:
    return root / kind / f"{name}.parquet", root / kind / f"{name}.json"


def _write(df: pd.DataFrame, meta: dict, kind: str, name: str, root: Path) -> dict:
    if len(df) and df["date"].max().date() >= HOLDOUT_START and not holdout.permits(df["date"].max().date()):
        raise HoldoutError(f"{name}: received a row dated {df['date'].max().date()}; refusing to keep 2026 data.")
    pq, js = _paths(kind, name, root)
    pq.parent.mkdir(parents=True, exist_ok=True)
    tmp = pq.with_name(f"{pq.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        df.to_parquet(tmp, engine="pyarrow", index=False)
        os.replace(tmp, pq)
    finally:
        if tmp.exists():
            tmp.unlink()
    meta = {**meta, "rows": len(df), "first_date": df["date"].min().date().isoformat() if len(df) else None,
            "last_date": df["date"].max().date().isoformat() if len(df) else None,
            "content_sha256": hashlib.sha256(df.to_csv(index=False).encode("utf-8")).hexdigest()}
    js.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def read(kind: str, name: str, root: Path = CACHE_DIR) -> tuple[pd.DataFrame, dict]:
    pq, js = _paths(kind, name, root)
    if not pq.is_file() or not js.is_file():
        raise FileNotFoundError(f"{name} is not cached under {pq.parent} (run `python -m research.core_candidates.run download`).")
    df = pd.read_parquet(pq)
    df["date"] = pd.to_datetime(df["date"])
    if len(df) and df["date"].max().date() >= HOLDOUT_START and not holdout.permits(df["date"].max().date()):
        raise HoldoutError(f"{name}: the cache holds a 2026 row; refusing to read it.")
    return df, json.loads(js.read_text(encoding="utf-8"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def download_yahoo(symbol: str, *, root: Path = CACHE_DIR, fetch=None) -> dict:
    df = normalise_yahoo((fetch or yahoo_history)(symbol, DATA_FROM.isoformat(), YAHOO_END))
    if not len(df):
        raise ValueError(f"Yahoo returned no rows for {symbol}; nothing was cached.")
    try:
        import yfinance
        version = yfinance.__version__
    except Exception:
        version = "unavailable"
    splits = [{"date": d.date().isoformat(), "ratio": float(r)} for d, r in zip(df["date"], df["split"]) if r > 0]
    return _write(df, {"symbol": symbol, "yfinance": version, "downloaded_utc": _now(), "splits": splits,
                       "request": {"start": DATA_FROM.isoformat(), "end_exclusive": YAHOO_END},
                       "adjustment": "auto_adjust=False; adj_close is Yahoo's dividend-and-split adjusted close"}, "yahoo", symbol, root)


def download_fred(series: str, *, root: Path = CACHE_DIR, fetch: Callable[[str], str] | None = None) -> dict:
    df = parse_fred_csv((fetch or http_text)(FRED_URL.format(series=series)), series)
    if not len(df) or df["value"].notna().sum() == 0:
        raise ValueError(f"FRED returned no usable rows for {series}; nothing was cached.")
    return _write(df, {"series": series, "source": FRED_URL.format(series=series), "downloaded_utc": _now()}, "fred", series, root)


def ensure_cache(*, root: Path = CACHE_DIR, refresh: bool = False, fetch_text=None, fetch_yahoo=None, log=None) -> dict[str, dict]:
    metas = {}
    plan = ([("yahoo", s, lambda s=s: download_yahoo(s, root=root, fetch=fetch_yahoo)) for s in SYMBOLS]
            + [("fred", s, lambda s=s: download_fred(s, root=root, fetch=fetch_text)) for s in FRED_SERIES])
    for kind, name, go in plan:
        pq, js = _paths(kind, name, root)
        if refresh or not (pq.is_file() and js.is_file()):
            metas[name] = go()
            if log:
                log(f"{name}: {metas[name]['rows']} rows {metas[name]['first_date']}..{metas[name]['last_date']}")
        else:
            metas[name] = json.loads(js.read_text(encoding="utf-8"))
    return metas
