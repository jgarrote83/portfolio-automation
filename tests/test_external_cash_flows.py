"""Unit tests for external cash-flow detection (collector).

2026-08-21 measurement-integrity cycle, Task C: `_load_equity_spy_series`'s
own docstring asserts the normalized return line is valid because there are
"no external cash flows" -- but nothing ever enforced or checked that. A
deposit, withdrawal, or paper-account reset makes equity jump and the chart
renders that jump as return, with no warning anywhere. This is detect-and-
disclose only this cycle (chain-linking into a true TWR is a separate cycle,
decision gate F-3).

Activity type codes verified against Alpaca's own docs (2026-08-21, not
merely assumed from a suggested list): CSD = cash deposit(+), CSW = cash
withdrawal(-), JNLC = journal entry (cash). The dollar amount field on a
non-trade activity is `net_amount` (Alpaca's documented schema).

Run: PYTHONPATH=src pytest tests/test_external_cash_flows.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import collector.handler as ch  # noqa: E402
from collector.handler import (  # noqa: E402
    _build_external_flows,
    _classify_external_flows,
    _scan_external_flows,
)


# --- _classify_external_flows: pure activity-row extraction -----------------

def test_classify_extracts_cash_movement_types_only():
    raw = [
        {"activity_type": "FILL", "date": "2026-06-01", "net_amount": "100.00"},
        {"activity_type": "CSD", "date": "2026-06-02", "net_amount": "5000.00"},
        {"activity_type": "CSW", "date": "2026-06-03", "net_amount": "-1000.00"},
        {"activity_type": "JNLC", "date": "2026-06-04", "net_amount": "250.50"},
        {"activity_type": "DIV", "date": "2026-06-05", "net_amount": "1.23"},
    ]
    out = _classify_external_flows(raw)
    assert [f["type"] for f in out] == ["CSD", "CSW", "JNLC"]
    assert out[0] == {"date": "2026-06-02", "type": "CSD", "amount": 5000.0}
    assert out[1]["amount"] == -1000.0


def test_classify_sorts_by_date():
    raw = [
        {"activity_type": "CSW", "date": "2026-06-10", "net_amount": "-1.0"},
        {"activity_type": "CSD", "date": "2026-06-01", "net_amount": "1.0"},
    ]
    out = _classify_external_flows(raw)
    assert [f["date"] for f in out] == ["2026-06-01", "2026-06-10"]


def test_classify_empty_input():
    assert _classify_external_flows([]) == []


def test_classify_malformed_amount_never_raises():
    raw = [{"activity_type": "CSD", "date": "2026-06-01", "net_amount": "not-a-number"}]
    out = _classify_external_flows(raw)
    assert out[0]["amount"] is None


# --- _scan_external_flows: pure merge + integrity verdict --------------------

def test_no_flows_is_clean():
    result = _scan_external_flows([], [], "2026-08-21")
    assert result == {
        "available": True, "flows": [], "series_integrity": "clean",
        "checked_through": "2026-08-21",
    }


def test_a_deposit_mid_series_compromises_from_that_date():
    new_flows = [{"date": "2026-07-15", "type": "CSD", "amount": 5000.0}]
    result = _scan_external_flows([], new_flows, "2026-08-21")
    assert result["series_integrity"] == "compromised_from:2026-07-15"
    assert result["flows"] == new_flows


def test_earliest_flow_governs_compromised_from_date():
    prior = [{"date": "2026-07-15", "type": "CSD", "amount": 5000.0}]
    new = [{"date": "2026-06-01", "type": "CSW", "amount": -200.0}]  # earlier
    result = _scan_external_flows(prior, new, "2026-08-21")
    assert result["series_integrity"] == "compromised_from:2026-06-01"
    assert len(result["flows"]) == 2


def test_duplicate_flows_across_runs_not_double_counted():
    flow = {"date": "2026-07-15", "type": "CSD", "amount": 5000.0}
    result = _scan_external_flows([flow], [dict(flow)], "2026-08-21")
    assert result["flows"] == [flow]


def test_checked_through_reflects_this_run():
    result = _scan_external_flows([], [], "2026-08-21")
    assert result["checked_through"] == "2026-08-21"


# --- _build_external_flows: orchestration (fake Alpaca client + blob I/O) ---

class _FakeAlpaca:
    def __init__(self, activities_by_type=None, raise_on_call=False):
        self._by_type = activities_by_type or {}
        self._raise = raise_on_call
        self.calls: list[tuple] = []

    def get_activities(self, activity_type="FILL", after=None, **kw):
        self.calls.append((activity_type, after))
        if self._raise:
            raise RuntimeError("Alpaca activities endpoint unavailable")
        return self._by_type.get(activity_type, [])


def test_build_external_flows_clean_when_no_activity(monkeypatch):
    monkeypatch.setattr(ch, "read_json_blob", lambda c, n: None)
    written = {}
    monkeypatch.setattr(ch, "write_json_blob", lambda c, n, obj: written.update(obj=obj))
    alp = _FakeAlpaca()
    result = _build_external_flows(alp, "2026-08-21", "2026-05-26")
    assert result["available"] is True
    assert result["series_integrity"] == "clean"
    assert written["obj"]["flows"] == []


def test_build_external_flows_detects_a_deposit(monkeypatch):
    monkeypatch.setattr(ch, "read_json_blob", lambda c, n: None)
    written = {}
    monkeypatch.setattr(ch, "write_json_blob", lambda c, n, obj: written.update(obj=obj))
    alp = _FakeAlpaca({"CSD": [
        {"activity_type": "CSD", "date": "2026-07-01", "net_amount": "10000.00"},
    ]})
    result = _build_external_flows(alp, "2026-08-21", "2026-05-26")
    assert result["series_integrity"] == "compromised_from:2026-07-01"
    assert result["flows"][0]["amount"] == 10000.0


def test_build_external_flows_endpoint_failure_never_reports_false_clean(monkeypatch):
    monkeypatch.setattr(ch, "read_json_blob", lambda c, n: None)
    monkeypatch.setattr(
        ch, "write_json_blob",
        lambda c, n, obj: (_ for _ in ()).throw(AssertionError("must not persist on failure")),
    )
    alp = _FakeAlpaca(raise_on_call=True)
    result = _build_external_flows(alp, "2026-08-21", "2026-05-26")
    assert result["available"] is False
    assert result["series_integrity"] != "clean"  # never a false "clean"


def test_build_external_flows_historical_scan_runs_from_inception_once(monkeypatch):
    """First run (no persisted state) scans from the account's own
    inception, not merely 'since the last few days' -- the existing chart
    may already be compromised and nobody would know otherwise."""
    monkeypatch.setattr(ch, "read_json_blob", lambda c, n: None)
    monkeypatch.setattr(ch, "write_json_blob", lambda c, n, obj: None)
    alp = _FakeAlpaca()
    _build_external_flows(alp, "2026-08-21", "2026-05-26")
    assert all(after == "2026-05-26" for _typ, after in alp.calls)


def test_build_external_flows_second_run_scans_incrementally_not_from_inception(monkeypatch):
    """Once `checked_through` is persisted, a later run must query only
    NEW activity (after the last checked date), not re-scan the whole
    history every day."""
    monkeypatch.setattr(ch, "read_json_blob",
                         lambda c, n: {"checked_through": "2026-08-10", "flows": []})
    monkeypatch.setattr(ch, "write_json_blob", lambda c, n, obj: None)
    alp = _FakeAlpaca()
    _build_external_flows(alp, "2026-08-21", "2026-05-26")
    assert all(after == "2026-08-11" for _typ, after in alp.calls)


def test_build_external_flows_persists_result_for_next_run(monkeypatch):
    monkeypatch.setattr(ch, "read_json_blob", lambda c, n: None)
    written = {}
    monkeypatch.setattr(ch, "write_json_blob", lambda c, n, obj: written.update(container=c, name=n, obj=obj))
    alp = _FakeAlpaca({"CSW": [
        {"activity_type": "CSW", "date": "2026-08-20", "net_amount": "-500.0"},
    ]})
    _build_external_flows(alp, "2026-08-21", "2026-05-26")
    assert written["obj"]["checked_through"] == "2026-08-21"
    assert written["obj"]["flows"][0]["type"] == "CSW"
