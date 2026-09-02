"""Unit tests for the settling-tranche-cap window (2026-09-02, G-2/B3 — MERGE
BLOCKER for the reference-degeneracy cycle).

`advance_settling_window` / `resolve_settling_tranche_cap` (shared/
reference_execution.py) are PURE — the collector wraps them with Table Storage
(SettlingWindowState, same PK/RK shape and non-fatal-on-read-failure contract
as AxisDirectionState). No date literal ever appears in config: the window is
self-initiating off the ABSENCE of persisted state, exactly like the
axis-confirmation D-A2 first-run rule.

Run: PYTHONPATH=src pytest tests/test_settling_window.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shared.reference_execution import (  # noqa: E402
    advance_settling_window,
    reconcile,
    resolve_settling_tranche_cap,
)
import collector.handler as handler  # noqa: E402

CFG = {
    "override_protocol": {"max_magnitude_pp": 15.0, "re_risk_min_evidence": 2, "gap_band_pp": 5.0},
    "reference_execution": {
        "tranche_pp_max": 10.0, "enforce": True,
        "enforcement_turnover_max_pct": 20.0, "min_notional_usd": 115.0,
    },
}


# --- advance_settling_window (pure) ------------------------------------------

def test_first_run_ever_self_initiates_no_date_literal():
    """No persisted state (the very first post-merge run) -> the window starts
    TODAY, session 1 of settling_sessions, with NO date read from config."""
    out = advance_settling_window(None, "2026-09-03", 5, 3.0)
    assert out["active"] is True
    assert out["start_date"] == "2026-09-03"
    assert out["sessions_elapsed"] == 1
    assert out["sessions_remaining"] == 4
    assert out["effective_cap"] == 3.0
    assert out["last_date"] == "2026-09-03"


def test_subsequent_session_advances_the_counter():
    prior = {"start_date": "2026-09-03", "sessions_elapsed": 1, "last_date": "2026-09-03"}
    out = advance_settling_window(prior, "2026-09-04", 5, 3.0)
    assert out["sessions_elapsed"] == 2
    assert out["sessions_remaining"] == 3
    assert out["start_date"] == "2026-09-03"   # unchanged
    assert out["active"] is True


def test_same_day_retry_does_not_double_count():
    """A second invocation on the SAME calendar day (a retry) must not advance
    the session counter a second time."""
    prior = {"start_date": "2026-09-03", "sessions_elapsed": 1, "last_date": "2026-09-03"}
    out = advance_settling_window(prior, "2026-09-03", 5, 3.0)
    assert out["sessions_elapsed"] == 1
    assert out["sessions_remaining"] == 4


def test_window_expires_after_settling_sessions():
    prior = {"start_date": "2026-09-03", "sessions_elapsed": 5, "last_date": "2026-09-09"}
    out = advance_settling_window(prior, "2026-09-10", 5, 3.0)
    assert out["sessions_elapsed"] == 6
    assert out["active"] is False
    assert out["sessions_remaining"] == 0
    assert out["effective_cap"] is None


def test_window_active_through_exactly_settling_sessions():
    prior = {"start_date": "2026-09-03", "sessions_elapsed": 4, "last_date": "2026-09-08"}
    out = advance_settling_window(prior, "2026-09-09", 5, 3.0)
    assert out["sessions_elapsed"] == 5
    assert out["active"] is True   # last active session
    assert out["sessions_remaining"] == 0
    assert out["effective_cap"] == 3.0


# --- resolve_settling_tranche_cap (pure) -------------------------------------

def test_effective_cap_is_the_lesser_while_active():
    assert resolve_settling_tranche_cap(10.0, {"active": True, "effective_cap": 3.0}) == 3.0


def test_effective_cap_falls_back_to_plain_tranche_when_expired():
    assert resolve_settling_tranche_cap(10.0, {"active": False, "effective_cap": None}) == 10.0


def test_effective_cap_falls_back_when_window_absent():
    """A caller that predates B3 (no settling_window in the snapshot at all)
    gets EXACTLY the old behavior."""
    assert resolve_settling_tranche_cap(10.0, None) == 10.0
    assert resolve_settling_tranche_cap(10.0, {}) == 10.0


def test_first_post_merge_run_reduces_10pp_tranche_to_3pp():
    """The literal motivating scenario: tranche_pp_max=10.0 on the first
    post-merge run must resolve to 3.0, not 10.0."""
    window = advance_settling_window(None, "2026-09-03", 5, 3.0)
    assert resolve_settling_tranche_cap(10.0, window) == 3.0


# --- end-to-end: reconcile() actually paces at the settling cap -------------

def test_reconcile_uses_settling_cap_on_first_post_merge_run():
    """The literal §2.7 proof: simulate the first post-merge run (no prior
    state) and show the effective tranche the ENFORCEMENT engine actually
    applies is 3.0pp, not the plain 10.0pp — an amplifier overweight of the
    same shape as the SOXX motivating incident (19.08 vs a 5.782 reference =
    13.3pp gap) would submit ~10pp/$20k unpaced; paced it submits ~3pp/~$6k."""
    window = advance_settling_window(None, "2026-09-03", 5, 3.0)
    cfg = dict(CFG)
    cfg["reference_execution"] = {
        **CFG["reference_execution"],
        "tranche_pp_max": resolve_settling_tranche_cap(
            CFG["reference_execution"]["tranche_pp_max"], window),
    }
    gaps = [{"symbol": "SPY", "current_pct": 19.08, "reference_pct": 5.782, "price": 200.0}]
    ctx = {"deployment_gate": "closed", "equity_usd": 100_000.0, "cash_usd": 0.0,
           "date": "2026-09-03", "exempt_holds": []}
    r = reconcile(gaps, [], [], cfg, ctx)
    spy = r["sleeves"]["SPY"]
    assert spy["required_move_today_pp"] == 3.0   # paced, not the plain 10.0pp tranche
    assert spy["status"] == "enforced"
    assert spy["enforced_trade"]["quantity"] == 15   # 3pp of $100K at $200


# --- collector Table Storage wrapper (mirrors AxisDirectionState shape) ------

def test_load_state_returns_none_on_empty_table(monkeypatch):
    monkeypatch.setattr(handler, "query_entities", lambda table, *a, **kw: [])
    assert handler._load_settling_window_state() is None


def test_load_state_round_trips_saved_entity(monkeypatch):
    saved = {}

    def _fake_upsert(table, entity):
        saved["table"] = table
        saved["entity"] = entity

    monkeypatch.setattr(handler, "upsert_entity", _fake_upsert)
    handler._save_settling_window_state(
        {"start_date": "2026-09-03", "sessions_elapsed": 2, "last_date": "2026-09-04"})
    assert saved["table"] == "SettlingWindowState"
    assert saved["entity"]["PartitionKey"] == "state"
    assert saved["entity"]["RowKey"] == "reference_execution"
    assert saved["entity"]["start_date"] == "2026-09-03"
    assert saved["entity"]["sessions_elapsed"] == 2

    monkeypatch.setattr(handler, "query_entities", lambda table, *a, **kw: [saved["entity"]])
    loaded = handler._load_settling_window_state()
    assert loaded == {"start_date": "2026-09-03", "sessions_elapsed": 2, "last_date": "2026-09-04"}


def test_load_state_non_fatal_on_query_failure(monkeypatch):
    """Mirrors the AxisDirectionState contract: query_entities already returns
    [] on any internal error (shared/storage.py), so a read failure degrades
    to None (first-run behavior) rather than raising."""
    def _boom(table, *a, **kw):
        return []   # shared.storage.query_entities' own contract on error

    monkeypatch.setattr(handler, "query_entities", _boom)
    assert handler._load_settling_window_state() is None
