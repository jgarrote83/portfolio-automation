"""FOLLOWUPS #80 probe (2026-08-21 axis-correctness cycle, Task C) — REPORT ONLY,
per #80's own explicit instruction: "Do not implement without a specific empirical
probe showing the current +-0.1 band actually misfires on a real vintage-boundary
case." This script is that probe. It implements nothing in `_build_growth_axis`;
Task A's `_detect_growth_rollover` fix (branch `fix/20260821-axis-correctness`) is
already committed separately and is only IMPORTED here, read-only, to answer
question (4) below.

Scans stored `daily-snapshots/{date}.json` blobs and reports, for every session
where `growth_axis.raw_direction` changed vs the prior session:
  (1) the trajectory/basis/vintage-count that drove the change,
  (2) whether `direction_change_diagnostics.attribution` (already computed by the
      collector, session 2026-07-28 Task A) called it `window_rolloff` (the oldest
      vintage aging out of the window, not new information) or `new_print`,
  (3) whether the change fell within 10 CALENDAR days of a quarter boundary
      (Jan/Apr/Jul/Oct 1st) -- an approximation; this script has no trading
      calendar, so it counts calendar days, not sessions,
  (4) the highest-value number: how many of those historical changes Task A's
      peak-drawdown rollover rule would now SUPPRESS -- i.e. reclassify the raw
      read from rising/falling to flat because the trajectory had already turned
      over by the final vintage.

Note on scope: (2)'s `window_rolloff` attribution is a DIFFERENT failure mode from
what Task A fixes. `window_rolloff` fires when the oldest vintage ages out BETWEEN
two different days' 6-vintage windows (a cross-day comparison, already instrumented
since 2026-07-28). Task A's rollover detection instead looks WITHIN a single day's
own trajectory for an interior peak/trough the head-to-tail comparison can't see.
A change can be `window_rolloff`-attributed, rollover-suppressed, both, or neither --
this script reports them as independent columns, never conflates them.

Why this is a script, not something run during the authoring session: this repo's
storage account lives in the EasyGridsProduction subscription under the
jgarrote@easygrids.com identity (see CLAUDE.md "Deployment lessons") -- a different
Entra tenant from this session's authenticated `az` context (QuirchFoodsSubscription /
jgarrote.admin@quirchfoods.com). `shared.storage`'s `DefaultAzureCredential` chain
will pick up a local `az login` session automatically once run under the right
identity; this script is committed so the check is reproducible by the account
holder rather than a finding taken on faith (same doctrine as
`scripts/probe_fmp_tier.py` / `scripts/probe_quadrant_basket_bias.py`).

Run (after `az login --use-device-code` as jgarrote@easygrids.com and
`az account set --subscription EasyGridsProduction`, per CLAUDE.md):
    PYTHONPATH=src python scripts/probe_gdpnow_rollover_history.py \\
        --start 2026-07-03 --end 2026-08-21

Or, to avoid needing live credentials, download the snapshot blobs once and point
the script at a local directory of `{date}.json` files:
    az storage blob download-batch --account-name stpfautoprod \\
        -s daily-snapshots -d /tmp/snapshots --auth-mode login
    python scripts/probe_gdpnow_rollover_history.py --snapshot-dir /tmp/snapshots
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_QUARTER_STARTS_MMDD = {(1, 1), (4, 1), (7, 1), (10, 1)}


def _near_quarter_boundary(d: date, window_days: int = 10) -> bool:
    for y in (d.year - 1, d.year, d.year + 1):
        for m, day in _QUARTER_STARTS_MMDD:
            qd = date(y, m, day)
            if abs((d - qd).days) <= window_days:
                return True
    return False


def _iter_local_snapshots(snapshot_dir: str):
    for name in sorted(os.listdir(snapshot_dir)):
        if not name.endswith(".json"):
            continue
        d = name[:-5]
        with open(os.path.join(snapshot_dir, name), encoding="utf-8") as f:
            yield d, json.load(f)


def _iter_azure_snapshots(start: str, end: str):
    from shared.storage import read_snapshot
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    d = d0
    while d <= d1:
        iso = d.isoformat()
        try:
            snap = read_snapshot(iso)
        except Exception:  # noqa: BLE001
            d += timedelta(days=1)
            continue
        if snap:
            yield iso, snap
        d += timedelta(days=1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2026-07-03",
                     help="first date to scan (growth_axis's own inception, PR #9)")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--snapshot-dir", help="local dir of {date}.json snapshot files (skips Azure)")
    args = ap.parse_args()

    from collector.handler import _detect_growth_rollover

    if args.snapshot_dir:
        rows = list(_iter_local_snapshots(args.snapshot_dir))
    else:
        rows = list(_iter_azure_snapshots(args.start, args.end))

    if not rows:
        print(f"No snapshots found in range {args.start}..{args.end} "
              f"(or empty --snapshot-dir) -- nothing to probe.")
        return

    entries = []
    for d, snap in rows:
        ga = (snap or {}).get("growth_axis") or {}
        entries.append({
            "date": d,
            "raw_direction": ga.get("raw_direction"),
            "basis": ga.get("basis"),
            "trajectory": ga.get("gdpnow_trajectory"),
            "vintage_count": ga.get("gdpnow_vintage_count"),
            "diagnostics": ga.get("direction_change_diagnostics"),
        })

    print(f"Scanned {len(entries)} snapshot(s), {entries[0]['date']} .. {entries[-1]['date']}\n")

    changes = []
    for prev, cur in zip(entries, entries[1:]):
        if cur["raw_direction"] and cur["raw_direction"] != prev["raw_direction"]:
            changes.append((prev, cur))

    if not changes:
        print("No raw_direction changes found in this window.")
        return

    print(f"{'date':<12}{'prior->new':<18}{'basis':<26}{'attribution':<18}"
          f"{'near_qtr':<10}{'would_suppress':<15}")
    suppressed_n = 0
    for prev, cur in changes:
        traj = cur["trajectory"] or []
        band = 0.1
        # cur["raw_direction"] IS the pre-Task-A head-to-tail read (old snapshots never
        # had rollover detection), so it's exactly the `head_to_tail_direction` argument
        # `_detect_growth_rollover` expects.
        cur_h2t = cur["raw_direction"]
        would_suppress = False
        if len(traj) >= 2 and cur_h2t in ("rising", "falling"):
            r = _detect_growth_rollover(traj, [], cur_h2t, band, band)
            would_suppress = bool(r["detected"])
        if would_suppress:
            suppressed_n += 1
        attribution = ((cur["diagnostics"] or {}).get("attribution")
                       if cur["diagnostics"] else "n/a (no diagnostics on this snapshot)")
        try:
            near_q = _near_quarter_boundary(date.fromisoformat(cur["date"]))
        except ValueError:
            near_q = None
        transition = f"{prev['raw_direction']}->{cur['raw_direction']}"
        print(f"{cur['date']:<12}{transition:<18}{str(cur['basis']):<26}"
              f"{str(attribution):<18}{str(near_q):<10}{str(would_suppress):<15}")

    print(f"\n{len(changes)} raw_direction change(s) found; "
          f"Task A's rollover rule would suppress {suppressed_n} of them "
          f"(i.e. keep the read at 'flat' instead of flipping).")


if __name__ == "__main__":
    main()
