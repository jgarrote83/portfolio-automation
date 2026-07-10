"""Unit tests for the analyzer's Tier-1 validation addendum (submittable count +
post-validation cash note).

Task 7 (2026-07-09 audit): the report body's cash/sleeve prose assumed every proposed
trade executed, but the validator rejected/clamped some — so the narrated cash level
was wrong. The addendum now states "Submittable: X of Y proposed trades" and, when a
rejection/clamp changes the cash arithmetic, a corrected literal-cash figure. Run:
    PYTHONPATH=src pytest tests/test_validation_addendum.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer.handler import _post_validation_cash, _validation_addendum  # noqa: E402


def _stamped(sym, side, qty, status="passed", reasons=None):
    return {"id": f"T-{sym}-{side}", "symbol": sym, "side": side, "quantity": qty,
            "validation": {"status": status, "reasons": reasons or []}}


def test_addendum_contains_submittable_count_line():
    tv = {
        "summary": {"passed": 1, "clamped": 0, "rejected": 1},
        "trades": [_stamped("SPY", "sell", 10)],
        "rejected": [_stamped("SGOV", "buy", 40, status="rejected",
                              reasons=["over window"])],
    }
    md = _validation_addendum(tv)
    assert "Submittable: 1 of 2 proposed trades." in md


def test_rejection_produces_corrected_cash_line():
    # Pre-trade cash $10,000. Validated trades: a SELL of 10 SPY @ $100 = +$1,000.
    # The rejected SGOV buy is NOT counted (it never executes), so post cash = $11,000.
    tv = {
        "summary": {"passed": 1, "clamped": 0, "rejected": 1},
        "trades": [_stamped("SPY", "sell", 10)],
        "rejected": [_stamped("SGOV", "buy", 40, status="rejected",
                              reasons=["over window"])],
    }
    gaps = [{"symbol": "SPY", "price": 100.0}, {"symbol": "SGOV", "price": 100.0}]
    ctx = {"cash_usd": 10_000.0}
    md = _validation_addendum(tv, gaps, ctx)
    assert "post-validation literal cash ≈ $11,000" in md


def test_no_cash_line_when_ctx_absent():
    tv = {
        "summary": {"passed": 0, "clamped": 1, "rejected": 0},
        "trades": [_stamped("SGOV", "buy", 5, status="clamped", reasons=["buffer"])],
        "rejected": [],
    }
    md = _validation_addendum(tv)
    assert "post-validation literal cash" not in md
    assert "Submittable: 1 of 1 proposed trades." in md


def test_post_validation_cash_counts_buys_and_sells():
    trades = [_stamped("SPY", "sell", 10), _stamped("GLD", "buy", 5)]
    gaps = [{"symbol": "SPY", "price": 100.0}, {"symbol": "GLD", "price": 200.0}]
    # 10_000 + 10*100 (sell) - 5*200 (buy) = 10_000.
    assert _post_validation_cash(trades, gaps, {"cash_usd": 10_000.0}) == 10_000.0


def test_post_validation_cash_none_without_pretrade_cash():
    assert _post_validation_cash([], [], {}) is None
