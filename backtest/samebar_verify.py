"""Post-run verification of a same-minute run, with code deliberately separate from `backtest.samebar`.

    PYTHONPATH=src python -m backtest.samebar_verify [--run reports/orb/samebar/<run-id>] [--n 40]

Re-derives, with naive loops that import nothing from `backtest.samebar`, a random sample of the run's results from
the raw cached bars and trade prints (offline cache only: zero API calls, it cannot change a result):

  A. the tick outcome (dip first / real stop) of changed trades, from the raw prints, with its own copy of the
     excluded condition codes (so a drift between the two copies shows up as a disagreement);
  B. the trade each changed trade became in the tick-resolved result (exit minute, exit fill, gross P&L), from the
     one-minute bars: a real stop is stopped at the stop level in the entry minute; a dip-first trade runs on with
     the stop checked from the NEXT minute, to the close of the 15:59 (13:00 half-day: 12:59) bar;
  C. the ambiguity classification and the optimistic gross R of a sample of ALL stop_same_bar trades (changed or not),
     from the entry-minute bar alone.
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from .data import DataLayer
from .data.config import REPO_ROOT

# the verifier's OWN copy of the excluded sale-condition codes
EXCLUDE = set("I4ZULTBW7VCNRPH")
HALF_DAYS = {date(2024, 7, 3), date(2024, 11, 29), date(2024, 12, 24), date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24)}
ET = "America/New_York"


def latest_run(root: Path | None = None) -> Path:
    root = root or REPO_ROOT / "reports" / "orb" / "samebar"
    runs = sorted(p for p in root.iterdir() if (p / "header.json").is_file())
    if not runs:
        raise FileNotFoundError(f"no same-minute run under {root}")
    return runs[-1]


def minute_bars(layer: DataLayer, symbol: str, day: date) -> dict[int, tuple]:
    df = layer.get_bars([symbol], day, day, "1Min", "sip", window=("09:30", "16:00"), offline=True)
    out = {}
    for r in df.itertuples():
        out[r.ts.hour * 60 + r.ts.minute] = (float(r.open), float(r.high), float(r.low), float(r.close))
    return out


def replay_ticks(layer: DataLayer, symbol: str, day: date, minute: int, side: str, trigger: float, stop: float) -> str:
    start = pd.Timestamp(f"{day.isoformat()} {minute // 60:02d}:{minute % 60:02d}:00", tz=ET)
    df = layer.get_trades(symbol, start, start + pd.Timedelta(seconds=60), offline=True)
    prints = []
    for ts, price, seq, conds in zip(df["ts"], df["price"], df["seq"], df["conditions"]):
        if any(c in EXCLUDE for c in conds):
            continue
        prints.append((ts.value, float(price), int(seq)))
    prints.sort(key=lambda p: (p[0], p[2]))
    long_ = side == "long"
    fill_i = None
    for i, (_t, px, _s) in enumerate(prints):
        if (px >= trigger) if long_ else (px <= trigger):
            fill_i = i
            break
    if fill_i is None:
        return "no_trigger_print" if prints else "unresolved"
    t_fill = prints[fill_i][0]
    hit_before = hit_same = hit_after = False
    for i, (t, px, _s) in enumerate(prints):
        touched = (px <= stop) if long_ else (px >= stop)
        if not touched:
            continue
        if t < t_fill:
            hit_before = True
        elif t == t_fill and i != fill_i:
            hit_same = True
        elif t > t_fill:
            hit_after = True
    if hit_after:
        return "real_stop"
    if hit_same:
        return "tie_real_stop"
    return "dip_first" if hit_before else "stop_never_touched"


def run_on(bars: dict[int, tuple], side: str, entry_minute: int, stop: float, last_minute: int):
    """Optimistic path after an entry in `entry_minute`: the stop is checked from the NEXT minute onward.
    -> (exit_minute, exit_fill, reason)."""
    long_ = side == "long"
    for m in sorted(bars):
        if m <= entry_minute or m > last_minute:
            continue
        o, h, lo, c = bars[m]
        if long_ and lo <= stop:
            return m, (o if o < stop else stop), "stop"
        if (not long_) and h >= stop:
            return m, (o if o > stop else stop), "stop"
        if m == last_minute:
            return m, c, "time"
    last = max((m for m in bars if entry_minute < m <= last_minute), default=None)
    return (last, bars[last][3], "time") if last is not None else (entry_minute, None, "none")


def check(layer: DataLayer, run: Path, *, n: int = 40, seed: int = 5, phase2_root: Path | None = None) -> dict:
    rng = random.Random(seed)
    cands = pd.read_csv(run / "candidates.csv", dtype={"day": str})
    p2 = Path(__import__("json").loads((run / "header.json").read_text(encoding="utf-8"))["phase2_run"])
    base = (phase2_root or REPO_ROOT / "reports" / "orb" / "phase2") / p2.name / "primary" / "trades.csv"
    prim = pd.read_csv(base, dtype={"day": str}).set_index(["day", "symbol"])
    tick = pd.read_csv(run / "tick_resolved" / "trades.csv", dtype={"day": str}).set_index(["day", "symbol"])
    changed = cands[cands["changed"]]
    out = {"A": {"sampled": 0, "differences": []}, "B": {"sampled": 0, "differences": []}, "C": {"sampled": 0, "differences": []}}

    # ---- A and B: changed trades, half dips and half real stops --------------------------------------
    picks = []
    for kind in ("dip_first", "real_stop"):
        pool = changed[changed["tick_outcome"] == kind]
        picks += pool.iloc[sorted(rng.sample(range(len(pool)), min(n // 2, len(pool))))].to_dict("records")
    for r in picks:
        day = date.fromisoformat(r["day"])
        mine = replay_ticks(layer, r["symbol"], day, int(r["entry_minute"]), r["side"], float(r["trigger"]), float(r["stop"]))
        out["A"]["sampled"] += 1
        if mine != r["tick_outcome"]:
            out["A"]["differences"].append(f"{r['day']} {r['symbol']}: run says {r['tick_outcome']}, replay says {mine}")
        bars = minute_bars(layer, r["symbol"], day)
        row = tick.loc[(r["day"], r["symbol"])]
        out["B"]["sampled"] += 1
        if mine in ("real_stop", "tie_real_stop"):
            ok = (row["exit_reason"] == "stop_same_bar" and abs(row["exit_fill"] - float(r["stop"])) < 1e-6
                  and int(row["exit_minute"]) == int(r["entry_minute"]))
        else:
            last = 779 if day in HALF_DAYS else 959
            xm, xf, why = run_on(bars, r["side"], int(r["entry_minute"]), float(r["stop"]), last)
            ok = (row["exit_reason"] == why and int(row["exit_minute"]) == xm and abs(row["exit_fill"] - xf) < 1e-6)
        if not ok:
            out["B"]["differences"].append(f"{r['day']} {r['symbol']}: trade {row['exit_reason']}@{row['exit_fill']} "
                                           f"minute {row['exit_minute']} does not match the replay")

    # ---- C: any stop_same_bar trade, classified from the entry-minute bar alone ---------------------------
    pool = cands.iloc[sorted(rng.sample(range(len(cands)), min(n * 2, len(cands))))].to_dict("records")
    for r in pool:
        day = date.fromisoformat(r["day"])
        bars = minute_bars(layer, r["symbol"], day)
        o, h, lo = bars[int(r["entry_minute"])][:3]
        long_ = r["side"] == "long"
        trig, stop = float(r["trigger"]), float(r["stop"])
        amb = (o < trig) if long_ else (o > trig)
        last = 779 if day in HALF_DAYS else 959
        xm, xf, _why = run_on(bars, r["side"], int(r["entry_minute"]), stop, last)
        risk = abs(trig - stop)
        r_opt = ((xf - trig) if long_ else (trig - xf)) / risk
        changed_mine = amb and abs(r_opt - (-1.0)) > 0.01
        out["C"]["sampled"] += 1
        bad = []
        if amb != bool(r["ambiguous"]):
            bad.append("ambiguous")
        if bool(r["ambiguous"]) and abs(r_opt - float(r["r_gross_optimistic"])) > 1e-6:
            bad.append(f"optimistic R {r_opt:.4f} vs {float(r['r_gross_optimistic']):.4f}")
        if changed_mine != bool(r["changed"]):
            bad.append("changed")
        if not (h >= trig if long_ else lo <= trig) or not (lo <= stop if long_ else h >= stop):
            bad.append("bar does not reach both the trigger and the stop")
        pr = prim.loc[(r["day"], r["symbol"])]
        if pr["exit_reason"] != "stop_same_bar" or abs(pr["trigger"] - trig) > 1e-6 or abs(pr["stop"] - stop) > 1e-6:
            bad.append("Phase 2 primary row differs")
        if bad:
            out["C"]["differences"].append(f"{r['day']} {r['symbol']}: " + "; ".join(bad))
    out["pass"] = all(v["sampled"] > 0 and not v["differences"] for v in (out["A"], out["B"], out["C"]))
    return out


def render(res: dict, run: Path) -> str:
    rows = [("A", "tick outcome of changed trades (half dip-first, half real-stop) re-derived from the raw prints with the verifier's own code list"),
            ("B", "the resulting tick-resolved trade (exit minute, exit fill, exit reason) re-derived from the one-minute bars"),
            ("C", "ambiguity, `changed` and the optimistic gross R of random stop_same_bar trades (changed or not), from the entry-minute bar alone")]
    lines = [f"# Post-run verification: `{run.name}`", "",
             "Run after the diagnostic with code separate from `backtest.samebar`; offline cache only, no API call, cannot change a result.",
             "", "| Check | Result |", "| --- | --- |"]
    for key, label in rows:
        r = res[key]
        lines.append(f"| {key}. {r['sampled']} {label} | **{'PASS' if not r['differences'] and r['sampled'] else 'FAIL'}**: "
                     f"{r['sampled'] - len(r['differences'])} of {r['sampled']} match |")
    lines.append("")
    for key in ("A", "B", "C"):
        if res[key]["differences"]:
            lines.append(f"Differences ({key}): " + "; ".join(res[key]["differences"][:10]))
    lines.append(f"Overall: **{'PASS' if res['pass'] else 'FAIL'}**")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backtest.samebar_verify", description=__doc__.split("\n")[0])
    ap.add_argument("--run")
    ap.add_argument("--n", type=int, default=40, help="changed trades to re-derive (half of each outcome); C uses 2n")
    ap.add_argument("--seed", type=int, default=5)
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
