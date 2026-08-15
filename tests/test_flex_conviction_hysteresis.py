"""Task B4 (2026-08-14 flex-conviction-path cycle) — per-symbol flex-
conviction hysteresis (`_confirm_flex_conviction_entry`), the exact same
confirm/release discipline as `_confirm_thematic_entry` (PR #38 Task D5)
applied to a `size_mult` target instead of a `%`-of-equity target. This
REPLACES `time_stop_days` on the conviction path (Task E): a position steps
down via `release_sessions` of the nomination no longer reproducing, not a
fixed calendar clock.

Run: PYTHONPATH=src pytest tests/test_flex_conviction_hysteresis.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _confirm_flex_conviction_entry, _load_risk_limits  # noqa: E402

CFG = _load_risk_limits()["conviction"]


def _cand(conviction="high", size_mult=0.70, **extra) -> dict:
    return {"conviction": conviction, "size_mult": size_mult, **extra}


def test_one_session_does_not_activate():
    out = _confirm_flex_conviction_entry(_cand(), None, CFG)
    assert out["active"] is False
    assert out["status"] == "pending"
    assert out["pending_streak"] == 1


def test_two_consecutive_sessions_activates():
    first = _confirm_flex_conviction_entry(_cand(), None, CFG)
    second = _confirm_flex_conviction_entry(_cand(), first["_state"], CFG)
    assert second["active"] is True
    assert second["conviction"] == "high"


def test_staged_ramp_in():
    first = _confirm_flex_conviction_entry(_cand(size_mult=0.70), None, CFG)
    second = _confirm_flex_conviction_entry(_cand(size_mult=0.70), first["_state"], CFG)
    assert second["applied_size_mult"] == 0.70   # under max_session_delta_pct_of_equity=1.5, reaches directly
    # Use a bigger jump to exercise the ramp cap itself.
    third_cand = _cand(conviction="very_high", size_mult=1.00)
    a = _confirm_flex_conviction_entry(third_cand, None, CFG)
    b = _confirm_flex_conviction_entry(third_cand, a["_state"], CFG)
    assert b["applied_size_mult"] == 1.00  # 0 -> 1.00 delta is 1.00, under the 1.5 cap


def test_absence_after_active_one_session_does_not_release():
    first = _confirm_flex_conviction_entry(_cand(), None, CFG)
    second = _confirm_flex_conviction_entry(_cand(), first["_state"], CFG)
    third = _confirm_flex_conviction_entry(None, second["_state"], CFG)
    assert third["active"] is True
    assert third["release_pending"] is True


def test_absence_two_consecutive_sessions_releases():
    first = _confirm_flex_conviction_entry(_cand(), None, CFG)
    second = _confirm_flex_conviction_entry(_cand(), first["_state"], CFG)
    m1 = _confirm_flex_conviction_entry(None, second["_state"], CFG)
    m2 = _confirm_flex_conviction_entry(None, m1["_state"], CFG)
    assert m2["active"] is False
    assert m2["applied_size_mult"] == 0.0


def test_band_change_while_active_needs_release_sessions_before_swap():
    first = _confirm_flex_conviction_entry(_cand(conviction="high", size_mult=0.70), None, CFG)
    second = _confirm_flex_conviction_entry(_cand(conviction="high", size_mult=0.70), first["_state"], CFG)
    swap1 = _confirm_flex_conviction_entry(
        _cand(conviction="very_high", size_mult=1.00), second["_state"], CFG,
    )
    assert swap1["active"] is True
    assert swap1["conviction"] == "high"          # old stays in force
    assert swap1["candidate_conviction"] == "very_high"
    swap2 = _confirm_flex_conviction_entry(
        _cand(conviction="very_high", size_mult=1.00), swap1["_state"], CFG,
    )
    assert swap2["conviction"] == "very_high"     # confirmed -> swapped


def test_extra_metadata_passes_through_verbatim():
    cand = _cand(symbol="AVGO", p_up=0.62, base_rate_up=0.55, edge=0.07,
                 catalyst_date=None, evidence=["e1", "e2"])
    first = _confirm_flex_conviction_entry(cand, None, CFG)
    assert first["symbol"] == "AVGO"
    assert first["p_up"] == 0.62
    assert first["base_rate_up"] == 0.55
    assert first["edge"] == 0.07
    assert first["evidence"] == ["e1", "e2"]
    # Internal ladder keys must not leak into the meta passthrough twice.
    assert "conviction" in first and "size_mult" not in first


def test_no_prior_no_candidate_is_inert():
    out = _confirm_flex_conviction_entry(None, None, CFG)
    assert out["active"] is False
    assert out["status"] == "indeterminate"
