"""Flex Sleeve Performance Ledger (2026-08-10) — src/flex/trades.py.

Run: PYTHONPATH=src pytest tests/test_flex_trades.py
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import flex.trades as trades_mod  # noqa: E402


@pytest.fixture
def blob_store(monkeypatch):
    """In-memory fake for shared.storage.read_json_blob/write_json_blob, as
    imported into flex.trades's own module namespace."""
    store: dict = {}

    def _read(container, name):
        return store.get((container, name))

    def _write(container, name, obj):
        store[(container, name)] = obj

    monkeypatch.setattr(trades_mod, "read_json_blob", _read)
    monkeypatch.setattr(trades_mod, "write_json_blob", _write)
    return store


# --- build_closed_trade: the never-fabricate contract -----------------------

def _entry(**overrides):
    base = dict(
        trade_id="FLEX-2026-08-10-XYZ-a1b2c3d4", symbol="XYZ",
        entry_date="2026-08-10", entry_price=100.0, qty_initial=100,
        initial_stop=96.0, risk_per_share=4.0,
        catalyst_score=0.72, score_components={"momentum": 0.8},
        nomination_thesis="Contract win catalyst.",
    )
    base.update(overrides)
    return base


def test_build_closed_trade_computes_pnl_and_r_multiple():
    fills = [{"date": "2026-08-14", "qty": 100, "price": 108.0, "reason": "stop_fill"}]
    trade = trades_mod.build_closed_trade(_entry(), fills, "stop_fill", "2026-08-14")
    assert trade["pnl_usd"] == 800.0  # (108-100)*100
    assert trade["r_multiple"] == 2.0  # 8 / 4
    assert trade["pnl_unavailable_reason"] is None
    assert trade["holding_days"] == 4  # Mon->Fri
    assert trade["catalyst_score"] == 0.72
    assert trade["score_components"] == {"momentum": 0.8}
    assert trade["nomination_thesis"] == "Contract win catalyst."


def test_build_closed_trade_multi_fill_scale_out_plus_stop():
    fills = [
        {"date": "2026-08-12", "qty": 50, "price": 110.0, "reason": "scale_out"},
        {"date": "2026-08-14", "qty": 50, "price": 102.0, "reason": "stop_fill"},
    ]
    trade = trades_mod.build_closed_trade(_entry(), fills, "stop_fill", "2026-08-14")
    # proceeds = 50*110 + 50*102 = 5500+5100=10600; cost = 100*100=10000; pnl=600
    assert trade["pnl_usd"] == 600.0
    assert trade["r_multiple"] == pytest.approx(600.0 / 100 / 4.0, rel=1e-6)
    assert len(trade["fills"]) == 2


def test_build_closed_trade_never_fabricates_missing_fill_price():
    fills = [{"date": "2026-08-14", "qty": 100, "price": None, "reason": "stop_fill"}]
    trade = trades_mod.build_closed_trade(_entry(), fills, "stop_fill", "2026-08-14")
    assert trade["pnl_usd"] is None
    assert trade["r_multiple"] is None
    assert trade["pnl_unavailable_reason"] == "missing_fill_price"


def test_build_closed_trade_no_fills_at_all():
    trade = trades_mod.build_closed_trade(_entry(), [], "stop_fill", "2026-08-14")
    assert trade["pnl_usd"] is None
    assert trade["pnl_unavailable_reason"] == "no_fills_recorded"


def test_build_closed_trade_missing_entry_price():
    fills = [{"date": "2026-08-14", "qty": 100, "price": 108.0, "reason": "stop_fill"}]
    trade = trades_mod.build_closed_trade(_entry(entry_price=None), fills, "stop_fill", "2026-08-14")
    assert trade["pnl_usd"] is None
    assert trade["pnl_unavailable_reason"] == "missing_entry_price"


def test_build_closed_trade_r_multiple_null_when_risk_zero():
    fills = [{"date": "2026-08-14", "qty": 100, "price": 108.0, "reason": "stop_fill"}]
    trade = trades_mod.build_closed_trade(_entry(risk_per_share=0.0), fills, "stop_fill", "2026-08-14")
    assert trade["pnl_usd"] == 800.0
    assert trade["r_multiple"] is None


# --- fills_from_activities ---------------------------------------------------

def test_fills_from_activities_filters_symbol_and_side():
    activities = [
        {"symbol": "XYZ", "side": "buy", "qty": "100", "price": "100.00",
         "transaction_time": "2026-08-10T14:31:00Z"},
        {"symbol": "XYZ", "side": "sell", "qty": "100", "price": "108.00",
         "transaction_time": "2026-08-14T15:00:00Z"},
        {"symbol": "ABC", "side": "buy", "qty": "50", "price": "20.00",
         "transaction_time": "2026-08-10T14:31:00Z"},
    ]
    buys = trades_mod.fills_from_activities(activities, "XYZ", "buy")
    sells = trades_mod.fills_from_activities(activities, "XYZ", "sell")
    assert buys == [{"date": "2026-08-10", "qty": 100.0, "price": 100.0}]
    assert sells == [{"date": "2026-08-14", "qty": 100.0, "price": 108.0}]


def test_fills_from_activities_drops_malformed_rows():
    activities = [{"symbol": "XYZ", "side": "buy", "qty": "bad", "price": "100.00"}]
    assert trades_mod.fills_from_activities(activities, "XYZ", "buy") == []


def test_fills_from_activities_empty():
    assert trades_mod.fills_from_activities([], "XYZ", "buy") == []
    assert trades_mod.fills_from_activities(None, "XYZ", "buy") == []


# --- merge_broker_fills: broker truth reconciliation -------------------------

def test_merge_broker_fills_no_recorded_fills_single_broker_sell():
    merged = trades_mod.merge_broker_fills([], [
        {"date": "2026-08-14", "qty": 100, "price": 96.0},
    ], "stop_fill")
    assert merged == [{"date": "2026-08-14", "qty": 100, "price": 96.0, "reason": "stop_fill"}]


def test_merge_broker_fills_recorded_scale_out_plus_unaccounted_stop():
    recorded = [{"date": "2026-08-12", "qty": 50, "price": 110.0, "reason": "scale_out"}]
    broker_sells = [
        {"date": "2026-08-12", "qty": 50, "price": 110.0},   # already accounted for
        {"date": "2026-08-14", "qty": 50, "price": 96.0},    # the stop fill
    ]
    merged = trades_mod.merge_broker_fills(recorded, broker_sells, "stop_fill")
    assert len(merged) == 2
    assert merged[0] == recorded[0]
    assert merged[1] == {"date": "2026-08-14", "qty": 50, "price": 96.0, "reason": "stop_fill"}


def test_merge_broker_fills_engine_missed_a_scale_out():
    # Engine's own record is EMPTY (e.g. a crash before it recorded the fill),
    # but broker shows two sells -- the earlier one is broker-confirmed as a
    # missed scale_out, the last one is the actual close.
    broker_sells = [
        {"date": "2026-08-12", "qty": 50, "price": 110.0},
        {"date": "2026-08-14", "qty": 50, "price": 96.0},
    ]
    merged = trades_mod.merge_broker_fills([], broker_sells, "stop_fill")
    assert merged[0]["reason"] == "scale_out"
    assert merged[1]["reason"] == "stop_fill"


def test_merge_broker_fills_fully_accounted_for_adds_nothing():
    recorded = [{"date": "2026-08-14", "qty": 100, "price": 96.0, "reason": "time_stop"}]
    broker_sells = [{"date": "2026-08-14", "qty": 100, "price": 96.0}]
    merged = trades_mod.merge_broker_fills(recorded, broker_sells, "stop_fill")
    assert merged == recorded


# --- record_closed_trade: idempotency ----------------------------------------

def test_record_closed_trade_writes_once(blob_store):
    trade = trades_mod.build_closed_trade(
        _entry(), [{"date": "2026-08-14", "qty": 100, "price": 108.0, "reason": "stop_fill"}],
        "stop_fill", "2026-08-14",
    )
    written = trades_mod.record_closed_trade(trade)
    assert written is True
    assert len(trades_mod.read_closed_trades()) == 1


def test_record_closed_trade_is_idempotent_on_double_write(blob_store):
    trade = trades_mod.build_closed_trade(
        _entry(), [{"date": "2026-08-14", "qty": 100, "price": 108.0, "reason": "stop_fill"}],
        "stop_fill", "2026-08-14",
    )
    first = trades_mod.record_closed_trade(trade)
    second = trades_mod.record_closed_trade(trade)  # re-entrant tick, same trade_id
    assert first is True
    assert second is False
    trades = trades_mod.read_closed_trades()
    assert len(trades) == 1  # never double-counted
    assert trades[0]["trade_id"] == trade["trade_id"]


def test_record_closed_trade_distinct_ids_both_land(blob_store):
    t1 = trades_mod.build_closed_trade(_entry(trade_id="FLEX-A"), [
        {"date": "2026-08-14", "qty": 100, "price": 108.0, "reason": "stop_fill"}],
        "stop_fill", "2026-08-14")
    t2 = trades_mod.build_closed_trade(_entry(trade_id="FLEX-B", symbol="ABC"), [
        {"date": "2026-08-14", "qty": 50, "price": 22.0, "reason": "time_stop"}],
        "time_stop", "2026-08-14")
    trades_mod.record_closed_trade(t1)
    trades_mod.record_closed_trade(t2)
    assert len(trades_mod.read_closed_trades()) == 2


# --- build_sleeve_mark --------------------------------------------------------

def test_build_sleeve_mark_aggregates_open_positions_and_realized():
    positions = [
        {"symbol": "XYZ", "market_value": "1200.00", "unrealized_pl": "50.00"},
        {"symbol": "AAPL", "market_value": "5000.00", "unrealized_pl": "-20.00"},  # not flex-managed
    ]
    ledger = {"XYZ": {"qty_current": 12}}
    closed_trades = [
        {"pnl_usd": 800.0}, {"pnl_usd": -150.0}, {"pnl_usd": None},  # null must not break the sum
    ]
    mark = trades_mod.build_sleeve_mark("2026-08-14", positions, ledger, closed_trades, 100_000.0)
    assert mark["sleeve_notional_usd"] == 1200.0
    assert mark["unrealized_usd"] == 50.0
    assert mark["cumulative_realized_usd"] == 650.0
    assert mark["total_equity"] == 100_000.0
    assert mark["open_positions"] == 1
    assert mark["closed_trades_to_date"] == 3


def test_build_sleeve_mark_no_positions_or_trades():
    mark = trades_mod.build_sleeve_mark("2026-08-14", [], {}, [], None)
    assert mark["sleeve_notional_usd"] == 0.0
    assert mark["unrealized_usd"] == 0.0
    assert mark["cumulative_realized_usd"] == 0.0
    assert mark["total_equity"] is None
    assert mark["open_positions"] == 0
    assert mark["closed_trades_to_date"] == 0


# --- upsert_equity_point: replace-by-date, never duplicate -------------------

def test_upsert_equity_point_replaces_same_date(blob_store):
    trades_mod.upsert_equity_point({"date": "2026-08-14", "sleeve_notional_usd": 100.0})
    trades_mod.upsert_equity_point({"date": "2026-08-14", "sleeve_notional_usd": 200.0})
    series = trades_mod.read_equity_series()
    assert len(series) == 1
    assert series[0]["sleeve_notional_usd"] == 200.0


def test_upsert_equity_point_appends_new_dates_sorted(blob_store):
    trades_mod.upsert_equity_point({"date": "2026-08-14", "sleeve_notional_usd": 1.0})
    trades_mod.upsert_equity_point({"date": "2026-08-12", "sleeve_notional_usd": 2.0})
    series = trades_mod.read_equity_series()
    assert [p["date"] for p in series] == ["2026-08-12", "2026-08-14"]
