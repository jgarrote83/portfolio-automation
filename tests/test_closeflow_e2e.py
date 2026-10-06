"""SPY close-flow end to end on a synthetic market: the REAL data layer and HTTP client against a fake session
(no network, no keys), every pre-registered variant, the IEX check, the verifier and the CLI. The answers are
constructed; nothing here is evidence about the real market."""
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`
sys.path.insert(0, os.path.dirname(__file__))                       # tests/, for backtest_fakes

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from backtest import closeflow as CF  # noqa: E402
from backtest import closeflow_verify as V  # noqa: E402
from backtest.data import DataLayer  # noqa: E402
from backtest.data.client import AlpacaDataClient  # noqa: E402
from backtest.data.ratelimit import RateLimiter  # noqa: E402
from backtest_fakes import ASSETS_URL, BARS_URL, ET, FakeClock, FakeMarket, FakeSession, utc_iso  # noqa: E402

DAYS = [date(2024, 1, 22), date(2024, 1, 23), date(2024, 1, 24), date(2024, 1, 25), date(2024, 1, 26)]
R_SIG = {22: 0.004, 23: -0.003, 24: 0.0, 25: 0.006, 26: -0.007}       # the 15:29 move vs the previous close
R_FH = {22: -0.002, 23: 0.001, 24: 0.003, 25: 0.0, 26: 0.005}          # the 09:59 move (sensitivity a)
IEX_DELTA = {22: 0.01, 23: -0.01, 24: 0.02, 25: 0.0, 26: 0.0}          # IEX 15:29 close minus SIP's
IEX_PREV_SHIFT = 0.02                                                  # IEX daily closes sit 2 cents above SIP's


def close_of(d: date) -> float:
    return 100.0 + 0.25 * (d.day % 9)


def prev_weekday(d: date) -> date:
    p = d - timedelta(days=1)
    while p.weekday() >= 5:
        p -= timedelta(days=1)
    return p


def sig_close(d: date) -> float:
    prev = close_of(prev_weekday(d))
    return prev if R_SIG[d.day] == 0.0 else round(prev * (1 + R_SIG[d.day]), 2)


def entry_open(d: date) -> float:
    return round(sig_close(d) + 0.05, 2)


def fh_close(d: date) -> float:
    prev = close_of(prev_weekday(d))
    return prev if R_FH[d.day] == 0.0 else round(prev * (1 + R_FH[d.day]), 2)


def _minute(feed: str, d: date, mi: int):
    prev = close_of(prev_weekday(d))
    if d not in DAYS:                                                  # background days: flat
        return (close_of(d), close_of(d), close_of(d), close_of(d), 1000)
    sc = sig_close(d) + (IEX_DELTA[d.day] if feed == "iex" else 0.0)
    if mi == 29:                                                       # the bar stamped 09:59
        c = fh_close(d)
        return (prev, max(prev, c), min(prev, c), c, 5000)
    if mi == 359:                                                      # the bar stamped 15:29 (the primary signal)
        return (prev, max(prev, sc), min(prev, sc), sc, 5000)
    if mi == 360:                                                      # the bar stamped 15:30 (the entry)
        o = entry_open(d)
        return (o, o + 0.02, o - 0.02, o, 5000)
    if mi == 389:                                                      # 15:59: a few cents from the daily close
        c = close_of(d) - 0.03
        return (c, c, c, c, 5000)
    return (sig_close(d), sig_close(d), sig_close(d), sig_close(d), 1000)


class SpyMarket(FakeMarket):
    def _daily(self, sym, d):
        shift = IEX_PREV_SHIFT if getattr(self, "feed_hint", "sip") == "iex" else 0.0
        c = close_of(d) + shift
        return {"t": utc_iso(datetime(d.year, d.month, d.day, tzinfo=ET)), "o": c, "h": c + 1, "l": c - 1, "c": c, "v": 1_000_000}

    def query(self, params):
        self.feed_hint = params["feed"]
        return super().query(params)


def _layer(tmp_path, market):
    clk = FakeClock()
    sess = FakeSession(market)
    client = AlpacaDataClient("K", "S", session=sess, sleep=clk.sleep,
                              limiter=RateLimiter(180, 10, clock=clk.now, sleep=clk.sleep))
    layer = DataLayer(tmp_path / "cache", client=client, now=lambda: datetime(2025, 6, 1, tzinfo=timezone.utc))
    return layer, sess, client


def _market():
    mk = SpyMarket(intraday_fn=lambda feed, sym, d, m: _minute(feed, d, m))
    mk.assets["active"] = [{"symbol": "SPY", "exchange": "ARCA", "name": "SPY"}]
    return mk


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("closeflow_e2e")
    layer, sess, client = _layer(tmp, _market())
    res = CF.execute(layer, "2024-01-22", "2024-01-26", log=lambda m: None)
    return {"layer": layer, "sess": sess, "client": client, "res": res, "tmp": tmp}


def _expected(d: date, cents: float = 1.0):
    """Closed-form primary answer for one day (independent of the code under test)."""
    prev, sc = close_of(prev_weekday(d)), sig_close(d)
    if sc == prev:
        return None
    side = "long" if sc > prev else "short"
    o, ex = entry_open(d), close_of(d)
    sh = math.floor(25_000 / o)
    gross = sh * ((ex - o) if side == "long" else (o - ex))
    return {"side": side, "shares": sh, "net": gross - sh * cents / 100 - 2 * sh * 0.0035, "r": sc / prev - 1}


def test_the_primary_trades_match_the_constructed_answer(world):
    outs = {o.day: o for o in world["res"]["results"]["primary"]}
    assert [o.status for o in world["res"]["results"]["primary"]] == ["traded", "traded", "flat", "traded", "traded"]
    for d in DAYS:
        exp = _expected(d)
        if exp is None:
            assert outs[d].trade is None and outs[d].r == 0.0
            continue
        t = outs[d].trade
        assert (t.side, t.shares) == (exp["side"], exp["shares"]) and t.net_pnl == pytest.approx(exp["net"])
        assert t.entry_open == entry_open(d) and t.exit_price == close_of(d)
        assert outs[d].r == pytest.approx(exp["r"])
    m = world["res"]["metrics"]["primary"]
    assert m["trades"] == 4 and m["days_flat"] == 1 and m["days_skipped"] == 0
    assert m["net_pnl"] == pytest.approx(sum(_expected(d)["net"] for d in DAYS if _expected(d)))
    assert go_no_go_is_no(m)


def go_no_go_is_no(m):
    return CF.go_no_go(m)["verdict"] == "NO-GO"                          # one week of one year can never be GO


def test_every_variant_changes_exactly_what_it_says(world):
    res = world["res"]["results"]
    net = lambda v: {o.day: o.trade.net_pnl for o in res[v] if o.trade}      # noqa: E731
    # (d) entry slippage 0 / 2 / 5 cents
    for name, cents in (("slippage_0c", 0.0), ("slippage_2c", 2.0), ("slippage_5c", 5.0)):
        assert net(name) == pytest.approx({d: _expected(d, cents)["net"] for d in DAYS if _expected(d)})
    # (c) long-only: the two short days (Tue, Fri) become no-trade days
    lo = res["long_only"]
    assert [o.status for o in lo] == ["traded", "short_excluded", "flat", "traded", "short_excluded"]
    # (b) |r| >= 0.5%: only Thursday (+0.6%) and Friday (-0.7%)
    th = res["threshold_0.5pct"]
    assert [o.status for o in th] == ["below_threshold", "below_threshold", "flat", "traded", "traded"]
    # (a) the first-half-hour signal reads the 09:59 bar: Mon -0.2% short, Tue +0.1% long, Wed +0.3% long, Thu flat, Fri +0.5% long
    fh = res["signal_first_half_hour"]
    assert [o.status for o in fh] == ["traded", "traded", "traded", "flat", "traded"]
    assert [o.trade.side if o.trade else None for o in fh] == ["short", "long", "long", None, "long"]
    for o in fh:
        if o.trade:                                                       # the entry and exit are the primary's
            assert o.trade.entry_open == entry_open(o.day) and o.trade.exit_price == close_of(o.day)
    assert [round(o.r * 100, 3) for o in fh if o.r is not None][:2] == [round((fh_close(DAYS[0]) / close_of(prev_weekday(DAYS[0])) - 1) * 100, 3),
                                                                        round((fh_close(DAYS[1]) / close_of(prev_weekday(DAYS[1])) - 1) * 100, 3)]


def test_the_iex_check_finds_the_one_constructed_sign_difference(world):
    iex = world["res"]["iex"]
    a, b = iex["A_iex_bar_over_sip_prev_close"], iex["B_iex_bar_over_iex_prev_close"]
    assert a["days_compared"] == 5 and iex["days_without_iex_signal_bar"] == 0
    assert a["sign_differs_n"] == 1 and a["sign_differs"][0]["day"] == "2024-01-24"      # SIP exactly flat, IEX +2c
    assert a["sign_agreement_pct"] == pytest.approx(80.0)
    assert a["max_abs_diff_bps"] == pytest.approx(0.02 / close_of(prev_weekday(DAYS[2])) * 1e4, rel=1e-6)
    # B divides the IEX bar by the IEX previous close (2 cents above SIP's): recompute the expected numbers by hand
    diffs, flips = [], []
    for d in DAYS:
        prev = close_of(prev_weekday(d))
        r_sip = sig_close(d) / prev - 1
        r_b = (sig_close(d) + IEX_DELTA[d.day]) / (prev + IEX_PREV_SHIFT) - 1
        diffs.append(abs(r_sip - r_b) * 1e4)
        if (r_sip > 0) - (r_sip < 0) != (r_b > 0) - (r_b < 0):
            flips.append(d.isoformat())
    assert b["days_compared"] == 5 and b["sign_differs_n"] == len(flips) == 0           # Wednesday: IEX bar and IEX prev close tie
    assert b["mean_abs_diff_bps"] == pytest.approx(sum(diffs) / 5, rel=1e-9) and b["max_abs_diff_bps"] == pytest.approx(max(diffs), rel=1e-9)


def test_the_sanity_check_and_the_ex_dividend_disclosure(world):
    s = world["res"]["sanity"]["close_vs_last_bar"]
    assert s["days_compared"] == 5 and 2.0 < s["mean_abs_bps"] < 4.0                      # 3 cents on ~$100
    assert world["res"]["sanity"]["primary_skipped_days"] == 0
    ex = world["res"]["exdiv"]
    assert ex["n_listed"] == 7 and ex["n_in_series"] == 0 and ex["days"] == []              # none falls in the test week


def test_only_read_only_data_calls_and_nothing_from_2026(world):
    calls = world["sess"].calls
    assert {c["url"] for c in calls} <= {BARS_URL, ASSETS_URL} and {c["method"] for c in calls} == {"GET"}
    assert all(not c["params"].get("start", "").startswith("2026") for c in world["sess"].bar_calls)
    feeds = {c["params"]["feed"] for c in world["sess"].bar_calls}
    assert feeds == {"sip", "iex"}
    symbols = {s for c in world["sess"].bar_calls for s in c["params"]["symbols"].split(",")}
    assert symbols == {"SPY"}                                                                # one instrument only


def test_a_rerun_is_free_and_writes_byte_identical_outputs(world, tmp_path):
    n = world["client"].requests_made
    again = CF.execute(world["layer"], "2024-01-22", "2024-01-26", log=lambda m: None)
    assert world["client"].requests_made == n and again["results"] == world["res"]["results"]
    hdr = {"run_id": "t", "variants": {v.name: dict(v.overrides) for v in CF.ALL_VARIANTS},
           "valid_for_verdict": False, "invalid_reasons": ["test"]}
    for name, r in (("a", world["res"]), ("b", again)):
        CF.write_run(tmp_path / name, hdr, r["results"], r["metrics"], None, r["exdiv"], r["iex"], r["sanity"])
    for rel in ("primary/trades.csv", "primary/daily.csv", "summary.md", "iex_check.csv", "exdividend.json",
                "slippage_5c/metrics.json"):
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes(), rel
    text = (tmp_path / "a" / "summary.md").read_text(encoding="utf-8")
    assert "NOT A PRE-REGISTERED RUN" in text and "Mechanical verdict" not in text
    assert "## Ex-dividend disclosure" in text and "## IEX vs. SIP check" in text and "Sanity checks" in text


def _run_dir(world, tmp_path):
    res = world["res"]
    hdr = {"run_id": "t", "variants": {v.name: dict(v.overrides) for v in CF.ALL_VARIANTS}, "valid_for_verdict": False,
           "invalid_reasons": ["test"]}
    out = tmp_path / "run"
    CF.write_run(out, hdr, res["results"], res["metrics"], None, res["exdiv"], res["iex"], res["sanity"])
    return out


def test_the_verifier_confirms_a_correct_run_and_catches_a_tampered_one(world, tmp_path):
    run = _run_dir(world, tmp_path)
    calls = world["client"].requests_made
    ok = V.check(world["layer"], run, sample=5, seed=1, end="2024-01-26")
    assert ok["pass"] and ok["sampled"] == 5 and ok["matched"] == 5 and ok["counts_consistent"]
    assert world["client"].requests_made == calls                                           # offline: zero API calls
    path = run / "primary" / "trades.csv"
    df = pd.read_csv(path, dtype={"day": str})
    df.loc[0, "net_pnl"] += 1.0
    df.loc[1, "shares"] += 1
    df.to_csv(path, index=False)
    bad = V.check(world["layer"], run, sample=5, seed=1, end="2024-01-26")
    assert not bad["pass"] and bad["matched"] == 3
    assert {d["day"] for d in bad["differences"]} == {df.loc[0, "day"], df.loc[1, "day"]}
    assert "FAIL" in V.render(bad, run) and "PASS" in V.render(ok, run)


def test_the_cli_labels_a_non_preregistered_run_writes_the_files_and_refuses_2026(world, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(CF, "DataLayer", lambda *_a, **_k: world["layer"])
    out, review = tmp_path / "out", tmp_path / "rev" / "results.md"
    rc = CF.main(["run", "--start", "2024-01-22", "--end", "2024-01-26", "--out", str(out), "--review-file", str(review)])
    assert rc == 0
    run_dir = next(out.iterdir())
    header = json.loads((run_dir / "header.json").read_text(encoding="utf-8"))
    assert header["valid_for_verdict"] is False and header["metrics_verdict"] is None
    assert any("window" in r for r in header["invalid_reasons"])
    assert header["preregistration"]["path"] == "docs/specs/CloseFlow_SPY_Preregistration.md"
    assert header["ex_dividend_source"]["dates"] == [d.isoformat() for d in CF.SPY_EX_DIVIDEND_DATES]
    assert header["holdout"].startswith("2026 was never read") and header["config_diff_from_default"] == {}
    assert (run_dir / "primary" / "trades.csv").is_file() and (run_dir / "slippage_5c" / "daily.csv").is_file()
    assert "NOT A PRE-REGISTERED RUN" in review.read_text(encoding="utf-8")

    class NoLayer:
        def __init__(self, *_a, **_k):
            raise AssertionError("the data layer must not even be constructed for a 2026 request")
    monkeypatch.setattr(CF, "DataLayer", NoLayer)
    assert CF.main(["run", "--start", "2025-12-01", "--end", "2026-01-05", "--out", str(out)]) == 2
    assert "holdout" in capsys.readouterr().err.lower()
