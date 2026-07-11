"""FOLLOWUPS #12 -- quadrant_performance pure-builder tests (regime-call
accountability).

Covers `_build_quadrant_performance` (window returns + excess vs SPY, the
favored_streak/streak_excess_pp/lagging_sessions hysteresis scan, and the
`suspect` gate) and `_quadrant_perf_series` (the collector-side mirror of the
SWA API's `_quadrant_series` -- must stay in lock-step; see
tests/test_quadrant_performance.py for the API-side equivalents). Run:
    PYTHONPATH=src pytest tests/test_quadrant_regime_accountability.py
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import (  # noqa: E402
    _build_quadrant_performance,
    _perf_point,
    _quadrant_perf_series,
)

_TODAY = date.today()


def _d(days_ago: int) -> str:
    return (_TODAY - timedelta(days=days_ago)).isoformat()


def _daily_series(rows: list[tuple]) -> list[dict]:
    """rows: [(days_ago, gld_close_or_None, spy_close, favored_list)] -> chronological series."""
    out = []
    for days_ago, gld, spy, fav in rows:
        closes = {} if gld is None else {"GLD": gld}
        out.append(_perf_point(_d(days_ago), 100_000.0, spy, None, closes=closes, favored=fav))
    return out


# --- _quadrant_perf_series: base semantics mirror the SWA API's _quadrant_series --

def test_quadrant_perf_series_equal_weight():
    pts = [{"closes": {"A": 100.0, "B": 200.0}}, {"closes": {"A": 110.0, "B": 190.0}}]
    out = _quadrant_perf_series(pts, {"Q1": ["A", "B"]})
    assert out[0]["Q1"] == 100.0
    assert out[1]["Q1"] == 102.5   # +10% and -5% -> +2.5% equal-weight


def test_quadrant_perf_series_late_joiner_no_retroactive_distortion():
    # B has no close on day 0; its base is day 1's 50.0, so day 2 it contributes
    # +10% rather than a spurious level shift (mirrors web API test verbatim).
    pts = [
        {"closes": {"A": 100.0}},
        {"closes": {"A": 100.0, "B": 50.0}},
        {"closes": {"A": 100.0, "B": 55.0}},
    ]
    out = _quadrant_perf_series(pts, {"Q1": ["A", "B"]})
    assert out[0]["Q1"] == 100.0
    assert out[1]["Q1"] == 100.0    # (100 + 100) / 2
    assert out[2]["Q1"] == 105.0    # (100 + 110) / 2


# --- _build_quadrant_performance: empty / insufficient history --------------------

def test_empty_series_unavailable():
    out = _build_quadrant_performance([], {"Q3": ("GLD",)})
    assert out == {"available": False, "note": "no perf series yet"}


def test_single_point_series_all_windows_null():
    series = [_perf_point(_d(0), 100_000.0, 500.0, None, closes={"GLD": 300.0}, favored=[])]
    out = _build_quadrant_performance(series, {"Q3": ("GLD",)})
    b = out["buckets"]["Q3"]
    assert b["ret_30d_pct"] is None and b["ret_60d_pct"] is None and b["ret_90d_pct"] is None
    assert out["spy_ret_30d_pct"] is None


# --- window returns + excess vs SPY -----------------------------------------------

def test_window_return_and_excess_vs_spy():
    series = [
        _perf_point(_d(30), 100_000.0, 500.0, None, closes={"GLD": 300.0}, favored=[]),
        _perf_point(_d(0), 100_000.0, 525.0, None, closes={"GLD": 330.0}, favored=[]),
    ]
    out = _build_quadrant_performance(series, {"Q3": ("GLD",)})
    b = out["buckets"]["Q3"]
    assert b["ret_30d_pct"] == 10.0                  # 330/300 -> +10%
    assert out["spy_ret_30d_pct"] == 5.0              # 525/500 -> +5%
    assert b["excess_30d_pp"] == 5.0
    # only 30d of history -> 60d/90d insufficient
    assert b["ret_60d_pct"] is None and b["ret_90d_pct"] is None


# --- favored_streak / union buckets / absence reset -------------------------------

def test_union_favored_bucket_advances_both_streaks():
    rows = [(i, 100.0 - (5 - i), 100.0 + (5 - i), ["Q3", "Q4"]) for i in range(6)]
    series = _daily_series(list(reversed(rows)))   # chronological: oldest days_ago first
    out = _build_quadrant_performance(
        series, {"Q3": ("GLD",), "Q4": ("GLD",)}, {"suspect_after_sessions": 3},
    )
    assert out["buckets"]["Q3"]["favored_streak"] == 6
    assert out["buckets"]["Q4"]["favored_streak"] == 6
    assert out["favored_today"] == ["Q3", "Q4"]


def test_absence_resets_streak():
    rows = [
        (5, 100.0, 100.0, ["Q3"]),
        (4, 99.0, 101.0, ["Q3"]),
        (3, 98.0, 102.0, []),          # NOT favored this day -> resets
        (2, 97.0, 103.0, ["Q3"]),
        (1, 96.0, 104.0, ["Q3"]),
        (0, 95.0, 105.0, ["Q3"]),
    ]
    series = _daily_series(rows)
    out = _build_quadrant_performance(series, {"Q3": ("GLD",)})
    assert out["buckets"]["Q3"]["favored_streak"] == 3   # only days_ago 2, 1, 0


def test_empty_favored_bucket_never_favored():
    rows = [(i, 100.0, 100.0, []) for i in range(5, -1, -1)]
    series = _daily_series(rows)
    out = _build_quadrant_performance(series, {"Q3": ("GLD",)})
    b = out["buckets"]["Q3"]
    assert b["favored_streak"] == 0
    assert b["streak_excess_pp"] is None
    assert b["lagging_sessions"] == 0
    assert b["suspect"] is False
    assert out["favored_today"] == []


# --- lagging_sessions hysteresis: 9 -> not suspect, 10 -> suspect, recovery resets -

def _losing_streak_rows(length: int) -> list[tuple]:
    """GLD monotonically loses vs a monotonically-rising SPY, every day favored --
    day 0 of the streak nets a 0 excess (anchor == itself); every subsequent day is
    negative, so a streak of `length` days yields lagging_sessions == length - 1."""
    return [
        (i, 100.0 - (length - 1 - i), 100.0 + (length - 1 - i), ["Q3"])
        for i in range(length - 1, -1, -1)
    ]


def test_nine_lagging_sessions_not_yet_suspect():
    series = _daily_series(_losing_streak_rows(10))   # streak_len 10 -> lagging 9
    out = _build_quadrant_performance(series, {"Q3": ("GLD",)}, {"suspect_after_sessions": 10})
    b = out["buckets"]["Q3"]
    assert b["favored_streak"] == 10
    assert b["lagging_sessions"] == 9
    assert b["suspect"] is False


def test_ten_lagging_sessions_trips_suspect():
    series = _daily_series(_losing_streak_rows(11))   # streak_len 11 -> lagging 10
    out = _build_quadrant_performance(series, {"Q3": ("GLD",)}, {"suspect_after_sessions": 10})
    b = out["buckets"]["Q3"]
    assert b["favored_streak"] == 11
    assert b["lagging_sessions"] == 10
    assert b["suspect"] is True


def test_recovery_day_resets_suspect():
    # The 11-day losing streak (shifted one day back), then one more day where the
    # bucket is no longer favored at all.
    rows = _losing_streak_rows(11)
    shifted = [(days_ago + 1, gld, spy, fav) for days_ago, gld, spy, fav in rows]
    shifted.append((0, 80.0, 120.0, []))   # today: not favored -> hard reset
    series = _daily_series(shifted)
    out = _build_quadrant_performance(series, {"Q3": ("GLD",)}, {"suspect_after_sessions": 10})
    b = out["buckets"]["Q3"]
    assert b["favored_streak"] == 0
    assert b["lagging_sessions"] == 0
    assert b["suspect"] is False


# --- missing closes day: graceful null, no crash, base persists across the gap ---

def test_missing_closes_day_does_not_crash_and_base_persists():
    rows = [
        (3, 100.0, 100.0, ["Q3"]),   # streak start (anchor == itself)
        (2, None, 101.0, ["Q3"]),    # GLD price gap this day
        (1, 90.0, 102.0, ["Q3"]),    # base (day3=100) still applies
        (0, 85.0, 103.0, ["Q3"]),
    ]
    series = _daily_series(rows)
    out = _build_quadrant_performance(series, {"Q3": ("GLD",)}, {"suspect_after_sessions": 10})
    b = out["buckets"]["Q3"]
    assert b["favored_streak"] == 4
    # day with the gap resets the lagging run (streak_excess undefined that day);
    # final value still computes cleanly off the persisted day-3 base.
    assert b["streak_excess_pp"] == -18.0
    assert b["lagging_sessions"] == 2
