"""Task D/E (2026-08-10, catalyst-sleeve-funnel) — regime_fit_score, the graded
regime-fit reading the catalyst_score composite consumes as a weighted input
(never a veto — flex_separation_set remains the only absolute gate).

Run: PYTHONPATH=src pytest tests/test_regime_fit_score.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flex.regime import regime_fit_score  # noqa: E402


def test_fit_under_pinned_active_quadrant_scores_full():
    assert regime_fit_score("Technology", "Q1", "active") == 1.0


def test_fit_under_borderline_tiebreak_scores_partial():
    assert regime_fit_score("Consumer Defensive", "Q3", "borderline_5d_tiebreak") == 0.6


def test_fit_under_favored_single_scores_partial():
    assert regime_fit_score("Utilities", "Q4", "favored_single") == 0.6


def test_recognized_sector_not_fitting_scores_zero_not_absent():
    # A real negative reading — Technology does not fit Q3 — must stay
    # distinguishable from "no regime read at all" (None).
    assert regime_fit_score("Technology", "Q3", "active") == 0.0


def test_unresolved_quadrant_is_absent_not_zero():
    assert regime_fit_score("Technology", "", "unresolved") is None
    assert regime_fit_score("Technology", None, None) is None


def test_unrecognized_sector_is_absent_not_zero():
    assert regime_fit_score("Cryptocurrency Mining", "Q1", "active") is None
    assert regime_fit_score(None, "Q1", "active") is None
