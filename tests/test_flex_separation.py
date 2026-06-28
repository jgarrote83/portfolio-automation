"""Separation Contract regression — Flex and Core never bleed into each other.

A flex order/idea never targets a core name and never carries a core-weight
concept; a flex computed state never carries weight fields. (Core weight changes
live in the analyzer's `trades[]` and are validated by the prompt, not here.)

Run: PYTHONPATH=src pytest tests/test_flex_separation.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flex.config import FlexConfig  # noqa: E402
from flex.entry import build_flex_entry  # noqa: E402
from flex.exit_state import build_flex_exit_state  # noqa: E402
from flex.handler import _enums, _flex_nominations  # noqa: E402
from flex.regime import CORE_TICKERS  # noqa: E402

# Intraday/flex-only concepts; must NEVER appear in a core weight change.
_INTRADAY_FIELDS = {"vwap", "vwap_slope", "atr14", "gap_in_adr", "stop_price", "size_shares"}
# Core-only concepts; must NEVER appear in a flex computation.
_CORE_FIELDS = {"weight", "recommended_weight", "current_weight", "quadrant_concentration", "floor"}


def _daily(rng=0.6):
    return [{"o": 100, "h": 100 + rng / 2, "l": 100 - rng / 2, "c": 100, "v": 1_000_000} for _ in range(20)]


def _intraday(closes):
    return [{"o": c, "h": c + 0.1, "l": c - 0.1, "c": c, "v": 1000} for c in closes]


def test_flex_nominations_never_target_a_core_name():
    doc = {"flex_nominations": [
        {"symbol": "SPY", "flex_source": "ai_conviction"},   # core — must be dropped
        {"symbol": "NVDA", "flex_source": "thematic"},        # flex — kept
    ]}
    syms = {n["symbol"] for n in _flex_nominations(doc)}
    assert "NVDA" in syms
    assert "SPY" not in syms
    assert all(s not in CORE_TICKERS for s in syms)


def test_flex_entry_carries_intraday_not_core_fields():
    e = build_flex_entry(
        {"symbol": "NVDA", "sector": "Technology"},
        _intraday([100, 100.5, 101, 101.5, 102, 102.5, 103]), _daily(),
        "Q1", 1_000_000.0, 45, FlexConfig(),
    )
    keys = set(e)
    assert _INTRADAY_FIELDS <= keys
    assert keys.isdisjoint(_CORE_FIELDS)


def test_flex_exit_state_has_no_core_weight_fields():
    st = build_flex_exit_state(
        {"symbol": "NVDA", "entry_price": 100, "initial_stop": 96, "risk_per_share": 4,
         "qty_initial": 10, "qty_current": 10, "scaled_out": False, "current_stop": 96,
         "entry_date": "2026-06-25"},
        _intraday([105] * 7), _daily(rng=2.0), FlexConfig(), datetime(2026, 6, 26),
    )
    assert "next_action" in st
    assert set(st).isdisjoint(_CORE_FIELDS)


def test_flex_trade_history_rows_are_tagged_flex():
    enums = _enums({"thesis_type": "catalyst"}, {"binding": "risk_budget", "stop_price": 95.0})
    assert enums["layer"] == "flex"
