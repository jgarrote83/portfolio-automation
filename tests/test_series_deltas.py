"""Unit tests for `_build_series_deltas` (session 2026-07-17, Task E).

Hardens F1 catalyst adjudication: 07-17 adjudicated a CPI flag against the wrong
prior report and the wrong prior value (named 07-14's value while claiming it was
the immediately-prior report's). This block reads back the prior trading day's
snapshot so every cadence/new-print statement is data, not the model's recollection.

Run: PYTHONPATH=src pytest tests/test_series_deltas.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import collector.handler as ch  # noqa: E402
from collector.handler import _build_series_deltas  # noqa: E402


def _macro(cpi_value, cpi_date, gdpnow_value=None, gdpnow_date=None):
    data = {"CPILFESL": [{"date": cpi_date, "value": cpi_value}]}
    if gdpnow_value is not None:
        data["GDPNOW"] = [{"date": gdpnow_date, "value": gdpnow_value}]
    return data


def test_no_prior_snapshot_within_window(monkeypatch):
    monkeypatch.setattr(ch, "read_snapshot", lambda d: None)
    result = _build_series_deltas(_macro("2.81", "2026-06-01"), "2026-07-17")
    assert result["available"] is False
    assert "no prior snapshot" in result["reason"]


def test_looks_back_multiple_days_when_yesterday_missing(monkeypatch):
    def _read(d):
        if d == "2026-07-15":
            return {"macro": {"data": _macro("2.81", "2026-06-01")}}
        return None

    monkeypatch.setattr(ch, "read_snapshot", _read)
    result = _build_series_deltas(_macro("2.81", "2026-06-01"), "2026-07-17")
    assert result["available"] is True
    assert result["prior_date"] == "2026-07-15"


def test_unchanged_value_and_as_of_is_not_a_new_print(monkeypatch):
    def _read(d):
        if d == "2026-07-16":
            return {"macro": {"data": _macro("2.81", "2026-06-01")}}
        return None

    monkeypatch.setattr(ch, "read_snapshot", _read)
    result = _build_series_deltas(_macro("2.81", "2026-06-01"), "2026-07-17")
    cpi = result["series"]["CPILFESL"]
    assert cpi["value"] == 2.81
    assert cpi["prior_value"] == 2.81
    assert cpi["delta"] == 0.0
    assert cpi["new_print"] is False


def test_value_change_flags_new_print_with_correct_delta(monkeypatch):
    def _read(d):
        if d == "2026-07-16":
            return {"macro": {"data": _macro("2.81", "2026-07-01")}}
        return None

    monkeypatch.setattr(ch, "read_snapshot", _read)
    result = _build_series_deltas(_macro("2.96", "2026-08-01"), "2026-07-17")
    cpi = result["series"]["CPILFESL"]
    assert cpi["value"] == 2.96
    assert cpi["prior_value"] == 2.81
    assert cpi["delta"] == 0.15
    assert cpi["new_print"] is True
    assert cpi["as_of"] == "2026-08-01"
    assert cpi["prior_as_of"] == "2026-07-01"


def test_as_of_change_alone_still_flags_new_print(monkeypatch):
    """Task F1's rule: an unchanged VALUE with a bumped as_of date still means new
    data landed — must not be called 'no new print'."""
    def _read(d):
        if d == "2026-07-16":
            return {"macro": {"data": _macro("2.81", "2026-06-01")}}
        return None

    monkeypatch.setattr(ch, "read_snapshot", _read)
    result = _build_series_deltas(_macro("2.81", "2026-07-01"), "2026-07-17")
    cpi = result["series"]["CPILFESL"]
    assert cpi["value"] == cpi["prior_value"] == 2.81
    assert cpi["as_of"] != cpi["prior_as_of"]
    assert cpi["new_print"] is True
    assert cpi["delta"] == 0.0


def test_series_absent_from_prior_snapshot_has_null_prior_and_no_false_new_print(monkeypatch):
    def _read(d):
        if d == "2026-07-16":
            return {"macro": {"data": {}}}   # GDPNOW not present in the prior snapshot
        return None

    monkeypatch.setattr(ch, "read_snapshot", _read)
    result = _build_series_deltas(
        _macro("2.81", "2026-06-01", gdpnow_value="2.4", gdpnow_date="2026-07-15"),
        "2026-07-17",
    )
    gdpnow = result["series"]["GDPNOW"]
    assert gdpnow["value"] == 2.4
    assert gdpnow["prior_value"] is None
    assert gdpnow["prior_as_of"] is None
    assert gdpnow["delta"] is None
    assert gdpnow["new_print"] is False


def test_missing_value_marker_treated_as_none(monkeypatch):
    def _read(d):
        if d == "2026-07-16":
            return {"macro": {"data": {"CPILFESL": [{"date": "2026-06-01", "value": "."}]}}}
        return None

    monkeypatch.setattr(ch, "read_snapshot", _read)
    result = _build_series_deltas(_macro("2.81", "2026-07-01"), "2026-07-17")
    cpi = result["series"]["CPILFESL"]
    assert cpi["prior_value"] is None
    assert cpi["delta"] is None


def test_untracked_series_not_included(monkeypatch):
    def _read(d):
        if d == "2026-07-16":
            return {"macro": {"data": {}}}
        return None

    monkeypatch.setattr(ch, "read_snapshot", _read)
    macro = {"SOME_UNTRACKED_SERIES": [{"date": "2026-07-17", "value": "1.0"}]}
    result = _build_series_deltas(macro, "2026-07-17")
    assert "SOME_UNTRACKED_SERIES" not in result["series"]
