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
    new_ledger, exits, repairs, _orphans = reconcile_ledger(ledger, [{"symbol": "NVDA", "qty": 10}], [])
    assert "NVDA" in new_ledger
    assert exits == []
    assert repairs[0]["action"] == "place_missing_stop"
    assert repairs[0]["stop_price"] == 95.0
    assert repairs[0]["qty"] == 10


def test_position_closed_records_exit_and_clears_row():
    ledger = {"NVDA": _entry()}
    new_ledger, exits, repairs, _orphans = reconcile_ledger(ledger, [], [_stop_order()])
    assert "NVDA" not in new_ledger
    assert exits[0]["symbol"] == "NVDA"
    assert any(r["action"] == "record_filled_stop" for r in repairs)


def test_partial_fill_resizes_qty_current():
    ledger = {"NVDA": _entry()}
    new_ledger, exits, repairs, _orphans = reconcile_ledger(
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
    new_ledger, exits, repairs, _orphans = reconcile_ledger(ledger, [], [])
    assert "NVDA" not in new_ledger
    assert exits[0]["entry"]["qty_current"] == 5


def test_phantom_order_cleared():
    ledger = {"NVDA": _entry(order_ids=["X"])}
    new_ledger, exits, repairs, _orphans = reconcile_ledger(
        ledger, [{"symbol": "NVDA", "qty": 10}], [_stop_order(oid="Y")],
    )
    assert any(r["action"] == "clear_phantom_order" and r["order_id"] == "X" for r in repairs)
    assert "X" not in new_ledger["NVDA"]["order_ids"]


def test_place_missing_stop_ordered_before_resize():
    # Wrong-sized stop (qty 10 vs held 5) → resize + replace; place_missing_stop first.
    ledger = {"NVDA": _entry()}
    _, _, repairs, _orphans = reconcile_ledger(
        ledger, [{"symbol": "NVDA", "qty": 5}], [_stop_order(qty=10)],
    )
    actions = [r["action"] for r in repairs]
    assert actions.index("place_missing_stop") < actions.index("resize_to_partial_fill")


# --- Task F1 (session 2026-07-17): orphan_orders ---------------------------------

def test_order_for_symbol_outside_the_ledger_is_an_orphan():
    """The MU-incident shape: a resting stop for a symbol the ledger doesn't (or
    no longer) track at all — reconcile_ledger's main loop never even looks at
    this symbol, so without orphan_orders it would be invisible."""
    _, _, _, orphans = reconcile_ledger(
        {}, [], [_stop_order(symbol="MU", qty=2, oid="mu-stop-1")],
    )
    assert len(orphans) == 1
    assert orphans[0]["symbol"] == "MU"
    assert orphans[0]["id"] == "mu-stop-1"


def test_order_for_a_currently_managed_symbol_is_not_an_orphan():
    ledger = {"NVDA": _entry()}
    _, _, _, orphans = reconcile_ledger(
        ledger, [{"symbol": "NVDA", "qty": 10}], [_stop_order(symbol="NVDA")],
    )
    assert orphans == []


def test_leftover_order_on_a_just_closed_symbol_is_an_orphan():
    """Position closed at the broker this tick (ledger row dropped) but a second
    resting order for the SAME symbol never got canceled — still an orphan going
    forward, since nothing manages that symbol anymore."""
    ledger = {"NVDA": _entry()}
    _, exits, _, orphans = reconcile_ledger(
        ledger, [], [_stop_order(symbol="NVDA", oid="leftover")],
    )
    assert exits and exits[0]["symbol"] == "NVDA"   # position closed, exit recorded
    assert any(o["id"] == "leftover" for o in orphans)


def test_orphan_entry_carries_client_order_id_and_stop_price():
    order = {
        "id": "abc123", "symbol": "MU", "side": "sell", "type": "stop",
        "stop_price": "103.50", "client_order_id": "flex-2026-07-07-MU-rep-302e8f",
        "submitted_at": "2026-07-08T14:00:00Z",
    }
    _, _, _, orphans = reconcile_ledger({}, [], [order])
    assert orphans[0]["client_order_id"] == "flex-2026-07-07-MU-rep-302e8f"
    assert orphans[0]["stop_price"] == "103.50"
    assert orphans[0]["submitted_at"] == "2026-07-08T14:00:00Z"
