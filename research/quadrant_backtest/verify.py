"""Post-run verifier for the quadrant-tilt backtest (pre-registration section 2, item 13). Separate code: imports nothing from `engine`,
`run`, `data` or `report`; reads the cached replay (`reports/quadrant_backtest/replay.json`) and the raw price parquet files directly.

    PYTHONPATH=src python -m research.quadrant_backtest.verify reports/quadrant_backtest/<run-id>

Re-derives (a) 12 random Q-TILT switches (seed 11): the date the change became visible, the execution date and the new basket's holdings;
(b) Q-TILT's and Q-ALL's returns for 12 random calendar months (seed 11), by re-simulating both books from the window start with its own
drift / composition / cost rules. Dates and baskets exactly; returns to 1e-9 relative.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PRICES = ROOT / "data" / "reference" / "quadrant_backtest" / "yahoo"
REPLAY = ROOT / "reports" / "quadrant_backtest" / "replay.json"
Q = ("Q1", "Q2", "Q3", "Q4")
B = {"Q1": ["SPY", "QQQ", "SMH"], "Q2": ["XLI", "XLF", "COWZ", "VDE", "PDBC", "VTIP"],
     "Q3": ["COWZ", "VDE", "GLD", "PDBC", "XLP", "XLV", "VTIP", "KMLM"], "Q4": ["GLD", "XLP", "XLV", "KMLM", "TLT", "IEF", "USMV"]}
PX = {"COWZ": "IWD", "PDBC": "DBC", "VTIP": "STIP", "USMV": "SPLV", "KMLM": None}
BPS = 2.0


def load_prices():
    syms = sorted({m for v in B.values() for m in v} | {p for p in PX.values() if p})
    px, first = {}, {}
    for s in syms:
        df = pd.read_parquet(PRICES / f"{s}.parquet").sort_values("date")
        ds = [pd.Timestamp(x).date() for x in df["date"]]
        assert max(ds) < date(2026, 1, 1)
        px[s] = dict(zip(ds, (float(v) for v in df["adj_close"])))
        first[s] = ds[0]
    spy = pd.read_parquet(PRICES / "SPY.parquet").sort_values("date")
    return [pd.Timestamp(x).date() for x in spy["date"]], px, first


def held_symbol(m, d, first):
    if d >= first[m]:
        return m
    p = PX.get(m)
    return p if (p and d >= first[p]) else None


def comp(q, d, first):
    return [h for h in (held_symbol(m, d, first) for m in B[q]) if h]


def window_start(rows):
    det = [r["active_quadrant"] in Q for r in rows]
    for i, r in enumerate(rows):
        if r["date"] >= "2011-07-01" and i + 20 <= len(rows) and all(det[i:i + 20]):
            return date.fromisoformat(r["date"])
    raise ValueError("no window start")


def force_series(rows, days):
    eff, last = [], "Q-ALL"
    for r in rows:
        if r["active_quadrant"] in Q:
            last = r["active_quadrant"]
        eff.append((date.fromisoformat(r["date"]), last))
    out, j, cur = [], 0, "Q-ALL"
    for d in days:
        while j < len(eff) and eff[j][0] <= d:
            cur = eff[j][1]
            j += 1
        out.append(cur)
    return out


def is_month_end(days, i):
    if i + 1 < len(days):
        return days[i + 1].month != days[i].month
    n = days[i] + timedelta(days=1)
    while n.weekday() >= 5:
        n += timedelta(days=1)
    return n.month != days[i].month


def resimulate(kind, days, s, force, px, first):
    """Own re-implementation of the two books: per-sleeve dollar balances; a full reset on a Q-TILT quadrant change (executed at the
    next close) or at a Q-ALL month-end; a sleeve reset when its basket's holdings change; 2 bps on the net per-symbol trade."""
    sleeves, label, basket_of, eq = {}, None, {}, {}
    for t in range(s, len(days)):
        d = days[t]
        if t > s:
            for sl in sleeves.values():
                for h in sl:
                    sl[h] *= px[h][d] / px[h][days[t - 1]]
        E = sum(v for sl in sleeves.values() for v in sl.values()) if sleeves else 100_000.0
        target = None
        if kind == "tilt":
            want = force[t - 1]
            if not sleeves or want != label:
                if want == "Q-ALL":
                    basket_of = {q: q for q in Q}
                    target = {q: {h: E / 4 / len(comp(q, d, first)) for h in comp(q, d, first)} for q in Q}
                else:
                    basket_of = {"tilt": want}
                    c = comp(want, d, first)
                    target = {"tilt": {h: E / len(c) for h in c}}
                label = want
        else:
            if not sleeves or is_month_end(days, t):
                basket_of = {q: q for q in Q}
                target = {q: {h: E / 4 / len(comp(q, d, first)) for h in comp(q, d, first)} for q in Q}
        if target is None and sleeves:
            ch = {}
            for n, sl in sleeves.items():
                c = comp(basket_of[n], d, first)
                if set(c) != set(sl):
                    tot = sum(sl.values())
                    ch[n] = {h: tot / len(c) for h in c}
            if ch:
                target = {**sleeves, **ch}
        if target is not None:
            def totals(x):
                o = {}
                for sl in x.values():
                    for h, v in sl.items():
                        o[h] = o.get(h, 0.0) + v
                return o
            a, b = totals(sleeves), totals(target)
            cost = BPS / 1e4 * sum(abs(b.get(k, 0) - a.get(k, 0)) for k in set(a) | set(b))
            k = (E - cost) / E
            sleeves = {n: {h: v * k for h, v in sl.items()} for n, sl in target.items()}
        eq[d] = sum(v for sl in sleeves.values() for v in sl.values())
    return eq


def check(run_dir: Path, n: int = 12, seed: int = 11) -> dict:
    rows = json.loads(REPLAY.read_text(encoding="utf-8"))
    days, px, first = load_prices()
    ws = window_start(rows)
    s = next(i for i, d in enumerate(days) if d >= ws)
    force = force_series(rows, days)
    # (a) switches
    mine = []
    for t in range(s + 1, len(days)):
        if force[t - 1] != force[t - 2] if t - 2 >= 0 else False:
            mine.append({"visible": days[t - 1], "execution": days[t], "to": force[t - 1], "basket": sorted(comp(force[t - 1], days[t], first)) if force[t - 1] in Q else None})
    events = [e for e in json.loads((run_dir / "events_Q-TILT.json").read_text(encoding="utf-8")) if e["kind"] == "rebalance"][1:]
    run_sw = [(e["date"], e["label"]) for e in events]
    my_sw = [(m["execution"].isoformat(), m["to"]) for m in mine]
    pick = sorted(random.Random(seed).sample(range(len(mine)), min(n, len(mine)))) if mine else []
    sw_rows = [{"visible": mine[i]["visible"].isoformat(), "execution": mine[i]["execution"].isoformat(), "to": mine[i]["to"],
                "basket": mine[i]["basket"], "in_run": (mine[i]["execution"].isoformat(), mine[i]["to"]) in run_sw} for i in pick]
    # (b) months
    tilt = resimulate("tilt", days, s, force, px, first)
    qall = resimulate("all", days, s, force, px, first)
    run_t = pd.read_csv(run_dir / "equity_Q-TILT.csv").set_index("date")["equity"]
    run_a = pd.read_csv(run_dir / "equity_Q-ALL.csv").set_index("date")["equity"]
    month_ends = [days[i] for i in range(s, len(days)) if is_month_end(days, i)]
    pairs = list(zip(month_ends, month_ends[1:]))
    mpick = sorted(random.Random(seed).sample(range(len(pairs)), min(n, len(pairs))))
    m_rows = []
    for i in mpick:
        a, b = pairs[i]
        for name, mine_eq, run_eq in (("Q-TILT", tilt, run_t), ("Q-ALL", qall, run_a)):
            got = mine_eq[b] / mine_eq[a] - 1.0
            want = float(run_eq[b.isoformat()] / run_eq[a.isoformat()] - 1.0)
            m_rows.append({"month": b.isoformat()[:7], "book": name, "return": got, "run_return": want,
                           "ok": math.isclose(got, want, rel_tol=1e-9, abs_tol=1e-12)})
    ok = my_sw == run_sw and all(r["in_run"] for r in sw_rows) and all(r["ok"] for r in m_rows)
    return {"window_start": ws.isoformat(), "switches_all_match": my_sw == run_sw, "switches_total": len(mine), "switch_checks": sw_rows,
            "month_checks": m_rows, "checks": len(sw_rows) + len(m_rows), "matched": sum(r["in_run"] for r in sw_rows) + sum(r["ok"] for r in m_rows),
            "pass": ok, "seed": seed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research.quadrant_backtest.verify")
    ap.add_argument("run_dir")
    a = ap.parse_args(argv)
    res = check(Path(a.run_dir))
    (Path(a.run_dir) / "verification.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(f"{res['matched']}/{res['checks']} {'PASS' if res['pass'] else 'FAIL'}; all switches match: {res['switches_all_match']}")
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
