"""Read-only Alpaca market-data client: historical bars and the assets list. Nothing else.

No trading endpoint is reachable from here: the only URLs ever built are
`{DATA_BASE_URL}/v2/stocks/bars` and `{ASSETS_BASE_URL}/v2/assets`, and only with GET.
Keys are held privately, never appear in `repr()` or in any exception message.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterator

import requests

from .config import (
    ADJUSTMENT,
    ASSETS_BASE_URL,
    ASSETS_PATH,
    BACKOFF_BASE_S,
    BACKOFF_CAP_S,
    BARS_PATH,
    DATA_BASE_URL,
    HTTP_TIMEOUT_S,
    MAX_REQUESTS_PER_MINUTE,
    MAX_RETRIES,
    PAGE_LIMIT,
    RATE_BURST,
)
from .credentials import load_credentials
from .ratelimit import RateLimiter

_RETRYABLE = {429, 500, 502, 503, 504}


class AlpacaDataError(RuntimeError):
    """A request failed for good (non-retryable status, or retries exhausted)."""


class AlpacaAuthError(AlpacaDataError):
    """401/403: bad keys, or a subscription that does not permit the query."""


class AlpacaDataClient:
    def __init__(self, key: str, secret: str, *, session: requests.Session | None = None,
                 limiter: RateLimiter | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 data_url: str = DATA_BASE_URL, assets_url: str = ASSETS_BASE_URL,
                 max_retries: int = MAX_RETRIES) -> None:
        self.__headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
                          "Accept": "application/json"}
        self.session = session or requests.Session()
        self.sleep = sleep
        self.limiter = limiter or RateLimiter(MAX_REQUESTS_PER_MINUTE, RATE_BURST, sleep=sleep)
        self.data_url = data_url.rstrip("/")
        self.assets_url = assets_url.rstrip("/")
        self.max_retries = max_retries
        self.requests_made = 0            # every HTTP attempt, retries included

    @classmethod
    def from_env(cls, **kw) -> "AlpacaDataClient":
        key, secret = load_credentials()
        return cls(key, secret, **kw)

    def __repr__(self) -> str:                         # never expose the headers
        return f"AlpacaDataClient(data_url={self.data_url!r}, requests_made={self.requests_made})"

    # ------------------------------------------------------------------ HTTP
    def _get(self, url: str, params: dict) -> dict | list:
        attempt = 0
        while True:
            self.limiter.acquire()
            self.requests_made += 1
            try:
                resp = self.session.get(url, params=params, headers=self.__headers,
                                        timeout=HTTP_TIMEOUT_S)
            except (requests.ConnectionError, requests.Timeout):
                if attempt >= self.max_retries:
                    raise AlpacaDataError(f"network error after {attempt + 1} attempts: {url}") from None
                self.sleep(min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** attempt))
                attempt += 1
                continue
            status = resp.status_code
            if status == 200:
                return resp.json()
            if status in _RETRYABLE:
                if attempt >= self.max_retries:
                    raise AlpacaDataError(f"HTTP {status} after {attempt + 1} attempts: {url}")
                delay = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** attempt)
                if status == 429:
                    try:
                        delay = max(delay, float(resp.headers.get("Retry-After", 0)))
                    except (TypeError, ValueError):
                        pass
                self.sleep(delay)
                attempt += 1
                continue
            detail = ""
            try:
                body = resp.json()
                detail = str(body.get("message", ""))[:200] if isinstance(body, dict) else ""
            except ValueError:
                pass
            if status in (401, 403):
                raise AlpacaAuthError(
                    f"HTTP {status} from {url.split('?')[0]} (check the keys and that the "
                    f"subscription permits this query). {detail}".strip())
            raise AlpacaDataError(f"HTTP {status} from {url.split('?')[0]}. {detail}".strip())

    # ------------------------------------------------------------------ bars
    def iter_bars(self, symbols: list[str], timeframe: str, start: str, end: str, feed: str,
                  *, adjustment: str = ADJUSTMENT, limit: int = PAGE_LIMIT) -> Iterator[tuple[str, dict]]:
        """Yield (symbol, bar) for every bar in [start, end], following `next_page_token`.
        `start`/`end` are RFC3339 strings. Bars are Alpaca's `{t,o,h,l,c,v,...}` dicts."""
        params = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": start, "end": end,
                  "limit": limit, "adjustment": adjustment, "feed": feed, "sort": "asc"}
        token = None
        while True:
            page_params = dict(params)
            if token:
                page_params["page_token"] = token
            body = self._get(f"{self.data_url}{BARS_PATH}", page_params)
            bars = (body or {}).get("bars") or {}
            for sym, rows in bars.items():
                for bar in rows or []:
                    yield sym, bar
            token = (body or {}).get("next_page_token")
            if not token:
                return

    # ---------------------------------------------------------------- assets
    def list_assets(self, status: str) -> list[dict]:
        """`GET /v2/assets?status=<active|inactive>&asset_class=us_equity` (read-only reference data)."""
        if status not in ("active", "inactive"):
            raise ValueError("status must be 'active' or 'inactive'")
        body = self._get(f"{self.assets_url}{ASSETS_PATH}",
                         {"status": status, "asset_class": "us_equity"})
        return body if isinstance(body, list) else []
