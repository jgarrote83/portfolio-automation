"""Task D2 (2026-08-14 audit) — `_build_thematic_conviction`, the collector
orchestration function tying eligibility (D4), the ladder (D3), aggregate/
per-ticker caps (D5), and per-symbol hysteresis (D5) together.

Run: PYTHONPATH=src pytest tests/test_build_thematic_conviction.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_thematic_conviction, _load_risk_limits  # noqa: E402

RL = _load_risk_limits()


def _nom(symbol, p_up=0.68, evidence=None, theme="hormuz_energy_supply") -> dict:
    return {
        "symbol": symbol, "theme": theme, "p_up": p_up,
        "evidence": evidence if evidence is not None else ["a", "b"],
        "review_date": "2026-09-12",
    }


def test_enabled_false_is_complete_no_op():
    rl = {"thematic_conviction": {**RL["thematic_conviction"], "enabled": False}}
    block, new_states = _build_thematic_conviction(rl, [_nom("VDE")], {}, set(), {}, {})
    assert block == {"available": True, "enabled": False}
    assert new_states == {}


def test_legacy_exit_nomination_rejected():
    block, _ = _build_thematic_conviction(RL, [_nom("AMZN")], {}, set(), {}, {})
    reasons = {e["symbol"]: e["reason"] for e in block["excluded"]}
    assert reasons.get("AMZN") == "legacy_exit"
    assert block["active"] == []
    assert block["pending"] == []


def test_price_quarantined_nomination_rejected():
    block, _ = _build_thematic_conviction(RL, [_nom("MU")], {}, {"MU"}, {}, {})
    reasons = {e["symbol"]: e["reason"] for e in block["excluded"]}
    assert reasons.get("MU") == "price_quarantined"


def test_non_selected_pool_member_nomination_rejected():
    block, _ = _build_thematic_conviction(
        RL, [_nom("SOXX")], {}, set(), {"semis": "SMH"}, {},
    )
    reasons = {e["symbol"]: e["reason"] for e in block["excluded"]}
    assert reasons.get("SOXX") == "non_selected_pool_member"


def test_non_roster_ticker_routes_to_flex_no_core_reference():
    """MU is not in CORE_ROSTER (and not quarantined here) -> routes to flex,
    never receives a core reference weight."""
    block, new_states = _build_thematic_conviction(RL, [_nom("MU")], {}, set(), {}, {})
    flex_entries = [e for e in block["excluded"] if e["symbol"] == "MU"]
    assert len(flex_entries) == 1
    assert flex_entries[0]["reason"] == "routed_to_flex"
    assert flex_entries[0]["flex_source"] == "thematic"
    assert block["active"] == []
    assert "MU" not in new_states


def test_below_min_evidence_items_rejected_not_downsized():
    """min_evidence_items=2; a nomination with only 1 evidence item must be
    REJECTED outright, never downsized to a smaller target."""
    block, new_states = _build_thematic_conviction(
        RL, [_nom("VDE", evidence=["only_one"])], {}, set(), {}, {},
    )
    reasons = {e["symbol"]: e["reason"] for e in block["excluded"]}
    assert reasons.get("VDE") == "insufficient_evidence"
    assert block["active"] == []
    assert block["pending"] == []
    assert "VDE" not in new_states


def test_eligible_nomination_starts_pending_on_first_session():
    block, new_states = _build_thematic_conviction(RL, [_nom("VDE", p_up=0.68)], {}, set(), {}, {})
    assert block["active"] == []
    assert len(block["pending"]) == 1
    assert block["pending"][0]["symbol"] == "VDE"
    assert block["pending"][0]["candidate_conviction"] == "high"
    assert "VDE" in new_states


def test_eligible_nomination_activates_on_second_confirming_session():
    _, states1 = _build_thematic_conviction(RL, [_nom("VDE", p_up=0.68)], {}, set(), {}, {})
    block2, states2 = _build_thematic_conviction(
        RL, [_nom("VDE", p_up=0.68)], states1, set(), {}, {},
    )
    assert len(block2["active"]) == 1
    assert block2["active"][0]["symbol"] == "VDE"
    assert block2["active"][0]["conviction"] == "high"
    assert block2["active"][0]["applied_pct_of_equity"] == 1.5  # ramp cap


def test_eligible_universe_and_excluded_describe_whole_roster_not_just_nominations():
    """The universe/excluded lists are describe-only for the WHOLE roster,
    independent of whether anything was nominated this session."""
    block, _ = _build_thematic_conviction(RL, [], {}, set(), {}, {})
    assert "AMZN" in [e["symbol"] for e in block["excluded"]]  # legacy exit, always excluded
    assert isinstance(block["eligible_universe"], list) and block["eligible_universe"]


def test_aggregate_cap_scales_pro_rata_through_to_active_ramp():
    """Three nominations each targeting the 4.0 per-ticker cap sum to 12.0,
    exceeding the 8.0 aggregate cap -> each scaled to 8/12 of 4.0 = 2.667,
    and hysteresis ramps toward THAT capped target, not the raw one."""
    noms = [_nom("VDE", p_up=0.80), _nom("PDBC", p_up=0.80), _nom("XLI", p_up=0.80)]
    _, states1 = _build_thematic_conviction(RL, noms, {}, set(), {}, {})
    block2, _ = _build_thematic_conviction(RL, noms, states1, set(), {}, {})
    assert len(block2["active"]) == 3
    for entry in block2["active"]:
        assert abs(entry["target_pct_of_equity"] - (4.0 * 8.0 / 12.0)) < 1e-3


def test_calibration_echoed_verbatim():
    calib = {"sample_size": 14, "brier_score": 0.21, "hit_rate": 0.64, "damping_factor": 1.0}
    block, _ = _build_thematic_conviction(RL, [], {}, set(), {}, calib)
    assert block["calibration"] == calib


def test_damping_factor_applied_to_ladder_target():
    """D5: thematic_target = ladder_lookup(p_up) x calibration.damping_factor,
    applied once at the collector level (echo-not-re-derive)."""
    calib = {"damping_factor": 0.5}
    noms = [_nom("VDE", p_up=0.68)]  # ladder target 2.5 undamped
    _, states1 = _build_thematic_conviction(RL, noms, {}, set(), {}, calib)
    block2, _ = _build_thematic_conviction(RL, noms, states1, set(), {}, calib)
    assert block2["active"][0]["target_pct_of_equity"] == 1.25   # 2.5 * 0.5
