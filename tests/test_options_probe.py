"""The read-only options-data probe on a fake Alpaca (no network, no keys): the pure helpers, the GET-only client (retries, logging,
no key leakage, 401/403 recorded and never worked around), contract paging, the earliest-expiration search, bar coverage, and a whole
`run_probe` against constructed data, including the report and the guarantee that no 2026 date is ever requested."""
import json
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`

import pytest  # noqa: E402
from backtest import options_probe as P  # noqa: E402
from backtest.data.ratelimit import RateLimiter  # noqa: E402

KEY, SECRET = "PKTESTKEY123456", "SECRETVALUE987654321"


def occ(root, exp, kind, strike):
    return f"{root}{exp.strftime('%y%m%d')}{kind}{int(round(strike * 1000)):08d}"


def contract(root, exp, strike, kind="P", status="inactive"):
    return {"symbol": occ(root, exp, kind, strike), "underlying_symbol": root, "strike_price": str(strike), "expiration_date": exp.isoformat(),
            "type": "put" if kind == "P" else "call", "status": status, "open_interest": "10", "close_price": "1.0"}


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class FakeAlpaca:
    """Routes GETs by URL; there is deliberately no post/put/delete/request method: a non-GET would raise AttributeError."""

    def __init__(self, contracts=(), bars=None, quotes=(404, {"message": "not found"}), trades=None, feed_status=None, script=None, page=3):
        self.contracts = list(contracts)
        self.bars = bars or {}
        self.quotes = quotes
        self.trades = trades or {}
        self.feed_status = feed_status or {}
        self.script = list(script or [])
        self.page = page
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        params = dict(params or {})
        self.calls.append((url, params, dict(headers or {})))
        if self.script:
            status = self.script.pop(0)
            return FakeResponse(status, {"message": f"scripted {status}"})
        path = "/" + url.split("//", 1)[1].split("/", 1)[1]
        if path == P.CONTRACTS_PATH:
            return FakeResponse(200, self._contracts(params))
        if path == P.BARS_PATH:
            feed = params.get("feed")
            if feed in self.feed_status:
                return FakeResponse(self.feed_status[feed], {"message": f"feed {feed} not permitted"})
            return FakeResponse(200, self._bars(params))
        if path == P.QUOTES_PATH:
            return FakeResponse(*self.quotes)
        if path == P.TRADES_PATH:
            syms = params["symbols"].split(",")
            return FakeResponse(200, {"trades": {s: self.trades.get(s, []) for s in syms if self.trades.get(s)}})
        return FakeResponse(404, {"message": "unknown path"})

    def _contracts(self, q):
        rows = [c for c in self.contracts if c["underlying_symbol"] == q["underlying_symbols"]]
        if "status" in q:
            rows = [c for c in rows if c["status"] == q["status"]]
        if "type" in q:
            rows = [c for c in rows if c["type"] == q["type"]]
        for key, fn in (("expiration_date_gte", lambda c, v: c["expiration_date"] >= v), ("expiration_date_lte", lambda c, v: c["expiration_date"] <= v),
                        ("strike_price_gte", lambda c, v: float(c["strike_price"]) >= float(v)), ("strike_price_lte", lambda c, v: float(c["strike_price"]) <= float(v))):
            if key in q:
                rows = [c for c in rows if fn(c, q[key])]
        rows.sort(key=lambda c: (c["expiration_date"], float(c["strike_price"])))
        limit = min(int(q.get("limit", 100)), self.page)
        off = int(q.get("page_token") or 0)
        page = rows[off:off + limit]
        nxt = off + len(page)
        return {"option_contracts": page, "next_page_token": str(nxt) if nxt < len(rows) else None}

    def _bars(self, q):
        out = {}
        start, end = q["start"][:10], q["end"][:10]
        for s in q["symbols"].split(","):
            rows = [b for b in self.bars.get(s, []) if start <= b["t"][:10] <= end]
            if rows:
                out[s] = rows
        return {"bars": out, "next_page_token": None}


def probe(fake, **kw):
    return P.Probe(KEY, SECRET, session=fake, limiter=RateLimiter(100000, 100000), sleep=lambda s: None, **kw)


def bar(day):
    return {"t": f"{day}T04:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 5, "n": 2, "vw": 1}


# ======================================================================================== pure helpers
def test_occ_symbols_parse_and_garbage_does_not():
    assert P.occ_parse("QQQ240119P00400000") == {"root": "QQQ", "expiration": date(2024, 1, 19), "type": "put", "strike": 400.0}
    assert P.occ_parse("SPY251231C00612500")["strike"] == 612.5 and P.occ_parse("SPY251231C00612500")["type"] == "call"
    for bad in ("", "QQQ", "QQQ240119X00400000", "QQQ241319P00400000", None, "qqq240119P00400000"):
        assert P.occ_parse(bad) is None


def test_twenty_evenly_spaced_dates_include_the_first_and_last_and_have_no_duplicates():
    days = [date(2024, 1, 2) + timedelta(days=i) for i in range(502)]
    d = P.evenly_spaced(days, 20)
    assert len(d) == 20 and d[0] == days[0] and d[-1] == days[-1] and d == sorted(set(d))
    gaps = [(b - a).days for a, b in zip(d, d[1:])]
    assert max(gaps) - min(gaps) <= 1 and 26 <= min(gaps) <= 27
    assert P.evenly_spaced(days[:5], 20) == days[:5] and P.evenly_spaced([], 20) == []


def test_third_friday_and_the_most_liquid_selection():
    assert P.third_friday(2023, 9) == date(2023, 9, 15) and P.third_friday(2024, 3) == date(2024, 3, 15) and P.third_friday(2025, 8) == date(2025, 8, 15)
    assert P.third_friday(2016, 1) == date(2016, 1, 15) and all(P.third_friday(y, m).weekday() == 4 for y in (2016, 2024) for m in range(1, 13))
    cs = [{"symbol": "A", "open_interest": "5"}, {"symbol": "B", "open_interest": "50"}, {"symbol": "C", "open_interest": None}, {"symbol": "D", "open_interest": "x"},
          {"symbol": "E", "open_interest": "50"}, {"open_interest": "99"}]
    assert P.most_liquid(cs, 3) == ["B", "E", "A"] and P.most_liquid(cs) == ["B", "E", "A", "C", "D"]


def test_the_expiry_window_the_strike_bounds_and_the_distance_buckets():
    assert P.expiry_window(date(2024, 1, 2)) == (date(2024, 1, 27), date(2024, 2, 6))
    assert P.strike_bounds(500.0) == (425.0, 500.0) and P.strike_bounds(412.34) == (350.49, 412.34)
    assert P.bucket_of(100, 99.99) == P.BUCKETS[0] and P.bucket_of(100, 95.0) == P.BUCKETS[1] and P.bucket_of(100, 90.0) == P.BUCKETS[2]
    assert P.bucket_of(100, 90.01) == P.BUCKETS[1] and P.bucket_of(100, 85.0) == P.BUCKETS[2] and P.bucket_of(100, 100.0) == P.BUCKETS[0]
    assert P.day_bounds_utc(date(2024, 3, 5)) == ("2024-03-05T00:00:00Z", "2024-03-05T23:59:59Z")
    assert list(P.chunks(list(range(5)), 2)) == [[0, 1], [2, 3], [4]]


def test_coverage_table_counts_overall_and_by_bucket_with_shares():
    rows = [{"spot": 100, "strike": 99, "has_bar": True}, {"spot": 100, "strike": 98, "has_bar": False, "has_quote": True},
            {"spot": 100, "strike": 92, "has_bar": True}, {"spot": 100, "strike": 86, "has_bar": False}]
    t = P.coverage_table(rows)
    assert t["all"]["contracts"] == 4 and t["all"]["with_bar"] == 2 and t["all"]["bar_share"] == 0.5 and t["all"]["with_quote"] == 1
    assert t[P.BUCKETS[0]]["contracts"] == 2 and t[P.BUCKETS[0]]["bar_share"] == 0.5 and t[P.BUCKETS[1]]["with_bar"] == 1
    assert t[P.BUCKETS[2]]["contracts"] == 1 and t[P.BUCKETS[2]]["bar_share"] == 0.0
    assert P.coverage_table([])["all"]["bar_share"] is None


# ======================================================================================== the client
def test_the_client_only_ever_issues_gets_and_never_puts_a_key_in_a_log_or_a_repr():
    fake = FakeAlpaca(contracts=[contract("QQQ", date(2024, 2, 2), 400)])
    p = probe(fake)
    st, body = p.get(P.TRADING_URL, P.CONTRACTS_PATH, {"underlying_symbols": "QQQ", "limit": 10})
    assert st == 200 and body["option_contracts"] and not hasattr(fake, "post")
    blob = json.dumps(p.log) + repr(p) + str(p.log)
    assert KEY not in blob and SECRET not in blob and "APCA" not in blob
    url, params, headers = fake.calls[0]
    assert url == P.TRADING_URL + P.CONTRACTS_PATH and headers["APCA-API-KEY-ID"] == KEY and KEY not in json.dumps(params)


def test_long_symbol_lists_are_summarised_in_the_log():
    p = probe(FakeAlpaca())
    p.get(P.DATA_URL, P.BARS_PATH, {"symbols": ",".join(f"QQQ240119P{i:08d}" for i in range(100)), "timeframe": "1Day",
                                    "start": "2024-01-02T00:00:00Z", "end": "2024-01-02T23:59:59Z"})
    assert p.log[0]["params"]["symbols"].startswith("<100 OCC symbols, first QQQ240119P00000000") and len(json.dumps(p.log)) < 400


def test_429_and_5xx_are_retried_but_a_403_is_recorded_and_never_retried_or_worked_around():
    fake = FakeAlpaca(script=[429, 503])
    p = probe(fake)
    st, _b = p.get(P.DATA_URL, P.BARS_PATH, {"symbols": "X", "timeframe": "1Day", "start": "2024-01-02T00:00:00Z", "end": "2024-01-02T23:59:59Z"})
    assert st == 200 and len(fake.calls) == 3
    fake2 = FakeAlpaca(script=[403])
    p2 = probe(fake2)
    st2, _ = p2.get(P.DATA_URL, P.BARS_PATH, {"symbols": "X"})
    assert st2 == 403 and len(fake2.calls) == 1 and p2.log[0]["note"] == "scripted 403"
    always = FakeAlpaca(script=[503] * 10)
    assert probe(always, max_retries=2).get(P.DATA_URL, P.BARS_PATH, {"symbols": "X"})[0] == 503 and len(always.calls) == 3


# ===================================================================================== contracts
def test_contract_listing_follows_page_tokens_and_honours_max_pages():
    cons = [contract("QQQ", date(2024, 2, 2), 400 + i) for i in range(8)]
    fake = FakeAlpaca(contracts=cons, page=3)
    st, rows = P.list_contracts(probe(fake), "QQQ", status="inactive")
    assert st == 200 and len(rows) == 8 and len(fake.calls) == 3 and fake.calls[0][1]["limit"] == 10000
    fake2 = FakeAlpaca(contracts=cons, page=3)
    _st, one = P.list_contracts(probe(fake2), "QQQ", max_pages=1, status="inactive", limit=3)
    assert len(one) == 3 and len(fake2.calls) == 1 and fake2.calls[0][1]["limit"] == 3
    assert P.list_contracts(probe(FakeAlpaca(script=[403])), "QQQ") == (403, [])


def test_the_earliest_expiration_is_found_by_narrowing_year_month_day():
    cons = [contract("QQQ", date(2021, 6, 17), 300), contract("QQQ", date(2021, 6, 24), 300), contract("QQQ", date(2023, 1, 20), 300)]
    p = probe(FakeAlpaca(contracts=cons))
    r = P.earliest_expiration(p, "QQQ", "inactive", 2016, 2025)
    assert r["earliest"] == "2021-06-17" and r["first_status"] == 200 and r["queries"] < 80
    assert all(e["params"]["limit"] == 1 for e in p.log)                                              # existence queries only
    none = P.earliest_expiration(probe(FakeAlpaca(contracts=[])), "QQQ", "inactive", 2020, 2022)
    assert none["earliest"] is None and none["first_status"] == 200
    denied = P.earliest_expiration(probe(FakeAlpaca(script=[403])), "QQQ", "inactive", 2020, 2022)
    assert denied == {"earliest": None, "first_status": 403, "queries": 1}


# ========================================================================================== bars
def test_bars_are_requested_100_symbols_at_a_time_for_the_one_day_and_counted_per_symbol():
    d = date(2024, 3, 5)
    syms = [occ("QQQ", date(2024, 4, 5), "P", 300 + i) for i in range(250)]
    bars = {s: [bar(d.isoformat())] for s in syms[::2]}
    bars[syms[1]] = [bar((d - timedelta(days=1)).isoformat())]                                        # a bar on another day does not count
    fake = FakeAlpaca(bars=bars)
    got, statuses = P.bars_for_day(probe(fake), syms, d)
    assert len(fake.calls) == 3 and statuses == [200, 200, 200]
    assert set(got) == set(syms[::2]) and all(v == 1 for v in got.values()) and syms[1] not in got
    q = fake.calls[0][1]
    assert q["timeframe"] == "1Day" and q["start"] == "2024-03-05T00:00:00Z" and q["end"] == "2024-03-05T23:59:59Z" and len(q["symbols"].split(",")) == 100


def test_the_earliest_bar_date_comes_from_the_ascending_response():
    syms = [occ("QQQ", date(2024, 4, 5), "P", 300)]
    fake = FakeAlpaca(bars={syms[0]: [bar("2024-02-10"), bar("2024-02-12")]})
    assert P.first_bar_date(probe(fake), syms, date(2023, 1, 1), date(2024, 4, 5)) == (date(2024, 2, 10), 200)
    assert fake.calls[0][1]["sort"] == "asc"
    assert P.first_bar_date(probe(FakeAlpaca()), syms, date(2023, 1, 1), date(2023, 2, 1)) == (None, 200)
    assert P.first_bar_date(probe(FakeAlpaca(script=[403])), syms, date(2023, 1, 1), date(2024, 1, 1)) == (None, 403)


# ================================================================================ the whole probe
D1, D2 = date(2024, 3, 4), date(2025, 12, 22)


def _world():
    cons, bars = [], {}
    for root, spot in (("QQQ", 440.0), ("SPY", 512.0)):
        for exp in (date(2024, 4, 5), date(2024, 4, 8), date(2024, 4, 12)):                         # 32-39 days after D1; only some inside the 25-35 window
            for k in range(0, 80, 4):
                strike = float(int(spot) - k)
                cons.append(contract(root, exp, strike))
        cons.append(contract(root, date(2023, 9, 15), 350.0))                                       # an old expired contract (the third Friday): earliest expiration
        cons.append(contract(root, date(2024, 4, 5), spot + 20))                                    # above the spot: outside the strike filter
        cons.append(contract(root, date(2024, 4, 5), spot - 100))                                   # more than 15% below: outside
    for c in cons:
        if c["expiration_date"] == "2024-04-05" and float(c["strike_price"]) > 400 and float(c["strike_price"]) % 8 == 0:
            bars[c["symbol"]] = [bar(D1.isoformat())]
    old = [c["symbol"] for c in cons if c["expiration_date"] == "2023-09-15"]
    for s in old:
        bars[s] = [bar("2023-08-10"), bar("2023-09-01")]
    return cons, bars


def test_run_probe_end_to_end_on_constructed_data_reports_coverage_and_the_missing_quotes_endpoint():
    cons, bars = _world()
    fake = FakeAlpaca(contracts=cons, bars=bars, quotes=(404, {"message": "route not found"}),
                      trades={c["symbol"]: [{"t": f"{D1}T15:00:00Z", "p": 1.0, "s": 1}] for c in cons}, feed_status={"opra": 403})
    p = probe(fake)
    spots = {"QQQ": {D1: 440.0, D2: 520.0}, "SPY": {D1: 512.0, D2: 690.0}}
    res = P.run_probe(p, spots, [D1, D2])
    q = res["contracts"]["QQQ"]
    assert q["inactive"]["earliest"] == "2023-09-15" and q["active"]["earliest"] is None
    assert q["expired_puts_2024_2025"]["listed"] > 0 and set(q["expired_puts_2024_2025"]["by_year"]) == {2024}
    cov = res["coverage"]["QQQ"]
    d1 = next(r for r in cov["per_date"] if r["day"] == str(D1))
    assert d1["expiries"] == 2                                                                         # 04-05 (32 days) and 04-08 (35, inclusive); 04-12 is 39 days out
    assert d1["contracts"] == 17 * 2 + 0                                                              # 17 strikes in [374, 440] on each of the two expiries
    assert d1["contracts"] > 0 and 0 < d1["with_bar"] < d1["contracts"]
    assert next(r for r in cov["per_date"] if r["day"] == str(D2)).get("skipped") is True            # the window runs past 2025-12-31: skipped, not queried
    assert cov["table"]["all"]["contracts"] == d1["contracts"] and cov["table"]["all"]["bar_share"] == pytest.approx(d1["with_bar"] / d1["contracts"])
    assert set(P.BUCKETS) <= set(cov["table"])
    assert res["quotes"] and all(v["status"] == 404 and v["rows"] == 0 for v in res["quotes"].values())
    assert res["trades"] and all(v["status"] == 200 and v["rows"] == 1 for v in res["trades"].values())
    assert res["depth"]["QQQ"] == {"first_bar_seen": "2023-08-10", "from_contracts_expiring": "2023-09-15", "status": 200}
    assert res["feeds"]["(none)"]["status"] == 200 and res["feeds"]["opra"]["status"] == 403 and res["feeds"]["opra"]["message"].startswith("feed opra")
    assert [e["status"] for e in res["access_errors"]] and res["non_200"].get(403, 0) >= 1 and res["requests"] == len(p.log)
    text = P.render_findings(res, p, [D1, D2])
    assert "Expired puts listed" in text and "2023-09-15" in text and "`/options/quotes` status" in text and KEY not in text and SECRET not in text
    assert "feed=opra" in text and "GET https://paper-api.alpaca.markets/v2/options/contracts" in text and "401/403 answers: 403 on `/v1beta1/options/bars`" in text


def test_nothing_the_probe_requests_is_a_post_or_touches_2026_or_a_non_options_endpoint():
    cons, bars = _world()
    fake = FakeAlpaca(contracts=cons, bars=bars)
    p = probe(fake)
    P.run_probe(p, {"QQQ": {D1: 440.0}, "SPY": {D1: 512.0}}, [D1])
    assert fake.calls
    for url, params, _h in fake.calls:
        path = "/" + url.split("//", 1)[1].split("/", 1)[1]
        assert path in (P.CONTRACTS_PATH, P.BARS_PATH, P.TRADES_PATH, P.QUOTES_PATH) and url.split("/v")[0] in (P.TRADING_URL, P.DATA_URL)
        assert "2026" not in json.dumps(params)
        assert not re.search(r"order|account|position|activities", path)


def test_the_probe_refuses_a_2026_date_outright():
    with pytest.raises(AssertionError, match="never queries 2026"):
        P.run_probe(probe(FakeAlpaca()), {"QQQ": {}, "SPY": {}}, [date(2026, 1, 2)])
    assert P.PROBE_END < date(2026, 1, 1)


def test_a_403_on_every_endpoint_is_reported_not_worked_around():
    fake = FakeAlpaca(script=[403] * 400)
    p = probe(fake)
    res = P.run_probe(p, {"QQQ": {D1: 440.0}, "SPY": {D1: 512.0}}, [D1])
    assert res["contracts"]["QQQ"]["inactive"]["earliest"] is None and res["contracts"]["QQQ"]["inactive"]["first_status"] == 403
    assert res["coverage"]["QQQ"]["table"]["all"]["contracts"] == 0 and len(res["access_errors"]) == res["requests"]
    assert "403" in P.render_findings(res, p, [D1])
