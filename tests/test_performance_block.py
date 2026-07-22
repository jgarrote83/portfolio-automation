"""Unit tests for the Phase C §4 performance-scoreboard pure logic.

Covers `_build_performance` (return-since-inception, rolling-window availability,
max drawdown) and the `_perf_point` cash_pct math — neither observable live until
enough funded snapshots accumulate. Run:
    PYTHONPATH=src pytest tests/test_performance_block.py
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_performance, _perf_point  # noqa: E402


def _series(points):
    """[(days_ago, equity, spy)] -> sorted compact series ending today."""
    today = date.today()
    out = []
    for days_ago, eq, spy in points:
        d = (today - timedelta(days=days_ago)).isoformat()
        out.append(_perf_point(d, eq, spy, None))
    out.sort(key=lambda p: p["date"])
    return out


# --- _perf_point -------------------------------------------------------------

def test_perf_point_cash_pct():
    p = _perf_point("2026-06-25", 100_000.0, 640.0, 25_000.0)
    assert p["cash_pct"] == 25.0
    assert p["equity"] == 100_000.0
    assert p["spy_close"] == 640.0


def test_perf_point_cash_pct_none_when_no_cash():
    assert _perf_point("2026-06-25", 100_000.0, 640.0, None)["cash_pct"] is None


# --- _build_performance: empty ----------------------------------------------

def test_empty_series_unavailable():
    out = _build_performance([])
    assert out["available"] is False


# --- since-inception + excess ------------------------------------------------

def test_since_inception_excess_vs_spy():
    # account +10%, SPY +4% over the window -> excess +6pp.
    series = _series([(40, 100_000.0, 100.0), (0, 110_000.0, 104.0)])
    out = _build_performance(series)
    assert out["available"] is True
    assert out["return_since_inception_pct"] == 10.0
    assert out["spy_return_since_inception_pct"] == 4.0
    assert out["excess_vs_spy_pp"] == 6.0
    assert out["days_live"] == 40


def test_account_block_surfaces_cash_pct():
    today = date.today()
    series = [
        _perf_point((today - timedelta(days=10)).isoformat(), 100_000.0, 100.0, 50_000.0),
        _perf_point(today.isoformat(), 101_000.0, 101.0, 20_000.0),
    ]
    out = _build_performance(series)
    # cash_pct comes from the latest point.
    assert out["account"]["cash_pct"] == round(20_000.0 / 101_000.0 * 100, 2)
    assert out["account"]["equity"] == 101_000.0


# --- rolling windows ---------------------------------------------------------

def test_rolling_window_null_when_history_too_short():
    # Only 10 days of history -> 30/60/90d windows predate inception -> null.
    series = _series([(10, 100_000.0, 100.0), (0, 105_000.0, 102.0)])
    out = _build_performance(series)
    for n in ("30d", "60d", "90d"):
        assert out["rolling"][n]["account_pct"] is None
        assert out["rolling"][n]["excess_pp"] is None


def test_rolling_30d_computed_when_history_present():
    # Points at 35d (before the 30d mark), 30d, and today.
    series = _series([
        (35, 100_000.0, 100.0),
        (30, 100_000.0, 100.0),
        (0, 106_000.0, 103.0),
    ])
    out = _build_performance(series)
    r = out["rolling"]["30d"]
    # snaps to the 30d-ago point (100k / 100): account +6%, SPY +3% -> +3pp.
    assert r["account_pct"] == 6.0
    assert r["spy_pct"] == 3.0
    assert r["excess_pp"] == 3.0


# --- max drawdown ------------------------------------------------------------

def test_max_drawdown_peak_to_trough():
    # Peak 110k then trough 99k -> dd = (99/110 - 1)*100 = -10.0%.
    series = _series([
        (40, 100_000.0, 100.0),
        (30, 110_000.0, 101.0),
        (20, 99_000.0, 99.0),
        (0, 105_000.0, 102.0),
    ])
    out = _build_performance(series)
    assert out["max_drawdown_pct"] == -10.0


def test_no_drawdown_when_monotonic():
    series = _series([(20, 100_000.0, 100.0), (10, 101_000.0, 101.0), (0, 102_000.0, 102.0)])
    assert _build_performance(series)["max_drawdown_pct"] == 0.0


# --- note --------------------------------------------------------------------

def test_note_flags_sub_year_history():
    series = _series([(20, 100_000.0, 100.0), (0, 101_000.0, 101.0)])
    assert "not yet available" in _build_performance(series)["note"]


# --- B5: excess_attribution (two-term decomposition) -------------------------

def _series_cash(points, sgov=100.0):
    """[(days_ago, equity, spy, cash_pct)] → series with a flat SGOV close series."""
    today = date.today()
    out = []
    for days_ago, eq, spy, cash_pct in points:
        d = (today - timedelta(days=days_ago)).isoformat()
        out.append(_perf_point(d, eq, spy, eq * cash_pct / 100.0, closes={"SGOV": sgov}))
    out.sort(key=lambda p: p["date"])
    return out


def test_excess_attribution_cash_helps_when_spy_negative():
    # Book flat (0%), SPY −1.13%, cash 30% flat → cash ADDS excess (positive contribution).
    series = _series_cash([(40, 100_000.0, 100.0, 30.0), (0, 100_000.0, 98.87, 30.0)])
    ea = _build_performance(series)["excess_attribution"]["inception"]
    assert ea["cash_contribution_pp"] > 0
    assert ea["invested_contribution_pp"] < ea["excess_pp"]  # invested carries the rest
    # the two terms sum to the excess (exact residual).
    assert abs(ea["cash_contribution_pp"] + ea["invested_contribution_pp"] - ea["excess_pp"]) < 0.01


def test_excess_attribution_cash_drags_when_spy_positive():
    # SPY +10%, cash 30% flat → cash is a genuine DRAG (negative contribution).
    series = _series_cash([(40, 100_000.0, 100.0, 30.0), (0, 108_000.0, 110.0, 30.0)])
    ea = _build_performance(series)["excess_attribution"]["inception"]
    assert ea["cash_contribution_pp"] < 0
    assert ea["avg_cash_pct"] == 30.0
    assert abs(ea["cash_contribution_pp"] + ea["invested_contribution_pp"] - ea["excess_pp"]) < 0.01


def test_excess_attribution_windows_present():
    series = _series_cash([(40, 100_000.0, 100.0, 30.0), (30, 100_000.0, 100.0, 30.0),
                           (0, 101_000.0, 99.0, 30.0)])
    ea = _build_performance(series)["excess_attribution"]
    assert ea["inception"]["window"] == "inception"
    assert ea["30d"]["window"] == "30d"
