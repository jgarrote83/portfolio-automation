"""Phase-2 executor: places paper trades on Alpaca for approved recommendations.

Trigger: HTTP POST to /api/executor with body `{"date": "YYYY-MM-DD", "force": false}`.
Auth: function-level (master key) — the SWA managed API holds the key.

Inputs (blob):
- `daily-trades/{date}.json`  — analyzer output, list of recommendations
- `approvals/{date}.json`     — SWA-recorded user decisions

Outputs:
- `daily-executions/{date}.json` — per-trade execution result
- `TradeHistory` table rows updated with execution outcome
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from shared.keyvault import load_secrets
from shared.storage import (
    read_approvals,
    read_executions,
    read_trades,
    upsert_entity,
    write_executions,
)
from shared.clients.alpaca import AlpacaClient

logger = logging.getLogger(__name__)


def execute_approvals(date_str: str, force: bool = False, auto: bool = False) -> dict:
    """Place Alpaca paper orders for trades on `date_str`.

    Parameters
    ----------
    date_str : YYYY-MM-DD
    force    : ignore cached `daily-executions/{date}.json` and re-run.
    auto     : paper-only auto-execute. Skips approval doc and treats every
               recommendation in `daily-trades/{date}.json` as approved.
               Caller is responsible for ensuring this is paper-only.
    """
    logger.info("=== Executor starting for %s (force=%s, auto=%s) ===", date_str, force, auto)

    if not force:
        existing = read_executions(date_str)
        if existing:
            logger.info("Executions already exist for %s — returning cached result", date_str)
            return {**existing, "cached": True}

    trades_doc = read_trades(date_str)
    if not trades_doc:
        return {"date": date_str, "status": "no_trades", "executions": []}

    trades = _extract_trades(trades_doc)
    if not trades:
        return {"date": date_str, "status": "no_trades", "executions": []}

    if auto:
        approved = list(trades)
        logger.info("Auto-execute: approving all %d recommended trades", len(approved))
    else:
        approvals_doc = read_approvals(date_str) or {}
        approved_ids = {
            d.get("id")
            for d in approvals_doc.get("decisions", [])
            if d.get("status") == "approved" and d.get("id")
        }
        if not approved_ids:
            return {"date": date_str, "status": "no_approvals", "executions": []}

        approved = [t for t in trades if t.get("id") in approved_ids]
        if not approved:
            return {"date": date_str, "status": "no_match", "executions": []}

    # CLAUDE.md rule: sells first to free up cash, then buys.
    approved.sort(key=lambda t: 0 if str(t.get("side", "")).lower() == "sell" else 1)

    secrets = load_secrets()
    key = secrets.get("AlpacaApiKey")
    secret = secrets.get("AlpacaApiSecret")
    if not key or not secret:
        raise RuntimeError("Alpaca credentials missing from Key Vault")

    client = AlpacaClient(api_key=key, api_secret=secret)

    # Defensive filter: drop sells we cannot fill (paper book doesn't hold the
    # symbol, or holds less than requested). Claude sometimes recommends sells
    # against the E*TRADE book even though the paper account has different
    # holdings — these would all reject with 422. Skipping them keeps the
    # execution log clean and avoids API-error noise.
    skipped: list[dict] = []
    try:
        paper_qty = {
            str(p.get("symbol", "")).upper(): float(p.get("qty") or 0)
            for p in client.list_positions()
        }
    except Exception:  # noqa: BLE001
        logger.exception("Could not fetch paper positions — skipping sell filter")
        paper_qty = None

    if paper_qty is not None:
        filtered: list[dict] = []
        for t in approved:
            side = str(t.get("side", "")).lower()
            sym = str(t.get("symbol") or t.get("ticker") or "").upper()
            req_qty = float(t.get("quantity") or t.get("qty") or 0)
            if side == "sell":
                held = paper_qty.get(sym, 0.0)
                if held <= 0:
                    skipped.append({
                        "id": t.get("id"), "symbol": sym, "side": side,
                        "requested_qty": req_qty,
                        "reason": "not_held_in_paper_account",
                    })
                    continue
                if req_qty > held + 1e-6:
                    # Trim to what we actually hold rather than skip entirely.
                    logger.warning(
                        "%s sell qty %s > held %s — trimming to held",
                        sym, req_qty, held,
                    )
                    t = {**t, "quantity": held}
            filtered.append(t)
        if skipped:
            logger.warning(
                "Dropped %d sell(s) not held in paper book: %s",
                len(skipped), [s["symbol"] for s in skipped],
            )
        approved = filtered

    if not approved:
        result = {
            "date": date_str,
            "status": "all_filtered",
            "skipped": skipped,
            "executions": [],
        }
        write_executions(date_str, result)
        return result

    # Gate on market clock. Fractional + day-tif orders are rejected by Alpaca
    # when market is closed; even whole-share queued orders span weekends badly
    # (see 2026-05-24 batch rejection). Defer to next open instead.
    try:
        clock = client.get_clock()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to read Alpaca clock — proceeding anyway")
        clock = {"is_open": True}
    if not clock.get("is_open", False):
        logger.warning(
            "Market closed (next_open=%s) — deferring %d trades for %s",
            clock.get("next_open"), len(approved), date_str,
        )
        return {
            "date": date_str,
            "status": "deferred_market_closed",
            "next_open": clock.get("next_open"),
            "pending": len(approved),
            "executions": [],
        }

    executions: list[dict] = []
    for trade in approved:
        executions.append(_place_one(client, trade, date_str))

    result = {
        "date": date_str,
        "status": "ok",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "total": len(executions),
        "succeeded": sum(1 for e in executions if e["status"] == "submitted"),
        "failed": sum(1 for e in executions if e["status"] == "error"),
        "skipped": skipped,
        "executions": executions,
    }
    write_executions(date_str, result)
    _write_trade_history(date_str, executions)
    logger.info(
        "=== Executor done for %s — %d/%d submitted ===",
        date_str, result["succeeded"], result["total"],
    )
    return result


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _extract_trades(doc: dict | list) -> list[dict]:
    if isinstance(doc, list):
        return [t for t in doc if isinstance(t, dict)]
    if isinstance(doc, dict):
        trades = doc.get("trades") or doc.get("recommendations") or []
        return [t for t in trades if isinstance(t, dict)]
    return []


def _place_one(client: AlpacaClient, trade: dict, date_str: str) -> dict:
    # Single-leg market/limit orders only. `stop_loss` / `take_profit` on the trade
    # are ADVISORY levels evaluated by the analyzer on the next run (it proposes an
    # exit if the snapshot price breaches them) — they are intentionally NOT sent to
    # Alpaca as bracket/OCO legs. A resting broker stop would make this executor
    # stateful and collide with the daily re-recommendation loop; the daily
    # analyzer check is the stop mechanism by design. Do not wire them in here.
    trade_id = str(trade.get("id") or uuid.uuid4())
    symbol = trade.get("symbol") or trade.get("ticker")
    side = str(trade.get("side") or trade.get("action") or "").lower()
    qty = trade.get("quantity") or trade.get("qty")
    order_type = str(trade.get("order_type") or "market").lower()
    tif = str(trade.get("time_in_force") or "day").lower()
    limit_price = trade.get("limit_price")
    stop_price = trade.get("stop_price")

    base = {
        "id": trade_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "order_type": order_type,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }

    if not symbol or side not in ("buy", "sell") or not qty:
        return {**base, "status": "error", "error": "invalid trade payload"}

    try:
        order = client.submit_order(
            symbol=symbol,
            qty=qty,
            side=side,
            order_type=order_type,
            time_in_force=tif,
            limit_price=limit_price,
            stop_price=stop_price,
            client_order_id=f"{date_str}-{trade_id}"[:48],
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Order failed for %s %s %s", side, qty, symbol)
        return {**base, "status": "error", "error": str(e)}

    return {
        **base,
        "status": "submitted",
        "alpaca_order_id": order.get("id"),
        "alpaca_status": order.get("status"),
        "alpaca_client_order_id": order.get("client_order_id"),
    }


def _write_trade_history(date_str: str, executions: list[dict]) -> None:
    year_month = date_str[:7]  # YYYY-MM
    for ex in executions:
        try:
            # Lowercase keys align with the analyzer's recommendation row (same
            # PK/RK), so this upsert MERGES execution status onto that one row
            # rather than creating duplicate mixed-case columns. `status`
            # transitions recommended -> submitted/error. Phase C §9.
            upsert_entity("TradeHistory", {
                "PartitionKey": year_month,
                "RowKey": str(ex.get("id")),
                "symbol": ex.get("symbol") or "",
                "side": ex.get("side") or "",
                "order_type": ex.get("order_type") or "",
                "status": ex.get("status") or "",
                "exec_qty": int(ex.get("qty") or 0),   # actually submitted (may be trimmed)
                "executed_at": date_str,
                "alpaca_order_id": ex.get("alpaca_order_id") or "",
                "alpaca_status": ex.get("alpaca_status") or "",
                "error": ex.get("error") or "",
                "submitted_at": ex.get("submitted_at") or "",
            })
        except Exception as e:  # noqa: BLE001
            logger.warning("TradeHistory upsert failed for %s: %s", ex.get("id"), e)
