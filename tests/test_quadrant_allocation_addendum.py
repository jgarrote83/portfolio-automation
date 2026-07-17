"""Session 2026-07-17, Task D — post-trade quadrant allocation addendum (Table A's
"Recommended" column, computed deterministically post-model).

Run: PYTHONPATH=src pytest tests/test_quadrant_allocation_addendum.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer.handler import _quadrant_allocation_addendum  # noqa: E402
from collector.handler import _build_quadrant_allocation  # noqa: E402


def _qa(positions, equity, cash=0.0):
    return _build_quadrant_allocation(positions, equity, cash)


def test_empty_string_when_unavailable():
    out = _quadrant_allocation_addendum({"available": False}, [], [], 100_000.0)
    assert out == ""


def test_empty_string_when_no_equity():
    qa = _qa([{"ticker": "SPY", "market_value": 40_000.0}], 0.0)
    out = _quadrant_allocation_addendum(qa, [], [], 0.0)
    assert out == ""


def test_no_trades_recommended_equals_current():
    qa = _qa([{"ticker": "SPY", "market_value": 40_000.0}], 100_000.0)
    out = _quadrant_allocation_addendum(qa, [], [], 100_000.0)
    assert "| Q1 | 40.0 |" in out
    assert "Recommended" in out


def test_buy_moves_bucket_up_sell_moves_bucket_down():
    qa = _qa([{"ticker": "SPY", "market_value": 40_000.0},
               {"ticker": "GLD", "market_value": 10_000.0}], 100_000.0)
    gaps = [{"symbol": "SPY", "price": 500.0}, {"symbol": "GLD", "price": 200.0}]
    trades = [
        {"symbol": "SPY", "side": "sell", "quantity": 20},   # -20*500/100000*100 = -10pp
        {"symbol": "GLD", "side": "buy", "quantity": 25},    # +25*200/100000*100 = +5pp
    ]
    out = _quadrant_allocation_addendum(qa, trades, gaps, 100_000.0)
    assert "| Q1 | 30.0 |" in out     # 40 - 10
    assert "| Q3 | 15.0 |" in out     # 10 + 5 (GLD -> Q3 per primary_quadrant)


def test_legacy_exit_sell_reduces_legacy_exits_bucket_not_a_quadrant():
    qa = _qa([{"ticker": "MCK", "market_value": 5_000.0}], 100_000.0)
    gaps = [{"symbol": "MCK", "price": 100.0}]
    trades = [{"symbol": "MCK", "side": "sell", "quantity": 10}]  # -10*100/100000*100=-1pp
    out = _quadrant_allocation_addendum(qa, trades, gaps, 100_000.0)
    assert "| legacy_exits | 4.0 |" in out


def test_unpriced_trade_symbol_noted_not_silently_skipped():
    qa = _qa([{"ticker": "SPY", "market_value": 40_000.0}], 100_000.0)
    trades = [{"symbol": "SPY", "side": "sell", "quantity": 5}]
    out = _quadrant_allocation_addendum(qa, trades, [], 100_000.0)   # no gaps -> no price
    assert "SPY" in out and "no price available" in out
    # Bucket unchanged since the trade couldn't be applied.
    assert "| Q1 | 40.0 |" in out
