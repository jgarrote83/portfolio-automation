"""Reference data for the safe-wheel model study: FRED volatility indices and T-bill rate, Yahoo daily prices and
dividends for SPY/QQQ, and Cboe's PUT index. Read-only; never reads 2026.

    PYTHONPATH=src python -m backtest.safe_wheel_run download [--refresh]

Sources (docs/specs/Safe_Wheel_Model_Preregistration.md, section 4):
  * FRED CSV `fredgraph.csv?id=<SERIES>&cosd=2004-01-01&coed=2025-12-31` for VIXCLS, VXNCLS and DTB3, so nothing dated
    after 2025-12-31 is ever requested;
  * `yfinance` for SPY/QQQ, `auto_adjust=False, actions=True`, `end` exclusive at 2026-01-01 (reusing the lead-lag
    study's `foreign.yahoo_history` / `normalise_history`);
  * Cboe's public `PUT_History.csv`, a whole-history file with no date parameter: its rows dated on/after
    2026-01-01 are dropped by the parser at once and are never stored, printed or used.
Cache: `data/reference/wheel/` (gitignored), one parquet + one JSON metadata file per series. A study run reads only
this cache. Any cached row dated on/after 2026-01-01 is a failure (`HoldoutError`), never used.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
import uuid
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .data.bars import HoldoutError
from .data.config import REPO_ROOT
from .foreign import normalise_history, yahoo_history

WHEEL_DIR = REPO_ROOT / "data" / "reference" / "wheel"
DATA_FROM = date(2004, 1, 1)
LAST_DAY = date(2025, 12, 31)
HOLDOUT_START = date(2026, 1, 1)
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd=2004-01-01&coed=2025-12-31"
PUT_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/PUT_History.csv"
FRED_SERIES = ("VIXCLS", "VXNCLS", "DTB3")
UNDERLYINGS = ("SPY", "QQQ")
MAX_STALE_DAYS = 5
YAHOO_END = "2026-01-01"                       # exclusive: the last day requested is 2025-12-31

TextFetch = Callable[[str], str]


def assert_not_holdout(*days: date) -> None:
    """Nothing on or after 2026-01-01 is ever requested, read or kept, from any source."""
    for d in days:
        if d >= HOLDOUT_START:
            raise HoldoutError(f"{d} is on/after {HOLDOUT_START}: the 2026 holdout is closed to the wheel study "
                               "(no override exists).")


def http_text(url: str, *, attempts: int = 4, sleep: Callable[[float], None] = time.sleep) -> str:
    """The real fetch (imported lazily so the tests and CI never need the network). A timeout, a dropped connection or a
    5xx/429 answer is retried with a growing pause; any other failure is raised at once."""
    import requests
    last: Exception | None = None
    for i in range(attempts):
        try:
            r = requests.get(url, timeout=60)           # the library default User-Agent (FRED drops connections that send a custom one)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
            r.raise_for_status()
            return r.text
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if isinstance(exc, requests.HTTPError) and status not in (429, 500, 502, 503, 504):
                raise
            last = exc
            if i + 1 < attempts:
                sleep(2.0 ** (i + 1))
    raise ValueError(f"{url}: no answer after {attempts} attempts ({last})")


# ============================================================================================== parsers
def parse_fred_csv(text: str, series: str) -> pd.DataFrame:
    """FRED CSV -> `date, value` rows (value NaN where FRED has a blank). A row dated on/after 2026-01-01 is a failure."""
    raw = pd.read_csv(io.StringIO(text))
    if raw.shape[1] < 2:
        raise ValueError(f"{series}: the FRED response has no value column")
    out = pd.DataFrame({"date": pd.to_datetime(raw.iloc[:, 0], errors="coerce"),
                        "value": pd.to_numeric(raw.iloc[:, 1], errors="coerce")}).dropna(subset=["date"])
    out = out.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
    if len(out) and out["date"].max().date() >= HOLDOUT_START:
        raise HoldoutError(f"{series}: FRED returned a row dated {out['date'].max().date()}; refusing to keep 2026 data.")
    return out


def parse_put_csv(text: str) -> tuple[pd.DataFrame, bool]:
    """Cboe `DATE,PUT` CSV (MM/DD/YYYY) -> (`date, value` rows dated before 2026-01-01, whether any row was dropped).
    The dropped rows are not inspected beyond their date."""
    raw = pd.read_csv(io.StringIO(text))
    cols = {c.strip().upper(): c for c in raw.columns}
    if not {"DATE", "PUT"} <= set(cols):
        raise ValueError(f"the Cboe file has columns {list(raw.columns)}, expected DATE and PUT")
    d = pd.to_datetime(raw[cols["DATE"]], format="%m/%d/%Y", errors="coerce")
    keep = d.notna() & (d < pd.Timestamp(HOLDOUT_START))
    dropped = bool((d.notna() & ~keep).any())
    out = pd.DataFrame({"date": d[keep], "value": pd.to_numeric(raw[cols["PUT"]][keep], errors="coerce")})
    out = out.dropna(subset=["value"]).drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
    return out, dropped


# ============================================================================================ cache I/O
def _paths(kind: str, name: str, root: Path) -> tuple[Path, Path]:
    return root / kind / f"{name}.parquet", root / kind / f"{name}.json"


def _write(df: pd.DataFrame, meta: dict, kind: str, name: str, root: Path) -> dict:
    pq, js = _paths(kind, name, root)
    pq.parent.mkdir(parents=True, exist_ok=True)
    tmp = pq.with_name(f"{pq.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        df.to_parquet(tmp, engine="pyarrow", index=False)
        os.replace(tmp, pq)
    finally:
        if tmp.exists():
            tmp.unlink()
    meta = {**meta, "rows": len(df),
            "first_date": df["date"].min().date().isoformat() if len(df) else None,
            "last_date": df["date"].max().date().isoformat() if len(df) else None,
            "content_sha256": hashlib.sha256(df.to_csv(index=False).encode("utf-8")).hexdigest()}
    js.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def _read(kind: str, name: str, root: Path) -> tuple[pd.DataFrame, dict]:
    pq, js = _paths(kind, name, root)
    if not pq.is_file() or not js.is_file():
        raise FileNotFoundError(f"{name} is not cached under {pq.parent} (run `python -m backtest.safe_wheel_run download`).")
    df = pd.read_parquet(pq)
    df["date"] = pd.to_datetime(df["date"])
    if len(df) and df["date"].max().date() >= HOLDOUT_START:
        raise HoldoutError(f"{name}: the cache holds a 2026 row; refusing to read it.")
    return df, json.loads(js.read_text(encoding="utf-8"))


def _now(now: Callable[[], datetime] | None) -> str:
    return (now() if now else datetime.now(timezone.utc)).isoformat()


def download_fred(series: str, *, root: Path = WHEEL_DIR, fetch: TextFetch | None = None,
                  now: Callable[[], datetime] | None = None) -> dict:
    df = parse_fred_csv((fetch or http_text)(FRED_URL.format(series=series)), series)
    if not len(df) or df["value"].notna().sum() == 0:
        raise ValueError(f"FRED returned no usable rows for {series}; nothing was cached.")
    return _write(df, {"series": series, "source": FRED_URL.format(series=series), "downloaded_utc": _now(now)},
                  "fred", series, root)


def download_underlying(symbol: str, *, root: Path = WHEEL_DIR, fetch=None,
                        now: Callable[[], datetime] | None = None) -> dict:
    """Daily rows (date, open, close, dividends, split) from Yahoo, `end` exclusive at 2026-01-01."""
    raw = (fetch or yahoo_history)(symbol, DATA_FROM.isoformat(), YAHOO_END)
    df, tz = normalise_history(raw)
    if not len(df):
        raise ValueError(f"Yahoo returned no rows for {symbol}; nothing was cached.")
    if df["date"].max().date() >= HOLDOUT_START:
        raise HoldoutError(f"{symbol}: Yahoo returned a row dated {df['date'].max().date()}; refusing to keep 2026 data.")
    try:
        import yfinance
        version = yfinance.__version__
    except Exception:                                        # the fetch was injected; the package is not needed
        version = "unavailable"
    splits = [{"date": d.date().isoformat(), "ratio": float(r)} for d, r in zip(df["date"], df["split"]) if r > 0]
    return _write(df, {"symbol": symbol, "tz": tz, "splits": splits, "yfinance": version, "downloaded_utc": _now(now),
                       "request": {"start": DATA_FROM.isoformat(), "end_exclusive": YAHOO_END},
                       "adjustment": "auto_adjust=False (close is not dividend-adjusted; dividends are the ex-date cash amounts)"},
                  "yahoo", symbol, root)


def download_put(*, root: Path = WHEEL_DIR, fetch: TextFetch | None = None,
                 now: Callable[[], datetime] | None = None) -> dict:
    df, dropped = parse_put_csv((fetch or http_text)(PUT_URL))
    if not len(df):
        raise ValueError("the Cboe file had no usable rows dated before 2026; nothing was cached.")
    return _write(df, {"series": "PUT", "source": PUT_URL, "downloaded_utc": _now(now), "rows_dated_2026_dropped_at_parse": dropped},
                  "cboe", "PUT", root)


def ensure_cache(*, root: Path = WHEEL_DIR, refresh: bool = False, fetch_text: TextFetch | None = None, fetch_yahoo=None,
                 log: Callable[[str], None] | None = None) -> dict[str, dict]:
    """Download whatever is not cached (everything with `refresh`): 3 FRED series, 2 underlyings, the PUT index."""
    metas = {}
    plan = ([("fred", s, lambda s=s: download_fred(s, root=root, fetch=fetch_text)) for s in FRED_SERIES]
            + [("yahoo", s, lambda s=s: download_underlying(s, root=root, fetch=fetch_yahoo)) for s in UNDERLYINGS]
            + [("cboe", "PUT", lambda: download_put(root=root, fetch=fetch_text))])
    for kind, name, go in plan:
        pq, js = _paths(kind, name, root)
        if refresh or not (pq.is_file() and js.is_file()):
            metas[name] = go()
            if log:
                log(f"{name}: {metas[name]['rows']} rows {metas[name]['first_date']}..{metas[name]['last_date']}")
        else:
            metas[name] = json.loads(js.read_text(encoding="utf-8"))
    return metas


# ================================================================================================ loaders
def load_series(kind: str, name: str, root: Path = WHEEL_DIR) -> tuple[dict[date, float], dict]:
    """{date: value} (blanks omitted) and the metadata, for a FRED series (`fred`) or the PUT index (`cboe`)."""
    df, meta = _read(kind, name, root)
    return {d.date(): float(v) for d, v in zip(df["date"], df["value"]) if pd.notna(v)}, meta


def load_underlying(symbol: str, root: Path = WHEEL_DIR) -> tuple[pd.DataFrame, dict]:
    return _read("yahoo", symbol, root)


# ============================================================================================ alignment
def carry_forward(obs: dict[date, float], days: list[date], *, name: str,
                  max_stale_days: int = MAX_STALE_DAYS) -> tuple[dict[date, float], list[dict]]:
    """The value on each trading day = the latest observation dated on or before it (pre-registration item 13).
    -> ({day: value}, [{day, observed_on, stale_days}] for every day that is not an exact observation).
    More than `max_stale_days` calendar days stale, or no earlier observation at all, is an error (no verdict)."""
    keys = sorted(obs)
    out, stale = {}, []
    for d in days:
        i = bisect_right(keys, d)
        if i == 0:
            raise ValueError(f"{name}: no observation on or before {d}")
        k = keys[i - 1]
        age = (d - k).days
        if age > max_stale_days:
            raise ValueError(f"{name}: the latest observation for {d} is {k}, {age} calendar days stale (limit {max_stale_days})")
        out[d] = obs[k]
        if age:
            stale.append({"day": d.isoformat(), "observed_on": k.isoformat(), "stale_days": age})
    return out, stale


def trailing_dividend_yield(dividends: list[tuple[date, float]], close: dict[date, float], days: list[date],
                            *, first_valid: date) -> dict[date, float]:
    """q on each trading day = (cash dividends with ex-date in (day - 365 days, day]) / that day's close.
    Only days on or after `first_valid` (whose 365-day look-back lies inside the data) get a value."""
    divs = sorted(dividends)
    out = {}
    for d in days:
        if d < first_valid:
            continue
        lo = d - timedelta(days=365)
        out[d] = sum(a for x, a in divs if lo < x <= d) / close[d]
    return out


@dataclass(frozen=True)
class WheelInputs:
    """One underlying's aligned daily inputs: trading days, closes, volatility index, T-bill rate (percent) and q."""
    symbol: str
    vol_name: str
    days: tuple[date, ...]
    close: dict[date, float]
    vol: dict[date, float]
    rate_pct: dict[date, float]
    div_yield: dict[date, float]
    stale: dict[str, list[dict]]
    dividends: tuple[tuple[date, float], ...]
    splits: tuple[dict, ...]


def build_inputs(symbol: str, vol_name: str, underlying: pd.DataFrame, meta: dict, vol: dict[date, float],
                 rate: dict[date, float]) -> WheelInputs:
    """Align the cached series on the underlying's trading days and validate them (pre-registration items 1, 5, 13)."""
    df = underlying.sort_values("date")
    days = [d.date() for d in df["date"]]
    if not days:
        raise ValueError(f"{symbol}: no daily rows")
    if len(set(days)) != len(days):
        raise ValueError(f"{symbol}: duplicate dates")
    assert_not_holdout(days[-1])
    if meta.get("splits") or bool((df["split"] > 0).any()):
        raise ValueError(f"{symbol}: the data contains a stock split; unadjusted closes are not usable across it")
    close = {d: float(c) for d, c in zip(days, df["close"])}
    bad = [d for d, c in close.items() if not c > 0]
    if bad:
        raise ValueError(f"{symbol}: non-positive or missing close on {bad[:3]}")
    for a, b in zip(days, days[1:]):
        if (b - a).days > 6:
            raise ValueError(f"{symbol}: a gap of {(b - a).days} calendar days between {a} and {b}")
    vol_al, vol_stale = carry_forward(vol, days, name=vol_name)
    rate_al, rate_stale = carry_forward(rate, days, name="DTB3")
    if any(not 0 < v < 200 for v in vol_al.values()):
        raise ValueError(f"{vol_name}: a value outside (0, 200)")
    if any(not -2 < v < 30 for v in rate_al.values()):
        raise ValueError("DTB3: a value outside (-2, 30) percent")
    divs = [(d.date(), float(a)) for d, a in zip(df["date"], df["dividends"]) if a > 0]
    q = trailing_dividend_yield(divs, close, days, first_valid=days[0] + timedelta(days=366))
    return WheelInputs(symbol, vol_name, tuple(days), close, vol_al, rate_al, q,
                       {"vol": vol_stale, "rate": rate_stale}, tuple(divs), tuple(meta.get("splits", [])))


def load_inputs(symbol: str, vol_name: str, root: Path = WHEEL_DIR) -> WheelInputs:
    df, meta = load_underlying(symbol, root)
    vol, _ = load_series("fred", vol_name, root)
    rate, _ = load_series("fred", "DTB3", root)
    return build_inputs(symbol, vol_name, df, meta, vol, rate)


# ========================================================================================= source checks
def two_source_check(a: dict[date, float], b: dict[date, float], start: date, end: date, *, tol: float = 0.0005) -> dict:
    """Compare two daily-close sources over [start, end] (pre-registration item 16): agreement means
    |a - b| / b <= tol. Lists every disagreement and every date present in one source only."""
    rng_a = {d for d in a if start <= d <= end}
    rng_b = {d for d in b if start <= d <= end}
    common = sorted(rng_a & rng_b)
    rows = []
    for d in common:
        diff = abs(a[d] - b[d]) / b[d]
        rows.append((d, a[d], b[d], diff))
    bad = [{"day": d.isoformat(), "a": x, "b": y, "diff_pct": diff * 100} for d, x, y, diff in rows if diff > tol]
    return {"start": start.isoformat(), "end": end.isoformat(), "tolerance_pct": tol * 100, "compared": len(common),
            "agree": len(common) - len(bad), "max_diff_pct": max((r[3] for r in rows), default=0.0) * 100,
            "disagreements": bad, "only_in_a": sorted(d.isoformat() for d in rng_a - rng_b),
            "only_in_b": sorted(d.isoformat() for d in rng_b - rng_a)}
