"""Pipeline tests for build_flex_entry — the deterministic confirmation gates.

Run: PYTHONPATH=src pytest tests/test_flex_entry.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flex.config import FlexConfig  # noqa: E402
from flex.entry import build_flex_entry  # noqa: E402

CFG = FlexConfig()
EQUITY = 1_000_000.0


def _daily(n=20, base=100.0, rng=0.6, v=1_000_000):
    return [{"o": base, "h": base + rng / 2, "l": base - rng / 2, "c": base, "v": v} for _ in range(n)]


def _intraday(closes, rng=0.2, v=1000):
    return [{"o": c, "h": c + rng / 2, "l": c - rng / 2, "c": c, "v": v} for c in closes]


def _run(intraday, daily, sector="Technology", quadrant="Q1", minutes=45):
    return build_flex_entry(
        {"symbol": "NVDA", "sector": sector},
        intraday, daily, quadrant, EQUITY, minutes, CFG,
    )


def test_pass_above_rising_vwap_acceptable_stop():
    r = _run(_intraday([100, 100.5, 101, 101.5, 102, 102.5, 103]), _daily())
    assert r["entry_trigger"] == "pass"
    assert r["size_shares"] >= 1
    assert r["above_vwap"] is True
    assert r["binding"] in ("risk_budget", "per_name_cap")


def test_below_vwap_fails():
    r = _run(_intraday([103, 102.5, 102, 101.5, 101, 100.5, 100]), _daily())
    assert r["entry_trigger"] == "fail"
    assert r["skip_reason"] == "below_vwap"


def test_stop_too_wide_skips():
    r = _run(_intraday([100, 100.5, 101, 101.5, 102, 102.5, 103]), _daily(rng=12.0))
    assert r["entry_trigger"] == "fail"
    assert r["skip_reason"] == "stop_too_wide"


def test_liquidity_below_min_rejected():
    r = _run(_intraday([100, 100.5, 101, 101.5, 102, 102.5, 103]), _daily(v=1))
    assert r["skip_reason"] == "liquidity_below_min"


def test_pre_window_and_after_cutoff():
    bars = _intraday([100, 100.5, 101, 101.5, 102, 102.5, 103])
    assert _run(bars, _daily(), minutes=10)["skip_reason"] == "pre_window"
    assert _run(bars, _daily(), minutes=120)["skip_reason"] == "after_cutoff"


def test_regime_fit_fail():
    r = _run(_intraday([100, 100.5, 101, 101.5, 102, 102.5, 103]), _daily(), sector="Utilities")
    assert r["entry_trigger"] == "fail"
    assert r["skip_reason"].startswith("regime_fit")


def test_big_gap_strong_vwap_passes():
    # Open gaps +2% (gap_in_adr ~3.3 > 2) but holds a rising VWAP well above → pass.
    r = _run(_intraday([102, 102.4, 102.8, 103.2, 103.6, 104.0, 104.4]), _daily())
    assert r["gap_in_adr"] > CFG.gap_adr_mult
    assert r["entry_trigger"] == "pass"


def test_big_gap_vwap_fail_skips():
    r = _run(_intraday([102, 101.5, 101, 100.5, 100, 99.5, 99]), _daily())
    assert r["gap_in_adr"] > CFG.gap_adr_mult
    assert r["skip_reason"] == "below_vwap"


def test_big_gap_weak_hold_skips():
    # Big gap, above VWAP but only barely (< 0.1×ATR) → weak hold, not a clean entry.
    r = _run(_intraday([102, 102, 102, 102, 102, 102, 102.02]), _daily(rng=0.5))
    assert r["gap_in_adr"] > CFG.gap_adr_mult
    assert r["above_vwap"] is True
    assert r["skip_reason"] == "big_gap_weak_hold"
