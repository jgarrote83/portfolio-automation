"""Quadrant-tilt backtest engine (research/quadrant_backtest/engine.py). Synthetic inputs only; no network."""
import os
import sys
from datetime import date, timedelta

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from research.quadrant_backtest import engine as E  # noqa: E402

ALL = sorted({m for b in E.BASKETS.values() for m in b} | {p for p in E.PROXY.values() if p})


def weekdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def flat_world(days, first=None, returns=None):
    first = first or {s: date(2000, 1, 1) for s in ALL}
    rets = {s: [0.0] * len(days) for s in ALL}
    for s, r in (returns or {}).items():
        rets[s] = r
    return rets, first


def rows(days, quads):
    return [{"date": d.isoformat(), "active_quadrant": q} for d, q in zip(days, quads)]


# --- signal -------------------------------------------------------------------------------------------------------------

def test_indeterminate_day_keeps_the_previous_quadrant_and_starts_as_qall():
    days = weekdays(date(2016, 6, 1), 5)
    eff = E.effective_quadrants(rows(days, ["", "Q1", "", "Q3", ""]))
    assert [eff[d] for d in days] == ["Q-ALL", "Q1", "Q1", "Q3", "Q3"]


def test_window_start_needs_twenty_consecutive_determinate_days_and_not_before_the_floor():
    days = weekdays(date(2011, 6, 1), 80)
    q = ["Q2"] * 80
    q[30] = ""                                                         # breaks the first run after the floor
    start = E.window_start(rows(days, q))
    assert start == days[31] and start >= date(2011, 7, 1)
    with pytest.raises(ValueError):
        E.window_start(rows(days, [""] * 80))


def test_in_force_uses_the_last_weekday_at_or_before_each_trading_day():
    wk = weekdays(date(2016, 6, 1), 6)
    eff = {d: q for d, q in zip(wk, ["Q1", "Q1", "Q2", "Q2", "Q4", "Q4"])}
    trading = [wk[0], wk[1], wk[3], wk[5]]                            # wk[2] and wk[4] are holidays
    assert E.in_force(trading, eff) == ["Q1", "Q1", "Q2", "Q4"]       # a holiday's change is seen on the next trading day


# --- trading ------------------------------------------------------------------------------------------------------------

def test_quadrant_change_trades_at_the_next_close_not_the_same_close():
    days = weekdays(date(2017, 3, 1), 6)
    force = ["Q1", "Q1", "Q1", "Q4", "Q4", "Q4"]                       # the change is visible at the close of day 3
    rets, first = flat_world(days, returns={"SPY": [0, 0, 0, 0, 0.10, 0], "TLT": [0, 0, 0, 0, 0.10, 0]})
    res = E.simulate("t", days, 1, rets, first, E.plan_qtilt(days, force, first), bps=0.0)
    assert res.held == ["Q1", "Q1", "Q1", "Q1", "Q4"]                  # days 1-5: day 4's return is still earned in Q1; Q4 from day 5
    ev = [e for e in res.events if e["kind"] == "rebalance"]
    assert [(e["date"], e["label"]) for e in ev] == [(days[1], "Q1"), (days[4], "Q4")]
    assert res.switches == 1
    # on day 4 the book held Q1 (SPY one third): +10%/3; TLT's +10% on day 4 is not earned
    assert res.equity[3] == pytest.approx(E.START_EQUITY * (1 + 0.10 / 3))


def test_qall_rebalances_at_month_ends_to_a_quarter_per_basket():
    days = weekdays(date(2017, 1, 25), 10)                             # crosses the January month-end
    rets, first = flat_world(days, returns={"SPY": [0.0] + [0.02] * 9})
    res = E.simulate("a", days, 0, rets, first, E.plan_qall(days, first), bps=0.0)
    me = [e["date"] for e in res.events if e["kind"] == "rebalance"]
    assert me == [days[0], date(2017, 1, 31)]
    assert res.switches == 0


def test_qall_holds_a_shared_name_once_at_the_sum_of_its_shares():
    days = weekdays(date(2017, 3, 1), 2)
    rets, first = flat_world(days)
    tgt = E._qall_target(days[0], first, 100.0)
    tot = E._symbols_total(tgt)
    assert tot["GLD"] == pytest.approx(25 / 8 + 25 / 7)               # Q3 has 8 names, Q4 has 7
    assert sum(tot.values()) == pytest.approx(100.0)


# --- proxies ------------------------------------------------------------------------------------------------------------

def test_proxy_until_the_first_date_then_the_member_and_a_dropped_member_appears():
    first = {s: date(2000, 1, 1) for s in ALL}
    first["COWZ"], first["KMLM"] = date(2017, 3, 3), date(2017, 3, 6)
    assert E.holding_of("COWZ", date(2017, 3, 2), first) == "IWD" and E.holding_of("COWZ", date(2017, 3, 3), first) == "COWZ"
    assert E.holding_of("KMLM", date(2017, 3, 3), first) is None and E.holding_of("KMLM", date(2017, 3, 6), first) == "KMLM"
    assert len(E.composition(E.BASKETS["Q3"], date(2017, 3, 3), first)) == 7


def test_composition_event_resets_the_held_basket_on_the_members_first_date():
    days = weekdays(date(2017, 3, 1), 6)
    first = {s: date(2000, 1, 1) for s in ALL}
    first["COWZ"] = days[3]
    rets, _ = flat_world(days, first)
    res = E.simulate("t", days, 0, rets, first, E.plan_qtilt([days[0]] + days, ["Q2"] * 7, first), bps=2.0)
    comp = [e for e in res.events if e["kind"] == "composition"]
    assert [e["date"] for e in comp] == [days[3]]
    assert res.costs > 0                                               # selling IWD and buying COWZ is a trade


def test_costs_are_charged_on_the_net_trade_and_the_opening_buy_only():
    days = weekdays(date(2017, 3, 1), 3)
    rets, first = flat_world(days)
    res = E.simulate("t", days, 0, rets, first, E.plan_qtilt([days[0]] + days, ["Q1"] * 4, first), bps=2.0)
    assert res.costs == pytest.approx(E.START_EQUITY * 0.0002)          # one side only for the opening purchase
    assert res.equity[-1] == pytest.approx(E.START_EQUITY * (1 - 0.0002))


# --- refusal and determinism --------------------------------------------------------------------------------------------

def test_2026_is_refused():
    days = weekdays(date(2025, 12, 30), 3)                             # runs into 2026-01-01
    rets, first = flat_world(days)
    with pytest.raises(E.HoldoutRefused):
        E.simulate("t", days, 0, rets, first, E.plan_qall(days, first))


def test_deterministic():
    days = weekdays(date(2018, 1, 2), 60)
    rng = np.random.default_rng(1)
    rets = {s: list(rng.normal(0, 0.01, len(days))) for s in ALL}
    first = {s: date(2000, 1, 1) for s in ALL}
    force = (["Q1"] * 20 + ["Q3"] * 20 + ["Q2"] * 20)
    a = E.simulate("t", days, 1, rets, first, E.plan_qtilt(days, force, first))
    b = E.simulate("t", days, 1, rets, first, E.plan_qtilt(days, force, first))
    assert np.array_equal(a.equity, b.equity) and a.held == b.held


def test_verdicts():
    tilt = {"cagr": 0.12, "mdd": 0.30, "sub_periods": [0.1, 0.5, 0.2]}
    qall = {"cagr": 0.10, "mdd": 0.25, "sub_periods": [0.2, 0.4, 0.1]}
    spy = {"cagr": 0.13, "mdd": 0.34, "sub_periods": [0.0, 0.6, 0.3]}
    v = E.verdicts(tilt, qall, spy)
    assert v["calls"] == "CALLS ADD VALUE" and v["spy"] == "DOES NOT BEAT SPY"


def test_an_empty_sub_period_is_none_and_never_counts_as_beaten():
    dates = [date(2016, 6, 1), date(2016, 6, 2), date(2017, 1, 3)]
    eq = np.array([100.0, 101.0, 102.0])
    assert E.period_return_or_none(dates, eq, date(2011, 7, 1), date(2015, 12, 31)) is None
    tilt = {"cagr": 0.12, "mdd": 0.30, "sub_periods": [None, 0.5, 0.2]}
    other = {"cagr": 0.10, "mdd": 0.40, "sub_periods": [None, 0.4, 0.1]}
    v = E.verdicts(tilt, other, other)
    assert v["calls_detail"]["sub_wins"] == [False, True, True] and v["calls"] == "CALLS ADD VALUE"
