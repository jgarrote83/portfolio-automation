"""The news half of the data layer (`get_news`): pagination, the cache, ET-day boundaries across DST changes,
the 2026 refusal, offline mode and failure handling -- all against a fake HTTP session (no network, no keys)."""
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`
sys.path.insert(0, os.path.dirname(__file__))                       # tests/, for backtest_fakes

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from backtest.data import DataLayer, get_news  # noqa: E402
from backtest.data import bars as bars_module  # noqa: E402
from backtest.data.bars import CacheMissError, HoldoutError  # noqa: E402
from backtest.data.client import AlpacaAuthError, AlpacaDataClient  # noqa: E402
from backtest.data.manifest import Manifest  # noqa: E402
from backtest.data.ratelimit import RateLimiter  # noqa: E402
from backtest.data.store import NEWS_COLUMNS  # noqa: E402
from backtest_fakes import BARS_URL, NEWS_URL, FakeClock, FakeMarket, FakeNews, NewsSession, article  # noqa: E402


def NOW():
    return datetime(2025, 6, 1, tzinfo=timezone.utc)


def _layer(tmp_path, articles, page_size=3, script=None, now=NOW):
    clk = FakeClock()
    sess = NewsSession(FakeMarket(), FakeNews(articles, page_size), script)
    client = AlpacaDataClient("K", "S", session=sess, sleep=clk.sleep,
                              limiter=RateLimiter(180, 10, clock=clk.now, sleep=clk.sleep))
    return DataLayer(tmp_path / "cache", client=client, now=now), sess, client


# ======================================================================================= pagination
def test_pagination_follows_the_page_token_and_returns_every_article_oldest_first(tmp_path):
    arts = [article(100 + i, f"2024-01-03T15:{i:02d}:00Z", symbols=[f"S{i}"]) for i in range(10)]
    layer, sess, client = _layer(tmp_path, arts, page_size=3)
    df = layer.get_news("2024-01-03", "2024-01-03")
    assert df["id"].tolist() == [100 + i for i in range(10)] and list(df.columns) == NEWS_COLUMNS
    calls = sess.news_calls
    assert len(calls) == 4 and [c["params"].get("page_token") for c in calls] == [None, "3", "6", "9"]   # 10 articles, 3 a page
    p = calls[0]["params"]
    assert p["sort"] == "asc" and p["limit"] == 50 and p["include_content"] == "false" and "symbols" not in p
    assert client.requests_made == 4


def test_only_the_six_stored_fields_come_back_with_symbols_as_a_list_and_utc_timestamps(tmp_path):
    arts = [article(1, "2024-01-03T14:00:00Z", symbols=["AAA", "BBB"], headline="Big news", updated_at="2024-01-03T14:00:05Z"),
            article(2, "2024-01-03T14:01:00Z", symbols=[], source="other")]
    df = _layer(tmp_path, arts)[0].get_news("2024-01-03", "2024-01-03")
    assert list(df.columns) == ["id", "created_at", "updated_at", "headline", "symbols", "source"]
    assert df.loc[0, "symbols"] == ["AAA", "BBB"] and df.loc[1, "symbols"] == [] and df.loc[1, "source"] == "other"
    assert str(df["created_at"].dt.tz) == "UTC"
    assert (df.loc[0, "updated_at"] - df.loc[0, "created_at"]).total_seconds() == 5
    assert df.loc[0, "headline"] == "Big news"


# ============================================================================================= cache
def test_a_repeated_call_makes_zero_requests_and_only_missing_days_are_fetched(tmp_path):
    arts = [article(1, "2024-01-02T20:00:00Z"), article(2, "2024-01-03T20:00:00Z"), article(3, "2024-01-04T20:00:00Z")]
    layer, sess, client = _layer(tmp_path, arts, page_size=50)
    first = layer.get_news("2024-01-02", "2024-01-03")
    n = client.requests_made
    assert n == 2 and first["id"].tolist() == [1, 2]                      # one request per ET calendar day
    again = layer.get_news("2024-01-02", "2024-01-03")
    sub = layer.get_news("2024-01-03", "2024-01-03")
    assert client.requests_made == n and again["id"].tolist() == [1, 2] and sub["id"].tolist() == [2]
    wider = layer.get_news("2024-01-02", "2024-01-04")                      # only 01-04 is new
    assert client.requests_made == n + 1 and wider["id"].tolist() == [1, 2, 3]
    # a fresh layer on the same cache (a new process) also needs nothing
    fresh = DataLayer(tmp_path / "cache", client=client, now=NOW)
    assert fresh.get_news("2024-01-02", "2024-01-04")["id"].tolist() == [1, 2, 3] and client.requests_made == n + 1


def test_every_calendar_day_is_fetched_weekends_included_and_empty_days_are_not_refetched(tmp_path):
    arts = [article(1, "2024-01-05T21:30:00Z"), article(2, "2024-01-08T13:00:00Z")]       # Friday evening, Monday morning
    layer, sess, client = _layer(tmp_path, arts, page_size=50)
    df = layer.get_news("2024-01-05", "2024-01-08")                       # Fri, Sat, Sun, Mon
    assert df["id"].tolist() == [1, 2] and client.requests_made == 4
    starts = [c["params"]["start"] for c in sess.news_calls]
    assert starts == ["2024-01-05T05:00:00Z", "2024-01-06T05:00:00Z", "2024-01-07T05:00:00Z", "2024-01-08T05:00:00Z"]
    layer.get_news("2024-01-06", "2024-01-07")                            # the two empty weekend days: cached as empty
    assert client.requests_made == 4
    assert layer.cache_stats()["news_days"] == 4 and layer.cache_stats()["news_articles"] == 2


def test_the_cache_layout_is_one_parquet_file_per_month_and_a_rewrite_deduplicates_by_id(tmp_path):
    arts = [article(1, "2024-01-30T20:00:00Z"), article(2, "2024-02-01T20:00:00Z")]
    layer, _s, _c = _layer(tmp_path, arts, page_size=50)
    layer.get_news("2024-01-30", "2024-02-01")
    assert (tmp_path / "cache" / "news" / "2024-01.parquet").is_file() and (tmp_path / "cache" / "news" / "2024-02.parquet").is_file()
    assert layer.news_store.write("2024-01", bars_module.normalise_news([article(1, "2024-01-30T20:00:00Z", headline="edited")]))[0] == 1
    assert layer.news_store.read(["2024-01"]).loc[0, "headline"] == "edited"                   # keep=last, still one row


def test_an_interrupted_write_records_nothing_and_the_retry_refetches(tmp_path, monkeypatch):
    layer, _sess, client = _layer(tmp_path, [article(1, "2024-01-03T20:00:00Z")], page_size=50)

    def boom(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(layer.news_store, "write", boom)
    with pytest.raises(OSError):
        layer.get_news("2024-01-03", "2024-01-03")
    assert layer.manifest.news_missing([date(2024, 1, 3)]) == [date(2024, 1, 3)]            # parquet first, manifest second
    assert not list((tmp_path / "cache" / "news").glob("*.tmp-*"))
    monkeypatch.undo()
    n = client.requests_made
    assert layer.get_news("2024-01-03", "2024-01-03")["id"].tolist() == [1] and client.requests_made == n + 1


# ================================================================ ET-day boundaries across DST changes
def test_each_request_covers_one_et_calendar_day_across_the_spring_and_autumn_dst_changes(tmp_path):
    layer, sess, _c = _layer(tmp_path, [], page_size=50)
    layer.get_news("2024-03-09", "2024-03-11")
    layer.get_news("2024-11-02", "2024-11-04")
    spans = [(c["params"]["start"], c["params"]["end"]) for c in sess.news_calls]
    assert spans[:3] == [("2024-03-09T05:00:00Z", "2024-03-10T04:59:59Z"),      # EST: midnight = 05:00Z
                         ("2024-03-10T05:00:00Z", "2024-03-11T03:59:59Z"),      # the 23-hour day: DST starts at 02:00
                         ("2024-03-11T04:00:00Z", "2024-03-12T03:59:59Z")]      # EDT: midnight = 04:00Z
    assert spans[3:] == [("2024-11-02T04:00:00Z", "2024-11-03T03:59:59Z"),
                         ("2024-11-03T04:00:00Z", "2024-11-04T04:59:59Z"),      # the 25-hour day: DST ends at 02:00
                         ("2024-11-04T05:00:00Z", "2024-11-05T04:59:59Z")]


def test_an_article_belongs_to_the_et_day_of_its_created_at_not_its_utc_date(tmp_path):
    arts = [article(1, "2024-03-10T04:59:59Z"),          # 23:59:59 EST on Mar 9
            article(2, "2024-03-10T05:00:00Z"),          # 00:00:00 EST on Mar 10
            article(3, "2024-03-11T03:59:59Z"),          # 23:59:59 EDT on Mar 10 (still the 10th)
            article(4, "2024-03-11T04:00:00Z")]          # 00:00:00 EDT on Mar 11
    layer, _s, _c = _layer(tmp_path, arts, page_size=50)
    assert layer.get_news("2024-03-10", "2024-03-10")["id"].tolist() == [2, 3]
    assert layer.get_news("2024-03-09", "2024-03-09")["id"].tolist() == [1]
    assert layer.get_news("2024-03-11", "2024-03-11")["id"].tolist() == [4]
    df = layer.get_news("2024-03-09", "2024-03-11")
    et = df["created_at"].dt.tz_convert("US/Eastern")
    assert et.dt.strftime("%Y-%m-%d %H:%M:%S").tolist() == ["2024-03-09 23:59:59", "2024-03-10 00:00:00",
                                                            "2024-03-10 23:59:59", "2024-03-11 00:00:00"]


# ================================================================================ holdout and failures
def test_the_2026_holdout_is_refused_before_any_cache_or_network_work(tmp_path):
    layer, sess, client = _layer(tmp_path, [article(1, "2024-01-03T20:00:00Z")])
    for args in (("2025-12-31", "2026-01-01"), ("2026-01-01", "2026-01-05"), ("2026-03-01", "2026-03-02")):
        with pytest.raises(HoldoutError):
            layer.get_news(*args)
    assert client.requests_made == 0 and not (tmp_path / "cache" / "news").exists()
    assert layer.get_news("2025-12-31", "2025-12-31").empty                           # the last in-sample day is allowed
    with pytest.raises(ValueError):
        layer.get_news("2024-01-05", "2024-01-04")


def test_the_module_level_get_news_uses_the_default_layer_and_the_same_guards(tmp_path):
    layer, _s, client = _layer(tmp_path, [article(1, "2024-01-03T20:00:00Z")], page_size=50)
    bars_module.configure(layer)
    try:
        assert get_news("2024-01-03", "2024-01-03")["id"].tolist() == [1]
        with pytest.raises(HoldoutError):
            get_news("2025-12-31", "2026-01-02")
    finally:
        bars_module.configure(None)


def test_offline_raises_on_a_miss_serves_a_warm_cache_and_never_serves_today(tmp_path):
    layer, _s, client = _layer(tmp_path, [article(1, "2024-01-03T20:00:00Z")], page_size=50)
    with pytest.raises(CacheMissError, match="not cached"):
        layer.get_news("2024-01-03", "2024-01-03", offline=True)
    assert client.requests_made == 0
    layer.get_news("2024-01-03", "2024-01-03")
    n = client.requests_made
    assert layer.get_news("2024-01-03", "2024-01-03", offline=True)["id"].tolist() == [1] and client.requests_made == n
    keyless = DataLayer(tmp_path / "cache", client=None, now=NOW)                        # offline needs no client at all
    assert keyless.get_news("2024-01-03", "2024-01-03", offline=True)["id"].tolist() == [1]
    today_layer = DataLayer(tmp_path / "cache", client=client, now=lambda: datetime(2024, 1, 3, 15, 0, tzinfo=timezone.utc))
    with pytest.raises(CacheMissError, match="today"):
        today_layer.get_news("2024-01-03", "2024-01-03", offline=True)


def test_today_is_fetched_into_memory_only_and_never_cached(tmp_path):
    arts = [article(1, "2024-01-03T14:00:00Z"), article(2, "2024-01-03T15:50:00Z")]
    now = lambda: datetime(2024, 1, 3, 21, 0, tzinfo=timezone.utc)                       # 16:00 ET on the 3rd  # noqa: E731
    layer, sess, _c = _layer(tmp_path, arts, page_size=50, now=now)
    df = layer.get_news("2024-01-03", "2024-01-03")
    assert df["id"].tolist() == [1, 2]
    assert not (tmp_path / "cache" / "news").exists() and layer.manifest.news_missing([date(2024, 1, 3)]) == [date(2024, 1, 3)]
    assert sess.news_calls[0]["params"]["end"] == "2024-01-03T20:45:00Z"                 # clamped to now - 15 minutes


def test_a_401_or_403_stops_with_an_auth_error_is_not_retried_and_records_nothing(tmp_path):
    for status in (401, 403):
        layer, sess, client = _layer(tmp_path / str(status), [article(1, "2024-01-03T20:00:00Z")], script=[status])
        with pytest.raises(AlpacaAuthError):
            layer.get_news("2024-01-03", "2024-01-03")
        assert len(sess.news_calls) == 1 and client.requests_made == 1                        # no retry, no workaround
        assert layer.manifest.news_missing([date(2024, 1, 3)]) == [date(2024, 1, 3)]


def test_429_is_retried_with_backoff_like_every_other_request(tmp_path):
    layer, sess, _c = _layer(tmp_path, [article(1, "2024-01-03T20:00:00Z")], page_size=50, script=[429, 503])
    assert layer.get_news("2024-01-03", "2024-01-03")["id"].tolist() == [1] and len(sess.news_calls) == 3


def test_only_read_only_data_urls_are_called_and_only_with_get(tmp_path):
    layer, sess, _c = _layer(tmp_path, [article(1, "2024-01-03T20:00:00Z")], page_size=50)
    layer.get_news("2024-01-03", "2024-01-03")
    assert {c["url"] for c in sess.calls} <= {NEWS_URL, BARS_URL} and {c["method"] for c in sess.calls} == {"GET"}
    assert Manifest(tmp_path / "cache" / "manifest.sqlite").news_missing([date(2024, 1, 3)]) == []
    assert (pd.read_parquet(tmp_path / "cache" / "news" / "2024-01.parquet").columns.tolist() == NEWS_COLUMNS)
