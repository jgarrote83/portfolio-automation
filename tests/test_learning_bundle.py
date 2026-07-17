"""Learning Loop v1.0 bundle-builder pure logic tests (src/learning/bundle.py).
Run: PYTHONPATH=src pytest tests/test_learning_bundle.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import learning.bundle as bundle  # noqa: E402
from learning.bundle import (  # noqa: E402
    _fit_reports_to_budget,
    _split_followups_open,
    fetch_override_history,
)


# --- _fit_reports_to_budget: oldest-first drop order ----------------------------------

def test_all_reports_kept_when_under_budget():
    reports = [("2026-06-01", "x" * 40), ("2026-06-15", "y" * 40)]
    kept, stats = _fit_reports_to_budget(reports, fixed_tokens=10, max_tokens=1000)
    assert kept == reports
    assert stats["reports_dropped"] == 0
    assert stats["over_budget"] is False


def test_oldest_reports_drop_first():
    reports = [("2026-06-01", "x" * 400), ("2026-06-15", "y" * 400), ("2026-07-01", "z" * 400)]
    kept, stats = _fit_reports_to_budget(reports, fixed_tokens=50, max_tokens=150)
    assert [d for d, _ in kept] == ["2026-07-01"]
    assert stats["dropped_dates"] == ["2026-06-01", "2026-06-15"]
    assert stats["reports_kept"] == 1
    assert stats["reports_dropped"] == 2
    assert stats["reports_total"] == 3


def test_stats_reflect_no_drops_when_fits():
    reports = [("2026-07-01", "z" * 40)]
    kept, stats = _fit_reports_to_budget(reports, fixed_tokens=10, max_tokens=1000)
    assert stats["reports_dropped"] == 0
    assert stats["dropped_dates"] == []
    assert stats["total_tokens_est"] == 10 + len("z" * 40) // 4


def test_fixed_tokens_alone_over_budget_drops_everything():
    """Graded records + config (fixed_tokens) never drop themselves, but if they
    alone already exceed budget, every report is dropped (reports are the ONLY
    truncatable section)."""
    reports = [("2026-07-01", "z" * 40)]
    kept, stats = _fit_reports_to_budget(reports, fixed_tokens=10_000, max_tokens=100)
    assert kept == []
    assert stats["reports_dropped"] == 1
    assert stats["over_budget"] is True  # fixed_tokens alone already exceeds max_tokens


def test_empty_reports_list():
    kept, stats = _fit_reports_to_budget([], fixed_tokens=10, max_tokens=1000)
    assert kept == []
    assert stats["reports_total"] == 0
    assert stats["over_budget"] is False


# --- _split_followups_open --------------------------------------------------------

def test_split_followups_extracts_open_only():
    text = "intro\n## Open\nitem1\nitem2\n## Done\nold stuff\nmore old\n"
    result = _split_followups_open(text)
    assert result == "## Open\nitem1\nitem2"
    assert "old stuff" not in result


def test_split_followups_missing_headings_falls_back_to_full_text():
    text = "no headings here at all\n"
    assert _split_followups_open(text) == text


def test_split_followups_done_before_open_falls_back():
    text = "## Done\nold\n## Open\nnew\n"  # Done appears first -- malformed, fail open
    assert _split_followups_open(text) == text


# --- fetch_override_history: _direction_suspect annotation (Task G-b) ----------------

def test_pre_e1_accepted_row_flagged_suspect(monkeypatch):
    """A pre-Task-E1 accepted override never had `declared_direction` at all —
    the field didn't exist in the code until 2026-07-15."""
    monkeypatch.setattr(bundle, "query_entities", lambda table: [
        {"layer": "override", "outcome": "accepted", "sleeve": "GLD", "direction": "re_risk"},
    ])
    rows = fetch_override_history()
    assert rows[0]["_direction_suspect"] is True


def test_post_e1_accepted_row_with_declared_direction_not_suspect(monkeypatch):
    monkeypatch.setattr(bundle, "query_entities", lambda table: [
        {"layer": "override", "outcome": "accepted", "sleeve": "GLD",
         "direction": "de_risk", "declared_direction": "de_risk"},
    ])
    rows = fetch_override_history()
    assert rows[0]["_direction_suspect"] is False


def test_post_e1_disagreement_still_not_suspect(monkeypatch):
    """A post-E1 row where the model's declared direction disagreed with the
    derived one is NOT suspect — E1 already corrected it and flagged the
    disagreement; both fields are populated, so it's trustworthy."""
    monkeypatch.setattr(bundle, "query_entities", lambda table: [
        {"layer": "override", "outcome": "downsized", "sleeve": "XLP",
         "direction": "de_risk", "declared_direction": "re_risk"},
    ])
    rows = fetch_override_history()
    assert rows[0]["_direction_suspect"] is False


def test_rejected_row_never_flagged(monkeypatch):
    monkeypatch.setattr(bundle, "query_entities", lambda table: [
        {"layer": "override", "outcome": "rejected", "sleeve": "TLT"},
    ])
    rows = fetch_override_history()
    assert "_direction_suspect" not in rows[0]


def test_non_override_layer_rows_untouched(monkeypatch):
    monkeypatch.setattr(bundle, "query_entities", lambda table: [
        {"layer": "sleeve_switch", "outcome": "accepted", "sleeve": "SEMIS"},
        {"layer": "regime_suspect", "sleeve": "Q3"},
    ])
    rows = fetch_override_history()
    assert all("_direction_suspect" not in r for r in rows)
