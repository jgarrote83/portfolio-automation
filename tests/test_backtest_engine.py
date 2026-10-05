"""The minute-by-minute engine on synthetic price paths with known answers (no network, no data layer).

Standard long: first 5-min bar O 100.00 / H 101.00 / L 99.50 / C 100.80 (up), ATR $2.00 ->
trigger 101.00, stop 100.80, R = $0.20/share; at $101 the sleeve/20 cap binds -> 12 shares.
Standard short: O 100.00 / H 100.50 / L 99.00 / C 99.20 (down) -> trigger 99.00, stop 99.20.
"""
import inspect
import os
import random
import sys
from dataclasses import replace
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`

import pytest  # noqa: E402
from backtest import engine as E  # noqa: E402
from backtest.data.bars import HoldoutError  # noqa: E402
from backtest.engine import DayInput, Pick, simulate_day  # noqa: E402
from orb.config import OrbConfig  # noqa: E402
from orb.signals import LONG, SHORT  # noqa: E402

DAY = date(2024, 3, 5)
FREE = OrbConfig().replace(slippage_cents_per_side=0.0, commission_per_share=0.0)
PRIMARY = OrbConfig()


def t(h, m):
    return h * 60 + m


def bar(o, h, lo, c, v=1000):
    return (o, h, lo, c, v)


def long_pick(sym="AAA", **kw):
    return Pick(sym, kw.get("rvol", 2.0), LONG, 100.00, 101.00, 99.50, 100.80, kw.get("atr", 2.0))


def short_pick(sym="BBB", **kw):
    return Pick(sym, kw.get("rvol", 2.0), SHORT, 100.00, 100.50, 99.00, 99.20, kw.get("atr", 2.0))


def doji_pick(sym="DDD"):
    return Pick(sym, 3.0, None, 100.00, 101.00, 99.00, 100.00, 2.0)


def run(picks, paths, cfg=FREE, close_min=960, day=DAY):
    return simulate_day(DayInput(day, close_min, tuple(picks), paths), cfg)


def only(res):
    assert len(res.trades) == 1, (res.trades, res.skips)
    return res.trades[0]


# ============================================================================== known answers
def test_a_day_built_to_return_exactly_plus_3R():
    path = {t(9, 35): bar(100.90, 101.00, 100.85, 100.95),      # touches the level; low stays above the stop
            t(12, 0): bar(101.00, 101.10, 100.90, 101.00),
            t(15, 59): bar(101.40, 101.70, 101.40, 101.60)}     # close = entry + 3 x 0.20
    res = run([long_pick()], {"AAA": path})
    tr = only(res)
    assert (tr.side, tr.shares, tr.binding, tr.entry_minute, tr.exit_minute) == (LONG, 12, "cap", t(9, 35), t(15, 59))
    assert tr.entry_fill == 101.00 and tr.exit_fill == 101.60 and not tr.entry_gap
    assert tr.exit_reason == "time" and not tr.exit_stale
    assert tr.stop == 100.80 and tr.stop_distance == pytest.approx(0.20)
    assert tr.gross_pnl == pytest.approx(7.20) and tr.net_pnl == pytest.approx(7.20)
    assert tr.r_gross == pytest.approx(3.0) and tr.r_net == pytest.approx(3.0)
    assert res.net_pnl == pytest.approx(7.20) and not res.loss_limit_hit and res.skips == []


def test_the_short_mirror_of_the_plus_3R_day():
    path = {t(9, 35): bar(99.10, 99.15, 99.00, 99.05),          # touches the level; high stays below the stop
            t(12, 0): bar(99.00, 99.10, 98.90, 99.00),
            t(15, 59): bar(98.60, 98.60, 98.40, 98.40)}         # close = entry - 3 x 0.20
    tr = only(run([short_pick("BBB")], {"BBB": path}))
    assert (tr.side, tr.shares, tr.entry_fill, tr.exit_fill, tr.stop) == (SHORT, 12, 99.00, 98.40, 99.20)
    assert tr.gross_pnl == pytest.approx(7.20) and tr.r_net == pytest.approx(3.0)


def test_a_gap_through_the_entry_fills_at_the_open_not_the_level():
    path = {t(9, 35): bar(101.50, 101.60, 101.45, 101.55),      # opens above the 101.00 trigger
            t(15, 59): bar(101.60, 101.70, 101.50, 101.50)}
    tr = only(run([long_pick()], {"AAA": path}))
    assert tr.entry_fill == 101.50 and tr.entry_gap
    assert tr.stop == 100.80                                    # the stop stays at trigger - 0.1 ATR
    assert tr.gross_pnl == pytest.approx(0.0)
    # a short gapping DOWN through its 99.00 trigger
    sp = {t(9, 36): bar(98.50, 98.60, 98.40, 98.45), t(15, 59): bar(98.50, 98.55, 98.40, 98.50)}
    st = only(run([short_pick()], {"BBB": sp}))
    assert st.entry_fill == 98.50 and st.entry_gap


def test_a_gap_through_the_stop_exits_at_the_open_with_a_loss_worse_than_1R():
    path = {t(9, 35): bar(100.90, 101.00, 100.85, 100.95),      # enter 101.00
            t(10, 0): bar(100.50, 100.60, 100.30, 100.40),      # opens BELOW the 100.80 stop
            t(15, 59): bar(100.0, 100.0, 100.0, 100.0)}
    tr = only(run([long_pick()], {"AAA": path}))
    assert tr.exit_reason == "stop" and tr.exit_fill == 100.50 and tr.exit_minute == t(10, 0)
    assert tr.gross_pnl == pytest.approx(-6.00) and tr.r_net == pytest.approx(-2.5)


def test_a_stop_touched_without_a_gap_fills_at_the_stop_level_for_minus_1R():
    path = {t(9, 35): bar(100.90, 101.00, 100.85, 100.95),
            t(10, 0): bar(100.95, 100.95, 100.80, 100.90)}      # open above, low touches the stop
    tr = only(run([long_pick()], {"AAA": path}))
    assert tr.exit_reason == "stop" and tr.exit_fill == 100.80
    assert tr.gross_pnl == pytest.approx(-2.40) and tr.r_net == pytest.approx(-1.0)


def test_entry_and_stop_in_the_same_minute_bar_is_a_stop_out():
    path = {t(9, 35): bar(100.90, 101.10, 100.70, 100.95),      # high >= trigger AND low <= stop
            t(15, 59): bar(105.0, 105.0, 105.0, 105.0)}         # the "rally" after the stop never counts
    tr = only(run([long_pick()], {"AAA": path}))
    assert tr.exit_reason == "stop_same_bar" and tr.entry_fill == 101.00 and tr.exit_fill == 100.80
    assert tr.entry_minute == tr.exit_minute == t(9, 35)
    assert tr.r_net == pytest.approx(-1.0)
    # the same-bar rule with a gap entry: filled at the open, stopped at the stop level
    gp = {t(9, 35): bar(101.50, 101.60, 100.70, 101.00)}
    gt = only(run([long_pick()], {"AAA": gp}))
    assert gt.entry_fill == 101.50 and gt.exit_fill == 100.80 and gt.r_net == pytest.approx(-3.5)
    # and for a short
    sp = {t(9, 35): bar(99.10, 99.30, 98.95, 99.0)}
    st = only(run([short_pick()], {"BBB": sp}))
    assert st.exit_reason == "stop_same_bar" and st.r_net == pytest.approx(-1.0)


def test_a_day_that_never_triggers_has_no_trade_and_no_pnl():
    path = {t(9, 35): bar(100.50, 100.99, 100.40, 100.60), t(11, 0): bar(100.6, 100.9, 100.5, 100.8),
            t(15, 59): bar(100.7, 100.95, 100.6, 100.9)}
    res = run([long_pick()], {"AAA": path})
    assert res.trades == [] and res.net_pnl == 0.0
    assert [(s.symbol, s.reason) for s in res.skips] == [("AAA", "never_triggered")]


def test_a_doji_day_never_trades_even_if_price_would_have_triggered():
    path = {t(9, 35): bar(100.9, 102.0, 98.0, 100.0), t(15, 59): bar(100.0, 100.0, 100.0, 100.0)}
    res = run([doji_pick("DDD")], {"DDD": path})
    assert res.trades == [] and [(s.symbol, s.reason) for s in res.skips] == [("DDD", "doji")]


def test_bars_before_9_35_are_never_tradeable_and_one_entry_per_symbol_per_day():
    path = {t(9, 34): bar(101.5, 101.9, 101.4, 101.8),          # inside the opening range: ignored
            t(9, 35): bar(100.90, 101.00, 100.70, 100.95),      # entry + same-bar stop
            t(9, 36): bar(100.9, 101.5, 100.9, 101.4),          # would re-trigger: no re-entry
            t(15, 59): bar(101.0, 101.0, 101.0, 101.0)}
    res = run([long_pick()], {"AAA": path})
    assert len(res.trades) == 1 and res.trades[0].exit_reason == "stop_same_bar"
    early = {t(9, 34): bar(101.5, 101.9, 101.4, 101.8), t(15, 59): bar(100.0, 100.0, 100.0, 100.0)}
    assert run([long_pick()], {"AAA": early}).trades == []


# ===================================================================================== sizing
def test_the_position_cap_binds_in_a_real_trade_and_the_risk_rule_binds_when_the_stop_is_wide():
    cap = only(run([long_pick()], {"AAA": {t(9, 35): bar(100.9, 101.0, 100.85, 100.95),
                                           t(15, 59): bar(101, 101, 101, 101)}}))
    assert (cap.shares, cap.binding) == (12, "cap")
    wide = Pick("WWW", 2.0, LONG, 10.0, 10.5, 9.5, 10.2, 40.0)       # ATR $40 on a $10 stock: R = $4
    path = {t(9, 35): bar(10.4, 10.5, 10.3, 10.45), t(15, 59): bar(10.6, 10.6, 10.6, 10.6)}
    tr = only(run([wide], {"WWW": path}))
    assert tr.stop == 6.50 and tr.stop_distance == 4.0 and tr.binding == "risk"
    assert tr.shares == 62                                       # floor(250 / 4.0 = 62.5); the cap would allow 119
    assert tr.notional == pytest.approx(62 * 10.5)


def test_a_pick_too_expensive_for_one_share_is_skipped_not_traded():
    pricey = Pick("BIG", 2.0, LONG, 1499.0, 1500.0, 1490.0, 1495.0, 2.0)
    res = run([pricey], {"BIG": {t(9, 35): bar(1500, 1501, 1499, 1500), t(15, 59): bar(1500, 1500, 1500, 1500)}})
    assert res.trades == [] and [(s.symbol, s.reason) for s in res.skips] == [("BIG", "zero_shares")]


def test_a_pick_with_no_minute_bars_is_counted_and_does_not_affect_the_others():
    path = {t(9, 35): bar(100.90, 101.00, 100.85, 100.95), t(15, 59): bar(101.0, 101.0, 101.0, 101.0)}
    res = run([long_pick("AAA"), long_pick("GHOST")], {"AAA": path})
    assert [t_.symbol for t_ in res.trades] == ["AAA"]
    assert [(s.symbol, s.reason) for s in res.skips] == [("GHOST", "no_minute_data")]


def test_long_only_drops_short_picks_without_replacing_them():
    cfg = FREE.replace(allow_shorts=False)
    lp = {t(9, 35): bar(100.90, 101.00, 100.85, 100.95), t(15, 59): bar(101.0, 101.0, 101.0, 101.0)}
    sp = {t(9, 35): bar(99.10, 99.15, 99.00, 99.05), t(15, 59): bar(99.0, 99.0, 99.0, 99.0)}
    res = run([long_pick("AAA"), short_pick("BBB")], {"AAA": lp, "BBB": sp}, cfg)
    assert [x.symbol for x in res.trades] == ["AAA"]
    assert [(s.symbol, s.reason) for s in res.skips] == [("BBB", "short_excluded")]


# ====================================================================================== costs
@pytest.mark.parametrize("cents,slip", [(0.0, 0.0), (1.0, 0.24), (2.0, 0.48), (5.0, 1.20)])
def test_cost_arithmetic_at_each_slippage_level(cents, slip):
    cfg = PRIMARY.replace(slippage_cents_per_side=cents)
    path = {t(9, 35): bar(100.90, 101.00, 100.85, 100.95), t(15, 59): bar(101.4, 101.7, 101.4, 101.60)}
    tr = only(run([long_pick()], {"AAA": path}, cfg))
    commission = 2 * 12 * 0.0035                                 # a round trip = two fills of 12 shares
    assert tr.shares == 12 and tr.gross_pnl == pytest.approx(7.20)
    assert tr.commission == pytest.approx(commission) == pytest.approx(0.084)
    assert tr.slippage_cost == pytest.approx(slip)               # 2 fills x 12 sh x cents
    assert tr.net_pnl == pytest.approx(7.20 - commission - slip)
    assert tr.r_net == pytest.approx((7.20 - commission - slip) / 2.40)
    # costs hurt a stop-out the same way (a loser gets worse, not better)
    loss = {t(9, 35): bar(100.90, 101.00, 100.85, 100.95), t(10, 0): bar(100.95, 100.95, 100.80, 100.9)}
    lt = only(run([long_pick()], {"AAA": loss}, cfg))
    assert lt.net_pnl == pytest.approx(-2.40 - commission - slip)


def test_short_trades_pay_the_same_adverse_costs():
    cfg = PRIMARY
    path = {t(9, 35): bar(99.10, 99.15, 99.00, 99.05), t(15, 59): bar(98.4, 98.6, 98.4, 98.40)}
    tr = only(run([short_pick("BBB")], {"BBB": path}, cfg))
    assert tr.gross_pnl == pytest.approx(7.20)
    assert tr.net_pnl == pytest.approx(7.20 - 0.084 - 0.48)


# ============================================================================ daily loss limit
def _crash_paths():
    """Four identical longs. AAA and BBB enter at 09:35 and gap down to $60 at 10:00 / 10:10;
    CCC enters at 09:35 and sits flat; DDD would not trigger until 11:00."""
    enter = bar(100.90, 101.00, 100.85, 100.95)
    flat = bar(100.95, 101.00, 100.90, 100.95)
    crash = bar(60.0, 60.5, 59.0, 60.0)
    quiet = bar(100.2, 100.5, 100.1, 100.3)                   # never reaches the 101.00 trigger
    return {
        "AAA": {t(9, 35): enter, t(10, 0): crash, t(10, 10): flat, t(15, 59): flat},
        "BBB": {t(9, 35): enter, t(10, 0): flat, t(10, 10): crash, t(15, 59): flat},
        "CCC": {t(9, 35): enter, t(10, 0): flat, t(10, 10): flat, t(15, 59): flat},
        "DDD": {t(9, 35): quiet, t(10, 0): quiet, t(10, 10): quiet,
                t(11, 0): bar(100.9, 101.2, 100.85, 101.1), t(15, 59): flat}}


def test_the_daily_loss_limit_fires_mid_day_flattens_open_positions_and_cancels_pending_entries():
    picks = [long_pick(s) for s in ("AAA", "BBB", "CCC", "DDD")]
    res = run(picks, _crash_paths(), PRIMARY)
    by = {tr.symbol: tr for tr in res.trades}
    assert set(by) == {"AAA", "BBB", "CCC"}                       # DDD never entered
    assert by["AAA"].exit_reason == "stop" and by["AAA"].exit_fill == 60.0 and by["AAA"].exit_minute == t(10, 0)
    assert by["BBB"].exit_reason == "stop" and by["BBB"].exit_minute == t(10, 10)
    # after AAA: about -$492 (> -$750) so trading continues; after BBB the sleeve is below -$750
    assert res.loss_limit_hit and res.loss_limit_minute == t(10, 10)
    assert by["CCC"].exit_reason == "loss_limit" and by["CCC"].exit_minute == t(10, 10)
    assert by["CCC"].exit_fill == 100.95                           # flattened at that bar's close
    assert [(s.symbol, s.reason) for s in res.skips] == [("DDD", "cancelled_loss_limit")]
    assert res.net_pnl <= -750
    # nothing traded after the limit fired
    assert max(tr.exit_minute for tr in res.trades) == t(10, 10)


def test_the_loss_limit_counts_marked_positions_not_just_realized_losses():
    cfg = FREE.replace(daily_loss_limit_pct=0.004)               # $1.00 of the $25,000 sleeve
    path = {t(9, 35): bar(100.90, 101.00, 100.85, 100.95),
            t(9, 36): bar(100.95, 100.95, 100.85, 100.90),       # still above the stop: marked -$0.10 x 12 = -$1.20
            t(15, 59): bar(110.0, 110.0, 110.0, 110.0)}
    res = run([long_pick()], {"AAA": path}, cfg)
    tr = only(res)
    assert res.loss_limit_hit and res.loss_limit_minute == t(9, 36)
    assert tr.exit_reason == "loss_limit" and tr.exit_fill == 100.90 and tr.gross_pnl == pytest.approx(-1.20)


def test_a_day_with_losses_inside_the_limit_is_not_flattened_early():
    path = {t(9, 35): bar(100.90, 101.00, 100.85, 100.95), t(10, 0): bar(100.95, 100.95, 100.80, 100.90),
            t(15, 59): bar(100.0, 100.0, 100.0, 100.0)}
    res = run([long_pick()], {"AAA": path}, PRIMARY)
    assert not res.loss_limit_hit and res.trades[0].exit_reason == "stop"


# ================================================================================ session close
def test_a_full_day_enters_through_15_45_and_exits_at_the_15_59_close():
    late = {t(15, 45): bar(100.9, 101.0, 100.85, 100.95),        # the last bar an entry may trigger in
            t(15, 59): bar(101.2, 101.3, 101.1, 101.20), t(16, 0): bar(50.0, 50.0, 50.0, 50.0)}
    tr = only(run([long_pick()], {"AAA": late}))
    assert tr.entry_minute == t(15, 45) and tr.exit_minute == t(15, 59) and tr.exit_fill == 101.20
    too_late = {t(15, 46): bar(100.9, 101.0, 100.85, 100.95), t(15, 59): bar(101.2, 101.3, 101.1, 101.2)}
    assert run([long_pick()], {"AAA": too_late}).trades == []


def test_a_half_day_uses_the_calendar_close_for_the_exit_and_the_entry_cutoff():
    day = date(2024, 7, 3)
    paths = {"AAA": {t(12, 45): bar(100.9, 101.0, 100.85, 100.95),   # last entry bar on a 13:00 close
                     t(12, 59): bar(101.3, 101.4, 101.2, 101.30),     # the exit bar
                     t(13, 0): bar(50.0, 50.0, 50.0, 50.0),           # after the close: ignored
                     t(15, 59): bar(10.0, 10.0, 10.0, 10.0)}}
    tr = only(run([long_pick()], paths, close_min=13 * 60, day=day))
    assert tr.entry_minute == t(12, 45) and tr.exit_minute == t(12, 59) and tr.exit_fill == 101.30
    late = {"AAA": {t(12, 46): bar(100.9, 101.0, 100.85, 100.95), t(12, 59): bar(101.3, 101.4, 101.2, 101.3)}}
    assert run([long_pick()], late, close_min=13 * 60, day=day).trades == []


def test_a_position_with_no_bar_at_the_exit_minute_exits_at_the_last_known_close_and_says_so():
    a = {t(9, 35): bar(100.90, 101.00, 100.85, 100.95), t(14, 0): bar(101.0, 101.1, 100.95, 101.05)}
    other = {t(15, 59): bar(5.0, 5.0, 5.0, 5.0)}                  # keeps the clock running to 15:59
    res = run([long_pick("AAA"), long_pick("OTH")], {"AAA": a, "OTH": other})
    tr = [x for x in res.trades if x.symbol == "AAA"][0]
    assert tr.exit_reason == "time" and tr.exit_fill == 101.05 and tr.exit_stale and tr.exit_minute == t(15, 59)
    only_a = run([long_pick()], {"AAA": a})                       # nobody has a 15:59 bar at all
    assert only_a.trades[0].exit_stale and only_a.trades[0].exit_fill == 101.05


# ====================================================================== holdout and determinism
def test_the_engine_refuses_2026_and_has_no_override_flag():
    d = DayInput(date(2026, 1, 2), 960, (long_pick(),), {"AAA": {t(9, 35): bar(1, 1, 1, 1)}})
    with pytest.raises(HoldoutError, match="closed in Phase 2"):
        simulate_day(d, FREE)
    with pytest.raises(HoldoutError):
        E.assert_not_holdout(date(2025, 12, 31), date(2026, 1, 1))
    E.assert_not_holdout(date(2025, 12, 31))                      # the last in-sample day is fine
    for fn in (E.simulate_day, E.run_backtest, E.assert_not_holdout):
        assert "allow_holdout" not in inspect.signature(fn).parameters
    from backtest import selection
    assert "allow_holdout" not in inspect.signature(selection.build_selection).parameters


def test_same_inputs_give_identical_outputs_whatever_the_dict_order():
    rng = random.Random(7)
    syms = [f"S{i:02d}" for i in range(12)]
    picks = [Pick(s, 1.0 + i / 10, LONG if i % 2 else SHORT, 50.0, 50.6, 49.4, 50.3 if i % 2 else 49.6, 3.0)
             for i, s in enumerate(syms)]
    paths = {s: {m: bar(*(lambda p: (p, p + 0.7, p - 0.7, p + rng.uniform(-0.5, 0.5)))(50 + rng.uniform(-1, 1)))
                 for m in range(t(9, 35), t(15, 59) + 1, 7)} for s in syms}
    a = simulate_day(DayInput(DAY, 960, tuple(picks), paths), PRIMARY)
    shuffled_picks = list(reversed(picks))
    shuffled_paths = {s: paths[s] for s in reversed(list(paths))}
    b = simulate_day(DayInput(DAY, 960, tuple(shuffled_picks), shuffled_paths), PRIMARY)
    c = simulate_day(DayInput(DAY, 960, tuple(picks), paths), PRIMARY)
    assert a.trades == b.trades == c.trades and a.skips == c.skips
    assert replace(a).net_pnl == c.net_pnl
