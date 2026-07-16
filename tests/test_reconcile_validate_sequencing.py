"""Regression test for Task B1+B2 (session 2026-07-15): the 2026-07-14 KMLM
under-sizing, reproduced end-to-end from the pure `validate_trades`/`reconcile`
functions.

Root cause (two compounding seams in the SAME session):
- B1 (sequencing): `analyzer/handler.py` used to run `reconcile()` on the model's
  RAW trades BEFORE Tier-1 validation. A buy Tier-1 would go on to reject (07-14:
  a gate-closed VXUS buy — intl_broad is unconditionally amplifier_intl) was still
  counted as already-spent cash while `reconcile` sized its own enforcement buy.
- B2 (off-roster cash): `reconcile`'s `rows` dict excluded off-roster held names
  (flex leftovers like MU) entirely, so an off-roster SELL's proceeds never
  entered `cash_avail` either.

Together these cash-starved the synthesized KMLM enforcement buy on 07-14 to 57
shares of a true ~126 affordable. This test builds a scaled analogue (same shape,
smaller numbers) of that exact scenario and shows the fixed two-pass composition
(validate -> reconcile against SURVIVORS -> validate the merged list) recovers the
correct, larger enforcement size, while the OLD ordering (reconcile on the raw,
not-yet-validated list) still starves it even with B2 already fixed in `reconcile`
itself — isolating B1's contribution specifically.

Run: PYTHONPATH=src pytest tests/test_reconcile_validate_sequencing.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shared.reference_execution import reconcile  # noqa: E402
from shared.trade_validation import validate_trades  # noqa: E402

CFG = {
    "override_protocol": {
        "max_magnitude_pp": 15.0, "re_risk_min_evidence": 2, "gap_band_pp": 5.0,
    },
    "reference_execution": {
        "tranche_pp_max": 10.0, "enforce": True,
        "enforcement_turnover_max_pct": 20.0, "min_notional_usd": 115.0,
    },
    "sleeve_floor_pct_of_core": 0.1,
    "active_quadrant_ceiling_pct_of_core": 90.0,
    "exempt_holds": [],
}


def _gaps():
    return [
        # Amplifier-intl buy the gate-closed Tier-1 validator will reject.
        {"symbol": "VXUS", "current_pct": 0.0, "reference_pct": 2.0,
         "price": 84.65, "held_qty": 0},
        # Off-roster flex orphan sell (the MU analogue) — proceeds are real cash.
        {"symbol": "MU", "current_pct": 2.0, "reference_pct": 0.0,
         "price": 983.57, "held_qty": 2, "off_roster": True},
        # Under-tranche damper buy (the KMLM analogue) — model buys some, not all,
        # of the required tranche, leaving a shortfall for D3 to top up.
        {"symbol": "KMLM", "current_pct": 0.0, "reference_pct": 12.46,
         "price": 28.69, "held_qty": 0},
    ]


def _model_trades():
    return [
        {"id": "T-1", "symbol": "VXUS", "side": "buy", "quantity": 23},
        {"id": "T-2", "symbol": "MU", "side": "sell", "quantity": 2},
        {"id": "T-3", "symbol": "KMLM", "side": "buy", "quantity": 120},
    ]


def _ctx(cash_usd=6_000.0):
    return {
        "deployment_gate": "closed", "equity_usd": 100_000.0, "cash_usd": cash_usd,
        "date": "2026-07-14", "exempt_holds": [],
    }


def test_old_single_pass_ordering_starves_kmlm_enforcement():
    """The OLD composition: reconcile() runs on the model's RAW trades (including
    the not-yet-rejected VXUS buy) BEFORE Tier-1 validation ever sees the list.
    VXUS's $1,946.95 notional is wrongly counted as already spent, cash-starving
    the KMLM enforcement buy — even though `reconcile` itself already has the B2
    fix (MU's off-roster proceeds ARE counted here), this isolates B1's
    sequencing bug specifically. required_move_today is 7.46pp — a full,
    affordable-in-principle $7,460 shortfall — but the old ordering leaves only
    89 shares (~$2,553) of headroom instead of the true ~140 shares (~$4,017)."""
    gaps = _gaps()
    old_recon = reconcile(gaps, _model_trades(), [], CFG, _ctx())
    kmlm = old_recon["sleeves"]["KMLM"]
    assert kmlm["status"] == "enforced"
    starved_qty = kmlm["enforced_trade"]["quantity"]
    assert starved_qty == 89, f"expected the specific cash-starved fill of 89, got {starved_qty}"


def test_two_pass_composition_recovers_correct_kmlm_size():
    """The FIXED composition (mirrors analyzer/handler.py post-Task-B1): pass 1
    validates the model's raw trades and drops VXUS; reconcile runs against the
    SURVIVORS (VXUS's cash never counted as spent, MU's proceeds counted per B2);
    pass 2 re-validates the full merged list. Recovers the FULL required
    shortfall (140 shares — required_move_today_pp is not tranche-capped here),
    materially more than the old ordering's cash-starved 89."""
    gaps = _gaps()
    ctx = _ctx()

    tv1 = validate_trades(gaps, _model_trades(), [], CFG, ctx)
    rejected_symbols = {t["symbol"] for t in tv1["rejected"]}
    assert rejected_symbols == {"VXUS"}, tv1["rejected"]
    survivors = tv1["trades"]
    assert {t["symbol"] for t in survivors} == {"MU", "KMLM"}

    recon = reconcile(gaps, survivors, [], CFG, ctx)
    kmlm = recon["sleeves"]["KMLM"]
    assert kmlm["status"] == "enforced"
    fixed_qty = kmlm["enforced_trade"]["quantity"]
    assert fixed_qty == 140, f"expected the full required shortfall of 140, got {fixed_qty}"
    assert fixed_qty > 89   # materially larger than the old starved fill

    merged = sorted(
        survivors + recon["enforced_trades"],
        key=lambda t: str(t.get("side", "")).lower() != "sell",
    )
    tv2 = validate_trades(gaps, merged, [], CFG, ctx)
    # The synthesized trade must pass by construction (a rejection would be a
    # reconcile bug — the bad_enforced tripwire in analyzer/handler.py).
    assert not any(t.get("source") == "band_enforcement" for t in tv2["rejected"])
    kmlm_trades = [t for t in tv2["trades"] if t["symbol"] == "KMLM"]
    assert sum(t["quantity"] for t in kmlm_trades) == 120 + fixed_qty


def test_post_validation_cash_agrees_with_reconciles_cash_view():
    """B1's invariant: `analyzer._post_validation_cash` and `reconcile`'s internal
    `cash_avail` must never disagree about how much cash a trade list leaves —
    both now count an off-roster sell's proceeds (Task B2) toward available cash,
    using the same cash_usd + sell_notional - buy_notional formula."""
    from analyzer.handler import _post_validation_cash

    gaps = _gaps()
    ctx = _ctx(cash_usd=4_000.0)
    trades = [
        {"id": "T-2", "symbol": "MU", "side": "sell", "quantity": 2},
        {"id": "T-3", "symbol": "KMLM", "side": "buy", "quantity": 50},
    ]
    post_cash = _post_validation_cash(trades, gaps, ctx)

    sell_notional = 2 * 983.57
    buy_notional = 50 * 28.69
    reconcile_cash_avail = max(0.0, ctx["cash_usd"] + sell_notional - buy_notional)
    assert round(post_cash, 2) == round(reconcile_cash_avail, 2)
