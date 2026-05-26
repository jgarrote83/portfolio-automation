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
            upsert_entity("TradeHistory", {
                "PartitionKey": year_month,
                "RowKey": str(ex.get("id")),
                "Date": date_str,
                "Symbol": ex.get("symbol") or "",
                "Side": ex.get("side") or "",
                "Quantity": str(ex.get("qty") or ""),
                "OrderType": ex.get("order_type") or "",
                "Status": ex.get("status") or "",
                "AlpacaOrderId": ex.get("alpaca_order_id") or "",
                "AlpacaStatus": ex.get("alpaca_status") or "",
                "Error": ex.get("error") or "",
                "SubmittedAt": ex.get("submitted_at") or "",
            })
        except Exception as e:  # noqa: BLE001
            logger.warning("TradeHistory upsert failed for %s: %s", ex.get("id"), e)
