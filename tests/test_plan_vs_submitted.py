"""Task A2 (2026-08-14, decision D-1) — plan_vs_submitted reconciliation.

Extends `_build_execution_review` with a sub-block comparing the analyzer's
deterministic intended order array (`daily-trades/{prev}.json`'s trades[]) to
what actually reached the broker (`daily-executions/{prev}.json`). This is a
JSON-to-broker fidelity check — a DIFFERENT failure mode than Task A1's
canonical addendum (which guards against the prose-vs-JSON divergence found
in the 2026-08-12 incident during the A0 probe).

Run: PYTHONPATH=src pytest tests/test_plan_vs_submitted.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import collector.handler as ch  # noqa: E402
from collector.handler import _build_plan_vs_submitted  # noqa: E402


def test_clean_match_is_ok(monkeypatch):
    monkeypatch.setattr(ch, "read_trades", lambda d: {
        "trades": [
            {"id": "T-1", "side": "sell", "symbol": "SGOV", "quantity": 90},
            {"id": "T-2", "side": "buy", "symbol": "SPY", "quantity": 8},
        ],
    })
    executions = [
        {"id": "T-1", "symbol": "SGOV", "side": "sell", "qty": 90},
        {"id": "T-2", "symbol": "SPY", "side": "buy", "qty": 8},
    ]
    result = _build_plan_vs_submitted("2026-08-13", executions)
    assert result["available"] is True
    assert result["status"] == "ok"
    assert result["planned_count"] == 2
    assert result["submitted_count"] == 2
    assert result["missing_from_submission"] == []
    assert result["qty_mismatch"] == []
    assert result["extra_in_submission"] == []


def test_missing_order_flagged(monkeypatch):
    """The 2026-08-12 shape, if it HAD made it into trades[]: 6 planned, only 1
    submitted -> 5 missing_from_submission, status mismatch."""
    monkeypatch.setattr(ch, "read_trades", lambda d: {
        "trades": [
            {"id": "T-1", "side": "sell", "symbol": "SGOV", "quantity": 102},
            {"id": "T-2", "side": "buy", "symbol": "VXUS", "quantity": 2},
            {"id": "T-3", "side": "buy", "symbol": "COWZ", "quantity": 45},
            {"id": "T-4", "side": "buy", "symbol": "VDE", "quantity": 18},
            {"id": "T-5", "side": "buy", "symbol": "SPY", "quantity": 3},
            {"id": "T-6", "side": "buy", "symbol": "QQQ", "quantity": 1},
        ],
    })
    executions = [{"id": "T-3", "symbol": "COWZ", "side": "buy", "qty": 45}]
    result = _build_plan_vs_submitted("2026-08-12", executions)
    assert result["status"] == "mismatch"
    assert result["planned_count"] == 6
    assert result["submitted_count"] == 1
    missing_symbols = {m["symbol"] for m in result["missing_from_submission"]}
    assert missing_symbols == {"SGOV", "VXUS", "VDE", "SPY", "QQQ"}
    assert result["qty_mismatch"] == []


def test_qty_mismatch_flagged(monkeypatch):
    monkeypatch.setattr(ch, "read_trades", lambda d: {
        "trades": [{"id": "T-1", "side": "buy", "symbol": "COWZ", "quantity": 45}],
    })
    executions = [{"id": "T-1", "symbol": "COWZ", "side": "buy", "qty": 20}]
    result = _build_plan_vs_submitted("2026-08-12", executions)
    assert result["status"] == "mismatch"
    assert result["qty_mismatch"] == [
        {"symbol": "COWZ", "side": "buy", "planned_qty": 45, "submitted_qty": 20},
    ]
    assert result["missing_from_submission"] == []


def test_extra_enforced_trade_does_not_flag(monkeypatch):
    """A submission row with no plan row that is legitimately a reconcile-
    synthesized band_enforcement trade must NOT flip status to mismatch."""
    monkeypatch.setattr(ch, "read_trades", lambda d: {"trades": []})
    executions = [
        {"id": "E-1", "symbol": "COWZ", "side": "buy", "qty": 20, "source": "band_enforcement"},
    ]
    result = _build_plan_vs_submitted("2026-08-12", executions)
    assert result["status"] == "ok"
    assert result["extra_in_submission"] == [
        {"symbol": "COWZ", "side": "buy", "qty": 20, "origin": "enforced"},
    ]


def test_extra_non_enforced_trade_flags_mismatch(monkeypatch):
    monkeypatch.setattr(ch, "read_trades", lambda d: {"trades": []})
    executions = [{"id": "E-1", "symbol": "MU", "side": "sell", "qty": 5}]
    result = _build_plan_vs_submitted("2026-08-12", executions)
    assert result["status"] == "mismatch"
    assert result["extra_in_submission"] == [
        {"symbol": "MU", "side": "sell", "qty": 5, "origin": "unknown"},
    ]


def test_missing_daily_trades_blob_is_indeterminate_never_ok(monkeypatch):
    monkeypatch.setattr(ch, "read_trades", lambda d: None)
    result = _build_plan_vs_submitted("2026-08-12", [])
    assert result["available"] is False
    assert result["status"] == "indeterminate"
    assert result["status"] != "ok"


def test_malformed_daily_trades_blob_is_indeterminate(monkeypatch):
    monkeypatch.setattr(ch, "read_trades", lambda d: {"not_trades_key": []})
    result = _build_plan_vs_submitted("2026-08-12", [])
    assert result["available"] is False
    assert result["status"] == "indeterminate"


def test_execution_review_embeds_plan_vs_submitted(monkeypatch):
    """Full integration through _build_execution_review's own call site."""
    from collector.handler import _build_execution_review

    def _read_exec(d):
        if d == "2026-08-13":
            return {"executions": [{"id": "T-1", "symbol": "SPY", "side": "buy",
                                     "qty": 8, "alpaca_order_id": "o1"}]}
        return None

    class _FakeAlpaca:
        def get_order(self, oid):
            return {"status": "filled", "filled_qty": "8"}

    monkeypatch.setattr(ch, "read_executions", _read_exec)
    monkeypatch.setattr(ch, "AlpacaClient", lambda api_key, api_secret: _FakeAlpaca())
    monkeypatch.setattr(ch, "read_trades", lambda d: {
        "trades": [{"id": "T-1", "side": "buy", "symbol": "SPY", "quantity": 8}],
    })
    result = _build_execution_review(
        {"AlpacaApiKey": "k", "AlpacaApiSecret": "s"}, "2026-08-14",
    )
    assert result["available"] is True
    assert result["plan_vs_submitted"]["status"] == "ok"
    assert result["plan_vs_submitted"]["date"] == "2026-08-13"
