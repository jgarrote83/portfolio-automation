import logging
import requests

logger = logging.getLogger(__name__)

_BASE = "https://finnhub.io/api/v1"


class FinnhubClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers["X-Finnhub-Token"] = api_key

    def _get(self, path: str, params: dict | None = None) -> dict | list | None:
        try:
            r = self.session.get(f"{_BASE}{path}", params=params or {}, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error("Finnhub %s failed: %s", path, e)
            return None

    def get_market_news(self, category: str = "general") -> list[dict]:
        result = self._get("/news", {"category": category, "minId": 0})
        return result if isinstance(result, list) else []

    def get_company_news(self, ticker: str, from_date: str, to_date: str) -> list[dict]:
        result = self._get("/company-news", {
            "symbol": ticker,
            "from": from_date,
            "to": to_date,
        })
        return result if isinstance(result, list) else []

    def get_quote(self, ticker: str) -> dict | None:
        return self._get("/quote", {"symbol": ticker})
