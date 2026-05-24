"""Financial Modeling Prep client — Starter plan, `/stable/*` endpoints.

Auth: apikey query parameter on every request (header also supported, but query is
simpler for caching diagnostics). All methods return [] / None on error and log the
failure rather than raising, so a single endpoint outage cannot kill the collector.

Verified endpoints (Starter plan, May 2026):
- Company profile:      /stable/profile?symbol=...
- Batch quote short:    /stable/batch-quote-short?symbols=A,B,C
- Historical EOD light: /stable/historical-price-eod/light?symbol=...
- Earnings calendar:    /stable/earnings-calendar?from=...&to=...
- Stock news search:    /stable/news/stock?symbols=A,B,C
- ETF holdings:         /stable/etf/holdings?symbol=SPY
- ETF country weights:  /stable/etf/country-weightings?symbol=SPY
- ETF sector weights:   /stable/etf/sector-weightings?symbol=SPY
- Senate trades:        /stable/senate-trades?symbol=AAPL
- House trades:         /stable/house-trades?symbol=AAPL
- Latest senate flow:   /stable/senate-latest?page=0&limit=100
- Latest house flow:    /stable/house-latest?page=0&limit=100
- DCF valuation:        /stable/discounted-cash-flow?symbol=AAPL
- Ratings snapshot:     /stable/ratings-snapshot?symbol=AAPL
"""
from __future__ import annotations

import logging
import requests

logger = logging.getLogger(__name__)

_BASE = "https://financialmodelingprep.com/stable"
_TIMEOUT = 30


class FMPClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()

    # ---- low-level ---------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict | list | None:
        p = {"apikey": self.api_key}
        if params:
            p.update(params)
        try:
            r = self.session.get(f"{_BASE}{path}", params=p, timeout=_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            logger.error("FMP %s failed: %s", path, e)
            return None

    # ---- profile / fundamentals -------------------------------------------
    def get_profile(self, ticker: str) -> dict | None:
        result = self._get("/profile", {"symbol": ticker})
        if isinstance(result, list) and result:
            return result[0]
        return None

    def get_profiles(self, tickers: list[str]) -> list[dict]:
        """No batch on /stable/profile — fan out per ticker (~1 call each)."""
        out: list[dict] = []
        for t in tickers:
            p = self.get_profile(t)
            if p:
                out.append(p)
        return out

    def get_dcf(self, ticker: str) -> dict | None:
        result = self._get("/discounted-cash-flow", {"symbol": ticker})
        if isinstance(result, list) and result:
            return result[0]
        return None

    def get_ratings_snapshot(self, ticker: str) -> dict | None:
        result = self._get("/ratings-snapshot", {"symbol": ticker})
        if isinstance(result, list) and result:
            return result[0]
        return None

    # ---- quotes / prices ---------------------------------------------------
    def get_batch_quote_short(self, tickers: list[str]) -> list[dict]:
        if not tickers:
            return []
        result = self._get("/batch-quote-short", {"symbols": ",".join(tickers)})
        return result if isinstance(result, list) else []

    def get_eod_prices(self, tickers: list[str]) -> dict[str, dict]:
        """Return {ticker: {c, v, t}} for each ticker.

        Uses /stable/historical-price-eod/light (one call per ticker) and reads the
        latest row — batch-quote endpoints are above Starter tier. Cost: N calls.
        """
        out: dict[str, dict] = {}
        for t in tickers:
            rows = self.get_historical_price_light(t)
            if not rows:
                continue
            latest = rows[0]
            out[t] = {
                "c": latest.get("price") or latest.get("close"),
                "v": latest.get("volume"),
                "t": latest.get("date"),
            }
        return out

    def get_historical_price_light(self, ticker: str) -> list[dict]:
        """End-of-day OHLC + volume (last ~5 years on Starter)."""
        result = self._get("/historical-price-eod/light", {"symbol": ticker})
        if isinstance(result, dict) and "historical" in result:
            return result["historical"] or []
        return result if isinstance(result, list) else []

    # ---- calendars ---------------------------------------------------------
    def get_earnings_calendar(self, from_date: str, to_date: str) -> list[dict]:
        result = self._get("/earnings-calendar", {"from": from_date, "to": to_date})
        return result if isinstance(result, list) else []

    # ---- news --------------------------------------------------------------
    def get_stock_news(self, tickers: list[str], limit: int = 30) -> list[dict]:
        if not tickers:
            return []
        result = self._get("/news/stock", {
            "symbols": ",".join(tickers),
            "limit": limit,
        })
        return result if isinstance(result, list) else []

    # ---- ETF look-through --------------------------------------------------
    def get_etf_holdings(self, ticker: str) -> list[dict]:
        """NOT AVAILABLE on Starter tier (returns 402). Kept for forward-compat;
        always returns []. Use country + sector weights instead."""
        return []

    def get_etf_country_weights(self, ticker: str) -> list[dict]:
        result = self._get("/etf/country-weightings", {"symbol": ticker})
        return result if isinstance(result, list) else []

    def get_etf_sector_weights(self, ticker: str) -> list[dict]:
        result = self._get("/etf/sector-weightings", {"symbol": ticker})
        return result if isinstance(result, list) else []

    # ---- political flow (FMP — backup; Quiver is primary) -----------------
    def get_senate_trades(self, ticker: str) -> list[dict]:
        result = self._get("/senate-trades", {"symbol": ticker})
        return result if isinstance(result, list) else []

    def get_house_trades(self, ticker: str) -> list[dict]:
        result = self._get("/house-trades", {"symbol": ticker})
        return result if isinstance(result, list) else []

    def get_latest_senate(self, limit: int = 100) -> list[dict]:
        result = self._get("/senate-latest", {"page": 0, "limit": limit})
        return result if isinstance(result, list) else []

    def get_latest_house(self, limit: int = 100) -> list[dict]:
        result = self._get("/house-latest", {"page": 0, "limit": limit})
        return result if isinstance(result, list) else []

    def get_congressional_trading(self, from_date: str | None = None) -> list[dict]:
        """Backward-compat wrapper: combined latest senate + house flow.

        `from_date` clips client-side using `transactionDate` or `disclosureDate`.
        """
        senate = self.get_latest_senate(limit=100)
        house  = self.get_latest_house(limit=100)
        for r in senate:
            r["chamber"] = "senate"
        for r in house:
            r["chamber"] = "house"
        combined = senate + house
        if from_date:
            combined = [
                r for r in combined
                if (r.get("transactionDate") or r.get("disclosureDate") or "") >= from_date
            ]
        return combined
