"""web/sleeve_trades.js — the Flex catalyst sleeve's per-ticker view: chart markers, the by-ticker table, the trade log.

The module is pure (no DOM, no Chart.js) so it is exercised here under Node. Skipped, loudly, if Node is not installed.
"""
import json
import os
import shutil
import subprocess

import pytest

MODULE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web", "sleeve_trades.js"))
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed; web/sleeve_trades.js is not exercised")


def run(expr: str, data=None):
    """Evaluate a JS expression with ST (the module) and D (the JSON `data`) in scope; return its JSON value."""
    prog = (f"const ST = require({json.dumps(MODULE)}); const D = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
            f"process.stdout.write(JSON.stringify({expr}));")
    out = subprocess.run([NODE, "-e", prog], input=json.dumps(data), capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout) if out.stdout else None


DATES = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]


def series(values=(0.0, 0.1, 0.25, 0.2, 0.3), dates=DATES):
    return [{"date": d, "sleeve_contribution_pp": v} for d, v in zip(dates, values)]


def trade(symbol="NVDA", entry="2026-10-12", closed="2026-10-14", pnl=30.0, ret=1.5, reason="time_stop", **kw):
    t = {"symbol": symbol, "entry_date": entry, "closed_date": closed, "entry_price": 100.0, "exit_price": 101.5, "qty": 20, "pnl_usd": pnl,
         "return_pct": ret, "contribution_pp": 0.03, "exit_reason": reason, "holding_days": 2, "pnl_unavailable_reason": None}
    t.update(kw)
    return t


def st(trades=(), open_=(), by_symbol=None, totals=None):
    return {"available": True, "trades": list(trades), "open": list(open_), "by_symbol": by_symbol or [],
            "totals": totals or {"closed_trades": len(trades), "open_positions": len(open_), "pnl_usd": None, "capital_usd": None,
                                 "gain_pct": None, "contribution_pp": None, "pnl_unavailable": 0}}


# --- formatting -------------------------------------------------------------------------------------------------------

def test_money_and_percent_formatting_round_before_signing():
    assert run("[ST.fmtUsd(1234.5), ST.fmtUsd(1234.5, false), ST.fmtUsd(-7), ST.fmtUsd(-0.004), ST.fmtUsd(null)]") == ["+$1,234.50", "$1,234.50", "-$7.00", "$0.00", "—"]
    assert run("[ST.fmtPct(1.5), ST.fmtPct(-0.004), ST.fmtPct(-2.345), ST.fmtPct(NaN), ST.fmtPct(12.34, 1)]") == ["+1.50%", "0.00%", "-2.35%", "—", "+12.3%"]
    assert run("[ST.fmtPp(0.0304), ST.fmtPp(-0.0004), ST.fmtPp(null)]") == ["+0.030pp", "0.000pp", "—"]


def test_exit_reason_labels():
    assert run("['stop_fill', 'time_stop', 'conviction_released', 'some_new_reason', null].map(ST.exitLabel)") == [
        "Stop hit", "Time stop", "Conviction released", "Some new reason", "—"]


def test_html_escaping():
    assert run("ST.esc('<b>\"x\" & y</b>')") == "&lt;b&gt;&quot;x&quot; &amp; y&lt;/b&gt;"


# --- dates and the line ------------------------------------------------------------------------------------------------

def test_index_for_snaps_forward_and_refuses_outside_the_window():
    labels = {"l": ["2026-10-12", "2026-10-14", "2026-10-16"]}
    # exact hit, a weekend-style gap that snaps FORWARD to the next label, then everything outside the window or unusable
    assert run("[ST.indexFor(D.l, '2026-10-12'), ST.indexFor(D.l, '2026-10-14'), ST.indexFor(D.l, '2026-10-13'), ST.indexFor(D.l, '2026-10-15')]", labels) == [0, 1, 1, 2]
    assert run("[ST.indexFor(D.l, '2026-10-11'), ST.indexFor(D.l, '2026-10-17'), ST.indexFor(D.l, null), ST.indexFor([], '2026-10-12')]", labels) == [-1, -1, -1, -1]


def test_line_value_falls_back_to_nearest_earlier_then_later():
    assert run("[ST.lineValueNear(D.v, 2), ST.lineValueNear(D.v, 0), ST.lineValueNear([null, null], 1)]", {"v": [None, 0.5, None, None, 0.9]}) == [0.5, 0.5, None]


# --- markers ----------------------------------------------------------------------------------------------------------

def test_markers_place_entry_and_exit_on_the_line():
    m = run("ST.buildMarkers(D.s, D.st)", {"s": series(), "st": st([trade()])})
    assert [(x["index"], x["y"], x["label"]) for x in m["entries"]] == [(0, 0.0, "NVDA")]
    assert [(x["index"], x["y"], x["label"], x["outcome"]) for x in m["exits"]] == [(2, 0.25, "NVDA +1.5%", "gain")]


def test_markers_open_position_is_an_entry_only():
    open_ = {"symbol": "AAPL", "entry_date": "2026-10-15", "entry_price": 190.0, "qty": 6, "path": "catalyst", "scaled_out": False}
    m = run("ST.buildMarkers(D.s, D.st)", {"s": series(), "st": st([], [open_])})
    assert [(x["index"], x["label"], x["items"][0]["open"]) for x in m["entries"]] == [(3, "AAPL", True)] and m["exits"] == []


def test_markers_outside_the_window_are_dropped_but_the_in_window_half_stays():
    # entered before the first chart date, closed inside it: only the exit is drawn
    m = run("ST.buildMarkers(D.s, D.st)", {"s": series(), "st": st([trade(entry="2026-10-01", closed="2026-10-14")])})
    assert m["entries"] == [] and len(m["exits"]) == 1
    # closed after the last chart date: no exit marker (the tables still list it)
    m2 = run("ST.buildMarkers(D.s, D.st)", {"s": series(), "st": st([trade(entry="2026-10-15", closed="2026-10-19")])})
    assert len(m2["entries"]) == 1 and m2["exits"] == []


def test_markers_weekend_date_snaps_to_the_next_chart_day():
    s = series(dates=["2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"])
    m = run("ST.buildMarkers(D.s, D.st)", {"s": s, "st": st([trade(entry="2026-10-10", closed="2026-10-14")])})
    assert m["entries"][0]["index"] == 1


def test_markers_same_day_trades_merge_into_one_marker_with_a_joined_label():
    t1, t2 = trade("NVDA", pnl=30.0, ret=1.5), trade("AMD", pnl=-12.0, ret=-0.6)
    m = run("ST.buildMarkers(D.s, D.st)", {"s": series(), "st": st([t1, t2])})
    assert len(m["entries"]) == 1 and m["entries"][0]["label"] == "NVDA · AMD"
    assert len(m["exits"]) == 1 and m["exits"][0]["label"] == "NVDA +1.5% · AMD -0.6%"
    assert m["exits"][0]["outcome"] == "gain"             # net P&L of the merged pair is +18


def test_markers_outcome_loss_and_unknown():
    loss = run("ST.buildMarkers(D.s, D.st)", {"s": series(), "st": st([trade(pnl=-5.0, ret=-0.25)])})
    assert loss["exits"][0]["outcome"] == "loss"
    unk = run("ST.buildMarkers(D.s, D.st)", {"s": series(), "st": st([trade(pnl=None, ret=None, pnl_unavailable_reason="missing_fill_price")])})
    assert unk["exits"][0]["outcome"] == "unknown" and unk["exits"][0]["label"] == "NVDA"


def test_markers_use_nearest_line_value_when_the_day_is_a_gap():
    m = run("ST.buildMarkers(D.s, D.st)", {"s": series(values=(0.0, 0.1, None, 0.2, 0.3)), "st": st([trade()])})
    assert m["exits"][0]["y"] == 0.1


def test_markers_none_when_there_is_no_line_at_all_or_no_data():
    assert run("ST.buildMarkers(D.s, D.st)", {"s": series(values=(None,) * 5), "st": st([trade()])}) == {"entries": [], "exits": []}
    assert run("ST.buildMarkers(D.s, null)", {"s": series()}) == {"entries": [], "exits": []}
    assert run("ST.buildMarkers(D.s, {available: false})", {"s": series()}) == {"entries": [], "exits": []}
    assert run("ST.buildMarkers([], D.st)", {"st": st([trade()])}) == {"entries": [], "exits": []}


def test_tooltip_lines():
    m = run("ST.buildMarkers(D.s, D.st)", {"s": series(), "st": st([trade()])})
    assert run("ST.tooltipLines(D)", m["entries"][0]) == ["Entered NVDA @ $100.00 (2026-10-12)"]
    assert run("ST.tooltipLines(D)", m["exits"][0]) == ["Closed NVDA — +$30.00 (+1.50%) — Time stop"]
    unk = run("ST.buildMarkers(D.s, D.st)", {"s": series(), "st": st([trade(pnl=None, ret=None)])})
    assert run("ST.tooltipLines(D)", unk["exits"][0]) == ["Closed NVDA — P&L unavailable — Time stop"]


def test_label_offset_avoids_collisions():
    box = {"x0": 0, "x1": 50, "y0": 0, "y1": 15}
    placed = [{"x0": 10, "x1": 60, "y0": 0, "y1": 15}]
    assert run("ST.pickLabelOffset(D.box, D.placed, [0, 15, -15])", {"box": box, "placed": placed}) == 15
    assert run("ST.pickLabelOffset(D.box, [], [0, 15])", {"box": box}) == 0
    # nothing fits -> the first offset (never throws, never loops)
    crowd = [{"x0": 0, "x1": 100, "y0": -100, "y1": 100}]
    assert run("ST.pickLabelOffset(D.box, D.placed, [0, 15])", {"box": box, "placed": crowd}) == 0


# --- tables -----------------------------------------------------------------------------------------------------------

BY = [{"symbol": "NVDA", "closed_trades": 2, "pnl_usd": 10.0, "capital_usd": 10000.0, "gain_pct": 0.1, "contribution_pp": 0.02, "pnl_unavailable": 0,
       "open": False, "last_date": "2026-10-14"},
      {"symbol": "AAPL", "closed_trades": 0, "pnl_usd": None, "capital_usd": None, "gain_pct": None, "contribution_pp": None, "pnl_unavailable": 0,
       "open": True, "last_date": "2026-10-15"}]
TOTALS = {"closed_trades": 2, "open_positions": 1, "pnl_usd": 10.0, "capital_usd": 10000.0, "gain_pct": 0.1, "contribution_pp": 0.02, "pnl_unavailable": 0}


def test_by_ticker_table_shows_gain_pct_and_totals():
    html = run("ST.byTickerHtml(D)", st([], [], BY, TOTALS))
    assert "<strong>NVDA</strong>" in html and "+0.10%" in html and "+$10.00" in html and "$10,000.00" in html
    assert "open position" in html and "flat" in html
    assert "All tickers" in html and "1 open" in html


def test_by_ticker_table_flags_unknown_pnl_in_a_tooltip():
    by = [dict(BY[0], pnl_unavailable=1)]
    html = run("ST.byTickerHtml(D)", st([], [], by, TOTALS))
    assert 'title="1 closed trade with unknown P&amp;L left out' in html


def test_by_ticker_table_escapes_symbols():
    by = [dict(BY[0], symbol="<img src=x>")]
    html = run("ST.byTickerHtml(D)", st([], [], by, TOTALS))
    assert "<img" not in html and "&lt;img src=x&gt;" in html


def test_tables_have_an_empty_state_and_no_skill_statistics():
    assert "No flex trades recorded yet." in run("ST.byTickerHtml(null)")
    assert "No flex trades recorded yet." in run("ST.byTickerHtml({available: false})")
    assert "No flex trades recorded yet." in run("ST.tradeLogHtml({available: true, trades: [], open: []})")
    full = (run("ST.byTickerHtml(D)", st([trade()], [], BY, TOTALS)) + run("ST.tradeLogHtml(D)", st([trade()], [], BY, TOTALS))).lower()
    assert not any(w in full for w in ("win rate", "win-rate", "sharpe", "hit rate", "sortino"))


def test_trade_log_lists_open_first_then_closed_newest_first():
    open_ = {"symbol": "AAPL", "entry_date": "2026-10-15", "entry_price": 190.0, "qty": 6, "path": "catalyst", "scaled_out": True}
    older = trade("AMD", entry="2026-10-01", closed="2026-10-02", pnl=-5.0, ret=-0.25, reason="stop_fill")
    newer = trade("NVDA")
    html = run("ST.tradeLogHtml(D)", st([older, newer], [open_]))
    order = [html.index(f"<strong>{s}</strong>") for s in ("AAPL", "NVDA", "AMD")]
    assert order == sorted(order)
    assert "Open (partly scaled out)" in html and "Time stop" in html and "Stop hit" in html
    assert "-$5.00" in html and "-0.25%" in html and "+$30.00" in html and "+1.50%" in html


def test_trade_log_explains_unavailable_pnl():
    t = trade(pnl=None, ret=None, pnl_unavailable_reason="missing_fill_price", exit_price=None)
    html = run("ST.tradeLogHtml(D)", st([t]))
    assert 'title="P&amp;L unavailable: missing fill price"' in html


def test_headline():
    assert run("ST.headline(D)", st([], [], BY, TOTALS)) == "Realised +$10.00 on $10,000.00 deployed = +0.10% (not annualised)."
    assert run("ST.headline(D)", st([], [], BY, dict(TOTALS, pnl_usd=None, capital_usd=None, gain_pct=None))) == ""
    assert run("ST.headline(null)") == ""
