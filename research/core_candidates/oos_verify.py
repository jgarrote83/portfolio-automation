"""Post-run verifier for the 1989-2004 out-of-sample run (OOS pre-registration, interpretation 12). Separate code: imports nothing from
`engine`, `oos`, `oos_data` or the parent verifier, and re-derives from the raw cached parquet files.

    PYTHONPATH=src python -m research.core_candidates.oos_verify reports/core_candidates_oos/<run-id>

For 12 random month-end decisions (seed 11) it re-derives, for C1, C4 and C5, the holding decided and the return from that decision's execution
close to the next month-end decision's execution close (after that close's trading cost). Holdings exactly; returns to 1e-9 relative.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "reference" / "core_candidates_oos"
SPREAD, EXPENSE, BPS, REL_TOL = 0.005, 0.0091, 2.0, 1e-9
LAST = date(2004, 12, 31)


class Raw:
    def __init__(self, cache: Path = CACHE):
        ix = pd.read_parquet(cache / "yahoo" / "IDX_SP500TR.parquet").sort_values("date")
        self.days = [pd.Timestamp(x).date() for x in ix["date"]]
        assert max(self.days) <= LAST, "a row after 2004 in the cache"
        self.pos = {d: i for i, d in enumerate(self.days)}
        self.px = [float(v) for v in ix["adj_close"]]
        self.fred = {}
        for s in ("DTB3", "BAA10Y"):
            df = pd.read_parquet(cache / "fred" / f"{s}.parquet")
            self.fred[s] = sorted((pd.Timestamp(a).date(), float(b)) for a, b in zip(df["date"], df["value"]) if b == b)

    def _last(self, s, cutoff, strict):
        best = None
        for d, v in self.fred[s]:
            if d < cutoff or (not strict and d == cutoff):
                best = v
            else:
                break
        return best

    def rate(self, i):
        return self._last("DTB3", self.days[i - 1], False) / 100.0

    def credit(self, i):
        return self._last("BAA10Y", self.days[i], True)

    def month_ends(self):
        d = self.days
        return [i for i in range(len(d) - 1) if d[i + 1].month != d[i].month]


def decide(raw: Raw, rule: str, i: int) -> str:
    me = [j for j in raw.month_ends() if j <= i][-10:]
    trend = raw.px[i] > sum(raw.px[j] for j in me) / 10.0
    window = [raw.credit(k) for k in range(i - 125, i + 1)]
    calm = raw.credit(i) <= sum(window) / 126.0
    if rule == "C1":
        return "2X" if trend else "TB"
    if rule == "C4":
        return "2X" if calm else "TB"
    return "2X" if (trend and calm) else ("SPY" if (trend or calm) else "TB")


def growth(raw: Raw, held: str, k: int) -> float:
    rt = raw.rate(k)
    r = raw.px[k] / raw.px[k - 1] - 1.0
    if held == "TB":
        return rt / 252.0
    if held == "2X":
        return 2.0 * r - (rt + SPREAD) / 252.0 - EXPENSE / 252.0
    return r


def span(raw: Raw, held: str, nxt: str, a: int, b: int) -> float:
    g = 1.0
    for k in range(a + 1, b + 1):
        g *= 1.0 + growth(raw, held, k)
    if nxt != held:
        g *= 1.0 - BPS / 10_000.0 * 2.0
    return g - 1.0


def check(run_dir: Path, n: int = 12, seed: int = 11, cache: Path = CACHE) -> dict:
    raw = Raw(cache)
    ex = {r: json.loads((run_dir / f"executions_{r}.json").read_text(encoding="utf-8")) for r in ("C1", "C4", "C5")}
    monthly = ex["C1"]
    pick = sorted(random.Random(seed).sample(range(len(monthly) - 1), min(n, len(monthly) - 1)))
    rows, bad = [], []
    for j in pick:
        d0 = date.fromisoformat(monthly[j]["decision_date"])
        a = raw.pos[date.fromisoformat(monthly[j]["execution_date"])]
        b = raw.pos[date.fromisoformat(monthly[j + 1]["execution_date"])]
        for rule in ("C1", "C4", "C5"):
            e0, e1 = ex[rule][j], ex[rule][j + 1]
            assert e0["decision_date"] == d0.isoformat()
            held, nxt = decide(raw, rule, raw.pos[d0]), decide(raw, rule, b - 1)
            got, want = span(raw, held, nxt, a, b), e1["equity_after"] / e0["equity_after"] - 1.0
            ok = held == e0["label"] and math.isclose(got, want, rel_tol=REL_TOL, abs_tol=1e-12)
            rows.append({"decision": d0.isoformat(), "rule": rule, "holding": held, "run_holding": e0["label"], "return": got, "run_return": want, "ok": ok})
            if not ok:
                bad.append(rows[-1])
    return {"month_ends": [monthly[j]["decision_date"] for j in pick], "checks": len(rows), "matched": sum(r["ok"] for r in rows),
            "pass": not bad and bool(rows), "mismatches": bad, "rows": rows, "seed": seed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research.core_candidates.oos_verify")
    ap.add_argument("run_dir")
    args = ap.parse_args(argv)
    res = check(Path(args.run_dir))
    (Path(args.run_dir) / "verification.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(f"{res['matched']}/{res['checks']} {'PASS' if res['pass'] else 'FAIL'}")
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
