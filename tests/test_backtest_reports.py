"""Metrics, the mechanical verdict, the ETF list, session closes, provenance, run validity."""
import json
import math
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from backtest import cli, provenance, reports  # noqa: E402
from backtest.engine import ALL_VARIANTS, BacktestRun, DayResult, Skip, Trade  # noqa: E402
from backtest.etf import (  # noqa: E402
    EtfListMissingError,
    load_etf_list,
    parse_etf_symbols,
)
from backtest.sessions import EARLY_CLOSE_DATES, close_minute  # noqa: E402
from orb.config import OrbConfig  # noqa: E402
from orb.signals import LONG, SHORT  # noqa: E402

CFG = OrbConfig()


def trade(day, net, *, side=LONG, r=None, binding="cap", stale=False, gap=False, reason="time",
          sym="AAA", shares=10, stop_distance=0.2):
    r = (net / (shares * stop_distance)) if r is None else r
    return Trade(day=day, symbol=sym, side=side, shares=shares, binding=binding, rvol=2.0, atr=2.0,
                 trigger=100.0, stop=99.8, stop_distance=stop_distance, entry_fill=100.0, entry_minute=575,
                 entry_gap=gap, exit_fill=100.0, exit_minute=959, exit_reason=reason, exit_stale=stale,
                 gross_pnl=net + 1.0, slippage_cost=0.6, commission=0.4, net_pnl=net, r_gross=r + 0.1,
                 r_net=r, notional=1000.0)


def day_result(d, nets=(), **kw):
    res = DayResult(d, picks_n=len(nets), loss_limit_hit=kw.pop("loss_limit_hit", False))
    res.trades = [trade(d, n, **kw) for n in nets]
    return res


def series(year, nets_per_day, start_month=1):
    """One DayResult per entry; each entry is a list of trade nets ([] = no-trade day)."""
    return [day_result(date(year, start_month, 2 + i), nets) for i, nets in enumerate(nets_per_day)]


# ====================================================================================== metrics
def test_sharpe_uses_every_day_with_no_trade_days_as_zero_and_the_sample_std():
    nets = [100.0, -50.0, 0.0, 200.0, -100.0, 25.0]                    # day 3 had no trade
    res = series(2024, [[100.0], [-50.0], [], [120.0, 80.0], [-100.0], [25.0]])
    m = reports.compute_metrics(res, CFG)
    rets = np.array(nets) / 25_000
    expected = rets.mean() / rets.std(ddof=1) * math.sqrt(252)
    assert m["sharpe_net"] == pytest.approx(expected, rel=1e-12)
    assert m["days"] == 6 and m["days_with_no_trades"] == 1 and m["trades"] == 6
    assert m["net_pnl"] == pytest.approx(175.0) and m["sleeve_return_pct"] == pytest.approx(0.7)
    assert m["contribution_to_equity_pct"] == pytest.approx(0.7 * 0.25)
    assert m["mean_daily_return_pct"] == pytest.approx(rets.mean() * 100)


def test_sharpe_is_undefined_not_zero_or_infinite_when_nothing_varies():
    m = reports.compute_metrics(series(2024, [[], [], []]), CFG)
    assert m["sharpe_net"] is None and m["trades"] == 0 and m["hit_ratio"] is None
    flat = reports.compute_metrics(series(2024, [[10.0], [10.0], [10.0]]), CFG)
    assert flat["sharpe_net"] is None                                    # zero variance


def test_max_drawdown_is_measured_on_the_sleeve_plus_cumulative_net_pnl():
    m = reports.compute_metrics(series(2024, [[100.0], [-300.0], [50.0], [-100.0]]), CFG)
    # equity 25000 -> 25100 (peak) -> 24800 -> 24850 -> 24750 : drawdown 350
    assert m["max_drawdown_usd"] == pytest.approx(350.0)
    assert m["max_drawdown_pct_of_peak"] == pytest.approx(350 / 25_100 * 100)
    assert reports.compute_metrics(series(2024, [[10.0], [20.0]]), CFG)["max_drawdown_usd"] == 0.0


def test_per_year_split_hit_ratio_r_statistics_and_buckets():
    res = (series(2024, [[100.0, -40.0], [], [60.0]]) + series(2025, [[-30.0], [90.0, 10.0]]))
    m = reports.compute_metrics(res, CFG)
    assert m["by_year"]["2024"]["net_pnl"] == pytest.approx(120.0) and m["by_year"]["2024"]["days"] == 3
    assert m["by_year"]["2025"]["net_pnl"] == pytest.approx(70.0) and m["by_year"]["2025"]["trades"] == 3
    assert m["by_year"]["2024"]["contribution_to_equity_pct"] == pytest.approx(120 / 25_000 * 0.25 * 100)
    assert m["trades"] == 6 and m["hit_ratio"] == pytest.approx(4 / 6)
    rs = [100 / 2, -40 / 2, 60 / 2, -30 / 2, 90 / 2, 10 / 2]            # r = net / (10 sh x 0.2) = net / 2
    assert m["avg_r_net"] == pytest.approx(sum(rs) / 6)
    q = m["r_net_quantiles"]
    assert q["min"] == min(rs) and q["max"] == max(rs) and q["p50"] == pytest.approx(float(np.median(rs)))


def test_r_buckets_partition_the_trades_with_half_open_edges():
    rs = [-5.0, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 2.99, 3.0, 8.0]
    res = [day_result(date(2024, 1, 2 + i), [r * 2.0]) for i, r in enumerate(rs)]     # net = 2 r (10 sh x 0.2)
    m = reports.compute_metrics(res, CFG)
    got = {d["bin"]: d["trades"] for d in m["r_net_distribution"]}
    assert got == {"< -2R": 1, "-2R .. -1R": 2, "-1R .. 0R": 2, "0R .. 1R": 2, "1R .. 2R": 1,
                   "2R .. 3R": 2, ">= 3R": 2}
    assert sum(got.values()) == len(rs)


def test_long_short_sizing_exit_and_skip_breakdowns():
    d = date(2024, 5, 6)
    r1 = DayResult(d, picks_n=6)
    r1.trades = [trade(d, 10.0, side=LONG, binding="cap"), trade(d, -4.0, side=SHORT, binding="risk", reason="stop"),
                 trade(d, 3.0, side=SHORT, binding="cap", stale=True, gap=True)]
    r1.skips = [Skip(d, "X", "doji"), Skip(d, "Y", "doji"), Skip(d, "Z", "zero_shares")]
    r2 = day_result(date(2024, 5, 7), [], loss_limit_hit=True)
    m = reports.compute_metrics([r1, r2], CFG)
    assert m["long"]["trades"] == 1 and m["short"]["trades"] == 2
    assert m["short"]["net_pnl"] == pytest.approx(-1.0) and m["short"]["hit_ratio"] == pytest.approx(0.5)
    assert m["sizing_binding"] == {"risk": 1, "cap": 2, "tie": 0, "cap_share": pytest.approx(2 / 3)}
    assert m["skips"] == {"doji": 2, "zero_shares": 1} and m["exit_reasons"] == {"stop": 1, "time": 2}
    assert m["exits_at_stale_price"] == 1 and m["gap_entries"] == 1 and m["days_loss_limit_hit"] == 1
    assert m["days_with_no_trades"] == 1 and m["trades_per_day"] == pytest.approx(1.5)


# ====================================================================================== verdict
def _metrics_for(y24, y25):
    return reports.compute_metrics(series(2024, y24) + series(2025, y25), CFG)


def test_go_requires_sharpe_at_least_1_and_positive_net_pnl_in_both_years():
    good = [[100.0], [60.0], [80.0], [40.0], [90.0]]
    v = reports.go_no_go(_metrics_for(good, good))
    assert v["verdict"] == "GO" and all(v["checks"].values()) and v["net_sharpe"] >= 1.0


def test_a_losing_or_flat_year_is_no_go_even_with_a_great_sharpe():
    good = [[100.0], [60.0], [80.0], [40.0], [90.0]]
    assert reports.go_no_go(_metrics_for(good, [[-10.0], [-20.0], [5.0], [-5.0]]))["verdict"] == "NO-GO"
    v = reports.go_no_go(_metrics_for(good, [[], [], []]))              # a year of exactly $0 is not positive
    assert v["verdict"] == "NO-GO" and v["checks"]["net_pnl_2025 > 0"] is False
    # a year with positive total P&L but a negative one elsewhere
    assert reports.go_no_go(_metrics_for([[-50.0], [-40.0]], good))["checks"]["net_pnl_2024 > 0"] is False


def test_positive_in_both_years_but_a_sharpe_below_one_is_no_go():
    noisy = [[100.0], [-90.0]] * 3
    m = _metrics_for(noisy, noisy)
    assert m["by_year"]["2024"]["net_pnl"] > 0 and m["by_year"]["2025"]["net_pnl"] > 0
    v = reports.go_no_go(m)
    assert v["verdict"] == "NO-GO" and 0 < v["net_sharpe"] < 1.0
    assert v["checks"]["net_sharpe >= 1.0"] is False


def test_the_threshold_is_inclusive_and_compared_unrounded_and_a_missing_year_fails():
    ok = {"by_year": {"2024": {"net_pnl": 1.0}, "2025": {"net_pnl": 1.0}}}
    assert reports.go_no_go({"sharpe_net": 1.0, **ok})["verdict"] == "GO"
    assert reports.go_no_go({"sharpe_net": 0.99999999, **ok})["verdict"] == "NO-GO"
    assert reports.go_no_go({"sharpe_net": None, **ok})["verdict"] == "NO-GO"
    assert reports.go_no_go({"sharpe_net": 2.0, "by_year": {"2024": {"net_pnl": 5.0}}})["verdict"] == "NO-GO"
    assert reports.go_no_go({"sharpe_net": 2.0, "by_year": {"2024": {"net_pnl": 5.0}, "2025": {"net_pnl": 0.0}}})["verdict"] == "NO-GO"


# ========================================================================= the run's validity
def test_only_the_exact_preregistered_run_can_produce_a_verdict():
    ok_prereg = {"unchanged": True}
    assert cli.validity("2024-01-02", "2025-12-31", None, CFG, ALL_VARIANTS, ok_prereg) == []
    bad = cli.validity("2024-01-02", "2025-06-30", None, CFG, ALL_VARIANTS, ok_prereg)
    assert len(bad) == 1 and "window" in bad[0]
    assert "smoke" in cli.validity("2024-01-02", "2025-12-31", 300, CFG, ALL_VARIANTS, ok_prereg)[0]
    changed = cli.validity("2024-01-02", "2025-12-31", None, CFG.replace(top_n=10), ALL_VARIANTS, ok_prereg)
    assert "top_n" in changed[0]
    assert "variant" in cli.validity("2024-01-02", "2025-12-31", None, CFG, ALL_VARIANTS[:2], ok_prereg)[0]
    assert "no longer matches" in cli.validity("2024-01-02", "2025-12-31", None, CFG, ALL_VARIANTS,
                                               {"unchanged": False})[0]
    assert "could not be verified" in cli.validity("2024-01-02", "2025-12-31", None, CFG, ALL_VARIANTS,
                                                   {"unchanged": None})[0]


# ======================================================================================= outputs
def test_outputs_are_written_deterministically_and_a_non_run_carries_no_verdict(tmp_path):
    res = series(2024, [[100.0], [-30.0, 10.0], []])
    run = BacktestRun(CFG, [r.day for r in res], {v.name: res for v in ALL_VARIANTS}, ALL_VARIANTS, {}, {})
    metrics = reports.compute_all(run)
    header = {"run_id": "x", "variants": reports.variants_header(ALL_VARIANTS), "valid_for_verdict": False,
              "invalid_reasons": ["window 2024-01-01..2024-01-03 is not the pre-registered one"]}
    files = reports.write_run(tmp_path / "r", header, run, metrics, None)
    assert {p.name for p in files} >= {"header.json", "summary.md", "trades.csv", "daily.csv", "metrics.json"}
    text = (tmp_path / "r" / "summary.md").read_text(encoding="utf-8")
    assert "NOT A PRE-REGISTERED RUN" in text and "Mechanical verdict" not in text
    head = json.loads((tmp_path / "r" / "header.json").read_text(encoding="utf-8"))
    assert head["metrics_verdict"] is None and head["valid_for_verdict"] is False
    lines = (tmp_path / "r" / "primary" / "trades.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("day,symbol,side,shares") and len(lines) == 1 + 3
    assert lines[1].split(",")[0] == "2024-01-02"
    daily = (tmp_path / "r" / "primary" / "daily.csv").read_text(encoding="utf-8").splitlines()
    assert len(daily) == 1 + 3 and daily[3].split(",")[2] == "0"            # the no-trade day is present, with 0 trades
    # a valid run shows the verdict
    verdict = reports.go_no_go(metrics["primary"])
    ok_header = dict(header, valid_for_verdict=True, invalid_reasons=[])
    assert "## Mechanical verdict (primary variant): **NO-GO**" in reports.render_summary_md(ok_header, metrics, verdict)


# ===================================================================================== ETF list
NASDAQ = ("Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
          "AAAP|Pacer ETF|G|N|N|100|Y|N\n"
          "AAPL|Apple Inc.|Q|N|N|100|N|N\n"
          "QQQ|Invesco QQQ Trust|G|N|N|100|Y|N\n"
          "File Creation Time: 1002202621:31|||||||\n")
OTHER = ("ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
         "A|Agilent|N|A|N|100|N|A\n"
         "SPY|SPDR S&P 500 ETF|P|SPY|Y|100|N|SPY\n"
         "BRK.B|Berkshire B|N|BRK.B|N|100|N|BRK.B\n"
         "XYZ.W|Some ETF warrant-like|A|XYZ.WS|Y|100|N|XYZ+\n"
         "File Creation Time: 1002202621:31||||||\n")


def test_etf_symbols_come_from_the_etf_column_under_every_symbol_column():
    n_syms, n_rows, n_etf = parse_etf_symbols(NASDAQ, ("Symbol",))
    assert n_syms == {"AAAP", "QQQ"} and (n_rows, n_etf) == (3, 2)           # the trailer row is not a symbol
    o_syms, o_rows, o_etf = parse_etf_symbols(OTHER, ("ACT Symbol", "CQS Symbol", "NASDAQ Symbol"))
    assert o_syms == {"SPY", "XYZ.W", "XYZ.WS", "XYZ+"} and (o_rows, o_etf) == (4, 2)
    assert "A" not in o_syms and "BRK.B" not in o_syms


def test_load_etf_list_reads_both_files_records_hashes_and_never_touches_the_network(tmp_path, monkeypatch):
    (tmp_path / "nasdaqlisted.txt").write_bytes(NASDAQ.encode("utf-8"))      # bytes: no newline translation
    (tmp_path / "otherlisted.txt").write_bytes(OTHER.encode("utf-8"))
    import socket

    def no_net(*_a, **_k):
        raise AssertionError("the ETF loader must not use the network")
    monkeypatch.setattr(socket, "socket", no_net)
    etf = load_etf_list(tmp_path)
    assert {"AAPL", "A", "BRK.B"}.isdisjoint(etf.symbols) and {"QQQ", "SPY", "AAAP"} <= etf.symbols
    names = {f["name"]: f for f in etf.files}
    assert names["nasdaqlisted.txt"]["bytes"] == len(NASDAQ.encode()) and len(names["nasdaqlisted.txt"]["sha256"]) == 64
    assert (names["otherlisted.txt"]["rows"], names["otherlisted.txt"]["etf_rows"]) == (4, 2)


def test_a_missing_reference_file_says_how_to_get_it(tmp_path):
    (tmp_path / "nasdaqlisted.txt").write_text(NASDAQ, encoding="utf-8")
    with pytest.raises(EtfListMissingError, match="otherlisted.txt"):
        load_etf_list(tmp_path)


# ================================================================================ session closes
def test_the_nyse_early_closes_are_the_preregistered_six_and_everything_else_closes_at_16_00():
    assert EARLY_CLOSE_DATES == {date(2024, 7, 3), date(2024, 11, 29), date(2024, 12, 24),
                                 date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24)}
    assert all(close_minute(d) == 13 * 60 for d in EARLY_CLOSE_DATES)
    assert close_minute(date(2024, 7, 5)) == 16 * 60 and close_minute(date(2025, 11, 26)) == 16 * 60


# ================================================================================== provenance
GIT = shutil.which("git")


def _git(repo, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo,
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.mark.skipif(GIT is None, reason="git not installed")
def test_prereg_info_detects_an_edit_ignores_line_endings_and_finds_the_first_commit(tmp_path):
    path = "docs/specs/ORB_Phase2_Preregistration.md"
    _git(tmp_path, "init", "-q")
    f = tmp_path / path
    f.parent.mkdir(parents=True)
    f.write_bytes(b"# prereg\nrule 1\nrule 2\n")
    _git(tmp_path, "add", path)
    _git(tmp_path, "commit", "-q", "-m", "prereg")
    first = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "other.txt").write_text("x")
    _git(tmp_path, "add", "other.txt")
    _git(tmp_path, "commit", "-q", "-m", "later")

    info = provenance.prereg_info(tmp_path, path)
    assert info["first_commit"] == first and info["unchanged"] is True and info["path"] == path
    assert info["sha256_now"] == info["sha256_at_first_commit"]
    f.write_bytes(b"# prereg\r\nrule 1\r\nrule 2\r\n")                    # a Windows checkout: same content
    assert provenance.prereg_info(tmp_path, path)["unchanged"] is True
    f.write_bytes(b"# prereg\nrule 1 (edited)\nrule 2\n")
    edited = provenance.prereg_info(tmp_path, path)
    assert edited["unchanged"] is False and edited["first_commit"] == first
    assert provenance.code_commit(tmp_path) == {"sha": _git(tmp_path, "rev-parse", "HEAD"), "dirty": True}
    _git(tmp_path, "checkout", "--", path)
    assert provenance.code_commit(tmp_path)["dirty"] is False


def test_provenance_degrades_to_none_instead_of_raising_outside_a_repo(tmp_path):
    info = provenance.prereg_info(tmp_path, "docs/nope.md")
    assert info["first_commit"] is None and info["unchanged"] is None
    # (a directory that is not a git repo)
    assert provenance.code_commit(Path(tmp_path))["sha"] in (None,) or len(provenance.code_commit(tmp_path)["sha"]) == 40


@pytest.mark.skipif(GIT is None, reason="git not installed")
def test_the_real_preregistration_has_not_been_edited_since_its_first_commit():
    info = provenance.prereg_info()
    if info["first_commit"] is None:
        pytest.skip("no git history available")
    assert info["unchanged"] is True, "the pre-registration is locked: a change needs a new pre-registration"


@pytest.mark.skipif(GIT is None, reason="git not installed")
def test_an_appended_post_run_clarification_is_not_an_edit_but_a_change_to_a_rule_still_is(tmp_path):
    path = "docs/specs/Some_Preregistration.md"
    _git(tmp_path, "init", "-q")
    f = tmp_path / path
    f.parent.mkdir(parents=True)
    f.write_bytes(b"# prereg\nrule 1\nrule 2\n")
    _git(tmp_path, "add", path)
    _git(tmp_path, "commit", "-q", "-m", "prereg")
    note = b"\n## Post-run clarification, no rule change\n\n*Added later.* A confirmation, not a rule.\n"
    f.write_bytes(b"# prereg\nrule 1\nrule 2\n" + note)
    info = provenance.prereg_info(tmp_path, path)
    assert info["unchanged"] is True and info["has_post_run_note"] is True
    assert info["sha256_now"] == info["sha256_at_first_commit"]
    f.write_bytes(b"# prereg\r\nrule 1\r\nrule 2\r\n" + note.replace(b"\n", b"\r\n"))             # CRLF working copy
    assert provenance.prereg_info(tmp_path, path)["unchanged"] is True
    f.write_bytes(b"# prereg\nrule 1\nrule 2\n" + note + b"\nA second note line.\n")               # more text in the note
    assert provenance.prereg_info(tmp_path, path)["unchanged"] is True
    f.write_bytes(b"# prereg\nrule 1 (edited)\nrule 2\n" + note)                                     # a rule edited ABOVE the note
    edited = provenance.prereg_info(tmp_path, path)
    assert edited["unchanged"] is False and edited["has_post_run_note"] is True
    f.write_bytes(b"# prereg\nrule 1\nrule 2 ## Post-run clarification inline, not a heading\nextra rule\n")
    assert provenance.prereg_info(tmp_path, path)["unchanged"] is False                              # a heading must start its own line
    f.write_bytes(b"# prereg\nrule 1\nrule 2\nextra rule\n")                                          # a rule ADDED, no note
    assert provenance.prereg_info(tmp_path, path)["unchanged"] is False
