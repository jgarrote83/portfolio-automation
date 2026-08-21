"""Unit tests for the equity-vs-(cash+net_mv) reconciliation guard (collector).

2026-08-21 measurement-integrity cycle, Task B: `equity`, `cash`, and
`net_mv = sum(position market_value)` are computed on adjacent lines in
`run()` and were never checked against each other — if Alpaca ever returns
a partial positions list, `net_mv` goes wrong while `equity` stays right (or
vice versa) and nothing notices, even though `equity` is the single number
the entire performance chart rests on. `_build_equity_reconciliation` is a
pure, non-fatal, non-gating check mirroring the existing
`_build_flex_reconciliation` pattern exactly. Run:
    PYTHONPATH=src pytest tests/test_equity_reconciliation.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_equity_reconciliation  # noqa: E402

TOLERANCE = 50.0  # matches gate F-2's floor for a small-equity test account


def test_clean_account_is_ok():
    res = _build_equity_reconciliation(
        equity=100_000.0, cash=20_000.0, net_mv=80_000.0,
        tolerance=TOLERANCE, position_count=5,
    )
    assert res["status"] == "ok"
    assert res["delta"] == 0.0
    assert res["equity"] == 100_000.0
    assert res["cash"] == 20_000.0
    assert res["net_mv"] == 80_000.0
    assert res["tolerance"] == TOLERANCE
    assert res["position_count"] == 5


def test_drift_inside_tolerance_is_ok():
    # equity=100,000; cash+net_mv=99,970 -> delta=30, tolerance=50.
    res = _build_equity_reconciliation(
        equity=100_000.0, cash=20_000.0, net_mv=79_970.0,
        tolerance=TOLERANCE, position_count=5,
    )
    assert res["status"] == "ok"
    assert res["delta"] == 30.0


def test_delta_exactly_at_tolerance_boundary_is_ok():
    res = _build_equity_reconciliation(
        equity=100_000.0, cash=20_000.0, net_mv=79_950.0,  # delta == 50.0 exactly
        tolerance=TOLERANCE, position_count=5,
    )
    assert res["status"] == "ok"
    assert res["delta"] == 50.0


def test_positions_list_truncated_is_mismatch_with_correct_delta():
    """A partial positions list understates net_mv while equity (a separate
    Alpaca field) stays correct -- the exact failure mode this guard exists
    to catch."""
    res = _build_equity_reconciliation(
        equity=100_000.0, cash=20_000.0, net_mv=50_000.0,  # $30k of positions "missing"
        tolerance=TOLERANCE, position_count=3,
    )
    assert res["status"] == "mismatch"
    assert res["delta"] == 30_000.0


def test_negative_delta_also_mismatches():
    res = _build_equity_reconciliation(
        equity=100_000.0, cash=20_000.0, net_mv=90_000.0,  # cash+net_mv overstates equity
        tolerance=TOLERANCE, position_count=5,
    )
    assert res["status"] == "mismatch"
    assert res["delta"] == -10_000.0


def test_alpaca_unavailable_is_unavailable_never_an_exception():
    res = _build_equity_reconciliation(
        equity=None, cash=None, net_mv=None, tolerance=TOLERANCE, position_count=0,
    )
    assert res["status"] == "unavailable"
    assert res["delta"] is None


def test_partial_none_inputs_also_unavailable():
    """Alpaca returning SOME but not all fields must not be misread as a
    real (and possibly huge, spurious) delta."""
    res = _build_equity_reconciliation(
        equity=100_000.0, cash=None, net_mv=80_000.0, tolerance=TOLERANCE, position_count=5,
    )
    assert res["status"] == "unavailable"
    assert res["delta"] is None
