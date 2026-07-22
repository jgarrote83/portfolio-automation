"""Separation Contract regression — Flex and Core never bleed into each other.

A flex order/idea never targets a core name and never carries a core-weight
concept; a flex computed state never carries weight fields. (Core weight changes
live in the analyzer's `trades[]` and are validated by the prompt, not here.)

A2 (2026-07-21, decision D3): the separation set is derived from the LIVE
`sleeve-roles.json` pools + legacy-exit doctrine (`flex_separation_set`), NOT the
retired fixed-24 `CORE_TICKERS` roster — so every current pool member is blocked
while re-enterable legacy names (INTC/MCK/PPA/EUAD) are nominatable once flat.

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
from flex.regime import FLEX_REENTERABLE, flex_separation_set  # noqa: E402

# Intraday/flex-only concepts; must NEVER appear in a core weight change.
_INTRADAY_FIELDS = {"vwap", "vwap_slope", "atr14", "gap_in_adr", "stop_price", "size_shares"}
# Core-only concepts; must NEVER appear in a flex computation.
_CORE_FIELDS = {"weight", "recommended_weight", "current_weight", "quadrant_concentration", "floor"}


def _daily(rng=0.6):
    return [{"o": 100, "h": 100 + rng / 2, "l": 100 - rng / 2, "c": 100, "v": 1_000_000} for _ in range(20)]


def _intraday(closes):
    return [{"o": c, "h": c + 0.1, "l": c - 0.1, "c": c, "v": 1000} for c in closes]


# --- flex_separation_set: derived from the LIVE roster -----------------------

def test_current_roster_pool_members_are_blocked():
    sep = flex_separation_set(frozenset())
    # Non-selected pool members AND selected incumbents are all off-limits.
    for t in ("SOXX", "KMLM", "XLV", "IEF", "SMH", "XLI", "GLD"):
        assert t in sep, t
    # International pool members (both rotation roles).
    for t in ("VXUS", "ACWX", "IXUS", "AIA", "EWZ"):
        assert t in sep, t


def test_reenterable_legacy_allowed_when_flat_blocked_when_held():
    sep_flat = flex_separation_set(frozenset())
    assert "PPA" not in sep_flat
    assert "EUAD" not in sep_flat
    assert "MCK" not in sep_flat
    # MCK mid-wind-down (still held) must not exist in both books.
    sep_held = flex_separation_set(frozenset({"MCK"}))
    assert "MCK" in sep_held
    assert FLEX_REENTERABLE == frozenset({"INTC", "MCK", "PPA", "EUAD"})


def test_non_reenterable_legacy_always_blocked():
    for held in (frozenset(), frozenset({"AMZN", "DBA", "TIP"})):
        sep = flex_separation_set(held)
        for t in ("AMZN", "GOOGL", "DBA", "TIP"):
            assert t in sep, t


def test_xsd_blocked_by_pool_membership():
    # XSD is both a legacy exit and a semis pool member — pool membership blocks it.
    assert "XSD" in flex_separation_set(frozenset())


# --- _flex_nominations wired to the separation set ---------------------------

def test_flex_nominations_drop_pool_member_keep_reenterable_legacy():
    doc = {"flex_nominations": [
        {"symbol": "KMLM", "flex_source": "ai_conviction"},  # trend pool → dropped
        {"symbol": "PPA", "flex_source": "thematic"},          # re-enterable, not held → kept
        {"symbol": "NVDA", "flex_source": "thematic"},         # clean → kept
    ]}
    syms = {n["symbol"] for n in _flex_nominations(doc)}
    assert syms == {"PPA", "NVDA"}


def test_flex_nominations_drop_held_reenterable_legacy():
    doc = {"flex_nominations": [{"symbol": "MCK"}, {"symbol": "NVDA"}]}
    syms = {n["symbol"] for n in _flex_nominations(doc, held_symbols=frozenset({"MCK"}))}
    assert syms == {"NVDA"}


def test_flex_nominations_never_target_a_pool_member():
    doc = {"flex_nominations": [{"symbol": "SPY"}, {"symbol": "NVDA"}]}
    syms = {n["symbol"] for n in _flex_nominations(doc)}
    assert "NVDA" in syms and "SPY" not in syms
    assert all(s not in flex_separation_set(frozenset()) for s in syms)


# --- structural separation (unchanged) ---------------------------------------

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
