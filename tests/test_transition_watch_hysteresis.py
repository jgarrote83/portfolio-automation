"""Task C (2026-08-14 audit) — transition_watch confirm/release hysteresis +
staged-fraction ramp, wrapped around the pre-existing stateless
`_build_transition_watch` evaluation.

Motivating incident: a single session's stateless activation blew the Q2
reference block from 0.091% to 4.118% and back the very next day (the
2026-08-12 VDE whipsaw) — transition_watch had no memory at all. These tests
exercise `_confirm_transition_watch` directly (pure, no I/O) against
hand-built "raw" stateless-evaluation inputs.

Run: PYTHONPATH=src pytest tests/test_transition_watch_hysteresis.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _confirm_transition_watch, _load_risk_limits  # noqa: E402

CFG = _load_risk_limits()["transition_watch"]


def _raw(active: bool, proj: str | None = None, direction: str | None = None,
         frac: float = 0.0) -> dict:
    return {
        "active": active,
        "projected_quadrant": proj,
        "direction": direction,
        "staged_fraction": frac,
        "basis": ["be_5y.delta_20d_bp=-28.0"],
        "sides": [],
        "status": "active" if active else "indeterminate",
        "rule": "test",
    }


# --- activation: confirm_sessions gate --------------------------------------

def test_one_session_activation_does_not_apply():
    raw = _raw(True, "Q4", "de_risk", 0.30)
    out = _confirm_transition_watch(raw, None, CFG)
    assert out["active"] is False
    assert out["status"] == "pending"
    assert out["pending_streak"] == 1
    assert out["pending_projected_quadrant"] == "Q4"
    assert out["staged_fraction"] == 0.0


def test_two_consecutive_sessions_activates():
    raw = _raw(True, "Q4", "de_risk", 0.30)
    first = _confirm_transition_watch(raw, None, CFG)
    prev_state = first["_state"]
    second = _confirm_transition_watch(raw, prev_state, CFG)
    assert second["active"] is True
    assert second["status"] == "active"
    assert second["projected_quadrant"] == "Q4"
    assert second["direction"] == "de_risk"


def test_different_candidate_each_session_never_accumulates_streak():
    """A different (proj, direction) each session must restart the streak at 1,
    never accumulate toward confirmation."""
    prev = None
    for proj in ("Q4", "Q3", "Q4"):
        raw = _raw(True, proj, "de_risk", 0.30)
        out = _confirm_transition_watch(raw, prev, CFG)
        assert out["active"] is False
        assert out["confirm_streak"] == 1
        prev = out["_state"]


def test_no_candidate_resets_pending_streak():
    raw_hit = _raw(True, "Q4", "de_risk", 0.30)
    pending = _confirm_transition_watch(raw_hit, None, CFG)
    assert pending["confirm_streak"] == 1
    raw_miss = _raw(False)
    out = _confirm_transition_watch(raw_miss, pending["_state"], CFG)
    assert out["active"] is False
    assert out["status"] == "indeterminate"
    assert out["confirm_streak"] == 0
    assert out["candidate_projected_quadrant"] is None


# --- release: release_sessions gate -----------------------------------------

def _activated_state(proj="Q4", direction="de_risk", frac=0.30) -> dict:
    raw = _raw(True, proj, direction, frac)
    first = _confirm_transition_watch(raw, None, CFG)
    second = _confirm_transition_watch(raw, first["_state"], CFG)
    assert second["active"] is True
    return second["_state"]


def test_one_session_absence_after_active_does_not_release():
    active_state = _activated_state()
    raw_miss = _raw(False)
    out = _confirm_transition_watch(raw_miss, active_state, CFG)
    assert out["active"] is True
    assert out["release_pending"] is True
    assert out["release_streak"] == 1
    # Old projection stays fully in force, unchanged.
    assert out["projected_quadrant"] == "Q4"
    assert out["direction"] == "de_risk"


def test_two_consecutive_absences_releases():
    active_state = _activated_state()
    raw_miss = _raw(False)
    first_miss = _confirm_transition_watch(raw_miss, active_state, CFG)
    second_miss = _confirm_transition_watch(raw_miss, first_miss["_state"], CFG)
    assert second_miss["active"] is False
    assert second_miss["status"] == "indeterminate"
    assert second_miss["projected_quadrant"] is None
    assert second_miss["staged_fraction"] == 0.0


# --- staged-fraction ramp ----------------------------------------------------

def test_staged_fraction_ramps_not_one_step():
    """max_session_delta_frac=0.10, target=0.30 -> 3 sessions to reach target,
    never a single-step jump from 0 to 0.30."""
    raw = _raw(True, "Q4", "de_risk", 0.30)
    s1 = _confirm_transition_watch(raw, None, CFG)   # pending, streak 1
    s2 = _confirm_transition_watch(raw, s1["_state"], CFG)  # confirms -> active
    assert s2["active"] is True
    assert s2["staged_fraction"] == 0.10   # ramped in from 0, capped at max_delta
    assert s2["target_fraction"] == 0.30
    s3 = _confirm_transition_watch(raw, s2["_state"], CFG)
    assert s3["staged_fraction"] == 0.20
    s4 = _confirm_transition_watch(raw, s3["_state"], CFG)
    assert s4["staged_fraction"] == 0.30
    s5 = _confirm_transition_watch(raw, s4["_state"], CFG)
    assert s5["staged_fraction"] == 0.30  # capped at target, no overshoot


def test_staged_fraction_ramps_down_on_target_swap():
    """de_risk (0.30) fully ramped in, then a re_risk (0.15) target swaps in —
    applied fraction ramps DOWN toward the new lower target, not a hard drop."""
    state = _activated_state(frac=0.30)
    # Fully ramp in first.
    raw_de = _raw(True, "Q4", "de_risk", 0.30)
    cur = {"_state": state}
    for _ in range(4):
        cur = _confirm_transition_watch(raw_de, cur["_state"], CFG)
    assert cur["staged_fraction"] == 0.30

    # Now a re_risk candidate confirms and swaps in.
    raw_re = _raw(True, "Q3", "re_risk", 0.15)
    swap1 = _confirm_transition_watch(raw_re, cur["_state"], CFG)
    assert swap1["active"] is True
    assert swap1["projected_quadrant"] == "Q4"  # old stays in force, pending swap
    assert swap1["candidate_projected_quadrant"] == "Q3"
    swap2 = _confirm_transition_watch(raw_re, swap1["_state"], CFG)
    assert swap2["projected_quadrant"] == "Q3"  # confirmed -> swapped
    assert swap2["direction"] == "re_risk"
    assert swap2["staged_fraction"] == 0.20  # ramped DOWN from 0.30 toward 0.15


# --- mid-active candidate swap: no no-projection limbo ----------------------

def test_projected_quadrant_change_mid_active_keeps_old_until_new_confirms():
    active_state = _activated_state(proj="Q4", direction="de_risk", frac=0.30)
    raw_new = _raw(True, "Q3", "re_risk", 0.15)
    out1 = _confirm_transition_watch(raw_new, active_state, CFG)
    # Old projection stays in force -- never a no-projection limbo.
    assert out1["active"] is True
    assert out1["projected_quadrant"] == "Q4"
    assert out1["direction"] == "de_risk"
    assert out1["candidate_projected_quadrant"] == "Q3"
    assert out1["confirm_streak"] == 1

    out2 = _confirm_transition_watch(raw_new, out1["_state"], CFG)
    assert out2["active"] is True
    assert out2["projected_quadrant"] == "Q3"
    assert out2["direction"] == "re_risk"


def test_reproducing_active_target_resets_release_streak():
    active_state = _activated_state()
    raw_miss = _raw(False)
    missed_once = _confirm_transition_watch(raw_miss, active_state, CFG)
    assert missed_once["release_streak"] == 1

    raw_hit = _raw(True, "Q4", "de_risk", 0.30)
    reproduced = _confirm_transition_watch(raw_hit, missed_once["_state"], CFG)
    assert reproduced["release_streak"] == 0
    assert reproduced["release_pending"] is False
    assert reproduced["active"] is True
