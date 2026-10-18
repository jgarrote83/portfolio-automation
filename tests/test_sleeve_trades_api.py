"""Flex catalyst sleeve, per-ticker view (session 2026-10-17) —
web/api/function_app.py's `_sleeve_trade_row` / `_sleeve_trades` / `_attach_sleeve_trades`.

The page needs to show WHICH tickers the sleeve chose, the trades done in each, and the total gain % per ticker.
Gain % is pooled (total realised P&L / total cost of the shares sold), never an average of trade returns, and the block carries no
win-rate / Sharpe / skill statistic at any sample size.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "web", "api"))

import function_app  # noqa: E402


def _trade(symbol="NVDA", entry="2026-10-12", closed="2026-10-14", entry_price=100.0, fills=None, pnl=30.0, reason="time_stop", **kw):
    fills = [{"date": closed, "qty": 10, "price": 102.0}, {"date": closed, "qty": 10, "price": 101.0}] if fills is None else fills
    row = {"trade_id": f"T-{symbol}-{entry}", "symbol": symbol, "entry_date": entry, "closed_date": closed, "entry_price": entry_price,
           "fills": fills, "pnl_usd": pnl, "pnl_unavailable_reason": None, "exit_reason": reason, "r_multiple": 0.5, "holding_days": 2}
    row.update(kw)
    return row


EQUITY = [{"date": "2026-10-14", "total_equity": 100_000.0}, {"date": "2026-10-13", "total_equity": 99_000.0}]


# --- one trade --------------------------------------------------------------------------------------------------------

def test_trade_row_derives_exit_price_cost_return_and_contribution():
    row = function_app._sleeve_trade_row(_trade(symbol="nvda"), {"2026-10-14": 100_000.0})
    assert row["symbol"] == "NVDA"                       # upper-cased
    assert row["qty"] == 20
    assert row["exit_price"] == 101.5                    # quantity-weighted over BOTH fills
    assert row["cost_usd"] == 2000.0                     # entry price x shares actually sold (the engine's own basis)
    assert row["pnl_usd"] == 30.0                        # the engine's figure, passed through untouched
    assert row["return_pct"] == 1.5                      # 30 / 2000
    assert row["contribution_pp"] == 0.03                # 30 / 100000 equity on the close date
    assert row["exit_reason"] == "time_stop"


def test_trade_row_unknown_pnl_is_never_fabricated():
    t = _trade(fills=[{"date": "2026-10-14", "qty": 5, "price": None}], pnl=None, pnl_unavailable_reason="missing_fill_price", r_multiple=None)
    row = function_app._sleeve_trade_row(t, {"2026-10-14": 100_000.0})
    assert row["pnl_usd"] is None and row["return_pct"] is None and row["contribution_pp"] is None
    assert row["exit_price"] is None                     # one fill has no price -> no average exit price either
    assert row["pnl_unavailable_reason"] == "missing_fill_price"


def test_trade_row_without_equity_on_close_date_has_no_contribution_but_keeps_the_rest():
    row = function_app._sleeve_trade_row(_trade(), {})
    assert row["contribution_pp"] is None and row["return_pct"] == 1.5


def test_trade_row_rejects_rows_with_no_symbol():
    assert function_app._sleeve_trade_row({"symbol": "  ", "fills": []}, {}) is None
    assert function_app._sleeve_trade_row({}, {}) is None


def test_trade_row_tolerates_garbage_numbers():
    t = _trade(entry_price="abc", fills=[{"qty": "x", "price": float("nan")}, "not-a-dict"], pnl="n/a")
    row = function_app._sleeve_trade_row(t, {})
    assert row["entry_price"] is None and row["pnl_usd"] is None and row["cost_usd"] is None


# --- the per-ticker roll-up -------------------------------------------------------------------------------------------

def test_gain_pct_is_pooled_not_an_average_of_trade_returns():
    a = _trade(entry="2026-10-01", closed="2026-10-02", entry_price=100.0, fills=[{"qty": 10, "price": 110.0}], pnl=100.0)   # +10% on 1000
    b = _trade(entry="2026-10-05", closed="2026-10-06", entry_price=100.0, fills=[{"qty": 90, "price": 99.0}], pnl=-90.0)    # -1% on 9000
    out = function_app._sleeve_trades([a, b], {}, [])
    nvda = out["by_symbol"][0]
    assert nvda["symbol"] == "NVDA" and nvda["closed_trades"] == 2
    assert nvda["pnl_usd"] == 10.0 and nvda["capital_usd"] == 10_000.0
    assert nvda["gain_pct"] == 0.1                       # NOT the 4.5% a mean of (10%, -1%) would give
    assert out["totals"]["gain_pct"] == 0.1


def test_unknown_pnl_trades_are_counted_but_add_to_neither_pnl_nor_capital():
    good = _trade(symbol="AMD")
    bad = _trade(symbol="AMD", entry="2026-10-08", closed="2026-10-09", fills=[{"qty": 5, "price": None}], pnl=None,
                 pnl_unavailable_reason="missing_fill_price")
    out = function_app._sleeve_trades([good, bad], {}, EQUITY)
    amd = out["by_symbol"][0]
    assert amd["closed_trades"] == 2 and amd["pnl_unavailable"] == 1
    assert amd["pnl_usd"] == 30.0 and amd["capital_usd"] == 2000.0 and amd["gain_pct"] == 1.5
    assert out["totals"]["closed_trades"] == 2 and out["totals"]["pnl_unavailable"] == 1


def test_a_ticker_with_only_unknown_trades_has_no_gain_pct():
    bad = _trade(symbol="XYZ", fills=[{"qty": 5, "price": None}], pnl=None, pnl_unavailable_reason="no_fills_recorded")
    out = function_app._sleeve_trades([bad], {}, EQUITY)
    assert out["by_symbol"][0]["gain_pct"] is None and out["by_symbol"][0]["pnl_usd"] is None
    assert out["totals"]["pnl_usd"] is None and out["totals"]["gain_pct"] is None


def test_open_positions_come_from_the_ledger_and_carry_no_pnl():
    ledger = {"AAPL": {"symbol": "AAPL", "entry_date": "2026-10-15", "entry_price": 190.0, "qty_initial": 10, "qty_current": 6,
                       "path": "catalyst", "scaled_out": True}}
    out = function_app._sleeve_trades([_trade(symbol="AAPL")], ledger, EQUITY)
    assert out["open"] == [{"symbol": "AAPL", "entry_date": "2026-10-15", "entry_price": 190.0, "qty": 6.0, "path": "catalyst", "scaled_out": True}]
    aapl = out["by_symbol"][0]
    assert aapl["open"] is True and aapl["closed_trades"] == 1 and aapl["gain_pct"] == 1.5   # open leg does not disturb the closed-trade gain
    assert out["totals"]["open_positions"] == 1


def test_open_position_with_no_closed_trades_is_still_listed():
    ledger = {"MSFT": {"symbol": "MSFT", "entry_date": "2026-10-15", "entry_price": 400.0, "qty_initial": 3}}
    out = function_app._sleeve_trades([], ledger, [])
    assert out["by_symbol"][0]["symbol"] == "MSFT" and out["by_symbol"][0]["closed_trades"] == 0 and out["by_symbol"][0]["gain_pct"] is None
    assert out["open"][0]["qty"] == 3.0                  # falls back to qty_initial when qty_current is absent


def test_by_symbol_is_most_recent_first_and_trades_chronological():
    old = _trade(symbol="AAA", entry="2026-09-01", closed="2026-09-02")
    new = _trade(symbol="BBB", entry="2026-10-12", closed="2026-10-14")
    out = function_app._sleeve_trades([new, old], {}, EQUITY)
    assert [b["symbol"] for b in out["by_symbol"]] == ["BBB", "AAA"]
    assert [t["symbol"] for t in out["trades"]] == ["AAA", "BBB"]


def test_contribution_per_ticker_sums_its_trades():
    t1 = _trade(entry="2026-10-12", closed="2026-10-14")
    t2 = _trade(entry="2026-10-12", closed="2026-10-13", pnl=-10.0, fills=[{"qty": 20, "price": 99.5}])
    out = function_app._sleeve_trades([t1, t2], {}, EQUITY)
    # t1: 30 / 100000 = 0.03pp ; t2: -10 / 99000 = -0.0101pp
    assert out["by_symbol"][0]["contribution_pp"] == round(0.03 + (-10.0 / 99_000.0 * 100.0), 4)


def test_malformed_input_is_skipped_not_raised():
    out = function_app._sleeve_trades(["junk", None, {"symbol": ""}, _trade()], {"X": "not-a-dict"}, [None, {"date": None}])
    assert len(out["trades"]) == 1 and out["open"] == []


def test_block_carries_no_skill_statistic():
    out = function_app._sleeve_trades([_trade(), _trade(symbol="AMD", pnl=-5.0)], {}, EQUITY)
    keys = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                keys.add(k.lower())
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(out)
    assert not any(bad in k for k in keys for bad in ("win", "sharpe", "hit_rate", "sortino", "skill"))


# --- attach -----------------------------------------------------------------------------------------------------------

def _blobs(monkeypatch, files):
    def fake(container, name):
        assert container == "flex-ledger"
        return files.get(name)
    monkeypatch.setattr(function_app, "_download_json", fake)


def test_attach_trades_absent_blobs_means_unavailable_and_nothing_else_changes(monkeypatch):
    _blobs(monkeypatch, {})
    payload = {"series": [{"date": "2026-10-14"}], "as_of": "2026-10-14"}
    function_app._attach_sleeve_trades(payload, None)
    assert payload == {"series": [{"date": "2026-10-14"}], "as_of": "2026-10-14", "sleeve_trades": {"available": False}}


def test_attach_trades_populates_when_closed_trades_exist(monkeypatch):
    _blobs(monkeypatch, {"closed-trades.json": [_trade()], "ledger.json": {}})
    payload = {}
    function_app._attach_sleeve_trades(payload, EQUITY)
    st = payload["sleeve_trades"]
    assert st["available"] is True and st["totals"]["closed_trades"] == 1 and st["trades"][0]["contribution_pp"] == 0.03


def test_attach_trades_wrong_blob_shapes_degrade(monkeypatch):
    _blobs(monkeypatch, {"closed-trades.json": {"not": "a list"}, "ledger.json": ["not", "a dict"]})
    payload = {}
    function_app._attach_sleeve_trades(payload, None)
    assert payload["sleeve_trades"] == {"available": False}


def test_attach_trades_download_failure_degrades(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("blob store down")
    monkeypatch.setattr(function_app, "_download_json", boom)
    payload = {}
    function_app._attach_sleeve_trades(payload, None)       # must not raise
    assert payload["sleeve_trades"] == {"available": False}


def test_attach_series_adds_trades_and_keeps_the_equity_fields(monkeypatch):
    equity_series = [
        {"date": "2026-10-13", "total_equity": 99_000.0, "cumulative_realized_usd": 0.0, "unrealized_usd": 0.0, "closed_trades_to_date": 0},
        {"date": "2026-10-14", "total_equity": 100_000.0, "cumulative_realized_usd": 30.0, "unrealized_usd": 0.0, "closed_trades_to_date": 1},
    ]
    _blobs(monkeypatch, {"equity-series.json": equity_series, "closed-trades.json": [_trade()], "ledger.json": {}})
    payload = {"series": [{"date": "2026-10-13"}, {"date": "2026-10-14"}]}
    function_app._attach_sleeve_series(payload, "2026-10-13")
    assert payload["sleeve_available"] is True and payload["sleeve_closed_trade_count_total"] == 1
    assert payload["series"][1]["sleeve_contribution_pp"] is not None
    assert payload["sleeve_trades"]["available"] is True


def test_attach_series_trade_contribution_uses_equity_outside_the_window(monkeypatch):
    # The window cutoff trims the CHART series, but a trade that closed before the cutoff still needs its day's equity for its contribution.
    equity_series = [
        {"date": "2026-10-14", "total_equity": 100_000.0, "cumulative_realized_usd": 30.0, "unrealized_usd": 0.0, "closed_trades_to_date": 1},
        {"date": "2026-10-16", "total_equity": 101_000.0, "cumulative_realized_usd": 30.0, "unrealized_usd": 0.0, "closed_trades_to_date": 1},
    ]
    _blobs(monkeypatch, {"equity-series.json": equity_series, "closed-trades.json": [_trade()], "ledger.json": {}})
    payload = {"series": [{"date": "2026-10-16"}]}
    function_app._attach_sleeve_series(payload, "2026-10-15")      # excludes the 10-14 point from the chart
    assert payload["sleeve_trades"]["trades"][0]["contribution_pp"] == 0.03


def test_attach_series_trades_still_attach_when_equity_blob_is_missing(monkeypatch):
    _blobs(monkeypatch, {"closed-trades.json": [_trade()], "ledger.json": {}})
    payload = {"series": []}
    function_app._attach_sleeve_series(payload, "2026-10-01")
    assert payload["sleeve_available"] is False and payload["sleeve_trades"]["available"] is True
    assert payload["sleeve_trades"]["trades"][0]["contribution_pp"] is None
