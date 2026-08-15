"""Task F (2026-08-14 flex-conviction-path cycle) — `_write_flex_conviction_
history` (analyzer, WRITE ONLY): one ThematicHistory row per `path ==
"conviction"` entry in flex_nominations[], tagged `path: "flex_conviction"`
so it never blends with the core thematic-conviction calibration track.

Run: PYTHONPATH=src pytest tests/test_write_flex_conviction_history.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import analyzer.handler as ah  # noqa: E402


def test_writes_one_row_per_conviction_nomination(monkeypatch):
    captured = []
    monkeypatch.setattr(ah, "upsert_entity", lambda table, entity: captured.append((table, entity)))
    noms = [{
        "symbol": "AVGO", "path": "conviction", "p_up": 0.64, "horizon_days": 21,
        "evidence": ["AI capex commentary", "60d relative strength +18pp vs SPY"],
        "invalidation": "94.0",
    }]
    ah._write_flex_conviction_history("2026-08-14", noms)
    assert len(captured) == 1
    table, e = captured[0]
    assert table == "ThematicHistory"
    assert e["symbol"] == "AVGO"
    assert e["path"] == "flex_conviction"
    assert e["p_up"] == 0.64
    assert e["horizon_days"] == 21
    assert e["filed_date"] == "2026-08-14"
    assert e["evidence_count"] == 2
    assert e["PartitionKey"] == "2026-08"
    assert e["RowKey"] == "FLEXCV-20260814-000"
    assert e["outcome_status"] == ""


def test_catalyst_path_entries_in_same_list_are_skipped(monkeypatch):
    captured = []
    monkeypatch.setattr(ah, "upsert_entity", lambda table, entity: captured.append(entity))
    noms = [
        {"symbol": "NVDA", "path": "catalyst"},
        {"symbol": "AVGO", "path": "conviction", "p_up": 0.6, "evidence": ["e1", "e2"]},
    ]
    ah._write_flex_conviction_history("2026-08-14", noms)
    assert len(captured) == 1
    assert captured[0]["symbol"] == "AVGO"


def test_missing_path_field_entirely_is_skipped(monkeypatch):
    captured = []
    monkeypatch.setattr(ah, "upsert_entity", lambda table, entity: captured.append(entity))
    ah._write_flex_conviction_history("2026-08-14", [{"symbol": "NVDA", "p_up": 0.6}])
    assert captured == []


def test_empty_list_writes_nothing(monkeypatch):
    captured = []
    monkeypatch.setattr(ah, "upsert_entity", lambda table, entity: captured.append(entity))
    ah._write_flex_conviction_history("2026-08-14", [])
    assert captured == []


def test_missing_symbol_skipped(monkeypatch):
    captured = []
    monkeypatch.setattr(ah, "upsert_entity", lambda table, entity: captured.append(entity))
    ah._write_flex_conviction_history(
        "2026-08-14", [{"path": "conviction", "p_up": 0.6, "evidence": []}],
    )
    assert captured == []


def test_malformed_horizon_days_degrades_to_zero_not_a_crash(monkeypatch):
    captured = []
    monkeypatch.setattr(ah, "upsert_entity", lambda table, entity: captured.append(entity))
    ah._write_flex_conviction_history(
        "2026-08-14",
        [{"symbol": "AVGO", "path": "conviction", "p_up": 0.6, "horizon_days": "not-a-number",
          "evidence": []}],
    )
    assert captured[0]["horizon_days"] == 0
