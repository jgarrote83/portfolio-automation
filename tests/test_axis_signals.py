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
