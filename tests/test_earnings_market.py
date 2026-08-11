"""Task A (2026-08-10, catalyst-sleeve-funnel, G1 fix) —
``collector.handler._screen_earnings_market_rows``: the market-wide earnings
calendar rows are no longer simply discarded once filtered to the book's
universe; the ADDITIONAL subset is screened + capped and kept.

Run: PYTHONPATH=src pytest tests/test_earnings_market.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _screen_earnings_market_rows  # noqa: E402


def test_drops_rows_already_in_book_universe():
    rows = [
        {"symbol": "GOOGL", "date": "2026-08-12"},   # already covered by earnings_calendar
        {"symbol": "MCD", "date": "2026-08-13"},      # genuinely new
    ]
    kept, dropped = _screen_earnings_market_rows(rows, {"GOOGL"}, cap=10)
    syms = {r["symbol"] for r in kept}
    assert syms == {"MCD"}
    assert dropped == 0


def test_drops_non_common_stock_ticker_formats():
    rows = [
        {"symbol": "MCD", "date": "2026-08-13"},       # plain ticker — kept
        {"symbol": "BRK.B", "date": "2026-08-13"},      # class share w/ dot — dropped
        {"symbol": "TOOLONGX", "date": "2026-08-13"},   # >5 chars — dropped
        {"symbol": "abc123", "date": "2026-08-13"},     # digits — dropped
    ]
    kept, _ = _screen_earnings_market_rows(rows, set(), cap=10)
    syms = {r["symbol"] for r in kept}
    assert syms == {"MCD"}


def test_caps_row_count_nearest_dated_first_and_reports_dropped():
    rows = [
        {"symbol": "AAA", "date": "2026-08-20"},
        {"symbol": "BBB", "date": "2026-08-11"},
        {"symbol": "CCC", "date": "2026-08-15"},
    ]
    kept, dropped = _screen_earnings_market_rows(rows, set(), cap=2)
    assert [r["symbol"] for r in kept] == ["BBB", "CCC"]  # nearest-dated first
    assert dropped == 1


def test_empty_inputs():
    kept, dropped = _screen_earnings_market_rows([], set(), cap=10)
    assert kept == []
    assert dropped == 0


def test_case_insensitive_universe_and_symbol_matching():
    rows = [{"symbol": "mcd", "date": "2026-08-13"}]
    kept, _ = _screen_earnings_market_rows(rows, {"OTHER"}, cap=10)
    assert len(kept) == 1
    kept2, _ = _screen_earnings_market_rows(rows, {"MCD"}, cap=10)
    assert kept2 == []
