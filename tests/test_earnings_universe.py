"""B2 (deferred finding 4) — the market-wide FMP earnings calendar is filtered to the
book's universe before it is written to the snapshot, so held names' confirmed dates
surface and irrelevant names don't. Run:
    PYTHONPATH=src pytest tests/test_earnings_universe.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _filter_earnings_to_universe  # noqa: E402


def test_filters_to_universe_keeps_held_and_flex_drops_irrelevant():
    rows = [
        {"symbol": "GOOGL", "date": "2026-07-22"},   # held
        {"symbol": "MCD", "date": "2026-07-23"},      # irrelevant
        {"symbol": "PPA", "date": "2026-07-24"},      # flex candidate
    ]
    universe = {"GOOGL", "PPA", "SPY", "GLD"}
    out = _filter_earnings_to_universe(rows, universe)
    syms = {r["symbol"] for r in out}
    assert syms == {"GOOGL", "PPA"}
    assert "MCD" not in syms


def test_preserves_row_schema_and_is_case_insensitive():
    rows = [{"symbol": "googl", "date": "2026-07-22", "epsEstimated": 1.2}]
    out = _filter_earnings_to_universe(rows, {"GOOGL"})
    assert out == [{"symbol": "googl", "date": "2026-07-22", "epsEstimated": 1.2}]


def test_empty_inputs():
    assert _filter_earnings_to_universe([], {"GOOGL"}) == []
    assert _filter_earnings_to_universe([{"symbol": "GOOGL"}], set()) == []
