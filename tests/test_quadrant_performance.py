"""Quadrant-vs-SPY performance chart — pure logic tests.

Covers the collector side (roster closes extraction, extended perf points, the
self-healing closes/favored_bucket backfill) and the SWA API's equal-weight
quadrant basket index. Run:
    PYTHONPATH=src pytest tests/test_quadrant_performance.py
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import collector.handler as ch  # noqa: E402
from collector.handler import (  # noqa: E402
    _load_equity_spy_series,
    _perf_point,
    _roster_closes,
)

_API_PATH = os.path.join(os.path.dirname(__file__), "..", "web", "api", "function_app.py")
_spec = importlib.util.spec_from_file_location("swa_api", _API_PATH)
swa_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(swa_api)


# --- _roster_closes ------------------------------------------------------------

def test_roster_closes_filters_to_core_roster():
    prices = {
        "GLD": {"c": 310.5}, "TLT": {"c": 88.123456},
        "SPY": {"c": 620.0},         # core roster member (Q1 concentrate)
        "MU": {"c": 140.0},          # flex leftover, not core roster
        "QQQ": {},                   # no close today
    }
    out = _roster_closes(prices)
    assert out["GLD"] == 310.5
    assert out["TLT"] == 88.1235  # rounded to 4dp
    assert out["SPY"] == 620.0
    assert "MU" not in out and "QQQ" not in out


def test_roster_closes_empty_prices():
    assert _roster_closes(None) == {}
    assert _roster_closes({}) == {}


# --- _perf_point extension ------------------------------------------------------

def test_perf_point_backward_compatible_without_new_fields():
    p = _perf_point("2026-07-02", 100_000.0, 620.0, 25_000.0)
    assert "closes" not in p and "favored_bucket" not in p


def test_perf_point_carries_closes_and_favored():
    p = _perf_point("2026-07-02", 100_000.0, 620.0, None,
                    closes={"GLD": 310.0}, favored=["Q3", "Q4"])
    assert p["closes"] == {"GLD": 310.0}
    assert p["favored_bucket"] == ["Q3", "Q4"]


# --- _load_equity_spy_series: self-healing backfill ------------------------------

def _snap(equity, spy, gld, g_dir, i_dir):
    return {
        "paper_account": {"equity": equity, "cash": None},
        "prices": {"SPY": {"c": spy}, "GLD": {"c": gld}},
        "growth_axis": {"direction": g_dir},
        "inflation_axis": {"direction": i_dir},
    }


def test_series_patches_v1_points_and_appends_new(monkeypatch):
    # Cache holds a v1 point (no closes); one newer snapshot is unseen.
    v1 = _perf_point("2026-06-01", 100_000.0, 600.0, None)
    written = {}
    monkeypatch.setattr(ch, "read_perf_series", lambda: [v1])
    monkeypatch.setattr(ch, "list_snapshot_dates", lambda: ["2026-06-01", "2026-06-02"])
    snaps = {
        "2026-06-01": _snap(100_000.0, 600.0, 300.0, "falling", "falling"),
        "2026-06-02": _snap(101_000.0, 603.0, 305.0, "falling", "flat"),
    }
    monkeypatch.setattr(ch, "read_snapshot", lambda d: snaps[d])
    monkeypatch.setattr(ch, "write_perf_series", lambda s: written.update(series=s))

    out = _load_equity_spy_series(
        "2026-06-03", 102_000.0, 605.0, None,
        prices={"SPY": {"c": 605.0}, "GLD": {"c": 306.0}},
        growth_axis={"direction": "falling"}, inflation_axis={"direction": "rising"},
    )
    by_date = {p["date"]: p for p in out}
    # v1 point re-hydrated in place (closes + favored patched, equity kept).
    assert by_date["2026-06-01"]["closes"]["GLD"] == 300.0
    assert by_date["2026-06-01"]["favored_bucket"] == ["Q4"]
    assert by_date["2026-06-01"]["equity"] == 100_000.0
    # New prior day appended with fields; borderline axes -> two-quadrant bucket.
    assert by_date["2026-06-02"]["favored_bucket"] == ["Q3", "Q4"]
    # Today's point from in-memory values.
    assert by_date["2026-06-03"]["closes"]["GLD"] == 306.0
    assert by_date["2026-06-03"]["favored_bucket"] == ["Q3"]
    assert written  # cache persisted


def test_series_skips_already_hydrated_points(monkeypatch):
    hydrated = _perf_point("2026-06-01", 100_000.0, 600.0, None,
                           closes={"GLD": 300.0}, favored=["Q4"])
    monkeypatch.setattr(ch, "read_perf_series", lambda: [hydrated])
    monkeypatch.setattr(ch, "list_snapshot_dates", lambda: ["2026-06-01"])
    monkeypatch.setattr(
        ch, "read_snapshot",
        lambda d: (_ for _ in ()).throw(AssertionError("must not re-read hydrated point")),
    )
    monkeypatch.setattr(
        ch, "write_perf_series",
        lambda s: (_ for _ in ()).throw(AssertionError("nothing changed — must not write")),
    )
    out = _load_equity_spy_series("2026-06-02", None, None, None)
    assert out == [hydrated]


# --- API _quadrant_series ---------------------------------------------------------

def test_quadrant_index_equal_weight():
    pts = [
        {"closes": {"A": 100.0, "B": 200.0}},
        {"closes": {"A": 110.0, "B": 190.0}},  # +10% and -5% -> +2.5%
    ]
    out = swa_api._quadrant_series(pts, {"Q1": ["A", "B"]})
    assert out[0]["Q1"] == 100.0
    assert out[1]["Q1"] == 102.5


def test_quadrant_index_none_when_no_members_priced():
    pts = [{"closes": {"A": 100.0}}, {"closes": {"A": 101.0}}]
    out = swa_api._quadrant_series(pts, {"Q2": ["X", "Y"]})
    assert out == [{"Q2": None}, {"Q2": None}]


def test_late_appearing_member_bases_at_first_appearance():
    # B has no close on day 0; its base is day 1's 50.0, so day 2 it contributes
    # +10% rather than a spurious level shift.
    pts = [
        {"closes": {"A": 100.0}},
        {"closes": {"A": 100.0, "B": 50.0}},
        {"closes": {"A": 100.0, "B": 55.0}},
    ]
    out = swa_api._quadrant_series(pts, {"Q1": ["A", "B"]})
    assert out[0]["Q1"] == 100.0
    assert out[1]["Q1"] == 100.0            # (100 + 100) / 2
    assert out[2]["Q1"] == 105.0            # (100 + 110) / 2


def test_member_missing_a_day_is_skipped_that_day():
    pts = [
        {"closes": {"A": 100.0, "B": 200.0}},
        {"closes": {"A": 104.0}},            # B unpriced -> average over A only
    ]
    out = swa_api._quadrant_series(pts, {"Q1": ["A", "B"]})
    assert out[1]["Q1"] == 104.0


def test_overlapping_membership_both_quadrants():
    pts = [{"closes": {"GLD": 100.0}}, {"closes": {"GLD": 108.0}}]
    out = swa_api._quadrant_series(pts, {"Q3": ["GLD"], "Q4": ["GLD"]})
    assert out[1]["Q3"] == 108.0 and out[1]["Q4"] == 108.0
