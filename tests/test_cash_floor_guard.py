"""Unit tests for Task D (F6) cash-floor guard and Task E (F7) price-sanity quarantine.

Task D: The sweep must be sized on surplus = literal_cash - target - Σ(same-session buy notionals).
When the post-all-trades literal cash would fall below 0.75% of equity, the SGOV sweep is
trimmed (and only the sweep — other trades are never touched).

Task E: Flex-candidate prices are quarantined when:
  1. Price is outside the 52-week high/low range by > 20%, OR
  2. Price moved > 50% vs the prior snapshot EOD without news corroboration.

Run: PYTHONPATH=src pytest tests/test_cash_floor_guard.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer.handler import _apply_cash_floor_guard  # noqa: E402
from collector.handler import _quarantine_flex_price   # noqa: E402


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


# ---------------------------------------------------------------------------
# Task E — _quarantine_flex_price
# ---------------------------------------------------------------------------

def _profile(symbol: str, high_52: float = 120.0, low_52: float = 80.0) -> dict:
    return {"symbol": symbol, "yearHigh": high_52, "yearLow": low_52}


def _prices(sym: str, price: float) -> dict:
    return {sym: {"c": price}}


def test_normal_price_not_quarantined():
    """Price within 52-week range → no quarantine."""
    q, reason = _quarantine_flex_price(
        _profile("MU", high_52=120.0, low_52=80.0),
        _prices("MU", 100.0), {}, {}, {"price_quarantine": {"range_pct": 20.0, "single_day_move_pct": 50.0}},
    )
    assert q is False
    assert reason == ""


def test_price_above_52w_high_quarantined():
    """Price > high_52 * 1.20 (20% above range) → quarantined."""
    # 52w high = $100, price = $125 (25% above → quarantined)
    q, reason = _quarantine_flex_price(
        _profile("MU", high_52=100.0, low_52=50.0),
        _prices("MU", 125.0), {}, {},
        {"price_quarantine": {"range_pct": 20.0, "single_day_move_pct": 50.0}},
    )
    assert q is True
    assert "above" in reason.lower()


def test_price_below_52w_low_quarantined():
    """Price < low_52 * 0.80 (20% below range) → quarantined."""
    # 52w low = $100, price = $79 (21% below → quarantined)
    q, reason = _quarantine_flex_price(
        _profile("MU", high_52=200.0, low_52=100.0),
        _prices("MU", 79.0), {}, {},
        {"price_quarantine": {"range_pct": 20.0, "single_day_move_pct": 50.0}},
    )
    assert q is True
    assert "below" in reason.lower()


def test_10x_price_vs_prior_without_news_quarantined():
    """Price 10x vs prior snapshot without news → quarantined (the MU case)."""
    # Prior = $50, now = $500 = 900% move → > 50% threshold → quarantined
    prior = {"MU": {"c": 50.0}}
    q, reason = _quarantine_flex_price(
        _profile("MU", high_52=600.0, low_52=10.0),  # range allows 500
        _prices("MU", 500.0), prior, {},
        {"price_quarantine": {"range_pct": 20.0, "single_day_move_pct": 50.0}},
    )
    assert q is True
    assert "no news" in reason.lower() or "news" in reason.lower()


def test_large_move_with_news_not_quarantined():
    """Price > 50% move vs prior BUT corroborating news exists → NOT quarantined."""
    prior = {"NVDA": {"c": 100.0}}
    news = {"NVDA": [{"headline": "NVDA announces $2T GPU deal"}]}
    q, reason = _quarantine_flex_price(
        _profile("NVDA", high_52=200.0, low_52=80.0),
        _prices("NVDA", 160.0),  # 60% move but within 52-week range
        prior, news,
        {"price_quarantine": {"range_pct": 20.0, "single_day_move_pct": 50.0}},
    )
    assert q is False
    assert reason == ""


def test_no_price_available_no_quarantine():
    """Missing price in prices dict → cannot quarantine, return False."""
    q, reason = _quarantine_flex_price(
        _profile("XYZ"),
        {},  # no price for XYZ
        {}, {},
        {"price_quarantine": {"range_pct": 20.0, "single_day_move_pct": 50.0}},
    )
    assert q is False
    assert reason == ""


def test_missing_52w_range_skips_gate1():
    """No 52-week high/low in profile → gate 1 skipped; gate 2 still runs."""
    prior = {"ABC": {"c": 50.0}}
    q, reason = _quarantine_flex_price(
        {"symbol": "ABC"},  # no yearHigh/yearLow
        _prices("ABC", 100.0),   # 100% move, no news
        prior, {},
        {"price_quarantine": {"range_pct": 20.0, "single_day_move_pct": 50.0}},
    )
    # Gate 2: 100% move without news → quarantined
    assert q is True


def test_small_move_below_threshold_not_quarantined():
    """Price moved 30% (below 50% threshold) without news → NOT quarantined."""
    prior = {"TSLA": {"c": 200.0}}
    q, reason = _quarantine_flex_price(
        _profile("TSLA", high_52=300.0, low_52=100.0),
        _prices("TSLA", 260.0),  # 30% move, within range
        prior, {},
        {"price_quarantine": {"range_pct": 20.0, "single_day_move_pct": 50.0}},
    )
    assert q is False
