"""De-risk classifier vs the LIVE auto-switched incumbent map (session 2026-09-12).

The defect: `shared/reference_execution.py` resolved the Amplifier/Damper blocks
against the FROZEN module-level sets built from `sleeve-roles.json`'s config
`selected`, and accepted no `overrides` parameter. Blanket sleeve auto-switch
(2026-07-27) made that config value a BASELINE, not the live authority — so after
`semis` auto-switched SMH->SOXX, `is_de_risk_move("sell", "SOXX")` read False, the
required sell of the book's largest amplifier was classified a re-risk shortfall,
and re-risk shortfalls are NEVER synthesized (spec §6 asymmetry). SOXX sat ~20% of
equity against a 10.164% reference for three consecutive sessions (required move
3.00 / 7.89 / 8.14pp) with enforcement reporting and discarding the obligation each
time. The identical hazard was caught one module over in the SAME 2026-07-27 PR
(`trade_validation`'s V1 amplifier gate resolves `amplifier_set` per call);
`reference_execution.py` was simply absent from the documented consumer inventory.

Run: PYTHONPATH=src pytest tests/test_derisk_classifier.py
"""
import pytest

from analyzer import handler as ah
from collector import handler as ch
from shared.overrides import validate_overrides
from shared.quadrants import AMPLIFIER_US, amplifier_block_pool, defensive_set
from shared.reference_execution import (
    advance_settling_window,
    classification_conflict,
    derive_override_direction,
    is_de_risk_move,
    reconcile,
    resolve_settling_tranche_cap,
)

# The live effective map on 2026-09-11 (SleeveSelectionState, published by the
# collector as the snapshot's top-level `effective_selected`).
EFF = {"semis": "SOXX", "healthcare_def": "IHE"}

CFG = {
    "override_protocol": {
        "gap_band_pp": 5.0, "relative_band_frac": 0.5,
        "max_magnitude_pp": 15.0, "max_magnitude_rel_frac": 1.0,
        "re_risk_min_evidence": 2,
    },
    "reference_execution": {
        "tranche_pp_max": 10.0, "enforce": True,
        "enforcement_turnover_max_pct": 20.0, "min_notional_usd": 115.0,
    },
    "single_sleeve_cap_pct_of_equity": 12.0,
    "sleeve_floor_pct_of_core": 0.1,
}

# The reconstructed 2026-09-11 SOXX row.
EQUITY = 99_452.80
SOXX_PX = 275.0
SOXX_GAP = {"symbol": "SOXX", "current_pct": 20.14, "reference_pct": 10.164,
            "price": SOXX_PX, "held_qty": 72.0, "off_roster": False}


def _ctx(**kw):
    base = {"deployment_gate": "closed", "equity_usd": EQUITY, "cash_usd": 1500.0,
            "date": "2026-09-11", "exempt_holds": (), "effective_selected": EFF}
    base.update(kw)
    return base


# --- A1: the classifiers resolve through the live incumbent map --------------

def test_effective_amplifier_sell_classifies_de_risk():
    """THE BUG. SOXX is `semis`' live incumbent; selling it is de-risk."""
    assert is_de_risk_move("sell", "SOXX", EFF) is True


def test_effective_damper_buy_classifies_de_risk():
    """The same defect on the defensive side (healthcare_def XLV->IHE)."""
    assert is_de_risk_move("buy", "IHE", EFF) is True
    assert "IHE" in defensive_set(EFF)
    assert "XLV" not in defensive_set(EFF)   # deselected: no longer THE damper


def test_deselected_incumbent_sell_still_classifies_de_risk():
    """Guards the regression the naive fix introduces.

    Swapping the frozen tuples for `amplifier_set(overrides)` ALONE makes SOXX
    classifiable but stops classifying the DESELECTED SMH — whose sanctioned end
    state is a full exit to zero (auto-switch decision D-G1), reached through
    this very synthesis path. That would have traded one permanently-stuck sleeve
    for another. The sell side therefore keys on amplifier-block POOL membership,
    which no selection can change."""
    assert is_de_risk_move("sell", "SMH", EFF) is True
    assert {"SMH", "SOXX"} <= amplifier_block_pool()


def test_derive_override_direction_classifies_effective_incumbents():
    """Task E1's deterministic cross-check silently stopped running for exactly
    the sleeves the auto-switch created: `derive_override_direction` returned
    None for SOXX/IHE, and `shared/overrides.py` then falls back to whatever the
    model DECLARED — no cross-check at all on the two live incumbents."""
    assert derive_override_direction("SOXX", +3.0, EFF) == "re_risk"
    assert derive_override_direction("SOXX", -3.0, EFF) == "de_risk"
    assert derive_override_direction("IHE", +3.0, EFF) == "de_risk"
    assert derive_override_direction("IHE", -3.0, EFF) == "re_risk"


def test_validate_overrides_derives_direction_for_effective_incumbent():
    """End-to-end through the Tier-2 path the analyzer actually calls: a model
    that mislabels a SOXX overweight `de_risk` (the cheap bar) must be corrected
    to the derived `re_risk` (the 2-evidence bar) instead of standing unchecked."""
    ov = {
        "sleeve": "SOXX", "direction": "de_risk",
        "premise_challenged": "growth_axis", "magnitude_pp": 4.0,
        "evidence": ["one item"], "falsifier": "growth rising 2+ runs",
        "falsifier_date": "2026-10-01", "clean_data_only": True,
    }
    gaps = [SOXX_GAP]
    out = validate_overrides([ov], CFG["override_protocol"], gaps, EFF)
    dec = out["decisions"][0]
    assert dec["override"]["direction"] == "re_risk"          # derived, not declared
    assert dec["override"]["declared_direction"] == "de_risk"  # model's claim kept
    assert dec["outcome"] == "downsized"   # 1 evidence item < re_risk_min_evidence 2


def test_zero_override_path_unchanged_for_every_selected_name():
    """Verification #3: the config-`selected` incumbents behave exactly as before
    when no map is supplied."""
    for sym in AMPLIFIER_US:
        assert is_de_risk_move("sell", sym) is True
        assert is_de_risk_move("sell", sym, {}) is True
    for sym in ("GLD", "TLT", "XLP", "XLV", "SGOV"):
        assert is_de_risk_move("buy", sym) is True
        assert is_de_risk_move("buy", sym, {}) is True
    # ...and selling a damper stays re-risk (the D3 asymmetry, unchanged).
    assert is_de_risk_move("sell", "GLD", EFF) is False


# --- A1 end-to-end: the live-conditions enforcement probe --------------------

def test_reconcile_synthesizes_the_soxx_sell_under_live_conditions():
    """The 2026-09-11 book: SOXX 20.14% vs a 10.164% reference, equity
    $99,452.80. Pre-fix this emitted `non_compliant_flagged` with a "re-risk
    shortfall ... never synthesized" reason and NO trade, three sessions
    running."""
    out = reconcile([SOXX_GAP], [], [], CFG, _ctx())
    entry = out["sleeves"]["SOXX"]
    assert entry["status"] == "enforced"
    assert out["enforced_trades"], "no trade synthesized"
    t = out["enforced_trades"][0]
    assert (t["side"], t["symbol"], t["source"]) == ("sell", "SOXX", "band_enforcement")
    assert t["quantity"] > 0


def test_defensive_side_synthesis_is_driven_by_the_effective_map():
    """The buy side keys on the INCUMBENT set (not the pool), so it isolates the
    map-threading itself: an underweight IHE — `healthcare_def`'s live incumbent
    — is only synthesizable once ctx carries `effective_selected`."""
    row = {"symbol": "IHE", "current_pct": 0.1, "reference_pct": 8.0,
           "price": 60.0, "held_qty": 1.0, "off_roster": False}
    ctx = _ctx(deployment_gate="closed", cash_usd=20_000.0)

    without = reconcile([row], [], [], CFG, {**ctx, "effective_selected": None})
    assert without["sleeves"]["IHE"]["status"] == "non_compliant_flagged"
    assert without["enforced_trades"] == []

    with_map = reconcile([row], [], [], CFG, ctx)
    assert with_map["sleeves"]["IHE"]["status"] == "enforced"
    assert with_map["enforced_trades"][0]["side"] == "buy"


# --- A3: the hard concentration cap actually forces a sell ------------------

def test_concentration_cap_survives_a_full_override():
    """`single_sleeve_cap_pct_of_equity` = 12.0 was specified as "a breaching
    sleeve produces a required sell that no override can shelter". With an
    ACCEPTED re_risk override at the full relative shelter (magnitude_pp =
    10.164 = the reference itself, k=1.0), the band path shelters the entire
    9.976pp gap — and the cap must STILL require 20.14 - 12.0 = 8.14pp."""
    decisions = [{
        "outcome": "accepted",
        "override": {"sleeve": "SOXX", "direction": "re_risk", "magnitude_pp": 10.164},
    }]
    out = reconcile([SOXX_GAP], [], decisions, CFG, _ctx())
    entry = out["sleeves"]["SOXX"]
    assert entry["allowed_residual_pp"] == pytest.approx(10.16, abs=0.01)
    assert entry["required_move_total_pp"] == pytest.approx(8.14, abs=0.01)
    assert entry["status"] == "enforced"
    assert out["enforced_trades"][0]["side"] == "sell"


def test_concentration_cap_never_sells_below_reference():
    """A ceiling on holdings, not a floor on selling: a legitimately concentrated
    reference at/above the cap is not a breach, and the synthesized sell never
    lands the sleeve under its own reference."""
    # Reference itself >= the cap -> no breach at all.
    row = {"symbol": "SPY", "current_pct": 13.0, "reference_pct": 12.5,
           "price": 200.0, "held_qty": 64.6, "off_roster": False}
    out = reconcile([row], [], [], CFG, _ctx())
    assert "SPY" not in out["sleeves"] or out["sleeves"]["SPY"]["status"] != "enforced"

    # And the SOXX sell lands at/above SOXX's own reference, never below.
    out2 = reconcile([SOXX_GAP], [], [], CFG, _ctx())
    sold = out2["enforced_trades"][0]["quantity"] * SOXX_PX
    landing_pct = (SOXX_GAP["current_pct"] / 100 * EQUITY - sold) / EQUITY * 100
    assert landing_pct >= SOXX_GAP["reference_pct"]


# --- A4: the settling window binds, and re-arms on a revision change ---------

def test_revision_change_rearms_an_expired_window():
    """The 2026-09-02 self-initiating rule arms the window exactly ONCE ever (on
    the first run with no persisted state). By the time this cycle's engine
    change ships, state exists and is expired — so without a re-arm trigger the
    ~8pp SOXX sell would have gone out unpaced on the first post-merge run."""
    expired = {"start_date": "2026-09-02", "sessions_elapsed": 9,
               "last_date": "2026-09-11", "revision": "2026-09-02-reference-degeneracy"}
    assert advance_settling_window(expired, "2026-09-12", 5, 3.0,
                                   "2026-09-02-reference-degeneracy")["active"] is False
    out = advance_settling_window(expired, "2026-09-12", 5, 3.0,
                                  "2026-09-12-derisk-classifier")
    assert out["active"] is True
    assert out["start_date"] == "2026-09-12"
    assert out["sessions_elapsed"] == 1
    assert out["sessions_remaining"] == 5
    assert out["effective_cap"] == 3.0


def test_unchanged_revision_does_not_rearm_mid_window():
    """A re-arm must be driven by the tag CHANGING, never by its mere presence —
    otherwise every run would restart the window and it would never expire."""
    prior = {"start_date": "2026-09-12", "sessions_elapsed": 2,
             "last_date": "2026-09-14", "revision": "rev-A"}
    out = advance_settling_window(prior, "2026-09-15", 5, 3.0, "rev-A")
    assert out["start_date"] == "2026-09-12"   # unchanged
    assert out["sessions_elapsed"] == 3


def test_settling_window_paces_the_soxx_sell_to_3pp():
    """The merge prerequisite: the first post-merge run must submit ~3pp of SOXX,
    not the full 8.14pp (~$8.1k) the cap breach requires."""
    window = advance_settling_window(
        {"start_date": "2026-09-02", "sessions_elapsed": 9, "last_date": "2026-09-11",
         "revision": "old"}, "2026-09-12", 5, 3.0, "2026-09-12-derisk-classifier")
    cfg = {**CFG, "reference_execution": {
        **CFG["reference_execution"],
        "tranche_pp_max": resolve_settling_tranche_cap(
            CFG["reference_execution"]["tranche_pp_max"], window),
    }}
    out = reconcile([SOXX_GAP], [], [], cfg, _ctx(date="2026-09-12"))
    entry = out["sleeves"]["SOXX"]
    assert entry["required_move_total_pp"] == pytest.approx(8.14, abs=0.01)
    assert entry["required_move_today_pp"] == 3.0          # paced, not 8.14
    notional = out["enforced_trades"][0]["quantity"] * SOXX_PX
    assert notional / EQUITY * 100 <= 3.0 + 0.01


# --- A5: the mislabel can no longer repeat silently -------------------------

def test_classification_conflict_catches_an_amplifier_sell_labeled_re_risk():
    """The tripwire fires on exactly the shape of the 07-27->09-12 defect."""
    detail = classification_conflict("sell", "SOXX", EFF)
    assert detail and "AMPLIFIER" in detail and "SOXX" in detail
    assert classification_conflict("sell", "SMH", EFF)      # deselected, still risk-on
    assert classification_conflict("buy", "IHE", EFF)       # defensive side


def test_no_classification_conflict_on_a_healthy_run():
    """Selling a damper and buying an amplifier are genuinely re-risk — the
    tripwire must stay silent, or it is noise rather than a signal."""
    assert classification_conflict("sell", "GLD", EFF) is None
    assert classification_conflict("buy", "SOXX", EFF) is None
    assert classification_conflict("sell", "MU", EFF) is None     # off-roster
    out = reconcile([SOXX_GAP], [], [], CFG, _ctx())
    assert out["classification_conflicts"] == []


def test_conflict_addendum_renders_a_blocking_warning():
    md = ah._classification_conflict_addendum(
        [{"symbol": "SOXX", "side": "sell", "classified": "re_risk",
          "detail": "SOXX SELL classified RE-RISK, but SOXX resolves as an AMPLIFIER"}]
    )
    assert "Data Integrity Warning" in md and "SOXX" in md
    assert ah._classification_conflict_addendum([]) == ""


# --- A2 audit fixes ---------------------------------------------------------

def test_override_sign_uses_the_effective_damper():
    """Phase-5 grading: an override on the live healthcare damper (IHE) graded
    with an INVERTED sign against the frozen DAMPER tuple, silently reversing
    `excess_pp`/`resolved_correct` in the record the Learning Loop reads."""
    assert ch._override_sign("IHE", "de_risk", EFF) == 1.0
    assert ch._override_sign("IHE", "re_risk", EFF) == -1.0


def test_override_sign_treats_sgov_as_defensive():
    """Second, independent defect on the same line: it read `set(DAMPER)`, which
    EXCLUDES SGOV, while `derive_override_direction`/`is_de_risk_move` both
    classify SGOV as defensive. A cash-sleeve override graded backwards
    regardless of auto-switch."""
    assert ch._override_sign("SGOV", "de_risk") == 1.0
    assert ch._override_sign("SGOV", "re_risk") == -1.0
    # Unchanged for an ordinary damper / amplifier.
    assert ch._override_sign("GLD", "de_risk") == 1.0
    assert ch._override_sign("SPY", "de_risk") == -1.0


def test_regime_suspect_history_sees_the_effective_incumbent(monkeypatch):
    """`_write_regime_suspect_history` bucketed trades against the frozen
    QUADRANT_CONCENTRATE, so a SOXX trade matched no Q1 member and the row
    recorded `action: "held"` on a session the book had actually REDUCED Q1 —
    wrong evidence in the dataset the monthly review reads to judge regime
    calls."""
    written = []
    monkeypatch.setattr(ah, "upsert_entity", lambda t, e: written.append((t, e)))
    snapshot = {
        "quadrant_performance": {"buckets": {"Q1": {
            "suspect": True, "favored_streak": 12, "streak_excess_pp": -3.4}}},
        "prices": {"SOXX": {"c": SOXX_PX}},
        "effective_selected": EFF,
    }
    trades_obj = {"trades": [{"symbol": "SOXX", "side": "sell", "quantity": 29}]}
    ah._write_regime_suspect_history("2026-09-12", snapshot, trades_obj)
    assert written, "no regime_suspect row written"
    assert written[0][1]["action"] == "reduced"
