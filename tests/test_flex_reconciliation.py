"""Unit tests for the flex engine-vs-broker reconciliation guard (collector).

Task 4 (2026-07-09 MU incident): the flex ledger can drift from the broker (a lost
or never-persisted ledger row orphans an open position — engine `held=[]`, `exits=[]`,
yet the paper account still holds MU). `_build_flex_reconciliation` compares the
engine's ledger-derived `held` against the broker's OFF-CORE-ROSTER positions; the
paper account is canonical, so any disagreement is a `mismatch`. Run:
    PYTHONPATH=src pytest tests/test_flex_reconciliation.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_flex_reconciliation  # noqa: E402


def _pa(*positions):
    return {"positions": [{"ticker": t, "qty": q} for t, q in positions]}


def test_engine_empty_broker_holds_off_roster_is_mismatch():
    # The MU case: engine forgot the position, broker still holds it.
    res = _build_flex_reconciliation({"held": []}, _pa(("MU", 2)))
    assert res["status"] == "mismatch"
    assert res["engine_held"] == []
    assert res["broker_held"] == ["MU"]


def test_both_agree_is_ok():
    res = _build_flex_reconciliation({"held": ["MU"]}, _pa(("MU", 2)))
    assert res["status"] == "ok"
    assert res["engine_held"] == ["MU"]
    assert res["broker_held"] == ["MU"]


def test_core_roster_positions_are_ignored():
    # SPY/GLD/AMZN are core (AMZN/GOOGL/INTC/MCK are in CORE_ROSTER) — not flex.
    res = _build_flex_reconciliation(
        {"held": []}, _pa(("SPY", 100), ("GLD", 50), ("AMZN", 10))
    )
    assert res["status"] == "ok"
    assert res["broker_held"] == []


def test_engine_holds_name_broker_lacks_is_mismatch():
    res = _build_flex_reconciliation({"held": ["MU"]}, _pa(("SPY", 100)))
    assert res["status"] == "mismatch"
    assert res["engine_held"] == ["MU"]
    assert res["broker_held"] == []


def test_zero_qty_broker_row_not_counted():
    res = _build_flex_reconciliation({"held": []}, _pa(("MU", 0)))
    assert res["status"] == "ok"
    assert res["broker_held"] == []


def test_missing_engine_state_still_flags_broker_orphan():
    # flex_state unavailable (no prior run) but broker holds a flex name → mismatch.
    res = _build_flex_reconciliation({"available": False}, _pa(("MU", 2)))
    assert res["status"] == "mismatch"
    assert res["broker_held"] == ["MU"]
