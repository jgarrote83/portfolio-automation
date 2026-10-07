"""News event study end to end on a synthetic market: the REAL data layer and HTTP client against a fake session
(no network, no keys) -- assets, daily bars, news, 10:00 bars, every variant, the verifier and the CLI. The answers
are constructed; nothing here is evidence about real markets."""
import json
import math
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`
sys.path.insert(0, os.path.dirname(__file__))                       # tests/, for backtest_fakes

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from backtest import news_gap as NG  # noqa: E402
from backtest import news_gap_verify as V  # noqa: E402
from backtest.data import DataLayer  # noqa: E402
from backtest.data.client import AlpacaDataClient  # noqa: E402
from backtest.data.ratelimit import RateLimiter  # noqa: E402
from backtest.etf import EtfList  # noqa: E402
from backtest_fakes import BARS_URL, ET, NEWS_URL, FakeClock, FakeMarket, FakeNews, NewsSession, article, utc_iso  # noqa: E402

MON, TUE = date(2024, 1, 22), date(2024, 1, 23)
ETFS = frozenset({"ETFX"})
SYMS = [f"S{i:02d}" for i in range(1, 13)] + ["S20", "S21", "ETFX"]

# day -> symbol -> (open, close); every other (symbol, day) opens at the previous close and closes at 50.0
GAPS = {
    MON: {"S01": (52.5, 53.0), "S02": (48.0, 47.0), "S03": (51.5, 52.0), "S04": (53.0, 53.5), "S05": (51.4, 51.9),
          "S06": (51.0, 51.2), "S07": (54.0, 54.5), "S08": (49.2, 49.5), "S09": (25.0, 25.5), "S10": (8.0, 8.2),
          "S11": (54.5, 54.9), "S12": (51.25, 51.5), "ETFX": (55.0, 55.5)},
    TUE: {"S20": (47.5, 46.0), "S21": (52.0, 52.5)},
}
O10 = {(MON, "S01"): 52.7, (MON, "S02"): 47.8, (MON, "S03"): 51.7, (MON, "S04"): 53.2, (MON, "S05"): 51.5, (MON, "S06"): 51.1,
       (MON, "S09"): 25.3, (MON, "S12"): 51.4, (TUE, "S20"): 47.3, (TUE, "S21"): 52.1}
NO10 = {(MON, "S07")}                                                  # the highest-ranked real event has no 10:00 bar


def prev_weekday(d):
    p = d - timedelta(days=1)
    while p.weekday() >= 5:
        p -= timedelta(days=1)
    return p


def close_of(sym, d):
    return GAPS.get(d, {}).get(sym, (None, 50.0))[1] if d in GAPS else 50.0


def open_of(sym, d):
    if sym in GAPS.get(d, {}):
        return GAPS[d][sym][0]
    return close_of(sym, prev_weekday(d))


def window(d):
    """[16:00 ET on the previous weekday, 09:30 ET on d) in UTC (EST: UTC-5)."""
    p = prev_weekday(d)
    return (datetime(p.year, p.month, p.day, 21, 0, tzinfo=timezone.utc), datetime(d.year, d.month, d.day, 14, 30, tzinfo=timezone.utc))


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _articles():
    ms, me = window(MON)
    ts, te = window(TUE)
    mid = ms + timedelta(hours=15)                                       # Saturday 10:00 ET
    return [
        article(1, _iso(mid), ["S01"]), article(2, _iso(mid + timedelta(hours=1)), ["S02", "S99"]),
        article(3, _iso(mid), ["S03", "S98", "S97"]),                      # three symbols: never matches
        article(4, _iso(me), ["S05"]),                                       # exactly 09:30:00: outside the window
        article(5, _iso(ms), ["S06"]),                                       # exactly 16:00:00 on D-1: inside
        article(6, _iso(mid), ["S07"], headline="Acme to acquire Beta in a takeover"),
        article(7, _iso(mid), ["S08"]), article(8, _iso(mid), ["S09"], headline="Acme announces 2-for-1 stock split"),
        article(9, _iso(mid), ["S10"]), article(10, _iso(mid), ["S11"]), article(11, _iso(mid), ["S12"], headline="Analyst upgrade lifts Acme"),
        article(12, _iso(mid), ["ETFX"]),
        article(13, _iso(ms - timedelta(seconds=1)), ["S04"]),               # one second before the window: outside (S04 stays a control)
        article(14, _iso(ts + timedelta(hours=1)), ["S20"], headline="Acme Q4 EPS miss, revenue falls", updated_at=_iso(ts + timedelta(hours=2))),
        article(15, _iso(te + timedelta(minutes=1)), ["S21"]),               # after 09:30 on D: outside
    ]


class GapMarket(FakeMarket):
    def _daily(self, sym, d):
        o, c = open_of(sym, d), close_of(sym, d)
        vol = 10_000 if sym == "S11" else 2_000_000
        return {"t": utc_iso(datetime(d.year, d.month, d.day, tzinfo=ET)), "o": o, "h": max(o, c) + 1, "l": min(o, c) - 1, "c": c, "v": vol}

    def _intraday(self, sym, tf, feed, d):
        out = []
        for m in range(0, 390):
            if (d, sym) in NO10 and m == 30:
                continue
            o = O10.get((d, sym), open_of(sym, d))
            t = datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET) + timedelta(minutes=m)
            out.append({"t": utc_iso(t), "o": o, "h": o + 0.1, "l": o - 0.1, "c": o, "v": 1000})
        return out


def _layer(tmp_path):
    clk = FakeClock()
    market = GapMarket()
    market.assets["active"] = [{"symbol": s, "exchange": "NYSE", "name": s} for s in SYMS]
    sess = NewsSession(market, FakeNews(_articles(), page_size=4))
    client = AlpacaDataClient("K", "S", session=sess, sleep=clk.sleep,
                              limiter=RateLimiter(180, 10, clock=clk.now, sleep=clk.sleep))
    return DataLayer(tmp_path / "cache", client=client, now=lambda: datetime(2025, 6, 1, tzinfo=timezone.utc)), sess, client


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("newsgap_e2e")
    layer, sess, client = _layer(tmp)
    data = NG.prepare(layer, "2024-01-22", "2024-01-23", NG.NewsGapConfig(), etfs=ETFS)
    res = NG.analyse(data)
    return {"layer": layer, "sess": sess, "client": client, "data": data, "res": res, "tmp": tmp}


def _trade(sym, d, side, o10, close, cents=2.0):
    sh = math.floor(5000 / o10)
    gross = sh * ((close - o10) if side == "long" else (o10 - close))
    return {"shares": sh, "net": gross - sh * cents / 100 - 2 * sh * 0.0035}


# ================================================================================ candidates
def test_events_and_controls_are_what_the_rules_say(world):
    plans = {p.day: p for p in world["data"].plans}
    mon, tue = plans[MON], plans[TUE]
    # the split (-50% on raw prices) outranks every real gap; S06 is 2.0% exactly and S12 2.5%: S06 is cut by the top-5 rule
    assert [c.symbol for c in mon.events] == ["S09", "S07", "S01", "S02", "S12", "S06"]
    assert [c.symbol for c in mon.picks(NG.NewsGapConfig())] == ["S09", "S07", "S01", "S02", "S12"]
    assert [c.symbol for c in mon.controls] == ["S04", "S03", "S05"]        # S04: news one second early; S03: 3 symbols; S05: at 09:30
    assert [c.symbol for c in tue.events] == ["S20"] and [c.symbol for c in tue.controls] == ["S21"]
    out = {c.symbol for c in mon.events} | {c.symbol for c in mon.controls}
    assert not ({"ETFX", "S10", "S11", "S08"} & out)                         # ETF; open < $10; ADV < $50M; gap < 2%
    s9 = mon.events[0]
    assert s9.g == pytest.approx(-0.5) and s9.article_ids == (8,) and s9.types == ("other",)
    assert tue.events[0].types == ("earnings",) and mon.events[1].types == ("deal",) and mon.events[4].types == ("analyst",)
    assert mon.n_gap_candidates == 9 and tue.n_gap_candidates == 2


def test_the_top_5_cut_comes_before_a_skip_and_a_skipped_event_is_not_replaced(world):
    res = world["res"]["results"]["primary"]
    mon = res[0]
    assert [t.symbol for t in mon.trades] == ["S09", "S01", "S02", "S12"] and mon.skips == (("S07", "no_10_00_bar"),)
    assert "S06" not in [t.symbol for t in mon.trades] and mon.n_events == 6


def test_primary_trades_match_the_constructed_answer_to_the_cent(world):
    mon, tue = world["res"]["results"]["primary"]
    exp = {("S09", "short"): _trade("S09", MON, "short", 25.3, 25.5), ("S01", "long"): _trade("S01", MON, "long", 52.7, 53.0),
           ("S02", "short"): _trade("S02", MON, "short", 47.8, 47.0), ("S12", "long"): _trade("S12", MON, "long", 51.4, 51.5)}
    for t in mon.trades:
        e = exp[(t.symbol, t.side)]
        assert (t.shares, t.net_pnl) == (e["shares"], pytest.approx(e["net"])) and t.kind == "news"
    s20 = tue.trades[0]
    assert (s20.symbol, s20.side) == ("S20", "short") and s20.net_pnl == pytest.approx(_trade("S20", TUE, "short", 47.3, 46.0)["net"])
    assert s20.types == ("earnings",)
    m = world["res"]["metrics"]["primary"]
    assert m["trades"] == 5 and m["days"] == 2 and m["days_with_no_event"] == 0 and m["skips"] == {"no_10_00_bar": 1}
    assert m["net_pnl"] == pytest.approx(sum(e["net"] for e in exp.values()) + _trade("S20", TUE, "short", 47.3, 46.0)["net"])
    assert NG.go_no_go(m)["verdict"] == "NO-GO"


def test_the_control_and_every_diagnostic_change_exactly_what_they_say(world):
    r = world["res"]["results"]
    ctrl = r["control_no_news"]
    assert [t.symbol for t in ctrl[0].trades] == ["S04", "S03", "S05"] and [t.symbol for t in ctrl[1].trades] == ["S21"]
    assert all(t.kind == "control" for o in ctrl for t in o.trades)
    exp04 = _trade("S04", MON, "long", 53.2, 53.5)
    assert ctrl[0].trades[0].net_pnl == pytest.approx(exp04["net"])
    # confirmation filter: S09 (short, but the 10:00 open is ABOVE the day open) is dropped and not replaced
    conf = r["confirmation_filter"]
    assert [t.symbol for t in conf[0].trades] == ["S01", "S02", "S12"] and sorted(conf[0].skips) == [("S07", "no_10_00_bar"), ("S09", "not_confirmed")]
    assert [t.symbol for t in conf[1].trades] == ["S20"]
    # long-only: the three shorts (S09, S02, S20) become no-trade days/slots
    lo = r["long_only"]
    assert [t.symbol for t in lo[0].trades] == ["S01", "S12"] and ("S09", "short_excluded") in lo[0].skips and lo[1].trades == ()
    # slippage 0 / 1 / 5 cents change only the costs
    for name, cents in (("slippage_0c", 0.0), ("slippage_1c", 1.0), ("slippage_5c", 5.0)):
        want = sum(_trade("", d, s, o, c, cents)["net"] for d, s, o, c in
                   [(MON, "short", 25.3, 25.5), (MON, "long", 52.7, 53.0), (MON, "short", 47.8, 47.0), (MON, "long", 51.4, 51.5), (TUE, "short", 47.3, 46.0)])
        assert world["res"]["metrics"][name]["net_pnl"] == pytest.approx(want)


def test_news_vs_no_news_comparison_and_event_types(world):
    cmp = world["res"]["comparison"]
    news_bps = [t.net_ret_bps for o in world["res"]["results"]["primary"] for t in o.trades]
    ctrl_bps = [t.net_ret_bps for o in world["res"]["results"]["control_no_news"] for t in o.trades]
    assert cmp["n_news"] == 5 and cmp["n_control"] == 4
    assert cmp["difference"] == pytest.approx(sum(news_bps) / 5 - sum(ctrl_bps) / 4)
    bd = world["res"]["types"]
    assert bd["earnings"]["trades"] == 1 and bd["analyst"]["trades"] == 1 and bd["other"]["trades"] == 3 and bd["deal"]["trades"] == 0   # S07 was skipped


def test_data_statistics_and_the_largest_gap_disclosure(world):
    st, ev = world["res"]["stats"], world["res"]["events"]
    assert st["articles"] >= 15 and st["by_source"]["benzinga"] == st["articles"]
    assert st["matched_articles"] == 7 and 0 <= st["share_updated_differs_all"] <= 1
    assert st["share_updated_differs_matched"] == pytest.approx(1 / 7)                        # only article 14 was edited
    assert ev["events_per_day_mean"] == 3.5 and ev["events_per_day_median"] == 3.5 and ev["days_with_no_event"] == 0
    big = world["res"]["largest_gaps"]
    assert big[0]["symbol"] == "S09" and big[0]["g_pct"] == pytest.approx(-50.0) and big[0]["traded"] and big[1]["symbol"] == "S07"
    assert big[1]["skip_reason"] == "no_10_00_bar" and big[1]["net_pnl"] == 0.0


# ====================================================================== the data actually used
def test_only_read_only_calls_one_minute_bars_only_for_picks_and_nothing_from_2026(world):
    calls = world["sess"].calls
    assert {c["url"] for c in calls} <= {BARS_URL, NEWS_URL, "https://paper-api.alpaca.markets/v2/assets"} and {c["method"] for c in calls} == {"GET"}
    assert all(not c["params"].get("start", "").startswith("2026") for c in calls)
    one_min = [c for c in world["sess"].bar_calls if c["params"]["timeframe"] == "1Min"]
    asked = {s for c in one_min for s in c["params"]["symbols"].split(",")}
    picks = {"S09", "S07", "S01", "S02", "S12", "S04", "S03", "S05", "S20", "S21"}
    assert asked == picks and not ({"S06", "ETFX", "S08", "S10", "S11"} & asked)
    assert all(c["params"]["start"].endswith("T15:00:00Z") and c["params"]["end"].endswith("T15:00:59Z") for c in one_min)       # 10:00-10:01 ET
    news_days = {c["params"]["start"][:10] for c in world["sess"].news_calls}
    assert {"2024-01-20", "2024-01-21"} <= news_days                                                    # the weekend was fetched


def test_a_rerun_is_free_and_writes_byte_identical_outputs(world, tmp_path):
    n = world["client"].requests_made
    data2 = NG.prepare(world["layer"], "2024-01-22", "2024-01-23", NG.NewsGapConfig(), etfs=ETFS)
    res2 = NG.analyse(data2)
    assert world["client"].requests_made == n and res2["results"] == world["res"]["results"]
    hdr = {"run_id": "t", "variants": {v.name: dict(v.overrides) for v in NG.ALL_VARIANTS}, "valid_for_verdict": False, "invalid_reasons": ["test"]}
    for name, data, res in (("a", world["data"], world["res"]), ("b", data2, res2)):
        NG.write_run(tmp_path / name, hdr, res["results"], res["metrics"], None, res["comparison"], res["types"], res["stats"],
                     res["events"], res["largest_gaps"], data)
    for rel in ("primary/trades.csv", "primary/daily.csv", "control_no_news/trades.csv", "summary.md", "picks.csv", "daily_counts.csv", "comparison.json"):
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes(), rel
    text = (tmp_path / "a" / "summary.md").read_text(encoding="utf-8")
    assert "NOT A PRE-REGISTERED RUN" in text and "News vs. no-news gaps" in text and "Event types" in text and "Data statistics" in text


# ================================================================================== the verifier
def _run_dir(world, tmp_path):
    res, data = world["res"], world["data"]
    hdr = {"run_id": "t", "variants": {v.name: dict(v.overrides) for v in NG.ALL_VARIANTS}, "valid_for_verdict": False, "invalid_reasons": ["test"]}
    out = tmp_path / "run"
    NG.write_run(out, hdr, res["results"], res["metrics"], None, res["comparison"], res["types"], res["stats"], res["events"], res["largest_gaps"], data)
    return out


def test_the_verifier_confirms_a_correct_run_and_catches_a_tampered_one(world, tmp_path):
    run = _run_dir(world, tmp_path)
    calls = world["client"].requests_made
    ok = V.check(world["layer"], run, n_primary=5, n_control=4, seed=1, etfs=ETFS, start="2024-01-22", end="2024-01-23")
    assert ok["pass"] and ok["primary"]["sampled"] == 5 and ok["control"]["sampled"] == 4
    assert ok["primary"]["matched"] == 5 and ok["control"]["matched"] == 4
    assert world["client"].requests_made == calls                                                 # offline: zero API calls
    p = run / "primary" / "trades.csv"
    df = pd.read_csv(p, dtype={"day": str})
    df.loc[0, "net_pnl"] += 1.0
    df.loc[1, "shares"] += 1
    df.to_csv(p, index=False)
    c = run / "control_no_news" / "trades.csv"
    cd = pd.read_csv(c, dtype={"day": str})
    cd.loc[0, "g"] = cd.loc[0, "g"] + 0.01
    cd.to_csv(c, index=False)
    bad = V.check(world["layer"], run, n_primary=5, n_control=4, seed=1, etfs=ETFS, start="2024-01-22", end="2024-01-23")
    assert not bad["pass"] and bad["primary"]["matched"] == 3 and bad["control"]["matched"] == 3
    assert "FAIL" in V.render(bad, run) and "PASS" in V.render(ok, run)


# ====================================================================================== the CLI
def test_the_cli_labels_a_non_preregistered_run_writes_the_files_and_refuses_2026(world, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(NG, "DataLayer", lambda *_a, **_k: world["layer"])
    monkeypatch.setattr(NG, "load_etf_list", lambda *_a, **_k: EtfList(ETFS, ()))
    out, review = tmp_path / "out", tmp_path / "rev" / "results.md"
    assert NG.main(["run", "--start", "2024-01-22", "--end", "2024-01-23", "--out", str(out), "--review-file", str(review)]) == 0
    run_dir = next(out.iterdir())
    header = json.loads((run_dir / "header.json").read_text(encoding="utf-8"))
    assert header["valid_for_verdict"] is False and header["metrics_verdict"] is None and any("window" in r for r in header["invalid_reasons"])
    assert header["preregistration"]["path"] == "docs/specs/News_Gap_Preregistration.md" and header["config_diff_from_default"] == {}
    assert header["holdout"].startswith("2026 was never read") and header["symbols_considered"] == len(SYMS) - 1
    assert (run_dir / "primary" / "trades.csv").is_file() and (run_dir / "control_no_news" / "daily.csv").is_file()
    assert "NOT A PRE-REGISTERED RUN" in review.read_text(encoding="utf-8")

    class NoLayer:
        def __init__(self, *_a, **_k):
            raise AssertionError("the data layer must not even be constructed for a 2026 request")
    monkeypatch.setattr(NG, "DataLayer", NoLayer)
    assert NG.main(["run", "--start", "2025-12-01", "--end", "2026-01-05", "--out", str(out)]) == 2
    assert "holdout" in capsys.readouterr().err.lower()


# ============================================================== verifier tolerance (found on the real run)
class _StubWorld:
    """Just enough of `V.World` for `_check_set`: one day's top-5 and its 10:00 open."""

    def top5(self, day, kind):
        return [("AAA", 1 / 3, 30.0, 31.0)]                              # g = 1/3: no terminating decimal expansion

    def open_10(self, day, sym):
        return 30.5


def test_the_verifier_accepts_a_gap_as_trades_csv_records_it_rounded_to_8_decimals():
    # trades.csv writes g with 8 decimals; the verifier once compared at 1e-9 and flagged ~86% of real trades on "g" alone
    shares = math.floor(5000 / 30.5)
    net = shares * (31.0 - 30.5) - shares * 0.02 - 2 * shares * 0.0035
    row = {"day": "2024-01-22", "symbol": "AAA", "side": "long", "g": float(f"{1 / 3:.8f}"), "daily_open": 30.0,
           "entry_open": 30.5, "exit_price": 31.0, "shares": shares, "net_pnl": net}
    ok = V._check_set(_StubWorld(), pd.DataFrame([row]), "news", 1, random.Random(1))
    assert ok == {"sampled": 1, "matched": 1, "differences": []}
    row["g"] += 0.0001                                                   # a real gap difference is still caught
    bad = V._check_set(_StubWorld(), pd.DataFrame([row]), "news", 1, random.Random(1))
    assert bad["matched"] == 0 and bad["differences"][0]["fields"] == ["g"]
