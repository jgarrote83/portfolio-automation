"""Core-vs-SPY analysis (analysis/core_performance): statistics, ledger reconstruction, the exactly-additive daily decomposition, the
GET-only Alpaca client and an end-to-end build on a tiny synthetic account whose numbers are checked by hand. No network, no Azure.

The synthetic account (days 05-22 base, 05-26, 05-27, 05-28, 05-29): $100,000 journalled earlier; on 05-26 it buys 100 SPY at 100.50 and
100 SGOV at 100.00; on 05-28 it sells 50 SPY at 102.00 and pays a $0.01 fee. SPY closes 100 / 101 / 100 / 102 / 103, SGOV 100 / 100 / 100 /
100.10 / 100.10. Equity by session: 100,000 / 100,050 / 99,950 / 100,159.99 / 100,209.99.
"""
import json
import math
import os
import sys
from datetime import datetime, timezone
from statistics import mean, stdev

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `analysis`
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from analysis.core_performance import alpaca_ro, attribution, fetch, ledger, metrics, report, run  # noqa: E402


# ============================================================================================ metrics
def test_returns_total_return_and_compounding():
    v = [100.0, 110.0, 99.0]
    assert metrics.returns(v) == pytest.approx([0.10, -0.10])
    assert metrics.total_return(v) == pytest.approx(-0.01)
    assert metrics.compound(metrics.returns(v)) == pytest.approx(metrics.total_return(v))


def test_max_drawdown_is_the_largest_peak_to_trough_fall_with_its_indices():
    m = metrics.max_drawdown([100, 120, 100, 130, 104, 135])
    assert m["max_drawdown"] == pytest.approx(-0.20) and (m["peak_index"], m["trough_index"]) == (3, 4)
    m2 = metrics.max_drawdown([100, 90, 120, 60, 130])            # the later, deeper fall wins
    assert m2["max_drawdown"] == pytest.approx(-0.5) and (m2["peak_index"], m2["trough_index"]) == (2, 3)
    assert metrics.max_drawdown([1, 2, 3])["max_drawdown"] == 0.0


def test_volatility_and_sharpe_use_the_sample_std_and_root_252():
    r = [0.01, -0.005, 0.02, 0.0, 0.004]
    assert metrics.annualized_vol(r) == pytest.approx(stdev(r) * math.sqrt(252))
    assert metrics.sharpe(r) == pytest.approx(mean(r) / stdev(r) * math.sqrt(252))
    assert metrics.sharpe(r, rf_daily=0.001) == pytest.approx((mean(r) - 0.001) / stdev(r) * math.sqrt(252))
    assert metrics.sharpe([0.01, 0.01, 0.01]) is None and metrics.sharpe([0.01]) is None
    assert metrics.annualized_vol([0.01]) is None


def test_sharpe_band_is_wider_with_fewer_observations_and_centred_on_the_estimate():
    lo96, hi96 = metrics.sharpe_ci95(1.0, 96)
    lo500, hi500 = metrics.sharpe_ci95(1.0, 500)
    assert (lo96 + hi96) / 2 == pytest.approx(1.0) and hi96 - lo96 > hi500 - lo500
    sr_d = 1.0 / math.sqrt(252)
    assert hi96 - 1.0 == pytest.approx(1.96 * math.sqrt((1 + 0.5 * sr_d ** 2) / 96) * math.sqrt(252))


def test_regression_recovers_an_exact_linear_relationship():
    bench = [0.01, -0.02, 0.015, 0.0, 0.03, -0.01]
    book = [0.5 * x + 0.001 for x in bench]
    reg = metrics.regression(book, bench)
    assert reg["beta"] == pytest.approx(0.5) and reg["alpha_annual"] == pytest.approx(0.001 * 252) and reg["corr"] == pytest.approx(1.0)
    assert reg["r2"] == pytest.approx(1.0) and reg["n"] == 6
    neg = metrics.regression([-x for x in bench], bench)
    assert neg["beta"] == pytest.approx(-1.0) and neg["corr"] == pytest.approx(-1.0)
    assert metrics.regression([0.1, 0.1, 0.1], bench[:3])["beta"] is None
    with pytest.raises(ValueError):
        metrics.regression([0.1, 0.2], [0.1, 0.2])


def test_value_index_buy_and_hold_and_rebalancing():
    prices = {"A": {"d1": 100.0, "d2": 110.0, "d3": 110.0}, "B": {"d1": 100.0, "d2": 100.0, "d3": 120.0}}
    dates = ["d1", "d2", "d3"]
    hold = metrics.value_index(prices, {"A": 0.5, "B": 0.5}, dates)
    assert hold == pytest.approx([1.0, 1.05, 1.15])
    reb = metrics.value_index(prices, {"A": 0.5, "B": 0.5}, dates, {"d2"})        # reset to 50/50 at the d2 close
    assert reb[1] == pytest.approx(1.05) and reb[2] == pytest.approx(1.05 * (0.5 * 110 / 110 + 0.5 * 120 / 100))
    with pytest.raises(ValueError):
        metrics.value_index(prices, {"A": 0.5, "B": 0.6}, dates)


def test_month_end_dates_excludes_a_month_still_in_progress():
    dates = ["2026-05-28", "2026-05-29", "2026-06-01", "2026-06-30", "2026-07-01", "2026-07-02"]
    assert metrics.month_end_dates(dates) == {"2026-05-29", "2026-06-30"}


def test_volatility_scaled_benchmark_hits_the_target_volatility():
    spy = [0.01, -0.02, 0.015, 0.005, -0.01, 0.02]
    cash = [0.0001] * 6
    target = metrics.annualized_vol(spy) / 2
    out = metrics.volatility_scaled(spy, cash, target)
    assert out["k"] == pytest.approx(0.5)
    assert out["returns"] == pytest.approx([0.5 * r + 0.5 * 0.0001 for r in spy])
    assert metrics.annualized_vol(out["returns"]) == pytest.approx(target)
    with pytest.raises(ValueError):
        metrics.volatility_scaled([0.0, 0.0, 0.0], cash[:3], 0.1)


# ============================================================================================ ledger
def test_et_date_converts_utc_stamps_to_the_new_york_session_date():
    assert ledger.et_date("2026-05-23T00:00:00Z") == "2026-05-22"           # midnight UTC is still the evening before in New York
    assert ledger.et_date("2026-05-26T13:35:00Z") == "2026-05-26"
    assert ledger.et_date("2026-01-15T04:59:59Z") == "2026-01-14" and ledger.et_date("2026-01-15T05:00:00Z") == "2026-01-15"   # EST boundary
    assert ledger.et_date("2026-07-15T03:59:59.5Z") == "2026-07-14" and ledger.et_date("2026-07-15T04:00:00Z") == "2026-07-15"   # EDT boundary


ACTS = [
    {"activity_type": "JNLC", "date": "2026-05-12", "net_amount": "100000"},
    {"activity_type": "FILL", "id": "1", "symbol": "SPY", "side": "buy", "qty": "100", "price": "100.50", "transaction_time": "2026-05-26T14:00:00Z"},
    {"activity_type": "FILL", "id": "2", "symbol": "SGOV", "side": "buy", "qty": "100", "price": "100.00", "transaction_time": "2026-05-26T14:01:00Z"},
    {"activity_type": "FILL", "id": "3", "symbol": "SPY", "side": "sell", "qty": "50", "price": "102.00", "transaction_time": "2026-05-28T13:35:00Z"},
    {"activity_type": "FEE", "id": "4", "date": "2026-05-28", "net_amount": "-0.01", "description": "TAF"},
]
DAYS = ["2026-05-22", "2026-05-26", "2026-05-27", "2026-05-28", "2026-05-29"]


def test_daily_cash_applies_every_flow_fee_and_fill_dated_on_or_before_the_day():
    parts = ledger.split_activities(ACTS)
    cash = ledger.daily_cash(parts, DAYS)
    assert cash["2026-05-22"] == pytest.approx(100000.0)
    assert cash["2026-05-26"] == pytest.approx(100000 - 10050 - 10000)
    assert cash["2026-05-27"] == pytest.approx(79950.0)
    assert cash["2026-05-28"] == pytest.approx(79950 + 5100 - 0.01) and cash["2026-05-29"] == pytest.approx(85049.99)
    assert [f["symbol"] for f in parts["fills"]] == ["SPY", "SGOV", "SPY"] and len(parts["fees"]) == 1 and len(parts["flows"]) == 1


def test_a_fill_just_after_midnight_utc_counts_on_the_previous_new_york_day():
    acts = [{"activity_type": "FILL", "id": "9", "symbol": "SPY", "side": "buy", "qty": "1", "price": "100", "transaction_time": "2026-05-27T01:00:00Z"}]
    cash = ledger.daily_cash(ledger.split_activities(acts), ["2026-05-26", "2026-05-27"])
    assert cash["2026-05-26"] == pytest.approx(-100.0) and cash["2026-05-27"] == pytest.approx(-100.0)


def test_daily_shares_and_net_quantities():
    fills = ledger.split_activities(ACTS)["fills"]
    sh = ledger.daily_shares(fills, "SPY", DAYS)
    assert [sh[d] for d in DAYS] == [0.0, 100.0, 100.0, 50.0, 50.0]
    assert ledger.net_quantities(fills) == {"SPY": 50.0, "SGOV": 100.0}


def _fills(rows):
    return [{"symbol": s, "side": side, "qty": q, "price": p, "day": d, "transaction_time": f"{d}T{i:02d}:00:00Z"} for i, (s, side, q, p, d) in enumerate(rows)]


def test_fifo_matches_the_oldest_lots_first_and_values_the_remainder_at_the_mark():
    fills = _fills([("X", "buy", 10, 10.0, "2026-05-26"), ("X", "buy", 10, 20.0, "2026-05-27"), ("X", "sell", 15, 30.0, "2026-05-28")])
    out = ledger.fifo_pnl(fills, {"X": 25.0})["X"]
    assert out["realized"] == pytest.approx(10 * (30 - 10) + 5 * (30 - 20))      # 200 + 50
    assert out["qty"] == pytest.approx(5.0) and out["cost_remaining"] == pytest.approx(100.0)
    assert out["unrealized"] == pytest.approx(5 * 25 - 100) and out["total"] == pytest.approx(250 + 25)
    assert out["bought"] == pytest.approx(300.0) and out["sold"] == pytest.approx(450.0) and out["unmatched_sell_qty"] == 0.0


def test_fifo_records_a_sell_with_no_lots_instead_of_inventing_one():
    out = ledger.fifo_pnl(_fills([("Y", "buy", 5, 10.0, "2026-05-26"), ("Y", "sell", 8, 12.0, "2026-05-27")]), {})["Y"]
    assert out["unmatched_sell_qty"] == pytest.approx(3.0) and out["realized"] == pytest.approx(5 * 2.0) and out["qty"] == pytest.approx(0.0)


def test_fifo_total_equals_cash_flows_plus_market_value_less_nothing_else():
    fills = _fills([("A", "buy", 10, 10.0, "2026-05-26"), ("B", "buy", 4, 50.0, "2026-05-26"), ("A", "sell", 4, 12.0, "2026-05-27"), ("B", "sell", 4, 45.0, "2026-05-28")])
    out = ledger.fifo_pnl(fills, {"A": 11.0})
    cash = sum(-f["qty"] * f["price"] if f["side"] == "buy" else f["qty"] * f["price"] for f in fills)
    assert sum(v["total"] for v in out.values()) == pytest.approx(cash + 6 * 11.0)


def test_monthly_turnover_is_one_way_gross_notional_over_the_average_equity():
    fills = _fills([("A", "buy", 10, 100.0, "2026-05-26"), ("A", "sell", 5, 100.0, "2026-05-28"), ("A", "buy", 1, 100.0, "2026-06-01")])
    rows = ledger.monthly_turnover(fills, {"2026-05-26": 10000.0, "2026-05-28": 10000.0, "2026-06-01": 20000.0})
    may, jun = rows
    assert (may["bought"], may["sold"], may["fills"]) == (1000.0, 500.0, 2) and may["turnover_one_way"] == pytest.approx(1500 / 2 / 10000)
    assert jun["turnover_one_way"] == pytest.approx(100 / 2 / 20000)


# ======================================================================================== attribution
def test_daily_terms_add_to_the_daily_excess_exactly_including_a_cash_only_day_and_a_levered_day():
    cases = [(100000.0, 100000.0, 0.0), (100000.0, 30000.0, 15000.0), (100000.0, -5000.0, 40000.0), (90000.0, 0.0, 0.0)]
    for E, C, S in cases:
        t = attribution.daily_terms(E, C, S, r_book=0.004, r_spy=0.01, r_sgov=0.0002)
        assert t["cash"] + t["invested"] == pytest.approx(t["excess"], abs=1e-15)
        assert t["cash"] == pytest.approx((C * (0 - 0.01) + S * (0.0002 - 0.01)) / E)
        assert t["cash_literal"] + t["cash_sgov"] == pytest.approx(t["cash"])
    allcash = attribution.daily_terms(100000.0, 100000.0, 0.0, r_book=0.0, r_spy=0.01, r_sgov=0.0)
    assert allcash["cash"] == pytest.approx(-0.01) and allcash["invested"] == pytest.approx(0.0)     # flat book, all cash: the whole miss is the cash term
    lev = attribution.daily_terms(100000.0, -10000.0, 0.0, r_book=0.011, r_spy=0.01, r_sgov=0.0)
    assert lev["cash_literal"] == pytest.approx(0.001) and lev["cash"] > 0                         # negative cash helps when SPY rises


def test_policy_terms_add_with_the_cash_term_to_the_excess():
    t = attribution.daily_terms(100000.0, 20000.0, 10000.0, r_book=0.003, r_spy=0.01, r_sgov=0.0001, r_policy=0.006)
    assert t["allocation"] == pytest.approx((1 - 0.3) * (0.006 - 0.01)) and t["tactical"] == pytest.approx(t["invested"] - t["allocation"])
    assert t["cash"] + t["allocation"] + t["tactical"] == pytest.approx(t["excess"])


def test_decompose_sums_the_terms_and_reports_the_compounding_residual():
    days = [attribution.daily_terms(100000.0, 20000.0, 0.0, r_book=0.002, r_spy=0.01, r_sgov=0.0) for _ in range(3)]
    d = attribution.decompose(days, book_total_return=0.006, spy_total_return=0.0303)
    assert d["excess"] == pytest.approx(3 * (0.002 - 0.01) * 100) and d["cash"] + d["invested"] == pytest.approx(d["excess"])
    assert d["compounded_excess"] == pytest.approx((0.006 - 0.0303) * 100)
    assert d["compounding_residual"] == pytest.approx(d["compounded_excess"] - d["excess"])
    assert d["allocation"] is None and d["tactical"] is None and d["days"] == 3
    withp = [attribution.daily_terms(100000.0, 20000.0, 0.0, 0.002, 0.01, 0.0, r_policy=0.004) for _ in range(3)]
    dp = attribution.decompose(withp, 0.006, 0.0303)
    assert dp["cash"] + dp["allocation"] + dp["tactical"] == pytest.approx(dp["excess"])


def test_existing_method_is_average_cash_weight_times_the_return_gap():
    assert attribution.existing_method(16.06, -0.219, 1.785)["cash_contribution_pp"] == pytest.approx(0.1606 * (-0.219 - 1.785))


# ============================================================================================ Alpaca client
class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self._p, self.text = status, payload, text

    def json(self):
        return self._p


class FakeSession:
    """Records every call; only `get` exists, so any attempt to write would raise AttributeError."""

    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        return self.responses.pop(0)


def test_the_client_has_only_get_methods_and_sends_keys_as_headers_only():
    for forbidden in ("post", "put", "patch", "delete", "submit_order", "cancel_order", "replace_order"):
        assert not hasattr(alpaca_ro.ReadOnlyAlpaca, forbidden)
    sess = FakeSession([FakeResponse(payload={"equity": "1"})])
    api = alpaca_ro.ReadOnlyAlpaca("KEYVALUE", "SECRETVALUE", sess, sleep=lambda s: None)
    assert api.account() == {"equity": "1"}
    url, params, headers = sess.calls[0]
    assert url == "https://paper-api.alpaca.markets/v2/account" and headers["APCA-API-KEY-ID"] == "KEYVALUE" and "KEYVALUE" not in url and "KEYVALUE" not in str(params)
    assert api.requests_made == ["/v2/account"] and "KEYVALUE" not in str(api.requests_made)


def test_the_client_retries_rate_limits_and_errors_clearly_without_echoing_keys():
    sess = FakeSession([FakeResponse(429), FakeResponse(503), FakeResponse(payload=[1])])
    sleeps = []
    api = alpaca_ro.ReadOnlyAlpaca("K", "S", sess, sleep=sleeps.append)
    assert api.positions() == [1] and sleeps == [2.0, 4.0] and len(sess.calls) == 3
    with pytest.raises(RuntimeError, match="HTTP 403"):
        alpaca_ro.ReadOnlyAlpaca("K", "S", FakeSession([FakeResponse(403, text="nope")]), sleep=lambda s: None).account()
    with pytest.raises(RuntimeError, match="failed after 2 attempts"):
        alpaca_ro.ReadOnlyAlpaca("K", "S", FakeSession([FakeResponse(500), FakeResponse(500)]), sleep=lambda s: None, attempts=2).account()


def test_activities_page_with_the_last_id_until_a_short_page():
    p1 = [{"id": f"a{i}"} for i in range(3)]
    p2 = [{"id": "a3"}]
    sess = FakeSession([FakeResponse(payload=p1), FakeResponse(payload=p2)])
    api = alpaca_ro.ReadOnlyAlpaca("K", "S", sess, sleep=lambda s: None)
    out = api.activities(page_size=3)
    assert [a["id"] for a in out] == ["a0", "a1", "a2", "a3"]
    assert "page_token" not in sess.calls[0][1] and sess.calls[1][1]["page_token"] == "a2" and sess.calls[0][1]["direction"] == "asc"


def test_daily_bars_page_on_the_token_and_collect_per_symbol():
    sess = FakeSession([FakeResponse(payload={"bars": {"SPY": [{"t": "2026-05-20T04:00:00Z"}]}, "next_page_token": "tok"}),
                        FakeResponse(payload={"bars": {"SPY": [{"t": "2026-05-21T04:00:00Z"}], "AGG": [{"t": "2026-05-21T04:00:00Z"}]}, "next_page_token": None})])
    out = alpaca_ro.ReadOnlyAlpaca("K", "S", sess, sleep=lambda s: None).daily_bars(["SPY", "AGG"], "2026-05-20", "2026-05-21", adjustment="raw")
    assert len(out["SPY"]) == 2 and len(out["AGG"]) == 1
    assert sess.calls[0][1]["adjustment"] == "raw" and sess.calls[0][1]["timeframe"] == "1Day" and sess.calls[1][1]["page_token"] == "tok"


def test_keys_come_from_the_environment_then_the_env_file_and_a_missing_key_names_variables_not_values(tmp_path):
    assert alpaca_ro.load_keys({"ALPACA_API_KEY": "k1", "ALPACA_API_SECRET": "s1"}, tmp_path / "none") == ("k1", "s1")
    f = tmp_path / ".env"
    f.write_text('# comment\nALPACA_API_KEY="fk"\nALPACA_API_SECRET=fs\nOTHER=x\n', encoding="utf-8")
    assert alpaca_ro.load_keys({}, f) == ("fk", "fs")
    assert alpaca_ro.load_keys({"ALPACA_API_KEY": "envk"}, f) == ("envk", "fs")
    f.write_text("ALPACA_API_KEY=onlykey\n", encoding="utf-8")
    with pytest.raises(alpaca_ro.MissingKeys) as e:
        alpaca_ro.load_keys({}, f)
    assert "ALPACA_API_SECRET" in str(e.value) and "onlykey" not in str(e.value)


def test_last_weekday_before_skips_the_weekend():
    from datetime import date
    assert fetch.last_weekday_before(date(2026, 10, 12)) == date(2026, 10, 9)      # Monday -> Friday
    assert fetch.last_weekday_before(date(2026, 10, 11)) == date(2026, 10, 9)      # Sunday -> Friday
    assert fetch.last_weekday_before(date(2026, 10, 13)) == date(2026, 10, 12)     # Tuesday -> Monday


def test_fetch_writes_files_drops_the_account_number_and_never_the_keys(tmp_path, monkeypatch):
    class Api:
        requests_made = ["/v2/account"]
        def account(self): return {"account_number": "PA123", "id": "uuid", "equity": "100"}
        def portfolio_history(self, **k): return {"timestamp": [1], "equity": [100.0]}
        def activities(self): return [{"id": "a"}]
        def positions(self): return []
        def daily_bars(self, symbols, start, end, adjustment="all", feed="sip"): return {s: [{"t": f"{start}T04:00:00Z", "c": 1.0}] for s in symbols}
    monkeypatch.setattr(fetch, "ReadOnlyAlpaca", lambda k, s: Api())
    monkeypatch.setattr(fetch, "load_keys", lambda: ("SECRETKEY", "SECRETSECRET"))
    monkeypatch.setattr(fetch, "DATA_DIR", tmp_path)
    from datetime import date
    meta = fetch.run("2026-05-20", today=date(2026, 10, 12))
    assert meta["bars_end"] == "2026-10-09"
    acct = json.loads((tmp_path / "account.json").read_text(encoding="utf-8"))
    assert "account_number" not in acct and "id" not in acct and acct["equity"] == "100"
    blob = "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())
    assert "SECRETKEY" not in blob and "SECRETSECRET" not in blob and "PA123" not in blob


# ================================================================================== end-to-end on a tiny account
def _ts(day):          # Alpaca stamps a session's portfolio-history point at midnight UTC of the NEXT day
    from datetime import date, timedelta
    d = date.fromisoformat(day) + timedelta(days=1)
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def _bar(day, c):
    return {"t": f"{day}T04:00:00Z", "c": c}


def synthetic_raw():
    eq = [100000.0, 100050.0, 99950.0, 100159.99, 100209.99]
    spy = [100.0, 101.0, 100.0, 102.0, 103.0]
    sgov = [100.0, 100.0, 100.0, 100.10, 100.10]
    flat = [100.0, 100.0, 100.5, 100.5, 101.0]
    bars_all = {"SPY": [_bar(d, c * 1.001) for d, c in zip(DAYS, spy)], "SGOV": [_bar(d, c * 1.0005) for d, c in zip(DAYS, sgov)]}
    for s in ("AGG", "VTI", "TLT", "IEF", "GLD", "DBC"):
        bars_all[s] = [_bar(d, c) for d, c in zip(DAYS, flat)]
    bars_raw = {"SPY": [_bar(d, c) for d, c in zip(DAYS, spy)], "SGOV": [_bar(d, c) for d, c in zip(DAYS, sgov)]}
    return {"account": {"cash": "85049.99", "equity": "100209.99"},
            "portfolio_history": {"timestamp": [_ts("2026-05-21")] + [_ts(d) for d in DAYS], "equity": [100000.0] + eq},
            "activities": ACTS,
            "positions": [{"symbol": "SPY", "qty": "50", "current_price": "103", "market_value": "5150"},
                          {"symbol": "SGOV", "qty": "100", "current_price": "100.10", "market_value": "10010"}],
            "bars_all": bars_all, "bars_raw": bars_raw, "fetch_meta": {"fetched_utc": "2026-05-29T22:00:00+00:00", "bars_start": "2026-05-20",
                                                                      "bars_end": "2026-05-29", "paths": [], "requests": 0, "counts": {"activities": 5, "positions": 2}}}


def test_equity_by_day_maps_each_stamp_to_its_new_york_session():
    e = run.equity_by_day(synthetic_raw()["portfolio_history"])
    assert sorted(e) == ["2026-05-21"] + DAYS and e["2026-05-22"] == 100000.0 and e["2026-05-29"] == 100209.99


def test_build_on_the_synthetic_account_reconciles_and_the_decomposition_is_exact():
    res = run.build(synthetic_raw(), inception="2026-05-26", validate_as_of=None)
    assert (res["base"], res["first"], res["last"], res["trading_days_in_window"]) == ("2026-05-22", "2026-05-26", "2026-05-29", 4)
    rc = res["reconciliation"]
    assert rc["cash_diff"] == pytest.approx(0.0, abs=1e-6) and rc["qty_mismatches"] == {}
    w = {x["day"]: x for x in res["weights"]}
    assert w["2026-05-22"]["cash_pct"] == pytest.approx(100.0)
    assert w["2026-05-26"]["cash"] == pytest.approx(79950.0) and w["2026-05-26"]["sgov"] == pytest.approx(10000.0)
    assert w["2026-05-29"]["cash_pct"] == pytest.approx(100 * 85049.99 / 100209.99) and w["2026-05-29"]["holdings_pct"] == pytest.approx(100 - w["2026-05-29"]["cash_pct"])
    assert w["2026-05-29"]["risk_assets_pct"] == pytest.approx(100 * 5150 / 100209.99, rel=1e-6)
    # the first day starts from 100% cash: the cash term is minus the whole SPY move on that day, weighted by 1
    t0 = res["terms"][0]
    assert t0["cash"] == pytest.approx(-(101.0 / 100.0 - 1)) and t0["sleeve_weight"] == pytest.approx(1.0)
    for t in res["terms"]:
        assert t["cash"] + t["invested"] == pytest.approx(t["excess"], abs=1e-12)
    d = res["decomp_latest"]
    assert d["excess"] == pytest.approx(sum(t["excess"] for t in res["terms"]) * 100) and d["allocation"] is None
    book_tr, spy_tr = 100209.99 / 100000 - 1, 103.0 / 100.0 - 1
    assert d["compounded_excess"] == pytest.approx((book_tr - spy_tr) * 100)
    assert res["board_latest"]["book"]["total_return"] == pytest.approx(book_tr) and res["board_latest"]["excess_price_pp"] == pytest.approx((book_tr - spy_tr) * 100)


def test_build_refuses_to_interpolate_a_missing_equity_session():
    raw = synthetic_raw()
    i = raw["portfolio_history"]["timestamp"].index(_ts("2026-05-27"))
    del raw["portfolio_history"]["timestamp"][i]
    del raw["portfolio_history"]["equity"][i]
    with pytest.raises(ValueError, match="no equity for"):
        run.build(raw, inception="2026-05-26", validate_as_of=None)


def test_build_flags_an_external_flow_inside_the_window_and_a_position_mismatch():
    raw = synthetic_raw()
    raw["activities"] = raw["activities"] + [{"activity_type": "CSD", "date": "2026-05-27", "net_amount": "500"}]
    assert [f["amount"] for f in run.build(raw, validate_as_of=None)["external_flows_in_window"]] == [500.0]
    raw2 = synthetic_raw()
    raw2["positions"][0]["qty"] = "49"
    assert "SPY" in run.build(raw2, validate_as_of=None)["reconciliation"]["qty_mismatches"]


def test_build_holdings_pnl_reconciles_to_equity_and_groups_symbols():
    res = run.build(synthetic_raw(), validate_as_of=None)
    h = res["holdings"]
    assert h["identity"]["cash_plus_mv"] == pytest.approx(100209.99) and abs(h["identity"]["diff"]) < 1e-6
    assert h["identity"]["pnl_total_after_fees"] == pytest.approx(h["identity"]["equity_minus_start"])
    spy = h["fifo"]["SPY"]
    assert spy["realized"] == pytest.approx(50 * (102 - 100.5)) and spy["unrealized"] == pytest.approx(50 * 103 - 50 * 100.5)
    assert h["fifo"]["SGOV"]["total"] == pytest.approx(100 * 0.10)
    assert res["fees"]["total"] == pytest.approx(-0.01) and res["non_core_fills"] == [] and res["non_core_activity"] == {}


def test_policy_portfolio_terms_are_used_when_the_inception_snapshot_and_bars_are_supplied():
    snap = {"reference_weights": {"target_weights_pct": {"SPY": 50.0, "AGG": 40.0, "SGOV": 8.5}}}       # 1.5% literal cash is left out
    w = run.policy_weights(snap)
    assert sum(w.values()) == pytest.approx(1.0) and w["SPY"] == pytest.approx(50 / 98.5)
    raw = synthetic_raw()
    policy_bars = {"SPY": raw["bars_raw"]["SPY"], "SGOV": raw["bars_raw"]["SGOV"], "AGG": [_bar(d, c) for d, c in zip(DAYS, [100, 100, 100.5, 100.5, 101])]}
    res = run.build(raw, inception_snapshot=snap, policy_bars=policy_bars, validate_as_of=None)
    d = res["decomp_latest"]
    assert d["allocation"] is not None and d["tactical"] is not None
    assert d["cash"] + d["allocation"] + d["tactical"] == pytest.approx(d["excess"])
    assert res["policy"]["available"] is True
    # without the bars the policy terms stay blocked, never guessed
    assert run.build(raw, inception_snapshot=snap, policy_bars=None, validate_as_of=None)["decomp_latest"]["allocation"] is None


def test_fetch_policy_bars_uses_raw_prices_and_refuses_a_missing_constituent():
    calls = []
    class Api:
        def daily_bars(self, symbols, start, end, adjustment="all", feed="sip"):
            calls.append(adjustment)
            return {"SPY": [{"t": "x", "c": 1.0}], "AGG": []}
    with pytest.raises(ValueError, match="AGG"):
        run.fetch_policy_bars(["SPY", "AGG"], "2026-05-20", "2026-10-09", api=Api())
    assert calls == ["raw"]


def test_the_report_renders_blocked_sections_without_a_policy_and_computed_ones_with_it():
    raw = synthetic_raw()
    snap = {"reference_weights": {"target_weights_pct": {"SPY": 60.0, "AGG": 40.0}}}
    blocked = report.render(run.build(raw, validate_as_of=None), raw["fetch_meta"], snap_path="x.json", inception_given=False)
    assert "**BLOCKED**" in blocked and "daily-snapshots" in blocked and "## 6. Conclusion" in blocked
    assert "No recommendation to change Core is made" in blocked
    policy_bars = {"SPY": raw["bars_raw"]["SPY"], "AGG": [_bar(d, c) for d, c in zip(DAYS, [100, 100, 100.5, 100.5, 101])]}
    done = report.render(run.build(raw, inception_snapshot=snap, policy_bars=policy_bars, validate_as_of=None), raw["fetch_meta"], snap_path="x.json", inception_given=True)
    assert "The inception-day snapshot was supplied" in done and "BLOCKED, not estimated" not in done
