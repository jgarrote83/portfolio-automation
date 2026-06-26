"""Unit tests for the conviction-sleeve flex review (collector handler) + the
entry-metadata round-trip (analyzer handler).

Covers the full dual-benchmark `review_status` matrix (`_classify_flex_review`),
the integration builder (`_build_flex_review`) including the missing-data→unknown
and regime-fit-lost paths and the beats-ETF-lags-SPY-in-a-bull → review_due
regression, the entry-metadata persistence round-trip, and the prompt-contract
regressions (missing-data→WATCH, catalyst/mispricing gates removed). Run:
    PYTHONPATH=src pytest tests/test_flex_review.py
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import (  # noqa: E402
    _build_flex_review,
    _classify_flex_review,
)

CFG = {"REVIEW_DAYS": 60, "LAG_TOL_PP": -2.0, "BREAK_PP": -5.0,
       "EXTENSION_DAYS": 30, "DEADBAND_PP": 1.0}


def _classify(days, etf, spy, spy_ret, fit_lost=False):
    return _classify_flex_review(
        days_held=days, excess_vs_etf_pp=etf, excess_vs_spy_pp=spy,
        spy_return_since_entry_pct=spy_ret, regime_fit_lost=fit_lost, cfg=CFG,
    )


# --- classifier matrix -------------------------------------------------------

def test_within_holding_window_is_ok_regardless_of_performance():
    assert _classify(30, -20, -20, 5.0)["review_status"] == "ok"


def test_beats_both_is_ok():
    assert _classify(90, 3.0, 3.0, 5.0)["review_status"] == "ok"


def test_lags_both_binding_beyond_break_is_breaking():
    r = _classify(90, -8.0, -8.0, 5.0)  # spy rising -> spy binds, -8 < -5
    assert r["review_status"] == "breaking"
    assert r["binding_benchmark"] == "spy"


def test_lags_both_binding_within_break_is_review_due():
    assert _classify(90, -3.0, -3.0, 5.0)["review_status"] == "review_due"


def test_beats_etf_lags_spy_bull_is_review_due():
    """Key regression: the Q1 case — beating the quadrant but not the mission."""
    r = _classify(90, 3.0, -3.0, 5.0)
    assert r["review_status"] == "review_due"
    assert r["binding_benchmark"] == "spy"


def test_beats_etf_lags_spy_drawdown_is_ok():
    """Drawdown: ETF binds; beating the sleeve is the win even if lagging SPY."""
    r = _classify(90, 3.0, -3.0, -5.0)
    assert r["review_status"] == "ok"
    assert r["binding_benchmark"] == "etf"


def test_beats_spy_lags_etf_drawdown_is_review_due():
    """Closet beta: ETF binds in a drawdown; rode the sleeve, added nothing."""
    r = _classify(90, -3.0, 3.0, -5.0)
    assert r["review_status"] == "review_due"
    assert r["binding_benchmark"] == "etf"


def test_beats_spy_lags_etf_bull_is_ok_flagged():
    r = _classify(90, -3.0, 3.0, 5.0)
    assert r["review_status"] == "ok_flagged"
    assert r["binding_benchmark"] == "spy"


def test_regime_fit_lost_forces_breaking_regardless():
    assert _classify(90, 10.0, 10.0, 5.0, fit_lost=True)["review_status"] == "breaking"
    # overrides the holding window too
    assert _classify(10, 10.0, 10.0, 5.0, fit_lost=True)["review_status"] == "breaking"


def test_spy_flat_within_deadband_binds_spy():
    r = _classify(90, 3.0, 3.0, 0.5)  # within ±1.0 deadband
    assert r["spy_direction"] == "flat"
    assert r["binding_benchmark"] == "spy"


# --- integration builder -----------------------------------------------------

class _FakeFMP:
    def __init__(self, series):
        self.series = series  # {sym: {date: price}}

    def get_historical_price_light(self, symbol):
        return [{"date": d, "price": p} for d, p in sorted(self.series.get(symbol, {}).items())]


_Q1 = ({"direction": "rising"}, {"direction": "falling"})    # active quadrant Q1
_Q3 = ({"direction": "falling"}, {"direction": "rising"})    # active quadrant Q3
_TODAY = date(2026, 6, 26)


def test_build_beats_etf_lags_spy_bull_review_due():
    """End-to-end: full entry metadata, Q1, beats QQQ, lags a rising SPY → review_due."""
    fmp = _FakeFMP({
        "ZZZ": {"2026-01-01": 100.0, "2026-06-26": 110.0},   # +10%
        "QQQ": {"2026-01-01": 100.0, "2026-06-26": 106.0},   # +6%  -> excess +4
        "SPY": {"2026-01-01": 100.0, "2026-06-26": 113.0},   # +13% -> excess -3 (within break)
    })
    rows = [{"layer": "flex", "side": "buy", "symbol": "ZZZ",
             "entry_date": "2026-01-01", "entry_price": 100.0,
             "flex_benchmark_etf": "QQQ", "entry_quadrant": "Q1"}]
    pa = {"positions": [{"ticker": "ZZZ", "qty": 10, "current_price": 110.0}]}
    out = _build_flex_review(fmp, pa, rows, _Q1[0], _Q1[1], CFG, today=_TODAY)
    n = out["names"][0]
    assert n["review_status"] == "review_due"
    assert n["binding_benchmark"] == "spy"
    assert round(n["excess_vs_etf_pp"], 1) == 4.0
    assert round(n["excess_vs_spy_pp"], 1) == -3.0


def test_build_missing_entry_data_is_unknown():
    """A held flex name with no entry metadata (e.g. predates the feature) → unknown."""
    rows = [{"layer": "flex", "side": "buy", "symbol": "MU"}]
    pa = {"positions": [{"ticker": "MU", "qty": 2, "current_price": 1100.0}]}
    out = _build_flex_review(_FakeFMP({}), pa, rows, _Q1[0], _Q1[1], CFG, today=_TODAY)
    assert out["names"][0]["review_status"] == "unknown"


def test_build_regime_fit_lost_forces_breaking():
    """Entered Q1, active quadrant now Q3 → regime fit lost → breaking despite gains."""
    fmp = _FakeFMP({
        "ZZZ": {"2026-01-01": 100.0, "2026-06-26": 150.0},
        "QQQ": {"2026-01-01": 100.0, "2026-06-26": 105.0},
        "SPY": {"2026-01-01": 100.0, "2026-06-26": 105.0},
    })
    rows = [{"layer": "flex", "side": "buy", "symbol": "ZZZ",
             "entry_date": "2026-01-01", "entry_price": 100.0,
             "flex_benchmark_etf": "QQQ", "entry_quadrant": "Q1"}]
    pa = {"positions": [{"ticker": "ZZZ", "qty": 10, "current_price": 150.0}]}
    out = _build_flex_review(fmp, pa, rows, _Q3[0], _Q3[1], CFG, today=_TODAY)
    n = out["names"][0]
    assert n["review_status"] == "breaking"
    assert n["regime_fit_lost"] is True


def test_build_core_position_not_reviewed():
    """A held position with no flex BUY row is core — not in the review."""
    pa = {"positions": [{"ticker": "SPY", "qty": 5, "current_price": 700.0}]}
    out = _build_flex_review(_FakeFMP({}), pa, [], _Q1[0], _Q1[1], CFG, today=_TODAY)
    assert out["names"] == []


# --- entry-metadata round-trip (analyzer) ------------------------------------

def test_entry_metadata_round_trips(monkeypatch):
    import analyzer.handler as ah
    captured = []
    monkeypatch.setattr(ah, "upsert_entity", lambda table, entity: captured.append(entity))
    snapshot = {
        "prices": {"ZZZ": {"c": 123.45}},
        "growth_axis": {"direction": "rising"},
        "inflation_axis": {"direction": "falling"},  # -> Q1 -> QQQ
    }
    trades_obj = {
        "quadrant_current": "Q1",
        "trades": [{"id": "T-1", "side": "buy", "symbol": "ZZZ", "layer": "flex",
                    "quantity": 5, "confidence": 0.7}],
    }
    ah._write_trade_history("2026-06-26", trades_obj, snapshot)
    e = captured[0]
    assert e["entry_price"] == 123.45
    assert e["flex_benchmark_etf"] == "QQQ"
    assert e["entry_quadrant"] == "Q1"
    assert e["entry_date"] == "2026-06-26"


def test_core_trade_gets_no_entry_metadata(monkeypatch):
    import analyzer.handler as ah
    captured = []
    monkeypatch.setattr(ah, "upsert_entity", lambda table, entity: captured.append(entity))
    trades_obj = {"trades": [{"id": "T-2", "side": "buy", "symbol": "SPY",
                              "layer": "core", "quantity": 5}]}
    ah._write_trade_history("2026-06-26", trades_obj, {"prices": {}})
    assert "entry_price" not in captured[0]


# --- prompt-contract regressions (NEE-class) ---------------------------------

def _prompt_text():
    p = os.path.join(os.path.dirname(__file__), "..", "src", "config",
                     "project-instructions.md")
    with open(p, encoding="utf-8") as f:
        return f.read()


def test_prompt_missing_data_is_watch_not_reject():
    t = _prompt_text()
    assert "Missing data → WATCH" in t


def test_prompt_removed_catalyst_and_mispricing_gates():
    t = _prompt_text()
    assert "no catalyst gate and no mispricing gate" in t


def test_prompt_has_conviction_entry_gates():
    t = _prompt_text()
    assert "G1 — Regime fit (hard)" in t
    assert "G2 — Quality (hard)" in t
    assert "G3 — Opportunity cost vs the active-quadrant ETF (hard)" in t
