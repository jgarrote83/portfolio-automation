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
        order_type: str = "market",  # "market" | "limit" | "stop" | "stop_limit"
        time_in_force: str = "day",
        limit_price: float | None = None,
        stop_price: float | None = None,
        client_order_id: str | None = None,
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
            params={"status": status, "limit": limit},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []
