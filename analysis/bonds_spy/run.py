"""Run the bonds-vs-SPY exploratory analysis end to end.

    python -m analysis.bonds_spy.run [--online]

Reads the bars through `backtest.data.get_bars` (offline by default: the cache was filled once), builds the day table, answers Q1-Q4, runs the
independent verifier, and writes:
    reports/bonds_spy/day_table.csv, reports/bonds_spy/results.json   (gitignored)
    .review/bonds-spy-analysis.md                                      (the report)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import analyse, data, report, returns, verify

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "reports" / "bonds_spy"
REPORT = REPO_ROOT / ".review" / "bonds-spy-analysis.md"


def compute(daily, minute) -> tuple[dict, "object"]:
    dmap = data.daily_maps(daily)
    closes, open10 = data.minute_maps(minute)
    calendar = sorted(dmap["SPY"])
    table, skips = returns.build_day_table(calendar, dmap, open10, data.FIRST_DAY, data.LAST_DAY)
    days = list(table.index)
    res = {
        "counts": {"daily_rows": {s: int((daily["symbol"] == s).sum()) for s in data.SYMBOLS},
                   "minute_bars": {s: int((minute["symbol"] == s).sum()) for s in data.SYMBOLS},
                   "minute_days": {s: len(closes[s]) for s in data.SYMBOLS}},
        "skips": {"days_in_window": skips["days_in_window"], "used": skips["used"], "by_reason": skips["by_reason"],
                  "skipped_days": {str(k): v for k, v in skips["skipped_days"].items()}},
        "describe": analyse.describe(table),
        "q1": analyse.q1_correlations(table),
        "q1_quarterly": analyse.q1_quarterly(table),
        "q2": analyse.q2_cross_correlation(days, closes),
        "q3": analyse.q3_all(table),
        "q4": analyse.q4_big_days(table),
        "q4_robustness": analyse.q4_robustness(table),
        "month_start": analyse.month_start_sensitivity(table, returns.first_trading_day_of_month(calendar)),
        "coverage": analyse.minute_coverage(days, closes),
    }
    return res, table


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="analysis.bonds_spy.run")
    ap.add_argument("--online", action="store_true", help="allow read-only fetches for anything not cached")
    args = ap.parse_args(argv)
    daily, minute = data.load_frames(offline=not args.online)
    res, table = compute(daily, minute)
    OUT.mkdir(parents=True, exist_ok=True)
    table.reset_index().assign(date=lambda f: f["date"].astype(str)).to_csv(OUT / "day_table.csv", index=False)
    ver = verify.check(OUT / "day_table.csv")
    res["verification"] = ver
    (OUT / "results.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(report.render(res), encoding="utf-8")
    print(f"days used {res['skips']['used']} of {res['skips']['days_in_window']}; verification {ver['matched']}/{ver['values_checked']} "
          f"{'PASS' if ver['pass'] else 'FAIL'}; report {REPORT}")
    return 0 if ver["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
