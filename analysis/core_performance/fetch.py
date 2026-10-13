"""Download the raw inputs for the Core-vs-SPY analysis into `analysis/core_performance/data/` (gitignored).

    python -m analysis.core_performance.fetch [--start 2026-05-20]

Read-only: GET requests to the PAPER trading API (account, portfolio history, activities, positions) and the market-data
API (daily bars for the benchmark symbols and the cash proxy). Nothing is written back anywhere. The account number and id are
dropped from the saved account file. Keys are never printed or saved.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .alpaca_ro import ReadOnlyAlpaca, load_keys

DATA_DIR = Path(__file__).resolve().parent / "data"
# Benchmarks named in the brief, plus SGOV as the cash-return proxy (the existing excess_attribution uses SGOV's price return).
BENCH_SYMBOLS = ["SPY", "AGG", "VTI", "TLT", "IEF", "GLD", "DBC", "SGOV"]


def _save(name: str, obj) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = DATA_DIR / name
    p.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    return p


def last_weekday_before(day: date) -> date:
    """The latest Monday-Friday strictly before `day` (a market holiday is not excluded: Alpaca simply returns no bar for it)."""
    d = day - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def run(start: str, today: date | None = None) -> dict:
    api = ReadOnlyAlpaca(*load_keys())
    today = today or datetime.now(ZoneInfo("America/New_York")).date()      # the market's calendar day, not UTC
    acct = {k: v for k, v in api.account().items() if k not in ("account_number", "id")}
    hist = api.portfolio_history(period="1A", timeframe="1D", pnl_reset="no_reset", extended_hours="false", intraday_reporting="market_hours")
    acts = api.activities()
    pos = api.positions()
    end = last_weekday_before(today).isoformat()      # the last COMPLETED trading-week day: the SIP feed refuses a range that reaches today
    bars_all = api.daily_bars(BENCH_SYMBOLS, start, end, adjustment="all")
    bars_raw = api.daily_bars(["SPY", "SGOV"], start, end, adjustment="raw")
    meta = {"fetched_utc": datetime.now(timezone.utc).isoformat(), "bars_start": start, "bars_end": end, "bench_symbols": BENCH_SYMBOLS,
            "requests": len(api.requests_made), "paths": sorted(set(api.requests_made)),
            "counts": {"activities": len(acts), "positions": len(pos), "history_points": len(hist.get("timestamp") or []),
                       **{f"bars_all_{s}": len(b) for s, b in bars_all.items()}}}
    for name, obj in (("account.json", acct), ("portfolio_history.json", hist), ("activities.json", acts), ("positions.json", pos),
                      ("bars_all.json", bars_all), ("bars_raw.json", bars_raw), ("fetch_meta.json", meta)):
        _save(name, obj)
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="analysis.core_performance.fetch", description=__doc__.split("\n")[0])
    ap.add_argument("--start", default="2026-05-20", help="first day of benchmark bars (a few days before inception)")
    args = ap.parse_args(argv)
    meta = run(args.start)
    print(json.dumps(meta, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
