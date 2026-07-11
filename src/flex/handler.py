"""Intraday catalyst Flex engine — orchestration (thin glue).

Runs every ~15 minutes (gated by ``FLEX_ENABLED`` in function_app + the market
clock here). All decision logic lives in the pure modules (`reconcile`, `entry`,
`exit_state`, `indicators`); this module only fetches broker/market state, calls
them in the mandatory order, issues idempotent orders, and persists state.

Order of operations (mandatory): STEP 0 reconcile FIRST → clock gate → read
quadrant + nominations → fetch bars → manage held names → enter (morning window
only) → persist flex-state / flex-decisions / flex-executions / ledger.

``dry_run=True`` computes and persists the would-do state but places no orders.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from flex.config import load_flex_config
from flex.entry import build_flex_entry
from flex.exit_state import build_flex_exit_state
from flex.ledger import new_entry, read_ledger, write_ledger
from flex.reconcile import reconcile_ledger
from flex.regime import CORE_TICKERS
from shared.clients.alpaca import AlpacaClient
from shared.keyvault import load_secrets
from shared.quadrants import active_quadrant
from shared.storage import (
    append_jsonl_blob,
    read_json_blob,
    read_trades,
    upsert_entity,
    write_json_blob,
)

logger = logging.getLogger(__name__)

_ET = "America/New_York"
_FLEX_COUNT_CAP = 10  # ≤ 10 flex tickers (matches the doctrine)


def run_flex_intraday(date_str: str | None = None, dry_run: bool = False) -> dict:
    cfg = load_flex_config()
    now_et = _now_et()
    today = date_str or now_et.strftime("%Y-%m-%d")
    logger.info("=== flex_intraday %s (dry_run=%s) ===", today, dry_run)

    secrets = load_secrets()
    key, secret = secrets.get("AlpacaApiKey"), secrets.get("AlpacaApiSecret")
    if not key or not secret:
        raise RuntimeError("Alpaca credentials missing from Key Vault")
    client = AlpacaClient(api_key=key, api_secret=secret)

    decisions: dict = {
        "ts": _now_utc_iso(), "date": today, "dry_run": dry_run,
        "reconcile": {}, "entries": [], "exits": [],
        "orders_issued": [], "orders_suppressed": [],
    }
    executions: list[dict] = []
    ledger = read_ledger()

    # ── STEP 0 — reconciliation FIRST (broker truth before any compute) ──────
    try:
        positions = client.list_positions()
        open_orders = client.list_orders("open", 500)
    except Exception:  # noqa: BLE001
        logger.exception("Could not read positions/orders — skipping reconciliation")
        positions, open_orders = [], []

    ledger, exits_to_record, repairs = reconcile_ledger(ledger, positions, open_orders)
    decisions["reconcile"] = {
        "repairs": repairs,
        "closed": [e["symbol"] for e in exits_to_record],
    }
    for ex in exits_to_record:
        _record_trade_history(today, ex["symbol"], "sell", int(ex["entry"].get("qty_current") or 0),
                              status="closed_at_broker", extra={})
    if not dry_run:
        for rep in repairs:
            _apply_repair(client, rep, today, decisions, executions)
    write_ledger(ledger)

    # ── STEP 1 — clock gate (closed → free no-op) ────────────────────────────
    try:
        is_open = bool(client.get_clock().get("is_open"))
    except Exception:  # noqa: BLE001
        logger.exception("Clock read failed — treating market as closed")
        is_open = False
    if not is_open:
        decisions["orders_suppressed"].append({"reason": "market_closed"})
        _persist(today, decisions, quadrant="", ledger=ledger, executions=executions)
        return {"status": "closed", "date": today, "reconcile": decisions["reconcile"]}

    # ── STEP 2 — quadrant (deterministic shared input) + nominations ─────────
    snapshot = read_json_blob("daily-snapshots", f"{today}.json") or {}
    quadrant = active_quadrant(
        ((snapshot.get("growth_axis") or {}).get("direction")),
        ((snapshot.get("inflation_axis") or {}).get("direction")),
    )
    daytrade_syms = _daytrade_ledger_symbols()
    nominations = _flex_nominations(read_trades(today), exclude=daytrade_syms)
    minutes = _session_minutes(client, today, now_et)

    # ── STEP 3 — fetch bars for held ∪ nominated symbols ─────────────────────
    symbols = sorted(set(ledger) | {n["symbol"] for n in nominations})
    minute_bars, daily_bars = _fetch_bars(client, symbols, today)

    equity = _equity(client)

    # ── STEP 4 — management (every in-hours tick) ────────────────────────────
    for sym in list(ledger.keys()):
        entry = ledger[sym]
        st = build_flex_exit_state(
            {**entry, "symbol": sym}, minute_bars.get(sym, []), daily_bars.get(sym, []), cfg, now_et,
        )
        decisions["exits"].append(st)
        if not dry_run:
            _act_on_exit(client, ledger, sym, st, today, decisions, executions)

    # ── STEP 5 — entry (morning window only) ─────────────────────────────────
    # Joint sleeve arbitration (DayTrade_Lab spec §1): the flex sleeve cap holds
    # across BOTH engines, so the lab's open notional consumes catalyst headroom.
    sleeve_used = _flex_notional(positions, ledger) \
        + _symbols_notional(positions, daytrade_syms)
    sleeve_cap_usd = cfg.sleeve_cap_pct / 100.0 * equity if equity else 0.0
    for nom in nominations:
        sym = nom["symbol"]
        if sym in ledger:
            continue
        if len(ledger) >= _FLEX_COUNT_CAP:
            decisions["orders_suppressed"].append({"symbol": sym, "reason": "flex_count_cap"})
            continue
        sleeve_room = max(0.0, sleeve_cap_usd - sleeve_used)
        cand = {"symbol": sym, "sector": _sector_for(sym, snapshot, nom)}
        e = build_flex_entry(
            cand, minute_bars.get(sym, []), daily_bars.get(sym, []),
            quadrant, equity, minutes if minutes is not None else -1, cfg,
            sleeve_room_usd=sleeve_room,
        )
        decisions["entries"].append(e)
        if e["entry_trigger"] == "pass" and not dry_run:
            opened = _open_position(client, ledger, sym, e, nom, today, decisions, executions)
            if opened:
                sleeve_used += e["notional_usd"]

    # ── STEP 6/7 — persist ───────────────────────────────────────────────────
    write_ledger(ledger)
    _persist(today, decisions, quadrant=quadrant, ledger=ledger, executions=executions)
    return {
        "status": "ok", "date": today, "quadrant": quadrant,
        "held": len(ledger), "entries_evaluated": len(decisions["entries"]),
        "orders_issued": len(decisions["orders_issued"]),
    }


# ---------------------------------------------------------------------------
# order actions (idempotent)
# ---------------------------------------------------------------------------

def _apply_repair(client, rep, today, decisions, executions) -> None:
    action, sym = rep.get("action"), rep.get("symbol")
    try:
        if action == "place_missing_stop":
            for oid in rep.get("cancel_order_ids", []):
                client.cancel_order(oid)
            qty = int(rep.get("qty") or 0)
            stop_price = float(rep.get("stop_price") or 0)
            if qty > 0 and stop_price > 0:
                order = client.submit_order(
                    sym, qty, "sell", order_type="stop", time_in_force="gtc",
                    stop_price=round(stop_price, 2),
                    client_order_id=_coid(today, sym, "rep"),
                )
                _issued(decisions, executions, sym, "place_missing_stop", order)
        # resize/clear/record repairs are ledger-side only (already applied).
    except Exception as e:  # noqa: BLE001
        logger.exception("repair %s for %s failed", action, sym)
        decisions["orders_suppressed"].append({"symbol": sym, "reason": f"repair_error:{action}:{e}"})


def _act_on_exit(client, ledger, sym, st, today, decisions, executions) -> None:
    action = st.get("next_action")
    entry = ledger.get(sym)
    if entry is None or action in ("hold", "unknown", "stopped"):
        if action in ("hold", "unknown"):
            decisions["orders_suppressed"].append(
                {"symbol": sym, "reason": f"no_action:{action}"})
        return
    try:
        if action == "time_stop":
            qty = int(st.get("scale_out_qty") or entry.get("qty_current") or 0)
            _cancel_stops(client, entry)
            if qty > 0:
                order = client.submit_order(
                    sym, qty, "sell", order_type="market", time_in_force="day",
                    client_order_id=_coid(today, sym, "tstop"))
                _issued(decisions, executions, sym, "time_stop", order)
                _record_trade_history(today, sym, "sell", qty, status="time_stop", extra={})
            ledger.pop(sym, None)

        elif action == "scale_out":
            qty = int(st.get("scale_out_qty") or 0)
            if qty > 0:
                order = client.submit_order(
                    sym, qty, "sell", order_type="market", time_in_force="day",
                    client_order_id=_coid(today, sym, "scale"))
                _issued(decisions, executions, sym, "scale_out", order)
                _record_trade_history(today, sym, "sell", qty, status="scale_out", extra={})
                entry["qty_current"] = int(entry.get("qty_current") or 0) - qty
                entry["scaled_out"] = True
            # Move the stop to breakeven on the remainder.
            _replace_stop(client, entry, float(st.get("target_stop") or entry["current_stop"]),
                          today, sym, decisions, executions)

        elif action == "trail":
            _replace_stop(client, entry, float(st.get("target_stop") or entry["current_stop"]),
                          today, sym, decisions, executions)
    except Exception as e:  # noqa: BLE001
        logger.exception("exit action %s for %s failed", action, sym)
        decisions["orders_suppressed"].append({"symbol": sym, "reason": f"exit_error:{action}:{e}"})


def _open_position(client, ledger, sym, e, nom, today, decisions, executions) -> bool:
    qty = int(e["size_shares"])
    stop_price = round(float(e["stop_price"]), 2)
    try:
        # Native OTO: entry buy + protective stop child that arms on fill (no naked long).
        order = client.submit_order(
            sym, qty, "buy", order_type="market", time_in_force="day",
            order_class="oto", stop_loss={"stop_price": stop_price},
            client_order_id=_coid(today, sym, "entry"))
        _issued(decisions, executions, sym, "entry_oto", order)
    except Exception as ex:  # noqa: BLE001
        logger.exception("OTO entry for %s failed", sym)
        decisions["orders_suppressed"].append({"symbol": sym, "reason": f"entry_error:{ex}"})
        return False

    legs = order.get("legs") or []
    stop_ids = [str(leg.get("id")) for leg in legs if str(leg.get("type", "")).startswith("stop")]
    ledger[sym] = new_entry(
        sym, float(e["entry_price"]), today, stop_price, qty,
        order_ids=[str(order.get("id"))] + stop_ids,
    )
    # Persist the ledger IMMEDIATELY on entry (defensive — MU orphan incident). The
    # tick otherwise only writes the ledger at end-of-STEP-6; a crash/timeout after
    # this fill but before that write left a broker position with no ledger row, and
    # reconcile never re-adopts broker orphans, so it went invisible with no exit.
    try:
        write_ledger(ledger)
    except Exception:  # noqa: BLE001
        logger.exception("ledger persist-on-entry for %s failed", sym)
    _record_trade_history(today, sym, "buy", qty, status="submitted", extra=_enums(nom, e))
    return True


def _replace_stop(client, entry, new_stop, today, sym, decisions, executions) -> None:
    new_stop = round(float(new_stop), 2)
    qty = int(entry.get("qty_current") or 0)
    if qty <= 0:
        return
    try:
        _cancel_stops(client, entry)
        order = client.submit_order(
            sym, qty, "sell", order_type="stop", time_in_force="gtc",
            stop_price=new_stop, client_order_id=_coid(today, sym, "stop"))
        _issued(decisions, executions, sym, "stop_replace", order)
        entry["current_stop"] = new_stop
        entry["order_ids"] = [str(order.get("id"))]
    except Exception as e:  # noqa: BLE001
        logger.exception("stop replace for %s failed", sym)
        decisions["orders_suppressed"].append({"symbol": sym, "reason": f"stop_error:{e}"})


def _cancel_stops(client, entry) -> None:
    for oid in (entry.get("order_ids") or []):
        try:
            client.cancel_order(oid)
        except Exception:  # noqa: BLE001
            logger.warning("cancel stop %s failed", oid)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _issued(decisions, executions, symbol, kind, order) -> None:
    rec = {
        "symbol": symbol, "kind": kind,
        "alpaca_order_id": order.get("id"),
        "alpaca_status": order.get("status"),
        "qty": order.get("qty"),
    }
    decisions["orders_issued"].append(rec)
    executions.append({**rec, "submitted_at": _now_utc_iso()})


def _flex_nominations(trades_doc, exclude: frozenset[str] = frozenset()) -> list[dict]:
    """Nominations minus core names and ``exclude`` (the DayTrade Lab's open
    symbols — three-way separation, DayTrade_Lab spec §1)."""
    if not isinstance(trades_doc, dict):
        return []
    noms = trades_doc.get("flex_nominations") or []
    out = []
    for n in noms:
        if isinstance(n, dict) and n.get("symbol"):
            sym = str(n["symbol"]).upper()
            if sym not in CORE_TICKERS and sym not in exclude:
                out.append({**n, "symbol": sym})
    return out


def _daytrade_ledger_symbols() -> frozenset[str]:
    """The DayTrade Lab's open symbols (blob read only — no daytrade import)."""
    data = read_json_blob("daytrade-ledger", "ledger.json")
    return frozenset(data.keys()) if isinstance(data, dict) else frozenset()


def _sector_for(sym, snapshot, nom) -> str | None:
    if nom.get("sector"):
        return nom["sector"]
    for block in ("fundamentals", "flex_candidates"):
        data = snapshot.get(block) or {}
        prof = data.get(sym) if isinstance(data, dict) else None
        if isinstance(prof, dict) and prof.get("sector"):
            return prof["sector"]
    return None


def _enums(nom, e) -> dict:
    return {
        "layer": "flex",
        "flex_source": nom.get("flex_source") or "",
        "primary_trigger": nom.get("primary_trigger") or "",
        "thesis_type": nom.get("thesis_type") or "catalyst",
        "trigger_evidence": nom.get("trigger_evidence") or "",
        "catalyst_date": nom.get("catalyst_date") or "",
        "binding": e.get("binding") or "",
        "stop_loss": e.get("stop_price"),
    }


def _record_trade_history(today, symbol, side, qty, status, extra) -> None:
    try:
        upsert_entity("TradeHistory", {
            "PartitionKey": today[:7],
            "RowKey": f"FLEX-{today}-{symbol}-{side}-{uuid.uuid4().hex[:8]}",
            "symbol": symbol, "side": side, "layer": "flex",
            "status": status, "exec_qty": int(qty), "executed_at": today,
            "recommended_at": today, "engine": "flex_intraday",
            **(extra or {}),
        })
    except Exception as e:  # noqa: BLE001
        logger.warning("TradeHistory flex upsert failed for %s: %s", symbol, e)


def _fetch_bars(client, symbols, today):
    if not symbols:
        return {}, {}
    minute_bars, daily_bars = {}, {}
    try:
        minute_bars = client.get_bars(symbols, "1Min", start=f"{today}T00:00:00Z", feed="iex")
    except Exception:  # noqa: BLE001
        logger.exception("minute bars fetch failed")
    try:
        start = (date.today() - timedelta(days=90)).isoformat()
        daily_bars = client.get_bars(symbols, "1Day", start=start, feed="iex")
    except Exception:  # noqa: BLE001
        logger.exception("daily bars fetch failed")
    return minute_bars, daily_bars


def _session_minutes(client, today, now_et) -> float | None:
    try:
        cal = client.get_calendar(today, today)
        if not cal:
            return None
        oh, om = (int(x) for x in str(cal[0]["open"]).split(":"))
        open_et = now_et.replace(hour=oh, minute=om, second=0, microsecond=0)
        return (now_et - open_et).total_seconds() / 60.0
    except Exception:  # noqa: BLE001
        logger.exception("session-minutes calc failed")
        return None


def _equity(client) -> float:
    try:
        return float(client.get_account().get("equity") or 0)
    except Exception:  # noqa: BLE001
        logger.exception("equity read failed")
        return 0.0


def _flex_notional(positions, ledger) -> float:
    return _symbols_notional(positions, set(ledger))


def _symbols_notional(positions, symbols) -> float:
    total = 0.0
    for p in positions or []:
        sym = str(p.get("symbol", "")).upper()
        if sym in symbols:
            try:
                total += abs(float(p.get("market_value") or 0))
            except (TypeError, ValueError):
                pass
    return total


def _persist(today, decisions, quadrant, ledger, executions) -> None:
    flex_state = {
        "as_of": today, "quadrant": quadrant,
        "reconcile": decisions.get("reconcile", {}),
        "exits": decisions.get("exits", []),
        "entries": decisions.get("entries", []),
        "held": sorted(ledger.keys()),
    }
    try:
        write_json_blob("flex-state", f"{today}.json", flex_state)
    except Exception:  # noqa: BLE001
        logger.exception("flex-state write failed")
    append_jsonl_blob("flex-decisions", f"{today}.jsonl", decisions)
    if executions:
        try:
            write_json_blob("flex-executions", f"{today}.json",
                            {"date": today, "executions": executions})
        except Exception:  # noqa: BLE001
            logger.exception("flex-executions write failed")


def _coid(today, sym, kind) -> str:
    # FLEXC- namespaces catalyst-engine orders vs the DayTrade Lab's FLEXD-
    # (DayTrade_Lab spec §1). Reconcile keys on the ledger, not the prefix, so
    # pre-existing "flex-" orders remain managed.
    return f"FLEXC-{today}-{sym}-{kind}-{uuid.uuid4().hex[:6]}"[:48]


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_et() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(_ET))
    except Exception:  # noqa: BLE001
        # Fallback: UTC minus 4h ≈ EDT (only the date/window math depends on this;
        # the real session open comes from Alpaca's calendar).
        return datetime.now(timezone.utc).astimezone()
