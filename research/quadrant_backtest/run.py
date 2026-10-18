"""Quadrant-tilt backtest (docs/specs/Quadrant_Backtest_Preregistration.md).

    PYTHONPATH=src python -m research.quadrant_backtest.run download        # prices (yfinance), read-only
    PYTHONPATH=src python -m research.quadrant_backtest.run replay          # the production classifier on the ALFRED cache -> replay.json
    PYTHONPATH=src python -m research.quadrant_backtest.run run             # the backtest, the verifier and the report
    PYTHONPATH=src python -m research.quadrant_backtest.run render <run-dir>

Nothing dated on or after 2026-01-01 is read. Outputs: `reports/quadrant_backtest/` (gitignored) and `.review/quadrant-backtest-results.md`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from backtest.provenance import code_commit
from research.core_candidates import data as ccd
from research.core_candidates import engine as cce

from . import data as D
from . import engine as E

REPO = Path(__file__).resolve().parents[2]
PREREG = "docs/specs/Quadrant_Backtest_Preregistration.md"
OUT = REPO / "reports" / "quadrant_backtest"
REPLAY = OUT / "replay.json"
REVIEW = REPO / ".review" / "quadrant-backtest-results.md"
END = date(2025, 12, 31)


def sub_periods(start: date):
    return ((start, date(2015, 12, 31)), (date(2016, 1, 1), date(2020, 12, 31)), (date(2021, 1, 1), END))


def build_replay() -> list[dict]:
    sys.path[:0] = [str(REPO / "src"), str(REPO / "scripts")]
    import alfred_cache
    import replay_axes
    rows = replay_axes.replay("2010-01-04", END.isoformat(), alfred_cache.load_all_cached())
    OUT.mkdir(parents=True, exist_ok=True)
    REPLAY.write_text(json.dumps(rows, default=str), encoding="utf-8")
    return rows


def prereg_rule_text_unchanged() -> dict:
    """The rule text is everything above '## Appendix A'. Compare it with the file as first committed."""
    path = REPO / PREREG

    def rules(txt: str) -> str:
        return txt.replace("\r\n", "\n").split("\n## Appendix A")[0].split("\n## Post-run clarification")[0].rstrip()
    first = subprocess.run(["git", "log", "--diff-filter=A", "--format=%H", "--", PREREG], cwd=REPO, capture_output=True, text=True).stdout.split()
    first_sha = first[-1] if first else None
    orig = subprocess.run(["git", "show", f"{first_sha}:{PREREG}"], cwd=REPO, capture_output=True, text=True, encoding="utf-8").stdout if first_sha else ""
    now = path.read_text(encoding="utf-8")
    h0, h1 = hashlib.sha256(rules(orig).encode()).hexdigest(), hashlib.sha256(rules(now).encode()).hexdigest()
    return {"first_commit": first_sha, "unchanged": bool(first_sha) and h0 == h1, "has_appendix_a": "## Appendix A" in now,
            "has_appendix_b": "## Appendix B" in now}


def load_world():
    spy = D.read("SPY")
    days = [d.date() for d in spy["date"]]
    E.refuse_holdout(days[-1])
    rets, first = {}, D.first_dates()
    first = {s: date.fromisoformat(v) for s, v in first.items()}
    for s in D.SYMBOLS:
        df = D.read(s)
        m = dict(zip((d.date() for d in df["date"]), df["adj_close"].astype(float)))
        a = [m.get(d) for d in days]
        rets[s] = [None] + [(a[i] / a[i - 1] - 1.0) if (a[i] is not None and a[i - 1] is not None) else None for i in range(1, len(days))]
    adj_spy = spy["adj_close"].to_numpy(dtype=float)
    dtb3 = ccd.read("fred", "DTB3")[0]
    rates = E.daily_rates(days, [(d.date(), float(v)) for d, v in zip(dtb3["date"], dtb3["value"])])
    return days, rets, first, adj_spy, rates


def c5_result(window_first_day: date):
    """C5 via the parent engine, unchanged; the two window-tied constants are set in a scoped override (interpretation 10)."""
    prices = {s: ccd.read("yahoo", s)[0] for s in ccd.SYMBOLS}
    m = cce.build_market(prices, ccd.read("fred", "DTB3")[0], ccd.read("fred", "BAA10Y")[0], (window_first_day, END))
    me = [i for i in cce.month_end_indices(m.days) if m.days[i] >= m.days[m.idx(window_first_day) - 1]]
    first_dec = m.days[me[0]]
    start = m.days[me[0] + 1]
    saved = (cce.FIRST_DECISION, cce.SUB_PERIODS)
    cce.FIRST_DECISION, cce.SUB_PERIODS = first_dec, sub_periods(window_first_day)
    try:
        res = cce.simulate(m, cce.DEFAULTS["C5"], (start, END))
    finally:
        cce.FIRST_DECISION, cce.SUB_PERIODS = saved
    return res, m


def run_study() -> dict:
    from . import report, verify
    rows = json.loads(REPLAY.read_text(encoding="utf-8")) if REPLAY.is_file() else build_replay()
    ws = E.window_start(rows)
    eff = E.effective_quadrants(rows)
    days, rets, first, adj_spy, rates = load_world()
    force = E.in_force(days, eff)
    s = next(i for i, d in enumerate(days) if d >= ws)
    subs = sub_periods(days[s])
    res = {"Q-TILT": E.simulate("Q-TILT", days, s, rets, first, E.plan_qtilt(days, force, first)),
           "Q-ALL": E.simulate("Q-ALL", days, s, rets, first, E.plan_qall(days, first)),
           "Soft tilt (50/50)": E.simulate("Soft tilt (50/50)", days, s, rets, first, E.plan_soft(days, force, first)),
           "Q-TILT monthly": E.simulate("Q-TILT monthly", days, s, rets, first, E.plan_monthly(days, force, first))}
    spy_eq = E.START_EQUITY * adj_spy[s:] / adj_spy[s]
    res["SPY"] = E.Result("SPY", days[s:], spy_eq, ["SPY"] * len(spy_eq))
    rw = rates[s:]
    met = {k: E.metrics(v, rw, subs) for k, v in res.items()}
    c5, cm = c5_result(days[s])
    c5_rates = rates[days.index(c5.dates[0]):days.index(c5.dates[0]) + len(c5.dates)]
    met["C5 (trend + credit)"] = E.metrics(E.Result("C5 (trend + credit)", c5.dates, c5.equity, c5.holding,
                                                     switches=sum(1 for x in c5.executions[1:] if x["switched"]), costs=c5.costs), c5_rates, subs)
    v = E.verdicts(met["Q-TILT"], met["Q-ALL"], met["SPY"])
    tilt = res["Q-TILT"]
    held = tilt.held
    time_in = {q: sum(1 for h in held[1:] if h == q) / (len(held) - 1) for q in E.QUADS + ("Q-ALL",)}
    switches = []
    for e in tilt.events:
        if e["kind"] == "rebalance":
            i = days.index(e["date"])
            switches.append({"execution": e["date"], "visible": days[i - 1], "to": e["label"]})
    hits = [E.hit_table(held, E.basket_index(days, s, rets, first, q), q) for q in E.QUADS]
    window = days[s:]
    proxy_use = []
    for mbr, p in E.PROXY.items():
        n = sum(1 for d in window if d < first[mbr])
        proxy_use.append({"member": mbr, "proxy": p or "dropped", "first_date": first[mbr], "window_days_before_first": n, "share_of_window": n / len(window)})
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = OUT / run_id
    out.mkdir(parents=True, exist_ok=True)
    for k, r in res.items():
        pd.DataFrame({"date": [d.isoformat() for d in r.dates], "equity": r.equity, "held": r.held}).to_csv(
            out / f"equity_{k.replace(' ', '_').replace('/', '-')}.csv", index=False, float_format="%.17g")
    (out / "events_Q-TILT.json").write_text(json.dumps(tilt.events, default=str), encoding="utf-8")
    pd.DataFrame({"date": [d.isoformat() for d in days], "in_force": force}).to_csv(out / "in_force.csv", index=False)
    prov = prereg_rule_text_unchanged()
    results = {"run_id": run_id, "prereg": prov, "code": code_commit(), "preregistered_run": prov["unchanged"] and prov["has_appendix_a"] and prov["has_appendix_b"],
               "window_start_rule": ws, "first_trading_day": days[s], "end": END, "trading_days": len(window), "sub_periods": subs,
               "metrics": met, "verdicts": v, "time_in": time_in, "switches": switches, "hits": hits, "proxy_use": proxy_use,
               "c5_window": [c5.dates[0], c5.dates[-1]], "replay_rows": len(rows),
               "replay_determinate_share": sum(1 for r in rows if r["active_quadrant"] in E.QUADS) / len(rows),
               "replay_first_determinate": next((r["date"] for r in rows if r["active_quadrant"] in E.QUADS), None),
               "missing_series": [k for k, x in json.loads((REPO / "scripts" / ".alfred_cache" / "manifest.json").read_text())["series"].items() if not x.get("row_count")]}
    (out / "results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    ver = verify.check(out)
    (out / "verification.json").write_text(json.dumps(ver, indent=1, default=str), encoding="utf-8")
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    results["verification"] = ver
    REVIEW.parent.mkdir(parents=True, exist_ok=True)
    REVIEW.write_text(report.render(results), encoding="utf-8")
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research.quadrant_backtest.run")
    sub = ap.add_subparsers(dest="cmd", required=True)
    dl = sub.add_parser("download")
    dl.add_argument("--refresh", action="store_true")
    sub.add_parser("replay")
    sub.add_parser("run")
    rd = sub.add_parser("render")
    rd.add_argument("run_dir")
    a = ap.parse_args(argv)
    if a.cmd == "download":
        D.ensure_cache(refresh=a.refresh, log=print)
    elif a.cmd == "replay":
        rows = build_replay()
        print(len(rows), "rows; window start", E.window_start(rows))
    elif a.cmd == "render":
        from . import report
        d = Path(a.run_dir)
        r = json.loads((d / "results.json").read_text(encoding="utf-8"))
        r["verification"] = json.loads((d / "verification.json").read_text(encoding="utf-8"))
        REVIEW.write_text(report.render(r), encoding="utf-8")
        print("report:", REVIEW)
    else:
        r = run_study()
        print(r["verdicts"]["calls"], "|", r["verdicts"]["spy"], "| verification", r["verification"]["matched"], "/", r["verification"]["checks"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
