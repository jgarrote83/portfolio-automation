"""Grading (pure) — spec §5. Expectancy net of haircut + the PRE-REGISTERED rules.

The rules are encoded as module constants with their trigger counts and the
engine ENFORCES the flags this module emits — they are not advisory:

- at ``N_UNLOCK`` graded trades: expectancy > 0 ⇒ ``concurrency_unlock``;
  expectancy ≤ 0 ⇒ ``kill`` (the handler refuses further entries until the spec
  version string changes);
- at ``N_CELL`` per ``catalyst_class × pattern`` cell: negative cells are removed
  (the handler refuses that cell).

Counting is scoped to rows stamped with the CURRENT ``spec_version`` — any
threshold change bumps the version and resets every count by design.
"""
from __future__ import annotations

N_UNLOCK = 20   # graded trades before the unlock/kill rule triggers
N_CELL = 40     # graded trades before negative cells are removed

_GRADED_OUTCOMES = ("win", "loss", "scratch")


def haircut_r(entry: float, stop: float, haircut_pp_per_side: float) -> float | None:
    """The round-trip slippage haircut expressed in R (0.10 pp/side ⇒ 0.20 pp)."""
    risk = entry - stop
    if entry <= 0 or risk <= 0:
        return None
    return round((2.0 * haircut_pp_per_side / 100.0 * entry) / risk, 6)


def net_r(entry: float, stop: float, exit_price: float,
          haircut_pp_per_side: float) -> tuple[float | None, float | None]:
    """Return ``(r_multiple_raw, r_multiple_net)`` for a closed round trip."""
    risk = entry - stop
    if entry <= 0 or risk <= 0:
        return None, None
    raw = (exit_price - entry) / risk
    hc = haircut_r(entry, stop, haircut_pp_per_side)
    return round(raw, 4), round(raw - hc, 4)


def outcome_of(r_net: float, scratch_band_r: float = 0.1) -> str:
    """win / loss / scratch from the NET r-multiple (scratch = |r| ≤ 0.1R)."""
    if abs(r_net) <= scratch_band_r:
        return "scratch"
    return "win" if r_net > 0 else "loss"


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def _agg(rows: list[dict]) -> dict:
    rs = [float(r["r_multiple_net"]) for r in rows
          if r.get("r_multiple_net") is not None]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    return {
        "n": len(rs),
        "expectancy_net_r": _mean(rs),
        "win_pct": round(len(wins) / len(rs) * 100.0, 2) if rs else None,
        "avg_win_r": _mean(wins),
        "avg_loss_r": _mean(losses),
    }


def build_daytrade_grades(rows: list[dict], spec_version: str) -> dict:
    """Aggregate the log into grades + the ENFORCED pre-registered flags.

    ``rows`` are daytrade-log records; only graded trades (outcome in
    win/loss/scratch) with the current ``spec_version`` count. ``no_setup`` and
    discard rows are excluded from n but never dropped from the log.
    """
    graded = [
        r for r in (rows or [])
        if r.get("outcome") in _GRADED_OUTCOMES
        and str(r.get("spec_version", spec_version)) == spec_version
    ]
    overall = _agg(graded)

    cells: dict[str, dict] = {}
    for r in graded:
        key = f"{r.get('catalyst_class') or '?'}x{r.get('pattern') or '?'}"
        cells.setdefault(key, []).append(r)
    per_cell = {k: _agg(v) for k, v in sorted(cells.items())}

    slots: dict[str, dict] = {}
    for r in graded:
        key = f"slot{r.get('slot') or 1}"
        slots.setdefault(key, []).append(r)
    per_slot = {k: _agg(v) for k, v in sorted(slots.items())}

    n = overall["n"]
    exp = overall["expectancy_net_r"]
    unlock = bool(n >= N_UNLOCK and exp is not None and exp > 0)
    kill = bool(n >= N_UNLOCK and exp is not None and exp <= 0)
    blocked_cells = sorted(
        k for k, a in per_cell.items()
        if a["n"] >= N_CELL and a["expectancy_net_r"] is not None
        and a["expectancy_net_r"] < 0
    )

    return {
        "spec_version": spec_version,
        "overall": overall,
        "per_cell": per_cell,
        "per_slot": per_slot,
        # Pre-registered rules — trigger counts recorded beside their flags.
        "rules": {
            "n_unlock": N_UNLOCK,
            "n_cell": N_CELL,
            "concurrency_unlock": unlock,
            "kill": kill,
            "blocked_cells": blocked_cells,
        },
    }


def entry_refusal(grades: dict | None, spec_version: str,
                  catalyst_class: str | None, pattern: str) -> str | None:
    """The handler-side ENFORCEMENT of the pre-registered flags.

    Returns a refusal reason or None. Grades from a different spec version never
    refuse (a version bump resets the rules by design).
    """
    if not grades or str(grades.get("spec_version")) != spec_version:
        return None
    rules = grades.get("rules") or {}
    if rules.get("kill"):
        return "kill_active_expectancy_nonpositive"
    cell = f"{catalyst_class or '?'}x{pattern}"
    if cell in (rules.get("blocked_cells") or []):
        return f"cell_blocked:{cell}"
    return None
