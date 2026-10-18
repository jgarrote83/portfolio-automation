"""C5 / "EasyGrid" engine: the only module of `c5` that does I/O. Spec: docs/specs/C5_Engine_v1.0.md.

INDEPENDENCE (non-negotiable; enforced by tests/test_c5_engine.py's source scan): C5 trades ONLY its own paper account, with ONLY the
`C5_ALPACA_KEY_ID` / `C5_ALPACA_SECRET` app settings (Key Vault references to `Alpaca-Easygrid-API-Key` / `-Secret`). It never reads or
writes Core's account, positions, orders, ledgers, blobs or settings; the only Key Vault secret it reads itself is `FredApiKey`, by name.

Every 15 minutes (`c5_tick`, gated by Alpaca's clock and calendar; no hard-coded hour):
  1. reconcile the account's positions with `c5-ledger/ledger.json` (alert on any mismatch);
  2. after the settled close of the LAST session of a month: decide (c5.decision / c5.signals) and write `c5-state/<yyyymm>.json`;
  3. on the next session, while the market is open and before the MOC cutoff: submit market-on-close orders, sells first;
  4. after that session's settled close: verify the account holds the target, update the ledger, record fills and slippage;
  5. after every settled close: append `c5-equity-series/series.json` and write `c5-state/preview.json` (a preview, never a decision).
First enable (no state at all): decide for the most recent completed month-end and execute at the next session's close.
Fail closed: any missing input -> no trade, an ERROR log, holdings kept. Kill switch: `c5-state/kill-switch.json` {"tripped": true}.
`C5_ENABLED` != "true" -> no trading at all; the tick only refreshes the dry run, the preview and the health record.
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
KILL_SWITCH = "kill-switch.json"
FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"
HISTORY_DAYS = 420                                # covers 10 month-ends and 126 sessions with room


@dataclass
class Deps:
    alpaca: Any                                   # an AlpacaClient built from the C5 keys
    fred: Callable[[date, date], list[tuple[date, float]]]
    read: Callable[[str, str], Any]
    write: Callable[[str, str, Any], None]
    list_names: Callable[[str], list[str]]
    now: Callable[[], datetime]
    enabled: bool


def enabled() -> bool:
    return os.environ.get("C5_ENABLED", "false").strip().lower() == "true"


def default_deps() -> Deps:
    from shared import storage
    from shared.clients.alpaca import AlpacaClient
    key, secret = os.environ.get("C5_ALPACA_KEY_ID"), os.environ.get("C5_ALPACA_SECRET")
    if not key or not secret:
        raise dec.MissingInput("C5_ALPACA_KEY_ID / C5_ALPACA_SECRET are not set")
    return Deps(alpaca=AlpacaClient(key, secret), fred=_fred_fetcher(), read=storage.read_json_blob, write=storage.write_json_blob,
                list_names=storage.list_blob_names, now=lambda: datetime.now(sch.ET), enabled=enabled())


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


# ================================================================================================ inputs
def _bar_day(b: dict) -> date:
    return datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(sch.ET).date()


def load_inputs(d: Deps, now: datetime) -> tuple[list[sch.Session], dict[date, float], dict[str, dict[date, float]], list[tuple[date, float]]]:
    """(sessions, SPY adjusted closes, raw closes of SSO/SPY/BIL, BAA10Y observations). Any failure -> MissingInput."""
    today = now.date()
    try:
        ss = sch.sessions(d.alpaca.get_calendar((today - timedelta(days=HISTORY_DAYS)).isoformat(), (today + timedelta(days=40)).isoformat()))
        end = (now - timedelta(minutes=16)).isoformat()     # the free plan serves SIP bars older than 15 minutes
        start = (today - timedelta(days=HISTORY_DAYS)).isoformat()
        adj = d.alpaca.get_bars("SPY", "1Day", start, end, feed="sip", adjustment="all").get("SPY") or []
        raw = d.alpaca.get_bars(sorted(dec.INSTRUMENTS), "1Day", start, end, feed="sip", adjustment="raw")
        baa = d.fred(today - timedelta(days=HISTORY_DAYS), today)
    except dec.MissingInput:
        raise
    except Exception as e:  # noqa: BLE001
        raise dec.MissingInput(f"input fetch failed: {type(e).__name__}") from None
    if not ss or not adj or not baa:
        raise dec.MissingInput("calendar, SPY bars or BAA10Y came back empty")
    closes = {_bar_day(b): float(b["c"]) for b in adj}
    raws = {s: {_bar_day(b): float(b["c"]) for b in (raw.get(s) or [])} for s in dec.INSTRUMENTS}
    return ss, closes, raws, baa


def positions_of(d: Deps) -> dict[str, float]:
    return {str(p["symbol"]).upper(): float(p["qty"]) for p in d.alpaca.list_positions()}


def _states(d: Deps) -> list[dict]:
    names = sorted(n for n in d.list_names(STATE) if len(n) == 11 and n[:6].isdigit() and n.endswith(".json"))
    return [x for x in (d.read(STATE, n) for n in names) if isinstance(x, dict)]


def _write_state(d: Deps, st: dict) -> None:
    d.write(STATE, f"{st['month']}.json", st)


def _price(raws: dict[str, dict[date, float]], sym: str) -> float:
    px = raws.get(sym) or {}
    if not px:
        raise dec.MissingInput(f"no recent close for {sym}")
    return px[max(px)]


def _new_state(month_end: sch.Session, execution: sch.Session, decision: dict, first_enable: bool) -> dict:
    return {"month": sch.yyyymm(month_end.day), "status": "decided", "decision_date": month_end.day.isoformat(),
            "execution_date": execution.day.isoformat(), "first_enable": first_enable, "decision": decision, "orders": [], "fills": []}


# ================================================================================================= tick
def tick(d: Deps | None = None) -> dict:
    """One 15-minute tick. Returns the health record it wrote. Never raises."""
    d = d or default_deps()
    now = d.now()
    health: dict = {"last_run": now.isoformat(), "enabled": d.enabled, "kill_switch": False, "reconcile": "not_checked", "error": None, "actions": []}
    try:
        kill = d.read(STATE, KILL_SWITCH) or {}
        health["kill_switch"] = bool(kill.get("tripped"))
        if not d.enabled and _dry_run_fresh(d, now):
            health["actions"].append("disabled: dry run still fresh, nothing to do")
            return _finish(d, health)
        clock = d.alpaca.get_clock()
        ss, closes, raws, baa = load_inputs(d, now)
        today = sch.session_on(ss, now.date())
        positions = positions_of(d)
        if not d.enabled:
            _refresh_dry_run(d, now, ss, closes, raws, baa, positions, health)
            return _finish(d, health)
        ledger = d.read(LEDGER, "ledger.json") or {}
        mism = ords.reconcile(positions, ledger)
        health["reconcile"] = "ok" if not mism else {"mismatches": mism}
        if mism:
            logger.error("C5 reconcile mismatch: %s", mism)
        states = _states(d)
        pending = next((s for s in reversed(states) if s.get("status") in ("decided", "submitted")), None)

        # first enable / routine month-end decision
        if not states:
            me = sch.last_completed_month_end(ss, now)
            if me is None:
                raise dec.MissingInput("no completed month-end in the calendar window")
            ex = today if (today and now < sch.moc_cutoff(today)) else sch.next_session(ss, now.date())
            st = _new_state(me, ex, dec.decide(me.day, closes, ss, baa), first_enable=True)
            _write_state(d, st)
            pending = st
            health["actions"].append(f"first-enable decision for {me.day}: {st['decision']['target']}, executing {ex.day}")
        elif today and sch.is_month_end(ss, today.day) and sch.settled(today, now) and not any(s["month"] == sch.yyyymm(today.day) for s in states):
            if pending is not None:
                logger.error("C5: a new month-end decision while %s is still %s", pending["month"], pending["status"])
            st = _new_state(today, sch.next_session(ss, today.day), dec.decide(today.day, closes, ss, baa), first_enable=False)
            _write_state(d, st)
            pending = st
            health["actions"].append(f"decision for {today.day}: {st['decision']['target']}")

        # execution: market open, before the MOC cutoff, on or after the execution day
        if (pending and pending["status"] == "decided" and today and clock.get("is_open") and today.open <= now < sch.moc_cutoff(today)
                and today.day >= date.fromisoformat(pending["execution_date"])):
            if health["kill_switch"]:
                health["actions"].append("kill switch tripped: no orders")
            else:
                _submit(d, pending, positions, raws, today, health)

        # after the settled close
        if today and sch.settled(today, now):
            if pending and pending["status"] == "submitted" and pending.get("submitted_on") == today.day.isoformat():
                _verify(d, pending, states, raws, closes, today, health)
            _after_close(d, today, ss, closes, baa, health)
    except dec.MissingInput as e:
        health["error"] = f"missing input: {e}"
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
                      "filled_qty": float(b.get("filled_qty") or 0), "avg_price": px, "close": close, "slippage_bps_vs_close": slip})
    acct = d.alpaca.get_account()
    st["fills"], st["equity_after_close"] = fills, float(acct["equity"])
    st["spy_close_at_execution"] = closes.get(today.day)
    if ords.holds_target(positions, target):
        st["status"] = "executed"
        d.write(LEDGER, "ledger.json", {"holdings": positions, "as_of": today.day.isoformat(), "month": st["month"]})
        health["actions"].append(f"verified: holds {target}")
        prev = next((s for s in reversed(states) if s.get("status") == "executed" and s["month"] < st["month"]), None)
        if prev and prev.get("equity_after_close") and prev.get("spy_close_at_execution") and closes.get(today.day):
            prev["period"] = {"from": prev["submitted_on"], "to": today.day.isoformat(),
                              "return": st["equity_after_close"] / prev["equity_after_close"] - 1.0,
                              "spy_return": closes[today.day] / prev["spy_close_at_execution"] - 1.0}
            _write_state(d, prev)
    else:
        st["status"] = "verify_failed"
        logger.error("C5 verification failed: account holds %s, target %s", positions, target)
        health["actions"].append("verification FAILED")
    _write_state(d, st)


def _after_close(d: Deps, today: sch.Session, ss, closes, baa, health: dict) -> None:
    series = d.read(SERIES, "series.json") or []
    if not any(p.get("date") == today.day.isoformat() for p in series):
        acct = d.alpaca.get_account()
        pos = positions_of(d)
        series.append({"date": today.day.isoformat(), "equity": float(acct["equity"]), "cash": float(acct["cash"]),
                       "holding": ",".join(sorted(s for s, q in pos.items() if q)) or "cash"})
        d.write(SERIES, "series.json", series)
    try:
        d.write(STATE, "preview.json", dec.preview(today.day, closes, ss, baa))
    except dec.MissingInput as e:
        health["actions"].append(f"preview unavailable: {e}")


# =============================================================================================== dry run
def dry_run(d: Deps | None = None) -> dict:
    """Inputs, decision and intended orders for the most recent completed month-end, against the C5 account now. Never trades."""
    d = d or default_deps()
    now = d.now()
    ss, closes, raws, baa = load_inputs(d, now)
    return _dry(d, now, ss, closes, raws, baa, positions_of(d))


def _dry(d, now, ss, closes, raws, baa, positions) -> dict:
    me = sch.last_completed_month_end(ss, now)
    if me is None:
        raise dec.MissingInput("no completed month-end in the calendar window")
    decision = dec.decide(me.day, closes, ss, baa)
    equity = float(d.alpaca.get_account()["equity"])
    target = decision["target"]
    return {"as_of": now.isoformat(), "enabled": d.enabled, "decision": decision, "equity": equity, "positions": positions,
            "intended_orders": ords.plan(positions, target, equity, _price(raws, target), sch.yyyymm(me.day)),
            "note": "dry run: nothing was submitted"}


def _dry_run_fresh(d: Deps, now: datetime) -> bool:
    last = d.read(STATE, "dry-run.json") or {}
    return bool(last.get("as_of")) and now - datetime.fromisoformat(last["as_of"]) < timedelta(hours=1)


def _refresh_dry_run(d: Deps, now, ss, closes, raws, baa, positions, health: dict) -> None:
    d.write(STATE, "dry-run.json", _dry(d, now, ss, closes, raws, baa, positions))
    today = sch.session_on(ss, now.date())
    if today and sch.settled(today, now):
        try:
            d.write(STATE, "preview.json", dec.preview(today.day, closes, ss, baa))
        except dec.MissingInput as e:
            health["actions"].append(f"preview unavailable: {e}")
    health["actions"].append("disabled: dry run refreshed, no trading")
