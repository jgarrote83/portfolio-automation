"""Task D5 (2026-08-14 audit) — per-symbol thematic-conviction hysteresis
(`_confirm_thematic_entry`), mirroring Task C's confirm/release discipline.

Run: PYTHONPATH=src pytest tests/test_thematic_hysteresis.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _confirm_thematic_entry, _load_risk_limits  # noqa: E402

CFG = _load_risk_limits()["thematic_conviction"]


def _cand(conviction="high", target=2.5, **extra) -> dict:
    return {"conviction": conviction, "target_pct_of_equity": target, **extra}


def test_one_session_does_not_activate():
    out = _confirm_thematic_entry(_cand(), None, CFG)
    assert out["active"] is False
    assert out["status"] == "pending"
    assert out["pending_streak"] == 1


def test_two_consecutive_sessions_activates():
    first = _confirm_thematic_entry(_cand(), None, CFG)
    second = _confirm_thematic_entry(_cand(), first["_state"], CFG)
    assert second["active"] is True
    assert second["conviction"] == "high"


def test_staged_ramp_in():
    first = _confirm_thematic_entry(_cand(target=2.5), None, CFG)
    second = _confirm_thematic_entry(_cand(target=2.5), first["_state"], CFG)
    assert second["applied_pct_of_equity"] == 1.5   # max_session_delta_pp cap
    third = _confirm_thematic_entry(_cand(target=2.5), second["_state"], CFG)
    assert third["applied_pct_of_equity"] == 2.5     # reaches target


def test_absence_after_active_one_session_does_not_release():
    first = _confirm_thematic_entry(_cand(), None, CFG)
    second = _confirm_thematic_entry(_cand(), first["_state"], CFG)
    third = _confirm_thematic_entry(None, second["_state"], CFG)
    assert third["active"] is True
    assert third["release_pending"] is True


def test_absence_two_consecutive_sessions_releases():
    first = _confirm_thematic_entry(_cand(), None, CFG)
    second = _confirm_thematic_entry(_cand(), first["_state"], CFG)
    m1 = _confirm_thematic_entry(None, second["_state"], CFG)
    m2 = _confirm_thematic_entry(None, m1["_state"], CFG)
    assert m2["active"] is False
    assert m2["applied_pct_of_equity"] == 0.0


def test_band_change_while_active_needs_release_sessions_before_swap():
    first = _confirm_thematic_entry(_cand(conviction="high", target=2.5), None, CFG)
    second = _confirm_thematic_entry(_cand(conviction="high", target=2.5), first["_state"], CFG)
    # Now the nomination steps up to very_high.
    swap1 = _confirm_thematic_entry(_cand(conviction="very_high", target=4.0), second["_state"], CFG)
    assert swap1["active"] is True
    assert swap1["conviction"] == "high"          # old stays in force
    assert swap1["candidate_conviction"] == "very_high"
    swap2 = _confirm_thematic_entry(_cand(conviction="very_high", target=4.0), swap1["_state"], CFG)
    assert swap2["conviction"] == "very_high"     # confirmed -> swapped
