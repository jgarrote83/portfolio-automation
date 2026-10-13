"""Read-only Alpaca access for the Core-vs-SPY analysis. GET requests only: this class has no method that can submit,
replace or cancel anything, and `get` is the only function that touches the network.

Two hosts: the PAPER trading API (account, portfolio history, activities, positions) and the market-data API (daily
bars for benchmarks). Keys come from the process environment or the gitignored repo-root `.env`; they are sent as
headers and never printed, logged or written to disk. This module is deliberately separate from `backtest.data`: the
research data layer's refusal of 2026 dates stays intact and is not used or loosened here.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TRADING_HOST = "https://paper-api.alpaca.markets"
DATA_HOST = "https://data.alpaca.markets"
KEY_VAR, SECRET_VAR = "ALPACA_API_KEY", "ALPACA_API_SECRET"


class MissingKeys(RuntimeError):
    """No Alpaca keys; the message names the variables, never a value."""


def load_keys(env: dict | None = None, env_file: Path | None = None) -> tuple[str, str]:
    env = os.environ if env is None else env
    key, secret = env.get(KEY_VAR), env.get(SECRET_VAR)
    if key and secret:
        return key, secret
    path = env_file or (REPO_ROOT / ".env")
    if path.is_file():
        found = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            found[k.strip()] = v.strip().strip('"').strip("'")
        key, secret = key or found.get(KEY_VAR), secret or found.get(SECRET_VAR)
    if not key or not secret:
        raise MissingKeys(f"{KEY_VAR} / {SECRET_VAR} not found in the environment or {path}")
    return key, secret


class ReadOnlyAlpaca:
    """GET-only client. `session` is a `requests.Session`-like object (injected in tests); only `.get` is ever called."""

    def __init__(self, key: str, secret: str, session=None, *, sleep: Callable[[float], None] = time.sleep, attempts: int = 4):
        import requests
        self._s = session or requests.Session()
        self._headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        self._sleep, self._attempts = sleep, attempts
        self.requests_made: list[str] = []            # paths only, for the audit line in the report

    def get(self, host: str, path: str, params: dict | None = None):
        url = f"{host}{path}"
        last = None
        for i in range(self._attempts):
            r = self._s.get(url, headers=self._headers, params=params, timeout=60)
            self.requests_made.append(path)
            if r.status_code in (429, 500, 502, 503, 504):
                last = r.status_code
                self._sleep(2.0 ** (i + 1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"GET {path} -> HTTP {r.status_code}: {r.text[:200]}")
            return r.json()
        raise RuntimeError(f"GET {path} failed after {self._attempts} attempts (last HTTP {last})")

    # ----------------------------------------------------------------------------------- paper trading API
    def account(self) -> dict:
        return self.get(TRADING_HOST, "/v2/account")

    def positions(self) -> list:
        return self.get(TRADING_HOST, "/v2/positions")

    def portfolio_history(self, **params) -> dict:
        return self.get(TRADING_HOST, "/v2/account/portfolio/history", params)

    def activities(self, *, after: str | None = None, until: str | None = None, page_size: int = 100) -> list:
        """Every account activity, oldest first, paged with `page_token` until a short page."""
        out, token = [], None
        while True:
            params = {"direction": "asc", "page_size": page_size}
            if after:
                params["after"] = after
            if until:
                params["until"] = until
            if token:
                params["page_token"] = token
            page = self.get(TRADING_HOST, "/v2/account/activities", params)
            out.extend(page)
            if len(page) < page_size:
                return out
            token = page[-1]["id"]

    # --------------------------------------------------------------------------------- market-data API (bars)
    def daily_bars(self, symbols: list[str], start: str, end: str, *, adjustment: str = "all", feed: str = "sip") -> dict[str, list[dict]]:
        """Daily bars per symbol, `adjustment` 'raw' or 'all' (splits and dividends). Pages on `next_page_token`."""
        out: dict[str, list[dict]] = {s: [] for s in symbols}
        token = None
        while True:
            params = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": start, "end": end, "adjustment": adjustment,
                      "feed": feed, "limit": 10000}
            if token:
                params["page_token"] = token
            page = self.get(DATA_HOST, "/v2/stocks/bars", params)
            for sym, bars in (page.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bars)
            token = page.get("next_page_token")
            if not token:
                return out
