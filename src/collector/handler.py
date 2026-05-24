import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from shared.keyvault import load_secrets
from shared.storage import ensure_tables, upsert_entity, write_snapshot
from shared.clients.etrade import ETradeClient
from shared.clients.fmp import FMPClient
from shared.clients.fred import FREDClient
from shared.clients.finnhub import FinnhubClient
from shared.clients.quiver import QuiverClient

logger = logging.getLogger(__name__)

_SRC = Path(__file__).parent.parent   # src/
_MACRO_SERIES_FILE = _SRC / "config" / "macro-series.json"
_PORTFOLIO_FALLBACK = _SRC / "config" / "portfolio.json"
_ETF_WATCHLIST = ["IDVO", "IDMO", "AIA"]


def run() -> None:
    today = date.today().isoformat()
    logger.info("=== Collector starting for %s ===", today)

    secrets = load_secrets()
    ensure_tables()

    # --- Portfolio -----------------------------------------------------------
    etrade = ETradeClient(
        consumer_key=secrets.get("EtradeConsumerKey"),
        consumer_secret=secrets.get("EtradeConsumerSecret"),
        access_token=secrets.get("EtradeAccessToken"),
        access_token_secret=secrets.get("EtradeAccessTokenSecret"),
    )
    positions = etrade.get_portfolio()
    balances = etrade.get_balances()

    if positions is None:
        logger.warning("E*TRADE unavailable — loading config/portfolio.json fallback")
        with open(_PORTFOLIO_FALLBACK) as f:
            fb = json.load(f)
        positions = fb.get("positions", [])
        balances = fb.get("balances", {})

    tickers = [p["ticker"] for p in positions if p.get("ticker")]
    logger.info("Portfolio tickers (%d): %s", len(tickers), tickers)

    # --- FMP -----------------------------------------------------------------
    fmp = FMPClient(secrets["FmpApiKey"])
    profiles = fmp.get_profiles(tickers)

    from_2w = (date.today() - timedelta(days=1)).isoformat()
    to_2w   = (date.today() + timedelta(days=14)).isoformat()
    from_30d = (date.today() - timedelta(days=30)).isoformat()

    earnings           = fmp.get_earnings_calendar(from_2w, to_2w)
    stock_news         = fmp.get_stock_news(tickers, limit=30)
    etf_holdings: dict = {etf: fmp.get_etf_holdings(etf) for etf in _ETF_WATCHLIST}
    etf_country: dict  = {etf: fmp.get_etf_country_weights(etf) for etf in _ETF_WATCHLIST}
    etf_sector: dict   = {etf: fmp.get_etf_sector_weights(etf) for etf in _ETF_WATCHLIST}

    logger.info("FMP: %d profiles, %d earnings, %d news",
                len(profiles), len(earnings), len(stock_news))

    # --- Quiver (primary congressional source) ------------------------------
    quiver = QuiverClient(secrets.get("QuiverApiKey"))
    if quiver.ready:
        congressional = quiver.get_live_congress_trades()
        if from_30d:
            congressional = [
                r for r in congressional
                if (r.get("TransactionDate") or r.get("Date") or r.get("transactionDate") or "") >= from_30d
            ]
        lobbying      = quiver.get_live_lobbying()
        gov_contracts = quiver.get_live_gov_contracts()
    else:
        logger.warning("Quiver key missing — falling back to FMP senate/house latest")
        congressional = fmp.get_congressional_trading(from_30d)
        lobbying      = []
        gov_contracts = []
    logger.info("Quiver/FMP: %d congressional, %d lobbying, %d gov contracts",
                len(congressional), len(lobbying), len(gov_contracts))

    # --- FRED ----------------------------------------------------------------
    with open(_MACRO_SERIES_FILE) as f:
        macro_meta: dict = json.load(f)

    fred = FREDClient(secrets["FredApiKey"])
    macro_data = fred.get_all_series(list(macro_meta.keys()))
    logger.info("FRED: %d series collected", sum(1 for v in macro_data.values() if v))

    # --- EOD prices (FMP batch-quote, single call) --------------------------
    all_tickers = list(dict.fromkeys(tickers + _ETF_WATCHLIST))  # preserve order, dedupe
    prices = fmp.get_eod_prices(all_tickers)
    logger.info("FMP prices: %d/%d collected", len(prices), len(all_tickers))

    # --- Finnhub -------------------------------------------------------------
    finnhub = FinnhubClient(secrets["FinnhubApiKey"])
    market_news = finnhub.get_market_news("general")
    forex_news  = finnhub.get_market_news("forex")

    week_ago = (date.today() - timedelta(days=7)).isoformat()
    company_news: dict = {}
    # Cap at 10 tickers to stay within 60 calls/min free tier
    for ticker in tickers[:10]:
        company_news[ticker] = finnhub.get_company_news(ticker, week_ago, today)

    logger.info("Finnhub: %d market news, %d company news items",
                len(market_news), sum(len(v) for v in company_news.values()))

    # --- Assemble snapshot ---------------------------------------------------
    snapshot = {
        "date": today,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "portfolio": {
            "positions": positions,
            "balances": balances,
            "source": "etrade" if etrade.ready else "fallback",
        },
        "fundamentals": profiles,
        "earnings_calendar": earnings,
        "stock_news": stock_news,
        "congressional_trades": congressional,
        "lobbying": lobbying,
        "gov_contracts": gov_contracts,
        "etf_holdings": etf_holdings,
        "etf_country_weights": etf_country,
        "etf_sector_weights": etf_sector,
        "macro": {
            "series_meta": macro_meta,
            "data": macro_data,
        },
        "prices": prices,
        "news": {
            "market": market_news[:50],
            "forex": forex_news[:20],
            "company": company_news,
        },
    }

    # --- Persist -------------------------------------------------------------
    write_snapshot(today, snapshot)
    _write_portfolio_history(today, positions, prices)
    _write_fundamentals_history(today, profiles)
    _write_macro_history(today, macro_data, macro_meta)
    _write_etf_history(today, etf_holdings, prices)
    _write_sentiment_history(today, snapshot)

    logger.info("=== Collector completed for %s ===", today)


# ---------------------------------------------------------------------------
# Table writers
# ---------------------------------------------------------------------------

def _write_portfolio_history(today: str, positions: list[dict], prices: dict) -> None:
    total_value = sum(p.get("market_value", 0) for p in positions) or 1.0
    for pos in positions:
        ticker = pos.get("ticker")
        if not ticker:
            continue
        price_data = prices.get(ticker, {})
        upsert_entity("PortfolioHistory", {
            "PartitionKey": ticker,
            "RowKey": today,
            "quantity":     pos.get("quantity", 0),
            "market_value": pos.get("market_value", 0),
            "cost_basis":   pos.get("cost_basis", 0),
            "weight":       round(pos.get("market_value", 0) / total_value, 4),
            "day_gain":     pos.get("day_gain", 0),
            "total_gain":   pos.get("total_gain", 0),
            "close_price":  price_data.get("c", 0),
            "volume":       price_data.get("v", 0),
            "security_type": pos.get("security_type", "EQ"),
        })


def _write_fundamentals_history(today: str, profiles: list[dict]) -> None:
    for p in profiles:
        ticker = p.get("symbol")
        if not ticker:
            continue
        upsert_entity("FundamentalsHistory", {
            "PartitionKey":    ticker,
            "RowKey":          today,
            "price":           p.get("price"),
            "market_cap":      p.get("mktCap"),
            "pe_ratio":        p.get("pe"),
            "beta":            p.get("beta"),
            "dcf_value":       p.get("dcf"),
            "analyst_rating":  p.get("rating"),
            "div_yield":       p.get("lastAnnualDividendYield"),
            "sector":          p.get("sector", ""),
            "industry":        p.get("industry", ""),
        })


def _write_macro_history(today: str, macro_data: dict, meta: dict) -> None:
    for series_id, observations in macro_data.items():
        if not observations:
            continue
        latest = observations[0]
        prev   = observations[1] if len(observations) > 1 else None
        try:
            val = float(latest.get("value", "nan"))
        except (ValueError, TypeError):
            val = None
        try:
            delta = (val - float(prev["value"])) if (prev and val is not None
                                                     and prev.get("value") not in (".", "")) else None
        except (ValueError, TypeError):
            delta = None
        upsert_entity("MacroHistory", {
            "PartitionKey": series_id,
            "RowKey":       today,
            "obs_date":     latest.get("date", ""),
            "value":        val,
            "delta":        delta,
            "series_name":  meta.get(series_id, {}).get("name", series_id),
            "unit":         meta.get(series_id, {}).get("unit", ""),
        })


def _write_etf_history(today: str, etf_holdings: dict, prices: dict) -> None:
    for etf, holdings in etf_holdings.items():
        price_data = prices.get(etf, {})
        upsert_entity("ETFLookthroughHistory", {
            "PartitionKey":    etf,
            "RowKey":          today,
            "holdings_count":  len(holdings),
            "top_10":          json.dumps(holdings[:10], default=str),
            "close_price":     price_data.get("c", 0),
            "volume":          price_data.get("v", 0),
        })


def _write_sentiment_history(today: str, snapshot: dict) -> None:
    news = snapshot.get("news", {})
    upsert_entity("SentimentHistory", {
        "PartitionKey":         today,
        "RowKey":               "market_overview",
        "market_news_count":    len(news.get("market", [])),
        "forex_news_count":     len(news.get("forex", [])),
        "company_news_count":   sum(len(v) for v in news.get("company", {}).values()),
        "positions_count":      len(snapshot.get("portfolio", {}).get("positions", [])),
        "etrade_source":        snapshot.get("portfolio", {}).get("source", "unknown"),
    })
