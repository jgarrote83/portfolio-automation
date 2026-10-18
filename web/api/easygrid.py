"""EasyGrid (the C5 paper account) — read-only portal views. Pure functions over the C5 engine's own blobs; values are passed through
verbatim, with only arithmetic the page needs (an index to 100, a return between two of the engine's own equity readings).

Blobs (written by func-pfauto's `c5_tick`, src/c5/handler.py):
  c5-state/<yyyymm>.json    one per monthly decision: inputs, decision, orders, fills, period return vs SPY
  c5-state/health.json      the last tick: last_run, enabled, kill_switch, reconcile, error, next_decision_date
  c5-state/dry-run.json     the decision and intended orders for the latest month-end (refreshed while disabled)
  c5-state/preview.json     "if the month ended today" (a preview, never a decision)
  c5-ledger/ledger.json     the holdings the engine expects, and since when
  c5-equity-series/series.json   {date, equity, cash, holding} after each close
Core's line and SPY's come from `performance/equity-series.json` (`date`, `equity`, `spy_close`).
"""
from __future__ import annotations

PREVIEW_LABEL = "If the month ended today (preview, not a decision)."


def executed(states: list[dict]) -> list[dict]:
    return [s for s in sorted(states, key=lambda s: s.get("month", "")) if s.get("status") == "executed"]


def first_fill(states: list[dict]) -> dict | None:
    ex = executed(states)
    return ex[0] if ex else None


def summary(states: list[dict], series: list[dict], health: dict | None, ledger: dict | None, dry_run: dict | None) -> dict:
    health = health or {}
    ff = first_fill(states)
    out = {"started": ff is not None, "health": {k: health.get(k) for k in ("last_run", "enabled", "kill_switch", "reconcile", "error")},
           "next_decision_date": health.get("next_decision_date")}
    if ff is None:
        out.update({"status": "Not started", "dry_run": dry_run})
        return out
    last = series[-1] if series else None
    base = ff.get("equity_after_close")
    out.update({"status": "Running", "first_fill_date": ff.get("submitted_on"), "equity": last.get("equity") if last else None,
                "as_of": last.get("date") if last else None,
                "return_since_first_fill": (last["equity"] / base - 1.0) if (last and base) else None,
                "holding": ", ".join(sorted((ledger or {}).get("holdings", {}))) or None, "holding_since": (ledger or {}).get("as_of")})
    return out


def series_indexed(states: list[dict], c5_series: list[dict], perf: list[dict]) -> dict:
    """EasyGrid, Core and SPY, each indexed to 100 at the first fill's close (the same dates for all three)."""
    ff = first_fill(states)
    if ff is None:
        return {"started": False, "points": [], "markers": []}
    start = ff["submitted_on"]
    core = {p.get("date"): p for p in perf or []}
    pts = [p for p in c5_series if p.get("date", "") >= start]
    if not pts:
        return {"started": True, "points": [], "markers": []}
    b_eg = pts[0]["equity"]
    b_core = (core.get(start) or {}).get("equity")
    b_spy = (core.get(start) or {}).get("spy_close")
    out = []
    for p in pts:
        c = core.get(p["date"]) or {}
        out.append({"date": p["date"], "easygrid": round(p["equity"] / b_eg * 100, 3) if b_eg else None,
                    "core": round(c["equity"] / b_core * 100, 3) if (b_core and c.get("equity")) else None,
                    "spy": round(c["spy_close"] / b_spy * 100, 3) if (b_spy and c.get("spy_close")) else None, "holding": p.get("holding")})
    markers, prev = [], None
    for s in executed(states):
        tgt = (s.get("decision") or {}).get("target")
        if tgt != prev:
            markers.append({"date": s.get("submitted_on"), "holding": tgt, "decision_date": s.get("decision_date")})
        prev = tgt
    return {"started": True, "base_date": start, "points": out, "markers": markers}


def decisions(states: list[dict]) -> list[dict]:
    out = []
    for s in sorted(states, key=lambda s: s.get("month", ""), reverse=True):
        d = s.get("decision") or {}
        out.append({"month": s.get("month"), "decision_date": s.get("decision_date"), "execution_date": s.get("execution_date"),
                    "status": s.get("status"), "first_enable": s.get("first_enable"), "late": s.get("late"),
                    "trend": d.get("trend"), "credit": d.get("credit"), "holding": d.get("holding"), "target": d.get("target"),
                    "orders": s.get("orders") or [], "fills": s.get("fills") or [], "period": s.get("period")})
    return out


def criteria(states: list[dict], preview: dict | None, dry_run: dict | None) -> dict:
    decided = [s for s in sorted(states, key=lambda s: s.get("month", "")) if s.get("decision")]
    last = decided[-1]["decision"] if decided else ((dry_run or {}).get("decision"))
    source = "decision" if decided else ("dry_run" if last else None)
    pv = dict(preview) if preview else None
    if pv is not None:
        pv["note"] = PREVIEW_LABEL                      # the label is always present, whatever the blob says
    return {"last_decision": last, "last_decision_source": source, "preview": pv, "preview_label": PREVIEW_LABEL,
            "rule": "Both ✓ → SSO (2x S&P); one ✓ → SPY; neither → T-bills (BIL)."}
