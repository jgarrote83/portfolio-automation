"""The same-minute diagnostic on constructed print sequences and constructed days with known answers
(no network, no data layer): the pure per-minute resolution, the print filter, the entry-bar rules, the
changed set and the multi-rule window loop.

Standard long (from `test_backtest_engine`): first 5-min bar O 100.00 / H 101.00 / L 99.50 / C 100.80, ATR $2.00
-> trigger 101.00, stop 100.80. Standard short: O 100.00 / H 100.50 / L 99.00 / C 99.20 -> trigger 99.00, stop 99.20.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from backtest import samebar as S  # noqa: E402
from backtest.data.store import normalise_trades  # noqa: E402
from backtest.engine import DayInput, Pick, simulate_day  # noqa: E402
from orb import signals  # noqa: E402
from orb.config import OrbConfig  # noqa: E402
from orb.signals import LONG, SHORT  # noqa: E402

DAY = date(2024, 3, 5)
BASE = pd.Timestamp("2024-03-05 09:35:00", tz="America/New_York").value
FREE = OrbConfig().replace(slippage_cents_per_side=0.0, commission_per_share=0.0)
TRIG, STOP = 101.00, 100.80                                           # the standard long
STRIG, SSTOP = 99.00, 99.20                                           # the standard short


def P(sec, price, seq=0):
    """A print `sec` seconds into the 09:35 minute."""
    return (BASE + int(round(sec * 1e9)), price, seq)


def res(prints, side=LONG, trigger=TRIG, stop=STOP):
    return S.resolve_entry_minute(side, trigger, stop, prints)


# ================================================================================= the resolution
def test_a_dip_to_the_stop_BEFORE_the_trigger_print_is_not_a_stop_out():
    r = res([P(1, 100.90), P(2, 100.70), P(3, 100.95), P(4, 101.05), P(5, 100.95)])
    assert r.outcome == S.DIP_FIRST
    assert (r.trigger_ts, r.trigger_price) == (P(4, 0)[0], 101.05)
    assert (r.stop_before_ts, r.stop_before_price) == (P(2, 0)[0], 100.70) and r.stop_after_ts is None


def test_a_stop_print_AFTER_the_trigger_print_is_a_real_stop_even_if_the_stop_also_traded_before():
    only_after = res([P(1, 100.90), P(2, 101.00), P(3, 100.80), P(4, 100.60)])
    assert only_after.outcome == S.REAL_STOP and only_after.stop_before_ts is None
    assert (only_after.stop_after_ts, only_after.stop_after_price) == (P(3, 0)[0], 100.80)         # a print AT the stop counts
    both = res([P(1, 100.70), P(2, 101.10), P(3, 100.79)])
    assert both.outcome == S.REAL_STOP and both.stop_before_ts == P(1, 0)[0] and both.stop_after_price == 100.79


def test_the_first_print_AT_the_trigger_fills_and_a_print_just_inside_the_stop_does_not_stop():
    r = res([P(1, 100.81), P(2, 101.00), P(3, 100.81)])
    assert r.outcome == S.STOP_NEVER_TOUCHED and r.trigger_price == 101.00      # 100.81 is above the stop: never touched


def test_a_stop_print_that_shares_the_trigger_prints_timestamp_is_scored_as_a_real_stop_and_flagged():
    t = 2.0
    stop_sorts_first = res([P(1, 100.90), P(t, 100.70, seq=0), P(t, 101.02, seq=1), P(3, 100.95)])
    assert stop_sorts_first.outcome == S.TIE_REAL_STOP and stop_sorts_first.tie_would_be_dip is True    # order says dip; scored real
    stop_sorts_second = res([P(1, 100.90), P(t, 101.02, seq=0), P(t, 100.70, seq=1)])
    assert stop_sorts_second.outcome == S.TIE_REAL_STOP and stop_sorts_second.tie_would_be_dip is False
    # the same timestamp but a strictly LATER stop print is an ordinary real stop, not a tie
    assert res([P(t, 101.02, seq=0), P(2.000000001, 100.70, seq=1)]).outcome == S.REAL_STOP
    # a stop print one nanosecond BEFORE the trigger print is a dip, not a tie
    assert res([P(1.999999999, 100.70, seq=0), P(t, 101.02, seq=1)]).outcome == S.DIP_FIRST


def test_the_replay_is_in_timestamp_order_whatever_order_the_prints_arrive_in():
    ordered = [P(1, 100.70), P(2, 101.05), P(3, 100.95)]
    assert res(list(reversed(ordered))).outcome == res(ordered).outcome == S.DIP_FIRST
    ordered2 = [P(1, 100.90), P(2, 101.05), P(3, 100.70)]
    assert res(list(reversed(ordered2))).outcome == S.REAL_STOP


def test_no_trigger_print_no_stop_print_and_no_prints_at_all_are_distinguished():
    assert res([P(1, 100.90), P(2, 100.95)]).outcome == S.NO_TRIGGER_PRINT       # ticks never confirm the entry
    assert res([P(1, 100.90), P(2, 101.20), P(3, 101.10)]).outcome == S.STOP_NEVER_TOUCHED
    assert res([]).outcome == S.UNRESOLVED and res([]).n_prints == 0


def test_the_short_is_the_mirror():
    pr = lambda *xs: list(xs)                                                    # noqa: E731
    dip = res(pr(P(1, 99.10), P(2, 99.30), P(3, 99.05), P(4, 98.95), P(5, 99.00)), SHORT, STRIG, SSTOP)
    assert dip.outcome == S.DIP_FIRST and dip.trigger_price == 98.95
    real = res(pr(P(1, 99.10), P(2, 99.00), P(3, 99.20)), SHORT, STRIG, SSTOP)
    assert real.outcome == S.REAL_STOP and real.stop_after_price == 99.20
    tie = res(pr(P(2, 99.30, 0), P(2, 98.99, 1)), SHORT, STRIG, SSTOP)
    assert tie.outcome == S.TIE_REAL_STOP and tie.tie_would_be_dip is True
    assert res(pr(P(1, 99.10), P(2, 99.05)), SHORT, STRIG, SSTOP).outcome == S.NO_TRIGGER_PRINT


# ================================================================================== the print filter
def test_codes_exclude_a_print_only_when_one_of_its_codes_is_in_the_set():
    assert S.counted(["@"]) and S.counted([" "]) and S.counted(["@", "F"]) and S.counted(["@", "5", "X"])
    assert not S.counted(["@", "I"]) and not S.counted(["@", "4"]) and not S.counted(["@", "F", "I"])
    assert not S.counted(["Z"]) and not S.counted(["@", "U"]) and not S.counted(["@", "7", "V"])
    assert S.counted(["@", "I"], S.PRIMARY_EXCLUDED - {"I"}) and S.counted(["@", "I", "4"], frozenset())
    assert not S.counted(["@", "I"], S.MINIMAL_EXCLUDED) and S.counted(["@", "Z"], S.MINIMAL_EXCLUDED)
    assert S.MINIMAL_EXCLUDED < S.PRIMARY_EXCLUDED and set(S.EXCLUDED_CODES) == set(S.PRIMARY_EXCLUDED)
    assert all(S.EXCLUDED_CODES.values())                                         # every excluded code carries its reason


def test_counted_prints_filters_a_get_trades_frame_and_keeps_timestamp_and_seq():
    rows = [{"t": "2024-03-05T14:35:01Z", "p": 100.9, "s": 100, "c": ["@"], "i": 1},
            {"t": "2024-03-05T14:35:02Z", "p": 90.0, "s": 10, "c": ["@", "I"], "i": 2},        # odd lot
            {"t": "2024-03-05T14:35:03Z", "p": 101.1, "s": 100, "c": ["@", "Z"], "i": 3},      # out of sequence
            {"t": "2024-03-05T14:35:04Z", "p": 101.0, "s": 100, "c": ["@", "F"], "i": 4}]
    df = normalise_trades(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("US/Eastern")
    assert S.counted_prints(df) == [(BASE + 1_000_000_000, 100.9, 0), (BASE + 4_000_000_000, 101.0, 3)]
    assert len(S.counted_prints(df, frozenset())) == 4 and len(S.counted_prints(df, S.MINIMAL_EXCLUDED)) == 3
    assert S.counted_prints(df.iloc[0:0]) == []


def test_the_tick_derived_bar_is_compared_with_the_bar_field_by_field():
    pr = [P(1, 100.90), P(2, 101.10), P(3, 100.70), P(4, 100.95)]
    assert S.bar_from_prints(pr) == (100.90, 101.10, 100.70, 100.95)
    assert S.bar_from_prints(list(reversed(pr))) == (100.90, 101.10, 100.70, 100.95)
    assert S.bar_from_prints([]) is None
    ok = S.bar_matches(S.bar_from_prints(pr), (100.90, 101.10, 100.70, 100.95, 1234))
    assert all(ok.values())
    off = S.bar_matches(S.bar_from_prints(pr), (100.90, 101.10, 100.71, 100.95, 1234))
    assert off == {"open": True, "high": True, "low": False, "close": True}
    assert not any(S.bar_matches(None, (1, 1, 1, 1, 1)).values())


def test_an_entry_bar_is_ambiguous_only_if_it_opened_short_of_the_trigger():
    assert S.is_ambiguous_entry(LONG, 101.0, 100.90) and not S.is_ambiguous_entry(LONG, 101.0, 101.0)
    assert not S.is_ambiguous_entry(LONG, 101.0, 101.50)                              # a gap fills on the open print
    assert S.is_ambiguous_entry(SHORT, 99.0, 99.10) and not S.is_ambiguous_entry(SHORT, 99.0, 99.0)
    assert not S.is_ambiguous_entry(SHORT, 99.0, 98.50)


# ================================================================================ the entry-bar rules
def bar(o, h, lo, c, v=1000):
    return (o, h, lo, c, v)


def t(h, m):
    return h * 60 + m


def long_pick(sym):
    return Pick(sym, 2.0, LONG, 100.00, 101.00, 99.50, 100.80, 2.0)


AMBIG = bar(100.90, 101.10, 100.70, 100.95)                    # opens below the trigger, reaches trigger AND stop
GAP = bar(101.50, 101.60, 100.70, 101.00)                      # opens through the trigger


def _paths():
    return {
        "AAA": {t(9, 35): AMBIG, t(15, 59): bar(101.40, 101.70, 101.40, 101.60)},                  # dip first, then +3R
        "BBB": {t(9, 35): AMBIG, t(9, 36): bar(100.95, 100.95, 100.80, 100.85)},                   # stopped one bar later
        "CCC": {t(9, 35): GAP, t(15, 59): bar(101.40, 101.70, 101.40, 101.60)},                    # a gap entry
    }


def _day():
    return DayInput(DAY, 960, tuple(long_pick(s) for s in ("AAA", "BBB", "CCC")), _paths())


def test_the_pre_registered_rule_function_is_the_engine_default_and_the_ignore_rule_is_the_optimistic_bound():
    d = _day()
    default = simulate_day(d, FREE)
    assert simulate_day(d, FREE, entry_bar_stop=S.assume_stop_rule).trades == default.trades
    assert {x.symbol: x.exit_reason for x in default.trades} == {s: "stop_same_bar" for s in ("AAA", "BBB", "CCC")}
    opt = simulate_day(d, FREE, entry_bar_stop=S.ignore_stop_rule)
    assert {x.symbol: x.exit_reason for x in opt.trades} == {"AAA": "time", "BBB": "stop", "CCC": "time"}


def test_the_recorder_remembers_every_entry_bar_and_plan_and_passes_the_answer_through():
    rec = S.Recorder(S.ignore_stop_rule)
    simulate_day(_day(), FREE, entry_bar_stop=rec)
    assert set(rec.calls) == {(DAY, "AAA"), (DAY, "BBB"), (DAY, "CCC")}
    plan, b = rec.calls[(DAY, "AAA")]
    assert isinstance(plan, signals.EntryPlan) and (plan.trigger, plan.stop, b) == (TRIG, STOP, AMBIG)


def test_the_resolved_rule_decides_per_trade_and_leaves_every_other_entry_pre_registered():
    rule = S.ResolvedRule({(DAY, "AAA"): False, (DAY, "BBB"): True})
    out = {x.symbol: x for x in simulate_day(_day(), FREE, entry_bar_stop=rule).trades}
    assert out["AAA"].exit_reason == "time" and out["AAA"].r_gross == pytest.approx(3.0)       # a dip first: runs to the close
    assert out["BBB"].exit_reason == "stop_same_bar"                                          # a real stop, as scored
    assert out["CCC"].exit_reason == "stop_same_bar"                                          # not listed: the pre-registered rule


# ================================================================================== the changed set
def _candidates():
    d = _day()
    rec = S.Recorder(S.assume_stop_rule)
    prim = S.trades_by_key([simulate_day(d, FREE, entry_bar_stop=rec)])
    opt = S.trades_by_key([simulate_day(d, FREE, entry_bar_stop=S.ignore_stop_rule)])
    return S.build_candidates(prim, opt, rec.calls), prim, opt


def test_the_changed_set_is_the_ambiguous_trades_whose_result_the_optimistic_rule_changes():
    cands, prim, _opt = _candidates()
    by = {c.symbol: c for c in cands}
    assert [c.symbol for c in cands] == ["AAA", "BBB", "CCC"] and all(p.exit_reason == "stop_same_bar" for p in prim.values())
    assert (by["AAA"].ambiguous, by["AAA"].changed, by["AAA"].r_gross_optimistic) == (True, True, pytest.approx(3.0))
    assert (by["BBB"].ambiguous, by["BBB"].changed) == (True, False)                           # stopped later at the same level: -1.00
    assert by["BBB"].r_gross_optimistic == pytest.approx(-1.0)
    assert (by["CCC"].ambiguous, by["CCC"].changed) == (False, False)                          # not ambiguous, so never in the set
    assert by["AAA"].bar == AMBIG and by["AAA"].trigger == TRIG and by["AAA"].stop == STOP and by["AAA"].side == LONG


def test_a_trade_that_is_stopped_later_through_a_gap_changes_by_more_than_the_tolerance():
    paths = {"AAA": {t(9, 35): AMBIG, t(9, 36): bar(100.60, 100.65, 100.50, 100.55)}}            # gaps through the stop: -2R
    d = DayInput(DAY, 960, (long_pick("AAA"),), paths)
    rec = S.Recorder(S.assume_stop_rule)
    prim = S.trades_by_key([simulate_day(d, FREE, entry_bar_stop=rec)])
    opt = S.trades_by_key([simulate_day(d, FREE, entry_bar_stop=S.ignore_stop_rule)])
    (c,) = S.build_candidates(prim, opt, rec.calls)
    assert c.changed and c.r_gross_optimistic == pytest.approx(-2.0)


def test_non_stop_same_bar_trades_are_not_candidates_and_a_missing_optimistic_trade_counts_as_changed():
    paths = {"AAA": {t(9, 35): bar(100.90, 101.00, 100.85, 100.95), t(15, 59): bar(101.4, 101.7, 101.4, 101.6)}}
    d = DayInput(DAY, 960, (long_pick("AAA"),), paths)
    rec = S.Recorder(S.assume_stop_rule)
    prim = S.trades_by_key([simulate_day(d, FREE, entry_bar_stop=rec)])
    assert S.build_candidates(prim, prim, rec.calls) == []                                      # a normal winner: not a candidate
    d2 = _day()
    rec2 = S.Recorder(S.assume_stop_rule)
    prim2 = S.trades_by_key([simulate_day(d2, FREE, entry_bar_stop=rec2)])
    cands = S.build_candidates(prim2, {}, rec2.calls)
    assert {c.symbol: c.changed for c in cands} == {"AAA": True, "BBB": True, "CCC": False}


def test_decisions_and_counts_follow_the_outcomes():
    mk = lambda o: S.Resolution(o, 5, 1, 1.0, None, None, None, None, False)                    # noqa: E731
    rs = {(DAY, "A"): mk(S.DIP_FIRST), (DAY, "B"): mk(S.REAL_STOP), (DAY, "C"): mk(S.TIE_REAL_STOP),
          (DAY, "D"): mk(S.NO_TRIGGER_PRINT), (DAY, "E"): mk(S.STOP_NEVER_TOUCHED), (DAY, "F"): mk(S.UNRESOLVED)}
    assert S.tick_decisions(rs) == {(DAY, "A"): False, (DAY, "B"): True, (DAY, "C"): True,
                                    (DAY, "D"): True, (DAY, "E"): False, (DAY, "F"): True}
    assert S.outcome_counts(rs)[S.DIP_FIRST] == 1 and sum(S.outcome_counts(rs).values()) == 6


# ================================================================================ the window loop
class StubLayer:
    """Just the `get_bars` the engine's `load_minute_bars` calls: one day's constructed minute bars."""

    def __init__(self, paths_by_day):
        self.paths_by_day = paths_by_day
        self.calls = 0

    def get_bars(self, symbols, start, end, timeframe, feed, window=None, **_kw):
        self.calls += 1
        rows = []
        for sym in symbols:
            for m, b in self.paths_by_day[start].get(sym, {}).items():
                ts = pd.Timestamp(f"{start.isoformat()} {m // 60:02d}:{m % 60:02d}", tz="US/Eastern")
                rows.append({"symbol": sym, "ts": ts, "open": b[0], "high": b[1], "low": b[2], "close": b[3], "volume": b[4]})
        return pd.DataFrame(rows, columns=["symbol", "ts", "open", "high", "low", "close", "volume"])


def test_the_window_loop_reads_each_days_bars_once_and_matches_simulating_each_rule_directly():
    d2 = date(2024, 3, 6)
    picks = {DAY: tuple(long_pick(s) for s in ("AAA", "BBB", "CCC")), d2: (long_pick("AAA"),)}
    layer = StubLayer({DAY: _paths(), d2: {"AAA": {t(9, 35): AMBIG, t(15, 59): bar(100.0, 100.0, 100.0, 100.0)}}})
    rec = S.Recorder(S.assume_stop_rule)
    out = S.simulate_window(layer, [DAY, d2], picks, FREE, {"default": None, "opt": S.ignore_stop_rule, "rec": rec})
    assert layer.calls == 2                                                                      # one read per day, three rules
    assert [r.day for r in out["default"]] == [DAY, d2]
    assert out["default"][0].trades == simulate_day(_day(), FREE).trades
    assert out["opt"][0].trades == simulate_day(_day(), FREE, entry_bar_stop=S.ignore_stop_rule).trades
    assert {x.symbol: x.exit_reason for x in out["default"][1].trades} == {"AAA": "stop_same_bar"}
    late = out["opt"][1].trades[0]                    # no entry-bar stop; the next bar opens below the stop: a gap-through stop at 100.00
    assert (late.exit_reason, late.exit_minute, late.exit_fill) == ("stop", t(15, 59), 100.0) and late.r_gross == pytest.approx(-5.0)
    assert set(rec.calls) == {(DAY, "AAA"), (DAY, "BBB"), (DAY, "CCC"), (d2, "AAA")}
    assert S.trades_by_key(out["default"]).keys() == {(DAY, "AAA"), (DAY, "BBB"), (DAY, "CCC"), (d2, "AAA")}


def test_a_pick_with_no_side_is_neither_read_nor_traded():
    doji = Pick("DDD", 3.0, None, 100.00, 101.00, 99.00, 100.00, 2.0)
    layer = StubLayer({DAY: _paths()})
    out = S.simulate_window(layer, [DAY], {DAY: (doji, long_pick("AAA"))}, FREE, {"default": None})
    assert [x.symbol for x in out["default"][0].trades] == ["AAA"] and out["default"][0].skips[0].reason == "doji"
