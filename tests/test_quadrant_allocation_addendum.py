"""Session 2026-07-17, Task D — post-trade quadrant allocation addendum (Table A's
"Recommended" column, computed deterministically post-model).

Run: PYTHONPATH=src pytest tests/test_quadrant_allocation_addendum.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer.handler import _quadrant_allocation_addendum  # noqa: E402
from collector.handler import _build_quadrant_allocation  # noqa: E402


def _qa(positions, equity, cash=0.0):
    return _build_quadrant_allocation(positions, equity, cash)


def _parse_bucket_table(md: str) -> dict:
    """Pull {bucket: post_trade_pct} out of the addendum's markdown table."""
    rows = {}
    for line in md.splitlines():
        m = re.match(r"\|\s*(\w[\w_]*)\s*\|\s*([\-0-9.]+)\s*\|", line.strip())
        if m:
            rows[m.group(1)] = float(m.group(2))
    return rows


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


def test_post_trade_bucket_sum_equals_pre_trade_sum_exactly():
    """A trade moves value BETWEEN buckets — it never creates or destroys it. The
    post-trade bucket total (including cash_sleeve, which must absorb the
    opposite side of every applied trade) must equal the pre-trade total exactly."""
    qa = _qa([{"ticker": "SPY", "market_value": 40_000.0},
               {"ticker": "GLD", "market_value": 10_000.0}], 100_000.0)
    gaps = [{"symbol": "SPY", "price": 500.0}, {"symbol": "GLD", "price": 200.0}]
    trades = [
        {"symbol": "SPY", "side": "sell", "quantity": 20},   # -10pp from Q1
        {"symbol": "GLD", "side": "buy", "quantity": 25},    # +5pp to Q3
    ]
    pre_trade_sum = sum(qa["buckets"].values())
    out = _quadrant_allocation_addendum(qa, trades, gaps, 100_000.0)
    post_trade_sum = sum(_parse_bucket_table(out).values())
    assert post_trade_sum == pre_trade_sum


def test_cash_sleeve_moves_by_net_sell_minus_buy_on_mixed_fixture():
    """Sells return cash, buys spend it — cash_sleeve must move by exactly
    (total sell pp - total buy pp) on a fixture mixing both sides."""
    qa = _qa([{"ticker": "SPY", "market_value": 40_000.0},
               {"ticker": "GLD", "market_value": 10_000.0}], 100_000.0)
    gaps = [{"symbol": "SPY", "price": 500.0}, {"symbol": "GLD", "price": 200.0}]
    trades = [
        {"symbol": "SPY", "side": "sell", "quantity": 20},   # sell +10pp of cash
        {"symbol": "GLD", "side": "buy", "quantity": 25},    # buy -5pp of cash
    ]
    pre_cash = qa["buckets"]["cash_sleeve"]
    out = _quadrant_allocation_addendum(qa, trades, gaps, 100_000.0)
    post = _parse_bucket_table(out)
    assert post["cash_sleeve"] == pre_cash + (10.0 - 5.0)
