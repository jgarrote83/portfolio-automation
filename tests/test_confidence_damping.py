"""Unit tests for staleness-damped axis confidence (2026-09-02, C1 — DESCRIBE-
ONLY, decision gate G-4).

Core CPI/PCE run 63 days stale between prints, yet the inflation axis's
CONFIRMED direction reads at a 24-run streak with nothing surfacing that
tension. This cycle adds `confidence_damped` (+ inputs) to growth_axis and
inflation_axis, and promotes the inflation axis's existing bridge_direction-
vs-direction tension to an explicit numeric `bridge_disagreement_score`.
BOTH are wired to NOTHING — `direction`/the plain `confidence` label must
never move; no consumer reads either field this cycle (see G-4).

Run: PYTHONPATH=src pytest tests/test_confidence_damping.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import (  # noqa: E402
    _damp_confidence_by_staleness,
    _growth_confidence_damped,
    _inflation_confidence_damped,
)


# --- the damping formula itself -----------------------------------------------

def test_undamped_at_or_under_threshold():
    assert _damp_confidence_by_staleness(1.0, 10, 30) == 1.0
    assert _damp_confidence_by_staleness(1.0, 30, 30) == 1.0


def test_halved_at_double_the_threshold():
    assert _damp_confidence_by_staleness(1.0, 60, 30) == 0.5


def test_decays_toward_zero_never_negative_for_extreme_staleness():
    out = _damp_confidence_by_staleness(1.0, 30_000, 30)
    assert 0.0 <= out < 0.01


def test_missing_days_stale_or_threshold_is_undamped():
    assert _damp_confidence_by_staleness(0.7, None, 30) == 0.7
    assert _damp_confidence_by_staleness(0.7, 40, None) == 0.7
    assert _damp_confidence_by_staleness(0.7, 40, 0) == 0.7


# --- growth_axis.confidence_damped --------------------------------------------

def test_growth_confidence_damped_fresh_gdpnow_undamped():
    growth_axis = {"confidence": "high"}
    freshness = {"series": {"GDPNOW": {"days_stale": 3, "threshold_days": 10}}}
    out = _growth_confidence_damped(growth_axis, freshness)
    assert out["confidence_damped"] == 1.0
    assert out["confidence_damped_inputs"]["governing_series"] == "GDPNOW"
    assert out["confidence_damped_inputs"]["base_confidence_label"] == "high"


def test_growth_confidence_damped_stale_gdpnow_reduced():
    growth_axis = {"confidence": "high"}
    freshness = {"series": {"GDPNOW": {"days_stale": 81, "threshold_days": 27}}}
    out = _growth_confidence_damped(growth_axis, freshness)
    assert out["confidence_damped"] < 1.0


def test_growth_confidence_damped_missing_freshness_non_fatal():
    out = _growth_confidence_damped({"confidence": "medium"}, {})
    assert out["confidence_damped"] == 0.7   # base only, undamped


# --- inflation_axis.confidence_damped + bridge_disagreement_score ------------

def test_inflation_confidence_damped_stale_core_reduced():
    """The literal motivating case: 63d-stale core, base 1.0 -> reduced."""
    inflation_axis = {"direction": "falling", "core_pce_ann3": 2.1, "bridge_direction": "falling"}
    freshness = {"series": {"PCEPILFE": {"days_stale": 63, "threshold_days": 35}}}
    out = _inflation_confidence_damped(inflation_axis, freshness)
    assert out["confidence_damped"] < 1.0
    assert out["confidence_damped_inputs"]["governing_series"] == "PCEPILFE"


def test_inflation_confidence_damped_prefers_pce_falls_back_to_cpi():
    ia_pce = {"core_pce_ann3": 2.0}
    assert _inflation_confidence_damped(ia_pce, {})["confidence_damped_inputs"][
        "governing_series"] == "PCEPILFE"
    ia_cpi = {"core_pce_ann3": None}
    assert _inflation_confidence_damped(ia_cpi, {})["confidence_damped_inputs"][
        "governing_series"] == "CPILFESL"


def test_bridge_disagreement_score_zero_when_aligned():
    ia = {"direction": "falling", "bridge_direction": "falling"}
    assert _inflation_confidence_damped(ia, {})["bridge_disagreement_score"] == 0.0


def test_bridge_disagreement_score_two_when_fully_opposite():
    """The live-book counter-signal shape: realized falling, bridge (breakevens
    +18bp/20d + oil momentum) reading rising."""
    ia = {"direction": "falling", "bridge_direction": "rising"}
    assert _inflation_confidence_damped(ia, {})["bridge_disagreement_score"] == 2.0


def test_bridge_disagreement_score_one_when_partial():
    ia = {"direction": "falling", "bridge_direction": "flat"}
    assert _inflation_confidence_damped(ia, {})["bridge_disagreement_score"] == 1.0


def test_bridge_disagreement_score_none_when_either_side_absent():
    assert _inflation_confidence_damped({"direction": None, "bridge_direction": "rising"}, {}
        )["bridge_disagreement_score"] is None
    assert _inflation_confidence_damped({"direction": "falling", "bridge_direction": None}, {}
        )["bridge_disagreement_score"] is None


# --- describe-only guarantee: direction is NEVER present in the output ------

def test_neither_builder_ever_touches_direction_or_confidence():
    """The output dicts are MERGED on top of the axis (`{**axis, **updates}`)
    — a describe-only contract requires the updates dict itself carries
    neither `direction` nor `confidence`, so a merge can never overwrite them."""
    g_out = _growth_confidence_damped({"confidence": "high", "direction": "rising"}, {})
    assert "direction" not in g_out
    assert "confidence" not in g_out

    i_out = _inflation_confidence_damped({"direction": "falling"}, {})
    assert "direction" not in i_out
