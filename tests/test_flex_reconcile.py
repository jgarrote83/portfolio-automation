"""Tests for reconcile_ledger — STEP 0 broker-truth reconciliation.

Run: PYTHONPATH=src pytest tests/test_flex_reconcile.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flex.reconcile import reconcile_ledger  # noqa: E402


def _entry(**kw):
    base = {
        "symbol": "NVDA", "entry_price": 100.0, "initial_stop": 90.0,
        "current_stop": 95.0, "qty_initial": 10, "qty_current": 10, "order_ids": [],
    }
    base.update(kw)
    return base


def _stop_order(symbol="NVDA", qty=10, oid="stop1"):
    return {"id": oid, "symbol": symbol, "side": "sell", "type": "stop", "qty": qty}


def test_no_naked_long_places_missing_stop_first():
    ledger = {"NVDA": _entry()}
    new_ledger, exits, repairs = reconcile_ledger(ledger, [{"symbol": "NVDA", "qty": 10}], [])
    assert "NVDA" in new_ledger
    assert exits == []
    assert repairs[0]["action"] == "place_missing_stop"
    assert repairs[0]["stop_price"] == 95.0
    assert repairs[0]["qty"] == 10


def test_position_closed_records_exit_and_clears_row():
    ledger = {"NVDA": _entry()}
    new_ledger, exits, repairs = reconcile_ledger(ledger, [], [_stop_order()])
    assert "NVDA" not in new_ledger
    assert exits[0]["symbol"] == "NVDA"
    assert any(r["action"] == "record_filled_stop" for r in repairs)


def test_partial_fill_resizes_qty_current():
    ledger = {"NVDA": _entry()}
    new_ledger, exits, repairs = reconcile_ledger(
        ledger, [{"symbol": "NVDA", "qty": 5}], [_stop_order(qty=5)],
    )
    assert new_ledger["NVDA"]["qty_current"] == 5
    assert any(r["action"] == "resize_to_partial_fill" for r in repairs)
    # Stop is correctly sized (qty 5) → no missing-stop repair.
    assert not any(r["action"] == "place_missing_stop" for r in repairs)


def test_partial_scale_out_then_stop_records_exit_at_qty_current():
    # After a scale-out the ledger qty_current is 5; the stop then fills (position
    # gone) → the recorded exit must carry qty_current 5, not qty_initial 10.
    ledger = {"NVDA": _entry(qty_current=5, scaled_out=True)}
    new_ledger, exits, repairs = reconcile_ledger(ledger, [], [])
    assert "NVDA" not in new_ledger
    assert exits[0]["entry"]["qty_current"] == 5


def test_phantom_order_cleared():
    ledger = {"NVDA": _entry(order_ids=["X"])}
    new_ledger, exits, repairs = reconcile_ledger(
        ledger, [{"symbol": "NVDA", "qty": 10}], [_stop_order(oid="Y")],
    )
    assert any(r["action"] == "clear_phantom_order" and r["order_id"] == "X" for r in repairs)
    assert "X" not in new_ledger["NVDA"]["order_ids"]


def test_place_missing_stop_ordered_before_resize():
    # Wrong-sized stop (qty 10 vs held 5) → resize + replace; place_missing_stop first.
    ledger = {"NVDA": _entry()}
    _, _, repairs = reconcile_ledger(
        ledger, [{"symbol": "NVDA", "qty": 5}], [_stop_order(qty=10)],
    )
    actions = [r["action"] for r in repairs]
    assert actions.index("place_missing_stop") < actions.index("resize_to_partial_fill")
