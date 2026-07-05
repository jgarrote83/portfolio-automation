"""Unit tests for the auto-exec retry hardening (#29).

Covers the ET-date helper (the latent UTC-date bug: an evening clock must NOT roll
the trading date), the retry idempotency contract (cached terminal outcomes exit in
one blob read without touching secrets/Alpaca; uncached failures genuinely
re-attempt), and the escalation boundary (no_trades is WARNING at 10:05 ET, ERROR
at >=11:00 ET; refused_validation is ERROR on any retry). Run:
    PYTHONPATH=src pytest tests/test_auto_exec_retries.py
"""
import logging
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import executor.handler as ex  # noqa: E402
from shared.timeutil import now_et, today_et  # noqa: E402

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


# --- today_et / now_et --------------------------------------------------------------

def test_today_et_evening_does_not_roll_to_tomorrow():
    """2026-07-06 20:30 ET is already 2026-07-07 in UTC — the trading date must
    stay 07-06 (the audit's latent bug)."""
    utc_clock = datetime(2026, 7, 7, 0, 30, tzinfo=UTC)   # = 20:30 ET on 07-06
    assert today_et(utc_clock) == "2026-07-06"


def test_today_et_morning_matches():
    utc_clock = datetime(2026, 7, 6, 13, 35, tzinfo=UTC)   # = 09:35 ET
    assert today_et(utc_clock) == "2026-07-06"
    assert now_et(utc_clock).hour == 9


def test_today_et_naive_input_assumed_utc():
    naive = datetime(2026, 7, 7, 0, 30)   # naive == UTC by contract
    assert today_et(naive) == "2026-07-06"


def test_today_et_et_aware_input_passthrough():
    et_clock = datetime(2026, 7, 6, 20, 30, tzinfo=ET)
    assert today_et(et_clock) == "2026-07-06"


# --- retry idempotency (cache asymmetry) ---------------------------------------------

def _boom(*_a, **_k):
    raise AssertionError("must not be called on this path")


def test_retry_after_success_returns_cached_without_alpaca(monkeypatch):
    """Terminal outcome cached: a retry is ONE blob read + exit — secrets/Alpaca
    untouched (load_secrets patched to explode proves it)."""
    monkeypatch.setattr(ex, "read_executions", lambda d: {"status": "ok", "date": d})
    monkeypatch.setattr(ex, "load_secrets", _boom)
    monkeypatch.setattr(ex, "read_trades", _boom)
    result = ex.execute_approvals("2026-07-06", force=False, auto=True)
    assert result["cached"] is True
    assert result["status"] == "ok"


def test_retry_after_no_trades_reattempts(monkeypatch):
    """no_trades is NOT cached — the retry reads the trades blob again."""
    calls = {"trades": 0}

    def _read_trades(d):
        calls["trades"] += 1
        return None

    monkeypatch.setattr(ex, "read_executions", lambda d: None)
    monkeypatch.setattr(ex, "read_trades", _read_trades)
    result = ex.execute_approvals("2026-07-06", force=False, auto=True)
    assert result["status"] == "no_trades"
    assert calls["trades"] == 1   # a genuine re-attempt, not a cache hit


# --- run_auto_execute: gating + escalation --------------------------------------------

def _wire_no_trades(monkeypatch):
    monkeypatch.setenv("AUTO_EXECUTE_ENABLED", "true")
    monkeypatch.setattr(ex, "read_executions", lambda d: None)
    monkeypatch.setattr(ex, "read_trades", lambda d: None)


def test_run_auto_execute_gated_off(monkeypatch):
    monkeypatch.setenv("AUTO_EXECUTE_ENABLED", "false")
    monkeypatch.setattr(ex, "read_executions", _boom)
    assert ex.run_auto_execute("auto_executor") is None


def test_retry_no_trades_at_1005_is_warning(monkeypatch, caplog):
    _wire_no_trades(monkeypatch)
    clock = datetime(2026, 7, 6, 14, 5, tzinfo=UTC)   # 10:05 ET
    with caplog.at_level(logging.WARNING, logger="executor.handler"):
        result = ex.run_auto_execute("auto_executor_retry", now=clock)
    assert result["status"] == "no_trades"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("no trades file yet" in r.getMessage() for r in warnings)
    assert errors == []


def test_retry_no_trades_at_1105_is_error(monkeypatch, caplog):
    _wire_no_trades(monkeypatch)
    clock = datetime(2026, 7, 6, 15, 5, tzinfo=UTC)   # 11:05 ET — final retry
    with caplog.at_level(logging.WARNING, logger="executor.handler"):
        result = ex.run_auto_execute("auto_executor_retry", now=clock)
    assert result["status"] == "no_trades"
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("analyzer never produced daily-trades/2026-07-06.json"
               in r.getMessage() for r in errors)


def test_primary_fire_no_trades_does_not_escalate(monkeypatch, caplog):
    """The 09:35 primary shot is not a retry — no escalation logging."""
    _wire_no_trades(monkeypatch)
    clock = datetime(2026, 7, 6, 13, 35, tzinfo=UTC)   # 09:35 ET
    with caplog.at_level(logging.WARNING, logger="executor.handler"):
        ex.run_auto_execute("auto_executor", now=clock)
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_retry_refused_validation_is_error_any_hour(monkeypatch, caplog):
    """A quarantined file (#28) escalates at ERROR even on the 10:05 fire — the
    file exists, so 'analyzer late' does not apply."""
    monkeypatch.setenv("AUTO_EXECUTE_ENABLED", "true")
    monkeypatch.setattr(ex, "read_executions", lambda d: None)
    monkeypatch.setattr(
        ex, "read_trades",
        lambda d: {"validation_error": True, "trades": [{"id": "T-1", "symbol": "GLD",
                                                         "side": "buy", "quantity": 1}]},
    )
    clock = datetime(2026, 7, 6, 14, 5, tzinfo=UTC)   # 10:05 ET
    with caplog.at_level(logging.WARNING, logger="executor.handler"):
        result = ex.run_auto_execute("auto_executor_retry", now=clock)
    assert result["status"] == "refused_validation"
    assert any("QUARANTINED" in r.getMessage() for r in caplog.records
               if r.levelno == logging.ERROR)


def test_retry_uses_et_trading_date(monkeypatch):
    """An evening re-fire reads TODAY's file, not tomorrow's (the UTC-date bug)."""
    monkeypatch.setenv("AUTO_EXECUTE_ENABLED", "true")
    seen = {}

    def _read_executions(d):
        seen["date"] = d
        return {"status": "ok", "date": d}

    monkeypatch.setattr(ex, "read_executions", _read_executions)
    clock = datetime(2026, 7, 7, 0, 30, tzinfo=UTC)   # 20:30 ET on 07-06
    ex.run_auto_execute("auto_executor_retry", now=clock)
    assert seen["date"] == "2026-07-06"
