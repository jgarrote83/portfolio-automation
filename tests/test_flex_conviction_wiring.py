"""Task B/E (2026-08-14 flex-conviction-path cycle) — the flex/handler.py
orchestration wiring for the conviction path: reading today's active
nominations from the collector's same-day `flex_conviction` snapshot block,
the release-driven exit (a held conviction position whose hysteresis has
released bypasses build_flex_exit_state's mechanical evaluation entirely),
and B5's cash-figures read.

Run: PYTHONPATH=src pytest tests/test_flex_conviction_wiring.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flex.handler import (  # noqa: E402
    _act_on_release_exit,
    _cash_figures,
    _flex_conviction_candidates,
)


class _FakeClient:
    def __init__(self, cash=100_000.0):
        self._cash = cash
        self.cancelled: list[str] = []
        self.submitted: list[dict] = []
        self.orders_by_id: dict = {}

    def get_account(self):
        return {"cash": self._cash}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)

    def submit_order(self, symbol, qty, side, **kwargs):
        oid = f"order-{len(self.submitted) + 1}"
        rec = {"id": oid, "symbol": symbol, "qty": qty, "side": side, "status": "filled",
               "filled_avg_price": "100.0", "legs": [], **kwargs}
        self.submitted.append(rec)
        self.orders_by_id[oid] = rec
        return rec

    def get_order(self, order_id):
        return self.orders_by_id.get(order_id, {})


# --- _flex_conviction_candidates ---------------------------------------------

def test_disabled_block_yields_empty():
    snapshot = {"flex_conviction": {"available": True, "enabled": False}}
    assert _flex_conviction_candidates(snapshot) == {}


def test_missing_block_yields_empty():
    assert _flex_conviction_candidates({}) == {}


def test_active_candidates_keyed_by_symbol():
    snapshot = {"flex_conviction": {"enabled": True, "active": [
        {"symbol": "avgo", "conviction": "high", "applied_size_mult": 0.70},
    ]}}
    out = _flex_conviction_candidates(snapshot)
    assert set(out) == {"AVGO"}
    assert out["AVGO"]["applied_size_mult"] == 0.70


def test_daytrade_excluded_symbol_dropped():
    snapshot = {"flex_conviction": {"enabled": True, "active": [
        {"symbol": "AVGO", "applied_size_mult": 0.70},
    ]}}
    out = _flex_conviction_candidates(snapshot, exclude=frozenset({"AVGO"}))
    assert out == {}


def test_separation_set_member_dropped():
    # QQQ is a core pool member (semis/tech role) -- must never be flex-nominatable
    # regardless of what the snapshot's flex_conviction block claims.
    snapshot = {"flex_conviction": {"enabled": True, "active": [
        {"symbol": "QQQ", "applied_size_mult": 0.70},
    ]}}
    out = _flex_conviction_candidates(snapshot)
    assert "QQQ" not in out


def test_malformed_entries_skipped_not_crashed():
    snapshot = {"flex_conviction": {"enabled": True, "active": [
        "not-a-dict", {"applied_size_mult": 0.70},  # no symbol
        {"symbol": "AVGO", "applied_size_mult": 0.70},
    ]}}
    out = _flex_conviction_candidates(snapshot)
    assert set(out) == {"AVGO"}


# --- _cash_figures ------------------------------------------------------------

def test_cash_figures_reads_cash_and_sgov_from_positions():
    client = _FakeClient(cash=12_345.0)
    positions = [
        {"symbol": "SGOV", "market_value": "50000.0"},
        {"symbol": "QQQ", "market_value": "200000.0"},
    ]
    cash, sgov = _cash_figures(client, positions)
    assert cash == 12_345.0
    assert sgov == 50_000.0


def test_cash_figures_no_sgov_position_is_zero():
    client = _FakeClient(cash=1000.0)
    cash, sgov = _cash_figures(client, [{"symbol": "QQQ", "market_value": "1.0"}])
    assert sgov == 0.0


def test_cash_figures_account_read_failure_degrades_to_zero():
    class _FailingClient(_FakeClient):
        def get_account(self):
            raise RuntimeError("network error")

    cash, sgov = _cash_figures(_FailingClient(), [])
    assert cash == 0.0
    assert sgov == 0.0


# --- _act_on_release_exit -----------------------------------------------------

def test_release_exit_sells_full_qty_and_clears_ledger():
    client = _FakeClient()
    ledger = {"AVGO": {
        "symbol": "AVGO", "qty_current": 10, "entry_price": 90.0, "initial_stop": 82.0,
        "trade_id": "FLEX-2026-08-14-AVGO-abc", "order_ids": [],
    }}
    decisions = {"orders_issued": [], "orders_suppressed": []}
    _act_on_release_exit(client, ledger, "AVGO", "2026-08-14", decisions, [])
    assert client.submitted[0]["side"] == "sell"
    assert client.submitted[0]["qty"] == 10
    assert "AVGO" not in ledger


def test_release_exit_missing_ledger_entry_is_a_no_op():
    client = _FakeClient()
    ledger: dict = {}
    decisions = {"orders_issued": [], "orders_suppressed": []}
    _act_on_release_exit(client, ledger, "AVGO", "2026-08-14", decisions, [])
    assert client.submitted == []


def test_release_exit_submit_failure_is_non_fatal():
    class _FailingClient(_FakeClient):
        def submit_order(self, *a, **kw):
            raise RuntimeError("network error")

    client = _FailingClient()
    ledger = {"AVGO": {
        "symbol": "AVGO", "qty_current": 10, "entry_price": 90.0, "initial_stop": 82.0,
        "trade_id": "FLEX-2026-08-14-AVGO-abc", "order_ids": [],
    }}
    decisions = {"orders_issued": [], "orders_suppressed": []}
    _act_on_release_exit(client, ledger, "AVGO", "2026-08-14", decisions, [])  # must not raise
    assert decisions["orders_suppressed"]
    assert "AVGO" in ledger  # not popped on failure -- next tick retries
