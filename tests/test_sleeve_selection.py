"""Unit tests for the sleeve-selection scorecard (Task E — role-based core).

Deterministic, describe-only ranking of a role's candidate pool: a momentum blend
(0.5·r120 + 0.3·r60 + 0.2·r252, renormalized over available windows) minus an
expense-ratio penalty, with a benchmark-correlation eligibility floor and a hysteresis
gate on the switch_signal. A switch_signal never auto-trades. Run:
    PYTHONPATH=src pytest tests/test_sleeve_selection.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import (  # noqa: E402
    _build_sleeve_selection,
    _member_momentum_score,
)

WEIGHTS = {"r120": 0.5, "r60": 0.3, "r252": 0.2}
CFG = {
    "momentum_weights": WEIGHTS,
    "expense_penalty_mult": 1.0,
    "min_benchmark_corr": 0.6,
    "hysteresis_lead": 2.0,
    "hysteresis_runs": 10,
}


def _role(pool, selected, ers, benchmark=None):
    return {
        "role_id": "semis", "selection": "scorecard",
        "quadrants": ["Q1"], "block": "amplifier_us",
        "pool": pool, "selected": selected,
        "benchmark_proxy": benchmark or selected,
        "expense_ratio": ers,
    }


def _metrics(r120, r60, r252, corr):
    return {"r120": r120, "r60": r60, "r252": r252, "corr_bench_120d": corr}


# --- momentum blend ---------------------------------------------------------------

def test_blend_weights_and_expense_penalty():
    s = _member_momentum_score(_metrics(10, 10, 10, 1.0), 0.10, WEIGHTS, 1.0)
    assert round(s, 2) == 9.90   # 10 - 0.10 ER penalty


def test_missing_window_renormalizes_not_penalizes():
    # r252 missing -> weights renormalize over r120+r60 (0.8), not dropped to a smaller total.
    s = _member_momentum_score({"r120": 10, "r60": 10, "r252": None}, 0.0, WEIGHTS, 1.0)
    assert round(s, 2) == 10.0


def test_no_history_scores_none():
    assert _member_momentum_score({"r120": None, "r60": None, "r252": None}, 0.0, WEIGHTS, 1.0) is None


# --- eligibility ------------------------------------------------------------------

def test_low_benchmark_correlation_makes_member_ineligible():
    roles = [_role(["A", "B"], "A", {"A": 0.1, "B": 0.1})]
    metrics = {"A": _metrics(10, 10, 10, 1.0), "B": _metrics(20, 20, 20, 0.4)}
    block, _ = _build_sleeve_selection(roles, metrics, {}, CFG)
    r = block["roles"][0]
    assert r["ineligible"] == ["B"]
    assert "B" not in r["scores"]
    assert r["challenger"] is None    # no eligible challenger
    assert r["switch_signal"] is False


# --- hysteresis -------------------------------------------------------------------

def test_switch_signal_needs_ten_consecutive_leading_runs():
    roles = [_role(["A", "B"], "A", {"A": 0.1, "B": 0.1})]
    metrics = {"A": _metrics(10, 10, 10, 1.0), "B": _metrics(15, 15, 15, 0.9)}  # B leads by 5
    state: dict = {}
    for run in range(1, 10):   # runs 1..9
        block, state = _build_sleeve_selection(roles, metrics, state, CFG)
        r = block["roles"][0]
        assert r["challenger"] == "B" and r["streak"] == run
        assert r["switch_signal"] is False
    # 10th consecutive leading run fires the signal
    block, state = _build_sleeve_selection(roles, metrics, state, CFG)
    r = block["roles"][0]
    assert r["streak"] == 10
    assert r["switch_signal"] is True


def test_streak_resets_when_lead_drops_below_threshold():
    roles = [_role(["A", "B"], "A", {"A": 0.1, "B": 0.1})]
    leading = {"A": _metrics(10, 10, 10, 1.0), "B": _metrics(15, 15, 15, 0.9)}
    state: dict = {}
    for _ in range(5):
        _, state = _build_sleeve_selection(roles, leading, state, CFG)
    assert state["semis"]["streak"] == 5
    # B's lead collapses below 2.0 -> streak resets to 0
    weak = {"A": _metrics(10, 10, 10, 1.0), "B": _metrics(11, 11, 11, 0.9)}
    block, state = _build_sleeve_selection(roles, weak, state, CFG)
    r = block["roles"][0]
    assert r["streak"] == 0
    assert r["switch_signal"] is False


def test_streak_resets_when_challenger_changes():
    roles = [_role(["A", "B", "C"], "A", {"A": 0.1, "B": 0.1, "C": 0.1})]
    b_leads = {"A": _metrics(10, 10, 10, 1.0), "B": _metrics(15, 15, 15, 0.9),
               "C": _metrics(9, 9, 9, 0.9)}
    state: dict = {}
    for _ in range(4):
        _, state = _build_sleeve_selection(roles, b_leads, state, CFG)
    assert state["semis"] == {"challenger": "B", "streak": 4}
    # now C becomes the leader -> streak restarts at 1 for the new challenger
    c_leads = {"A": _metrics(10, 10, 10, 1.0), "B": _metrics(9, 9, 9, 0.9),
               "C": _metrics(15, 15, 15, 0.9)}
    block, state = _build_sleeve_selection(roles, c_leads, state, CFG)
    r = block["roles"][0]
    assert r["challenger"] == "C" and r["streak"] == 1


def test_switch_signal_never_edits_selected():
    # The block reports incumbent = the config `selected`; it is describe-only.
    roles = [_role(["A", "B"], "A", {"A": 0.1, "B": 0.1})]
    metrics = {"A": _metrics(10, 10, 10, 1.0), "B": _metrics(30, 30, 30, 0.9)}
    block, _ = _build_sleeve_selection(roles, metrics, {}, CFG)
    assert block["roles"][0]["incumbent"] == "A"
    assert "never" in block["_note"].lower()
