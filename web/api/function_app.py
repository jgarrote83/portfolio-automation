"""
SWA managed API — Phase 1 wiring (read-only from blob storage).

Routes:
  GET  /api/dates             list YYYY-MM-DD from daily-reports/
  GET  /api/report/{date}     download daily-reports/{date}.md (text/markdown)
  GET  /api/trades/{date}     download daily-trades/{date}.json, merge approvals
  GET  /api/snapshot/{date}   download daily-snapshots/{date}.json
  GET  /api/executions/{date} download daily-executions/{date}.json or []
  POST /api/trades/{date}/approve  write approvals/{date}.json (status=approved)
  POST /api/trades/{date}/reject   write approvals/{date}.json (status=rejected)
  GET  /api/me                echo SWA client principal

Phase 2 will add executor invocation in approve handler using FUNC_MASTER_KEY.

Auth: SWA enforces role=owner on /api/* via staticwebapp.config.json. The
x-ms-client-principal header is auto-injected by SWA.

Storage: STORAGE_CONNECTION_STRING app setting.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import socket
from datetime import datetime, timezone
from threading import Lock

import azure.functions as func
import learning_github
from azure.core.exceptions import ResourceNotFoundError
from azure.data.tables import TableServiceClient
from azure.storage.blob import BlobServiceClient

try:  # urllib is stdlib, but keep import isolated so module load is clean
    from urllib import request as urlrequest
    from urllib import error as urlerror
except Exception:  # pragma: no cover
    urlrequest = None
    urlerror = None

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
log = logging.getLogger("pfauto.web.api")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ── blob client (lazy, cached) ────────────────────────────────────────────────
_blob_lock = Lock()
_blob_client: BlobServiceClient | None = None


def _blobs() -> BlobServiceClient:
    global _blob_client
    if _blob_client is None:
        with _blob_lock:
            if _blob_client is None:
                conn = os.environ.get("STORAGE_CONNECTION_STRING")
                if not conn:
                    raise RuntimeError("STORAGE_CONNECTION_STRING app setting is not set")
                _blob_client = BlobServiceClient.from_connection_string(conn)
    return _blob_client


# ── table client (lazy, cached) — Learning Loop reads/writes only ────────────
_table_lock = Lock()
_table_client: TableServiceClient | None = None


def _tables() -> TableServiceClient:
    global _table_client
    if _table_client is None:
        with _table_lock:
            if _table_client is None:
                conn = os.environ.get("STORAGE_CONNECTION_STRING")
                if not conn:
                    raise RuntimeError("STORAGE_CONNECTION_STRING app setting is not set")
                _table_client = TableServiceClient.from_connection_string(conn)
    return _table_client


# ── helpers ───────────────────────────────────────────────────────────────────
def _json(body, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        body=json.dumps(body), status_code=status, mimetype="application/json"
    )


def _err(msg: str, status: int = 400) -> func.HttpResponse:
    return _json({"error": msg}, status=status)


def _valid_date(d: str | None) -> bool:
    return bool(d and _DATE_RE.match(d))


def _client_principal(req: func.HttpRequest) -> dict | None:
    raw = req.headers.get("x-ms-client-principal")
    if not raw:
        return None
    try:
        return json.loads(base64.b64decode(raw).decode("utf-8"))
    except Exception:
        return None


def _download_text(container: str, name: str) -> str | None:
    try:
        blob = _blobs().get_blob_client(container, name)
        return blob.download_blob().readall().decode("utf-8")
    except ResourceNotFoundError:
        return None


def _download_json(container: str, name: str):
    text = _download_text(container, name)
    if text is None:
        return None
    return json.loads(text)


def _upload_json(container: str, name: str, body) -> None:
    container_client = _blobs().get_container_client(container)
    try:
        container_client.create_container()
    except Exception:
        pass  # already exists
    blob = container_client.get_blob_client(name)
    blob.upload_blob(json.dumps(body, indent=2).encode("utf-8"), overwrite=True)


def _normalize_trade(t: dict) -> dict:
    """Map analyzer schema (side/symbol/order_type) to UI schema (action/ticker)."""
    side = t.get("side") or t.get("action")
    symbol = t.get("symbol") or t.get("ticker")
    return {
        **t,
        "action": (side or "").upper(),
        "ticker": symbol,
        # Quantity / confidence / limit_price / rationale already match UI fields.
    }


def _merge_decisions(trades: list[dict], approvals: dict | None) -> list[dict]:
    """Apply approval decisions onto trade records by id."""
    by_id: dict = {}
    if approvals:
        by_id = {d.get("id"): d for d in approvals.get("decisions", []) if d.get("id")}
    out: list[dict] = []
    for raw in trades:
        t = _normalize_trade(raw)
        d = by_id.get(t.get("id"))
        if d:
            t["status"] = d.get("status", "pending")
            t["decided_by"] = d.get("user")
            t["decided_at"] = d.get("ts")
        else:
            t.setdefault("status", "pending")
        out.append(t)
    return out


def _record_decisions(date_str: str, ids: list[str], status: str, principal: dict | None) -> dict:
    """Read approvals/{date}.json, upsert decisions for ids, write back."""
    existing = _download_json("approvals", f"{date_str}.json") or {
        "date": date_str, "decisions": []
    }
    by_id = {d.get("id"): d for d in existing.get("decisions", []) if d.get("id")}
    user = (principal or {}).get("userDetails")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for tid in ids:
        by_id[tid] = {"id": tid, "status": status, "user": user, "ts": now}
    existing["decisions"] = list(by_id.values())
    existing["updated_at"] = now
    _upload_json("approvals", f"{date_str}.json", existing)
    return existing


# ── routes ────────────────────────────────────────────────────────────────────
@app.route(route="dates", methods=["GET"])
def dates(req: func.HttpRequest) -> func.HttpResponse:
    try:
        container = _blobs().get_container_client("daily-reports")
        names: list[str] = []
        for b in container.list_blobs():
            stem = b.name.rsplit(".", 1)[0]
            if _DATE_RE.match(stem):
                names.append(stem)
        names.sort(reverse=True)
        return _json(names[:60])
    except ResourceNotFoundError:
        return _json([])
    except Exception as e:
        log.exception("dates failed")
        return _err(str(e), status=500)


@app.route(route="report/{date}", methods=["GET"])
def report(req: func.HttpRequest) -> func.HttpResponse:
    d = req.route_params.get("date")
    if not _valid_date(d):
        return _err("invalid date", status=400)
    try:
        md = _download_text("daily-reports", f"{d}.md")
        if md is None:
            return func.HttpResponse(
                body=f"# No report for {d}\n", status_code=404, mimetype="text/markdown"
            )
        return func.HttpResponse(body=md, status_code=200, mimetype="text/markdown")
    except Exception as e:
        log.exception("report failed")
        return _err(str(e), status=500)


@app.route(route="trades/{date}", methods=["GET"])
def trades(req: func.HttpRequest) -> func.HttpResponse:
    d = req.route_params.get("date")
    if not _valid_date(d):
        return _err("invalid date", status=400)
    try:
        raw = _download_json("daily-trades", f"{d}.json")
        if raw is None:
            return _json([])
        # Analyzer may write either a bare list or {"trades": [...]}
        if isinstance(raw, dict):
            items = raw.get("trades") or raw.get("recommendations") or []
        else:
            items = raw
        if not isinstance(items, list):
            items = []
        approvals = _download_json("approvals", f"{d}.json")
        return _json(_merge_decisions(items, approvals))
    except Exception as e:
        log.exception("trades failed")
        return _err(str(e), status=500)


@app.route(route="snapshot/{date}", methods=["GET"])
def snapshot(req: func.HttpRequest) -> func.HttpResponse:
    d = req.route_params.get("date")
    if not _valid_date(d):
        return _err("invalid date", status=400)
    try:
        snap = _download_json("daily-snapshots", f"{d}.json")
        if snap is None:
            return _json({}, status=404)
        return _json(snap)
    except Exception as e:
        log.exception("snapshot failed")
        return _err(str(e), status=500)


@app.route(route="executions/{date}", methods=["GET"])
def executions(req: func.HttpRequest) -> func.HttpResponse:
    d = req.route_params.get("date")
    if not _valid_date(d):
        return _err("invalid date", status=400)
    try:
        body = _download_json("daily-executions", f"{d}.json")
        return _json(body if body is not None else [])
    except Exception as e:
        log.exception("executions failed")
        return _err(str(e), status=500)


def _decide(req: func.HttpRequest, status: str) -> func.HttpResponse:
    d = req.route_params.get("date")
    if not _valid_date(d):
        return _err("invalid date", status=400)
    principal = _client_principal(req)
    try:
        body = req.get_json() or {}
    except ValueError:
        body = {}
    ids = [str(i) for i in (body.get("ids") or []) if i]
    if not ids:
        return _err("no ids provided", status=400)
    try:
        record = _record_decisions(d, ids, status, principal)
        log.info(
            "decision date=%s status=%s ids=%s user=%s",
            d, status, ids, principal and principal.get("userDetails"),
        )
        return _json({"status": status, "ids": ids, "approvals": record})
    except Exception as e:
        log.exception("decision failed")
        return _err(str(e), status=500)


def _invoke_executor(date_str: str | None) -> tuple[dict | None, str | None]:
    """POST to func-pfauto executor; returns (result, error). Both None if not configured."""
    if not date_str:
        return None, "missing date"
    host = (
        os.environ.get("FUNCTION_APP_HOST")
        or (os.environ.get("FUNCTION_APP_NAME") and f"{os.environ['FUNCTION_APP_NAME']}.azurewebsites.net")
        or "func-pfauto.azurewebsites.net"
    )
    key = os.environ.get("FUNC_MASTER_KEY")
    if not key:
        log.warning("FUNC_MASTER_KEY not set — skipping executor invocation")
        return None, None
    if urlrequest is None:
        return None, "urllib unavailable"
    url = f"https://{host}/api/executor"
    payload = json.dumps({"date": date_str}).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-functions-key": key,
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8")
            return json.loads(text), None
    except urlerror.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            pass
        log.exception("executor HTTP %s: %s", e.code, body)
        return None, f"executor http {e.code}: {body or e.reason}"
    except Exception as e:  # noqa: BLE001
        log.exception("executor invocation failed")
        return None, str(e)


@app.route(route="trades/{date}/approve", methods=["POST"])
def approve(req: func.HttpRequest) -> func.HttpResponse:
    """Record approval, then invoke func-pfauto executor (Phase 2)."""
    resp = _decide(req, "approved")
    if resp.status_code != 200:
        return resp
    d = req.route_params.get("date")
    exec_result, exec_err = _invoke_executor(d)
    if exec_result is None and exec_err is None:
        # Executor not configured — return approval-only response.
        return resp
    payload = json.loads(resp.get_body().decode("utf-8"))
    payload["execution"] = exec_result
    if exec_err:
        payload["execution_error"] = exec_err
    return _json(payload)


@app.route(route="trades/{date}/reject", methods=["POST"])
def reject(req: func.HttpRequest) -> func.HttpResponse:
    return _decide(req, "rejected")


@app.route(route="me", methods=["GET"])
def me(req: func.HttpRequest) -> func.HttpResponse:
    return _json({"clientPrincipal": _client_principal(req)})


# ── Learning Loop (FOLLOWUPS #13/#32, docs/specs/Learning_Loop_v1.0.md) ──────
# Proposer != approver: nothing here can apply a change directly — Approve
# opens a GitHub PR (learning_github.py) that a human must merge. All three
# routes sit behind the platform's mandatory owner-role auth (staticwebapp.
# config.json); POST routes additionally require the `owner` role on the
# principal, optionally pinned tighter to a specific SWA user id via
# OWNER_USER_ID (defense in depth, spec §7 — see _owner_ok).

_LEARNING_PROPOSALS_TABLE = "LearningProposals"
_LEARNING_CYCLES_TABLE = "LearningCycles"
_PR_RECONCILE_CACHE_SECONDS = 600  # spec §8 point 3: cache >= 10 min


def _learning_phase() -> int:
    try:
        return int(os.environ.get("LEARNING_PHASE", "1"))
    except ValueError:
        return 1


def _owner_ok(principal: dict | None) -> bool:
    """Baseline: the authenticated principal must hold the `owner` role — the
    same invitation-only role the platform's route rules already enforce
    (staticwebapp.config.json). Optionally pinned tighter: if `OWNER_USER_ID`
    is set, the principal's SWA `userId` must also match it exactly.

    NOTE: on SWA's built-in AAD provider, `x-ms-client-principal.userId` is an
    OPAQUE SWA-GENERATED HASH, not the Entra object id — the two are unrelated
    identifiers. Capture the real value from `/.auth/me` AFTER signing in, not
    from Entra/az CLI (fixed 2026-07-12 after that exact mistake shipped in
    PR #23 as `OWNER_OBJECT_ID`, which would have denied everyone, including
    the real owner, since no request's `userId` could ever equal an Entra OID)."""
    if not principal:
        return False
    if "owner" not in (principal.get("userRoles") or []):
        return False
    pin = os.environ.get("OWNER_USER_ID")
    if pin:
        return principal.get("userId") == pin
    return True


def _find_proposal(table, proposal_id: str) -> dict | None:
    """Look up a proposal by RowKey alone (a query, not a point read) — the
    caller only has the id, not the PartitionKey (year-month of its cycle)."""
    rows = list(table.query_entities(f"RowKey eq '{proposal_id}'"))
    return dict(rows[0]) if rows else None


def _write_amendment_history(proposal: dict) -> None:
    """OverrideHistory row, layer amendment (spec §9) — grading hooks null;
    the mechanical grader is deferred (Task G FOLLOWUPS entry)."""
    table = _tables().get_table_client("OverrideHistory")
    cycle = str(proposal.get("cycle") or "")
    table.upsert_entity({
        "PartitionKey": cycle[:7] or "unknown",
        "RowKey": f"AMD-{proposal.get('RowKey') or proposal.get('id')}",
        "recommended_at": cycle,
        "layer": "amendment",
        "proposal_id": proposal.get("RowKey") or proposal.get("id"),
        "sleeve": proposal.get("target_file", ""),
        "falsifier": (proposal.get("falsifier") or "")[:32000],
        "review_by": proposal.get("review_by", ""),
        "outcome_status": "",
        "resolved_correct": None,
    })


def _reconcile_approved_prs() -> None:
    """Lazily flip `approved` proposals to `applied` once their PR merges
    (no webhook in v1 — checked opportunistically on every GET, cached per
    row so we don't hammer the GitHub API)."""
    try:
        table = _tables().get_table_client(_LEARNING_PROPOSALS_TABLE)
        rows = list(table.query_entities("status eq 'approved'"))
    except Exception:
        log.exception("learning: could not query approved proposals for reconciliation")
        return
    now = datetime.now(timezone.utc)
    for row in rows:
        last_checked = row.get("pr_last_checked")
        if last_checked:
            try:
                if (now - datetime.fromisoformat(last_checked)).total_seconds() < _PR_RECONCILE_CACHE_SECONDS:
                    continue
            except ValueError:
                pass
        pr_number = row.get("pr_number")
        if not pr_number:
            continue
        try:
            merged = learning_github.is_pr_merged(int(pr_number))
        except Exception:
            log.exception("learning: PR state check failed for %s", row.get("RowKey"))
            continue
        row["pr_last_checked"] = now.isoformat()
        if merged:
            row["status"] = "applied"
            row["applied_at"] = now.isoformat()
            try:
                _write_amendment_history(row)
            except Exception:
                log.exception("learning: OverrideHistory amendment write failed for %s", row.get("RowKey"))
        try:
            table.upsert_entity(row)
        except Exception:
            log.exception("learning: reconciliation upsert failed for %s", row.get("RowKey"))


@app.route(route="learning/proposals", methods=["GET"])
def learning_proposals(req: func.HttpRequest) -> func.HttpResponse:
    try:
        _reconcile_approved_prs()
    except Exception:
        log.exception("learning: reconciliation pass failed (non-fatal)")
    try:
        prop_table = _tables().get_table_client(_LEARNING_PROPOSALS_TABLE)
        rows = [dict(e) for e in prop_table.list_entities()]
        cyc_table = _tables().get_table_client(_LEARNING_CYCLES_TABLE)
        cycles = sorted(
            (dict(e) for e in cyc_table.list_entities()),
            key=lambda c: str(c.get("RowKey", "")), reverse=True,
        )
    except Exception as e:
        log.exception("learning_proposals failed")
        return _err(str(e), status=500)
    pending = [r for r in rows if r.get("status") == "pending"]
    history = [r for r in rows if r.get("status") != "pending"]
    return _json({
        "phase": _learning_phase(),
        "pending": pending,
        "history": history,
        "cycles": cycles[:12],
        "last_cycle": cycles[0] if cycles else None,
    })


@app.route(route="learning/decision", methods=["POST"])
def learning_decision(req: func.HttpRequest) -> func.HttpResponse:
    if _learning_phase() < 3:
        return _err("decisions are not open yet (LEARNING_PHASE < 3)", status=409)
    principal = _client_principal(req)
    if not _owner_ok(principal):
        return _err("forbidden", status=403)
    try:
        body = req.get_json() or {}
    except ValueError:
        body = {}
    proposal_id = body.get("id")
    decision = body.get("decision")
    reason = body.get("reason")
    if not proposal_id or decision not in ("approve", "reject"):
        return _err("body must be {id, decision: approve|reject, reason?}", status=400)
    if decision == "reject" and not (reason and str(reason).strip()):
        return _err("reason is required on reject", status=400)

    table = _tables().get_table_client(_LEARNING_PROPOSALS_TABLE)
    try:
        row = _find_proposal(table, proposal_id)
    except Exception as e:
        log.exception("learning_decision lookup failed")
        return _err(str(e), status=500)
    if row is None:
        return _err(f"no such proposal: {proposal_id}", status=404)
    if row.get("status") != "pending":
        return _err(f"proposal {proposal_id} is not pending (status={row.get('status')})", status=409)

    now = datetime.now(timezone.utc).isoformat()
    user = (principal or {}).get("userDetails")

    if decision == "reject":
        row["status"] = "rejected"
        row["decision"] = "reject"
        row["decision_reason"] = str(reason)
        row["decided_at"] = now
        row["decided_by"] = user or ""
        table.upsert_entity(row)
        return _json({"id": proposal_id, "status": "rejected"})

    try:
        result = learning_github.approve_proposal(row)
    except Exception as e:  # noqa: BLE001
        log.exception("learning approve failed for %s", proposal_id)
        return _err(f"approve failed: {e}", status=500)

    row["decision"] = "approve"
    row["decided_at"] = now
    row["decided_by"] = user or ""
    if result.get("status") == "stale":
        row["status"] = "stale"
        row["decision_reason"] = result.get("reason", "diff no longer applies to current master")
        table.upsert_entity(row)
        return _json({"id": proposal_id, "status": "stale", "reason": result.get("reason")})

    row["status"] = "approved"
    row["pr_url"] = result.get("pr_url", "")
    row["pr_number"] = result.get("pr_number")
    table.upsert_entity(row)
    return _json({"id": proposal_id, "status": "approved", "pr_url": result.get("pr_url")})


def _invoke_learning_run(date_str: str | None) -> tuple[dict | None, str | None]:
    """POST to func-pfauto's learning_run trigger (mirrors _invoke_executor).

    SWA managed-function requests are capped at 45s by the platform, but a
    reviewer cycle's Foundry call can run well past that. A client-side
    timeout here does NOT mean the cycle failed — func-pfauto keeps running
    independently — so it is reported as "started", not an error.
    """
    host = (
        os.environ.get("FUNCTION_APP_HOST")
        or (os.environ.get("FUNCTION_APP_NAME") and f"{os.environ['FUNCTION_APP_NAME']}.azurewebsites.net")
        or "func-pfauto.azurewebsites.net"
    )
    key = os.environ.get("FUNC_MASTER_KEY")
    if not key:
        return None, "FUNC_MASTER_KEY not set"
    if urlrequest is None:
        return None, "urllib unavailable"
    url = f"https://{host}/api/learning_run"
    payload = json.dumps({"date": date_str} if date_str else {}).encode("utf-8")
    req = urlrequest.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json", "x-functions-key": key},
    )
    try:
        with urlrequest.urlopen(req, timeout=35) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except socket.timeout:
        return {"status": "started", "note": "still running in the background — check back shortly"}, None
    except urlerror.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            pass
        return None, f"learning_run http {e.code}: {body or e.reason}"
    except Exception as e:  # noqa: BLE001
        return None, str(e)


@app.route(route="learning/run", methods=["POST"])
def learning_run_proxy(req: func.HttpRequest) -> func.HttpResponse:
    if _learning_phase() < 2:
        return _err("manual run is not available yet (LEARNING_PHASE < 2)", status=409)
    principal = _client_principal(req)
    if not _owner_ok(principal):
        return _err("forbidden", status=403)
    try:
        body = req.get_json() or {}
    except ValueError:
        body = {}
    result, error = _invoke_learning_run(body.get("date"))
    if result is None:
        return _err(error or "learning run invocation failed", status=502)
    return _json(result)


# ── performance: portfolio vs S&P 500 time series ─────────────────────────────
_WINDOW_DAYS = {
    "YTD": None,  # special-cased below
    "3M":  93,
    "6M":  186,
    "1Y":  366,
    "2Y":  732,
    "3Y":  1098,
    "ALL": None,
}


def _quadrant_series(points: list[dict], quadrant_map: dict) -> list[dict]:
    """Equal-weight buy-and-hold index (window start = 100) per quadrant basket.

    Pure over the cache points (each carrying `closes`: {ticker: close}). A
    member's base is its first close inside the window; a day's quadrant index
    is the mean of member normalized closes available that day. A member with
    no base yet contributes nothing (late-appearing tickers can't distort the
    index retroactively); a quadrant with no members priced that day is None.
    """
    bases: dict[str, float] = {}
    out: list[dict] = []
    for p in points:
        closes = p.get("closes") or {}
        for t, c in closes.items():
            if c and t not in bases:
                bases[t] = float(c)
        row: dict = {}
        for q, members in (quadrant_map or {}).items():
            vals = [
                float(closes[t]) / bases[t] * 100.0
                for t in members
                if closes.get(t) and bases.get(t)
            ]
            row[q] = round(sum(vals) / len(vals), 3) if vals else None
        out.append(row)
    return out


def _holdings_from(positions: list, balances: dict, prices: dict) -> list[dict]:
    """Current holdings valuation rows (shared by the cache + legacy paths)."""
    holdings = []
    total_mv = (balances or {}).get("netMv") or sum(
        (p.get("market_value") or 0) for p in positions
    )
    for p in positions:
        cost = p.get("cost_basis")
        mv   = p.get("market_value")
        tg   = p.get("total_gain")
        tg_pct = (tg / cost * 100) if (cost and tg is not None) else None
        holdings.append({
            "ticker": p.get("ticker"),
            "quantity": p.get("quantity"),
            "cost_basis": cost,
            "market_value": mv,
            "last_price": ((prices or {}).get(p.get("ticker")) or {}).get("c"),
            "total_gain": tg,
            "total_gain_pct": round(tg_pct, 2) if tg_pct is not None else None,
            "weight_pct": round((mv / total_mv * 100), 2) if (mv and total_mv) else None,
            "dividends_gain": None,  # TODO: requires FMP dividend history
        })
    holdings.sort(key=lambda h: -(h.get("market_value") or 0))
    return holdings


def _latest_snapshot():
    """(date, snapshot) for the most recent daily snapshot; (None, None) if none."""
    container = _blobs().get_container_client("daily-snapshots")
    names = []
    for b in container.list_blobs():
        stem = b.name.rsplit(".", 1)[0]
        if _DATE_RE.match(stem):
            names.append(stem)
    for d in sorted(names, reverse=True):
        snap = _download_json("daily-snapshots", f"{d}.json")
        if snap:
            return d, snap
    return None, None


@app.route(route="performance", methods=["GET"])
def performance(req: func.HttpRequest) -> func.HttpResponse:
    """Return time series for portfolio total value and SPY close, plus current
    holdings valuations. Query param: window=YTD|3M|6M|1Y|2Y|3Y|ALL (default 1Y).
    """
    from datetime import date, timedelta

    window = (req.params.get("window") or "1Y").upper()
    if window not in _WINDOW_DAYS:
        return _err(f"invalid window (use {list(_WINDOW_DAYS)})", 400)

    today = date.today()
    if window == "YTD":
        cutoff = date(today.year, 1, 1)
    elif window == "ALL":
        cutoff = date(1900, 1, 1)
    else:
        cutoff = today - timedelta(days=_WINDOW_DAYS[window])
    cutoff_str = cutoff.isoformat()

    try:
        # ── Fast path: the collector-owned compact cache (one small blob read
        # instead of one ~1.2 MB snapshot download per day in the window). Also
        # the only path that serves the quadrant-basket series + regime bands.
        cache = _download_json("performance", "equity-series.json")
        if isinstance(cache, list) and cache:
            qcfg = _download_json("performance", "quadrant-config.json") or {}
            pts = [p for p in cache if (p.get("date") or "") >= cutoff_str]
            series = [{
                "date": p.get("date"),
                "portfolio_value": p.get("equity"),
                "spy_close": p.get("spy_close"),
                "favored_bucket": p.get("favored_bucket") or [],
            } for p in pts]
            if series:
                p0 = series[0]["portfolio_value"]
                s0 = series[0]["spy_close"]
                for pt in series:
                    pt["portfolio_norm"] = (
                        round((pt["portfolio_value"] / p0) * 100, 3)
                        if p0 and pt["portfolio_value"] else None
                    )
                    pt["spy_norm"] = (
                        round((pt["spy_close"] / s0) * 100, 3)
                        if s0 and pt["spy_close"] else None
                    )
            qmap = qcfg.get("quadrants") or {}
            if qmap:
                for pt, row in zip(series, _quadrant_series(pts, qmap)):
                    pt["quadrants"] = row

            latest_date, snap = _latest_snapshot()
            pf = (snap or {}).get("portfolio") or {}
            balances = pf.get("balances") or {}
            holdings = _holdings_from(
                pf.get("positions") or [], balances, (snap or {}).get("prices") or {}
            )
            return _json({
                "window": window,
                "cutoff": cutoff_str,
                "as_of": latest_date,
                "series": series,
                "holdings": holdings,
                "balances": balances,
                "quadrant_config": qcfg or None,
            })

        # ── Legacy fallback (cache not yet populated): full snapshot scan.
        container = _blobs().get_container_client("daily-snapshots")
        # List all snapshot blob names (cheap; metadata only).
        all_names = []
        for b in container.list_blobs():
            stem = b.name.rsplit(".", 1)[0]
            if _DATE_RE.match(stem) and stem >= cutoff_str:
                all_names.append(stem)
        all_names.sort()

        series = []
        latest_positions = []
        latest_balances = {}
        latest_prices = {}
        latest_date = None

        for d in all_names:
            snap = _download_json("daily-snapshots", f"{d}.json")
            if not snap:
                continue
            pf = snap.get("portfolio") or {}
            bal = pf.get("balances") or {}
            prices = snap.get("prices") or {}
            spy = (prices.get("SPY") or {}).get("c")
            # Performance basis = Alpaca paper-account EQUITY (cash + position
            # market value), NOT balances.totalAccountValue. `tav` was sourced
            # inconsistently across snapshots: a static config-fallback value
            # (~$44,195) on days before the paper account was funded, then the
            # live value (~$99K) after — which rendered as a fake +124% step.
            # Requiring paper_account.equity also makes the series begin on the
            # first funded/trading day (the account was seeded 2026-05-26), so
            # the chart starts when buying began rather than at a placeholder.
            # With no external cash flows into the paper account, normalized
            # equity %-change is the true (time-weighted) return vs SPY.
            equity = (snap.get("paper_account") or {}).get("equity")
            if equity is not None and spy is not None:
                series.append({
                    "date": d,
                    "portfolio_value": round(float(equity), 2),
                    "spy_close": round(float(spy), 4),
                })
            latest_date = d
            latest_positions = pf.get("positions") or latest_positions
            latest_balances  = bal or latest_balances
            latest_prices    = prices or latest_prices

        # Normalize both series to 100 at first point for chart comparability.
        if series:
            p0 = series[0]["portfolio_value"]
            s0 = series[0]["spy_close"]
            for pt in series:
                pt["portfolio_norm"] = round((pt["portfolio_value"] / p0) * 100, 3) if p0 else None
                pt["spy_norm"]       = round((pt["spy_close"] / s0) * 100, 3) if s0 else None

        holdings = _holdings_from(latest_positions, latest_balances, latest_prices)

        return _json({
            "window": window,
            "cutoff": cutoff_str,
            "as_of": latest_date,
            "series": series,
            "holdings": holdings,
            "balances": latest_balances,
        })
    except Exception as e:
        log.exception("performance failed")
        return _err(str(e), status=500)
