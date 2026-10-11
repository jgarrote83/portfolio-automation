"""Post-run verification of an ORB-on-QQQ run, with code deliberately separate from the strategy modules.

    PYTHONPATH=src python -m backtest.orb_qqq_verify [--run reports/orb_qqq/<run-id>] [--n 50]

Re-derives a random sample of the run's PRIMARY trades (seed 11) from the raw cached one-minute and daily bars with naive
loops that import nothing from `orb`, `backtest.engine`, `backtest.orb_qqq` or `backtest.reports`: the opening bar from the
five one-minute bars, direction, trigger, the simple-mean ATR (14 prior true ranges), the stop (rounded away from the
entry), shares = floor(min(1% of the sleeve / stop distance, sleeve / trigger)), the entry bar and fill, the entry-bar
stop rule, later-bar stops (at the level or the open on a gap), the 15:59 (12:59 on a half-day) exit, and net P&L with 1
cent of slippage and $0.0035 of commission on every fill. Offline cache only: zero API calls; it cannot change a result.
A trade that exited on the daily loss limit is counted as not verifiable (the limit is inherited and untested here).
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

SYMBOL, SLEEVE, RISK_PCT, STOP_FRAC, SLIP, COMM = "QQQ", 25000.0, 1.0, 0.10, 0.01, 0.0035
EARLY = {date(2024, 7, 3), date(2024, 11, 29), date(2024, 12, 24), date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24)}
FETCH_FROM = "2023-12-01"


def latest_run(root: Path | None = None) -> Path:
    root = root or REPO_ROOT / "reports" / "orb_qqq"
    runs = sorted(p for p in root.iterdir() if (p / "header.json").is_file())
    if not runs:
        raise FileNotFoundError(f"no QQQ run under {root}")
    return runs[-1]


def load(layer: DataLayer, last_day: date):
    cal = layer.trading_days(FETCH_FROM, last_day, offline=True)
    d = layer.get_bars([SYMBOL], FETCH_FROM, last_day, "1Day", "sip", offline=True)
    daily = {r.ts.date(): (float(r.open), float(r.high), float(r.low), float(r.close)) for r in d.itertuples()}
    m = layer.get_bars([SYMBOL], "2024-01-02", last_day, "1Min", "sip", window=("09:30", "16:00"), offline=True)
    minute: dict[date, dict[int, tuple]] = {}
    for r in m.itertuples():
        minute.setdefault(r.ts.date(), {})[r.ts.hour * 60 + r.ts.minute] = (float(r.open), float(r.high), float(r.low), float(r.close))
    return cal, daily, minute


def derive(day: date, cal, daily, minute) -> dict | None:
    """The trade the rules produce on `day`, or None if they produce none."""
    i = cal.index(day)
    if i < 15:
        return None
    prior = cal[i - 14:i]
    trs, prev = [], daily[cal[i - 15]][3]
    for d in prior:
        _o, h, lo, c = daily[d]
        trs.append(max(h - lo, abs(h - prev), abs(lo - prev)))
        prev = c
    atr = sum(trs) / 14.0
    bars = minute[day]
    five = [bars[m] for m in range(570, 575)]
    o, h, lo, c = five[0][0], max(b[1] for b in five), min(b[2] for b in five), five[-1][3]
    if c == o:
        return None
    long_ = c > o
    trigger = h if long_ else lo
    raw = trigger - STOP_FRAC * atr if long_ else trigger + STOP_FRAC * atr
    n = round(raw / 0.01, 6)
    stop = round((math.floor(n) if long_ else math.ceil(n)) * 0.01, 6)
    dist = round(abs(trigger - stop), 6)
    shares = int(math.floor(min(SLEEVE * RISK_PCT / 100.0 / dist, SLEEVE / 1 / trigger) + 1e-9))
    if shares < 1:
        return None
    close_min = 780 if day in EARLY else 960
    last_entry, exit_min = close_min - 15, close_min - 1
    entry = None
    for m in sorted(bars):
        if m < 575 or m > last_entry:
            continue
        bo, bh, bl, bc = bars[m]
        trig_hit = bh >= trigger if long_ else bl <= trigger
        if not trig_hit:
            continue
        fill = (bo if bo > trigger else trigger) if long_ else (bo if bo < trigger else trigger)
        entry = (m, fill)
        stop_in_bar = bl <= stop if long_ else bh >= stop
        if stop_in_bar:
            return _trade(day, long_, shares, trigger, stop, dist, m, fill, m, stop, "stop_same_bar")
        break
    if entry is None:
        return None
    em, fill = entry
    last_close = bars[em][3]
    for m in sorted(bars):
        if m <= em or m > exit_min:
            continue
        bo, bh, bl, bc = bars[m]
        hit = bl <= stop if long_ else bh >= stop
        if hit:
            px = (bo if bo < stop else stop) if long_ else (bo if bo > stop else stop)
            return _trade(day, long_, shares, trigger, stop, dist, em, fill, m, px, "stop")
        last_close = bc
    xbar = bars.get(exit_min)
    return _trade(day, long_, shares, trigger, stop, dist, em, fill, exit_min, xbar[3] if xbar else last_close, "time")


def _trade(day, long_, shares, trigger, stop, dist, em, fill, xm, xpx, reason) -> dict:
    gross = (1 if long_ else -1) * (xpx - fill) * shares
    net = gross - 2 * shares * SLIP - 2 * shares * COMM
    return {"day": day.isoformat(), "side": "long" if long_ else "short", "trigger": trigger, "stop": stop, "shares": shares,
            "entry_fill": fill, "entry_minute": em, "exit_fill": xpx, "exit_minute": xm, "exit_reason": reason, "net_pnl": net}


def check(layer: DataLayer, run: Path, *, n: int = 50, seed: int = 11) -> dict:
    trades = pd.read_csv(run / "primary" / "trades.csv", dtype={"day": str})
    last = date.fromisoformat(trades["day"].max()) if len(trades) else date(2025, 12, 31)
    cal, daily, minute = load(layer, last)
    rng = random.Random(seed)
    picks = sorted(rng.sample(range(len(trades)), min(n, len(trades))))
    diffs, unverifiable = [], 0
    for k in picks:
        t = trades.iloc[k]
        if t["exit_reason"] == "loss_limit":
            unverifiable += 1
            continue
        mine = derive(date.fromisoformat(t["day"]), cal, daily, minute)
        if mine is None:
            diffs.append({"day": t["day"], "fields": ["no trade re-derived"]})
            continue
        bad = []
        for f in ("side", "exit_reason"):
            if mine[f] != t[f]:
                bad.append(f)
        for f in ("trigger", "stop", "entry_fill", "exit_fill"):
            if abs(mine[f] - float(t[f])) > 1e-5:
                bad.append(f)
        for f in ("shares", "entry_minute", "exit_minute"):
            if int(mine[f]) != int(t[f]):
                bad.append(f)
        if abs(mine["net_pnl"] - float(t["net_pnl"])) > 1e-4:
            bad.append("net_pnl")
        if bad:
            diffs.append({"day": t["day"], "fields": bad})
    sampled = len(picks) - unverifiable
    return {"sampled": sampled, "matched": sampled - len(diffs), "differences": diffs, "unverifiable_loss_limit": unverifiable,
            "pass": sampled > 0 and not diffs}


def render(res: dict, run: Path) -> str:
    lines = [f"# Post-run verification: `{run.name}`", "",
             "Run after the backtest with code separate from the strategy modules; offline cache only, no API call, cannot change a result.", "",
             "| Check | Result |", "| --- | --- |",
             f"| Independent re-derivation of {res['sampled']} random primary trades from the raw one-minute and daily bars (opening bar, side, trigger, "
             f"ATR, stop, shares, entry, exit, exit reason, net P&L) | **{'PASS' if res['pass'] else 'FAIL'}**: {res['matched']} of {res['sampled']} match |", ""]
    if res["unverifiable_loss_limit"]:
        lines.append(f"{res['unverifiable_loss_limit']} sampled trades exited on the daily loss limit and were not verified.\n")
    if res["differences"]:
        lines.append("Differences: " + "; ".join(f"{d['day']} {d['fields']}" for d in res["differences"][:12]) + "\n")
    lines.append(f"Overall: **{'PASS' if res['pass'] else 'FAIL'}**\n")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backtest.orb_qqq_verify", description=__doc__.split("\n")[0])
    ap.add_argument("--run")
    ap.add_argument("--n", type=int, default=50)
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
