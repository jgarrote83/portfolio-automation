"""Parquet bar cache: `{root}/{feed}/{timeframe}/{symbol}/{YYYY-MM}.parquet`, one file per symbol-month.

Writes are atomic (temp file in the same directory, then `os.replace`): an interrupted write can
never leave a half-written `.parquet` where a reader would find it, and a pre-existing complete
file survives a failed overwrite. Values are stored as returned by the API (raw, unadjusted);
timestamps are stored in UTC and converted to US/Eastern on read.
"""
from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from .config import COLUMNS, DEFAULT_CACHE_DIR, ET_NAME

# Windows cannot create a directory named after a DOS device, and some tickers collide with them.
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}
_STORED = ["ts", "open", "high", "low", "close", "volume"]


def symbol_dir(symbol: str) -> str:
    """Filesystem-safe, case-normalised directory name for a ticker."""
    s = re.sub(r"[^A-Z0-9._-]", "_", symbol.upper()).rstrip(".")
    return f"{s}_" if s in _RESERVED else s


def empty_frame() -> pd.DataFrame:
    df = pd.DataFrame({
        "symbol": pd.Series(dtype="object"),
        "ts": pd.Series(dtype=f"datetime64[ns, {ET_NAME}]"),
        "open": pd.Series(dtype="float64"), "high": pd.Series(dtype="float64"),
        "low": pd.Series(dtype="float64"), "close": pd.Series(dtype="float64"),
        "volume": pd.Series(dtype="int64"),
    })
    return df[COLUMNS]


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a frame to the stored schema (ts as tz-aware UTC ns; numeric columns typed)."""
    out = pd.DataFrame({
        "ts": pd.to_datetime(df["ts"], utc=True).dt.as_unit("ns"),
        "open": df["open"].astype("float64"), "high": df["high"].astype("float64"),
        "low": df["low"].astype("float64"), "close": df["close"].astype("float64"),
        "volume": df["volume"].astype("int64"),
    })
    return out[_STORED]


class BarStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else DEFAULT_CACHE_DIR

    def path(self, feed: str, timeframe: str, symbol: str, month: str) -> Path:
        return self.root / feed / timeframe / symbol_dir(symbol) / f"{month}.parquet"

    # ----------------------------------------------------------------- write
    def write(self, feed: str, timeframe: str, symbol: str, month: str,
              new: pd.DataFrame) -> tuple[int, int]:
        """Merge `new` rows into the symbol-month file (dedupe on ts, keep latest). Atomic.
        Returns (nrows, nbytes) of the file now on disk."""
        path = self.path(feed, timeframe, symbol, month)
        path.parent.mkdir(parents=True, exist_ok=True)
        merged = normalise(new)
        if path.is_file():
            merged = pd.concat([pd.read_parquet(path), merged], ignore_index=True)
        merged = (merged.drop_duplicates(subset="ts", keep="last")
                  .sort_values("ts").reset_index(drop=True))
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
        try:
            merged.to_parquet(tmp, engine="pyarrow", index=False)
            os.replace(tmp, path)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        return len(merged), path.stat().st_size

    # ------------------------------------------------------------------ read
    def read(self, feed: str, timeframe: str, symbols: Iterable[str],
             months: Iterable[str], *, workers: int = 8) -> pd.DataFrame:
        """All cached rows for symbols x months, in the public schema (ts in US/Eastern)."""
        jobs = [(s, m) for s in dict.fromkeys(symbols) for m in months
                if self.path(feed, timeframe, s, m).is_file()]
        if not jobs:
            return empty_frame()

        def _one(job):
            sym, month = job
            df = pd.read_parquet(self.path(feed, timeframe, sym, month))
            df.insert(0, "symbol", sym)
            return df
        if len(jobs) > 32 and workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                frames = list(ex.map(_one, jobs))
        else:
            frames = [_one(j) for j in jobs]
        out = pd.concat(frames, ignore_index=True)
        out["ts"] = pd.to_datetime(out["ts"], utc=True).dt.tz_convert(ET_NAME)
        return out[COLUMNS]

    def disk_bytes(self) -> int:
        total = 0
        if not self.root.is_dir():
            return 0
        for dirpath, _dirs, files in os.walk(self.root):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
        return total


# --------------------------------------------------------------------------------------- news
NEWS_COLUMNS = ["id", "created_at", "updated_at", "headline", "symbols", "source"]


def empty_news() -> pd.DataFrame:
    return pd.DataFrame({
        "id": pd.Series(dtype="int64"),
        "created_at": pd.Series(dtype="datetime64[ns, UTC]"),
        "updated_at": pd.Series(dtype="datetime64[ns, UTC]"),
        "headline": pd.Series(dtype="object"),
        "symbols": pd.Series(dtype="object"),
        "source": pd.Series(dtype="object"),
    })[NEWS_COLUMNS]


def normalise_news(rows: list[dict]) -> pd.DataFrame:
    """Alpaca article dicts -> the stored schema (only id, created_at, updated_at, headline, symbols, source)."""
    if not rows:
        return empty_news()
    df = pd.DataFrame({
        "id": [int(r["id"]) for r in rows],
        "created_at": pd.to_datetime([r["created_at"] for r in rows], utc=True).as_unit("ns"),
        "updated_at": pd.to_datetime([r.get("updated_at") or r["created_at"] for r in rows], utc=True).as_unit("ns"),
        "headline": [str(r.get("headline") or "") for r in rows],
        "symbols": [[str(s) for s in (r.get("symbols") or [])] for r in rows],
        "source": [str(r.get("source") or "") for r in rows],
    })
    return df[NEWS_COLUMNS]


class NewsStore:
    """`{root}/news/{YYYY-MM}.parquet`, one file per ET month of `created_at`; writes are atomic and
    merge-by-id (a re-fetched article replaces its earlier copy)."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else DEFAULT_CACHE_DIR

    def path(self, month: str) -> Path:
        return self.root / "news" / f"{month}.parquet"

    def write(self, month: str, new: pd.DataFrame) -> tuple[int, int]:
        path = self.path(month)
        path.parent.mkdir(parents=True, exist_ok=True)
        merged = new[NEWS_COLUMNS]
        if path.is_file():
            merged = pd.concat([pd.read_parquet(path), merged], ignore_index=True)
        merged = (merged.drop_duplicates(subset="id", keep="last")
                  .sort_values(["created_at", "id"]).reset_index(drop=True))
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
        try:
            merged.to_parquet(tmp, engine="pyarrow", index=False)
            os.replace(tmp, path)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        return len(merged), path.stat().st_size

    def read(self, months: Iterable[str]) -> pd.DataFrame:
        frames = [pd.read_parquet(self.path(m)) for m in dict.fromkeys(months) if self.path(m).is_file()]
        if not frames:
            return empty_news()
        out = pd.concat(frames, ignore_index=True)
        out["created_at"] = pd.to_datetime(out["created_at"], utc=True)
        out["updated_at"] = pd.to_datetime(out["updated_at"], utc=True)
        out["symbols"] = out["symbols"].map(list)                  # parquet hands back numpy arrays; consumers get lists
        return out[NEWS_COLUMNS]
