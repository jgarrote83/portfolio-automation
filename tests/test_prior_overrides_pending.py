"""Unit tests for _build_prior_overrides_pending (2026-08-06 audit M1).

Closes the seam where 08-04 filed a de-risk TLT hold with a dated falsifier
and 08-05 sold TLT to the floor without ever adjudicating whether that
falsifier had actually fired — same sleeve, opposite action, consecutive
days, no engagement with the record it had just filed.

Run: PYTHONPATH=src pytest tests/test_prior_overrides_pending.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import collector.handler as handler  # noqa: E402

TODAY = "2026-08-05"


def _row(sleeve, outcome="accepted", filed="2026-08-04", falsifier="",
         falsifier_date="2026-08-20", outcome_status="", layer="override"):
    return {
        "sleeve": sleeve, "outcome": outcome, "recommended_at": filed,
        "falsifier": falsifier, "falsifier_date": falsifier_date,
        "outcome_status": outcome_status, "direction": "de_risk", "layer": layer,
    }


def test_pending_override_falsifier_not_met(monkeypatch):
    """08-04 filed TLT hold, falsifier requires inflation falling 5+ runs and
    growth rising 3+ vintages; 08-05's inflation raw_streak is only 4 ->
    falsifier_met False."""
    monkeypatch.setattr(handler, "query_entities", lambda table, *a, **kw: [
        _row("TLT", falsifier="inflation falling 5+ runs AND growth rising 3+ vintages"),
    ])
    growth_axis = {"raw_direction": "rising", "raw_streak": 3}
    inflation_axis = {"raw_direction": "falling", "raw_streak": 4}
    result = handler._build_prior_overrides_pending(TODAY, growth_axis, inflation_axis, {})
    assert len(result) == 1
    assert result[0]["sleeve"] == "TLT"
    assert result[0]["falsifier_met"] is False
    assert result[0]["current_axis_state"]["inflation"]["raw_streak"] == 4


def test_pending_override_falsifier_met(monkeypatch):
    monkeypatch.setattr(handler, "query_entities", lambda table, *a, **kw: [
        _row("TLT", falsifier="inflation falling 5+ runs AND growth rising 3+ vintages"),
    ])
    growth_axis = {"raw_direction": "rising", "raw_streak": 4}
    inflation_axis = {"raw_direction": "falling", "raw_streak": 5}
    result = handler._build_prior_overrides_pending(TODAY, growth_axis, inflation_axis, {})
    assert result[0]["falsifier_met"] is True


def test_rejected_override_never_pending(monkeypatch):
    """A rejected override authorizes nothing — never surfaced as pending."""
    monkeypatch.setattr(handler, "query_entities", lambda table, *a, **kw: [
        _row("TLT", outcome="rejected"),
    ])
    result = handler._build_prior_overrides_pending(TODAY, {}, {}, {})
    assert result == []


def test_already_graded_override_not_pending(monkeypatch):
    """Phase 5 already stamped an outcome_status -> resolved, not pending."""
    monkeypatch.setattr(handler, "query_entities", lambda table, *a, **kw: [
        _row("TLT", outcome_status="held_correct"),
    ])
    result = handler._build_prior_overrides_pending(TODAY, {}, {}, {})
    assert result == []


def test_stale_override_outside_lookback_not_pending(monkeypatch):
    monkeypatch.setattr(handler, "query_entities", lambda table, *a, **kw: [
        _row("TLT", filed="2026-01-01"),
    ])
    result = handler._build_prior_overrides_pending(TODAY, {}, {}, {})
    assert result == []


def test_todays_own_override_not_pending(monkeypatch):
    """An override filed THIS session isn't a "prior" one yet."""
    monkeypatch.setattr(handler, "query_entities", lambda table, *a, **kw: [
        _row("TLT", filed=TODAY),
    ])
    result = handler._build_prior_overrides_pending(TODAY, {}, {}, {})
    assert result == []


def test_query_failure_is_non_fatal(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("table unavailable")
    monkeypatch.setattr(handler, "query_entities", _boom)
    # The caller in run() wraps this in try/except; call it directly here to
    # confirm the failure propagates cleanly (non-fatal is enforced by the
    # caller, not silently swallowed inside this pure-ish query function).
    try:
        handler._build_prior_overrides_pending(TODAY, {}, {}, {})
        raised = False
    except RuntimeError:
        raised = True
    assert raised
