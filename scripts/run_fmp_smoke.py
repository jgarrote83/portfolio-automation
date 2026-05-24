"""Smoke test for the new FMP /stable/* client.

Run from repo root with the venv active:
    $env:FMP_API_KEY = "<your_rotated_key>"
    python scripts/run_fmp_smoke.py

Hits one endpoint per method against a small slice of the real portfolio
and prints the first row of each response so you can eyeball the shape.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from shared.clients.fmp import FMPClient  # noqa: E402


SAMPLE_TICKERS = ["AAPL", "MCK", "GOOGL", "AMZN"]
ETF_TICKERS = ["SPY", "IDMO", "AIA"]


def banner(title: str) -> None:
    print(f"\n=== {title} " + "=" * (60 - len(title)))


def show(label: str, data) -> None:
    if not data:
        print(f"  {label}: EMPTY")
        return
    if isinstance(data, list):
        print(f"  {label}: {len(data)} rows | first = {json.dumps(data[0], default=str)[:240]}")
    else:
        print(f"  {label}: {json.dumps(data, default=str)[:240]}")


def main() -> int:
    key = os.environ.get("FMP_API_KEY")
    if not key:
        print("ERROR: set $env:FMP_API_KEY before running")
        return 2

    fmp = FMPClient(key)

    banner("Profile / fundamentals")
    show("profile(AAPL)", fmp.get_profile("AAPL"))
    show("dcf(AAPL)", fmp.get_dcf("AAPL"))
    show("ratings_snapshot(AAPL)", fmp.get_ratings_snapshot("AAPL"))

    banner("Batch quote + EOD price wrapper")
    show("batch_quote_short(sample)", fmp.get_batch_quote_short(SAMPLE_TICKERS))
    eod = fmp.get_eod_prices(SAMPLE_TICKERS + ETF_TICKERS)
    print(f"  get_eod_prices: {len(eod)} keys -> {list(eod.items())[:3]}")

    banner("Historical price light")
    hist = fmp.get_historical_price_light("AAPL")
    show("historical_price_light(AAPL)", hist[:1] if isinstance(hist, list) else hist)

    banner("Earnings calendar (next 14 days)")
    today = date.today()
    show(
        "earnings_calendar",
        fmp.get_earnings_calendar(today.isoformat(), (today + timedelta(days=14)).isoformat()),
    )

    banner("News")
    show("stock_news(sample, limit=5)", fmp.get_stock_news(SAMPLE_TICKERS, limit=5))

    banner("ETF look-through")
    for etf in ETF_TICKERS:
        show(f"etf_holdings({etf})", fmp.get_etf_holdings(etf))
        show(f"etf_country_weights({etf})", fmp.get_etf_country_weights(etf))
        show(f"etf_sector_weights({etf})", fmp.get_etf_sector_weights(etf))

    banner("Political flow (FMP backup)")
    show("senate_trades(MCK)", fmp.get_senate_trades("MCK"))
    show("house_trades(MCK)", fmp.get_house_trades("MCK"))
    show("latest_senate(limit=5)", fmp.get_latest_senate(limit=5))
    show("latest_house(limit=5)", fmp.get_latest_house(limit=5))

    banner("Backward-compat wrapper")
    combined = fmp.get_congressional_trading((today - timedelta(days=30)).isoformat())
    print(f"  get_congressional_trading(from -30d): {len(combined)} rows")

    print("\nAll calls completed. Check above for any EMPTY rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
