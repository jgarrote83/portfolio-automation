"""Task D5 (2026-08-14 audit) — thematic conviction floor-lift integration
into `_build_reference_weights`.

Deviation from the literal spec note: the spec describes this as inserted
"before step 4's floor assembly" (core-relative units); this codebase's
`raw_core`/`core_target` are %-of-CORE at that point, while
`thematic_conviction.active[].applied_pct_of_equity` is %-of-EQUITY by design
(D2) — the %-of-core -> %-of-equity conversion factor (`scale`, derived from
`core_room`) is not known until step 5. The lift is therefore applied
immediately after step 5's equity-denominated `weights` dict is built —
functionally identical (still a pure floor, still post-quadrant-math) but at
the point where the units actually line up. Verified empirically below.

Run: PYTHONPATH=src pytest tests/test_thematic_reference_weights_integration.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_reference_weights, _load_risk_limits  # noqa: E402

CFG = _load_risk_limits()


def _paper(weights, equity=100_000.0, cash_pct=2.0):
    positions = [{"ticker": t, "qty": 1.0, "market_value": equity * w / 100.0}
                 for t, w in weights.items()]
    return {"available": True, "equity": equity, "cash": equity * cash_pct / 100.0,
            "positions": positions}


G = {"direction": "rising", "confidence": "high"}
INFL = {"direction": "falling"}
GATE = {"status": "open", "derived_from": {"policy_stance": "dovish"}}
ROT = {"dxy_tailwind_for_intl": "neutral"}
PAPER = _paper({"SPY": 30, "QQQ": 20, "SMH": 20, "VDE": 0.2, "SGOV": 10})


def _build(thematic=None):
    return _build_reference_weights(
        PAPER, G, INFL, GATE, ROT, {}, {}, {}, CFG, thematic_conviction=thematic,
    )


def test_no_thematic_block_is_a_pure_no_op():
    baseline = _build(None)
    assert baseline["thematic_lean"] == {
        "applied": False, "lifted": {}, "ceiling_pressure": False, "ceiling_pressure_pp": 0.0,
    }


def test_disabled_thematic_block_is_a_no_op():
    out = _build({"available": True, "enabled": False, "active": [
        {"symbol": "VDE", "applied_pct_of_equity": 2.0},
    ]})
    baseline = _build(None)
    assert out["target_weights_pct"]["VDE"] == baseline["target_weights_pct"]["VDE"]
    assert out["thematic_lean"]["applied"] is False


def test_small_lift_within_room_never_touches_active_quadrant():
    """A modest thematic lift (2.0pp on VDE, a floored Q1 damper at 0.096%)
    fits inside the non-active-quadrant remainder — SPY (an active Q1
    concentrate name) must be UNCHANGED."""
    baseline = _build(None)
    lifted = _build({"available": True, "enabled": True, "active": [
        {"symbol": "VDE", "applied_pct_of_equity": 2.0},
    ]})
    assert lifted["target_weights_pct"]["VDE"] == 2.0
    assert lifted["target_weights_pct"]["SPY"] == baseline["target_weights_pct"]["SPY"]
    assert lifted["thematic_lean"]["ceiling_pressure"] is False


def test_floor_lift_never_reduces_a_quadrant_driven_weight():
    """A thematic 'lift' target BELOW the current quadrant-driven weight must
    never reduce it — floor lift, never a ceiling."""
    baseline = _build(None)
    spy_baseline = baseline["target_weights_pct"]["SPY"]
    out = _build({"available": True, "enabled": True, "active": [
        {"symbol": "SPY", "applied_pct_of_equity": 1.0},   # far below SPY's real weight
    ]})
    assert out["target_weights_pct"]["SPY"] == spy_baseline


def test_large_lift_exceeding_room_triggers_ceiling_pressure_and_trims_active_quadrant():
    """A large thematic lift (40pp on VDE) exceeds the non-active-quadrant
    remainder — ceiling_pressure must surface True with the pp taken, and the
    active quadrant's own names (SPY) must be trimmed down, never silently
    breaching the 90%-of-core ceiling unnoticed."""
    baseline = _build(None)
    out = _build({"available": True, "enabled": True, "active": [
        {"symbol": "VDE", "applied_pct_of_equity": 40.0},
    ]})
    assert out["target_weights_pct"]["VDE"] == 40.0
    assert out["target_weights_pct"]["SPY"] < baseline["target_weights_pct"]["SPY"]
    assert out["thematic_lean"]["ceiling_pressure"] is True
    assert out["thematic_lean"]["ceiling_pressure_pp"] > 0


def test_lifted_dict_reports_only_the_delta_applied():
    lifted = _build({"available": True, "enabled": True, "active": [
        {"symbol": "VDE", "applied_pct_of_equity": 2.0},
    ]})
    # VDE's baseline floor was 0.096 -> delta is 2.0 - 0.096, not the full 2.0.
    assert abs(lifted["thematic_lean"]["lifted"]["VDE"] - 1.904) < 1e-3


def test_zero_or_negative_applied_pct_is_a_no_op_for_that_symbol():
    baseline = _build(None)
    out = _build({"available": True, "enabled": True, "active": [
        {"symbol": "VDE", "applied_pct_of_equity": 0.0},
    ]})
    assert out["target_weights_pct"]["VDE"] == baseline["target_weights_pct"]["VDE"]
    assert out["thematic_lean"]["applied"] is False
