"""Quiver Quantitative API client — alternative data for congress trades, wikipedia
views, lobbying, gov contracts, off-exchange, etc.

Auth: `Authorization: Token <YOUR_TOKEN>` header (note: NOT `Bearer`).
Base URL: https://api.quiverquant.com

Hobbyist plan endpoints exposed here (most useful for portfolio analysis):
- /beta/live/congresstrading              — newest congressional trades, all tickers
- /beta/historical/congresstrading/{tkr}  — per-ticker congressional history
- /beta/live/lobbying                     — recent corporate lobbying
- /beta/historical/lobbying/{tkr}         — per-ticker lobbying history
- /beta/live/govcontractsall              — recent government contracts
- /beta/historical/govcontractsall/{tkr}  — per-ticker contract history
- /beta/historical/wikipedia/{ticker}     — wikipedia page views per ticker
"""
from __future__ import annotations

import logging
import requests

logger = logging.getLogger(__name__)

_BASE = "https://api.quiverquant.com"
_TIMEOUT = 30


class QuiverClient:
    def __init__(self, api_key: str | None):
        self.api_key = api_key
        self.session = requests.Session()
        if api_key:
            self.session.headers.update({
                "Authorization": f"Token {api_key}",
                "Accept": "application/json",
            })

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: dict | None = None) -> list | dict | None:
        if not self.ready:
            return None
        try:
            r = self.session.get(f"{_BASE}{path}", params=params, timeout=_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            logger.error("Quiver %s failed: %s", path, e)
            return None

    # ---- Congressional trading --------------------------------------------
    def get_live_congress_trades(self) -> list[dict]:
        """Newest ~100 congressional trades across all tickers (both chambers)."""
        result = self._get("/beta/live/congresstrading")
        return result if isinstance(result, list) else []

    def get_congress_trades_for(self, ticker: str) -> list[dict]:
        result = self._get(f"/beta/historical/congresstrading/{ticker}")
        return result if isinstance(result, list) else []

    # ---- Lobbying ---------------------------------------------------------
    def get_live_lobbying(self) -> list[dict]:
        result = self._get("/beta/live/lobbying")
        return result if isinstance(result, list) else []

    def get_lobbying_for(self, ticker: str) -> list[dict]:
        result = self._get(f"/beta/historical/lobbying/{ticker}")
        return result if isinstance(result, list) else []

    # ---- Government contracts ---------------------------------------------
    def get_live_gov_contracts(self) -> list[dict]:
        result = self._get("/beta/live/govcontractsall")
        return result if isinstance(result, list) else []

    def get_gov_contracts_for(self, ticker: str) -> list[dict]:
        result = self._get(f"/beta/historical/govcontractsall/{ticker}")
        return result if isinstance(result, list) else []

    # ---- Wikipedia attention ----------------------------------------------
    def get_wikipedia_views(self, ticker: str) -> list[dict]:
        result = self._get(f"/beta/historical/wikipedia/{ticker}")
        return result if isinstance(result, list) else []
