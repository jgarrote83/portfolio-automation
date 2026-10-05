"""Symbols the bars endpoint rejects: the summary, its persistence, the recovery probe, and both reports."""
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`
sys.path.insert(0, os.path.dirname(__file__))                       # tests/, for backtest_fakes

import pytest  # noqa: E402
from backtest import iex_vs_sip as R  # noqa: E402
from backtest.data import DataLayer  # noqa: E402
from backtest.data.bars import CacheMissError, HoldoutError  # noqa: E402
from backtest.data.client import AlpacaDataClient  # noqa: E402
from backtest.data.manifest import Manifest  # noqa: E402
from backtest.data.ratelimit import RateLimiter  # noqa: E402
from backtest.invalid import LIST_MAX, SAMPLE_N, invalid_summary, render_invalid_md  # noqa: E402
from backtest_fakes import FakeClock, FakeMarket, RejectingSession  # noqa: E402


def NOW():
    return datetime(2025, 6, 1, tzinfo=timezone.utc)


def _layer(tmp_path, market, bad=(), with_client=True):
    clk = FakeClock()
    sess = RejectingSession(market, bad)
    client = AlpacaDataClient("K", "S", session=sess, sleep=clk.sleep,
                              limiter=RateLimiter(180, 10, clock=clk.now, sleep=clk.sleep))
    return DataLayer(tmp_path / "cache", client=client if with_client else None, now=NOW), sess, client


# ================================================================================ the summary
def test_fewer_than_100_rejected_symbols_are_listed_in_full():
    bad = [f"0{i:08d}" for i in range(37)]
    d = invalid_summary(bad + ["IGNORED"], considered=bad + ["AAA", "BBB"], daily_symbols=["AAA", "BBB"],
                        eligible_union=["AAA"])
    assert d["count"] == 37 and d["listing"] == {"mode": "full", "symbols": sorted(bad)}
    assert d["count_recorded_in_cache"] == 38                 # one recorded symbol is outside this run's universe
    assert d["returned_a_daily_bar"] == [] and d["passed_universe_filter"] == []


def test_100_or_more_gives_a_deterministic_evenly_spaced_sample_of_20_plus_the_count():
    bad = [f"X{i:04d}" for i in range(LIST_MAX)]              # exactly 100: NOT "under 100"
    d = invalid_summary(bad, bad, [], [])
    assert d["count"] == 100 and d["listing"]["mode"] == "sample"
    sample = d["listing"]["symbols"]
    assert len(sample) == SAMPLE_N == 20 and sample == sorted(sample) and set(sample) <= set(bad)
    assert sample[0] == "X0000" and sample[1] == "X0005" and sample[-1] == "X0095"    # every 5th
    assert invalid_summary(bad[::-1], bad, [], []) == d                               # order-independent
    assert invalid_summary(bad[:99], bad, [], [])["listing"]["mode"] == "full"        # 99 -> full list


def test_the_check_flags_a_rejected_symbol_that_returned_bars_or_passed_the_filter():
    d = invalid_summary(["BAD1", "BAD2", "BAD3"], ["BAD1", "BAD2", "BAD3", "OK"], daily_symbols=["OK", "BAD2"],
                        eligible_union=["OK", "BAD3"])
    assert d["returned_a_daily_bar"] == ["BAD2"] and d["passed_universe_filter"] == ["BAD3"]
    md = render_invalid_md(d)
    assert "UNEXPECTED" in md and "BAD2" in md and "BAD3" in md and "none of them returned" not in md


def test_the_rendered_section_states_the_count_the_list_and_the_confirmation():
    ok = render_invalid_md(invalid_summary(["0029900E0", "046CVR015"], ["0029900E0", "046CVR015", "AAA"],
                                           ["AAA"], ["AAA"]))
    assert "rejected **2** of this run" in ok and "Full list (2): `0029900E0`, `046CVR015`." in ok
    assert "none of them returned a single daily bar, and none passed the universe filter" in ok
    names = [f"Z{i:03d}" for i in range(250)]
    big = render_invalid_md(invalid_summary(names, names, [], []))
    assert "rejected **250**" in big and "Sample of 20" in big and "sorted list of 250" in big
    assert render_invalid_md(None) == "" and render_invalid_md({}) == ""


# ================================================================================ persistence
def test_the_manifest_remembers_rejected_symbols_across_processes(tmp_path):
    path = tmp_path / "m.sqlite"
    m = Manifest(path)
    assert m.record_invalid(["B", "A", "A"], "2026-10-05T00:00:00+00:00", "bars_400") == 2
    assert m.record_invalid(["A", "C"], "2026-10-05T01:00:00+00:00", "probe") == 1      # A already known
    m.close()
    assert Manifest(path).invalid_symbols() == ["A", "B", "C"]
    assert Manifest(path).record_invalid([], "x", "y") == 0


def test_a_layer_persists_what_the_client_saw_and_a_fresh_layer_without_a_client_can_report_it(tmp_path):
    layer, _sess, client = _layer(tmp_path, FakeMarket(), bad={"0029900E0"})
    df = layer.get_bars(["AAA", "0029900E0"], "2024-01-02", "2024-01-03", "1Day", "sip")
    assert set(df["symbol"]) == {"AAA"} and client.invalid_symbols == {"0029900E0"}
    assert layer.manifest.invalid_symbols() == ["0029900E0"]
    assert layer.cache_stats()["invalid_symbols_dropped"] == 1
    fresh = DataLayer(tmp_path / "cache", client=None, now=NOW)            # no keys, no client: an offline report
    assert fresh.invalid_symbols() == ["0029900E0"] and fresh.cache_stats()["invalid_symbols_dropped"] == 1


def test_probe_recovers_the_list_for_a_cache_built_before_it_was_persisted(tmp_path):
    market = FakeMarket(halted=[("QUIET", date(2024, 1, 2))])
    market.assets["active"] = [{"symbol": s, "exchange": "NYSE", "name": s} for s in ("AAA", "QUIET")]
    market.assets["inactive"] = [{"symbol": s, "exchange": "NASDAQ", "name": s, "status": "inactive"}
                                 for s in ("0029900E0", "046CVR015", "GONE")]
    market.assets["inactive"].append({"symbol": "OTCJUNK", "exchange": "OTC", "name": "x", "status": "inactive"})
    layer, sess, _client = _layer(tmp_path, market, bad={"0029900E0", "046CVR015", "OTCJUNK"})
    layer.get_assets()
    layer.get_bars(["AAA"], "2024-01-02", "2024-01-03", "1Day", "sip")      # AAA has bars; the rest were never fetched
    n_bars_calls = len(sess.bar_calls)
    out = layer.probe_invalid()
    # candidates = equity-exchange symbols with no cached bars: QUIET, GONE and the two junk NASDAQ codes (not OTC)
    assert out == {"candidates": 4, "newly_recorded": 2, "total_recorded": 2}
    assert layer.manifest.invalid_symbols() == ["0029900E0", "046CVR015"]
    asked = {s for c in sess.bar_calls[n_bars_calls:] for s in c["params"]["symbols"].split(",")}
    assert asked <= {"QUIET", "GONE", "0029900E0", "046CVR015"} and "AAA" not in asked and "OTCJUNK" not in asked
    # it only probed: nothing from the probe landed in the bar cache
    assert layer.get_bars(["AAA"], "2024-01-02", "2024-01-03", "1Day", "sip", offline=True)["symbol"].nunique() == 1
    with pytest.raises(CacheMissError):
        layer.get_bars(["GONE"], "2024-01-02", "2024-01-03", "1Day", "sip", offline=True)


def test_the_probe_refuses_a_holdout_day(tmp_path):
    layer, _sess, _client = _layer(tmp_path, FakeMarket())
    with pytest.raises(HoldoutError):
        layer.probe_invalid(day=date(2026, 1, 5))


# ====================================================== both reports carry the section end to end
def _junk_market():
    market = FakeMarket(daily_fn=lambda sym, d: (50.0, 52.0, 49.0, 51.0, 2_000_000))
    syms = [f"S{i:02d}" for i in range(24)]
    market.assets["active"] = [{"symbol": s, "exchange": "NYSE", "name": s} for s in syms]
    market.assets["inactive"] = [{"symbol": "0029900E0", "exchange": "NYSE", "name": "junk", "status": "inactive"},
                                 {"symbol": "ABC_DELISTED", "exchange": "NASDAQ", "name": "x", "status": "inactive"}]
    return market


def test_the_phase_1_report_lists_the_rejected_symbols_and_confirms_none_passed_the_filter(tmp_path):
    layer, sess, _client = _layer(tmp_path, _junk_market(), bad={"0029900E0", "ABC_DELISTED"})
    md = tmp_path / "out.md"
    _df, stats = R.run("2024-01-22", "2024-01-23", layer, csv_path=None, summary_path=md,
                       exclude=set(), fetch_from="2023-12-01")
    inv = stats["invalid"]
    assert inv["count"] == 2 and inv["listing"]["symbols"] == ["0029900E0", "ABC_DELISTED"]
    assert inv["returned_a_daily_bar"] == [] and inv["passed_universe_filter"] == []
    text = md.read_text(encoding="utf-8")
    assert "## Symbols the bars endpoint rejected" in text and "`0029900E0`, `ABC_DELISTED`" in text
    assert "none of them returned a single daily bar" in text
    assert "symbols the API rejected as invalid and that were dropped: 2" in text
    # the rejected symbols never became candidates, so were never asked for opening-range bars
    five_min = {s for c in sess.bar_calls if c["params"]["timeframe"] == "5Min"
                for s in c["params"]["symbols"].split(",")}
    assert not ({"0029900E0", "ABC_DELISTED"} & five_min)


def test_the_phase_2_summary_carries_the_same_section(tmp_path):
    from backtest.engine import ALL_VARIANTS, run_backtest
    from backtest.reports import compute_all, render_summary_md, variants_header
    from backtest.selection import build_selection
    from orb.config import OrbConfig
    layer, _s, _c = _layer(tmp_path, _junk_market(), bad={"0029900E0", "ABC_DELISTED"})
    symbols = ["0029900E0", "ABC_DELISTED"] + [f"S{i:02d}" for i in range(24)]
    sel = build_selection(layer, symbols, frozenset(), OrbConfig(), "2024-01-22", "2024-01-23",
                          fetch_from="2023-12-01", log=lambda m: None)
    assert "0029900E0" not in sel.daily_symbols and "0029900E0" not in sel.eligible_union
    assert sel.eligible_union <= sel.daily_symbols and len(sel.eligible_union) == 24
    inv = invalid_summary(layer.invalid_symbols(), symbols, sel.daily_symbols, sel.eligible_union)
    assert inv["count"] == 2 and inv["passed_universe_filter"] == [] and inv["returned_a_daily_bar"] == []
    run = run_backtest(layer, sel, OrbConfig(), ALL_VARIANTS)
    header = {"run_id": "t", "variants": variants_header(ALL_VARIANTS), "valid_for_verdict": False,
              "invalid_reasons": ["test"], "invalid_symbols": inv}
    md = render_summary_md(header, compute_all(run), None)
    assert "## Symbols the bars endpoint rejected" in md and "`0029900E0`, `ABC_DELISTED`" in md
    assert "none passed the universe filter on any day" in md
