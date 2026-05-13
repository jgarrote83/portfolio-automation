import logging
import time
import requests

logger = logging.getLogger(__name__)

# Massive rebranded from Polygon.io; the REST API domain and spec remain the same.
_BASE = "https://api.polygon.io"
_MIN_INTERVAL = 12.0  # 5 calls/min free tier


class MassiveClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self._last_call: float = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        self._last_call = time.monotonic()

    def get_previous_close(self, ticker: str) -> dict | None:
        self._throttle()
        try:
            r = self.session.get(
                f"{_BASE}/v2/aggs/ticker/{ticker}/prev",
                params={"adjusted": "true", "apiKey": self.api_key},
                timeout=30,
            )
            r.raise_for_status()
            results = r.json().get("results", [])
            return results[0] if results else None
        except Exception as e:
            logger.error("Massive %s failed: %s", ticker, e)
            return None

    def get_prices(self, tickers: list[str]) -> dict[str, dict]:
        prices: dict[str, dict] = {}
        for ticker in tickers:
            data = self.get_previous_close(ticker)
            if data:
                prices[ticker] = data
        return prices
