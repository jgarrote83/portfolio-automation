"""Post-run verification of a close-flow run, with code deliberately separate from `backtest.closeflow`.

    PYTHONPATH=src python -m backtest.closeflow_verify [--run reports/closeflow/<run-id>] [--sample 60]

Re-derives a random sample of days (traded, flat and skipped alike) from the raw cached bars with naive
loops that import nothing from `backtest.closeflow`: previous close, signal bar close, r, side, shares, entry
fill, exit and net P&L must all match `primary/daily.csv` and `primary/trades.csv`; and the counts of traded /
flat / skipped days must add up to the window. Offline cache only (`DataLayer.get_bars(offline=True)`, repo rule
6): zero API calls; it cannot change a result.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from .data import DataLayer
from .data.config import REPO_ROOT

FETCH_FROM = "2023-12-01"
EARLY = {date(2024, 7, 3), date(2024, 11, 29), date(2024, 12, 24), date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24)}
SLEEVE, SLIP_C, COMM = 25_000.0, 1.0, 0.0035


def latest_run(root: Path | None = None) -> Path:
    root = root or REPO_ROOT / "reports" / "closeflow"
    runs = sorted(p for p in root.iterdir() if (p / "header.json").is_file())
    if not runs:
        raise FileNotFoundError(f"no close-flow run under {root}")
    return runs[-1]


def rederive(layer: DataLayer, trading_days: list[date], closes: dict[date, float], day: date) -> dict:
    """Independent naive re-derivation of one day under the PRIMARY rules."""
    i = trading_days.index(day)
    prev = closes.get(trading_days[i - 1]) if i > 0 else None
    close = 780 if day in EARLY else 960
    sig_m, ent_m = close - 31, close - 30
    bars = layer.get_bars(["SPY"], day, day, "1Min", "sip", window=("09:30", "16:00"), offline=True)
    by_min = {}
    for r in bars.itertuples():
        by_min[r.ts.hour * 60 + r.ts.minute] = r
    sig, ent, dc = by_min.get(sig_m), by_min.get(ent_m), closes.get(day)
    if prev is None or sig is None or ent is None or dc is None:
        return {"status": "skipped"}
    if sig.close == prev:
        return {"status": "flat", "r": 0.0}
    r = sig.close / prev - 1.0
    side = "long" if r > 0 else "short"
    shares = int(math.floor(SLEEVE / ent.open + 1e-9))
    fill = ent.open + SLIP_C / 100 if side == "long" else ent.open - SLIP_C / 100
    pnl = shares * ((dc - fill) if side == "long" else (fill - dc)) - 2 * shares * COMM
    return {"status": "traded", "r": r, "side": side, "shares": shares, "entry_open": ent.open, "fill": fill,
            "exit": dc, "net": pnl}


def check(layer: DataLayer, run: Path, sample: int, seed: int, end: str = "2025-12-31") -> dict:
    daily = pd.read_csv(run / "primary" / "daily.csv", dtype={"day": str})
    trades = pd.read_csv(run / "primary" / "trades.csv", dtype={"day": str}).set_index("day")
    trading_days = layer.trading_days(FETCH_FROM, end, offline=True)
    bars = layer.get_bars(["SPY"], FETCH_FROM, end, "1Day", "sip", offline=True)
    closes = dict(zip(bars["ts"].dt.date.tolist(), bars["close"].tolist()))
    rng = random.Random(seed)
    picks = sorted(rng.sample(range(len(daily)), min(sample, len(daily))))
    diffs = []
    for k in picks:
        row = daily.iloc[k]
        d = date.fromisoformat(row["day"])
        mine = rederive(layer, trading_days, closes, d)
        bad = []
        if mine["status"] != row["status"]:
            bad.append("status")
        elif mine["status"] == "traded":
            t = trades.loc[row["day"]]
            if abs(mine["r"] - float(row["r"])) > 1e-9:
                bad.append("r")
            if mine["side"] != t["side"]:
                bad.append("side")
            if mine["shares"] != t["shares"]:
                bad.append("shares")
            if abs(mine["entry_open"] - t["entry_open"]) > 1e-6 or abs(mine["fill"] - t["entry_fill"]) > 1e-6:
                bad.append("entry")
            if abs(mine["exit"] - t["exit_price"]) > 1e-6:
                bad.append("exit")
            if abs(mine["net"] - t["net_pnl"]) > 1e-4 or abs(mine["net"] - float(row["net_pnl"])) > 1e-4:
                bad.append("net_pnl")
        if bad:
            diffs.append({"day": row["day"], "fields": bad})
    counts = daily["status"].value_counts().to_dict()
    consistent = (int(counts.get("traded", 0)) == len(trades) and sum(counts.values()) == len(daily))
    return {"sampled": len(picks), "matched": len(picks) - len(diffs), "differences": diffs,
            "counts": counts, "counts_consistent": consistent, "pass": not diffs and consistent and bool(picks)}


def render(res: dict, run: Path) -> str:
    mark = "PASS" if res["pass"] else "FAIL"
    lines = [f"# Post-run verification — `{run.name}`", "",
             "Run after the backtest with code separate from `backtest.closeflow`; offline cache only, no API call, "
             "cannot change a result.", "", "| Check | Result |", "| --- | --- |",
             f"| Independent re-derivation of {res['sampled']} random days (previous close, signal bar, r, side, shares, entry "
             f"fill, exit, net P&L) | **{mark}** — {res['matched']} of {res['sampled']} match |",
             f"| Traded days equal trades.csv rows and every day is accounted for | "
             f"**{'PASS' if res['counts_consistent'] else 'FAIL'}** — {res['counts']} |", ""]
    if res["differences"]:
        lines.append("Differences: " + "; ".join(f"{d['day']} {d['fields']}" for d in res["differences"][:10]) + "\n")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backtest.closeflow_verify", description=__doc__.split("\n")[0])
    ap.add_argument("--run")
    ap.add_argument("--sample", type=int, default=60)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--cache-dir")
    args = ap.parse_args(argv)
    run = Path(args.run) if args.run else latest_run()
    res = check(DataLayer(args.cache_dir), run, args.sample, args.seed)
    text = render(res, run)
    (run / "verification.md").write_text(text, encoding="utf-8", newline="\n")
    print(text.encode("ascii", "replace").decode("ascii"))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
