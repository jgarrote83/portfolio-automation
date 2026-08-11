"""Flex Sleeve Performance Ledger (2026-08-10) — every close path routes
through the same `_finalize_closed_trade` funnel in src/flex/handler.py.
Covers all four paths named in the task: time_stop, scale_out (a partial
fill, not itself a close), broker-stop-fill (the reconcile path -- how most
losers actually exit), and the entry-side-failure case (no confirmed broker
buy fill -- nothing is recorded, on purpose).

Run: PYTHONPATH=src pytest tests/test_flex_close_paths.py
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import flex.handler as handler  # noqa: E402
import flex.trades as trades_mod  # noqa: E402
from flex.ledger import new_entry  # noqa: E402


@pytest.fixture
def blob_store(monkeypatch):
    store: dict = {}

    def _read(container, name):
        return store.get((container, name))

    def _write(container, name, obj):
        store[(container, name)] = obj

    monkeypatch.setattr(trades_mod, "read_json_blob", _read)
    monkeypatch.setattr(trades_mod, "write_json_blob", _write)
    return store


class _StubAlpaca:
    """Minimal Alpaca test double -- just enough surface for the closing
    paths under test. `order_id_seq` gives each submit_order call a distinct
    id; `fills` maps order_id -> filled_avg_price for get_order; `activities`
    is the canned FILL history get_activities returns."""

    def __init__(self, fills=None, activities=None):
        self.fills = fills or {}
        self.activities = activities or []
        self.submitted = []
        self.cancelled = []
        self._n = 0

    def submit_order(self, symbol, qty, side, **kwargs):
        self._n += 1
        oid = f"ord-{self._n}"
        self.submitted.append({"id": oid, "symbol": symbol, "qty": qty, "side": side, **kwargs})
        return {"id": oid, "status": "accepted", "qty": str(qty)}

    def get_order(self, order_id):
        price = self.fills.get(order_id)
        return {"id": order_id, "status": "filled" if price is not None else "new",
                "filled_avg_price": price}

    def get_activities(self, activity_type="FILL", after=None, until=None, page_size=100):
        return self.activities

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)


def _open_ledger_entry(**overrides):
    e = new_entry("XYZ", 100.0, "2026-08-10", 96.0, 100, order_ids=["ord-entry"],
                  trade_id="FLEX-2026-08-10-XYZ-test1234",
                  catalyst_score=0.72, score_components={"momentum": 0.8},
                  nomination_thesis="Contract win catalyst.")
    e.update(overrides)
    return e


# --- Path 1: time_stop (engine-initiated full close) -------------------------

def test_time_stop_records_closed_trade_and_pops_ledger(blob_store, monkeypatch):
    entry = _open_ledger_entry()
    ledger = {"XYZ": entry}
    client = _StubAlpaca(
        activities=[
            {"symbol": "XYZ", "side": "buy", "qty": "100", "price": "100.00",
             "transaction_time": "2026-08-10T14:31:00Z"},
        ],
        fills={},  # get_order for the time_stop sell never confirms synchronously
    )
    # Patch get_order for whatever order id the time_stop sell gets (2nd submit).
    st = {"next_action": "time_stop", "scale_out_qty": 100}
    monkeypatch.setattr(handler, "_record_trade_history", lambda *a, **k: None)
    handler._act_on_exit(client, ledger, "XYZ", st, "2026-08-14", {"orders_suppressed": [], "orders_issued": []}, [])
    assert "XYZ" not in ledger
    trades = trades_mod.read_closed_trades()
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "time_stop"
    assert trades[0]["trade_id"] == "FLEX-2026-08-10-XYZ-test1234"
    assert trades[0]["catalyst_score"] == 0.72


def test_time_stop_pnl_present_when_broker_confirms_both_fills(blob_store, monkeypatch):
    entry = _open_ledger_entry()
    ledger = {"XYZ": entry}
    client = _StubAlpaca(
        activities=[
            {"symbol": "XYZ", "side": "buy", "qty": "100", "price": "100.00",
             "transaction_time": "2026-08-10T14:31:00Z"},
            {"symbol": "XYZ", "side": "sell", "qty": "100", "price": "94.00",
             "transaction_time": "2026-08-14T15:00:00Z"},
        ],
    )
    st = {"next_action": "time_stop", "scale_out_qty": 100}
    monkeypatch.setattr(handler, "_record_trade_history", lambda *a, **k: None)
    handler._act_on_exit(client, ledger, "XYZ", st, "2026-08-14", {"orders_suppressed": [], "orders_issued": []}, [])
    trades = trades_mod.read_closed_trades()
    assert trades[0]["pnl_usd"] == -600.0  # (94-100)*100


# --- write-failure visibility (PR #37 pre-merge correction, Task 2) ---------

def test_time_stop_write_failure_still_pops_ledger_and_records_history(blob_store, monkeypatch):
    """A record_closed_trade failure must not block the order action (the
    ledger row is popped, TradeHistory is still written) but must surface
    exactly once in decisions["closed_trade_write_failures"] -- visibility,
    not recovery."""
    entry = _open_ledger_entry()
    ledger = {"XYZ": entry}
    client = _StubAlpaca(activities=[
        {"symbol": "XYZ", "side": "buy", "qty": "100", "price": "100.00",
         "transaction_time": "2026-08-10T14:31:00Z"},
        {"symbol": "XYZ", "side": "sell", "qty": "100", "price": "94.00",
         "transaction_time": "2026-08-14T15:00:00Z"},
    ])

    def _boom(trade):
        raise RuntimeError("blob write failed")
    monkeypatch.setattr(trades_mod, "record_closed_trade", _boom)

    history_calls = []
    monkeypatch.setattr(handler, "_record_trade_history",
                        lambda *a, **k: history_calls.append((a, k)))

    st = {"next_action": "time_stop", "scale_out_qty": 100}
    decisions = {"orders_suppressed": [], "orders_issued": []}
    handler._act_on_exit(client, ledger, "XYZ", st, "2026-08-14", decisions, [])

    assert "XYZ" not in ledger  # order action unblocked -- still popped
    assert len(history_calls) == 1  # TradeHistory still recorded
    failures = decisions.get("closed_trade_write_failures")
    assert failures is not None and len(failures) == 1
    assert failures[0]["symbol"] == "XYZ"
    assert failures[0]["exit_reason"] == "time_stop"
    assert "blob write failed" in failures[0]["error"]
    # And the failure genuinely means nothing landed -- no fabricated record.
    assert trades_mod.read_closed_trades() == []


def test_broker_stop_fill_write_failure_surfaces_in_decisions(blob_store, monkeypatch):
    entry = _open_ledger_entry()
    client = _StubAlpaca(activities=[
        {"symbol": "XYZ", "side": "buy", "qty": "100", "price": "100.00",
         "transaction_time": "2026-08-10T14:31:00Z"},
        {"symbol": "XYZ", "side": "sell", "qty": "100", "price": "96.00",
         "transaction_time": "2026-08-13T10:05:00Z"},
    ])

    def _boom(trade):
        raise RuntimeError("blob write failed")
    monkeypatch.setattr(trades_mod, "record_closed_trade", _boom)
    monkeypatch.setattr(handler, "_record_trade_history", lambda *a, **k: None)

    decisions = {"reconcile": {}}
    exits_to_record = [{"symbol": "XYZ", "entry": entry, "reason": "closed_at_broker"}]
    for ex in exits_to_record:
        trade = None
        try:
            trade = handler._finalize_closed_trade(
                client, ex["entry"], ex["symbol"], "stop_fill", "2026-08-13")
        except Exception as cte:  # noqa: BLE001
            handler._record_closed_trade_write_failure(decisions, ex["symbol"], "stop_fill", cte)
        handler._record_trade_history(
            "2026-08-13", ex["symbol"], "sell", 100, status="closed_at_broker",
            extra=handler._trade_history_extra(trade))

    failures = decisions.get("closed_trade_write_failures")
    assert failures is not None and len(failures) == 1
    assert failures[0]["exit_reason"] == "stop_fill"


# --- Path 2: scale_out (partial realization -- NOT a close) -----------------

def test_scale_out_appends_fill_without_closing_position(blob_store, monkeypatch):
    entry = _open_ledger_entry()
    ledger = {"XYZ": entry}
    client = _StubAlpaca(fills={"ord-1": 110.0})
    st = {"next_action": "scale_out", "scale_out_qty": 50, "target_stop": 100.0}
    monkeypatch.setattr(handler, "_record_trade_history", lambda *a, **k: None)
    handler._act_on_exit(client, ledger, "XYZ", st, "2026-08-12", {"orders_suppressed": [], "orders_issued": []}, [])
    assert "XYZ" in ledger  # still open
    assert ledger["XYZ"]["qty_current"] == 50
    assert ledger["XYZ"]["scaled_out"] is True
    assert ledger["XYZ"]["fills"] == [
        {"date": "2026-08-12", "qty": 50, "price": 110.0, "reason": "scale_out"}
    ]
    assert trades_mod.read_closed_trades() == []  # no closed-trade record yet


def test_scale_out_records_null_price_when_fill_unconfirmed(blob_store, monkeypatch):
    entry = _open_ledger_entry()
    ledger = {"XYZ": entry}
    client = _StubAlpaca(fills={})  # get_order never confirms a fill
    st = {"next_action": "scale_out", "scale_out_qty": 50, "target_stop": 100.0}
    monkeypatch.setattr(handler, "_record_trade_history", lambda *a, **k: None)
    handler._act_on_exit(client, ledger, "XYZ", st, "2026-08-12", {"orders_suppressed": [], "orders_issued": []}, [])
    assert ledger["XYZ"]["fills"][0]["price"] is None  # never fabricated


# --- Path 3: broker stop fill (reconcile's exits_to_record) -----------------

def test_broker_stop_fill_reconstructs_trade_from_activities_alone(blob_store):
    entry = _open_ledger_entry()
    client = _StubAlpaca(activities=[
        {"symbol": "XYZ", "side": "buy", "qty": "100", "price": "100.00",
         "transaction_time": "2026-08-10T14:31:00Z"},
        {"symbol": "XYZ", "side": "sell", "qty": "100", "price": "96.00",
         "transaction_time": "2026-08-13T10:05:00Z"},
    ])
    trade = handler._finalize_closed_trade(client, entry, "XYZ", "stop_fill", "2026-08-13")
    assert trade is not None
    assert trade["exit_reason"] == "stop_fill"
    assert trade["pnl_usd"] == -400.0  # (96-100)*100
    assert trade["fills"] == [
        {"date": "2026-08-13", "qty": 100.0, "price": 96.0, "reason": "stop_fill"}
    ]


def test_broker_stop_fill_after_a_prior_scale_out_merges_both(blob_store):
    entry = _open_ledger_entry(qty_current=50, scaled_out=True, fills=[
        {"date": "2026-08-12", "qty": 50, "price": 110.0, "reason": "scale_out"},
    ])
    client = _StubAlpaca(activities=[
        {"symbol": "XYZ", "side": "buy", "qty": "100", "price": "100.00",
         "transaction_time": "2026-08-10T14:31:00Z"},
        {"symbol": "XYZ", "side": "sell", "qty": "50", "price": "110.00",
         "transaction_time": "2026-08-12T13:00:00Z"},
        {"symbol": "XYZ", "side": "sell", "qty": "50", "price": "100.00",
         "transaction_time": "2026-08-14T15:00:00Z"},  # breakeven stop on the runner
    ])
    trade = handler._finalize_closed_trade(client, entry, "XYZ", "stop_fill", "2026-08-14")
    # proceeds = 50*110 + 50*100 = 5500+5000=10500; cost=100*100=10000; pnl=500
    assert trade["pnl_usd"] == 500.0
    assert len(trade["fills"]) == 2
    assert trade["fills"][1]["reason"] == "stop_fill"


# --- Path 4: entry-side failure -- no confirmed buy fill ---------------------

def test_entry_never_filled_records_nothing(blob_store):
    entry = _open_ledger_entry()
    client = _StubAlpaca(activities=[])  # no buy fill EVER confirmed at the broker
    trade = handler._finalize_closed_trade(client, entry, "XYZ", "stop_fill", "2026-08-11")
    assert trade is None
    assert trades_mod.read_closed_trades() == []  # nothing fabricated


# --- idempotency across the reconcile-driven path ---------------------------

def test_finalize_closed_trade_is_idempotent_on_repeat_call(blob_store):
    entry = _open_ledger_entry()
    client = _StubAlpaca(activities=[
        {"symbol": "XYZ", "side": "buy", "qty": "100", "price": "100.00",
         "transaction_time": "2026-08-10T14:31:00Z"},
        {"symbol": "XYZ", "side": "sell", "qty": "100", "price": "96.00",
         "transaction_time": "2026-08-13T10:05:00Z"},
    ])
    handler._finalize_closed_trade(client, entry, "XYZ", "stop_fill", "2026-08-13")
    handler._finalize_closed_trade(client, entry, "XYZ", "stop_fill", "2026-08-13")  # re-entrant tick
    trades = trades_mod.read_closed_trades()
    assert len(trades) == 1  # never double-recorded


# --- Task B: catalyst_score stamped at entry ---------------------------------

def test_open_position_stamps_catalyst_score_from_lookup(monkeypatch):
    ledger = {}
    client = _StubAlpaca()
    e = {"size_shares": 10, "stop_price": 96.0, "entry_price": 100.0}
    nom = {"rationale": "Contract win catalyst."}
    monkeypatch.setattr(handler, "_record_trade_history", lambda *a, **k: None)
    monkeypatch.setattr(handler, "write_ledger", lambda *a, **k: None)
    opened = handler._open_position(
        client, ledger, "XYZ", e, nom, "2026-08-10", {"orders_suppressed": [], "orders_issued": []}, [],
        catalyst={"score": 0.81, "components": {"news_tone": 1.0}},
    )
    assert opened is True
    assert ledger["XYZ"]["catalyst_score"] == 0.81
    assert ledger["XYZ"]["score_components"] == {"news_tone": 1.0}
    assert ledger["XYZ"]["nomination_thesis"] == "Contract win catalyst."


def test_open_position_without_catalyst_lookup_stays_none(monkeypatch):
    ledger = {}
    client = _StubAlpaca()
    e = {"size_shares": 10, "stop_price": 96.0, "entry_price": 100.0}
    nom = {"rationale": "thesis"}
    monkeypatch.setattr(handler, "_record_trade_history", lambda *a, **k: None)
    monkeypatch.setattr(handler, "write_ledger", lambda *a, **k: None)
    handler._open_position(
        client, ledger, "XYZ", e, nom, "2026-08-10", {"orders_suppressed": [], "orders_issued": []}, [],
    )
    assert ledger["XYZ"]["catalyst_score"] is None


def test_catalyst_score_lookup_reads_snapshot_ledger():
    snapshot = {"catalyst_screen": {"ledger": [
        {"symbol": "XYZ", "score": 0.81, "components": {"momentum": 1.0}},
        {"symbol": "ABC", "score": None, "components": {}},
    ]}}
    lookup = handler._catalyst_score_lookup(snapshot)
    assert lookup["XYZ"] == {"score": 0.81, "components": {"momentum": 1.0}}
    assert "ABC" in lookup


def test_catalyst_score_lookup_absent_block_is_empty():
    assert handler._catalyst_score_lookup({}) == {}
