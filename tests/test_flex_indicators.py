"""Unit tests for the pure flex indicator math.

Run: PYTHONPATH=src pytest tests/test_flex_indicators.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flex.indicators import (  # noqa: E402
    atr14,
    avg_daily_range,
    avg_dollar_volume,
    gap_in_adr,
    gap_pct,
    opening_range_low,
    session_vwap,
    vwap_slope,
)


def _bar(c, rng=0.2, v=1000):
    return {"o": c, "h": c + rng / 2, "l": c - rng / 2, "c": c, "v": v}


def test_session_vwap_equals_mean_typical_when_volume_constant():
    bars = [_bar(100), _bar(102)]
    # typical == c when h/l symmetric; equal volume → simple mean
    assert session_vwap(bars) == 101.0


def test_session_vwap_none_without_volume():
    assert session_vwap([{"o": 1, "h": 1, "l": 1, "c": 1, "v": 0}]) is None


def test_vwap_slope_positive_when_rising():
    bars = [_bar(100 + i * 0.5) for i in range(8)]
    assert vwap_slope(bars, 5) > 0


def test_vwap_slope_negative_when_falling():
    bars = [_bar(110 - i * 0.5) for i in range(8)]
    assert vwap_slope(bars, 5) < 0


def test_vwap_slope_none_when_too_few_bars():
    assert vwap_slope([_bar(100), _bar(101)], 5) is None


def test_atr14_needs_14_bars():
    assert atr14([_bar(100) for _ in range(10)]) is None
    bars = [_bar(100, rng=0.6) for _ in range(20)]
    # constant closes → TR == high-low == 0.6
    assert round(atr14(bars), 4) == 0.6


def test_avg_daily_range_is_fractional():
    bars = [_bar(100, rng=0.6) for _ in range(20)]
    assert round(avg_daily_range(bars), 5) == 0.006  # 0.6 / 100


def test_gap_pct_and_in_adr():
    assert round(gap_pct(102, 100), 4) == 0.02
    assert gap_pct(100, 0) is None
    assert round(gap_in_adr(0.02, 0.006), 2) == 3.33
    assert gap_in_adr(0.02, 0) is None


def test_avg_dollar_volume():
    bars = [_bar(100, v=1_000_000) for _ in range(20)]
    assert avg_dollar_volume(bars) == 100_000_000.0


def test_opening_range_low():
    bars = [_bar(100, rng=0.2), _bar(101, rng=0.2)]
    assert round(opening_range_low(bars), 4) == 99.9
