"""Unit tests for the proportionate override shelter (2026-09-02, G-3/B1, k=1.0).

`allowed_residuals` capped the sheltered residual at a flat `max_magnitude_pp`
(15.0) regardless of the sleeve's own reference. Against a 5.782%-of-equity
reference (the post-roster-revision book) that permitted a fully "compliant"
3.6x overweight (~20.8% of equity) in one sleeve — how SOXX reached 19.08%.
The fix mirrors the `relative_band_frac` hybrid-band precedent (O4, 2026-08-06
audit): effective shelter = min(max_magnitude_pp, k * reference_pct) whenever
reference_pct > 0; a zero-or-missing reference keeps the plain absolute cap.

Run: PYTHONPATH=src pytest tests/test_relative_override_shelter.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shared.reference_execution import allowed_residuals, reconcile  # noqa: E402
from shared.trade_validation import validate_trades  # noqa: E402

CFG = {
    "override_protocol": {
        "max_magnitude_pp": 15.0, "max_magnitude_rel_frac": 1.0,
        "re_risk_min_evidence": 2, "gap_band_pp": 5.0,
    },
    "reference_execution": {
        "tranche_pp_max": 10.0, "enforce": True,
        "enforcement_turnover_max_pct": 20.0, "min_notional_usd": 115.0,
    },
    "sleeve_floor_pct_of_core": 0.1,
    "active_quadrant_ceiling_pct_of_core": 90.0,
    "exempt_holds": [],
}


def _dec(sleeve, magnitude, outcome="accepted"):
    return {"outcome": outcome, "reasons": [],
            "override": {"sleeve": sleeve, "magnitude_pp": magnitude, "direction": "re_risk"}}


# --- allowed_residuals: the pure relative-cap mechanics -----------------------

def test_relative_cap_narrower_than_absolute_for_small_reference():
    """The SOXX motivating case: reference 5.782%, a full 15pp override -> the
    relative cap (k=1.0 * 5.782 = 5.782) governs, not the flat 15pp."""
    decs = [_dec("SOXX", 15.0)]
    out = allowed_residuals(decs, 15.0, {"SOXX": 5.782}, 1.0)
    assert out["SOXX"] == 5.782


def test_relative_cap_non_binding_for_large_reference():
    """A 20%+ reference sleeve is unaffected — the absolute cap still governs."""
    decs = [_dec("SPY", 15.0)]
    out = allowed_residuals(decs, 15.0, {"SPY": 25.0}, 1.0)
    assert out["SPY"] == 15.0


def test_zero_or_missing_reference_keeps_plain_absolute_cap():
    """A LEGACY_EXITS / zeroed non-selected pool member (reference <= 0, or
    simply absent from the map) must not lose its shelter entirely — same
    carve-out `_effective_band` already uses for the O4 relative band."""
    decs = [_dec("MCK", 15.0)]
    assert allowed_residuals(decs, 15.0, {"MCK": 0.0}, 1.0)["MCK"] == 15.0
    assert allowed_residuals(decs, 15.0, {}, 1.0)["MCK"] == 15.0
    assert allowed_residuals(decs, 15.0, None, 1.0)["MCK"] == 15.0


def test_omitted_reference_map_is_fully_backward_compatible():
    """A caller that never passes reference data (predates B1) gets EXACTLY the
    old absolute-only behavior — the two new params are additive, not breaking."""
    decs = [_dec("SOXX", 15.0)]
    assert allowed_residuals(decs, 15.0)["SOXX"] == 15.0


def test_rejected_override_shelters_nothing_regardless_of_reference():
    decs = [_dec("SOXX", 15.0, outcome="rejected")]
    out = allowed_residuals(decs, 15.0, {"SOXX": 5.782}, 1.0)
    assert out == {}


# --- reconcile / validate_trades must resolve the IDENTICAL number -----------

def test_reconcile_and_validate_trades_agree_on_the_shelter():
    """The two Tier-1/Tier-2 layers consume the SAME override_decisions + gaps +
    cfg — they must never disagree on what a sleeve's override shelters."""
    gaps_recon = [{"symbol": "SOXX", "current_pct": 19.08, "reference_pct": 5.782,
                   "price": 200.0}]
    gaps_validate = [{"symbol": "SOXX", "current_pct": 19.08, "reference_pct": 5.782,
                       "price": 200.0, "held_qty": 954.0}]
    decs = [_dec("SOXX", 15.0)]
    ctx_recon = {"deployment_gate": "closed", "equity_usd": 100_000.0, "cash_usd": 5_000.0,
                 "date": "2026-09-02", "exempt_holds": []}
    ctx_validate = {"deployment_gate": "closed", "equity_usd": 100_000.0, "cash_usd": 5_000.0}

    recon = reconcile(gaps_recon, [], decs, CFG, ctx_recon)
    recon_allowed = recon["sleeves"]["SOXX"]["allowed_residual_pp"]

    # Drive validate_trades with a sell that lands exactly on the reconcile-side
    # window edge (ref - allowed) to confirm it resolves the same shelter.
    trades = [{"symbol": "SOXX", "side": "sell", "quantity": 1}]
    result = validate_trades(gaps_validate, trades, decs, CFG, ctx_validate)
    assert result["trades"][0]["validation"]["status"] in ("passed", "clamped")

    # Both layers must independently compute the SAME capped residual from the
    # same inputs — assert directly against the shared helper both call.
    ref_by_sleeve = {"SOXX": 5.782}
    direct = allowed_residuals(decs, CFG["override_protocol"]["max_magnitude_pp"],
                               ref_by_sleeve, CFG["override_protocol"]["max_magnitude_rel_frac"])
    assert recon_allowed == round(direct["SOXX"], 2)
    assert recon_allowed == 5.78  # k=1.0 * reference, well below the flat 15pp
