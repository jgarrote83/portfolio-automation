"""Tests for build_flex_exit_state — the mechanical exit triple.

Run: PYTHONPATH=src pytest tests/test_flex_exit.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flex.config import FlexConfig  # noqa: E402
from flex.exit_state import build_flex_exit_state, trading_days_between  # noqa: E402

CFG = FlexConfig()
NOW = datetime(2026, 6, 26, 15, 0)


def _daily(n=20, base=100.0, rng=2.0, v=1_000_000):
    return [{"o": base, "h": base + rng / 2, "l": base - rng / 2, "c": base, "v": v} for _ in range(n)]


def _intraday(closes, rng=0.2, v=1000):
    return [{"o": c, "h": c + rng / 2, "l": c - rng / 2, "c": c, "v": v} for c in closes]


def _entry(**kw):
    base = {
        "symbol": "NVDA", "entry_price": 100.0, "initial_stop": 96.0,
        "risk_per_share": 4.0, "qty_initial": 10, "qty_current": 10,
        "scaled_out": False, "current_stop": 96.0, "entry_date": "2026-06-25",
    }
    base.update(kw)
    return base


def test_scale_out_at_first_target_then_breakeven():
    # current 110 → R = (110-100)/4 = 2.5 ≥ 2 → scale out half, stop → breakeven.
    r = build_flex_exit_state(_entry(), _intraday([110] * 7), _daily(), CFG, NOW)
    assert r["next_action"] == "scale_out"
    assert r["scale_out_qty"] == 5
    assert r["target_stop"] == 100.0


def test_trail_moves_stop_up():
    # current 105 (R=1.25, no scale), VWAP support ~103 above the ATR trail (99) → trail to ~103.
    r = build_flex_exit_state(
        _entry(), _intraday([103, 103, 103, 103, 103, 103, 105]), _daily(), CFG, NOW,
    )
    assert r["next_action"] == "trail"
    assert r["target_stop"] > 96.0
    assert r["target_stop"] < 105.0


def test_time_stop_fires_after_horizon():
    r = build_flex_exit_state(
        _entry(entry_date="2026-06-01"), _intraday([105] * 7), _daily(), CFG, NOW,
    )
    assert r["next_action"] == "time_stop"
    assert r["scale_out_qty"] == 10  # full remaining qty


def test_hold_when_nothing_fires():
    # current 100 == entry (R=0), stop already trailed to 99 → trail < epsilon, no action.
    r = build_flex_exit_state(
        _entry(current_stop=99.0), _intraday([100] * 7), _daily(), CFG, NOW,
    )
    assert r["next_action"] == "hold"


def test_unknown_on_missing_data():
    assert build_flex_exit_state(_entry(), [], _daily(), CFG, NOW)["next_action"] == "unknown"
    assert build_flex_exit_state(_entry(), _intraday([100] * 7), [], CFG, NOW)["next_action"] == "unknown"


def test_quantities_key_off_qty_current_after_scale_out():
    # Already scaled out, qty_current halved to 5 → time stop sells 5, not 10.
    r = build_flex_exit_state(
        _entry(entry_date="2026-06-01", scaled_out=True, qty_current=5),
        _intraday([105] * 7), _daily(), CFG, NOW,
    )
    assert r["next_action"] == "time_stop"
    assert r["scale_out_qty"] == 5


def test_trading_days_between():
    # Mon 2026-06-22 .. Fri 2026-06-26 → 4 weekdays elapsed.
    assert trading_days_between("2026-06-22", datetime(2026, 6, 26)) == 4
    assert trading_days_between("2026-06-26", datetime(2026, 6, 26)) == 0
    assert trading_days_between(None, NOW) is None
