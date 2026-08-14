"""Task D5 (2026-08-14 audit) — thematic conviction floor-lift integration
into `_build_reference_weights`. Rewritten 2026-08-14 (same-day re-audit,
PR #38 remediation M1/M2/M4).

Deviation from the literal spec note: the spec describes this as inserted
"before step 4's floor assembly" (core-relative units); this codebase's
`raw_core`/`core_target` are %-of-CORE at that point, while
`thematic_conviction.active[].applied_pct_of_equity` is %-of-EQUITY by design
(D2) — the %-of-core -> %-of-equity conversion factor (`scale`, derived from
`core_room`) is not known until step 5. The lift is therefore applied
immediately after step 5's equity-denominated `weights` dict is built —
functionally identical (still a pure floor, still post-quadrant-math) but at
the point where the units actually line up. Verified empirically below.

M1/M2 remediation: the original cut applied the lift as a bare `max()` with NO
compensating reduction — purely additive to a book already at ~100%. At the
configured `aggregate_cap_pct_of_equity` (8.0) this asked the reference to
hold ~106-108% of equity, and `ceiling_pressure` (gated on a THEORETICAL
"living hedge" size that ignored floor protection) never fired in-cap. The
fix is budget-conserving: every pp added is drawn from elsewhere in the core
block, non-active-quadrant names first (pool 1), active-quadrant names only
as spillover (pool 2, `ceiling_pressure`), each name protected at its own
equity-equivalent sleeve floor. NOTE: in a single-quadrant-CONFIRMED regime
(not borderline), every non-active core name already sits at EXACTLY its
floor (`weights[t] == floor_pct_of_core * scale`, zero headroom by
construction — the "floor" IS the scaled value, there is nothing above it to
reduce) — so pool 1 has ZERO real capacity and ANY in-cap lift necessarily
spills into the active quadrant. This is not a test artifact; it is the
actual behavior of a confirmed-quadrant regime. A borderline regime (no
single confirmed quadrant, multiple quadrants blended via
`borderline_blend`) is the one case where non-"active" names carry real
above-floor weight and pool 1 has genuine capacity — covered separately below.

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

# Borderline (growth confirmed rising, inflation ambiguous -> favored=[Q1,Q2],
# no single confirmed quad) -> genuine non-active headroom via the intersection
# blend (COWZ/PDBC/QQQ/SMH/SPY/VDE/VTIP/XLF/XLI all elevated together), unlike
# a fully-indeterminate borderline (both axes flat) where EVERY name sits at
# exactly the scaled bare floor with zero headroom anywhere.
BORDERLINE_G = {"direction": "rising", "confidence": "high"}
BORDERLINE_I = {"direction": "flat"}
BORDERLINE_PAPER = _paper({"SPY": 20, "QQQ": 15, "SMH": 10, "GLD": 5, "VTIP": 5,
                           "VDE": 0.2, "SGOV": 10})


def _build(thematic=None, paper=None, g=None, i=None, quarantined=None):
    return _build_reference_weights(
        paper or PAPER, g or G, i or INFL, GATE, ROT, {}, {}, {}, CFG,
        thematic_conviction=thematic, quarantined_symbols=quarantined,
    )


def _total(rw):
    return sum(rw["target_weights_pct"].values()) + rw.get("literal_cash_target_pct", 0.0)


def test_no_thematic_block_is_a_pure_no_op():
    baseline = _build(None)
    assert baseline["thematic_lean"] == {
        "applied": False, "lifted": {}, "ceiling_pressure": False, "ceiling_pressure_pp": 0.0,
        "budget_clamped": False, "budget_dropped_pp": 0.0, "rejected_at_apply": [],
    }


def test_disabled_thematic_block_is_a_no_op():
    out = _build({"available": True, "enabled": False, "active": [
        {"symbol": "VDE", "applied_pct_of_equity": 2.0},
    ]})
    baseline = _build(None)
    assert out["target_weights_pct"]["VDE"] == baseline["target_weights_pct"]["VDE"]
    assert out["thematic_lean"]["applied"] is False


# --- M1: the sum-to-~100 invariant, across the full 0->cap range + above ----

def test_invariant_holds_across_full_lift_range():
    """The required invariant test: for every thematic total from 0 to
    aggregate_cap_pct_of_equity in 0.5pp steps, plus one value above the cap,
    the reference total must stay within 0.05pp of the baseline — never
    purely additive. This is the point of the M1 fix; it fails outright on
    pre-fix source (a bare max() with no compensating reduction)."""
    baseline_total = _total(_build(None))
    cap = float(CFG["thematic_conviction"]["aggregate_cap_pct_of_equity"])
    pp = 0.0
    steps = []
    while pp <= cap:
        steps.append(round(pp, 2))
        pp += 0.5
    steps.append(cap + 4.0)   # one value above the cap
    for lift_pp in steps:
        tc = {"available": True, "enabled": True, "active": [
            {"symbol": "VDE", "applied_pct_of_equity": lift_pp},
        ]}
        out = _build(tc)
        total = _total(out)
        assert abs(total - baseline_total) < 0.05, (
            f"lift={lift_pp}: total={total} vs baseline={baseline_total}"
        )


def test_extreme_lift_triggers_budget_clamp_not_a_broken_total():
    """When even the full reducible pool (pool1+pool2, floor-protected)
    can't fund the lift, the LIFT ITSELF is scaled down pro-rata —
    budget_clamped: true with the pp dropped — rather than breaking the
    total or silently under-funding without saying so."""
    baseline_total = _total(_build(None))
    tc = {"available": True, "enabled": True, "active": [
        {"symbol": "VDE", "applied_pct_of_equity": 95.0},
    ]}
    out = _build(tc)
    assert abs(_total(out) - baseline_total) < 0.05
    tl = out["thematic_lean"]
    assert tl["budget_clamped"] is True
    assert tl["budget_dropped_pp"] > 0
    # The actually-applied VDE weight is below the requested 95.0 (clamped).
    assert out["target_weights_pct"]["VDE"] < 95.0


# --- M2: ceiling_pressure as a real, in-cap, reachable spillover flag -------

def test_confirmed_quadrant_regime_spills_into_active_quadrant_in_cap():
    """In a CONFIRMED single-quadrant regime (PAPER: Q1), every non-active
    core name already sits at exactly its floor (zero headroom by
    construction) -- so pool 1 has NO real capacity and an in-cap lift on an
    out-of-favor name (VDE) necessarily spills into the active quadrant
    (SPY/QQQ/SMH). This is real, in-cap spillover -- unlike the pre-fix
    "ceiling_pressure" which only fired at a 40pp lift the caps forbid."""
    cap = float(CFG["thematic_conviction"]["aggregate_cap_pct_of_equity"])
    assert cap <= 8.0   # sanity: the lift below is in-cap under current config
    baseline = _build(None)
    out = _build({"available": True, "enabled": True, "active": [
        {"symbol": "VDE", "applied_pct_of_equity": 2.5},   # in-cap
    ]})
    assert out["target_weights_pct"]["VDE"] == 2.5
    assert out["target_weights_pct"]["SPY"] < baseline["target_weights_pct"]["SPY"]
    tl = out["thematic_lean"]
    assert tl["ceiling_pressure"] is True
    assert tl["ceiling_pressure_pp"] > 0
    assert abs(_total(out) - _total(baseline)) < 0.05


def test_borderline_regime_has_non_active_headroom_no_spill_needed():
    """In a BORDERLINE regime with a genuine 2-quadrant intersection blend
    (growth confirmed rising, inflation ambiguous -> favored=[Q1,Q2] -- the
    intersection names COWZ/PDBC/QQQ/SMH/SPY/VTIP/XLF/XLI/VDE all carry real
    above-floor weight), a lift above VDE's own elevated baseline is funded
    entirely from that non-active headroom -- ceiling_pressure stays False.
    (A fully-indeterminate borderline, both axes flat, gives every name the
    exact scaled bare floor with zero headroom anywhere -- that is NOT this
    case, and is not a realistic "spare capacity" scenario at all.)"""
    baseline = _build(None, paper=BORDERLINE_PAPER, g=BORDERLINE_G, i=BORDERLINE_I)
    assert baseline["active_quadrant"] is None   # confirms genuinely borderline
    vde_baseline = baseline["target_weights_pct"]["VDE"]
    out = _build({"available": True, "enabled": True, "active": [
        {"symbol": "VDE", "applied_pct_of_equity": vde_baseline + 2.5},
    ]}, paper=BORDERLINE_PAPER, g=BORDERLINE_G, i=BORDERLINE_I)
    assert out["target_weights_pct"]["VDE"] == round(vde_baseline + 2.5, 3)
    assert out["target_weights_pct"]["GLD"] == baseline["target_weights_pct"]["GLD"]  # Q3/Q4-only, untouched
    assert out["target_weights_pct"]["SPY"] < baseline["target_weights_pct"]["SPY"]   # pool 1 funded it
    assert out["thematic_lean"]["ceiling_pressure"] is False
    assert abs(_total(out) - _total(baseline)) < 0.05


def test_floor_lift_never_reduces_a_quadrant_driven_weight():
    """A thematic 'lift' target BELOW the current quadrant-driven weight must
    never reduce it — floor lift, never a ceiling."""
    baseline = _build(None)
    spy_baseline = baseline["target_weights_pct"]["SPY"]
    out = _build({"available": True, "enabled": True, "active": [
        {"symbol": "SPY", "applied_pct_of_equity": 1.0},   # far below SPY's real weight
    ]})
    assert out["target_weights_pct"]["SPY"] == spy_baseline


def test_lifted_dict_reports_only_the_delta_applied():
    lifted = _build({"available": True, "enabled": True, "active": [
        {"symbol": "VDE", "applied_pct_of_equity": 2.0},
    ]})
    # VDE's baseline floor was ~0.096 -> delta is 2.0 - 0.096, not the full 2.0.
    assert abs(lifted["thematic_lean"]["lifted"]["VDE"] - 1.904) < 1e-3


def test_zero_or_negative_applied_pct_is_a_no_op_for_that_symbol():
    baseline = _build(None)
    out = _build({"available": True, "enabled": True, "active": [
        {"symbol": "VDE", "applied_pct_of_equity": 0.0},
    ]})
    assert out["target_weights_pct"]["VDE"] == baseline["target_weights_pct"]["VDE"]
    assert out["thematic_lean"]["applied"] is False


# --- M4: apply-gate re-validation — never trust tc["active"] blindly -------

def test_legacy_exit_injected_directly_is_rejected_at_apply():
    out = _build({"available": True, "enabled": True, "active": [
        {"symbol": "EUAD", "applied_pct_of_equity": 4.0},
    ]})
    assert out["target_weights_pct"].get("EUAD") is None
    assert out["thematic_lean"]["applied"] is False
    assert out["thematic_lean"]["rejected_at_apply"] == [
        {"symbol": "EUAD", "reason": "legacy_exit"},
    ]


def test_price_quarantined_injected_directly_is_rejected_at_apply():
    out = _build({"available": True, "enabled": True, "active": [
        {"symbol": "MU", "applied_pct_of_equity": 4.0},
    ]}, quarantined={"MU"})
    assert out["target_weights_pct"].get("MU") is None
    assert out["thematic_lean"]["rejected_at_apply"] == [
        {"symbol": "MU", "reason": "price_quarantined"},
    ]


def test_non_selected_pool_member_injected_directly_is_rejected_at_apply():
    """SOXX (semis pool, non-selected -- SMH is the default) must be rejected
    even with no override map at all (effective_selected=None is the DEFAULT
    _build() passes) -- the M3 leak this apply-gate check must not repeat."""
    out = _build({"available": True, "enabled": True, "active": [
        {"symbol": "SOXX", "applied_pct_of_equity": 4.0},
    ]})
    assert out["target_weights_pct"].get("SOXX") is None
    assert out["thematic_lean"]["rejected_at_apply"] == [
        {"symbol": "SOXX", "reason": "non_selected_pool_member"},
    ]


def test_off_roster_name_injected_directly_is_rejected_at_apply():
    """A ticker not in CORE_ROSTER at all (routes to flex, D4 rule 3) must
    never receive a core reference weight even if handed directly to
    _build_reference_weights via a hand-edited/corrupted thematic block."""
    out = _build({"available": True, "enabled": True, "active": [
        {"symbol": "PANW", "applied_pct_of_equity": 4.0},
    ]})
    assert out["target_weights_pct"].get("PANW") is None
    assert out["thematic_lean"]["rejected_at_apply"] == [
        {"symbol": "PANW", "reason": "flex_route"},
    ]


def test_mixed_eligible_and_ineligible_entries_only_eligible_applies():
    out = _build({"available": True, "enabled": True, "active": [
        {"symbol": "VDE", "applied_pct_of_equity": 2.0},
        {"symbol": "EUAD", "applied_pct_of_equity": 4.0},
    ]})
    assert out["target_weights_pct"]["VDE"] == 2.0
    assert out["target_weights_pct"].get("EUAD") is None
    reasons = {e["symbol"]: e["reason"] for e in out["thematic_lean"]["rejected_at_apply"]}
    assert reasons == {"EUAD": "legacy_exit"}
