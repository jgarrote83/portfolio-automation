"""PR #41 M-B remediation — grader/calibration isolation between the two
`ThematicHistory` tracks (`path: "core_thematic"` vs `path: "flex_conviction"`).

Pre-fix, `_stamp_thematic_outcomes` queried `ThematicHistory` with NO path
filter and derived horizons from `filed_date + {30,60,90}` unconditionally —
so a `flex_conviction` row (which sets `outcome_status: ""` identically to a
core row) was graded by the CORE stamper at the wrong horizon, marked
`outcome_status: "resolved"` before the flex stamper ever saw it, and its
`actual_up_60d` polluted `_build_thematic_calibration`'s core Brier track.
`_build_thematic_calibration` had the same defect (no path filter on its
`outcome_status eq 'resolved'` query).

This file provides a FILTER-AWARE fake `query_entities` (unlike
`test_thematic_grading.py`'s filter-ignoring fakes, which test calibration
math, not isolation) so the fix's actual `path eq '...'` scoping is exercised,
not just its calibration arithmetic. It also proves the backfill migration
handles legacy pre-`path` rows without silently dropping them.

Run: PYTHONPATH=src pytest tests/test_thematic_history_path_isolation.py
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import collector.handler as ch  # noqa: E402

TODAY = date(2026, 9, 20)
FILED = (TODAY - timedelta(days=65)).isoformat()
_HZ = {h: (date.fromisoformat(FILED) + timedelta(days=h)).isoformat() for h in (30, 60, 90)}


def _core_row(**kw):
    row = {
        "PartitionKey": "2026-08", "RowKey": "THM-20260101-000",
        "symbol": "VDE", "filed_date": FILED, "p_up": 0.6,
        "path": "core_thematic", "outcome_status": "",
        "horizon_30d": _HZ[30], "horizon_60d": _HZ[60], "horizon_90d": _HZ[90],
    }
    row.update(kw)
    return row


def _flex_row(**kw):
    row = {
        "PartitionKey": "2026-08", "RowKey": "FLEXCV-20260101-000",
        "symbol": "AVGO", "filed_date": FILED, "p_up": 0.6, "horizon_days": 21,
        "path": "flex_conviction", "outcome_status": "",
    }
    row.update(kw)
    return row


def _legacy_row_no_path(**kw):
    row = {
        "PartitionKey": "2026-07", "RowKey": "THM-20260601-000",
        "symbol": "GLD", "filed_date": FILED, "p_up": 0.55, "outcome_status": "",
        "horizon_30d": _HZ[30], "horizon_60d": _HZ[60], "horizon_90d": _HZ[90],
    }
    row.update(kw)
    return row


class _FakeStore:
    """Filter-aware in-memory ThematicHistory double: parses the exact
    literal `path eq '...'`/`outcome_status eq '...'` clauses this codebase's
    query strings actually use (not a general OData parser)."""

    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]

    def _matches(self, row, filt):
        if not filt:
            return True
        ok = True
        if "path eq 'core_thematic'" in filt:
            ok = ok and row.get("path") == "core_thematic"
        if "path eq 'flex_conviction'" in filt:
            ok = ok and row.get("path") == "flex_conviction"
        if "outcome_status eq 'resolved'" in filt:
            ok = ok and row.get("outcome_status") == "resolved"
        return ok

    def query_entities(self, table, filt=None):
        return [r for r in self.rows if self._matches(r, filt)]

    def upsert_entity(self, table, entity):
        for r in self.rows:
            if r["PartitionKey"] == entity["PartitionKey"] and r["RowKey"] == entity["RowKey"]:
                r.update(entity)
                return
        self.rows.append(dict(entity))

    def by_key(self, row_key):
        return next(r for r in self.rows if r["RowKey"] == row_key)


def _perf_series():
    return [
        {"date": FILED, "closes": {"VDE": 100.0, "AVGO": 200.0, "GLD": 150.0, "SPY": 500.0}},
        {"date": TODAY.isoformat(), "closes": {"VDE": 110.0, "AVGO": 220.0, "GLD": 160.0, "SPY": 510.0}},
    ]


def _patch_common(monkeypatch, store):
    monkeypatch.setattr(ch, "query_entities", store.query_entities)
    monkeypatch.setattr(ch, "upsert_entity", store.upsert_entity)
    monkeypatch.setattr(ch, "read_perf_series", _perf_series)

    class _FrozenDate(date):
        @classmethod
        def today(cls):
            return TODAY

    monkeypatch.setattr(ch, "date", _FrozenDate)


def test_each_row_graded_by_exactly_one_stamper_at_its_own_horizon(monkeypatch):
    store = _FakeStore([_core_row(), _flex_row()])
    _patch_common(monkeypatch, store)

    ch._backfill_thematic_history_path()
    ch._stamp_thematic_outcomes(fmp=None)
    ch._stamp_flex_conviction_outcomes(fmp=None)

    core = store.by_key("THM-20260101-000")
    flex = store.by_key("FLEXCV-20260101-000")

    # Core row graded by the core stamper at 30/60d, never touched by flex fields.
    assert core["outcome_status"] == "resolved"
    assert "actual_up_60d" in core
    assert "actual_up" not in core  # the flex stamper's own field name

    # Flex row graded by the flex stamper at its OWN horizon_days (21d), never
    # by the core 30/60/90 ladder.
    assert flex["outcome_status"] == "resolved"
    assert "actual_up" in flex
    assert "actual_up_60d" not in flex
    assert "actual_up_30d" not in flex


def test_core_calibration_sample_size_excludes_flex_row(monkeypatch):
    store = _FakeStore([_core_row(), _flex_row()])
    _patch_common(monkeypatch, store)
    ch._backfill_thematic_history_path()
    ch._stamp_thematic_outcomes(fmp=None)
    ch._stamp_flex_conviction_outcomes(fmp=None)

    core_cal = ch._build_thematic_calibration(ch._RISK_LIMITS_DEFAULTS)
    assert core_cal["sample_size"] == 1


def test_flex_calibration_sample_size_excludes_core_row(monkeypatch):
    store = _FakeStore([_core_row(), _flex_row()])
    _patch_common(monkeypatch, store)
    ch._backfill_thematic_history_path()
    ch._stamp_thematic_outcomes(fmp=None)
    ch._stamp_flex_conviction_outcomes(fmp=None)

    flex_cal = ch._build_flex_conviction_calibration(ch._RISK_LIMITS_DEFAULTS)
    assert flex_cal["sample_size"] == 1


def test_legacy_row_with_no_path_field_lands_in_core_track_after_backfill(monkeypatch):
    store = _FakeStore([_legacy_row_no_path()])
    _patch_common(monkeypatch, store)

    backfilled = ch._backfill_thematic_history_path()
    assert backfilled == 1
    assert store.by_key("THM-20260601-000")["path"] == "core_thematic"

    ch._stamp_thematic_outcomes(fmp=None)
    legacy = store.by_key("THM-20260601-000")
    assert legacy["outcome_status"] == "resolved"
    assert "actual_up_60d" in legacy

    core_cal = ch._build_thematic_calibration(ch._RISK_LIMITS_DEFAULTS)
    assert core_cal["sample_size"] == 1


def test_backfill_is_a_cheap_noop_on_second_run(monkeypatch):
    store = _FakeStore([_legacy_row_no_path()])
    _patch_common(monkeypatch, store)
    first = ch._backfill_thematic_history_path()
    second = ch._backfill_thematic_history_path()
    assert first == 1
    assert second == 0


def test_flex_row_never_reaches_core_stamper_query_at_all(monkeypatch):
    """Belt: the path filter alone should already exclude it from the query
    results the core stamper iterates."""
    store = _FakeStore([_core_row(), _flex_row()])
    _patch_common(monkeypatch, store)
    ch._backfill_thematic_history_path()
    rows = ch.query_entities("ThematicHistory", "path eq 'core_thematic'")
    assert [r["RowKey"] for r in rows] == ["THM-20260101-000"]


def test_flex_row_structurally_ungradeable_even_if_path_filter_were_bypassed(monkeypatch):
    """Braces: even a flex-shaped row erroneously tagged core_thematic (no
    horizon_Nd fields) must not be gradeable by the core stamper."""
    mislabeled = _flex_row(path="core_thematic")
    store = _FakeStore([mislabeled])
    _patch_common(monkeypatch, store)
    ch._stamp_thematic_outcomes(fmp=None)
    row = store.by_key("FLEXCV-20260101-000")
    # Never marked resolved by the core stamper -- it has no horizon_Nd fields.
    assert row.get("outcome_status") in ("", "indeterminate_data") or row.get("outcome_status") is None
    assert "actual_up_60d" not in row
