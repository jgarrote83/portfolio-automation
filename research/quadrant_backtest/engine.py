"""Quadrant-tilt backtest engine (pure; no I/O). Item numbers refer to section 2 of docs/specs/Quadrant_Backtest_Preregistration.md.

A portfolio is a set of SLEEVES (a basket each, or the tilt sleeve), each a dict holding-symbol -> dollars that drifts with the holdings'
total returns between events. Costs are charged on the net per-symbol trade across sleeves (a name held in two baskets trades once).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

QUADS = ("Q1", "Q2", "Q3", "Q4")
HOLDOUT_START = date(2026, 1, 1)
EARLIEST_START = date(2011, 7, 1)
DETERMINATE_RUN = 20
START_EQUITY = 100_000.0
COST_BPS = 2.0
PPY = 252

# item 5: the config baskets (shared.quadrants.QUADRANT_CONCENTRATE), and Appendix A's proxies
BASKETS = {"Q1": ("SPY", "QQQ", "SMH"), "Q2": ("XLI", "XLF", "COWZ", "VDE", "PDBC", "VTIP"),
           "Q3": ("COWZ", "VDE", "GLD", "PDBC", "XLP", "XLV", "VTIP", "KMLM"), "Q4": ("GLD", "XLP", "XLV", "KMLM", "TLT", "IEF", "USMV")}
PROXY = {"COWZ": "IWD", "PDBC": "DBC", "VTIP": "STIP", "USMV": "SPLV", "KMLM": None}


class HoldoutRefused(ValueError):
    pass


def refuse_holdout(*days) -> None:
    for d in days:
        if d >= HOLDOUT_START:
            raise HoldoutRefused(f"{d} is on/after {HOLDOUT_START}: refused (no override).")


# ========================================================================================= quadrant signal
def window_start(replay_rows: list[dict], earliest: date = EARLIEST_START, run: int = DETERMINATE_RUN) -> date:
    """Item 2: the earliest business day d >= earliest such that d and the next run-1 business days all have a determinate quadrant."""
    days = [date.fromisoformat(r["date"]) for r in replay_rows]
    det = [r["active_quadrant"] in QUADS for r in replay_rows]
    for i, d in enumerate(days):
        if d >= earliest and i + run <= len(days) and all(det[i:i + run]):
            return d
    raise ValueError("no window start: the quadrant is never determinate for 20 consecutive business days")


def effective_quadrants(replay_rows: list[dict]) -> dict[date, str]:
    """Rule: an indeterminate day keeps the previous determinate quadrant; before the first one it is 'Q-ALL'."""
    out, last = {}, "Q-ALL"
    for r in replay_rows:
        q = r["active_quadrant"]
        if q in QUADS:
            last = q
        out[date.fromisoformat(r["date"])] = last
    return out


def in_force(trading_days: list[date], eff: dict[date, str]) -> list[str]:
    """Item 3: the quadrant in force at the close of trading day t = the effective quadrant of the last weekday <= t."""
    keys = sorted(eff)
    out, j, cur = [], 0, "Q-ALL"
    for t in trading_days:
        while j < len(keys) and keys[j] <= t:
            cur = eff[keys[j]]
            j += 1
        out.append(cur)
    return out


# ============================================================================================= baskets
def holding_of(member: str, t: date, first: dict[str, date]) -> str | None:
    """Item 7: the symbol actually held for `member` as of the close of t (the member from its first date; else its proxy; else None)."""
    if t >= first[member]:
        return member
    p = PROXY.get(member)
    if p is None:
        return None
    if t >= first[p]:
        return p
    return None


def composition(basket: tuple[str, ...], t: date, first: dict[str, date]) -> tuple[str, ...]:
    return tuple(h for h in (holding_of(m, t, first) for m in basket) if h is not None)


def equal(holdings: tuple[str, ...], total: float) -> dict[str, float]:
    return {h: total / len(holdings) for h in holdings} if holdings else {}


# ========================================================================================== simulation
@dataclass
class Result:
    name: str
    dates: list
    equity: np.ndarray
    held: list                         # the quadrant (or label) each day's RETURN was earned in (after the previous close's events)
    events: list = field(default_factory=list)
    costs: float = 0.0
    switches: int = 0


def _symbols_total(sleeves: dict) -> dict[str, float]:
    tot: dict[str, float] = {}
    for sl in sleeves.values():
        for h, v in sl.items():
            tot[h] = tot.get(h, 0.0) + v
    return tot


def _apply(sleeves_before: dict, sleeves_target: dict, bps: float) -> tuple[dict, float]:
    """Charge bps on the net per-symbol trade and scale every target by (E - C) / E (item 8)."""
    a, b = _symbols_total(sleeves_before), _symbols_total(sleeves_target)
    traded = sum(abs(b.get(s, 0.0) - a.get(s, 0.0)) for s in set(a) | set(b) if s != "_cash")   # the opening cash is not a holding
    c = bps / 10_000.0 * traded
    E = sum(b.values())
    k = (E - c) / E if E else 1.0
    return {n: {h: v * k for h, v in sl.items()} for n, sl in sleeves_target.items()}, c


def simulate(name: str, days: list[date], s: int, rets: dict, first: dict, plan, bps: float = COST_BPS) -> Result:
    """Generic sleeve simulator. `plan(t_index, sleeves, state) -> (target_sleeves | None, label)` decides the full-reset events;
    composition events (item 7) are applied by the simulator itself, per sleeve. Days are trading days; `s` is the start index."""
    refuse_holdout(days[-1])
    sleeves: dict[str, dict[str, float]] = {}
    state: dict = {"basket_of": {}}
    eq, held, events, costs, switches = [], [], [], 0.0, 0
    for t in range(s, len(days)):
        d = days[t]
        if t > s:
            for sl in sleeves.values():
                for h in sl:
                    r = rets[h][t]
                    if r is None or math.isnan(r):
                        raise ValueError(f"{h} has no return on {d} while held")
                    sl[h] *= 1.0 + r
        held.append(state.get("label"))
        E = sum(v for sl in sleeves.values() for v in sl.values()) if sleeves else START_EQUITY
        target, label = plan(t, sleeves, state, E)
        if target is None:
            # composition events: reset a sleeve whose basket membership changed at this close
            changed = {}
            for n, sl in sleeves.items():
                b = state["basket_of"].get(n)
                if b is None:
                    continue
                comp = composition(BASKETS[b], d, first)
                if set(comp) != set(sl):
                    changed[n] = equal(comp, sum(sl.values()))
            if changed:
                target = {**sleeves, **changed}
                label = state.get("label")
                events.append({"date": d, "kind": "composition", "sleeves": sorted(changed)})
        else:
            if state.get("label") is not None and label != state.get("label") and state.get("counts_as_switch", True):
                switches += 1
            events.append({"date": d, "kind": "rebalance", "label": label})
        if target is not None:
            before = sleeves if sleeves else {"_cash": {"_cash": START_EQUITY}}
            sleeves, c = _apply(before, target, bps)
            costs += c
            state["label"] = label
        if t == s:
            held[0] = state.get("label")          # no return is earned on the start day; record what was bought at its close
        eq.append(sum(v for sl in sleeves.values() for v in sl.values()))
    return Result(name, days[s:], np.array(eq), held, events, costs, switches)


# ------------------------------------------------------------------------------------------- the plans
def month_ends(days: list[date]) -> set[int]:
    """Indices of month-end trading days. The last day of the series counts only if the next calendar weekday falls in another month."""
    out = {i for i in range(len(days) - 1) if (days[i + 1].year, days[i + 1].month) != (days[i].year, days[i].month)}
    if days:
        nxt = days[-1] + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        if (nxt.year, nxt.month) != (days[-1].year, days[-1].month):
            out.add(len(days) - 1)
    return out


def plan_qtilt(days, force, first):
    """Q-TILT: full reset at the start (per the quadrant in force at the previous close) and whenever the in-force quadrant at the
    previous close differs from the held one (executed at this close)."""
    def plan(t, sleeves, state, E):
        want = force[t - 1]
        if not sleeves or want != state.get("label"):
            q = want
            if q == "Q-ALL":
                state["basket_of"] = {qq: qq for qq in QUADS}
                return _qall_target(days[t], first, E), "Q-ALL"
            state["basket_of"] = {"tilt": q}
            return {"tilt": equal(composition(BASKETS[q], days[t], first), E)}, q
        return None, None
    return plan


def _qall_target(d, first, E, frac: float = 1.0) -> dict:
    return {q: equal(composition(BASKETS[q], d, first), E * frac / 4.0) for q in QUADS}


def plan_qall(days, first):
    me = month_ends(days)

    def plan(t, sleeves, state, E):
        if not sleeves or t in me:
            state["basket_of"] = {q: q for q in QUADS}
            state["counts_as_switch"] = False
            return _qall_target(days[t], first, E), "Q-ALL"
        return None, None
    return plan


def plan_soft(days, force, first):
    me = month_ends(days)

    def plan(t, sleeves, state, E):
        want = force[t - 1]
        if not sleeves or want != state.get("label") or t in me:
            tgt = _qall_target(days[t], first, E, 0.5)
            bo = {q: q for q in QUADS}
            if want == "Q-ALL":
                extra = _qall_target(days[t], first, E, 0.5)
                tgt = {q: {h: tgt[q].get(h, 0) + extra[q].get(h, 0) for h in set(tgt[q]) | set(extra[q])} for q in QUADS}
            else:
                tgt["tilt"] = equal(composition(BASKETS[want], days[t], first), E * 0.5)
                bo["tilt"] = want
            state["basket_of"] = bo
            return tgt, want
        return None, None
    return plan


def plan_monthly(days, force, first):
    me = month_ends(days)

    def plan(t, sleeves, state, E):
        if not sleeves or (t - 1) in me:
            q = force[t - 1]
            if q == "Q-ALL":
                state["basket_of"] = {qq: qq for qq in QUADS}
                return _qall_target(days[t], first, E), "Q-ALL"
            state["basket_of"] = {"tilt": q}
            return {"tilt": equal(composition(BASKETS[q], days[t], first), E)}, q
        return None, None
    return plan


def basket_index(days, s, rets, first, q) -> np.ndarray:
    """Item 12: the standalone equal-weight basket, reset at month-ends and composition events, no cost."""
    me = month_ends(days)

    def plan(t, sleeves, state, E):
        if not sleeves or t in me:
            state["basket_of"] = {"b": q}
            return {"b": equal(composition(BASKETS[q], days[t], first), E)}, q
        return None, None
    return simulate(f"basket {q}", days, s, rets, first, plan, bps=0.0).equity


# ============================================================================================== metrics
def daily_rates(days: list[date], dtb3: list[tuple[date, float]]) -> list[float | None]:
    """DTB3/100 from the latest value dated on or before the previous trading day (None before any)."""
    vals = sorted((d, v) for d, v in dtb3 if v == v)
    out, j, cur = [None], 0, None
    for t in range(1, len(days)):
        while j < len(vals) and vals[j][0] <= days[t - 1]:
            cur = vals[j][1] / 100.0
            j += 1
        out.append(cur)
    return out


def metrics(res: Result, rates_window: list[float], sub_periods) -> dict:
    from research.core_candidates import engine as cce
    dates, eq = res.dates, res.equity
    r = eq[1:] / eq[:-1] - 1.0
    rf = np.array(rates_window[1:]) / PPY
    span = (dates[-1] - dates[0]).days
    ex = r - rf
    cal = cce.calendar_years(dates, eq)
    return {"name": res.name, "cagr": float((eq[-1] / eq[0]) ** (365.25 / span) - 1.0), "total_return": float(eq[-1] / eq[0] - 1.0),
            "vol": float(np.std(r, ddof=1) * math.sqrt(PPY)), "sharpe": float(ex.mean() / np.std(ex, ddof=1) * math.sqrt(PPY)),
            **cce.max_drawdown(dates, eq), "worst_year": min(cal.items(), key=lambda kv: kv[1]), "calendar": cal,
            "switches": res.switches, "switches_per_year": res.switches / (span / 365.25), "costs": res.costs, "end_equity": float(eq[-1]),
            "sub_periods": [period_return_or_none(dates, eq, a, b) for a, b in sub_periods]}


def period_return_or_none(dates, eq, a, b):
    """A sub-period with no trading day inside the window has no return (None); it can never count as beaten (Appendix B)."""
    from research.core_candidates import engine as cce
    if not any(a <= d <= b for d in dates):
        return None
    return cce.period_return(dates, eq, a, b)


def verdicts(tilt: dict, qall: dict, spy: dict) -> dict:
    def beats(a, b):
        return a is not None and b is not None and a > b
    w_all = [beats(a, b) for a, b in zip(tilt["sub_periods"], qall["sub_periods"])]
    w_spy = [beats(a, b) for a, b in zip(tilt["sub_periods"], spy["sub_periods"])]
    v1 = tilt["cagr"] > qall["cagr"] and sum(w_all) >= 2
    v2 = tilt["cagr"] > spy["cagr"] and tilt["mdd"] <= spy["mdd"] and sum(w_spy) >= 2
    return {"calls": "CALLS ADD VALUE" if v1 else "CALLS ADD NO VALUE", "calls_detail": {"cagr": tilt["cagr"] > qall["cagr"], "sub_wins": w_all},
            "spy": "BEATS SPY" if v2 else "DOES NOT BEAT SPY",
            "spy_detail": {"cagr": tilt["cagr"] > spy["cagr"], "mdd": tilt["mdd"] <= spy["mdd"], "sub_wins": w_spy}}


def hit_table(held: list, basket_eq: np.ndarray, q: str) -> dict:
    r = basket_eq[1:] / basket_eq[:-1] - 1.0
    on = np.array([h == q for h in held[1:]])
    def stats(mask):
        x = r[mask]
        return {"days": int(mask.sum()), "ann_mean": float(x.mean() * PPY) if len(x) else None, "cum": float(np.prod(1 + x) - 1) if len(x) else None}
    return {"quadrant": q, "active": stats(on), "inactive": stats(~on)}
