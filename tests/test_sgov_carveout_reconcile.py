"""Regression test for Task B (decision D-B1, session 2026-08-01): `reconcile()`
must stop flagging the sanctioned literal-cash -> SGOV carve-out sweep as the
model having "traded AWAY" from reference.

07-31 fixture (shape reproduced, not exact live numbers): SGOV gap +17.74pp, an
accepted override sheltering the max 15pp residual (required move 2.74pp), and a
69-share SGOV buy @ $100.715 funded entirely from pre-trade literal cash above
the 1.5% buffer — the same sweep `shared/trade_validation.py::validate_trades`
exempts from SGOV's per-name window as a pure cash-sleeve composition swap.
Before the fix, `reconcile`'s `move_pp` had no carve-out awareness and scored the
whole buy notional as a move AWAY from reference (observed: -7.01pp, "model
traded AWAY from reference on this sleeve") — a false alarm on the exact steady
state the carve-out exists to enable, firing on every future sweep while SGOV
sits above reference+band. Fixed (Option 1): the qualifying carve-out notional
(min(buy_notional, pre_trade_cash - buffer), same formula the validator uses) is
excluded from SGOV's move_pp contribution.

Run: PYTHONPATH=src pytest tests/test_sgov_carveout_reconcile.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shared.reference_execution import reconcile  # noqa: E402

CFG = {
    "override_protocol": {
        "max_magnitude_pp": 15.0, "re_risk_min_evidence": 2, "gap_band_pp": 5.0,
    },
    "reference_execution": {
        "tranche_pp_max": 10.0, "enforce": True,
        "enforcement_turnover_max_pct": 20.0, "min_notional_usd": 115.0,
    },
}


def _gaps():
    return [
        {"symbol": "SGOV", "current_pct": 27.74, "reference_pct": 10.0,
         "price": 100.715, "held_qty": 900},
    ]


def _override_decisions():
    # An accepted override sheltering the max 15pp residual for SGOV — required
    # move = max(0, 17.74 - max(15, 5)) = 2.74pp, matching the 07-31 shape.
    return [{"outcome": "accepted", "override": {"sleeve": "SGOV", "magnitude_pp": 15.0}}]


def _ctx(cash_usd=13_511.06):
    return {
        "deployment_gate": "open", "equity_usd": 100_000.0, "cash_usd": cash_usd,
        "date": "2026-07-31", "exempt_holds": [], "literal_cash_target_pct": 1.5,
    }


def test_sgov_carveout_sweep_no_longer_flagged_as_traded_away():
    gaps = _gaps()
    trades = [{"id": "T-1", "symbol": "SGOV", "side": "buy", "quantity": 69}]
    recon = reconcile(gaps, trades, _override_decisions(), CFG, _ctx())
    sgov = recon["sleeves"]["SGOV"]

    assert sgov["gap_pp"] == 17.74
    assert sgov["allowed_residual_pp"] == 15.0
    assert sgov["required_move_total_pp"] == 2.74
    assert sgov["model_move_pp"] == 0.0
    assert not any("traded AWAY" in r for r in sgov["reasons"])
    # The 2.74pp re-risk shortfall (nothing was SOLD to close the residual) is a
    # true, separate statement and may legitimately remain.
    assert sgov["status"] == "non_compliant_flagged"
    assert any("re-risk shortfall" in r for r in sgov["reasons"])


def test_sgov_buy_beyond_carveout_budget_still_scores_negative():
    """A SGOV buy LARGER than the pre-trade-cash carve-out budget must still
    count the excess as a real away-from-reference move — the fix narrows the
    exemption to the sanctioned budget, it does not blanket-exempt SGOV."""
    gaps = _gaps()
    budget = 13_511.06 - 0.015 * 100_000.0   # 12,011.06
    trades = [{"id": "T-1", "symbol": "SGOV", "side": "buy", "quantity": 200}]
    recon = reconcile(gaps, trades, _override_decisions(), CFG, _ctx())
    sgov = recon["sleeves"]["SGOV"]

    excess_notional = 200 * 100.715 - budget
    expected_move = -round(excess_notional / 100_000.0 * 100.0, 2)
    assert sgov["model_move_pp"] == expected_move
    assert sgov["model_move_pp"] < 0
    assert any("traded AWAY" in r for r in sgov["reasons"])


def test_carveout_budget_is_pretrade_cash_only_same_day_sells_excluded():
    """Mirrors the validator's `pre_cash` tracker: a same-day sell of an
    UNRELATED sleeve must not inflate the SGOV exemption budget."""
    gaps = _gaps() + [
        {"symbol": "GLD", "current_pct": 20.0, "reference_pct": 10.0,
         "price": 200.0, "held_qty": 100},
    ]
    trades = [
        {"id": "T-1", "symbol": "GLD", "side": "sell", "quantity": 50},    # +$10,000 proceeds
        {"id": "T-2", "symbol": "SGOV", "side": "buy", "quantity": 150},   # $15,107.25
    ]
    recon = reconcile(gaps, trades, _override_decisions(), CFG, _ctx(cash_usd=13_511.06))
    sgov = recon["sleeves"]["SGOV"]

    budget = 13_511.06 - 0.015 * 100_000.0   # 12,011.06 — NOT +$10,000 from the GLD sell
    buy_notional = 150 * 100.715
    excess = buy_notional - budget
    expected_move = -round(excess / 100_000.0 * 100.0, 2)
    assert sgov["model_move_pp"] == expected_move
