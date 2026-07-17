"""Session 2026-07-17, Task A — gap-row price basis must agree with `current_pct`'s
basis (both paper-account-priced), or V3's window math phantom-clamps a legitimate
full exit.

Root cause (2026-07-16 production incident, MU): `_build_reference_gaps` computed
`current_pct` from the paper account's `market_value` (paper-priced) but its `_price`
helper let the FMP EOD close win over the position's `current_price` for a HELD name.
V3's landing-percentage math (`shared/trade_validation.py`) mixes the two:
    delta_pp = qty * price / equity * 100        # price = FMP close
    post = current_pct - delta_pp                 # current_pct = paper-priced
so a full exit (which must land at exactly 0%) lands at a small nonzero residual
whenever FMP and paper prices diverge — and whether that residual trips the
`_EPS_PP = 0.05` epsilon is direction- and magnitude-dependent, not a real signal.
Verified matrix reproduced below with the production numbers: 5.9% divergence
clamped 2 shares -> 1; 2.8% slipped under epsilon and passed (luck, not correctness);
3.1% clamped again.

The fix makes `_price` prefer the paper-account position's `current_price` for any
symbol currently held, falling back to the FMP close only when there is no position
(an unheld reference target). This makes `current_pct` and the gap-row `price` always
share one basis, so `delta_pp` exactly cancels `current_pct` on a full exit regardless
of how far FMP and paper prices have diverged.

Run: PYTHONPATH=src pytest tests/test_price_basis_coherence.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer.handler import _build_reference_gaps  # noqa: E402
from shared.trade_validation import validate_trades  # noqa: E402

CFG = {
    "override_protocol": {"max_magnitude_pp": 15.0, "re_risk_min_evidence": 2, "gap_band_pp": 5.0},
    "reference_execution": {"tranche_pp_max": 10.0, "enforce": True,
                            "enforcement_turnover_max_pct": 20.0, "min_notional_usd": 115.0},
    "sleeve_floor_pct_of_core": 0.1,
    "active_quadrant_ceiling_pct_of_core": 90.0,
    "exempt_holds": [],
}

_EQUITY = 97_842.94
_MU_QTY = 2
_MU_PAPER_PRICE = 853.90


def _mu_snapshot(fmp_price: float) -> dict:
    mv = _MU_QTY * _MU_PAPER_PRICE
    return {
        "reference_weights": {"target_weights_pct": {"SPY": 40.0}},
        "regime_gate": {"status": "open"},
        "paper_account": {
            "equity": _EQUITY, "cash": 5_000.0,
            "positions": [
                {"ticker": "SPY", "qty": 80, "market_value": 40_000.0, "current_price": 500.0},
                {"ticker": "MU", "qty": _MU_QTY, "market_value": mv,
                 "current_price": _MU_PAPER_PRICE},
            ],
        },
        "prices": {"SPY": {"c": 500.0}, "MU": {"c": fmp_price}},
    }


def _full_exit_result(fmp_price: float) -> dict:
    gaps, ctx = _build_reference_gaps(_mu_snapshot(fmp_price))
    ctx["date"] = "2026-07-17"
    ctx["exempt_holds"] = []
    return validate_trades(
        gaps, [{"id": "T-MU-exit", "symbol": "MU", "side": "sell", "quantity": _MU_QTY}],
        [], CFG, ctx,
    )


def test_gap_row_price_prefers_paper_position_price_for_held_name():
    """The fix itself: with FMP and paper prices diverging 5.9%, the gap row's price
    must equal the paper price (853.90), not the FMP close (904.28)."""
    gaps, _ctx = _build_reference_gaps(_mu_snapshot(904.28))
    mu = next(g for g in gaps if g["symbol"] == "MU")
    assert mu["price"] == _MU_PAPER_PRICE


def test_unheld_reference_target_still_priced_from_fmp():
    """A symbol with no paper-account position (an unheld reference target) has no
    paper price to prefer — FMP remains the only source."""
    snap = _mu_snapshot(904.28)
    snap["reference_weights"]["target_weights_pct"]["GLD"] = 20.0
    snap["prices"]["GLD"] = {"c": 205.5}
    gaps, _ctx = _build_reference_gaps(snap)
    gld = next(g for g in gaps if g["symbol"] == "GLD")
    assert gld["price"] == 205.5


def test_full_exit_matrix_high_divergence_5_9pct_no_longer_clamps():
    """5.9% divergence (904.28 vs 853.90): on pre-fix master this clamped 2->1 shares
    ("landing -0.10% would breach the window floor"). Post-fix the full exit passes."""
    res = _full_exit_result(904.28)
    assert res["rejected"] == []
    t = res["trades"][0]
    assert t["quantity"] == _MU_QTY
    assert t["validation"]["status"] == "passed"


def test_full_exit_matrix_low_divergence_2_8pct_no_longer_lucky():
    """2.8% divergence (877.90 vs 853.90): on pre-fix master this happened to slip
    under the 0.05pp epsilon and pass — not because it was correct, but by luck.
    Post-fix it passes because the basis mismatch no longer exists at all."""
    res = _full_exit_result(877.90)
    assert res["rejected"] == []
    t = res["trades"][0]
    assert t["quantity"] == _MU_QTY
    assert t["validation"]["status"] == "passed"


def test_full_exit_matrix_medium_divergence_3_1pct_no_longer_clamps():
    """3.1% divergence (880.37 vs 853.90): on pre-fix master this clamped again
    (post = -0.0544%, over epsilon). Post-fix the full exit passes."""
    res = _full_exit_result(880.37)
    assert res["rejected"] == []
    t = res["trades"][0]
    assert t["quantity"] == _MU_QTY
    assert t["validation"]["status"] == "passed"


def test_coherence_invariant_current_pct_matches_price_times_held_qty():
    """Permanent regression pin: for every gap row with a held quantity, current_pct
    must agree with held_qty * price / equity * 100 (the property whose violation
    caused this whole bug class) — checked across a spread of FMP/paper divergences.
    Tolerance is 1e-4 (not tighter): `current_pct` is `round(..., 4)`, so up to half
    a unit of that rounding is expected float noise, not a basis mismatch."""
    for fmp_price in (853.90, 877.90, 880.37, 904.28, 800.00):
        gaps, _ctx = _build_reference_gaps(_mu_snapshot(fmp_price))
        for g in gaps:
            if g["held_qty"] > 0:
                implied_pct = g["held_qty"] * g["price"] / _EQUITY * 100.0
                assert abs(g["current_pct"] - implied_pct) < 1e-4, (
                    f"{g['symbol']}: current_pct={g['current_pct']} vs "
                    f"implied={implied_pct} (price basis mismatch)"
                )
