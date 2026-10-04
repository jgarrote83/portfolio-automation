"""Command line for the data layer.

    python -m backtest.data.cli bars --symbols SPY,QQQ --start 2024-01-02 --end 2024-01-31 \
        --timeframe 1Day --feed sip [--window 09:30-09:35] [--offline]
    python -m backtest.data.cli assets [--refresh] [--offline]
    python -m backtest.data.cli stats

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
    sub.add_parser("stats")
    args = ap.parse_args(argv)

    layer = DataLayer(args.cache_dir)
    try:
        return _dispatch(args, layer)
    except (CacheMissError, HoldoutError, MissingCredentialsError, AlpacaDataError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


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
    else:
        print(layer.cache_stats())
    return 0


if __name__ == "__main__":
    sys.exit(main())
