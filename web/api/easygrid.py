"""EasyGrid (the C5 paper account): the read-only portal views and the validation of the two portal controls. Pure functions; the
routes in function_app.py do the I/O. Every value the page shows is computed HERE, so the page only formats (the design rule:
"the page never recomputes anything").

Engine blobs (func-pfauto's `c5_tick`, src/c5/handler.py):
  c5-state/<yyyymm>.json, PT*.json, RA*.json   decisions and control actions (inputs, orders, fills, period returns)
  c5-state/health.json            last tick: last_run, enabled, control, reconcile, holding_mismatch, data, next_decision_date
  c5-state/dry-run.json           the latest month-end decision and intended orders under the current control state
  c5-state/preview.json           "if the month ended today" (a preview, never a decision)
  c5-state/kill-switch.json       {"state": "active"|"paused"|"paused_to_tbills", "changed_at", "changed_by", "reason"}
  c5-state/realign-request.json   a one-shot re-align request (cleared by the engine once honoured)
  c5-state/kill-switch-log.jsonl  every control change, appended by the engine
  c5-ledger/ledger.json           the holdings the engine expects, and since when
  c5-equity-series/series.json    {date, equity, cash, holding, spy_close} after each close
Core's line comes from `performance/equity-series.json` (`date`, `equity`).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

PREVIEW_LABEL = "If the month ended today (preview, not a decision)."
HOLDING_NAME = {"SSO": "SSO · 2x S&P 500", "SPY": "SPY · 1x", "BIL": "T-bills (BIL)"}
SHORT_NAME = {"SSO": "SSO · 2x", "SPY": "SPY · 1x", "BIL": "T-bills"}
RULE_ROWS = [("Both healthy", "SSO"), ("One healthy", "SPY"), ("Neither", "BIL")]
RANGES = {"since_start": None, "3m": 91, "1m": 30}
CONTROL_ACTIONS = {"pause": ("PAUSE", "paused"), "pause_to_tbills": ("PAUSE TO T-BILLS", "paused_to_tbills"),
                   "resume": ("RESUME", "active"), "realign": ("REALIGN", None)}
CONTROL_HEADER = "X-EasyGrid-Control"
MAX_REASON = 500


# ================================================================================================ helpers
def _ret(a, b):
    return (b / a - 1.0) if (a and b) else None


def acted(states: list[dict]) -> list[dict]:
    """States the account actually moved on (executed), oldest first."""
    return sorted((s for s in states if s.get("status") == "executed"), key=lambda s: (s.get("created_at") or "", s.get("month", "")))


def first_fill(states: list[dict]) -> dict | None:
    ex = [s for s in acted(states) if s.get("submitted_on")]
    return ex[0] if ex else None


def _point_on_or_after(series: list[dict], d: str) -> dict | None:
    return next((p for p in series if p.get("date", "") >= d), None)


def _core_by_date(perf: list[dict]) -> dict:
    return {p.get("date"): p.get("equity") for p in (perf or []) if p.get("date")}


def _core_at(core: dict, d: str):
    """Core's equity on date d, or the latest one before it."""
    keys = sorted(k for k in core if k <= d)
    return core[keys[-1]] if keys else None


def _drawdown(values: list[float]) -> list[float | None]:
    out, peak = [], None
    for v in values:
        if v is None:
            out.append(None)
            continue
        peak = v if peak is None else max(peak, v)
        out.append(v / peak - 1.0)
    return out


def _month_label(d: str | None) -> str | None:
    if not d:
        return None
    return date.fromisoformat(d).strftime("%b %Y")


# ================================================================================================ views
def summary(states: list[dict], series: list[dict], perf: list[dict], health: dict | None, ledger: dict | None, dry_run: dict | None) -> dict:
    h = health_view(health)
    ff = first_fill(states)
    out = {"started": ff is not None, "enabled": h["enabled"], "health": h, "next_decision_date": (health or {}).get("next_decision_date")}
    if ff is None:
        out.update({"status": "Not started", "dry_run": dry_run})
        return out
    start = ff["submitted_on"]
    pts = [p for p in series if p.get("date", "") >= start]
    last = pts[-1] if pts else None
    base = pts[0] if pts else None
    core = _core_by_date(perf)
    eg = _ret(base and base.get("equity"), last and last.get("equity"))
    spy = _ret(base and base.get("spy_close"), last and last.get("spy_close"))
    core_r = _ret(_core_at(core, start), _core_at(core, last["date"])) if last else None
    eg_dd = _drawdown([p.get("equity") for p in pts])
    spy_dd = _drawdown([p.get("spy_close") for p in pts])
    holdings = sorted(((ledger or {}).get("holdings") or {}))
    held = holdings[0] if len(holdings) == 1 else (", ".join(holdings) or None)
    out.update({
        "status": "Running", "first_fill_date": start, "as_of": last["date"] if last else None,
        "headline": {"easygrid": eg, "spy": spy, "core": core_r,
                     "vs_spy_points": None if (eg is None or spy is None) else (eg - spy) * 100.0,
                     "ahead": None if (eg is None or spy is None) else eg >= spy},
        "account_value": last.get("equity") if last else None, "started_at": ff.get("equity_after_close"),
        "holding": held, "holding_name": HOLDING_NAME.get(held, held), "holding_since": (ledger or {}).get("as_of"),
        "below_peak": {"easygrid": eg_dd[-1] if eg_dd else None, "spy": spy_dd[-1] if spy_dd else None},
        "month_to_date": month_to_date(states, series, perf),
    })
    return out


def month_to_date(states: list[dict], series: list[dict], perf: list[dict]) -> dict | None:
    """From the close of the latest executed decision to the latest close: resets at each monthly decision."""
    ex = acted(states)
    if not ex or not series:
        return None
    start = ex[-1].get("submitted_on")
    b, last = _point_on_or_after(series, start), series[-1]
    if not b:
        return None
    core = _core_by_date(perf)
    return {"since": b["date"], "easygrid": _ret(b.get("equity"), last.get("equity")), "spy": _ret(b.get("spy_close"), last.get("spy_close")),
            "core": _ret(_core_at(core, b["date"]), _core_at(core, last["date"]))}


def growth(states: list[dict], series: list[dict], perf: list[dict], rng: str = "since_start") -> dict:
    """Growth of 100 for EasyGrid, Core and SPY from the range's first point; EasyGrid and SPY drawdowns from the first fill; the dates
    the holding changed, with that decision."""
    ff = first_fill(states)
    if ff is None:
        return {"started": False, "range": rng, "points": [], "markers": [], "drawdown": []}
    if rng not in RANGES:
        rng = "since_start"
    full = [p for p in series if p.get("date", "") >= ff["submitted_on"]]
    if not full:
        return {"started": True, "range": rng, "points": [], "markers": [], "drawdown": []}
    days = RANGES[rng]
    start = full[0]["date"] if days is None else max(full[0]["date"], (date.fromisoformat(full[-1]["date"]) - timedelta(days=days)).isoformat())
    pts = [p for p in full if p["date"] >= start]
    core = _core_by_date(perf)
    b_eg, b_spy, b_core = pts[0].get("equity"), pts[0].get("spy_close"), _core_at(core, pts[0]["date"])
    points = []
    for p in pts:
        c = _core_at(core, p["date"])
        points.append({"date": p["date"], "easygrid": round(p["equity"] / b_eg * 100, 3) if b_eg and p.get("equity") else None,
                       "core": round(c / b_core * 100, 3) if (b_core and c) else None,
                       "spy": round(p["spy_close"] / b_spy * 100, 3) if (b_spy and p.get("spy_close")) else None})
    markers, prev = [], None
    for s in acted(states):
        tgt = (s.get("decision") or {}).get("target")
        if tgt != prev and s.get("submitted_on", "") >= start:
            markers.append({"date": s["submitted_on"], "holding": tgt, "holding_name": HOLDING_NAME.get(tgt, tgt),
                            "decision_date": s.get("decision_date"), "kind": s.get("kind", "monthly")})
        prev = tgt
    eg_dd, spy_dd = _drawdown([p.get("equity") for p in full]), _drawdown([p.get("spy_close") for p in full])
    dd = [{"date": p["date"], "easygrid": a, "spy": b} for p, a, b in zip(full, eg_dd, spy_dd) if p["date"] >= start]
    return {"started": True, "range": rng, "base_date": pts[0]["date"], "points": points, "markers": markers, "drawdown": dd}


def _bar(pos: float) -> float:
    return max(0.03, min(0.97, pos))


def _criteria_card(dec: dict | None) -> dict | None:
    if not dec:
        return None
    t, c = dec.get("trend") or {}, dec.get("credit") or {}
    dist = t.get("distance_pct")
    diff = (c["baa10y"] - c["avg126"]) if (c.get("baa10y") is not None and c.get("avg126") is not None) else None
    return {"date": dec.get("date"), "target": dec.get("target"), "target_name": HOLDING_NAME.get(dec.get("target"), dec.get("target")),
            "trend": {**t, "status": "Healthy" if t.get("ok") else "Not healthy",
                      "bar_pos": _bar(0.5 + dist / 20.0) if dist is not None else None},            # full scale: +/-10% from the average
            "credit": {**c, "diff": diff, "status": "Healthy" if c.get("ok") else "Not healthy",
                       "bar_pos": _bar(0.5 + diff / 0.5) if diff is not None else None}}            # full scale: +/-0.25 points


def criteria(states: list[dict], preview: dict | None, dry_run: dict | None) -> dict:
    decided = [s for s in sorted(states, key=lambda s: (s.get("created_at") or "", s.get("month", ""))) if s.get("decision") and s.get("kind", "monthly") != "to_tbills"]
    last = decided[-1]["decision"] if decided else ((dry_run or {}).get("decision"))
    source = "decision" if decided else ("dry_run" if last else None)
    pv = _criteria_card(preview)
    if pv is not None:
        pv["note"] = PREVIEW_LABEL                      # the label is always present, whatever the blob says
    today_target = (pv or {}).get("target") or (last or {}).get("target")
    return {"last_decision": _criteria_card(last), "last_decision_source": source, "preview": pv, "preview_label": PREVIEW_LABEL,
            "rule": [{"when": w, "holding": h, "holding_name": HOLDING_NAME[h], "today": h == today_target} for w, h in RULE_ROWS]}


def decisions(states: list[dict]) -> list[dict]:
    out = []
    for s in sorted(states, key=lambda s: (s.get("created_at") or "", s.get("month", "")), reverse=True):
        d = s.get("decision") or {}
        kind = s.get("kind", "monthly")
        t, c = d.get("trend") or {}, d.get("credit") or {}
        buy = next((f for f in (s.get("fills") or []) if f.get("side") == "buy"), None)
        p = s.get("period") or {}
        label = {"monthly": _month_label(s.get("decision_date")), "to_tbills": "Pause to T-bills", "realign": "Re-align"}.get(kind, kind)
        out.append({"month": label, "kind": kind, "decision_date": s.get("decision_date"), "execution_date": s.get("execution_date"),
                    "status": s.get("status"), "first_enable": bool(s.get("first_enable")),
                    "trend_ok": t.get("ok"), "trend_distance_pct": t.get("distance_pct"),
                    "credit_ok": c.get("ok"), "credit_diff": (c["baa10y"] - c["avg126"]) if (c.get("baa10y") is not None and c.get("avg126") is not None) else None,
                    "target": d.get("target"), "decision": SHORT_NAME.get(d.get("target"), d.get("target")),
                    "fill_vs_close": buy.get("fill_minus_close") if buy else None,
                    "easygrid_return": p.get("return"), "spy_return": p.get("spy_return"), "reason": s.get("reason")})
    return out


def health_view(health: dict | None) -> dict:
    h = health or {}
    mm = h.get("holding_mismatch")
    rec = h.get("reconcile")
    return {"last_run": h.get("last_run"), "enabled": h.get("enabled") is True, "control": h.get("control") or "active",
            "kill_switch": h.get("control") not in (None, "active"), "realign_pending": bool(h.get("realign_pending")),
            "positions_match": None if rec in (None, "not_checked") else (rec == "ok" and not mm),
            "holding_mismatch": mm, "reconcile_mismatches": (rec or {}).get("mismatches") if isinstance(rec, dict) else None,
            "data": h.get("data"), "error": h.get("error")}


# ================================================================================================ controls
def is_admin(principal: dict | None, admin_ids: str | None) -> bool:
    """Signed in with the `owner` role AND the principal's SWA userId on the allowlist (`EASYGRID_ADMIN_IDS`, comma-separated).
    The SWA userId is the value from /.auth/me after signing in, NOT the Entra object id. An empty allowlist admits nobody."""
    if not principal or "owner" not in (principal.get("userRoles") or []):
        return False
    allowed = {x.strip() for x in (admin_ids or "").split(",") if x.strip()}
    return bool(allowed) and principal.get("userId") in allowed


def validate_control(principal: dict | None, admin_ids: str | None, headers: dict, method: str, body: dict | None,
                     current: dict | None, now: datetime | None = None) -> tuple[int, str | None, dict]:
    """-> (http status, error, {"kill_switch": doc?, "realign": doc?}). The ONLY documents the API may write, by construction."""
    if method != "POST":
        return 405, "POST only", {}
    if (headers.get(CONTROL_HEADER) or headers.get(CONTROL_HEADER.lower())) != "1":
        return 400, f"missing the {CONTROL_HEADER} header", {}
    if not is_admin(principal, admin_ids):
        return 403, "not allowed", {}
    body = body if isinstance(body, dict) else {}
    action = body.get("action")
    if action not in CONTROL_ACTIONS:
        return 400, "unknown action", {}
    phrase, new_state = CONTROL_ACTIONS[action]
    if (body.get("confirm") or "") != phrase:
        return 400, f'type "{phrase}" to confirm', {}
    reason = (body.get("reason") or "").strip()
    if not reason:
        return 400, "a reason is required", {}
    if len(reason) > MAX_REASON:
        return 400, f"the reason must be at most {MAX_REASON} characters", {}
    cur = (current or {}).get("state") or "active"
    if action == "realign" and cur != "active":
        return 409, f"re-align needs the engine active (it is {cur}); resume first", {}
    ts = (now or datetime.now(timezone.utc)).isoformat()
    who = f"{principal.get('userDetails') or ''} ({principal.get('userId')})".strip()
    writes: dict = {}
    if new_state is not None:
        writes["kill_switch"] = {"state": new_state, "changed_at": ts, "changed_by": who, "reason": reason}
    if action in ("resume", "realign"):
        writes["realign"] = {"requested_at": ts, "requested_by": who, "reason": reason + (" (resume)" if action == "resume" else "")}
    return 200, None, writes


def controls_view(current: dict | None, log: list[dict], admin: bool, realign: dict | None) -> dict:
    cur = current or {}
    st = cur.get("state") or ("paused" if cur.get("tripped") else "active")
    return {"state": st, "changed_at": cur.get("changed_at"), "changed_by": cur.get("changed_by"), "reason": cur.get("reason"),
            "realign_pending": bool(realign and realign.get("requested_at") and not realign.get("cleared_at")),
            "log": list(reversed(log[-10:])), "is_admin": admin, "actions": {k: v[0] for k, v in CONTROL_ACTIONS.items()}}
