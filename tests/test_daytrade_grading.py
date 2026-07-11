"""DayTrade Lab grading (spec §5) — haircut math, expectancy, the PRE-REGISTERED
n=20 unlock/kill and n=40 cell-removal rules, spec-version reset, enforcement.

Run: PYTHONPATH=src pytest tests/test_daytrade_grading.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from daytrade.grading import (  # noqa: E402
    N_CELL,
    N_UNLOCK,
    build_daytrade_grades,
    entry_refusal,
    haircut_r,
    net_r,
    outcome_of,
)

V = "v0.1"


def _row(r_net, klass="A", pattern="orb", slot=1, outcome=None, version=V):
    return {
        "outcome": outcome or outcome_of(r_net),
        "r_multiple_net": r_net, "catalyst_class": klass,
        "pattern": pattern, "slot": slot, "spec_version": version,
    }


# ── haircut + r math ─────────────────────────────────────────────────────────

def test_haircut_round_trip_in_r():
    # entry 20, stop 19.6 (risk .4); 0.10 pp/side ⇒ 0.20% of 20 = $0.04 ⇒ 0.1R.
    assert haircut_r(20.0, 19.6, 0.10) == 0.1


def test_net_r_applies_haircut():
    raw, net = net_r(20.0, 19.6, 20.8, 0.10)   # +2R raw
    assert raw == 2.0 and net == 1.9
    raw, net = net_r(20.0, 19.6, 19.6, 0.10)   # stopped: −1R raw ⇒ −1.1R net
    assert raw == -1.0 and net == -1.1


def test_outcome_bands():
    assert outcome_of(1.5) == "win"
    assert outcome_of(-0.5) == "loss"
    assert outcome_of(0.05) == "scratch"
    assert outcome_of(-0.1) == "scratch"


# ── aggregation ──────────────────────────────────────────────────────────────

def test_no_setup_and_discard_rows_excluded_from_n():
    rows = [_row(1.0), {"outcome": "no_setup", "spec_version": V},
            {"outcome": "discarded", "spec_version": V}]
    g = build_daytrade_grades(rows, V)
    assert g["overall"]["n"] == 1


def test_per_cell_and_per_slot_aggregation():
    rows = [_row(1.0), _row(-1.1, klass="B", pattern="vwap_pullback", slot=2)]
    g = build_daytrade_grades(rows, V)
    assert g["per_cell"]["Axorb"]["n"] == 1
    assert g["per_cell"]["Bxvwap_pullback"]["expectancy_net_r"] == -1.1
    assert g["per_slot"]["slot2"]["n"] == 1


# ── pre-registered rules — the engine ENFORCES these ─────────────────────────

def test_below_n20_no_rule_fires():
    g = build_daytrade_grades([_row(-1.1)] * (N_UNLOCK - 1), V)
    assert not g["rules"]["kill"] and not g["rules"]["concurrency_unlock"]


def test_n20_positive_expectancy_unlocks():
    g = build_daytrade_grades([_row(0.5)] * N_UNLOCK, V)
    assert g["rules"]["concurrency_unlock"] and not g["rules"]["kill"]


def test_n20_nonpositive_expectancy_kills():
    g = build_daytrade_grades([_row(-0.2)] * N_UNLOCK, V)
    assert g["rules"]["kill"] and not g["rules"]["concurrency_unlock"]
    assert g["rules"]["n_unlock"] == N_UNLOCK    # trigger count recorded


def test_n40_negative_cell_removed():
    rows = [_row(-0.3, klass="C", pattern="orb")] * N_CELL \
        + [_row(0.8, klass="A", pattern="orb")] * 5
    g = build_daytrade_grades(rows, V)
    assert g["rules"]["blocked_cells"] == ["Cxorb"]


def test_spec_version_bump_resets_counts():
    old = [_row(-0.5, version="v0.1")] * (N_UNLOCK * 2)
    g = build_daytrade_grades(old, "v0.2")     # thresholds changed ⇒ new version
    assert g["overall"]["n"] == 0 and not g["rules"]["kill"]


def test_enforcement_refusals():
    killed = build_daytrade_grades([_row(-0.2)] * N_UNLOCK, V)
    assert entry_refusal(killed, V, "A", "orb") == "kill_active_expectancy_nonpositive"
    # a version bump neutralizes stale grades — reset by design
    assert entry_refusal(killed, "v0.2", "A", "orb") is None
    blocked = build_daytrade_grades(
        [_row(-0.3, klass="C", pattern="orb")] * N_CELL
        + [_row(0.8)] * N_CELL, V)
    assert not blocked["rules"]["kill"]
    assert entry_refusal(blocked, V, "C", "orb") == "cell_blocked:Cxorb"
    assert entry_refusal(blocked, V, "A", "orb") is None
    assert entry_refusal(None, V, "A", "orb") is None
