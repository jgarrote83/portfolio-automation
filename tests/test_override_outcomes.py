"""Unit tests for brief Phase 5 — override-outcome stamping + override_record.

Covers the reference-path counterfactual (LOCKED: overrides are graded "did
disagreeing beat obeying" vs the filed-date reference vector, NOT vs SPY), the
sign convention (more-of-sleeve vs less-of-sleeve derived from direction × block
membership), the indeterminate_data guards (never guess), and the pure aggregator
(by_direction / by_status splits, enforced rows aggregated SEPARATELY, n≥10
premise promotion, small-n caveats). Run:
    PYTHONPATH=src pytest tests/test_override_outcomes.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import (  # noqa: E402
    _aggregate_override_record,
    _grade_override,
    _override_sign,
)

FILED, MATURED = "2026-07-06", "2026-07-25"

# Filed-date reference vector: SPY floored, GLD/TLT concentrated, SGOV cash sleeve,
# a de-minimis 0.1% floor sleeve. Sums to ~98.6 (literal cash absent → implicit 0).
REF = {"SPY": 0.5, "GLD": 40.0, "TLT": 33.0, "SGOV": 25.0, "XSD": 0.1}

# Price maps: date -> {sym: close}. GLD +10%, TLT flat, SGOV +0.3%, SPY -4%.
PRICES = {
    FILED:   {"SPY": 550.0, "GLD": 200.0, "TLT": 90.0, "SGOV": 100.0, "XSD": 210.0},
    MATURED: {"SPY": 528.0, "GLD": 220.0, "TLT": 90.0, "SGOV": 100.3, "XSD": 210.0},
}


def _px(sym, d):
    return (PRICES.get(d) or {}).get(sym)


def _row(**kw):
    base = {
        "recommended_at": FILED,
        "falsifier_date": MATURED,
        "sleeve": "SPY",
        "direction": "re_risk",
        "outcome": "accepted",
    }
    base.update(kw)
    return base


def _ref_return():
    """Expected reference-path return for REF over the window (hand math)."""
    return (0.5 / 100 * -4.0) + (40.0 / 100 * 10.0) + (33.0 / 100 * 0.0) \
        + (25.0 / 100 * 0.3) + (0.1 / 100 * 0.0)


# --- sign convention -----------------------------------------------------------------

def test_sign_convention_all_four_cells():
    assert _override_sign("SPY", "re_risk") == 1.0    # held MORE amplifier than ref
    assert _override_sign("SPY", "de_risk") == -1.0   # held LESS amplifier
    assert _override_sign("GLD", "de_risk") == 1.0    # held MORE damper
    assert _override_sign("GLD", "re_risk") == -1.0   # held LESS damper (refused buy)
    assert _override_sign("SPY", "sideways") is None


# --- counterfactual math --------------------------------------------------------------

def test_re_risk_spy_hold_graded_wrong():
    """Held MORE SPY than reference; SPY −4% vs reference ~+4.1% → disagreeing
    LOST → resolved_wrong, excess ≈ −8.1pp."""
    g = _grade_override(_row(), REF, _px)
    assert g["outcome_status"] == "resolved_wrong"
    assert g["resolved_correct"] is False
    assert abs(g["ret_reference_pct"] - _ref_return()) < 1e-6
    assert abs(g["excess_pp"] - (1.0 * (-4.0 - _ref_return()))) < 1e-6


def test_de_risk_spy_underweight_graded_correct():
    """Held LESS SPY than reference (trimmed beyond ref); SPY lagged the reference
    → disagreeing WON → resolved_correct, excess = +8.1pp (sign flips)."""
    g = _grade_override(_row(direction="de_risk"), REF, _px)
    assert g["outcome_status"] == "resolved_correct"
    assert g["resolved_correct"] is True
    assert g["excess_pp"] > 0


def test_damper_overweight_graded_correct_when_it_beats_reference():
    """Held MORE GLD than reference; GLD +10% vs reference ~+4.1% → correct."""
    g = _grade_override(_row(sleeve="GLD", direction="de_risk"), REF, _px)
    assert g["outcome_status"] == "resolved_correct"
    assert abs(g["excess_pp"] - (10.0 - _ref_return())) < 1e-6


def test_sgov_cash_sleeve_priced_not_zeroed():
    """The reference vector is SGOV-denominated cash — SGOV's real +0.3% return is
    in the counterfactual (the literal-cash remainder is absent → implicit 0.0)."""
    g = _grade_override(_row(), REF, _px)
    assert g["ret_reference_pct"] > (0.5 / 100 * -4.0) + (40.0 / 100 * 10.0)


# --- indeterminate guards --------------------------------------------------------------

def test_missing_reference_vector_indeterminate():
    g = _grade_override(_row(), None, _px)
    assert g["outcome_status"] == "indeterminate_data"
    assert g["resolved_correct"] is None


def test_missing_sleeve_price_indeterminate():
    g = _grade_override(_row(sleeve="EWJ"), REF, _px)   # EWJ not in price maps
    assert g["outcome_status"] == "indeterminate_data"


def test_material_reference_component_unpriced_indeterminate():
    """A ≥1% reference component that can't be priced voids the grade — never guess."""
    ref = {**REF, "IEMG": 5.0}   # IEMG unpriced, weight 5%
    g = _grade_override(_row(), ref, _px)
    assert g["outcome_status"] == "indeterminate_data"


def test_deminimis_floor_component_skipped():
    """A <1% floor sleeve without prices is skipped, not fatal."""
    ref = {**REF, "EWZ": 0.1}   # EWZ unpriced but de minimis
    g = _grade_override(_row(), ref, _px)
    assert g["outcome_status"] in ("resolved_correct", "resolved_wrong")


def test_invalid_direction_indeterminate():
    g = _grade_override(_row(direction="hold"), REF, _px)
    assert g["outcome_status"] == "indeterminate_data"


# --- aggregator -------------------------------------------------------------------------

def _stamped(direction="de_risk", correct=True, excess=5.0, outcome="accepted",
             enforced=False, premise="growth_axis"):
    return {
        "outcome_status": "resolved_correct" if correct else "resolved_wrong",
        "resolved_correct": correct,
        "excess_pp": excess if correct else -abs(excess),
        "direction": direction,
        "outcome": outcome,
        "enforced": enforced,
        "premise_challenged": premise,
    }


def test_aggregator_empty():
    block = _aggregate_override_record([])
    assert block["sample_size"] == 0
    assert "overall" not in block
    assert "do not infer" in block["caveat"]


def test_aggregator_splits_and_enforced_separation():
    rows = (
        [_stamped("de_risk", True, 6.0)] * 3
        + [_stamped("re_risk", False, 4.0, outcome="downsized")] * 2
        + [_stamped("de_risk", True, 2.0, outcome="rejected", enforced=True)] * 4
        + [{"outcome_status": "indeterminate_data", "direction": "de_risk"}]
    )
    block = _aggregate_override_record(rows)
    # indeterminate + enforced rows excluded from the model's judgment sample
    assert block["sample_size"] == 5
    assert block["overall"]["n"] == 5
    assert block["overall"]["win_rate"] == 0.6
    assert block["by_direction"]["de_risk"]["win_rate"] == 1.0
    assert block["by_direction"]["re_risk"]["win_rate"] == 0.0
    assert block["by_status"]["accepted"]["n"] == 3
    assert block["by_status"]["downsized"]["n"] == 2
    # enforced rows grade the ENFORCEMENT system, separately
    assert block["enforced_separately"]["n"] == 4
    assert block["enforced_separately"]["win_rate"] == 1.0
    assert "never a per-sleeve veto" in block["caveat"]


def test_aggregator_premise_promotion_at_n10():
    rows = [_stamped(premise="policy")] * 10 + [_stamped(premise="dollar_tilt")] * 3
    block = _aggregate_override_record(rows)
    assert "policy" in block["by_premise"]          # promoted at n≥10
    assert "dollar_tilt" not in block.get("by_premise", {})   # below threshold


def test_aggregator_avg_excess_math():
    rows = [_stamped(correct=True, excess=10.0), _stamped(correct=False, excess=4.0)]
    block = _aggregate_override_record(rows)
    assert block["overall"]["avg_excess_pp"] == 3.0   # (10 − 4) / 2
