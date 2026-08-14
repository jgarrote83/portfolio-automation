"""Task D (2026-08-14 audit, decisions D-4/D-5) — thematic conviction overlay,
pure-function layer: ladder lookup, eligibility classification, aggregate-cap
scaling, and Brier calibration/damping.

Run: PYTHONPATH=src pytest tests/test_thematic_conviction_pure.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from collector.handler import (  # noqa: E402
    _thematic_brier,
    _thematic_classify_symbol,
    _thematic_damping_factor,
    _thematic_ladder_lookup,
    _thematic_scale_to_caps,
    _RISK_LIMITS_DEFAULTS,
)
from shared.quadrants import roles_config, selected_for_role  # noqa: E402

LADDER = _RISK_LIMITS_DEFAULTS["thematic_conviction"]["ladder"]
DAMPING = _RISK_LIMITS_DEFAULTS["thematic_conviction"]["brier_damping"]


# --- ladder lookup: every band boundary, including exact edges -------------

def test_ladder_exact_edge_075_lands_very_high():
    target, conviction = _thematic_ladder_lookup(LADDER, 0.75)
    assert conviction == "very_high"
    assert target == 4.0


def test_ladder_just_below_075_lands_high():
    target, conviction = _thematic_ladder_lookup(LADDER, 0.7499)
    assert conviction == "high"
    assert target == 2.5


def test_ladder_exact_edge_065_lands_high():
    target, conviction = _thematic_ladder_lookup(LADDER, 0.65)
    assert conviction == "high"
    assert target == 2.5


def test_ladder_exact_edge_058_lands_moderate():
    target, conviction = _thematic_ladder_lookup(LADDER, 0.58)
    assert conviction == "moderate"
    assert target == 1.25


def test_ladder_exact_edge_052_lands_low():
    target, conviction = _thematic_ladder_lookup(LADDER, 0.52)
    assert conviction == "low"
    assert target == 0.50


def test_ladder_just_below_052_lands_none():
    target, conviction = _thematic_ladder_lookup(LADDER, 0.5199)
    assert conviction == "none"
    assert target == 0.0


def test_ladder_zero_lands_none():
    target, conviction = _thematic_ladder_lookup(LADDER, 0.0)
    assert conviction == "none"
    assert target == 0.0


def test_ladder_above_075_still_very_high_no_overshoot():
    target, conviction = _thematic_ladder_lookup(LADDER, 0.99)
    assert conviction == "very_high"
    assert target == 4.0


# --- eligibility classification (D4) ----------------------------------------

def test_legacy_exit_rejected():
    out = _thematic_classify_symbol("AMZN", set(), {})
    assert out == {"status": "excluded", "reason": "legacy_exit"}


def test_price_quarantined_rejected():
    out = _thematic_classify_symbol("MU", {"MU"}, {})
    assert out == {"status": "excluded", "reason": "price_quarantined"}


def test_non_selected_pool_member_rejected():
    """SOXX and SMH are both in the semis role's pool; if SMH is the
    effective selected incumbent, a thematic lift on SOXX is rejected."""
    out = _thematic_classify_symbol("SOXX", set(), {"semis": "SMH"})
    assert out == {"status": "excluded", "reason": "non_selected_pool_member"}


def test_selected_incumbent_is_core_eligible():
    out = _thematic_classify_symbol("SMH", set(), {"semis": "SMH"})
    assert out == {"status": "core_eligible", "reason": None}


def test_non_roster_ticker_routes_to_flex_not_excluded():
    """MU is not in CORE_ROSTER at all -- routes to flex, is NOT an
    'excluded' status (excluded implies a rejected core reference; flex
    routing is a different, non-excluded outcome)."""
    out = _thematic_classify_symbol("MU", set(), {})
    assert out["status"] == "flex_route"
    assert out["reason"] is None


# --- M3 remediation (2026-08-14, PR #38 re-audit): the classifier must ------
# resolve through selected_for_role (the SAME static-config fallback every
# other roster consumer uses), never a hand-rolled `.get(role_id)` that is
# simply falsy whenever no auto-switch override is live -- the ordinary case.
# Parametrized across EVERY non-selected pool member in the live roster, at
# effective_selected=None AND {} (both the "no override at all" shape), plus
# a live-switch case confirming the OLD incumbent becomes ineligible and the
# NEW one becomes eligible.

def _non_selected_pool_members() -> list[tuple[str, str]]:
    """(symbol, role_id) for every pool member that is NOT its role's static
    config `selected` -- i.e. every symbol that must classify `excluded` /
    `non_selected_pool_member` on an ordinary day (no live override)."""
    from shared.quadrants import LEGACY_EXITS
    pairs = []
    for r in roles_config():
        role_id = r.get("role_id")
        selected = selected_for_role(role_id, None)
        for m in r.get("pool", ()):
            sym = str(m).upper()
            if sym != selected and sym not in LEGACY_EXITS:
                pairs.append((sym, role_id))
    return pairs


@pytest.mark.parametrize("effective_selected", [None, {}])
@pytest.mark.parametrize("sym,role_id", _non_selected_pool_members())
def test_every_non_selected_pool_member_rejected_with_no_live_override(
    sym, role_id, effective_selected,
):
    """The exact leak the M3 finding reproduced: 11+ pool members classified
    core_eligible when effective_selected was falsy (None/{}), because the
    pre-fix classifier read `(effective_selected or {}).get(role_id)` directly
    instead of falling back to the static config `selected` via
    `selected_for_role`."""
    out = _thematic_classify_symbol(sym, set(), effective_selected)
    assert out == {"status": "excluded", "reason": "non_selected_pool_member"}, (
        f"{sym} (role {role_id}) leaked through as {out} with "
        f"effective_selected={effective_selected!r}"
    )


def test_live_override_switch_flips_eligibility_both_ways():
    """A live auto-switch override (effective_selected={'semis': 'SOXX'})
    must make the OLD incumbent (SMH) ineligible and the NEW one (SOXX)
    eligible -- the classifier tracks the LIVE effective incumbent, not a
    frozen snapshot of the static config."""
    switched = {"semis": "SOXX"}
    old_incumbent = _thematic_classify_symbol("SMH", set(), switched)
    new_incumbent = _thematic_classify_symbol("SOXX", set(), switched)
    assert old_incumbent == {"status": "excluded", "reason": "non_selected_pool_member"}
    assert new_incumbent == {"status": "core_eligible", "reason": None}


def test_quarantine_checked_before_roster_membership():
    """A quarantined off-roster name is still excluded (quarantine wins),
    not silently routed to flex."""
    out = _thematic_classify_symbol("MU", {"MU"}, {})
    assert out == {"status": "excluded", "reason": "price_quarantined"}


# --- aggregate cap: pro-rata scaling, never truncation ----------------------

def test_aggregate_cap_scales_pro_rata_not_truncation():
    targets = {"VDE": 4.0, "PDBC": 4.0, "XLI": 4.0}   # sum 12.0, cap 8.0
    out = _thematic_scale_to_caps(targets, per_ticker_cap=4.0, aggregate_cap=8.0)
    # Pro-rata: every entry scaled by 8/12, none truncated to zero by rank.
    assert all(v > 0 for v in out.values())
    assert abs(sum(out.values()) - 8.0) < 1e-9
    for t in targets:
        assert abs(out[t] - (4.0 * (8.0 / 12.0))) < 1e-9


def test_per_ticker_cap_applied_before_aggregate():
    targets = {"VDE": 6.0}   # exceeds per_ticker_cap of 4.0
    out = _thematic_scale_to_caps(targets, per_ticker_cap=4.0, aggregate_cap=8.0)
    assert out["VDE"] == 4.0   # clamped, aggregate not binding


def test_under_aggregate_cap_unchanged():
    targets = {"VDE": 2.0, "PDBC": 1.0}
    out = _thematic_scale_to_caps(targets, per_ticker_cap=4.0, aggregate_cap=8.0)
    assert out == {"VDE": 2.0, "PDBC": 1.0}


def test_negative_target_floored_to_zero():
    out = _thematic_scale_to_caps({"VDE": -1.0}, per_ticker_cap=4.0, aggregate_cap=8.0)
    assert out["VDE"] == 0.0


# --- Brier score + hit rate --------------------------------------------------

def test_brier_known_vector():
    # p=1.0 correct (actual 1) -> 0 error, hit; p=0.0 correct (actual 0) -> 0
    # error, hit; p=0.6 wrong-sided (actual 0) -> 0.36 error, miss; p=0.5
    # (actual 1) -> 0.25 error, hit (p>=0.5 matches a>=0.5).
    pairs = [(1.0, 1.0), (0.0, 0.0), (0.6, 0.0), (0.5, 1.0)]
    out = _thematic_brier(pairs)
    assert out["sample_size"] == 4
    assert abs(out["brier_score"] - (0.0 + 0.0 + 0.36 + 0.25) / 4) < 1e-9
    assert out["hit_rate"] == 0.75   # 3 of 4: (1,1) hit, (0,0) hit, (0.6,0) miss, (0.5,1) hit


def test_brier_empty_input():
    out = _thematic_brier([])
    assert out["sample_size"] == 0
    assert out["brier_score"] is None
    assert out["hit_rate"] is None


def test_brier_perfect_calibration():
    pairs = [(1.0, 1.0), (0.0, 0.0), (1.0, 1.0), (0.0, 0.0)]
    out = _thematic_brier(pairs)
    assert out["brier_score"] == 0.0
    assert out["hit_rate"] == 1.0


# --- damping ladder ----------------------------------------------------------

def test_damping_below_min_sample_no_damping():
    factor = _thematic_damping_factor(brier_score=0.5, sample_size=3, damping_ladder=DAMPING,
                                       brier_min_sample=10)
    assert factor == 1.0


def test_damping_none_brier_no_damping():
    factor = _thematic_damping_factor(brier_score=None, sample_size=20, damping_ladder=DAMPING,
                                       brier_min_sample=10)
    assert factor == 1.0


def test_damping_each_rung():
    assert _thematic_damping_factor(0.15, 20, DAMPING, 10) == 1.0
    assert _thematic_damping_factor(0.20, 20, DAMPING, 10) == 1.0
    assert _thematic_damping_factor(0.22, 20, DAMPING, 10) == 0.75
    assert _thematic_damping_factor(0.25, 20, DAMPING, 10) == 0.75
    assert _thematic_damping_factor(0.28, 20, DAMPING, 10) == 0.50
    assert _thematic_damping_factor(0.30, 20, DAMPING, 10) == 0.50
    assert _thematic_damping_factor(0.50, 20, DAMPING, 10) == 0.0
