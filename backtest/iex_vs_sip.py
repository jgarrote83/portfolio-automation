"""IEX vs SIP: can the free real-time IEX feed rank "Stocks in Play" like the paid SIP feed?

For each trading day in the window (default 2024-01-02 .. 2025-12-31):

  1. UNIVERSE from SIP daily bars -- open > $5, 14-day average volume >= 1M shares, 14-day ATR
     > $0.50, Core roster excluded. The averages use the 14 trading days BEFORE the day (the
     day's own bar is never in its own average); only the day's OPEN is used from the day itself.
  2. RELATIVE VOLUME twice: 9:30-9:35 volume / the prior-14-day average of 9:30-9:35 volume,
     once from SIP bars and once from IEX bars (days with no bar count as zero volume; the
     average must be positive). Same-day volume is never in its own average.
  3. TOP 20 at RVOL >= 100% per feed -> OVERLAP |SIP ∩ IEX| / 20; DIRECTION AGREEMENT for the
     SIP top 20: does the first 5-minute bar close up/down under both feeds' prices?

Every bar comes through `backtest.data.get_bars` (repo rule 6). The report REPORTS numbers; it
never decides whether the $99/month SIP plan is worth it.

    PYTHONPATH=src python -m backtest.iex_vs_sip --start 2024-01-02 --end 2025-12-31 \
        --csv reports/orb/iex_vs_sip_2024_2025.csv --summary .review/phase1-iex-vs-sip.md

Definitions are deliberate simplifications recorded here so the numbers are reproducible:
ATR = simple mean of the true range over the prior 14 days (not Wilder-smoothed); "up/down" =
bar close above/below its open (equal = doji, excluded from the agreement denominator).
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .data import DataLayer
from .data import config as C

LOOKBACK = 14
MIN_PRICE = 5.0
MIN_AVG_VOLUME = 1_000_000
MIN_ATR = 0.50
RVOL_MIN = 1.0              # "Relative Volume >= 100%"
TOP_N = 20
WINDOW = ("09:30", "09:35")
FETCH_FROM = "2023-12-01"   # so the first window day has a full 14-day lookback


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# =============================================================================== pure metrics
def panels_from_daily(daily: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Long daily bars -> wide panels (index = ET trading day, columns = symbol)."""
    d = daily.assign(day=daily["ts"].dt.date)
    out = {}
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = d.pivot_table(index="day", columns="symbol", values=col, aggfunc="last").sort_index()
    return out


def eligibility(panels: dict[str, pd.DataFrame], exclude: set[str] | frozenset[str] = frozenset(),
                *, lookback: int = LOOKBACK, min_price: float = MIN_PRICE,
                min_avg_volume: float = MIN_AVG_VOLUME, min_atr: float = MIN_ATR) -> pd.DataFrame:
    """Boolean (day x symbol): passes the universe filter on that day, using only data from
    BEFORE the day (plus the day's own open)."""
    close, high, low = panels["close"], panels["high"], panels["low"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()]).groupby(level=0).max()
    tr = tr.reindex(close.index)
    atr = tr.rolling(lookback, min_periods=lookback).mean().shift(1)
    avgvol = panels["volume"].rolling(lookback, min_periods=lookback).mean().shift(1)
    ok = (panels["open"] > min_price) & (avgvol >= min_avg_volume) & (atr > min_atr)
    drop = [c for c in ok.columns if c in exclude]
    if drop:
        ok = ok.copy()
        ok[drop] = False
    return ok.fillna(False).astype(bool)


def opening_panels(bars: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Long 5Min window bars (one 09:30 bar per symbol-day) -> wide open/close/volume panels."""
    if bars.empty:
        return {k: pd.DataFrame() for k in ("open", "close", "volume")}
    b = bars.assign(day=bars["ts"].dt.date)
    return {k: b.pivot_table(index="day", columns="symbol", values=k, aggfunc="first").sort_index()
            for k in ("open", "close", "volume")}


def relative_volume(open_volume: pd.DataFrame, all_days: list[date], symbols: list[str],
                    *, lookback: int = LOOKBACK) -> pd.DataFrame:
    """RVOL (day x symbol) = opening volume / mean of the PRIOR `lookback` days' opening volume.
    A missing bar is zero volume; a non-positive average gives NaN (excluded)."""
    ov = open_volume.reindex(index=all_days, columns=symbols).fillna(0.0)
    avg = ov.rolling(lookback, min_periods=lookback).mean().shift(1)
    return ov / avg.where(avg > 0)


def top_n(rvol_row: pd.Series, candidates: list[str], *, n: int = TOP_N, rvol_min: float = RVOL_MIN) -> list[str]:
    s = rvol_row.reindex(candidates).dropna()
    s = s[s >= rvol_min]
    order = sorted(s.items(), key=lambda kv: (-kv[1], kv[0]))
    return [sym for sym, _ in order[:n]]


def direction(o, c) -> str | None:
    if o is None or c is None or pd.isna(o) or pd.isna(c):
        return None
    return "up" if c > o else ("down" if c < o else "doji")


def needed_open_symbols(eligible: dict[date, list[str]], all_days: list[date],
                        *, lookback: int = LOOKBACK) -> dict[date, set[str]]:
    """Which symbols need their opening bar on which day: for every day D with candidates, the
    candidates of D need D and the `lookback` trading days before it."""
    pos = {d: i for i, d in enumerate(all_days)}
    need: dict[date, set[str]] = {}
    for D, syms in eligible.items():
        if D not in pos:
            continue
        for d in all_days[max(0, pos[D] - lookback): pos[D] + 1]:
            need.setdefault(d, set()).update(syms)
    return need


def compare_day(day: date, candidates: list[str], rvol: dict[str, pd.DataFrame],
                bars: dict[str, dict[str, pd.DataFrame]]) -> dict:
    sip_top = top_n(rvol["sip"].loc[day], candidates) if day in rvol["sip"].index else []
    iex_top = top_n(rvol["iex"].loc[day], candidates) if day in rvol["iex"].index else []

    def _dir(feed: str, sym: str):
        o, c = bars[feed]["open"], bars[feed]["close"]
        if day not in o.index or sym not in o.columns:
            return None
        return direction(o.at[day, sym], c.at[day, sym])
    den = agree = miss = 0
    for sym in sip_top:
        ds, di = _dir("sip", sym), _dir("iex", sym)
        if di is None:
            miss += 1
        if ds in ("up", "down"):
            den += 1
            agree += int(ds == di)
    overlap = len(set(sip_top) & set(iex_top))
    return {"date": day.isoformat(), "universe_n": len(candidates), "sip_qualified_n": int(
        (rvol["sip"].loc[day].reindex(candidates) >= RVOL_MIN).sum()) if day in rvol["sip"].index else 0,
        "iex_qualified_n": int((rvol["iex"].loc[day].reindex(candidates) >= RVOL_MIN).sum())
        if day in rvol["iex"].index else 0,
        "sip_top_n": len(sip_top), "iex_top_n": len(iex_top), "overlap_n": overlap,
        "overlap_frac": overlap / TOP_N, "dir_agree_n": agree, "dir_den": den, "iex_missing_n": miss,
        "sip_top20": " ".join(sip_top), "iex_top20": " ".join(iex_top)}


def compute_report(daily: pd.DataFrame, sip_open: pd.DataFrame, iex_open: pd.DataFrame,
                   window_days: list[date], exclude: set[str]) -> pd.DataFrame:
    """The per-day table, from already-fetched long frames."""
    panels = panels_from_daily(daily)
    elig = eligibility(panels, exclude)
    all_days = list(panels["close"].index)
    symbols = list(panels["close"].columns)
    bars = {"sip": opening_panels(sip_open), "iex": opening_panels(iex_open)}
    rvol = {f: relative_volume(bars[f]["volume"] if not bars[f]["volume"].empty else pd.DataFrame(),
                               all_days, symbols) for f in ("sip", "iex")}
    rows = []
    for D in window_days:
        if D not in elig.index:
            continue
        cands = [s for s in elig.columns[elig.loc[D].to_numpy()]]
        rows.append(compare_day(D, cands, rvol, bars))
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"days": 0}
    n = df["overlap_n"]
    den, miss = int(df["dir_den"].sum()), int(df["iex_missing_n"].sum())
    sip_total = int(df["sip_top_n"].sum())
    by_year = {}
    for yr, g in df.groupby(df["date"].str[:4]):
        by_year[yr] = {"days": int(len(g)), "mean_overlap": float(g["overlap_n"].mean()),
                       "median_overlap": float(g["overlap_n"].median())}
    worst = df.sort_values(["overlap_n", "date"]).head(10)[
        ["date", "overlap_n", "sip_qualified_n", "iex_qualified_n"]].to_dict("records")
    return {
        "days": int(len(df)), "mean_overlap": float(n.mean()), "median_overlap": float(n.median()),
        "share_ge_15": float((n >= 15).mean()), "share_le_10": float((n <= 10).mean()),
        "direction_agreement": float(df["dir_agree_n"].sum() / den) if den else float("nan"),
        "direction_den": den,
        "iex_bar_missing_share": float(miss / sip_total) if sip_total else float("nan"),
        "days_sip_lt_20_qualified": int((df["sip_qualified_n"] < TOP_N).sum()),
        "days_iex_lt_20_qualified": int((df["iex_qualified_n"] < TOP_N).sum()),
        "mean_universe_n": float(df["universe_n"].mean()), "by_year": by_year, "worst_ten": worst}


def render_summary(stats: dict, cache: dict, *, wall_s: float, start: str, end: str,
                   n_symbols: int, limited: bool) -> str:
    if stats.get("days", 0) == 0:
        return "# IEX vs SIP\n\nNo days produced.\n"
    mb = cache.get("disk_bytes", 0) / 1e6
    lim = ("\n> **This run used `--limit-symbols`: it is a smoke test, NOT the report.**\n" if limited else "")
    w = "\n".join(f"| {r['date']} | {r['overlap_n']}/20 | {r['sip_qualified_n']} | {r['iex_qualified_n']} |"
                  for r in stats["worst_ten"])
    yrs = "\n".join(f"| {y} | {v['days']} | {v['mean_overlap']:.2f} | {v['median_overlap']:.1f} |"
                    for y, v in sorted(stats["by_year"].items()))
    return f"""# IEX vs SIP — can the free feed rank Stocks in Play?

Window {start} .. {end}, {stats['days']} trading days, {n_symbols} symbols considered.{lim}

## Headline numbers (top-20 overlap, SIP vs IEX, per day)

| Metric | Value |
| --- | --- |
| Mean overlap | **{stats['mean_overlap']:.2f} / 20** |
| Median overlap | **{stats['median_overlap']:.1f} / 20** |
| Days with overlap ≥ 15/20 | **{stats['share_ge_15']:.1%}** |
| Days with overlap ≤ 10/20 | **{stats['share_le_10']:.1%}** |
| Direction agreement (SIP top 20, first 5-min bar up/down in both feeds) | **{stats['direction_agreement']:.1%}** (n = {stats['direction_den']} non-doji picks) |
| SIP top-20 picks with NO IEX opening bar at all | {stats['iex_bar_missing_share']:.1%} |
| Days with fewer than 20 names at RVOL ≥ 100% | SIP {stats['days_sip_lt_20_qualified']}, IEX {stats['days_iex_lt_20_qualified']} |
| Mean universe size after the price/volume/ATR filters | {stats['mean_universe_n']:.0f} names/day |

By calendar year:

| Year | Days | Mean overlap | Median overlap |
| --- | --- | --- | --- |
{yrs}

Ten worst days (lowest overlap):

| Date | Overlap | SIP names ≥100% | IEX names ≥100% |
| --- | --- | --- | --- |
{w}

## Cache statistics for this run

Requests made {cache.get('requests_made', 0):,} · parquet files {cache.get('parquet_files', 0):,} · rows {cache.get('rows', 0):,} · disk {mb:,.1f} MB · wall time {wall_s / 60:.1f} min.

## How to read this (numbers only — the $99/month question is NOT decided here)

Per the build plan, high overlap would mean the free real-time feed could replace the paid SIP plan; low overlap would mean IEX cannot rank the same stocks. Read the median and the ≥15/20 and ≤10/20 shares together with the direction-agreement rate, because the live engine trades only names whose first 5-minute bar has a direction. Caveats that bear on interpretation:

* The universe filter (price, volume, ATR) is built from SIP daily bars for BOTH feeds, so this isolates the effect of the *opening-range volume* feed, not of the universe.
* IEX carries a small share of consolidated volume, so absolute IEX volumes are tiny and sparse for thinly traded names; RVOL is a ratio to the symbol's own IEX history, which is what the live engine would see. A missing bar counts as zero volume.
* Historical IEX bars equal what the real-time IEX feed delivers; the live engine would also see the 9:30–9:35 window complete only after 9:35.
* The assets list leans toward current tickers (survivorship), though inactive names were included.
* ATR is the simple mean of the prior 14 true ranges; "up/down" is close vs open of the first 5-minute bar.

Per-day CSV: `reports/orb/iex_vs_sip_2024_2025.csv` (gitignored).
"""


# ============================================================================== orchestration
def core_roster() -> set[str]:
    try:
        from shared.quadrants import CORE_ROSTER          # read-only; needs PYTHONPATH=src
    except ImportError as exc:                            # pragma: no cover - environment guidance
        raise SystemExit("Run with PYTHONPATH=src so the Core roster can be excluded.") from exc
    return {str(s).upper() for s in CORE_ROSTER}


def equity_symbols(assets: pd.DataFrame) -> list[str]:
    a = assets[assets["exchange"].isin(C.EQUITY_EXCHANGES)]
    return sorted(set(a["symbol"].astype(str).str.upper()))


def run(start: str, end: str, layer: DataLayer, *, csv_path: Path | None, summary_path: Path | None,
        exclude: set[str], offline: bool = False, limit_symbols: int | None = None,
        fetch_from: str = FETCH_FROM, daily_batch: int = 1000) -> tuple[pd.DataFrame, dict]:
    t0 = time.time()
    assets = layer.get_assets(offline=offline)
    symbols = [s for s in equity_symbols(assets) if s not in exclude]      # Core names are never candidates
    if limit_symbols:
        symbols = symbols[:limit_symbols]
    _log(f"assets: {len(assets)} total, {len(symbols)} US-exchange equities to consider")

    frames = []
    for i in range(0, len(symbols), daily_batch):
        chunk = symbols[i:i + daily_batch]
        frames.append(layer.get_bars(chunk, fetch_from, end, "1Day", "sip", offline=offline))
        _log(f"SIP daily bars: {min(i + daily_batch, len(symbols))}/{len(symbols)} symbols")
    daily = pd.concat([f for f in frames if not f.empty], ignore_index=True)

    window_days = layer.trading_days(start, end, offline=offline)
    panels = panels_from_daily(daily)
    elig = eligibility(panels, exclude)
    all_days = list(panels["close"].index)
    eligible = {D: [s for s in elig.columns[elig.loc[D].to_numpy()]] for D in window_days if D in elig.index}
    need = needed_open_symbols(eligible, all_days)
    _log(f"universe: mean {np.mean([len(v) for v in eligible.values()]):.0f} names/day; "
         f"opening-range pairs to cover per feed: {sum(len(v) for v in need.values()):,}")

    # One get_bars call per calendar month per feed (symbols = the union needed in that month):
    # every symbol-month parquet file is then written once, not once per day. The union costs a
    # modest over-fetch (names that were candidates on another day of the month), which the
    # manifest records like any other fetch.
    by_month: dict[tuple[int, int], dict[date, set[str]]] = {}
    for d, syms in need.items():
        by_month.setdefault((d.year, d.month), {})[d] = syms
    opens = {}
    for feed in ("sip", "iex"):
        parts = []
        for k, ym in enumerate(sorted(by_month)):
            days = sorted(by_month[ym])
            union = sorted(set().union(*by_month[ym].values()))
            parts.append(layer.get_bars(union, days[0], days[-1], "5Min", feed, window=WINDOW,
                                        offline=offline))
            _log(f"{feed.upper()} opening bars: month {k + 1}/{len(by_month)} ({ym[0]}-{ym[1]:02d}, "
                 f"{len(union)} symbols)")
        opens[feed] = pd.concat([p for p in parts if not p.empty], ignore_index=True) if parts else pd.DataFrame()

    df = compute_report(daily, opens["sip"], opens["iex"], window_days, exclude)
    stats = summarize(df)
    if csv_path is not None:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(csv_path, index=False)
    cache = layer.cache_stats()
    if summary_path is not None:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(render_summary(stats, cache, wall_s=time.time() - t0, start=start, end=end,
                                               n_symbols=len(symbols), limited=bool(limit_symbols)),
                                encoding="utf-8")
    return df, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", default="2024-01-02")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--csv", default="reports/orb/iex_vs_sip_2024_2025.csv")
    ap.add_argument("--summary", default=".review/phase1-iex-vs-sip.md")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--limit-symbols", type=int, help="smoke test on the first N symbols (NOT the report)")
    ap.add_argument("--cache-dir")
    args = ap.parse_args(argv)
    layer = DataLayer(args.cache_dir)
    df, stats = run(args.start, args.end, layer, csv_path=Path(args.csv), summary_path=Path(args.summary),
                    exclude=core_roster(), offline=args.offline, limit_symbols=args.limit_symbols)
    print(f"{stats.get('days', 0)} days; mean overlap {stats.get('mean_overlap', float('nan')):.2f}/20; "
          f"median {stats.get('median_overlap', float('nan'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
