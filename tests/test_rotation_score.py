"""Unit tests for the rotation-score rounding seam (collector handler).

Task 1 (2026-07-09 audit): the composite Rotation Score is displayed rounded to
1dp, but the category used to be bucketed on the UNROUNDED weighted score — so a
weighted 3.049 displayed as 3.0 (rubric: "us_leadership_intact") yet was labelled
"transition_window". `_rotation_composite_category` rounds first, then buckets the
rounded value, so the number and the label can never disagree. Run:
    PYTHONPATH=src pytest tests/test_rotation_score.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _rotation_composite_category  # noqa: E402


def test_weighted_3049_rounds_down_and_stays_us_leadership():
    # The 2026-07-09 seam: weighted just over 3 but rounds to 3.0.
    composite, category = _rotation_composite_category(3.049)
    assert composite == 3.0
    assert category == "us_leadership_intact"


def test_weighted_306_rounds_up_into_transition_window():
    composite, category = _rotation_composite_category(3.06)
    assert composite == 3.1
    assert category == "transition_window"


def test_exact_boundary_3_stays_us_leadership():
    composite, category = _rotation_composite_category(3.0)
    assert composite == 3.0
    assert category == "us_leadership_intact"


def test_boundary_6_stays_transition_window():
    composite, category = _rotation_composite_category(6.0)
    assert composite == 6.0
    assert category == "transition_window"


def test_high_score_is_rotation_underway():
    composite, category = _rotation_composite_category(7.42)
    assert composite == 7.4
    assert category == "rotation_underway"
