"""The runner's pieces on constructed data: the Phase 2 reproduction check, the per-trade tick fetch and
resolution, the trades CSV, and the report (including the pre-committed closing-sentence rule)."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from backtest import reports  # noqa: E402
from backtest import samebar as S  # noqa: E402
from backtest import samebar_report as R  # noqa: E402
from backtest import samebar_run as run  # noqa: E402
from backtest.data.store import normalise_trades  # noqa: E402
from backtest.engine import DayInput, Pick, simulate_day  # noqa: E402
from orb.config import OrbConfig  # noqa: E402
from orb.signals import LONG  # noqa: E402

DAY = date(2024, 3, 5)
FREE = OrbConfig().replace(slippage_cents_per_side=0.0, commission_per_share=0.0)
ET = "US/Eastern"


def bar(o, h, lo, c, v=1000):
    return (o, h, lo, c, v)


def t(h, m):
    return h * 60 + m


def long_pick(sym):
    return Pick(sym, 2.0, LONG, 100.00, 101.00, 99.50, 100.80, 2.0)


AMBIG = bar(100.90, 101.10, 100.70, 100.95)
PATHS = {"AAA": {t(9, 35): AMBIG, t(15, 59): bar(101.40, 101.70, 101.40, 101.60)},                # dip first, then +3R
         "BBB": {t(9, 35): AMBIG, t(9, 36): bar(100.95, 100.95, 100.80, 100.85)},                 # stopped a bar later
         "CCC": {t(9, 35): bar(101.50, 101.60, 100.70, 101.00), t(15, 59): bar(101.4, 101.7, 101.4, 101.6)}}


def _day():
    return DayInput(DAY, 960, tuple(long_pick(s) for s in ("AAA", "BBB", "CCC")), PATHS)


def _frame(rows):
    df = normalise_trades(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(ET)
    df.insert(0, "symbol", "AAA")
    return df


def tr(sec, price, seq_codes=("@",), size=100, i=1):
    return {"t": f"2024-03-05T14:35:{sec:09.6f}Z", "p": price, "s": size, "c": list(seq_codes), "i": i}


# the 09:35 minute of AAA: an odd-lot print at 100.50 (counts for nothing), then a real dip to 100.70, then the fill
AAA_PRINTS = [tr(1, 100.90), tr(2, 100.50, ("@", "I"), 10, 2), tr(3, 100.70, i=3), tr(4, 100.95, i=4),
              tr(5, 101.05, i=5), tr(6, 100.95, i=6)]


class StubLayer:
    def __init__(self, frames):
        self.frames = frames
        self.fetched = []

    def get_trades(self, symbol, start, end, **_kw):
        self.fetched.append((symbol, start, end))
        return self.frames[symbol]

    def trade_conditions(self, tape):
        return {"@": "Regular Sale", "I": "Odd Lot Trade", "F": "Inter-market Sweep Order", "Z": "Sold (Out Of Sequence)"}


def _scenarios():
    d = _day()
    rec = S.Recorder(S.assume_stop_rule)
    default = [simulate_day(d, FREE)]
    assume = [simulate_day(d, FREE, entry_bar_stop=rec)]
    opt = [simulate_day(d, FREE, entry_bar_stop=S.ignore_stop_rule)]
    cands = S.build_candidates(S.trades_by_key(assume), S.trades_by_key(opt), rec.calls)
    return d, default, opt, cands


# ============================================================================== reproduction check
def test_the_reproduction_check_matches_a_phase2_trades_csv_and_names_every_difference(tmp_path):
    _d, default, _opt, _c = _scenarios()
    path = tmp_path / "trades.csv"
    run.write_trades_csv(path, default)
    ok = run.check_reproduces_phase2(default, path)
    assert ok["identical"] and ok["phase2_trades"] == ok["reproduced_trades"] == 3 and ok["mismatches"] == 0
    df = pd.read_csv(path, dtype={"day": str})
    df.loc[0, "net_pnl"] += 1.0                                       # one trade differs by a dollar
    df = df.iloc[:-1]                                                 # and one is missing from the reference
    df.to_csv(path, index=False)
    bad = run.check_reproduces_phase2(default, path)
    assert not bad["identical"] and bad["mismatches"] == 1 and bad["extra_in_rerun"] == 1
    assert bad["first_mismatches"][0][2] == "differs"


def test_the_trades_csv_has_the_phase2_columns_and_one_row_per_trade(tmp_path):
    _d, default, _opt, _c = _scenarios()
    path = tmp_path / "x" / "trades.csv"
    run.write_trades_csv(path, default)
    df = pd.read_csv(path, dtype={"day": str})
    assert list(df.columns) == reports.TRADE_COLUMNS and len(df) == 3 and set(df["day"]) == {"2024-03-05"}


# ======================================================================== the tick fetch and replay
def test_each_changed_trade_is_fetched_for_its_exact_entry_minute_and_resolved_under_every_filter():
    _d, _default, _opt, cands = _scenarios()
    changed = [c for c in cands if c.changed]
    assert [c.symbol for c in changed] == ["AAA"]
    layer = StubLayer({"AAA": _frame(AAA_PRINTS)})
    sets = {"primary": S.PRIMARY_EXCLUDED, "minimal": S.MINIMAL_EXCLUDED, "none": frozenset()}
    res, checks, raw = run.fetch_and_resolve(layer, changed, sets, log=lambda _m: None)
    (sym, start, end), = layer.fetched
    assert sym == "AAA" and start == pd.Timestamp("2024-03-05 09:35:00", tz=ET) and end == start + pd.Timedelta(seconds=60)
    key = (DAY, "AAA")
    assert res["primary"][key].outcome == S.DIP_FIRST and res["minimal"][key].outcome == S.DIP_FIRST
    assert res["primary"][key].n_prints == 5 and res["none"][key].n_prints == 6        # the odd lot only counts with no filter
    assert checks["primary"][key]["open"] and raw[key] is layer.frames["AAA"]
    # without the filter the odd-lot print at 100.50 is the first stop touch, and the bar no longer matches
    assert res["none"][key].stop_before_price == 100.50 and not checks["none"][key]["low"]
    # tick-derived (100.90, 101.05, 100.70, 100.95) against Alpaca's bar (100.90, 101.10, 100.70, 100.95): only the high differs
    assert checks["primary"][key] == {"open": True, "high": False, "low": True, "close": True}


def test_the_odd_lot_filter_can_flip_an_answer():
    # the ONLY print at/through the stop AFTER the fill is an odd lot: a real stop without the filter, none with it
    rows = [tr(1, 100.90), tr(2, 101.05, i=2), tr(3, 100.60, ("@", "I"), 10, 3), tr(4, 100.95, i=4)]
    _d, _default, _opt, cands = _scenarios()
    layer = StubLayer({"AAA": _frame(rows)})
    res, _checks, _raw = run.fetch_and_resolve(layer, [c for c in cands if c.changed], {"primary": S.PRIMARY_EXCLUDED, "none": frozenset()},
                                               log=lambda _m: None)
    key = (DAY, "AAA")
    assert res["primary"][key].outcome == S.STOP_NEVER_TOUCHED and res["none"][key].outcome == S.REAL_STOP
    assert S.tick_decisions(res["primary"])[key] is False and S.tick_decisions(res["none"])[key] is True


# ================================================================================= the report
def _render(passed=False, monkeypatch=None):
    d, default, opt, cands = _scenarios()
    changed = [c for c in cands if c.changed]
    layer = StubLayer({"AAA": _frame(AAA_PRINTS)})
    sets = {"primary": S.PRIMARY_EXCLUDED, "minimal": S.MINIMAL_EXCLUDED, "none": frozenset()}
    res, checks, raw = run.fetch_and_resolve(layer, changed, sets, log=lambda _m: None)
    resolved = [simulate_day(d, FREE, entry_bar_stop=S.ResolvedRule(S.tick_decisions(res["primary"])))]
    metrics = {name: reports.compute_metrics(r, FREE) for name, r in
               {"reported": default, "optimistic": opt, "tick_resolved": resolved, "tick_resolved_minimal_filter": resolved,
                "tick_resolved_no_filter": default, "changed_set_all_dips": opt}.items()}
    header = {"run_id": "RUN1", "api_requests_this_run": 7, "phase2_run": "P2",
              "reproduces_phase2": {"phase2_trades": 3, "reproduced_trades": 3, "mismatches": 0, "extra_in_rerun": 0, "identical": True},
              "explicit_pre_registered_rule_equals_default": True, "changed_set_all_real_equals_default": True}
    if passed:
        monkeypatch.setattr(reports, "go_no_go", lambda m: {"verdict": "GO", "checks": {"a": True, "b": True, "c": True}})
    validation = {"n": 1, "outcomes": {S.REAL_STOP: 1}, "at_trigger": 0, "through_trigger": 1}
    return R.render(header, cands, changed, res, checks, raw, metrics, layer, 1, validation), metrics


def test_the_report_states_the_counts_the_three_results_the_codes_and_the_example_sequences():
    text, metrics = _render()
    assert "Reported (Phase 2)" in text and "Optimistic bound" in text and "Tick-resolved" in text
    assert "`stop_same_bar` exits in the Phase 2 primary | 3 |" in text
    assert "ambiguous (the entry bar opened short of the trigger) | 2 |" in text and "**changed set** (ambiguous, and the optimistic R differs" in text
    assert "| **Total** | **1** | 1 | 0 |" in text                    # one changed trade, a long
    assert "stop level traded only BEFORE the trigger print" in text and "dip_first" in text
    assert "| `I` | Odd Lot Trade |" in text and "| `Z` | Sold (Out Of Sequence) |" in text
    assert "### Example 1" in text and "outcome: dip_first" in text and "first print at/through the trigger (the fill): 09:35:05.000000000 @ 101.05" in text
    assert "Identical: **yes**" in text and "equals the engine default (yes)" in text
    assert "**Timestamp resolution.**" in text and "of 6 prints" in text and "In plain language." in text
    assert "Only the order of events inside the entry minute was resolved." in text
    assert "this run counts **2**" in text and "all 1 unambiguous entries (0 opened at the trigger, 1 opened through it)" in text
    assert "1 real_stop, 0 tie_real_stop" in text
    assert metrics["reported"]["net_pnl"] != metrics["optimistic"]["net_pnl"]


def test_the_closing_sentence_follows_the_rule_fixed_before_the_run(monkeypatch):
    text, _m = _render()                                              # a one-day sample cannot reach Sharpe 1.0 in both years
    assert R.SENTENCE_UNCHANGED in text and R.SENTENCE_MISMEASURED not in text
    text2, _m2 = _render(passed=True, monkeypatch=monkeypatch)        # all three pre-registered checks passing
    assert R.SENTENCE_MISMEASURED in text2 and R.SENTENCE_UNCHANGED not in text2
    assert "not a verdict" in text and "stays NO-GO" in text


def test_the_two_candidate_sentences_are_the_ones_in_the_brief():
    assert R.SENTENCE_MISMEASURED == ("ORB v1 was mis-measured; a v2 with tick-resolved entries may be worth pre-registering "
                                      "for the 2026 holdout.")
    assert R.SENTENCE_UNCHANGED == "The ambiguity doesn't change the conclusion."


def test_no_2026_date_can_reach_the_diagnostic(monkeypatch):
    with pytest.raises(Exception, match="closed in Phase 2"):
        run.assert_not_holdout(date(2026, 1, 2))
    assert run.START == "2024-01-02" and run.END == "2025-12-31"
