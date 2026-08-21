"""2026-08-21 quadrant-reachability audit, Task A — joint (diagonal) quadrant
projection.

F1 (root cause): `_project_quadrant` (inflation side) and `_project_quadrant_growth`
(growth side) each move along ONE axis only, holding the other fixed. When both
sides fire, `_build_transition_watch` picked the more-defensive SINGLE-side
projection rather than composing them — so from a realized quadrant, only the two
orthogonally-adjacent quadrants were ever reachable; the DIAGONAL was structurally
unreachable. From realized Q4 this meant Q2 (the best-performing basket in the
2026-05-26..08-21 window) could never be projected, no matter how strongly both
leading signals pointed there.

Run: PYTHONPATH=src pytest tests/test_transition_watch_joint_projection.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import (  # noqa: E402
    _build_transition_watch,
    _confirm_transition_watch,
    _load_risk_limits,
)

CFG = _load_risk_limits()


def _axes(growth, inflation):
    return {"direction": growth}, {"direction": inflation}


def _infl_div(direction, be=-28.0, oil=-21.0, status="active"):
    """A leading_vs_lagging_inflation divergence entry (Phase-2 shape)."""
    return {
        "id": "leading_vs_lagging_inflation",
        "direction_implied": direction,
        "status": status,
        "signals": [
            {"name": "be_5y.delta_20d_bp", "value": be, "as_of": None},
            {"name": "inflation_axis.oil_20d_pct_governing", "value": oil, "as_of": None},
        ],
    }


def _growth_div(direction, score=0.5, confidence="medium", status="active"):
    """A leading_vs_lagging_growth divergence entry (Phase-2 shape)."""
    return {
        "id": "leading_vs_lagging_growth",
        "direction_implied": direction,
        "status": status,
        "signals": [
            {"name": "leading_growth.direction", "value": direction},
            {"name": "leading_growth.score", "value": score},
            {"name": "leading_growth.confidence", "value": confidence},
        ],
    }


# --- the root-cause fix: both sides re-risk -> diagonal composition ---------

def test_both_sides_re_risk_from_q4_composes_q2():
    """Realized Q4 (falling/falling). Growth side leads rising (-> Q2, re-risk on
    its own axis alone would be Q1); inflation side leads rising (-> Q3, re-risk
    alone). BOTH re-risk -> compose the diagonal: g_eff=rising, i_eff=rising ->
    Q2. On master (single-side tiebreak, `min` on defensiveness) this returns Q1
    (growth side's own single-axis projection, defensiveness 0 < inflation
    side's Q3 at defensiveness 2)."""
    g, i = _axes("falling", "falling")
    growth_div = _growth_div("rising", confidence="medium")
    infl_div = _infl_div("rising", be=25.0, oil=18.0)
    tw = _build_transition_watch([growth_div, infl_div], g, i, CFG)
    assert tw["active"] is True
    assert tw["direction"] == "re_risk"
    assert tw["projected_quadrant"] == "Q2"
    assert tw["staged_fraction"] == CFG["transition_watch"].get("staged_fraction_re_risk_joint", 0.10)
    assert len(tw["sides"]) == 2


def test_joint_basis_unions_both_sides():
    g, i = _axes("falling", "falling")
    growth_div = _growth_div("rising", confidence="medium")
    infl_div = _infl_div("rising", be=25.0, oil=18.0)
    tw = _build_transition_watch([growth_div, infl_div], g, i, CFG)
    basis_str = " ".join(tw["basis"])
    assert "leading_growth" in basis_str
    assert "be_5y" in basis_str or "oil" in basis_str


# --- safety bias: any de-risk side present -> no composition ----------------

def test_one_side_de_risk_one_re_risk_uses_most_defensive_single_side():
    """Realized Q3 (falling/rising). Growth leads rising -> Q2 (re-risk, less
    defensive than Q3). Inflation leads falling -> Q4 (de-risk, more defensive
    than Q3). NOT both re-risk -> fall back to today's most-defensive-wins
    behavior: the de-risk side (Q4) wins, unchanged from master."""
    g, i = _axes("falling", "rising")
    growth_div = _growth_div("rising", confidence="medium")
    infl_div = _infl_div("falling", be=-28.0, oil=-21.0)
    tw = _build_transition_watch([growth_div, infl_div], g, i, CFG)
    assert tw["active"] is True
    assert tw["direction"] == "de_risk"
    assert tw["projected_quadrant"] == "Q4"


def test_both_sides_de_risk_uses_most_defensive_single_side():
    """Realized Q1 (rising/falling) -- as defensive as it gets from Q1's own
    quadrant on either axis alone; both sides therefore de-risk. No joint
    composition (composition requires BOTH sides re-risk)."""
    g, i = _axes("rising", "falling")
    growth_div = _growth_div("falling", confidence="medium")   # -> Q4, de_risk
    infl_div = _infl_div("rising", be=25.0, oil=18.0)            # -> Q2, de_risk
    tw = _build_transition_watch([growth_div, infl_div], g, i, CFG)
    assert tw["active"] is True
    assert tw["direction"] == "de_risk"
    # most defensive of the two de-risk projections (Q4 defensiveness 3 > Q2's 1)
    assert tw["projected_quadrant"] == "Q4"


# --- regression guards: single-side-activatable behavior is bit-identical ---

def test_only_growth_side_activatable_matches_single_side_behavior():
    g, i = _axes("falling", "rising")  # realized Q3
    growth_div = _growth_div("rising", confidence="medium")     # -> Q2, re_risk
    infl_div = _infl_div("falling", status="indeterminate")     # not activatable
    tw = _build_transition_watch([growth_div, infl_div], g, i, CFG)
    assert tw["active"] is True
    assert tw["projected_quadrant"] == "Q2"
    assert tw["direction"] == "re_risk"
    assert len(tw["sides"]) == 1
    assert tw["sides"][0]["side"] == "growth"


def test_only_inflation_side_activatable_matches_single_side_behavior():
    g, i = _axes("falling", "rising")  # realized Q3
    growth_div = _growth_div("rising", status="indeterminate")  # not activatable
    infl_div = _infl_div("falling", be=-28.0, oil=-21.0)         # -> Q4, de_risk
    tw = _build_transition_watch([growth_div, infl_div], g, i, CFG)
    assert tw["active"] is True
    assert tw["projected_quadrant"] == "Q4"
    assert tw["direction"] == "de_risk"
    assert len(tw["sides"]) == 1
    assert tw["sides"][0]["side"] == "inflation"


# --- hysteresis is load-bearing and must not be bypassed ---------------------

def test_joint_projection_requires_confirm_sessions():
    """The composed (Q2, re_risk) pair is a NEW pair to the hysteresis wrapper
    (never seen before as a single-side projection from Q4) -> must still
    require confirm_sessions=2 like any other candidate, never a same-session
    activation just because it's a joint projection."""
    g, i = _axes("falling", "falling")
    growth_div = _growth_div("rising", confidence="medium")
    infl_div = _infl_div("rising", be=25.0, oil=18.0)
    raw = _build_transition_watch([growth_div, infl_div], g, i, CFG)
    assert raw["projected_quadrant"] == "Q2"
    tw_cfg = CFG["transition_watch"]

    first = _confirm_transition_watch(raw, None, tw_cfg)
    assert first["active"] is False
    assert first["status"] == "pending"
    assert first["pending_projected_quadrant"] == "Q2"

    second = _confirm_transition_watch(raw, first["_state"], tw_cfg)
    assert second["active"] is True
    assert second["projected_quadrant"] == "Q2"
    assert second["direction"] == "re_risk"
    assert second["target_fraction"] == CFG["transition_watch"].get("staged_fraction_re_risk_joint", 0.10)
