"""Independent verifier for the C5 holdout smoke test (pre-registration item 5(b)). Imports nothing from `research/core_candidates`'s other
modules; reads the raw cached parquet files under `data/reference/c5_holdout/` directly.

For EVERY decision in the window it re-derives the trend test, the credit test (latest BAA10Y dated strictly before the decision day,
against its 126-trading-day average), the holding, and C5's return from that execution close to the next (or to the window's last close).
"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "reference" / "c5_holdout"
LAST = date(2026, 5, 19)
SPREAD, EXPENSE, BPS = 0.005, 0.0091, 2.0


def _load():
    spy = pd.read_parquet(CACHE / "yahoo" / "SPY.parquet").sort_values("date")
    days = [pd.Timestamp(x).date() for x in spy["date"]]
    assert max(days) <= LAST
    px = [float(v) for v in spy["adj_close"]]
    fred = {}
    for s in ("DTB3", "BAA10Y"):
        df = pd.read_parquet(CACHE / "fred" / f"{s}.parquet")
        rows = sorted((pd.Timestamp(a).date(), float(b)) for a, b in zip(df["date"], df["value"]) if b == b)
        assert max(r[0] for r in rows) <= LAST
        fred[s] = rows
    return days, px, fred


def _last(rows, cutoff, strict):
    v = None
    for d, x in rows:
        if d < cutoff or (not strict and d == cutoff):
            v = x
        else:
            break
    return v


def check(run_dir: Path) -> dict:
    days, px, fred = _load()
    pos = {d: i for i, d in enumerate(days)}
    ex = json.loads((run_dir / "executions.json").read_text(encoding="utf-8"))
    eq = pd.read_csv(run_dir / "equity_C5.csv").set_index("date")["equity"]
    month_end = [i for i in range(len(days) - 1) if days[i + 1].month != days[i].month]
    rate = lambda i: _last(fred["DTB3"], days[i - 1], False) / 100.0  # noqa: E731
    credit = lambda i: _last(fred["BAA10Y"], days[i], True)  # noqa: E731

    def decide(i):
        me = [j for j in month_end if j <= i][-10:]
        trend = px[i] > sum(px[j] for j in me) / 10.0
        w = [credit(k) for k in range(i - 125, i + 1)]
        calm = credit(i) <= sum(w) / 126.0
        return ("2X" if trend and calm else ("SPY" if trend or calm else "TB")), trend, calm

    def grow(h, k):
        r = rate(k)
        if h == "TB":
            return r / 252.0
        rs = px[k] / px[k - 1] - 1.0
        return 2.0 * rs - (r + SPREAD) / 252.0 - EXPENSE / 252.0 if h == "2X" else rs

    rows, bad = [], []
    for j, x in enumerate(ex):
        d = pos[date.fromisoformat(x["decision_date"])]
        held, trend, calm = decide(d)
        a = pos[date.fromisoformat(x["execution_date"])]
        if j + 1 < len(ex):
            b = pos[date.fromisoformat(ex[j + 1]["execution_date"])]
            nxt = decide(pos[date.fromisoformat(ex[j + 1]["decision_date"])])[0]
        else:
            b, nxt = pos[LAST], held
        g = 1.0
        for k in range(a + 1, b + 1):
            g *= 1.0 + grow(held, k)
        if nxt != held:
            g *= 1.0 - BPS / 1e4 * 2.0
        got = g - 1.0
        want = float(eq[days[b].isoformat()] / eq[days[a].isoformat()] - 1.0)
        ok = held == x["label"] and trend == x["trend"] and calm == x["credit"] and math.isclose(got, want, rel_tol=1e-9, abs_tol=1e-12)
        rows.append({"decision": x["decision_date"], "holding": held, "run_holding": x["label"], "trend": trend, "credit": calm, "return": got, "run_return": want, "ok": ok})
        if not ok:
            bad.append(rows[-1])
    return {"checks": len(rows), "matched": sum(r["ok"] for r in rows), "pass": bool(rows) and not bad, "rows": rows, "mismatches": bad}
