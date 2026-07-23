"""Unit tests for pnl_decomposition (Task C, 2026-07-23).

Verifies: FIFO realized P&L math, bucket assignment, partial-lot handling,
fill-count accounting, and non-fatal behavior on empty data.

Run: PYTHONPATH=src pytest tests/test_pnl_decomposition.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import (  # noqa: E402
    _build_pnl_decomposition,
    _fifo_realized_pnl,
)


# ---------------------------------------------------------------------------
# _fifo_realized_pnl (pure function)
# ---------------------------------------------------------------------------

def _fill(symbol: str, side: str, qty: float, price: float,
          dt: str = "2026-06-01T10:00:00Z") -> dict:
    return {"symbol": symbol, "side": side, "qty": qty, "price": price,
            "transaction_time": dt}


def test_simple_buy_then_sell():
    """Buy 10 @ $100, sell 10 @ $110 → $100 realized gain."""
    fills = [
        _fill("SPY", "buy", 10.0, 100.0, "2026-06-01"),
        _fill("SPY", "sell", 10.0, 110.0, "2026-06-02"),
    ]
    result = _fifo_realized_pnl(fills)
    assert abs(result.get("SPY", 0) - 100.0) < 0.01


def test_partial_lot_fifo():
    """Buy 10 @ $100 and 5 @ $120, sell 12 @ $115.

    FIFO: 10 shares from lot 1 → P&L = 10*(115-100) = 150
           2 shares from lot 2 → P&L = 2*(115-120) = -10
    Total = 140.
    """
    fills = [
        _fill("QQQ", "buy", 10.0, 100.0, "2026-06-01"),
        _fill("QQQ", "buy", 5.0, 120.0, "2026-06-02"),
        _fill("QQQ", "sell", 12.0, 115.0, "2026-06-03"),
    ]
    result = _fifo_realized_pnl(fills)
    expected = 10 * (115 - 100) + 2 * (115 - 120)
    assert abs(result.get("QQQ", 0) - expected) < 0.01


def test_sell_at_loss():
    """Buy 5 @ $200, sell 5 @ $180 → -$100 realized loss."""
    fills = [
        _fill("GLD", "buy", 5.0, 200.0, "2026-06-01"),
        _fill("GLD", "sell", 5.0, 180.0, "2026-06-02"),
    ]
    result = _fifo_realized_pnl(fills)
    assert abs(result.get("GLD", 0) - (-100.0)) < 0.01


def test_no_sells_zero_realized():
    """All buys, no sells → zero realized P&L."""
    fills = [
        _fill("TLT", "buy", 10.0, 90.0, "2026-06-01"),
        _fill("TLT", "buy", 5.0, 92.0, "2026-06-02"),
    ]
    result = _fifo_realized_pnl(fills)
    assert result.get("TLT", 0) == 0.0


def test_multiple_symbols():
    """Independent FIFO queues per symbol."""
    fills = [
        _fill("A", "buy", 10.0, 100.0, "2026-06-01"),
        _fill("B", "buy", 5.0, 200.0, "2026-06-01"),
        _fill("A", "sell", 10.0, 110.0, "2026-06-02"),
        _fill("B", "sell", 5.0, 190.0, "2026-06-02"),
    ]
    result = _fifo_realized_pnl(fills)
    assert abs(result.get("A", 0) - 100.0) < 0.01
    assert abs(result.get("B", 0) - (-50.0)) < 0.01


def test_empty_fills_returns_empty():
    assert _fifo_realized_pnl([]) == {}


def test_sells_before_any_buy_are_ignored():
    """Sells without a prior buy position — no queue → P&L stays 0."""
    fills = [
        _fill("MU", "sell", 2.0, 50.0, "2026-06-01"),
        _fill("MU", "buy", 2.0, 45.0, "2026-06-02"),   # buy AFTER sell
    ]
    result = _fifo_realized_pnl(fills)
    # No realized P&L since there was nothing to sell FIFO-style
    assert result.get("MU", 0) == 0.0


# ---------------------------------------------------------------------------
# _build_pnl_decomposition (integration with mock AlpacaClient)
# ---------------------------------------------------------------------------

class _MockAlpaca:
    def __init__(self, fills: list[dict]):
        self._fills = fills

    def get_activities(self, activity_type: str = "FILL", after: str | None = None,
                       until: str | None = None, page_size: int = 100) -> list[dict]:
        return self._fills


class _FailingAlpaca:
    def get_activities(self, **kwargs) -> list[dict]:
        raise RuntimeError("API failure")


def _paper(positions: list[dict], equity: float = 100_000.0) -> dict:
    return {"available": True, "equity": equity, "positions": positions}


def _pos(ticker: str, unrealized_pl: float = 0.0) -> dict:
    return {"ticker": ticker, "unrealized_pl": unrealized_pl, "current_price": 100.0}


def test_pnl_decomposition_non_fatal_on_api_error():
    """API failure → available: False, never raises."""
    result = _build_pnl_decomposition(_FailingAlpaca(), _paper([]), "2026-05-26")
    assert result["available"] is False
    assert "reason" in result


def test_pnl_decomposition_empty_fills():
    """No fills, no positions → buckets all zero."""
    alp = _MockAlpaca([])
    result = _build_pnl_decomposition(alp, _paper([]), "2026-05-26")
    assert result["available"] is True
    assert result["fill_count"] == 0
    for bucket in ("core_current", "legacy_exits", "off_roster_flex"):
        assert result[bucket]["total_usd"] == 0.0


def test_pnl_decomposition_core_bucket():
    """A CORE_ROSTER symbol's realized + unrealized lands in core_current."""
    fills = [
        _fill("SPY", "buy", 10.0, 400.0, "2026-05-27T09:30:00Z"),
        _fill("SPY", "sell", 10.0, 420.0, "2026-06-01T09:30:00Z"),
    ]
    positions = [_pos("SPY", unrealized_pl=50.0)]
    alp = _MockAlpaca(fills)
    result = _build_pnl_decomposition(alp, _paper(positions), "2026-05-26")
    assert result["available"] is True
    bucket = result["core_current"]
    # Realized: 10*(420-400) = 200
    assert abs(bucket["realized_usd"] - 200.0) < 0.01
    assert abs(bucket["unrealized_usd"] - 50.0) < 0.01
    assert abs(bucket["total_usd"] - 250.0) < 0.01


def test_pnl_decomposition_legacy_exits_bucket():
    """A LEGACY_EXITS symbol lands in legacy_exits, not core_current."""
    fills = [
        _fill("AMZN", "buy", 1.0, 3000.0, "2026-05-27"),
        _fill("AMZN", "sell", 1.0, 3100.0, "2026-06-01"),
    ]
    alp = _MockAlpaca(fills)
    result = _build_pnl_decomposition(alp, _paper([]), "2026-05-26")
    assert result["legacy_exits"]["realized_usd"] > 0
    assert result["core_current"]["realized_usd"] == 0.0


def test_pnl_decomposition_off_roster_flex_bucket():
    """A non-roster symbol (e.g. MU) lands in off_roster_flex."""
    fills = [
        _fill("MU", "buy", 2.0, 100.0, "2026-05-27"),
        _fill("MU", "sell", 2.0, 90.0, "2026-06-01"),
    ]
    positions = [_pos("MU", unrealized_pl=-20.0)]
    alp = _MockAlpaca(fills)
    result = _build_pnl_decomposition(alp, _paper(positions), "2026-05-26")
    assert result["off_roster_flex"]["realized_usd"] < 0   # sold at loss
    assert result["off_roster_flex"]["unrealized_usd"] < 0


def test_pnl_decomposition_top15_cap():
    """Even with many symbols in a bucket, contributors list is capped at 15."""
    fills = [
        _fill(f"SYM{i}", "buy", 1.0, 100.0, f"2026-05-{(27 + i):02d}")
        for i in range(20)
    ]
    alp = _MockAlpaca(fills)
    result = _build_pnl_decomposition(alp, _paper([]), "2026-05-26")
    # All 20 are off_roster_flex (unknown symbols)
    assert len(result["off_roster_flex"]["contributors"]) <= 15


def test_pnl_decomposition_pct_of_equity():
    """pct_of_equity is computed as total_usd / equity * 100."""
    fills = [
        _fill("GLD", "buy", 10.0, 200.0, "2026-05-27"),
        _fill("GLD", "sell", 10.0, 220.0, "2026-06-01"),
    ]
    equity = 100_000.0
    alp = _MockAlpaca(fills)
    result = _build_pnl_decomposition(alp, _paper([], equity=equity), "2026-05-26")
    # Realized = 10*(220-200) = 200
    expected_pct = 200.0 / equity * 100.0
    assert abs(result["core_current"]["pct_of_equity"] - expected_pct) < 0.001
