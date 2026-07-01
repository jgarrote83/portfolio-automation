"""Unit tests for transition_watch (responsiveness brief Phase 3).

Realized inflation is laggy, so transition_watch lets the LEADING inflation signal
pre-stage a bounded, partial lean in reference_weights toward the projected quadrant —
WITHOUT moving the binding active_quadrant / regime_gate / realized inflation axis. The
core invariant tests assert that binding fields do not move; the asymmetry tests assert a
de-risk transition stages more readily/larger than a re-risk one (spec §6). The trigger is
the Phase-2 `leading_vs_lagging_inflation` divergence, REUSED not re-derived. Run:
    PYTHONPATH=src pytest tests/test_transition_watch.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import (  # noqa: E402
    _build_reference_weights,
    _build_transition_watch,
    _load_risk_limits,
)

CFG = _load_risk_limits()


def _div(direction, be=-28.0, oil=-21.0, status="active"):
    """A leading_vs_lagging_inflation divergence entry (Phase-2 shape)."""
    return [{
        "id": "leading_vs_lagging_inflation",
        "direction_implied": direction,
        "status": status,
        "signals": [
            {"name": "be_5y.delta_20d_bp", "value": be, "as_of": None},
            {"name": "inflation_axis.oil_wti_20d_pct", "value": oil, "as_of": None},
            {"name": "inflation_axis.direction (realized)", "value": "flat", "as_of": None},
        ],
    }]


def _axes(growth, inflation):
    return {"direction": growth}, {"direction": inflation}


# --- projection + direction --------------------------------------------------

def test_borderline_realized_leading_falling_projects_q4_de_risk():
    """Today's case: growth falling + realized flat (Q3/Q4 border), leading falling ->
    projects Q4, de-risk (Q4 more defensive than the Q3/Q4 midpoint)."""
    g, i = _axes("falling", "flat")
    tw = _build_transition_watch(_div("falling"), g, i, CFG)
    assert tw["active"] is True
    assert tw["projected_quadrant"] == "Q4"
    assert tw["direction"] == "de_risk"
    assert tw["staged_fraction"] == CFG["transition_watch"]["staged_fraction_de_risk"]
    assert tw["basis"]  # echoes the leading signals


def test_decided_q3_leading_falling_projects_q4_de_risk():
    """Realized decided Q3 (growth falling + inflation rising), leading falling -> Q4 de-risk."""
    g, i = _axes("falling", "rising")
    tw = _build_transition_watch(_div("falling"), g, i, CFG)
    assert tw["active"] is True
    assert tw["projected_quadrant"] == "Q4"
    assert tw["direction"] == "de_risk"


def test_decided_q4_leading_rising_projects_q3_re_risk_needs_bar():
    """Realized Q4 (falling/falling), leading rising -> projects Q3 (less defensive) = re-risk.
    Strong confirmation (both be+oil rising past threshold) -> activates at the re-risk frac."""
    g, i = _axes("falling", "falling")
    tw = _build_transition_watch(_div("rising", be=25.0, oil=18.0), g, i, CFG)
    assert tw["active"] is True
    assert tw["projected_quadrant"] == "Q3"
    assert tw["direction"] == "re_risk"
    assert tw["staged_fraction"] == CFG["transition_watch"]["staged_fraction_re_risk"]


# --- asymmetry (spec §6, the safety) ----------------------------------------

def test_de_risk_stages_larger_fraction_than_re_risk():
    g_dr, i_dr = _axes("falling", "flat")          # de-risk toward Q4
    de = _build_transition_watch(_div("falling"), g_dr, i_dr, CFG)
    g_rr, i_rr = _axes("falling", "falling")       # re-risk toward Q3
    re = _build_transition_watch(_div("rising", be=25.0, oil=18.0), g_rr, i_rr, CFG)
    assert de["staged_fraction"] > re["staged_fraction"]


def test_re_risk_below_confirmation_bar_does_not_activate():
    """Re-risk with only ONE leading signal confirming (< re_risk_min_confirmations=2) ->
    does NOT activate (higher bar). A de-risk with the same single signal WOULD activate."""
    g, i = _axes("falling", "falling")   # realized Q4; leading rising -> re-risk Q3
    # only oil rising past threshold; breakeven flat (below thr)
    tw = _build_transition_watch(_div("rising", be=2.0, oil=18.0), g, i, CFG)
    assert tw["active"] is False
    assert tw["status"] == "indeterminate"


def test_de_risk_activates_on_single_signal():
    """De-risk has the low bar — a single leading signal is enough (no confirmation gate)."""
    g, i = _axes("falling", "flat")
    tw = _build_transition_watch(_div("falling", be=2.0, oil=-21.0), g, i, CFG)
    assert tw["active"] is True
    assert tw["direction"] == "de_risk"


# --- reuse (not re-derivation) + missing data --------------------------------

def test_indeterminate_divergence_yields_indeterminate_transition():
    """Leading data stale/absent -> Phase-2 divergence indeterminate -> no transition."""
    g, i = _axes("falling", "flat")
    tw = _build_transition_watch(_div("falling", status="indeterminate"), g, i, CFG)
    assert tw["active"] is False
    assert tw["status"] == "indeterminate"


def test_no_divergence_present_yields_indeterminate():
    g, i = _axes("falling", "flat")
    tw = _build_transition_watch([], g, i, CFG)
    assert tw["active"] is False
    assert tw["status"] == "indeterminate"


def test_growth_flat_cannot_project():
    """Growth axis not pinned -> can't place the projection on the grid -> indeterminate."""
    g, i = _axes("flat", "flat")
    tw = _build_transition_watch(_div("falling"), g, i, CFG)
    assert tw["active"] is False


# --- the core invariant: binding fields do NOT move -------------------------

def _paper():
    eq = 100_000.0
    holds = {"SPY": 17.0, "QQQ": 14.0, "GLD": 5.0, "MCK": 4.0, "XLP": 2.0, "TLT": 2.0, "SGOV": 20.0}
    return {"available": True, "equity": eq, "cash": eq * 0.02 / 100,
            "positions": [{"ticker": t, "market_value": eq * w / 100.0} for t, w in holds.items()]}


def _rw(tw):
    g, i = _axes("falling", "flat")
    return _build_reference_weights(
        _paper(), g, i, {"status": "closed"}, {"dxy_tailwind_for_intl": "neutral"},
        {}, {}, {"shock_level": 0}, CFG, tw,
    )


def test_reference_binding_fields_unchanged_by_transition():
    """The invariant: active_quadrant / favored_bucket / borderline / conviction do NOT
    move when transition_watch is applied — only the target weights lean."""
    tw = _build_transition_watch(_div("falling"), *_axes("falling", "flat"), CFG)
    before = _rw(None)
    after = _rw(tw)
    for k in ("active_quadrant", "favored_bucket", "borderline", "conviction_proxy",
              "active_quadrant_target_pct_of_core", "ceiling_pct_of_core"):
        assert before.get(k) == after.get(k), f"{k} moved: {before.get(k)} -> {after.get(k)}"


def test_reference_lean_is_partial_never_a_full_flip():
    """The lean shifts weight toward Q4 (TLT up) but does NOT make Q4 dominate — the base
    Q3/Q4 intersection names still hold the majority; bounded by staged_fraction."""
    tw = _build_transition_watch(_div("falling"), *_axes("falling", "flat"), CFG)
    before = _rw(None)["target_weights_pct"]
    after = _rw(tw)["target_weights_pct"]
    assert after.get("TLT", 0) > before.get("TLT", 0)   # Q4 leg pre-staged up
    # not a full flip: TLT is still far from dominating; intersection names (GLD/XLP) lead
    assert after.get("TLT", 0) < after.get("GLD", 0)
    assert after["transition_lean"]["applied"] if "transition_lean" in after else True


def test_reference_lean_preserves_total():
    tw = _build_transition_watch(_div("falling"), *_axes("falling", "flat"), CFG)
    after = _rw(tw)
    total = sum(after["target_weights_pct"].values()) + after.get("literal_cash_target_pct", 0.0)
    assert abs(total - 100.0) < 0.5


def test_reference_transition_lean_field_reports_applied():
    tw = _build_transition_watch(_div("falling"), *_axes("falling", "flat"), CFG)
    after = _rw(tw)
    lean = after["transition_lean"]
    assert lean["applied"] is True
    assert lean["projected_quadrant"] == "Q4"
    assert lean["direction"] == "de_risk"


def test_reference_no_lean_when_transition_inactive():
    after = _rw(None)
    assert after["transition_lean"]["applied"] is False
