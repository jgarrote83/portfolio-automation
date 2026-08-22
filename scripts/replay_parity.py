"""ALFRED backtest harness (2026-08-21, `feat/20260821-alfred-backtest-harness`),
Task D — parity check + turn lag table. THIS GATES EVERYTHING ELSE.

If the harness cannot reproduce what production ACTUALLY stored on a real
date, every lag number it produces for a historical turn is fiction -- the
harness might faithfully reconstruct "what the classifier would have said,"
but if that disagrees with what the classifier ACTUALLY said on a date we can
check, the reconstruction itself is wrong somewhere (a genuine production bug
is possible too -- report it as a finding, never paper over a mismatch by
"adjusting" this script to force agreement).

Two independent checks:

1. `check_parity(records, snapshots)` -- for every date we have a REAL stored
   snapshot for, assert the harness's replayed `growth_axis.direction` /
   `inflation_axis.direction` / `active_quadrant` matches exactly. Reports a
   rate, with every mismatch itemized (field, reconstructed value, stored
   value) -- never a bare pass/fail.

2. `turn_lag_table(records, turns)` -- scores the pre-registered turns in
   `scripts/config/regime-turns.json` (committed BEFORE any run, decision
   J-3): for each turn, finds the nearest genuine direction transition (not
   merely "already at that direction") in a window around the true turn date,
   for both the RAW and CONFIRMED series, and reports the lag in days
   (positive = classifier lagged; negative = classifier led). A turn whose
   axis has no primary-input coverage across most of its search window is
   marked `reconstructable: false` and DROPPED from the aggregate median --
   never silently scored against a degraded/absent input (section 6.2).

`false_flip_rate` separately scores, across the WHOLE replay (not just
turn windows), how many confirmed-direction changes are NOT associated with
any registered turn for that axis -- the denominator the admission rule
(`admission.py`) needs.

Offline only; never imported by collector/analyzer/flex runtime code.
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_SCRIPTS))

REGIME_TURNS_FILE = _SCRIPTS / "config" / "regime-turns.json"

# A turn's axis needs its primary input present for at least this fraction of
# the turn's own search-window business days to be scored at all -- a purely
# mechanical data-availability gate (not a signal-scoring threshold, so it
# does not need the admission rule's pre-registration ceremony). Chosen so a
# genuinely data-void era (2007-08, before GDPNow existed -- 0% coverage) is
# unambiguously distinguished from an ordinary turn with occasional data gaps
# (expected ~100% coverage); any threshold strictly between those two extremes
# works identically for that purpose.
_RECONSTRUCTABLE_COVERAGE_MIN_FRACTION = 0.5

_DEFAULT_WINDOW_BEFORE_DAYS = 180
_DEFAULT_WINDOW_AFTER_DAYS = 365


def load_turns(path: Path = REGIME_TURNS_FILE) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["turns"]


def _in_window(d: str, center: str, before_days: int, after_days: int) -> bool:
    dd = date.fromisoformat(d)
    c = date.fromisoformat(center)
    return (c - timedelta(days=before_days)) <= dd <= (c + timedelta(days=after_days))


# --- 1. parity check -----------------------------------------------------------

def check_parity(records: list[dict], snapshots: dict[str, dict]) -> dict:
    """``records``: `replay_axes.replay()` output. ``snapshots``: {date:
    {growth_axis: {...}, inflation_axis: {...}, active_quadrant: ...}} for
    real dates we have stored ground truth for. A date present in
    ``snapshots`` but absent from ``records`` (the replay window didn't cover
    it) is simply not checked -- never counted as a pass OR a mismatch."""
    by_date = {r["date"]: r for r in records}
    checked = 0
    matched = 0
    mismatches: list[dict] = []
    for d, snap in sorted(snapshots.items()):
        rec = by_date.get(d)
        if rec is None:
            continue
        checked += 1
        fields = {
            "growth_axis.direction": (rec["growth"]["direction"], (snap.get("growth_axis") or {}).get("direction")),
            "inflation_axis.direction": (rec["inflation"]["direction"], (snap.get("inflation_axis") or {}).get("direction")),
            "active_quadrant": (rec["active_quadrant"], snap.get("active_quadrant")),
        }
        row_mismatches = [
            {"field": f, "reconstructed": r_val, "stored": s_val}
            for f, (r_val, s_val) in fields.items() if r_val != s_val
        ]
        if row_mismatches:
            mismatches.append({"date": d, "mismatches": row_mismatches})
        else:
            matched += 1
    return {
        "checked": checked,
        "matched": matched,
        "parity_rate": (matched / checked) if checked else None,
        "mismatches": mismatches,
    }


# --- 2. turn lag table -----------------------------------------------------------

def _flip_date(
    dated_directions: list[tuple[str, str]], target_direction: str, true_turn_date: str,
    window_before_days: int, window_after_days: int,
) -> str | None:
    """The date of the first genuine transition INTO ``target_direction``
    within the search window -- 'genuine' meaning the immediately preceding
    record (looked up in the FULL series, not just the window, so a
    transition right at the window's edge is still recognized) did not
    already read ``target_direction``. Returns ``None`` if the direction
    never transitions into the target within the window (it may already have
    been there before the window opened, in which case this reports no NEW
    transition -- the true flip predates the search entirely)."""
    lo = (date.fromisoformat(true_turn_date) - timedelta(days=window_before_days)).isoformat()
    hi = (date.fromisoformat(true_turn_date) + timedelta(days=window_after_days)).isoformat()
    # i=0 (the very first record in the WHOLE series) is never eligible: there
    # is no known prior state to have transitioned FROM, so it is an unknown
    # starting condition, not a genuine flip.
    for i in range(1, len(dated_directions)):
        d, direction = dated_directions[i]
        if not (lo <= d <= hi):
            continue
        if direction != target_direction:
            continue
        if dated_directions[i - 1][1] != target_direction:
            return d
    return None


def _reconstructable_for_window(records: list[dict], axis: str, true_turn_date: str,
                                 window_before_days: int, window_after_days: int) -> bool:
    windowed = [r for r in records if _in_window(r["date"], true_turn_date, window_before_days, window_after_days)]
    if not windowed:
        return False
    covered = sum(1 for r in windowed if r["reconstructable"][axis])
    return (covered / len(windowed)) >= _RECONSTRUCTABLE_COVERAGE_MIN_FRACTION


def per_turn_lag(
    records: list[dict], turn: dict,
    window_before_days: int = _DEFAULT_WINDOW_BEFORE_DAYS,
    window_after_days: int = _DEFAULT_WINDOW_AFTER_DAYS,
) -> dict:
    axis = turn["axis"]
    target_dir = turn["direction"]
    true_date = turn["true_turn_date"]

    reconstructable = _reconstructable_for_window(records, axis, true_date, window_before_days, window_after_days)

    dated_raw = [(r["date"], r[axis]["raw_direction"]) for r in records]
    dated_confirmed = [(r["date"], r[axis]["direction"]) for r in records]

    raw_flip = _flip_date(dated_raw, target_dir, true_date, window_before_days, window_after_days) if reconstructable else None
    confirmed_flip = _flip_date(dated_confirmed, target_dir, true_date, window_before_days, window_after_days) if reconstructable else None

    def _lag(flip: str | None) -> int | None:
        return (date.fromisoformat(flip) - date.fromisoformat(true_date)).days if flip else None

    return {
        "turn_id": turn["id"],
        "axis": axis,
        "direction": target_dir,
        "true_turn_date": true_date,
        "reconstructable": reconstructable,
        "reconstructable_reason": None if reconstructable else "primary_input_coverage_below_threshold_in_search_window",
        "raw_flip_date": raw_flip,
        "raw_lag_days": _lag(raw_flip),
        "confirmed_flip_date": confirmed_flip,
        "confirmed_lag_days": _lag(confirmed_flip),
    }


def turn_lag_table(records: list[dict], turns: list[dict]) -> dict:
    per_turn = [per_turn_lag(records, t) for t in turns]
    reconstructable = [t for t in per_turn if t["reconstructable"]]
    dropped = [t for t in per_turn if not t["reconstructable"]]
    lags = [t["confirmed_lag_days"] for t in reconstructable if t["confirmed_lag_days"] is not None]
    return {
        "per_turn": per_turn,
        "n_turns_total": len(per_turn),
        "n_reconstructable": len(reconstructable),
        "dropped_turns": [{"turn_id": t["turn_id"], "reason": t["reconstructable_reason"]} for t in dropped],
        "median_confirmed_lag_days": statistics.median(lags) if lags else None,
        "n_scored_for_median": len(lags),
    }


# --- false-flip rate (feeds admission.py) ---------------------------------------

def false_flip_rate(
    records: list[dict], axis: str, turns: list[dict],
    window_before_days: int = _DEFAULT_WINDOW_BEFORE_DAYS,
    window_after_days: int = _DEFAULT_WINDOW_AFTER_DAYS,
) -> dict:
    """Confirmed-direction changes, across the WHOLE replay, not associated
    with any registered turn for ``axis``. ``per_year`` normalizes by the
    number of business days actually replayed (~252/year) so windows of
    different lengths are comparable."""
    dated_confirmed = [(r["date"], r[axis]["direction"]) for r in records]
    flips = [
        dated_confirmed[i][0] for i in range(1, len(dated_confirmed))
        if dated_confirmed[i][1] != dated_confirmed[i - 1][1]
    ]
    axis_turns = [t for t in turns if t["axis"] == axis]

    def _associated(flip_date: str) -> bool:
        return any(_in_window(flip_date, t["true_turn_date"], window_before_days, window_after_days)
                    for t in axis_turns)

    associated = [f for f in flips if _associated(f)]
    false = [f for f in flips if not _associated(f)]
    n_days = len(records)
    years = n_days / 252.0 if n_days else 0.0
    return {
        "axis": axis,
        "total_flips": len(flips),
        "associated_flips": len(associated),
        "false_flips": len(false),
        "false_flip_dates": false,
        "false_flips_per_year": (len(false) / years) if years else None,
    }


if __name__ == "__main__":
    print(__doc__)
    print("This module has no standalone CLI -- see the harness README section "
          "in the PR body for the end-to-end invocation (alfred_cache -> "
          "replay_axes -> replay_parity).")
    sys.exit(0)
