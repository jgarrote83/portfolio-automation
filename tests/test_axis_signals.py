"""Unit tests for the deterministic quadrant axes (collector handler).

Covers _build_growth_axis (GDPNow within-quarter vintage slope + cross-quarter
fallback), _build_inflation_axis (realized-core trend + oil-keyed energy overlay,
NOT the news-shock level), and _build_regime_gate. These remove the LLM discretion
that previously let the quadrant call anchor on its prior label. Run:
    PYTHONPATH=src pytest tests/test_axis_signals.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import (  # noqa: E402
    _build_growth_axis,
    _build_inflation_axis,
    _build_policy_axis,
    _build_regime_gate,
)


def _obs(values):
    """FRED-shaped rows (newest-first) from a newest-first list of values."""
    return [{"value": str(v)} for v in values]


def _vintages(values):
    """GDPNOW_VINTAGES rows (oldest-first)."""
    return [{"date": "2026-04-01", "asof": f"2026-04-{i:02d}", "value": str(v)}
            for i, v in enumerate(values, start=1)]


def _monthly_index(latest_yoy_pct, ann3_pct, n=18):
    """Synthesize a newest-first monthly index series with a target YoY and 3m-annualized.

    index[0]=latest=100; index[3] set so (100/index[3])**4-1 == ann3; index[12] set so
    (100/index[12]) - 1 == yoy. Other months filled by linear interpolation (only
    indices 0/3/12 are read by the builder)."""
    idx = [None] * n
    idx[0] = 100.0
    idx[3] = 100.0 / ((1 + ann3_pct / 100.0) ** 0.25)
    idx[12] = 100.0 / (1 + latest_yoy_pct / 100.0)
    # fill remaining so list is all floats (builder only reads 0,3,12 but be safe)
    for i in range(n):
        if idx[i] is None:
            idx[i] = 99.0
    return _obs(idx)


# --- growth axis -------------------------------------------------------------

def test_growth_falling_from_within_quarter_vintages():
    """The Q2 nowcast marked down 3.70 -> 2.54 is FALLING, even though Q/Q rose."""
    md = {
        "GDPNOW_VINTAGES": _vintages([3.70, 3.99, 4.26, 3.82, 3.02, 2.54]),
        "GDPNOW": _obs([2.54, 1.24]),  # cross-quarter would say 'rising' — must NOT win
    }
    g = _build_growth_axis(md)
    assert g["direction"] == "falling"
    assert g["confidence"] == "high"
    assert g["basis"] == "within_quarter_vintages"


def test_growth_rising_within_quarter():
    md = {"GDPNOW_VINTAGES": _vintages([1.0, 1.4, 2.0, 2.6])}
    assert _build_growth_axis(md)["direction"] == "rising"


def test_growth_flat_within_band():
    md = {"GDPNOW_VINTAGES": _vintages([2.50, 2.55, 2.45, 2.52])}
    assert _build_growth_axis(md)["direction"] == "flat"


def test_growth_cross_quarter_fallback_low_confidence():
    """<3 vintages -> fall back to quarterly slope, flagged low confidence."""
    md = {"GDPNOW_VINTAGES": _vintages([2.5]), "GDPNOW": _obs([2.54, 1.24])}
    g = _build_growth_axis(md)
    assert g["direction"] == "rising"
    assert g["confidence"] == "low"
    assert g["basis"] == "cross_quarter_fallback"


def test_growth_indeterminate_no_data():
    g = _build_growth_axis({})
    assert g["direction"] == "indeterminate"
    assert g["confidence"] == "none"


# --- inflation axis ----------------------------------------------------------

def test_inflation_flat_sticky_core():
    """core PCE 3m-ann ~ YoY -> flat, even with no oil/headline pressure."""
    md = {"PCEPILFE": _monthly_index(3.41, 3.52), "CPILFESL": _monthly_index(2.96, 3.17)}
    i = _build_inflation_axis(md)
    assert i["direction"] == "flat"


def test_inflation_falling_core_decelerating():
    md = {"PCEPILFE": _monthly_index(3.40, 2.10)}
    assert _build_inflation_axis(md)["direction"] == "falling"


def test_inflation_headline_hot_but_oil_collapsing_classifies_by_core():
    """The key bug fix: headline 4.27% rising must NOT force 'rising' when oil is
    collapsing — that headline is a rear-view oil artifact. Classify by core (flat)."""
    md = {
        "CPIAUCSL": _obs([100.0] + [99.0, 99.0] + [99.0] * 9 + [100.0 / 1.0427, 100.0 / 1.0395] + [99.0] * 4),
        "PCEPILFE": _monthly_index(3.41, 3.52),
        "DCOILWTICO": _obs([78.94] + [99.0] * 19 + [100.20] + [99.0] * 5),   # -21% over 20d
        "DCOILBRENTEU": _obs([76.49] + [99.0] * 19 + [106.90] + [99.0] * 5),  # -28% over 20d
    }
    i = _build_inflation_axis(md)
    assert i["direction"] != "rising"
    assert i["oil_wti_20d_pct"] is not None and i["oil_wti_20d_pct"] < -10


def test_inflation_rising_when_headline_hot_and_oil_rising():
    """Genuine energy push: headline elevated+rising AND oil rising -> 'rising'."""
    # headline YoY ~4.3% and rising: idx[0]=100, idx[12]=100/1.043; prev higher base
    head = [100.0, 99.5] + [99.0] * 10 + [100.0 / 1.043, 100.0 / 1.041] + [99.0] * 4
    md = {
        "CPIAUCSL": _obs(head),
        "PCEPILFE": _monthly_index(3.41, 3.52),
        "DCOILWTICO": _obs([120.0] + [99.0] * 19 + [100.0] + [99.0] * 5),   # +20% over 20d
    }
    i = _build_inflation_axis(md)
    assert i["direction"] == "rising"
    assert "energy" in i["reason"]


# --- policy axis (FOLLOWUPS #16) ----------------------------------------------

_TODAY = "2026-07-03"


def _dgs2(latest, day20, n=30):
    """Newest-first DGS2 rows: [0]=latest, [20]=day20, filler elsewhere."""
    vals = [day20] * n
    vals[0] = latest
    return _obs(vals)


def _policy(md, manual=None, cfg=None, today=_TODAY):
    return _build_policy_axis(md, manual or {}, cfg or {}, today)


def test_policy_market_implied_hawkish_at_threshold():
    """DGS2 +20bp/20d exactly meets the hawkish bar (inclusive)."""
    p = _policy({"DGS2": _dgs2(4.50, 4.30), "DFF": _obs([4.33])})
    assert p["stance"] == "hawkish"
    assert p["source"] == "market_implied"
    assert p["market_implied"]["dgs2_delta_20d_bp"] == 20.0
    assert p["market_implied"]["spread_bp"] == 17.0


def test_policy_market_implied_dovish_at_threshold():
    p = _policy({"DGS2": _dgs2(4.10, 4.30), "DFF": _obs([4.33])})
    assert p["stance"] == "dovish"
    assert p["market_implied"]["dgs2_delta_20d_bp"] == -20.0


def test_policy_market_implied_neutral_inside_band():
    """+19.9bp / -19.9bp stay neutral — the band is open below the thresholds."""
    for latest in (4.499, 4.101):
        p = _policy({"DGS2": _dgs2(latest, 4.30)})
        assert p["stance"] == "neutral", latest
        assert p["source"] == "market_implied"


def test_policy_market_implied_needs_21_obs():
    """20 DGS2 observations are not enough for the 20d delta -> unconfirmed."""
    p = _policy({"DGS2": _obs([4.5] * 20)})
    assert p["stance"] == "unconfirmed"
    assert p["source"] == "unconfirmed"
    assert p["market_implied"]["stance"] is None
    assert p["market_implied"]["dgs2_delta_20d_bp"] is None


def test_policy_manual_fresh_wins_over_market():
    """A fresh SEP/dot-plot stance governs even when the market proxy disagrees."""
    p = _policy(
        {"DGS2": _dgs2(4.50, 4.30)},   # market-implied: hawkish
        manual={"stance": "neutral", "as_of": "2026-06-18"},   # 15d old, fresh
    )
    assert p["stance"] == "neutral"
    assert p["source"] == "manual_fresh"
    assert p["manual"]["fresh"] is True
    assert p["agreement"] is False
    assert "DISAGREEMENT" in p["note"]


def test_policy_manual_stale_loses_to_market():
    p = _policy(
        {"DGS2": _dgs2(4.50, 4.30)},
        manual={"stance": "dovish", "as_of": "2026-03-01"},   # 124d old
    )
    assert p["stance"] == "hawkish"
    assert p["source"] == "market_implied"
    assert p["manual"]["fresh"] is False


def test_policy_manual_null_as_of_loses_to_market():
    """The live pathology: stance file all-null since inception -> market governs."""
    p = _policy(
        {"DGS2": _dgs2(4.35, 4.30), "DFF": _obs([4.33])},
        manual={"stance": "unconfirmed", "as_of": None},
    )
    assert p["stance"] == "neutral"
    assert p["source"] == "market_implied"


def test_policy_unconfirmed_only_when_both_missing():
    p = _policy({}, manual={"stance": "unconfirmed", "as_of": None})
    assert p["stance"] == "unconfirmed"
    assert p["source"] == "unconfirmed"
    assert p["agreement"] is None


def test_policy_agreement_true_when_layers_align():
    p = _policy(
        {"DGS2": _dgs2(4.35, 4.30)},
        manual={"stance": "neutral", "as_of": "2026-06-18"},
    )
    assert p["source"] == "manual_fresh"
    assert p["agreement"] is True
    assert "DISAGREEMENT" not in p["note"]


def test_policy_manual_fresh_days_from_config():
    """manual_fresh_days is config-driven: a 15d-old stance is stale at 10."""
    p = _policy(
        {"DGS2": _dgs2(4.35, 4.30)},
        manual={"stance": "hawkish", "as_of": "2026-06-18"},
        cfg={"policy_axis": {"manual_fresh_days": 10}},
    )
    assert p["source"] == "market_implied"
    assert p["stance"] == "neutral"


# --- regime gate -------------------------------------------------------------

def test_gate_closed_on_falling_growth():
    g = {"direction": "falling"}
    i = {"direction": "flat"}
    gate = _build_regime_gate(g, i, {"stance": "unconfirmed"})
    assert gate["status"] == "closed"
    assert any("growth" in r for r in gate["reasons"])
    assert "UNCONFIRMED" in gate["policy_note"]


def test_gate_open_only_when_all_clear():
    gate = _build_regime_gate(
        {"direction": "rising"}, {"direction": "falling"}, {"stance": "neutral"}
    )
    assert gate["status"] == "open"
    assert gate["reasons"] == []


def test_gate_closed_on_rising_inflation_and_hawkish():
    gate = _build_regime_gate(
        {"direction": "rising"}, {"direction": "rising"}, {"stance": "hawkish"}
    )
    assert gate["status"] == "closed"
    assert len(gate["reasons"]) == 2


def test_gate_consumes_resolved_policy_axis():
    """End-to-end #16: a market-implied hawkish repricing closes the gate; the
    resolved stance + source land in derived_from (the conviction proxy reads it)."""
    pa = _policy({"DGS2": _dgs2(4.50, 4.30)})
    gate = _build_regime_gate({"direction": "rising"}, {"direction": "falling"}, pa)
    assert gate["status"] == "closed"
    assert any("hawkish" in r for r in gate["reasons"])
    assert gate["derived_from"]["policy_stance"] == "hawkish"
    assert gate["derived_from"]["policy_source"] == "market_implied"


def test_gate_open_on_market_implied_neutral():
    """The #16 payoff: a null manual file no longer strands policy at unconfirmed —
    a readable DGS2 gives neutral, the gate opens, no policy_note."""
    pa = _policy(
        {"DGS2": _dgs2(4.35, 4.30)},
        manual={"stance": "unconfirmed", "as_of": None},
    )
    gate = _build_regime_gate({"direction": "rising"}, {"direction": "falling"}, pa)
    assert gate["status"] == "open"
    assert gate["policy_note"] == ""


def test_gate_unconfirmed_still_flags_policy_note():
    pa = _policy({}, manual={"stance": "unconfirmed", "as_of": None})
    gate = _build_regime_gate({"direction": "rising"}, {"direction": "falling"}, pa)
    assert gate["status"] == "open"   # unconfirmed does not hard-close (unchanged)
    assert "UNCONFIRMED" in gate["policy_note"]
