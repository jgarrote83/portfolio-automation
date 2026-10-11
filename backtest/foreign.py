"""Foreign daily prices for the global lead-lag study: Yahoo Finance download + parquet cache, and the Stooq
files used by the two-source check. Read-only; never reads 2026.

    PYTHONPATH=src python -m backtest.leadlag download [--refresh]

Yahoo (via `yfinance`, listed in `backtest/requirements.txt` only): one `Ticker(symbol).history(start, end,
interval="1d", auto_adjust=False, actions=True)` per member, `end` EXCLUSIVE at 2026-01-01, so no 2026 bar is ever
requested; rows dated before 2023-12-01 are discarded and any row dated on/after 2026-01-01 is a failure
(`HoldoutError`), never kept. Cache: `data/reference/foreign/yahoo/<symbol>.parquet` (+ `.json` metadata: download
time, yfinance version, rows, time zone, split events). A study run reads only this cache.

Stooq: its CSV endpoint answers with a JavaScript anti-bot page (pre-registration item 15), which is NOT bypassed.
`load_stooq` only reads CSV files placed by hand in `data/reference/foreign/stooq/<Yahoo symbol>.csv`; nothing is
ever fetched from Stooq by this code.
"""
from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from .data.bars import HoldoutError
from .data.config import REPO_ROOT

FOREIGN_DIR = REPO_ROOT / "data" / "reference" / "foreign"
DATA_FROM = date(2023, 12, 1)
REQUEST_START = "2023-11-25"            # a few days earlier so every row from DATA_FROM is surely included
REQUEST_END = "2026-01-01"              # yfinance's `end` is exclusive: the last bar requested is 2025-12-31
HOLDOUT_START = date(2026, 1, 1)

HistoryFetch = Callable[[str, str, str], pd.DataFrame]       # (symbol, start, end) -> a yfinance-shaped history frame
CACHE_COLUMNS = ["date", "open", "close", "dividends", "split"]


def assert_not_holdout(*days: date) -> None:
    """Nothing on or after 2026-01-01 is ever requested, read or kept, from any source."""
    for d in days:
        if d >= HOLDOUT_START:
            raise HoldoutError(f"{d} is on/after {HOLDOUT_START}: the 2026 holdout is closed to the lead-lag study "
                               "(no override exists).")


def yahoo_history(symbol: str, start: str, end: str) -> pd.DataFrame:
    """The real fetch (imported lazily so the tests and CI never need the network or the package at import)."""
    import yfinance as yf
    return yf.Ticker(symbol).history(start=start, end=end, interval="1d", auto_adjust=False, actions=True)


# ======================================================================================== normalising
def normalise_history(h: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """A yfinance history frame -> (rows with the local session date, open, close, dividends, split; the time zone).

    The session date is the row's calendar date in the exchange time zone Yahoo reports (the index is tz-aware
    local midnight). Duplicate dates keep the last row; rows are sorted by date."""
    if h is None or len(h) == 0:
        return pd.DataFrame({"date": pd.Series(dtype="datetime64[ns]"), "open": pd.Series(dtype=float), "close": pd.Series(dtype=float),
                             "dividends": pd.Series(dtype=float), "split": pd.Series(dtype=float)}), ""
    tz = str(h.index.tz) if getattr(h.index, "tz", None) is not None else ""
    dates = [ts.date() for ts in h.index]
    zero = pd.Series(0.0, index=h.index)
    out = pd.DataFrame({
        "date": pd.to_datetime(dates),
        "open": pd.to_numeric(h["Open"], errors="coerce").to_numpy(dtype=float),
        "close": pd.to_numeric(h["Close"], errors="coerce").to_numpy(dtype=float),
        "dividends": pd.to_numeric(h["Dividends"] if "Dividends" in h else zero, errors="coerce").fillna(0.0).to_numpy(dtype=float),
        "split": pd.to_numeric(h["Stock Splits"] if "Stock Splits" in h else zero, errors="coerce").fillna(0.0).to_numpy(dtype=float),
    })
    out = out.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
    return out, tz


def split_events(df: pd.DataFrame) -> list[dict]:
    """[{date, ratio}] for every row carrying a stock split."""
    s = df[df["split"] > 0]
    return [{"date": d.date().isoformat(), "ratio": float(r)} for d, r in zip(s["date"], s["split"])]


# ============================================================================================== cache
def _paths(symbol: str, root: Path) -> tuple[Path, Path]:
    return root / "yahoo" / f"{symbol}.parquet", root / "yahoo" / f"{symbol}.json"


def download_member(symbol: str, *, root: Path = FOREIGN_DIR, fetch: HistoryFetch | None = None,
                    start: str = REQUEST_START, end: str = REQUEST_END, now: Callable[[], datetime] | None = None) -> dict:
    """Fetch one member from Yahoo and write the cache. -> the metadata dict. Refuses any 2026 request or row."""
    if date.fromisoformat(end) > HOLDOUT_START or date.fromisoformat(start) >= HOLDOUT_START:
        raise HoldoutError(f"{start}..{end} reaches the 2026 holdout (the end is exclusive and may be at most {HOLDOUT_START}).")
    raw = (fetch or yahoo_history)(symbol, start, end)
    df, tz = normalise_history(raw)
    n_raw = len(df)
    if not n_raw:
        raise ValueError(f"Yahoo returned no rows for {symbol} ({start}..{end}); nothing was cached.")
    if n_raw and df["date"].max().date() >= HOLDOUT_START:
        raise HoldoutError(f"{symbol}: Yahoo returned a row dated {df['date'].max().date()}; refusing to keep 2026 data.")
    df = df[df["date"] >= pd.Timestamp(DATA_FROM)].reset_index(drop=True)
    pq, js = _paths(symbol, root)
    pq.parent.mkdir(parents=True, exist_ok=True)
    tmp = pq.with_name(f"{pq.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        df.to_parquet(tmp, engine="pyarrow", index=False)
        os.replace(tmp, pq)
    finally:
        if tmp.exists():
            tmp.unlink()
    try:
        import yfinance
        version = yfinance.__version__
    except Exception:                                      # the fetch was injected; the package is not needed
        version = "unavailable"
    meta = {"symbol": symbol, "tz": tz, "rows": len(df), "rows_downloaded": n_raw,
            "first_date": df["date"].min().date().isoformat() if len(df) else None,
            "last_date": df["date"].max().date().isoformat() if len(df) else None,
            "splits": split_events(df), "request": {"start": start, "end_exclusive": end},
            "downloaded_utc": (now() if now else datetime.now(timezone.utc)).isoformat(), "yfinance": version,
            "adjustment": "auto_adjust=False (dividends not applied; Yahoo may back-adjust for splits)"}
    js.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def load_member(symbol: str, root: Path = FOREIGN_DIR) -> tuple[pd.DataFrame, dict]:
    """(rows, metadata) from the cache. Raises FileNotFoundError when the member was never downloaded."""
    pq, js = _paths(symbol, root)
    if not pq.is_file() or not js.is_file():
        raise FileNotFoundError(f"{symbol} is not cached under {pq.parent} (run `python -m backtest.leadlag download`).")
    df = pd.read_parquet(pq)
    df["date"] = pd.to_datetime(df["date"])
    if len(df) and df["date"].max().date() >= HOLDOUT_START:
        raise HoldoutError(f"{symbol}: the cache holds a 2026 row; refusing to read it.")
    return df[CACHE_COLUMNS], json.loads(js.read_text(encoding="utf-8"))


def ensure_cache(symbols, *, root: Path = FOREIGN_DIR, refresh: bool = False, fetch: HistoryFetch | None = None,
                 log: Callable[[str], None] | None = None) -> dict[str, dict]:
    """Download whatever is not cached (everything with `refresh`). One request per missing member."""
    metas = {}
    for s in symbols:
        pq, js = _paths(s, root)
        if refresh or not (pq.is_file() and js.is_file()):
            metas[s] = download_member(s, root=root, fetch=fetch)
            if log:
                log(f"{s}: {metas[s]['rows']} rows {metas[s]['first_date']}..{metas[s]['last_date']} ({metas[s]['tz']}), "
                    f"{len(metas[s]['splits'])} split(s)")
        else:
            metas[s] = json.loads(js.read_text(encoding="utf-8"))
    return metas


# ======================================================================================= stooq files
def stooq_path(symbol: str, root: Path = FOREIGN_DIR) -> Path:
    return root / "stooq" / f"{symbol}.csv"


def load_stooq(symbol: str, root: Path = FOREIGN_DIR) -> pd.DataFrame | None:
    """Stooq's daily CSV for a member as `date, open, close` rows, or None when no file was placed by hand.
    Rows dated on/after 2026-01-01 are dropped (the holdout is never used) and a file with no usable rows is None."""
    path = stooq_path(symbol, root)
    if not path.is_file():
        return None
    raw = pd.read_csv(path)
    cols = {c.strip().lower(): c for c in raw.columns}
    if not {"date", "open", "close"} <= set(cols):
        return None
    out = pd.DataFrame({"date": pd.to_datetime(raw[cols["date"]], errors="coerce"),
                        "open": pd.to_numeric(raw[cols["open"]], errors="coerce"),
                        "close": pd.to_numeric(raw[cols["close"]], errors="coerce")}).dropna(subset=["date"])
    out = out[out["date"] < pd.Timestamp(HOLDOUT_START)].sort_values("date").reset_index(drop=True)
    return out if len(out) else None


@dataclass(frozen=True)
class MemberRows:
    """One member's daily rows as plain tuples, indexed by local session date (the study's working form)."""
    symbol: str
    tz: str
    dates: tuple[date, ...]
    open: tuple[float, ...]
    close: tuple[float, ...]
    splits: tuple[tuple[date, float], ...]

    @classmethod
    def from_frame(cls, symbol: str, df: pd.DataFrame, meta: dict | None = None) -> "MemberRows":
        d = [x.date() for x in df["date"]]
        sp = tuple((date.fromisoformat(e["date"]), float(e["ratio"])) for e in (meta or {}).get("splits", []))
        return cls(symbol, (meta or {}).get("tz", ""), tuple(d), tuple(float(x) for x in df["open"]),
                   tuple(float(x) for x in df["close"]), sp)

    def index(self) -> dict[date, int]:
        return {d: i for i, d in enumerate(self.dates)}


def project_root_relative(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)
