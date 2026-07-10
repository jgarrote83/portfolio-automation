"""Unit tests for the deterministic per-quadrant aggregation of reference weights.

Task 5 (2026-07-09 audit): the analyzer used to sum the per-name reference weights
by quadrant freehand and got it wrong (Q3 claimed ~42.9% while its own footnote summed
to ~58%, and the Reference column totalled ~89.5%). The collector now emits
`reference_weights.by_quadrant` — a deterministic aggregation using a fixed
primary-quadrant map (SGOV + literal cash → `cash_sleeve`). Run:
    PYTHONPATH=src pytest tests/test_reference_by_quadrant.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _aggregate_by_quadrant  # noqa: E402
from shared.quadrants import CORE_ROSTER, primary_quadrant  # noqa: E402


def test_every_core_ticker_lands_in_exactly_one_bucket():
    valid = {"Q1", "Q2", "Q3", "Q4", "cash_sleeve"}
    for t in CORE_ROSTER:
        assert primary_quadrant(t) in valid, t
    # SGOV is the cash sleeve, not a quadrant; unknown names never silently drop.
    assert primary_quadrant("SGOV") == "cash_sleeve"
    assert primary_quadrant("MU") == "unclassified"


def test_documented_primaries_hold():
    # Matches the prompt's "Notes on the multi-quadrant tickers" primaries.
    assert primary_quadrant("SPY") == "Q1"
    assert primary_quadrant("AMZN") == "Q1"
    assert primary_quadrant("GOOGL") == "Q1"
    assert primary_quadrant("EWZ") == "Q2"
    assert primary_quadrant("TIP") == "Q2"
    assert primary_quadrant("GLD") == "Q3"
    assert primary_quadrant("MCK") == "Q3"
    assert primary_quadrant("TLT") == "Q4"


def test_aggregation_sums_to_100_within_rounding():
    targets = {"SPY": 30.0, "GLD": 20.0, "TLT": 15.0, "VDE": 10.0, "SGOV": 23.5}
    by_q = _aggregate_by_quadrant(targets, literal_cash_pct=1.5)
    assert abs(sum(by_q.values()) - 100.0) <= 0.5


def test_each_target_contributes_to_one_bucket():
    targets = {"SPY": 30.0, "GLD": 20.0, "TLT": 15.0, "VDE": 10.0, "SGOV": 23.5}
    by_q = _aggregate_by_quadrant(targets, literal_cash_pct=1.5)
    assert by_q["Q1"] == 30.0     # SPY
    assert by_q["Q2"] == 10.0     # VDE
    assert by_q["Q3"] == 20.0     # GLD
    assert by_q["Q4"] == 15.0     # TLT
    # SGOV (23.5) + literal cash (1.5)
    assert by_q["cash_sleeve"] == 25.0


def test_cash_sleeve_is_sgov_target_plus_literal_cash():
    by_q = _aggregate_by_quadrant({"SGOV": 23.5}, literal_cash_pct=1.5)
    assert by_q["cash_sleeve"] == 25.0
    by_q2 = _aggregate_by_quadrant({}, literal_cash_pct=1.5)
    assert by_q2["cash_sleeve"] == 1.5
