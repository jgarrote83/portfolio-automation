"""Unit tests for the hard per-sleeve concentration cap (2026-09-02, G-6/B2).

A new Tier-1 invariant, independent of the override path: no single core sleeve
may exceed `single_sleeve_cap_pct_of_equity` (12.0) of equity, regardless of
override state — an override justifies a deviation, not a concentration. SOXX
reached 19.08% while passing every existing gate (B1's relative shelter alone
would have permitted ~11.6% at a 5.782% reference — this closes the remaining
gap for a larger reference or an unrelated shelter path). Enforced in BOTH
`reconcile` (unshelterable required sell) and `validate_trades` (buy window
ceiling), and never forces a sell below the sleeve's own reference.

Run: PYTHONPATH=src pytest tests/test_concentration_cap.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shared.reference_execution import reconcile  # noqa: E402
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
    "single_sleeve_cap_pct_of_equity": 12.0,
    "exempt_holds": [],
}


def _ctx(**kw):
    base = {"deployment_gate": "open", "equity_usd": 100_000.0, "cash_usd": 5_000.0,
            "date": "2026-09-02", "exempt_holds": []}
    base.update(kw)
    return base


def _gap(sym, cur, ref, price=100.0, held=None):
    if held is None:
        held = float(int(cur / 100 * 100_000 / price))
    return {"symbol": sym, "current_pct": cur, "reference_pct": ref, "price": price,
            "held_qty": held}


def _dec(sleeve, magnitude, direction="re_risk"):
    return {"outcome": "accepted", "reasons": [],
            "override": {"sleeve": sleeve, "magnitude_pp": magnitude, "direction": direction}}


# --- reconcile: unshelterable even under a full override ---------------------

def test_cap_breach_unshelterable_even_with_full_override():
    """SOXX at 19.08% vs a 5.782% reference: a full 15pp override (pre-B1) would
    have covered the whole 13.3pp gap. Post-B1 the relative cap alone already
    narrows this; B2 makes the portion above the 12% cap unshelterable
    regardless — assert a required move exists reaching down to at least 12%."""
    gaps = [_gap("SOXX", 19.08, 5.782, price=200.0)]
    decs = [_dec("SOXX", 15.0)]
    r = reconcile(gaps, [], decs, CFG, _ctx(deployment_gate="closed"))
    soxx = r["sleeves"]["SOXX"]
    assert soxx["required_move_total_pp"] >= 19.08 - 12.0 - 0.01


def test_cap_breach_surfaces_even_inside_the_normal_band():
    """XLP at 13% vs a 10% reference sits inside the default 5pp band (gap 3)
    but breaches the 12% cap — B2 must surface it regardless of the band."""
    gaps = [_gap("XLP", 13.0, 10.0)]
    r = reconcile(gaps, [], [], CFG, _ctx())
    assert "XLP" in r["sleeves"]
    assert r["sleeves"]["XLP"]["required_move_total_pp"] >= 1.0 - 0.01


def test_cap_never_forces_a_sell_below_reference():
    """A legitimately concentrated reference AT or ABOVE the cap (a genuine
    high-conviction target) is not a 'breach' — the cap must never force a
    sell that would land below the sleeve's own reference."""
    gaps = [_gap("SPY", 19.0, 15.0)]   # reference itself (15) >= cap (12)
    r = reconcile(gaps, [], [], CFG, _ctx())
    # Only the ORDINARY gap (19-15=4, inside the 5pp band) applies -- no extra
    # cap-driven requirement, since forcing to 12 would go below reference (15)
    # -- so the sleeve isn't even out of band, exactly as without B2.
    assert r["sleeves"] == {}


def test_cap_non_binding_at_normal_book_reference_size():
    """B1 x B2 interaction: at the 2026-09-02 live-book reference size (5.782%),
    B1's relative shelter ceiling (~11.6%) is already tighter than B2's 12% —
    B2 is non-binding in the normal case (documents which bound binds first)."""
    gaps = [_gap("SOXX", 19.08, 5.782, price=200.0)]
    decs = [_dec("SOXX", 15.0)]
    r = reconcile(gaps, [], decs, CFG, _ctx(deployment_gate="closed"))
    soxx = r["sleeves"]["SOXX"]
    # B1 alone (5.782 + 1.0*5.782 = 11.564) already requires a bigger move than
    # B2 alone would (19.08 - 12.0 = 7.08) -- B1 is the binding constraint here.
    assert soxx["allowed_residual_pp"] == 5.78  # B1's relative cap, not B2's 12%


# --- validate_trades: buy window ceiling -------------------------------------

def test_buy_past_cap_clamped_to_cap_not_window_ceiling():
    """Reference 5.782, override 15pp -> B1's relative cap already narrows the
    shelter to 5.782, so the window ceiling here is ref+5.782=11.564 -- below
    B2's 12% (documents B1 binding first at this reference size; see
    test_cap_non_binding_at_normal_book_reference_size in the B1 suite)."""
    gaps = [_gap("SOXX", 8.0, 5.782, price=200.0, held=40)]
    decs = [_dec("SOXX", 15.0)]
    ctx = _ctx(deployment_gate="open", cash_usd=50_000.0)
    ctx["effective_selected"] = {"semis": "SOXX"}
    res = validate_trades(gaps, [{"symbol": "SOXX", "side": "buy", "quantity": 100}],
                          decs, CFG, ctx)
    t = res["trades"][0]
    assert t["validation"]["status"] == "clamped"
    # post <= 12% -> quantity <= (12-8)pp of $100K at $200 = 20 shares
    assert t["quantity"] <= 20


def test_buy_rejected_when_already_at_or_above_cap():
    gaps = [_gap("SOXX", 12.5, 5.782, price=200.0, held=63)]
    ctx = _ctx(deployment_gate="open", cash_usd=50_000.0)
    ctx["effective_selected"] = {"semis": "SOXX"}
    res = validate_trades(gaps, [{"symbol": "SOXX", "side": "buy", "quantity": 10}],
                          [], CFG, ctx)
    assert len(res["rejected"]) == 1


def test_buy_up_to_a_concentrated_reference_above_cap_not_blocked():
    """A reference that is ITSELF >= the 12% cap (legitimate concentration) is
    not artificially blocked by B2 -- the ordinary window (ref+band) governs."""
    gaps = [_gap("SPY", 10.0, 15.0, price=100.0, held=100)]
    res = validate_trades(gaps, [{"symbol": "SPY", "side": "buy", "quantity": 40}],
                          [], CFG, _ctx(deployment_gate="open", cash_usd=50_000.0))
    t = res["trades"][0]
    assert t["validation"]["status"] == "passed"   # post 14% <= ref(15)+band(5)=20, uncapped
