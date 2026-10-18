"""Post-run verifier for the Core-candidates study (pre-registration section 2, item 19). Separate code: it imports nothing from `engine`,
`run` or `report`, and re-derives from the raw cached parquet files.

    PYTHONPATH=src python -m research.core_candidates.verify reports/core_candidates/<run-id>

For 12 random month-end decisions (seed 11) it re-derives, for C1, C3, C4 and C5, the holding decided and the candidate's return from that
decision's execution close to the next month-end decision's execution close (both after that close's trading cost); for C2, every weekly
exposure decided in the same span and the same span return (re-simulated from C2's last execution on or before the span start). Holdings
must match exactly, returns to 1e-9 relative.
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
CACHE = ROOT / "data" / "reference" / "core_candidates"
SPREAD, EXPENSE, BPS = 0.005, 0.0091, 2.0
REL_TOL = 1e-9


class Raw:
    """Adjusted closes on SPY's days, the T-bill rate per day and the credit value per day, read straight from parquet."""

    def __init__(self, cache: Path = CACHE):
        spy = pd.read_parquet(cache / "yahoo" / "SPY.parquet")
        self.days = [pd.Timestamp(x).date() for x in spy["date"]]
        self.pos = {d: i for i, d in enumerate(self.days)}
        self.px = {}
        for s in ("SPY", "EFA", "AGG"):
            df = pd.read_parquet(cache / "yahoo" / f"{s}.parquet")
            mp = {pd.Timestamp(a).date(): float(b) for a, b in zip(df["date"], df["adj_close"])}
            self.px[s] = [mp.get(d, float("nan")) for d in self.days]
        self.fred = {}
        for s in ("DTB3", "BAA10Y"):
            df = pd.read_parquet(cache / "fred" / f"{s}.parquet")
            self.fred[s] = sorted((pd.Timestamp(a).date(), float(b)) for a, b in zip(df["date"], df["value"]) if b == b)
        for d in self.days:
            assert d < date(2026, 1, 1), "2026 row in the cache"

    def _last(self, series: str, cutoff: date, strict: bool) -> float:
        best = None
        for d, v in self.fred[series]:
            if d < cutoff or (not strict and d == cutoff):
                best = v
            else:
                break
        return best

    def rate(self, i: int) -> float:
        """Annual DTB3 / 100 known before day i: the latest value dated on or before the previous SPY day."""
        return self._last("DTB3", self.days[i - 1], strict=False) / 100.0

    def credit(self, i: int) -> float:
        return self._last("BAA10Y", self.days[i], strict=True)

    def r(self, sym: str, i: int) -> float:
        return self.px[sym][i] / self.px[sym][i - 1] - 1.0

    def month_ends(self) -> list[int]:
        d = self.days
        return [i for i in range(len(d) - 1) if d[i + 1].month != d[i].month]


def decide(raw: Raw, rule: str, i: int) -> str:
    me = [j for j in raw.month_ends() if j <= i]
    spy = raw.px["SPY"]
    trend = spy[i] > sum(spy[j] for j in me[-10:]) / 10.0
    c = [raw.credit(k) for k in range(i - 125, i + 1)]
    calm = raw.credit(i) <= sum(c) / len(c)
    if rule == "C1":
        return "2X" if trend else "TB"
    if rule == "C4":
        return "2X" if calm else "TB"
    if rule == "C5":
        return "2X" if (trend and calm) else ("SPY" if (trend or calm) else "TB")
    if rule == "C3":
        p = [j for j in raw.month_ends() if raw.days[j].year == raw.days[i].year - 1 and raw.days[j].month == raw.days[i].month][0]
        tb = 1.0
        for k in range(p + 1, i + 1):
            tb *= 1.0 + raw.rate(k) / 252.0
        s12 = spy[i] / spy[p] - 1.0
        e12 = raw.px["EFA"][i] / raw.px["EFA"][p] - 1.0
        if not s12 > tb - 1.0:
            return "AGG"
        return "SPY" if s12 >= e12 else "EFA"
    raise ValueError(rule)


def day_growth(raw: Raw, asset: str, k: int, balance: float) -> float:
    rt = raw.rate(k)
    if asset == "TB":
        return rt / 252.0 if balance >= 0 else (rt + SPREAD) / 252.0
    if asset == "2X":
        return 2.0 * raw.r("SPY", k) - (rt + SPREAD) / 252.0 - EXPENSE / 252.0
    return raw.r(asset, k)


def monthly_span(raw: Raw, held: str, nxt: str, a: int, b: int) -> float:
    """Return from the close of execution day a (after its cost) to the close of execution day b (after its cost) for a single holding."""
    g = 1.0
    for k in range(a + 1, b + 1):
        g *= 1.0 + day_growth(raw, held, k, 1.0)
    if nxt != held:
        g *= 1.0 - BPS / 10_000.0 * 2.0          # sell everything, buy everything
    return g - 1.0


def vol(raw: Raw, i: int) -> float:
    rr = [raw.r("SPY", k) for k in range(i - 20, i + 1)]
    m = sum(rr) / 21.0
    return math.sqrt(sum((x - m) ** 2 for x in rr) / 20.0) * math.sqrt(252.0)


def c2_span(raw: Raw, start_exec: dict, a: int, b: int) -> tuple[float, list]:
    """Re-simulate C2 from its execution `start_exec` (balances known exactly) to day b; return E(b)/E(a) - 1 and the exposures it decided."""
    i0 = raw.pos[date.fromisoformat(start_exec["execution_date"])]
    E0 = start_exec["equity_after"]
    e = start_exec["exposure"]
    bal = {"SPY": e * E0, "TB": (1.0 - e) * E0}
    eq, decided = {i0: E0}, []
    for k in range(i0 + 1, b + 1):
        for x in list(bal):
            bal[x] *= 1.0 + day_growth(raw, x, k, bal[x])
        E = bal["SPY"] + bal["TB"]
        prev = k - 1                                              # is k an execution day: was k-1 a week's last trading day?
        if raw.days[prev].isocalendar()[:2] != raw.days[k].isocalendar()[:2]:
            ex = min(1.5, 0.15 / vol(raw, prev))
            tgt = {"SPY": ex * E, "TB": (1.0 - ex) * E}
            cost = BPS / 10_000.0 * (abs(tgt["SPY"] - bal["SPY"]) + abs(max(tgt["TB"], 0) - max(bal["TB"], 0)))
            bal = {x: v * (E - cost) / E for x, v in tgt.items()}
            E = bal["SPY"] + bal["TB"]
            decided.append((raw.days[prev].isoformat(), ex))
        eq[k] = E
    return eq[b] / eq[a] - 1.0, decided


def check(run_dir: Path, n: int = 12, seed: int = 11, cache: Path = CACHE) -> dict:
    raw = Raw(cache)
    ex = {r: json.loads((run_dir / f"executions_{r}.json").read_text(encoding="utf-8")) for r in ("C1", "C2", "C3", "C4", "C5")}
    eqs = {r: pd.read_csv(run_dir / f"equity_{r}.csv") for r in ("C1", "C2", "C3", "C4", "C5")}
    monthly = ex["C1"]
    pick = sorted(random.Random(seed).sample(range(len(monthly) - 1), min(n, len(monthly) - 1)))
    rows, bad = [], []
    for j in pick:
        d0 = date.fromisoformat(monthly[j]["decision_date"])
        a, b = raw.pos[date.fromisoformat(monthly[j]["execution_date"])], raw.pos[date.fromisoformat(monthly[j + 1]["execution_date"])]
        for rule in ("C1", "C3", "C4", "C5"):
            e0, e1 = ex[rule][j], ex[rule][j + 1]
            assert e0["decision_date"] == d0.isoformat()
            held, nxt = decide(raw, rule, raw.pos[d0]), decide(raw, rule, b - 1)
            want = e1["equity_after"] / e0["equity_after"] - 1.0
            got = monthly_span(raw, held, nxt, a, b)
            ok_h = held == e0["label"]
            ok_r = math.isclose(got, want, rel_tol=REL_TOL, abs_tol=1e-12)
            rows.append({"decision": d0.isoformat(), "rule": rule, "holding": held, "run_holding": e0["label"], "return": got, "run_return": want,
                         "ok": ok_h and ok_r})
            if not (ok_h and ok_r):
                bad.append(rows[-1])
        start = [x for x in ex["C2"] if date.fromisoformat(x["execution_date"]) <= raw.days[a]][-1]
        got, decided = c2_span(raw, start, a, b)
        e2 = eqs["C2"].set_index("date")["equity"]
        want = float(e2[raw.days[b].isoformat()] / e2[raw.days[a].isoformat()] - 1.0)
        run_exp = {x["decision_date"]: x["exposure"] for x in ex["C2"]}
        exp_ok = all(math.isclose(v, run_exp[dd], rel_tol=REL_TOL) for dd, v in decided)
        ok_r = math.isclose(got, want, rel_tol=REL_TOL, abs_tol=1e-12)
        rows.append({"decision": d0.isoformat(), "rule": "C2", "holding": f"{len(decided)} weekly exposures", "run_holding": "matched" if exp_ok else "DIFFER",
                     "return": got, "run_return": want, "ok": exp_ok and ok_r})
        if not rows[-1]["ok"]:
            bad.append(rows[-1])
    return {"month_ends": [monthly[j]["decision_date"] for j in pick], "checks": len(rows), "matched": sum(r["ok"] for r in rows),
            "pass": not bad and bool(rows), "mismatches": bad, "rows": rows, "seed": seed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research.core_candidates.verify")
    ap.add_argument("run_dir")
    args = ap.parse_args(argv)
    res = check(Path(args.run_dir))
    (Path(args.run_dir) / "verification.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(f"{res['matched']}/{res['checks']} {'PASS' if res['pass'] else 'FAIL'}")
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
