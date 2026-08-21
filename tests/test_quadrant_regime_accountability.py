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


# ---------------------------------------------------------------------------
# 2026-08-21 quadrant-reachability audit, Task E (F7 fix) — reachable
# accountability. The legacy `lagging_sessions >= suspect_after_sessions`
# path requires a single unbroken run of negative-streak-excess sessions; a
# call that flips favored on/off every 3-5 sessions (the observed 2026-05-26
# .. 08-21 pattern) can never survive to `suspect_after_sessions` (10) even
# while genuinely losing to SPY every time it's checked. A NEW rolling path
# (trailing_excess_pp_N / favored_sessions_N) fires on sustained underperformance
# regardless of favored-bucket continuity.
# ---------------------------------------------------------------------------

_QP_CFG = {
    "suspect_after_sessions": 10,
    "trailing_window_sessions": 20,
    "min_favored_sessions": 5,
    "suspect_excess_threshold_pp": 0.0,
}


def _whipsaw_rolling_suspect_rows() -> list[tuple]:
    """20 sessions, Q3/GLD favored on exactly 8 SCATTERED (never a run > 2)
    sessions including today. GLD flat at 100 the whole window except the
    final session (99.0); SPY flat at 100 except the final session (101.0) --
    net trailing window excess = -1.0 - (+1.0) = -2.0pp. Because almost every
    day is flat vs its OWN prior day, the legacy per-session streak_excess is
    never negative except the very last day -> lagging_sessions stays <= 1,
    nowhere near suspect_after_sessions=10 -- yet the bucket has genuinely
    lagged SPY by 2pp while favored on 8 of the last 20 sessions."""
    favored_idx = {0, 3, 6, 9, 12, 15, 18, 19}
    rows = []
    for idx in range(20):
        days_ago = 19 - idx
        gld = 99.0 if idx == 19 else 100.0
        spy = 101.0 if idx == 19 else 100.0
        fav = ["Q3"] if idx in favored_idx else []
        rows.append((days_ago, gld, spy, fav))
    return rows


def test_rolling_path_trips_suspect_where_legacy_streak_never_would():
    series = _daily_series(_whipsaw_rolling_suspect_rows())
    out = _build_quadrant_performance(series, {"Q3": ("GLD",)}, _QP_CFG)
    b = out["buckets"]["Q3"]
    assert b["favored_sessions_20"] == 8
    assert b["trailing_excess_pp_20"] == -2.0
    assert b["lagging_sessions"] <= 3   # legacy path nowhere near 10
    assert b["suspect"] is True         # rolling path fires anyway


def test_rolling_path_absent_reverts_to_legacy_only_behavior():
    """On master (no trailing_window_sessions/min_favored_sessions config,
    i.e. the OLD suspect_after_sessions-only cfg), the same whipsaw fixture
    must NOT trip suspect -- proves the rolling path is what changed the
    outcome, not some other side effect."""
    series = _daily_series(_whipsaw_rolling_suspect_rows())
    out = _build_quadrant_performance(series, {"Q3": ("GLD",)}, {"suspect_after_sessions": 10})
    assert out["buckets"]["Q3"]["suspect"] is False


def test_trailing_excess_not_reset_by_an_intermediate_up_session():
    """5-session trailing window with an interior POSITIVE day sandwiched
    between declines -- trailing_excess_pp_N is a plain start-to-end window
    calc (mirrors ret_Nd_pct/excess_Nd_pp semantics), never a
    reset-on-non-negative streak scan like the legacy lagging_sessions is."""
    rows = [
        (4, 100.0, 100.0, []),
        (3, 90.0, 100.0, []),     # GLD down hard
        (2, 95.0, 100.0, []),     # interior UP day (would reset a streak scan)
        (1, 85.0, 100.0, []),
        (0, 80.0, 105.0, ["Q3"]),  # today: favored
    ]
    series = _daily_series(rows)
    cfg = {**_QP_CFG, "trailing_window_sessions": 5, "min_favored_sessions": 1}
    out = _build_quadrant_performance(series, {"Q3": ("GLD",)}, cfg)
    b = out["buckets"]["Q3"]
    # window start->end: GLD 100->80 (-20%), SPY 100->105 (+4.76...%)
    expected = round((80.0 / 100.0 - 1.0) * 100.0 - (105.0 / 100.0 - 1.0) * 100.0, 3)
    assert b["trailing_excess_pp_5"] == expected
    assert expected < 0


def test_legacy_streak_path_still_fires_independently():
    """Regression guard: with min_favored_sessions set impossibly high, the
    rolling path CANNOT fire -- suspect must still trip purely off the
    pre-existing lagging_sessions>=suspect_after_sessions path."""
    series = _daily_series(_losing_streak_rows(11))   # streak_len 11 -> lagging 10
    cfg = {"suspect_after_sessions": 10, "trailing_window_sessions": 20,
           "min_favored_sessions": 999, "suspect_excess_threshold_pp": 0.0}
    out = _build_quadrant_performance(series, {"Q3": ("GLD",)}, cfg)
    b = out["buckets"]["Q3"]
    assert b["lagging_sessions"] == 10
    assert b["suspect"] is True


def test_cumulative_favored_excess_hand_computed():
    """4 sessions, GLD [100, 102, 101, 105], SPY [100, 101, 101, 102].
    Favored on sessions 1 and 3 only (index 2 excluded).
    Day1: GLD +2.0%, SPY +1.0% -> excess +1.0pp (favored, included).
    Day2: GLD -0.9804%, SPY 0% -> excess -0.9804pp (NOT favored, excluded).
    Day3: GLD +3.9604%, SPY +0.9901% -> excess +2.9703pp (favored, included).
    cumulative = 1.0 + 2.9702970297... ~= 3.970."""
    rows = [
        (3, 100.0, 100.0, []),
        (2, 102.0, 101.0, ["Q3"]),
        (1, 101.0, 101.0, []),
        (0, 105.0, 102.0, ["Q3"]),
    ]
    series = _daily_series(rows)
    out = _build_quadrant_performance(series, {"Q3": ("GLD",)}, _QP_CFG)
    val = out["buckets"]["Q3"]["cumulative_favored_excess_pp"]
    assert abs(val - 3.970297) < 0.001


def test_cumulative_favored_excess_sign_correct_when_lagging():
    """Mirror fixture: favored sessions where GLD underperforms SPY ->
    cumulative_favored_excess_pp must be NEGATIVE."""
    rows = [
        (3, 100.0, 100.0, []),
        (2, 99.0, 101.0, ["Q3"]),    # GLD -1%, SPY +1% -> excess -2pp, favored
        (1, 99.0, 101.0, []),
        (0, 98.0, 102.0, ["Q3"]),    # GLD -1.0101%, SPY +0.9901% -> excess ~-2.0pp, favored
    ]
    series = _daily_series(rows)
    out = _build_quadrant_performance(series, {"Q3": ("GLD",)}, _QP_CFG)
    assert out["buckets"]["Q3"]["cumulative_favored_excess_pp"] < 0


# ---------------------------------------------------------------------------
# 2026-08-21 SWA lean-visibility cycle, Task A (premise correction): the SWA
# API (`web/api/function_app.py`) is a SEPARATE deployment with NO access to
# `risk-limits.json` (verified: zero references to it or `src/config`
# anywhere in `web/api/*.py`), so it cannot itself determine which suspect
# path fired without re-deriving thresholds it doesn't have. `suspect_path`
# is computed HERE, once, from the same two booleans `suspect` itself already
# combines -- so it can never disagree with `suspect`, and the API only ever
# echoes it.
# ---------------------------------------------------------------------------

def test_suspect_path_streak_only():
    series = _daily_series(_losing_streak_rows(11))   # lagging=10, legacy fires
    cfg = {"suspect_after_sessions": 10}               # rolling NOT configured
    out = _build_quadrant_performance(series, {"Q3": ("GLD",)}, cfg)
    b = out["buckets"]["Q3"]
    assert b["suspect"] is True
    assert b["suspect_path"] == "streak"


def test_suspect_path_rolling_only():
    series = _daily_series(_whipsaw_rolling_suspect_rows())  # lagging<=3, rolling fires
    out = _build_quadrant_performance(series, {"Q3": ("GLD",)}, _QP_CFG)
    b = out["buckets"]["Q3"]
    assert b["suspect"] is True
    assert b["lagging_sessions"] < _QP_CFG["suspect_after_sessions"]
    assert b["suspect_path"] == "rolling"


def test_suspect_path_both():
    """A long enough monotonic losing streak trips BOTH the legacy streak
    (lagging >= 10) and the rolling window (favored_sessions_20 >= 5 AND
    trailing_excess_pp_20 < 0) simultaneously."""
    series = _daily_series(_losing_streak_rows(21))   # lagging=20, favored every day
    out = _build_quadrant_performance(series, {"Q3": ("GLD",)}, _QP_CFG)
    b = out["buckets"]["Q3"]
    assert b["lagging_sessions"] >= _QP_CFG["suspect_after_sessions"]
    assert b["favored_sessions_20"] >= _QP_CFG["min_favored_sessions"]
    assert b["trailing_excess_pp_20"] < 0
    assert b["suspect"] is True
    assert b["suspect_path"] == "both"


def test_suspect_path_none_when_not_suspect():
    series = _daily_series([(i, 100.0, 100.0, []) for i in range(5, -1, -1)])
    out = _build_quadrant_performance(series, {"Q3": ("GLD",)}, _QP_CFG)
    b = out["buckets"]["Q3"]
    assert b["suspect"] is False
    assert b["suspect_path"] is None
