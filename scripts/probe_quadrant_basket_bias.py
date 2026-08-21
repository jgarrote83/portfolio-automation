"""2026-08-21 measurement-integrity cycle — the headline probe this cycle
exists to answer.

The 2026-08-21 chart review reported these window basket returns
(2026-05-26 -> 2026-08-21): Q1 -0.53%, Q2 +4.82%, Q3 +4.06%, Q4 +0.60%. All
three known defects in `_quadrant_series` (A1: late-appearing members
based-in retroactively; A2: today's roster applied retroactively to
history; A3: a day missing one member silently averaged over the rest
instead of gapping) bias basket returns UPWARD -- so those published
figures are suspect in a known direction.

This script prints the SAME window's four basket returns under the OLD
(pre-fix) logic and the NEW (fixed) logic side by side, plus
`members_dropped`, per-quadrant coverage (`members_priced`/
`members_expected`), and `membership_basis` -- exactly the evidence the PR
body's verification section requires.

**Why this is a script, not something run during the authoring session:**
this repo's storage account lives in the EasyGridsProduction subscription
under the jgarrote@easygrids.com identity (see CLAUDE.md "Deployment
lessons") -- a different Entra tenant from this session's authenticated
`az` context. `shared.storage`'s `DefaultAzureCredential` chain will pick
up a local `az login` session automatically once run under the right
identity; this script is committed so the check is reproducible by the
account holder rather than a finding taken on faith (same doctrine as
`scripts/probe_fmp_tier.py`).

Run (after `az login --use-device-code` as jgarrote@easygrids.com and
`az account set --subscription EasyGridsProduction`, per CLAUDE.md):
    PYTHONPATH=src;web/api python scripts/probe_quadrant_basket_bias.py \\
        --start 2026-05-26 --end 2026-08-21

Or, to avoid needing live credentials at all, download the two blobs once
and point the script at local files:
    az storage blob download --account-name stpfautoprod -c performance \\
        -n equity-series.json -f /tmp/equity-series.json --auth-mode login
    az storage blob download --account-name stpfautoprod -c performance \\
        -n quadrant-config.json -f /tmp/quadrant-config.json --auth-mode login
    python scripts/probe_quadrant_basket_bias.py \\
        --equity-series /tmp/equity-series.json \\
        --quadrant-config /tmp/quadrant-config.json \\
        --start 2026-05-26 --end 2026-08-21
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "web", "api"))

QUADS = ["Q1", "Q2", "Q3", "Q4"]


def _old_quadrant_series(points: list[dict], quadrant_map: dict) -> list[dict]:
    """Byte-for-byte the PRE-2026-08-21-fix `_quadrant_series` (all three
    defects intact) — replicated here ONLY for this comparison, since the
    real function no longer has this behavior. Do not import/reuse
    elsewhere; this exists to answer one question and should be deleted
    once that question is answered."""
    bases: dict[str, float] = {}
    out: list[dict] = []
    for p in points:
        closes = p.get("closes") or {}
        for t, c in closes.items():
            if c and t not in bases:
                bases[t] = float(c)
        row: dict = {}
        for q, members in (quadrant_map or {}).items():
            vals = [
                float(closes[t]) / bases[t] * 100.0
                for t in members
                if closes.get(t) and bases.get(t)
            ]
            row[q] = round(sum(vals) / len(vals), 3) if vals else None
        out.append(row)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2026-05-26")
    ap.add_argument("--end", default="2026-08-21")
    ap.add_argument("--equity-series", help="local equity-series.json path (skips Azure)")
    ap.add_argument("--quadrant-config", help="local quadrant-config.json path (skips Azure)")
    args = ap.parse_args()

    if args.equity_series:
        with open(args.equity_series, encoding="utf-8") as f:
            series = json.load(f)
    else:
        from shared.storage import read_perf_series
        series = read_perf_series()

    if args.quadrant_config:
        with open(args.quadrant_config, encoding="utf-8") as f:
            qcfg = json.load(f)
    else:
        from shared.storage import read_json_blob
        qcfg = read_json_blob("performance", "quadrant-config.json") or {}

    qmap = qcfg.get("quadrants") or {}
    pts = [p for p in series if args.start <= (p.get("date") or "") <= args.end]
    if not pts:
        print(f"No points in window {args.start}..{args.end} -- nothing to probe.")
        return

    from function_app import _quadrant_series  # the real, FIXED function

    old_rows = _old_quadrant_series(pts, qmap)
    new_rows, coverage, meta = _quadrant_series(pts, qmap)

    print(f"Window: {pts[0]['date']} .. {pts[-1]['date']}  ({len(pts)} points)")
    print(f"membership_basis: {meta.get('membership_basis')}\n")

    print(f"{'Quadrant':<10}{'OLD ret %':>12}{'NEW ret %':>12}{'used':>8}{'dropped':>10}")
    for q in QUADS:
        old_first = next((r[q] for r in old_rows if r.get(q) is not None), None)
        old_last = next((r[q] for r in reversed(old_rows) if r.get(q) is not None), None)
        old_ret = round(old_last - old_first, 2) if (old_first and old_last) else None

        new_first = next((r[q] for r in new_rows if r.get(q) is not None), None)
        new_last = next((r[q] for r in reversed(new_rows) if r.get(q) is not None), None)
        new_ret = round(new_last - new_first, 2) if (new_first and new_last) else None

        qmeta = meta.get(q) or {}
        used_n = len(qmeta.get("members_used") or [])
        dropped_n = len(qmeta.get("members_dropped") or [])
        print(f"{q:<10}{str(old_ret):>12}{str(new_ret):>12}{used_n:>8}{dropped_n:>10}")
        if qmeta.get("members_dropped"):
            print(f"    dropped: {qmeta['members_dropped']}")

    print("\nPer-point coverage gaps (A3 — a day where NOT every fixed member priced):")
    gap_days = 0
    for p, cov in zip(pts, coverage):
        for q, c in cov.items():
            if c["members_priced"] < c["members_expected"]:
                gap_days += 1
                print(f"    {p.get('date')}  {q}: {c['members_priced']}/{c['members_expected']} priced")
    if not gap_days:
        print("    none")


if __name__ == "__main__":
    main()
