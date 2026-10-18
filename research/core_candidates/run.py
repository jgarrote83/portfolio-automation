"""Core candidates study: download the reference data, or run the pre-registered study end to end.

    PYTHONPATH=src python -m research.core_candidates.run download [--refresh]
    PYTHONPATH=src python -m research.core_candidates.run run

`run` reads only the cache, simulates the five candidates, SPY and the 17 sensitivities, validates the 2x simulation against SSO, runs the
independent verifier, and writes `reports/core_candidates/<run-id>/` (gitignored) and `.review/core-candidates-results.md`.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from backtest.provenance import code_commit, prereg_info

from . import data as D
from . import engine as E
from . import report, verify

REPO = Path(__file__).resolve().parents[2]
PREREG = "docs/specs/Core_Candidates_Preregistration.md"
REPORTS = REPO / "reports" / "core_candidates"
REVIEW = REPO / ".review" / "core-candidates-results.md"


def load_market() -> E.Market:
    prices = {s: D.read("yahoo", s)[0] for s in D.SYMBOLS}
    return E.build_market(prices, D.read("fred", "DTB3")[0], D.read("fred", "BAA10Y")[0])


def _json_default(o):
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(type(o))


def run_study() -> dict:
    m = load_market()
    spy_res = E.benchmark(m)
    spy = E.metrics(spy_res, m)
    defaults = {r: E.simulate(m, c) for r, c in E.DEFAULTS.items()}
    cand = {r: E.metrics(res, m) for r, res in defaults.items()}
    verdicts = {r: E.verdict(cand[r], spy) for r in cand}
    sens = []
    for c in E.sensitivities():
        mm = E.metrics(E.simulate(m, c), m)
        sens.append({"name": c.name, **{k: mm[k] for k in ("cagr", "mdd", "sharpe", "sub_periods", "costs", "trades_per_year")},
                     "would_pass": E.verdict(mm, spy)})
    crises = {}
    for label, a, b in E.CRISES:
        crises[label] = {"SPY": E.crisis(spy_res, a, b), **{r: E.crisis(res, a, b) for r, res in defaults.items()}}
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = REPORTS / run_id
    out.mkdir(parents=True, exist_ok=True)
    for r, res in [("SPY", spy_res), *defaults.items()]:
        pd.DataFrame({"date": [d.isoformat() for d in res.dates], "equity": res.equity, "holding": res.holding, "exposure": res.exposure}).to_csv(
            out / f"equity_{r}.csv", index=False, float_format="%.17g")
        if r != "SPY":
            (out / f"executions_{r}.json").write_text(json.dumps(res.executions, default=_json_default), encoding="utf-8")
    prov = prereg_info(path=PREREG)
    commit = code_commit()
    preregistered = bool(prov.get("unchanged")) and len(sens) == 17
    results = {"run_id": run_id, "preregistered_run": preregistered, "prereg": prov, "code": commit, "window": [E.WINDOW_START, E.WINDOW_END],
               "spy": spy, "candidates": cand, "verdicts": verdicts, "sensitivities": sens, "crises": crises, "sso": E.sso_validation(m),
               "carry": {"DTB3": m.rate_carry, "BAA10Y": m.credit_carry},
               "c2_exposure": {"mean": float(defaults["C2"].exposure.mean()), "min": float(defaults["C2"].exposure.min()),
                               "max": float(defaults["C2"].exposure.max())},
               "holdings_count": {r: pd.Series(res.holding).value_counts().to_dict() for r, res in defaults.items() if r != "C2"}}
    (out / "results.json").write_text(json.dumps(results, indent=1, default=_json_default), encoding="utf-8")
    ver = verify.check(out)
    (out / "verification.json").write_text(json.dumps(ver, indent=1, default=_json_default), encoding="utf-8")
    results["verification"] = ver
    REVIEW.parent.mkdir(parents=True, exist_ok=True)
    REVIEW.write_text(report.render(results), encoding="utf-8")
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research.core_candidates.run")
    sub = ap.add_subparsers(dest="cmd", required=True)
    dl = sub.add_parser("download")
    dl.add_argument("--refresh", action="store_true")
    sub.add_parser("run")
    args = ap.parse_args(argv)
    if args.cmd == "download":
        D.ensure_cache(refresh=args.refresh, log=print)
        return 0
    res = run_study()
    for r, v in res["verdicts"].items():
        print(r, v["verdict"], f"CAGR {res['candidates'][r]['cagr']:.4f} vs SPY {res['spy']['cagr']:.4f}",
              f"MDD {res['candidates'][r]['mdd']:.4f} vs {res['spy']['mdd']:.4f}", v["sub_wins"])
    print("verification", res["verification"]["matched"], "/", res["verification"]["checks"], "PASS" if res["verification"]["pass"] else "FAIL")
    print("preregistered run:", res["preregistered_run"], "report:", REVIEW)
    return 0


if __name__ == "__main__":
    sys.exit(main())
