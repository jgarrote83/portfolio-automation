"""The four questions of the bonds-vs-SPY analysis, computed from the day table and the minute closes (pure; no I/O).

Q1  contemporaneous correlations (gap, morning, full day) of TLT and IEF with SPY, overall and per year; the quarter-by-quarter full-day correlation.
Q2  pooled minute cross-correlation 09:31-11:00 at lags -10..+10 (positive lag = the bond moves first).
Q3  SPY's rest-of-day return on the bond's gap and morning move AND SPY's own gap and morning move, OLS with Newey-West t-statistics.
Q4  the top 10% of days by |TLT gap|: the same Q1 and Q3 on those days alone, and the 20 largest days.

Everything is descriptive and exploratory. Regression variables are in PERCENT.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from . import stats

BONDS = ("TLT", "IEF")
Q1_MEASURES = ("gap", "morning", "full")
PERIODS = {"all": None, "2024": 2024, "2025": 2025}
Q3_COLS = ["const", "bond gap", "bond morning", "SPY gap", "SPY morning"]


def _sub(table: pd.DataFrame, period: int | None) -> pd.DataFrame:
    if period is None:
        return table
    return table[[d.year == period for d in table.index]]


# ---------------------------------------------------------------------------------------------- descriptive
def describe(table: pd.DataFrame) -> list[dict]:
    rows = []
    for s in ("TLT", "IEF", "SPY"):
        for m in ("gap", "morning", "rest", "full"):
            v = table[f"{s}_{m}"].to_numpy() * 100.0
            rows.append({"symbol": s, "measure": m, "n": len(v), "mean": float(v.mean()), "std": float(v.std(ddof=1)), "min": float(v.min()), "max": float(v.max())})
    return rows


# ------------------------------------------------------------------------------------------------------- Q1
def q1_correlations(table: pd.DataFrame) -> list[dict]:
    """Rows: bond, measure, period -> n, Pearson, Spearman."""
    out = []
    for bond in BONDS:
        for measure in Q1_MEASURES:
            for label, yr in PERIODS.items():
                t = _sub(table, yr)
                out.append({"bond": bond, "measure": measure, "period": label, **stats.corr_row(t[f"{bond}_{measure}"], t["SPY_" + measure])})
    return out


def q1_quarterly(table: pd.DataFrame) -> list[dict]:
    quarters = sorted({stats.quarter_label(d) for d in table.index})
    out = []
    for q in quarters:
        t = table[[stats.quarter_label(d) == q for d in table.index]]
        row = {"quarter": q}
        for bond in BONDS:
            row[bond] = stats.corr_row(t[f"{bond}_full"], t["SPY_full"])
        out.append(row)
    return out


# ------------------------------------------------------------------------------------------------------- Q2
def q2_cross_correlation(days: list[date], closes: dict[str, dict[date, np.ndarray]], lags=range(-10, 11), boot: int = 2000, seed: int = 11) -> dict:
    """For each bond, the pooled minute cross-correlation with SPY over `days` (the usable days of the day table)."""
    use = [d for d in days if d in closes["SPY"] and all(d in closes[b] for b in BONDS)]
    out = {"days": len(use)}
    for bond in BONDS:
        out[bond] = stats.cross_correlation([closes[bond][d] for d in use], [closes["SPY"][d] for d in use], lags, boot=boot, seed=seed)
    return out


# ------------------------------------------------------------------------------------------------------- Q3
def q3_design(table: pd.DataFrame, bond: str) -> tuple[np.ndarray, np.ndarray]:
    y = table["SPY_rest"].to_numpy() * 100.0
    X = np.column_stack([np.ones(len(table)), table[f"{bond}_gap"].to_numpy() * 100.0, table[f"{bond}_morning"].to_numpy() * 100.0,
                         table["SPY_gap"].to_numpy() * 100.0, table["SPY_morning"].to_numpy() * 100.0])
    return y, X


def q3_fit(table: pd.DataFrame, bond: str, lag: int | None = None) -> dict:
    """SPY rest-of-day (%) on the bond's gap and morning and SPY's own gap and morning (%), HAC t-statistics; the same model without the bond
    terms gives the baseline R^2; the Wald test is the joint test that BOTH bond coefficients are zero."""
    y, X = q3_design(table, bond)
    fit = stats.ols_hac(y, X, Q3_COLS, lag=lag)
    base = stats.ols_hac(y, X[:, [0, 3, 4]], ["const", "SPY gap", "SPY morning"], lag=lag)
    w = stats.wald(fit, [1, 2])
    return {"bond": bond, "n": fit["n"], "lag": fit["lag"], "beta": fit["beta"].tolist(), "se": fit["se"].tolist(), "t": fit["t"].tolist(), "p": fit["p"].tolist(),
            "r2": fit["r2"], "adj_r2": fit["adj_r2"], "baseline_r2": base["r2"], "baseline_adj_r2": base["adj_r2"],
            "delta_r2": fit["r2"] - base["r2"], "wald": w}


def q3_all(table: pd.DataFrame) -> list[dict]:
    out = []
    for bond in BONDS:
        for label, yr in PERIODS.items():
            t = _sub(table, yr)
            out.append({"period": label, **q3_fit(t, bond)})
    return out


# ------------------------------------------------------------------------------------------------------- Q4
def top_abs_days(table: pd.DataFrame, col: str = "TLT_gap", frac: float = 0.10) -> list[date]:
    """The k = round(frac * n) days with the largest |col|; ties broken by date (deterministic)."""
    k = max(1, round(frac * len(table)))
    order = sorted(table.index, key=lambda d: (-abs(float(table.loc[d, col])), d))
    return sorted(order[:k])


def q4_big_days(table: pd.DataFrame, listing: int = 20) -> dict:
    big = top_abs_days(table)
    bigset = set(big)
    in_big = table[[d in bigset for d in table.index]]
    rest = table[[d not in bigset for d in table.index]]
    corr = []
    for bond in BONDS:
        for measure in Q1_MEASURES:
            corr.append({"bond": bond, "measure": measure, "big": stats.corr_row(in_big[f"{bond}_{measure}"], in_big["SPY_" + measure]),
                         "other": stats.corr_row(rest[f"{bond}_{measure}"], rest["SPY_" + measure])})
    # event days are scattered through time, so adjacent rows are not adjacent days: no HAC lag (White) for them
    reg = []
    for bond in BONDS:
        reg.append({"bond": bond, "sample": "big |TLT gap| days", **q3_fit(in_big, bond, lag=0)})
        reg.append({"bond": bond, "sample": "all other days", **q3_fit(rest, bond)})
    ranked = sorted(table.index, key=lambda d: (-abs(float(table.loc[d, "TLT_gap"])), d))[:listing]
    top = [{"date": d, "weekday": d.strftime("%a"), "tlt_gap": float(table.loc[d, "TLT_gap"]) * 100, "ief_gap": float(table.loc[d, "IEF_gap"]) * 100,
            "spy_gap": float(table.loc[d, "SPY_gap"]) * 100, "spy_morning": float(table.loc[d, "SPY_morning"]) * 100,
            "spy_rest": float(table.loc[d, "SPY_rest"]) * 100} for d in ranked]
    thr = min(abs(float(table.loc[d, "TLT_gap"])) for d in big) * 100
    return {"n_big": len(big), "n_other": len(rest), "threshold_abs_gap_pct": thr, "corr": corr, "reg": reg, "top": top, "big_days": big}


def q4_robustness(table: pd.DataFrame) -> list[dict]:
    """How much of the big-day regression rests on single days: the joint bond-term Wald p on the big days, its range under leave-one-day-out,
    and the fit with April 2025 (the tariff shock) removed. Added after the first look at the numbers; descriptive only."""
    big = top_abs_days(table)
    b = table[[d in set(big) for d in table.index]]
    out = []
    for bond in BONDS:
        full = q3_fit(b, bond, lag=0)
        loo = sorted((q3_fit(b[[x != d for x in b.index]], bond, lag=0)["wald"]["p"], d) for d in big)
        apr = b[[not (d.year == 2025 and d.month == 4) for d in b.index]]
        fa = q3_fit(apr, bond, lag=0)
        out.append({"bond": bond, "n": full["n"], "p": full["wald"]["p"], "delta_r2": full["delta_r2"], "loo_min": loo[0], "loo_max": loo[-1],
                    "april_2025_days": [d for d in big if d.year == 2025 and d.month == 4], "n_ex_april": fa["n"], "p_ex_april": fa["wald"]["p"],
                    "delta_r2_ex_april": fa["delta_r2"], "beta_ex_april": fa["beta"], "t_ex_april": fa["t"]})
    return out


# ------------------------------------------------------------------- sensitivity: first trading day of each month
def month_start_sensitivity(table: pd.DataFrame, month_starts: set[date]) -> dict:
    """TLT and IEF distribute monthly and the raw prices drop by the distribution on the ex-date. There is no distribution calendar in the
    data, so this is a crude check: the first trading day of each month against the rest, and the headline Q1/Q3 numbers without those days."""
    flag = [d in month_starts for d in table.index]
    on, off = table[flag], table[[not f for f in flag]]
    gaps = []
    for bond in BONDS:
        a, b = on[f"{bond}_gap"].to_numpy() * 100, off[f"{bond}_gap"].to_numpy() * 100
        gaps.append({"bond": bond, "n_on": len(a), "mean_on": float(a.mean()), "n_off": len(b), "mean_off": float(b.mean()),
                     "diff": float(a.mean() - b.mean()), "t_welch": float((a.mean() - b.mean()) / math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b)))})
    rerun = {"n": len(off), "corr": [], "reg": []}
    for bond in BONDS:
        for measure in ("gap", "full"):
            rerun["corr"].append({"bond": bond, "measure": measure, **stats.corr_row(off[f"{bond}_{measure}"], off["SPY_" + measure])})
        rerun["reg"].append(q3_fit(off, bond))
    return {"gaps": gaps, "without": rerun}


# ----------------------------------------------------------------------------------------------- coverage
def minute_coverage(days: list[date], closes: dict[str, dict[date, np.ndarray]]) -> list[dict]:
    """How many of the 91 grid bars (09:30-11:00) exist, per symbol, over the usable days (a missing bar is never filled)."""
    out = []
    for s in ("TLT", "IEF", "SPY"):
        have = [int(np.isfinite(closes[s][d]).sum()) for d in days if d in closes[s]]
        out.append({"symbol": s, "days": len(have), "mean_bars": float(np.mean(have)) if have else None, "min_bars": min(have) if have else None,
                    "share_present": float(np.sum(have)) / (91 * len(have)) if have else None})
    return out
