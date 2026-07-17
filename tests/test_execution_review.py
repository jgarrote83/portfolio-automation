"""Unit tests for `_build_execution_review` (session 2026-07-15, Task A1).

Response to the 2026-07-14/07-15 MU incident: a submitted-but-403'd sell was
invisible to the next day's analyzer run, which silently re-proposed the same
trade with no trace of the prior failure. This block reads back the prior day's
`daily-executions/{date}.json` and reconciles each order's terminal Alpaca state.

Run: PYTHONPATH=src pytest tests/test_execution_review.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import collector.handler as ch  # noqa: E402
from collector.handler import _build_execution_review  # noqa: E402


class _FakeAlpacaOrders:
    def __init__(self, orders_by_id: dict):
        self._orders = orders_by_id

    def get_order(self, order_id):
        if order_id not in self._orders:
            raise RuntimeError(f"404: no such order {order_id}")
        return self._orders[order_id]


def test_no_prior_executions_within_window(monkeypatch):
    monkeypatch.setattr(ch, "read_executions", lambda d: None)
    result = _build_execution_review({"AlpacaApiKey": "k", "AlpacaApiSecret": "s"}, "2026-07-16")
    assert result["available"] is False
    assert "no prior" in result["reason"]


def test_missing_credentials(monkeypatch):
    monkeypatch.setattr(ch, "read_executions", lambda d: {"executions": []})
    result = _build_execution_review({}, "2026-07-16")
    assert result == {"available": False, "reason": "Alpaca credentials missing"}


def test_mu_style_403_never_reached_alpaca_counts_as_failed(monkeypatch):
    """The exact 07-14/07-15 shape: an execution row with status='error' and no
    alpaca_order_id (never got an order id — the submission itself 403'd)."""
    def _read(d):
        if d == "2026-07-15":
            return {
                "executions": [
                    {"id": "T-1", "symbol": "MU", "side": "sell", "qty": 2,
                     "status": "error", "error": "403 Client Error: Forbidden"},
                ],
            }
        return None

    monkeypatch.setattr(ch, "read_executions", _read)
    monkeypatch.setattr(ch, "AlpacaClient", lambda api_key, api_secret: _FakeAlpacaOrders({}))
    result = _build_execution_review(
        {"AlpacaApiKey": "k", "AlpacaApiSecret": "s"}, "2026-07-16",
    )
    assert result["available"] is True
    assert result["date"] == "2026-07-15"
    assert result["submitted"] == 1
    assert result["filled"] == 0
    assert len(result["failed"]) == 1
    assert result["failed"][0]["symbol"] == "MU"
    assert "403" in result["failed"][0]["error"]
    assert result["unfilled"] == []


def test_classifies_filled_failed_and_unfilled(monkeypatch):
    def _read(d):
        if d == "2026-07-15":
            return {
                "executions": [
                    {"id": "T-1", "symbol": "MCK", "side": "sell", "qty": 2,
                     "status": "submitted", "alpaca_order_id": "order-filled"},
                    {"id": "T-2", "symbol": "GOOGL", "side": "sell", "qty": 2,
                     "status": "submitted", "alpaca_order_id": "order-canceled"},
                    {"id": "T-3", "symbol": "KMLM", "side": "buy", "qty": 40,
                     "status": "submitted", "alpaca_order_id": "order-resting"},
                ],
            }
        return None

    fake = _FakeAlpacaOrders({
        "order-filled": {"status": "filled", "filled_qty": "2"},
        "order-canceled": {"status": "canceled", "filled_qty": "0"},
        "order-resting": {"status": "new", "filled_qty": "0"},
    })
    monkeypatch.setattr(ch, "read_executions", _read)
    monkeypatch.setattr(ch, "AlpacaClient", lambda api_key, api_secret: fake)

    result = _build_execution_review(
        {"AlpacaApiKey": "k", "AlpacaApiSecret": "s"}, "2026-07-16",
    )
    assert result["submitted"] == 3
    assert result["filled"] == 1
    assert [f["symbol"] for f in result["failed"]] == ["GOOGL"]
    assert result["failed"][0]["status"] == "canceled"
    assert [u["symbol"] for u in result["unfilled"]] == ["KMLM"]
    assert result["unfilled"][0]["status"] == "new"


def test_looks_back_multiple_days_when_yesterday_missing(monkeypatch):
    """A weekend/holiday gap: the most recent daily-executions file within the
    lookback window is used, not just yesterday's."""
    def _read(d):
        if d == "2026-07-14":
            return {"executions": []}
        return None

    monkeypatch.setattr(ch, "read_executions", _read)
    monkeypatch.setattr(ch, "AlpacaClient", lambda api_key, api_secret: _FakeAlpacaOrders({}))
    result = _build_execution_review(
        {"AlpacaApiKey": "k", "AlpacaApiSecret": "s"}, "2026-07-16",
    )
    assert result["available"] is True
    assert result["date"] == "2026-07-14"
    assert result["submitted"] == 0
