"""Core candidates (research/core_candidates): signal timing, volatility window, caps, financing, the 2x simulation, momentum ties,
costs, sub-periods, the 2026 refusal, determinism, credit timing and C5's three states. Synthetic inputs only; no network."""
import math
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from research.core_candidates import engine as E  # noqa: E402

WIN = (date(2005, 1, 3), date(2005, 6, 30))


def market(spy=None, efa=None, agg=None, rate=0.03, credit=None, start="2003-01-01", end="2005-06-30"):
    days = [d.date() for d in pd.bdate_range(start, end)]
    n = len(days)
    spy = np.full(n, 100.0) if spy is None else np.asarray(spy, dtype=float)
    adj = {"SPY": spy, "EFA": np.full(n, 50.0) if efa is None else np.asarray(efa, float),
           "AGG": np.full(n, 80.0) if agg is None else np.asarray(agg, float), "ACWX": np.full(n, np.nan), "SSO": np.full(n, np.nan)}
    r = np.full(n, rate)
    r[0] = np.nan
    c = np.full(n, 2.0) if credit is None else np.asarray(credit, float)
    return E.Market(days, adj, r, c)


def idx(m, d):
    return m.days.index(d)


# --- the 2x simulation and financing ------------------------------------------------------------------------------------

def test_two_x_daily_return_by_hand():
    assert E.lev_return(0.01, 0.04) == pytest.approx(0.02 - 0.045 / 252 - 0.0091 / 252, abs=1e-15)
    # 1.5x: only the extra 0.5x is financed; the expense ratio is unchanged
    assert E.lev_return(0.01, 0.04, 1.5) == pytest.approx(0.015 - 0.5 * 0.045 / 252 - 0.0091 / 252, abs=1e-15)


def test_two_x_series_compounds_daily_like_a_hand_computed_series():
    n = len(pd.bdate_range("2003-01-01", "2005-06-30"))
    spy = 100.0 * np.cumprod(np.r_[1.0, np.tile([1.01, 0.99, 1.02, 0.985], n)[: n - 1]])
    m = market(spy=spy)
    res = E.simulate(m, E.Config("x", "C1", cost_bps=0.0), WIN)     # rising market: holds 2X from the first execution
    s = idx(m, WIN[0])
    hand = E.START_EQUITY
    for t in range(s + 1, s + 6):
        hand *= 1 + 2 * (spy[t] / spy[t - 1] - 1) - (0.03 + 0.005) / 252 - 0.0091 / 252
    assert res.holding[0] == "2X" and res.equity[5] == pytest.approx(hand, rel=1e-12)


def test_leverage_above_one_is_financed_at_rate_plus_spread():
    m = market()                                                     # SPY flat -> sigma 0 would divide by zero: give it noise
    n = len(m.days)
    rng = np.random.default_rng(0)
    m.adj["SPY"] = 100 * np.cumprod(np.r_[1.0, 1 + rng.normal(0, 0.001, n - 1)])
    s = idx(m, WIN[0])
    res = E.simulate(m, E.Config("x", "C2", cost_bps=0.0), WIN)
    assert res.executions[0]["exposure"] == 1.5                       # tiny vol -> capped
    # day after the first execution: SPY balance 1.5E grows by r_spy; borrowed -0.5E grows by (r+spread)/252
    r_spy = m.adj["SPY"][s + 1] / m.adj["SPY"][s] - 1
    want = 1.5 * E.START_EQUITY * (1 + r_spy) - 0.5 * E.START_EQUITY * (1 + (0.03 + 0.005) / 252)
    assert res.equity[1] == pytest.approx(want, rel=1e-12)


def test_tbill_balance_accrues_rate_over_252():
    m = market()
    m.adj["SPY"][:] = np.linspace(110, 90, len(m.days))              # falling: C1 stays in T-bills
    res = E.simulate(m, E.Config("x", "C1", cost_bps=0.0), WIN)
    assert res.holding[0] == "TB" and res.equity[3] == pytest.approx(E.START_EQUITY * (1 + 0.03 / 252) ** 3, rel=1e-12)


# --- signal timing ------------------------------------------------------------------------------------------------------

def _flip_market():
    """SPY flat at 100, then from 2005-02-15 a jump to 120: the February month-end is the first trend-healthy one."""
    days = [d.date() for d in pd.bdate_range("2003-01-01", "2005-06-30")]
    spy = np.array([120.0 if d >= date(2005, 2, 15) else 100.0 - 1e-6 * i for i, d in enumerate(days)])
    return market(spy=spy)


def test_month_end_signal_trades_at_the_next_close_not_the_decision_close():
    m = _flip_market()
    res = E.simulate(m, E.Config("x", "C1", cost_bps=0.0), WIN)
    feb_end, mar_first = date(2005, 2, 28), date(2005, 3, 1)
    h = dict(zip(res.dates, res.holding))
    assert h[feb_end] == "TB" and h[mar_first] == "2X"
    ex = [x for x in res.executions if x["label"] == "2X"][0]
    assert ex["decision_date"] == feb_end and ex["execution_date"] == mar_first


def test_no_lookahead_future_prices_never_change_a_decision():
    m1 = _flip_market()
    m2 = _flip_market()
    d = idx(m1, date(2005, 2, 28))
    m2.adj["SPY"][d + 1:] *= 0.5                                      # crash everything after the decision close
    assert E.trend_monthly(m1, d) == E.trend_monthly(m2, d)


def test_trend_sma_includes_the_decision_month_end_and_is_strict():
    m = market()
    d = idx(m, date(2005, 2, 28))
    assert E.trend_monthly(m, d) is False                            # all equal -> not strictly above
    me = [i for i in E.month_end_indices(m.days) if i <= d][-10:]
    m.adj["SPY"][d] = 100.0 + 1e-9
    assert E.trend_monthly(m, d) is True and len(me) == 10


def test_first_decision_is_2004_12_31_executing_on_the_first_window_day():
    m = market()
    dec = E.decisions(m, E.Config("x", "C1"), WIN)
    assert m.days[dec[0][0]] == date(2004, 12, 31)
    last = m.days[dec[-1][0]]
    assert last == date(2005, 5, 31)                                  # the June 2005 month-end would execute after the window


def test_week_ends_are_the_last_trading_day_of_each_calendar_week():
    days = [date(2024, 12, 30), date(2024, 12, 31), date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
    assert [days[i] for i in E.week_end_indices(days)] == [date(2025, 1, 3), date(2025, 1, 6)]


# --- volatility ---------------------------------------------------------------------------------------------------------

def test_volatility_uses_exactly_the_last_21_returns():
    m = market()
    rng = np.random.default_rng(5)
    m.adj["SPY"] = 100 * np.cumprod(np.r_[1.0, 1 + rng.normal(0, 0.01, len(m.days) - 1)])
    d = idx(m, date(2005, 3, 4))
    r = m.adj["SPY"][d - 20: d + 1] / m.adj["SPY"][d - 21: d] - 1
    assert E.realised_vol(m, d) == pytest.approx(float(np.std(r, ddof=1) * math.sqrt(252)), rel=1e-12)
    m.adj["SPY"][d - 22] *= 3                                         # a return outside the window changes nothing
    assert E.realised_vol(m, d) == pytest.approx(float(np.std(r, ddof=1) * math.sqrt(252)), rel=1e-12)


def test_exposure_cap():
    assert E.vol_exposure(0.05) == 1.5 and E.vol_exposure(0.30) == 0.5 and E.vol_exposure(0.15) == 1.0
    assert E.vol_exposure(0.10, target=0.12) == pytest.approx(1.2) and E.vol_exposure(0.01, target=0.18) == 1.5


# --- dual momentum ------------------------------------------------------------------------------------------------------

def _mom_market(spy_end, efa_end):
    m = market()
    d = idx(m, date(2005, 2, 28))
    p = idx(m, date(2004, 2, 27))
    m.adj["SPY"][d], m.adj["EFA"][d] = spy_end, efa_end
    m.adj["SPY"][p], m.adj["EFA"][p] = 100.0, 50.0
    return m, d


def test_momentum_tie_goes_to_spy_and_the_winner_otherwise():
    m, d = _mom_market(110.0, 55.0)                                   # both +10%
    assert E.dual_momentum(m, d) == "SPY"
    m, d = _mom_market(110.0, 56.0)
    assert E.dual_momentum(m, d) == "EFA"


def test_momentum_goes_to_bonds_when_spy_does_not_beat_tbills():
    m, d = _mom_market(100.0, 70.0)                                   # SPY 0% < T-bills ~3%; EFA's +40% is irrelevant
    assert E.dual_momentum(m, d) == "AGG"


def test_acwx_falls_back_to_efa_without_a_twelve_month_history():
    m, d = _mom_market(110.0, 56.0)
    assert E.dual_momentum(m, d, intl="ACWX") == "EFA"


# --- credit timing ------------------------------------------------------------------------------------------------------

def test_credit_never_uses_the_same_day_value():
    days = pd.bdate_range("2004-01-01", "2005-06-30")
    spy = pd.DataFrame({"date": days, "adj_close": 100.0})
    baa = pd.DataFrame({"date": days, "value": 2.0})
    spike = pd.Timestamp("2005-02-28")
    baa.loc[baa["date"] == spike, "value"] = 9.0
    dtb3 = pd.DataFrame({"date": days, "value": 3.0})
    m = E.build_market({"SPY": spy}, dtb3, baa, window=WIN)
    t = m.days.index(date(2005, 2, 28))
    assert m.credit[t] == 2.0 and m.credit[t + 1] == 9.0              # visible only on the next trading day
    assert m.rate[t] == pytest.approx(0.03)


def test_missing_fred_days_carry_forward_are_counted_and_long_gaps_stop():
    days = pd.bdate_range("2004-01-01", "2005-06-30")
    spy = pd.DataFrame({"date": days, "adj_close": 100.0})
    dtb3 = pd.DataFrame({"date": days, "value": 3.0})
    baa = pd.DataFrame({"date": days, "value": 2.0})
    dtb3.loc[dtb3["date"] == pd.Timestamp("2005-02-14"), "value"] = np.nan
    m = E.build_market({"SPY": spy}, dtb3, baa, window=WIN)
    assert m.rate_carry == [(date(2005, 2, 15), date(2005, 2, 11))] and m.credit_carry == []
    gap = (dtb3["date"] >= pd.Timestamp("2005-03-01")) & (dtb3["date"] <= pd.Timestamp("2005-03-14"))
    dtb3.loc[gap, "value"] = np.nan
    with pytest.raises(ValueError, match="carried"):
        E.build_market({"SPY": spy}, dtb3, baa, window=WIN)


def test_credit_test_uses_the_inclusive_window_mean():
    m = market()
    d = idx(m, date(2005, 2, 28))
    m.credit[:] = 2.0
    assert E.credit_calm(m, d) is True                                # equal -> calm (<=)
    m.credit[d] = 2.1
    assert E.credit_calm(m, d) is False


# --- C5 -----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("trend,credit,want", [(True, True, {"2X": 1.0}), (True, False, {"SPY": 1.0}), (False, True, {"SPY": 1.0}),
                                               (False, False, {"TB": 1.0})])
def test_c5_three_states(trend, credit, want):
    m = market()
    d = idx(m, date(2005, 2, 28))
    if trend:
        m.adj["SPY"][d] = 101.0
    m.credit[:] = 2.0
    if not credit:
        m.credit[d] = 3.0
    got = [w for dd, w, _ in E.decisions(m, E.Config("x", "C5"), WIN) if dd == d][0]
    assert got == want


# --- costs --------------------------------------------------------------------------------------------------------------

def test_cost_charges_both_sides_and_tbills_but_not_borrowing():
    assert E.trade_cost({"TB": 100.0}, {"2X": 100.0}, 2.0) == pytest.approx(0.0002 * 200)
    assert E.trade_cost({"SPY": 150.0, "TB": -50.0}, {"SPY": 120.0, "TB": -20.0}, 2.0) == pytest.approx(0.0002 * 30)
    assert E.trade_cost({"SPY": 100.0}, {"SPY": 100.0}, 2.0) == 0.0


def test_first_execution_pays_cost_and_an_unchanged_holding_does_not():
    m = market()
    m.adj["SPY"][:] = np.linspace(110, 90, len(m.days))
    res = E.simulate(m, E.Config("x", "C3", cost_bps=2.0), WIN)        # falling SPY -> AGG every month
    first = res.executions[0]
    assert first["label"] == "AGG" and first["cost"] == pytest.approx(E.START_EQUITY * 0.0002 * 2)
    assert all(x["cost"] == 0.0 for x in res.executions[1:])
    assert res.equity[0] == pytest.approx(E.START_EQUITY - first["cost"])


# --- metrics and verdict ------------------------------------------------------------------------------------------------

def test_sub_period_returns_base_on_the_last_day_before_the_period():
    dates = [date(2011, 12, 29), date(2011, 12, 30), date(2012, 1, 3), date(2012, 12, 31)]
    eq = np.array([100.0, 110.0, 121.0, 132.0])
    assert E.period_return(dates, eq, date(2012, 1, 1), date(2012, 12, 31)) == pytest.approx(132 / 110 - 1)
    assert E.period_return(dates, eq, date(2005, 1, 1), date(2011, 12, 31)) == pytest.approx(110 / 100 - 1)


def test_max_drawdown_dates():
    dates = [date(2020, 1, d) for d in range(1, 7)]
    dd = E.max_drawdown(dates, np.array([100.0, 120, 90, 100, 125, 110]))
    assert dd["mdd"] == pytest.approx(0.25) and dd["peak"] == dates[1] and dd["trough"] == dates[2] and dd["recovery"] == dates[4]


def test_verdict_needs_all_three():
    spy = {"cagr": 0.10, "mdd": 0.50, "sub_periods": [0.1, 0.2, 0.3]}
    assert E.verdict({"cagr": 0.11, "mdd": 0.50, "sub_periods": [0.2, 0.3, 0.0]}, spy)["verdict"] == "CANDIDATE"
    assert E.verdict({"cagr": 0.11, "mdd": 0.51, "sub_periods": [0.2, 0.3, 0.4]}, spy)["verdict"] == "REJECTED"
    assert E.verdict({"cagr": 0.10, "mdd": 0.10, "sub_periods": [0.2, 0.3, 0.4]}, spy)["verdict"] == "REJECTED"
    assert E.verdict({"cagr": 0.12, "mdd": 0.10, "sub_periods": [0.2, 0.2, 0.3]}, spy)["verdict"] == "REJECTED"   # ties do not beat


def test_sensitivity_list_is_exactly_the_preregistered_seventeen():
    names = [c.name for c in E.sensitivities()]
    assert names == ["C1-200d", "C1-1.5x", "C2-12", "C2-18", "C3-ACWX", "C4-63", "C4-252",
                     "C1-0bp", "C1-10bp", "C2-0bp", "C2-10bp", "C3-0bp", "C3-10bp", "C4-0bp", "C4-10bp", "C5-0bp", "C5-10bp"]


# --- 2026 and determinism -----------------------------------------------------------------------------------------------

def test_2026_is_refused_everywhere(tmp_path):
    with pytest.raises(E.HoldoutRefused):
        E.refuse_holdout(date(2026, 1, 2))
    m = market()
    with pytest.raises(E.HoldoutRefused):
        E.simulate(m, E.Config("x", "C1"), (date(2005, 1, 3), date(2026, 1, 2)))
    from research.core_candidates import data as D
    with pytest.raises(Exception, match="2026"):
        D.assert_not_holdout(date(2026, 1, 1))
    raw = pd.DataFrame({"Close": [1.0], "Adj Close": [1.0]}, index=pd.DatetimeIndex(["2026-01-02"]).tz_localize("America/New_York"))
    with pytest.raises(Exception, match="2026"):
        D.download_yahoo("SPY", root=tmp_path, fetch=lambda *a: raw)
    assert not list(tmp_path.rglob("*.parquet"))                      # nothing was written


def test_simulation_is_deterministic():
    m = _flip_market()
    a = E.simulate(m, E.Config("x", "C5"), WIN)
    b = E.simulate(m, E.Config("x", "C5"), WIN)
    assert np.array_equal(a.equity, b.equity) and a.holding == b.holding
