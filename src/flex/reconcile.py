"""STEP 0 — reconcile the flex ledger to broker truth (pure).

This runs as the literal first step of every tick, before any entry/exit
computation: the ledger is a mirror of a live account and drifts between runs.
The single most important repair is **no-naked-long** — a held flex position
with no resting protective stop gets a ``place_missing_stop`` repair emitted
*first*, so the engine never knowingly holds an unstopped position past STEP 0.

Pure so it is fully unit-testable without a broker. The handler executes the
repairs and records the exits.
"""
from __future__ import annotations

import copy

_QTY_EPS = 1e-6
# Repair execution order — no-naked-long before anything else.
_PRIORITY = {
    "place_missing_stop": 0,
    "resize_to_partial_fill": 1,
    "clear_phantom_order": 2,
    "record_filled_stop": 3,
}


def _num(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _is_resting_stop(order: dict, symbol: str) -> bool:
    return (
        str(order.get("symbol", "")).upper() == symbol
        and str(order.get("side", "")).lower() == "sell"
        and str(order.get("type", "")).lower() in ("stop", "stop_limit")
    )


def reconcile_ledger(
    ledger: dict,
    alpaca_positions: list[dict],
    alpaca_orders: list[dict],
) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """Return ``(new_ledger, exits_to_record, repairs, orphan_orders)``.

    ``repairs`` is sorted so ``place_missing_stop`` is always first.

    ``orphan_orders`` (session 2026-07-17, Task F1) — every broker OPEN order
    whose symbol is NOT a key of the POST-reconciliation ``new_ledger`` (i.e. not
    a symbol this run's ledger manages, including one just closed this tick). This
    is the visibility fix for the MU incident's root cause: a repair/entry stop is
    placed directly against the broker (`flex/handler.py::_apply_repair`/
    `_open_position`), never through this function, so if the ledger row that
    "owns" that order is EVER lost by some other path (the MU incident: the
    engine's internal ``held[]`` state dropped the position while its GTC repair
    stop stayed resting), nothing before this scanned the broker's full open-order
    list for a symbol the ledger no longer tracks — the order sat there,
    invisible, locking the shares as collateral. Each entry: ``{symbol, side,
    type, stop_price, client_order_id, submitted_at, id}`` (``id`` is the Alpaca
    order id, needed to cancel it — see ``flex/handler.py::_sweep_orphan_orders``,
    Task F2). Describe-only here — this function never cancels anything itself.
    """
    new_ledger = copy.deepcopy(ledger or {})
    pos_qty = {
        str(p.get("symbol", "")).upper(): _num(p.get("qty"))
        for p in (alpaca_positions or [])
    }
    orders = alpaca_orders or []

    exits_to_record: list[dict] = []
    repairs: list[dict] = []

    for symbol in list(new_ledger.keys()):
        entry = new_ledger[symbol]
        held = pos_qty.get(symbol, 0.0)

        # Position gone at the broker → the resting stop (or a manual sell) closed
        # it between ticks. Record the exit and drop the row; do NOT then manage a
        # position that no longer exists.
        if held <= _QTY_EPS:
            exits_to_record.append({"symbol": symbol, "entry": entry, "reason": "closed_at_broker"})
            repairs.append({"action": "record_filled_stop", "symbol": symbol})
            del new_ledger[symbol]
            continue

        # Partial fill (entry or scale-out) → reconcile qty to broker truth.
        if abs(held - _num(entry.get("qty_current"))) > _QTY_EPS:
            repairs.append({
                "action": "resize_to_partial_fill",
                "symbol": symbol,
                "from_qty": _num(entry.get("qty_current")),
                "to_qty": held,
            })
            entry["qty_current"] = held

        # Drop phantom stop ids (recorded but no longer open at the broker).
        open_ids = {str(o.get("id")) for o in orders}
        recorded = [str(i) for i in (entry.get("order_ids") or [])]
        phantom = [i for i in recorded if i not in open_ids]
        for pid in phantom:
            repairs.append({"action": "clear_phantom_order", "symbol": symbol, "order_id": pid})
        entry["order_ids"] = [i for i in recorded if i in open_ids]

        # No-naked-long: a held position must carry a resting protective stop sized
        # to the current quantity. If none (or the wrong size), place/replace it.
        resting = [o for o in orders if _is_resting_stop(o, symbol)]
        adequate = any(abs(_num(o.get("qty")) - held) <= _QTY_EPS for o in resting)
        if not adequate:
            stop_price = _num(entry.get("current_stop")) or _num(entry.get("initial_stop"))
            repairs.append({
                "action": "place_missing_stop",
                "symbol": symbol,
                "stop_price": stop_price,
                "qty": held,
                "cancel_order_ids": [str(o.get("id")) for o in resting],
            })

    repairs.sort(key=lambda r: _PRIORITY.get(r.get("action"), 99))

    managed = set(new_ledger.keys())
    orphan_orders = [
        {
            "symbol": str(o.get("symbol", "")).upper(),
            "side": o.get("side"),
            "type": o.get("type"),
            "stop_price": o.get("stop_price"),
            "client_order_id": o.get("client_order_id"),
            "submitted_at": o.get("submitted_at") or o.get("created_at"),
            "id": o.get("id"),
        }
        for o in orders
        if str(o.get("symbol", "")).upper() not in managed
    ]

    return new_ledger, exits_to_record, repairs, orphan_orders
