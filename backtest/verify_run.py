"""Post-run verification of a Phase 2 backtest run, with code deliberately separate from the engine.

    PYTHONPATH=src python -m backtest.verify_run [--run reports/orb/phase2/<run-id>] [--sample 80]

Three checks, none of which can change a result (they only read the run's outputs and the cache, offline):

  1. REPLAY: re-derive a random sample of primary-variant trades from the raw cached bars with naive
     code that imports nothing from `orb` or `backtest.engine` -- side, trigger, simple-mean ATR,
     relative volume, stop, shares, entry, exit and exit reason must all match `trades.csv`.
  2. SELECTION vs PHASE 1: every ETFs-included trade symbol must be in the Phase 1 report's
     `sip_top20` for that day (an independent pandas implementation of the same universe / RVOL).
  3. EARLY CLOSES: the days whose volume collapses right after 13:00 (across every traded symbol's
     one-minute bars) must be exactly the NYSE early-close table. This replaces the in-run "last SPY bar"
     comparison, which cannot work: extended-hours bars exist after a 13:00 close, so the last bar is 15:59
     on every day.

Data is read only through `DataLayer.get_bars(offline=True)` (repo rule 6): zero API calls, and a pair
that was never cached raises instead of being fetched.
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
from .sessions import EARLY_CLOSE_DATES

FETCH_FROM = "2023-12-01"
PRE_MIN = (12 * 60 + 31, 13 * 60)            # 12:31-12:59, the half hour before an early close
POST_MIN = (13 * 60 + 1, 13 * 60 + 30)       # 13:01-13:29, after the close (the 13:00 bar holds the auction)


def latest_run(root: Path | None = None) -> Path:
    root = root or REPO_ROOT / "reports" / "orb" / "phase2"
    runs = sorted(p for p in root.iterdir() if (p / "header.json").is_file())
    if not runs:
        raise FileNotFoundError(f"no Phase 2 run under {root}")
    return runs[-1]


def _mins(ts: pd.Series) -> pd.Series:
    return ts.dt.hour * 60 + ts.dt.minute


# ============================================================================ 1. replay
def replay_trade(layer: DataLayer, days: list[date], t: pd.Series) -> list[str]:
    """Names of the fields where an independent re-derivation of trade `t` disagrees with it."""
    sym, day, side = t["symbol"], date.fromisoformat(t["day"]), t["side"]
    i = days.index(day)
    prior = days[i - 14:i]
    daily = layer.get_bars([sym], days[i - 15], day, "1Day", "sip", offline=True)
    dd = daily.assign(d=daily["ts"].dt.date).set_index("d")
    o5 = layer.get_bars([sym], days[i - 14], day, "5Min", "sip", window=("09:30", "09:35"), offline=True)
    o5 = o5.assign(d=o5["ts"].dt.date).set_index("d")
    ob = o5.loc[day]
    exp_side = "long" if ob["close"] > ob["open"] else "short"
    trigger = ob["high"] if exp_side == "long" else ob["low"]
    # simple-mean ATR of the 14 prior bars (the oldest bar's previous close is days[i-15], if present)
    pc = dd.loc[days[i - 15], "close"] if days[i - 15] in dd.index else None
    trs = []
    for p in prior:
        h, lo, c = dd.loc[p, "high"], dd.loc[p, "low"], dd.loc[p, "close"]
        trs.append(h - lo if pc is None else max(h - lo, abs(h - pc), abs(lo - pc)))
        pc = c
    atr = sum(trs) / 14
    vols = o5["volume"]
    avg = sum(float(vols.get(p, 0.0)) for p in prior) / 14
    rvol = float(vols.get(day, 0.0)) / avg
    raw_stop = trigger - 0.1 * atr if exp_side == "long" else trigger + 0.1 * atr
    n = round(raw_stop / 0.01, 6)
    stop = round((math.floor(n) if exp_side == "long" else math.ceil(n)) * 0.01, 6)
    dist = abs(trigger - stop)
    shares = int(math.floor(min(250.0 / dist, 1250.0 / trigger) + 1e-9))

    close = 780 if day in EARLY_CLOSE_DATES else 960
    bars = layer.get_bars([sym], day, day, "1Min", "sip", window=("09:30", "16:00"), offline=True)
    bars = bars.assign(m=_mins(bars["ts"]))
    bars = bars[(bars["m"] >= 575) & (bars["m"] <= close - 1)]
    entry = exit_ = reason = None
    last_close = None
    for b in bars.itertuples():
        if entry is None:
            if b.m > close - 15:
                break
            hit = b.high >= trigger if exp_side == "long" else b.low <= trigger
            if hit:
                beyond = b.open > trigger if exp_side == "long" else b.open < trigger
                entry = b.open if beyond else trigger
                same = b.low <= stop if exp_side == "long" else b.high >= stop
                if same:
                    exit_, reason = stop, "stop_same_bar"
                    break
                last_close = b.close
        else:
            hit = b.low <= stop if exp_side == "long" else b.high >= stop
            if hit:
                gap = b.open < stop if exp_side == "long" else b.open > stop
                exit_, reason = (b.open if gap else stop), "stop"
                break
            last_close = b.close
    if entry is not None and exit_ is None:
        exit_, reason = last_close, "time"
    checks = {
        "side": exp_side == side, "trigger": abs(trigger - t["trigger"]) < 1e-6,
        "atr": abs(atr - t["atr"]) < 1e-6, "rvol": abs(rvol - t["rvol"]) < 1e-6,
        "stop": abs(stop - t["stop"]) < 1e-6, "shares": shares == t["shares"],
        "entry": entry is not None and abs(entry - t["entry_fill"]) < 1e-6,
        "exit": exit_ is not None and abs(exit_ - t["exit_fill"]) < 1e-6,
        "reason": reason == t["exit_reason"],
    }
    return [k for k, ok in checks.items() if not ok]


def check_replay(layer: DataLayer, run: Path, sample: int, seed: int, end: str = "2025-12-31") -> dict:
    trades = pd.read_csv(run / "primary" / "trades.csv", dtype={"day": str})
    days = layer.trading_days(FETCH_FROM, end, offline=True)
    rng = random.Random(seed)
    idx = sorted(rng.sample(range(len(trades)), min(sample, len(trades))))
    diffs = []
    for k in idx:
        t = trades.iloc[k]
        bad = replay_trade(layer, days, t)
        if bad:
            diffs.append({"symbol": t["symbol"], "day": t["day"], "fields": bad})
    return {"sampled": len(idx), "matched": len(idx) - len(diffs), "differences": diffs,
            "pass": not diffs and len(idx) > 0}


# ================================================================ 2. selection vs Phase 1
def check_selection(run: Path, phase1_csv: Path) -> dict:
    p1 = pd.read_csv(phase1_csv, dtype={"date": str})
    top = {r["date"]: set(str(r["sip_top20"]).split()) for _, r in p1.iterrows()}
    etf = pd.read_csv(run / "etfs_included" / "trades.csv", dtype={"day": str})
    outside = [(r["day"], r["symbol"]) for _, r in etf.iterrows() if r["symbol"] not in top.get(r["day"], set())]
    return {"etfs_included_trades": len(etf), "not_in_phase1_top20": len(outside), "examples": outside[:5],
            "pass": len(etf) > 0 and not outside}


# ================================================================== 3. early closes
def check_early_closes_by_volume(layer: DataLayer, run: Path) -> dict:
    """Across every symbol that traded on a day, volume in the half hour after 13:00 vs the half hour
    before it. The k smallest ratios must be exactly the table's early-close days that fall in the window."""
    trades = pd.read_csv(run / "primary" / "trades.csv", dtype={"day": str})
    ratios: dict[str, float] = {}
    for day_s, g in trades.groupby("day"):
        day = date.fromisoformat(day_s)
        syms = sorted(set(g["symbol"]))
        bars = layer.get_bars(syms, day, day, "1Min", "sip", window=("09:30", "16:00"), offline=True)
        if bars.empty:
            continue
        m, v = _mins(bars["ts"]), bars["volume"]
        pre = int(v[(m >= PRE_MIN[0]) & (m < PRE_MIN[1])].sum())
        post = int(v[(m >= POST_MIN[0]) & (m < POST_MIN[1])].sum())
        ratios[day_s] = post / max(pre, 1)
    table = sorted(d.isoformat() for d in EARLY_CLOSE_DATES if d.isoformat() in ratios)
    ordered = sorted(ratios, key=ratios.get)
    lowest = sorted(ordered[:len(table)])
    others = [ratios[d] for d in ordered if d not in table]
    return {"days_checked": len(ratios), "table_days": table, "lowest_days": lowest,
            "table_ratios": {d: round(ratios[d], 3) for d in table},
            "max_table_ratio": round(max((ratios[d] for d in table), default=0.0), 3),
            "min_other_ratio": round(min(others), 3) if others else None,
            "median_other_ratio": round(float(pd.Series(others).median()), 3) if others else None,
            "pass": lowest == table and (not others or not table or max(ratios[d] for d in table) < min(others))}


# ======================================================================================= main
def render(res: dict, run: Path) -> str:
    r, s, e = res["replay"], res["selection"], res["early_closes"]
    mark = lambda ok: "PASS" if ok else "FAIL"      # noqa: E731
    lines = [f"# Post-run verification — `{run.name}`", "",
             "Run after the backtest, with code separate from the engine (`backtest/verify_run.py`); reads the "
             "run's outputs and the offline cache only, makes no API call and cannot change a result.", "",
             "| Check | Result |", "| --- | --- |",
             f"| Independent replay of {r['sampled']} random primary trades from the raw cached bars "
             f"(side, trigger, ATR, RVOL, stop, shares, entry, exit, exit reason) | **{mark(r['pass'])}** — "
             f"{r['matched']} of {r['sampled']} match |",
             f"| ETFs-included trades vs the Phase 1 report's independent top-20 | **{mark(s['pass'])}** — "
             f"{s['not_in_phase1_top20']} of {s['etfs_included_trades']:,} trades outside it |",
             f"| Early-close table vs the volume collapse after 13:00 | **{mark(e['pass'])}** — lowest "
             f"{len(e['table_days'])} days {'==' if e['lowest_days'] == e['table_days'] else '!='} the table; "
             f"table ratios at most {e['max_table_ratio']}, every other day at least {e['min_other_ratio']} "
             f"(median {e['median_other_ratio']}) |", ""]
    if r["differences"]:
        lines += ["Replay differences: " + "; ".join(f"{d['symbol']} {d['day']} {d['fields']}" for d in r["differences"][:10]), ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backtest.verify_run", description=__doc__.split("\n")[0])
    ap.add_argument("--run", help="run directory (default: the latest under reports/orb/phase2)")
    ap.add_argument("--sample", type=int, default=80)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--phase1-csv", default=str(REPO_ROOT / "reports" / "orb" / "iex_vs_sip_2024_2025.csv"))
    ap.add_argument("--cache-dir")
    args = ap.parse_args(argv)
    run = Path(args.run) if args.run else latest_run()
    layer = DataLayer(args.cache_dir)
    res = {"replay": check_replay(layer, run, args.sample, args.seed),
           "selection": check_selection(run, Path(args.phase1_csv)),
           "early_closes": check_early_closes_by_volume(layer, run)}
    text = render(res, run)
    (run / "verification.md").write_text(text, encoding="utf-8", newline="\n")
    print(text.encode("ascii", "replace").decode("ascii"))      # a cp1252 console cannot print the dashes
    return 0 if all(v["pass"] for v in res.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
