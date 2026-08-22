"""2026-08-21 quadrant-reachability audit, Task B — inflation-side re-risk
confirmation gains a THIRD source (`market_implied_quadrant.structural_inflation_score`,
Task C) alongside the existing breakeven/oil pair, and the OR-semantics
documented in divergence-config.json are honored by counting sources rather
than requiring all of them.

F2: the OR-documented detector (be OR oil) was being re-applied by the
consumer as effectively AND via `re_risk_min_confirmations: 2` counted
against exactly 2 possible sources — with current 2026-08-21 readings
(be +7bp vs 15 needed, oil -3.5% vs 10 needed), the inflation side had
NEVER staged a re-risk lean.

Run: PYTHONPATH=src pytest tests/test_transition_watch_confirmation_sources.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import (  # noqa: E402
    _build_inflation_axis,
    _build_transition_watch,
    _load_risk_limits,
)

CFG = _load_risk_limits()


def _cfg_with_min_confirmations(n: int) -> dict:
    c = copy.deepcopy(CFG)
    c["transition_watch"]["re_risk_min_confirmations"] = n
    return c


def _axes(growth, inflation):
    return {"direction": growth}, {"direction": inflation}


def _infl_div(direction, be=-28.0, oil=-21.0, status="active"):
    return [{
        "id": "leading_vs_lagging_inflation",
        "direction_implied": direction,
        "status": status,
        "signals": [
            {"name": "be_5y.delta_20d_bp", "value": be, "as_of": None},
            {"name": "inflation_axis.oil_20d_pct_governing", "value": oil, "as_of": None},
        ],
    }]


# --- three-source counting ---------------------------------------------------

def test_tape_only_confirmation_counts_as_one_of_three():
    """be/oil both below their own thresholds (0 confirmations from the
    original pair); the structural tape score alone clears INFL_THR and
    agrees with leading_dir='rising' -> exactly 1 of 3 sources."""
    g, i = _axes("falling", "falling")  # realized Q4
    div = _infl_div("rising", be=2.0, oil=2.0)
    miq = {"available": True, "structural_inflation_score": 0.418}
    tw = _build_transition_watch(div, g, i, _cfg_with_min_confirmations(1),
                                  market_implied_quadrant=miq)
    assert tw["active"] is True
    side = tw["sides"][0]
    assert side["confirmations"] == 1
    assert side["confirmations_of"] == 3


def test_two_of_three_still_required_by_default_config():
    """Same tape-only-agreement fixture, default re_risk_min_confirmations=2
    -> does NOT stage (this is the D-3 finding: 1-of-3 today is inert)."""
    g, i = _axes("falling", "falling")
    div = _infl_div("rising", be=2.0, oil=2.0)
    miq = {"available": True, "structural_inflation_score": 0.418}
    tw = _build_transition_watch(div, g, i, CFG, market_implied_quadrant=miq)
    assert tw["active"] is False
    assert tw["status"] == "indeterminate"


def test_opposite_sign_tape_score_never_confirms():
    """leading_dir is 'rising' (inflation side wants rising); a structural
    score of the OPPOSITE sign (-0.418, implying falling) must never count as
    a confirmation, even under the relaxed 1-of-3 bar."""
    g, i = _axes("falling", "falling")
    div = _infl_div("rising", be=2.0, oil=2.0)
    miq = {"available": True, "structural_inflation_score": -0.418}
    tw = _build_transition_watch(div, g, i, _cfg_with_min_confirmations(1),
                                  market_implied_quadrant=miq)
    assert tw["active"] is False


def test_missing_structural_score_reduces_available_sources_to_two():
    """No market_implied_quadrant at all -> only 2 possible sources (be/oil),
    never a fabricated 3rd. Both be/oil confirm strongly -> 2-of-2, which the
    DEFAULT min_confirmations=2 bar still satisfies (regression guard: the
    pre-Task-B 2-of-2 behavior must be unchanged when the 3rd source is
    absent)."""
    g, i = _axes("falling", "falling")
    div = _infl_div("rising", be=25.0, oil=18.0)
    tw = _build_transition_watch(div, g, i, CFG, market_implied_quadrant=None)
    assert tw["active"] is True
    side = tw["sides"][0]
    assert side["confirmations"] == 2
    assert side["confirmations_of"] == 2


def test_structural_score_key_absent_from_available_miq_also_reduces_to_two():
    """An available market_implied_quadrant block that simply predates Task C
    (no structural_inflation_score key at all) must degrade identically to
    the 'no miq' case -- never crash, never fabricate a confirmation."""
    g, i = _axes("falling", "falling")
    div = _infl_div("rising", be=25.0, oil=18.0)
    tw = _build_transition_watch(div, g, i, CFG, market_implied_quadrant={"available": True})
    assert tw["active"] is True
    side = tw["sides"][0]
    assert side["confirmations_of"] == 2


def test_all_three_sources_confirming_counts_three_of_three():
    g, i = _axes("falling", "falling")
    div = _infl_div("rising", be=25.0, oil=18.0)
    miq = {"available": True, "structural_inflation_score": 0.418}
    tw = _build_transition_watch(div, g, i, CFG, market_implied_quadrant=miq)
    assert tw["active"] is True
    side = tw["sides"][0]
    assert side["confirmations"] == 3
    assert side["confirmations_of"] == 3


# --- FOLLOWUPS #19 (2026-08-21, Task B) regression guard ---------------------

def test_new_inflation_quality_block_does_not_become_a_fourth_confirmation_source():
    """The new `inflation_axis.quality` diagnostics sub-block (4 FRED
    inflation-quality series, FOLLOWUPS #19) must have ZERO effect on the
    inflation-side re-risk confirmation count -- confirmations_of must still
    resolve to 3 (breakeven + oil + structural tape), never 4, even against a
    REAL `_build_inflation_axis` output that now carries `quality` populated
    alongside those three sources. Decision D-3/#78 (1-of-3 vs 2-of-3) stays
    untouched and open."""
    macro_data = {
        "CORESTICKM159SFRBATL": [{"date": "2026-08-01", "value": "4.3"},
                                  {"date": "2026-07-01", "value": "4.1"}],
        "FLEXCPIM159SFRBATL": [{"date": "2026-08-01", "value": "2.0"},
                                {"date": "2026-07-01", "value": "2.3"}],
        "PCETRIM12M159SFRBDAL": [{"date": "2026-08-01", "value": "2.9"},
                                  {"date": "2026-07-01", "value": "2.92"}],
        "MICH": [{"date": "2026-08-01", "value": "3.2"}, {"date": "2026-07-01", "value": "3.2"}],
    }
    ia = _build_inflation_axis(macro_data, today="2026-08-21")
    assert "quality" in ia and len(ia["quality"]) == 4   # sanity: really populated

    g = {"direction": "falling"}
    div = _infl_div("rising", be=25.0, oil=18.0)
    miq = {"available": True, "structural_inflation_score": 0.418}
    tw = _build_transition_watch(div, g, ia, CFG, market_implied_quadrant=miq)
    assert tw["active"] is True
    side = tw["sides"][0]
    assert side["confirmations"] == 3
    assert side["confirmations_of"] == 3


# --- de-risk side is unaffected (no confirmation bar at all) -----------------

def test_de_risk_side_ignores_confirmation_machinery_entirely():
    g, i = _axes("falling", "flat")
    div = _infl_div("falling", be=2.0, oil=2.0)   # de-risk: single-signal bar, no confirmation gate
    miq = {"available": True, "structural_inflation_score": -0.02}  # irrelevant, well below thr
    tw = _build_transition_watch(div, g, i, CFG, market_implied_quadrant=miq)
    assert tw["active"] is True
    assert tw["direction"] == "de_risk"
