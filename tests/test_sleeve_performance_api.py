"""Flex Sleeve Performance Ledger (2026-08-10) Task D —
web/api/function_app.py's `_sleeve_series` / `_attach_sleeve_series`.

Run: PYTHONPATH=src;web/api pytest tests/test_sleeve_performance_api.py
(also requires web/api on sys.path, added below)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "web", "api"))

import function_app  # noqa: E402


# --- _sleeve_series: cumulative-from-window-start, never index-normalized ---

def test_sleeve_series_cumulative_from_window_start():
    points = [
        {"date": "2026-08-10", "total_equity": 100_000, "cumulative_realized_usd": 500.0,
         "unrealized_usd": 0.0, "closed_trades_to_date": 3},
        {"date": "2026-08-11", "total_equity": 100_000, "cumulative_realized_usd": 800.0,
         "unrealized_usd": 50.0, "closed_trades_to_date": 4},
    ]
    rows = function_app._sleeve_series(points)
    # day 1 baseline: (500+0)/100000*100 = 0.5% -> contribution 0 at t0
    assert rows[0]["sleeve_contribution_pp"] == 0.0
    assert rows[0]["sleeve_trade_count"] == 0
    # day 2 raw: (800+50)/100000*100 = 0.85%; delta from 0.5% baseline = 0.35pp
    assert rows[1]["sleeve_contribution_pp"] == 0.35
    assert rows[1]["sleeve_trade_count"] == 1


def test_sleeve_series_gap_when_equity_missing_never_interpolated():
    points = [
        {"date": "2026-08-10", "total_equity": 100_000, "cumulative_realized_usd": 500.0,
         "unrealized_usd": 0.0, "closed_trades_to_date": 3},
        {"date": "2026-08-11", "total_equity": None, "cumulative_realized_usd": 800.0,
         "unrealized_usd": 50.0, "closed_trades_to_date": 4},
    ]
    rows = function_app._sleeve_series(points)
    assert rows[1]["sleeve_contribution_pp"] is None  # a gap, not a guess


def test_sleeve_series_empty_input():
    assert function_app._sleeve_series([]) == []


def test_sleeve_series_never_index_normalized_to_100():
    # A buy-and-hold index would start every series at 100; the sleeve panel
    # must never do that -- confirm the first point is 0 (a delta), not 100.
    points = [{"date": "2026-08-10", "total_equity": 50_000, "cumulative_realized_usd": 0.0,
               "unrealized_usd": 0.0, "closed_trades_to_date": 0}]
    rows = function_app._sleeve_series(points)
    assert rows[0]["sleeve_contribution_pp"] == 0.0
    assert rows[0]["sleeve_contribution_pp"] != 100


# --- _attach_sleeve_series: graceful degradation ----------------------------

def test_attach_sleeve_series_absent_blob_leaves_response_unchanged(monkeypatch):
    monkeypatch.setattr(function_app, "_download_json", lambda *a, **k: None)
    payload = {"series": [{"date": "2026-08-10"}]}
    original_series = list(payload["series"])
    function_app._attach_sleeve_series(payload, "2026-08-01")
    assert payload["series"] == original_series  # untouched
    assert payload["sleeve_available"] is False
    assert payload["sleeve_closed_trade_count_total"] == 0


def test_attach_sleeve_series_malformed_blob_degrades_gracefully(monkeypatch):
    monkeypatch.setattr(function_app, "_download_json", lambda *a, **k: {"not": "a list"})
    payload = {"series": [{"date": "2026-08-10"}]}
    function_app._attach_sleeve_series(payload, "2026-08-01")
    assert payload["sleeve_available"] is False


def test_attach_sleeve_series_populates_matching_dates(monkeypatch):
    flex_series = [
        {"date": "2026-08-10", "total_equity": 100_000, "cumulative_realized_usd": 500.0,
         "unrealized_usd": 0.0, "closed_trades_to_date": 3},
        {"date": "2026-08-11", "total_equity": 100_000, "cumulative_realized_usd": 800.0,
         "unrealized_usd": 50.0, "closed_trades_to_date": 4},
    ]
    monkeypatch.setattr(function_app, "_download_json", lambda *a, **k: flex_series)
    payload = {"series": [{"date": "2026-08-10"}, {"date": "2026-08-11"}, {"date": "2026-08-12"}]}
    function_app._attach_sleeve_series(payload, "2026-08-01")
    assert payload["sleeve_available"] is True
    assert payload["sleeve_closed_trade_count_total"] == 4
    assert payload["series"][0]["sleeve_contribution_pp"] == 0.0
    assert payload["series"][1]["sleeve_contribution_pp"] == 0.35
    # A date the flex series doesn't cover gets no sleeve fields at all --
    # never a fabricated 0.
    assert "sleeve_contribution_pp" not in payload["series"][2]


def test_attach_sleeve_series_respects_cutoff(monkeypatch):
    flex_series = [
        {"date": "2026-01-01", "total_equity": 100_000, "cumulative_realized_usd": 500.0,
         "unrealized_usd": 0.0, "closed_trades_to_date": 3},
        {"date": "2026-08-10", "total_equity": 100_000, "cumulative_realized_usd": 800.0,
         "unrealized_usd": 50.0, "closed_trades_to_date": 4},
    ]
    monkeypatch.setattr(function_app, "_download_json", lambda *a, **k: flex_series)
    payload = {"series": [{"date": "2026-08-10"}]}
    function_app._attach_sleeve_series(payload, "2026-08-01")  # excludes the Jan point
    # Only the in-window point is the baseline -> contribution 0 at its own date.
    assert payload["series"][0]["sleeve_contribution_pp"] == 0.0


def test_attach_sleeve_series_download_exception_degrades(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("blob read failed")
    monkeypatch.setattr(function_app, "_download_json", _boom)
    payload = {"series": [{"date": "2026-08-10"}]}
    function_app._attach_sleeve_series(payload, "2026-08-01")  # must not raise
    assert payload["sleeve_available"] is False
