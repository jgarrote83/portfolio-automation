"""Learning Loop v1.0 reviewer handler tests (src/learning/handler.py).
Pure-gate tests need no mocking; run_cycle tests mock all I/O (storage,
Foundry) via monkeypatch. Run: PYTHONPATH=src pytest tests/test_learning_handler.py
"""
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import learning.handler as lh  # noqa: E402


# --- first-Saturday timer gate ------------------------------------------------------

def test_first_saturday_true_for_day_1_to_7():
    found = False
    for day in range(1, 8):
        d = date(2026, 8, day)
        if d.weekday() == 5:
            assert lh.is_first_saturday(d) is True
            found = True
    assert found  # sanity: 2026-08 does have a Saturday in the first week


def test_first_saturday_false_for_later_saturdays():
    later = [date(2026, 8, 8), date(2026, 8, 15), date(2026, 8, 22), date(2026, 8, 29)]
    for d in later:
        assert d.weekday() == 5
        assert lh.is_first_saturday(d) is False


def test_first_saturday_false_on_non_saturday():
    assert lh.is_first_saturday(date(2026, 8, 3)) is False  # a Monday


# --- skip rule: <15 trading sessions -> observation-only -----------------------------

def test_count_sessions_since_counts_reports_in_range():
    reports = [f"2026-07-{d:02d}" for d in range(1, 20)]
    n = lh.count_sessions_since("2026-07-05", date(2026, 7, 19), reports)
    assert n == 14  # 07-06 through 07-19 inclusive


def test_count_sessions_since_no_prior_cycle_never_triggers_observation_only():
    reports = ["2026-07-01"]
    n = lh.count_sessions_since(None, date(2026, 7, 19), reports)
    assert n >= lh._MIN_SESSIONS_FOR_FULL_MODE


def test_is_observation_only_below_threshold():
    reports = [f"2026-07-{d:02d}" for d in range(1, 20)]
    assert lh.is_observation_only("2026-07-18", date(2026, 7, 19), reports) is True


def test_is_observation_only_above_threshold():
    reports = [f"2026-06-{d:02d}" for d in range(1, 31)] + [f"2026-07-{d:02d}" for d in range(1, 20)]
    assert lh.is_observation_only("2026-06-01", date(2026, 7, 19), reports) is False


def test_is_observation_only_no_prior_cycle():
    assert lh.is_observation_only(None, date(2026, 7, 19), []) is False


# --- forced re-review: due amendment computation --------------------------------------

def test_due_amendment_ids_finds_past_review_by():
    history = [{"layer": "amendment", "proposal_id": "AMD-2026-06-01", "review_by": "2026-07-01"}]
    assert lh.due_amendment_ids(history, [], date(2026, 7, 19)) == ["AMD-2026-06-01"]


def test_due_amendment_ids_excludes_future_review_by():
    history = [{"layer": "amendment", "proposal_id": "AMD-2026-06-01", "review_by": "2026-08-01"}]
    assert lh.due_amendment_ids(history, [], date(2026, 7, 19)) == []


def test_due_amendment_ids_excludes_already_reviewed():
    history = [{"layer": "amendment", "proposal_id": "AMD-2026-06-01", "review_by": "2026-07-01"}]
    cycles = [{"proposals": [{"re_review_of": "AMD-2026-06-01"}]}]
    assert lh.due_amendment_ids(history, cycles, date(2026, 7, 19)) == []


def test_due_amendment_ids_ignores_non_amendment_layers():
    history = [{"layer": "override", "proposal_id": "OV-1", "review_by": "2026-07-01"}]
    assert lh.due_amendment_ids(history, [], date(2026, 7, 19)) == []


# --- run_cycle: I/O fully mocked -----------------------------------------------------

class _FakeFoundryClient:
    def __init__(self, raw_output, ready=True):
        self.raw_output = raw_output
        self.ready = ready

    def complete(self, **kwargs):
        return self.raw_output


class _FakePromptFile:
    def read_text(self, encoding=None):
        return "system prompt"


def _patch_common(monkeypatch, raw_output):
    bundle = {
        "as_of": "2026-07-19", "diff_base_sha": "abc123",
        "daily_reports": [], "trade_history": [], "override_history": [],
        "live_config": {"src/config/risk-limits.json": "A: 1\n"},
        "followups_open": "## Open\n", "learning_history": {"cycles": [], "proposals": []},
        "performance": {}, "quadrant_performance": {},
        "bundle_stats": {"reports_kept": 0},
    }
    monkeypatch.setattr(lh, "_list_report_dates", lambda: [])
    monkeypatch.setattr(lh, "_last_completed_cycle_date", lambda: None)
    monkeypatch.setattr(lh, "build_bundle", lambda **kw: bundle)
    monkeypatch.setattr(lh, "load_secrets", lambda: {"FoundryApiKey": "fake-key"})
    monkeypatch.setattr(lh, "FoundryClient", lambda api_key, model: _FakeFoundryClient(raw_output))
    monkeypatch.setattr(lh, "_REVIEW_PROMPT_FILE", _FakePromptFile())

    written_blobs: dict = {}
    upserted: dict = {"LearningCycles": [], "LearningProposals": []}

    monkeypatch.setattr(lh, "write_json_blob", lambda container, name, obj: written_blobs.__setitem__((container, name), obj))
    monkeypatch.setattr(lh, "upsert_entity", lambda table, entity: upserted.setdefault(table, []).append(entity))
    return written_blobs, upserted


def test_failed_validation_cycle_preserves_raw_output_and_writes_no_proposals(monkeypatch):
    written_blobs, upserted = _patch_common(monkeypatch, raw_output="not valid json{")
    result = lh.run_cycle(trigger="manual", date_str="2026-07-19")

    assert result["status"] == "failed_validation"
    assert upserted["LearningProposals"] == []  # zero pending rows surfaced

    blob = written_blobs[("learning", "proposals/2026-07-19.json")]
    assert blob["status"] == "failed_validation"
    assert blob["raw_output"] == "not valid json{"

    cycle_rows = upserted["LearningCycles"]
    assert len(cycle_rows) == 1
    assert cycle_rows[0]["status"] == "failed_validation"


def test_completed_cycle_writes_proposal_rows(monkeypatch):
    doc = {
        "narrative": "ok", "mode": "full",
        "proposals": [{
            "id": "AMD-2026-07-19", "class": 0, "title": "t",
            "change_summary": "cs", "data_summary": "ds n=1",
            "expected_effect": "x", "falsifier": "y", "review_by": "2027-01-01",
            "evidence": [],
        }],
    }
    written_blobs, upserted = _patch_common(monkeypatch, raw_output=json.dumps(doc))
    result = lh.run_cycle(trigger="timer", date_str="2026-07-19")

    assert result["status"] == "completed"
    assert len(upserted["LearningProposals"]) == 1
    assert upserted["LearningProposals"][0]["RowKey"] == "AMD-2026-07-19"
    assert upserted["LearningProposals"][0]["status"] == "pending"
    blob = written_blobs[("learning", "proposals/2026-07-19.json")]
    assert blob["status"] == "completed"
