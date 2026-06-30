"""Unit tests for the deterministic reference-weight precompute (strategy-spec §10).

Covers _conviction_proxy and _build_reference_weights (collector handler) + the
favored_bucket / intersection helpers in shared.quadrants. The reference is the
"precomputed target weights the LLM executes toward" — the layer whose absence let the
2026-06-30 book hold 31% Q1 growth beta in a falling-growth regime and call inaction
"discipline". Acceptance criteria mirror the implementation brief Phase 1. Run:
    PYTHONPATH=src pytest tests/test_reference_weights.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import (  # noqa: E402
    _build_reference_weights,
    _conviction_proxy,
    _load_risk_limits,
)
from shared.quadrants import favored_bucket, intersection_names  # noqa: E402

CFG = _load_risk_limits()


def _axes(growth, inflation, growth_conf="high"):
    return {"direction": growth, "confidence": growth_conf}, {"direction": inflation}


def _gate(status, stance="neutral"):
    return {"status": status, "derived_from": {"policy_stance": stance}}


def _paper(weights, equity=100_000.0, cash_pct=2.0):
    positions = [
        {"ticker": t, "qty": 1.0, "market_value": equity * w / 100.0}
        for t, w in weights.items()
    ]
    return {
        "available": True,
        "equity": equity,
        "cash": equity * cash_pct / 100.0,
        "positions": positions,
    }


def _rot(dxy_tag="neutral"):
    return {"dxy_tailwind_for_intl": dxy_tag}


def _build(paper, g, i, gate, rot=None, bond=None, labor=None, shock=None):
    return _build_reference_weights(
        paper, g, i, gate, rot or _rot(), bond or {}, labor or {}, shock or {}, CFG
    )


def _total(rw):
    return sum(rw["target_weights_pct"].values()) + rw.get("literal_cash_target_pct", 0.0)


# --- conviction proxy --------------------------------------------------------

def test_proxy_clean_riskon_is_high_conviction():
    """Gate open, both axes pinned, policy confirmed, no flags -> low score (high conv)."""
    g, i = _axes("rising", "falling")
    p = _conviction_proxy(g, i, _gate("open", "dovish"), {}, {}, {})
    assert p["score"] <= 2.5


def test_proxy_contradicted_regime_is_low_conviction():
    """Gate closed + flat inflation + unconfirmed policy + shock 3 -> high score."""
    g, i = _axes("falling", "flat")
    p = _conviction_proxy(g, i, _gate("closed", "unconfirmed"), {}, {}, {"shock_level": 3})
    assert p["score"] >= 6
    assert any("gate closed" in d for d in p["drivers"])
    assert any("shock level 3" in d for d in p["drivers"])


def test_proxy_monotonic_more_stress_higher_score():
    g, i = _axes("rising", "falling")
    base = _conviction_proxy(g, i, _gate("open", "dovish"), {}, {}, {})["score"]
    worse = _conviction_proxy(
        g, i, _gate("closed", "unconfirmed"), {"scorecard": {"composite": -4}},
        {"scorecard": {"composite": -4}}, {"shock_level": 2},
    )["score"]
    assert worse > base


# --- favored_bucket / intersection helpers -----------------------------------

def test_favored_bucket_flat_inflation_defensive_union():
    assert favored_bucket("falling", "flat") == ["Q3", "Q4"]
    assert favored_bucket("rising", "falling") == ["Q1"]


def test_intersection_q3_q4_includes_gld():
    """GLD concentrates in both Q3 and Q4 -> in the intersection; TLT (Q4-only) not."""
    inter = intersection_names(["Q3", "Q4"])
    assert "GLD" in inter
    assert "TLT" not in inter  # Q4 only


# --- reference weights: structural acceptance criteria -----------------------

def test_sums_to_about_100():
    g, i = _axes("rising", "falling")
    rw = _build(_paper({"SPY": 20, "QQQ": 20, "SGOV": 10}), g, i, _gate("open", "dovish"))
    assert abs(_total(rw) - 100.0) < 0.5


def test_floor_respected_no_sleeve_below_floor():
    g, i = _axes("rising", "falling")  # Q1: damper names go to floor
    rw = _build(_paper({"SPY": 30, "GLD": 20, "TLT": 10, "SGOV": 10}), g, i, _gate("open", "dovish"))
    # every core ETF that is out-of-quadrant sits at (scaled) floor, never 0
    assert rw["target_weights_pct"]["TLT"] > 0
    assert rw["target_weights_pct"]["TLT"] < 1.0  # floored, not concentrated


def test_ceiling_respected_active_quadrant_target_capped():
    """High conviction must not drive the active-quadrant target past the 90%-of-core ceiling."""
    g, i = _axes("rising", "falling")  # Q1, very high conviction
    rw = _build(_paper({"SPY": 20, "SGOV": 10}), g, i, _gate("open", "dovish"))
    assert rw["active_quadrant_target_pct_of_core"] <= CFG["active_quadrant_ceiling_pct_of_core"]


def test_amzn_googl_never_forced_below_current():
    """Exempt holds keep at least their current weight even in an out-of-favor (Q3) regime."""
    g, i = _axes("falling", "rising")  # Q3 — AMZN/GOOGL are Q1, would otherwise floor
    paper = _paper({"AMZN": 6.0, "GOOGL": 7.0, "GLD": 10, "SGOV": 10})
    rw = _build(paper, g, i, _gate("closed", "neutral"))
    assert rw["target_weights_pct"]["AMZN"] >= 6.0 - 0.5
    assert rw["target_weights_pct"]["GOOGL"] >= 7.0 - 0.5


def test_q3_trims_spy_qqq_to_floor():
    """THE regression: in a falling-growth regime SPY/QQQ are trimmed toward the floor,
    not held at their large current weights (the 2026-06-30 failure)."""
    g, i = _axes("falling", "rising")  # Q3
    paper = _paper({"SPY": 17.0, "QQQ": 14.0, "GLD": 5, "SGOV": 10})
    rw = _build(paper, g, i, _gate("closed", "neutral"))
    assert rw["target_weights_pct"]["SPY"] < 1.0
    assert rw["target_weights_pct"]["QQQ"] < 1.0
    # ...and the Q3 concentrate names get materially more than the floor.
    assert rw["target_weights_pct"]["GLD"] > 5.0


def test_conviction_scaling_low_score_concentrates_harder():
    """Lower proxy score (higher conviction) -> higher active-quadrant target."""
    g, i = _axes("rising", "falling")
    high_conv = _build(_paper({"SPY": 20, "SGOV": 10}), g, i, _gate("open", "dovish"))
    # Degrade conviction via shock + unconfirmed policy (same quadrant).
    low_conv = _build(
        _paper({"SPY": 20, "SGOV": 10}), g, i, _gate("open", "unconfirmed"),
        shock={"shock_level": 2},
    )
    assert (high_conv["active_quadrant_target_pct_of_core"]
            > low_conv["active_quadrant_target_pct_of_core"])


def test_dollar_tilt_falling_dxy_favors_international():
    """Falling dollar (tailwind) tilts the Q1 amplifier toward intl vs US growth."""
    g, i = _axes("rising", "falling")  # Q1
    paper = _paper({"SPY": 10, "QQQ": 10, "AIA": 5, "EWJ": 5, "SGOV": 10})
    intl = _build(paper, g, i, _gate("open", "dovish"), rot=_rot("tailwind"))
    us = _build(paper, g, i, _gate("open", "dovish"), rot=_rot("headwind"))
    assert intl["dollar_tilt"] == "international"
    assert us["dollar_tilt"] == "us_growth"
    # intl leg (AIA+EWJ) should get a bigger share under a tailwind than under a headwind
    intl_share = intl["target_weights_pct"].get("AIA", 0) + intl["target_weights_pct"].get("EWJ", 0)
    us_share = us["target_weights_pct"].get("AIA", 0) + us["target_weights_pct"].get("EWJ", 0)
    assert intl_share > us_share


def test_borderline_blend_is_specific_not_degenerate():
    """A flat inflation axis -> intersection blend, sums ~100, not all-cash/all-one-name."""
    g, i = _axes("falling", "flat")  # Q3/Q4 borderline
    rw = _build(_paper({"SPY": 17, "QQQ": 14, "GLD": 5, "SGOV": 10}), g, i, _gate("closed", "neutral"))
    assert rw["borderline"] is True
    assert rw["favored_bucket"] == ["Q3", "Q4"]
    assert abs(_total(rw) - 100.0) < 0.5
    tw = rw["target_weights_pct"]
    # GLD (Q3∩Q4 intersection) gets a meaningful weight; not degenerate.
    assert tw["GLD"] > 5.0
    # no single non-cash name dominates the whole book
    non_cash = {k: v for k, v in tw.items() if k != "SGOV"}
    assert max(non_cash.values()) < 60.0


def test_cash_band_above_ceiling_deploys_surplus():
    """A 25% cash sleeve (above the 15% ceiling, no shock) is pulled toward the ceiling,
    freeing surplus for the core."""
    g, i = _axes("rising", "falling")
    paper = _paper({"SPY": 20, "SGOV": 25.0}, cash_pct=2.0)  # sleeve ~27%
    rw = _build(paper, g, i, _gate("open", "dovish"))  # no shock
    assert rw["cash_sleeve_target_pct"] <= CFG["cash_sleeve_band_pct"]["ceiling"] + 0.01
    assert "cash_above_band" in rw["binding"]


def test_cash_band_shock3_allows_expansion():
    """Shock level 3 lifts the cash ceiling toward 25%."""
    g, i = _axes("falling", "flat")
    paper = _paper({"GLD": 10, "SGOV": 23.0}, cash_pct=2.0)  # sleeve ~25%
    rw = _build(paper, g, i, _gate("closed", "unconfirmed"), shock={"shock_level": 3})
    assert rw["cash_sleeve_target_pct"] <= CFG["cash_sleeve_band_pct"]["shock3_ceiling"] + 0.01
    assert rw["cash_sleeve_target_pct"] > CFG["cash_sleeve_band_pct"]["ceiling"]


def test_cash_band_below_floor_lifts_to_floor():
    """A near-zero sleeve is lifted to the 5% floor."""
    g, i = _axes("rising", "falling")
    paper = _paper({"SPY": 30, "QQQ": 30, "SGOV": 0.5}, cash_pct=0.5)  # sleeve ~1%
    rw = _build(paper, g, i, _gate("open", "dovish"))
    assert rw["cash_sleeve_target_pct"] >= CFG["cash_sleeve_band_pct"]["floor"] - 0.01
    assert "cash_below_band" in rw["binding"]


def test_unavailable_paper_account():
    g, i = _axes("rising", "falling")
    rw = _build_reference_weights({"available": False}, g, i, _gate("open"), {}, {}, {}, {}, CFG)
    assert rw["available"] is False


def test_off_roster_name_not_given_core_target():
    """A held flex leftover (MU) is not on the core roster -> absent from core targets."""
    g, i = _axes("rising", "falling")
    paper = _paper({"SPY": 20, "MU": 5, "SGOV": 10})
    rw = _build(paper, g, i, _gate("open", "dovish"))
    assert "MU" not in rw["target_weights_pct"]
