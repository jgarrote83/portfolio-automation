"""`get_bars` -- the ONE door through which a backtest gets market data (repo rule 6).

    get_bars(symbols, start, end, timeframe="1Min", feed="sip") -> DataFrame
    columns: symbol, ts (US/Eastern), open, high, low, close, volume

Serves bars from the local parquet cache and calls Alpaca only for what is missing; there is no
upfront download. See `docs/specs/ORB_Engine_v1.0.md`, "Phase 1: Data layer".

Guards
  * holdout (rule 6): any requested date on or after 2026-01-01 raises `HoldoutError` unless
    `allow_holdout=True` (nothing in Phase 1 passes it);
  * freshness: today, and anything within 15 minutes of now, is never cached or recorded in the
    manifest (it is fetched into memory only, end-clamped to now-15min);
  * offline: `offline=True` raises `CacheMissError` on any miss instead of calling the API.

Coverage is tracked per (feed, timeframe, adjustment, window, symbol, day) in a SQLite manifest,
including days that returned no bars. Trading days come from SPY daily bars (no trading-API
calendar is used), so holidays are never requested for every symbol.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from . import config as C
from .client import AlpacaDataClient
from .manifest import Manifest, month_key, window_key
from .store import BarStore, empty_frame

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class HoldoutError(RuntimeError):
    """The request touches the 2026 holdout, which needs Jorge's explicit approval (rule 6)."""


class CacheMissError(RuntimeError):
    """offline=True and the requested bars are not (fully) in the cache."""


# --------------------------------------------------------------------------- pure helpers
def _parse_bound(value) -> tuple[date, pd.Timestamp | None]:
    """-> (ET calendar day, exact ET timestamp or None). Date-only inputs are whole days."""
    if isinstance(value, str) and _DATE_ONLY.match(value.strip()):
        return date.fromisoformat(value.strip()), None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value, None
    ts = pd.Timestamp(value)
    ts = ts.tz_localize(C.ET) if ts.tzinfo is None else ts.tz_convert(C.ET)
    return ts.date(), ts


def _utc_str(ts: pd.Timestamp) -> str:
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _et(day: date, hhmm: str, second: int = 0) -> pd.Timestamp:
    hh, mm = (int(x) for x in hhmm.split(":"))
    return pd.Timestamp(year=day.year, month=day.month, day=day.day, hour=hh, minute=mm,
                        second=second, tz=C.ET)


def window_bounds(day: date, window: tuple[str, str]) -> tuple[str, str]:
    """RFC3339 (start, end) for one day's window. `end` is one second BEFORE the window's end,
    because Alpaca's `end` is inclusive and bars are stamped at their START: ('09:30','09:35')
    must return the 09:30 five-minute bar but not the 09:35 one."""
    return _utc_str(_et(day, window[0])), _utc_str(_et(day, window[1], 0) - pd.Timedelta(seconds=1))


def day_bounds(lo: date, hi: date) -> tuple[str, str]:
    return _utc_str(_et(lo, "00:00")), _utc_str(_et(hi, "23:59", 59))


def _minutes(hhmm: str) -> int:
    hh, mm = hhmm.split(":")
    return int(hh) * 60 + int(mm)


def _chunks(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _normalise_symbols(symbols) -> list[str]:
    if isinstance(symbols, str):
        symbols = [symbols]
    return list(dict.fromkeys(str(s).strip().upper() for s in symbols if str(s).strip()))


def _rows_to_frame(rows: list[tuple]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["symbol", "ts", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["symbol", "ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.as_unit("ns")
    return df


# --------------------------------------------------------------------------- write batching
class _Batch:
    """Accumulates fetched bars and coverage marks; writes parquet FIRST, manifest SECOND, so a
    crash can only cause a refetch, never a recorded-but-missing day."""

    def __init__(self, layer: "DataLayer", feed: str, timeframe: str, win: str) -> None:
        self.layer, self.feed, self.timeframe, self.win = layer, feed, timeframe, win
        self.rows: list[tuple] = []
        self.marks: list[tuple[str, date]] = []
        self.bar_days: set[tuple[str, date]] = set()

    def add(self, sym: str, bar: dict) -> None:
        self.rows.append((sym, bar["t"], bar["o"], bar["h"], bar["l"], bar["c"], bar["v"]))
        if len(self.rows) >= C.FLUSH_ROWS:
            self.flush_rows()

    def mark(self, pairs: Iterable[tuple[str, date]]) -> None:
        self.marks.extend(pairs)

    def flush_rows(self) -> None:
        if not self.rows:
            return
        df = _rows_to_frame(self.rows)
        self.rows = []
        et = df["ts"].dt.tz_convert(C.ET_NAME)
        df["_day"] = et.dt.date
        df["_month"] = et.dt.strftime("%Y-%m")
        self.bar_days.update(zip(df["symbol"], df["_day"]))
        files = []
        for (sym, month), g in df.groupby(["symbol", "_month"], sort=False):
            n, nbytes = self.layer.store.write(self.feed, self.timeframe, sym, month,
                                               g[["ts", "open", "high", "low", "close", "volume"]])
            files.append((self.feed, self.timeframe, sym, month, n, nbytes))
        self.layer.manifest.record_files(files)

    def commit(self) -> None:
        self.flush_rows()
        if self.marks:
            self.layer.manifest.record(
                self.feed, self.timeframe, C.ADJUSTMENT, self.win,
                ((s, d, (s, d) in self.bar_days) for s, d in self.marks))
        self.marks, self.bar_days = [], set()


# --------------------------------------------------------------------------------- layer
class DataLayer:
    def __init__(self, cache_dir: str | Path | None = None, *,
                 manifest_path: str | Path | None = None,
                 client: AlpacaDataClient | None = None,
                 now=None) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else C.DEFAULT_CACHE_DIR
        self.store = BarStore(self.cache_dir)
        self.manifest = Manifest(manifest_path or self.cache_dir / "manifest.sqlite")
        self._client = client
        self._now = now or (lambda: datetime.now(timezone.utc))

    # ----------------------------------------------------------------- plumbing
    @property
    def client(self) -> AlpacaDataClient:
        if self._client is None:
            self._client = AlpacaDataClient.from_env()   # raises MissingCredentialsError, key-free
        return self._client

    def _today_et(self) -> date:
        return pd.Timestamp(self._now()).tz_convert(C.ET).date()

    @staticmethod
    def _guard_holdout(start_day: date, end_day: date, allow: bool) -> None:
        if not allow and (end_day >= C.HOLDOUT_START or start_day >= C.HOLDOUT_START):
            raise HoldoutError(
                f"{start_day}..{end_day} reaches the holdout (on/after {C.HOLDOUT_START}). Repo "
                "rule 6: the January-September 2026 holdout is run once, after a strategy is "
                "chosen, and only with Jorge's explicit approval (allow_holdout=True).")

    # ------------------------------------------------------------------- fetching
    def _fetch_missing(self, timeframe: str, feed: str, window, win: str,
                       missing: dict[str, list[date]], all_days: list[date]) -> None:
        batch = _Batch(self, feed, timeframe, win)
        client = self.client
        if window is not None:
            by_day: dict[date, list[str]] = defaultdict(list)
            for sym, days in missing.items():
                for d in days:
                    by_day[d].append(sym)
            for d in sorted(by_day):
                start, end = window_bounds(d, window)
                for chunk in _chunks(sorted(by_day[d]), C.SYMBOLS_PER_REQUEST):
                    for sym, bar in client.iter_bars(chunk, timeframe, start, end, feed):
                        batch.add(sym, bar)
                    batch.mark((s, d) for s in chunk)
                if len(batch.marks) >= C.FLUSH_ROWS:
                    batch.commit()
        else:
            spans = {s: (days[0], days[-1]) for s, days in missing.items()}
            order = sorted(spans, key=lambda s: (spans[s][0], spans[s][1], s))
            per = C.SYMBOLS_PER_REQUEST if timeframe == "1Day" else C.SYMBOLS_PER_REQUEST_INTRADAY
            for chunk in _chunks(order, per):
                lo = min(spans[s][0] for s in chunk)
                hi = max(spans[s][1] for s in chunk)
                start, end = day_bounds(lo, hi)
                for sym, bar in client.iter_bars(chunk, timeframe, start, end, feed):
                    batch.add(sym, bar)
                span_days = [d for d in all_days if lo <= d <= hi]
                batch.mark((s, d) for s in chunk for d in span_days)
                batch.commit()
        batch.commit()

    def _fetch_uncached(self, symbols: list[str], timeframe: str, feed: str, window,
                        days: list[date]) -> pd.DataFrame:
        """Bars for today / the last 15 minutes: fetched into memory ONLY (never stored, never
        recorded). `end` is clamped to now-15min, which the free plan can serve."""
        cutoff = pd.Timestamp(self._now()).tz_convert("UTC") - pd.Timedelta(C.RECENT_GUARD)
        rows: list[tuple] = []
        per = C.SYMBOLS_PER_REQUEST if (timeframe == "1Day" or window) else C.SYMBOLS_PER_REQUEST_INTRADAY
        for d in days:
            start, end = window_bounds(d, window) if window else day_bounds(d, d)
            if pd.Timestamp(start) >= cutoff:
                continue
            if pd.Timestamp(end) > cutoff:
                end = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
            for chunk in _chunks(symbols, per):
                for sym, bar in self.client.iter_bars(chunk, timeframe, start, end, feed):
                    rows.append((sym, bar["t"], bar["o"], bar["h"], bar["l"], bar["c"], bar["v"]))
        return _rows_to_frame(rows)

    # --------------------------------------------------------------------- reading
    def _cached(self, symbols: list[str], timeframe: str, feed: str, window, win: str,
                days: list[date], offline: bool) -> pd.DataFrame:
        """Ensure `days` (all cacheable) are covered for `symbols`, then read them back."""
        if not days or not symbols:
            return empty_frame()
        missing = self.manifest.missing(feed, timeframe, C.ADJUSTMENT, win, symbols, days)
        if missing:
            if offline:
                n = sum(len(v) for v in missing.values())
                ex = next(iter(missing.items()))
                raise CacheMissError(
                    f"offline=True but {n} (symbol, day) pair(s) are not cached for "
                    f"{feed}/{timeframe}/{win} (e.g. {ex[0]} on {ex[1][0]}).")
            self._fetch_missing(timeframe, feed, window, win, missing, days)
        months = sorted({month_key(d) for d in days})
        df = self.store.read(feed, timeframe, symbols, months)
        if df.empty:
            return df
        dset = set(days)
        return df[df["ts"].dt.date.isin(dset)]

    # ------------------------------------------------------------------ public API
    def trading_days(self, start, end, *, offline: bool = False, allow_holdout: bool = False) -> list[date]:
        """Trading days in [start, end], derived from SPY daily bars (days SPY has a bar), plus
        weekdays from today on (not yet knowable)."""
        s_day, _ = _parse_bound(start)
        e_day, _ = _parse_bound(end)
        self._guard_holdout(s_day, e_day, allow_holdout)
        today = self._today_et()
        weekdays = [d.date() for d in pd.bdate_range(s_day, e_day)]
        past = [d for d in weekdays if d < today]
        out: set[date] = set()
        if past:
            spy = self._cached([C.CALENDAR_SYMBOL], "1Day", "sip", None, "full", past, offline)
            out.update(spy["ts"].dt.date.tolist() if not spy.empty else [])
        out.update(d for d in weekdays if d >= today)
        return sorted(out)

    def get_bars(self, symbols, start, end, timeframe: str = "1Min", feed: str = "sip", *,
                 window: tuple[str, str] | None = None, offline: bool = False,
                 allow_holdout: bool = False) -> pd.DataFrame:
        if timeframe not in C.TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {C.TIMEFRAMES}")
        if feed not in C.FEEDS:
            raise ValueError(f"feed must be one of {C.FEEDS}")
        if window is not None:
            if timeframe == "1Day":
                raise ValueError("window applies to intraday timeframes only")
            if _minutes(window[0]) >= _minutes(window[1]):
                raise ValueError("window must be (start, end) with start < end, e.g. ('09:30','09:35')")
        syms = _normalise_symbols(symbols)
        s_day, s_exact = _parse_bound(start)
        e_day, e_exact = _parse_bound(end)
        if e_day < s_day:
            raise ValueError("end is before start")
        self._guard_holdout(s_day, e_day, allow_holdout)      # BEFORE any cache or network work
        if not syms:
            return empty_frame()

        days = self.trading_days(s_day, e_day, offline=offline, allow_holdout=allow_holdout)
        today = self._today_et()
        cacheable = [d for d in days if d < today]
        fresh = [d for d in days if d >= today]
        win = window_key(window)

        frames = [self._cached(syms, timeframe, feed, window, win, cacheable, offline)]
        if fresh:
            if offline:
                raise CacheMissError(f"offline=True but {fresh[0]} is today (never cached).")
            frames.append(self._fetch_uncached(syms, timeframe, feed, window, fresh))
        frames = [f for f in frames if not f.empty]
        if not frames:
            return empty_frame()
        df = pd.concat([_to_et(f) for f in frames], ignore_index=True)

        # clip to the requested range / window, order, and return the public schema
        et_day = df["ts"].dt.date
        df = df[(et_day >= s_day) & (et_day <= e_day)]
        if s_exact is not None:
            df = df[df["ts"] >= s_exact]
        if e_exact is not None:
            df = df[df["ts"] <= e_exact]
        if window is not None:
            mins = df["ts"].dt.hour * 60 + df["ts"].dt.minute
            df = df[(mins >= _minutes(window[0])) & (mins < _minutes(window[1]))]
        order = {s: i for i, s in enumerate(syms)}
        df = (df.assign(_o=df["symbol"].map(order)).sort_values(["_o", "ts"])
              .drop(columns="_o").reset_index(drop=True))
        return df[C.COLUMNS].astype({"volume": "int64"})

    # ---------------------------------------------------------------------- assets
    def get_assets(self, *, refresh: bool = False, offline: bool = False) -> pd.DataFrame:
        """Active AND inactive US equities (inactive names reduce survivorship gaps). Cached in
        `{cache}/assets/us_equity.parquet`; refetched only with `refresh=True`."""
        path = self.cache_dir / "assets" / "us_equity.parquet"
        if path.is_file() and not refresh:
            return pd.read_parquet(path)
        if offline:
            raise CacheMissError("offline=True but the assets list is not cached.")
        rows = []
        for status in ("active", "inactive"):
            for a in self.client.list_assets(status):
                rows.append({"symbol": str(a.get("symbol", "")).upper(), "name": a.get("name", ""),
                             "exchange": a.get("exchange", ""), "status": a.get("status", status),
                             "tradable": bool(a.get("tradable", False)),
                             "shortable": bool(a.get("shortable", False)),
                             "easy_to_borrow": bool(a.get("easy_to_borrow", False))})
        df = (pd.DataFrame(rows, columns=["symbol", "name", "exchange", "status", "tradable",
                                          "shortable", "easy_to_borrow"])
              .drop_duplicates(subset=["symbol", "status"]).reset_index(drop=True))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp-{id(df)}")
        try:
            df.to_parquet(tmp, engine="pyarrow", index=False)
            tmp.replace(path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return df

    def cache_stats(self) -> dict:
        s = self.manifest.stats()
        s["disk_bytes"] = self.store.disk_bytes()
        s["requests_made"] = getattr(self._client, "requests_made", 0) if self._client else 0
        s["invalid_symbols_dropped"] = len(getattr(self._client, "invalid_symbols", ()) or ()) if self._client else 0
        return s


def _to_et(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ts"] = pd.to_datetime(out["ts"], utc=True).dt.tz_convert(C.ET_NAME)
    return out


# ------------------------------------------------------------ module-level default layer
_default: DataLayer | None = None


def configure(layer: DataLayer | None) -> None:
    """Install (or clear) the process-wide default layer used by `get_bars`."""
    global _default
    _default = layer


def default_layer() -> DataLayer:
    global _default
    if _default is None:
        _default = DataLayer()
    return _default


def get_bars(symbols, start, end, timeframe: str = "1Min", feed: str = "sip", *,
             window: tuple[str, str] | None = None, offline: bool = False,
             allow_holdout: bool = False) -> pd.DataFrame:
    """See the module docstring. Thin wrapper over the process-wide `DataLayer`."""
    return default_layer().get_bars(symbols, start, end, timeframe, feed, window=window,
                                    offline=offline, allow_holdout=allow_holdout)
