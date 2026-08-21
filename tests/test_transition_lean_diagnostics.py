"""2026-08-21 quadrant-reachability audit, Task F (F6 fix) — the inert-lean
diagnostic. Diagnostic only this cycle (decision D-6): no allocation change,
no suppression — `_transition_lean_diagnostics` only surfaces whether the
projected concentrate is actually buyable under the CURRENT deployment gate.

Motivating probe (F6): with the gate closed, Q1's concentrate (SPY/QQQ/SMH)
is 100% amplifier-blocked (3/3), while Q2/Q3/Q4 (all dampers) are 0% blocked
— so the only lean the pre-Task-A system could ever generate from a
defensive realized quadrant (Q1, orthogonally adjacent) was structurally
unbuyable, corrupting the gap tables with an unreachable target.

Run: PYTHONPATH=src pytest tests/test_transition_lean_diagnostics.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _transition_lean_diagnostics  # noqa: E402


def test_projected_q1_gate_closed_fully_inert():
    d = _transition_lean_diagnostics("Q1", "closed", None)
    assert d["inert"] is True
    assert sorted(d["lean_blocked_names"]) == ["QQQ", "SMH", "SPY"]
    assert d["lean_deployable_fraction"] == 0.0
    assert d["lean_gate_status"] == "closed"


def test_projected_q2_gate_closed_fully_deployable():
    """Q2's concentrate (XLI/XLF/COWZ/VDE/PDBC/VTIP) are all dampers — none
    blocked by a closed gate."""
    d = _transition_lean_diagnostics("Q2", "closed", None)
    assert d["inert"] is False
    assert d["lean_blocked_names"] == []
    assert d["lean_deployable_fraction"] == 1.0


def test_projected_q3_gate_closed_fully_deployable():
    d = _transition_lean_diagnostics("Q3", "closed", None)
    assert d["inert"] is False
    assert d["lean_deployable_fraction"] == 1.0


def test_projected_q4_gate_closed_fully_deployable():
    d = _transition_lean_diagnostics("Q4", "closed", None)
    assert d["inert"] is False
    assert d["lean_deployable_fraction"] == 1.0


def test_gate_open_never_inert_for_any_quadrant():
    for q in ("Q1", "Q2", "Q3", "Q4"):
        d = _transition_lean_diagnostics(q, "open", None)
        assert d["inert"] is False, f"{q} unexpectedly inert with gate open"
        assert d["lean_blocked_names"] == []
        assert d["lean_deployable_fraction"] == 1.0


def test_no_projected_quadrant_never_inert():
    """No active lean (projected_quadrant is None) -> nothing to diagnose;
    never a false 'inert'."""
    d = _transition_lean_diagnostics(None, "closed", None)
    assert d["inert"] is False
    assert d["lean_blocked_names"] == []


def test_gate_status_echoed_verbatim():
    d = _transition_lean_diagnostics("Q1", "closed", None)
    assert d["lean_gate_status"] == "closed"
    d2 = _transition_lean_diagnostics("Q1", "open", None)
    assert d2["lean_gate_status"] == "open"
