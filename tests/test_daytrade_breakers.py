"""DayTrade Lab breakers + slot state machine (spec §3a/§6) — one loss ends the
day, −3R weekly halt, halted engine refuses, slot-2 preconditions (all five,
including loss-ends-day precedence), reconcile repairs.

Run: PYTHONPATH=src pytest tests/test_daytrade_breakers.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from daytrade.config import DayTradeConfig  # noqa: E402
from daytrade.reconcile import reconcile_daytrade  # noqa: E402
from daytrade.state import (  # noqa: E402
    apply_weekly_breaker,
    can_enter_slot1,
    can_enter_slot2,
    is_halted,
    new_day_state,
    record_outcome,
    week_monday,
)

CFG = DayTradeConfig()


def _day(tone="neutral"):
    return new_day_state("2026-07-07", tone)


# ── daily breaker ────────────────────────────────────────────────────────────

def test_one_loss_ends_the_day():
    day = _day()
    record_outcome(day, 1, "ABCD", "loss", -1.05, 20.0, CFG)
    assert day["day_done"] and day["day_done_reason"] == "daily_breaker_one_loss"
    ok, reason = can_enter_slot1(day, 25.0, CFG)
    assert not ok and reason == "daily_breaker_one_loss"
    ok, reason = can_enter_slot2(day, 35.0, CFG)
    assert not ok and reason == "daily_breaker_one_loss"


def test_win_or_scratch_does_not_end_day():
    day = _day()
    record_outcome(day, 1, "ABCD", "win", 1.8, 20.0, CFG)
    assert not day["day_done"]


# ── slot 1 ───────────────────────────────────────────────────────────────────

def test_slot1_carry_stress_refused():
    ok, reason = can_enter_slot1(_day("carry_stress"), 10.0, CFG)
    assert not ok and reason == "carry_stress_day_refused"


def test_slot1_once_only_and_cutoff():
    day = _day()
    day["entered_symbols"].append("ABCD")
    assert can_enter_slot1(day, 10.0, CFG) == (False, "slot1_already_used")
    assert can_enter_slot1(_day(), CFG.entry_cutoff_min, CFG) == (
        False, "after_entry_cutoff")
    assert can_enter_slot1(_day(), 10.0, CFG) == (True, None)


# ── slot 2 — §3a: all five preconditions ─────────────────────────────────────

def _slot1_won(day, resolved_min=25.0, outcome="win"):
    record_outcome(day, 1, "ABCD", outcome, 1.8 if outcome == "win" else 0.0,
                   resolved_min, CFG)
    day["backup"] = {"symbol": "EFGH", "rvol": 5.0, "gates": []}
    return day


def test_slot2_happy_path():
    day = _slot1_won(_day())
    assert can_enter_slot2(day, 35.0, CFG) == (True, None)


def test_slot2_requires_slot1_resolved():
    day = _day()
    day["backup"] = {"symbol": "EFGH"}
    assert can_enter_slot2(day, 35.0, CFG) == (False, "slot1_unresolved")


def test_slot2_loss_precedence_over_all():
    """A slot-1 LOSS trips the daily breaker — reported as the breaker, never as
    a mere precondition miss (the breaker outranks, spec §3a)."""
    day = _day()
    record_outcome(day, 1, "ABCD", "loss", -1.0, 15.0, CFG)
    day["backup"] = {"symbol": "EFGH"}
    ok, reason = can_enter_slot2(day, 35.0, CFG)
    assert not ok and reason == "daily_breaker_one_loss"


def test_slot2_resolved_too_late():
    day = _slot1_won(_day(), resolved_min=CFG.slot1_resolve_by_min)   # not BEFORE 10:00
    assert can_enter_slot2(day, 35.0, CFG) == (False, "slot1_resolved_too_late")


def test_slot2_window_and_backup_and_once():
    day = _slot1_won(_day())
    assert can_enter_slot2(day, 20.0, CFG) == (False, "outside_slot2_window")
    assert can_enter_slot2(day, CFG.slot2_end_min + 1, CFG) == (
        False, "outside_slot2_window")
    day2 = _slot1_won(_day())
    day2["backup"] = None
    assert can_enter_slot2(day2, 35.0, CFG) == (False, "no_backup_candidate")
    day3 = _slot1_won(_day())
    record_outcome(day3, 2, "EFGH", "scratch", 0.02, 45.0, CFG)
    assert can_enter_slot2(day3, 50.0, CFG) == (False, "slot2_already_used")


def test_slot2_scratch_also_qualifies():
    day = _slot1_won(_day(), outcome="scratch")
    assert can_enter_slot2(day, 35.0, CFG) == (True, None)


# ── weekly breaker ───────────────────────────────────────────────────────────

def test_weekly_breaker_halts_until_monday():
    halt = apply_weekly_breaker(None, "2026-07-08", -3.2, CFG)
    assert halt is not None and halt["until"] == "2026-07-13"   # next Monday
    assert is_halted(halt, "2026-07-09")
    assert is_halted(halt, "2026-07-10")
    assert not is_halted(halt, "2026-07-13")                    # Monday: clear


def test_weekly_breaker_not_tripped_above_threshold():
    assert apply_weekly_breaker(None, "2026-07-08", -2.5, CFG) is None


def test_weekly_breaker_existing_halt_unchanged():
    halt = {"until": "2026-07-13", "reason": "weekly_breaker_-3.0R"}
    assert apply_weekly_breaker(halt, "2026-07-09", -5.0, CFG) is halt


def test_week_monday():
    assert week_monday("2026-07-07") == "2026-07-06"   # Tue → Mon
    assert week_monday("2026-07-06") == "2026-07-06"   # Mon → itself


# ── reconcile (STEP 0) ───────────────────────────────────────────────────────

def _row(sym="ABCD", qty=100):
    return {"symbol": sym, "entry_price": 20.0, "stop_price": 19.7,
            "target_price": 20.6, "qty_initial": qty, "qty_current": qty,
            "risk_per_share": 0.3, "slot": 1, "order_ids": ["o1"],
            "entered_min": 8.0}


def test_reconcile_records_broker_closure():
    ledger = {"ABCD": _row()}
    new, exits, repairs = reconcile_daytrade(ledger, [], [])
    assert new == {} and exits[0]["reason"] == "closed_at_broker"


def test_reconcile_resizes_to_broker_truth():
    ledger = {"ABCD": _row(qty=100)}
    pos = [{"symbol": "ABCD", "qty": "60"}]
    orders = [{"symbol": "ABCD", "side": "sell", "type": "stop", "id": "o1", "qty": "60"}]
    new, exits, repairs = reconcile_daytrade(ledger, pos, orders)
    assert not exits
    assert new["ABCD"]["qty_current"] == 60.0
    assert any(r["action"] == "resize_to_broker" for r in repairs)


def test_reconcile_flattens_orphan_without_protective_order():
    ledger = {"ABCD": _row()}
    pos = [{"symbol": "ABCD", "qty": "100"}]
    new, exits, repairs = reconcile_daytrade(ledger, pos, [])
    assert repairs and repairs[0]["action"] == "flatten_orphan"


def test_reconcile_never_touches_foreign_positions():
    """Separation: broker positions the lab did not open are ignored."""
    pos = [{"symbol": "MU", "qty": "2"}, {"symbol": "SPY", "qty": "23"}]
    new, exits, repairs = reconcile_daytrade({}, pos, [])
    assert new == {} and not exits and not repairs
