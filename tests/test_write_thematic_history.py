"""Task D6 (2026-08-14 audit) — `_write_thematic_history` (analyzer, WRITE
ONLY). Mirrors the existing `_write_trade_history` monkeypatch-`upsert_entity`
testing precedent (tests/test_flex_review.py).

Run: PYTHONPATH=src pytest tests/test_write_thematic_history.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import analyzer.handler as ah  # noqa: E402


def test_writes_one_row_per_nomination_with_entry_price(monkeypatch):
    captured = []
    monkeypatch.setattr(ah, "upsert_entity", lambda table, entity: captured.append((table, entity)))
    noms = [{
        "symbol": "VDE", "theme": "hormuz_energy_supply", "p_up": 0.68,
        "horizon_days": 60,
        "evidence": ["inflation_axis.oil_20d_pct_governing = +4.8", "market_shock streak 5"],
        "invalidation": "Hormuz reopens and Brent < $75 for 3 sessions",
    }]
    gaps = [{"symbol": "VDE", "price": 171.55}]
    ah._write_thematic_history("2026-08-14", noms, gaps)
    assert len(captured) == 1
    table, e = captured[0]
    assert table == "ThematicHistory"
    assert e["symbol"] == "VDE"
    assert e["p_up"] == 0.68
    assert e["entry_price"] == 171.55
    assert e["filed_date"] == "2026-08-14"
    assert e["horizon_30d"] == "2026-09-13"
    assert e["horizon_60d"] == "2026-10-13"
    assert e["horizon_90d"] == "2026-11-12"
    assert e["evidence_count"] == 2
    assert e["PartitionKey"] == "2026-08"
    assert e["RowKey"] == "THM-20260814-000"


def test_unpriced_symbol_still_writes_row_with_null_entry_price(monkeypatch):
    captured = []
    monkeypatch.setattr(ah, "upsert_entity", lambda table, entity: captured.append(entity))
    noms = [{"symbol": "PDBC", "p_up": 0.6, "evidence": ["a", "b"]}]
    ah._write_thematic_history("2026-08-14", noms, [])
    assert captured[0]["entry_price"] is None


def test_empty_nominations_writes_nothing(monkeypatch):
    captured = []
    monkeypatch.setattr(ah, "upsert_entity", lambda table, entity: captured.append(entity))
    ah._write_thematic_history("2026-08-14", [], [])
    assert captured == []


def test_missing_symbol_skipped(monkeypatch):
    captured = []
    monkeypatch.setattr(ah, "upsert_entity", lambda table, entity: captured.append(entity))
    ah._write_thematic_history("2026-08-14", [{"p_up": 0.6, "evidence": []}], [])
    assert captured == []
