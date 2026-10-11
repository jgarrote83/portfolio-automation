"""The whole lead-lag run on a constructed market: a synthetic Yahoo cache for all 22 members, stub US bars, the real
`analyse`, the CLI (writing outputs and the report), and the independent post-run verifier (including a tamper check).

The market is built so the answers are known: on day 1 every foreign member rises (Asia +2%, Europe/UK +1%: s = +1.5%)
and the US instrument rises open-to-close; on day 2 they fall and it falls; day 3 is a foreign holiday for all
(no signal); day 4 a split excludes one member; day 5 one European member (BP.L) is stale (open = previous close); day 6 has a signal of exactly 0 (Asia +1%, Europe/UK -1%).
"""
import csv
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from backtest import foreign as F  # noqa: E402
from backtest import leadlag as L  # noqa: E402
from backtest import leadlag_run as R  # noqa: E402
from backtest import leadlag_verify as V  # noqa: E402

DAYS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 9)]
D1, D2, D3, D4, D5, D6 = DAYS
PREV = date(2023, 12, 29)


def _rows(symbol, region):
    """(date, open, close) for one member. Base: previous close 100 on every row so returns are exactly the percent below."""
    asia = region == L.ASIA
    r = {}
    r[PREV] = (100.0, 100.0)
    # day 1: Asia close +2%, Europe open +1% (close irrelevant); day 2: Asia -2%, Europe -1%
    r[D1] = (100.0 if asia else 101.0, 102.0 if asia else 95.0)
    r[D2] = (102.0 if asia else 95.0 * 0.99, 102.0 * 0.98 if asia else 70.0)       # Europe: -1% vs D1's (wild) close of 95
    # day 3 (D3) is missing for everybody (a foreign holiday): no row
    prev_close = r[D2][1]
    r[D4] = (prev_close if not asia else prev_close, prev_close * 1.01)             # Asia +1% (D4 vs D2's close, across the holiday)
    if not asia:
        r[D4] = (prev_close * 1.03, prev_close * 1.03)                              # Europe +3% vs D2's close
    c4 = r[D4][1]
    r[D5] = (c4, c4 * 1.01) if (asia or symbol == "BP.L") else (c4 * 1.005, c4 * 1.01)   # only BP.L opens exactly at the previous close: stale
    c5 = r[D5][1]
    r[D6] = (c5, c5 * 1.01) if asia else (c5 * 0.99, c5)                            # Asia +1%, Europe -1%: s is exactly 0 on D6
    return [(d, *v) for d, v in sorted(r.items())]


def _write_cache(root, *, split_member=None):
    for g in L.GROUPS:
        for s in g.members:
            rows = _rows(s, g.region_of(s))
            idx = pd.DatetimeIndex([pd.Timestamp(d) for d, _o, _c in rows])
            h = pd.DataFrame({"Open": [o for _d, o, _c in rows], "Close": [c for _d, _o, c in rows], "Dividends": 0.0,
                              "Stock Splits": [2.0 if (s == split_member and d == D4) else 0.0 for d, _o, _c in rows]}, index=idx)
            h.index = h.index.tz_localize("Asia/Tokyo")
            F.download_member(s, root=root, fetch=lambda *_a, h=h: h)


US_BARS = {D1: (400.0, 408.0), D2: (410.0, 400.0), D3: (400.0, 401.0), D4: (401.0, 402.0), D5: (402.0, 400.0), D6: (400.0, 400.5)}


class StubLayer:
    """The two data-layer calls the runner makes, answered from constants and insisting on offline use when asked."""

    def __init__(self, *_a, **_k):
        self.calls = 0

    def trading_days(self, start, end, offline=False, **_k):
        return [d for d in DAYS if start <= d <= end]

    def get_bars(self, symbols, start, end, timeframe, feed, offline=False, **_k):
        assert timeframe == "1Day" and feed == "sip"
        start = start if isinstance(start, date) else date.fromisoformat(start)
        rows = []
        for s in symbols:
            k = {"SPY": 1.0, "SMH": 0.5, "XLF": 0.1, "XLE": 0.2}[s]
            allb = {PREV: (399.0, 400.0), **US_BARS}
            for d, (o, c) in allb.items():
                if start <= d <= end:
                    rows.append({"symbol": s, "ts": pd.Timestamp(d, tz="US/Eastern"), "open": o * k, "close": c * k})
        return pd.DataFrame(rows)

    def cache_stats(self):
        return {"requests_made": 0}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    froot = tmp_path / "foreign"
    _write_cache(froot, split_member="^FCHI")
    monkeypatch.setattr(R, "DataLayer", StubLayer)
    monkeypatch.setattr(R.provenance, "prereg_info", lambda path=None: {"path": path, "first_commit": "abc", "unchanged": True})
    monkeypatch.setattr(R.provenance, "code_commit", lambda: {"sha": "deadbeef" * 5, "dirty": False})
    return {"froot": froot, "out": tmp_path / "out", "review": tmp_path / "review" / "results.md", "tmp": tmp_path}


def _rows_frame():
    idx = pd.DatetimeIndex([pd.Timestamp("2024-01-02", tz="Asia/Tokyo")])
    return pd.DataFrame({"Open": [1.0], "Close": [1.0], "Dividends": [0.0], "Stock Splits": [0.0]}, index=idx)


def _analyse(froot):
    members, _metas = R.load_members(froot)
    days = DAYS
    us = {g.us: {d: (o * {"SPY": 1.0, "SMH": 0.5, "XLF": 0.1, "XLE": 0.2}[g.us], c * {"SPY": 1.0, "SMH": 0.5, "XLF": 0.1, "XLE": 0.2}[g.us])
                 for d, (o, c) in {PREV: (399.0, 400.0), **US_BARS}.items()} for g in L.GROUPS}
    return L.analyse(members, us, {s: None for s in L.ALL_MEMBERS}, L.LeadLagConfig(), days)


# ============================================================================================ the signal
def test_every_member_return_on_the_constructed_market_matches_the_known_answer(world):
    members, _m = R.load_members(world["froot"])
    idx = members["^N225"].index()
    assert L.member_return(members["^N225"], idx, D1, L.ASIA)[0] == pytest.approx(0.02)
    assert L.member_return(members["^GDAXI"], members["^GDAXI"].index(), D1, L.EUROPE)[0] == pytest.approx(0.01)   # the open, not the 95 close
    assert L.member_return(members["^N225"], idx, D3, L.ASIA) == (None, "no_row")                                  # the shared holiday
    assert L.member_return(members["^N225"], idx, D4, L.ASIA)[0] == pytest.approx(0.01)                            # D4 over D2's close
    assert L.member_return(members["^GDAXI"], members["^GDAXI"].index(), D6, L.EUROPE)[0] == pytest.approx(-0.01)
    assert L.member_return(members["^N225"], idx, D6, L.ASIA)[0] == pytest.approx(0.01)


def test_groups_signals_trades_and_the_split_exclusion_on_the_constructed_market(world):
    res = _analyse(world["froot"])
    broad = res["groups"]["Broad"]
    s = {sd.day: sd for sd in broad.signals}
    assert s[D1].s == pytest.approx(0.015) and s[D1].s_asia == pytest.approx(0.02) and s[D1].s_europe == pytest.approx(0.01)
    assert s[D2].s == pytest.approx(-0.015) and s[D3].s is None
    assert s[D4].n_europe == 3 and s[D4].s_europe == pytest.approx(0.03) and s[D4].s == pytest.approx(0.02)         # ^FCHI split on D4: dropped
    assert s[D6].s == pytest.approx(0.0, abs=1e-12)
    o = {x.day: x for x in broad.outcomes["primary"]}
    assert o[D1].trade.side == "long" and o[D1].trade.shares == 62 and o[D1].trade.gross_pnl == pytest.approx(62 * 8.0)
    assert o[D2].trade.side == "short" and o[D2].trade.shares == 60 and o[D2].trade.gross_pnl == pytest.approx(60 * 10.0)   # opens 410, closes 400
    assert o[D3].skip == "no_signal" and o[D6].skip == "zero_signal" and o[D4].trade.side == "long" and o[D5].trade.side == "long"
    energy = res["groups"]["Energy"]
    assert energy.signals[0].s_asia is None and energy.signals[0].s == pytest.approx(0.01)
    assert res["groups"]["Banks"].unavailable["HSBA.L"]["no_row"] == 1
    assert broad.unavailable["^FCHI"]["split"] == 1


def test_a_stale_european_open_excludes_that_member_everywhere_it_appears_and_only_that_member(world):
    # BP.L's D5 open equals the previous close exactly: 1 of 5 rows = 20% > 5%, so BP.L is excluded; its neighbours are not
    res = _analyse(world["froot"])
    assert {s for s, q in res["quality"].items() if q["excluded"]} == {"BP.L"}
    assert res["quality"]["BP.L"]["stale_open"] == {"rows": 5, "stale": 1, "share": pytest.approx(0.2)}
    assert res["quality"]["^N225"]["stale_open"] is None                                            # Asian opens are never used
    energy = res["groups"]["Energy"]
    assert energy.members_excluded == {"BP.L": "stop"} or energy.members_excluded == {"BP.L": "stale_open"}
    assert energy.members_used == ["SHEL.L", "TTE.PA"] and energy.unavailable["BP.L"]["excluded"] == 6
    assert energy.metrics["primary"]["trades"] == 5                                                  # D1, D2, D4, D5 and D6 (Europe alone: -1%)
    assert res["unchecked"] == list(L.ALL_MEMBERS) and res["groups"]["Broad"].members_unchecked == res["groups"]["Broad"].members_used


def test_the_two_source_files_when_placed_by_hand_make_a_member_checked(world):
    members, _m = R.load_members(world["froot"])
    m = members["^N225"]
    p = F.stooq_path("^N225", world["froot"])
    p.parent.mkdir(parents=True)
    p.write_text("Date,Open,High,Low,Close\n" + "\n".join(f"{d},{o},{o},{o},{c}" for d, o, c in
                 zip(m.dates, m.open, m.close)) + "\n", encoding="utf-8")
    stooq = R.load_stooq_all(world["froot"])
    assert stooq["^N225"] and stooq["^HSI"] is None
    q = L.member_quality(m, L.ASIA, stooq["^N225"], DAYS, L.LeadLagConfig())
    assert q["two_source"]["status"] == "checked" and q["two_source"]["share"] == 1.0 and q["excluded"] is None
    assert q["two_source"]["sampled"] == 5 and q["two_source"]["close_agree"] == 5     # D1, D2, D4, D5, D6 (D3 is the holiday)


# ================================================================================================ the CLI
def _args(world, **extra):
    import argparse
    base = dict(start="2024-01-02", end="2025-12-31", out=str(world["out"]), review_file=str(world["review"]), offline=True,
                foreign_dir=str(world["froot"]), cache_dir=None)
    base.update(extra)
    return argparse.Namespace(**base)


def test_the_cli_writes_outputs_and_a_report_and_labels_a_non_preregistered_window(world, capsys):
    assert R.cmd_run(_args(world, end="2024-01-09")) == 0
    out = capsys.readouterr().out
    assert "NOT a pre-registered run" in out
    run = next(world["out"].iterdir())
    header = json.loads((run / "header.json").read_text(encoding="utf-8"))
    assert header["valid_for_verdict"] is False and "is not the pre-registered" in header["invalid_reasons"][0]
    assert header["holdout"].startswith("2026 was never read") and header["excluded_members"]
    text = world["review"].read_text(encoding="utf-8")
    assert "NOT A PRE-REGISTERED RUN" in text and "UNCHECKED" in text and "## Session-timing verification" in text
    for name in ("Broad", "Semis", "Banks", "Energy"):
        for v in ("primary", "asia_only", "europe_only", "long_only", "slippage_2c"):
            assert (run / name / v / "trades.csv").is_file() and (run / name / v / "daily.csv").is_file()
        assert (run / name / "signals.csv").is_file() and (run / name / "metrics.json").is_file()
    with (run / "Broad" / "primary" / "trades.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["day"] for r in rows] == ["2024-01-02", "2024-01-03", "2024-01-05", "2024-01-08"] and rows[0]["side"] == "long"


def test_the_exact_preregistered_window_carries_verdicts_and_the_validity_rules_fire_otherwise(world, capsys, monkeypatch):
    class Wide(StubLayer):
        def trading_days(self, start, end, offline=False, **_k):
            return DAYS                                                                 # the constructed market in a full-window run
    monkeypatch.setattr(R, "DataLayer", Wide)
    assert R.cmd_run(_args(world)) == 0
    assert "NOT a pre-registered" not in capsys.readouterr().out
    run = next(world["out"].iterdir())
    header = json.loads((run / "header.json").read_text(encoding="utf-8"))
    assert header["valid_for_verdict"] is True and header["invalid_reasons"] == []
    why = R.validity("2024-01-02", "2025-12-31", L.LeadLagConfig(), L.GROUPS, L.ALL_VARIANTS, {"unchanged": True})
    assert why == []
    assert R.validity("2024-01-03", "2025-12-31", L.LeadLagConfig(), L.GROUPS, L.ALL_VARIANTS, {"unchanged": True})
    assert R.validity("2024-01-02", "2025-12-31", L.LeadLagConfig(sleeve_capital_usd=1.0), L.GROUPS, L.ALL_VARIANTS, {"unchanged": True})
    assert R.validity("2024-01-02", "2025-12-31", L.LeadLagConfig(), L.GROUPS[:3], L.ALL_VARIANTS, {"unchanged": True})
    assert R.validity("2024-01-02", "2025-12-31", L.LeadLagConfig(), L.GROUPS, L.ALL_VARIANTS[:2], {"unchanged": True})
    assert R.validity("2024-01-02", "2025-12-31", L.LeadLagConfig(), L.GROUPS, L.ALL_VARIANTS, {"unchanged": False})
    assert R.validity("2024-01-02", "2025-12-31", L.LeadLagConfig(), L.GROUPS, L.ALL_VARIANTS, {"unchanged": None})


def test_the_cli_refuses_2026_for_us_and_foreign_data_alike(world, capsys):
    assert R.cmd_run.__name__ and R.main(["run", "--start", "2024-01-02", "--end", "2026-01-02", "--foreign-dir", str(world["froot"]),
                                           "--out", str(world["out"])]) == 2
    assert "holdout" in capsys.readouterr().err
    assert not world["out"].exists()


def test_the_download_command_fetches_only_what_is_missing(world, monkeypatch):
    calls = []
    monkeypatch.setattr(F, "yahoo_history", lambda sym, a, b: calls.append(sym) or _rows_frame())
    R.cmd_download(_args(world, refresh=False, foreign_dir=str(world["froot"])))
    assert calls == []                                                                  # the fixture cached all 22 already
    R.cmd_download(_args(world, refresh=True, foreign_dir=str(world["froot"])))
    assert sorted(calls) == sorted(L.ALL_MEMBERS)


# ========================================================================================== the verifier
def test_the_verifier_confirms_a_correct_run_and_catches_tampered_ones(world, monkeypatch, capsys):
    class Offline(StubLayer):
        def get_bars(self, symbols, start, end, timeframe, feed, offline=False, **k):
            assert offline is True
            return super().get_bars(symbols, start, end, timeframe, feed, offline=offline, **k)
    monkeypatch.setattr(R, "DataLayer", Offline)
    R.cmd_run(_args(world, end="2024-01-09"))
    run = next(world["out"].iterdir())
    ok = V.check(Offline(), run, n=30, foreign_root=world["froot"] / "yahoo")
    assert ok["pass"] and ok["sampled"] == 17                                     # D1, D2, D4, D5 in each of the four groups; D6 too for Energy
    assert ok["matched"] == ok["sampled"] and not ok["holdout_rows_in_cache"]
    assert "Overall: **PASS**" in V.render(ok, run)
    p = run / "Broad" / "primary" / "trades.csv"
    df = pd.read_csv(p)
    df.loc[0, "signal"] += 0.01                       # a wrong signal
    df.loc[1, "net_pnl"] += 5.0                       # a wrong P&L
    df.loc[2, "side"] = "short" if df.loc[2, "side"] == "long" else "long"
    df.to_csv(p, index=False)
    bad = V.check(Offline(), run, n=30, foreign_root=world["froot"] / "yahoo")
    assert not bad["pass"] and len(bad["differences"]) >= 3
    flat = " ".join(str(d["fields"]) for d in bad["differences"])
    assert "signal" in flat and "net P&L" in flat and "side" in flat
    assert "Overall: **FAIL**" in V.render(bad, run)


def test_the_verifier_catches_a_2026_row_in_the_cache(world, monkeypatch):
    class Offline(StubLayer):
        def get_bars(self, symbols, start, end, timeframe, feed, offline=False, **k):
            return super().get_bars(symbols, start, end, timeframe, feed, offline=offline, **k)
    monkeypatch.setattr(R, "DataLayer", Offline)
    R.cmd_run(_args(world, end="2024-01-09"))
    run = next(world["out"].iterdir())
    pq = world["froot"] / "yahoo" / "^N225.parquet"
    df = pd.read_parquet(pq)
    df.loc[len(df)] = [pd.Timestamp("2026-01-05"), 1.0, 1.0, 0.0, 0.0]
    df.to_parquet(pq, index=False)
    res = V.check(Offline(), run, n=10, foreign_root=world["froot"] / "yahoo")
    assert res["holdout_rows_in_cache"] == ["^N225"] and not res["pass"]


def test_the_verifier_shares_no_code_with_the_study_modules():
    import ast
    import inspect
    imported = []
    for node in ast.walk(ast.parse(inspect.getsource(V))):
        if isinstance(node, ast.ImportFrom):
            imported.append(("." * node.level) + (node.module or ""))
            imported += [("." * node.level) + (node.module or "") + "." + a.name for a in node.names]
        elif isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
    assert not [m for m in imported if "leadlag" in m.replace("leadlag_verify", "") or "foreign" in m], imported


def test_day_arithmetic_helpers_used_by_the_fixture_are_sane():
    assert (D2 - D1) == timedelta(days=1) and PREV < D1
