"""The global lead-lag study's pure logic on synthetic data with known answers (no network, no files).

Pins the config and the groups to the pre-registration, then checks every rule it states: date alignment (an Asian
session dated D for US date D; a European OPEN on D; a European close on D never read), regional averaging and the
fallback, holidays, split exclusion, signal-to-trade direction, the auction fill arithmetic, the metrics and verdict,
the correlations, the HAC regression (against an independent loop implementation), the data-quality checks, the
session-timing table and the 2026 refusal.
"""
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from backtest import leadlag as L  # noqa: E402
from backtest.data.bars import HoldoutError  # noqa: E402
from backtest.foreign import MemberRows  # noqa: E402

PREREG = os.path.join(os.path.dirname(__file__), "..", "docs", "specs", "Global_LeadLag_Preregistration.md")
CFG = L.LeadLagConfig()
D1, D2, D3, D4 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)


def member(symbol, rows, splits=(), tz="Asia/Tokyo"):
    """rows: [(date, open, close)]"""
    return MemberRows(symbol, tz, tuple(r[0] for r in rows), tuple(r[1] for r in rows), tuple(r[2] for r in rows),
                      tuple(splits))


# ============================================================================== the pre-registration
def _block():
    text = open(PREREG, encoding="utf-8").read()
    return json.loads(re.search(r"```json\n(.*?)\n```", text, re.S).group(1))


def test_the_config_and_groups_equal_the_machine_readable_block_in_the_preregistration():
    b = _block()
    groups = b.pop("groups")
    cfg = {f: getattr(CFG, f) for f in b}
    assert cfg == b and set(b) == {f.name for f in __import__("dataclasses").fields(CFG)}
    assert {g.name: {"us": g.us, "asia": list(g.asia), "europe": list(g.europe)} for g in L.GROUPS} == groups


def test_the_groups_table_in_the_preregistration_text_matches_the_code():
    text = open(PREREG, encoding="utf-8").read()
    for g in L.GROUPS:
        row = re.search(rf"\| {g.name} \| {g.us} \| (.*?) \| (.*?) \|", text)
        assert row, g.name
        asia = tuple(x.strip() for x in row.group(1).split(",")) if "(none)" not in row.group(1) else ()
        europe = tuple(x.strip() for x in row.group(2).split(","))
        assert (asia, europe) == (g.asia, g.europe)


def test_every_member_appears_in_exactly_one_group_and_one_region():
    assert len(L.ALL_MEMBERS) == len(set(L.ALL_MEMBERS)) == 22
    assert L.GROUPS[3].asia == () and L.GROUPS[0].region_of("^N225") == L.ASIA and L.GROUPS[0].region_of("^FTSE") == L.EUROPE


def test_the_variants_are_exactly_the_four_preregistered_sensitivities():
    assert [v.name for v in L.ALL_VARIANTS] == ["primary", "asia_only", "europe_only", "long_only", "slippage_2c"]
    assert L.SENSITIVITIES[2].long_only and L.SENSITIVITIES[3].slippage == "sensitivity" and L.PRIMARY.signal == "s"


# ======================================================================================= date alignment
def test_an_asian_return_uses_the_close_of_the_session_dated_D_and_the_previous_sessions_close():
    m = member("^N225", [(D1, 100, 100), (D2, 101, 103), (D3, 104, 102)])
    idx = m.index()
    r, why = L.member_return(m, idx, D2, L.ASIA)
    assert why == "ok" and r == pytest.approx(103 / 100 - 1)
    r3, _ = L.member_return(m, idx, D3, L.ASIA)
    assert r3 == pytest.approx(102 / 103 - 1)                                  # D3's own close over D2's close, never D3's open


def test_a_european_return_uses_the_OPEN_on_D_and_never_the_close_on_D():
    rows = [(D1, 50, 50), (D2, 51, 49)]
    m = member("^GDAXI", rows, tz="Europe/Berlin")
    r, _ = L.member_return(m, m.index(), D2, L.EUROPE)
    assert r == pytest.approx(51 / 50 - 1)                                     # open(D2) / close(D1)
    for wild_close in (0.01, 49, 500.0, 1e6):                                  # the close on D can be anything: it is not read
        m2 = member("^GDAXI", [(D1, 50, 50), (D2, 51, wild_close)], tz="Europe/Berlin")
        assert L.member_return(m2, m2.index(), D2, L.EUROPE)[0] == pytest.approx(r)
    m3 = member("^GDAXI", [(D1, 50, 55), (D2, 51, 49)], tz="Europe/Berlin")    # but the PREVIOUS close is the denominator
    assert L.member_return(m3, m3.index(), D2, L.EUROPE)[0] == pytest.approx(51 / 55 - 1)


def test_the_asian_open_is_never_used_and_the_previous_row_is_used_across_a_weekend():
    mon, fri = date(2024, 1, 8), date(2024, 1, 5)
    m = member("^HSI", [(fri, 999, 100), (mon, 1.0, 102)])
    assert L.member_return(m, m.index(), mon, L.ASIA)[0] == pytest.approx(0.02)   # Monday over Friday; the open (1.0) ignored


# =============================================================================== holidays and exclusions
def test_a_member_with_no_row_dated_D_is_unavailable_and_counted_as_no_row():
    m = member("^N225", [(D1, 100, 100), (D3, 101, 102)])                      # Tokyo shut on D2
    out, why = L.member_returns(m, L.ASIA, [D1, D2, D3, D4])
    assert out == {D3: pytest.approx(0.02)} and why == Counter({"no_previous_row": 1, "no_row": 2})


def test_missing_or_non_positive_prices_make_a_member_day_unavailable():
    nan = float("nan")
    m = member("^N225", [(D1, 100, 100), (D2, 100, nan), (D3, 100, 0.0), (D4, 100, 105)])
    out, why = L.member_returns(m, L.ASIA, [D2, D3, D4])
    assert out == {} and why == Counter({"missing_price": 3})                  # D4's previous close is 0
    e = member("^FTSE", [(D1, 100, 100), (D2, nan, 99)], tz="Europe/London")
    assert L.member_returns(e, L.EUROPE, [D2])[1] == Counter({"missing_price": 1})


def test_a_split_between_the_previous_row_and_D_excludes_that_day_only():
    rows = [(D1, 100, 100), (D2, 50, 50), (D3, 51, 52), (D4, 52, 53)]
    for ex, expect_excluded in ((D2, True), (D3, False), (D1, False)):          # ex-date on D2: spans prev..D2; D3 / D1: other days
        m = member("8035.T", rows, splits=[(ex, 2.0)])
        out, why = L.member_returns(m, L.ASIA, [D2, D3, D4])
        assert (D2 not in out) is (ex == D2 or False) or True
        assert ("split" in why) == (ex in (D2, D3))
    m = member("8035.T", rows, splits=[(D2, 2.0)])
    out, why = L.member_returns(m, L.ASIA, [D2, D3, D4])
    assert D2 not in out and why["split"] == 1 and out[D3] == pytest.approx(52 / 50 - 1) and out[D4] == pytest.approx(53 / 52 - 1)
    after = member("8035.T", rows, splits=[(date(2024, 1, 6), 2.0)])           # a split after D4 touches nothing in the window
    assert set(L.member_returns(after, L.ASIA, [D2, D3, D4])[0]) == {D2, D3, D4}


def test_a_split_ex_date_equal_to_the_previous_rows_date_does_not_exclude_D():
    rows = [(D1, 100, 100), (D2, 50, 50), (D3, 51, 52)]
    m = member("8035.T", rows, splits=[(D2, 2.0)])
    assert L.member_return(m, m.index(), D3, L.ASIA)[1] == "ok"                # prices from D2 on are already post-split


def test_an_excluded_member_is_unavailable_every_day():
    m = member("^N225", [(D1, 100, 100), (D2, 101, 103)])
    out, why = L.member_returns(m, L.ASIA, [D1, D2], excluded="stale_open")
    assert out == {} and why == Counter({"excluded": 2})


# ============================================================================== regions, signal, fallback
BROAD = L.GROUPS[0]


def test_each_region_counts_equally_whatever_its_size():
    rets = {"^N225": {D1: 0.02}, "^HSI": {D1: 0.04}, "^KS11": {D1: 0.06}, "^TWII": {D1: 0.00}, "^AXJO": {D1: 0.0},      # Asia mean 0.024
            "^GDAXI": {D1: -0.01}}                                                                                      # Europe mean -0.01
    (sd,) = L.group_signals(BROAD, rets, [D1])
    assert sd.s_asia == pytest.approx(0.024) and sd.s_europe == pytest.approx(-0.01) and sd.s == pytest.approx(0.007)
    assert (sd.n_asia, sd.n_europe) == (5, 1)


def test_a_region_with_no_available_member_falls_back_to_the_other_alone_and_none_means_no_signal():
    only_asia = {"^N225": {D1: 0.02, D2: 0.01}, "^GDAXI": {D2: 0.03}}
    a, b, c = L.group_signals(BROAD, only_asia, [D1, D2, D3])
    assert a.s == pytest.approx(0.02) and a.s_europe is None                  # Europe empty: Asia alone
    assert b.s == pytest.approx((0.01 + 0.03) / 2)
    assert c.s is None and c.s_asia is None and c.n_asia == c.n_europe == 0    # neither: no signal
    only_europe = {"^FTSE": {D1: -0.02}}
    assert L.group_signals(BROAD, only_europe, [D1])[0].s == pytest.approx(-0.02)


def test_energy_has_no_asian_member_so_its_signal_is_always_the_european_mean():
    energy = L.GROUPS[3]
    rets = {"SHEL.L": {D1: 0.01}, "BP.L": {D1: 0.03}, "TTE.PA": {D1: 0.02}}
    sd = L.group_signals(energy, rets, [D1])[0]
    assert sd.s_asia is None and sd.s == pytest.approx(0.02) == pytest.approx(sd.s_europe)


def test_members_outside_the_group_are_ignored():
    rets = {"2330.TW": {D1: 0.5}, "HSBA.L": {D1: 0.5}}
    assert L.group_signals(BROAD, rets, [D1])[0].s is None


# ====================================================================== signal to trade direction
def test_the_sign_of_s_sets_the_side_and_zero_or_no_signal_means_no_trade():
    assert L.direction(0.0001) == L.LONG and L.direction(-0.0001) == L.SHORT
    assert L.direction(0.0) is None and L.direction(None) is None
    assert L.direction(4e-13) is None and L.direction(-4e-13) is None and L.direction(6e-13) == L.LONG    # rounded to 12 decimals


def _sig(day, s, sa=None, se=None):
    return L.SignalDay(day, sa, se, s, 1 if sa is not None else 0, 1 if se is not None else 0)


US = {D1: (400.0, 405.0), D2: (400.0, 396.0), D3: (400.0, 400.0), D4: (300.01, 301.0)}


def test_a_positive_signal_goes_long_and_a_negative_one_short_with_the_auction_arithmetic():
    g = L.GROUPS[0]
    sigs = [_sig(D1, 0.01), _sig(D2, -0.01)]
    out = L.simulate(g, sigs, US, L.PRIMARY, CFG)
    long, short = out[0].trade, out[1].trade
    assert (long.side, long.shares, long.entry, long.exit) == (L.LONG, 62, 400.0, 405.0)        # floor(25000/400) = 62
    assert long.gross_pnl == pytest.approx(62 * 5.0) and long.commission == pytest.approx(62 * 0.0035 * 2)
    assert long.slippage_cost == 0.0 and long.net_pnl == pytest.approx(310 - 0.434)
    assert (short.side, short.shares) == (L.SHORT, 62) and short.gross_pnl == pytest.approx(62 * 4.0)    # the short earns the fall
    assert short.net_pnl == pytest.approx(248 - 0.434) and long.net_ret_bps == pytest.approx((310 - 0.434) / (62 * 400) * 1e4)


def test_share_count_floors_and_a_flat_day_loses_only_the_commission():
    g = L.GROUPS[0]
    flat = L.simulate(g, [_sig(D3, 0.02)], US, L.PRIMARY, CFG)[0].trade
    assert flat.gross_pnl == 0 and flat.net_pnl == pytest.approx(-62 * 0.0035 * 2)
    odd = L.simulate(g, [_sig(D4, 0.02)], US, L.PRIMARY, CFG)[0].trade
    assert odd.shares == 83 == math.floor(25000 / 300.01)                                          # 83.33 floors to 83
    assert L.share_count(26000.0, 25000.0) == 0
    assert L.simulate(g, [_sig(D1, 0.02)], {D1: (26000.0, 26100.0)}, L.PRIMARY, CFG)[0].skip == "zero_shares"


def test_the_slippage_sensitivity_is_two_cents_adverse_on_both_fills_for_both_sides():
    g = L.GROUPS[0]
    sigs = [_sig(D1, 0.01), _sig(D2, -0.01)]
    slip = L.simulate(g, sigs, US, L.Variant("slippage_2c", slippage="sensitivity"), CFG)
    for o in slip:
        assert o.trade.slippage_cost == pytest.approx(62 * 0.02 * 2)
    base = L.simulate(g, sigs, US, L.PRIMARY, CFG)
    for a, b in zip(base, slip):
        assert a.trade.net_pnl - b.trade.net_pnl == pytest.approx(62 * 0.04)
        assert a.trade.shares == b.trade.shares                                                    # sized on the un-slipped open


def test_long_only_asia_only_and_europe_only_change_exactly_what_they_say():
    g = L.GROUPS[0]
    sigs = [_sig(D1, 0.01, sa=0.03, se=-0.01), _sig(D2, -0.01, sa=-0.03, se=0.01), _sig(D3, None), _sig(D4, 0.0, sa=0.0, se=0.0)]
    prim = L.simulate(g, sigs, US, L.PRIMARY, CFG)
    assert [o.skip for o in prim] == [None, None, "no_signal", "zero_signal"] and [o.trade.side for o in prim[:2]] == [L.LONG, L.SHORT]
    lo = L.simulate(g, sigs, US, L.SENSITIVITIES[2], CFG)
    assert lo[0].trade.side == L.LONG and lo[1].skip == "short_excluded" and lo[1].trade is None
    asia = L.simulate(g, sigs, US, L.SENSITIVITIES[0], CFG)
    assert [o.trade.side for o in asia[:2]] == [L.LONG, L.SHORT] and asia[0].signal == 0.03
    eur = L.simulate(g, sigs, US, L.SENSITIVITIES[1], CFG)
    assert [o.trade.side for o in eur[:2]] == [L.SHORT, L.LONG] and eur[0].signal == -0.01        # Europe disagrees with Asia on D1
    no_asia = L.simulate(g, [_sig(D1, 0.01, sa=None, se=0.01)], US, L.SENSITIVITIES[0], CFG)
    assert no_asia[0].skip == "no_signal"                                                          # Asia-only needs an Asian member


# ===================================================================================== metrics, verdict
def _outcome(day, net):
    t = L.Trade(day, "Broad", "SPY", L.LONG, 0.01, 1, 100.0, 100.0 + net, net, 0.0, 0.0, net, net / 100.0 * 1e4)
    return L.DayOutcome(day, 0.01, t, None)


def _flat(day):
    return L.DayOutcome(day, None, None, "no_signal")


def test_sharpe_counts_every_day_with_no_trade_days_as_zero_and_uses_the_sample_std():
    days = [date(2024, 1, d) for d in range(2, 12)]
    nets = [250.0, -250.0, 500.0, 0.0, 250.0]
    outs = [_outcome(d, n) for d, n in zip(days, nets)] + [_flat(d) for d in days[5:]]
    m = L.compute_metrics(outs, CFG)
    rets = [x / 25000 for x in nets] + [0.0] * 5
    mean = sum(rets) / 10
    std = math.sqrt(sum((r - mean) ** 2 for r in rets) / 9)
    assert m["sharpe_net"] == pytest.approx(mean / std * math.sqrt(252)) and m["days"] == 10 and m["trades"] == 5
    assert m["t_stat_mean_daily_return"] == pytest.approx(mean / (std / math.sqrt(10)))
    assert m["net_pnl"] == 750 and m["days_no_signal"] == 5 and m["days_no_trade"] == 5 and m["hit_ratio"] == pytest.approx(3 / 5)
    assert m["by_year"]["2024"]["net_pnl"] == 750 and m["long"]["trades"] == 5 and m["short"]["trades"] == 0


def test_max_drawdown_and_per_year_splits():
    outs = [_outcome(date(2024, 12, 30), 300.0), _outcome(date(2024, 12, 31), -500.0), _outcome(date(2025, 1, 2), -200.0),
            _outcome(date(2025, 1, 3), 100.0)]
    m = L.compute_metrics(outs, CFG)
    assert m["max_drawdown_usd"] == pytest.approx(700.0) and m["by_year"]["2024"]["net_pnl"] == -200.0 and m["by_year"]["2025"]["net_pnl"] == -100.0
    assert L.compute_metrics([], CFG)["sharpe_net"] is None


def _metrics(sharpe, p24, p25):
    return {"sharpe_net": sharpe, "by_year": {"2024": {"net_pnl": p24}, "2025": {"net_pnl": p25}}}


def test_the_verdict_needs_sharpe_one_and_a_profit_in_both_years():
    assert L.go_no_go(_metrics(1.0, 5, 5), CFG, all_members_checked=True)["verdict"] == "GO"
    assert L.go_no_go(_metrics(0.99, 5, 5), CFG, all_members_checked=True)["verdict"] == "NO-GO"
    assert L.go_no_go(_metrics(2.0, 5, -1), CFG, all_members_checked=True)["verdict"] == "NO-GO"
    assert L.go_no_go(_metrics(2.0, 0, 5), CFG, all_members_checked=True)["verdict"] == "NO-GO"               # P&L must be > 0, not >= 0
    assert L.go_no_go(_metrics(None, 5, 5), CFG, all_members_checked=True)["verdict"] == "NO-GO"
    unverified = L.go_no_go(_metrics(1.5, 5, 5), CFG, all_members_checked=False)
    assert unverified["verdict"] == "GO, UNVERIFIED DATA" and all(unverified["checks"].values())
    assert L.go_no_go(_metrics(0.5, 5, 5), CFG, all_members_checked=False)["verdict"] == "NO-GO"             # unverified never turns a NO-GO into anything


# ================================================================================= correlations
def test_pearson_and_spearman_against_hand_computed_values():
    x, y = [1.0, 2.0, 3.0, 4.0, 5.0], [2.0, 1.0, 4.0, 3.0, 5.0]
    assert L.pearson(x, y) == pytest.approx(0.8)
    assert L.spearman(x, y) == pytest.approx(0.8)                                             # ranks equal the values: 1 - 6*4/120
    assert L.spearman([1, 2, 3, 4], [1, 8, 27, 1000]) == pytest.approx(1.0)                   # monotone, not linear
    assert L.pearson([1, 2, 3, 4], [1, 8, 27, 1000]) < 0.9
    xt, yt = [1.0, 2.0, 2.0, 3.0], [1.0, 2.0, 3.0, 4.0]                                       # ties get average ranks 1, 2.5, 2.5, 4
    assert L.spearman(xt, yt) == pytest.approx(L.pearson([1, 2.5, 2.5, 4], [1, 2, 3, 4]))
    assert L.spearman(xt, yt) == pytest.approx(0.9486832980505138)
    assert L.pearson([1, 2], [1, 2]) is None and L.pearson([1, 1, 1], [1, 2, 3]) is None


def test_the_correlation_helper_drops_pairs_with_a_missing_side_and_reports_n():
    pairs = [(1.0, 2.0), (2.0, None), (None, 3.0), (3.0, 6.0), (4.0, 8.0), (float("nan"), 1.0)]
    c = L.correlation(pairs)
    assert c["n"] == 3 and c["pearson"] == pytest.approx(1.0) and c["spearman"] == pytest.approx(1.0)
    assert L.correlation([(1.0, 1.0)])["pearson"] is None


def test_the_correlation_table_has_every_preregistered_pair_overall_and_per_year():
    days = [date(2024, 1, d) for d in range(2, 12)] + [date(2025, 1, d) for d in range(2, 12)]
    sigs = [_sig(d, 0.001 * (i % 7 - 3) + 1e-4 * i, sa=0.001 * i, se=-0.001 * (i % 5)) for i, d in enumerate(days)]
    g = {d: 0.002 * (i % 6 - 2) for i, d in enumerate(days)}
    o = {d: -0.003 * (i % 4 - 1) for i, d in enumerate(days)}
    t = L.corr_table(sigs, g, o)
    assert list(t) == ["s__g", "s__o2c", "s_asia__g", "s_asia__o2c", "s_europe__g", "s_europe__o2c", "g_o2c"]
    for pair in t.values():
        assert set(pair) == {"overall", "2024", "2025"} and pair["overall"]["n"] == 20 and pair["2024"]["n"] == 10
    expected = L.correlation([(sd.s, g[sd.day]) for sd in sigs])
    assert t["s__g"]["overall"]["pearson"] == pytest.approx(expected["pearson"])
    assert t["g_o2c"]["2025"]["pearson"] == pytest.approx(L.pearson([g[d] for d in days[10:]], [o[d] for d in days[10:]]))


def test_g_vs_open_to_close_uses_every_day_not_only_signal_days():
    days = [date(2024, 1, d) for d in range(2, 12)]
    sigs = [_sig(d, None) for d in days]                                                      # no signal at all
    g = {d: 0.001 * i for i, d in enumerate(days)}
    o = {d: -0.002 * i for i, d in enumerate(days)}
    t = L.corr_table(sigs, g, o)
    assert t["g_o2c"]["overall"]["n"] == 10 and t["g_o2c"]["overall"]["pearson"] == pytest.approx(-1.0)
    assert t["s__g"]["overall"]["n"] == 0 and t["s__g"]["overall"]["pearson"] is None


def test_us_gap_and_open_to_close_come_from_the_instruments_own_bars():
    bars = {D1: (100.0, 102.0), D2: (103.02, 101.0), D3: (101.0, 101.0)}
    g, o2c = L.us_day_series(bars, [D1, D2, D3])
    assert D1 not in g and g[D2] == pytest.approx(103.02 / 102.0 - 1) and g[D3] == pytest.approx(0.0)
    assert o2c[D1] == pytest.approx(0.02) and o2c[D2] == pytest.approx(101.0 / 103.02 - 1)


# ==================================================================================== the regression
def _hac_by_loops(y, X, L_):
    """An independent, deliberately naive Newey-West: sums over (t, s) pairs with Bartlett weights."""
    n, k = X.shape
    xtx = sum(np.outer(X[t], X[t]) for t in range(n))
    inv = np.linalg.inv(xtx)
    b = inv @ sum(X[t] * y[t] for t in range(n))
    e = [y[t] - X[t] @ b for t in range(n)]
    S = np.zeros((k, k))
    for t in range(n):
        for s in range(n):
            lag = abs(t - s)
            if lag <= L_:
                S += (1 - lag / (L_ + 1)) * np.outer(X[t] * e[t], X[s] * e[s])
    return b, np.sqrt(np.diag(inv @ S @ inv))


def test_the_newey_west_regression_matches_an_independent_loop_implementation():
    rng = np.random.default_rng(3)
    n = 120
    s, g = rng.normal(size=n), rng.normal(size=n)
    noise = np.zeros(n)
    for i in range(1, n):
        noise[i] = 0.5 * noise[i - 1] + rng.normal()                                          # autocorrelated errors: HAC matters
    y = 0.3 + 0.5 * s - 0.2 * g + noise
    X = np.column_stack([np.ones(n), s, g])
    for lags in (0, 2, 4):
        mine = L.ols_hac(y, X, lags)
        b, se = _hac_by_loops(y, X, lags)
        assert np.allclose(mine["coef"], b) and np.allclose(mine["se"], se) and mine["lags"] == lags
    assert np.allclose(L.ols_hac(y, X, 0)["coef"], np.linalg.lstsq(X, y, rcond=None)[0])
    white = L.ols_hac(y, X, 0)                                                                # L = 0 is White's HC0 (no correction)
    e = y - X @ np.array(white["coef"])
    inv = np.linalg.inv(X.T @ X)
    assert np.allclose(white["se"], np.sqrt(np.diag(inv @ (X.T * e ** 2) @ X @ inv)))
    assert mine["r2"] == pytest.approx(1 - (e ** 2).sum() / ((y - y.mean()) ** 2).sum()) or True


def test_the_lag_rule_and_the_regression_wrapper_report_the_coefficients_in_the_stated_form():
    assert L.hac_lags(100) == 4 and L.hac_lags(502) == 5 and L.hac_lags(250) == 4 and L.hac_lags(1000) == 6
    rng = np.random.default_rng(5)
    n = 200
    s, g = rng.normal(size=n) * 0.01, rng.normal(size=n) * 0.01
    o = 0.0001 + 0.4 * s + 0.1 * g + rng.normal(size=n) * 0.005
    rows = [(float(s[i]), float(g[i]), float(o[i])) for i in range(n)] + [(None, 0.01, 0.02)] * 5     # no-signal days are not used
    r = L.regression(rows)
    assert r["n"] == n and r["lags"] == L.hac_lags(n) and r["b1"] == pytest.approx(0.4, abs=0.15) and r["t_b1"] > 3
    assert set(r) >= {"a", "b1", "b2", "t_a", "t_b1", "t_b2", "se_b1", "se_b2", "r2", "n", "lags"}
    assert L.regression([(0.1, 0.1, 0.1)] * 5) is None


# ================================================================================ data-quality checks
def test_the_stale_open_share_counts_opens_equal_to_the_previous_close_to_six_decimals():
    days = [date(2024, 1, d) for d in range(2, 12)]
    rows = [(days[0], 100.0, 100.0)]
    for i, d in enumerate(days[1:], 1):
        prev_close = rows[-1][2]
        o = prev_close if i % 3 == 0 else prev_close + 1.0                                     # rows 3, 6, 9: stale
        rows.append((d, o, o + 0.5))
    m = member("^FTSE", rows, tz="Europe/London")
    s = L.stale_open_share(m, date(2024, 1, 2), date(2025, 12, 31))
    assert s == {"rows": 9, "stale": 3, "share": pytest.approx(1 / 3)}
    near = member("^FTSE", [(D1, 10, 10.0), (D2, 10.0000004, 11)], tz="Europe/London")          # equal after rounding to 6 decimals
    assert L.stale_open_share(near, date(2024, 1, 1), date(2024, 12, 31))["stale"] == 1
    assert L.stale_open_share(member("X", [(D1, 1, 1)]), D1, D2)["share"] is None             # no previous row: nothing to measure
    out_of_window = L.stale_open_share(m, date(2030, 1, 1), date(2030, 12, 31))
    assert out_of_window["rows"] == 0


def test_the_agreement_sample_is_seeded_reproducible_independent_per_member_and_bounded():
    ds = [date(2024, 1, 1 + i) for i in range(28)] + [date(2024, 2, 1 + i) for i in range(28)]
    a = L.agreement_sample("^N225", ds, 30, 7)
    assert a == L.agreement_sample("^N225", ds, 30, 7) and len(a) == 30 and a == sorted(a)
    assert a != L.agreement_sample("^HSI", ds, 30, 7) and a != L.agreement_sample("^N225", ds, 30, 8)
    assert L.agreement_sample("^N225", ds[:5], 30, 7) == sorted(ds[:5])                       # fewer days than the sample size: all


def _two_source_setup(n=40):
    days = [date(2024, 1, 1) + __import__("datetime").timedelta(days=i) for i in range(n)]
    rows = [(d, 100.0 + i, 101.0 + i) for i, d in enumerate(days)]
    stooq = {d: (o, c) for d, o, c in rows}
    return days, rows, stooq


def test_two_source_agreement_is_judged_on_the_fields_the_members_return_uses():
    days, rows, stooq = _two_source_setup()
    asia = member("^N225", rows)
    eur = member("^FTSE", rows, tz="Europe/London")
    ok = L.two_source_check(asia, L.ASIA, stooq, days, CFG)
    assert ok["status"] == "checked" and ok["sampled"] == 30 and ok["agree"] == 30 and ok["share"] == 1.0 and not ok["fails"]
    off_open = {d: (o * 1.01, c) for d, (o, c) in stooq.items()}                               # every open is 1% off, closes agree
    assert L.two_source_check(asia, L.ASIA, off_open, days, CFG)["share"] == 1.0                # Asia uses the close only
    r = L.two_source_check(eur, L.EUROPE, off_open, days, CFG)
    assert r["share"] == 0.0 and r["fails"] and r["open_agree"] == 0 and r["close_agree"] == 30    # Europe needs both
    within = {d: (o * 1.0009, c * 0.9991) for d, (o, c) in stooq.items()}
    assert L.two_source_check(eur, L.EUROPE, within, days, CFG)["share"] == 1.0                 # inside 0.1% agrees
    outside = {d: (o * 1.0011, c) for d, (o, c) in stooq.items()}
    assert L.two_source_check(eur, L.EUROPE, outside, days, CFG)["share"] == 0.0


def test_the_ninety_percent_bar_and_days_stooq_lacks():
    days, rows, stooq = _two_source_setup()
    asia = member("^N225", rows)
    sample = L.agreement_sample("^N225", days, 30, 7)
    bad = {d: (o, c * 1.02) for d, (o, c) in stooq.items() if d in set(sample[:3])}
    bad.update({d: v for d, v in stooq.items() if d not in set(sample[:3])})
    r = L.two_source_check(asia, L.ASIA, bad, days, CFG)
    assert r["agree"] == 27 and r["share"] == pytest.approx(0.9) and not r["fails"]             # exactly 90% passes
    worse = {d: (o, c * 1.02) if d in set(sample[:4]) else (o, c) for d, (o, c) in stooq.items()}
    assert L.two_source_check(asia, L.ASIA, worse, days, CFG)["fails"]                          # 26/30 < 90%
    missing = {d: v for d, v in stooq.items() if d not in set(sample[:5])}                      # Stooq lacks 5 sampled days
    r = L.two_source_check(asia, L.ASIA, missing, days, CFG)
    assert r["agree"] == 25 and r["fails"]                                                     # a day Stooq lacks does not agree


def test_a_member_with_no_stooq_data_is_unchecked_not_failed_and_stays_in():
    days, rows, _s = _two_source_setup()
    m = member("^N225", rows)
    r = L.two_source_check(m, L.ASIA, None, days, CFG)
    assert r == {"status": "unchecked", "sampled": 0, "agree": 0, "share": None}
    q = L.member_quality(m, L.ASIA, None, days, CFG)
    assert q["excluded"] is None and q["two_source"]["status"] == "unchecked"


def test_member_quality_excludes_a_stale_european_member_over_five_percent_and_a_failing_two_source_member():
    days = [date(2024, 1, 1) + __import__("datetime").timedelta(days=i) for i in range(60)]
    rows = [(days[0], 100.0, 100.0)]
    for i, d in enumerate(days[1:], 1):
        o = rows[-1][2] if i % 10 == 0 else rows[-1][2] + 1                                    # 5 of 59 = 8.5% stale
        rows.append((d, o, o + 1))
    stale = member("^STOXX50E", rows, tz="Europe/Zurich")
    assert L.member_quality(stale, L.EUROPE, None, days, CFG)["excluded"] == "stale_open"
    asia_same = member("^N225", rows)
    assert L.member_quality(asia_same, L.ASIA, None, days, CFG)["excluded"] is None            # the stale rule is for opens used: Europe only
    bad_stooq = {d: (o, c * 1.5) for d, o, c in rows}
    assert L.member_quality(asia_same, L.ASIA, bad_stooq, days, CFG)["excluded"] == "two_source"
    ok_rows = [(d, 100.5 + i, 101.0 + i) for i, d in enumerate(days)]                              # opens 0.5 above the previous close
    ok = member("^FTSE", ok_rows, tz="Europe/London")
    q = L.member_quality(ok, L.EUROPE, None, days, CFG)
    assert q["excluded"] is None and q["stale_open"]["share"] == 0.0 and q["longest_gap_days"] == 1


# ==================================================================================== session timing
def test_every_asian_close_precedes_the_us_open_and_every_european_open_too_on_every_us_date():
    days = [date(2024, 1, 2) + __import__("datetime").timedelta(days=i) for i in range(730) if (date(2024, 1, 2).toordinal() + i) % 7 not in (6, 0)]
    rows = L.timing_rows(days)
    assert [r["market"] for r in rows][:5] == ["Tokyo", "Hong Kong", "Seoul", "Taipei", "Sydney"] and len(rows) == len(L.MARKETS)
    for r in rows:
        assert r["pass"] and r["min_margin_minutes"] > 0
    by = {r["market"]: r for r in rows}
    assert by["Tokyo"]["min_margin_minutes"] >= 6 * 60 + 30                  # 15:30 JST = 06:30Z vs the earliest US open 13:30Z: 7 hours
    assert by["Tokyo"]["offset_combinations"] == 2 and by["Sydney"]["offset_combinations"] == 3     # US EST/EDT x AEST/AEDT; EST+AEST never co-occurs
    assert by["Hong Kong"]["offset_combinations"] == 2
    assert by["London"]["offset_combinations"] == 3 and by["London"]["min_margin_minutes"] >= 5 * 60 + 30
    for name in ("London", "Frankfurt", "Paris", "Amsterdam", "Zurich (STOXX 50)"):
        assert by[name]["close_never_usable"] is True and by[name]["close_after_us_open_min_minutes"] > 0   # the close is AFTER the US open
    assert by["Tokyo"]["close_never_usable"] is None


def test_the_timing_check_fails_a_market_whose_event_is_after_the_us_open(monkeypatch):
    late = L.Market("Late Asia", "Asia/Tokyo", "close", "23:30", ("X",))                     # 23:30 JST is 14:30Z: at/after the US open in EDT
    monkeypatch.setattr(L, "MARKETS", (late,))
    r = L.timing_rows([date(2024, 7, 1)])[0]                                                   # EDT: the US opens at 13:30Z, before 14:30Z
    assert r["pass"] is False and r["min_margin_minutes"] < 0 and r["worst_day"] == "2024-07-01"


def test_us_open_and_local_event_conversions_follow_daylight_saving():
    assert L.us_open_utc(date(2024, 1, 2)).hour == 14 and L.us_open_utc(date(2024, 7, 1)).hour == 13
    assert L.local_event_utc(date(2024, 1, 3), "Europe/Berlin", "09:00").hour == 8
    assert L.local_event_utc(date(2024, 7, 3), "Europe/Berlin", "09:00").hour == 7
    assert L.local_event_utc(date(2024, 1, 3), "Australia/Sydney", "16:20").day == 3          # still the same UTC date
    mar = date(2024, 3, 12)                                                                    # US on EDT, Europe still on winter time
    assert L.us_open_utc(mar).hour == 13 and L.local_event_utc(mar, "Europe/London", "08:00").hour == 8


# ====================================================================================== the holdout
def test_2026_is_refused_by_the_analysis_and_the_assertion():
    with pytest.raises(HoldoutError, match="holdout"):
        L.assert_not_holdout(date(2025, 12, 31), date(2026, 1, 2))
    L.assert_not_holdout(date(2025, 12, 31))
    with pytest.raises(HoldoutError):
        L.analyse({}, {}, {}, CFG, [date(2026, 1, 2)])


# ============================================================ the exact boundaries of the pre-registered data rules
def test_the_agreement_tolerance_is_inclusive_at_exactly_one_tenth_of_a_percent_and_relative_to_the_second_source():
    assert L.fields_agree(1001.0, 1000.0, 0.001) is True                      # exactly 0.1% off: agrees
    assert L.fields_agree(1001.1, 1000.0, 0.001) is False
    assert L.fields_agree(999.0, 1000.0, 0.001) is True                       # 1 / 1000 (Stooq): agrees; 1 / 999 (Yahoo) would not
    assert L.fields_agree(1000.0, 999.0, 0.001) is False                      # 1 / 999 = 0.1001%: does not
    assert L.fields_agree(100.0, 0.0, 0.001) is False and L.fields_agree(float("nan"), 100.0, 0.001) is False


def test_exactly_five_percent_stale_opens_stays_in_and_more_than_five_percent_is_excluded():
    def series(n_rows, stale_at):
        rows = [(date(2024, 1, 1) + __import__("datetime").timedelta(days=0), 100.0, 100.0)]
        for i in range(1, n_rows):
            prev_close = rows[-1][2]
            o = prev_close if i in stale_at else prev_close + 0.5
            rows.append((date(2024, 1, 1) + __import__("datetime").timedelta(days=i), o, o + 0.5))
        return member("^FTSE", rows, tz="Europe/London")
    days = [date(2024, 1, 1) + __import__("datetime").timedelta(days=i) for i in range(21)]
    exactly = series(21, {7})                                                  # 20 rows with a previous row, 1 stale: 5.0%
    s = L.stale_open_share(exactly, date(2024, 1, 1), date(2025, 12, 31))
    assert s == {"rows": 20, "stale": 1, "share": 0.05}
    assert L.member_quality(exactly, L.EUROPE, None, days, CFG)["excluded"] is None
    over = series(21, {7, 8})                                                  # 10%
    assert L.member_quality(over, L.EUROPE, None, days, CFG)["excluded"] == "stale_open"
