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
from datetime import datetime, timezone
from threading import Lock

import azure.functions as func
from azure.core.exceptions import ResourceNotFoundError
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

        # Build current holdings table (from latest snapshot).
        holdings = []
        total_mv = latest_balances.get("netMv") or sum((p.get("market_value") or 0) for p in latest_positions)
        for p in latest_positions:
            cost = p.get("cost_basis")
            mv   = p.get("market_value")
            tg   = p.get("total_gain")
            tg_pct = (tg / cost * 100) if (cost and tg is not None) else None
            holdings.append({
                "ticker": p.get("ticker"),
                "quantity": p.get("quantity"),
                "cost_basis": cost,
                "market_value": mv,
                "last_price": (latest_prices.get(p.get("ticker")) or {}).get("c"),
                "total_gain": tg,
                "total_gain_pct": round(tg_pct, 2) if tg_pct is not None else None,
                "weight_pct": round((mv / total_mv * 100), 2) if (mv and total_mv) else None,
                "dividends_gain": None,  # TODO: requires E*TRADE transactions or FMP dividend history
            })
        holdings.sort(key=lambda h: -(h.get("market_value") or 0))

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
