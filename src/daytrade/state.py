"""Day-state machine (pure) — slots, breakers, halts (spec §3a/§6).

The handler persists this dict to ``daytrade-state/{date}.json`` between ticks;
all transitions live here so the breaker/slot rules are unit-testable without a
broker. R accounting uses NET r-multiples (after the grading haircut) — the
breakers and the grades must count the same number.
"""
from __future__ import annotations

from datetime import date, timedelta


def new_day_state(date_str: str, tone: str | None) -> dict:
    return {
        "date": date_str,
        "tone": (tone or "neutral").lower(),
        "validated": False,
        "candidates": [],          # gate results (incl. discards) for the log
        "primary": None,           # survivor dicts from gates.select_survivors
        "backup": None,
        "entered_symbols": [],     # one entry per name per day
        "slots": [],               # [{slot, symbol, outcome, r_net, resolved_min}]
        "day_r": 0.0,
        "day_done": False,
        "day_done_reason": None,
        "graded": False,
        "no_setup_logged": False,
    }


def record_outcome(state: dict, slot: int, symbol: str, outcome: str,
                   r_net: float, minutes: float, cfg) -> dict:
    """Record a resolved slot; apply the daily breaker (one loss ends the day)."""
    state["slots"].append({
        "slot": slot, "symbol": symbol, "outcome": outcome,
        "r_net": r_net, "resolved_min": minutes,
    })
    state["day_r"] = round(state["day_r"] + r_net, 4)
    if outcome == "loss":
        state["day_done"] = True
        state["day_done_reason"] = "daily_breaker_one_loss"
    return state


def can_enter_slot1(state: dict, minutes: float, cfg) -> tuple[bool, str | None]:
    if state["day_done"]:
        return False, state["day_done_reason"] or "day_done"
    if state.get("tone") == "carry_stress":
        return False, "carry_stress_day_refused"
    if any(s["slot"] == 1 for s in state["slots"]) or state["entered_symbols"]:
        return False, "slot1_already_used"
    if minutes >= cfg.entry_cutoff_min:
        return False, "after_entry_cutoff"
    return True, None


def can_enter_slot2(state: dict, minutes: float, cfg) -> tuple[bool, str | None]:
    """§3a — ALL five preconditions; the loss breaker outranks everything."""
    if state["day_done"]:
        return False, state["day_done_reason"] or "day_done"
    slot1 = next((s for s in state["slots"] if s["slot"] == 1), None)
    if slot1 is None:
        return False, "slot1_unresolved"
    if slot1["outcome"] not in ("win", "scratch"):
        return False, "slot1_not_win_or_scratch"
    if slot1["resolved_min"] >= cfg.slot1_resolve_by_min:
        return False, "slot1_resolved_too_late"
    if not (cfg.slot2_start_min <= minutes <= cfg.slot2_end_min):
        return False, "outside_slot2_window"
    if state.get("backup") is None:
        return False, "no_backup_candidate"
    if any(s["slot"] == 2 for s in state["slots"]):
        return False, "slot2_already_used"
    return True, None


def week_monday(date_str: str) -> str:
    """The Monday of ``date_str``'s ISO week (the weekly-R accumulation key)."""
    d = date.fromisoformat(date_str)
    return (d - timedelta(days=d.weekday())).isoformat()


def apply_weekly_breaker(halt: dict | None, date_str: str, week_r: float, cfg) -> dict | None:
    """Return a halt record when the week's cumulative NET R breaches the breaker.

    The halt persists until the following Monday; an existing unexpired halt is
    returned unchanged.
    """
    if halt and halt.get("until", "") > date_str:
        return halt
    if week_r <= cfg.week_halt_r:
        d = date.fromisoformat(date_str)
        next_monday = d + timedelta(days=7 - d.weekday())
        return {
            "halted_on": date_str,
            "week_r": round(week_r, 4),
            "until": next_monday.isoformat(),
            "reason": f"weekly_breaker_{cfg.week_halt_r}R",
        }
    return None


def is_halted(halt: dict | None, date_str: str) -> bool:
    return bool(halt) and str(halt.get("until", "")) > date_str
