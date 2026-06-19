"""Unit tests for the Phase C outcome-stamping pure logic (collector handler).

Covers the date-snapping and return/call-correct math that has no CI coverage and
can't be observed live until the first recommendation hits its 30-day mark. Run:
    PYTHONPATH=src pytest tests/test_outcome_stamping.py
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import (  # noqa: E402
    _close_on_or_before,
    _max_matured_horizon,
    _outcome_level,
    _outcome_metrics,
)


# --- _close_on_or_before -----------------------------------------------------

def test_close_exact_match():
    m = {"2026-06-10": 100.0, "2026-06-11": 101.0, "2026-06-12": 102.0}
    assert _close_on_or_before(m, "2026-06-11") == 101.0


def test_close_snaps_back_over_weekend():
    # 2026-06-13/14 is a weekend (no rows) -> snap to Fri 2026-06-12.
    m = {"2026-06-11": 101.0, "2026-06-12": 102.0}
    assert _close_on_or_before(m, "2026-06-14") == 102.0


def test_close_none_when_all_dates_later():
    assert _close_on_or_before({"2026-06-20": 100.0}, "2026-06-10") is None


# --- _max_matured_horizon ----------------------------------------------------

def test_matured_exactly_30d():
    # 2026-05-19 + 30d == 2026-06-18 (== today) -> 30 matured, 60 not.
    assert _max_matured_horizon("2026-05-19", date(2026, 6, 18)) == 30


def test_matured_all_90d():
    assert _max_matured_horizon("2026-03-01", date(2026, 6, 18)) == 90


def test_matured_none_yet():
    assert _max_matured_horizon("2026-06-10", date(2026, 6, 18)) == 0


# --- _outcome_level ----------------------------------------------------------

def test_outcome_level_mapping():
    assert _outcome_level(None) == 0
    assert _outcome_level("") == 0
    assert _outcome_level("30d") == 30
    assert _outcome_level("60d") == 60
    assert _outcome_level("90d") == 90
    assert _outcome_level("closed") == 90


# --- _outcome_metrics (the core call-correct logic) --------------------------

def test_buy_correct_when_symbol_beats_spy():
    # symbol +10%, SPY +4% -> excess +6pp, a BUY is correct.
    m = _outcome_metrics("buy", 100.0, 100.0, 110.0, 104.0)
    assert m["ret"] == 10.0
    assert m["spy_ret"] == 4.0
    assert m["excess"] == 6.0
    assert m["correct"] is True


def test_buy_wrong_when_symbol_lags_spy():
    m = _outcome_metrics("buy", 100.0, 100.0, 102.0, 106.0)
    assert m["correct"] is False


def test_sell_correct_when_symbol_lags_spy():
    # We sold/trimmed; symbol +2% vs SPY +6% -> it lagged -> sell was right.
    m = _outcome_metrics("sell", 100.0, 100.0, 102.0, 106.0)
    assert m["excess"] == -4.0
    assert m["correct"] is True


def test_sell_wrong_when_symbol_beats_spy():
    m = _outcome_metrics("sell", 100.0, 100.0, 112.0, 104.0)
    assert m["correct"] is False


def test_no_correct_key_for_unknown_side():
    assert "correct" not in _outcome_metrics("", 100.0, 100.0, 110.0, 104.0)
