"""The trade-print half of the data layer (`get_trades`): the exact-window cache, pagination, nanosecond
timestamps and print order, the 2026 refusal, offline mode and failures -- against a fake HTTP session
(no network, no keys)."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`
sys.path.insert(0, os.path.dirname(__file__))                       # tests/, for the fakes

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from backtest.data import DataLayer, get_trades  # noqa: E402
from backtest.data import bars as bars_module  # noqa: E402
from backtest.data.bars import CacheMissError, HoldoutError  # noqa: E402
from backtest.data.client import AlpacaAuthError, AlpacaDataClient  # noqa: E402
from backtest.data.ratelimit import RateLimiter  # noqa: E402
from backtest.data.store import TRADE_COLUMNS  # noqa: E402
from backtest_fakes import FakeClock, FakeMarket  # noqa: E402
from backtest_fakes_trades import CONDITIONS_URL, TRADES_URL, FakeTrades, TradesSession, trade  # noqa: E402

ET = "America/New_York"
START = pd.Timestamp("2024-01-03 09:35:00", tz=ET)
END = pd.Timestamp("2024-01-03 09:36:00", tz=ET)


def NOW():
    return datetime(2025, 6, 1, tzinfo=timezone.utc)


# 09:35:00 ET on 2024-01-03 is 14:35:00Z
PRINTS = [trade("2024-01-03T14:35:00.123456789Z", 100.10, 50, ("@", "I"), i=1),
          trade("2024-01-03T14:35:10.000000000Z", 100.20, 200, ("@",), i=2),
          trade("2024-01-03T14:35:10.000000000Z", 100.00, 300, ("@", "F"), i=3),      # same timestamp as the print before
          trade("2024-01-03T14:35:59.999999999Z", 100.30, 100, ("@", "4"), i=4),
          trade("2024-01-03T14:36:00.000000000Z", 101.00, 100, ("@",), i=5),          # exactly the END bound: excluded
          trade("2024-01-03T14:34:59.999999999Z", 99.00, 100, ("@",), i=6)]           # one nanosecond before START: excluded


def _layer(tmp_path, prints=None, page_size=2, script=None, now=NOW):
    clk = FakeClock()
    sess = TradesSession(FakeMarket(), FakeTrades({"AAA": PRINTS if prints is None else prints}, page_size), script)
    client = AlpacaDataClient("K", "S", session=sess, sleep=clk.sleep,
                              limiter=RateLimiter(180, 10, clock=clk.now, sleep=clk.sleep))
    return DataLayer(tmp_path / "cache", client=client, now=now), sess, client


# ===================================================================================== the request
def test_one_symbol_one_window_paged_with_the_documented_parameters(tmp_path):
    layer, sess, client = _layer(tmp_path)
    layer.get_trades("aaa", START, END)
    calls = sess.trade_calls
    # the fake's end bound is inclusive like Alpaca's, so 5 prints are returned over 3 pages of 2; the 6th is before START
    assert len(calls) == 3 and [c["params"].get("page_token") for c in calls] == [None, "2", "4"]
    p = calls[0]["params"]
    assert p["symbols"] == "AAA" and p["feed"] == "sip" and p["sort"] == "asc" and p["limit"] == 10000
    assert p["start"] == "2024-01-03T14:35:00Z" and p["end"] == "2024-01-03T14:36:00Z"
    assert all(c["url"] == TRADES_URL for c in calls) and client.requests_made == 3
    assert not any(c["url"] == CONDITIONS_URL for c in sess.calls)


def test_the_end_bound_is_exclusive_and_nanosecond_timestamps_and_tie_order_survive(tmp_path):
    df = _layer(tmp_path)[0].get_trades("AAA", START, END)
    assert list(df.columns) == ["symbol", *TRADE_COLUMNS]
    assert df["id"].tolist() == [1, 2, 3, 4]                       # the 14:36:00.000000000 print (id 5) is out
    ts = df["ts"]
    assert str(ts.dt.tz) == "US/Eastern" and ts.iloc[0].nanosecond == 789 and ts.iloc[0].microsecond == 123456
    assert ts.iloc[1] == ts.iloc[2]                                # two prints with the same timestamp
    assert df["seq"].tolist() == [0, 1, 2, 3] and df["price"].tolist() == [100.10, 100.20, 100.00, 100.30]
    assert df["conditions"].tolist() == [["@", "I"], ["@"], ["@", "F"], ["@", "4"]]
    assert df["size"].tolist() == [50, 200, 300, 100] and set(df["symbol"]) == {"AAA"}


# ==================================================================================== the cache
def test_a_repeat_makes_zero_requests_and_an_empty_window_is_a_recorded_fact(tmp_path):
    layer, sess, client = _layer(tmp_path)
    a = layer.get_trades("AAA", START, END)
    n = client.requests_made
    b = layer.get_trades("AAA", START, END)
    assert client.requests_made == n and a.equals(b)
    q0, q1 = pd.Timestamp("2024-01-03 11:00:00", tz=ET), pd.Timestamp("2024-01-03 11:01:00", tz=ET)
    quiet = layer.get_trades("AAA", q0, q1)
    assert quiet.empty and list(quiet.columns) == ["symbol", *TRADE_COLUMNS]
    n2 = client.requests_made
    assert layer.get_trades("AAA", q0, q1).empty
    assert client.requests_made == n2                              # "nothing traded" is cached, not refetched
    f = layer.trade_store.path("sip", "AAA", "2024-01-03", "093500-093600")
    assert f.is_file() and f.parent.name == "2024-01-03"


def test_the_cache_key_is_the_exact_window_so_a_different_window_is_a_new_request(tmp_path):
    layer, sess, client = _layer(tmp_path)
    layer.get_trades("AAA", START, END)
    n = client.requests_made
    layer.get_trades("AAA", START, START + pd.Timedelta(seconds=30))
    assert client.requests_made > n


def test_offline_serves_the_cache_and_raises_on_a_miss(tmp_path):
    layer, sess, client = _layer(tmp_path)
    layer.get_trades("AAA", START, END)
    n = client.requests_made
    assert len(layer.get_trades("AAA", START, END, offline=True)) == 4 and client.requests_made == n
    with pytest.raises(CacheMissError, match="not cached"):
        layer.get_trades("AAA", START + pd.Timedelta(minutes=1), END + pd.Timedelta(minutes=1), offline=True)
    with pytest.raises(CacheMissError):
        layer.get_trades("BBB", START, END, offline=True)
    assert client.requests_made == n


def test_today_is_fetched_but_never_cached(tmp_path):
    day = pd.Timestamp("2025-06-01 09:35:00", tz=ET)
    prints = [trade("2025-06-01T13:35:10Z", 50.0, i=1)]
    layer, sess, client = _layer(tmp_path, prints=prints, now=lambda: datetime(2025, 6, 1, 20, 0, tzinfo=timezone.utc))
    assert len(layer.get_trades("AAA", day, day + pd.Timedelta(seconds=60))) == 1
    n = client.requests_made
    layer.get_trades("AAA", day, day + pd.Timedelta(seconds=60))
    assert client.requests_made > n
    assert not layer.trade_store.has("sip", "AAA", "2025-06-01", "093500-093600")


def test_an_interrupted_write_leaves_neither_a_partial_file_nor_a_false_cache_hit(tmp_path, monkeypatch):
    layer, sess, client = _layer(tmp_path)

    def boom(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr("backtest.data.store.os.replace", boom)
    with pytest.raises(OSError):
        layer.get_trades("AAA", START, END)
    d = layer.trade_store.path("sip", "AAA", "2024-01-03", "093500-093600").parent
    assert (not d.exists()) or not any(d.iterdir())
    monkeypatch.undo()
    n = client.requests_made
    assert len(layer.get_trades("AAA", START, END)) == 4 and client.requests_made > n    # refetched, not served from a stub


# ======================================================================================== guards
def test_2026_is_refused_before_any_cache_or_network_work(tmp_path):
    layer, sess, client = _layer(tmp_path)
    s, e = pd.Timestamp("2026-01-02 09:35:00", tz=ET), pd.Timestamp("2026-01-02 09:36:00", tz=ET)
    with pytest.raises(HoldoutError):
        layer.get_trades("AAA", s, e)
    with pytest.raises(HoldoutError):
        layer.get_trades("AAA", s, e, offline=True)
    assert client.requests_made == 0 and sess.calls == []
    with pytest.raises(HoldoutError):
        get_trades("AAA", s, e)                                    # the module-level wrapper has the same guard


def test_bad_arguments_are_refused(tmp_path):
    layer = _layer(tmp_path)[0]
    with pytest.raises(ValueError, match="exact start and end"):
        layer.get_trades("AAA", "2024-01-03", "2024-01-03")
    with pytest.raises(ValueError, match="one ET trading day"):
        layer.get_trades("AAA", START, END + pd.Timedelta(days=1))
    with pytest.raises(ValueError, match="not after"):
        layer.get_trades("AAA", END, START)
    with pytest.raises(ValueError, match="feed"):
        layer.get_trades("AAA", START, END, feed="otc")
    with pytest.raises(ValueError, match="symbol"):
        layer.get_trades(" ", START, END)


def test_auth_failure_raises_and_is_never_worked_around(tmp_path):
    layer, sess, client = _layer(tmp_path, script=[403])
    with pytest.raises(AlpacaAuthError):
        layer.get_trades("AAA", START, END)
    assert len(sess.trade_calls) == 1


def test_a_retryable_failure_is_retried(tmp_path):
    layer, sess, client = _layer(tmp_path, script=[429])
    assert len(layer.get_trades("AAA", START, END)) == 4 and client.requests_made == 4


def test_the_condition_table_is_fetched_once_per_tape_and_cached(tmp_path):
    layer, sess, client = _layer(tmp_path)
    t1 = layer.trade_conditions("A")
    assert t1["I"] == "Odd Lot Trade" and sess.condition_calls[0]["params"] == {"tape": "A"}
    n = client.requests_made
    assert layer.trade_conditions("A") == t1 and client.requests_made == n
    with pytest.raises(CacheMissError):
        layer.trade_conditions("B", offline=True)


def test_the_client_only_builds_get_requests_on_the_market_data_host(tmp_path):
    layer, sess, client = _layer(tmp_path)
    layer.get_trades("AAA", START, END)
    layer.trade_conditions("A")
    assert {c["method"] for c in sess.calls} == {"GET"}
    assert {c["url"] for c in sess.calls} <= {TRADES_URL, CONDITIONS_URL}


def test_the_module_level_wrapper_uses_the_configured_layer(tmp_path):
    layer, sess, client = _layer(tmp_path)
    bars_module.configure(layer)
    try:
        assert len(get_trades("AAA", START, END)) == 4
    finally:
        bars_module.configure(None)
