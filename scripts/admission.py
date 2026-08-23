"""ALFRED backtest harness (2026-08-21, `feat/20260821-alfred-backtest-harness`),
Task E — the signal-admission rule, codified.

FOLLOWUPS #23's pre-registered rule, verbatim: "no new signal enters a
composite unless it demonstrably reduces median point-in-time flip lag
without materially increasing false flips." `admit()` turns that sentence
into an executable, pure check against two metric dicts (each shaped like
the combination of `replay_parity.turn_lag_table`'s
``median_confirmed_lag_days`` and `replay_parity.false_flip_rate`'s
``false_flips_per_year``).

**This PR provides ONLY the worked baseline** (current axes' own metrics,
scored via the harness) and this rule. No candidate signal (#19's flexible
CPI, #22, the tape scores, anything else) is scored against it here --
admission decisions are a deliberately separate, later cycle, so the rule
cannot be tuned after the fact to admit a favored signal.

Thresholds live in `scripts/config/admission-rule.json`, pre-registered
(decision J-2) before any candidate was scored.
"""
from __future__ import annotations

import json
from pathlib import Path

ADMISSION_RULE_FILE = Path(__file__).resolve().parent / "config" / "admission-rule.json"

_DEFAULTS = {
    "min_lag_reduction_days": 3.0,
    "max_relative_false_flip_increase": 0.10,
}


def load_admission_rule(path: Path = ADMISSION_RULE_FILE) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_DEFAULTS)
    merged = dict(_DEFAULTS)
    for k, v in data.items():
        if not k.startswith("_"):
            merged[k] = v
    return merged


def admit(baseline_metrics: dict, candidate_metrics: dict, cfg: dict | None = None) -> dict:
    """Pure function of its two metric inputs (plus a fixed, pre-registered
    config) -- same inputs always produce the same decision.

    Each metrics dict: ``{"median_lag_days": float, "false_flips_per_year":
    float}``. Missing ``median_lag_days`` in either input makes the rule
    unassessable -- ``admit: False`` with a named reason, never a fabricated
    decision from partial data.
    """
    cfg = cfg if cfg is not None else load_admission_rule()

    b_lag = baseline_metrics.get("median_lag_days")
    c_lag = candidate_metrics.get("median_lag_days")
    if b_lag is None or c_lag is None:
        return {
            "admit": False,
            "lag_delta_days": None,
            "false_flip_delta": None,
            "reason": "median_lag_days missing from baseline or candidate metrics -- cannot assess",
        }

    lag_delta = b_lag - c_lag   # positive = candidate reduced lag
    min_reduction = float(cfg.get("min_lag_reduction_days", _DEFAULTS["min_lag_reduction_days"]))
    demonstrable = lag_delta >= min_reduction

    b_ff = baseline_metrics.get("false_flips_per_year")
    c_ff = candidate_metrics.get("false_flips_per_year")
    if b_ff is None or c_ff is None:
        return {
            "admit": False,
            "lag_delta_days": lag_delta,
            "false_flip_delta": None,
            "reason": "false_flips_per_year missing from baseline or candidate metrics -- cannot assess materiality",
        }

    false_flip_delta = c_ff - b_ff
    max_relative_increase = float(
        cfg.get("max_relative_false_flip_increase", _DEFAULTS["max_relative_false_flip_increase"])
    )
    if b_ff > 0:
        relative_increase = false_flip_delta / b_ff
    else:
        # A zero-false-flip baseline has no relative-increase denominator --
        # ANY new false flip is treated as material rather than dividing by
        # zero or silently passing an unbounded increase.
        relative_increase = float("inf") if false_flip_delta > 0 else 0.0
    material_increase = relative_increase > max_relative_increase

    admit_decision = demonstrable and not material_increase

    if admit_decision:
        reason = (f"lag reduced {lag_delta:.2f}d (>= {min_reduction:.2f}d threshold) "
                  f"with false-flip increase within tolerance")
    elif not demonstrable:
        reason = f"lag reduction {lag_delta:.2f}d below the {min_reduction:.2f}d 'demonstrable' bar"
    else:
        reason = (f"false-flip relative increase {relative_increase:.2%} exceeds the "
                  f"{max_relative_increase:.2%} 'material' tolerance")

    return {
        "admit": admit_decision,
        "lag_delta_days": lag_delta,
        "false_flip_delta": false_flip_delta,
        "reason": reason,
    }


if __name__ == "__main__":
    print(__doc__)
