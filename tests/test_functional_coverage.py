"""B3 (deferred finding 7) — deterministic Table-B functional-coverage block.

Each held name counts in EVERY quadrant its role covers (NOT additive to 100%); SGOV
counts in Q4 (primary) and Q3 (secondary); a dual-quadrant role name (VDE = energy,
Q2/Q3) counts in both; intl holdings go to the intl row only; off-roster names are
excluded with a note. The model echoes it verbatim instead of mis-summing it. Run:
    PYTHONPATH=src pytest tests/test_functional_coverage.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_functional_coverage  # noqa: E402


def _pos(ticker, pct, equity=100_000.0):
    return {"ticker": ticker, "market_value": equity * pct / 100.0}


def _book():
    # GLD (gold Q3/Q4), VDE (energy Q2/Q3), TLT (duration_long Q4-only), SGOV (cash),
    # VXUS (intl), MU (off-roster flex leftover).
    return [
        _pos("GLD", 17.0), _pos("VDE", 4.0), _pos("TLT", 10.0),
        _pos("SGOV", 24.62), _pos("VXUS", 3.0), _pos("MU", 2.0),
    ]


def test_unavailable_without_equity():
    assert _build_functional_coverage(_book(), 0.0)["available"] is False


def test_totals_equal_sum_of_listed_names():
    fc = _build_functional_coverage(_book(), 100_000.0)
    assert fc["available"] is True
    for q, cell in fc["quadrants"].items():
        listed = round(sum(n["pct"] for n in cell["names"]), 2)
        assert cell["total_pct"] == listed, (q, cell["total_pct"], listed)


def test_sgov_counts_in_q3_and_q4():
    fc = _build_functional_coverage(_book(), 100_000.0)
    q3_names = {n["ticker"] for n in fc["quadrants"]["Q3"]["names"]}
    q4_names = {n["ticker"] for n in fc["quadrants"]["Q4"]["names"]}
    assert "SGOV" in q3_names and "SGOV" in q4_names


def test_dual_quadrant_name_counts_in_both():
    fc = _build_functional_coverage(_book(), 100_000.0)
    q2_names = {n["ticker"] for n in fc["quadrants"]["Q2"]["names"]}
    q3_names = {n["ticker"] for n in fc["quadrants"]["Q3"]["names"]}
    assert "VDE" in q2_names and "VDE" in q3_names


def test_intl_and_off_roster_handling():
    fc = _build_functional_coverage(_book(), 100_000.0)
    intl_names = {n["ticker"] for n in fc["quadrants"]["intl"]["names"]}
    assert "VXUS" in intl_names
    excluded = {e["ticker"] for e in fc["excluded"]}
    assert "MU" in excluded          # off-roster flex leftover
    assert "VXUS" not in excluded    # intl is classified, not excluded


def test_sgov_note_inputs_and_non_additive_totals():
    fc = _build_functional_coverage(_book(), 100_000.0)
    assert fc["sgov_note_inputs"]["sgov_pct"] == 24.62
    assert fc["sgov_note_inputs"]["committed_q4_pct"] == 10.0   # TLT only (Q4-exclusive)
    # Deliberately NOT additive to 100% (secondary counting).
    total = sum(cell["total_pct"] for cell in fc["quadrants"].values())
    assert total != 100.0
