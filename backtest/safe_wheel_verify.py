"""Post-run verification of a safe-wheel model run, with code deliberately separate from the strategy modules.

    PYTHONPATH=src python -m backtest.safe_wheel_verify [--run reports/safe_wheel_model/<run-id>] [--n 30]

Re-prices a random sample of the run's PRIMARY positions (seed 11) from the raw cached inputs (the parquet files under
`data/reference/wheel/`, read directly) with naive loops that import nothing from `backtest.safe_wheel`,
`backtest.wheel_data` or `backtest.safe_wheel_report`, and that compute the normal distribution function by a different
formula (complementary error function). Per position it re-derives, knowing only which expiry was traded: the expiry
(third Friday, or the trading day before it), the entry day (the closest trading day to 35 days before, ties earlier),
the spot, VIX, T-bill rate and dividend yield, the short strike (a scan of whole-dollar strikes for the delta closest to
-0.20, ties lower), the width, the credit, the contracts, the daily marks, the profit-take / seven-day exit, and the net
P&L. Whether a cycle was taken at all (kill switch) is the run's own decision and is not re-derived. Offline: no network.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .data.config import REPO_ROOT

WHEEL = REPO_ROOT / "data" / "reference" / "wheel"
# This verifier's own copies of the pre-registered primary parameters.
ENTRY_DAYS, EXIT_DAYS, DELTA_TARGET, WIDTH_PCT, SKEW, HAIRCUT, FEE = 35, 7, -0.20, 2.0, 0.5, 0.03, 0.65
PT_FRAC, BUDGET, MULT = 0.5, 2500.0, 100


def latest_run(root: Path | None = None) -> Path:
    root = root or REPO_ROOT / "reports" / "safe_wheel_model"
    runs = sorted(p for p in root.iterdir() if (p / "header.json").is_file())
    if not runs:
        raise FileNotFoundError(f"no safe-wheel run under {root}")
    return runs[-1]


# ---- the normal distribution function by a different route than the strategy module (erfc, not erf)
def cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def put_value(spot: float, strike: float, years: float, rate: float, qy: float, vix: float) -> float:
    sigma = vix / 100.0 + SKEW * max(0.0, math.log(spot / strike))
    d1 = (math.log(spot / strike) + (rate - qy + sigma * sigma / 2.0) * years) / (sigma * math.sqrt(years))
    d2 = d1 - sigma * math.sqrt(years)
    return strike * math.exp(-rate * years) * cdf(-d2) - spot * math.exp(-qy * years) * cdf(-d1)


def put_slope(spot: float, strike: float, years: float, rate: float, qy: float, vix: float) -> float:
    sigma = vix / 100.0 + SKEW * max(0.0, math.log(spot / strike))
    d1 = (math.log(spot / strike) + (rate - qy + sigma * sigma / 2.0) * years) / (sigma * math.sqrt(years))
    return math.exp(-qy * years) * (cdf(d1) - 1.0)


# ---- raw inputs, read straight from the cache
def load_raw(symbol: str = "SPY", vol: str = "VIXCLS"):
    u = pd.read_parquet(WHEEL / "yahoo" / f"{symbol}.parquet")
    days = [pd.Timestamp(d).date() for d in u["date"]]
    close = {d: float(c) for d, c in zip(days, u["close"])}
    divs = [(pd.Timestamp(d).date(), float(a)) for d, a in zip(u["date"], u["dividends"]) if a > 0]
    series = {}
    for name in (vol, "DTB3"):
        f = pd.read_parquet(WHEEL / "fred" / f"{name}.parquet")
        series[name] = sorted((pd.Timestamp(d).date(), float(v)) for d, v in zip(f["date"], f["value"]) if pd.notna(v))
    return days, close, divs, series[vol], series["DTB3"]


def last_obs(obs: list[tuple[date, float]], day: date) -> float:
    val = None
    for d, v in obs:                       # naive scan; the list is sorted
        if d <= day:
            val = v
        else:
            break
    if val is None:
        raise ValueError(f"no observation on or before {day}")
    return val


def third_friday_of(year: int, month: int) -> date:
    fridays = [date(year, month, d) for d in range(1, 29) if date(year, month, d).weekday() == 4]
    return fridays[2]


def derive(expiry_used: date, days: list[date], close, divs, vix_obs, rate_obs) -> dict:
    """Re-derive one position from its expiry alone."""
    tf = third_friday_of(expiry_used.year, expiry_used.month)
    dayset = set(days)
    expiry = tf if tf in dayset else max(d for d in days if d < tf)
    target = expiry - timedelta(days=ENTRY_DAYS)
    best = min(days, key=lambda d: (abs((d - target).days), d))              # closest to the target, ties earlier
    entry = best
    s = close[entry]
    vix = last_obs(vix_obs, entry)
    r = last_obs(rate_obs, entry) / 100.0
    qy = sum(a for x, a in divs if entry - timedelta(days=365) < x <= entry) / s
    t = (expiry - entry).days / 365.0
    best_k, best_dist = None, 1e9
    for k in range(1, int(s) + 1):
        dist = abs(put_slope(s, float(k), t, r, qy, vix) - DELTA_TARGET)
        if dist < best_dist - 0.0:                                         # strictly closer replaces; a tie keeps the lower strike
            best_k, best_dist = k, dist
    k_short = best_k
    width = max(1, int(math.floor(WIDTH_PCT / 100.0 * s + 0.5)))
    k_long = k_short - width
    mid = put_value(s, float(k_short), t, r, qy, vix) - put_value(s, float(k_long), t, r, qy, vix)
    credit = mid - 2 * HAIRCUT
    n = int(math.floor(BUDGET / ((width - credit) * MULT)))
    exit_sched = max(d for d in days if d <= expiry - timedelta(days=EXIT_DAYS))
    exit_day, reason, mark_exit = None, None, None
    for d in sorted(x for x in days if entry < x <= exit_sched):
        tt = (expiry - d).days / 365.0
        m = (put_value(close[d], float(k_short), tt, last_obs(rate_obs, d) / 100.0, sum(a for x, a in divs if d - timedelta(days=365) < x <= d) / close[d], last_obs(vix_obs, d))
             - put_value(close[d], float(k_long), tt, last_obs(rate_obs, d) / 100.0, sum(a for x, a in divs if d - timedelta(days=365) < x <= d) / close[d], last_obs(vix_obs, d)))
        if m <= PT_FRAC * credit or d >= exit_sched:
            exit_day, mark_exit = d, m
            reason = "profit_take" if m <= PT_FRAC * credit else "time_exit"
            break
    net = None if mark_exit is None else (credit - (mark_exit + 2 * HAIRCUT)) * MULT * n - 4 * FEE * n
    return {"expiry": expiry, "entry_day": entry, "spot": s, "vix": vix, "rate": r, "q": qy, "k_short": k_short, "k_long": k_long,
            "width": width, "credit": credit, "contracts": n, "exit_day": exit_day, "exit_reason": reason, "mark_exit": mark_exit,
            "net_pnl": net, "sched_exit": exit_sched}


def compare(row: pd.Series, d: dict) -> list[str]:
    bad = []
    def eq(name, a, b, tol=0.0):
        if (abs(a - b) > tol) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else (a != b):
            bad.append(f"{name}: run {a} vs verifier {b}")
    eq("expiry", str(row["expiry"]), d["expiry"].isoformat())
    eq("entry_day", str(row["entry_day"]), d["entry_day"].isoformat())
    eq("spot", float(row["spot"]), d["spot"], 1e-9)
    eq("vol_index", float(row["vol_index"]), d["vix"], 1e-9)
    eq("q", float(row["q"]), d["q"], 1e-9)
    eq("k_short", int(row["k_short"]), d["k_short"])
    eq("k_long", int(row["k_long"]), d["k_long"])
    eq("width", int(row["width"]), d["width"])
    eq("credit", float(row["credit"]), d["credit"], 1e-9)
    eq("contracts", int(row["contracts"]), d["contracts"])
    if d["exit_day"] is None:
        return bad + ["the verifier found no exit before the scheduled exit day"]
    eq("exit_day", str(row["exit_day"]), d["exit_day"].isoformat())
    eq("exit_reason", str(row["exit_reason"]), d["exit_reason"])
    eq("net_pnl", float(row["net_pnl"]), d["net_pnl"], 0.01)
    return bad


def check(run: Path, *, n: int = 30, seed: int = 11) -> dict:
    days, close, divs, vix_obs, rate_obs = load_raw()
    pos = pd.read_csv(run / "primary" / "positions.csv")
    idx = list(range(len(pos)))
    pick = sorted(random.Random(seed).sample(idx, min(n, len(idx))))
    results = []
    for i in pick:
        row = pos.iloc[i]
        d = derive(pd.Timestamp(row["expiry"]).date(), days, close, divs, vix_obs, rate_obs)
        results.append({"expiry": str(row["expiry"]), "mismatches": compare(row, d)})
    bad = [r for r in results if r["mismatches"]]
    return {"checked": len(results), "positions_in_run": len(pos), "matched": len(results) - len(bad), "failures": bad,
            "pass": not bad and len(results) > 0, "seed": seed}


def render(res: dict, run: Path) -> str:
    out = [f"# Safe-wheel post-run verification: {run.name}", "",
           f"Positions re-priced from the raw cached inputs by separate code: **{res['checked']}** of {res['positions_in_run']} (seed {res['seed']}).",
           f"**Matched: {res['matched']} of {res['checked']}** ({'PASS' if res['pass'] else 'FAIL'})."]
    if res["failures"]:
        out += ["", "| Expiry | Mismatches |", "| --- | --- |"]
        out += [f"| {f['expiry']} | {'; '.join(f['mismatches'])} |" for f in res["failures"]]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backtest.safe_wheel_verify", description=__doc__.split("\n")[0])
    ap.add_argument("--run")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args(argv)
    run = Path(args.run) if args.run else latest_run()
    res = check(run, n=args.n, seed=args.seed)
    text = render(res, run)
    (run / "verification.md").write_text(text, encoding="utf-8", newline="\n")
    print(text.encode("ascii", "replace").decode("ascii"))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
