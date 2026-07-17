"""Session 2026-07-17, Task F — flex order hygiene (root-cause closure of the MU
incident: a repair/entry stop orphaned when the ledger lost its row locked the
position as broker collateral for 8+ sessions, invisible and unmanaged).

- F2: the orphan sweep cancels only this engine's own order family
  (`FLEXC-` current + legacy `flex-`), never a DayTrade Lab (`FLEXD-`) or daily
  executor order.
- F3 (decision G1 = yes): repair/trail stop orders now use `time_in_force="day"`,
  not `"gtc"` — re-placed every in-hours tick by the existing no-naked-long path.

Run: PYTHONPATH=src pytest tests/test_flex_order_hygiene.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flex.handler import (  # noqa: E402
    _apply_repair,
    _is_flex_catalyst_order_id,
    _replace_stop,
    _sweep_orphan_orders,
)


class _FakeClient:
    def __init__(self):
        self.cancelled: list[str] = []
        self.submitted: list[dict] = []

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)

    def submit_order(self, symbol, qty, side, **kwargs):
        rec = {"symbol": symbol, "qty": qty, "side": side, **kwargs}
        self.submitted.append(rec)
        return {"id": "new-order-1", "status": "new", "legs": []}


# --- _is_flex_catalyst_order_id --------------------------------------------------

def test_current_prefix_recognized():
    assert _is_flex_catalyst_order_id("FLEXC-2026-07-17-MU-rep-abc123")


def test_legacy_prefix_recognized():
    assert _is_flex_catalyst_order_id("flex-2026-07-07-MU-rep-302e8f")


def test_daytrade_lab_prefix_excluded():
    assert not _is_flex_catalyst_order_id("FLEXD-2026-07-17-ABCD-e1-abc123")


def test_daily_executor_id_excluded():
    assert not _is_flex_catalyst_order_id("2026-07-17-T-20260717-001")


def test_empty_or_none_excluded():
    assert not _is_flex_catalyst_order_id("")
    assert not _is_flex_catalyst_order_id(None)


# --- F2: _sweep_orphan_orders -----------------------------------------------------

def test_sweep_cancels_current_prefix_orphan():
    client = _FakeClient()
    decisions: dict = {}
    orphans = [{"symbol": "MU", "id": "order-1", "client_order_id": "FLEXC-2026-07-17-MU-rep-abc"}]
    _sweep_orphan_orders(client, orphans, decisions)
    assert client.cancelled == ["order-1"]
    assert decisions["orphan_orders_cancelled"][0]["symbol"] == "MU"


def test_sweep_cancels_legacy_prefix_orphan():
    """The exact real-world MU order id from the 2026-07-08 incident."""
    client = _FakeClient()
    decisions: dict = {}
    orphans = [{"symbol": "MU", "id": "mu-stop-1",
                "client_order_id": "flex-2026-07-07-MU-rep-302e8f"}]
    _sweep_orphan_orders(client, orphans, decisions)
    assert client.cancelled == ["mu-stop-1"]


def test_sweep_never_touches_daytrade_lab_order():
    client = _FakeClient()
    decisions: dict = {}
    orphans = [{"symbol": "ABCD", "id": "lab-order-1",
                "client_order_id": "FLEXD-2026-07-17-ABCD-e1-xyz"}]
    _sweep_orphan_orders(client, orphans, decisions)
    assert client.cancelled == []
    assert "orphan_orders_cancelled" not in decisions


def test_sweep_never_touches_daily_executor_order():
    client = _FakeClient()
    decisions: dict = {}
    orphans = [{"symbol": "SPY", "id": "exec-order-1",
                "client_order_id": "2026-07-17-T-20260717-005"}]
    _sweep_orphan_orders(client, orphans, decisions)
    assert client.cancelled == []


def test_sweep_is_best_effort_on_cancel_failure():
    class _FailingClient(_FakeClient):
        def cancel_order(self, order_id):
            raise RuntimeError("network error")

    client = _FailingClient()
    decisions: dict = {}
    orphans = [{"symbol": "MU", "id": "order-1", "client_order_id": "FLEXC-x"}]
    _sweep_orphan_orders(client, orphans, decisions)   # must not raise
    assert "orphan_orders_cancelled" not in decisions


def test_sweep_skips_orphan_with_no_order_id():
    client = _FakeClient()
    decisions: dict = {}
    orphans = [{"symbol": "MU", "client_order_id": "FLEXC-x"}]   # no "id"
    _sweep_orphan_orders(client, orphans, decisions)
    assert client.cancelled == []


# --- F3: DAY not GTC on repair/trail stops ---------------------------------------

def test_repair_stop_uses_day_time_in_force():
    client = _FakeClient()
    rep = {"action": "place_missing_stop", "symbol": "NVDA", "stop_price": 90.0,
           "qty": 10, "cancel_order_ids": []}
    _apply_repair(client, rep, "2026-07-17", {"orders_issued": []}, [])
    assert client.submitted[0]["time_in_force"] == "day"


def test_trail_stop_replacement_uses_day_time_in_force():
    client = _FakeClient()
    entry = {"qty_current": 10, "current_stop": 95.0, "order_ids": []}
    _replace_stop(client, entry, 97.0, "2026-07-17", "NVDA",
                  {"orders_suppressed": []}, [])
    assert client.submitted[0]["time_in_force"] == "day"
