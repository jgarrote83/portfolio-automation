"""EasyGrid tab: the read-only API views (web/api/easygrid.py) pass engine values through unchanged; the page's pure renderers
(web/easygrid.js, under Node) show the empty state before the first fill and always carry the preview label."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web" / "api"))

import easygrid as EG  # noqa: E402

DECISION = {"kind": "decision", "date": "2026-06-30", "trend": {"spy_close": 700.0, "sma10": 650.0, "distance_pct": 7.69, "ok": True},
            "credit": {"baa10y": 1.7, "baa10y_date": "2026-06-29", "avg126": 1.72, "ok": True}, "holding": "2X", "target": "SSO"}


def executed(month, on, eq, target="SSO", period=None):
    return {"month": month, "status": "executed", "decision_date": "x", "execution_date": on, "submitted_on": on, "equity_after_close": eq,
            "decision": dict(DECISION, target=target), "orders": [{"side": "buy", "qty": 100, "symbol": target}],
            "fills": [{"symbol": target, "side": "buy", "filled_qty": 100.0, "avg_price": 50.0, "close": 50.0, "slippage_bps_vs_close": 0.0}],
            "period": period}


# --- API views --------------------------------------------------------------------------------------------------------------

def test_summary_before_the_first_fill_is_not_started_with_the_dry_run():
    s = EG.summary([], [], {"enabled": False, "last_run": "t", "next_decision_date": "2026-07-31"}, None, {"decision": DECISION})
    assert s["started"] is False and s["status"] == "Not started" and s["dry_run"]["decision"] == DECISION
    assert s["next_decision_date"] == "2026-07-31" and s["health"]["enabled"] is False


def test_summary_passes_engine_values_through():
    st = [executed("202606", "2026-07-01", 100_000.0)]
    series = [{"date": "2026-07-01", "equity": 100_000.0}, {"date": "2026-07-02", "equity": 101_500.0}]
    s = EG.summary(st, series, {"enabled": True, "reconcile": "ok", "kill_switch": False}, {"holdings": {"SSO": 1990.0}, "as_of": "2026-07-01"}, None)
    assert s["equity"] == 101_500.0 and s["return_since_first_fill"] == pytest.approx(0.015)
    assert s["holding"] == "SSO" and s["holding_since"] == "2026-07-01" and s["health"]["reconcile"] == "ok"


def test_series_is_indexed_to_100_at_the_first_fill_with_markers_on_changes():
    st = [executed("202606", "2026-07-01", 100_000.0, "SSO"), executed("202607", "2026-08-03", 1.0, "SSO"), executed("202608", "2026-09-01", 1.0, "SPY")]
    c5 = [{"date": "2026-06-30", "equity": 1.0}, {"date": "2026-07-01", "equity": 100_000.0}, {"date": "2026-07-02", "equity": 102_000.0}]
    perf = [{"date": "2026-07-01", "equity": 50_000.0, "spy_close": 600.0}, {"date": "2026-07-02", "equity": 50_500.0, "spy_close": 606.0}]
    out = EG.series_indexed(st, c5, perf)
    assert out["base_date"] == "2026-07-01" and [p["date"] for p in out["points"]] == ["2026-07-01", "2026-07-02"]
    assert out["points"][1] == {"date": "2026-07-02", "easygrid": 102.0, "core": 101.0, "spy": 101.0, "holding": None}
    assert [m["holding"] for m in out["markers"]] == ["SSO", "SPY"]                 # the August repeat of SSO is not a change


def test_decisions_pass_inputs_orders_fills_and_period_through():
    st = [executed("202606", "2026-07-01", 1.0, period={"from": "2026-07-01", "to": "2026-08-03", "return": 0.02, "spy_return": 0.01})]
    d = EG.decisions(st)[0]
    assert d["trend"] == DECISION["trend"] and d["credit"] == DECISION["credit"] and d["fills"][0]["slippage_bps_vs_close"] == 0.0
    assert d["period"]["return"] == 0.02 and d["orders"][0]["qty"] == 100


def test_criteria_always_label_the_preview():
    c = EG.criteria([], {"kind": "preview", "note": "something else"}, {"decision": DECISION})
    assert c["preview"]["note"] == EG.PREVIEW_LABEL == c["preview_label"]
    assert c["last_decision"] == DECISION and c["last_decision_source"] == "dry_run"
    assert EG.criteria([], None, None)["preview_label"] == "If the month ended today (preview, not a decision)."


# --- page renderers under Node ----------------------------------------------------------------------------------------------

NODE = shutil.which("node")
MODULE = str(ROOT / "web" / "easygrid.js")


def js(expr, data=None):
    prog = f"const EG = require({json.dumps(MODULE)}); const D = JSON.parse(require('fs').readFileSync(0, 'utf8')); process.stdout.write(JSON.stringify({expr}));"
    r = subprocess.run([NODE, "-e", prog], input=json.dumps(data), capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_node_check_passes():
    assert subprocess.run([NODE, "--check", MODULE], capture_output=True).returncode == 0


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_empty_state_before_the_first_fill_shows_the_dry_run_and_not_started():
    html = js("EG.summaryHtml(D)", EG.summary([], [], {"next_decision_date": "2026-07-31"}, None, {"decision": DECISION}))
    assert "Not started." in html and "SSO (2x S&amp;P)" in html and "2026-07-31" in html
    assert "No decisions yet." in js("EG.decisionsHtml([])")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_preview_label_is_always_rendered():
    for data in ({}, EG.criteria([], None, None), EG.criteria([], {"kind": "preview", **DECISION}, None)):
        assert "If the month ended today (preview, not a decision)." in js("EG.criteriaHtml(D)", data)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_health_strip_shows_the_switch_kill_switch_and_mismatch():
    html = js("EG.healthHtml(D)", {"enabled": False, "kill_switch": True, "reconcile": {"mismatches": [1]}, "last_run": "t"})
    assert "C5_ENABLED <strong>false</strong>" in html and "TRIPPED" in html and "mismatch" in html
