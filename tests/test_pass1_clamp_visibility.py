"""Regression test for Task A (session 2026-08-01): a Tier-1 clamp applied in
PASS 1 of the two-pass validation (`analyzer/handler.py`) must survive pass 2 and
show up in the combined summary + report addendum.

Root cause (07-30 KMLM incident): the model proposed selling 182 KMLM ("trim to
1-share floor"); pass 1 clamped it to a floor-protected landing (179 shares in the
live incident). Pass 2 then re-validated the ALREADY-clamped quantity, found it
clean, and stamped it "passed" — silently overwriting the pass-1 "clamped" status
and its reason. `combined_summary["clamped"]` (sourced only from pass 2's own
summary) read 0, so the ⚠️ Trade-validation addendum never appeared — the report
said 182, Alpaca got a smaller clamped number, and nothing on the record explained
the gap.

Fixed by:
- A2 (`shared/trade_validation.py`): a trade entering `validate_trades` already
  stamped `"clamped"` keeps that status (and its reasons, deduped-merged with any
  new ones) even if this pass's own checks find nothing further to clamp.
- A1 (`analyzer/handler.py`): `combined_summary["clamped"]` — unchanged code, now
  correct as a consequence of A2, since pass 2's own clamped count already
  includes every clamp still standing after both passes.

Run: PYTHONPATH=src pytest tests/test_pass1_clamp_visibility.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer.handler import _validation_addendum  # noqa: E402
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
    # KMLM-shaped fixture: a low-price core damper held at (roughly) its own full
    # weight, reference sitting at its floor (out-of-favor sleeve) — a "sell it
    # all" proposal breaches the sleeve floor and must be clamped, not rejected.
    return [
        {"symbol": "KMLM", "current_pct": 5.22, "reference_pct": 0.1,
         "price": 28.69, "held_qty": 182},
    ]


def _ctx():
    return {
        "deployment_gate": "open", "equity_usd": 100_000.0, "cash_usd": 5_000.0,
        "exempt_holds": [],
    }


def test_pass1_clamp_survives_pass2_with_reason_and_status_intact():
    gaps = _gaps()
    ctx = _ctx()
    model_trades = [{"id": "T-1", "symbol": "KMLM", "side": "sell", "quantity": 182}]

    tv1 = validate_trades(gaps, model_trades, [], CFG, ctx)
    assert tv1["rejected"] == []
    kmlm1 = tv1["trades"][0]
    assert kmlm1["validation"]["status"] == "clamped"
    assert kmlm1["quantity"] < 182, "182 shares must breach the sleeve floor and get clamped"
    pass1_qty = kmlm1["quantity"]
    pass1_reasons = kmlm1["validation"]["reasons"]
    assert any("sell clamped 182" in r and "window floor" in r for r in pass1_reasons)

    # Pass 2 mirrors analyzer/handler.py: re-validate the pass-1 survivors (no
    # enforced trades needed here — the point under test is whether the clamp
    # SURVIVES a pass that finds nothing further to change).
    survivors = tv1["trades"]
    tv2 = validate_trades(gaps, survivors, [], CFG, ctx)
    assert tv2["rejected"] == []
    kmlm2 = tv2["trades"][0]
    assert kmlm2["quantity"] == pass1_qty, "pass 2 must not further change an already-clamped qty"
    assert kmlm2["validation"]["status"] == "clamped", (
        "pass 2 must NOT overwrite a pass-1 clamp back to 'passed'"
    )
    assert all(r in kmlm2["validation"]["reasons"] for r in pass1_reasons), (
        "the pass-1 clamp reason must survive into the final stamp"
    )

    # combined_summary exactly as analyzer/handler.py builds it (A1).
    combined_summary = {
        "passed": tv2["summary"]["passed"],
        "clamped": tv2["summary"]["clamped"],
        "rejected": 0,
    }
    assert combined_summary["clamped"] >= 1

    md = _validation_addendum(
        {"summary": combined_summary, "trades": tv2["trades"], "rejected": []},
    )
    assert "⚠️ Trade-validation addendum" in md
    assert "**CLAMPED**" in md
    assert "KMLM" in md


def test_pass2_clean_trade_still_stamps_passed_not_clamped():
    """Sanity check on the other side of A2: a trade with NO prior validation
    stamp (e.g. a band-enforcement-synthesized trade merged in fresh) that needs
    no clamping in this pass must still stamp "passed", not "clamped" — the fix
    only PRESERVES an existing clamp, it must never manufacture one."""
    gaps = [{"symbol": "KMLM", "current_pct": 0.0, "reference_pct": 3.0,
             "price": 28.69, "held_qty": 0}]
    ctx = _ctx()
    trades = [{"id": "T-2", "symbol": "KMLM", "side": "buy", "quantity": 10}]
    tv = validate_trades(gaps, trades, [], CFG, ctx)
    assert tv["rejected"] == []
    assert tv["trades"][0]["validation"]["status"] == "passed"
    assert tv["summary"]["clamped"] == 0
