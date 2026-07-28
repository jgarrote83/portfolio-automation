"""Unit tests for blanket autonomous sleeve switching (session 2026-07-27).

A scorecard role's `switch_signal` now auto-advances the EFFECTIVE incumbent via
SleeveSelectionState (mirroring the existing `intl_leader` auto-rotation pattern) —
`sleeve-roles.json`'s `selected` becomes the baseline/pin, not the live authority.
Covers: Change 1 (`_build_sleeve_selection` advance/pin/adoption/legacy-guard),
Change 1b (price universe sources persisted state; a scorecard-build failure keeps
the persisted effective reference), Change 2 (the reference builder + Table-A
bucketing resolve the effective incumbent), Change 3 (the Tier-1 validator's V1
amplifier gate / V1.5 selected-member check resolve the effective incumbent).

Every test below is a PROVE-FAILURE-BEFORE-FIX case: on the pre-2026-07-27 source
(`_build_sleeve_selection` never advances `selected`; `shared/quadrants.py` helpers
take no `overrides` argument; the validator never reads `effective_selected`) each
of these assertions fails or the call raises a TypeError for an unexpected keyword
argument. Run:
    PYTHONPATH=src pytest tests/test_sleeve_auto_switch.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer.handler import _build_reference_gaps  # noqa: E402
from collector.handler import (  # noqa: E402
    _aggregate_by_quadrant,
    _build_price_universe,
    _build_reference_weights,
    _build_sleeve_selection,
    _build_sleeve_switch_records,
    _effective_selected_map,
    _load_risk_limits,
)
from shared.quadrants import LEGACY_EXITS, quadrant_allocation_bucket, roles_config  # noqa: E402
from shared.trade_validation import validate_trades  # noqa: E402

SEL_CFG = {
    "momentum_weights": {"r120": 0.5, "r60": 0.3, "r252": 0.2},
    "expense_penalty_mult": 1.0,
    "min_benchmark_corr": 0.6,
    "hysteresis_lead": 2.0,
    "hysteresis_runs": 10,
}
RW_CFG = _load_risk_limits()
VAL_CFG = {
    "override_protocol": {"max_magnitude_pp": 15.0, "re_risk_min_evidence": 2, "gap_band_pp": 5.0},
    "reference_execution": {"tranche_pp_max": 10.0, "enforce": True,
                            "enforcement_turnover_max_pct": 20.0, "min_notional_usd": 115.0},
    "sleeve_floor_pct_of_core": 0.1,
    "active_quadrant_ceiling_pct_of_core": 90.0,
    "exempt_holds": [],
}


def _role(pool, selected, ers, benchmark=None, pin=False):
    return {
        "role_id": "semis", "selection": "scorecard",
        "quadrants": ["Q1"], "block": "amplifier_us",
        "pool": pool, "selected": selected,
        "benchmark_proxy": benchmark or selected,
        "expense_ratio": ers,
        "pin": pin,
    }


def _metrics(r120, r60, r252, corr):
    return {"r120": r120, "r60": r60, "r252": r252, "corr_bench_120d": corr}


# --- reference-weights fixtures (mirror tests/test_reference_weights.py) ----------

def _axes(growth, inflation, growth_conf="high"):
    return {"direction": growth, "confidence": growth_conf}, {"direction": inflation}


def _gate(status, stance="neutral"):
    return {"status": status, "derived_from": {"policy_stance": stance}}


def _paper(weights, equity=100_000.0, cash_pct=2.0):
    positions = [
        {"ticker": t, "qty": 1.0, "market_value": equity * w / 100.0}
        for t, w in weights.items()
    ]
    return {"available": True, "equity": equity, "cash": equity * cash_pct / 100.0,
            "positions": positions}


def _rot(dxy_tag="neutral"):
    return {"dxy_tailwind_for_intl": dxy_tag}


# --- trade-validator fixtures (mirror tests/test_trade_validation.py) -------------

def _vctx(**kw):
    base = {"deployment_gate": "closed", "equity_usd": 100_000.0, "cash_usd": 20_000.0,
            "date": "2026-07-27", "exempt_holds": []}
    base.update(kw)
    return base


def _vgap(sym, cur, ref, price=100.0, held=None):
    if held is None:
        held = float(int(cur / 100 * 100_000 / price))
    return {"symbol": sym, "current_pct": cur, "reference_pct": ref,
            "price": price, "held_qty": held}


def _vt(sym, side, qty, **kw):
    return {"id": f"T-20260727-{sym}-{side}", "symbol": sym, "side": side,
            "quantity": qty, **kw}


def _vstatuses(res):
    return {t["symbol"]: t["validation"]["status"] for t in res["trades"]}


# --- 1. switch_signal advances the effective incumbent ----------------------------

def test_switch_signal_advances_effective_incumbent():
    """A challenger already streaking at runs_thr+1 (persisted from before this
    feature existed — old code tracked streak/challenger but never advanced
    `selected`) that still leads this run auto-advances immediately: this is the
    "ships hot on first run" interaction the 2026-07-27 PR calls out."""
    roles = [_role(["A", "B"], "A", {"A": 0.1, "B": 0.1})]
    state = {"semis": {"challenger": "B", "streak": 11, "selected": "A", "config_selected": "A"}}
    metrics = {"A": _metrics(10, 10, 10, 1.0), "B": _metrics(15, 15, 15, 0.9)}  # B still leads by 5

    block, new_state = _build_sleeve_selection(roles, metrics, state, SEL_CFG)

    assert new_state["semis"]["selected"] == "B"
    assert new_state["semis"]["streak"] == 0
    r = block["roles"][0]
    assert r["auto_switched"] is True
    assert r["effective_selected"] == "B"


# --- 2. a pinned role never auto-switches -----------------------------------------

def test_pinned_role_never_switches():
    roles = [_role(["A", "B"], "A", {"A": 0.1, "B": 0.1}, pin=True)]
    state = {"semis": {"challenger": "B", "streak": 11, "selected": "A", "config_selected": "A"}}
    metrics = {"A": _metrics(10, 10, 10, 1.0), "B": _metrics(15, 15, 15, 0.9)}

    block, new_state = _build_sleeve_selection(roles, metrics, state, SEL_CFG)

    assert new_state["semis"]["selected"] == "A"
    r = block["roles"][0]
    assert r["auto_switched"] is False
    assert r["pinned"] is True


# --- 3. an auto-switch logs the existing sleeve_switch OverrideHistory row --------

def test_effective_switch_logs_sleeve_switch_row():
    roles = [_role(["A", "B"], "A", {"A": 0.1, "B": 0.1})]
    state = {"semis": {"challenger": "B", "streak": 11, "selected": "A", "config_selected": "A"}}
    metrics = {"A": _metrics(10, 10, 10, 1.0), "B": _metrics(15, 15, 15, 0.9)}

    _, new_state = _build_sleeve_selection(roles, metrics, state, SEL_CFG)
    recs = _build_sleeve_switch_records(state, new_state, None, None, "2026-07-27")

    assert len(recs) == 1
    assert recs[0]["layer"] == "sleeve_switch"
    assert recs[0]["incumbent"] == "A"
    assert recs[0]["new_member"] == "B"


# --- 4. the reference targets the effective incumbent -----------------------------

def test_reference_targets_effective_incumbent():
    g, i = _axes("falling", "rising")  # Q3 — healthcare_def concentrates
    paper = _paper({"XLV": 3, "GLD": 5, "SGOV": 10})

    rw_before = _build_reference_weights(
        paper, g, i, _gate("closed", "neutral"), _rot(), {}, {}, {}, RW_CFG,
    )
    xlv_target_before = rw_before["target_weights_pct"]["XLV"]
    assert xlv_target_before > 0.0

    eff = {"healthcare_def": "IHE"}
    rw = _build_reference_weights(
        paper, g, i, _gate("closed", "neutral"), _rot(), {}, {}, {}, RW_CFG,
        effective_selected=eff,
    )
    assert rw["target_weights_pct"].get("XLV", 0.0) == 0.0
    assert abs(rw["target_weights_pct"]["IHE"] - xlv_target_before) < 0.01

    # XLV, deselected but still HELD, must still land in the gap universe (reference
    # 0.0) so the wind-down sell validates — gap universe membership is pool-based
    # (CORE_ROSTER), unaffected by the selection override.
    snapshot = {
        "reference_weights": rw,
        "regime_gate": {"status": "closed"},
        "paper_account": {
            "equity": 100_000.0, "cash": 5_000.0,
            "positions": [{"ticker": "XLV", "qty": 30, "market_value": 3_000.0,
                          "current_price": 100.0}],
        },
        "prices": {"XLV": {"c": 100.0}},
    }
    gaps, _ = _build_reference_gaps(snapshot)
    xlv_gap = next((row for row in gaps if row["symbol"] == "XLV"), None)
    assert xlv_gap is not None
    assert xlv_gap["reference_pct"] == 0.0
    assert xlv_gap["current_pct"] > 0.0


# --- 5. the Tier-1 validator resolves the effective incumbent ---------------------

def test_validator_passes_effective_selected_buy_rejects_old():
    eff = {"healthcare_def": "IHE"}
    gaps = [
        _vgap("IHE", 1.0, 10.0, price=100.0),
        _vgap("XLV", 5.0, 0.0, price=100.0, held=50),
    ]
    res = validate_trades(
        gaps, [_vt("IHE", "buy", 10), _vt("XLV", "buy", 5)], [], VAL_CFG,
        _vctx(deployment_gate="open", effective_selected=eff),
    )
    assert {r["symbol"] for r in res["rejected"]} == {"XLV"}
    assert _vstatuses(res).get("IHE") == "passed"

    # SELL of the deselected old incumbent gets the sell-to-zero floor bypass
    # (D-G1: committed-switch parity).
    sell_res = validate_trades(
        gaps, [_vt("XLV", "sell", 50)], [], VAL_CFG, _vctx(effective_selected=eff),
    )
    xlv = sell_res["trades"][0]
    assert xlv["validation"]["status"] == "passed"
    assert xlv["quantity"] == 50

    # Control: intl_leader_pick's independent auto-rotation exception is unaffected.
    gaps2 = [_vgap("EWJ", 1.0, 3.0, price=100.0), _vgap("IEMG", 1.0, 3.0, price=100.0)]
    res2 = validate_trades(
        gaps2, [_vt("EWJ", "buy", 10), _vt("IEMG", "buy", 10)], [], VAL_CFG,
        _vctx(deployment_gate="open", intl_leader_pick="EWJ"),
    )
    assert {r["symbol"] for r in res2["rejected"]} == {"IEMG"}
    assert _vstatuses(res2).get("EWJ") == "passed"


# --- 6. flipping to pin reverts effective -> config and logs the revert ----------

def test_pin_reverts_and_logs():
    prev_state = {"semis": {"challenger": None, "streak": 0, "selected": "B", "config_selected": "A"}}
    roles = [_role(["A", "B"], "A", {"A": 0.1, "B": 0.1}, pin=True)]
    metrics = {"A": _metrics(10, 10, 10, 1.0), "B": _metrics(9, 9, 9, 0.9)}

    block, new_state = _build_sleeve_selection(roles, metrics, prev_state, SEL_CFG)

    assert new_state["semis"]["selected"] == "A"   # reverted to config
    r = block["roles"][0]
    assert r["pinned"] is True
    assert r["effective_selected"] == "A"

    recs = _build_sleeve_switch_records(prev_state, new_state, None, None, "2026-07-27")
    assert len(recs) == 1
    assert recs[0]["incumbent"] == "B" and recs[0]["new_member"] == "A"


# --- 7. D-G2: an unpinned config edit is adopted, never silently shadowed --------

def test_config_commit_adopted_when_unpinned():
    # A prior auto-switch to B is persisted; config_selected still says "A" (the
    # value the auto-switch itself was computed against).
    prev_state = {"semis": {"challenger": None, "streak": 0, "selected": "B", "config_selected": "A"}}
    # A human now commits a DIFFERENT config value, "C".
    roles = [_role(["A", "B", "C"], "C", {"A": 0.1, "B": 0.1, "C": 0.1})]
    metrics = {"A": _metrics(5, 5, 5, 0.9), "B": _metrics(5, 5, 5, 0.9), "C": _metrics(10, 10, 10, 1.0)}

    block, new_state = _build_sleeve_selection(roles, metrics, prev_state, SEL_CFG)

    assert new_state["semis"]["selected"] == "C"          # adopted the fresh config, not "B"
    assert new_state["semis"]["config_selected"] == "C"
    recs = _build_sleeve_switch_records(prev_state, new_state, None, None, "2026-07-27")
    assert len(recs) == 1
    assert recs[0]["incumbent"] == "B" and recs[0]["new_member"] == "C"


# --- 8. the price universe fetches effective incumbents ---------------------------

def test_price_universe_includes_effective_incumbents():
    eff = {"healthcare_def": "IHE", "semis": "SOXX"}
    universe = _build_price_universe(tickers=["SPY"], flex_candidate_tickers=[],
                                     effective_selected=eff)
    assert "IHE" in universe
    assert "SOXX" in universe


# --- 9. a LEGACY_EXITS challenger is never adopted --------------------------------

def test_legacy_exit_never_adopted_as_incumbent():
    assert "XSD" in LEGACY_EXITS   # sanity: XSD is both a semis pool member and legacy
    roles = [_role(["SMH", "XSD"], "SMH", {"SMH": 0.1, "XSD": 0.1})]
    state = {"semis": {"challenger": "XSD", "streak": 11, "selected": "SMH", "config_selected": "SMH"}}
    metrics = {"SMH": _metrics(10, 10, 10, 1.0), "XSD": _metrics(20, 20, 20, 0.9)}

    block, new_state = _build_sleeve_selection(roles, metrics, state, SEL_CFG)

    assert new_state["semis"]["selected"] == "SMH"
    r = block["roles"][0]
    assert r["auto_switched"] is False


# --- 10. the amplifier gate leak is closed ----------------------------------------

def test_amplifier_gate_leak_closed():
    """Pre-fix, SOXX (a non-amplifier-block config member) would not be recognized
    as an amplifier by the frozen module-level set, so a gate-closed SOXX buy would
    pass straight through V1 — a genuine Tier-1 gate leak once SOXX is the effective
    incumbent of an amplifier_us role."""
    eff = {"semis": "SOXX"}
    gaps = [_vgap("SOXX", 1.0, 10.0, price=100.0)]
    res = validate_trades(
        gaps, [_vt("SOXX", "buy", 10)], [], VAL_CFG,
        _vctx(deployment_gate="closed", effective_selected=eff),
    )
    assert len(res["rejected"]) == 1
    assert any("gate" in s for s in res["rejected"][0]["validation"]["reasons"])


# --- 11. Table A buckets the effective incumbent ----------------------------------

def test_table_a_buckets_effective_incumbent():
    eff = {"healthcare_def": "IHE"}
    assert quadrant_allocation_bucket("IHE") == "unmapped"      # baseline: no override
    assert quadrant_allocation_bucket("IHE", eff) == "Q3"       # effective -> bucketed correctly

    by_q = _aggregate_by_quadrant({"IHE": 10.888}, 0.0, eff)
    assert by_q["Q3"] == 10.89   # _aggregate_by_quadrant rounds to 2dp


# --- 12. a scorecard build failure keeps targeting the persisted effective ref ----

def test_scorecard_failure_keeps_effective_reference():
    """Mirrors the collector's failure-isolation design: `effective_selected` is
    derived from the PERSISTED state alone (no metrics, no `_build_sleeve_selection`
    call — simulating a caught FMP exception), and the reference still targets the
    already-switched incumbent rather than whipsawing back to config for the day."""
    roles = roles_config()   # the real config: healthcare_def.selected == "XLV"
    persisted = {"healthcare_def": {"challenger": None, "streak": 0,
                                    "selected": "IHE", "config_selected": "XLV"}}

    eff = _effective_selected_map(roles, persisted)
    assert eff["healthcare_def"] == "IHE"

    g, i = _axes("falling", "rising")  # Q3
    paper = _paper({"XLV": 3, "GLD": 5, "SGOV": 10})
    rw = _build_reference_weights(
        paper, g, i, _gate("closed", "neutral"), _rot(), {}, {}, {}, RW_CFG,
        effective_selected=eff,
    )
    assert rw["target_weights_pct"].get("IHE", 0.0) > 0.0
    assert rw["target_weights_pct"].get("XLV", 0.0) == 0.0
