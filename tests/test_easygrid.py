"""EasyGrid tab: the API views (web/api/easygrid.py) compute every displayed value from the engine's blobs; the controls are
validated server-side (allowlist, typed confirmation, reason, CSRF header) and can write only the two control documents; the page's
pure renderers (web/easygrid.js, under Node) show "Not started" before the first fill, always carry the preview label, and show a red
"Holding mismatch"."""
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web" / "api"))

import easygrid as EG  # noqa: E402

DEC = {"kind": "decision", "date": "2026-06-30", "trend": {"spy_close": 700.0, "sma10": 650.0, "distance_pct": 7.69, "ok": True},
       "credit": {"baa10y": 1.75, "baa10y_date": "2026-06-29", "avg126": 1.72, "ok": False}, "holding": "1X", "target": "SPY"}
ADMIN = {"userId": "swa-user-1", "userDetails": "jgarrote@easygrids.com", "userRoles": ["anonymous", "authenticated", "owner"]}
HDR = {"X-EasyGrid-Control": "1"}
NOW = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)


def st(month, on, eq, target, created, period=None, kind="monthly"):
    return {"month": month, "kind": kind, "status": "executed", "decision_date": "2026-06-30", "execution_date": on, "submitted_on": on,
            "equity_after_close": eq, "created_at": created, "decision": dict(DEC, target=target),
            "fills": [{"side": "buy", "symbol": target, "fill_minus_close": 0.02}], "period": period}


SERIES = [{"date": "2026-07-01", "equity": 100_000.0, "spy_close": 600.0}, {"date": "2026-07-02", "equity": 102_000.0, "spy_close": 606.0},
          {"date": "2026-08-03", "equity": 101_000.0, "spy_close": 612.0}, {"date": "2026-08-04", "equity": 103_020.0, "spy_close": 618.12}]
PERF = [{"date": "2026-07-01", "equity": 50_000.0}, {"date": "2026-07-02", "equity": 50_500.0}, {"date": "2026-08-03", "equity": 51_000.0},
        {"date": "2026-08-04", "equity": 51_510.0}]
STATES = [st("202606", "2026-07-01", 100_000.0, "SPY", "2026-07-01T10"), st("202607", "2026-08-03", 101_000.0, "SSO", "2026-08-01T10")]


# --- views --------------------------------------------------------------------------------------------------------------

def test_not_started_shows_the_dry_run():
    s = EG.summary([], [], [], {"enabled": False, "next_decision_date": "2026-10-30"}, None, {"decision": DEC})
    assert s["started"] is False and s["status"] == "Not started" and s["dry_run"]["decision"] == DEC and s["enabled"] is False


def test_headline_key_numbers_and_month_to_date():
    s = EG.summary(STATES, SERIES, PERF, {"enabled": True, "reconcile": "ok", "next_decision_date": "2026-08-31", "data": {"spy": True, "credit": True}},
                   {"holdings": {"SSO": 1990.0}, "as_of": "2026-08-03"}, None)
    h = s["headline"]
    assert h["easygrid"] == pytest.approx(0.0302) and h["spy"] == pytest.approx(0.0302) and h["core"] == pytest.approx(0.0302)
    assert h["ahead"] is True and h["vs_spy_points"] == pytest.approx(0.0, abs=1e-9)
    assert s["account_value"] == 103_020.0 and s["started_at"] == 100_000.0 and s["holding_name"] == "SSO · 2x S&P 500"
    assert s["below_peak"]["easygrid"] == 0.0 and s["month_to_date"]["since"] == "2026-08-03"
    assert s["month_to_date"]["easygrid"] == pytest.approx(0.02) and s["month_to_date"]["core"] == pytest.approx(0.01)
    assert s["health"]["positions_match"] is True and s["health"]["data"] == {"spy": True, "credit": True}


def test_growth_ranges_markers_and_drawdown():
    g = EG.growth(STATES, SERIES, PERF, "since_start")
    assert g["base_date"] == "2026-07-01" and g["points"][-1]["easygrid"] == pytest.approx(103.02)
    assert [m["holding"] for m in g["markers"]] == ["SPY", "SSO"]
    assert [round(x["easygrid"], 4) for x in g["drawdown"]] == [0.0, 0.0, round(101 / 102 - 1, 4), 0.0]
    one = EG.growth(STATES, SERIES, PERF, "1m")
    # 1 month back from 2026-08-04 is 2026-07-05; the first point on or after it is 2026-08-03, re-indexed to 100
    assert one["base_date"] == "2026-08-03" and one["points"][0]["easygrid"] == 100.0 and one["points"][-1]["easygrid"] == pytest.approx(102.0)
    assert [m["holding"] for m in one["markers"]] == ["SSO"]                                  # only changes inside the range
    assert EG.growth(STATES, SERIES, PERF, "bogus")["range"] == "since_start"


def test_criteria_cards_bars_and_today_row():
    c = EG.criteria(STATES, {**DEC, "kind": "preview", "date": "2026-10-16", "note": "whatever"}, None)
    assert c["preview"]["note"] == EG.PREVIEW_LABEL and c["preview_label"] == EG.PREVIEW_LABEL
    lc = c["last_decision"]
    assert lc["trend"]["status"] == "Healthy" and lc["credit"]["status"] == "Not healthy"
    assert lc["trend"]["bar_pos"] == pytest.approx(0.5 + 7.69 / 20) and lc["credit"]["diff"] == pytest.approx(0.03)
    assert [r["today"] for r in c["rule"]] == [False, True, False]
    assert EG.criteria([], None, None)["preview_label"] == "If the month ended today (preview, not a decision)."
    assert EG.criteria([], None, None)["last_decision"] is None


def test_decision_rows_pass_engine_values_through():
    rows = EG.decisions(STATES + [{"month": "PT202609011000", "kind": "to_tbills", "status": "executed", "created_at": "2026-09-01T10",
                                   "decision": {"target": "BIL"}, "reason": "risk off"}])
    assert rows[0]["month"] == "Pause to T-bills" and rows[0]["decision"] == "T-bills" and rows[0]["reason"] == "risk off"
    assert rows[1]["month"] == "Jun 2026" and rows[1]["trend_distance_pct"] == 7.69 and rows[1]["fill_vs_close"] == 0.02
    assert rows[1]["credit_diff"] == pytest.approx(0.03) and rows[1]["decision"] == "SSO · 2x"


def test_health_reports_a_holding_mismatch():
    h = EG.health_view({"reconcile": "ok", "holding_mismatch": {"expected": "SSO", "actual": {"SPY": 900.0}}, "control": "paused"})
    assert h["positions_match"] is False and h["holding_mismatch"]["expected"] == "SSO" and h["kill_switch"] is True


# --- controls (server-side validation) ----------------------------------------------------------------------------------

def ok_body(action="pause", confirm="PAUSE"):
    return {"action": action, "confirm": confirm, "reason": "testing the switch"}


def test_non_allowlisted_users_are_rejected():
    for principal, ids in ((None, "swa-user-1"), ({**ADMIN, "userId": "someone-else"}, "swa-user-1"),
                           ({**ADMIN, "userRoles": ["authenticated"]}, "swa-user-1"), (ADMIN, ""), (ADMIN, None)):
        code, err, writes = EG.validate_control(principal, ids, HDR, "POST", ok_body(), None, NOW)
        assert (code, writes) == (403, {}), (principal, ids)


def test_wrong_confirmation_missing_reason_or_header_is_rejected():
    assert EG.validate_control(ADMIN, "swa-user-1", HDR, "POST", ok_body(confirm="pause"), None, NOW)[0] == 400
    assert EG.validate_control(ADMIN, "swa-user-1", HDR, "POST", ok_body("pause_to_tbills", "PAUSE"), None, NOW)[0] == 400
    assert EG.validate_control(ADMIN, "swa-user-1", HDR, "POST", {**ok_body(), "reason": "   "}, None, NOW)[0] == 400
    assert EG.validate_control(ADMIN, "swa-user-1", {}, "POST", ok_body(), None, NOW)[0] == 400                  # CSRF header
    assert EG.validate_control(ADMIN, "swa-user-1", HDR, "GET", ok_body(), None, NOW)[0] == 405
    assert EG.validate_control(ADMIN, "swa-user-1", HDR, "POST", ok_body("delete_everything", "PAUSE"), None, NOW)[0] == 400


@pytest.mark.parametrize("action,phrase,state", [("pause", "PAUSE", "paused"), ("pause_to_tbills", "PAUSE TO T-BILLS", "paused_to_tbills"),
                                                 ("resume", "RESUME", "active")])
def test_valid_actions_write_only_the_control_documents(action, phrase, state):
    code, err, writes = EG.validate_control(ADMIN, "swa-user-1", HDR, "POST", ok_body(action, phrase), {"state": "paused"}, NOW)
    assert code == 200 and err is None and set(writes) <= {"kill_switch", "realign"}
    assert writes["kill_switch"]["state"] == state and writes["kill_switch"]["reason"] == "testing the switch"
    assert "swa-user-1" in writes["kill_switch"]["changed_by"]
    assert ("realign" in writes) == (action == "resume")                          # resume = active AND a re-align at the next close


def test_realign_needs_active_and_writes_only_the_request():
    assert EG.validate_control(ADMIN, "swa-user-1", HDR, "POST", ok_body("realign", "REALIGN"), {"state": "paused"}, NOW)[0] == 409
    code, _, writes = EG.validate_control(ADMIN, "swa-user-1", HDR, "POST", ok_body("realign", "REALIGN"), {"state": "active"}, NOW)
    assert code == 200 and set(writes) == {"realign"} and writes["realign"]["requested_at"] == NOW.isoformat()


def test_the_route_can_write_nowhere_but_the_two_sas_targets():
    src = (ROOT / "web" / "api" / "function_app.py").read_text(encoding="utf-8")
    block = src.split("# ============================================================================ EasyGrid (the C5 paper account)")[1]
    assert re.search(r'_EG_WRITE_TARGETS = \{"kill_switch": "C5_KILL_SWITCH_SAS_URL", "realign": "C5_REALIGN_SAS_URL"\}', block)
    assert "_upload_json(" not in block and "get_blob_client(" not in block          # never the account-wide client for a write
    assert block.count("upload_blob(") == 1 and "BlobClient.from_blob_url(url)" in block
    bicep = (ROOT / "infra" / "modules" / "staticwebapp.bicep").read_text(encoding="utf-8")
    assert "C5_KILL_SWITCH_SAS_URL: c5KillSwitchSasUrl" in bicep and "EASYGRID_ADMIN_IDS: easygridAdminIds" in bicep
    seed = (ROOT / "scripts" / "seed-c5-control-sas.sh").read_text(encoding="utf-8")
    assert "--permissions rcw" in seed and seed.count("generate-sas") == 1 and "kill-switch.json:" in seed and "realign-request.json:" in seed


def test_controls_view_hides_nothing_but_marks_admin_and_shows_the_last_ten():
    v = EG.controls_view({"state": "paused", "changed_at": "t", "reason": "r"}, [{"key": str(i)} for i in range(15)], False, None)
    assert v["state"] == "paused" and v["is_admin"] is False and [e["key"] for e in v["log"]][0] == "14" and len(v["log"]) == 10


# --- page renderers under Node ----------------------------------------------------------------------------------------------

NODE = shutil.which("node")
MODULE = str(ROOT / "web" / "easygrid.js")


def js(expr, data=None):
    prog = f"const EG = require({json.dumps(MODULE)}); const D = JSON.parse(require('fs').readFileSync(0, 'utf8')); process.stdout.write(JSON.stringify({expr}));"
    r = subprocess.run([NODE, "-e", prog], input=json.dumps(data), capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
def test_node_check_passes():
    assert subprocess.run([NODE, "--check", MODULE], capture_output=True).returncode == 0


@needs_node
def test_empty_state_before_the_first_fill():
    s = EG.summary([], [], [], {"next_decision_date": "2026-10-30"}, None, {"decision": DEC})
    head = js("EG.headlineHtml(D)", s)
    assert "Not started." in head and "SPY · 1x" in head and "Oct 30" in head
    assert "Not started" in js("EG.keyNumbersHtml(D)", s) and "No decisions yet." in js("EG.decisionsHtml([])")
    assert js("EG.engineChip(D)", s) == "Engine: Disabled"


@needs_node
def test_preview_label_is_always_rendered():
    for data in ({}, EG.criteria([], None, None), EG.criteria(STATES, {**DEC, "kind": "preview", "date": "2026-10-16"}, None)):
        assert "If the month ended today (preview, not a decision)." in js("EG.whyHtml(D)", data)


@needs_node
def test_status_is_never_colour_alone_and_mismatch_is_red_with_expected_vs_actual():
    why = js("EG.whyHtml(D)", EG.criteria(STATES, None, None))
    assert "Healthy" in why and "Not healthy" in why and "<svg" in why
    h = js("EG.healthHtml(D)", EG.health_view({"reconcile": "ok", "holding_mismatch": {"expected": "SSO", "actual": {"SPY": 900.0}}}))
    assert "Holding mismatch" in h and "expected SSO" in h and "SPY 900" in h and "eg-alert" in h


@needs_node
def test_control_buttons_only_for_admins():
    assert "data-action" not in js("EG.controlsHtml(D)", {"state": "active", "is_admin": False, "log": []})
    html = js("EG.controlsHtml(D)", {"state": "paused", "is_admin": True, "log": []})
    assert 'data-action="resume"' in html and 'data-action="pause"' not in html and "Paused" in html
