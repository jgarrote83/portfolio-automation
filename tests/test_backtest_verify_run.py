"""The post-run verifier (backtest/verify_run.py): it agrees with a correct run and catches a wrong one."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`
sys.path.insert(0, os.path.dirname(__file__))                       # tests/, for the fakes and the e2e market

import pandas as pd  # noqa: E402
import test_backtest_orb_e2e as E2E  # noqa: E402
from backtest import iex_vs_sip as P1  # noqa: E402
from backtest import reports, verify_run as V  # noqa: E402
from backtest.engine import ALL_VARIANTS, run_backtest  # noqa: E402
from backtest.selection import build_selection  # noqa: E402
from backtest.sessions import EARLY_CLOSE_DATES  # noqa: E402


def _built_run(tmp_path):
    market = E2E.OrbFakeMarket(daily_fn=lambda sym, d: (50.0, 52.0, 49.0, 51.0, 2_000_000))
    market.assets["active"] = [{"symbol": s, "exchange": "NASDAQ", "name": s} for s in E2E.SYMS]
    layer, _sess, _client = E2E._layer(tmp_path, market)
    sel = build_selection(layer, E2E.SYMS, E2E.ETFS, E2E.CFG, "2024-01-22", "2024-01-23",
                          fetch_from="2023-12-01", log=lambda m: None)
    run = run_backtest(layer, sel, E2E.CFG, ALL_VARIANTS)
    run_dir = tmp_path / "run"
    header = {"run_id": "t", "variants": reports.variants_header(ALL_VARIANTS), "valid_for_verdict": False,
              "invalid_reasons": ["test"]}
    reports.write_run(run_dir, header, run, reports.compute_all(run), None)
    p1_csv = tmp_path / "p1.csv"
    P1.run("2024-01-22", "2024-01-23", layer, csv_path=p1_csv, summary_path=None, exclude=set(),
           fetch_from="2023-12-01")
    return layer, run_dir, p1_csv


def test_the_verifier_confirms_a_correct_run_using_only_cached_data(tmp_path):
    layer, run_dir, p1_csv = _built_run(tmp_path)
    calls = layer.client.requests_made
    rep = V.check_replay(layer, run_dir, sample=12, seed=3, end="2024-01-23")
    assert rep["sampled"] == 12 and rep["matched"] == 12 and rep["pass"] and rep["differences"] == []
    sel = V.check_selection(run_dir, p1_csv)
    assert sel["pass"] and sel["not_in_phase1_top20"] == 0 and sel["etfs_included_trades"] > 0
    early = V.check_early_closes_by_volume(layer, run_dir)
    assert early["pass"] and early["table_days"] == [] and early["days_checked"] == 1     # no early close in the window
    assert layer.client.requests_made == calls                                              # zero API calls
    text = V.render({"replay": rep, "selection": sel, "early_closes": early}, run_dir)
    assert text.count("PASS") == 3 and "FAIL" not in text


def test_the_verifier_catches_a_trade_that_does_not_match_the_raw_data(tmp_path):
    layer, run_dir, p1_csv = _built_run(tmp_path)
    path = run_dir / "primary" / "trades.csv"
    df = pd.read_csv(path, dtype={"day": str})
    df.loc[0, "exit_fill"] = df.loc[0, "exit_fill"] + 0.07          # a wrong exit price
    df.loc[1, "shares"] = df.loc[1, "shares"] + 1                   # a wrong share count
    df.to_csv(path, index=False)
    rep = V.check_replay(layer, run_dir, sample=12, seed=3, end="2024-01-23")
    assert not rep["pass"] and rep["matched"] == 10
    flagged = {(d["symbol"], tuple(d["fields"])) for d in rep["differences"]}
    assert (df.loc[0, "symbol"], ("exit",)) in flagged and (df.loc[1, "symbol"], ("shares",)) in flagged
    # a trade from outside Phase 1's top 20 is caught too
    etf = run_dir / "etfs_included" / "trades.csv"
    e = pd.read_csv(etf, dtype={"day": str})
    e.loc[0, "symbol"] = "NOTINTOP20"
    e.to_csv(etf, index=False)
    sel = V.check_selection(run_dir, p1_csv)
    assert not sel["pass"] and sel["not_in_phase1_top20"] == 1


class _VolumeLayer:
    """Serves one-minute bars whose volume collapses after 13:00 on the days in `collapse`."""

    def __init__(self, collapse):
        self.collapse = {d.isoformat() for d in collapse}

    def get_bars(self, symbols, start, end, timeframe, feed, window=None, offline=False):
        rows = []
        for sym in symbols:
            for m in range(570, 960):
                ts = pd.Timestamp(year=start.year, month=start.month, day=start.day, hour=m // 60,
                                  minute=m % 60, tz="US/Eastern")
                vol = 5 if (start.isoformat() in self.collapse and m >= 13 * 60) else 100
                rows.append((sym, ts, 10.0, 10.0, 10.0, 10.0, vol))
        return pd.DataFrame(rows, columns=["symbol", "ts", "open", "high", "low", "close", "volume"])


def _trades_csv(tmp_path, days):
    run = tmp_path / "r"
    (run / "primary").mkdir(parents=True)
    pd.DataFrame([{"day": d.isoformat(), "symbol": "AAA"} for d in days]).to_csv(run / "primary" / "trades.csv", index=False)
    return run


def test_the_early_close_check_passes_when_the_collapse_days_are_exactly_the_table(tmp_path):
    days = sorted(EARLY_CLOSE_DATES) + [date(2024, 7, 2), date(2024, 7, 5), date(2025, 3, 3)]
    res = V.check_early_closes_by_volume(_VolumeLayer(EARLY_CLOSE_DATES), _trades_csv(tmp_path, days))
    assert res["pass"] and res["lowest_days"] == res["table_days"] and len(res["table_days"]) == 6
    assert res["max_table_ratio"] < res["min_other_ratio"]


def test_the_early_close_check_fails_on_a_missed_or_a_spurious_early_close(tmp_path):
    days = sorted(EARLY_CLOSE_DATES) + [date(2024, 7, 2), date(2024, 7, 5), date(2025, 3, 3)]
    run = _trades_csv(tmp_path, days)
    # the market collapsed on a day the table does NOT list (a missed early close) ...
    extra = set(EARLY_CLOSE_DATES) | {date(2025, 3, 3)}
    assert not V.check_early_closes_by_volume(_VolumeLayer(extra), run)["pass"]
    # ... and did NOT collapse on a day the table lists (a wrong table entry)
    short = set(EARLY_CLOSE_DATES) - {date(2025, 7, 3)}
    assert not V.check_early_closes_by_volume(_VolumeLayer(short), run)["pass"]


def test_latest_run_picks_the_newest_directory_with_a_header(tmp_path):
    for name in ("20261001T000000Z-a", "20261005T000000Z-b", "20261003T000000Z-c"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "header.json").write_text("{}", encoding="utf-8")
    (tmp_path / "20261009T000000Z-nohdr").mkdir()
    assert V.latest_run(tmp_path).name == "20261005T000000Z-b"
