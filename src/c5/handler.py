"""C5 / "EasyGrid" engine: the only module of `c5` that does I/O. Spec: docs/specs/C5_Engine_v1.0.md.

INDEPENDENCE (non-negotiable; enforced by tests/test_c5_engine.py's source scan): C5 trades ONLY its own paper account, with ONLY the
`C5_ALPACA_KEY_ID` / `C5_ALPACA_SECRET` app settings (Key Vault references to `Alpaca-Easygrid-API-Key` / `-Secret`). It never reads or
writes Core's account, positions, orders, ledgers, blobs or settings; the only Key Vault secret it reads itself is `FredApiKey`, by name.

Every 15 minutes (`c5_tick`, gated by Alpaca's clock and calendar; no hard-coded hour):
  1. read the two control files (`c5-state/kill-switch.json`, `c5-state/realign-request.json`) and log any change to
     `c5-state/kill-switch-log.jsonl`;
  2. reconcile the account's positions with `c5-ledger/ledger.json`, and check the account holds the last target (a wrong holding is an
     ERROR "C5 HOLDING MISMATCH" in App Insights, once a day, and a red "Holding mismatch" in the portal);
  3. after the settled close of the LAST session of a month: decide (c5.decision / c5.signals) and write `c5-state/<yyyymm>.json`
     (recorded in every control state; executed only while `active`);
  4. on the next session, while the market is open and before the MOC cutoff: submit market-on-close orders, sells first;
  5. after that session's settled close: verify the account holds the target, update the ledger, record fills and slippage;
  6. after every settled close: append `c5-equity-series/series.json` and write `c5-state/preview.json` (a preview, never a decision).

Control states (`kill-switch.json` `{"state", "changed_at", "changed_by", "reason"}`): `active` trades normally; `paused` places no
orders and keeps the holding; `paused_to_tbills` sells to BIL at the next close (once), then places nothing. A re-align request
(`realign-request.json`) is honoured at the next close only while `active`: it aligns to the latest month-end decision exactly as on first
enable, then is cleared. Resuming from the portal writes `active` AND a re-align request.
First enable (no state at all, `active`): decide for the most recent completed month-end and execute at the next close.
Fail closed: any missing input -> no trade, an ERROR log, holdings kept. `C5_ENABLED` != "true" -> no trading at all; the tick only
refreshes the dry run (which shows the control state's effect), the preview, the control log and the health record.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from c5 import decision as dec
from c5 import orders as ords
from c5 import schedule as sch

logger = logging.getLogger("c5")

STATE, LEDGER, SERIES = "c5-state", "c5-ledger", "c5-equity-series"
CONTROL, REALIGN, CONTROL_LOG, ALERTS = "kill-switch.json", "realign-request.json", "kill-switch-log.jsonl", "alerts.json"
CONTROL_STATES = ("active", "paused", "paused_to_tbills")
FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"
HISTORY_DAYS = 420                                # covers 10 month-ends and 126 sessions with room
TERMINAL = ("executed", "verify_failed", "superseded")


@dataclass
class Deps:
    alpaca: Any                                   # an AlpacaClient built from the C5 keys
    fred: Callable[[date, date], list[tuple[date, float]]]
    read: Callable[[str, str], Any]
    write: Callable[[str, str, Any], None]
    list_names: Callable[[str], list[str]]
    now: Callable[[], datetime]
    enabled: bool
    append: Callable[[str, str, dict], None] = None
    read_lines: Callable[[str, str], list[dict]] = None


def enabled() -> bool:
    return os.environ.get("C5_ENABLED", "false").strip().lower() == "true"


def default_deps() -> Deps:
    from shared import storage
    from shared.clients.alpaca import AlpacaClient
    key, secret = os.environ.get("C5_ALPACA_KEY_ID"), os.environ.get("C5_ALPACA_SECRET")
    if not key or not secret:
        raise dec.MissingInput("C5_ALPACA_KEY_ID / C5_ALPACA_SECRET are not set")
    return Deps(alpaca=AlpacaClient(key, secret), fred=_fred_fetcher(), read=storage.read_json_blob, write=storage.write_json_blob,
                list_names=storage.list_blob_names, now=lambda: datetime.now(sch.ET), enabled=enabled(),
                append=storage.append_jsonl_blob, read_lines=storage.read_jsonl_blob)


def _fred_fetcher() -> Callable[[date, date], list[tuple[date, float]]]:
    """BAA10Y from FRED with the collector's FredApiKey (read by name only). Errors never carry the URL (it holds the key)."""
    def fetch(start: date, end: date) -> list[tuple[date, float]]:
        import requests
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        key = SecretClient(vault_url=os.environ["KEY_VAULT_URI"], credential=DefaultAzureCredential()).get_secret("FredApiKey").value
        try:
            r = requests.get(FRED_OBS_URL, params={"series_id": "BAA10Y", "api_key": key, "file_type": "json",
                                                   "observation_start": start.isoformat(), "observation_end": end.isoformat()}, timeout=30)
        except requests.RequestException as e:
            raise dec.MissingInput(f"FRED BAA10Y request failed ({type(e).__name__})") from None
        if not r.ok:
            raise dec.MissingInput(f"FRED BAA10Y request failed (HTTP {r.status_code})")
        out = []
        for o in r.json().get("observations", []):
            try:
                out.append((date.fromisoformat(o["date"]), float(o["value"])))
            except (KeyError, ValueError):
                continue                          # FRED writes "." for a missing day
        return out
    return fetch


# ================================================================================================ control
def read_control(d: Deps) -> dict:
    """The control state. Absent -> active. The legacy `{"tripped": true}` -> paused. An unknown value -> paused (fail closed)."""
    raw = d.read(STATE, CONTROL) or {}
    if not isinstance(raw, dict):
        return {"state": "paused", "invalid": True}
    if "state" not in raw:
        return {"state": "paused" if raw.get("tripped") else "active", **{k: raw.get(k) for k in ("changed_at", "changed_by", "reason")}}
    if raw.get("state") not in CONTROL_STATES:
        return {**raw, "state": "paused", "invalid": True}
    return raw


def read_realign(d: Deps) -> dict | None:
    r = d.read(STATE, REALIGN)
    return r if isinstance(r, dict) and r.get("requested_at") and not r.get("cleared_at") else None


def _log(d: Deps, record: dict) -> None:
    if d.append is None:
        return
    try:
        d.append(STATE, CONTROL_LOG, record)
    except Exception:  # noqa: BLE001
        logger.exception("C5 control-log append failed")


def _sync_control_log(d: Deps, control: dict, realign: dict | None, now: datetime) -> None:
    """Append every control change (state or re-align request) to the log exactly once, with who, when and why."""
    seen = {e.get("key") for e in (d.read_lines(STATE, CONTROL_LOG) if d.read_lines else [])}
    if control.get("changed_at"):
        k = f"state:{control['state']}:{control['changed_at']}"
        if k not in seen:
            _log(d, {"key": k, "logged_at": now.isoformat(), "kind": "state", "state": control["state"],
                     "changed_at": control.get("changed_at"), "changed_by": control.get("changed_by"), "reason": control.get("reason")})
    if realign:
        k = f"realign:{realign['requested_at']}"
        if k not in seen:
            _log(d, {"key": k, "logged_at": now.isoformat(), "kind": "realign_request", "changed_at": realign["requested_at"],
                     "changed_by": realign.get("requested_by"), "reason": realign.get("reason")})


def _compact(ts: str | None) -> str:
    return "".join(ch for ch in (ts or "") if ch.isdigit())[:12] or "000000000000"


# ================================================================================================ inputs
def _bar_day(b: dict) -> date:
    return datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(sch.ET).date()


def load_inputs(d: Deps, now: datetime) -> tuple[list[sch.Session], dict[date, float], dict[str, dict[date, float]], list[tuple[date, float]]]:
    """(sessions, SPY adjusted closes, raw closes of SSO/SPY/BIL, BAA10Y observations). Any failure -> MissingInput."""
    today = now.date()
    start = (today - timedelta(days=HISTORY_DAYS)).isoformat()
    try:
        ss = sch.sessions(d.alpaca.get_calendar(start, (today + timedelta(days=40)).isoformat()))
        end = (now - timedelta(minutes=16)).isoformat()     # the free plan serves SIP bars older than 15 minutes
        adj = d.alpaca.get_bars("SPY", "1Day", start, end, feed="sip", adjustment="all").get("SPY") or []
        raw = d.alpaca.get_bars(sorted(dec.INSTRUMENTS), "1Day", start, end, feed="sip", adjustment="raw")
    except Exception as e:  # noqa: BLE001
        raise dec.MissingInput(f"Alpaca calendar or SPY bars failed: {type(e).__name__}") from None
    if not ss or not adj:
        raise dec.MissingInput("Alpaca calendar or SPY bars came back empty")
    try:
        baa = d.fred(today - timedelta(days=HISTORY_DAYS), today)
    except dec.MissingInput:
        raise
    except Exception as e:  # noqa: BLE001
        raise dec.MissingInput(f"FRED BAA10Y fetch failed: {type(e).__name__}") from None
    if not baa:
        raise dec.MissingInput("FRED BAA10Y came back empty")
    closes = {_bar_day(b): float(b["c"]) for b in adj}
    raws = {s: {_bar_day(b): float(b["c"]) for b in (raw.get(s) or [])} for s in dec.INSTRUMENTS}
    return ss, closes, raws, baa


def _data_status(err: str | None) -> dict:
    if not err:
        return {"spy": True, "credit": True}
    credit_bad = "BAA10Y" in err or "FRED" in err or "credit" in err
    spy_bad = "SPY" in err or "bars" in err or "calendar" in err or "month-end" in err
    return {"spy": not spy_bad, "credit": not credit_bad}


def positions_of(d: Deps) -> dict[str, float]:
    return {str(p["symbol"]).upper(): float(p["qty"]) for p in d.alpaca.list_positions()}


def _is_state_name(n: str) -> bool:
    stem = n[:-5] if n.endswith(".json") else ""
    return (len(stem) == 6 and stem.isdigit()) or (stem[:2] in ("PT", "RA") and stem[2:].isdigit())


def _states(d: Deps) -> list[dict]:
    docs = [x for x in (d.read(STATE, n) for n in sorted(d.list_names(STATE)) if _is_state_name(n)) if isinstance(x, dict)]
    return sorted(docs, key=lambda s: (s.get("created_at") or "", s.get("month", "")))


def _write_state(d: Deps, st: dict) -> None:
    d.write(STATE, f"{st['month']}.json", st)


def _price(raws: dict[str, dict[date, float]], sym: str) -> float:
    px = raws.get(sym) or {}
    if not px:
        raise dec.MissingInput(f"no recent close for {sym}")
    return px[max(px)]


def _new_state(tag: str, kind: str, decision_day: date | None, execution: sch.Session, decision: dict, now: datetime, **extra) -> dict:
    return {"month": tag, "kind": kind, "status": "decided", "decision_date": decision_day.isoformat() if decision_day else None,
            "execution_date": execution.day.isoformat(), "decision": decision, "orders": [], "fills": [], "created_at": now.isoformat(), **extra}


def _exec_session(ss, today, now) -> sch.Session:
    return today if (today and now < sch.moc_cutoff(today)) else sch.next_session(ss, now.date())


def _may_execute(st: dict, control: str) -> bool:
    if st.get("kind") == "to_tbills":
        return control == "paused_to_tbills"
    return control == "active"


# ================================================================================================= tick
def tick(d: Deps | None = None) -> dict:
    """One 15-minute tick. Returns the health record it wrote. Never raises."""
    d = d or default_deps()
    now = d.now()
    health: dict = {"last_run": now.isoformat(), "enabled": d.enabled, "control": "active", "kill_switch": False, "reconcile": "not_checked",
                    "holding_mismatch": None, "data": None, "error": None, "actions": []}
    try:
        control = read_control(d)
        realign = read_realign(d)
        cstate = control["state"]
        health.update({"control": cstate, "kill_switch": cstate != "active", "control_changed_at": control.get("changed_at"),
                       "realign_pending": bool(realign)})
        if control.get("invalid"):
            health["error"] = "kill-switch.json is unreadable or names an unknown state: treated as paused"
            logger.error("C5 control file invalid; treated as paused")
        _sync_control_log(d, control, realign, now)
        if not d.enabled and _dry_run_fresh(d, now, cstate, realign):
            health["actions"].append("disabled: dry run still fresh, nothing to do")
            return _finish(d, health)
        clock = d.alpaca.get_clock()
        ss, closes, raws, baa = load_inputs(d, now)
        health["data"] = _data_status(None)
        today = sch.session_on(ss, now.date())
        positions = positions_of(d)
        nxt = next((x for x in ss if x.day >= now.date() and not sch.settled(x, now) and sch.next_session(ss, x.day) and sch.is_month_end(ss, x.day)), None)
        health["next_decision_date"] = nxt.day.isoformat() if nxt else None
        if not d.enabled:
            _refresh_dry_run(d, now, ss, closes, raws, baa, positions, control, realign, health)
            return _finish(d, health)
        ledger = d.read(LEDGER, "ledger.json") or {}
        mism = ords.reconcile(positions, ledger)
        health["reconcile"] = "ok" if not mism else {"mismatches": mism}
        if mism:
            logger.error("C5 reconcile mismatch: %s", mism)
        states = _states(d)
        monthly = [s for s in states if s.get("kind", "monthly") == "monthly"]

        # first enable (only while active) / routine month-end decision (recorded in every control state)
        if not states and cstate == "active":
            me = sch.last_completed_month_end(ss, now)
            if me is None:
                raise dec.MissingInput("no completed month-end in the calendar window")
            ex = _exec_session(ss, today, now)
            st = _new_state(sch.yyyymm(me.day), "monthly", me.day, ex, dec.decide(me.day, closes, ss, baa), now, first_enable=True)
            _write_state(d, st)
            states.append(st)
            health["actions"].append(f"first-enable decision for {me.day}: {st['decision']['target']}, executing {ex.day}")
        elif today and sch.is_month_end(ss, today.day) and sch.settled(today, now) and not any(s["month"] == sch.yyyymm(today.day) for s in monthly):
            st = _new_state(sch.yyyymm(today.day), "monthly", today.day, sch.next_session(ss, today.day), dec.decide(today.day, closes, ss, baa), now)
            _write_state(d, st)
            states.append(st)
            health["actions"].append(f"decision for {today.day}: {st['decision']['target']}" + ("" if cstate == "active" else f" (recorded; {cstate}: not executed)"))

        # control actions
        if cstate == "paused_to_tbills":
            tag = "PT" + _compact(control.get("changed_at"))
            if not any(s["month"] == tag for s in states):
                ex = _exec_session(ss, today, now)
                st = _new_state(tag, "to_tbills", None, ex, {"target": "BIL", "holding": "TBILLS", "kind": "control"}, now,
                                reason=control.get("reason"), changed_by=control.get("changed_by"))
                if ords.holds_target(positions, "BIL"):
                    st["status"] = "executed"
                    st["note"] = "already in T-bills"
                _write_state(d, st)
                states.append(st)
                _log(d, {"key": f"engine:{tag}", "logged_at": now.isoformat(), "kind": "engine", "action": "pause_to_tbills scheduled",
                         "execution_date": st["execution_date"], "status": st["status"]})
                health["actions"].append(f"pause to T-bills: {st['status']} for {st['execution_date']}")
        elif realign and cstate == "active":
            me = sch.last_completed_month_end(ss, now)
            if me is None:
                raise dec.MissingInput("no completed month-end in the calendar window")
            tag = "RA" + _compact(realign["requested_at"])
            ex = _exec_session(ss, today, now)
            st = _new_state(tag, "realign", me.day, ex, dec.decide(me.day, closes, ss, baa), now,
                            reason=realign.get("reason"), changed_by=realign.get("requested_by"))
            for s in states:
                if s.get("status") == "decided":
                    s["status"], s["superseded_by"] = "superseded", tag
                    _write_state(d, s)
            _write_state(d, st)
            states.append(st)
            d.write(STATE, REALIGN, {"cleared_at": now.isoformat(), "handled_by": tag, "last_request": realign})
            _log(d, {"key": f"engine:{tag}", "logged_at": now.isoformat(), "kind": "engine", "action": "realign honoured",
                     "target": st["decision"]["target"], "execution_date": st["execution_date"]})
            health["actions"].append(f"re-align to the {me.day} decision ({st['decision']['target']}) at {ex.day}'s close")
            health["realign_pending"] = False
        elif realign:
            health["actions"].append(f"re-align request ignored while {cstate}")

        # execution
        live = [s for s in states if s.get("status") in ("decided", "submitted")]
        pending = live[-1] if live else None
        if (pending and pending["status"] == "decided" and today and clock.get("is_open") and today.open <= now < sch.moc_cutoff(today)
                and today.day >= date.fromisoformat(pending["execution_date"])):
            if _may_execute(pending, cstate):
                _submit(d, pending, positions, raws, today, health)
            else:
                health["actions"].append(f"{cstate}: no orders")

        # after the settled close
        if today and sch.settled(today, now):
            if pending and pending["status"] == "submitted" and pending.get("submitted_on") == today.day.isoformat():
                _verify(d, pending, states, raws, closes, today, health)
            _after_close(d, today, ss, closes, baa, health)
        _holding_check(d, states, positions_of(d) if (today and sch.settled(today, now)) else positions, now, health)
    except dec.MissingInput as e:
        health["error"] = f"missing input: {e}"
        health["data"] = _data_status(str(e))
        logger.error("C5 fail-closed (no trade, holdings kept): %s", e)
    except Exception as e:  # noqa: BLE001
        health["error"] = f"{type(e).__name__}: {e}"
        logger.exception("C5 tick failed (no trade, holdings kept)")
    return _finish(d, health)


def _finish(d: Deps, health: dict) -> dict:
    try:
        d.write(STATE, "health.json", health)
    except Exception:  # noqa: BLE001
        logger.exception("C5 health write failed")
    return health


def _holding_check(d: Deps, states: list[dict], positions: dict, now: datetime, health: dict) -> None:
    """The account must hold the target of the last acted-on state. Skipped while an order set is submitted but not yet verified."""
    if any(s.get("status") == "submitted" for s in states):
        return
    acted = [s for s in states if s.get("status") in ("executed", "verify_failed")]
    if not acted:
        return
    expected = (acted[-1].get("decision") or {}).get("target")
    if expected and not ords.holds_target(positions, expected):
        actual = {s: q for s, q in positions.items() if q}
        health["holding_mismatch"] = {"expected": expected, "actual": actual}
        alerts = d.read(STATE, ALERTS) or {}
        if alerts.get("last_holding_mismatch") != now.date().isoformat():
            logger.error("C5 HOLDING MISMATCH: expected %s, account holds %s", expected, actual)
            d.write(STATE, ALERTS, {**alerts, "last_holding_mismatch": now.date().isoformat()})


def _submit(d: Deps, st: dict, positions: dict, raws: dict, today: sch.Session, health: dict) -> None:
    target = st["decision"]["target"]
    equity = float(d.alpaca.get_account()["equity"])
    planned = ords.plan(positions, target, equity, _price(raws, target), st["month"])
    existing = {o.get("client_order_id") for o in d.alpaca.list_orders(status="all")}
    placed = []
    for o in planned:                                         # sells first (plan lists them first)
        if o["client_order_id"] in existing:
            placed.append({**o, "status": "already_submitted"})
            continue
        try:
            r = d.alpaca.submit_order(o["symbol"], o["qty"], o["side"], order_type="market", time_in_force="cls", client_order_id=o["client_order_id"])
            placed.append({**o, "order_id": r.get("id"), "status": r.get("status")})
        except Exception as e:  # noqa: BLE001
            placed.append({**o, "status": f"error: {type(e).__name__}"})
            logger.error("C5 order %s failed: %s", o["client_order_id"], type(e).__name__)
    st.update({"status": "submitted", "submitted_on": today.day.isoformat(), "equity_at_submit": equity, "orders": placed,
               "late": today.day > date.fromisoformat(st["execution_date"])})
    _write_state(d, st)
    health["actions"].append(f"submitted {len(placed)} MOC orders for {st['month']} -> {target}")


def _verify(d: Deps, st: dict, states: list[dict], raws: dict, closes: dict, today: sch.Session, health: dict) -> None:
    positions = positions_of(d)
    target = st["decision"]["target"]
    by_id = {o.get("client_order_id"): o for o in d.alpaca.list_orders(status="all")}
    fills = []
    for o in st.get("orders", []):
        b = by_id.get(o["client_order_id"]) or {}
        px = float(b["filled_avg_price"]) if b.get("filled_avg_price") else None
        close = (raws.get(o["symbol"]) or {}).get(today.day)
        slip = None if (px is None or not close) else ((px / close - 1.0) * 1e4 * (1 if o["side"] == "buy" else -1))
        fills.append({"client_order_id": o["client_order_id"], "symbol": o["symbol"], "side": o["side"], "status": b.get("status"),
                      "filled_qty": float(b.get("filled_qty") or 0), "avg_price": px, "close": close, "slippage_bps_vs_close": slip,
                      "fill_minus_close": None if (px is None or not close) else px - close})
    acct = d.alpaca.get_account()
    st["fills"], st["equity_after_close"] = fills, float(acct["equity"])
    st["spy_close_at_execution"] = closes.get(today.day)
    if ords.holds_target(positions, target):
        st["status"] = "executed"
        d.write(LEDGER, "ledger.json", {"holdings": positions, "as_of": today.day.isoformat(), "month": st["month"]})
        health["actions"].append(f"verified: holds {target}")
        prev = next((s for s in reversed(states) if s.get("status") == "executed" and s is not st and (s.get("created_at") or "") <= (st.get("created_at") or "")), None)
        if prev and prev.get("equity_after_close") and prev.get("spy_close_at_execution") and closes.get(today.day):
            prev["period"] = {"from": prev["submitted_on"], "to": today.day.isoformat(),
                              "return": st["equity_after_close"] / prev["equity_after_close"] - 1.0,
                              "spy_return": closes[today.day] / prev["spy_close_at_execution"] - 1.0}
            _write_state(d, prev)
    else:
        st["status"] = "verify_failed"
        logger.error("C5 HOLDING MISMATCH after the close: account holds %s, target %s", positions, target)
        health["actions"].append("verification FAILED")
    _write_state(d, st)


def _after_close(d: Deps, today: sch.Session, ss, closes, baa, health: dict) -> None:
    series = d.read(SERIES, "series.json") or []
    if not any(p.get("date") == today.day.isoformat() for p in series):
        acct = d.alpaca.get_account()
        pos = positions_of(d)
        series.append({"date": today.day.isoformat(), "equity": float(acct["equity"]), "cash": float(acct["cash"]),
                       "holding": ",".join(sorted(s for s, q in pos.items() if q)) or "cash", "spy_close": closes.get(today.day)})
        d.write(SERIES, "series.json", series)
    try:
        d.write(STATE, "preview.json", dec.preview(today.day, closes, ss, baa))
    except dec.MissingInput as e:
        health["actions"].append(f"preview unavailable: {e}")


# =============================================================================================== dry run
def dry_run(d: Deps | None = None) -> dict:
    """Inputs, decision and intended orders for the most recent completed month-end, against the C5 account now, under the current
    control state. Never trades."""
    d = d or default_deps()
    now = d.now()
    ss, closes, raws, baa = load_inputs(d, now)
    return _dry(d, now, ss, closes, raws, baa, positions_of(d), read_control(d), read_realign(d))


def _dry(d, now, ss, closes, raws, baa, positions, control, realign) -> dict:
    me = sch.last_completed_month_end(ss, now)
    if me is None:
        raise dec.MissingInput("no completed month-end in the calendar window")
    decision = dec.decide(me.day, closes, ss, baa)
    equity = float(d.alpaca.get_account()["equity"])
    cstate = control["state"]
    if cstate == "paused":
        intended, effect = [], "paused: no orders; the holding is kept"
    elif cstate == "paused_to_tbills":
        intended = [] if ords.holds_target(positions, "BIL") else ords.plan(positions, "BIL", equity, _price(raws, "BIL"), "PT" + _compact(control.get("changed_at")))
        effect = "paused to T-bills: sell to BIL at the next close, then no orders"
    else:
        intended = ords.plan(positions, decision["target"], equity, _price(raws, decision["target"]), sch.yyyymm(me.day))
        effect = "active" + (": a re-align to this decision is pending for the next close" if realign else "")
    return {"as_of": now.isoformat(), "enabled": d.enabled, "control": cstate, "realign_pending": bool(realign), "effect": effect,
            "decision": decision, "equity": equity, "positions": positions, "intended_orders": intended,
            "note": "dry run: nothing was submitted"}


def _dry_run_fresh(d: Deps, now: datetime, cstate: str, realign: dict | None) -> bool:
    last = d.read(STATE, "dry-run.json") or {}
    if not last.get("as_of") or last.get("control") != cstate or bool(last.get("realign_pending")) != bool(realign):
        return False                                  # a control change takes effect within one tick
    return now - datetime.fromisoformat(last["as_of"]) < timedelta(hours=1)


def _refresh_dry_run(d: Deps, now, ss, closes, raws, baa, positions, control, realign, health: dict) -> None:
    d.write(STATE, "dry-run.json", _dry(d, now, ss, closes, raws, baa, positions, control, realign))
    today = sch.session_on(ss, now.date())
    if today and sch.settled(today, now):
        try:
            d.write(STATE, "preview.json", dec.preview(today.day, closes, ss, baa))
        except dec.MissingInput as e:
            health["actions"].append(f"preview unavailable: {e}")
    health["actions"].append(f"disabled: dry run refreshed ({control['state']}), no trading")
