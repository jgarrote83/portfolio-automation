"""shared/clients/fred.py never logs the request URL or the API key (FOLLOWUPS #128: the 2026-10-18 leak came from logging a
requests exception, whose text embeds the full URL with api_key=). Uses genuine requests exceptions, not hand-made strings."""
import logging
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shared.clients.fred import FREDClient  # noqa: E402

KEY = "0123456789abcdef0123456789abcdef"          # a fake key with the real one's shape
URL = f"https://api.stlouisfed.org/fred/series/observations?series_id=CHPMINDXM&api_key={KEY}&file_type=json&limit=5"


def http_400():
    r = requests.Response()
    r.status_code, r.reason, r.url = 400, "Bad Request", URL
    try:
        r.raise_for_status()
    except requests.HTTPError as e:                # the genuine exception and message requests produces
        assert KEY in str(e) and "api_key=" in str(e)
        return e
    raise AssertionError("raise_for_status did not raise")


def connection_error():
    # urllib3's own wording: the URL appears WITHOUT a scheme, as a path plus query
    return requests.ConnectionError(f"HTTPSConnectionPool(host='api.stlouisfed.org', port=443): Max retries exceeded with url: "
                                    f"/fred/series/observations?series_id=CHPMINDXM&api_key={KEY}&file_type=json (Caused by NewConnectionError)")


class FailingSession:
    def __init__(self, exc):
        self.exc = exc

    def get(self, *a, **k):
        raise self.exc


class OkSession:
    def get(self, *a, **k):
        r = requests.Response()
        r.status_code, r._content = 200, b'{"observations": [{"date": "2026-10-16", "value": "1.75"}]}'
        return r


@pytest.mark.parametrize("make_exc", [http_400, connection_error])
@pytest.mark.parametrize("call", ["latest", "vintages"])
def test_a_failed_request_logs_no_api_key_and_no_url_query(caplog, make_exc, call):
    c = FREDClient(KEY)
    c.session = FailingSession(make_exc())
    with caplog.at_level(logging.DEBUG):
        out = c.get_series_latest("CHPMINDXM") if call == "latest" else c.get_series_vintages("CHPMINDXM", "2008-01-01", "2025-12-31")
    assert out == []                                                       # behaviour unchanged: failure -> []
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert caplog.records and "CHPMINDXM" in text                           # still logs which series failed
    assert "api_key" not in text.lower() and KEY not in text
    assert "file_type=json" not in text                                     # no query string at all


def test_the_http_status_is_kept_for_diagnosis(caplog):
    c = FREDClient(KEY)
    c.session = FailingSession(http_400())
    with caplog.at_level(logging.ERROR):
        c.get_series_latest("CHPMINDXM")
    assert "HTTPError (HTTP 400)" in caplog.records[0].getMessage()


def test_success_is_unchanged():
    c = FREDClient(KEY)
    c.session = OkSession()
    assert c.get_series_latest("BAA10Y") == [{"date": "2026-10-16", "value": "1.75"}]
    assert c.get_all_series(["BAA10Y"]) == {"BAA10Y": [{"date": "2026-10-16", "value": "1.75"}]}
