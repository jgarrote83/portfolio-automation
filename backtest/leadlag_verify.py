"""Post-run verification of a lead-lag run, with code deliberately separate from `backtest.leadlag`/`backtest.foreign`.

    PYTHONPATH=src python -m backtest.leadlag_verify [--run reports/global_leadlag/<run-id>] [--n 30]

Re-derives a random sample of primary-rule trade-days (all four groups pooled, seed 11) from the RAW cached data with
naive loops: for each sampled (group, day D) it reads each member's Yahoo parquet itself, finds the row dated D and the
previous row, applies the date rule (Asian close / European OPEN, never a European close), the split rule and the run's
recorded exclusions, takes the regional means and the signal, and recomputes side, shares and net P&L from the US
daily bar (offline `get_bars`). It also checks that the cache holds no 2026 row. Offline only: zero API calls; it
cannot change a result.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from .data import DataLayer
from .data.config import REPO_ROOT

FOREIGN = REPO_ROOT / "data" / "reference" / "foreign" / "yahoo"
COMMISSION, CAPITAL = 0.0035, 25000.0


def latest_run(root: Path | None = None) -> Path:
    root = root or REPO_ROOT / "reports" / "global_leadlag"
    runs = sorted(p for p in root.iterdir() if (p / "header.json").is_file())
    if not runs:
        raise FileNotFoundError(f"no lead-lag run under {root}")
    return runs[-1]


def read_member(symbol: str, root: Path = FOREIGN):
    df = pd.read_parquet(root / f"{symbol}.parquet")
    meta = json.loads((root / f"{symbol}.json").read_text(encoding="utf-8"))
    rows = [(pd.Timestamp(d).date(), float(o), float(c)) for d, o, c in zip(df["date"], df["open"], df["close"])]
    splits = [date.fromisoformat(e["date"]) for e in meta.get("splits", [])]
    return rows, splits


def member_return(rows, splits, d: date, europe: bool):
    """Naive re-derivation. -> return, or None when the member is unavailable on d."""
    dates = [r[0] for r in rows]
    if d not in dates:
        return None
    i = dates.index(d)
    if i == 0:
        return None
    prev_close = rows[i - 1][2]
    now = rows[i][1] if europe else rows[i][2]                 # Europe/UK: the OPEN on D; Asia: the CLOSE of the session dated D
    if not (now > 0 and prev_close > 0) or math.isnan(now) or math.isnan(prev_close):
        return None
    if any(rows[i - 1][0] < sd <= d for sd in splits):
        return None
    return now / prev_close - 1.0


def check(layer: DataLayer, run: Path, *, n: int = 30, seed: int = 11, foreign_root: Path = FOREIGN) -> dict:
    header = json.loads((run / "header.json").read_text(encoding="utf-8"))
    excluded = set(header.get("excluded_members", {}))
    groups = header["groups"]
    pool = []
    for g in groups:
        path = run / g / "primary" / "trades.csv"
        with path.open(newline="", encoding="utf-8") as f:
            pool += [(g, r) for r in csv.DictReader(f)]
    rng = random.Random(seed)
    picks = sorted(rng.sample(range(len(pool)), min(n, len(pool))))
    cache, diffs = {}, []
    leaks = []
    for k in picks:
        g, row = pool[k]
        d = date.fromisoformat(row["day"])
        spec = groups[g]
        regional = []
        for region, europe in (("asia", False), ("europe", True)):
            vals = []
            for s in spec[region]:
                if s in excluded:
                    continue
                if s not in cache:
                    cache[s] = read_member(s, foreign_root)
                rows, splits = cache[s]
                r = member_return(rows, splits, d, europe)
                if r is not None:
                    vals.append(r)
            if vals:
                regional.append(sum(vals) / len(vals))
        bad = []
        if not regional:
            bad.append("no signal re-derived")
        else:
            s_mine = sum(regional) / len(regional)
            if abs(s_mine - float(row["signal"])) > 1e-9:
                bad.append(f"signal {s_mine:.8f} vs {float(row['signal']):.8f}")
            side = "long" if s_mine > 0 else "short" if s_mine < 0 else None
            if side != row["side"]:
                bad.append(f"side {side} vs {row['side']}")
        bars = layer.get_bars([spec["us"]], d, d, "1Day", "sip", offline=True)
        if bars.empty:
            bad.append("no US bar")
        else:
            o, c = float(bars["open"].iloc[0]), float(bars["close"].iloc[0])
            shares = int(math.floor(CAPITAL / o + 1e-9))
            sign = 1.0 if row["side"] == "long" else -1.0
            net = sign * shares * (c - o) - 2 * shares * COMMISSION
            if shares != int(row["shares"]):
                bad.append(f"shares {shares} vs {row['shares']}")
            if abs(o - float(row["entry"])) > 1e-6 or abs(c - float(row["exit"])) > 1e-6:
                bad.append("entry/exit differ from the daily bar")
            if abs(net - float(row["net_pnl"])) > 1e-4:
                bad.append(f"net P&L {net:.4f} vs {float(row['net_pnl']):.4f}")
        if bad:
            diffs.append({"group": g, "day": row["day"], "fields": bad})
    for s in sorted(cache):
        if any(r[0] >= date(2026, 1, 1) for r in cache[s][0]):
            leaks.append(s)
    res = {"sampled": len(picks), "matched": len(picks) - len(diffs), "differences": diffs, "members_read": len(cache),
           "holdout_rows_in_cache": leaks}
    res["pass"] = res["sampled"] > 0 and not diffs and not leaks
    return res


def render(res: dict, run: Path) -> str:
    lines = [f"# Post-run verification: `{run.name}`", "",
             "Run after the study with code separate from `backtest.leadlag`; offline cache only, no API call, cannot change a result.", "",
             "| Check | Result |", "| --- | --- |",
             f"| Independent re-derivation of {res['sampled']} random primary-rule trade-days (all four groups pooled) from the raw Yahoo cache and the US daily bar "
             f"(date rule, splits, exclusions, regional means, signal, side, shares, entry, exit, net P&L) | "
             f"**{'PASS' if not res['differences'] and res['sampled'] else 'FAIL'}**: {res['matched']} of {res['sampled']} match "
             f"({res['members_read']} members read) |",
             f"| No 2026 row in the foreign cache | **{'PASS' if not res['holdout_rows_in_cache'] else 'FAIL: ' + ', '.join(res['holdout_rows_in_cache'])}** |", ""]
    if res["differences"]:
        lines.append("Differences: " + "; ".join(f"{d['group']} {d['day']} {d['fields']}" for d in res["differences"][:12]) + "\n")
    lines.append(f"Overall: **{'PASS' if res['pass'] else 'FAIL'}**\n")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backtest.leadlag_verify", description=__doc__.split("\n")[0])
    ap.add_argument("--run")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--cache-dir")
    args = ap.parse_args(argv)
    run = Path(args.run) if args.run else latest_run()
    res = check(DataLayer(args.cache_dir), run, n=args.n, seed=args.seed)
    text = render(res, run)
    (run / "verification.md").write_text(text, encoding="utf-8", newline="\n")
    print(text.encode("ascii", "replace").decode("ascii"))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
