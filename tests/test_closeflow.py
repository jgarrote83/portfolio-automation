"""SPY close-flow: the pure logic and metrics on synthetic days with known answers (no network, no data layer)."""
import dataclasses
import inspect
import json
import math
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`

import pytest  # noqa: E402
from backtest import closeflow as CF  # noqa: E402
from backtest.closeflow import (  # noqa: E402
    ALL_VARIANTS,
    CloseFlowConfig,
    Day,
    DayOutcome,
    decide,
    go_no_go,
    share_count,
    signal_minutes,
    simulate_day,
)
from backtest.data.bars import HoldoutError  # noqa: E402

PREREG = Path(__file__).resolve().parents[1] / "docs" / "specs" / "CloseFlow_SPY_Preregistration.md"
CFG = CloseFlowConfig()
FREE = CFG.replace(entry_slippage_cents=0.0, commission_per_share=0.0)
D = date(2024, 3, 5)
HALF = date(2024, 7, 3)                                     # an NYSE early close (13:00)


def m(h, mi):
    return h * 60 + mi


def bar(o, c=None, h=None, lo=None, v=1000):
    c = o if c is None else c
    return (o, max(o, c) if h is None else h, min(o, c) if lo is None else lo, c, v)


def day(prev, daily, sig_close, entry_open, *, d=D, close=960, extra=None, sig_m=None, ent_m=None):
    sm, em = (m(15, 29), m(15, 30)) if close == 960 else (m(12, 29), m(12, 30))
    bars = {sig_m or sm: bar(sig_close), ent_m or em: bar(entry_open)}
    bars.update(extra or {})
    return Day(d, close, prev, daily, bars)


# ====================================================================== pre-registration pin
@pytest.fixture(scope="module")
def doc() -> str:
    return PREREG.read_text(encoding="utf-8")


def test_default_config_equals_the_preregistered_parameter_block(doc):
    block = json.loads(re.search(r"```json\s*(\{.*?\})\s*```", doc, re.S).group(1))
    actual = dataclasses.asdict(CloseFlowConfig())
    assert set(block) == set(actual)
    for k, v in block.items():
        assert actual[k] == v, k
        assert isinstance(actual[k], bool) == isinstance(v, bool), k
        assert isinstance(actual[k], str) == isinstance(v, str), k
    assert block["spec_version"] == CF.SPEC_VERSION == "closeflow-1.0"


def test_the_variants_are_exactly_the_preregistered_sensitivities_each_changing_one_thing(doc):
    assert CF.PRIMARY.overrides == ()
    assert [v.name for v in CF.SENSITIVITIES] == ["signal_first_half_hour", "threshold_0.5pct", "long_only",
                                                  "slippage_0c", "slippage_2c", "slippage_5c"]
    assert all(len(v.overrides) == 1 for v in CF.SENSITIVITIES)
    assert [dict(v.overrides) for v in CF.SENSITIVITIES] == [
        {"signal_mode": "first_half_hour"}, {"min_abs_r": 0.005}, {"allow_shorts": False},
        {"entry_slippage_cents": 0.0}, {"entry_slippage_cents": 2.0}, {"entry_slippage_cents": 5.0}]
    for v in ALL_VARIANTS:
        assert set(v.config(CFG).diff_from_default()) <= {k for k, _ in v.overrides}
    assert "(a) first-half-hour signal" in doc and "(d) entry slippage of 0, 2 and 5¢" in doc


def test_the_primary_rules_and_the_verdict_are_in_the_document_word_for_word(doc):
    for phrase in (
        "r = (close of the 1-min bar stamped 15:29) ÷ (previous session's daily-bar close) − 1. Raw prices",
        "r > 0 → long; r < 0 → short; r = 0 → no trade. Trade every day; **no threshold**",
        "Open of the 1-min bar stamped 15:30, plus slippage",
        "The day's SIP daily-bar close (proxy for the official closing auction, i.e. a market-on-close order). "
        "No slippage on the exit; commission still applies",
        "Signal from the bar stamped 30 minutes before the calendar close, entry at that time, exit at the close "
        "(use the early-close table verified in Phase 2)",
        "Fixed $25,000 sleeve, non-compounding; shares = floor(25,000 ÷ entry price); never levered",
        "$0.0035/share commission on entry and exit, plus **1¢/share slippage on the entry**",
        "Daily sleeve return = day P&L ÷ $25,000; no-trade days count as 0; Sharpe = mean ÷ sample std × √252, risk-free 0",
        "GO only if net Sharpe ≥ **1.0** over 2024–2025 **and** net P&L > $0 in **both** 2024 and 2025. Anything else is NO-GO.",
        "SPY only. Day trading runs in its own Alpaca account, independent of Core (CLAUDE.md rule 4 as amended), "
        "so the Core-roster check does not apply.",
    ):
        assert phrase in doc, phrase
    assert CF.GO_MIN_SHARPE == 1.0 and CF.GO_YEARS == (2024, 2025)


def test_the_ex_dividend_list_matches_the_document_and_the_saved_source_when_present(doc):
    listed = re.findall(r"^\| (20\d\d-\d\d-\d\d) \| [\d.]+ \|$", doc.split("**The ex-dividend list.**")[1], re.M)
    assert [date.fromisoformat(x) for x in listed] == list(CF.SPY_EX_DIVIDEND_DATES) and len(listed) == 7
    src = Path(__file__).resolve().parents[1] / "data" / "reference" / "spy_dividends.json"
    if src.is_file():
        rows = json.loads(src.read_text(encoding="utf-8"))["corporate_actions"]["cash_dividends"]
        in_window = [r["ex_date"] for r in rows if "2024-01-02" <= r["ex_date"] <= "2025-12-31"]
        assert in_window == listed


# ================================================================================== signal sign
def test_signal_sign_and_the_exactly_flat_day():
    assert decide(101.0, 100.0, CFG) == ("long", "trade")
    assert decide(99.0, 100.0, CFG) == ("short", "trade")
    assert decide(100.0, 100.0, CFG) == (None, "flat")                      # r = 0: no trade
    assert CF.signal_return(101.0, 100.0) == pytest.approx(0.01)
    assert CF.signal_return(99.0, 100.0) == pytest.approx(-0.01)
    # a one-cent move is a signal: there is NO threshold in the primary variant
    assert decide(100.01, 100.0, CFG) == ("long", "trade") and decide(99.99, 100.0, CFG) == ("short", "trade")
    out = simulate_day(day(100.0, 101.0, 100.0, 100.0), CFG)
    assert out.status == "flat" and out.trade is None and out.r == 0.0


def test_the_half_percent_threshold_is_inclusive_and_float_noise_safe():
    cfg = CFG.replace(min_abs_r=0.005)
    assert decide(502.5, 500.0, cfg) == ("long", "trade")                    # exactly +0.5%
    assert decide(497.5, 500.0, cfg) == ("short", "trade")                   # exactly -0.5%
    assert decide(502.4, 500.0, cfg) == (None, "below_threshold")
    assert decide(497.6, 500.0, cfg) == (None, "below_threshold")
    assert decide(100.5, 100.0, cfg) == ("long", "trade")                    # 100.5/100 - 1 is 0.00499999999999989 in floats
    assert decide(100.0, 100.0, cfg) == (None, "flat")
    assert decide(99.0, 100.0, CFG.replace(allow_shorts=False)) == (None, "short_excluded")
    assert decide(101.0, 100.0, CFG.replace(allow_shorts=False)) == ("long", "trade")


# ======================================================================== timing, no lookahead
def test_the_signal_bar_is_15_29_and_the_entry_bar_15_30_and_half_days_use_the_same_offsets():
    assert signal_minutes(960, CFG) == (m(15, 29), m(15, 30))
    assert signal_minutes(780, CFG) == (m(12, 29), m(12, 30))               # 13:00 close - 30 min; signal one minute before
    fh = CFG.replace(signal_mode="first_half_hour")
    assert signal_minutes(960, fh) == (m(9, 59), m(15, 30)) and signal_minutes(780, fh) == (m(9, 59), m(12, 30))
    with pytest.raises(ValueError):
        signal_minutes(960, CFG.replace(signal_mode="nope"))


def test_the_signal_never_uses_the_entry_bar_or_anything_after_it():
    base = day(100.0, 101.0, 100.8, 100.9)
    ref = simulate_day(base, FREE)
    assert ref.status == "traded" and ref.r == pytest.approx(0.008)
    # change EVERYTHING about the entry bar and later bars: the signal (r, side) must not move
    wild = Day(D, 960, 100.0, 101.0, {**base.bars, m(15, 30): (50.0, 500.0, 1.0, 5.0, 9),
                                       m(15, 31): bar(1.0), m(15, 59): bar(999.0), m(15, 28): bar(3.0)})
    out = simulate_day(wild, FREE)
    assert out.r == ref.r and out.trade.side == ref.trade.side == "long"
    assert out.trade.entry_open == 50.0                                      # only the ENTRY price comes from that bar
    # change the 15:29 bar's close and the signal DOES move
    moved = Day(D, 960, 100.0, 101.0, {**base.bars, m(15, 29): bar(100.8, c=99.0)})
    assert simulate_day(moved, FREE).r == pytest.approx(-0.01) and simulate_day(moved, FREE).trade.side == "short"
    # a bar at 15:30 stamped differently never stands in for 15:29, nor 15:29 for 15:30
    assert simulate_day(Day(D, 960, 100.0, 101.0, {m(15, 30): bar(100.9), m(15, 31): bar(100.8)}), FREE).reason == "no_signal_bar"


def test_a_half_day_reads_12_29_and_12_30_and_ignores_the_full_day_minutes():
    bars = {m(12, 29): bar(100.0, c=100.6), m(12, 30): bar(100.7),
            m(15, 29): bar(100.0, c=90.0), m(15, 30): bar(1.0)}              # present but must be ignored
    out = simulate_day(Day(HALF, 780, 100.0, 101.0, bars), FREE)
    assert out.status == "traded" and out.r == pytest.approx(0.006)
    assert out.trade.entry_open == 100.7 and out.trade.exit_price == 101.0
    # the same bars on a FULL day read 15:29 / 15:30 instead
    full = simulate_day(Day(D, 960, 100.0, 101.0, bars), FREE)
    assert full.r == pytest.approx(-0.10) and full.trade.side == "short" and full.trade.entry_open == 1.0


# ================================================================================= P&L arithmetic
def test_long_trade_arithmetic_with_every_cost():
    out = simulate_day(day(100.0, 102.0, 101.0, 101.10), CFG)               # r = +1%, long, exit at the daily close
    t = out.trade
    assert t.shares == math.floor(25_000 / 101.10) == 247
    assert t.entry_open == 101.10 and t.entry_fill == pytest.approx(101.11)     # open + 1c slippage
    assert t.gross_pnl == pytest.approx(247 * (102.0 - 101.10))                 # measured at the un-slipped open
    assert t.slippage_cost == pytest.approx(247 * 0.01)                          # entry only
    assert t.commission == pytest.approx(2 * 247 * 0.0035)                       # entry and exit
    assert t.net_pnl == pytest.approx(222.3 - 2.47 - 1.729) == pytest.approx(247 * (102.0 - 101.11) - 1.729)
    assert t.net_ret_bps == pytest.approx(t.net_pnl / (247 * 101.10) * 1e4)


def test_short_trade_arithmetic_with_every_cost():
    out = simulate_day(day(100.0, 98.0, 99.0, 98.90), CFG)                  # r = -1%, short
    t = out.trade
    assert t.side == "short" and t.shares == math.floor(25_000 / 98.90) == 252
    assert t.entry_fill == pytest.approx(98.89)                                   # a short SELLS at open - slippage
    assert t.gross_pnl == pytest.approx(252 * (98.90 - 98.0))
    assert t.net_pnl == pytest.approx(252 * (98.89 - 98.0) - 2 * 252 * 0.0035)
    loser = simulate_day(day(100.0, 99.5, 99.0, 98.90), CFG).trade                # the short loses when SPY rallies
    assert loser.gross_pnl == pytest.approx(252 * (98.90 - 99.5)) and loser.net_pnl < loser.gross_pnl < 0


@pytest.mark.parametrize("cents", [0.0, 1.0, 2.0, 5.0])
def test_entry_slippage_levels(cents):
    cfg = CFG.replace(entry_slippage_cents=cents)
    t = simulate_day(day(100.0, 102.0, 101.0, 101.10), cfg).trade
    assert t.slippage_cost == pytest.approx(247 * cents / 100)
    assert t.net_pnl == pytest.approx(247 * 0.90 - 247 * cents / 100 - 2 * 247 * 0.0035)
    s = simulate_day(day(100.0, 98.0, 99.0, 98.90), cfg).trade                    # adverse for a short too
    assert s.net_pnl == pytest.approx(252 * 0.90 - 252 * cents / 100 - 2 * 252 * 0.0035)


def test_shares_are_whole_and_never_levered():
    assert share_count(250.0, 25_000.0) == 100 and share_count(333.33, 25_000.0) == 75
    assert share_count(25_000.0, 25_000.0) == 1 and share_count(25_000.01, 25_000.0) == 0
    assert 0.3 / 0.1 < 3 and share_count(0.1, 0.3) == 3                            # float noise never drops a share
    assert share_count(0.0, 25_000.0) == 0 and share_count(-5.0, 25_000.0) == 0
    for price in (101.1, 333.33, 7.77, 549.99):
        assert share_count(price, 25_000.0) * price <= 25_000.0
    out = simulate_day(day(100.0, 101.0, 100.5, 30_000.0), FREE)                  # one share costs more than the sleeve
    assert out.status == "skipped" and out.reason == "zero_shares"


# ==================================================================================== missing data
def test_missing_bars_or_closes_skip_the_day_and_are_never_filled_from_another_bar():
    ok = day(100.0, 101.0, 100.5, 100.6)
    assert simulate_day(ok, CFG).status == "traded"
    no_signal = Day(D, 960, 100.0, 101.0, {m(15, 28): bar(100.4), m(15, 30): bar(100.6)})        # 15:28 is NOT a substitute
    assert (simulate_day(no_signal, CFG).status, simulate_day(no_signal, CFG).reason) == ("skipped", "no_signal_bar")
    no_entry = Day(D, 960, 100.0, 101.0, {m(15, 29): bar(100.5), m(15, 31): bar(100.7)})         # 15:31 is NOT a substitute
    assert (simulate_day(no_entry, CFG).status, simulate_day(no_entry, CFG).reason) == ("skipped", "no_entry_bar")
    assert simulate_day(day(None, 101.0, 100.5, 100.6), CFG).reason == "no_prev_close"
    assert simulate_day(day(100.0, None, 100.5, 100.6), CFG).reason == "no_daily_close"
    assert simulate_day(Day(D, 960, 100.0, 101.0, {}), CFG).reason == "no_signal_bar"
    # a skipped day has no trade and no P&L; metrics count it with its reason
    outs = [simulate_day(no_signal, CFG), simulate_day(no_entry, CFG), simulate_day(ok, CFG)]
    mt = CF.compute_metrics(outs, CFG)
    assert mt["days_skipped"] == 2 and mt["skip_reasons"] == {"no_entry_bar": 1, "no_signal_bar": 1} and mt["trades"] == 1


def test_sensitivity_a_needs_the_09_59_bar_not_the_15_29_bar():
    cfg = CFG.replace(signal_mode="first_half_hour")
    bars = {m(9, 59): bar(100.0, c=100.4), m(15, 30): bar(100.9)}                # no 15:29 bar at all
    out = simulate_day(Day(D, 960, 100.0, 101.0, bars), cfg)
    assert out.status == "traded" and out.r == pytest.approx(0.004) and out.trade.entry_open == 100.9
    assert simulate_day(Day(D, 960, 100.0, 101.0, {m(15, 29): bar(100.4), m(15, 30): bar(100.9)}), cfg).reason == "no_signal_bar"
    # the same bars under the primary variant read the 15:29 bar instead (which is missing here)
    assert simulate_day(Day(D, 960, 100.0, 101.0, bars), CFG).reason == "no_signal_bar"


# ====================================================================== holdout, determinism
def test_the_2026_holdout_is_refused_and_there_is_no_override_flag():
    with pytest.raises(HoldoutError, match="no override"):
        simulate_day(day(100.0, 101.0, 100.5, 100.6, d=date(2026, 1, 2)), CFG)
    with pytest.raises(HoldoutError):
        CF.assert_not_holdout(date(2025, 12, 31), date(2026, 1, 1))
    CF.assert_not_holdout(date(2025, 12, 31))

    class Boom:
        def __getattr__(self, name):
            raise AssertionError(f"the data layer was touched ({name}) for a 2026 request")
    with pytest.raises(HoldoutError):
        CF.load_days(Boom(), CFG, "2025-12-01", "2026-01-02")
    with pytest.raises(HoldoutError):
        CF.execute(Boom(), "2026-02-01", "2026-03-01")
    with pytest.raises(HoldoutError):
        CF.run_variants([day(100.0, 101.0, 100.5, 100.6, d=date(2026, 1, 5))], ALL_VARIANTS, CFG)
    for fn in (CF.simulate_day, CF.load_days, CF.execute, CF.run_variants, CF.cmd_run):
        assert "allow_holdout" not in inspect.signature(fn).parameters
    assert CF.main(["run", "--start", "2025-12-01", "--end", "2026-01-05"]) == 2


def test_same_inputs_give_identical_outputs_whatever_the_dict_order():
    days = []
    for i in range(1, 25):
        d = date(2024, 3, i)
        if d.weekday() < 5:
            sig = 100.0 + math.sin(i) * 2
            days.append(day(100.0, 100.0 + math.cos(i), sig, sig + 0.1, d=d,
                            extra={m(9, 59): bar(100.0, c=100 + math.sin(i + 1))}))
    a = CF.run_variants(days, ALL_VARIANTS, CFG)
    shuffled = [Day(d.day, d.close_minute, d.prev_close, d.daily_close, dict(reversed(list(d.bars.items())))) for d in reversed(days)]
    b = CF.run_variants(sorted(shuffled, key=lambda x: x.day), ALL_VARIANTS, CFG)
    c = CF.run_variants(days, ALL_VARIANTS, CFG)
    assert a == b == c
    assert CF.compute_metrics(a["primary"], CFG) == CF.compute_metrics(c["primary"], CFG)


# ======================================================================================== metrics
def _outcome(d, net, side="long", r=0.01):
    t = CF.Trade(d, side, r, 10, 100.0, 100.01, 100.0 + net / 10, net, 0.0, 0.0, net, net / 1000 * 1e4)
    return DayOutcome(d, "traded", "traded", r, t)


def _flat(d):
    return DayOutcome(d, "flat", "flat", 0.0, None)


def test_sharpe_uses_all_days_with_the_sample_std_and_the_t_stat_follows_from_it():
    nets = [100.0, -50.0, 0.0, 200.0, -100.0, 25.0]
    outs = [_outcome(date(2024, 1, 2), 100.0), _outcome(date(2024, 1, 3), -50.0, "short"), _flat(date(2024, 1, 4)),
            _outcome(date(2024, 1, 5), 200.0), _outcome(date(2024, 1, 8), -100.0, "short"), _outcome(date(2024, 1, 9), 25.0)]
    mt = CF.compute_metrics(outs, CFG)
    rets = [x / 25_000 for x in nets]
    mean = sum(rets) / 6
    std = math.sqrt(sum((x - mean) ** 2 for x in rets) / 5)
    assert mt["sharpe_net"] == pytest.approx(mean / std * math.sqrt(252), rel=1e-12)
    assert mt["t_stat_mean_daily_return"] == pytest.approx(mean / (std / math.sqrt(6)), rel=1e-12)
    assert mt["t_stat_mean_daily_return"] == pytest.approx(mt["sharpe_net"] / math.sqrt(252) * math.sqrt(6), rel=1e-12)
    assert mt["days"] == 6 and mt["days_flat"] == 1 and mt["trades"] == 5 and mt["net_pnl"] == pytest.approx(175.0)
    assert mt["hit_ratio"] == pytest.approx(3 / 5)
    assert mt["long"]["trades"] == 3 and mt["short"]["trades"] == 2 and mt["short"]["net_pnl"] == pytest.approx(-150.0)
    assert mt["short"]["hit_ratio"] == 0.0 and mt["long"]["hit_ratio"] == pytest.approx(1.0)
    assert mt["avg_net_ret_bps"] == pytest.approx(sum(n / 1000 * 1e4 for n in (100.0, -50.0, 200.0, -100.0, 25.0)) / 5)


def test_drawdown_per_year_and_undefined_sharpe():
    outs = [_outcome(date(2024, 1, 2), 100.0), _outcome(date(2024, 1, 3), -300.0), _outcome(date(2025, 1, 2), 50.0),
            _outcome(date(2025, 1, 3), -100.0)]
    mt = CF.compute_metrics(outs, CFG)
    assert mt["max_drawdown_usd"] == pytest.approx(350.0) and mt["max_drawdown_pct_of_peak"] == pytest.approx(350 / 25_100 * 100)
    assert mt["by_year"]["2024"]["net_pnl"] == pytest.approx(-200.0) and mt["by_year"]["2025"]["net_pnl"] == pytest.approx(-50.0)
    flat = CF.compute_metrics([_flat(date(2024, 1, 2)), _flat(date(2024, 1, 3))], CFG)
    assert flat["sharpe_net"] is None and flat["t_stat_mean_daily_return"] is None and flat["hit_ratio"] is None


def test_the_verdict_is_mechanical():
    good = {"sharpe_net": 1.0, "by_year": {"2024": {"net_pnl": 1.0}, "2025": {"net_pnl": 1.0}}}
    assert go_no_go(good)["verdict"] == "GO"
    assert go_no_go({**good, "sharpe_net": 0.99999999})["verdict"] == "NO-GO"
    assert go_no_go({**good, "sharpe_net": None})["verdict"] == "NO-GO"
    assert go_no_go({**good, "by_year": {"2024": {"net_pnl": 5.0}, "2025": {"net_pnl": 0.0}}})["verdict"] == "NO-GO"
    assert go_no_go({**good, "by_year": {"2024": {"net_pnl": 5.0}}})["verdict"] == "NO-GO"
    assert go_no_go({**good, "by_year": {"2024": {"net_pnl": -5.0}, "2025": {"net_pnl": 5.0}}})["checks"]["net_pnl_2024 > 0"] is False


def test_validity_requires_the_exact_preregistered_run():
    ok = {"unchanged": True}
    assert CF.validity("2024-01-02", "2025-12-31", CFG, ALL_VARIANTS, ok) == []
    assert "window" in CF.validity("2024-01-02", "2025-06-30", CFG, ALL_VARIANTS, ok)[0]
    assert "entry_slippage_cents" in CF.validity("2024-01-02", "2025-12-31", CFG.replace(entry_slippage_cents=2.0), ALL_VARIANTS, ok)[0]
    assert "variant" in CF.validity("2024-01-02", "2025-12-31", CFG, ALL_VARIANTS[:3], ok)[0]
    assert "no longer matches" in CF.validity("2024-01-02", "2025-12-31", CFG, ALL_VARIANTS, {"unchanged": False})[0]
    assert "could not be verified" in CF.validity("2024-01-02", "2025-12-31", CFG, ALL_VARIANTS, {"unchanged": None})[0]


# ================================================================================ disclosures
def test_the_ex_dividend_disclosure_lists_the_days_and_recomputes_without_them():
    ex1, ex2 = date(2024, 3, 15), date(2025, 3, 21)
    outs = [_outcome(date(2024, 1, 2), 100.0), _outcome(ex1, -400.0, "short", r=-0.003), _outcome(date(2024, 5, 1), 50.0),
            _outcome(date(2025, 1, 2), 60.0), _outcome(ex2, -500.0, "short", r=-0.0031), _outcome(date(2025, 5, 1), 70.0)]
    with_all = CF.compute_metrics(outs, CFG)
    d = CF.exdiv_disclosure(outs, CFG, exdiv=[ex1, ex2, date(2025, 12, 22)])
    assert d["n_listed"] == 3 and d["n_in_series"] == 2
    assert [r["day"] for r in d["days"]] == ["2024-03-15", "2025-03-21"] and d["days"][0]["net_pnl"] == -400.0
    me = d["metrics_excluding"]
    assert me["days"] == 4 and me["net_pnl"] == pytest.approx(280.0)
    assert me["net_pnl_2024"] == pytest.approx(150.0) and me["net_pnl_2025"] == pytest.approx(130.0)
    assert with_all["net_pnl"] < 0 < me["net_pnl"] and me["sharpe_net"] > 0
    assert me["verdict_if_excluded"] in ("GO", "NO-GO")


def test_the_close_vs_last_bar_sanity_check_uses_15_59_and_12_59():
    days = [Day(D, 960, 100.0, 100.10, {m(15, 59): bar(100.0)}),                      # +10 bps
            Day(date(2024, 3, 6), 960, 100.0, 100.05, {m(15, 59): bar(100.0)}),        # +5 bps
            Day(HALF, 780, 100.0, 99.0, {m(12, 59): bar(99.0), m(15, 59): bar(1.0)}),  # 0 bps; the 15:59 bar is irrelevant
            Day(date(2024, 3, 7), 960, 100.0, 100.0, {}), Day(date(2024, 3, 8), 960, 100.0, None, {m(15, 59): bar(100.0)})]
    s = CF.close_vs_last_bar(days)
    assert s["days_compared"] == 3 and s["mean_abs_bps"] == pytest.approx(5.0) and s["max_abs_bps"] == pytest.approx(10.0, rel=1e-3)
    assert s["max_abs_day"] == "2024-03-05"


def _feed_day(d, prev, sig_close, close=960):
    sm = m(15, 29) if close == 960 else m(12, 29)
    return Day(d, close, prev, prev, {sm: bar(sig_close)} if sig_close is not None else {})


def test_the_iex_vs_sip_check_agreement_differences_sign_flips_and_both_denominators():
    d1, d2, d3, d4 = date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6), date(2024, 3, 7)
    sip = [_feed_day(d1, 100.0, 100.50), _feed_day(d2, 100.0, 99.50), _feed_day(d3, 100.0, 100.00), _feed_day(d4, 100.0, 101.0)]
    iex = [Day(d1, 960, 100.0, 100.0, {m(15, 29): bar(100.51)}),          # +1 bp vs SIP: same sign
           Day(d2, 960, 100.0, 100.0, {m(15, 29): bar(100.02)}),          # SIP -50 bp, IEX +2 bp: sign differs
           Day(d3, 960, 100.0, 100.0, {m(15, 29): bar(100.03)}),  # SIP exactly 0, IEX +3 bp: differs (0 is a sign)
           Day(d4, 960, 100.0, 100.0, {})]                                # no IEX bar: excluded and counted
    res = CF.iex_vs_sip(sip, iex, CFG)
    a = res["A_iex_bar_over_sip_prev_close"]
    assert a["days_compared"] == 3 and res["days_without_iex_signal_bar"] == 1 and res["days_in_sip_series"] == 4
    assert a["sign_differs_n"] == 2 and a["sign_agreement_pct"] == pytest.approx(100 / 3)
    assert [x["day"] for x in a["sign_differs"]] == ["2024-03-05", "2024-03-06"]
    assert a["max_abs_diff_bps"] == pytest.approx(52.0, abs=1e-6)                    # |-50 - (+2)|
    assert a["mean_abs_diff_bps"] == pytest.approx((1 + 52 + 3) / 3, abs=1e-6)
    # B divides the IEX bar by the IEX previous close, which can differ from the SIP one
    iex_b = [Day(d1, 960, 100.20, 100.0, {m(15, 29): bar(100.51)}), *iex[1:3], iex[3]]
    b = CF.iex_vs_sip(sip, iex_b, CFG)["B_iex_bar_over_iex_prev_close"]
    assert b["days_compared"] == 3
    assert b["mean_abs_diff_bps"] == pytest.approx((abs(50.0 - (100.51 / 100.20 - 1) * 1e4) + 52 + 3) / 3, abs=1e-6)
    # a day with no IEX previous close drops out of B only
    iex_np = [Day(d1, 960, None, 100.0, {m(15, 29): bar(100.51)}), *iex[1:]]
    r2 = CF.iex_vs_sip(sip, iex_np, CFG)
    assert r2["A_iex_bar_over_sip_prev_close"]["days_compared"] == 3
    assert r2["B_iex_bar_over_iex_prev_close"]["days_compared"] == 2 and r2["days_without_iex_prev_close"] == 1


def test_a_half_day_iex_check_reads_the_12_29_bar():
    sip = [_feed_day(HALF, 100.0, 100.4, close=780)]
    iex = [Day(HALF, 780, 100.0, 100.0, {m(12, 29): bar(100.41), m(15, 29): bar(1.0)})]
    a = CF.iex_vs_sip(sip, iex, CFG)["A_iex_bar_over_sip_prev_close"]
    assert a["days_compared"] == 1 and a["mean_abs_diff_bps"] == pytest.approx(1.0, abs=1e-6)


def test_zero_is_a_sign_of_its_own_in_the_iex_check():
    d1, d2 = date(2024, 3, 4), date(2024, 3, 5)
    sip = [_feed_day(d1, 100.0, 100.0), _feed_day(d2, 100.0, 100.0)]            # SIP r exactly 0 on both days
    iex = [Day(d1, 960, 100.0, 100.0, {m(15, 29): bar(99.98)}),                  # IEX slightly negative: 0 vs -1 differs
           Day(d2, 960, 100.0, 100.0, {m(15, 29): bar(100.0)})]                  # IEX exactly 0 too: agrees
    a = CF.iex_vs_sip(sip, iex, CFG)["A_iex_bar_over_sip_prev_close"]
    assert a["sign_differs_n"] == 1 and a["sign_differs"][0]["day"] == "2024-03-04"
    assert a["sign_agreement_pct"] == pytest.approx(50.0)
