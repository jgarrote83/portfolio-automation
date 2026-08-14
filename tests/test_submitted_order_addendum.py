"""Task A1 (2026-08-14, decision D-1) — canonical submitted-order addendum.

Motivating incident: the 2026-08-12 report narrated a 6-order "Final trade
plan" in its markdown while its own structured `trades[]` field contained a
single, unrelated, entirely synthesized band-enforcement trade. Nothing
rendered the ground-truth trades array next to the narrative, so the
divergence was invisible. This addendum renders the FINAL validated
`trades[]` array directly — no prose parsing, no interpretation.

Run: PYTHONPATH=src pytest tests/test_submitted_order_addendum.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer.handler import _submitted_order_addendum  # noqa: E402


def test_empty_trades_renders_explicit_zero_orders():
    out = _submitted_order_addendum([], [])
    assert "Total: 0 orders — no trades this session." in out


def test_renders_exact_final_trades_array_with_origin_model():
    trades = [
        {"id": "T1", "side": "sell", "symbol": "COWZ", "quantity": 19},
        {"id": "T2", "side": "sell", "symbol": "IHE", "quantity": 2},
    ]
    gaps = [{"symbol": "COWZ", "price": 70.88}, {"symbol": "IHE", "price": 103.0}]
    out = _submitted_order_addendum(trades, gaps)
    assert "| 1 | SELL | COWZ | 19 | $1,346.72 | model |" in out
    assert "| 2 | SELL | IHE | 2 | $206.00 | model |" in out
    assert "Total: 2 orders. Sells 2 / Buys 0." in out


def test_band_enforcement_trade_tagged_enforced():
    trades = [{"id": "E1", "side": "buy", "symbol": "COWZ", "quantity": 20,
               "source": "band_enforcement"}]
    gaps = [{"symbol": "COWZ", "price": 70.38}]
    out = _submitted_order_addendum(trades, gaps)
    assert "| enforced |" in out
    assert "Total: 1 order. Sells 0 / Buys 1." in out


def test_cash_floor_guard_trimmed_trade_tagged_guard():
    trades = [{"id": "S1", "side": "buy", "symbol": "SGOV", "quantity": 74,
               "adjusted_by": "cash_floor_guard"}]
    gaps = [{"symbol": "SGOV", "price": 100.51}]
    out = _submitted_order_addendum(trades, gaps)
    assert "| guard |" in out


def test_missing_price_renders_notional_as_na_not_fatal():
    trades = [{"id": "T1", "side": "buy", "symbol": "ZZZ", "quantity": 5}]
    out = _submitted_order_addendum(trades, [])
    assert "| n/a |" in out
    assert "Total: 1 order. Sells 0 / Buys 1." in out


def test_six_narrated_vs_one_actual_incident_reproduction():
    """The 2026-08-12 incident, reproduced: only ONE trade actually reached
    trades[] (a band-enforcement synthesis) despite whatever the model's prose
    might have narrated elsewhere. The addendum must show exactly that — 1
    order, tagged enforced — never inferring or backfilling the other five."""
    trades = [{"id": "T-20260812-E01", "side": "buy", "symbol": "COWZ",
               "quantity": 20, "source": "band_enforcement"}]
    gaps = [{"symbol": "COWZ", "price": 70.38}]
    out = _submitted_order_addendum(trades, gaps)
    assert "Total: 1 order. Sells 0 / Buys 1." in out
    assert "VDE" not in out
    assert "SGOV" not in out
    assert "SPY" not in out
    assert "QQQ" not in out
    assert "VXUS" not in out
