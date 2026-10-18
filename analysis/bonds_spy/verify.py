"""Independent re-derivation of the day table, with code deliberately separate from the analysis modules.

    python -m analysis.bonds_spy.verify [--table reports/bonds_spy/day_table.csv] [--n 20] [--seed 11]

Picks `n` random usable days (seed 11) and, for each of TLT, IEF and SPY, recomputes gap, morning, rest and full-day return straight from the raw
cached parquet files (`data/cache/sip/1Day/<SYMBOL>/<YYYY-MM>.parquet` and `.../1Min/...`; timestamps are UTC in the files and are converted to New
York time here), then compares them with the table's values. The comparison is EXACT (==): the same arithmetic on the same inputs must give the same
float. It imports nothing from `returns`, `stats`, `analyse` or `data`; the prior trading day comes from SPY's own daily files.
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE = REPO_ROOT / "data" / "cache" / "sip"
SYMS = ("TLT", "IEF", "SPY")
NAMES = ("gap", "morning", "rest", "full")


def _month_frame(timeframe: str, symbol: str, d: date, cache: Path) -> pd.DataFrame:
    f = cache / timeframe / symbol / f"{d.year:04d}-{d.month:02d}.parquet"
    df = pd.read_parquet(f)
    df["et"] = df["ts"].dt.tz_convert("America/New_York")
    return df


def _daily_bar(symbol: str, d: date, cache: Path) -> tuple[float, float]:
    df = _month_frame("1Day", symbol, d, cache)
    row = df[df["et"].dt.date == d]
    if len(row) != 1:
        raise ValueError(f"{symbol} {d}: {len(row)} daily bars")
    return float(row["open"].iloc[0]), float(row["close"].iloc[0])


def _prior_trading_day(d: date, cache: Path) -> date:
    days = []
    for ym in pd.period_range("2023-12", "2025-12", freq="M"):
        df = pd.read_parquet(cache / "1Day" / "SPY" / f"{ym.year:04d}-{ym.month:02d}.parquet")
        days += list(df["ts"].dt.tz_convert("America/New_York").dt.date)
    days = sorted(set(days))
    return days[days.index(d) - 1]


def _open_of_1000_bar(symbol: str, d: date, cache: Path) -> float:
    df = _month_frame("1Min", symbol, d, cache)
    row = df[(df["et"].dt.date == d) & (df["et"].dt.hour == 10) & (df["et"].dt.minute == 0)]
    if len(row) != 1:
        raise ValueError(f"{symbol} {d}: {len(row)} bars stamped 10:00")
    return float(row["open"].iloc[0])


def derive(symbol: str, d: date, cache: Path = CACHE) -> dict[str, float]:
    p = _prior_trading_day(d, cache)
    open_d, close_d = _daily_bar(symbol, d, cache)
    close_p = _daily_bar(symbol, p, cache)[1]
    o10 = _open_of_1000_bar(symbol, d, cache)
    return {"gap": open_d / close_p - 1.0, "morning": o10 / open_d - 1.0, "rest": close_d / o10 - 1.0, "full": close_d / close_p - 1.0}


def check(table_csv: Path, n: int = 20, seed: int = 11, cache: Path = CACHE) -> dict:
    t = pd.read_csv(table_csv, float_precision="round_trip")
    days = [datetime.strptime(s, "%Y-%m-%d").date() for s in t["date"]]
    pick = sorted(random.Random(seed).sample(range(len(days)), min(n, len(days))))
    results, bad = [], []
    for i in pick:
        d = days[i]
        for s in SYMS:
            got = derive(s, d, cache)
            for m in NAMES:
                want = float(t.loc[i, f"{s}_{m}"])
                ok = got[m] == want
                results.append((d.isoformat(), s, m, ok))
                if not ok:
                    bad.append({"date": d.isoformat(), "symbol": s, "measure": m, "table": want, "re-derived": got[m]})
    return {"days": [days[i].isoformat() for i in pick], "values_checked": len(results), "matched": sum(1 for r in results if r[3]), "mismatches": bad,
            "pass": not bad and bool(results), "seed": seed}


def render(res: dict) -> str:
    out = ["## Verification: independent re-derivation", "",
           f"{len(res['days'])} random usable days (seed {res['seed']}); for each, the four returns of each of TLT, IEF and SPY re-derived from the raw cached parquet files "
           f"by separate code and compared with the day table **exactly**: **{res['matched']} of {res['values_checked']} match** ({'PASS' if res['pass'] else 'FAIL'}).",
           "", "Days: " + ", ".join(res["days"]) + "."]
    if res["mismatches"]:
        out += ["", "| Date | Symbol | Measure | Table | Re-derived |", "| --- | --- | --- | ---: | ---: |"]
        out += [f"| {m['date']} | {m['symbol']} | {m['measure']} | {m['table']!r} | {m['re-derived']!r} |" for m in res["mismatches"]]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="analysis.bonds_spy.verify", description=__doc__.split("\n")[0])
    ap.add_argument("--table", default=str(REPO_ROOT / "reports" / "bonds_spy" / "day_table.csv"))
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args(argv)
    res = check(Path(args.table), n=args.n, seed=args.seed)
    print(render(res))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
