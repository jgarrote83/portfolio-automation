"""Unit tests for mechanical confidence damping on the conviction path
(2026-09-02, Task D2).

08-31 calibration (n=102 matured) was not merely poor but INVERTED — higher
stated confidence predicted WORSE outcomes. The 08-31 report's prose fix
("I am compressing confidence toward 0.5") did not survive into later
sessions. D2 makes it mechanical: `p_up` is damped TOWARD its own empirical
`base_rate_up` (reusing the existing brier_damping ladder) BEFORE
`edge = p_up - base_rate_up`, replacing the old post-hoc size_mult
multiplier.

Run: PYTHONPATH=src pytest tests/test_conviction_damping.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import collector.handler as handler  # noqa: E402
from collector.handler import (  # noqa: E402
    _build_flex_conviction,
    _damp_p_up_toward_base_rate,
)

CFG_BASE = {
    "conviction": {
        "enabled": True,
        "horizon_days_range": [15, 30],
        "base_rate_lookback_days": 504,
        "base_rate_min_windows": 60,
        "ladder": [
            {"edge_min": 0.18, "conviction": "very_high", "size_mult": 1.00},
            {"edge_min": 0.12, "conviction": "high", "size_mult": 0.70},
            {"edge_min": 0.07, "conviction": "moderate", "size_mult": 0.45},
            {"edge_min": 0.04, "conviction": "low", "size_mult": 0.25},
            {"edge_min": 0.0, "conviction": "none", "size_mult": 0.0},
        ],
        "catalyst_size_mult": 1.5,
        "catalyst_promotes_band": True,
        "min_evidence_items": 2,
        "confirm_sessions": 2,
        "release_sessions": 2,
        "max_session_delta_pct_of_equity": 1.5,
        "brier_min_sample": 10,
        "brier_damping": [
            {"brier_max": 0.20, "factor": 1.0},
            {"brier_max": 0.25, "factor": 0.75},
            {"brier_max": 0.30, "factor": 0.50},
            {"brier_max": 1.0, "factor": 0.0},
        ],
        "p_up_damping_factor_override": None,
    },
}


# --- the pure flattening formula ---------------------------------------------

def test_damping_factor_one_is_a_no_op():
    assert _damp_p_up_toward_base_rate(0.75, 0.55, 1.0) == 0.75


def test_damping_factor_zero_fully_flattens_to_base_rate():
    assert _damp_p_up_toward_base_rate(0.92, 0.55, 0.0) == 0.55


def test_intermediate_damping_interpolates():
    out = _damp_p_up_toward_base_rate(0.75, 0.55, 0.5)
    assert out == 0.65   # halfway between 0.55 and 0.75


# --- full _build_flex_conviction wiring --------------------------------------

def _fixture(monkeypatch, base_rate=0.55, windows=120):
    monkeypatch.setattr(
        handler, "_base_rate_up",
        lambda closes, horizon, lookback, min_w: {"base_rate_up": base_rate, "windows": windows},
    )
    nominations = [{
        "symbol": "NVDA", "p_up": 0.92, "horizon_days": 21,
        "evidence": ["e1", "e2"], "catalyst_date": None,
    }]
    eligibility = {"candidates": [{"symbol": "NVDA", "flex_nominatable": True}]}
    return nominations, eligibility


def test_full_override_flattens_edge_to_zero_regardless_of_stated_p_up(monkeypatch):
    """The literal D2 config default (0.0): a stated p_up of 0.92 (the exact
    08-31 shape that predicted 0.33 actual) produces NO edge and NO size at
    all — the inverted-confidence sizing lever is off."""
    nominations, eligibility = _fixture(monkeypatch)
    cfg = {"conviction": {**CFG_BASE["conviction"], "p_up_damping_factor_override": 0.0}}
    block, _ = _build_flex_conviction(cfg, nominations, {}, {"NVDA": {}}, eligibility, "2026-09-02", {})
    cand = block["active"][0] if block["active"] else block["pending"][0]
    assert cand["edge"] == 0.0
    assert cand["p_up"] == 0.92          # raw stated value preserved
    assert cand["p_up_damped"] == 0.55   # fully flattened to base_rate_up
    assert cand["damping_factor_applied"] == 0.0


def test_override_none_defers_to_mechanical_calibration_ladder(monkeypatch):
    """override=None (Jorge has relaxed it) -> the MECHANICAL brier ladder
    governs via `calibration.damping_factor`, exactly as before D2 — just
    applied to p_up now instead of the final size."""
    nominations, eligibility = _fixture(monkeypatch)
    cfg = {"conviction": {**CFG_BASE["conviction"], "p_up_damping_factor_override": None}}
    calibration = {"damping_factor": 1.0}   # e.g. a clean, well-calibrated Brier score
    block, _ = _build_flex_conviction(cfg, nominations, {}, {"NVDA": {}}, eligibility, "2026-09-02", calibration)
    cand = block["active"][0] if block["active"] else block["pending"][0]
    assert cand["p_up_damped"] == 0.92     # undamped: 0.92 + (0.92-0.55)*1.0... == 0.92
    assert round(cand["edge"], 4) == round(0.92 - 0.55, 4)
    assert cand["damping_factor_applied"] == 1.0


def test_mechanical_ladder_partial_damping_reduces_edge_and_rung(monkeypatch):
    """A partially-damped calibration factor (e.g. 0.5, a moderately-bad Brier
    score) shrinks edge proportionally and can drop the ladder rung."""
    nominations, eligibility = _fixture(monkeypatch)
    cfg = {"conviction": {**CFG_BASE["conviction"], "p_up_damping_factor_override": None}}
    calibration = {"damping_factor": 0.5}
    block, _ = _build_flex_conviction(cfg, nominations, {}, {"NVDA": {}}, eligibility, "2026-09-02", calibration)
    cand = block["active"][0] if block["active"] else block["pending"][0]
    # p_up_damped = 0.55 + (0.92-0.55)*0.5 = 0.735; edge = 0.185 -> still very_high
    assert cand["p_up_damped"] == 0.735
    assert round(cand["edge"], 4) == 0.185
    # Session 1 is "pending" (hysteresis confirm_sessions=2) -- the candidate's
    # OWN computed conviction label surfaces as `candidate_conviction`.
    assert cand["candidate_conviction"] == "very_high"


def test_no_double_damping_size_mult_matches_ladder_rung_exactly(monkeypatch):
    """The OLD mechanism additionally multiplied the final size_mult by
    damping_factor (double-damping if combined with the new p_up flattening).
    D2 removes that: size_mult must land EXACTLY on the ladder rung the
    (already-damped) edge selects, not further scaled down. Confirmed active
    over TWO sessions (hysteresis confirm_sessions=2) so `target_size_mult`
    reflects the real computed value rather than session-1's pending 0.0."""
    nominations, eligibility = _fixture(monkeypatch)
    cfg = {"conviction": {**CFG_BASE["conviction"], "p_up_damping_factor_override": None}}
    calibration = {"damping_factor": 0.5}
    _, states1 = _build_flex_conviction(cfg, nominations, {}, {"NVDA": {}}, eligibility, "2026-09-02", calibration)
    block2, _ = _build_flex_conviction(cfg, nominations, states1, {"NVDA": {}}, eligibility, "2026-09-03", calibration)
    cand = block2["active"][0]
    assert cand["target_size_mult"] == 1.00   # the very_high rung's size_mult verbatim
