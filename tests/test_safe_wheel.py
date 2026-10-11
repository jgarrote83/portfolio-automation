"""Safe-wheel model: pricing, calendar, the cycle simulation, metrics, the gate, the kill switch and the PUT calibration, on
synthetic markets with known answers (no network, no data layer). The pre-registration's machine-readable block is pinned to the
code here, so the document and `WheelConfig()` cannot drift apart unnoticed.

Synthetic calendars are Monday-Friday without holidays unless a test removes days. The first in-window cycle is the February 2005
expiry: third Friday 2005-02-18, entry 2005-01-14 (35 days before), scheduled time exit 2005-02-11 (7 days before).
"""
import json
import math
import os
import random
import re
import statistics
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from backtest import provenance  # noqa: E402
from backtest import safe_wheel as sw  # noqa: E402
from backtest import safe_wheel_report as rep  # noqa: E402
from backtest import safe_wheel_run as R  # noqa: E402
from backtest import safe_wheel_verify as V  # noqa: E402
from backtest import wheel_data as wd  # noqa: E402
from backtest.data.bars import HoldoutError  # noqa: E402

PREREG = os.path.join(os.path.dirname(__file__), "..", "docs", "specs", "Safe_Wheel_Model_Preregistration.md")
SMALL = sw.WheelConfig().replace(window_start="2005-01-03", window_end="2006-12-29")
SHORT = sw.WheelConfig().replace(window_start="2005-01-03", window_end="2005-02-28")     # for calendars that stop at 2005-03-31
GOOD_FRIDAYS = {date(2008, 3, 21), date(2014, 4, 18), date(2019, 4, 19), date(2022, 4, 15), date(2025, 4, 18)}


def weekdays(start, end, holidays=()):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in holidays:
            out.append(d)
        d += timedelta(days=1)
    return out


def _fn(x):
    return x if callable(x) else (lambda d: x)


def make_inputs(days, close=100.0, vix=20.0, rate=2.0, q=0.015, symbol="SPY", vol_name="VIXCLS"):
    """WheelInputs built directly. `close`, `vix`, `rate` (percent) and `q` are constants or callables of the date."""
    c, v, r, y = _fn(close), _fn(vix), _fn(rate), _fn(q)
    return wd.WheelInputs(symbol, vol_name, tuple(days), {d: c(d) for d in days}, {d: v(d) for d in days}, {d: r(d) for d in days},
                          {d: y(d) for d in days}, {"vol": [], "rate": []}, (), ())


def first_position(run):
    assert run.positions, "expected at least one position"
    return run.positions[0]


DAYS = weekdays(date(2004, 1, 1), date(2007, 3, 30))


# ===================================================================================== pricing: known values
def test_black_scholes_matches_hull_textbook_values():
    # Hull: S=42 K=40 r=10% sigma=20% T=0.5 -> call 4.76, put 0.81; and S=100 K=100 r=5% sigma=20% T=1 -> call 10.4506, put 5.5735
    assert sw.call_price(42, 40, 0.5, 0.10, 0.0, 0.20) == pytest.approx(4.76, abs=0.005)
    assert sw.put_price(42, 40, 0.5, 0.10, 0.0, 0.20) == pytest.approx(0.81, abs=0.005)
    assert sw.call_price(100, 100, 1, 0.05, 0.0, 0.20) == pytest.approx(10.4506, abs=5e-5)
    assert sw.put_price(100, 100, 1, 0.05, 0.0, 0.20) == pytest.approx(5.5735, abs=5e-5)
    assert sw.put_delta(100, 100, 1, 0.05, 0.0, 0.20) == pytest.approx(-0.36317, abs=5e-6)    # N(0.35) - 1


@pytest.mark.parametrize("s,k,t,r,q,sigma", [(100, 95, 0.5, 0.03, 0.02, 0.25), (450, 400, 35 / 365, 0.045, 0.013, 0.31),
                                             (80, 90, 7 / 365, -0.0005, 0.0, 0.5), (300, 300, 1.0, 0.0, 0.05, 0.1)])
def test_put_call_parity_holds_with_a_dividend_yield(s, k, t, r, q, sigma):
    lhs = sw.call_price(s, k, t, r, q, sigma) - sw.put_price(s, k, t, r, q, sigma)
    assert lhs == pytest.approx(s * math.exp(-q * t) - k * math.exp(-r * t), abs=1e-9)


def test_put_delta_is_the_derivative_of_the_put_price_with_respect_to_the_spot_including_the_dividend_term():
    s, k, t, r, q, sigma, h = 100.0, 92.0, 35 / 365, 0.02, 0.03, 0.22, 1e-4
    numeric = (sw.put_price(s + h, k, t, r, q, sigma) - sw.put_price(s - h, k, t, r, q, sigma)) / (2 * h)
    assert sw.put_delta(s, k, t, r, q, sigma) == pytest.approx(numeric, abs=1e-6)
    assert sw.put_delta(s, k, t, r, 0.0, sigma) != pytest.approx(sw.put_delta(s, k, t, r, q, sigma), abs=1e-4)   # q matters


def test_pricing_refuses_degenerate_inputs():
    for bad in ((100, 100, 0.0, 0.01, 0.0, 0.2), (100, 100, 0.1, 0.01, 0.0, 0.0), (0, 100, 0.1, 0.01, 0.0, 0.2), (100, 0, 0.1, 0.01, 0.0, 0.2)):
        with pytest.raises(ValueError):
            sw.put_price(*bad)


# ============================================================================================ the skew formula
def test_skew_formula_adds_half_a_vol_point_per_one_percent_out_of_the_money():
    assert sw.iv(20.0, 100.0, 100.0, 0.5) == pytest.approx(0.20)
    assert sw.iv(20.0, 100.0, 105.0, 0.5) == pytest.approx(0.20)                   # in the money: nothing added
    assert sw.iv(20.0, 100.0, 90.0, 0.5) == pytest.approx(0.20 + 0.5 * math.log(100 / 90))
    # the pre-registration's worked example: 10% out of the money (ln = 0.10536) adds 0.5 x 10.536 = 5.27 vol points
    strike = 100.0 / math.exp(0.10536)
    assert (sw.iv(20.0, 100.0, strike, 0.5) - 0.20) * 100 == pytest.approx(5.268, abs=0.001)
    # skew 1.0 doubles it, skew 0 removes it
    assert sw.iv(20.0, 100.0, 90.0, 1.0) - 0.20 == pytest.approx(2 * (sw.iv(20.0, 100.0, 90.0, 0.5) - 0.20))
    assert sw.iv(20.0, 100.0, 90.0, 0.0) == pytest.approx(0.20)


# ====================================================================================== strike search and rounding
def test_short_strike_is_the_whole_dollar_strike_with_delta_closest_to_the_target():
    s, t, r, q, v = 100.0, 35 / 365, 0.02, 0.015, 20.0
    k = sw.pick_short_strike(s, t, r, q, v, 0.5, -0.20)
    dist = {j: abs(sw.put_delta(s, float(j), t, r, q, sw.iv(v, s, float(j), 0.5)) + 0.20) for j in range(1, 101)}
    assert isinstance(k, int) and 1 <= k <= 100
    assert dist[k] == min(dist.values())
    assert dist[k] < 0.05
    # a stricter target moves the strike down, a looser one up; a steeper skew keeps the strike at or below the flat-skew one
    assert sw.pick_short_strike(s, t, r, q, v, 0.5, -0.10) < k < sw.pick_short_strike(s, t, r, q, v, 0.5, -0.30)
    assert sw.pick_short_strike(s, t, r, q, v, 1.0, -0.20) <= sw.pick_short_strike(s, t, r, q, v, 0.0, -0.20)


def test_an_exact_tie_between_two_strikes_goes_to_the_lower_strike(monkeypatch):
    table = {50: -0.125, 51: -0.375}                        # both exactly 0.125 from a target of -0.25
    monkeypatch.setattr(sw, "put_delta", lambda s, k, t, r, q, sigma: table.get(int(k), -0.9))
    assert sw.pick_short_strike(51.5, 0.1, 0.01, 0.0, 20.0, 0.5, -0.25) == 50


def test_round_half_up_and_the_width_rule():
    assert [sw.round_half_up(x) for x in (0.4, 0.5, 1.5, 2.49, 2.5, 3.5)] == [0, 1, 2, 2, 3, 4]
    days = weekdays(date(2004, 1, 1), date(2005, 3, 31))
    cyc = [c for c in sw.build_cycles(days, SHORT) if c.status == "in_window"][0]
    def width_at(spot):
        pos, why = sw.open_position(SHORT, make_inputs(days, close=spot, vix=40.0), cyc.entry_day, cyc)
        assert why is None
        return pos.width
    assert width_at(125.0) == 3            # 2% of 125 = 2.5 rounds half UP to 3
    assert width_at(124.9) == 2            # 2.498 -> 2
    assert width_at(60.0) == 1             # 1.2 -> 1
    assert width_at(20.0) == 1             # 0.4 -> 0, raised to the $1 minimum
    assert width_at(600.0) == 12


def test_the_long_strike_is_the_short_strike_minus_the_width_and_never_below_one_dollar():
    days = weekdays(date(2004, 1, 1), date(2005, 3, 31))
    cyc = [c for c in sw.build_cycles(days, SHORT) if c.status == "in_window"][0]
    pos, _ = sw.open_position(SHORT, make_inputs(days, close=100.0), cyc.entry_day, cyc)
    assert pos.k_long == pos.k_short - pos.width
    # a spot of $2 puts the short strike at $1 and the long strike at $0: the cycle is skipped and counted
    pos2, why = sw.open_position(SHORT, make_inputs(days, close=2.0), cyc.entry_day, cyc)
    assert pos2 is None and why in ("long_strike_below_1", "no_positive_credit", "zero_contracts")


# ======================================================================================== credit, costs and sizing
def test_entry_credit_is_the_model_credit_minus_both_haircuts_and_stays_below_the_width():
    for spot in (60.0, 100.0, 250.0, 600.0):
        for vix in (10.0, 20.0, 40.0, 80.0):
            for rate in (-0.05, 2.0, 5.0):
                days = weekdays(date(2004, 1, 1), date(2005, 3, 31))
                cyc = [c for c in sw.build_cycles(days, SHORT) if c.status == "in_window"][0]
                pos, why = sw.open_position(SHORT, make_inputs(days, close=spot, vix=vix, rate=rate), cyc.entry_day, cyc)
                if pos is None:
                    assert why in ("no_positive_credit", "zero_contracts")
                    continue
                assert 0 < pos.mid_credit < pos.width
                assert pos.credit == pytest.approx(pos.mid_credit - 0.06, abs=1e-12)
                assert pos.credit < pos.width
                # independent recomputation of the two legs
                t = (pos.expiry - pos.entry_day).days / 365
                def leg(k):
                    sig = vix / 100 + 0.5 * max(0.0, math.log(spot / k))
                    return sw.put_price(spot, k, t, rate / 100, 0.015, sig)
                assert pos.mid_credit == pytest.approx(leg(pos.k_short) - leg(pos.k_long), abs=1e-9)


def test_contract_sizing_floors_the_risk_budget_over_the_per_contract_max_loss():
    days = weekdays(date(2004, 1, 1), date(2005, 3, 31))
    cyc = [c for c in sw.build_cycles(days, SHORT) if c.status == "in_window"][0]
    inp = make_inputs(days, close=100.0)
    base, _ = sw.open_position(SHORT, inp, cyc.entry_day, cyc)
    loss = (base.width - base.credit) * 100
    assert base.contracts == math.floor(2500.0 / loss)
    def n_for(budget):
        cfg = SHORT.replace(risk_budget_pct_of_sleeve=budget / SHORT.sleeve_capital_usd * 100)
        pos, why = sw.open_position(cfg, inp, cyc.entry_day, cyc)
        return None if pos is None else pos.contracts, why
    assert n_for(3 * loss + 0.01)[0] == 3
    assert n_for(3 * loss - 0.01)[0] == 2                # just below three contracts' worth of risk
    assert n_for(loss - 0.01) == (None, "zero_contracts")  # not even one contract fits: skipped and counted


def test_zero_contract_cycles_are_skipped_and_counted_in_the_run():
    cfg = SMALL.replace(risk_budget_pct_of_sleeve=0.1)   # $25 of risk: never a whole contract
    run = sw.simulate(cfg, make_inputs(DAYS))
    assert not run.positions
    assert {s["reason"] for s in run.skips} == {"zero_contracts"}
    assert len(run.skips) == sum(1 for c in run.cycles if c.status == "in_window")
    assert run.daily[-1][1] == cfg.sleeve_capital_usd


def test_net_pnl_follows_the_cost_arithmetic_and_the_equity_curve_reconciles_with_the_positions():
    rng = random.Random(3)
    s, closes = 100.0, {}
    for d in DAYS:
        s *= math.exp(rng.gauss(0.0002, 0.012))
        closes[d] = s
    run = sw.simulate(SMALL, make_inputs(DAYS, close=lambda d: closes[d], vix=lambda d: 14 + 6 * math.sin(d.toordinal() / 40.0)))
    assert len(run.positions) >= 15
    for p in run.positions:
        debit = p.mark_exit + 0.06
        assert p.debit_exit == pytest.approx(debit, abs=1e-12)
        expect = (p.credit - debit) * 100 * p.contracts - 4 * 0.65 * p.contracts
        assert p.net_pnl == pytest.approx(expect, abs=1e-9)
        assert p.fees == pytest.approx(4 * 0.65 * p.contracts)
    assert run.daily[-1][1] - 25000.0 == pytest.approx(sum(p.net_pnl for p in run.positions), abs=1e-6)
    assert sum(p for _, _, p in run.daily) == pytest.approx(sum(p.net_pnl for p in run.positions), abs=1e-6)


def test_with_no_haircut_and_no_fees_the_pnl_is_the_pure_model_pnl():
    cfg = SMALL.replace(haircut_per_share_per_leg=0.0, fee_per_contract=0.0)
    run = sw.simulate(cfg, make_inputs(DAYS, vix=lambda d: 15 + 5 * math.sin(d.toordinal() / 31.0)))
    for p in run.positions:
        assert p.credit == pytest.approx(p.mid_credit, abs=1e-12)
        assert p.net_pnl == pytest.approx((p.mid_credit - p.mark_exit) * 100 * p.contracts, abs=1e-9)


def test_cost_haircut_sensitivities_change_only_the_haircut():
    base = sw.simulate(SMALL, make_inputs(DAYS))
    cheap = sw.simulate(SMALL.replace(haircut_per_share_per_leg=0.01), make_inputs(DAYS))
    dear = sw.simulate(SMALL.replace(haircut_per_share_per_leg=0.06), make_inputs(DAYS))
    p0, pc, pd_ = first_position(base), first_position(cheap), first_position(dear)
    assert (p0.k_short, p0.k_long, p0.entry_day) == (pc.k_short, pc.k_long, pc.entry_day) == (pd_.k_short, pd_.k_long, pd_.entry_day)
    assert pc.credit == pytest.approx(pc.mid_credit - 0.02) and pd_.credit == pytest.approx(pd_.mid_credit - 0.12)
    assert cheap.daily[-1][1] > base.daily[-1][1] > dear.daily[-1][1]


# ===================================================================================== calendar: expiries, entry, exits
def test_third_fridays_and_holiday_moves():
    assert sw.third_friday(2008, 3) == date(2008, 3, 21) and sw.third_friday(2025, 4) == date(2025, 4, 18)
    assert sw.third_friday(2005, 1) == date(2005, 1, 21) and sw.third_friday(2026, 6) == date(2026, 6, 19)
    days = weekdays(date(2004, 1, 1), date(2025, 12, 31), holidays=GOOD_FRIDAYS)
    exp = sw.monthly_expiries(days)
    assert len(exp) == 252 and exp[0].expiry == date(2005, 1, 21) and exp[-1].expiry == date(2025, 12, 19)
    assert {e.third_friday for e in exp if e.moved} == GOOD_FRIDAYS
    assert {e.expiry for e in exp if e.moved} == {date(2008, 3, 20), date(2014, 4, 17), date(2019, 4, 18), date(2022, 4, 14), date(2025, 4, 17)}
    assert all(e.expiry.weekday() == 4 for e in exp if not e.moved)
    chk = sw.calendar_check(days, sw.WheelConfig())
    assert chk["moved_equals_good_friday_set"] is True and chk["not_third_friday"] == []
    assert sw.calendar_check(weekdays(date(2004, 1, 1), date(2025, 12, 31)), sw.WheelConfig())["moved_equals_good_friday_set"] is False


def test_entry_day_is_the_closest_trading_day_to_35_days_before_and_ties_go_to_the_earlier_day():
    days = weekdays(date(2004, 1, 1), date(2005, 3, 31))
    assert sw.entry_day_for(date(2005, 2, 18), days) == date(2005, 1, 14)               # exactly 35 days before
    holiday = weekdays(date(2004, 1, 1), date(2005, 3, 31), holidays={date(2005, 1, 14)})
    assert sw.entry_day_for(date(2005, 2, 18), holiday) == date(2005, 1, 13)            # Thursday (1 day) beats Monday (3 days)
    pair = [date(2005, 1, 10), date(2005, 1, 14)]                                        # the target 01-12 is 2 days from each
    assert sw.entry_day_for(date(2005, 2, 16), pair) == date(2005, 1, 10)
    one_day_off = [date(2005, 1, 11), date(2005, 1, 13)]
    assert sw.entry_day_for(date(2005, 2, 16), one_day_off) == date(2005, 1, 11)


def test_time_exit_is_the_latest_trading_day_on_or_before_seven_days_before_expiry():
    days = weekdays(date(2004, 1, 1), date(2005, 3, 31))
    assert sw.time_exit_day(date(2005, 2, 18), days) == date(2005, 2, 11)
    holiday = weekdays(date(2004, 1, 1), date(2005, 3, 31), holidays={date(2005, 2, 11)})
    assert sw.time_exit_day(date(2005, 2, 18), holiday) == date(2005, 2, 10)
    moved = weekdays(date(2004, 1, 1), date(2014, 6, 30), holidays={date(2014, 4, 18)})
    e = [x for x in sw.monthly_expiries(moved, (2014, 1), (2014, 6)) if x.moved][0]
    assert e.expiry == date(2014, 4, 17) and sw.time_exit_day(e.expiry, moved) == date(2014, 4, 10)


def test_cycles_outside_the_window_are_classified_not_entered():
    days = weekdays(date(2004, 1, 1), date(2025, 12, 31))
    cycles = sw.build_cycles(days, sw.WheelConfig())
    assert len(cycles) == 252
    assert cycles[0].expiry == date(2005, 1, 21) and cycles[0].status == "before_window" and cycles[0].entry_day == date(2004, 12, 17)
    inside = [c for c in cycles if c.status == "in_window"]
    assert len(inside) == 251 and inside[0].expiry == date(2005, 2, 18) and inside[-1].expiry == date(2025, 12, 19)
    assert inside[-1].exit_day == date(2025, 12, 12)
    # a cycle whose scheduled exit falls after the window end is not entered (the January 2007 expiry exits on 2007-01-12)
    c = {x.expiry: x for x in sw.build_cycles(DAYS, SMALL.replace(window_end="2006-12-05"))}
    assert c[date(2006, 12, 15)].status == "after_window" and c[date(2006, 11, 17)].status == "in_window"      # exits 12-08 and 11-10
    # an expiry month beyond the last day with data is an error, never a 'holiday' that slides back to the last day
    with pytest.raises(ValueError, match="after the last trading day"):
        sw.monthly_expiries(weekdays(date(2004, 1, 1), date(2005, 2, 15)), (2005, 2), (2005, 3))


# ================================================================================================ exits
def _entry_and_exit_days():
    cyc = [c for c in sw.build_cycles(DAYS, SMALL) if c.status == "in_window"][0]
    return cyc, cyc.entry_day, cyc.exit_day


def test_a_rally_after_entry_triggers_the_profit_take_on_the_first_day_after_entry():
    cyc, entry, sched = _entry_and_exit_days()
    assert (entry, sched) == (date(2005, 1, 14), date(2005, 2, 11))
    rally = sw.simulate(SMALL, make_inputs(DAYS, close=lambda d: 100.0 if d <= entry else 106.0))
    p = first_position(rally)
    assert p.entry_day == entry and p.exit_day == date(2005, 1, 17) and p.exit_reason == "profit_take"
    assert p.mark_exit <= 0.5 * p.credit
    # the same market with profit-take switched off holds to the seven-day exit
    held = sw.simulate(SMALL.replace(profit_take_enabled=False), make_inputs(DAYS, close=lambda d: 100.0 if d <= entry else 106.0))
    h = first_position(held)
    assert h.exit_day == sched and h.exit_reason == "time_exit"


def test_a_volatility_spike_keeps_the_spread_marked_high_and_the_position_runs_to_the_seven_day_exit():
    cyc, entry, sched = _entry_and_exit_days()
    spike = sw.simulate(SMALL, make_inputs(DAYS, vix=lambda d: 20.0 if d <= entry else 45.0))
    p = first_position(spike)
    assert p.exit_day == sched == date(2005, 2, 11) and p.exit_reason == "time_exit"
    assert p.net_pnl < 0
    assert p.worst_unreal <= (p.credit - p.mark_exit) * 100 * p.contracts + 1e-9


def test_the_time_exit_moves_to_the_prior_trading_day_when_the_seventh_day_is_a_holiday():
    days = weekdays(date(2004, 1, 1), date(2007, 3, 30), holidays={date(2005, 2, 11)})
    spike = sw.simulate(SMALL, make_inputs(days, vix=lambda d: 20.0 if d <= date(2005, 1, 14) else 45.0))
    p = first_position(spike)
    assert p.sched_exit_day == date(2005, 2, 10) and p.exit_day == date(2005, 2, 10)


def test_profit_take_boundary_is_at_or_below_half_the_credit():
    assert sw.profit_take_hit(0.5, 1.0, 0.5) is True            # exactly half: take
    assert sw.profit_take_hit(0.5000001, 1.0, 0.5) is False
    assert sw.profit_take_hit(0.0, 1.0, 0.5) is True
    cyc, entry, sched = _entry_and_exit_days()
    inp = make_inputs(DAYS, vix=lambda d: 20.0 if d <= entry else 45.0)
    c = sw.open_position(SMALL, inp, entry, cyc)[0].credit
    from unittest import mock
    with mock.patch.object(sw, "spread_mark", lambda *a, **k: 0.5 * c):
        p = first_position(sw.simulate(SMALL, inp))
    assert p.exit_reason == "profit_take" and p.exit_day == date(2005, 1, 17)
    with mock.patch.object(sw, "spread_mark", lambda *a, **k: 0.5 * c + 1e-9):
        p = first_position(sw.simulate(SMALL, inp))
    assert p.exit_reason == "time_exit" and p.exit_day == sched


def test_no_exit_is_taken_on_the_entry_day_itself():
    inp = make_inputs(DAYS, vix=lambda d: 20.0)
    from unittest import mock
    with mock.patch.object(sw, "spread_mark", lambda *a, **k: 0.0):       # a worthless spread from the first mark
        p = first_position(sw.simulate(SMALL, inp))
    assert p.exit_day > p.entry_day


# ============================================================================== one position at a time and ordering
def test_an_exit_on_an_entry_day_is_processed_before_the_entry_so_the_next_cycle_is_not_skipped():
    # consecutive expiries 28 days apart: the next entry day (expiry - 35) is the old position's time-exit day (expiry - 7)
    run = sw.simulate(SMALL.replace(profit_take_enabled=False), make_inputs(DAYS, vix=lambda d: 20.0))
    by_entry = {p.entry_day: p for p in run.positions}
    assert date(2005, 3, 11) in by_entry                                       # the April 2005 cycle
    prev = [p for p in run.positions if p.expiry == date(2005, 3, 18)][0]
    assert prev.exit_day == date(2005, 3, 11) and by_entry[date(2005, 3, 11)].expiry == date(2005, 4, 15)
    assert not [s for s in run.skips if s["reason"] == "position_still_open"]
    assert len(run.positions) == sum(1 for c in run.cycles if c.status == "in_window")


def test_a_position_still_open_at_an_entry_day_blocks_that_cycle_and_is_counted():
    # exiting only one day before expiry leaves the old position open on the 28-day-gap entry days (expiry - 7)
    cfg = SMALL.replace(exit_calendar_days_before_expiry=1, profit_take_enabled=False)
    run = sw.simulate(cfg, make_inputs(DAYS, vix=lambda d: 20.0))
    open_skips = [s for s in run.skips if s["reason"] == "position_still_open"]
    # the January 14 position is open until 02-17, so the cycle entered on 02-11 (expiry 03-18) is skipped; the next entry (03-11) is free
    assert date(2005, 2, 11) in {s["entry_day"] for s in open_skips} and date(2005, 3, 11) not in {s["entry_day"] for s in open_skips}
    assert all(p.exit_day is not None for p in run.positions)
    spans = sorted((p.entry_day, p.exit_day) for p in run.positions)
    assert all(b[0] >= a[1] for a, b in zip(spans, spans[1:]))                  # never two positions at once


# ============================================================================================ the kill switch
def test_kill_switch_threshold_is_at_or_below_eighty_percent_of_the_peak():
    assert sw.kill_switch_hit(20000.0, 25000.0, 20.0) is True
    assert sw.kill_switch_hit(20000.01, 25000.0, 20.0) is False
    assert sw.kill_switch_hit(19999.99, 25000.0, 20.0) is True


def test_kill_switch_activates_pauses_three_months_resumes_on_the_resume_date_and_resets_the_peak():
    ks = sw.KillSwitch(True, 20.0, 3, 25000.0)
    ks.update(date(2005, 1, 3), 26000.0)
    ks.update(date(2005, 1, 4), 20800.0)                  # exactly 80% of 26,000: activates
    assert ks.paused and ks.events[0]["activated"] == date(2005, 1, 4) and ks.events[0]["peak"] == 26000.0
    assert ks.paused_until == date(2005, 4, 4)
    ks.update(date(2005, 2, 1), 1000.0)                   # not evaluated during the pause, and the peak does not move
    ks.update(date(2005, 4, 1), 50000.0)
    assert ks.paused and len(ks.events) == 1 and ks.peak == 26000.0
    ks.update(date(2005, 4, 4), 20800.0)                  # the resume date itself: entries allowed again, peak reset to this equity
    assert not ks.paused and ks.peak == 20800.0 and ks.events[0]["resumed_on"] == date(2005, 4, 4)
    ks.update(date(2005, 4, 5), 20800.0)                  # still 20% under the OLD peak, but not under the new one
    assert not ks.paused and len(ks.events) == 1
    ks.update(date(2005, 4, 6), 16640.0)                  # a fresh 20% fall below the new peak
    assert ks.paused and len(ks.events) == 2


def test_kill_switch_resumes_on_the_first_trading_day_after_a_weekend_resume_date():
    ks = sw.KillSwitch(True, 20.0, 3, 25000.0)
    ks.update(date(2005, 1, 17), 20000.0)                 # resume date 2005-04-17 is a Sunday
    assert ks.paused_until == date(2005, 4, 17)
    ks.update(date(2005, 4, 15), 21000.0)
    assert ks.paused
    ks.update(date(2005, 4, 18), 21000.0)
    assert not ks.paused and ks.peak == 21000.0


def test_kill_switch_month_arithmetic_clips_to_the_end_of_the_month():
    assert sw.add_months(date(2005, 11, 30), 3) == date(2006, 2, 28)
    assert sw.add_months(date(2007, 11, 30), 3) == date(2008, 2, 29)
    assert sw.add_months(date(2005, 12, 31), 3) == date(2006, 3, 31)
    assert sw.add_months(date(2005, 1, 31), 1) == date(2005, 2, 28)


def test_a_disabled_kill_switch_never_activates():
    ks = sw.KillSwitch(False, 20.0, 3, 25000.0)
    ks.update(date(2005, 1, 4), 1.0)
    assert not ks.paused and not ks.events


def _crash_inputs():
    # flat at 100 through the first entry (2005-01-14), then a permanent drop to 80
    return make_inputs(DAYS, close=lambda d: 100.0 if d <= date(2005, 1, 17) else 80.0)


def test_a_crash_after_entry_trips_the_kill_switch_skips_three_months_of_cycles_then_resumes():
    cfg = SMALL.replace(risk_budget_pct_of_sleeve=30.0)       # one full loss is about 30% of the sleeve
    run = sw.simulate(cfg, _crash_inputs())
    assert len(run.kill_switch) == 1
    ev = run.kill_switch[0]
    # the activation is the first day equity is at or below 80% of the running peak
    peak, first = 25000.0, None
    for d, e, _ in run.daily:
        peak = max(peak, e)
        if e <= 0.8 * peak:
            first = d
            break
    assert ev["activated"] == first == date(2005, 1, 18)
    assert ev["resume_date"] == date(2005, 4, 18) and ev["resumed_on"] == date(2005, 4, 18)
    assert ev["cycles_skipped"] == [date(2005, 3, 18), date(2005, 4, 15), date(2005, 5, 20)]
    skipped = [s for s in run.skips if s["reason"] == "kill_switch"]
    assert [s["entry_day"] for s in skipped] == [date(2005, 2, 11), date(2005, 3, 11), date(2005, 4, 15)]
    # the position open at the trip ran to its normal exit
    assert first_position(run).exit_day == date(2005, 2, 11) and first_position(run).net_pnl < -6000
    # trading resumed at the first cycle on/after the resume date, and the old peak did not re-trip the switch
    later = [p for p in run.positions if p.entry_day >= date(2005, 4, 18)]
    assert later and later[0].entry_day == date(2005, 5, 13)


def test_without_the_kill_switch_the_same_crash_does_not_pause_trading():
    cfg = SMALL.replace(risk_budget_pct_of_sleeve=30.0, kill_switch_enabled=False)
    run = sw.simulate(cfg, _crash_inputs())
    assert not run.kill_switch and not [s for s in run.skips if s["reason"] == "kill_switch"]
    assert len(run.positions) == sum(1 for c in run.cycles if c.status == "in_window")


# =================================================================================================== metrics
def _fake_run(equities, start=date(2005, 1, 3), cfg=None):
    cfg = cfg or SMALL
    days = weekdays(start, start + timedelta(days=4000))[: len(equities)]
    prev, daily = cfg.sleeve_capital_usd, []
    for d, e in zip(days, equities):
        daily.append((d, e, e - prev))
        prev = e
    return sw.WheelRun(cfg, [], [], [], daily, [], len(daily))


def test_sharpe_uses_daily_pnl_over_the_sleeve_the_sample_std_and_root_252():
    pnl = [250.0, -125.0, 500.0, 0.0, 375.0, -250.0, 125.0]
    eq, e = [], 25000.0
    for p in pnl:
        e += p
        eq.append(e)
    m = sw.compute_metrics(_fake_run(eq))
    rets = [p / 25000.0 for p in pnl]
    assert m["net_sharpe"] == pytest.approx(statistics.mean(rets) / statistics.stdev(rets) * math.sqrt(252), rel=1e-12)
    assert m["t_stat"] == pytest.approx(statistics.mean(rets) / (statistics.stdev(rets) / math.sqrt(len(rets))), rel=1e-12)
    assert m["total_net_pnl"] == pytest.approx(sum(pnl)) and m["days"] == 7


def test_max_drawdown_is_measured_from_the_never_reset_peak_in_dollars_over_the_sleeve():
    eq = [25500.0, 26000.0, 24500.0, 23000.0, 25000.0, 26500.0, 26000.0]
    m = sw.compute_metrics(_fake_run(eq))
    assert m["max_drawdown_usd"] == 3000.0 and m["max_drawdown_pct_of_sleeve"] == pytest.approx(12.0)
    assert m["max_drawdown_pct_of_peak"] == pytest.approx(3000 / 26000 * 100)
    d = [x[0] for x in _fake_run(eq).daily]
    assert (m["max_drawdown_peak_day"], m["max_drawdown_trough_day"]) == (d[1].isoformat(), d[3].isoformat())
    # a curve that starts by falling is measured from the initial sleeve
    assert sw.compute_metrics(_fake_run([24000.0, 23000.0, 24500.0]))["max_drawdown_usd"] == 2000.0


def test_calendar_year_pnl_is_the_sum_of_the_daily_changes_and_positive_means_strictly_above_zero():
    days = [date(2005, 12, 29), date(2005, 12, 30), date(2006, 1, 3), date(2006, 1, 4), date(2007, 1, 3)]
    eq = [25100.0, 25100.0, 25400.0, 25400.0, 25400.0]            # 2005: +100, 2006: +300, 2007: 0
    cfg = SMALL
    daily, prev = [], cfg.sleeve_capital_usd
    for d, e in zip(days, eq):
        daily.append((d, e, e - prev))
        prev = e
    m = sw.compute_metrics(sw.WheelRun(cfg, [], [], [], daily, [], 5))
    assert m["years"] == {2005: 100.0, 2006: 300.0, 2007: 0.0}
    assert m["positive_years"] == 2 and m["n_years"] == 3
    assert (m["worst_year"], m["best_year"]) == (2007, 2006)


def test_cagr_is_the_annualised_ending_equity_over_the_sleeve():
    days = [date(2005, 1, 3), date(2007, 1, 3)]
    daily = [(days[0], 25000.0, 0.0), (days[1], 30250.0, 5250.0)]
    m = sw.compute_metrics(sw.WheelRun(SMALL, [], [], [], daily, [], 2))
    assert m["cagr"] == pytest.approx((30250.0 / 25000.0) ** (365.25 / 730) - 1)


def _gate_metrics(**kw):
    base = {"net_sharpe": 0.6, "max_drawdown_usd": 5000.0, "max_drawdown_pct_of_sleeve": 20.0, "worst_year": 2008, "worst_year_pnl": -1000.0,
            "worst_year_pct_of_sleeve": -4.0, "positive_years": 18, "n_years": 21}
    return {**base, **kw}


def test_the_screening_gate_needs_all_four_checks_and_each_boundary_is_inclusive():
    assert sw.screening_gate(_gate_metrics())["verdict"] == "WORTH REAL DATA"
    assert sw.screening_gate(_gate_metrics(net_sharpe=0.5))["verdict"] == "WORTH REAL DATA"
    assert sw.screening_gate(_gate_metrics(net_sharpe=0.4999999))["verdict"] == "NOT WORTH REAL DATA"
    assert sw.screening_gate(_gate_metrics(net_sharpe=None))["verdict"] == "NOT WORTH REAL DATA"
    assert sw.screening_gate(_gate_metrics(max_drawdown_usd=7500.0))["verdict"] == "WORTH REAL DATA"
    assert sw.screening_gate(_gate_metrics(max_drawdown_usd=7500.01))["verdict"] == "NOT WORTH REAL DATA"
    assert sw.screening_gate(_gate_metrics(worst_year_pnl=-5000.0))["verdict"] == "WORTH REAL DATA"
    assert sw.screening_gate(_gate_metrics(worst_year_pnl=-5000.01))["verdict"] == "NOT WORTH REAL DATA"
    assert sw.screening_gate(_gate_metrics(positive_years=14))["verdict"] == "WORTH REAL DATA"          # exactly two thirds of 21
    assert sw.screening_gate(_gate_metrics(positive_years=13))["verdict"] == "NOT WORTH REAL DATA"
    g = sw.screening_gate(_gate_metrics(positive_years=13, net_sharpe=0.1))
    assert [k for k, c in g["checks"].items() if not c["pass"]] == ["net_sharpe", "positive_years"]


def test_the_verdict_vocabulary_is_only_worth_or_not_worth_real_data():
    assert {sw.WORTH, sw.NOT_WORTH} == {"WORTH REAL DATA", "NOT WORTH REAL DATA"}


# ============================================================================================ PUT calibration
def test_put_calibration_period_return_is_interest_plus_premium_less_payoff_over_the_strike():
    days = weekdays(date(2006, 1, 2), date(2007, 6, 29))
    close = lambda d: 100.0 if d < date(2007, 2, 16) else 90.0      # noqa: E731  falls through the Feb 16 expiry
    inp = make_inputs(days, close=close, vix=20.0, rate=3.0, q=0.0)
    exp = sw.monthly_expiries(days, (2007, 1), (2007, 4))
    put = {e.expiry: 1000.0 for e in exp}
    put[date(2007, 2, 16)] = 990.0
    cfg = sw.WheelConfig().replace(window_end="2007-12-31")
    cal = sw.calibrate_put(inp, put, cfg, exp)
    p = cal["periods"][0]
    assert (p["start"], p["end"], p["strike"]) == (date(2007, 1, 19), date(2007, 2, 16), 100)
    t = 28 / 365
    prem = sw.put_price(100.0, 100.0, t, 0.03, 0.0, 0.20)
    assert p["payoff"] == 10.0 and p["premium"] == pytest.approx(prem)
    assert p["model_return"] == pytest.approx(0.03 * t + (prem - 10.0) / 100.0)
    assert p["put_return"] == pytest.approx(990.0 / 1000.0 - 1.0)
    assert cal["n_periods"] == 3 and [y["year"] for y in cal["years"]] == [2007] and cal["years"][0]["periods"] == 3
    assert cal["years"][0]["model"] == pytest.approx(math.prod(1 + r["model_return"] for r in cal["periods"]) - 1)


def test_put_calibration_skips_periods_before_the_first_daily_row_after_the_window_and_without_a_put_value():
    days = weekdays(date(2005, 6, 1), date(2007, 12, 31))
    inp = make_inputs(days)
    exp = sw.monthly_expiries(days, (2006, 11), (2007, 4))        # Nov 17, Dec 15, Jan 19, Feb 16, Mar 16, Apr 20
    put = {e.expiry: 1000.0 for e in exp}
    del put[date(2007, 2, 16)]
    cal = sw.calibrate_put(inp, put, sw.WheelConfig(), exp)
    assert [(p["start"], p["end"]) for p in cal["periods"]] == [(date(2007, 3, 16), date(2007, 4, 20))]     # Nov/Dec rolls precede 2007-01-03
    assert {d["start"] for d in cal["dropped"]} == {"2007-01-19", "2007-02-16"}                              # the Feb 16 value is missing
    short = sw.calibrate_put(inp, put, sw.WheelConfig().replace(window_end="2007-04-19"), exp)
    assert short["n_periods"] == 0                                                                            # the last period ends after the window


def test_pearson_known_values():
    assert sw.pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert sw.pearson([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)
    assert sw.pearson([1, 2, 3], [5, 5, 5]) is None and sw.pearson([1, 2], [1, 2]) is None


# ============================================================================================== the 2026 refusal
def test_the_simulation_and_the_calibration_refuse_the_2026_holdout():
    inp = make_inputs(DAYS)
    with pytest.raises(HoldoutError):
        sw.simulate(SMALL.replace(window_end="2026-01-01"), inp)
    with pytest.raises(HoldoutError):
        sw.simulate(SMALL.replace(window_start="2026-01-02", window_end="2026-02-01"), inp)
    late = make_inputs(weekdays(date(2004, 1, 1), date(2026, 1, 9)))
    with pytest.raises(HoldoutError):
        sw.simulate(SMALL, late)                                          # the data itself reaches 2026
    with pytest.raises(HoldoutError):
        sw.calibrate_put(inp, {}, sw.WheelConfig().replace(window_end="2026-03-01"), [])
    sw.assert_not_holdout(date(2025, 12, 31))
    with pytest.raises(HoldoutError):
        sw.assert_not_holdout(date(2026, 1, 1))
    with pytest.raises(HoldoutError):
        wd.assert_not_holdout(date(2026, 1, 1))


def test_nothing_after_2025_is_ever_requested_from_any_source():
    assert "coed=2025-12-31" in wd.FRED_URL and "cosd=2004-01-01" in wd.FRED_URL
    assert wd.YAHOO_END == "2026-01-01"
    seen = {}
    def fetch(sym, start, end):
        seen["args"] = (sym, start, end)
        return pd.DataFrame({"Open": [1.0], "Close": [1.0], "Dividends": [0.0], "Stock Splits": [0.0]},
                            index=pd.DatetimeIndex([pd.Timestamp("2025-12-31", tz="America/New_York")]))
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        wd.download_underlying("SPY", root=Path(tmp), fetch=fetch)
    assert seen["args"] == ("SPY", "2004-01-01", "2026-01-01")


def test_parsers_and_the_cache_refuse_2026_rows(tmp_path):
    with pytest.raises(HoldoutError):
        wd.parse_fred_csv("observation_date,VIXCLS\n2025-12-31,15.0\n2026-01-02,16.0\n", "VIXCLS")
    ok = wd.parse_fred_csv("observation_date,VIXCLS\n2025-12-30,14.0\n2025-12-31,.\n", "VIXCLS")
    assert len(ok) == 2 and ok["value"].isna().sum() == 1
    df, dropped = wd.parse_put_csv("DATE,PUT\n12/30/2025,1000.5\n12/31/2025,1001.5\n01/02/2026,1002.5\n01/05/2026,1003.5\n")
    assert dropped is True and len(df) == 2 and df["date"].max() == pd.Timestamp("2025-12-31")
    assert wd.parse_put_csv("DATE,PUT\n12/30/2025,1000.5\n")[1] is False
    bad = pd.DataFrame({"Open": [1.0], "Close": [1.0], "Dividends": [0.0], "Stock Splits": [0.0]},
                       index=pd.DatetimeIndex([pd.Timestamp("2026-01-02", tz="America/New_York")]))
    with pytest.raises(HoldoutError):
        wd.download_underlying("SPY", root=tmp_path, fetch=lambda *a: bad)
    (tmp_path / "fred").mkdir()
    pd.DataFrame({"date": pd.to_datetime(["2025-12-31", "2026-01-02"]), "value": [1.0, 2.0]}).to_parquet(tmp_path / "fred" / "X.parquet")
    (tmp_path / "fred" / "X.json").write_text("{}", encoding="utf-8")
    with pytest.raises(HoldoutError):
        wd.load_series("fred", "X", tmp_path)


# =============================================================================================== data alignment
def test_carry_forward_uses_the_latest_earlier_value_and_lists_every_stale_day():
    obs = {date(2005, 10, 7): 3.5, date(2005, 10, 11): 3.6}
    days = [date(2005, 10, 7), date(2005, 10, 10), date(2005, 10, 11)]
    out, stale = wd.carry_forward(obs, days, name="DTB3")
    assert out == {days[0]: 3.5, days[1]: 3.5, days[2]: 3.6}
    assert stale == [{"day": "2005-10-10", "observed_on": "2005-10-07", "stale_days": 3}]
    assert wd.carry_forward(obs, [date(2005, 10, 12)], name="x", max_stale_days=5)[0][date(2005, 10, 12)] == 3.6
    with pytest.raises(ValueError, match="stale"):
        wd.carry_forward({date(2005, 10, 7): 3.5}, [date(2005, 10, 13)], name="x", max_stale_days=5)     # 6 days
    assert wd.carry_forward({date(2005, 10, 7): 3.5}, [date(2005, 10, 12)], name="x", max_stale_days=5)[0]      # exactly 5 is allowed
    with pytest.raises(ValueError, match="no observation"):
        wd.carry_forward(obs, [date(2005, 10, 3)], name="x")


def test_trailing_dividend_yield_window_is_365_days_ending_on_the_day_inclusive():
    divs = [(date(2005, 1, 3), 0.5), (date(2005, 4, 1), 0.5), (date(2006, 1, 3), 0.25)]
    close = {date(2006, 1, 3): 100.0, date(2006, 1, 2): 100.0, date(2006, 4, 1): 50.0, date(2006, 4, 3): 50.0}
    days = sorted(close)
    out = wd.trailing_dividend_yield(divs, close, days, first_valid=date(2006, 1, 1))
    assert out[date(2006, 1, 2)] == pytest.approx((0.5 + 0.5) / 100.0)             # 2005-01-03 is 364 days before: inside
    assert out[date(2006, 1, 3)] == pytest.approx((0.5 + 0.25) / 100.0)            # 2005-01-03 is exactly 365 days before: out; today's ex-date: in
    assert out[date(2006, 4, 1)] == pytest.approx(0.25 / 50.0)                     # 2005-04-01 is exactly 365 days before: out
    assert out[date(2006, 4, 3)] == pytest.approx(0.25 / 50.0)
    assert wd.trailing_dividend_yield(divs, close, days, first_valid=date(2006, 1, 3)).keys() == {date(2006, 1, 3), date(2006, 4, 1), date(2006, 4, 3)}


def _frame(days, close=100.0, divs=None, splits=None):
    return pd.DataFrame({"date": pd.to_datetime(days), "open": close, "close": close,
                         "dividends": [(divs or {}).get(d, 0.0) for d in days], "split": [(splits or {}).get(d, 0.0) for d in days]})


def test_build_inputs_validates_the_series():
    days = weekdays(date(2004, 1, 1), date(2005, 3, 31))
    vol = {d: 20.0 for d in days}
    rate = {d: 2.0 for d in days}
    ok = wd.build_inputs("SPY", "VIXCLS", _frame(days), {"splits": []}, vol, rate)
    assert ok.days[0] == days[0] and min(ok.div_yield) == date(2005, 1, 3)           # q starts once a full 365-day look-back is inside the data
    with pytest.raises(ValueError, match="non-positive"):
        wd.build_inputs("SPY", "VIXCLS", _frame(days, close=0.0), {}, vol, rate)
    with pytest.raises(ValueError, match="gap"):
        wd.build_inputs("SPY", "VIXCLS", _frame(days[:100] + days[110:]), {}, vol, rate)
    with pytest.raises(ValueError, match=r"outside \(0, 200\)"):
        wd.build_inputs("SPY", "VIXCLS", _frame(days), {}, {d: 500.0 for d in days}, rate)
    with pytest.raises(ValueError, match="split"):
        wd.build_inputs("SPY", "VIXCLS", _frame(days, splits={days[50]: 2.0}), {"splits": [{"date": days[50].isoformat(), "ratio": 2.0}]}, vol, rate)
    with pytest.raises(HoldoutError):
        wd.build_inputs("SPY", "VIXCLS", _frame(weekdays(date(2025, 12, 1), date(2026, 1, 5))), {}, vol, rate)


def test_two_source_check_tolerance_is_inclusive_and_it_lists_what_it_found():
    a = {date(2020, 1, 2): 1000.5, date(2020, 1, 3): 1000.51, date(2020, 1, 6): 50.0, date(2015, 1, 2): 9.0}
    b = {date(2020, 1, 2): 1000.0, date(2020, 1, 3): 1000.0, date(2020, 1, 7): 51.0, date(2015, 1, 2): 9.9}
    r = wd.two_source_check(a, b, date(2016, 1, 1), date(2025, 12, 31))
    assert r["compared"] == 2 and r["agree"] == 1                              # 0.05% exactly agrees, 0.051% does not
    assert [x["day"] for x in r["disagreements"]] == ["2020-01-03"]
    assert r["only_in_a"] == ["2020-01-06"] and r["only_in_b"] == ["2020-01-07"]    # 2015 is outside the range
    assert r["max_diff_pct"] == pytest.approx(0.051, abs=1e-9)


# =============================================================================================== determinism and ordering
def test_the_simulation_is_deterministic():
    rng = random.Random(9)
    s, closes = 100.0, {}
    for d in DAYS:
        s *= math.exp(rng.gauss(0.0, 0.011))
        closes[d] = s
    inp = make_inputs(DAYS, close=lambda d: closes[d])
    a, b = sw.simulate(SMALL, inp), sw.simulate(SMALL, inp)
    assert a.daily == b.daily and [vars(p) for p in a.positions] == [vars(p) for p in b.positions] and a.skips == b.skips


def test_the_run_refuses_inputs_for_another_underlying_or_volatility_index():
    with pytest.raises(ValueError, match="configuration wants"):
        sw.simulate(SMALL, make_inputs(DAYS, symbol="QQQ", vol_name="VXNCLS"))


def test_the_daily_series_covers_every_window_day_with_zero_before_the_first_entry():
    run = sw.simulate(SMALL, make_inputs(DAYS))
    window = [d for d in DAYS if date(2005, 1, 3) <= d <= date(2006, 12, 29)]
    assert [d for d, _, _ in run.daily] == window and run.window_days == len(window)
    assert run.daily[0] == (date(2005, 1, 3), 25000.0, 0.0)
    assert all(p == 0.0 for d, _, p in run.daily if d < date(2005, 1, 14))
    entry = [x for x in run.daily if x[0] == date(2005, 1, 14)][0]
    p = first_position(run)
    assert entry[2] == pytest.approx(-(0.06 * 100 * p.contracts) - 1.30 * p.contracts)      # the haircut and the entry fees land on the entry day


# ====================================================================================== the pre-registration pin
def _block(path=PREREG):
    return json.loads(re.search(r"```json\n(.*?)\n```", open(path, encoding="utf-8").read(), re.S).group(1))


def test_the_default_configuration_equals_the_preregistered_block():
    b = _block()
    cfg = sw.WheelConfig()
    assert b["spec_version"] == cfg.spec_version == sw.SPEC_VERSION
    fields = {k: getattr(cfg, k) for k in b["config"]}
    assert fields == b["config"]
    assert set(b["config"]) == {f.name for f in sw.dataclasses.fields(cfg)} - {"spec_version"}
    assert (cfg.window_start, cfg.window_end) == (sw.PREREG_START, sw.PREREG_END) == ("2005-01-03", "2025-12-31")
    g = b["gate"]
    assert (sw.GATE_MIN_NET_SHARPE, sw.GATE_MAX_DRAWDOWN_PCT, sw.GATE_MIN_WORST_YEAR_PCT) == (g["min_net_sharpe"], g["max_drawdown_pct_of_sleeve"], g["min_worst_year_pct_of_sleeve"])
    assert (sw.GATE_POSITIVE_YEARS_NUM, sw.GATE_POSITIVE_YEARS_DEN) == (g["min_positive_year_fraction_numerator"], g["min_positive_year_fraction_denominator"])
    assert b["verifier"] == {"seed": 11, "positions": 30}
    assert b["calibration"]["first_daily_row"] == sw.CALIBRATION_FIRST_ROW.isoformat()


def test_the_variants_are_exactly_the_preregistered_ones_and_each_changes_only_the_listed_fields():
    b = _block()
    assert [v.name for v in sw.ALL_VARIANTS] == ["primary"] + [s["name"] for s in b["sensitivities"]]
    base = sw.WheelConfig()
    for v, spec in zip(sw.SENSITIVITIES, b["sensitivities"]):
        assert dict(v.changes) == spec["changes"]
        assert v.config().diff_from_default() == spec["changes"]
        assert set(spec["changes"]) <= {f.name for f in sw.dataclasses.fields(base)}
    assert sw.PRIMARY.config() == base
    assert len(sw.SENSITIVITIES) == 9


def test_the_preregistration_states_the_rules_word_for_word():
    text = open(PREREG, encoding="utf-8").read()
    for phrase in ("Strike with model delta closest to −0.20, rounded to the nearest $1",
                   "Short strike minus a width w = 2% of spot, rounded to the nearest $1 (minimum $1)",
                   "**IV(K) = VIX/100 + skew × max(0, ln(S/K)) × 100 / 100**, with **skew = 0.5 vol points per 1% out of the money** (primary)",
                   "Each leg, each side: $0.03 per share haircut against the model price (sell below, buy above) plus $0.65 per contract fees. "
                   "So entry credit = model credit − 0.06, and exit debit = model debit + 0.06",
                   "(a) spread marked at ≤ 50% of the entry credit (profit-take, at that day's model price + costs); (b) the close **7 calendar days before expiry** (or the prior trading day)",
                   "Contracts = floor(2,500 ÷ ((w − entry credit) × 100)); 0 → skip and count",
                   "If marked sleeve equity falls **20% below its running peak**, open no new positions for 3 calendar months (an open position runs to its normal exit), then resume",
                   "All four hold → **WORTH REAL DATA**; anything else → **NOT WORTH REAL DATA**",
                   "Net P&L positive in **at least two-thirds** of calendar years",
                   "**QQQ with VXN** (FRED `VXNCLS`) instead of SPY with VIX"):
        assert phrase in text, phrase


def test_the_preregistration_rule_text_is_unchanged_since_its_first_commit():
    info = provenance.prereg_info(path=sw.PREREG_PATH)
    if info["first_commit"] is None:
        pytest.skip("no git history (shallow clone)")
    assert info["unchanged"] is True, info


# ======================================================================== validity, the report and the CLI end to end
def test_validity_flags_a_non_preregistered_run():
    good = {"unchanged": True}
    assert R.validity(sw.PREREG_START, sw.PREREG_END, sw.WheelConfig(), sw.ALL_VARIANTS, good) == []
    assert R.validity("2006-01-03", sw.PREREG_END, sw.WheelConfig(), sw.ALL_VARIANTS, good)
    assert R.validity(sw.PREREG_START, sw.PREREG_END, sw.WheelConfig().replace(short_delta=-0.25), sw.ALL_VARIANTS, good)
    assert R.validity(sw.PREREG_START, sw.PREREG_END, sw.WheelConfig(), sw.ALL_VARIANTS[:5], good)
    assert R.validity(sw.PREREG_START, sw.PREREG_END, sw.WheelConfig(), sw.ALL_VARIANTS, {"unchanged": False})
    assert R.validity(sw.PREREG_START, sw.PREREG_END, sw.WheelConfig(), sw.ALL_VARIANTS, {"unchanged": None})


def _synthetic_market(seed=5, end=date(2007, 3, 30)):
    rng = random.Random(seed)
    days = weekdays(date(2004, 1, 1), end, holidays={date(2005, 3, 25), date(2006, 4, 14)})
    s, close, vix, divs = 100.0, {}, {}, {}
    for d in days:
        s *= math.exp(rng.gauss(0.0003, 0.01))
        close[d] = round(s, 2)
        vix[d] = round(max(9.0, 18 + rng.gauss(0, 3)), 2)
        if d.month in (3, 6, 9, 12) and d.day in (17, 18, 19, 20):
            divs.setdefault((d.year, d.month), d)
    divs = {d: 0.4 for d in divs.values()}
    return days, close, vix, divs


def _write_cache(root, days, close, vix, divs, qqq_scale=1.0):
    (root / "yahoo").mkdir(parents=True, exist_ok=True)
    (root / "fred").mkdir(parents=True, exist_ok=True)
    for sym, scale in (("SPY", 1.0), ("QQQ", qqq_scale)):
        df = pd.DataFrame({"date": pd.to_datetime(days), "open": [close[d] * scale for d in days], "close": [close[d] * scale for d in days],
                           "dividends": [divs.get(d, 0.0) * scale for d in days], "split": [0.0] * len(days)})
        df.to_parquet(root / "yahoo" / f"{sym}.parquet", index=False)
        (root / "yahoo" / f"{sym}.json").write_text(json.dumps({"splits": [], "tz": "America/New_York"}), encoding="utf-8")
    for name, series in (("VIXCLS", vix), ("VXNCLS", {d: v + 4 for d, v in vix.items()}), ("DTB3", {d: 2.5 for d in days})):
        pd.DataFrame({"date": pd.to_datetime(list(series)), "value": list(series.values())}).to_parquet(root / "fred" / f"{name}.parquet", index=False)
        (root / "fred" / f"{name}.json").write_text("{}", encoding="utf-8")


@pytest.fixture()
def patched(monkeypatch, tmp_path):
    days, close, vix, divs = _synthetic_market()
    root = tmp_path / "wheel"
    _write_cache(root, days, close, vix, divs)
    monkeypatch.setattr(R, "run_price_check", lambda layer, spy_close, offline: (None, "test run", None))
    monkeypatch.setattr(R, "DataLayer", lambda cache_dir=None: None)
    monkeypatch.setattr(R.provenance, "prereg_info", lambda path=None: {"path": path, "first_commit": "abc", "unchanged": True,
                                                                         "sha256_at_first_commit": "x", "sha256_now": "x", "has_post_run_note": False})
    return root, tmp_path


def test_the_cli_writes_outputs_labels_a_non_preregistered_window_and_the_verifier_passes_then_catches_a_tamper(patched, capsys, monkeypatch):
    root, tmp = patched
    out = tmp / "reports"
    rc = R.main(["run", "--start", "2005-01-03", "--end", "2006-12-29", "--out", str(out), "--wheel-dir", str(root),
                 "--review-file", str(tmp / "review" / "results.md")])
    assert rc == 0
    assert "NOT a pre-registered run" in capsys.readouterr().out
    run = next(out.iterdir())
    header = json.loads((run / "header.json").read_text(encoding="utf-8"))
    assert header["valid_for_verdict"] is False and header["gate"]["verdict"] == "NOT A PRE-REGISTERED RUN"
    for v in sw.ALL_VARIANTS:
        for f in ("positions.csv", "daily.csv", "skips.csv", "cycles.csv"):
            assert (run / v.name / f).is_file()
    for f in ("metrics.json", "checks.json", "summary.md"):
        assert (run / f).is_file()
    text = (tmp / "review" / "results.md").read_text(encoding="utf-8")
    assert "NOT A PRE-REGISTERED RUN" in text and "## Crisis close-up" in text and "## Limitations" in text and "Not available" in text
    # the independent verifier agrees with the run, from the raw cache alone
    monkeypatch.setattr(V, "WHEEL", root)
    res = V.check(run, n=30)
    assert res["pass"] and res["matched"] == res["checked"] == min(30, res["positions_in_run"]) and res["checked"] > 10
    pos = pd.read_csv(run / "primary" / "positions.csv")
    pos.loc[3, "credit"] = float(pos.loc[3, "credit"]) + 0.01
    pos.to_csv(run / "primary" / "positions.csv", index=False)
    bad = V.check(run, n=len(pos))
    assert not bad["pass"] and any("credit" in m for f in bad["failures"] for m in f["mismatches"])


def test_the_verifier_catches_a_wrong_strike_and_a_wrong_exit_and_a_wrong_pnl(patched, monkeypatch):
    root, tmp = patched
    out = tmp / "reports"
    R.main(["run", "--start", "2005-01-03", "--end", "2006-12-29", "--out", str(out), "--wheel-dir", str(root)])
    run = next(out.iterdir())
    monkeypatch.setattr(V, "WHEEL", root)
    path = run / "primary" / "positions.csv"
    base = pd.read_csv(path)
    for col, delta in (("k_short", 1), ("contracts", 1), ("net_pnl", 1.0)):
        pos = base.copy()
        pos.loc[2, col] = pos.loc[2, col] + delta
        pos.to_csv(path, index=False)
        assert not V.check(run, n=len(pos))["pass"], col
    pos = base.copy()
    pos.loc[2, "exit_day"] = "2006-12-29"
    pos.to_csv(path, index=False)
    assert not V.check(run, n=len(pos))["pass"]
    base.to_csv(path, index=False)
    assert V.check(run, n=len(base))["pass"]


def test_the_exact_preregistered_window_and_variants_carry_a_verdict(monkeypatch, tmp_path, capsys):
    days = weekdays(date(2004, 1, 1), date(2025, 12, 31), holidays=GOOD_FRIDAYS)
    rng = random.Random(1)
    s, close = 100.0, {}
    for d in days:
        s *= math.exp(rng.gauss(0.0004, 0.009))
        close[d] = round(s, 2)
    vix = {d: 16.0 + 3 * math.sin(d.toordinal() / 50.0) for d in days}
    root = tmp_path / "wheel"
    _write_cache(root, days, close, vix, {})
    monkeypatch.setattr(R, "run_price_check", lambda layer, spy_close, offline: (None, "test run", None))
    monkeypatch.setattr(R, "DataLayer", lambda cache_dir=None: None)
    monkeypatch.setattr(R.provenance, "prereg_info", lambda path=None: {"path": path, "first_commit": "abc", "unchanged": True,
                                                                         "sha256_at_first_commit": "x", "sha256_now": "x", "has_post_run_note": False})
    assert R.main(["run", "--out", str(tmp_path / "reports"), "--wheel-dir", str(root)]) == 0
    run = next((tmp_path / "reports").iterdir())
    header = json.loads((run / "header.json").read_text(encoding="utf-8"))
    assert header["valid_for_verdict"] is True and header["gate"]["verdict"] in (sw.WORTH, sw.NOT_WORTH)
    assert header["window"] == {"start": "2005-01-03", "end": "2025-12-31", "trading_days": len([d for d in days if d >= date(2005, 1, 3)])}
    cycles = pd.read_csv(run / "primary" / "cycles.csv")
    assert len(cycles) == 252 and (cycles["status"] == "in_window").sum() == 251
    assert f"**Screening verdict: {header['gate']['verdict']}.**" in (run / "summary.md").read_text(encoding="utf-8")


def test_the_cli_refuses_a_2026_window(patched):
    root, tmp = patched
    assert R.main(["run", "--end", "2026-01-01", "--out", str(tmp / "r"), "--wheel-dir", str(root)]) == 2
    with pytest.raises(HoldoutError):
        R.sw.assert_not_holdout(date(2026, 6, 1))


def test_the_qqq_variant_uses_the_qqq_inputs_and_the_vxn_index():
    spy = make_inputs(DAYS, close=100.0, vix=20.0)
    qqq = make_inputs(DAYS, close=200.0, vix=30.0, symbol="QQQ", vol_name="VXNCLS")
    res = sw.analyse({("SPY", "VIXCLS"): spy, ("QQQ", "VXNCLS"): qqq}, None, (sw.PRIMARY, sw.SENSITIVITIES[-1]), SMALL)
    a, b = res["runs"]["primary"].positions[0], res["runs"]["qqq_vxn"].positions[0]
    assert a.spot == 100.0 and a.vol_index == 20.0 and b.spot == 200.0 and b.vol_index == 30.0 and b.width == 4
    assert res["runs"]["qqq_vxn"].cfg.underlying == "QQQ" and res["calibration"] is None


# ============================================================================================= crisis windows, report
def test_crisis_windows_are_the_three_preregistered_calendar_ranges():
    assert [(n, a.isoformat(), b.isoformat()) for n, a, b in sw.CRISIS_WINDOWS] == [
        ("Sept 2008 - Mar 2009", "2008-09-01", "2009-03-31"), ("Feb - Apr 2020", "2020-02-01", "2020-04-30"),
        ("2022 drawdown (Jan 3 - Oct 14)", "2022-01-03", "2022-10-14")]


def test_a_position_is_open_during_a_window_when_it_enters_by_the_window_end_and_exits_on_or_after_its_start(monkeypatch):
    run = sw.simulate(SMALL.replace(profit_take_enabled=False), make_inputs(DAYS, vix=lambda d: 20.0))
    p = [x for x in run.positions if x.expiry == date(2005, 6, 17)][0]               # entry 2005-05-13, exit 2005-06-10
    assert (p.entry_day, p.exit_day) == (date(2005, 5, 13), date(2005, 6, 10))
    def inside(lo, hi):
        monkeypatch.setattr(sw, "CRISIS_WINDOWS", (("w", lo, hi),))
        return p in sw.crisis_closeup(run)[0]["positions"]
    assert inside(date(2005, 6, 10), date(2005, 6, 30))          # the window starts on the exit day: open
    assert not inside(date(2005, 6, 11), date(2005, 6, 30))      # one day after the exit: not open
    assert inside(date(2005, 5, 1), date(2005, 5, 13))           # the window ends on the entry day: open
    assert not inside(date(2005, 5, 1), date(2005, 5, 12))       # one day before the entry: not open
    assert inside(date(2005, 5, 20), date(2005, 5, 25))          # strictly inside the holding period
    skipped = sw.simulate(SMALL.replace(risk_budget_pct_of_sleeve=0.1), make_inputs(DAYS))
    monkeypatch.setattr(sw, "CRISIS_WINDOWS", (("w", date(2005, 5, 1), date(2005, 5, 31)),))
    block = sw.crisis_closeup(skipped)[0]
    assert block["positions"] == [] and [s["entry_day"] for s in block["skips"]] == [date(2005, 5, 13)]


def test_the_report_text_states_the_verdict_the_gate_the_limits_and_never_approves_trading():
    g = sw.screening_gate(_gate_metrics())
    table = rep.gate_table(g)
    assert table.count("**PASS**") == 4 and "FAIL" not in table
    assert "**FAIL**" in rep.gate_table(sw.screening_gate(_gate_metrics(net_sharpe=0.1)))
    assert "No substitute is used" in rep.calibration_section(None, "the Cboe PUT index file is not cached")
    for phrase in ("Modeled, not real, prices", "The skew is approximate", "No real bid/ask", "European, not American", "proxy for SPY option implied volatility",
                   "A single path", "never an approval"):
        assert phrase in rep.LIMITATIONS
    assert "WORTH REAL DATA" in rep.LIMITATIONS and "approval" in rep.LIMITATIONS
