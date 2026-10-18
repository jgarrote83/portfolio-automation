"""Out-of-sample validation of C1 (reference), C4 and C5 on 1989-2004, rules unchanged (docs/specs/Core_Candidates_OOS_Preregistration.md).

    PYTHONPATH=src python -m research.core_candidates.oos download [--refresh]
    PYTHONPATH=src python -m research.core_candidates.oos run
    PYTHONPATH=src python -m research.core_candidates.oos render reports/core_candidates_oos/<run-id>

This module is only an ADAPTER: it puts `^SP500TR` in SPY's slot, sets the engine's two window constants inside a scoped override, and
calls the parent engine's functions unchanged. Nothing dated after 2004-12-31 is read.
"""
from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.provenance import code_commit, prereg_info

from . import engine as E
from . import oos_data as OD

REPO = Path(__file__).resolve().parents[2]
PREREG = "docs/specs/Core_Candidates_OOS_Preregistration.md"
REPORTS = REPO / "reports" / "core_candidates_oos"
REVIEW = REPO / ".review" / "core-candidates-oos-results.md"
PARENT_RUN = REPO / "reports" / "core_candidates" / "20261018T012418Z"

OOS_WINDOW = (date(1989, 1, 3), date(2004, 12, 31))
OOS_FIRST_DECISION = date(1988, 12, 30)
OOS_SUB_PERIODS = ((date(1989, 1, 1), date(1994, 12, 31)), (date(1995, 1, 1), date(1999, 12, 31)), (date(2000, 1, 1), date(2004, 12, 31)))
OOS_CRISES = (("1990 (Jul–Oct)", date(1990, 7, 2), date(1990, 10, 31)), ("1998 (Jul–Oct)", date(1998, 7, 1), date(1998, 10, 30)),
              ("2000-03 to 2002-10", date(2000, 3, 1), date(2002, 10, 31)))
JUDGED = ("C4", "C5")
REFERENCE = ("C1",)
RULES = ("C1", "C4", "C5")


@contextmanager
def oos_engine():
    """Interpretation 3: the engine's two window-tied constants, set for this run and restored on exit. No function is changed."""
    saved = (E.FIRST_DECISION, E.SUB_PERIODS)
    E.FIRST_DECISION, E.SUB_PERIODS = OOS_FIRST_DECISION, OOS_SUB_PERIODS
    try:
        yield
    finally:
        E.FIRST_DECISION, E.SUB_PERIODS = saved


def build_oos_market(index_df: pd.DataFrame, dtb3: pd.DataFrame, baa: pd.DataFrame, window=OOS_WINDOW) -> E.Market:
    """The substitution: the index occupies SPY's slot. EFA/AGG/ACWX are absent (C1/C4/C5 never hold them; holding one would raise)."""
    OD.refuse_after_2004(window[1])
    for df in (index_df, dtb3, baa):
        if len(df):
            OD.refuse_after_2004(df["date"].max())
    m = E.build_market({"SPY": index_df}, dtb3, baa, window)
    for sym in ("EFA", "AGG", "ACWX"):
        m.adj.setdefault(sym, np.full(len(m.days), np.nan))
    return m


def simulate(m: E.Market, rule: str, window=OOS_WINDOW) -> E.Result:
    with oos_engine():
        return E.simulate(m, E.DEFAULTS[rule], window)


def decisions(m: E.Market, rule: str, window=OOS_WINDOW) -> list:
    with oos_engine():
        return E.decisions(m, E.DEFAULTS[rule], window)


def metrics(res: E.Result, m: E.Market) -> dict:
    with oos_engine():
        return E.metrics(res, m)


def tracking(index_df: pd.DataFrame, spy_df: pd.DataFrame, start: date = date(1993, 2, 1), end: date = OOS_WINDOW[1]) -> dict:
    """Interpretation 8: SPY's adjusted total return vs the index, daily correlation and yearly differences (SPY - index, points)."""
    a = index_df.set_index(index_df["date"].dt.date)["adj_close"]
    b = spy_df.set_index(spy_df["date"].dt.date)["adj_close"]
    days = [d for d in a.index if d in b.index]
    ra, rb = a.loc[days].pct_change(), b.loc[days].pct_change()
    keep = [d for d in days if start <= d <= end]
    ra, rb = ra.loc[keep], rb.loc[keep]
    years = {}
    for y in sorted({d.year for d in keep}):
        k = [d for d in keep if d.year == y]
        ia, ib = float(np.prod(1 + ra.loc[k]) - 1), float(np.prod(1 + rb.loc[k]) - 1)
        years[y] = {"index": ia, "spy": ib, "diff_pp": (ib - ia) * 100}
    full = [v["diff_pp"] for y, v in years.items() if y >= 1994]
    return {"start": keep[0], "end": keep[-1], "days": len(keep), "corr": float(np.corrcoef(ra, rb)[0, 1]), "years": years,
            "mean_diff_pp": float(np.mean(full)), "index_total": float(np.prod(1 + ra) - 1), "spy_total": float(np.prod(1 + rb) - 1)}


def stitched(oos_res: E.Result, parent_csv: Path) -> dict:
    """Interpretation 10: chain this window's equity to the parent run's daily equity from 2005-01-03 (that day's return taken as zero)."""
    p = pd.read_csv(parent_csv)
    pdates = [date.fromisoformat(x) for x in p["date"]]
    if pdates[0] != date(2005, 1, 3):
        raise ValueError("the parent run does not start on 2005-01-03")
    scale = oos_res.equity[-1] / E.START_EQUITY
    dates = list(oos_res.dates) + pdates
    eq = np.r_[oos_res.equity, p["equity"].to_numpy(dtype=float) * scale]
    span = (dates[-1] - dates[0]).days
    cal = E.calendar_years(dates, eq)
    return {"cagr": float((eq[-1] / eq[0]) ** (365.25 / span) - 1.0), **E.max_drawdown(dates, eq), "worst_year": min(cal.items(), key=lambda kv: kv[1]),
            "end_multiple": float(eq[-1] / eq[0])}


def _json_default(o):
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(type(o))


def run_study() -> dict:
    from . import oos_report, oos_verify
    index_df = OD.read("yahoo", OD.INDEX)
    m = build_oos_market(index_df, OD.read("fred", "DTB3"), OD.read("fred", "BAA10Y"))
    with oos_engine():
        bench = E.benchmark(m, OOS_WINDOW)
        res = {r: E.simulate(m, E.DEFAULTS[r], OOS_WINDOW) for r in RULES}
        bm = E.metrics(bench, m)
        cand = {r: E.metrics(x, m) for r, x in res.items()}
        crises = {lab: {"S&P 500 TR": E.crisis(bench, a, b), **{r: E.crisis(x, a, b) for r, x in res.items()}} for lab, a, b in OOS_CRISES}
    verdicts = {}
    for r in JUDGED:
        v = E.verdict(cand[r], bm)
        verdicts[r] = {**v, "verdict": "VALIDATED" if v["verdict"] == "CANDIDATE" else "NOT VALIDATED"}
    ref = {r: E.verdict(cand[r], bm) for r in REFERENCE}
    stitch = {"S&P 500": stitched(bench, PARENT_RUN / "equity_SPY.csv"), **{r: stitched(res[r], PARENT_RUN / f"equity_{r}.csv") for r in RULES}}
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = REPORTS / run_id
    out.mkdir(parents=True, exist_ok=True)
    for r, x in [("INDEX", bench), *res.items()]:
        pd.DataFrame({"date": [d.isoformat() for d in x.dates], "equity": x.equity, "holding": x.holding}).to_csv(
            out / f"equity_{r}.csv", index=False, float_format="%.17g")
        if r != "INDEX":
            (out / f"executions_{r}.json").write_text(json.dumps(x.executions, default=_json_default), encoding="utf-8")
    prov = prereg_info(path=PREREG)
    results = {"run_id": run_id, "preregistered_run": bool(prov.get("unchanged")), "prereg": prov, "code": code_commit(),
               "window": list(OOS_WINDOW), "first_decision": OOS_FIRST_DECISION, "benchmark": bm, "candidates": cand, "verdicts": verdicts,
               "reference_criteria": ref, "crises": crises, "tracking": tracking(index_df, OD.read("yahoo", "SPY")), "stitched": stitch,
               "carry": {"DTB3": m.rate_carry, "BAA10Y": m.credit_carry},
               "holdings_count": {r: pd.Series(x.holding).value_counts().to_dict() for r, x in res.items()},
               "first_execution": {r: x.executions[0] for r, x in res.items()}, "parent_run": PARENT_RUN.name}
    (out / "results.json").write_text(json.dumps(results, indent=1, default=_json_default), encoding="utf-8")
    ver = oos_verify.check(out)
    (out / "verification.json").write_text(json.dumps(ver, indent=1, default=_json_default), encoding="utf-8")
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    results["verification"] = ver
    REVIEW.parent.mkdir(parents=True, exist_ok=True)
    REVIEW.write_text(oos_report.render(results), encoding="utf-8")
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research.core_candidates.oos")
    sub = ap.add_subparsers(dest="cmd", required=True)
    dl = sub.add_parser("download")
    dl.add_argument("--refresh", action="store_true")
    sub.add_parser("run")
    rd = sub.add_parser("render")
    rd.add_argument("run_dir")
    args = ap.parse_args(argv)
    if args.cmd == "download":
        OD.ensure_cache(refresh=args.refresh, log=print)
        return 0
    if args.cmd == "render":
        from . import oos_report
        d = Path(args.run_dir)
        res = json.loads((d / "results.json").read_text(encoding="utf-8"))
        res["verification"] = json.loads((d / "verification.json").read_text(encoding="utf-8"))
        REVIEW.write_text(oos_report.render(res), encoding="utf-8")
        print("report:", REVIEW)
        return 0
    res = run_study()
    b = res["benchmark"]
    for r in RULES:
        c = res["candidates"][r]
        v = res["verdicts"][r] if r in res["verdicts"] else {"verdict": "reference (no verdict)", "sub_wins": res["reference_criteria"][r]["sub_wins"]}
        print(r, v["verdict"], f"CAGR {c['cagr']:.4f} vs {b['cagr']:.4f}", f"MDD {c['mdd']:.4f} vs {b['mdd']:.4f}", v["sub_wins"])
    print("verification", res["verification"]["matched"], "/", res["verification"]["checks"], "PASS" if res["verification"]["pass"] else "FAIL")
    print("preregistered run:", res["preregistered_run"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
