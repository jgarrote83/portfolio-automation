"""Phase 1 -- IEX vs SIP report: pure metric functions + a mechanics check on the FAKE market.

The end-to-end test uses a synthetic market with a constructed answer. It validates the report's
logic only; it is NOT evidence about the real feeds (that run is `.review/phase1-iex-vs-sip.md`).
"""
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd  # noqa: E402

from backtest import iex_vs_sip as R  # noqa: E402
from backtest.data import DataLayer  # noqa: E402
from backtest.data.client import AlpacaDataClient  # noqa: E402
from backtest.data.ratelimit import RateLimiter  # noqa: E402
from backtest_fakes import BARS_URL, ASSETS_URL, FakeClock, FakeMarket, FakeSession  # noqa: E402


def _days(n, start=date(2024, 1, 2)):
    return [d.date() for d in pd.bdate_range(start, periods=n)]


def _panel(values: dict[str, list[float]], days) -> pd.DataFrame:
    return pd.DataFrame(values, index=days)


# ---------------------------------------------------------------------------------- universe
def test_universe_filter_uses_only_prior_days_and_the_days_own_open():
    days = _days(20)
    n = len(days)
    # SYM: price 5.01, volume 1M, true range 0.51 on every day -> passes once 14 prior days exist
    flat = lambda v: [v] * n  # noqa: E731
    panels = {
        "open": _panel({"OK": flat(5.01), "LOWPX": flat(4.99), "LOWVOL": flat(5.01), "LOWATR": flat(5.01)}, days),
        "high": _panel({"OK": flat(5.51), "LOWPX": flat(5.51), "LOWVOL": flat(5.51), "LOWATR": flat(5.49)}, days),
        "low": _panel({"OK": flat(5.0), "LOWPX": flat(5.0), "LOWVOL": flat(5.0), "LOWATR": flat(5.0)}, days),
        "close": _panel({"OK": flat(5.2), "LOWPX": flat(5.2), "LOWVOL": flat(5.2), "LOWATR": flat(5.2)}, days),
        "volume": _panel({"OK": flat(1_000_000), "LOWPX": flat(1_000_000), "LOWVOL": flat(999_999),
                          "LOWATR": flat(1_000_000)}, days),
    }
    ok = R.eligibility(panels)
    last = days[-1]
    assert [c for c in ok.columns if ok.at[last, c]] == ["OK"]
    assert not ok.loc[days[13]].any()              # day 14 has only 13 prior days -> nobody yet
    assert ok.at[days[14], "OK"]                   # first day with a full 14-day history


def test_a_same_day_volume_or_range_spike_cannot_make_a_name_eligible():
    days = _days(20)
    n = len(days)
    quiet = [10.0] * n
    panels = {
        "open": _panel({"X": [10.0] * n}, days), "close": _panel({"X": quiet}, days),
        "high": _panel({"X": [10.1] * (n - 1) + [20.0]}, days),       # huge range on the LAST day only
        "low": _panel({"X": [10.0] * n}, days),
        "volume": _panel({"X": [100_000] * (n - 1) + [90_000_000]}, days),   # volume spike on the LAST day
    }
    assert not R.eligibility(panels).at[days[-1], "X"]               # prior-14 avg is still tiny


def test_core_roster_names_are_excluded():
    days = _days(20)
    n = len(days)
    p = {k: _panel({"SPY": [50.0] * n, "ZZZ": [50.0] * n}, days) for k in ("open", "close")}
    p["high"] = _panel({"SPY": [52.0] * n, "ZZZ": [52.0] * n}, days)
    p["low"] = _panel({"SPY": [48.0] * n, "ZZZ": [48.0] * n}, days)
    p["volume"] = _panel({"SPY": [5e6] * n, "ZZZ": [5e6] * n}, days)
    ok = R.eligibility(p, exclude={"SPY"})
    assert [c for c in ok.columns if ok.at[days[-1], c]] == ["ZZZ"]


# --------------------------------------------------------------------------------------- RVOL
def test_relative_volume_never_includes_the_days_own_volume_in_its_average():
    days = _days(16)
    vol = pd.DataFrame({"A": [5.0] * 15 + [10.0]}, index=days)
    rv = R.relative_volume(vol, days, ["A"])
    assert rv.at[days[-1], "A"] == 2.0                    # 10 / mean(prior 14 = 5), not 10 / ~5.3
    assert pd.isna(rv.at[days[13], "A"])                  # fewer than 14 prior days -> undefined


def test_a_missing_opening_bar_counts_as_zero_and_a_zero_average_is_excluded():
    days = _days(16)
    vol = pd.DataFrame({"A": [4.0] * 7 + [float("nan")] * 7 + [4.0, 8.0]}, index=days)
    rv = R.relative_volume(vol, days, ["A", "NEVER"])
    assert rv.at[days[-1], "A"] == 8.0 / 2.0              # prior avg = 28/14 = 2 (NaN days are zero)
    assert rv["NEVER"].isna().all()                       # zero average -> excluded, never inf


def test_top_n_orders_by_rvol_breaks_ties_by_symbol_and_applies_the_100pct_floor():
    row = pd.Series({"B": 3.0, "A": 3.0, "C": 0.99, "D": 1.0, "E": float("nan")})
    assert R.top_n(row, ["A", "B", "C", "D", "E", "F"]) == ["A", "B", "D"]
    assert R.top_n(row, ["A", "B", "D"], n=2) == ["A", "B"]


def test_direction_is_up_down_doji_or_unknown():
    assert R.direction(10, 11) == "up" and R.direction(10, 9) == "down"
    assert R.direction(10, 10) == "doji" and R.direction(None, 1) is None
    assert R.direction(float("nan"), 1) is None


def test_needed_opening_bars_cover_the_lookback_for_every_candidate_day():
    all_days = _days(20)
    need = R.needed_open_symbols({all_days[15]: ["X"], all_days[17]: ["Y"]}, all_days)
    assert all_days[15] in need and all_days[1] in need and all_days[0] not in need
    assert need[all_days[17]] == {"Y"} and need[all_days[3]] == {"X", "Y"}


def test_summary_statistics_on_a_hand_built_table():
    df = pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03", "2025-01-02", "2025-01-03"],
        "overlap_n": [20, 16, 10, 4], "sip_qualified_n": [30, 30, 15, 30], "iex_qualified_n": [30, 30, 30, 12],
        "sip_top_n": [20, 20, 15, 20], "dir_agree_n": [18, 10, 5, 6], "dir_den": [20, 20, 10, 10],
        "iex_missing_n": [0, 0, 5, 5], "universe_n": [100, 100, 100, 100]})
    s = R.summarize(df)
    assert s["mean_overlap"] == 12.5 and s["median_overlap"] == 13.0
    assert s["share_ge_15"] == 0.5 and s["share_le_10"] == 0.5
    assert abs(s["direction_agreement"] - 39 / 60) < 1e-9 and s["direction_den"] == 60
    assert abs(s["iex_bar_missing_share"] - 10 / 75) < 1e-9
    assert s["days_sip_lt_20_qualified"] == 1 and s["days_iex_lt_20_qualified"] == 1
    assert s["by_year"]["2024"]["mean_overlap"] == 18.0 and s["worst_ten"][0]["date"] == "2025-01-03"
    assert "Mean overlap" in R.render_summary(s, {"requests_made": 5, "disk_bytes": 1e6}, wall_s=60,
                                              start="a", end="b", n_symbols=3, limited=False)


# ----------------------------------------------------------- end to end on the fake market
SPIKE_DAY = date(2024, 1, 10)
SYMS = [f"S{i:02d}" for i in range(24)]            # S00..S23; S23 plays "a Core roster name"


def _sip_spike(i):
    return 2000 + 100 * i


def _iex_spike(i):
    return 2000 + 100 * ((i * 7) % 23)


def _intraday(feed, sym, d, m):
    i = int(sym[1:]) if sym[1:].isdigit() else 0
    vol = 1000
    if m == 0 and d == SPIKE_DAY:
        vol = _sip_spike(i) if feed == "sip" else _iex_spike(i)
    up = feed == "sip" or i % 2 == 0                   # IEX shows odd-numbered names closing DOWN
    o = 50.0
    return o, o + 1, o - 1, (o + 0.5 if up else o - 0.5), vol


def _daily(sym, d):
    return 50.0, 52.0, 49.0, 51.0, 2_000_000


def test_end_to_end_overlap_and_direction_match_the_constructed_answer(tmp_path):
    market = FakeMarket(intraday_fn=_intraday, daily_fn=_daily)
    market.assets["active"] = [{"symbol": s, "exchange": "NYSE", "name": s} for s in SYMS] + [
        {"symbol": "OTCX", "exchange": "OTC", "name": "otc"}]
    market.assets["inactive"] = [{"symbol": "OLD1", "exchange": "NASDAQ", "name": "gone", "status": "inactive"}]
    clk = FakeClock()
    sess = FakeSession(market)
    client = AlpacaDataClient("K", "S", session=sess, sleep=clk.sleep,
                              limiter=RateLimiter(180, 10, clock=clk.now, sleep=clk.sleep))
    layer = DataLayer(tmp_path / "cache", client=client,
                      now=lambda: datetime(2025, 6, 1, tzinfo=timezone.utc))
    csv, md = tmp_path / "out.csv", tmp_path / "out.md"
    df, stats = R.run("2024-01-09", "2024-01-11", layer, csv_path=csv, summary_path=md,
                      exclude={"S23"}, fetch_from="2023-12-01")

    # only data reads happened, and the Core-excluded / OTC names were never candidates
    assert {c["url"] for c in sess.calls} <= {BARS_URL, ASSETS_URL}
    assert {c["method"] for c in sess.calls} == {"GET"}
    assert "OTCX" not in ",".join(c["params"].get("symbols", "") for c in sess.bar_calls)
    assert "S23" not in "".join(c["params"]["symbols"] for c in sess.bar_calls if c["params"]["timeframe"] == "5Min")

    row = df[df["date"] == SPIKE_DAY.isoformat()].iloc[0]
    cand = [s for s in SYMS if s != "S23"]
    sip20 = sorted(cand, key=lambda s: (-_sip_spike(int(s[1:])), s))[:20]
    iex20 = sorted(cand, key=lambda s: (-_iex_spike(int(s[1:])), s))[:20]
    assert row["sip_top20"].split() == sip20 and row["iex_top20"].split() == iex20
    assert row["overlap_n"] == len(set(sip20) & set(iex20)) and row["overlap_frac"] == row["overlap_n"] / 20
    expected_agree = sum(1 for s in sip20 if int(s[1:]) % 2 == 0)         # SIP up; IEX up only for even names
    assert (row["dir_agree_n"], row["dir_den"]) == (expected_agree, 20) and row["iex_missing_n"] == 0
    # a flat day BEFORE the spike: every name sits at RVOL exactly 1.0 -> both feeds pick the same
    # first 20 by symbol
    flat = df[df["date"] == "2024-01-09"].iloc[0]
    assert flat["overlap_n"] == 20 and flat["sip_top20"] == flat["iex_top20"]
    # the day AFTER the spike: the spike sits in the prior-14-day average, so baseline volume is
    # below 100% of it for every name -> nobody qualifies (documents the no-same-day-leak rule)
    after = df[df["date"] == "2024-01-11"].iloc[0]
    assert after["sip_top_n"] == 0 and after["iex_top_n"] == 0 and after["overlap_n"] == 0
    assert stats["days"] == 3 and csv.is_file() and "Mean overlap" in md.read_text(encoding="utf-8")
    # re-running is free: everything is cached
    n = client.requests_made
    R.run("2024-01-09", "2024-01-11", layer, csv_path=None, summary_path=None, exclude={"S23"})
    assert client.requests_made == n


def test_end_to_end_runs_fully_offline_after_a_warm_run(tmp_path):
    market = FakeMarket(intraday_fn=_intraday, daily_fn=_daily)
    market.assets["active"] = [{"symbol": s, "exchange": "NYSE", "name": s} for s in SYMS[:6]]
    clk = FakeClock()
    client = AlpacaDataClient("K", "S", session=FakeSession(market), sleep=clk.sleep,
                              limiter=RateLimiter(180, 10, clock=clk.now, sleep=clk.sleep))
    now = lambda: datetime(2025, 6, 1, tzinfo=timezone.utc)  # noqa: E731
    warm = DataLayer(tmp_path / "c", client=client, now=now)
    a, _ = R.run("2024-01-09", "2024-01-10", warm, csv_path=None, summary_path=None, exclude=set())
    cold = DataLayer(tmp_path / "c", client=None, now=now)                  # no client, no keys
    b, _ = R.run("2024-01-09", "2024-01-10", cold, csv_path=None, summary_path=None, exclude=set(),
                 offline=True)
    pd.testing.assert_frame_equal(a, b)
