"""Unit tests for the deterministic divergence detector (responsiveness brief Phase 2).

A divergence is a TENSION between two signals that should agree but don't. The detector
only DESCRIBES tensions — it never resolves, ranks, or acts on them (that is the LLM's job
in Phase 4). These tests assert: each divergence fires `active` with the correct
direction_implied on crafted disagreeing inputs; NO false positives on aligned inputs; and
a stale/absent input yields `status: "indeterminate"`, never a false `active`. Run:
    PYTHONPATH=src pytest tests/test_divergences.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import (  # noqa: E402
    _build_divergences,
    _div_credit_complacency,
    _div_dollar_vs_intl,
    _div_leading_vs_lagging_inflation,
    _div_price_vs_regime,
    _load_divergence_config,
    _sma_from_rows,
)

CFG = _load_divergence_config()
TODAY = "2026-06-30"


def _by_id(divs, did):
    return next(d for d in divs if d["id"] == did)


# --- #1 leading vs lagging inflation ----------------------------------------

def test_inflation_leading_falling_vs_realized_flat_fires():
    """Breakevens + oil falling while realized core is flat -> active, 'falling'."""
    infl = {"direction": "flat", "oil_wti_20d_pct": -21.0}
    bonds = {"breakevens": {"be_5y": {"delta_20d_bp": -28.0}}}
    d = _div_leading_vs_lagging_inflation(infl, bonds, CFG)
    assert d["status"] == "active"
    assert d["direction_implied"] == "falling"


def test_inflation_leading_rising_vs_realized_falling_fires():
    infl = {"direction": "falling", "oil_wti_20d_pct": 18.0}
    bonds = {"breakevens": {"be_5y": {"delta_20d_bp": 20.0}}}
    d = _div_leading_vs_lagging_inflation(infl, bonds, CFG)
    assert d["status"] == "active"
    assert d["direction_implied"] == "rising"


def test_inflation_aligned_no_false_positive():
    """Leading and realized agree (both falling) -> not active."""
    infl = {"direction": "falling", "oil_wti_20d_pct": -21.0}
    bonds = {"breakevens": {"be_5y": {"delta_20d_bp": -28.0}}}
    d = _div_leading_vs_lagging_inflation(infl, bonds, CFG)
    assert d["status"] == "indeterminate"
    assert d["direction_implied"] == "aligned"


def test_inflation_small_leading_move_does_not_fire():
    """Sub-threshold breakeven + oil move -> leading 'flat' -> no tension."""
    infl = {"direction": "flat", "oil_wti_20d_pct": -3.0}
    bonds = {"breakevens": {"be_5y": {"delta_20d_bp": -5.0}}}
    d = _div_leading_vs_lagging_inflation(infl, bonds, CFG)
    assert d["status"] == "indeterminate"


def test_inflation_missing_realized_is_indeterminate():
    infl = {"direction": None, "oil_wti_20d_pct": -21.0}
    bonds = {"breakevens": {"be_5y": {"delta_20d_bp": -28.0}}}
    d = _div_leading_vs_lagging_inflation(infl, bonds, CFG)
    assert d["status"] == "indeterminate"
    assert d["direction_implied"] == "unresolved"


# --- #2 credit complacency ---------------------------------------------------

def test_credit_complacency_fires_on_low_level_and_calm():
    """Level < 3.5% + calm (no stress, shock<=1) -> fires fragility/active. Note the
    percentile is mid-range (49) — the level gate must fire regardless, which is exactly
    the blind spot the old percentile gate had in a persistently tight-spread regime."""
    bonds = {"credit": {"hy_oas": {"latest": 2.83, "pct_rank_90d": 49},
                        "credit_stress": {"flag": False}}}
    d = _div_credit_complacency(bonds, {"shock_level": 0}, CFG)
    assert d["status"] == "active"
    assert d["direction_implied"] == "fragility"


def test_credit_low_level_but_shock_does_not_fire():
    """Level < 3.5 but shock >= 2 -> not latent complacency (stress is corroborating)."""
    bonds = {"credit": {"hy_oas": {"latest": 2.83, "pct_rank_90d": 5},
                        "credit_stress": {"flag": False}}}
    d = _div_credit_complacency(bonds, {"shock_level": 2}, CFG)
    assert d["status"] == "indeterminate"


def test_credit_low_level_but_stress_flag_does_not_fire():
    bonds = {"credit": {"hy_oas": {"latest": 2.8, "pct_rank_90d": 5},
                        "credit_stress": {"flag": True}}}
    d = _div_credit_complacency(bonds, {"shock_level": 0}, CFG)
    assert d["status"] == "indeterminate"


def test_credit_level_at_or_above_band_does_not_fire():
    """Level >= 3.5% is not complacent (normal/stress zone) -> indeterminate."""
    bonds = {"credit": {"hy_oas": {"latest": 3.5, "pct_rank_90d": 5},
                        "credit_stress": {"flag": False}}}
    d = _div_credit_complacency(bonds, {"shock_level": 0}, CFG)
    assert d["status"] == "indeterminate"


def test_credit_missing_level_indeterminate():
    bonds = {"credit": {"hy_oas": {"latest": None, "pct_rank_90d": 5}}}
    d = _div_credit_complacency(bonds, {}, CFG)
    assert d["status"] == "indeterminate"
    assert d["direction_implied"] == "unresolved"


# --- #3 price vs regime ------------------------------------------------------

def _rr_spy(date="2026-06-29"):
    return {"tickers": {"SPY": {"latest_date": date}}}


def test_price_above_200d_vs_defensive_quadrant_fires():
    sma = {"available": True, "sma": 700.0, "latest": 741.0, "latest_date": "2026-06-29", "above": True}
    d = _div_price_vs_regime(sma, {"active_quadrant": "Q3"}, _rr_spy(), TODAY, 7)
    assert d["status"] == "active"
    assert d["direction_implied"] == "price_risk_on_vs_defensive_call"


def test_price_below_200d_vs_riskon_quadrant_fires():
    sma = {"available": True, "sma": 700.0, "latest": 650.0, "latest_date": "2026-06-29", "above": False}
    d = _div_price_vs_regime(sma, {"active_quadrant": "Q1"}, _rr_spy(), TODAY, 7)
    assert d["status"] == "active"
    assert d["direction_implied"] == "price_risk_off_vs_riskon_call"


def test_price_aligned_no_false_positive():
    """Above 200d + risk-on quadrant -> agree -> no tension."""
    sma = {"available": True, "sma": 700.0, "latest": 741.0, "latest_date": "2026-06-29", "above": True}
    d = _div_price_vs_regime(sma, {"active_quadrant": "Q1"}, _rr_spy(), TODAY, 7)
    assert d["status"] == "indeterminate"
    assert d["direction_implied"] == "aligned"


def test_price_borderline_quadrant_is_indeterminate():
    """No single quadrant (None / borderline) -> nothing to disagree with."""
    sma = {"available": True, "sma": 700.0, "latest": 741.0, "latest_date": "2026-06-29", "above": True}
    d = _div_price_vs_regime(sma, {"active_quadrant": None}, _rr_spy(), TODAY, 7)
    assert d["status"] == "indeterminate"
    assert d["direction_implied"] == "unresolved"


def test_price_sma_unavailable_is_indeterminate():
    d = _div_price_vs_regime({"available": False}, {"active_quadrant": "Q3"}, _rr_spy(), TODAY, 7)
    assert d["status"] == "indeterminate"


def test_price_stale_spy_is_indeterminate():
    """SPY price older than the staleness window -> indeterminate, never a false active."""
    sma = {"available": True, "sma": 700.0, "latest": 741.0, "latest_date": "2026-05-01", "above": True}
    d = _div_price_vs_regime(sma, {"active_quadrant": "Q3"}, _rr_spy("2026-05-01"), TODAY, 7)
    assert d["status"] == "indeterminate"


# --- #4 dollar vs international tilt -----------------------------------------

def _paper_intl(intl_pct, equity=100_000.0):
    # AIA is an amplifier-intl name; load it to the requested aggregate weight.
    return {"available": True, "equity": equity,
            "positions": [{"ticker": "AIA", "market_value": equity * intl_pct / 100.0}]}


def _rr_dxy(tag, chg=1.0, date="2026-06-26"):
    return {"dxy_tailwind_for_intl": tag, "dxy_60d_pct_change": chg, "dxy_latest_date": date}


def test_dollar_headwind_but_heavy_intl_fires():
    d = _div_dollar_vs_intl(_paper_intl(25.0), _rr_dxy("neutral"), TODAY, 7, CFG)
    assert d["status"] == "active"
    assert d["direction_implied"] == "toward_us_growth"


def test_dollar_tailwind_but_light_intl_fires():
    d = _div_dollar_vs_intl(_paper_intl(4.0), _rr_dxy("tailwind", chg=-5.0), TODAY, 7, CFG)
    assert d["status"] == "active"
    assert d["direction_implied"] == "toward_international"


def test_dollar_aligned_no_false_positive():
    """Neutral dollar + mid intl weight -> no tension (the 2026-06-30 case ~10.6%)."""
    d = _div_dollar_vs_intl(_paper_intl(10.6), _rr_dxy("neutral"), TODAY, 7, CFG)
    assert d["status"] == "indeterminate"
    assert d["direction_implied"] == "aligned"


def test_dollar_stale_dxy_is_indeterminate():
    d = _div_dollar_vs_intl(_paper_intl(25.0), _rr_dxy("neutral", date="2026-05-01"), TODAY, 7, CFG)
    assert d["status"] == "indeterminate"


def test_dollar_unavailable_paper_is_indeterminate():
    d = _div_dollar_vs_intl({"available": False}, _rr_dxy("neutral"), TODAY, 7, CFG)
    assert d["status"] == "indeterminate"


# --- _sma_from_rows helper ---------------------------------------------------

def test_sma_insufficient_rows_unavailable():
    rows = [{"date": "2026-06-29", "price": 741.0}] * 50  # < 200
    assert _sma_from_rows(rows, 200)["available"] is False


def test_sma_computes_and_flags_above():
    rows = [{"date": "2026-06-29", "price": 800.0}] + [{"date": "x", "price": 700.0}] * 199
    out = _sma_from_rows(rows, 200)
    assert out["available"] is True
    assert out["above"] is True
    assert abs(out["sma"] - ((800.0 + 700.0 * 199) / 200)) < 1e-6


# --- end-to-end shape --------------------------------------------------------

def test_build_divergences_returns_all_four_with_schema():
    divs = _build_divergences(
        _paper_intl(10.0), {"direction": "falling"}, {"direction": "flat", "oil_wti_20d_pct": -21.0},
        {"breakevens": {"be_5y": {"delta_20d_bp": -28.0}}, "credit": {"hy_oas": {"latest": 2.8, "pct_rank_90d": 49}}},
        _rr_dxy("neutral"), {"active_quadrant": None}, {"shock_level": 0},
        {"available": False}, TODAY, CFG,
    )
    # 2026-07-23: 6 divergences (4 original + 2 new from #17/#18)
    assert len(divs) == 6
    ids = {d["id"] for d in divs}
    assert ids == {
        "leading_vs_lagging_inflation", "credit_complacency",
        "price_vs_regime", "dollar_vs_intl_tilt",
        "leading_vs_lagging_growth", "market_vs_macro_quadrant",
    }
    for d in divs:
        assert set(d) >= {"id", "description", "signals", "direction_implied", "status"}
        assert d["status"] in ("active", "indeterminate")
