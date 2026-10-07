"""Constants for the backtest data layer. Every tunable lives here so the tests can pin them."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "cache"
DEFAULT_ENV_PATH = REPO_ROOT / ".env"

# --- endpoints (read-only: historical bars + the assets list; NEVER a trading endpoint) ------
DATA_BASE_URL = "https://data.alpaca.markets"
BARS_PATH = "/v2/stocks/bars"
# The assets list lives on the Trading API host (the data host has no /v2/assets). Only
# GET /v2/assets is ever called there. Paper host, per repo rule 7.
ASSETS_BASE_URL = "https://paper-api.alpaca.markets"
ASSETS_PATH = "/v2/assets"

# News (read-only GET on the market-data host; Alpaca's news is Benzinga's). The page maximum is 50 articles.
NEWS_PATH = "/v1beta1/news"
NEWS_PAGE_LIMIT = 50

# Historical trades (ticks) and the trade-condition code table (read-only GETs on the market-data host).
# `get_trades` serves ONE symbol and ONE exact ET time window on one day (the same-minute diagnostic).
TRADES_PATH = "/v2/stocks/trades"
TRADE_PAGE_LIMIT = 10_000
CONDITIONS_PATH = "/v2/stocks/meta/conditions/trade"

# --- supported request shapes --------------------------------------------------------------
TIMEFRAMES = ("1Min", "5Min", "1Day")
FEEDS = ("sip", "iex")
ADJUSTMENT = "raw"                    # raw, unadjusted prices as in the paper; part of the cache key

# --- limits ---------------------------------------------------------------------------------
PAGE_LIMIT = 10_000                   # Alpaca's maximum rows per page
MAX_REQUESTS_PER_MINUTE = 180         # margin under the free tier's 200/min
RATE_BURST = 10                       # token-bucket capacity (the rolling-window guard is strict)
SYMBOLS_PER_REQUEST = 200             # daily bars and windowed requests (~1 row per symbol-day)
SYMBOLS_PER_REQUEST_INTRADAY = 25     # full-day 1Min/5Min requests (hundreds of rows per symbol-day)
FLUSH_ROWS = 400_000                  # buffered rows before an interim parquet flush
MAX_RETRIES = 6
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 30.0
HTTP_TIMEOUT_S = 30

# --- guards ----------------------------------------------------------------------------------
HOLDOUT_START = date(2026, 1, 1)      # rule 6: never touched without allow_holdout=True
RECENT_GUARD = timedelta(minutes=15)  # never cache today or the last 15 minutes

ET = ZoneInfo("America/New_York")
ET_NAME = "US/Eastern"                # the column tz named in the spec
CALENDAR_SYMBOL = "SPY"               # trading days are derived from SPY daily bars (no trading API)

COLUMNS = ["symbol", "ts", "open", "high", "low", "close", "volume"]

# Exchanges kept when building the equity universe from the assets list (OTC excluded).
EQUITY_EXCHANGES = ("NYSE", "NASDAQ", "AMEX", "ARCA", "BATS")
