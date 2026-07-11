"""Unit tests for the international governance block (Task F — FOLLOWUPS #36).

The intl sleeve is rotation/DXY-governed (roster_revision_2026-07 §4), leader-selective:
a base broad sleeve + a rotation-sized leader slot, with a DXY anti-chase and a gate
modifier that HALVES (never zeroes) the leader tilt — replacing the interim
suppress-to-zero rule. Run:
    PYTHONPATH=src pytest tests/test_intl_governance.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _aggregate_by_quadrant, _build_intl_governance  # noqa: E402

POOL = ["AIA", "EWJ", "IEMG", "IDMO", "VSS", "EWZ"]
INTL_CFG = {
    "intl_base_pp": 2.0, "leader_tilt_mid_pp": 1.0, "leader_tilt_high_pp": 3.0,
    "leader_min_excess_pp": 5.0, "max_leaders_high": 2,
}


def _rr(composite, leaders, dxy="tailwind", macross=None):
    return {
        "rotation_score": {"composite": composite, "category": "x"},
        "leaders_vs_spy": [{"ticker": t, "excess_pp": ex} for t, ex in leaders],
        "dxy_tailwind_for_intl": dxy,
        "ratio_ma_cross": {f"{t}/SPY": {"signal": s} for t, s in (macross or {}).items()},
    }


def _gate(status):
    return {"status": status}


def _build(rr, gate="open", shock=None, prev=None):
    return _build_intl_governance(
        rr, _gate(gate), {"shock_level": shock}, POOL, "VXUS", prev or {}, INTL_CFG,
    )


# --- sizing ladder ---------------------------------------------------------------

def test_low_composite_base_only():
    block, _ = _build(_rr(2, [("AIA", 11)]))
    assert block["leader_pp"] == 0.0
    assert block["sleeve_target_pp"] == 2.0
    assert block["intl_targets_pct"] == {"VXUS": 2.0}


def test_mid_composite_one_pp_leader():
    block, _ = _build(_rr(5, [("AIA", 11)]))
    assert block["leader_pick"] == "AIA"
    assert block["leader_pp"] == 1.0
    assert block["sleeve_target_pp"] == 3.0


def test_high_composite_three_pp_leader():
    block, _ = _build(_rr(8, [("AIA", 11)]))
    assert block["leader_pp"] == 3.0
    assert block["sleeve_target_pp"] == 5.0


# --- DXY anti-chase --------------------------------------------------------------

def test_dxy_headwind_zeroes_leader_tilt():
    block, _ = _build(_rr(8, [("AIA", 11)], dxy="headwind"))
    assert block["leader_pp"] == 0.0
    assert block["sleeve_target_pp"] == 2.0
    assert "dxy_headwind_zeroed" in block["modifiers"]


def test_dxy_neutral_halves_leader_tilt():
    block, _ = _build(_rr(8, [("AIA", 11)], dxy="neutral"))
    assert block["leader_pp"] == 1.5   # 3 / 2
    assert "dxy_neutral_halved" in block["modifiers"]


# --- gate modifier (replaces the interim suppress-to-zero) -----------------------

def test_gate_closed_halves_not_zeroes():
    block, _ = _build(_rr(8, [("AIA", 11)], dxy="tailwind"), gate="closed")
    assert block["leader_pp"] == 1.5   # halved, NOT zeroed
    assert "gate_closed_halved" in block["modifiers"]


def test_gate_closed_and_dxy_neutral_stack():
    block, _ = _build(_rr(8, [("AIA", 11)], dxy="neutral"), gate="closed")
    assert block["leader_pp"] == 0.75   # 3 / 2 / 2


def test_shock_lift_annotated():
    block, _ = _build(_rr(8, [("AIA", 11)]), shock=3)
    assert any("shock_level_3" in m for m in block["modifiers"])


# --- leader pick + tie-break -----------------------------------------------------

def test_leader_pick_restricted_to_pool_and_min_excess():
    # SPY-adjacent name not in pool, and a sub-5pp name, are both ineligible.
    block, _ = _build(_rr(8, [("SPY", 20), ("EWJ", 3)]))
    assert block["leader_pick"] is None
    assert block["leader_pp"] == 0.0


def test_tie_break_bullish_over_mixed():
    block, _ = _build(_rr(8, [("AIA", 11), ("EWJ", 8)],
                          macross={"AIA": "mixed", "EWJ": "bullish_intl"}))
    assert block["leader_pick"] == "EWJ"           # bullish ranks above mixed
    assert set(block["leader_picks"]) == {"EWJ", "AIA"}   # up to 2 at high composite


# --- de-rotation -----------------------------------------------------------------

def test_de_rotation_ma_bearish_unwinds_leader():
    block, _ = _build(
        _rr(8, [("AIA", 11)], macross={"AIA": "bearish_intl"}),
        prev={"leader": "AIA", "composite": 8},
    )
    assert block["leader_pick"] is None
    assert block["leader_pp"] == 0.0
    assert block["de_rotation"] == {"triggered": True, "trigger": "ma_bearish",
                                    "prior_leader": "AIA"}


def test_de_rotation_leader_lost_status():
    block, _ = _build(
        _rr(8, [("AIA", 3)]),   # AIA fell below +5pp
        prev={"leader": "AIA", "composite": 8},
    )
    assert block["leader_pick"] is None
    assert block["de_rotation"]["trigger"] == "leader_lost_status"


def test_composite_fade_echoed():
    block, _ = _build(_rr(5, [("AIA", 11)]), prev={"leader": "AIA", "composite": 8})
    assert block["de_rotation"]["trigger"] == "composite_fade"


def test_indeterminate_composite_holds_base_only():
    block, _ = _build(_rr(None, [("AIA", 11)]))
    assert block["status"] == "indeterminate"
    assert block["sleeve_target_pp"] == 2.0
    assert block["leader_pp"] == 0.0


# --- by_quadrant intl bucket -----------------------------------------------------

def test_by_quadrant_has_intl_bucket_summing_to_100():
    targets = {"SPY": 50.0, "GLD": 25.0, "VXUS": 2.0, "AIA": 1.0, "SGOV": 20.0}
    by_q = _aggregate_by_quadrant(targets, literal_cash_pct=2.0)
    assert by_q["intl"] == 3.0            # VXUS + AIA
    assert abs(sum(by_q.values()) - 100.0) <= 0.5
