import logging
import requests

logger = logging.getLogger(__name__)

_BASE = "https://financialmodelingprep.com/api"


class FMPClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()

    def _get(self, path: str, params: dict | None = None) -> dict | list | None:
        p = {"apikey": self.api_key}
        if params:
            p.update(params)
        try:
            r = self.session.get(f"{_BASE}{path}", params=p, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error("FMP %s failed: %s", path, e)
            return None

    def get_profiles(self, tickers: list[str]) -> list[dict]:
        if not tickers:
            return []
        result = self._get(f"/v3/profile/{','.join(tickers)}")
        return result if isinstance(result, list) else []

    def get_earnings_calendar(self, from_date: str, to_date: str) -> list[dict]:
        result = self._get("/v3/earning_calendar", {"from": from_date, "to": to_date})
        return result if isinstance(result, list) else []

    def get_etf_holdings(self, ticker: str) -> list[dict]:
        result = self._get(f"/v3/etf-holder/{ticker}")
        return result if isinstance(result, list) else []

    def get_congressional_trading(self, from_date: str) -> list[dict]:
        result = self._get("/v4/senate-trading", {"from": from_date})
        return result if isinstance(result, list) else []

    def get_stock_news(self, tickers: list[str], limit: int = 30) -> list[dict]:
        if not tickers:
            return []
        result = self._get("/v3/stock_news", {
            "tickers": ",".join(tickers),
            "limit": limit,
        })
        return result if isinstance(result, list) else []

    def get_analyst_ratings(self, ticker: str) -> dict | None:
        result = self._get(f"/v3/rating/{ticker}")
        if isinstance(result, list) and result:
            return result[0]
        return None
