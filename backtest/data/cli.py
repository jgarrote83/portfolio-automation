"""Command line for the data layer.

    python -m backtest.data.cli bars --symbols SPY,QQQ --start 2024-01-02 --end 2024-01-31 \
        --timeframe 1Day --feed sip [--window 09:30-09:35] [--offline]
    python -m backtest.data.cli assets [--refresh] [--offline]
    python -m backtest.data.cli news --start 2023-12-29 --end 2025-12-31   # Alpaca news, cached by month
    python -m backtest.data.cli stats
    python -m backtest.data.cli probe-invalid    # recover the rejected-symbol list for an older cache

`--offline` fails on any cache miss instead of calling the API (exact reruns, no keys needed).
Keys (when a fetch is needed) come from the gitignored `.env`; they are never printed.
"""
from __future__ import annotations

import argparse
import sys

from .bars import CacheMissError, DataLayer, HoldoutError
from .client import AlpacaDataError
from .credentials import MissingCredentialsError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backtest.data.cli", description=__doc__.split("\n")[0])
    ap.add_argument("--cache-dir", help="override data/cache")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bars")
    b.add_argument("--symbols", required=True, help="comma-separated")
    b.add_argument("--start", required=True)
    b.add_argument("--end", required=True)
    b.add_argument("--timeframe", default="1Min", choices=["1Min", "5Min", "1Day"])
    b.add_argument("--feed", default="sip", choices=["sip", "iex"])
    b.add_argument("--window", help="HH:MM-HH:MM, e.g. 09:30-09:35")
    b.add_argument("--offline", action="store_true")
    a = sub.add_parser("assets")
    a.add_argument("--refresh", action="store_true")
    a.add_argument("--offline", action="store_true")
    nw = sub.add_parser("news", help="fetch/cache Alpaca news by ET calendar day, one month at a time")
    nw.add_argument("--start", required=True)
    nw.add_argument("--end", required=True)
    nw.add_argument("--offline", action="store_true")
    sub.add_parser("stats")
    sub.add_parser("probe-invalid", help="recover the list of symbols the bars endpoint rejects (read-only)")
    args = ap.parse_args(argv)

    layer = DataLayer(args.cache_dir)
    try:
        return _dispatch(args, layer)
    except (CacheMissError, HoldoutError, MissingCredentialsError, AlpacaDataError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _news(args, layer: DataLayer) -> None:
    """Fetch/cache news month by month (resumable: every day is committed once fetched) and print progress."""
    import time
    from datetime import date, timedelta
    s_day, e_day = date.fromisoformat(args.start), date.fromisoformat(args.end)
    cur, total = s_day, 0
    t0 = time.time()
    while cur <= e_day:
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)          # first day of the next month
        last = min(e_day, nxt - timedelta(days=1))
        df = layer.get_news(cur, last, offline=args.offline)
        total += len(df)
        print(f"[{time.strftime('%H:%M:%S')}] news {cur}..{last}: {len(df):,} articles "
              f"(running total {total:,}; {layer.cache_stats().get('requests_made', 0):,} requests; {time.time() - t0:.0f}s)",
              flush=True)
        cur = nxt


def _dispatch(args, layer: DataLayer) -> int:
    if args.cmd == "bars":
        window = tuple(args.window.split("-")) if args.window else None
        df = layer.get_bars([s for s in args.symbols.split(",") if s], args.start, args.end,
                            args.timeframe, args.feed, window=window, offline=args.offline)
        print(f"{len(df)} rows, {df['symbol'].nunique() if len(df) else 0} symbols")
        if len(df):
            print(df.head(10).to_string(index=False))
    elif args.cmd == "assets":
        df = layer.get_assets(refresh=args.refresh, offline=args.offline)
        print(f"{len(df)} assets ({int((df['status'] == 'active').sum())} active, "
              f"{int((df['status'] == 'inactive').sum())} inactive)")
    elif args.cmd == "news":
        _news(args, layer)
    elif args.cmd == "probe-invalid":
        print(layer.probe_invalid())
    else:
        print(layer.cache_stats())
    return 0


if __name__ == "__main__":
    sys.exit(main())
