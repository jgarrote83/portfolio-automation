"""Flex-sleeve performance review (collector handler) + the entry-metadata
round-trip (analyzer handler).

**Rewritten session 2026-09-12 (B1/R1) — the review is SINGLE-benchmark (SPY).**
It was a dual-benchmark matrix: SPY bound in a rising/flat tape, the
active-quadrant ETF bound in a drawdown, and `regime_fit_lost` forced a cut when
the regime moved away from the entry quadrant. Both the ETF leg and the forced
cut depended on a macro quadrant, and regime is now removed from the flex sleeve
entirely. G3's opportunity-cost benchmark is RE-POINTED to SPY, not deleted.

What that deletes, stated so a future reader sees it was deliberate:
  - the drawdown carve-out (`beats_etf_lags_spy_drawdown_is_ok`) — a flex name
    that falls less than a falling SPY no longer earns an "ok" on that basis;
  - the closet-beta catch (`beats_spy_lags_etf_drawdown_is_review_due`);
  - `ok_flagged` (mission met, lagging the sleeve) — it has no second benchmark
    left to lag, so the status is unreachable and no longer produced;
  - `regime_fit_lost` forcing `breaking`.

Covers the surviving matrix, the integration builder (`_build_flex_review`)
including the missing-data→unknown path, and the entry-metadata round-trip.
Run: PYTHONPATH=src pytest tests/test_flex_review.py
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


def _classify(days, spy, spy_ret):
    return _classify_flex_review(
        days_held=days, excess_vs_spy_pp=spy,
        spy_return_since_entry_pct=spy_ret, cfg=CFG,
    )


# --- classifier matrix (SPY-only) --------------------------------------------

def test_within_holding_window_is_ok_regardless_of_performance():
    assert _classify(30, -20, 5.0)["review_status"] == "ok"


def test_beats_spy_is_ok():
    assert _classify(90, 3.0, 5.0)["review_status"] == "ok"


def test_lags_spy_beyond_break_is_breaking():
    r = _classify(90, -8.0, 5.0)   # -8 < BREAK_PP -5
    assert r["review_status"] == "breaking"
    assert r["binding_benchmark"] == "spy"


def test_lags_spy_within_break_is_review_due():
    assert _classify(90, -3.0, 5.0)["review_status"] == "review_due"


def test_lag_tolerance_absorbs_noise():
    """AHEAD := excess >= LAG_TOL_PP (-2.0), so a small lag still reads ok."""
    assert _classify(90, -2.0, 5.0)["review_status"] == "ok"
    assert _classify(90, -2.01, 5.0)["review_status"] == "review_due"


def test_spy_binds_in_every_tape_including_a_drawdown():
    """R1: the drawdown carve-out is GONE. Lagging SPY by more than the break
    threshold is `breaking` even while SPY itself is falling — previously this
    was `ok` because the quadrant ETF bound in a drawdown."""
    r = _classify(90, -8.0, -5.0)
    assert r["spy_direction"] == "falling"
    assert r["binding_benchmark"] == "spy"
    assert r["review_status"] == "breaking"


def test_spy_direction_is_reported_but_never_selects_a_benchmark():
    for spy_ret, want_dir in ((5.0, "rising"), (0.5, "flat"), (-5.0, "falling")):
        r = _classify(90, 3.0, spy_ret)
        assert r["spy_direction"] == want_dir
        assert r["binding_benchmark"] == "spy"


def test_ok_flagged_is_unreachable_without_a_second_benchmark():
    """`ok_flagged` meant "ahead of SPY but lagging the quadrant ETF". With one
    benchmark there is nothing to be flagged against, so it must never appear."""
    seen = {
        _classify(d, x, s)["review_status"]
        for d in (10, 90) for x in (-20, -8, -3, -2, 0, 3, 20) for s in (-5.0, 0.5, 5.0)
    }
    assert "ok_flagged" not in seen
    assert seen <= {"ok", "review_due", "breaking"}


# --- integration builder -----------------------------------------------------

class _FakeFMP:
    def __init__(self, series):
        self.series = series  # {sym: {date: price}}

    def get_historical_price_light(self, symbol):
        return [{"date": d, "price": p} for d, p in sorted(self.series.get(symbol, {}).items())]


_TODAY = date(2026, 6, 26)


def test_build_lags_rising_spy_is_review_due():
    """End-to-end: +10% vs a +13% SPY over the window → -3pp → review_due."""
    fmp = _FakeFMP({
        "ZZZ": {"2026-01-01": 100.0, "2026-06-26": 110.0},   # +10%
        "SPY": {"2026-01-01": 100.0, "2026-06-26": 113.0},   # +13%
    })
    pa = {"positions": [{"ticker": "ZZZ", "qty": 10, "current_price": 110.0}]}
    rows = [{"layer": "flex", "side": "buy", "symbol": "ZZZ",
             "entry_date": "2026-01-01", "entry_price": 100.0}]
    out = _build_flex_review(fmp, pa, rows, CFG, today=_TODAY)
    n = out["names"][0]
    assert n["review_status"] == "review_due"
    assert round(n["excess_vs_spy_pp"], 1) == -3.0
    assert n["benchmark_etf"] == "SPY"
    # R1: the quadrant-shaped fields are gone from the output entirely.
    for gone in ("excess_vs_etf_pp", "entry_quadrant", "active_quadrant",
                 "regime_fit_lost", "benchmark_return_since_entry_pct"):
        assert gone not in n, gone


def test_build_needs_no_quadrant_arguments():
    """The builder's signature no longer takes growth/inflation axes at all —
    pinned so a future change cannot quietly reintroduce a regime input."""
    import inspect
    params = set(inspect.signature(_build_flex_review).parameters)
    assert "growth_axis" not in params and "inflation_axis" not in params


def test_build_missing_entry_data_is_unknown():
    pa = {"positions": [{"ticker": "ZZZ", "qty": 10, "current_price": 110.0}]}
    rows = [{"layer": "flex", "side": "buy", "symbol": "ZZZ"}]   # no entry_date/price
    out = _build_flex_review(_FakeFMP({}), pa, rows, CFG, today=_TODAY)
    assert out["names"][0]["review_status"] == "unknown"
    assert out["names"][0]["benchmark_etf"] == "SPY"


def test_build_missing_price_series_is_unknown():
    pa = {"positions": [{"ticker": "ZZZ", "qty": 10, "current_price": 110.0}]}
    rows = [{"layer": "flex", "side": "buy", "symbol": "ZZZ",
             "entry_date": "2026-01-01", "entry_price": 100.0}]
    out = _build_flex_review(_FakeFMP({}), pa, rows, CFG, today=_TODAY)
    n = out["names"][0]
    assert n["review_status"] == "unknown"
    assert "SPY" in n["missing"]


def test_build_core_position_not_reviewed():
    pa = {"positions": [{"ticker": "SPY", "qty": 10, "current_price": 100.0}]}
    out = _build_flex_review(_FakeFMP({}), pa, [], CFG, today=_TODAY)
    assert out["names"] == []


# --- entry-metadata round-trip (analyzer) ------------------------------------

def test_entry_metadata_round_trips(monkeypatch):
    """R1: the flex BUY still stamps `entry_date`/`entry_price` write-once, but
    NO LONGER stamps `entry_quadrant`/`flex_benchmark_etf` — nothing reads them
    and they were the analyzer's last regime dependency on the flex path."""
    import analyzer.handler as ah
    captured = []
    monkeypatch.setattr(ah, "upsert_entity", lambda table, entity: captured.append(entity))
    snapshot = {
        "prices": {"ZZZ": {"c": 123.45}},
        "growth_axis": {"direction": "rising"},
        "inflation_axis": {"direction": "falling"},
    }
    trades_obj = {
        "quadrant_current": "Q1",
        "trades": [{"id": "T-1", "side": "buy", "symbol": "ZZZ", "layer": "flex",
                    "quantity": 5, "confidence": 0.7}],
    }
    ah._write_trade_history("2026-06-26", trades_obj, snapshot)
    e = captured[0]
    assert e["entry_price"] == 123.45
    assert e["entry_date"] == "2026-06-26"
    assert "flex_benchmark_etf" not in e
    assert "entry_quadrant" not in e


def test_core_trade_gets_no_entry_metadata(monkeypatch):
    import analyzer.handler as ah
    captured = []
    monkeypatch.setattr(ah, "upsert_entity", lambda table, entity: captured.append(entity))
    trades_obj = {"trades": [{"id": "T-2", "side": "buy", "symbol": "SPY",
                              "layer": "core", "quantity": 5}]}
    ah._write_trade_history("2026-06-26", trades_obj, {"prices": {}})
    assert "entry_price" not in captured[0]
