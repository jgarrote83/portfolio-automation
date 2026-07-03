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
    _gdpnow_vintage_rows,
)


def _obs(values):
    """FRED-shaped rows (newest-first) from a newest-first list of values."""
    return [{"value": str(v)} for v in values]


def _vintages(values, q="2026-04-01"):
    """GDPNOW_VINTAGES rows (oldest-first) for the quarter starting at ``q``."""
    return [{"date": q, "asof": f"{q[:8]}{i:02d}", "value": str(v)}
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


# --- growth axis: quarter-boundary splice (FOLLOWUPS #15) ---------------------

def test_growth_prior_tail_when_new_quarter_empty():
    """Day 1-3 of a new quarter: zero current vintages, but the just-ended quarter's
    trajectory is in the window — read its tail, NOT the cross-quarter fallback
    (which here would falsely say 'rising') and NEVER an empty trajectory."""
    md = {
        "GDPNOW_VINTAGES": [],
        "GDPNOW_VINTAGES_PRIOR": _vintages([3.70, 3.99, 4.26, 3.82, 3.02, 2.54]),
        "GDPNOW": _obs([2.54, 1.24]),  # cross-quarter says 'rising' — must NOT win
    }
    g = _build_growth_axis(md)
    assert g["direction"] == "falling"
    assert g["confidence"] == "medium"
    assert g["basis"] == "prior_quarter_tail"
    assert g["gdpnow_trajectory"]  # non-empty while FRED has vintages
    assert g["gdpnow_latest"] == 2.54


def test_growth_prior_tail_with_one_and_two_current_vintages():
    """1 or 2 current-quarter vintages still splice to the prior tail (need >=3)."""
    prior = _vintages([1.0, 1.2, 1.5, 1.8, 2.2, 2.6])
    for cur in ([2.9], [2.9, 3.0]):
        md = {
            "GDPNOW_VINTAGES": _vintages(cur, q="2026-07-01"),
            "GDPNOW_VINTAGES_PRIOR": prior,
        }
        g = _build_growth_axis(md)
        assert g["basis"] == "prior_quarter_tail"
        assert g["confidence"] == "medium"
        assert g["direction"] == "rising"   # the prior tail slope, not the new prints
        assert str(len(cur)) in g["note"]


def test_growth_prior_tail_reads_recent_slope_not_whole_quarter():
    """The tail (last 6 vintages) governs: a quarter that rose early but is being
    marked down late must read 'falling'."""
    md = {"GDPNOW_VINTAGES_PRIOR": _vintages(
        [1.0, 2.0, 3.0, 4.0, 4.3, 4.2, 4.0, 3.7, 3.4, 3.1])}
    g = _build_growth_axis(md)
    assert g["direction"] == "falling"
    assert g["gdpnow_trajectory"] == [4.3, 4.2, 4.0, 3.7, 3.4, 3.1]


def test_growth_current_quarter_wins_over_prior():
    """>=3 current vintages -> unchanged behavior; the prior trajectory is ignored."""
    md = {
        "GDPNOW_VINTAGES": _vintages([1.0, 1.4, 2.0, 2.6], q="2026-07-01"),
        "GDPNOW_VINTAGES_PRIOR": _vintages([4.0, 3.0, 2.0]),  # falling — must NOT win
    }
    g = _build_growth_axis(md)
    assert g["direction"] == "rising"
    assert g["confidence"] == "high"
    assert g["basis"] == "within_quarter_vintages"


def test_growth_fallback_when_both_quarters_thin():
    """<3 vintages in BOTH quarters -> existing cross-quarter fallback path."""
    md = {
        "GDPNOW_VINTAGES": _vintages([2.9], q="2026-07-01"),
        "GDPNOW_VINTAGES_PRIOR": _vintages([2.5, 2.6]),
        "GDPNOW": _obs([2.54, 1.24]),
    }
    g = _build_growth_axis(md)
    assert g["basis"] == "cross_quarter_fallback"
    assert g["confidence"] == "low"


def test_gdpnow_vintage_rows_split_by_observation_date():
    """The fetch-side helper splits one ALFRED response into per-quarter rows and
    drops FRED's '.' placeholders."""
    rows = [
        {"date": "2026-04-01", "realtime_start": "2026-06-27", "value": "2.5"},
        {"date": "2026-04-01", "realtime_start": "2026-06-30", "value": "2.6"},
        {"date": "2026-07-01", "realtime_start": "2026-07-17", "value": "2.9"},
        {"date": "2026-07-01", "realtime_start": "2026-07-18", "value": "."},
    ]
    cur = _gdpnow_vintage_rows(rows, "2026-07-01")
    pri = _gdpnow_vintage_rows(rows, "2026-04-01")
    assert [r["value"] for r in cur] == ["2.9"]
    assert cur[0]["asof"] == "2026-07-17"
    assert [r["value"] for r in pri] == ["2.5", "2.6"]
    assert _gdpnow_vintage_rows(None, "2026-07-01") == []


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
