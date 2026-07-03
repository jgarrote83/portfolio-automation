"""Unit tests for the band-enforcement reconciler (Finding 2 — silent-hold gap).

Covers the three locked decisions: D1 (an override caps the RESIDUAL it shelters, never
the move), D2 (tranche pace is CONFIRMING, first-class), D3 (de-risk-only synthesis of
shortfalls; re-risk is flagged, never synthesized — spec §6 asymmetry), plus the trade
constraints (integer shares, min notional, sells-before-buys, cash-after-sells,
turnover cap, gate, EXEMPT_HOLDS) and replay fixtures for the 2026-06-30 zero-trades
pathology and the 2026-07-03 tranche rotation. Run:
    PYTHONPATH=src pytest tests/test_reference_execution.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer.handler import (  # noqa: E402
    _build_reference_gaps,
    _stamp_enforced_decisions,
)
from shared.reference_execution import (  # noqa: E402
    is_de_risk_move,
    reconcile,
)

CFG = {
    "override_protocol": {
        "max_magnitude_pp": 15.0, "re_risk_min_evidence": 2, "gap_band_pp": 5.0,
    },
    "reference_execution": {
        "tranche_pp_max": 10.0, "enforce": True,
        "enforcement_turnover_max_pct": 20.0, "min_notional_usd": 115.0,
    },
}


def _ctx(**kw):
    base = {
        "deployment_gate": "closed",   # the live defensive-regime case
        "equity_usd": 100_000.0,
        "cash_usd": 5_000.0,
        "date": "2026-07-03",
        "exempt_holds": ["AMZN", "GOOGL"],
    }
    base.update(kw)
    return base


def _gap(sym, cur, ref, price=100.0):
    return {"symbol": sym, "current_pct": cur, "reference_pct": ref, "price": price}


def _dec(sleeve, outcome="accepted", magnitude=10.0, direction="de_risk"):
    return {
        "outcome": outcome, "reasons": [],
        "override": {"sleeve": sleeve, "magnitude_pp": magnitude, "direction": direction},
    }


def _trade(sym, side, qty):
    return {"symbol": sym, "side": side, "quantity": qty}


# --- de-risk classification (reuses quadrants.py) -----------------------------

def test_is_de_risk_move_classification():
    assert is_de_risk_move("sell", "SPY")        # selling amplifier = de-risk
    assert is_de_risk_move("sell", "IDMO")       # intl amplifier too
    assert is_de_risk_move("buy", "GLD")         # buying damper = de-risk
    assert is_de_risk_move("buy", "SGOV")        # SGOV counts as ballast
    assert not is_de_risk_move("sell", "TLT")    # selling damper = re-risk
    assert not is_de_risk_move("buy", "QQQ")     # risk-on buy = re-risk
    assert not is_de_risk_move("buy", "MU")      # off-roster buy = re-risk


# --- D2: confirming at tranche pace -------------------------------------------

def test_confirming_trade_no_synthesis():
    """A trade covering the tranche is CONFIRMING — no override, no synthesis."""
    gaps = [_gap("SPY", 17.0, 2.0)]              # gap +15 -> required today 10pp
    trades = [_trade("SPY", "sell", 100)]        # 100 x $100 / $100K = 10pp
    r = reconcile(gaps, trades, [], CFG, _ctx())
    assert r["sleeves"]["SPY"]["status"] == "confirming"
    assert r["enforced_trades"] == []


def test_partial_pace_shortfall_topped_up():
    """Half the tranche traded -> the de-risk remainder is synthesized."""
    gaps = [_gap("SPY", 17.0, 2.0)]              # required today 10pp
    trades = [_trade("SPY", "sell", 50)]         # 5pp only
    r = reconcile(gaps, trades, [], CFG, _ctx())
    e = r["sleeves"]["SPY"]
    assert e["status"] == "enforced"
    assert e["enforced_trade"]["quantity"] == 50   # the missing 5pp
    assert e["enforced_trade"]["source"] == "band_enforcement"


def test_in_band_sleeve_not_reported():
    r = reconcile([_gap("XLP", 6.0, 2.0)], [], [], CFG, _ctx())   # gap 4 < band 5
    assert r["sleeves"] == {}
    assert r["enforced_trades"] == []


# --- D1: overrides shelter the residual, capped at max_magnitude_pp -----------

def test_accepted_override_covers_gap_within_shelter():
    """gap 14 <= sheltered 15 -> override_covered, nothing to trade."""
    gaps = [_gap("QQQ", 16.0, 2.0)]
    decs = [_dec("QQQ", magnitude=15.0)]
    r = reconcile(gaps, [], decs, CFG, _ctx())
    assert r["sleeves"]["QQQ"]["status"] == "override_covered"
    assert r["enforced_trades"] == []


def test_override_shelters_at_most_max_magnitude():
    """D1: gap 18 with a full 15pp accepted override still owes 3pp — synthesized."""
    gaps = [_gap("SPY", 20.0, 2.0)]
    decs = [_dec("SPY", magnitude=15.0)]
    r = reconcile(gaps, [], decs, CFG, _ctx())
    e = r["sleeves"]["SPY"]
    assert e["status"] == "enforced"
    assert e["allowed_residual_pp"] == 15.0
    assert e["required_move_today_pp"] == 3.0
    assert e["enforced_trade"]["quantity"] == 30   # 3pp of $100K at $100


def test_rejected_override_shelters_nothing():
    """A rejected record authorizes nothing — full tranche synthesized."""
    gaps = [_gap("SPY", 17.0, 2.0)]
    decs = [_dec("SPY", outcome="rejected", magnitude=15.0)]
    r = reconcile(gaps, [], decs, CFG, _ctx())
    e = r["sleeves"]["SPY"]
    assert e["status"] == "enforced"
    assert e["allowed_residual_pp"] == 0.0
    assert e["enforced_trade"]["quantity"] == 100   # full 10pp tranche


def test_no_record_no_trade_synthesized():
    gaps = [_gap("SPY", 17.0, 2.0)]
    r = reconcile(gaps, [], [], CFG, _ctx())
    assert r["sleeves"]["SPY"]["status"] == "enforced"


def test_override_residual_is_per_sleeve():
    """A QQQ override does not shelter SPY (V1_1 per-sleeve semantics)."""
    gaps = [_gap("SPY", 17.0, 2.0), _gap("QQQ", 14.0, 2.0)]
    decs = [_dec("QQQ", magnitude=15.0)]
    r = reconcile(gaps, [], decs, CFG, _ctx())
    assert r["sleeves"]["SPY"]["status"] == "enforced"
    assert r["sleeves"]["QQQ"]["status"] == "override_covered"


# --- D3: the asymmetry — re-risk is never synthesized --------------------------

def test_re_risk_shortfall_flagged_never_synthesized():
    """Overweight damper (sell TLT) and underweight amplifier (buy QQQ) are both
    re-risk corrections -> flagged, zero synthesis (spec §6)."""
    gaps = [_gap("TLT", 20.0, 5.0), _gap("QQQ", 0.5, 12.0)]
    r = reconcile(gaps, [], [], CFG, _ctx())
    assert r["sleeves"]["TLT"]["status"] == "non_compliant_flagged"
    assert r["sleeves"]["QQQ"]["status"] == "non_compliant_flagged"
    assert r["enforced_trades"] == []
    assert any("re-risk" in s for s in r["sleeves"]["TLT"]["reasons"])


def test_exempt_hold_never_force_sold():
    gaps = [_gap("AMZN", 20.0, 5.0)]   # overweight amplifier — but exempt
    r = reconcile(gaps, [], [], CFG, _ctx())
    assert r["sleeves"]["AMZN"]["status"] == "non_compliant_flagged"
    assert any("exempt" in s for s in r["sleeves"]["AMZN"]["reasons"])
    assert r["enforced_trades"] == []


def test_gate_closed_defensive_buy_still_synthesized():
    """The gate forbids risk-on buys, not ballast — a GLD top-up passes while closed."""
    gaps = [_gap("GLD", 2.0, 20.0)]    # gap -18 -> required today 10pp
    r = reconcile(gaps, [], [], CFG, _ctx(deployment_gate="closed", cash_usd=50_000.0))
    e = r["sleeves"]["GLD"]
    assert e["status"] == "enforced"
    assert e["enforced_trade"]["side"] == "buy"


def test_enforce_false_only_flags():
    cfg = {**CFG, "reference_execution": {**CFG["reference_execution"], "enforce": False}}
    r = reconcile([_gap("SPY", 17.0, 2.0)], [], [], cfg, _ctx())
    assert r["sleeves"]["SPY"]["status"] == "non_compliant_flagged"
    assert any("disabled" in s for s in r["sleeves"]["SPY"]["reasons"])
    assert r["enforced_trades"] == []


# --- trade constraints ---------------------------------------------------------

def test_tranche_cap_bounds_synthesis():
    """gap 30 owes 25pp total but only 10pp (the tranche) today."""
    gaps = [_gap("GLD", 2.0, 32.0)]
    r = reconcile(gaps, [], [], CFG, _ctx(cash_usd=50_000.0))
    e = r["sleeves"]["GLD"]
    assert e["required_move_total_pp"] == 25.0
    assert e["required_move_today_pp"] == 10.0
    assert e["enforced_trade"]["quantity"] == 100   # 10pp, not 25pp


def test_turnover_cap_across_sleeves():
    """Three 10pp enforcements would be $30K; the $20K (20%) session cap funds two,
    the third floors to zero shares and is flagged (caps exhausted)."""
    gaps = [
        _gap("SPY", 17.0, 2.0), _gap("XSD", 17.0, 2.0), _gap("INTC", 17.0, 2.0),
    ]
    r = reconcile(gaps, [], [], CFG, _ctx())
    statuses = [r["sleeves"][s]["status"] for s in ("SPY", "XSD", "INTC")]
    assert statuses.count("enforced") == 2
    assert statuses.count("non_compliant_flagged") == 1
    assert r["enforcement_notional_usd"] <= 20_000.0


def test_buy_capped_by_available_cash():
    """No sells, $500 cash -> the GLD top-up shrinks to 5 affordable shares."""
    gaps = [_gap("GLD", 2.0, 20.0)]
    r = reconcile(gaps, [], [], CFG, _ctx(cash_usd=500.0))
    assert r["sleeves"]["GLD"]["enforced_trade"]["quantity"] == 5


def test_min_notional_skip():
    """A shortfall worth less than min_notional_usd is not tradeable — flagged."""
    gaps = [_gap("SPY", 8.0, 2.0, price=50.0)]   # gap 6 -> required 1pp = $100 < $115
    r = reconcile(gaps, [], [], CFG, _ctx(equity_usd=10_000.0))
    assert r["sleeves"]["SPY"]["status"] == "non_compliant_flagged"
    assert r["enforced_trades"] == []


def test_integer_share_floor_skip():
    """A shortfall smaller than one share of a high-priced name floors to zero."""
    gaps = [_gap("SPY", 8.0, 2.0, price=5_000.0)]   # required 1pp = $1000 < 1 share
    r = reconcile(gaps, [], [], CFG, _ctx())
    assert r["sleeves"]["SPY"]["status"] == "non_compliant_flagged"


def test_missing_price_flagged():
    gaps = [_gap("SPY", 17.0, 2.0, price=None)]
    r = reconcile(gaps, [], [], CFG, _ctx())
    assert r["sleeves"]["SPY"]["status"] == "non_compliant_flagged"
    assert any("price" in s for s in r["sleeves"]["SPY"]["reasons"])


def test_enforced_sells_come_before_buys():
    gaps = [_gap("GLD", 2.0, 14.0), _gap("SPY", 17.0, 2.0)]
    r = reconcile(gaps, [], [], CFG, _ctx(cash_usd=0.0))
    sides = [t["side"] for t in r["enforced_trades"]]
    assert sides == ["sell", "buy"]
    # the buy is funded by the synthesized sell's proceeds despite zero cash
    assert r["sleeves"]["GLD"]["enforced_trade"]["quantity"] > 0


def test_unavailable_account_returns_empty():
    r = reconcile([_gap("SPY", 17.0, 2.0)], [], [], CFG, _ctx(equity_usd=0.0))
    assert r["sleeves"] == {} and r["enforced_trades"] == []


# --- replay fixtures -----------------------------------------------------------

def test_replay_2026_06_30_pathology_now_trades():
    """The motivating failure: correct defensive call, ZERO trades, 'appropriately
    positioned'. Under Finding 2 the de-risk gaps are traded through: SPY/QQQ trims
    and a GLD top-up are synthesized, sells first, inside the turnover cap."""
    gaps = [
        _gap("SPY", 17.25, 0.5, price=550.0),
        _gap("QQQ", 13.91, 0.5, price=480.0),
        _gap("GLD", 6.0, 20.0, price=205.0),
    ]
    r = reconcile(gaps, [], [], CFG, _ctx())   # no trades, no overrides
    assert [t["side"] for t in r["enforced_trades"]] == ["sell", "sell", "buy"]
    assert {t["symbol"] for t in r["enforced_trades"]} == {"SPY", "QQQ", "GLD"}
    assert all(t["source"] == "band_enforcement" for t in r["enforced_trades"])
    assert all(isinstance(t["quantity"], int) and t["quantity"] >= 1
               for t in r["enforced_trades"])
    assert r["summary"]["enforced"] == 3
    assert r["enforcement_notional_usd"] <= 20_000.0   # session turnover cap


def test_replay_2026_07_03_rotation_is_confirming():
    """The legitimate staged rotation: ~30pp GLD/TLT gaps and a 17pp SPY trim, traded
    at tranche pace by the model — CONFIRMING everywhere, zero synthesis (D2 makes
    partial progress first-class by rule, not by the model's grace)."""
    gaps = [
        _gap("SPY", 17.5, 0.5, price=550.0),
        _gap("GLD", 3.4, 33.0, price=205.0),
        _gap("TLT", 2.0, 30.0, price=90.0),
    ]
    trades = [
        _trade("SPY", "sell", 19),    # $10,450 = 10.45pp >= 10pp tranche
        _trade("GLD", "buy", 49),     # $10,045 = 10.05pp
        _trade("TLT", "buy", 112),    # $10,080 = 10.08pp
    ]
    r = reconcile(gaps, trades, [], CFG, _ctx())
    assert all(e["status"] == "confirming" for e in r["sleeves"].values())
    assert r["enforced_trades"] == []
    assert r["summary"]["confirming"] == 3


# --- analyzer-side input/output plumbing ----------------------------------------

def test_build_reference_gaps_from_snapshot():
    snap = {
        "reference_weights": {"target_weights_pct": {"SPY": 0.5, "GLD": 30.0}},
        "paper_account": {
            "equity": 100_000.0, "cash": 5_000.0,
            "positions": [
                {"ticker": "SPY", "market_value": 17_000.0, "current_price": 550.0},
                {"ticker": "MU", "market_value": 3_000.0, "current_price": 120.0},
            ],
        },
        "prices": {"SPY": {"c": 555.0}, "GLD": {"c": 205.0}},
        "regime_gate": {"status": "closed"},
    }
    gaps, ctx = _build_reference_gaps(snap)
    by_sym = {g["symbol"]: g for g in gaps}
    assert set(by_sym) == {"SPY", "GLD"}          # MU is off-roster — excluded
    assert by_sym["SPY"]["current_pct"] == 17.0
    assert by_sym["SPY"]["price"] == 555.0        # snapshot price wins
    assert by_sym["GLD"]["current_pct"] == 0.0    # unheld target still gets a row
    assert ctx["deployment_gate"] == "closed"
    assert ctx["equity_usd"] == 100_000.0 and ctx["cash_usd"] == 5_000.0


def test_build_reference_gaps_unavailable_reference():
    assert _build_reference_gaps({}) == ([], {})
    assert _build_reference_gaps(
        {"reference_weights": {"target_weights_pct": {"SPY": 1.0}},
         "paper_account": {"equity": 0}}
    ) == ([], {})


def test_stamp_enforced_decisions():
    """A rejected record for an enforced sleeve is stamped; a record-less enforcement
    appends a synthetic decision (both feed OverrideHistory for Phase 5)."""
    decisions = [
        {"outcome": "rejected", "reasons": ["no evidence"],
         "override": {"sleeve": "SPY", "magnitude_pp": 20.0}},
    ]
    recon = {"sleeves": {
        "SPY": {"status": "enforced", "required_move_today_pp": 10.0,
                "enforced_trade": {"id": "T-20260703-E01"}},
        "GLD": {"status": "enforced", "required_move_today_pp": 9.0,
                "enforced_trade": {"id": "T-20260703-E02"}},
        "TLT": {"status": "confirming"},
    }}
    _stamp_enforced_decisions(decisions, recon)
    assert decisions[0]["enforced"] is True
    synthetic = [d for d in decisions if d.get("outcome") == "enforced"]
    assert len(synthetic) == 1
    assert synthetic[0]["override"]["sleeve"] == "GLD"
    assert synthetic[0]["enforced"] is True
