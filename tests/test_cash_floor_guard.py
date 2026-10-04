"""Unit tests for Task D (F6) cash-floor guard.

Task D: The sweep must be sized on surplus = literal_cash - target - Σ(same-session buy notionals).
When the post-all-trades literal cash would fall below 0.75% of equity, the SGOV sweep is
trimmed (and only the sweep — other trades are never touched).

Run: PYTHONPATH=src pytest tests/test_cash_floor_guard.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer.handler import _apply_cash_floor_guard  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(cash: float = 10_000.0, equity: float = 100_000.0) -> dict:
    return {"cash_usd": cash, "equity_usd": equity}


def _gap(symbol: str, price: float) -> dict:
    return {"symbol": symbol, "price": price}


def _trade(symbol: str, side: str, qty: int, price: float) -> dict:
    return {"symbol": symbol, "side": side, "quantity": qty, "qty": qty}


def _limits(floor_pct: float = 0.75) -> dict:
    return {"literal_cash_floor_pct": floor_pct}


# ---------------------------------------------------------------------------
# Task D — _apply_cash_floor_guard
# ---------------------------------------------------------------------------

def test_no_sgov_sweep_no_change():
    """No SGOV buy in trades → guard returns trades unchanged."""
    trades = [_trade("GLD", "buy", 2, 200.0)]
    gaps = [_gap("GLD", 200.0)]
    result_trades, addendum = _apply_cash_floor_guard(trades, gaps, _ctx(), None, _limits())
    assert result_trades == trades
    assert addendum == ""


def test_sgov_sweep_fits_floor_no_trim():
    """SGOV sweep is small enough that literal cash stays above floor → no trim."""
    # Pre-trade cash = $5000, floor = 0.75% * $100k = $750
    # SGOV buy: 10 shares @ $100 = $1000 → post-cash = $5000 - $1000 = $4000 > $750
    ctx = _ctx(cash=5_000.0, equity=100_000.0)
    trades = [_trade("SGOV", "buy", 10, 100.0)]
    gaps = [_gap("SGOV", 100.0)]
    result_trades, addendum = _apply_cash_floor_guard(trades, gaps, ctx, None, _limits())
    assert result_trades == trades
    assert addendum == ""


def test_sgov_sweep_violates_floor_trimmed():
    """Cash after sweep falls below floor → sweep is trimmed."""
    # Pre-trade cash = $1000, floor = 0.75% * $100k = $750
    # SGOV buy: 5 shares @ $100 = $500 → post-cash = $1000 - $500 = $500 < $750
    ctx = _ctx(cash=1_000.0, equity=100_000.0)
    trades = [_trade("SGOV", "buy", 5, 100.0)]
    gaps = [_gap("SGOV", 100.0)]
    result_trades, addendum = _apply_cash_floor_guard(trades, gaps, ctx, None, _limits())
    # Post-trim cash should be >= $750; so max SGOV we can buy = (1000-750)/100 = 2 shares
    sgov = next(t for t in result_trades if t["symbol"] == "SGOV")
    assert sgov["quantity"] <= 2
    assert addendum != ""   # must have an addendum note


def test_sgov_sweep_trimmed_to_zero_removes_trade():
    """When even 1 share would breach the floor, the SGOV buy is removed entirely."""
    # Pre-trade cash = $800, floor = 0.75% * $100k = $750
    # SGOV buy: 1 share @ $100 = $100 → post-cash = $700 < $750 → remove
    ctx = _ctx(cash=800.0, equity=100_000.0)
    trades = [_trade("SGOV", "buy", 1, 100.0)]
    gaps = [_gap("SGOV", 100.0)]
    result_trades, addendum = _apply_cash_floor_guard(trades, gaps, ctx, None, _limits())
    sgov_in_result = [t for t in result_trades if t["symbol"] == "SGOV"]
    assert len(sgov_in_result) == 0
    assert addendum != ""


def test_other_buys_starve_sgov_sweep():
    """Same-day buy of another name reduces available cash → SGOV trimmed."""
    # Pre-trade cash = $5000, floor = $750
    # GLD buy: 3 shares @ $1000 = $3000 spend
    # SGOV buy: 20 shares @ $100 = $2000 spend
    # Total spend = $5000 → post-cash = $0 → far below $750 → SGOV trimmed
    ctx = _ctx(cash=5_000.0, equity=100_000.0)
    trades = [
        _trade("GLD", "buy", 3, 1000.0),
        _trade("SGOV", "buy", 20, 100.0),
    ]
    gaps = [_gap("GLD", 1000.0), _gap("SGOV", 100.0)]
    result_trades, addendum = _apply_cash_floor_guard(trades, gaps, ctx, None, _limits())
    sgov = next((t for t in result_trades if t["symbol"] == "SGOV"), None)
    if sgov is not None:
        # SGOV reduced to leave $750 after the GLD buy
        # Cash left after GLD: $5000 - $3000 = $2000; floor = $750
        # Max SGOV = (2000-750)/100 = 12
        assert sgov["quantity"] <= 12
    # GLD trade must not be touched
    gld = next(t for t in result_trades if t["symbol"] == "GLD")
    assert gld["quantity"] == 3


def test_sell_proceeds_reduce_shortfall():
    """A sell adds cash → SGOV can absorb more (no trimming if proceeds cover)."""
    # Pre-trade cash = $500, sell TLT 5 shares @ $100 → +$500 → $1000 available
    # SGOV buy: 2 shares @ $100 → post-cash = $1000-$200 = $800 > $750
    ctx = _ctx(cash=500.0, equity=100_000.0)
    trades = [
        _trade("TLT", "sell", 5, 100.0),
        _trade("SGOV", "buy", 2, 100.0),
    ]
    gaps = [_gap("TLT", 100.0), _gap("SGOV", 100.0)]
    result_trades, addendum = _apply_cash_floor_guard(trades, gaps, ctx, None, _limits())
    assert addendum == ""   # no trim needed
    assert result_trades == trades


def test_zero_equity_no_action():
    """Zero equity → guard returns trades unchanged (avoids division by zero)."""
    trades = [_trade("SGOV", "buy", 5, 100.0)]
    gaps = [_gap("SGOV", 100.0)]
    result_trades, addendum = _apply_cash_floor_guard(
        trades, gaps, _ctx(equity=0), None, _limits())
    assert result_trades == trades
    assert addendum == ""
