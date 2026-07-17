"""Session 2026-07-17, Task D — `quadrant_allocation` snapshot block (Table A's
"Current % of equity" column, precomputed).

07-17 published two contradictory Table A's in the same report (Q1 0.77% vs a
corrected 1.46%; Q2 5.37% vs 3.72%), with the literal leaked text "wait — let me
recompute carefully" landing in the markdown; 07-16 leaked "— wait, see note"
inside a table cell with the cell contradicting its own note. This block kills the
freehand quadrant-sum arithmetic entirely: every held name lands in exactly one
bucket via the SAME static `primary_quadrant()` the Reference column
(`_aggregate_by_quadrant`) already uses, so Current and Reference are always
apples-to-apples.

Run: PYTHONPATH=src pytest tests/test_quadrant_allocation.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_quadrant_allocation  # noqa: E402


def _pos(sym, mv):
    return {"ticker": sym, "market_value": mv}


def test_unavailable_when_no_equity():
    out = _build_quadrant_allocation([_pos("SPY", 1000.0)], 0.0, 0.0)
    assert out["available"] is False
    assert out["total_pct"] == 0.0


def test_basic_bucketing_q1_q2_and_cash():
    positions = [_pos("SPY", 40_000.0), _pos("XLI", 20_000.0), _pos("SGOV", 10_000.0)]
    out = _build_quadrant_allocation(positions, 100_000.0, 5_000.0)
    assert out["available"] is True
    assert out["buckets"]["Q1"] == 40.0
    assert out["buckets"]["Q2"] == 20.0
    # SGOV position (10%) + literal cash (5%) both land in cash_sleeve.
    assert out["buckets"]["cash_sleeve"] == 15.0
    assert out["cash_literal_pct"] == 5.0


def test_dual_quadrant_role_uses_static_primary_quadrant_like_reference_column():
    """GLD (gold role, quads=["Q3","Q4"]) must land in Q3 — the SAME static
    first-listed-quadrant convention `_aggregate_by_quadrant`/`primary_quadrant`
    already use for the Reference column, so the two columns never disagree
    about which bucket a dual-quadrant name belongs to."""
    out = _build_quadrant_allocation([_pos("GLD", 30_000.0)], 100_000.0, 0.0)
    assert out["buckets"]["Q3"] == 30.0
    assert out["buckets"]["Q4"] == 0.0


def test_intl_role_member_lands_in_intl_bucket():
    out = _build_quadrant_allocation([_pos("AIA", 5_000.0)], 100_000.0, 0.0)
    assert out["buckets"]["intl"] == 5.0


def test_legacy_exit_gets_dedicated_bucket_not_folded_into_a_quadrant():
    out = _build_quadrant_allocation([_pos("MCK", 4_330.0)], 100_000.0, 0.0)
    assert out["buckets"]["legacy_exits"] == 4.33
    for q in ("Q1", "Q2", "Q3", "Q4"):
        assert out["buckets"][q] == 0.0


def test_off_roster_held_name_gets_dedicated_bucket():
    """A flex leftover like MU — held but neither a legacy exit nor any role's
    pool member — must be visible, not silently dropped or miscounted."""
    out = _build_quadrant_allocation([_pos("MU", 1_707.80)], 97_842.94, 0.0)
    assert out["buckets"]["off_roster"] > 0
    assert out["contributions"]["off_roster"][0]["symbol"] == "MU"


def test_non_selected_pool_member_falls_into_unmapped_not_silently_dropped():
    """SOXX is a semis-role pool member but not the role's `selected` (SMH) — it
    is NOT a legacy exit (unlike its sibling pool member XSD) and IS in
    CORE_ROSTER, so `primary_quadrant` returns "unclassified" for it (only the
    selected member is in QUADRANT_CONCENTRATE). It must still be visible as
    `unmapped`, never vanish."""
    out = _build_quadrant_allocation([_pos("SOXX", 2_000.0)], 100_000.0, 0.0)
    assert out["buckets"]["unmapped"] == 2.0
    assert out["contributions"]["unmapped"][0]["symbol"] == "SOXX"


def test_bucket_sum_approximately_100_pct():
    positions = [
        _pos("SPY", 30_000.0), _pos("GLD", 15_000.0), _pos("TLT", 10_000.0),
        _pos("AIA", 5_000.0), _pos("MCK", 3_000.0), _pos("MU", 2_000.0),
        _pos("SGOV", 20_000.0),
    ]
    out = _build_quadrant_allocation(positions, 100_000.0, 15_000.0)
    assert 99.0 <= out["total_pct"] <= 101.0


def test_empty_positions_all_zero_except_literal_cash():
    out = _build_quadrant_allocation([], 100_000.0, 8_000.0)
    assert out["available"] is True
    assert out["buckets"]["cash_sleeve"] == 8.0
    assert out["total_pct"] == 8.0
