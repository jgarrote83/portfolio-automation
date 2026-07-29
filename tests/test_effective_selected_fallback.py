"""Unit tests for the analyzer's `effective_selected` failure-day fallback
(session 2026-07-28, Task E — hardening from the 07-28 merge audit).

Seam: `_snapshot_effective_selected` sourced the map ONLY from the snapshot's
`sleeve_selection.roles[]` — the block that is UNAVAILABLE exactly when the
scorecard build fails. On such a day the collector's reference still targets the
auto-switched incumbent (persisted-state fallback, PR #31 Change 1b), but the
validator ctx fell back to config — so mid-transition, a buy of the effective
incumbent would be REJECTED by V1.5 and the deselected old incumbent's floor
reinstated, for that one day. Fix: the collector writes a top-level snapshot key
`effective_selected`, populated even on a scorecard-failure day; the analyzer now
prefers it. Run:
    PYTHONPATH=src pytest tests/test_effective_selected_fallback.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer.handler import _build_reference_gaps, _snapshot_effective_selected  # noqa: E402


def test_top_level_key_used_when_sleeve_selection_unavailable():
    """Fails on pre-Task-E source: sleeve_selection.roles[] is empty (scorecard
    build failed that day), so the old implementation returns {} even though the
    top-level effective_selected key IS populated (the collector's persisted-state
    fallback)."""
    snapshot = {
        "sleeve_selection": {"available": False, "roles": []},
        "effective_selected": {"healthcare_def": "IHE", "semis": "SOXX"},
    }
    result = _snapshot_effective_selected(snapshot)
    assert result == {"healthcare_def": "IHE", "semis": "SOXX"}


def test_old_snapshot_shape_falls_back_to_roles_scan():
    """No top-level key (a snapshot predating this feature) — unchanged behavior:
    scan sleeve_selection.roles[] for each role's effective_selected field."""
    snapshot = {
        "sleeve_selection": {
            "available": True,
            "roles": [
                {"role_id": "healthcare_def", "effective_selected": "IHE"},
                {"role_id": "semis", "effective_selected": "SMH"},
            ],
        },
    }
    result = _snapshot_effective_selected(snapshot)
    assert result == {"healthcare_def": "IHE", "semis": "SMH"}


def test_neither_present_returns_empty_dict():
    assert _snapshot_effective_selected({}) == {}
    assert _snapshot_effective_selected({"effective_selected": {}}) == {}


def test_reference_gaps_ctx_carries_top_level_effective_selected_on_failure_day():
    """Integration: _build_reference_gaps's ctx (fed to the Tier-1 validator) must
    carry the top-level map even when sleeve_selection is unavailable that day."""
    snapshot = {
        "reference_weights": {"target_weights_pct": {"IHE": 10.0, "SGOV": 10.0}},
        "regime_gate": {"status": "closed"},
        "sleeve_selection": {"available": False, "roles": []},
        "effective_selected": {"healthcare_def": "IHE"},
        "paper_account": {
            "equity": 100_000.0, "cash": 5_000.0,
            "positions": [{"ticker": "IHE", "qty": 10, "market_value": 1_000.0,
                          "current_price": 100.0}],
        },
        "prices": {"IHE": {"c": 100.0}},
    }
    _, ctx = _build_reference_gaps(snapshot)
    assert ctx["effective_selected"] == {"healthcare_def": "IHE"}
