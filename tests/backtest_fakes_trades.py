"""Fake Alpaca trade-print and trade-condition endpoints for the data-layer tests (no network, no keys).

Built on `backtest_fakes.FakeSession`: the REAL `AlpacaDataClient` runs against it, so pagination, retry, the
rate limiter and URL construction are all exercised. Not a data source for any real analysis.
"""
from __future__ import annotations

import pandas as pd
from backtest_fakes import FakeMarket, FakeResponse, FakeSession

TRADES_URL = "https://data.alpaca.markets/v2/stocks/trades"
CONDITIONS_URL = "https://data.alpaca.markets/v2/stocks/meta/conditions/trade"


def trade(t: str, p: float, s: int = 100, c=("@",), i: int = 1, x: str = "Q", z: str = "C") -> dict:
    """One Alpaca trade print `{t, x, p, s, c, i, z}`; `t` may carry nanoseconds."""
    return {"t": t, "x": x, "p": p, "s": s, "c": list(c), "i": i, "z": z}


class FakeTrades:
    """Trade prints per symbol served like `GET /v2/stocks/trades`: filtered to [start, end] INCLUSIVE,
    oldest first (ties keep their stored order), paged by `page_size` with an opaque `page_token`."""

    def __init__(self, trades: dict[str, list[dict]], page_size: int = 4) -> None:
        self.trades = {sym: [row for _k, row in sorted(enumerate(rows), key=lambda kr: (pd.Timestamp(kr[1]["t"]), kr[0]))]
                       for sym, rows in trades.items()}
        self.page_size = page_size

    def query(self, params: dict) -> dict:
        sym = params["symbols"]
        start, end = pd.Timestamp(params["start"]), pd.Timestamp(params["end"])
        rows = [r for r in self.trades.get(sym, []) if start <= pd.Timestamp(r["t"]) <= end]
        offset = int(params.get("page_token") or 0)
        limit = min(self.page_size, int(params.get("limit", 10000)))
        page = rows[offset: offset + limit]
        nxt = offset + len(page)
        return {"trades": {sym: page} if page else {}, "next_page_token": str(nxt) if nxt < len(rows) else None}


class TradesSession(FakeSession):
    """FakeSession that also serves the trades endpoint and the trade-condition table."""

    def __init__(self, market: FakeMarket, trades: FakeTrades, script: list | None = None,
                 conditions: dict | None = None) -> None:
        super().__init__(market, script)
        self.trades = trades
        self.conditions = conditions or {"@": "Regular Sale", "I": "Odd Lot Trade"}

    @property
    def trade_calls(self) -> list[dict]:
        return [c for c in self.calls if c["url"] == TRADES_URL]

    @property
    def condition_calls(self) -> list[dict]:
        return [c for c in self.calls if c["url"] == CONDITIONS_URL]

    def get(self, url, params=None, headers=None, timeout=None):
        if url in (TRADES_URL, CONDITIONS_URL):
            self.calls.append({"url": url, "params": dict(params or {}), "method": "GET"})
            if self.script:
                item = self.script.pop(0)
                status, hdrs = item if isinstance(item, tuple) else (item, {})
                return FakeResponse(status, {"message": "scripted failure"}, hdrs)
            return FakeResponse(200, self.trades.query(params) if url == TRADES_URL else self.conditions)
        return super().get(url, params=params, headers=headers, timeout=timeout)
