"""News event study: the pure logic on synthetic days with known answers (no network, no data layer)."""
import dataclasses
import inspect
import json
import math
import os
import re
import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`
sys.path.insert(0, os.path.dirname(__file__))                       # tests/, for backtest_fakes

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from backtest import news_gap as NG  # noqa: E402
from backtest.data.bars import HoldoutError  # noqa: E402
from backtest.data.store import normalise_news  # noqa: E402
from backtest.news_gap import (  # noqa: E402
    ALL_VARIANTS,
    Candidate,
    DayOutcome,
    NewsGapConfig,
    NewsIndex,
    classify_headlines,
    overnight_window,
    simulate_day,
)
from backtest_fakes import article  # noqa: E402

PREREG = Path(__file__).resolve().parents[1] / "docs" / "specs" / "News_Gap_Preregistration.md"
CFG = NewsGapConfig()
FREE = CFG.replace(entry_slippage_cents=0.0, commission_per_share=0.0)
D = date(2024, 3, 12)


# ====================================================================== pre-registration pin
@pytest.fixture(scope="module")
def doc() -> str:
    return PREREG.read_text(encoding="utf-8")


def test_default_config_equals_the_preregistered_parameter_block(doc):
    block = json.loads(re.search(r"```json\s*(\{.*?\})\s*```", doc, re.S).group(1))
    actual = dataclasses.asdict(NewsGapConfig())
    assert set(block) == set(actual)
    for k, v in block.items():
        assert actual[k] == v, k
        assert isinstance(actual[k], bool) == isinstance(v, bool), k
        assert isinstance(actual[k], str) == isinstance(v, str), k
    assert block["spec_version"] == NG.SPEC_VERSION == "newsgap-1.0" and CFG.slot_capital_usd == 5000.0


def test_the_variants_are_exactly_the_preregistered_diagnostics_each_changing_one_thing():
    assert NG.PRIMARY.overrides == ()
    assert [v.name for v in NG.DIAGNOSTICS] == ["control_no_news", "confirmation_filter", "long_only",
                                                "slippage_0c", "slippage_1c", "slippage_5c"]
    assert [dict(v.overrides) for v in NG.DIAGNOSTICS] == [
        {"kind": "control"}, {"require_confirmation": True}, {"allow_shorts": False},
        {"entry_slippage_cents": 0.0}, {"entry_slippage_cents": 1.0}, {"entry_slippage_cents": 5.0}]
    assert all(len(v.overrides) == 1 for v in NG.DIAGNOSTICS) and ALL_VARIANTS[0] is NG.PRIMARY


def test_the_rules_and_the_keyword_list_are_in_the_document_word_for_word(doc):
    for phrase in (
        "Day-D open ≥ $10; prior-20-day average dollar volume (close × volume, prior days only) ≥ $50M; not an ETF "
        "(the Phase 2 Nasdaq Trader list); has daily bars for D−1 and D",
        "At least one Alpaca news article whose `symbols` list contains the stock **and has at most 2 symbols**, with "
        "`created_at` in [16:00 ET on D−1, 09:30 ET on D). Use `created_at` only, never `updated_at`",
        "Rank day D's events by \\|g\\| descending (ties by symbol ascending); take the **top 5**",
        "Open of the 1-min bar stamped 10:00, plus slippage. No bar at 10:00 → skip and count; never substitute another bar",
        "Fixed $25,000 sleeve, non-compounding, 5 slots of $5,000; shares = floor(5,000 ÷ entry price); unused slots stay idle; never levered",
        "$0.0035/share commission on entry and exit, plus **2¢/share slippage on the entry**",
        "GO only if net Sharpe ≥ **1.0** over 2024–2025 **and** net P&L > $0 in **both** years. Anything else is NO-GO.",
        "Earnings: earnings, EPS, results, revenue, quarter, Q1–Q4. Guidance: guidance, outlook, forecast, raises, lowers, cuts. "
        "Analyst: upgrade, downgrade, price target, initiates. Deal: acquire, acquisition, merger, buyout, takeover. "
        "Regulatory: FDA, approval, trial, phase. Offering: offering, priced, dilution.",
    ):
        assert phrase in doc, phrase
    for ty, kws in NG.EVENT_TYPE_KEYWORDS.items():
        flat = [k for k in kws if not re.fullmatch(r"Q\d", k)]
        assert all(k in doc for k in flat), ty
    assert NG.GO_MIN_SHARPE == 1.0 and NG.GO_YEARS == (2024, 2025)


# ===================================================================== the overnight window
def _ts(s):
    return pd.Timestamp(s)


def test_the_overnight_window_boundaries_start_inclusive_end_exclusive():
    # D = Tuesday 2024-03-12; D-1 = Monday 03-11 (EDT: ET = UTC-4)
    start, end = overnight_window(date(2024, 3, 11), date(2024, 3, 12))
    assert start == _ts("2024-03-11T20:00:00Z") and end == _ts("2024-03-12T13:30:00Z")
    news = normalise_news([
        article(1, "2024-03-11T19:59:59Z", ["AAA"]),      # one second before 16:00 on D-1: outside
        article(2, "2024-03-11T20:00:00Z", ["AAA"]),      # exactly 16:00:00 on D-1: counts
        article(3, "2024-03-12T13:29:59Z", ["AAA"]),      # one second before 09:30 on D: counts
        article(4, "2024-03-12T13:30:00Z", ["AAA"]),      # exactly 09:30:00 on D: does NOT count
        article(5, "2024-03-12T13:30:01Z", ["AAA"])])
    hits = NewsIndex(news).matching("AAA", start, end)
    assert [h[0] for h in hits] == [2, 3]


def test_the_window_uses_each_dates_own_et_offset_across_the_dst_change_and_spans_weekends():
    # Friday 2024-03-08 (EST, UTC-5) 16:00 -> Monday 2024-03-11 (EDT, UTC-4) 09:30: the March DST change is in between
    start, end = overnight_window(date(2024, 3, 8), date(2024, 3, 11))
    assert start == _ts("2024-03-08T21:00:00Z") and end == _ts("2024-03-11T13:30:00Z")
    assert (end - start) == pd.Timedelta(hours=64, minutes=30)                        # a 65-hour weekend minus the lost hour
    news = normalise_news([article(1, "2024-03-09T15:00:00Z", ["AAA"]), article(2, "2024-03-10T12:00:00Z", ["AAA"]),
                           article(3, "2024-03-08T20:59:59Z", ["AAA"])])
    assert [h[0] for h in NewsIndex(news).matching("AAA", start, end)] == [1, 2]       # weekend news counts; 15:59:59 EST does not
    # autumn change: Friday 2024-11-01 (EDT) -> Monday 2024-11-04 (EST)
    s2, e2 = overnight_window(date(2024, 11, 1), date(2024, 11, 4))
    assert s2 == _ts("2024-11-01T20:00:00Z") and e2 == _ts("2024-11-04T14:30:00Z")
    # an event day whose D-1 is a holiday-shortened gap uses the previous TRADING day passed in
    s3, _ = overnight_window(date(2023, 12, 29), date(2024, 1, 2))
    assert s3 == _ts("2023-12-29T21:00:00Z")


def test_the_two_symbol_rule_and_exact_ticker_matching():
    cfg = CFG
    assert NG.article_matches(["AAA"], "AAA", cfg) and NG.article_matches(["AAA", "BBB"], "BBB", cfg)
    assert not NG.article_matches(["AAA", "BBB", "CCC"], "AAA", cfg)              # three symbols: never a match
    assert not NG.article_matches([], "AAA", cfg)
    assert not NG.article_matches(["AAAA"], "AAA", cfg) and not NG.article_matches(["AA"], "AAA", cfg)
    assert NG.article_matches(["aaa"], "AAA", cfg) and NG.article_matches(["BRK.B"], "BRK.B", cfg)
    news = normalise_news([article(1, "2024-03-12T12:00:00Z", ["AAA", "BBB", "CCC"]), article(2, "2024-03-12T12:01:00Z", ["AAA", "BBB"]),
                           article(3, "2024-03-12T12:02:00Z", []), article(4, "2024-03-12T12:03:00Z", ["BBB"])])
    idx = NewsIndex(news)
    s, e = overnight_window(date(2024, 3, 11), D)
    assert [h[0] for h in idx.matching("AAA", s, e)] == [2] and [h[0] for h in idx.matching("BBB", s, e)] == [2, 4]
    assert idx.matching("CCC", s, e) == [] and idx.matching("ZZZ", s, e) == []
    assert NewsIndex(news, CFG.replace(max_news_symbols=3)).matching("CCC", s, e)[0][0] == 1


def test_only_created_at_is_used_never_updated_at():
    news = normalise_news([article(1, "2024-03-12T14:00:00Z", ["AAA"], updated_at="2024-03-12T12:00:00Z"),        # edited INTO the window? no: created after
                           article(2, "2024-03-12T12:00:00Z", ["AAA"], updated_at="2024-03-12T14:30:00Z")])      # created inside, updated after
    s, e = overnight_window(date(2024, 3, 11), D)
    hits = NewsIndex(news).matching("AAA", s, e)
    assert [h[0] for h in hits] == [2] and hits[0][2] is True                      # flagged as updated != created


# ============================================================================ universe and gap
def test_universe_thresholds_and_the_exact_20_day_dollar_volume_window():
    closes, vols = [50.0] * 20, [1_000_000.0] * 20                                  # $50M exactly
    adv = NG.avg_dollar_volume(closes, vols, 20)
    assert adv == 50_000_000.0 and NG.in_universe(10.0, 50.0, adv, CFG)                # open >= $10 and adv >= $50M inclusive
    assert not NG.in_universe(9.99, 50.0, adv, CFG) and not NG.in_universe(10.0, 50.0, adv - 1, CFG)
    assert not NG.in_universe(10.0, None, adv, CFG) and not NG.in_universe(None, 50.0, adv, CFG)
    assert not NG.in_universe(10.0, 50.0, None, CFG) and not NG.in_universe(10.0, 0.0, adv, CFG)
    assert NG.avg_dollar_volume(closes[:19], vols[:19], 20) is None                     # exactly 20 days, no fewer
    assert NG.avg_dollar_volume([*closes[:19], None], vols, 20) is None and NG.avg_dollar_volume(closes, [*vols[:19], float("nan")], 20) is None
    # close x volume per day, then the mean (not mean close x mean volume)
    assert NG.avg_dollar_volume([10.0, 30.0], [30.0, 10.0], 2) == pytest.approx((300 + 300) / 2)
    assert NG.avg_dollar_volume([10.0, 30.0], [10.0, 30.0], 2) == pytest.approx((100 + 900) / 2) != (20 * 20)


def test_the_gap_is_open_over_the_previous_close_and_qualifies_at_exactly_two_percent():
    assert NG.gap_return(102.0, 100.0) == pytest.approx(0.02) and NG.gap_return(98.0, 100.0) == pytest.approx(-0.02)
    assert NG.gap_qualifies(0.02, CFG) and NG.gap_qualifies(-0.02, CFG) and not NG.gap_qualifies(0.019999, CFG)
    assert NG.gap_qualifies(NG.gap_return(51.0, 50.0), CFG)                         # 51/50 - 1 is 0.020000000000000018 in floats
    assert NG.gap_qualifies(NG.gap_return(49.0, 50.0), CFG)                         # 49/50 - 1 is -0.020000000000000018
    assert NG.gap_qualifies(NG.gap_return(102.0, 100.0), CFG)                       # 102/100 - 1 is 0.020000000000000018


# ================================================================ candidates: events vs controls
def _panels(n_days=24, symbols=("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "ETFX", "LOWV"), start="2024-02-01"):
    cal = [d.date() for d in pd.bdate_range(start, periods=n_days)]
    shape = (n_days, len(symbols))
    p = {"open": pd.DataFrame(np.full(shape, 50.0), index=cal, columns=list(symbols)),
         "close": pd.DataFrame(np.full(shape, 50.0), index=cal, columns=list(symbols)),
         "volume": pd.DataFrame(np.full(shape, 2_000_000.0), index=cal, columns=list(symbols))}
    return cal, p


def _news_for(day_prev, day, entries):
    """entries: (id, symbols, 'in'|'before'|'at_end'|'at_start')"""
    s, e = overnight_window(day_prev, day)
    when = {"in": s + pd.Timedelta(hours=5), "before": s - pd.Timedelta(seconds=1), "at_end": e, "at_start": s}
    return normalise_news([article(i, when[w].strftime("%Y-%m-%dT%H:%M:%SZ"), syms) for i, syms, w in entries])


def test_events_and_controls_are_split_by_the_overnight_news_and_ranked_by_abs_gap_then_symbol():
    cal, p = _panels()
    D2, P2 = cal[-1], cal[-2]
    sym = list(p["open"].columns)
    gaps = {"AAA": 0.05, "BBB": -0.04, "CCC": 0.05, "DDD": 0.03, "EEE": 0.0199, "FFF": -0.07, "ETFX": 0.09, "LOWV": 0.06}
    for s, g in gaps.items():
        p["open"].loc[D2, s] = 50.0 * (1 + g)
    p["volume"]["LOWV"] = 10_000.0                                                  # $0.5M a day: outside the universe
    news = _news_for(P2, D2, [(1, ["AAA"], "in"), (2, ["BBB", "AAA"], "in"), (3, ["CCC"], "in"), (4, ["DDD", "X", "Y"], "in"),
                              (5, ["EEE"], "in"), (6, ["FFF"], "at_start"), (7, ["ETFX"], "in"), (8, ["LOWV"], "in")])
    plans = NG.build_plans(p, cal, [D2], sym, NewsIndex(news), frozenset({"ETFX"}))
    plan = plans[0]
    assert plan.prev_day == P2 and plan.n_gap_candidates == 5                     # AAA BBB CCC DDD FFF (EEE < 2%, ETFX/LOWV out)
    assert [c.symbol for c in plan.events] == ["FFF", "AAA", "CCC", "BBB"]           # |g| desc; AAA before CCC at 5% (symbol asc)
    assert [c.symbol for c in plan.controls] == ["DDD"]                              # news with 3 symbols does not match
    f = plan.events[0]
    assert f.g == pytest.approx(-0.07) and f.n_articles == 1 and f.article_ids == (6,) and f.is_event
    assert plan.events[1].article_ids == (1, 2)                                      # AAA matched article 1 and article 2 (two symbols)
    assert plan.controls[0].n_articles == 0 and not plan.controls[0].is_event
    # a news article OUTSIDE the window (one second before 16:00 on D-1, or exactly at 09:30 on D) leaves a gap a control
    news2 = _news_for(P2, D2, [(1, ["AAA"], "before"), (2, ["BBB"], "at_end"), (3, ["CCC"], "at_start")])
    plan2 = NG.build_plans(p, cal, [D2], sym, NewsIndex(news2), frozenset({"ETFX"}))[0]
    assert [c.symbol for c in plan2.events] == ["CCC"] and [c.symbol for c in plan2.controls] == ["FFF", "AAA", "BBB", "DDD"]


def test_the_universe_needs_20_prior_days_a_previous_close_a_10_dollar_open_and_a_non_etf():
    cal, p = _panels()
    D2 = cal[-1]
    sym = list(p["open"].columns)
    for s in ("AAA", "BBB", "CCC", "DDD"):
        p["open"].loc[D2, s] = 50.0 * 1.05
    p["close"].loc[cal[-5], "AAA"] = np.nan                                         # a missing day inside AAA's 20-day window
    p["close"].loc[cal[-2], "BBB"] = np.nan                                         # no previous-day close for BBB
    p["open"].loc[D2, "CCC"] = 9.5                                                  # below $10 (and a -81% "gap")
    plan = NG.build_plans(p, cal, [D2], sym, NewsIndex(pd.DataFrame()), frozenset())[0]
    assert [c.symbol for c in plan.controls] == ["DDD"] and plan.events == []
    with pytest.raises(ValueError, match="prior trading days"):
        NG.build_plans(p, cal, [cal[5]], sym, NewsIndex(pd.DataFrame()), frozenset())


def test_raw_prices_make_a_split_look_like_a_gap_and_it_is_ranked_first():
    cal, p = _panels()
    D2 = cal[-1]
    p["open"].loc[D2, "AAA"] = 50.0 / 4                                             # a 4-for-1 split on raw prices: -75%
    p["open"].loc[D2, "BBB"] = 50.0 * 1.04
    plan = NG.build_plans(p, cal, [D2], list(p["open"].columns), NewsIndex(pd.DataFrame()), frozenset())[0]
    assert [c.symbol for c in plan.controls] == ["AAA", "BBB"] and plan.controls[0].g == pytest.approx(-0.75)


def test_top_n_ranking_and_ties():
    def c(sym, g):
        return Candidate(D, sym, g, 50.0, 50.0, 50.0, 1e8, 1)
    cands = [c("ZZZ", 0.05), c("AAA", -0.05), c("MMM", 0.05), c("BBB", 0.10), c("CCC", -0.03), c("DDD", 0.02), c("EEE", 0.021)]
    got = NG.rank_candidates(cands, 5)
    assert [x.symbol for x in got] == ["BBB", "AAA", "MMM", "ZZZ", "CCC"]           # 10%, then 5% x3 by symbol, then 3%
    assert NG.rank_candidates(cands, 100)[-1].symbol == "DDD" and NG.rank_candidates([], 5) == []
    assert [x.symbol for x in NG.rank_candidates(cands, 2)] == ["BBB", "AAA"]


# ===================================================================== trading, no lookahead
def cand(sym="AAA", g=0.05, daily_open=50.0, close=52.0, day=D):
    return Candidate(day, sym, g, daily_open, daily_open / (1 + g), close, 1e8, 1, (1,), ("h",), ("other",))


def test_a_gap_up_goes_long_and_a_gap_down_short_with_the_entry_at_the_10_00_open():
    out = simulate_day(D, [cand("UP", 0.05, 50.0, 52.0), cand("DN", -0.04, 48.0, 47.0)], {"UP": 50.5, "DN": 47.6}, FREE)
    up, dn = out.trades
    assert (up.side, up.entry_open, up.exit_price) == ("long", 50.5, 52.0) and up.gross_pnl == pytest.approx(up.shares * 1.5)
    assert (dn.side, dn.entry_open, dn.exit_price) == ("short", 47.6, 47.0) and dn.gross_pnl == pytest.approx(dn.shares * 0.6)
    assert out.skips == ()


def test_entry_uses_only_the_10_00_open_not_the_exit_or_anything_later():
    base = simulate_day(D, [cand("AAA", 0.05, 50.0, 52.0)], {"AAA": 50.5}, FREE).trades[0]
    wild = simulate_day(D, [cand("AAA", 0.05, 50.0, 90.0)], {"AAA": 50.5}, FREE).trades[0]   # a very different close
    assert wild.entry_open == base.entry_open == 50.5 and wild.shares == base.shares and wild.side == base.side
    assert wild.exit_price == 90.0 and wild.gross_pnl != base.gross_pnl                       # only the P&L moves
    # the decision (event, direction, rank) is fixed before 10:00: changing the 10:00 open changes only price and size
    other = simulate_day(D, [cand("AAA", 0.05, 50.0, 52.0)], {"AAA": 40.0}, FREE).trades[0]
    assert other.side == "long" and other.shares == math.floor(5000 / 40.0) and other.entry_open == 40.0


def test_a_missing_10_00_bar_is_skipped_counted_and_never_replaced():
    picks = [cand(f"S{i}", 0.09 - i * 0.01) for i in range(6)]                      # six picks; the engine is handed the top 5
    out = simulate_day(D, picks[:5], {f"S{i}": (None if i == 1 else 50.5) for i in range(6)}, FREE)
    assert [t.symbol for t in out.trades] == ["S0", "S2", "S3", "S4"] and out.skips == (("S1", "no_10_00_bar"),)
    assert "S5" not in [t.symbol for t in out.trades]                                     # the sixth is NOT promoted
    assert simulate_day(D, [cand()], {}, FREE).skips == (("AAA", "no_10_00_bar"),)
    assert simulate_day(D, [cand()], {"AAA": float("nan")}, FREE).skips == (("AAA", "no_10_00_bar"),)


def test_integer_shares_idle_slots_and_the_sleeve_is_never_levered():
    t = simulate_day(D, [cand("AAA", 0.05, 50.0, 52.0)], {"AAA": 333.33}, FREE).trades[0]
    assert t.shares == 15 == math.floor(5000 / 333.33)                                     # whole shares from a $5,000 slot
    out = simulate_day(D, [cand("A"), cand("B")], {"A": 50.0, "B": 50.0}, FREE)               # two picks -> three idle slots
    assert len(out.trades) == 2 and sum(t.shares * t.entry_open for t in out.trades) <= CFG.sleeve_capital_usd * 2 / 5
    expensive = simulate_day(D, [cand()], {"AAA": 6000.0}, FREE)                            # one share costs more than a slot
    assert expensive.trades == () and expensive.skips == (("AAA", "zero_shares"),)
    assert 0.3 / 0.1 < 3 and NG.share_count(0.1, 0.3) == 3                                 # float noise never drops a share
    assert NG.share_count(0.0, 5000.0) == 0 and NG.share_count(100.0, 5000.0) == 50
    for price in (12.34, 99.99, 333.33, 1234.5):
        assert NG.share_count(price, 5000.0) * price <= 5000.0


def test_cost_arithmetic_with_entry_slippage_and_two_sided_commission():
    t = simulate_day(D, [cand("AAA", 0.05, 50.0, 52.0)], {"AAA": 50.5}, CFG).trades[0]        # 2c slippage, long
    sh = math.floor(5000 / 50.5)
    assert t.shares == sh == 99 and t.entry_fill == pytest.approx(50.52)
    assert t.gross_pnl == pytest.approx(99 * 1.5) and t.slippage_cost == pytest.approx(99 * 0.02)
    assert t.commission == pytest.approx(2 * 99 * 0.0035) and t.net_pnl == pytest.approx(148.5 - 1.98 - 0.693)
    assert t.net_ret_bps == pytest.approx(t.net_pnl / (99 * 50.5) * 1e4)
    s = simulate_day(D, [cand("DN", -0.04, 48.0, 47.0)], {"DN": 47.6}, CFG).trades[0]          # short: sells at open - 2c
    assert s.entry_fill == pytest.approx(47.58) and s.shares == math.floor(5000 / 47.6) == 105
    assert s.gross_pnl == pytest.approx(105 * 0.6) and s.net_pnl == pytest.approx(105 * 0.6 - 105 * 0.02 - 2 * 105 * 0.0035)
    for cents in (0.0, 1.0, 5.0):
        v = simulate_day(D, [cand("AAA", 0.05, 50.0, 52.0)], {"AAA": 50.5}, CFG.replace(entry_slippage_cents=cents)).trades[0]
        assert v.slippage_cost == pytest.approx(99 * cents / 100) and v.net_pnl == pytest.approx(99 * 1.5 - 99 * cents / 100 - 0.693)
    loser = simulate_day(D, [cand("AAA", 0.05, 50.0, 49.0)], {"AAA": 50.5}, CFG).trades[0]       # a long that falls loses, plus costs
    assert loser.net_pnl < loser.gross_pnl < 0


def test_the_confirmation_filter_and_long_only_drop_picks_without_replacing_them():
    picks = [cand("UPOK", 0.05, 50.0, 52.0), cand("UPNO", 0.05, 50.0, 52.0), cand("DNOK", -0.04, 48.0, 47.0),
             cand("DNNO", -0.04, 48.0, 47.0), cand("FLAT", 0.03, 50.0, 51.0)]
    bars = {"UPOK": 50.2, "UPNO": 49.8, "DNOK": 47.5, "DNNO": 48.4, "FLAT": 50.0}               # 10:00 open vs the day open
    out = simulate_day(D, picks, bars, CFG.replace(require_confirmation=True))
    assert [t.symbol for t in out.trades] == ["UPOK", "DNOK"]
    assert sorted(out.skips) == [("DNNO", "not_confirmed"), ("FLAT", "not_confirmed"), ("UPNO", "not_confirmed")]   # equal prices: no
    lo = simulate_day(D, picks, bars, CFG.replace(allow_shorts=False))
    assert [t.symbol for t in lo.trades] == ["UPOK", "UPNO", "FLAT"] and sorted(lo.skips) == [("DNNO", "short_excluded"), ("DNOK", "short_excluded")]


def test_the_control_takes_the_no_news_picks_with_the_same_rule():
    cal, p = _panels()
    D2 = cal[-1]
    p["open"].loc[D2, "AAA"] = 52.5
    p["open"].loc[D2, "BBB"] = 47.0
    news = _news_for(cal[-2], D2, [(1, ["AAA"], "in")])
    plan = NG.build_plans(p, cal, [D2], list(p["open"].columns), NewsIndex(news), frozenset())[0]
    assert [c.symbol for c in plan.picks(CFG)] == ["AAA"] and [c.symbol for c in plan.picks(CFG.replace(kind="control"))] == ["BBB"]
    assert plan.picks(CFG.replace(top_n=0)) == []


def test_the_2026_holdout_is_refused_everywhere_and_there_is_no_override_flag(tmp_path):
    with pytest.raises(HoldoutError, match="no override"):
        simulate_day(date(2026, 1, 2), [cand(day=date(2026, 1, 2))], {"AAA": 50.0}, FREE)
    with pytest.raises(HoldoutError):
        NG.assert_not_holdout(date(2025, 12, 31), date(2026, 1, 1))
    NG.assert_not_holdout(date(2025, 12, 31))

    class Boom:
        def __getattr__(self, name):
            raise AssertionError(f"the data layer was touched ({name}) for a 2026 request")
    with pytest.raises(HoldoutError):
        NG.prepare(Boom(), "2025-12-01", "2026-01-02", CFG, etfs=frozenset())
    data = NG.StudyData([date(2026, 1, 5)], [], {}, pd.DataFrame(), 0, 0)
    with pytest.raises(HoldoutError):
        NG.run_variants(data, ALL_VARIANTS)
    for fn in (NG.simulate_day, NG.prepare, NG.run_variants, NG.cmd_run, NG.load_bar10, NG.load_daily_panels):
        assert "allow_holdout" not in inspect.signature(fn).parameters
    assert NG.main(["run", "--start", "2025-12-01", "--end", "2026-01-05", "--out", str(tmp_path)]) == 2


def test_determinism_same_inputs_give_identical_outputs():
    picks = [cand(f"S{i}", (-1) ** i * (0.03 + i / 100), 50.0 + i, 51.0 + i * 0.3) for i in range(5)]
    bars = {f"S{i}": 50.0 + i + 0.2 for i in range(5)}
    r1 = simulate_day(D, picks, bars, CFG)
    r2 = simulate_day(D, picks, dict(reversed(list(bars.items()))), CFG)
    assert r1 == r2 and len(r1.trades) == 5
    assert [t.symbol for t in r1.trades] == [f"S{i}" for i in range(5)]


# ==================================================================== keyword classification
@pytest.mark.parametrize("headline,types", [
    ("Acme Q2 EPS beats estimates, revenue up 12%", ("earnings",)),
    ("Acme raises full-year guidance after strong quarter", ("earnings", "guidance")),
    ("Analyst upgrade: Acme price target lifted to $80", ("analyst",)),
    ("Acme to acquire Beta in a $2B takeover", ("deal",)),
    ("FDA approval for Acme's phase 3 trial", ("regulatory",)),
    ("Acme prices public offering of 10 million shares", ("offering",)),
    ("Acme shares jump premarket", ("other",)),
    ("Why Acme Stock Is Moving Today", ("other",)),
    ("Seiko Epson unveils new printers", ("other",)),                                  # 'EPS' is a whole-word token: not 'Epson'
    ("Funds keep eyeing steps ahead", ("other",)),                                      # not 'eps' inside 'keep'/'steps'
    ("Acme price targets raised across the Street", ("analyst",)),                      # 'price targets' matches 'price target'; 'raised' is not 'raises'
])
def test_the_keyword_classifier_on_fixed_examples(headline, types):
    assert classify_headlines([headline]) == types


def test_classifier_rules_case_whole_word_acronyms_prefixes_and_multiple_headlines():
    assert classify_headlines(["fda clears device"]) == ("regulatory",) and classify_headlines(["Q3 results"]) == ("earnings",)
    assert classify_headlines(["Q5 results"]) == ("earnings",) and classify_headlines(["QQ3 outlook"]) == ("guidance",)
    assert classify_headlines(["Acme acquires Beta"]) == ("deal",) and classify_headlines(["Acme was acquired"]) == ("deal",)
    assert classify_headlines(["Quarterly dividend declared"]) == ("earnings",)           # 'quarter' at a word start
    assert classify_headlines(["Acme price   target cut"]) == ("analyst",)                  # flexible whitespace in a phrase
    assert classify_headlines(["Acme cuts forecast"]) == ("guidance",) and classify_headlines(["Acme cut costs"]) == ("other",)
    # several headlines: the union of their types, in the fixed order
    assert classify_headlines(["Acme prices offering", "Analyst initiates coverage", "Acme EPS"]) == ("earnings", "analyst", "offering")
    assert classify_headlines([]) == ("other",)


def test_the_type_breakdown_counts_a_multi_type_event_in_each_type():
    def tr(net, types):
        return NG.Trade(D, "AAA", "news", "long", 0.05, 10, 50.0, 50.0, 50.02, 51.0, net, 0.2, 0.07, net, net / 500 * 1e4, types)
    trades = [tr(10.0, ("earnings", "guidance")), tr(-5.0, ("earnings",)), tr(2.0, ("other",)), tr(4.0, ())]
    bd = NG.type_breakdown(trades)
    assert bd["earnings"]["trades"] == 2 and bd["earnings"]["hit_ratio"] == 0.5 and bd["guidance"]["trades"] == 1
    assert bd["other"]["trades"] == 2 and bd["deal"]["trades"] == 0 and bd["deal"]["avg_net_bps"] is None
    assert bd["earnings"]["avg_net_bps"] == pytest.approx((200.0 + -100.0) / 2)
    assert set(bd) == set(NG.EVENT_TYPES)


# ========================================================================= metrics and tests
def _day(d, nets, ev=3):
    ts = tuple(NG.Trade(d, f"S{i}", "news", "long", 0.05, 10, 50.0, 50.0, 50.0, 50.0 + n / 10, n, 0.0, 0.0, n, n / 500 * 1e4) for i, n in enumerate(nets))
    return DayOutcome(d, ts, (), ev, 0)


def test_sharpe_uses_every_day_with_no_event_days_as_zero_and_the_sample_std():
    nets = [100.0, -50.0, 0.0, 200.0, -100.0, 25.0]
    outs = [_day(date(2024, 1, 2), [60.0, 40.0]), _day(date(2024, 1, 3), [-50.0]), _day(date(2024, 1, 4), [], ev=0),
            _day(date(2024, 1, 5), [200.0]), _day(date(2024, 1, 8), [-100.0]), _day(date(2024, 1, 9), [25.0])]
    m = NG.compute_metrics(outs, CFG)
    rets = [x / 25_000 for x in nets]
    mean = sum(rets) / 6
    std = math.sqrt(sum((x - mean) ** 2 for x in rets) / 5)
    assert m["sharpe_net"] == pytest.approx(mean / std * math.sqrt(252), rel=1e-12)
    assert m["t_stat_mean_daily_return"] == pytest.approx(mean / (std / math.sqrt(6)), rel=1e-12)
    assert m["days"] == 6 and m["trades"] == 6 and m["days_with_no_event"] == 1 and m["days_with_no_trade"] == 1
    assert m["net_pnl"] == pytest.approx(175.0) and m["hit_ratio"] == pytest.approx(4 / 6) and m["events_per_day_median"] == 3
    assert m["long"]["trades"] == 6 and m["short"]["trades"] == 0 and m["avg_net_bps"] == pytest.approx(sum(n / 500 * 1e4 for n in [60, 40, -50, 200, -100, 25]) / 6)


def test_drawdown_per_year_and_the_mechanical_verdict():
    outs = [_day(date(2024, 1, 2), [100.0]), _day(date(2024, 1, 3), [-300.0]), _day(date(2025, 1, 2), [50.0]), _day(date(2025, 1, 3), [-100.0])]
    m = NG.compute_metrics(outs, CFG)
    assert m["max_drawdown_usd"] == pytest.approx(350.0) and m["by_year"]["2024"]["net_pnl"] == pytest.approx(-200.0)
    assert NG.compute_metrics([_day(date(2024, 1, 2), [], ev=0)] * 2, CFG)["sharpe_net"] is None
    good = {"sharpe_net": 1.0, "by_year": {"2024": {"net_pnl": 1.0}, "2025": {"net_pnl": 1.0}}}
    assert NG.go_no_go(good)["verdict"] == "GO"
    assert NG.go_no_go({**good, "sharpe_net": 0.99999999})["verdict"] == "NO-GO" and NG.go_no_go({**good, "sharpe_net": None})["verdict"] == "NO-GO"
    assert NG.go_no_go({**good, "by_year": {"2024": {"net_pnl": 5.0}, "2025": {"net_pnl": 0.0}}})["verdict"] == "NO-GO"
    assert NG.go_no_go({**good, "by_year": {"2024": {"net_pnl": 5.0}}})["verdict"] == "NO-GO"


def test_welch_t_matches_a_hand_computation_and_handles_thin_samples():
    a, b = [10.0, 14.0, 9.0, 12.0], [3.0, 5.0, 4.0, 8.0, 1.0]
    w = NG.welch_t(a, b)
    m1, m2 = statistics.fmean(a), statistics.fmean(b)
    s1, s2 = statistics.variance(a), statistics.variance(b)
    expected = (m1 - m2) / math.sqrt(s1 / 4 + s2 / 5)
    assert w["t_stat"] == pytest.approx(expected, rel=1e-12) and w["difference"] == pytest.approx(m1 - m2)
    assert (w["n_news"], w["n_control"]) == (4, 5) and w["mean_news"] == pytest.approx(m1)
    assert NG.welch_t([1.0], [1.0, 2.0])["t_stat"] is None and NG.welch_t([], [])["difference"] is None
    assert NG.welch_t([1.0, 1.0], [1.0, 1.0])["t_stat"] is None                          # zero variance: undefined, not infinite
    assert NG.welch_t([5.0, 7.0, 6.0], [1.0, 2.0, 3.0])["difference"] == pytest.approx(4.0)


def test_the_validity_rule_requires_the_exact_preregistered_run():
    ok = {"unchanged": True}
    assert NG.validity("2024-01-02", "2025-12-31", CFG, ALL_VARIANTS, ok) == []
    assert "window" in NG.validity("2024-01-02", "2025-06-30", CFG, ALL_VARIANTS, ok)[0]
    assert "top_n" in NG.validity("2024-01-02", "2025-12-31", CFG.replace(top_n=3), ALL_VARIANTS, ok)[0]
    assert "variant" in NG.validity("2024-01-02", "2025-12-31", CFG, ALL_VARIANTS[:3], ok)[0]
    assert "no longer matches" in NG.validity("2024-01-02", "2025-12-31", CFG, ALL_VARIANTS, {"unchanged": False})[0]
    assert "could not be verified" in NG.validity("2024-01-02", "2025-12-31", CFG, ALL_VARIANTS, {"unchanged": None})[0]


def test_the_news_statistics_and_the_largest_gap_disclosure():
    news = normalise_news([article(1, "2024-03-12T12:00:00Z", ["AAA"], updated_at="2024-03-12T12:00:05Z"),
                           article(2, "2024-03-12T12:01:00Z", ["AAA", "BBB"]), article(3, "2024-03-12T12:02:00Z", []),
                           article(4, "2024-03-12T12:03:00Z", ["A", "B", "C"], source="other")])
    c1 = Candidate(D, "AAA", 0.05, 52.5, 50.0, 53.0, 1e8, 2, (1, 2), ("a", "b"), ("other",), (True, False))
    c2 = Candidate(D, "BBB", -0.30, 35.0, 50.0, 33.0, 1e8, 1, (2,), ("b",), ("other",), (False,))
    plan = NG.DayPlan(D, date(2024, 3, 11), 2, [c1, c2], [])
    st = NG.news_stats(news, [plan])
    assert st["articles"] == 4 and st["articles_at_most_2_symbols"] == 2 and st["articles_empty_symbols"] == 1
    assert st["by_source"] == {"benzinga": 3, "other": 1} and st["share_updated_differs_all"] == 0.25
    assert st["matched_articles"] == 2 and st["share_updated_differs_matched"] == 0.5            # articles 1 (changed) and 2
    out = [simulate_day(D, plan.picks(CFG), {"AAA": 52.0, "BBB": 34.0}, CFG)]
    big = NG.largest_gaps([plan], out, CFG, n=1)
    assert big[0]["symbol"] == "BBB" and big[0]["side"] == "short" and big[0]["g_pct"] == pytest.approx(-30.0) and big[0]["traded"]
    assert NG.largest_gaps([plan], out, CFG, n=5)[1]["symbol"] == "AAA"


def test_the_dollar_volume_average_uses_prior_days_only_never_the_day_itself():
    cal, p = _panels()
    D2 = cal[-1]
    p["volume"].loc[:, :] = 900_000.0                                              # $45M a day for every prior day
    p["volume"].loc[D2, "AAA"] = 40_000_000.0                                      # day D itself is enormous ($2B)
    p["open"].loc[D2, "AAA"] = 52.5                                                # a 5% gap
    p["volume"].loc[cal[-2], "BBB"] = 20_000_000.0                                 # ... but a PRIOR day is enormous for BBB
    p["open"].loc[D2, "BBB"] = 52.5
    plan = NG.build_plans(p, cal, [D2], list(p["open"].columns), NewsIndex(pd.DataFrame()), frozenset())[0]
    assert [c.symbol for c in plan.controls] == ["BBB"]                              # AAA's own day does not lift it over $50M
    assert plan.controls[0].adv == pytest.approx((19 * 45_000_000 + 50.0 * 20_000_000) / 20)
