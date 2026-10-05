"""End to end on a synthetic market: assets -> daily bars -> universe -> RVOL -> top 20 -> direction
-> one-minute bars -> every variant -> metrics, plus a cross-check against the Phase 1 report's own
(pandas) implementation of the same universe / Relative Volume / top-20 definitions.

The REAL data layer and HTTP client run against a fake session (no network, no keys). Nothing here is
evidence about real markets: the answers are constructed.
"""
import os
import sys
import zlib
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`
sys.path.insert(0, os.path.dirname(__file__))                       # tests/, for backtest_fakes

import pytest  # noqa: E402
from backtest import cli, reports  # noqa: E402
from backtest import iex_vs_sip as P1  # noqa: E402
from backtest.data import DataLayer  # noqa: E402
from backtest.data.client import AlpacaDataClient  # noqa: E402
from backtest.data.ratelimit import RateLimiter  # noqa: E402
from backtest.engine import ALL_VARIANTS, PRIMARY, run_backtest  # noqa: E402
from backtest.etf import EtfList  # noqa: E402
from backtest.selection import Selection, build_selection  # noqa: E402
from backtest_fakes import ASSETS_URL, BARS_URL, ET, FakeClock, FakeMarket, FakeSession, utc_iso  # noqa: E402
from orb.config import OrbConfig  # noqa: E402
from orb.signals import LONG, SHORT  # noqa: E402

CFG = OrbConfig()
FLAT_DAY, EVENT_DAY = date(2024, 1, 22), date(2024, 1, 23)
SYMS = [f"S{i:02d}" for i in range(26)] + ["ETFA"]
ETFS = frozenset({"ETFA"})


def _idx(sym):
    return 99 if sym == "ETFA" else int(sym[1:])


def _plan(sym):
    """(kind, category) for the event day. A/B = long +3R / -1R, C/D = short +2R / never triggers."""
    if sym == "ETFA":
        return "long", "A"
    if sym not in SYMS:
        return "flat", None
    i = _idx(sym)
    if i % 5 == 0:
        return "doji", None
    if i % 2 == 0:
        return "long", ("A" if i % 4 == 0 else "B")
    return "short", ("C" if i % 4 == 1 else "D")


def _factor(sym, d):
    if d != EVENT_DAY or sym not in SYMS:
        return 1.0
    return 5.0 if sym == "ETFA" else 1.0 + _idx(sym) / 10.0


_OPEN_BAR = {"long": (50.00, 50.60, 49.90, 50.40), "short": (50.00, 50.10, 49.40, 49.60),
             "doji": (50.00, 50.20, 49.80, 50.00), "flat": (50.00, 50.20, 49.80, 50.00)}


def _minute(sym, d, m):
    kind, cat = _plan(sym) if d == EVENT_DAY else ("flat", None)
    vol = round(100 * (_factor(sym, d) if m < 5 else 1.0))      # round(): int() would turn 229.99999 into 229
    if m < 5:                                              # the opening range, five consistent 1-min bars
        o, h, lo, c = _OPEN_BAR[kind]
        mid = (o + c) / 2
        if m == 0:
            return (o, h, lo, mid, vol)
        if m < 4:
            return (mid, mid, mid, mid, vol)
        return (mid, max(mid, c), min(mid, c), c, vol)
    if cat == "A":
        return {5: (50.50, 50.60, 50.45, 50.55), 389: (51.40, 51.60, 51.40, 51.50)}.get(m, (50.55, 50.70, 50.50, 50.60)) + (vol,)
    if cat == "B":
        return {5: (50.50, 50.60, 50.45, 50.55), 6: (50.55, 50.55, 50.20, 50.25)}.get(m, (50.25, 50.30, 50.20, 50.25)) + (vol,)
    if cat == "C":
        return {5: (49.50, 49.55, 49.40, 49.45), 389: (48.90, 48.95, 48.80, 48.80)}.get(m, (49.40, 49.45, 49.30, 49.35)) + (vol,)
    if cat == "D":
        return (49.80, 49.90, 49.70, 49.80, vol)
    return (50.00, 50.05, 49.95, 50.00, vol)


class OrbFakeMarket(FakeMarket):
    """One-minute bars come from `_minute`; five-minute bars are their exact aggregation."""

    def _intraday(self, sym, tf, feed, d):
        out = []
        step = 1 if tf == "1Min" else 5
        for m in range(0, 390, step):
            bars = [_minute(sym, d, k) for k in range(m, m + step)]
            o, c = bars[0][0], bars[-1][3]
            h, lo, v = max(b[1] for b in bars), min(b[2] for b in bars), sum(b[4] for b in bars)
            t = datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET) + timedelta(minutes=m)
            out.append({"t": utc_iso(t), "o": o, "h": h, "l": lo, "c": c, "v": v})
        return out


def _layer(tmp_path, market):
    clk = FakeClock()
    sess = FakeSession(market)
    client = AlpacaDataClient("K", "S", session=sess, sleep=clk.sleep,
                              limiter=RateLimiter(180, 10, clock=clk.now, sleep=clk.sleep))
    return DataLayer(tmp_path / "cache", client=client,
                     now=lambda: datetime(2025, 6, 1, tzinfo=timezone.utc)), sess, client


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("orb_e2e")
    market = OrbFakeMarket(daily_fn=lambda sym, d: (50.0, 52.0, 49.0, 51.0, 2_000_000))
    market.assets["active"] = [{"symbol": s, "exchange": "NASDAQ", "name": s} for s in SYMS]
    layer, sess, client = _layer(tmp, market)
    sel = build_selection(layer, SYMS, ETFS, CFG, "2024-01-22", "2024-01-23", fetch_from="2023-12-01",
                          log=lambda m: None)
    run = run_backtest(layer, sel, CFG, ALL_VARIANTS)
    return {"layer": layer, "sess": sess, "client": client, "sel": sel, "run": run, "tmp": tmp,
            "metrics": reports.compute_all(run)}


def _exp_net(gross, shares, cents):
    return gross - 2 * shares * 0.0035 - 2 * shares * cents / 100.0


A = lambda c: _exp_net(24 * 0.90, 24, c)           # noqa: E731  long, 24 sh, +3R (3 x 0.30)
B = lambda c: _exp_net(-24 * 0.30, 24, c)          # noqa: E731  long, 24 sh, stopped out at -1R
C = lambda c: _exp_net(25 * 0.60, 25, c)           # noqa: E731  short, 25 sh, +2R


def _trades(world, variant):
    return [t for r in world["run"].results[variant] for t in r.trades]


# ====================================================================================== picks
def test_the_event_day_picks_are_the_constructed_top_20_in_rvol_order(world):
    sel = world["sel"]
    assert sel.window_days == [FLAT_DAY, EVENT_DAY]
    noetf = sel.picks_by_day[EVENT_DAY][False]
    assert [p.symbol for p in noetf] == [f"S{i:02d}" for i in range(25, 5, -1)]
    assert [p.rvol for p in noetf] == pytest.approx([1.0 + i / 10 for i in range(25, 5, -1)])
    with_etf = sel.picks_by_day[EVENT_DAY][True]
    assert [p.symbol for p in with_etf] == ["ETFA"] + [f"S{i:02d}" for i in range(25, 6, -1)]
    for p in noetf:
        kind, _ = _plan(p.symbol)
        assert p.side == {"long": LONG, "short": SHORT, "doji": None}[kind], p.symbol
        assert p.atr == pytest.approx(3.0)                       # simple-mean ATR of the constant bars
    assert noetf[0].bar_high == 50.10 or noetf[0].bar_high == 50.20      # S25 is a doji: bar from _OPEN_BAR


def test_a_flat_day_has_picks_but_every_one_is_a_doji(world):
    flat = world["sel"].picks_by_day[FLAT_DAY][False]
    assert len(flat) == 20 and all(p.side is None for p in flat)         # RVOL exactly 1.0 for all: ties by symbol
    assert [p.symbol for p in flat] == [f"S{i:02d}" for i in range(20)]


def test_selection_statistics_are_reported(world):
    st = world["sel"].stats
    assert st["window_days"] == 2 and st["picks_total_excl_etfs"] == 40
    assert st["etf_picks_in_top_n_incl_etfs"] == 2 and st["mean_universe_size_incl_etfs"] == 27


# ===================================================================================== results
def test_primary_trades_match_the_constructed_answer_to_the_cent(world):
    trades = _trades(world, "primary")
    by = {t.symbol: t for t in trades}
    assert len(trades) == 12 and {t.day for t in trades} == {EVENT_DAY}
    cats = {s: _plan(s)[1] for s in by}
    assert sorted(cats.values()) == ["A"] * 4 + ["B"] * 4 + ["C"] * 4
    for s, t in by.items():
        cat = cats[s]
        assert t.exit_reason == ("stop" if cat == "B" else "time"), s
        want = {"A": A(2.0), "B": B(2.0), "C": C(2.0)}[cat]
        assert t.net_pnl == pytest.approx(want), (s, cat)
        assert t.r_gross == pytest.approx({"A": 3.0, "B": -1.0, "C": 2.0}[cat])
        assert t.binding == "cap"
    day = world["run"].results["primary"][1]
    assert day.net_pnl == pytest.approx(4 * A(2.0) + 4 * B(2.0) + 4 * C(2.0)) == pytest.approx(103.876)
    skips = sorted((s.reason, s.symbol) for s in day.skips)
    assert [r for r, _ in skips].count("doji") == 4 and [r for r, _ in skips].count("never_triggered") == 4
    flat_day = world["run"].results["primary"][0]
    assert flat_day.trades == [] and flat_day.net_pnl == 0.0


def test_every_variant_changes_exactly_what_it_says(world):
    net = lambda v: sum(t.net_pnl for t in _trades(world, v))            # noqa: E731
    assert net("slippage_0c") == pytest.approx(4 * A(0) + 4 * B(0) + 4 * C(0))
    assert net("slippage_1c") == pytest.approx(4 * A(1) + 4 * B(1) + 4 * C(1))
    assert net("slippage_5c") == pytest.approx(4 * A(5) + 4 * B(5) + 4 * C(5))
    assert net("slippage_0c") > net("slippage_1c") > net("primary") > net("slippage_5c")
    lo = _trades(world, "long_only")
    assert {t.side for t in lo} == {LONG} and len(lo) == 8
    assert net("long_only") == pytest.approx(4 * A(2) + 4 * B(2))
    etf = _trades(world, "etfs_included")
    assert "ETFA" in {t.symbol for t in etf} and "S06" not in {t.symbol for t in etf}     # ETFA displaced S06
    assert net("etfs_included") == pytest.approx(5 * A(2) + 3 * B(2) + 4 * C(2)) == pytest.approx(132.676)
    # the primary variant never saw the ETF
    assert "ETFA" not in {t.symbol for t in _trades(world, "primary")}


def test_metrics_and_the_mechanical_verdict_on_the_constructed_run(world):
    m = world["metrics"]["primary"]
    assert m["days"] == 2 and m["trades"] == 12 and m["days_with_no_trades"] == 1
    assert m["net_pnl"] == pytest.approx(103.876)
    assert m["by_year"]["2024"]["net_pnl"] == pytest.approx(103.876)
    assert m["hit_ratio"] == pytest.approx(8 / 12)
    assert m["sizing_binding"]["cap"] == 12 and m["sizing_binding"]["risk"] == 0
    assert m["long"]["trades"] == 8 and m["short"]["trades"] == 4
    assert m["contribution_to_equity_pct"] == pytest.approx(103.876 / 25_000 * 0.25 * 100)
    v = reports.go_no_go(m)
    assert v["verdict"] == "NO-GO"                                         # only 2024 has any P&L
    assert v["checks"]["net_pnl_2025 > 0"] is False


def test_only_read_only_data_calls_were_made_and_nothing_from_2026(world):
    calls = world["sess"].calls
    assert {c["url"] for c in calls} <= {BARS_URL, ASSETS_URL} and {c["method"] for c in calls} == {"GET"}
    assert all(not c["params"].get("start", "").startswith("2026") for c in world["sess"].bar_calls)
    one_min = [c for c in world["sess"].bar_calls if c["params"]["timeframe"] == "1Min"]
    asked = set(",".join(c["params"]["symbols"] for c in one_min).split(","))
    assert not ({"S25", "S20", "S15", "S10"} & asked)                      # dojis are never fetched minute by minute
    assert {"S24", "S23", "ETFA"} <= asked


def test_a_rerun_is_free_and_byte_identical(world, tmp_path):
    layer, client = world["layer"], world["client"]
    n = client.requests_made
    sel2 = build_selection(layer, SYMS, ETFS, CFG, "2024-01-22", "2024-01-23", fetch_from="2023-12-01",
                           log=lambda m: None)
    run2 = run_backtest(layer, sel2, CFG, ALL_VARIANTS)
    assert client.requests_made == n                                       # everything came from the cache
    m1, m2 = world["metrics"], reports.compute_all(run2)
    hdr = {"run_id": "t", "variants": reports.variants_header(ALL_VARIANTS), "valid_for_verdict": False,
           "invalid_reasons": ["test"]}
    reports.write_run(tmp_path / "a", hdr, world["run"], m1, None)
    reports.write_run(tmp_path / "b", hdr, run2, m2, None)
    for rel in ("primary/trades.csv", "primary/daily.csv", "etfs_included/trades.csv", "summary.md",
                "primary/metrics.json"):
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes(), rel


# =========================================================== the 2026 holdout stays closed
def test_a_selection_or_run_that_reaches_2026_is_refused_before_any_data_call(tmp_path):
    from backtest.data.bars import HoldoutError

    class Boom:
        def __getattr__(self, name):
            raise AssertionError(f"the data layer was touched ({name}) for a 2026 request")

    with pytest.raises(HoldoutError):
        build_selection(Boom(), SYMS, ETFS, CFG, "2025-12-01", "2026-01-02")
    with pytest.raises(HoldoutError):
        build_selection(Boom(), SYMS, ETFS, CFG, "2026-02-01", "2026-03-02")
    with pytest.raises(HoldoutError):
        run_backtest(Boom(), Selection([date(2025, 12, 31), date(2026, 1, 2)], {}, {}), CFG)


# ========================================================= agreement with the Phase 1 report
def _rand(sym, d, salt):
    import random
    return random.Random(zlib.crc32(f"{sym}|{d}|{salt}".encode()))


def _random_daily(sym, d):
    r = _rand(sym, d, "daily")
    base = 3.0 + (zlib.crc32(sym.encode()) % 770) / 10.0                # $3 .. $80: some names fall under $5
    o = base * (1 + r.uniform(-0.03, 0.03))
    rng = base * r.uniform(0.002, 0.06)
    scale = 0.3 + (zlib.crc32(sym[::-1].encode()) % 120) / 100.0            # 0.3x .. 1.5x: the volume filter bites
    return o, o + rng, o - rng, o + r.uniform(-rng, rng), int(r.uniform(2e5, 3e6) * scale)


def _random_intraday(feed, sym, d, m):
    r = _rand(sym, d, f"intra{m}")
    o = 50.0 + r.uniform(-1, 1)
    c = o + r.choice([-0.3, 0.0, 0.3, 0.0001]) if r.random() < 0.9 else o     # some dojis
    return o, max(o, c) + 0.1, min(o, c) - 0.1, c, int(r.uniform(0, 5_000) * (3 if r.random() < 0.05 else 1))


def test_selection_agrees_with_the_phase_1_report_on_random_data_with_gaps(tmp_path):
    symbols = [f"R{i:02d}" for i in range(40)]
    halted = []
    for s in symbols:                                                    # ~3% missing symbol-days
        for k in range(60):
            d = date(2024, 1, 2) + timedelta(days=k)
            if _rand(s, d, "halt").random() < 0.03:
                halted.append((s, d))
    market = FakeMarket(daily_fn=_random_daily, intraday_fn=_random_intraday, halted=halted)
    market.assets["active"] = [{"symbol": s, "exchange": "NYSE", "name": s} for s in symbols]
    layer, _sess, _client = _layer(tmp_path, market)
    start, end, fetch_from = "2024-02-05", "2024-02-14", "2024-01-02"

    df, _ = P1.run(start, end, layer, csv_path=None, summary_path=None, exclude=set(),
                   fetch_from=fetch_from)
    sel = build_selection(layer, symbols, frozenset(), CFG, start, end, fetch_from=fetch_from,
                          log=lambda m: None)
    assert len(df) == 8
    some_picks = 0
    for _, row in df.iterrows():
        d = date.fromisoformat(row["date"])
        mine = [p.symbol for p in sel.picks_by_day[d][True]]
        assert mine == row["sip_top20"].split(), d
        some_picks += len(mine)
        assert sel.picks_by_day[d][False] == sel.picks_by_day[d][True]   # no ETFs in this universe
    assert some_picks > 20                                                # the comparison was not vacuous
    assert sel.stats["mean_universe_size_incl_etfs"] == pytest.approx(df["universe_n"].mean())


# ============================================================================ CLI plumbing
def test_the_cli_runs_end_to_end_labels_a_smoke_run_and_refuses_2026(world, tmp_path, monkeypatch, capsys):
    layer = world["layer"]
    monkeypatch.setattr(cli, "DataLayer", lambda *_a, **_k: layer)
    monkeypatch.setattr(cli, "load_etf_list", lambda *_a, **_k: EtfList(
        ETFS, ({"name": "fake.txt", "bytes": 1, "sha256": "0" * 64, "rows": 1, "etf_rows": 1},)))
    monkeypatch.setattr(layer, "get_assets", lambda **_k: __import__("pandas").DataFrame(
        [{"symbol": s, "exchange": "NASDAQ", "status": "active"} for s in SYMS + ["SPY", "TLT"]]))
    out, review = tmp_path / "out", tmp_path / "review" / "results.md"
    rc = cli.main(["run", "--start", "2024-01-22", "--end", "2024-01-23", "--out", str(out),
                   "--review-file", str(review)])
    assert rc == 0
    run_dir = next(out.iterdir())
    import json
    header = json.loads((run_dir / "header.json").read_text(encoding="utf-8"))
    assert header["valid_for_verdict"] is False and header["metrics_verdict"] is None
    assert any("window" in r for r in header["invalid_reasons"])
    assert header["code_commit"]["sha"] is None or len(header["code_commit"]["sha"]) == 40
    assert header["config"]["slippage_cents_per_side"] == 2.0 and header["config_diff_from_default"] == {}
    assert header["holdout"].startswith("2026 was never read")
    assert (run_dir / "primary" / "trades.csv").is_file() and (run_dir / "etfs_included" / "daily.csv").is_file()
    text = review.read_text(encoding="utf-8")
    assert "NOT A PRE-REGISTERED RUN" in text and "Mechanical verdict" not in text
    assert header["symbols_considered"] == len(SYMS)                       # SPY and TLT (Core roster) were removed

    class NoLayer:
        def __init__(self, *_a, **_k):
            raise AssertionError("the data layer must not even be constructed for a 2026 request")
    monkeypatch.setattr(cli, "DataLayer", NoLayer)
    assert cli.main(["run", "--start", "2025-12-01", "--end", "2026-01-05", "--out", str(out)]) == 2
    assert "holdout" in capsys.readouterr().err.lower()


def test_primary_is_the_first_variant_and_default_config_is_the_preregistered_one():
    assert ALL_VARIANTS[0] is PRIMARY and PRIMARY.config(CFG) == CFG == OrbConfig()
