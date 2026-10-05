"""Phase 1 -- the Alpaca data client: pagination, retry/backoff, read-only, no key leakage."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402

from backtest.data.client import AlpacaAuthError, AlpacaDataClient, AlpacaDataError  # noqa: E402
from backtest.data.credentials import MissingCredentialsError, load_credentials, parse_env  # noqa: E402
from backtest_fakes import ASSETS_URL, BARS_URL, FakeClock, FakeMarket, FakeSession  # noqa: E402

KEY, SECRET = "PKTESTKEY123456", "s3cr3t-value-ABCDEF"


def _client(market=None, script=None, clock=None, **kw):
    clk = clock or FakeClock()
    sess = FakeSession(market or FakeMarket(), script)
    return AlpacaDataClient(KEY, SECRET, session=sess, sleep=clk.sleep,
                            limiter=None, **kw), sess, clk


def test_pagination_follows_next_page_token_and_merges_symbols():
    market = FakeMarket(page_size=7)
    cli, sess, _ = _client(market)
    rows = list(cli.iter_bars(["AAA", "BBB"], "5Min", "2024-01-02T14:30:00Z",
                              "2024-01-02T15:29:59Z", "sip"))
    # 2 symbols x 12 five-minute bars, 7 per page -> 4 pages
    assert len(rows) == 24 and len(sess.bar_calls) == 4
    assert [c["params"].get("page_token") for c in sess.bar_calls] == [None, "7", "14", "21"]
    assert {s for s, _ in rows} == {"AAA", "BBB"}
    assert cli.requests_made == 4


def test_request_params_are_raw_adjustment_and_the_requested_feed():
    cli, sess, _ = _client()
    list(cli.iter_bars(["SPY"], "1Day", "2024-01-02T05:00:00Z", "2024-01-03T04:59:59Z", "iex"))
    p = sess.bar_calls[0]["params"]
    assert p["adjustment"] == "raw" and p["feed"] == "iex" and p["limit"] == 10_000
    assert p["symbols"] == "SPY" and p["timeframe"] == "1Day" and p["sort"] == "asc"


def test_429_is_retried_with_backoff_and_honours_retry_after():
    cli, sess, clk = _client(script=[(429, {"Retry-After": "7"}), 429, 503])
    rows = list(cli.iter_bars(["SPY"], "1Day", "2024-01-02T05:00:00Z", "2024-01-02T23:00:00Z", "sip"))
    assert len(rows) == 1
    assert cli.requests_made == 4                       # 3 failures + 1 success, each counted
    assert clk.sleeps[0] == 7.0                          # Retry-After beat the 1 s backoff
    assert clk.sleeps[1:3] == [2.0, 4.0]                 # then exponential 2**attempt


def test_retries_give_up_after_the_cap():
    cli, _, _ = _client(script=[500] * 20, max_retries=3)
    with pytest.raises(AlpacaDataError):
        list(cli.iter_bars(["SPY"], "1Day", "2024-01-02T05:00:00Z", "2024-01-02T23:00:00Z", "sip"))
    assert cli.requests_made == 4


def test_auth_failure_is_not_retried_and_never_echoes_a_key():
    cli, sess, _ = _client(script=[403])
    with pytest.raises(AlpacaAuthError) as ei:
        list(cli.iter_bars(["SPY"], "1Day", "2024-01-02T05:00:00Z", "2024-01-02T23:00:00Z", "sip"))
    assert len(sess.calls) == 1
    text = str(ei.value) + repr(cli)
    assert KEY not in text and SECRET not in text


def test_repr_and_attributes_do_not_expose_keys():
    cli, _, _ = _client()
    assert KEY not in repr(cli) and SECRET not in repr(cli)
    assert KEY not in str(cli) and SECRET not in str(cli)
    assert not {"key", "secret", "headers", "_headers"} & set(vars(cli))   # held name-mangled, private


def test_only_the_two_read_only_urls_are_ever_called_and_only_with_get():
    market = FakeMarket()
    market.assets["active"] = [{"symbol": "AAA", "exchange": "NYSE"}]
    cli, sess, _ = _client(market)
    list(cli.iter_bars(["SPY"], "1Day", "2024-01-02T05:00:00Z", "2024-01-02T23:00:00Z", "sip"))
    assert cli.list_assets("active") == market.assets["active"]
    assert {c["url"] for c in sess.calls} == {BARS_URL, ASSETS_URL}
    assert {c["method"] for c in sess.calls} == {"GET"}
    assert sess.calls[-1]["params"] == {"status": "active", "asset_class": "us_equity"}
    with pytest.raises(ValueError):
        cli.list_assets("all")
    assert not any(hasattr(cli, n) for n in ("submit_order", "cancel_order", "get_account", "get_clock"))


def test_credentials_missing_message_has_instructions_and_no_secrets(tmp_path, monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    with pytest.raises(MissingCredentialsError) as ei:
        load_credentials(tmp_path / "nope.env")
    assert ".env" in str(ei.value) and "never paste" in str(ei.value)


def test_env_file_is_only_read_when_git_confirms_it_is_ignored(tmp_path, monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    f = tmp_path / ".env"                                # outside the repo -> cannot be confirmed
    f.write_text(f"ALPACA_API_KEY={KEY}\nALPACA_API_SECRET={SECRET}\n")
    with pytest.raises(MissingCredentialsError) as ei:
        load_credentials(f)
    assert "refusing to read" in str(ei.value) and KEY not in str(ei.value)


def test_environment_variables_win_and_parse_env_handles_quotes_and_comments(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k1")
    monkeypatch.setenv("ALPACA_API_SECRET", "s1")
    assert load_credentials() == ("k1", "s1")
    assert parse_env("# c\nexport A='x y'\nB=\"z\"\n\nC=1\n") == {"A": "x y", "B": "z", "C": "1"}


# ------------------------------------------------------------------ invalid symbols (found live)
class _RejectingSession(FakeSession):
    """Mimics the real bars endpoint: ONE unknown symbol in a multi-symbol request fails the whole
    request with HTTP 400 `invalid symbol: X` (naming only the first offender)."""

    def __init__(self, market, bad, **kw):
        super().__init__(market, **kw)
        self.bad = set(bad)

    def get(self, url, params=None, headers=None, timeout=None):
        if url == BARS_URL:
            asked = [s for s in params["symbols"].split(",") if s]
            offenders = [s for s in asked if s in self.bad]
            if offenders:
                from backtest_fakes import FakeResponse
                self.calls.append({"url": url, "params": dict(params), "method": "GET"})
                return FakeResponse(400, {"message": f"invalid symbol: {offenders[0]}"})
        return super().get(url, params=params, headers=headers, timeout=timeout)


def test_an_invalid_symbol_is_dropped_and_the_request_retried_for_the_rest():
    sess = _RejectingSession(FakeMarket(), {"0029900E0", "046CVR015"})
    cli = AlpacaDataClient(KEY, SECRET, session=sess, sleep=FakeClock().sleep, limiter=None)
    rows = list(cli.iter_bars(["AAA", "0029900E0", "BBB", "046CVR015"], "1Day",
                              "2024-01-02T05:00:00Z", "2024-01-02T23:59:59Z", "sip"))
    assert {s for s, _ in rows} == {"AAA", "BBB"} and len(rows) == 2
    assert cli.invalid_symbols == {"0029900E0", "046CVR015"}
    assert [c["params"]["symbols"] for c in sess.bar_calls] == [
        "AAA,0029900E0,BBB,046CVR015", "AAA,BBB,046CVR015", "AAA,BBB"]
    assert cli.requests_made == 3


def test_a_request_whose_every_symbol_is_invalid_yields_nothing_and_does_not_raise():
    sess = _RejectingSession(FakeMarket(), {"X1", "X2"})
    cli = AlpacaDataClient(KEY, SECRET, session=sess, sleep=FakeClock().sleep, limiter=None)
    assert list(cli.iter_bars(["X1", "X2"], "1Day", "2024-01-02T05:00:00Z", "2024-01-02T23:59:59Z", "sip")) == []
    assert cli.invalid_symbols == {"X1", "X2"}


def test_other_400_errors_still_fail_loudly_and_a_symbol_we_did_not_send_is_not_guessed_at():
    cli, _sess, _ = _client(script=[400])                     # FakeSession's scripted body: "scripted failure"
    with pytest.raises(AlpacaDataError, match="HTTP 400"):
        list(cli.iter_bars(["AAA"], "1Day", "2024-01-02T05:00:00Z", "2024-01-02T23:59:59Z", "sip"))
    assert cli.invalid_symbols == set()

    class NamesAStranger(FakeSession):
        def get(self, url, params=None, headers=None, timeout=None):
            from backtest_fakes import FakeResponse
            return FakeResponse(400, {"message": "invalid symbol: SOMEONEELSE"})

    cli = AlpacaDataClient(KEY, SECRET, session=NamesAStranger(FakeMarket()), sleep=FakeClock().sleep,
                           limiter=None)
    with pytest.raises(AlpacaDataError, match="invalid symbol"):       # not ours: surface it, do not loop
        list(cli.iter_bars(["AAA"], "1Day", "2024-01-02T05:00:00Z", "2024-01-02T23:59:59Z", "sip"))
    assert cli.invalid_symbols == set()


def test_the_layer_records_a_rejected_symbol_as_covered_so_it_is_never_requested_twice(tmp_path):
    from datetime import datetime, timezone

    from backtest.data import DataLayer
    sess = _RejectingSession(FakeMarket(), {"0029900E0"})
    cli = AlpacaDataClient(KEY, SECRET, session=sess, sleep=FakeClock().sleep, limiter=None)
    layer = DataLayer(tmp_path / "cache", client=cli, now=lambda: datetime(2025, 6, 1, tzinfo=timezone.utc))
    df = layer.get_bars(["AAA", "0029900E0"], "2024-01-02", "2024-01-03", "1Day", "sip")
    assert set(df["symbol"]) == {"AAA"}
    n = cli.requests_made
    again = layer.get_bars(["AAA", "0029900E0"], "2024-01-02", "2024-01-03", "1Day", "sip")
    assert cli.requests_made == n and len(again) == len(df)
    assert layer.cache_stats()["invalid_symbols_dropped"] == 1
