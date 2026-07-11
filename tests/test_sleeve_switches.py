"""Unit tests for Phase-C sleeve-switch / intl-leader-rotation recording + grading
(Task G — roster_revision_2026-07).

Applied role member switches (a human commits a new `selected`) and intl leader
rotations are written to OverrideHistory and graded vs the INCUMBENT counterfactual
(did the new member beat the one it replaced). Run:
    PYTHONPATH=src pytest tests/test_sleeve_switches.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_sleeve_switch_records, _grade_switch  # noqa: E402


def test_member_switch_recorded_when_selected_changes():
    prev = {"semis": {"selected": "SMH"}}
    new = {"semis": {"selected": "SOXX"}}
    recs = _build_sleeve_switch_records(prev, new, None, None, "2026-07-10")
    assert len(recs) == 1
    r = recs[0]
    assert r["layer"] == "sleeve_switch"
    assert r["incumbent"] == "SMH" and r["new_member"] == "SOXX"
    assert r["RowKey"] == "SW-20260710-semis"
    assert r["resolved_correct"] is None


def test_no_record_when_selected_unchanged():
    prev = {"semis": {"selected": "SMH"}}
    new = {"semis": {"selected": "SMH"}}
    assert _build_sleeve_switch_records(prev, new, None, None, "2026-07-10") == []


def test_no_record_on_first_run_without_prior_selected():
    # No prior selected recorded yet → not a switch.
    assert _build_sleeve_switch_records({}, {"semis": {"selected": "SMH"}}, None, None,
                                        "2026-07-10") == []


def test_intl_leader_rotation_recorded():
    recs = _build_sleeve_switch_records({}, {}, "AIA", "EWJ", "2026-07-10")
    assert len(recs) == 1
    r = recs[0]
    assert r["layer"] == "intl_leader_rotation"
    assert r["incumbent"] == "AIA" and r["new_member"] == "EWJ"
    assert r["RowKey"] == "ILR-20260710"


def test_no_rotation_record_when_leader_unchanged_or_none():
    assert _build_sleeve_switch_records({}, {}, "AIA", "AIA", "2026-07-10") == []
    assert _build_sleeve_switch_records({}, {}, None, "AIA", "2026-07-10") == []
    assert _build_sleeve_switch_records({}, {}, "AIA", None, "2026-07-10") == []


def test_grade_switch_correct_when_new_outperforms():
    g = _grade_switch(incumbent_ret_pct=5.0, new_ret_pct=8.0)
    assert g == {"resolved_correct": True, "excess_pp": 3.0}


def test_grade_switch_incorrect_when_new_underperforms():
    g = _grade_switch(incumbent_ret_pct=8.0, new_ret_pct=5.0)
    assert g["resolved_correct"] is False
    assert g["excess_pp"] == -3.0


def test_grade_switch_none_when_return_missing():
    assert _grade_switch(None, 5.0) is None
    assert _grade_switch(5.0, None) is None
