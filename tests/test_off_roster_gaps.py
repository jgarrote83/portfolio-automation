"""Unit tests for off-roster held-name gap rows (2026-07-13 audit finding 3).

A held name outside both the reference targets and CORE_ROSTER (a flex leftover
like MU) previously had no gap row: `_post_validation_cash` missed its proceeds and
its SELL reached V3/V4 unvalidated (no sell<=held clamp). `_build_reference_gaps`
now appends a `reference_pct: 0.0`, `off_roster: True` row for it — visible to the
Tier-1 validator's sell-side checks, but invisible to `reconcile` (band enforcement
must never synthesize a trade for a flex leftover). Run:
    PYTHONPATH=src pytest tests/test_off_roster_gaps.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer.handler import _build_reference_gaps  # noqa: E402
from shared.reference_execution import reconcile  # noqa: E402
from shared.trade_validation import validate_trades  # noqa: E402

CFG = {
    "override_protocol": {"max_magnitude_pp": 15.0, "re_risk_min_evidence": 2, "gap_band_pp": 5.0},
    "reference_execution": {"tranche_pp_max": 10.0, "enforce": True,
                            "enforcement_turnover_max_pct": 20.0, "min_notional_usd": 115.0},
    "sleeve_floor_pct_of_core": 0.1,
    "active_quadrant_ceiling_pct_of_core": 90.0,
    "exempt_holds": [],
}


def _snapshot(mu_qty=2, mu_price=75.0):
    return {
        "reference_weights": {"target_weights_pct": {"SPY": 40.0, "GLD": 30.0}},
        "regime_gate": {"status": "open"},
        "paper_account": {
            "equity": 100_000.0, "cash": 5_000.0,
            "positions": [
                {"ticker": "SPY", "qty": 80, "market_value": 40_000.0, "current_price": 500.0},
                {"ticker": "MU", "qty": mu_qty, "market_value": mu_qty * mu_price,
                 "current_price": mu_price},
            ],
        },
        "prices": {"SPY": {"c": 500.0}},   # MU has NO snapshot price — position fallback only
    }


def test_off_roster_row_appended_with_zero_reference_and_flag():
    gaps, _ctx = _build_reference_gaps(_snapshot())
    mu = next((g for g in gaps if g["symbol"] == "MU"), None)
    assert mu is not None
    assert mu["reference_pct"] == 0.0
    assert mu["off_roster"] is True
    assert mu["price"] == 75.0   # priced via the position-price fallback
    assert mu["held_qty"] == 2


def test_non_off_roster_rows_not_flagged():
    gaps, _ctx = _build_reference_gaps(_snapshot())
    spy = next(g for g in gaps if g["symbol"] == "SPY")
    assert spy["off_roster"] is False


def test_reconcile_skips_off_roster_rows():
    """MU sits enormously overweight vs its 0 reference (10x the band), but reconcile
    must never synthesize a trade for it — it isn't even reported in `sleeves`."""
    gaps, ctx = _build_reference_gaps(_snapshot(mu_qty=200, mu_price=75.0))
    ctx["date"] = "2026-07-13"
    ctx["exempt_holds"] = []
    recon = reconcile(gaps, [], [], CFG, ctx)
    assert "MU" not in recon["sleeves"]
    assert all(t["symbol"] != "MU" for t in recon["enforced_trades"])


def test_off_roster_buy_still_rejected_by_v1_even_with_row_present():
    gaps, ctx = _build_reference_gaps(_snapshot())
    ctx["date"] = "2026-07-13"
    ctx["exempt_holds"] = []
    res = validate_trades(gaps, [{"id": "T-MU-buy", "symbol": "MU", "side": "buy",
                                  "quantity": 5}], [], CFG, ctx)
    assert len(res["rejected"]) == 1
    assert any("off-roster" in r for r in res["rejected"][0]["validation"]["reasons"])


def test_off_roster_full_exit_sell_passes_v3_no_floor():
    """floor_lb stays 0 for an off-roster name (not in CORE_ROSTER) — a full-exit
    sell to 0 is not floor-clamped."""
    gaps, ctx = _build_reference_gaps(_snapshot(mu_qty=2, mu_price=75.0))
    ctx["date"] = "2026-07-13"
    ctx["exempt_holds"] = []
    res = validate_trades(gaps, [{"id": "T-MU-sell", "symbol": "MU", "side": "sell",
                                  "quantity": 2}], [], CFG, ctx)
    assert res["rejected"] == []
    t = res["trades"][0]
    assert t["validation"]["status"] == "passed"
    assert t["quantity"] == 2


def test_off_roster_sell_clamped_to_held():
    """SELL 5 while holding 2 gets clamped to 2 — closing the 2026-07-13 audit
    finding 3(b) gap where an off-roster sell reached the executor unvalidated. The
    clamp fires via V3's zero-floor (ref is pinned to 0.0 for an off-roster row, so
    the window floor lo=0 is mathematically equivalent to "can't sell more than
    held" — V4's explicit held-clamp is then a no-op belt on top)."""
    gaps, ctx = _build_reference_gaps(_snapshot(mu_qty=2, mu_price=75.0))
    ctx["date"] = "2026-07-13"
    ctx["exempt_holds"] = []
    res = validate_trades(gaps, [{"id": "T-MU-sell", "symbol": "MU", "side": "sell",
                                  "quantity": 5}], [], CFG, ctx)
    assert res["rejected"] == []
    t = res["trades"][0]
    assert t["validation"]["status"] == "clamped"
    assert t["quantity"] == 2
