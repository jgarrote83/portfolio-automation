"""Learning Loop v1.0 SWA API endpoint tests (web/api/function_app.py).

Loads function_app.py via importlib (same pattern as test_quadrant_performance.py)
since it deploys standalone, outside src/. All Azure Table clients and GitHub
calls are mocked — no network, no Azure. Run:
    PYTHONPATH=src pytest tests/test_learning_api.py
"""
import base64
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_API_DIR = os.path.join(os.path.dirname(__file__), "..", "web", "api")
sys.path.insert(0, _API_DIR)
_spec = importlib.util.spec_from_file_location("swa_api_learning", os.path.join(_API_DIR, "function_app.py"))
swa_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(swa_api)

import azure.functions as func  # noqa: E402


def _principal_header(user_id="owner-object-id"):
    payload = {"userId": user_id, "userDetails": "jgarrote@easygrids.com", "userRoles": ["owner", "authenticated"]}
    return base64.b64encode(json.dumps(payload).encode()).decode()


def _req(method, route, body=None, principal=True, user_id="owner-object-id"):
    headers = {}
    if principal:
        headers["x-ms-client-principal"] = _principal_header(user_id=user_id)
    return func.HttpRequest(
        method=method, url=f"/api/learning/{route}", headers=headers, params={}, route_params={},
        body=json.dumps(body or {}).encode("utf-8"),
    )


class _FakeTableClient:
    def __init__(self, rows=None):
        self.rows = [dict(r) for r in (rows or [])]

    def list_entities(self):
        return [dict(r) for r in self.rows]

    def query_entities(self, filter_str):
        if "RowKey eq" in filter_str:
            val = filter_str.split("'")[1]
            return [dict(r) for r in self.rows if r.get("RowKey") == val]
        if "status eq" in filter_str:
            val = filter_str.split("'")[1]
            return [dict(r) for r in self.rows if r.get("status") == val]
        return [dict(r) for r in self.rows]

    def upsert_entity(self, entity):
        for i, r in enumerate(self.rows):
            if r.get("PartitionKey") == entity.get("PartitionKey") and r.get("RowKey") == entity.get("RowKey"):
                self.rows[i] = dict(entity)
                return
        self.rows.append(dict(entity))


class _FakeTableService:
    def __init__(self):
        self.tables: dict[str, _FakeTableClient] = {}

    def get_table_client(self, name):
        return self.tables.setdefault(name, _FakeTableClient())


def _setup(monkeypatch, phase="1", owner_id="owner-object-id", proposals_rows=None, cycles_rows=None):
    monkeypatch.setenv("LEARNING_PHASE", phase)
    monkeypatch.setenv("OWNER_OBJECT_ID", owner_id)
    fake_service = _FakeTableService()
    if proposals_rows is not None:
        fake_service.tables["LearningProposals"] = _FakeTableClient(proposals_rows)
    if cycles_rows is not None:
        fake_service.tables["LearningCycles"] = _FakeTableClient(cycles_rows)
    monkeypatch.setattr(swa_api, "_tables", lambda: fake_service)
    monkeypatch.setattr(swa_api, "_reconcile_approved_prs", lambda: None)
    return fake_service


# --- phase gating --------------------------------------------------------------------

def test_decision_at_phase_2_returns_409(monkeypatch):
    _setup(monkeypatch, phase="2")
    resp = swa_api.learning_decision(_req("POST", "decision", {"id": "AMD-1", "decision": "approve"}))
    assert resp.status_code == 409


def test_decision_at_phase_3_proceeds_past_gate(monkeypatch):
    rows = [{"PartitionKey": "2026-07", "RowKey": "AMD-1", "status": "pending"}]
    _setup(monkeypatch, phase="3", proposals_rows=rows)
    req = _req("POST", "decision", {"id": "AMD-1", "decision": "reject", "reason": "no evidence"})
    resp = swa_api.learning_decision(req)
    assert resp.status_code == 200
    assert json.loads(resp.get_body())["status"] == "rejected"


def test_run_at_phase_1_returns_409(monkeypatch):
    _setup(monkeypatch, phase="1")
    resp = swa_api.learning_run_proxy(_req("POST", "run", {}))
    assert resp.status_code == 409


def test_run_at_phase_2_passes_gate(monkeypatch):
    _setup(monkeypatch, phase="2")
    monkeypatch.setattr(swa_api, "_invoke_learning_run", lambda date_str: ({"status": "started"}, None))
    resp = swa_api.learning_run_proxy(_req("POST", "run", {}))
    assert resp.status_code == 200


def test_proposals_get_reports_phase(monkeypatch):
    _setup(monkeypatch, phase="2", proposals_rows=[], cycles_rows=[])
    resp = swa_api.learning_proposals(_req("GET", "proposals"))
    assert resp.status_code == 200
    assert json.loads(resp.get_body())["phase"] == 2


# --- OWNER_OBJECT_ID defense-in-depth --------------------------------------------------

def test_decision_forbidden_for_non_owner(monkeypatch):
    rows = [{"PartitionKey": "2026-07", "RowKey": "AMD-1", "status": "pending"}]
    _setup(monkeypatch, phase="3", owner_id="the-real-owner", proposals_rows=rows)
    req = _req("POST", "decision", {"id": "AMD-1", "decision": "reject", "reason": "x"}, user_id="someone-else")
    resp = swa_api.learning_decision(req)
    assert resp.status_code == 403


def test_run_forbidden_for_non_owner(monkeypatch):
    _setup(monkeypatch, phase="2", owner_id="the-real-owner")
    resp = swa_api.learning_run_proxy(_req("POST", "run", {}, user_id="someone-else"))
    assert resp.status_code == 403


def test_decision_forbidden_when_owner_object_id_unset(monkeypatch):
    rows = [{"PartitionKey": "2026-07", "RowKey": "AMD-1", "status": "pending"}]
    monkeypatch.setenv("LEARNING_PHASE", "3")
    monkeypatch.delenv("OWNER_OBJECT_ID", raising=False)
    fake_service = _FakeTableService()
    fake_service.tables["LearningProposals"] = _FakeTableClient(rows)
    monkeypatch.setattr(swa_api, "_tables", lambda: fake_service)
    req = _req("POST", "decision", {"id": "AMD-1", "decision": "reject", "reason": "x"})
    resp = swa_api.learning_decision(req)
    assert resp.status_code == 403


# --- reject requires a reason ---------------------------------------------------------

def test_reject_without_reason_400(monkeypatch):
    rows = [{"PartitionKey": "2026-07", "RowKey": "AMD-1", "status": "pending"}]
    _setup(monkeypatch, phase="3", proposals_rows=rows)
    resp = swa_api.learning_decision(_req("POST", "decision", {"id": "AMD-1", "decision": "reject"}))
    assert resp.status_code == 400


# --- approve: stale vs clean apply (learning_github mocked) ----------------------------

def test_approve_stale_diff_marks_stale(monkeypatch):
    rows = [{"PartitionKey": "2026-07", "RowKey": "AMD-1", "status": "pending", "class": 1}]
    fake_service = _setup(monkeypatch, phase="3", proposals_rows=rows)
    monkeypatch.setattr(swa_api.learning_github, "approve_proposal",
                        lambda row: {"status": "stale", "reason": "context mismatch at line 4"})
    resp = swa_api.learning_decision(_req("POST", "decision", {"id": "AMD-1", "decision": "approve"}))
    body = json.loads(resp.get_body())
    assert body["status"] == "stale"
    assert fake_service.tables["LearningProposals"].rows[0]["status"] == "stale"


def test_approve_clean_apply_opens_pr(monkeypatch):
    rows = [{"PartitionKey": "2026-07", "RowKey": "AMD-1", "status": "pending", "class": 1}]
    fake_service = _setup(monkeypatch, phase="3", proposals_rows=rows)
    monkeypatch.setattr(
        swa_api.learning_github, "approve_proposal",
        lambda row: {"status": "approved", "pr_url": "https://github.com/x/y/pull/1", "pr_number": 1},
    )
    resp = swa_api.learning_decision(_req("POST", "decision", {"id": "AMD-1", "decision": "approve"}))
    body = json.loads(resp.get_body())
    assert body["status"] == "approved"
    assert body["pr_url"] == "https://github.com/x/y/pull/1"
    updated = fake_service.tables["LearningProposals"].rows[0]
    assert updated["status"] == "approved"
    assert updated["pr_number"] == 1


def test_decision_unknown_proposal_404(monkeypatch):
    _setup(monkeypatch, phase="3", proposals_rows=[])
    resp = swa_api.learning_decision(_req("POST", "decision", {"id": "AMD-NOPE", "decision": "reject", "reason": "x"}))
    assert resp.status_code == 404


def test_decision_already_decided_409(monkeypatch):
    rows = [{"PartitionKey": "2026-07", "RowKey": "AMD-1", "status": "approved"}]
    _setup(monkeypatch, phase="3", proposals_rows=rows)
    resp = swa_api.learning_decision(_req("POST", "decision", {"id": "AMD-1", "decision": "reject", "reason": "x"}))
    assert resp.status_code == 409
