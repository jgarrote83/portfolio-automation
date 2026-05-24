"""
SWA managed API — stubs that return mock data.

Wire-up steps (deferred per `scaffold infra now, wire data later`):
  - GET  /api/dates           → list YYYY-MM-DD from daily-reports/ blob container
  - GET  /api/report/{date}   → download daily-reports/{date}.md
  - GET  /api/trades/{date}   → download daily-trades/{date}.json (merge approval state)
  - GET  /api/snapshot/{date} → download daily-snapshots/{date}.json
  - GET  /api/executions/{date} → download daily-executions/{date}.json (if exists)
  - POST /api/trades/{date}/approve → mark ids approved, write approvals/{date}.json,
                                       invoke func-pfauto executor (Phase 2)
  - POST /api/trades/{date}/reject  → mark ids rejected, write approvals/{date}.json

Auth: SWA injects `x-ms-client-principal` header on every request. The
staticwebapp.config.json restricts /api/* to role "owner" — assign that role to
your user via the SWA portal once auth is wired.

Storage / KV access via DefaultAzureCredential (SWA system-assigned MI).
Env vars (set in bicep): STORAGE_ACCOUNT_NAME, KEY_VAULT_NAME, FUNCTION_APP_NAME.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta

import azure.functions as func

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

log = logging.getLogger("pfauto.web.api")


def _client_principal(req: func.HttpRequest) -> dict | None:
    raw = req.headers.get("x-ms-client-principal")
    if not raw:
        return None
    import base64
    try:
        return json.loads(base64.b64decode(raw).decode("utf-8"))
    except Exception:
        return None


def _json(body, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        body=json.dumps(body),
        status_code=status,
        mimetype="application/json",
    )


# ─── stub data ────────────────────────────────────────────────────────────────
def _stub_dates() -> list[str]:
    today = date.today()
    return [(today - timedelta(days=i)).isoformat() for i in range(5)]


def _stub_trades(d: str) -> list[dict]:
    return [
        {"id": f"{d}-1", "action": "SELL", "ticker": "IDVO", "quantity": 50,
         "limit_price": 28.10, "confidence": 0.72, "status": "pending",
         "rationale": "Stub — wiring deferred."},
        {"id": f"{d}-2", "action": "BUY", "ticker": "AIA", "quantity": 25,
         "limit_price": 81.40, "confidence": 0.61, "status": "pending",
         "rationale": "Stub — wiring deferred."},
    ]


def _stub_report(d: str) -> str:
    return (
        f"# Portfolio report — {d}\n\n"
        "_Stub. Wire `/api/report/{date}` to download "
        "`daily-reports/{date}.md` from blob storage._\n\n"
        "## Summary\n\n- Item one\n- Item two\n"
    )


def _stub_snapshot(d: str) -> dict:
    return {
        "date": d,
        "portfolio": {
            "positions": [
                {"ticker": "IDVO", "quantity": 200, "weight": 0.18,
                 "last_price": 28.05, "day_pl": -42.10, "unrealised": 612.0},
                {"ticker": "AIA", "quantity": 100, "weight": 0.22,
                 "last_price": 81.20, "day_pl": 31.00, "unrealised": 1180.0},
            ]
        },
    }


# ─── routes ───────────────────────────────────────────────────────────────────
@app.route(route="dates", methods=["GET"])
def dates(req: func.HttpRequest) -> func.HttpResponse:
    return _json(_stub_dates())


@app.route(route="report/{date}", methods=["GET"])
def report(req: func.HttpRequest) -> func.HttpResponse:
    d = req.route_params.get("date")
    return func.HttpResponse(
        body=_stub_report(d), status_code=200, mimetype="text/markdown"
    )


@app.route(route="trades/{date}", methods=["GET"])
def trades(req: func.HttpRequest) -> func.HttpResponse:
    return _json(_stub_trades(req.route_params.get("date")))


@app.route(route="snapshot/{date}", methods=["GET"])
def snapshot(req: func.HttpRequest) -> func.HttpResponse:
    return _json(_stub_snapshot(req.route_params.get("date")))


@app.route(route="executions/{date}", methods=["GET"])
def executions(req: func.HttpRequest) -> func.HttpResponse:
    return _json([])  # stub — no executions yet


@app.route(route="trades/{date}/approve", methods=["POST"])
def approve(req: func.HttpRequest) -> func.HttpResponse:
    d = req.route_params.get("date")
    principal = _client_principal(req)
    try:
        body = req.get_json() or {}
    except ValueError:
        body = {}
    ids = body.get("ids", [])
    log.info("approve date=%s ids=%s user=%s", d, ids, principal and principal.get("userDetails"))
    # TODO: persist to approvals/{date}.json and invoke executor function
    return _json({"approved": ids, "status": "stub-ok"})


@app.route(route="trades/{date}/reject", methods=["POST"])
def reject(req: func.HttpRequest) -> func.HttpResponse:
    d = req.route_params.get("date")
    principal = _client_principal(req)
    try:
        body = req.get_json() or {}
    except ValueError:
        body = {}
    ids = body.get("ids", [])
    log.info("reject date=%s ids=%s user=%s", d, ids, principal and principal.get("userDetails"))
    return _json({"rejected": ids, "status": "stub-ok"})


@app.route(route="me", methods=["GET"])
def me(req: func.HttpRequest) -> func.HttpResponse:
    return _json({"clientPrincipal": _client_principal(req)})
