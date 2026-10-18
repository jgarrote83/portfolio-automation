"""C5 holdout smoke test, 2026-01-02 to 2026-05-19 (docs/specs/C5_Holdout_Preregistration.md).

    PYTHONPATH=src python -m research.core_candidates.holdout_run download
    PYTHONPATH=src python -m research.core_candidates.holdout_run run

Reads nothing dated after 2026-05-19. Everything 2026-dated happens inside `holdout.allow(date(2026, 5, 19))`; outside it the package's
2026 refusals are unchanged. The engine is called unchanged; its two window constants are set in a scoped override.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import contextmanager
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from backtest.data.config import REPO_ROOT
from backtest.provenance import code_commit, prereg_info
from backtest.wheel_data import http_text

from . import data as D
from . import engine as E
from . import holdout

CACHE = REPO_ROOT / "data" / "reference" / "c5_holdout"
PREREG = "docs/specs/C5_Holdout_Preregistration.md"
REPORTS = REPO_ROOT / "reports" / "c5_holdout"
REVIEW = REPO_ROOT / ".review" / "c5-holdout-results.md"
WINDOW = (date(2026, 1, 2), date(2026, 5, 19))
FIRST_DECISION = date(2025, 12, 31)
LAST_ALLOWED = holdout.HOLDOUT_END
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd=2024-06-01&coed=2026-05-19"


class AfterHoldout(ValueError):
    pass


def _bound(df: pd.DataFrame, name: str) -> pd.DataFrame:
    if len(df) and df["date"].max().date() > LAST_ALLOWED:
        raise AfterHoldout(f"{name}: a row dated {df['date'].max().date()} is after {LAST_ALLOWED}; refused")
    return df


def parse_fred(text: str, series: str) -> pd.DataFrame:
    """This study's own FRED CSV parser (backtest.wheel_data.parse_fred_csv refuses 2026 and must stay unchanged)."""
    raw = pd.read_csv(io.StringIO(text))
    out = pd.DataFrame({"date": pd.to_datetime(raw.iloc[:, 0], errors="coerce"), "value": pd.to_numeric(raw.iloc[:, 1], errors="coerce")})
    out = out.dropna(subset=["date"]).drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
    return _bound(out, series)


def download(fetch_text=None, fetch_yahoo=None) -> dict:
    metas = {}
    with holdout.allow(LAST_ALLOWED):
        df = _bound(D.normalise_yahoo((fetch_yahoo or D.yahoo_history)("SPY", "2024-06-01", "2026-05-20")), "SPY")
        metas["SPY"] = D._write(df, {"symbol": "SPY", "downloaded_utc": D._now(), "request": {"start": "2024-06-01", "end_exclusive": "2026-05-20"}},
                                "yahoo", "SPY", CACHE)
        for s in ("DTB3", "BAA10Y"):
            df = parse_fred((fetch_text or http_text)(FRED_URL.format(series=s)), s)
            metas[s] = D._write(df, {"series": s, "source": FRED_URL.format(series=s), "downloaded_utc": D._now()}, "fred", s, CACHE)
    return metas


@contextmanager
def holdout_engine():
    saved = (E.FIRST_DECISION, E.SUB_PERIODS)
    E.FIRST_DECISION, E.SUB_PERIODS = FIRST_DECISION, (WINDOW,)
    try:
        with holdout.allow(LAST_ALLOWED):
            yield
    finally:
        E.FIRST_DECISION, E.SUB_PERIODS = saved


def load_market() -> E.Market:
    spy = _bound(D.read("yahoo", "SPY", CACHE)[0], "SPY")
    dtb3 = _bound(D.read("fred", "DTB3", CACHE)[0], "DTB3")
    baa = _bound(D.read("fred", "BAA10Y", CACHE)[0], "BAA10Y")
    m = E.build_market({"SPY": spy}, dtb3, baa, WINDOW)
    for s in ("EFA", "AGG", "ACWX"):
        m.adj.setdefault(s, np.full(len(m.days), np.nan))
    return m


def smoke_data(m: E.Market) -> dict:
    s, e = m.idx(WINDOW[0]), m.idx(WINDOW[1])
    missing_px = [m.days[t] for t in range(s, e + 1) if np.isnan(m.adj["SPY"][t])]
    missing_rate = [m.days[t] for t in range(s, e + 1) if np.isnan(m.rate[t])]
    return {"window_days": e - s + 1, "missing_spy": missing_px, "missing_rate": missing_rate,
            "dtb3_carry": m.rate_carry, "baa_carry": m.credit_carry, "pass": not missing_px and not missing_rate}


def smoke_timing(m: E.Market, res: E.Result) -> dict:
    """Item 5(c): the holding changes only on the first trading day after a month-end."""
    me = set(E.month_end_indices(m.days))
    bad = []
    for i in range(1, len(res.holding)):
        if res.holding[i] != res.holding[i - 1]:
            t = m.idx(res.dates[i])
            if (t - 1) not in me:
                bad.append(res.dates[i])
    return {"changes": sum(1 for i in range(1, len(res.holding)) if res.holding[i] != res.holding[i - 1]), "off_schedule": bad, "pass": not bad}


def run() -> dict:
    from . import holdout_verify
    with holdout_engine():
        m = load_market()
        res = E.simulate(m, E.DEFAULTS["C5"], WINDOW)
        spy = E.benchmark(m, WINDOW)
        cm, sm = E.metrics(res, m), E.metrics(spy, m)
        inputs = []
        for x in res.executions:
            d = m.idx(x["decision_date"])
            me = [i for i in E.month_end_indices(m.days) if i <= d][-10:]
            inputs.append({"decision": x["decision_date"], "execution": x["execution_date"], "holding": x["label"],
                           "spy_close": float(m.adj["SPY"][d]), "sma10": float(np.mean(m.adj["SPY"][me])), "trend": x["trend"],
                           "credit": float(m.credit[d]), "credit_avg126": float(np.mean(m.credit[d - 125: d + 1])), "credit_ok": x["credit"],
                           "cost": x["cost"], "equity_after": x["equity_after"]})
        data_check, timing = smoke_data(m), smoke_timing(m, res)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = REPORTS / run_id
        out.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"date": [d.isoformat() for d in res.dates], "equity": res.equity, "holding": res.holding}).to_csv(out / "equity_C5.csv", index=False, float_format="%.17g")
        (out / "executions.json").write_text(json.dumps(res.executions, default=str), encoding="utf-8")
    ver = holdout_verify.check(out)
    prov = prereg_info(path=PREREG)
    result = {"run_id": run_id, "prereg": prov, "code": code_commit(), "window": WINDOW, "c5": {k: cm[k] for k in ("total_return", "mdd", "peak", "trough", "costs")},
              "spy": {k: sm[k] for k in ("total_return", "mdd", "peak", "trough")}, "decisions": inputs, "data": data_check, "timing": timing,
              "verification": ver, "holding_days": pd.Series(res.holding).value_counts().to_dict()}
    result["smoke"] = "SMOKE PASS" if (data_check["pass"] and ver["pass"] and timing["pass"] and prov.get("unchanged")) else "SMOKE FAIL"
    (out / "results.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    REVIEW.parent.mkdir(parents=True, exist_ok=True)
    REVIEW.write_text(render(json.loads((out / "results.json").read_text(encoding="utf-8"))), encoding="utf-8")
    return result


def render(r: dict) -> str:
    def pct(v):
        return f"{v * 100:+.2f}%"
    d, t, v = r["data"], r["timing"], r["verification"]
    out = ["# C5 holdout smoke test: 2026-01-02 to 2026-05-19", "",
           f"Run `{r['run_id']}`; pre-registration `{r['prereg']['path']}` (first commit `{(r['prereg'].get('first_commit') or '?')[:12]}`, rule text "
           f"{'unchanged' if r['prereg'].get('unchanged') else 'CHANGED'}); code `{(r['code'].get('sha') or '?')[:12]}`. Nothing dated after 2026-05-19 was read.", "",
           f"## Result: **{r['smoke']}**", "",
           "A smoke test, not a verdict: about four monthly decisions are far too few to judge performance, so there is no performance gate.", "",
           "| Check | Result |", "| --- | --- |",
           f"| Data loads with no uncounted gaps | {'pass' if d['pass'] else 'FAIL'}: {d['window_days']} window days, missing SPY closes {len(d['missing_spy'])}, missing T-bill rates "
           f"{len(d['missing_rate'])}; carried forward DTB3 on {len(d['dtb3_carry'])} days, BAA10Y on {len(d['baa_carry'])} |",
           f"| Every decision reproduced by an independent verifier | {'pass' if v['pass'] else 'FAIL'}: {v['matched']} of {v['checks']} checks |",
           f"| Positions change only at month-end decisions | {'pass' if t['pass'] else 'FAIL'}: {t['changes']} holding changes, {len(t['off_schedule'])} off schedule |", "",
           "## Decisions", "", "| Decided at the close of | Executed at the close of | SPY adj. close | 10-month average | Trend | BAA10Y (prior day) | 126-day average | Credit | Holding |",
           "| --- | --- | ---: | ---: | :---: | ---: | ---: | :---: | --- |"]
    for x in r["decisions"]:
        out.append(f"| {x['decision']} | {x['execution']} | {x['spy_close']:.2f} | {x['sma10']:.2f} | {'✓' if x['trend'] else '✗'} | {x['credit']:.2f} | "
                   f"{x['credit_avg126']:.3f} | {'✓' if x['credit_ok'] else '✗'} | {x['holding']} |")
    c, s = r["c5"], r["spy"]
    out += ["", "## Performance (reported, never judged)", "", "| | Return | Max drawdown | Peak → trough |", "| --- | ---: | ---: | --- |",
            f"| C5 | {pct(c['total_return'])} | {c['mdd'] * 100:.1f}% | {c['peak']} → {c['trough']} |",
            f"| SPY total return | {pct(s['total_return'])} | {s['mdd'] * 100:.1f}% | {s['peak']} → {s['trough']} |", "",
            f"Days by holding: {', '.join(f'{k} {n}' for k, n in r['holding_days'].items())}. Costs paid ${c['costs']:,.0f}. `2X` is the simulated 2x S&P fund of the parent study.", ""]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research.core_candidates.holdout_run")
    ap.add_argument("cmd", choices=("download", "run"))
    a = ap.parse_args(argv)
    if a.cmd == "download":
        for k, mm in download().items():
            print(k, mm["rows"], mm["first_date"], mm["last_date"])
        return 0
    r = run()
    print(r["smoke"], "| verification", r["verification"]["matched"], "/", r["verification"]["checks"])
    return 0 if r["smoke"] == "SMOKE PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
