"""Regression test for the 2026-07-14/07-15 MU sell failure (session A0/A3).

Root cause (confirmed against the live Alpaca paper book 2026-07-15): a stale
resting GTC stop order (`client_order_id="flex-2026-07-07-MU-rep-302e8f"`,
submitted 2026-07-08, never filled/canceled) locked both MU shares as order
collateral (`qty_available: 0`). Every subsequent daily-executor attempt to sell
those same 2 shares hit a 403 Forbidden from Alpaca two days running, and nothing
in the executor detected or cleared the stale order — it just logged an opaque
error and moved on, forever.

Run: PYTHONPATH=src pytest tests/test_executor_order_conflict.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import executor.handler as ex  # noqa: E402


class _StaleOrderAlpacaClient:
    """Fake Alpaca client reproducing the exact collision: one pre-existing OPEN
    order on MU locks the shares; a second order against the same symbol 403s
    until that stale order is canceled."""

    def __init__(self):
        self.cancelled: list[str] = []
        self.submitted: list[tuple] = []
        self._open_orders = [
            {"id": "stale-order-1", "symbol": "MU", "side": "sell",
             "order_type": "stop", "type": "stop", "stop_price": "628.48"},
        ]

    def list_positions(self):
        return [{"symbol": "MU", "qty": "2"}]

    def get_clock(self):
        return {"is_open": True}

    def list_orders(self, status="open", limit=200):
        if status not in ("open", "all"):
            return []
        return list(self._open_orders)

    def cancel_order(self, order_id):
        before = len(self._open_orders)
        self._open_orders = [o for o in self._open_orders if o["id"] != order_id]
        if len(self._open_orders) < before:
            self.cancelled.append(order_id)

    def submit_order(self, symbol, qty, side, **kwargs):
        if any(o["symbol"] == symbol.upper() for o in self._open_orders):
            raise RuntimeError(
                "403 Client Error: Forbidden for url: "
                "https://paper-api.alpaca.markets/v2/orders"
            )
        self.submitted.append((symbol, qty, side))
        return {
            "id": "new-order-1", "status": "pending_new",
            "client_order_id": kwargs.get("client_order_id"),
        }


def _wire(monkeypatch, fake_client):
    monkeypatch.setenv("AUTO_EXECUTE_ENABLED", "true")
    monkeypatch.setattr(ex, "read_executions", lambda d: None)
    monkeypatch.setattr(ex, "read_trades", lambda d: {
        "trades": [{
            "id": "T-20260714-001", "side": "sell", "symbol": "MU", "quantity": 2,
            "order_type": "market", "time_in_force": "day",
            "validation": {"status": "passed", "reasons": []},
        }],
    })
    monkeypatch.setattr(ex, "load_secrets", lambda: {
        "AlpacaApiKey": "k", "AlpacaApiSecret": "s",
    })
    monkeypatch.setattr(ex, "AlpacaClient", lambda api_key, api_secret: fake_client)
    monkeypatch.setattr(ex, "write_executions", lambda d, r: None)
    monkeypatch.setattr(ex, "upsert_entity", lambda *a, **k: None)


def test_stale_open_order_no_longer_blocks_the_days_sell(monkeypatch):
    """The day's validated sell must supersede a stale resting order on the same
    symbol: the executor should cancel it and successfully submit, not 403."""
    fake = _StaleOrderAlpacaClient()
    _wire(monkeypatch, fake)

    result = ex.execute_approvals("2026-07-14", force=True, auto=True)

    row = result["executions"][0]
    assert row["status"] == "submitted", (
        f"expected the sell to succeed after clearing the stale order, got: {row}"
    )
    assert fake.cancelled == ["stale-order-1"], (
        "expected the conflicting stale order to be canceled before resubmission"
    )
    assert fake.submitted == [("MU", 2.0, "sell")] or fake.submitted == [("MU", 2, "sell")]
