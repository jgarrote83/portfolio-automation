"""Core candidates: the market alignment, the five rules' decisions, the simulator and the metrics (pure; no I/O).

Every item number below refers to section 2 of docs/specs/Core_Candidates_Preregistration.md.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date

import numpy as np
import pandas as pd

WINDOW_START, WINDOW_END = date(2005, 1, 3), date(2025, 12, 31)
FIRST_DECISION = date(2004, 12, 31)
HOLDOUT_START = date(2026, 1, 1)
START_EQUITY = 100_000.0
PPY = 252
SPREAD = 0.005
LEV_EXPENSE = 0.0091
MAX_STALE_DAYS = 7
SUB_PERIODS = ((date(2005, 1, 1), date(2011, 12, 31)), (date(2012, 1, 1), date(2018, 12, 31)), (date(2019, 1, 1), date(2025, 12, 31)))
ASSETS = ("SPY", "EFA", "AGG", "ACWX", "2X")


class HoldoutRefused(ValueError):
    pass


def refuse_holdout(*days) -> None:
    for d in days:
        if (d.date() if isinstance(d, pd.Timestamp) else d) >= HOLDOUT_START:
            raise HoldoutRefused(f"{d}: dates on/after {HOLDOUT_START} are this study's holdout; refused (no override).")


# ============================================================================================== market
@dataclass
class Market:
    days: list                      # datetime.date, SPY trading days, ascending (look-back rows included)
    adj: dict                       # symbol -> np.ndarray adjusted close aligned to days (NaN where absent)
    rate: np.ndarray                # annual T-bill rate used for day t (item 5); NaN before it is defined
    credit: np.ndarray              # c_t (item 9)
    rate_carry: list = field(default_factory=list)      # [(day, value date used)] window days with a carried DTB3
    credit_carry: list = field(default_factory=list)

    def idx(self, d: date) -> int:
        return self.days.index(d)

    def ret(self, sym: str) -> np.ndarray:
        a = self.adj[sym]
        out = np.full(len(a), np.nan)
        out[1:] = a[1:] / a[:-1] - 1.0
        return out


def _prepared(fred: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    v = fred.dropna(subset=["value"]).sort_values("date")
    return np.array([d.date() for d in v["date"]], dtype=object), v["value"].to_numpy(dtype=float)


def _latest_value(prep: tuple[np.ndarray, np.ndarray], cutoff: date, strict: bool) -> tuple[float, date] | None:
    """The latest non-blank FRED value dated <= cutoff (or < cutoff when strict)."""
    dates, vals = prep
    k = int(np.searchsorted(dates, cutoff, side="left" if strict else "right")) - 1
    if k < 0:
        return None
    return float(vals[k]), dates[k]


def build_market(prices: dict[str, pd.DataFrame], dtb3: pd.DataFrame, baa: pd.DataFrame, window=(WINDOW_START, WINDOW_END)) -> Market:
    """Align every symbol's adjusted close on SPY's trading days; derive r_t (item 5) and c_t (item 9) with their carry-forward lists."""
    refuse_holdout(window[1])
    spy = prices["SPY"]
    days = [d.date() for d in spy["date"]]
    refuse_holdout(*days[-1:])
    adj = {}
    for sym, df in prices.items():
        m = dict(zip((d.date() for d in df["date"]), df["adj_close"].astype(float)))
        adj[sym] = np.array([m.get(d, np.nan) for d in days], dtype=float)
    n = len(days)
    rate, credit = np.full(n, np.nan), np.full(n, np.nan)
    dtb3_days = {d.date(): v for d, v in zip(dtb3["date"], dtb3["value"])}
    baa_days = {d.date(): v for d, v in zip(baa["date"], baa["value"])}
    rate_carry, credit_carry = [], []
    p_dtb3, p_baa = _prepared(dtb3), _prepared(baa)
    for t in range(1, n):
        prev = days[t - 1]
        r = _latest_value(p_dtb3, prev, strict=False)
        c = _latest_value(p_baa, days[t], strict=True)
        in_window = window[0] <= days[t] <= window[1]
        if r is not None:
            rate[t] = r[0] / 100.0
            if in_window and (pd.isna(dtb3_days.get(prev, np.nan))):
                rate_carry.append((days[t], r[1]))
                if (prev - r[1]).days > MAX_STALE_DAYS:
                    raise ValueError(f"DTB3 for {days[t]} would be carried {(prev - r[1]).days} days (> {MAX_STALE_DAYS}); no verdict.")
        if c is not None:
            credit[t] = c[0]
            if in_window and pd.isna(baa_days.get(prev, np.nan)):
                credit_carry.append((days[t], c[1]))
                if (prev - c[1]).days > MAX_STALE_DAYS:
                    raise ValueError(f"BAA10Y for {days[t]} would be carried {(prev - c[1]).days} days (> {MAX_STALE_DAYS}); no verdict.")
    return Market(days, adj, rate, credit, rate_carry, credit_carry)


# ====================================================================================== decision days
def month_end_indices(days: list) -> list[int]:
    return [i for i in range(len(days)) if i + 1 == len(days) or (days[i + 1].year, days[i + 1].month) != (days[i].year, days[i].month)]


def week_end_indices(days: list) -> list[int]:
    return [i for i in range(len(days)) if i + 1 == len(days) or days[i + 1].isocalendar()[:2] != days[i].isocalendar()[:2]]


def decision_indices(m: Market, kind: str, window=(WINDOW_START, WINDOW_END)) -> list[int]:
    """Decision days d with FIRST_DECISION <= d and an execution day (d + 1) inside the window (item 3)."""
    s, e = m.idx(window[0]), m.idx(window[1])
    if kind == "month":
        cand = month_end_indices(m.days)
    elif kind == "week":
        cand = week_end_indices(m.days)
    elif kind == "day":
        cand = list(range(len(m.days)))
    else:
        raise ValueError(kind)
    # the last trading day of the full SPY history is a month/week end only if the next day exists in the calendar: guard with e
    return [d for d in cand if s - 1 <= d and d + 1 <= e and m.days[d] >= FIRST_DECISION]


# ============================================================================================ signals
def trend_monthly(m: Market, d: int, months: int = 10) -> bool:
    """Item 7: adj at month-end d > mean of the `months` most recent month-end adj closes including d."""
    me = [i for i in month_end_indices(m.days) if i <= d]
    if me[-1] != d:
        raise ValueError(f"{m.days[d]} is not a month-end")
    if len(me) < months:
        raise ValueError("not enough month-ends for the moving average")
    a = m.adj["SPY"]
    return bool(a[d] > float(np.mean(a[me[-months:]])))


def trend_daily(m: Market, d: int, n: int = 200) -> bool:
    a = m.adj["SPY"][d - n + 1: d + 1]
    if d - n + 1 < 0 or np.isnan(a).any():
        raise ValueError("not enough days for the 200-day average")
    return bool(m.adj["SPY"][d] > float(a.mean()))


def credit_calm(m: Market, d: int, window: int = 126) -> bool:
    """Item 9: c_d <= mean of c over the `window` most recent SPY trading days ending at d (inclusive)."""
    c = m.credit[d - window + 1: d + 1]
    if d - window + 1 < 0 or np.isnan(c).any():
        raise ValueError("not enough credit history")
    return bool(m.credit[d] <= float(c.mean()))


def realised_vol(m: Market, d: int, n: int = 21) -> float:
    r = m.ret("SPY")[d - n + 1: d + 1]
    if np.isnan(r).any():
        raise ValueError("not enough returns for the volatility")
    return float(np.std(r, ddof=1) * math.sqrt(PPY))


def vol_exposure(sigma: float, target: float = 0.15, cap: float = 1.5) -> float:
    return min(cap, target / sigma)


def twelve_month(m: Market, sym: str, d: int) -> float | None:
    """Item 10: adj_d / adj at the month-end of the same calendar month one year earlier - 1; None without both prices."""
    me = month_end_indices(m.days)
    y, mo = m.days[d].year - 1, m.days[d].month
    prev = [i for i in me if (m.days[i].year, m.days[i].month) == (y, mo)]
    if not prev:
        return None
    a0, a1 = m.adj[sym][prev[0]], m.adj[sym][d]
    if np.isnan(a0) or np.isnan(a1):
        return None
    return float(a1 / a0 - 1.0)


def tbill_twelve_month(m: Market, d: int) -> float:
    me = month_end_indices(m.days)
    y, mo = m.days[d].year - 1, m.days[d].month
    p = [i for i in me if (m.days[i].year, m.days[i].month) == (y, mo)][0]
    r = m.rate[p + 1: d + 1]
    if np.isnan(r).any():
        raise ValueError("T-bill rate missing in the 12-month window")
    return float(np.prod(1.0 + r / PPY) - 1.0)


def dual_momentum(m: Market, d: int, intl: str = "EFA") -> str:
    spy = twelve_month(m, "SPY", d)
    if spy is None:
        raise ValueError("no 12-month SPY return")
    if not spy > tbill_twelve_month(m, d):
        return "AGG"
    other = intl
    if intl == "ACWX" and twelve_month(m, "ACWX", d) is None:
        other = "EFA"
    o = twelve_month(m, other, d)
    if o is None:
        raise ValueError(f"no 12-month {other} return")
    return "SPY" if spy >= o else other


# ======================================================================================== candidates
@dataclass(frozen=True)
class Config:
    name: str
    rule: str                       # C1 / C2 / C3 / C4 / C5
    cost_bps: float = 2.0
    leverage: float = 2.0
    sma_months: int = 10
    daily_200: bool = False
    target_vol: float = 0.15
    vol_cap: float = 1.5
    vol_window: int = 21
    intl: str = "EFA"
    credit_window: int = 126

    def with_(self, **kw) -> "Config":
        return replace(self, **kw)


DEFAULTS = {r: Config(r, r) for r in ("C1", "C2", "C3", "C4", "C5")}


def sensitivities() -> list[Config]:
    d = DEFAULTS
    out = [d["C1"].with_(name="C1-200d", daily_200=True), d["C1"].with_(name="C1-1.5x", leverage=1.5),
           d["C2"].with_(name="C2-12", target_vol=0.12), d["C2"].with_(name="C2-18", target_vol=0.18),
           d["C3"].with_(name="C3-ACWX", intl="ACWX"), d["C4"].with_(name="C4-63", credit_window=63), d["C4"].with_(name="C4-252", credit_window=252)]
    for r in ("C1", "C2", "C3", "C4", "C5"):
        out += [d[r].with_(name=f"{r}-0bp", cost_bps=0.0), d[r].with_(name=f"{r}-10bp", cost_bps=10.0)]
    return out


def decisions(m: Market, cfg: Config, window=(WINDOW_START, WINDOW_END)) -> list[tuple[int, dict, dict]]:
    """[(decision index, target weights, info)] in time order."""
    out = []
    if cfg.rule == "C2":
        for d in decision_indices(m, "week", window):
            sig = realised_vol(m, d, cfg.vol_window)
            e = vol_exposure(sig, cfg.target_vol, cfg.vol_cap)
            out.append((d, {"SPY": e, "TB": 1.0 - e}, {"sigma": sig, "exposure": e}))
        return out
    if cfg.rule == "C1" and cfg.daily_200:
        for d in decision_indices(m, "day", window):
            ok = trend_daily(m, d)
            out.append((d, {"2X": 1.0} if ok else {"TB": 1.0}, {"trend": ok}))
        return out
    for d in decision_indices(m, "month", window):
        if cfg.rule == "C1":
            ok = trend_monthly(m, d, cfg.sma_months)
            out.append((d, {"2X": 1.0} if ok else {"TB": 1.0}, {"trend": ok}))
        elif cfg.rule == "C3":
            h = dual_momentum(m, d, cfg.intl)
            out.append((d, {h: 1.0}, {"hold": h}))
        elif cfg.rule == "C4":
            ok = credit_calm(m, d, cfg.credit_window)
            out.append((d, {"2X": 1.0} if ok else {"TB": 1.0}, {"credit": ok}))
        elif cfg.rule == "C5":
            t, c = trend_monthly(m, d, cfg.sma_months), credit_calm(m, d, cfg.credit_window)
            w = {"2X": 1.0} if (t and c) else ({"SPY": 1.0} if (t or c) else {"TB": 1.0})
            out.append((d, w, {"trend": t, "credit": c}))
        else:
            raise ValueError(cfg.rule)
    return out


# ======================================================================================== simulation
def lev_return(spy_ret: float, rate: float, leverage: float = 2.0) -> float:
    """Item 6: leverage x SPY - (leverage - 1) x (rate + spread)/252 - expense/252."""
    return leverage * spy_ret - (leverage - 1.0) * (rate + SPREAD) / PPY - LEV_EXPENSE / PPY


def trade_cost(current: dict, target: dict, bps: float) -> float:
    """Item 12: bps x (sum |target - current| over the risky assets + |max(target TB, 0) - max(current TB, 0)|)."""
    traded = sum(abs(target.get(a, 0.0) - current.get(a, 0.0)) for a in ASSETS)
    traded += abs(max(target.get("TB", 0.0), 0.0) - max(current.get("TB", 0.0), 0.0))
    return bps / 10_000.0 * traded


def label(w: dict) -> str:
    live = {k: v for k, v in w.items() if abs(v) > 1e-12}
    if len(live) == 1:
        return next(iter(live))
    return " + ".join(f"{k} {v:.2f}" for k, v in sorted(live.items()))


@dataclass
class Result:
    cfg: Config
    dates: list
    equity: np.ndarray
    holding: list                   # label per window day (after that day's execution)
    exposure: np.ndarray            # SPY-equivalent exposure per window day (2X counts as leverage)
    executions: list                # dicts
    costs: float


def simulate(m: Market, cfg: Config, window=(WINDOW_START, WINDOW_END)) -> Result:
    refuse_holdout(window[1])
    s, e = m.idx(window[0]), m.idx(window[1])
    dec = {d + 1: (d, w, info) for d, w, info in decisions(m, cfg, window)}
    if s not in dec:
        raise ValueError("no decision executes on the first window day")
    rets = {a: m.ret(a) for a in ("SPY", "EFA", "AGG", "ACWX")}
    bal = {"TB": START_EQUITY}
    eq, hold, expo, execs, costs = [], [], [], [], 0.0
    cur_w = {"TB": 1.0}
    for t in range(s, e + 1):
        if t > s:
            r = m.rate[t]
            if np.isnan(r):
                raise ValueError(f"no T-bill rate for {m.days[t]}")
            for a in list(bal):
                v = bal[a]
                if a == "TB":
                    g = r / PPY if v >= 0 else (r + SPREAD) / PPY
                elif a == "2X":
                    g = lev_return(rets["SPY"][t], r, cfg.leverage)
                else:
                    g = rets[a][t]
                if np.isnan(g):
                    raise ValueError(f"{a} has no return on {m.days[t]} while held")
                bal[a] = v * (1.0 + g)
        E = sum(bal.values())
        if t in dec:
            d, w, info = dec[t]
            target = {a: x * E for a, x in w.items()}
            c = trade_cost(bal, target, cfg.cost_bps)
            scale = (E - c) / E
            new = {a: v * scale for a, v in target.items()}
            traded = c > 0 or any(abs(new.get(a, 0) - bal.get(a, 0)) > 1e-9 for a in set(new) | set(bal))
            prev_label = label(cur_w)
            bal, cur_w, costs = new, dict(w), costs + c
            E = sum(bal.values())
            execs.append({"decision_date": m.days[d], "execution_date": m.days[t], "weights": dict(w), "label": label(w),
                          "switched": label(w) != prev_label, "traded": traded, "cost": c, "equity_after": E, **info})
        eq.append(E)
        hold.append(label(cur_w))
        expo.append(cur_w.get("2X", 0.0) * cfg.leverage + cur_w.get("SPY", 0.0) + cur_w.get("EFA", 0.0) + cur_w.get("ACWX", 0.0))
    return Result(cfg, m.days[s:e + 1], np.array(eq), hold, np.array(expo), execs, costs)


def benchmark(m: Market, window=(WINDOW_START, WINDOW_END)) -> Result:
    s, e = m.idx(window[0]), m.idx(window[1])
    a = m.adj["SPY"]
    eq = START_EQUITY * a[s:e + 1] / a[s]
    n = e - s + 1
    return Result(Config("SPY", "SPY", cost_bps=0.0), m.days[s:e + 1], eq, ["SPY"] * n, np.ones(n), [], 0.0)


# =========================================================================================== metrics
def max_drawdown(dates: list, eq: np.ndarray) -> dict:
    peak = np.maximum.accumulate(eq)
    dd = 1.0 - eq / peak
    k = int(np.argmax(dd))
    p = int(np.argmax(eq[: k + 1])) if dd[k] > 0 else k
    rec = None
    for j in range(k, len(eq)):
        if eq[j] >= eq[p]:
            rec = dates[j]
            break
    return {"mdd": float(dd[k]), "peak": dates[p], "trough": dates[k], "recovery": rec if dd[k] > 0 else None}


def calendar_years(dates: list, eq: np.ndarray) -> dict[int, float]:
    out, prev_end = {}, eq[0]
    years = sorted({d.year for d in dates})
    for y in years:
        idx = [i for i, d in enumerate(dates) if d.year == y]
        out[y] = float(eq[idx[-1]] / prev_end - 1.0)
        prev_end = eq[idx[-1]]
    return out


def period_return(dates: list, eq: np.ndarray, start: date, end: date) -> float:
    inside = [i for i, d in enumerate(dates) if start <= d <= end]
    before = [i for i, d in enumerate(dates) if d < start]
    base = eq[before[-1]] if before else eq[0]
    return float(eq[inside[-1]] / base - 1.0)


def metrics(res: Result, m: Market) -> dict:
    dates, eq = res.dates, res.equity
    s = m.idx(dates[0])
    r = eq[1:] / eq[:-1] - 1.0
    rf = m.rate[s + 1: s + len(eq)] / PPY
    days_span = (dates[-1] - dates[0]).days
    years = days_span / 365.25
    ex = r - rf
    cal = calendar_years(dates, eq)
    mdd = max_drawdown(dates, eq)
    safe = sum(1 for h in res.holding if h in ("TB", "AGG"))
    out = {"name": res.cfg.name, "cagr": float((eq[-1] / eq[0]) ** (365.25 / days_span) - 1.0), "total_return": float(eq[-1] / eq[0] - 1.0),
           "vol": float(np.std(r, ddof=1) * math.sqrt(PPY)), "sharpe": float(ex.mean() / np.std(ex, ddof=1) * math.sqrt(PPY)),
           **mdd, "worst_year": min(cal.items(), key=lambda kv: kv[1]), "calendar": cal,
           "time_safe": safe / len(res.holding), "mean_exposure": float(res.exposure.mean()),
           "share_exposure_below_1": float((res.exposure < 1.0 - 1e-12).mean()),
           "trades_per_year": sum(1 for x in res.executions if x["traded"]) / years,
           "switches_per_year": sum(1 for x in res.executions[1:] if x["switched"]) / years,
           "costs": res.costs, "end_equity": float(eq[-1]),
           "sub_periods": [period_return(dates, eq, a, b) for a, b in SUB_PERIODS]}
    return out


def verdict(cand: dict, spy: dict) -> dict:
    c1 = cand["cagr"] > spy["cagr"]
    c2 = cand["mdd"] <= spy["mdd"]
    wins = [a > b for a, b in zip(cand["sub_periods"], spy["sub_periods"])]
    c3 = sum(wins) >= 2
    return {"cagr_beats": c1, "mdd_ok": c2, "sub_wins": wins, "sub_ok": c3, "verdict": "CANDIDATE" if (c1 and c2 and c3) else "REJECTED"}


# =========================================================================================== crisis
CRISES = (("2008–09", date(2007, 10, 1), date(2009, 12, 31)), ("Feb–Mar 2020", date(2020, 2, 1), date(2020, 4, 30)),
          ("2022", date(2022, 1, 3), date(2022, 12, 30)))


def crisis(res: Result, start: date, end: date) -> dict:
    idx = [i for i, d in enumerate(res.dates) if start <= d <= end]
    before = [i for i, d in enumerate(res.dates) if d < start]
    base = res.equity[before[-1]] if before else res.equity[idx[0]]
    seg = np.r_[base, res.equity[idx]]
    dd = float((1.0 - seg / np.maximum.accumulate(seg)).max())
    ex = [x for x in res.executions if start <= x["execution_date"] <= end]
    events, risk_on = [], None
    prior = [x for x in res.executions if x["execution_date"] < start]
    if prior:
        risk_on = _risk_on(prior[-1])
    for x in ex:
        on = _risk_on(x)
        if risk_on is None or on != risk_on:
            events.append({"date": x["execution_date"], "event": "risk restored" if on else "risk cut", "holding": x["label"]})
        risk_on = on
    return {"return": float(res.equity[idx[-1]] / base - 1.0), "max_dd": dd, "held_at_start": res.holding[idx[0]] if idx else None,
            "events": events, "executions": [(x["execution_date"], x["label"]) for x in ex if x["switched"] or res.cfg.rule == "C2"]}


def _risk_on(x: dict) -> bool:
    w = x["weights"]
    if "exposure" in x:
        return x["exposure"] >= 0.5
    return not (w.get("TB", 0) >= 1.0 - 1e-12 or w.get("AGG", 0) >= 1.0 - 1e-12)


# ===================================================================================== SSO validation
def sso_validation(m: Market, start: date = date(2006, 6, 22), end: date = WINDOW_END) -> dict:
    i0, i1 = m.idx(start), m.idx(end)
    spy, sso = m.ret("SPY"), m.ret("SSO")
    sim = np.array([lev_return(spy[t], m.rate[t]) for t in range(i0, i1 + 1)])
    real = sso[i0:i1 + 1]
    if np.isnan(real).any() or np.isnan(sim).any():
        raise ValueError("gap in the SSO validation series")
    dates = m.days[i0:i1 + 1]
    years = {}
    for y in sorted({d.year for d in dates}):
        k = [j for j, d in enumerate(dates) if d.year == y]
        a, b = float(np.prod(1 + sim[k]) - 1), float(np.prod(1 + real[k]) - 1)
        years[y] = {"sim": a, "sso": b, "diff_pp": (a - b) * 100}
    full = [v["diff_pp"] for y, v in years.items() if y >= 2007]
    return {"start": dates[0], "end": dates[-1], "corr": float(np.corrcoef(sim, real)[0, 1]), "years": years,
            "mean_diff_pp": float(np.mean(full)), "mean_abs_diff_pp": float(np.mean(np.abs(full))),
            "sim_total": float(np.prod(1 + sim) - 1), "sso_total": float(np.prod(1 + real) - 1),
            "flag": abs(float(np.mean(full))) > 1.5}
