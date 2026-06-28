"""Minimal Alpaca paper-trading REST client.

Phase 2 only — used by the executor to place orders after human approval.
Intentionally keeps deps to `requests` (no alpaca-py SDK) to avoid native wheel
issues on Functions Linux Consumption.

Docs: https://docs.alpaca.markets/reference/postorder
"""
from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

_PAPER_BASE = "https://paper-api.alpaca.markets"
_DATA_BASE = "https://data.alpaca.markets"


class AlpacaClient:
    def __init__(self, api_key: str, api_secret: str, base_url: str = _PAPER_BASE):
        if not api_key or not api_secret:
            raise ValueError("AlpacaClient requires api_key and api_secret")
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "accept": "application/json",
        })

    # ── account ───────────────────────────────────────────────────────────
    def get_account(self) -> dict:
        r = self.session.get(f"{self.base_url}/v2/account", timeout=20)
        r.raise_for_status()
        return r.json()
    # ── clock ───────────────────────────────────────────────────────────────────────
    def get_clock(self) -> dict:
        """Returns {'is_open': bool, 'next_open': iso, 'next_close': iso, 'timestamp': iso}."""
        r = self.session.get(f"{self.base_url}/v2/clock", timeout=20)
        r.raise_for_status()
        return r.json()
    # ── positions ─────────────────────────────────────────────────────────
    def list_positions(self) -> list[dict]:
        r = self.session.get(f"{self.base_url}/v2/positions", timeout=20)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    # ── orders ────────────────────────────────────────────────────────────
    def submit_order(
        self,
        symbol: str,
        qty: float | int,
        side: str,                # "buy" or "sell"
        order_type: str = "market",  # "market" | "limit" | "stop" | "stop_limit" | "trailing_stop"
        time_in_force: str = "day",
        limit_price: float | None = None,
        stop_price: float | None = None,
        client_order_id: str | None = None,
        order_class: str | None = None,   # "bracket" | "oco" | "oto" | "simple"
        take_profit: dict | None = None,  # {"limit_price": x}
        stop_loss: dict | None = None,    # {"stop_price": x, "limit_price"?: x}
        trail_price: float | None = None,
        trail_percent: float | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "symbol": symbol.upper(),
            "qty": str(qty),
            "side": side.lower(),
            "type": order_type.lower(),
            "time_in_force": time_in_force.lower(),
        }
        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        if stop_price is not None:
            body["stop_price"] = str(stop_price)
        if trail_price is not None:
            body["trail_price"] = str(trail_price)
        if trail_percent is not None:
            body["trail_percent"] = str(trail_percent)
        if order_class:
            body["order_class"] = order_class
        if take_profit is not None:
            body["take_profit"] = {k: str(v) for k, v in take_profit.items()}
        if stop_loss is not None:
            body["stop_loss"] = {k: str(v) for k, v in stop_loss.items()}
        if client_order_id:
            body["client_order_id"] = client_order_id

        r = self.session.post(
            f"{self.base_url}/v2/orders",
            json=body,
            timeout=30,
        )
        if not r.ok:
            logger.error("Alpaca order failed (%s): %s", r.status_code, r.text)
            r.raise_for_status()
        return r.json()

    def list_orders(self, status: str = "open", limit: int = 200) -> list[dict]:
        """List orders. ``status`` is one of 'open', 'closed', 'all'."""
        r = self.session.get(
            f"{self.base_url}/v2/orders",
            params={"status": status, "limit": limit, "nested": "true"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def get_order(self, order_id: str) -> dict:
        r = self.session.get(f"{self.base_url}/v2/orders/{order_id}", timeout=20)
        r.raise_for_status()
        return r.json()

    def cancel_order(self, order_id: str) -> None:
        """Cancel a single open order (idempotent — a 404/422 is treated as gone)."""
        r = self.session.delete(f"{self.base_url}/v2/orders/{order_id}", timeout=20)
        if r.status_code not in (200, 204, 404, 422):
            logger.error("Alpaca cancel failed (%s): %s", r.status_code, r.text)
            r.raise_for_status()

    def replace_order(
        self,
        order_id: str,
        qty: float | int | None = None,
        stop_price: float | None = None,
        limit_price: float | None = None,
        trail: float | None = None,
        client_order_id: str | None = None,
    ) -> dict:
        """PATCH an open order (used to move/resize a resting stop)."""
        body: dict[str, Any] = {}
        if qty is not None:
            body["qty"] = str(qty)
        if stop_price is not None:
            body["stop_price"] = str(stop_price)
        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        if trail is not None:
            body["trail"] = str(trail)
        if client_order_id:
            body["client_order_id"] = client_order_id
        r = self.session.patch(f"{self.base_url}/v2/orders/{order_id}", json=body, timeout=30)
        if not r.ok:
            logger.error("Alpaca replace failed (%s): %s", r.status_code, r.text)
            r.raise_for_status()
        return r.json()

    # ── market data + calendar ────────────────────────────────────────────
    def get_bars(
        self,
        symbols: list[str] | str,
        timeframe: str = "1Min",
        start: str | None = None,
        end: str | None = None,
        feed: str = "iex",
        limit: int = 10000,
        adjustment: str = "raw",
    ) -> dict[str, list[dict]]:
        """Return ``{SYMBOL: [bar, ...]}`` from the Alpaca market-data API.

        Bars are ``{t, o, h, l, c, v, ...}``. ``feed='iex'`` is the free tier (a
        thin slice of the consolidated tape — see the Flex spec caveats).
        """
        syms = symbols if isinstance(symbols, str) else ",".join(symbols)
        if not syms:
            return {}
        params: dict[str, Any] = {
            "symbols": syms, "timeframe": timeframe, "feed": feed,
            "limit": limit, "adjustment": adjustment,
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        out: dict[str, list[dict]] = {}
        url = f"{_DATA_BASE}/v2/stocks/bars"
        for _ in range(20):  # page through (defensive cap)
            r = self.session.get(url, params=params, timeout=30)
            r.raise_for_status()
            payload = r.json()
            for sym, bars in (payload.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bars)
            token = payload.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        return out

    def get_calendar(self, start: str, end: str) -> list[dict]:
        """Trading calendar rows ``{date, open, close, ...}`` for [start, end]."""
        r = self.session.get(
            f"{self.base_url}/v2/calendar",
            params={"start": start, "end": end},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []
