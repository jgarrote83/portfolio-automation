"""Phase 1 -- `get_bars` end to end against the fake Alpaca market (mocked HTTP only).

The real client, rate limiter, manifest and parquet store all run; only the HTTP layer is fake.
"""
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from backtest.data import CacheMissError, DataLayer, HoldoutError, configure, get_bars  # noqa: E402
from backtest.data import config as C  # noqa: E402
from backtest.data.client import AlpacaDataClient  # noqa: E402
from backtest.data.credentials import MissingCredentialsError  # noqa: E402
from backtest.data.manifest import window_key  # noqa: E402
from backtest.data.ratelimit import RateLimiter  # noqa: E402
from backtest_fakes import ASSETS_URL, BARS_URL, FakeClock, FakeMarket, FakeSession  # noqa: E402

LATER = datetime(2025, 6, 1, 16, 0, tzinfo=timezone.utc)      # "now" for tests of historical ranges


def make_layer(tmp_path, market=None, now=LATER, script=None):
    clk = FakeClock()
    sess = FakeSession(market or FakeMarket(), script)
    client = AlpacaDataClient("PKTEST", "SECRETTEST", session=sess, sleep=clk.sleep,
                              limiter=RateLimiter(180, 10, clock=clk.now, sleep=clk.sleep))
    return DataLayer(tmp_path / "cache", client=client, now=lambda: now), sess, client


# ------------------------------------------------------------------------ shape + reuse
def test_daily_bars_have_the_spec_columns_and_us_eastern_timestamps(tmp_path):
    layer, _, _ = make_layer(tmp_path)
    df = layer.get_bars(["BBB", "AAA"], "2024-01-02", "2024-01-05", "1Day", "sip")
    assert list(df.columns) == ["symbol", "ts", "open", "high", "low", "close", "volume"]
    assert str(df["ts"].dt.tz) == "US/Eastern" and len(df) == 8
    assert list(df["symbol"].unique()) == ["BBB", "AAA"]                     # caller's order kept
    assert df.groupby("symbol")["ts"].apply(lambda s: s.is_monotonic_increasing).all()
    assert {t.date() for t in df["ts"]} == {date(2024, 1, d) for d in (2, 3, 4, 5)}
    assert df["volume"].dtype.kind == "i" and df["open"].dtype.kind == "f"


def test_a_repeated_call_makes_zero_requests(tmp_path):
    layer, sess, client = make_layer(tmp_path)
    a = layer.get_bars(["AAA", "BBB"], "2024-01-02", "2024-01-31", "1Day")
    n = client.requests_made
    assert n >= 2 and len(sess.calls) == n
    b = layer.get_bars(["AAA", "BBB"], "2024-01-02", "2024-01-31", "1Day")
    assert client.requests_made == n and len(sess.calls) == n
    pd.testing.assert_frame_equal(a, b)
    layer.get_bars(["AAA"], "2024-01-10", "2024-01-12", "1Day")              # a subset is also free
    assert client.requests_made == n


def test_growing_the_request_fetches_only_what_is_missing(tmp_path):
    layer, sess, _ = make_layer(tmp_path)
    layer.get_bars(["AAA"], "2024-01-02", "2024-01-05", "1Day")
    sess.calls.clear()
    df = layer.get_bars(["AAA", "BBB"], "2024-01-02", "2024-01-05", "1Day")
    assert len(df) == 8 and len(sess.bar_calls) == 1
    assert sess.bar_calls[0]["params"]["symbols"] == "BBB"                   # AAA was already cached


def test_the_two_feeds_are_cached_separately(tmp_path):
    layer, sess, client = make_layer(tmp_path)
    layer.get_bars(["AAA"], "2024-01-02", "2024-01-03", "1Day", "sip")
    n = client.requests_made
    layer.get_bars(["AAA"], "2024-01-02", "2024-01-03", "1Day", "iex")
    assert client.requests_made == n + 1
    assert sess.bar_calls[-1]["params"]["feed"] == "iex"
    assert sess.bar_calls[0]["params"]["adjustment"] == "raw"


# ----------------------------------------------------------------------- calendar + empty days
def test_trading_days_come_from_spy_bars_so_holidays_are_skipped(tmp_path):
    layer, _, _ = make_layer(tmp_path, FakeMarket(holidays=["2024-01-15"]))
    assert layer.trading_days("2024-01-12", "2024-01-17") == [
        date(2024, 1, 12), date(2024, 1, 16), date(2024, 1, 17)]
    df = layer.get_bars(["AAA"], "2024-01-12", "2024-01-17", "1Day")
    assert [t.day for t in df["ts"]] == [12, 16, 17]


def test_empty_days_are_recorded_and_never_refetched(tmp_path):
    layer, sess, client = make_layer(tmp_path, FakeMarket(halted=[("BBB", "2024-01-03")]))
    df = layer.get_bars(["AAA", "BBB"], "2024-01-02", "2024-01-05", "1Day")
    assert len(df[df["symbol"] == "BBB"]) == 3                               # halted day has no bar
    n = client.requests_made
    again = layer.get_bars(["AAA", "BBB"], "2024-01-02", "2024-01-05", "1Day")
    assert client.requests_made == n and len(again) == len(df)
    fetched, bars = layer.manifest.coverage_row("sip", "1Day", "raw", "full", "BBB", "2024-01")
    assert fetched >> 2 & 1 == 1 and bars >> 2 & 1 == 0                      # Jan 3: fetched, no bars


# --------------------------------------------------------------------------- guards
def test_holdout_dates_raise_before_any_cache_or_network_work(tmp_path):
    layer, sess, _ = make_layer(tmp_path, now=datetime(2026, 3, 1, tzinfo=timezone.utc))
    for start, end in [("2025-12-30", "2026-01-02"), ("2026-01-01", "2026-01-05"),
                       ("2026-02-02", "2026-02-03")]:
        with pytest.raises(HoldoutError) as ei:
            layer.get_bars(["SPY"], start, end, "1Day")
        assert "rule 6" in str(ei.value)
    with pytest.raises(HoldoutError):
        layer.trading_days("2025-12-30", "2026-01-02")
    assert sess.calls == [] and not (tmp_path / "cache" / "sip").exists()
    df = layer.get_bars(["SPY"], "2025-12-30", "2026-01-02", "1Day", allow_holdout=True)
    assert len(df) == 4                                                       # only the explicit opt-in
    assert layer.get_bars(["SPY"], "2025-12-29", "2025-12-31", "1Day") is not None   # 2025 is open


def test_today_and_the_last_15_minutes_are_never_cached(tmp_path):
    now = datetime(2024, 6, 5, 18, 0, tzinfo=timezone.utc)                    # 14:00 ET
    layer, sess, _ = make_layer(tmp_path, now=now)
    df = layer.get_bars(["SPY"], "2024-06-04", "2024-06-05", "1Min", "sip")
    days = df["ts"].dt.date
    assert (days == date(2024, 6, 4)).sum() == 390                           # yesterday: full day
    assert 0 < (days == date(2024, 6, 5)).sum() < 390                        # today: partial, in memory
    assert df["ts"].max() <= pd.Timestamp("2024-06-05 13:45", tz="US/Eastern")   # now - 15 minutes
    today_call = [c for c in sess.bar_calls if c["params"]["timeframe"] == "1Min"][-1]
    assert today_call["params"]["end"] <= "2024-06-05T17:45:00Z"
    cached = layer.store.read("sip", "1Min", ["SPY"], ["2024-06"])
    assert set(cached["ts"].dt.date) == {date(2024, 6, 4)}                   # nothing from today on disk
    fetched, _ = layer.manifest.coverage_row("sip", "1Min", "raw", "full", "SPY", "2024-06")
    assert fetched >> 3 & 1 == 1 and fetched >> 4 & 1 == 0                   # Jun 4 yes, Jun 5 no
    sess.calls.clear()
    layer.get_bars(["SPY"], "2024-06-04", "2024-06-05", "1Min", "sip")
    spy_calls = [c for c in sess.bar_calls if c["params"]["timeframe"] == "1Min"]
    assert len(spy_calls) == 1 and spy_calls[0]["params"]["start"].startswith("2024-06-05")   # only today


def test_offline_raises_on_a_miss_and_serves_a_warm_cache_with_no_requests(tmp_path):
    cold = DataLayer(tmp_path / "cold", client=None, now=lambda: LATER)       # no client, no keys
    with pytest.raises(CacheMissError):
        cold.get_bars(["AAA"], "2024-01-02", "2024-01-05", "1Day", offline=True)

    layer, _, client = make_layer(tmp_path)
    online = layer.get_bars(["AAA"], "2024-01-02", "2024-01-05", "1Day")
    n = client.requests_made
    offline = layer.get_bars(["AAA"], "2024-01-02", "2024-01-05", "1Day", offline=True)
    assert client.requests_made == n
    pd.testing.assert_frame_equal(online, offline)
    with pytest.raises(CacheMissError) as ei:
        layer.get_bars(["AAA"], "2024-01-02", "2024-01-31", "1Day", offline=True)    # more days
    assert "not cached" in str(ei.value)
    with pytest.raises(CacheMissError):
        layer.get_bars(["ZZZ"], "2024-01-02", "2024-01-05", "1Day", offline=True)    # other symbol
    assert client.requests_made == n


def test_offline_never_serves_today(tmp_path):
    layer, _, _ = make_layer(tmp_path, now=datetime(2024, 6, 5, 18, 0, tzinfo=timezone.utc))
    layer.get_bars(["SPY"], "2024-06-03", "2024-06-04", "1Day")
    with pytest.raises(CacheMissError) as ei:
        layer.get_bars(["SPY"], "2024-06-03", "2024-06-05", "1Day", offline=True)
    assert "today" in str(ei.value)


def test_missing_keys_only_matter_when_a_fetch_is_needed(tmp_path, monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    monkeypatch.setattr("backtest.data.credentials.DEFAULT_ENV_PATH", tmp_path / "absent.env")
    layer = DataLayer(tmp_path / "c", now=lambda: LATER)
    with pytest.raises(MissingCredentialsError):
        layer.get_bars(["AAA"], "2024-01-02", "2024-01-03", "1Day")


# ------------------------------------------------------------------ intraday, window, DST
def test_pagination_merges_every_bar_exactly_once(tmp_path):
    layer, sess, _ = make_layer(tmp_path, FakeMarket(page_size=50))
    df = layer.get_bars(["SPY"], "2024-01-02", "2024-01-02", "1Min")
    assert len(df) == 390 and df["ts"].is_unique and df["ts"].is_monotonic_increasing
    pages = [c for c in sess.bar_calls if c["params"]["timeframe"] == "1Min"]
    assert len(pages) == 8 and pages[1]["params"]["page_token"] == "50"
    assert (df["ts"].iloc[0].hour, df["ts"].iloc[0].minute) == (9, 30)
    assert (df["ts"].iloc[-1].hour, df["ts"].iloc[-1].minute) == (15, 59)


def test_us_eastern_conversion_is_correct_across_the_march_dst_change(tmp_path):
    layer, sess, _ = make_layer(tmp_path)
    df = layer.get_bars(["SPY"], "2024-03-08", "2024-03-11", "5Min", window=("09:30", "09:35"))
    assert len(df) == 2
    assert [(t.hour, t.minute) for t in df["ts"]] == [(9, 30), (9, 30)]
    assert [t.utcoffset().total_seconds() / 3600 for t in df["ts"]] == [-5.0, -4.0]
    starts = sorted(c["params"]["start"] for c in sess.bar_calls if c["params"]["timeframe"] == "5Min")
    assert starts == ["2024-03-08T14:30:00Z", "2024-03-11T13:30:00Z"]        # 14:30Z then 13:30Z
    # the November change back as well
    df = layer.get_bars(["SPY"], "2024-11-01", "2024-11-04", "5Min", window=("09:30", "09:35"))
    assert [t.utcoffset().total_seconds() / 3600 for t in df["ts"]] == [-4.0, -5.0]


def test_window_requests_end_one_second_early_so_the_0935_bar_is_excluded(tmp_path):
    layer, sess, _ = make_layer(tmp_path)
    df = layer.get_bars(["AAA"], "2024-01-02", "2024-01-02", "1Min", window=("09:30", "09:35"))
    assert [t.minute for t in df["ts"]] == [30, 31, 32, 33, 34]
    call = [c for c in sess.bar_calls if c["params"]["timeframe"] == "1Min"][0]
    assert call["params"]["start"] == "2024-01-02T14:30:00Z" and call["params"]["end"] == "2024-01-02T14:34:59Z"


def test_window_and_full_day_fetches_do_not_alias(tmp_path):
    layer, sess, client = make_layer(tmp_path)
    layer.get_bars(["AAA"], "2024-01-02", "2024-01-03", "5Min", window=("09:30", "09:35"))
    n = client.requests_made
    full = layer.get_bars(["AAA"], "2024-01-02", "2024-01-03", "5Min")        # window != full day
    assert client.requests_made > n and len(full) == 156
    n = client.requests_made
    win = layer.get_bars(["AAA"], "2024-01-02", "2024-01-03", "5Min", window=("09:30", "09:35"))
    assert client.requests_made == n and len(win) == 2                       # full covers the window
    assert layer.manifest.coverage_row("sip", "5Min", "raw", window_key(("09:30", "09:35")),
                                       "AAA", "2024-01") is not None


def test_exact_timestamps_clip_the_result(tmp_path):
    layer, _, _ = make_layer(tmp_path)
    df = layer.get_bars(["AAA"], pd.Timestamp("2024-01-02 09:45", tz="US/Eastern"),
                        pd.Timestamp("2024-01-02 10:00", tz="US/Eastern"), "1Min")
    assert df["ts"].min().strftime("%H:%M") == "09:45" and df["ts"].max().strftime("%H:%M") == "10:00"
    assert len(df) == 16


def test_symbols_are_chunked_per_request(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SYMBOLS_PER_REQUEST", 2)
    layer, sess, _ = make_layer(tmp_path)
    df = layer.get_bars(["AAA", "BBB", "CCC", "DDD", "EEE"], "2024-01-02", "2024-01-03", "1Day")
    assert len(df) == 10
    daily = [c for c in sess.bar_calls if c["params"]["symbols"] != "SPY"]
    assert [c["params"]["symbols"] for c in daily] == ["AAA,BBB", "CCC,DDD", "EEE"]


# --------------------------------------------------------------------- interruption + misc
def test_an_interrupted_write_records_nothing_and_the_retry_refetches(tmp_path, monkeypatch):
    layer, _, client = make_layer(tmp_path)
    real = pd.DataFrame.to_parquet

    def dying(self, target, *a, **k):
        with open(target, "wb") as fh:
            fh.write(b"PAR1-partial")
        raise OSError("interrupted")
    monkeypatch.setattr(pd.DataFrame, "to_parquet", dying)
    with pytest.raises(OSError):
        layer.get_bars(["AAA"], "2024-01-02", "2024-01-05", "1Day")
    monkeypatch.setattr(pd.DataFrame, "to_parquet", real)
    assert [p for p in (tmp_path / "cache").rglob("*") if p.is_file() and p.suffix in (".parquet",)
            or ".tmp-" in p.name] == []                                       # no partial file
    assert layer.manifest.coverage_row("sip", "1Day", "raw", "full", "AAA", "2024-01") is None
    n = client.requests_made
    df = layer.get_bars(["AAA"], "2024-01-02", "2024-01-05", "1Day")
    assert len(df) == 4 and client.requests_made > n                          # refetched, now complete


def test_bad_arguments_and_empty_inputs(tmp_path):
    layer, sess, _ = make_layer(tmp_path)
    with pytest.raises(ValueError):
        layer.get_bars(["AAA"], "2024-01-02", "2024-01-03", "1Hour")
    with pytest.raises(ValueError):
        layer.get_bars(["AAA"], "2024-01-02", "2024-01-03", "1Day", "otc")
    with pytest.raises(ValueError):
        layer.get_bars(["AAA"], "2024-01-05", "2024-01-02", "1Day")
    with pytest.raises(ValueError):
        layer.get_bars(["AAA"], "2024-01-02", "2024-01-03", "1Day", window=("09:30", "09:35"))
    empty = layer.get_bars([], "2024-01-02", "2024-01-03", "1Day")
    assert empty.empty and list(empty.columns) == C.COLUMNS and sess.calls == []


def test_assets_include_inactive_and_are_cached(tmp_path):
    market = FakeMarket()
    market.assets["active"] = [{"symbol": "aaa", "exchange": "NYSE", "name": "A", "tradable": True}]
    market.assets["inactive"] = [{"symbol": "OLD", "exchange": "NASDAQ", "name": "Gone", "status": "inactive"}]
    layer, sess, _ = make_layer(tmp_path, market)
    df = layer.get_assets()
    assert sorted(df["symbol"]) == ["AAA", "OLD"] and set(df["status"]) == {"active", "inactive"}
    assert [c["url"] for c in sess.calls] == [ASSETS_URL, ASSETS_URL]
    layer.get_assets()
    assert len(sess.calls) == 2                                               # cached
    DataLayer(tmp_path / "cache", client=None).get_assets(offline=True)       # offline, from cache
    with pytest.raises(CacheMissError):
        DataLayer(tmp_path / "other", client=None).get_assets(offline=True)


def test_module_level_get_bars_uses_the_configured_layer(tmp_path):
    layer, _, _ = make_layer(tmp_path)
    configure(layer)
    try:
        assert len(get_bars("AAA", "2024-01-02", "2024-01-03", "1Day")) == 2
    finally:
        configure(None)


def test_only_data_reads_ever_hit_the_http_layer(tmp_path):
    layer, sess, _ = make_layer(tmp_path)
    layer.get_bars(["AAA"], "2024-01-02", "2024-01-05", "5Min", window=("09:30", "09:35"))
    assert {c["url"] for c in sess.calls} == {BARS_URL}
    assert {c["method"] for c in sess.calls} == {"GET"}


# ----------------------------------------------------------------------------------- the CLI
def test_cli_offline_flag_fails_cleanly_on_a_miss_and_serves_a_warm_cache(tmp_path, capsys):
    from backtest.data.cli import main
    cache = str(tmp_path / "cache")
    argv = ["--cache-dir", cache, "bars", "--symbols", "AAA", "--start", "2024-01-02",
            "--end", "2024-01-05", "--timeframe", "1Day", "--offline"]
    assert main(argv) == 2                                         # cold cache: error, no traceback, no keys
    assert "error:" in capsys.readouterr().err
    layer, _, _ = make_layer(tmp_path)                             # warm the same cache dir online
    layer.get_bars(["AAA"], "2024-01-02", "2024-01-05", "1Day")
    assert main(argv) == 0
    assert "4 rows, 1 symbols" in capsys.readouterr().out
    assert main(["--cache-dir", cache, "bars", "--symbols", "SPY", "--start", "2025-12-30",
                 "--end", "2026-01-02", "--timeframe", "1Day"]) == 2    # holdout refused by the CLI too
    assert "holdout" in capsys.readouterr().err.lower()
