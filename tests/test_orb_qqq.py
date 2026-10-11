"""ORB on QQQ on a constructed market with known answers (no network, no data layer).

Daily bars are 400 / 403 / 397 / 400 every day (true range 6.00), except 2023-12-13, whose range is 6.70. That bar is the OLDEST of the
14 prior days of 2024-01-02 only, so the ATR is (6.70 + 13 x 6.00) / 14 = 6.05 on 01-02 (stop distance 0.605 -> the stop rounds AWAY from
the entry to a distance of 0.61) and exactly 6.00 on 01-03. The 2024-01-03 daily bar has a range of 20 (it is the NEWEST prior bar of every later day's window), so the ATR is
(20 + 13 x 6.00) / 14 = 7.00 from 01-04 on (stop distance 0.70). Opening bar (up): O 400.00 / H 401.00 / L 399.50 /
C 400.80 -> long trigger 401.00, stop 400.39 on 01-02 and 400.40 later, 62 shares (the $25,000 cap: 25,000 / 401 = 62.3; the 1% risk limit
would allow 410-416). Opening bar (down): O 400.00 / H 400.50 / L 399.00 / C 399.20 -> short trigger 399.00, stop 399.60.

Days: 01-02 clean long (+1.00); 01-03 ambiguous entry minute (reaches trigger and stop); 01-04 short; 01-05 doji; 01-08 incomplete
opening bar (09:32 missing); 01-09 never triggers; 01-10 no minute bars; 01-11 gap entry at 401.50.
"""
import json
import os
import re
import sys
from datetime import date, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from backtest import engine as E  # noqa: E402
from backtest import orb_qqq as Q  # noqa: E402
from backtest import orb_qqq_verify as V  # noqa: E402
from backtest import samebar  # noqa: E402
from backtest.data.bars import HoldoutError  # noqa: E402
from orb.config import OrbConfig  # noqa: E402
from orb.signals import LONG, SHORT  # noqa: E402

PREREG = os.path.join(os.path.dirname(__file__), "..", "docs", "specs", "ORB_QQQ_Preregistration.md")
PHASE2 = os.path.join(os.path.dirname(__file__), "..", "docs", "specs", "ORB_Phase2_Preregistration.md")
CFG = Q.qqq_config()
ET = "US/Eastern"
D = {n: date(2024, 1, d) for n, d in zip("ABCDEFGH", (2, 3, 4, 5, 8, 9, 10, 11))}


def t(h, m):
    return h * 60 + m


def weekdays(a, b):
    out, d = [], a
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


CAL = weekdays(date(2023, 12, 1), date(2024, 1, 11))
DAILY = {d: (400.0, 403.0, 397.0, 400.0) for d in CAL}
DAILY[date(2023, 12, 13)] = (400.0, 403.7, 397.0, 400.0)                                      # true range 6.70: the oldest bar of 01-02's window
DAILY[date(2024, 1, 3)] = (400.0, 410.0, 390.0, 400.0)                                         # true range 20: the newest prior bar for every window day from 01-04

UP = {570: (400.00, 400.50, 399.50, 400.40, 1000), 571: (400.40, 401.00, 400.30, 400.90, 1000), 572: (400.90, 400.95, 400.60, 400.70, 1000),
      573: (400.70, 400.90, 400.50, 400.80, 1000), 574: (400.80, 400.85, 400.60, 400.80, 1000)}
DOWN = {570: (400.00, 400.50, 399.80, 399.90, 1000), 571: (399.90, 400.00, 399.40, 399.50, 1000), 572: (399.50, 399.70, 399.20, 399.30, 1000),
        573: (399.30, 399.50, 399.00, 399.40, 1000), 574: (399.40, 399.50, 399.10, 399.20, 1000)}


def bar(o, h, lo, c, v=1000):
    return (o, h, lo, c, v)


def _day(opening, rest):
    return {**opening, **rest}


MINUTE = {
    D["A"]: _day(UP, {575: bar(400.9, 401.0, 400.85, 400.95), 700: bar(401.0, 401.5, 400.9, 401.2), 959: bar(401.9, 402.1, 401.8, 402.0)}),
    D["B"]: _day(UP, {575: bar(400.9, 401.1, 400.3, 400.95), 700: bar(400.9, 401.2, 400.6, 401.0), 959: bar(402.4, 402.6, 402.3, 402.5)}),
    D["C"]: _day(DOWN, {575: bar(399.1, 399.3, 398.9, 399.0), 959: bar(398.1, 398.2, 397.9, 398.0)}),
    D["D"]: _day({**UP, 574: bar(400.8, 400.85, 400.6, 400.0)}, {575: bar(400.9, 401.5, 400.85, 401.2)}),                # doji: close == open
    D["E"]: _day({m: b for m, b in UP.items() if m != 572}, {575: bar(400.9, 401.5, 400.85, 401.2)}),
    D["F"]: _day(UP, {575: bar(400.5, 400.9, 400.4, 400.6), 959: bar(400.6, 400.8, 400.5, 400.7)}),
    D["H"]: _day(UP, {575: bar(401.5, 401.8, 401.4, 401.6), 959: bar(402.9, 403.1, 402.8, 403.0)}),
}
# IEX sees a subset of the trades: day A three minutes (range inside the SIP range), day B a down bar (direction disagrees)
IEX = {D["A"]: {570: bar(400.00, 400.40, 399.60, 400.20), 571: bar(400.20, 400.95, 400.20, 400.70), 573: bar(400.70, 400.80, 400.50, 400.75)},
       D["B"]: {570: bar(400.00, 400.30, 399.70, 399.90), 574: bar(399.90, 400.00, 399.70, 399.80)}}


def agg(bars):
    seq = [bars[m] for m in range(570, 575)]
    return (seq[0][0], max(b[1] for b in seq), min(b[2] for b in seq), seq[-1][3])


SIP5 = {d: agg(b) for d, b in MINUTE.items() if all(m in b for m in range(570, 575))}
SIP5[D["C"]] = (SIP5[D["C"]][0], SIP5[D["C"]][1] + 0.05, SIP5[D["C"]][2], SIP5[D["C"]][3])            # one disagreement with Alpaca's own 5-minute bar


class StubLayer:
    """The data-layer calls the study makes, answered from the constructed market (offline flag recorded)."""

    def __init__(self, *_a, **_k):
        self.calls = []

    def trading_days(self, start, end, offline=False, **_k):
        s, e = pd.Timestamp(start).date(), pd.Timestamp(end).date()
        return [d for d in CAL if s <= d <= e]

    def cache_stats(self):
        return {"requests_made": 0}

    def get_bars(self, symbols, start, end, timeframe="1Min", feed="sip", *, window=None, offline=False, **_k):
        self.calls.append((tuple(symbols), timeframe, feed, window, offline))
        assert list(symbols) == ["QQQ"]
        s, e = pd.Timestamp(start).date(), pd.Timestamp(end).date()
        rows = []
        if timeframe == "1Day":
            src = {d: (o, h, lo, c, 1) for d, (o, h, lo, c) in DAILY.items()}
            rows = [(d, 570 - 570, *v) for d, v in src.items() if s <= d <= e]
            rows = [{"symbol": "QQQ", "ts": pd.Timestamp(d, tz=ET), "open": v[0], "high": v[1], "low": v[2], "close": v[3], "volume": 1} for d, _m, *v in rows]
        elif timeframe == "5Min":
            rows = [{"symbol": "QQQ", "ts": pd.Timestamp(d, tz=ET) + pd.Timedelta(minutes=570), "open": b[0], "high": b[1], "low": b[2], "close": b[3], "volume": 1}
                    for d, b in SIP5.items() if s <= d <= e]
        else:
            src = MINUTE if feed == "sip" else IEX
            for d, bars in src.items():
                if not (s <= d <= e):
                    continue
                for m, b in bars.items():
                    if window and not (t(*map(int, window[0].split(":"))) <= m < t(*map(int, window[1].split(":")))):
                        continue
                    rows.append({"symbol": "QQQ", "ts": pd.Timestamp(d, tz=ET) + pd.Timedelta(minutes=m), "open": b[0], "high": b[1], "low": b[2],
                                 "close": b[3], "volume": b[4] if len(b) > 4 else 1000})
        return pd.DataFrame(rows, columns=["symbol", "ts", "open", "high", "low", "close", "volume"])


def _inputs():
    return Q.load_inputs(StubLayer(), "2024-01-02", "2024-01-11")


# =========================================================================== the pre-registration
def _block(path):
    return json.loads(re.search(r"```json\n(.*?)\n```", open(path, encoding="utf-8").read(), re.S).group(1))


def test_the_config_equals_the_machine_readable_block_and_inherits_everything_else_from_phase2():
    b, p2 = _block(PREREG), _block(PHASE2)
    cfg = Q.qqq_config()
    assert {k: getattr(cfg, k) for k in b["config"]} == b["config"] and b["symbol"] == Q.SYMBOL == "QQQ" and b["spec_version"] == Q.SPEC_VERSION
    same = [k for k in b["config"] if b["config"][k] == p2[k]]
    changed = [k for k in b["config"] if b["config"][k] != p2[k]]
    assert sorted(changed) == sorted(b["changed_from_phase2"]) == ["position_slots", "slippage_cents_per_side"]
    assert b["config"]["position_slots"] == 1 and b["config"]["slippage_cents_per_side"] == 1.0 and len(same) == len(b["config"]) - 2
    assert set(b["config"]) | {"spec_version"} == set(p2)                                               # no field is left out of the block
    assert cfg.diff_from_default() == {"position_slots": 1, "slippage_cents_per_side": 1.0}
    assert set(b["unused_in_qqq_run"]) <= set(b["config"])


def test_the_variants_are_exactly_the_preregistered_ones_and_each_changes_one_thing():
    assert [v.name for v in Q.ALL_VARIANTS] == ["primary"] + _block(PREREG)["sensitivities"]
    base = Q.qqq_config()
    assert {v.name: v.config(base).diff_from_default() for v in Q.SENSITIVITIES} == {
        "slippage_0c": {"position_slots": 1, "slippage_cents_per_side": 0.0}, "slippage_2c": {"position_slots": 1},                                    # 2.0 is the Phase 2 default
        
        "slippage_5c": {"position_slots": 1, "slippage_cents_per_side": 5.0}, "long_only": {"position_slots": 1, "slippage_cents_per_side": 1.0, "allow_shorts": False},
        "same_bar_optimistic": {"position_slots": 1, "slippage_cents_per_side": 1.0}}
    assert [v.name for v in Q.SENSITIVITIES if v.optimistic] == ["same_bar_optimistic"] and Q.PRIMARY.rule() is None
    assert Q.SENSITIVITIES[-1].rule() is samebar.ignore_stop_rule


def test_the_preregistration_states_the_inheritance_and_the_rules_word_for_word():
    text = open(PREREG, encoding="utf-8").read()
    assert "Everything not listed in section 1 is inherited unchanged from `docs/specs/ORB_Phase2_Preregistration.md`" in text
    for row in ("| Instrument | **QQQ only**: no universe filter, no Relative Volume ranking. Trade every day the rules allow |",
                "| Costs | $0.0035/share commission per fill plus **1¢/share slippage per side**, adverse on every fill |",
                "| Sizing | Fixed $25,000 sleeve, non-compounding; shares = floor(min(1% of sleeve ÷ stop distance, 25,000 ÷ trigger)); integer shares; never levered |"):
        assert row in text


# ============================================================================== pure helpers
def test_the_opening_bar_aggregates_the_five_one_minute_bars_and_needs_all_five():
    assert Q.opening_bar(UP) == (400.0, 401.0, 399.5, 400.8)
    assert Q.opening_bar(DOWN) == (400.0, 400.5, 399.0, 399.2)
    assert Q.opening_bar({m: b for m, b in UP.items() if m != 572}) is None
    assert Q.opening_bar({**UP, 575: bar(1, 1, 1, 1)}) == (400.0, 401.0, 399.5, 400.8)                 # the 09:35 bar is not part of it
    assert Q.opening_bar({}) is None


def test_the_partial_iex_bar_uses_the_minutes_present_and_counts_them():
    assert Q.partial_bar(IEX[D["A"]]) == (400.0, 400.95, 399.6, 400.75, 3)
    assert Q.partial_bar({}) is None and Q.partial_bar({575: bar(1, 1, 1, 1)}) is None
    assert Q.sign(400.0, 400.8) == 1 and Q.sign(400.0, 399.2) == -1 and Q.sign(400.0, 400.0) == 0


def test_atr_is_the_simple_mean_of_the_prior_fourteen_true_ranges_and_needs_history():
    cal = weekdays(date(2024, 1, 1), date(2024, 2, 9))
    daily = {d: (100.0, 102.0, 98.0, 100.0) for d in cal}                                              # TR 4 every day
    daily[cal[10]] = (100.0, 110.0, 99.0, 108.0)                                                       # TR = max(11, |110-100|, |99-100|) = 11
    daily[cal[11]] = (108.0, 109.0, 107.0, 108.0)                                                      # TR = max(2, 1, 1) = 2
    # cal[12] is a normal bar (98..102) but its previous close is 108: TR = max(4, |102-108|, |98-108|) = 10
    i = 17
    assert Q.atr_for_day(i, cal, daily, CFG) == pytest.approx((11 * 4 + 11 + 2 + 10) / 14)               # cal[3..16]
    assert Q.atr_for_day(13, cal, daily, CFG) is None                                                  # fewer than 14 prior days
    missing = dict(daily)
    del missing[cal[5]]
    assert Q.atr_for_day(i, cal, missing, CFG) is None                                                 # a prior day absent
    assert Q.atr_for_day(14, cal, daily, CFG) is not None                                              # no previous close for the OLDEST bar: allowed
    d2 = {d: (100.0, 102.0, 98.0, 100.0) for d in cal}
    d2[cal[0]] = (100.0, 100.0, 100.0, 90.0)                                                           # the oldest bar's previous close
    d2[cal[1]] = (100.0, 104.0, 96.0, 100.0)                                                           # high - low = 8; with prev close 90: 14
    assert Q.atr_for_day(15, cal, d2, CFG) == pytest.approx((14 + 13 * 4) / 14)
    del d2[cal[0]]
    assert Q.atr_for_day(15, cal, d2, CFG) == pytest.approx((8 + 13 * 4) / 14)                         # the previous close missing: high - low


def test_the_day_that_uses_todays_data_in_its_atr_is_caught():
    cal = weekdays(date(2024, 1, 1), date(2024, 2, 9))
    daily = {d: (100.0, 102.0, 98.0, 100.0) for d in cal}
    i = 20
    base = Q.atr_for_day(i, cal, daily, CFG)
    daily[cal[i]] = (100.0, 500.0, 1.0, 100.0)                                                         # today's bar must not enter the average
    assert Q.atr_for_day(i, cal, daily, CFG) == base == pytest.approx(4.0)


def test_build_pick_gives_the_reason_for_every_day_without_a_pick():
    inp = _inputs()
    pos = {d: i for i, d in enumerate(inp.cal)}
    picks = {k: Q.build_pick(pos[D[k]], inp.cal, inp.daily, inp.minute.get(D[k], {}), CFG) for k in "ABCDEFGH"}
    assert picks["A"][0].side == LONG and picks["A"][0].atr == pytest.approx(6.05) and picks["A"][0].rvol == 0.0
    assert picks["B"][0].atr == pytest.approx(6.0)                                                     # the 6.70 bar has left the window
    assert picks["C"][0].atr == pytest.approx(7.0) and picks["H"][0].atr == pytest.approx(7.0)        # the range-20 bar of 01-03 is in these windows
    assert (picks["A"][0].bar_open, picks["A"][0].bar_high, picks["A"][0].bar_low, picks["A"][0].bar_close) == (400.0, 401.0, 399.5, 400.8)
    assert picks["C"][0].side == SHORT and picks["D"][0].side is None                                  # a doji is a pick with no side
    assert picks["E"] == (None, "opening_bar_incomplete") and picks["G"] == (None, "no_minute_bars")
    early = Q.build_pick(5, inp.cal, inp.daily, MINUTE[D["A"]], CFG)
    assert early == (None, "atr_unavailable")


# ============================================================================== the study on the market
@pytest.fixture(scope="module")
def res():
    return Q.analyse(_inputs(), CFG)


def _trades(res, name):
    return {tr.day: tr for r in res["results"][name] for tr in r.trades}


def test_the_clean_long_ambiguous_short_and_gap_days_give_the_constructed_answers(res):
    tr = _trades(res, "primary")
    assert sorted(tr) == [D["A"], D["B"], D["C"], D["H"]]
    a = tr[D["A"]]
    assert (a.side, a.shares, a.binding, a.trigger, a.stop, a.entry_fill, a.exit_fill, a.exit_reason) == (LONG, 62, "cap", 401.0, 400.39, 401.0, 402.0, "time")
    assert a.stop_distance == pytest.approx(0.61) and a.gross_pnl == pytest.approx(62.0)                  # 401 - 0.605 = 400.395 -> 400.39, AWAY from the entry
    assert a.slippage_cost == pytest.approx(62 * 0.01 * 2) and a.commission == pytest.approx(62 * 0.0035 * 2)
    assert a.net_pnl == pytest.approx(62 - 1.24 - 0.434) and a.r_net == pytest.approx(a.net_pnl / (62 * 0.61))
    b = tr[D["B"]]
    assert (b.exit_reason, b.entry_minute, b.exit_minute, b.exit_fill, b.stop) == ("stop_same_bar", t(9, 35), t(9, 35), 400.4, 400.4)
    assert b.gross_pnl == pytest.approx(62 * -0.6) and b.r_gross == pytest.approx(-1.0)
    c = tr[D["C"]]
    assert (c.side, c.shares, c.trigger, c.stop, c.exit_fill) == (SHORT, 62, 399.0, 399.7, 398.0) and c.gross_pnl == pytest.approx(62.0)
    assert c.stop_distance == pytest.approx(0.7)
    h = tr[D["H"]]
    assert h.entry_gap and h.entry_fill == 401.5 and h.exit_fill == 403.0


def test_days_without_a_trade_are_counted_as_zero_and_the_reasons_are_recorded(res):
    assert res["skipped_days"] == {"opening_bar_incomplete": ["2024-01-08"], "no_minute_bars": ["2024-01-10"]}
    m = res["metrics"]["primary"]
    assert m["days"] == 8 and m["trades"] == 4 and m["days_with_no_trades"] == 4
    assert m["skips"] == {"doji": 1, "never_triggered": 1}
    assert [r.day for r in res["results"]["primary"]] == sorted(r.day for r in res["results"]["primary"]) and len(res["results"]["primary"]) == 8
    assert res["metrics"]["primary"]["net_pnl"] == pytest.approx(sum(tr.net_pnl for tr in _trades(res, "primary").values()))


def test_the_cost_sensitivities_and_long_only_change_exactly_one_thing(res):
    base = _trades(res, "primary")
    for name, cents in (("slippage_0c", 0.0), ("slippage_2c", 2.0), ("slippage_5c", 5.0)):
        tt = _trades(res, name)
        assert sorted(tt) == sorted(base)
        for d, x in tt.items():
            assert x.gross_pnl == pytest.approx(base[d].gross_pnl) and x.slippage_cost == pytest.approx(x.shares * cents / 100 * 2)
            assert x.commission == pytest.approx(base[d].commission)
    lo = _trades(res, "long_only")
    assert sorted(lo) == [D["A"], D["B"], D["H"]] and lo[D["A"]].net_pnl == pytest.approx(base[D["A"]].net_pnl)
    assert res["metrics"]["long_only"]["skips"]["short_excluded"] == 1


def test_same_bar_optimistic_differs_from_the_primary_only_where_the_entry_bar_reaches_the_stop(res):
    base, opt = _trades(res, "primary"), _trades(res, "same_bar_optimistic")
    assert sorted(opt) == sorted(base)
    for d in (D["A"], D["C"], D["H"]):                                                                 # the entry bar never reaches the stop: identical trade
        assert opt[d] == base[d]
    b, ob = base[D["B"]], opt[D["B"]]
    assert b.exit_reason == "stop_same_bar" and ob.exit_reason == "time" and ob.exit_fill == 402.5 and ob.entry_fill == b.entry_fill
    assert ob.gross_pnl == pytest.approx(62 * 1.5) and ob.r_gross == pytest.approx(62 * 1.5 / (62 * 0.6))
    ex_p, ex_c = res["extras"]["primary"], res["extras"]["same_bar_optimistic"]
    assert ex_p["stopped_entry_minute"] == 1 and ex_p["stopped_entry_minute_share"] == pytest.approx(0.25)
    assert ex_c["stopped_entry_minute"] == 0 and ex_c["stopped_entry_minute_share"] == 0.0               # zero by definition
    assert ex_p["entry_bar_reaches_stop"] == 1 == ex_c["entry_bar_reaches_stop"]                        # the quantity (c) ignores


def test_the_extra_statistics_follow_their_definitions(res):
    ex = res["extras"]["primary"]
    tr = list(_trades(res, "primary").values())
    dists = sorted(x.stop_distance for x in tr)
    assert dists == pytest.approx([0.6, 0.61, 0.7, 0.7])
    assert ex["mean_stop_distance_usd"] == pytest.approx(sum(dists) / 4)
    assert ex["mean_stop_distance_bps"] == pytest.approx(sum(abs(x.trigger - x.stop) / x.trigger * 1e4 for x in tr) / 4)
    cost = sum(x.slippage_cost + x.commission for x in tr)
    risk = sum(x.shares * x.stop_distance for x in tr)
    assert ex["costs_total_usd"] == pytest.approx(cost) and ex["risk_total_usd"] == pytest.approx(risk) == pytest.approx(62 * 2.61)
    assert ex["costs_over_total_risk"] == pytest.approx(cost / risk)
    assert ex["mean_costs_over_R"] == pytest.approx(sum((x.slippage_cost + x.commission) / (x.shares * x.stop_distance) for x in tr) / 4)
    assert ex["avg_net_R"] == pytest.approx(sum(x.r_net for x in tr) / 4) == pytest.approx(res["metrics"]["primary"]["avg_r_net"])
    assert ex["avg_net_bps"] == pytest.approx(sum(x.net_pnl / (x.shares * x.entry_fill) * 1e4 for x in tr) / 4)
    assert ex["mean_shares"] == 62 and ex["t_stat_mean_daily_return"] is not None
    assert res["metrics"]["primary"]["sizing_binding"]["cap"] == 4 and res["metrics"]["primary"]["sizing_binding"]["risk"] == 0
    assert Q.extras([], {}) == {"trades": 0}


def test_the_entry_bar_reaches_the_stop_at_exactly_the_stop_level_and_not_one_cent_short():
    base = dict(day=D["A"], symbol="QQQ", shares=1, binding="cap", rvol=0.0, atr=6.0, trigger=401.0, stop_distance=0.6, entry_fill=401.0,
                entry_minute=575, entry_gap=False, exit_fill=0.0, exit_minute=575, exit_reason="stop_same_bar", exit_stale=False, gross_pnl=0.0,
                slippage_cost=0.0, commission=0.0, net_pnl=0.0, r_gross=0.0, r_net=0.0, notional=401.0)
    long_ = E.Trade(side=LONG, stop=400.4, **base)
    assert Q.entry_bar_reaches_stop(long_, bar(400.9, 401.1, 400.40, 400.95)) is True                  # the low equals the stop: the stop is hit
    assert Q.entry_bar_reaches_stop(long_, bar(400.9, 401.1, 400.41, 400.95)) is False
    short = E.Trade(side=SHORT, stop=399.6, **{**base, "trigger": 399.0})
    assert Q.entry_bar_reaches_stop(short, bar(399.1, 399.60, 398.9, 399.0)) is True
    assert Q.entry_bar_reaches_stop(short, bar(399.1, 399.59, 398.9, 399.0)) is False


# ============================================================================ parity with Phase 2
def test_inherited_rule_parity_the_phase2_path_and_this_one_give_the_same_trade():
    """A constructed day through the Phase 2 path (`run_backtest` on a selection holding the pick a Phase 2 five-minute bar would give)
    and through the QQQ path (pick from the one-minute aggregate) produces identical trades, for every constructed day."""
    inp = _inputs()
    pos = {d: i for i, d in enumerate(inp.cal)}
    ours = {}
    phase2 = {}
    sel_picks = {}
    for k in "ABCH":
        d = D[k]
        pick, _ = Q.build_pick(pos[d], inp.cal, inp.daily, inp.minute[d], CFG)
        ours[d] = Q.simulate_variant(Q.PRIMARY, CFG, [d], {d: pick}, inp.minute)[0].trades
        five = SIP5[d]
        sel_picks[d] = {True: [E.Pick("QQQ", 5.0, pick.side, five[0], five[1], five[2], five[3], pick.atr)], False: []}
    selection = SimpleNamespace(window_days=[D[k] for k in "ABCH"], picks_by_day=sel_picks, stats={})
    run = E.run_backtest(StubLayer(), selection, CFG.replace(exclude_etfs=False), (E.PRIMARY,))
    for r in run.results["primary"]:
        phase2[r.day] = r.trades
    assert set(ours) == set(phase2) and all(len(v) == 1 for v in ours.values())
    for d in ours:
        a, b = ours[d][0], phase2[d][0]
        assert (a.side, a.shares, a.binding, a.trigger, a.stop, a.entry_fill, a.entry_minute, a.exit_fill, a.exit_minute, a.exit_reason, a.net_pnl) == \
               (b.side, b.shares, b.binding, b.trigger, b.stop, b.entry_fill, b.entry_minute, b.exit_fill, b.exit_minute, b.exit_reason, b.net_pnl)


def test_the_default_engine_rule_and_the_explicit_preregistered_rule_agree_on_this_market():
    inp = _inputs()
    pos = {d: i for i, d in enumerate(inp.cal)}
    picks = {D[k]: Q.build_pick(pos[D[k]], inp.cal, inp.daily, inp.minute[D[k]], CFG)[0] for k in "ABCH"}
    a = Q.simulate_variant(Q.PRIMARY, CFG, [D[k] for k in "ABCH"], picks, inp.minute)
    b = [E.simulate_day(E.DayInput(d, 960, (picks[d],), {"QQQ": inp.minute[d]}), CFG, entry_bar_stop=samebar.assume_stop_rule) for d in sorted(picks)]
    assert [r.trades for r in a] == [r.trades for r in b]


# ==================================================================================== IEX and 5-minute
def test_the_iex_check_compares_direction_and_the_opening_range_in_cents(res):
    i = res["iex"]
    assert i["days"] == 8 and i["sip_bar_days"] == 6 and i["compared"] == 2 and i["no_iex_bar_days"] == 6
    assert i["direction_agree"] == 1 and i["direction_agree_share"] == pytest.approx(0.5)
    assert i["disagreements"] == [{"day": "2024-01-03", "sip_dir": 1, "iex_dir": -1}]
    assert i["iex_minutes_present"] == {0: 6, 2: 1, 3: 1}
    rows = {r["day"]: r for r in i["rows"]}
    a = rows["2024-01-02"]
    assert a["high_diff_cents"] == pytest.approx((400.95 - 401.0) * 100) and a["low_diff_cents"] == pytest.approx((399.6 - 399.5) * 100)
    assert i["high"]["max_abs"] == pytest.approx(70.0) and i["low"]["max_abs"] == pytest.approx(20.0)        # day B: IEX high 400.30 vs 401.00
    assert i["iex_range_outside_sip"] == 0 and i["mean_signed_high_cents"] < 0 < i["mean_signed_low_cents"]
    assert i["high"]["share_exactly_equal"] == 0.0


def test_the_iex_check_flags_an_iex_range_outside_the_sip_range_and_equal_bars():
    sip = {D["A"]: (400.0, 401.0, 399.5, 400.8), D["B"]: (400.0, 401.0, 399.5, 400.8)}
    iex = {D["A"]: {570: bar(400.0, 401.0, 399.5, 400.8)}, D["B"]: {570: bar(400.0, 401.2, 399.5, 400.8)}}      # B's IEX high exceeds SIP: impossible
    i = Q.iex_check(sip, iex, [D["A"], D["B"]])
    assert i["iex_range_outside_sip"] == 1 and i["high"]["share_exactly_equal"] == pytest.approx(0.5) and i["direction_agree"] == 2
    one_cent = Q.iex_check({D["A"]: (400.0, 401.0, 399.5, 400.8)}, {D["A"]: {570: bar(400.0, 400.99, 399.5, 400.8)}}, [D["A"]])
    assert one_cent["high"]["share_exactly_equal"] == 0.0 and one_cent["high"]["max_abs"] == pytest.approx(1.0)       # one cent is not "exactly equal"
    assert one_cent["low"]["share_exactly_equal"] == 1.0


def test_the_five_minute_consistency_check_names_every_difference(res):
    f = res["five_min"]
    assert f["compared"] == 6 and f["equal"] == 5 and f["mismatches"] == 1
    assert f["first_mismatches"][0]["day"] == "2024-01-04" and f["five_min_days_without_aggregate"] == 0
    assert Q.five_min_check({D["A"]: (1, 2, 0, 1)}, {D["A"]: (1, 2, 0, 1)})["mismatches"] == 0
    assert Q.five_min_check({D["A"]: None}, {D["A"]: (1, 2, 0, 1)}) == {"compared": 0, "equal": 0, "mismatches": 0, "first_mismatches": [],
                                                                       "five_min_days_without_aggregate": 1}


# ================================================================================== validity and CLI
def _args(tmp, **kw):
    import argparse
    base = dict(start="2024-01-02", end="2024-01-11", out=str(tmp / "out"), review_file=str(tmp / "review" / "r.md"), offline=False, cache_dir=None)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture()
def patched(tmp_path, monkeypatch):
    monkeypatch.setattr(Q, "DataLayer", StubLayer)
    monkeypatch.setattr(Q.provenance, "prereg_info", lambda path=None: {"path": path, "first_commit": "abc", "unchanged": True})
    monkeypatch.setattr(Q.provenance, "code_commit", lambda: {"sha": "deadbeef" * 5, "dirty": False})
    return tmp_path


def test_validity_rules():
    ok = {"unchanged": True}
    assert Q.validity("2024-01-02", "2025-12-31", CFG, Q.ALL_VARIANTS, ok) == []
    assert Q.validity("2024-01-03", "2025-12-31", CFG, Q.ALL_VARIANTS, ok)
    assert Q.validity("2024-01-02", "2025-12-30", CFG, Q.ALL_VARIANTS, ok)
    assert Q.validity("2024-01-02", "2025-12-31", CFG.replace(slippage_cents_per_side=2.0), Q.ALL_VARIANTS, ok)
    assert Q.validity("2024-01-02", "2025-12-31", OrbConfig(), Q.ALL_VARIANTS, ok)                      # the Phase 2 config is not the QQQ one
    assert Q.validity("2024-01-02", "2025-12-31", CFG, Q.ALL_VARIANTS[:3], ok)
    assert Q.validity("2024-01-02", "2025-12-31", CFG, Q.ALL_VARIANTS, {"unchanged": False})
    assert Q.validity("2024-01-02", "2025-12-31", CFG, Q.ALL_VARIANTS, {"unchanged": None})


def test_the_cli_writes_outputs_and_labels_a_non_preregistered_window(patched, capsys):
    assert Q.cmd_run(_args(patched)) == 0
    assert "NOT a pre-registered run" in capsys.readouterr().out
    run = next((patched / "out").iterdir())
    header = json.loads((run / "header.json").read_text(encoding="utf-8"))
    assert header["valid_for_verdict"] is False and header["holdout"].startswith("2026 was never read") and header["picks"] == 6
    text = (patched / "review" / "r.md").read_text(encoding="utf-8")
    assert "NOT A PRE-REGISTERED RUN" in text and "## IEX vs. SIP" in text and "## Stopped in the entry minute" in text
    for v in Q.ALL_VARIANTS:
        assert (run / v.name / "trades.csv").is_file() and (run / v.name / "daily.csv").is_file() and (run / v.name / "metrics.json").is_file()
    assert (run / "iex_vs_sip.csv").is_file() and (run / "extras.json").is_file() and (run / "skipped_days.json").is_file()
    assert len(pd.read_csv(run / "primary" / "trades.csv")) == 4


def test_the_exact_preregistered_window_carries_a_verdict(patched, capsys):
    assert Q.cmd_run(_args(patched, start="2024-01-02", end="2025-12-31")) == 0
    out = capsys.readouterr().out
    assert "NOT a pre-registered" not in out and ("verdict GO" in out or "verdict NO-GO" in out)
    run = next((patched / "out").iterdir())
    header = json.loads((run / "header.json").read_text(encoding="utf-8"))
    assert header["valid_for_verdict"] is True and header["metrics_verdict"]["verdict"] in ("GO", "NO-GO")
    assert "Mechanical verdict (primary variant)" in (run / "summary.md").read_text(encoding="utf-8")


def test_2026_is_refused_with_no_override(patched, capsys):
    assert Q.main(["run", "--start", "2024-01-02", "--end", "2026-01-02", "--out", str(patched / "out")]) == 2
    assert "holdout" in capsys.readouterr().err and not (patched / "out").exists()
    with pytest.raises(HoldoutError):
        Q.load_inputs(StubLayer(), "2025-12-31", "2026-01-01")
    import inspect
    assert "allow_holdout" not in inspect.signature(Q.load_inputs).parameters and "allow_holdout" not in inspect.signature(Q.cmd_run).parameters


def test_only_qqq_is_ever_requested_and_the_five_data_requests_are_the_ones_named(patched):
    layer = StubLayer()
    Q.load_inputs(layer, "2024-01-02", "2024-01-11")
    assert {c[0] for c in layer.calls} == {("QQQ",)}
    assert sorted((c[1], c[2], c[3]) for c in layer.calls) == sorted([
        ("1Day", "sip", None), ("1Min", "sip", ("09:30", "16:00")), ("1Min", "iex", ("09:30", "09:35")), ("5Min", "sip", ("09:30", "09:35"))])


# ======================================================================================== the verifier
def test_the_verifier_confirms_a_correct_run_offline_and_catches_tampered_ones(patched):
    class Offline(StubLayer):
        def get_bars(self, *a, offline=False, **k):
            assert offline is True
            return super().get_bars(*a, offline=offline, **k)

        def trading_days(self, start, end, offline=False, **k):
            assert offline is True
            return super().trading_days(start, end, offline=offline, **k)
    Q.cmd_run(_args(patched))
    run = next((patched / "out").iterdir())
    ok = V.check(Offline(), run, n=50)
    assert ok["pass"] and ok["sampled"] == 4 and ok["matched"] == 4 and "Overall: **PASS**" in V.render(ok, run)
    p = run / "primary" / "trades.csv"
    df = pd.read_csv(p, dtype={"day": str})
    df.loc[0, "net_pnl"] += 1.0
    df.loc[1, "shares"] += 1
    df.loc[1, "exit_reason"] = "time"                                                                  # B was stopped in its entry minute
    df.loc[2, "trigger"] += 0.5
    df.loc[3, "exit_fill"] += 0.5
    df.to_csv(p, index=False)
    bad = V.check(Offline(), run, n=50)
    assert not bad["pass"] and len(bad["differences"]) == 4
    flat = " ".join(str(d["fields"]) for d in bad["differences"])
    assert "net_pnl" in flat and "shares" in flat and "exit_reason" in flat and "exit_fill" in flat and "trigger" in flat
    assert "Overall: **FAIL**" in V.render(bad, run)


def test_the_verifier_shares_no_code_with_the_strategy_modules():
    import ast
    import inspect
    imported = []
    for node in ast.walk(ast.parse(inspect.getsource(V))):
        if isinstance(node, ast.ImportFrom):
            imported.append(("." * node.level) + (node.module or ""))
        elif isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
    assert not [m for m in imported if m.split(".")[0] in ("orb", "engine", "reports", "orb_qqq", "samebar") or m in (".engine", ".reports", ".orb_qqq", ".samebar")], imported
