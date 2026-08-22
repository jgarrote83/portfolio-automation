"""ALFRED backtest harness (2026-08-21, `feat/20260821-alfred-backtest-harness`)
— end-to-end orchestration CLI: alfred_cache (already built) -> replay_axes ->
replay_parity (parity gate + turn lag table + false-flip rate) -> the WORKED
BASELINE metrics `admission.admit()` needs for a future candidate-scoring
cycle.

**Scores nothing against `admission.admit()` itself.** Per Task E's explicit
scope, this prints baseline metrics only -- admitting or rejecting any
candidate signal is a deliberately separate, later cycle.

Metrics are reported PER AXIS (growth, inflation) rather than blended: a
candidate signal is scored against whichever axis's composite it targets, so
a single combined number would be the wrong denominator for that comparison.

**Parity is a hard gate, not a formality.** If a `--snapshots-dir` is given
and parity is below 100% on the dates checked, this prints every mismatch
and a loud warning that the lag table below should not be trusted until the
discrepancy is understood -- it still prints the table (for diagnosis), but
never claims the numbers are reliable.

Run (after `alfred_cache.py` has built a local cache):
    python scripts/run_baseline.py --start 2015-01-01 --end 2026-08-21

With a parity check against real stored snapshots downloaded locally:
    az storage blob download-batch --account-name stpfautoprod \\
        -s daily-snapshots -d /tmp/snapshots --auth-mode login
    python scripts/run_baseline.py --start 2015-01-01 --end 2026-08-21 \\
        --snapshots-dir /tmp/snapshots
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import alfred_cache  # noqa: E402
from replay_axes import replay  # noqa: E402
from replay_parity import check_parity, false_flip_rate, load_turns, turn_lag_table  # noqa: E402


def _load_local_snapshots(snapshots_dir: str) -> dict[str, dict]:
    out = {}
    for p in sorted(Path(snapshots_dir).glob("*.json")):
        with open(p, encoding="utf-8") as f:
            out[p.stem] = json.load(f)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--snapshots-dir", help="local dir of {date}.json real stored snapshots for the parity check")
    args = ap.parse_args()

    cache = alfred_cache.load_all_cached()
    if not cache:
        print(f"No ALFRED cache found in {alfred_cache.CACHE_DIR} -- run alfred_cache.py first.")
        return 2

    records = replay(args.start, args.end, cache)
    turns = load_turns()
    print(f"Replayed {len(records)} business dates, {args.start}..{args.end}.")

    if args.snapshots_dir:
        snapshots = _load_local_snapshots(args.snapshots_dir)
        parity = check_parity(records, snapshots)
        print(f"\nParity: {parity['matched']}/{parity['checked']} "
              f"({parity['parity_rate']:.1%})" if parity["checked"] else "\nParity: no overlapping dates checked.")
        for m in parity["mismatches"]:
            print(f"  MISMATCH {m['date']}: {m['mismatches']}")
        if parity["checked"] and parity["parity_rate"] < 1.0:
            print("\n*** PARITY FAILED — do not trust the lag table below until this is "
                  "understood (see the harness's own doctrine: a mismatch may be a real "
                  "production bug, never force-adjust the harness to hide it). ***\n")

    print("\n--- Per-turn lag table (all turns) ---")
    table = turn_lag_table(records, turns)
    print(json.dumps(table, indent=2, default=str))

    print("\n--- Per-axis baseline metrics (feed admission.admit() later) ---")
    for axis in ("growth", "inflation"):
        axis_turns = [t for t in turns if t["axis"] == axis]
        axis_table = turn_lag_table(records, axis_turns)
        ff = false_flip_rate(records, axis, turns)
        baseline_metrics = {
            "median_lag_days": axis_table["median_confirmed_lag_days"],
            "false_flips_per_year": ff["false_flips_per_year"],
            "n_turns_scored": axis_table["n_scored_for_median"],
        }
        print(f"{axis}: {json.dumps(baseline_metrics, default=str)}")

    print("\nNo candidate signal was scored against admission.admit() in this run "
          "(baseline only, per Task E's scope).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
