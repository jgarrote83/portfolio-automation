"""STEP 0 — reconcile the daytrade ledger to broker truth (pure) — spec §4.

Mirrors the catalyst engine's reconcile-FIRST doctrine against THIS ledger only:
a symbol the lab did not open is never touched (separation contract). Repairs:

- position gone at the broker → a bracket leg (stop/target) filled between
  ticks → record the exit for grading, clear the row;
- qty drifted (partial fill / managed scale-out landed) → resize to broker truth;
- an open lab position with NO protective sell order → ``flatten_orphan``
  (the lab's no-naked-long is stricter than the catalyst's: intraday scope means
  a market flatten, not a re-stop).
"""
from __future__ import annotations

import copy

_QTY_EPS = 1e-6


def _num(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _is_protective_sell(order: dict, symbol: str) -> bool:
    return (
        str(order.get("symbol", "")).upper() == symbol
        and str(order.get("side", "")).lower() == "sell"
        and str(order.get("type", "")).lower() in ("stop", "stop_limit", "limit")
    )


def reconcile_daytrade(
    ledger: dict,
    alpaca_positions: list[dict],
    alpaca_orders: list[dict],
) -> tuple[dict, list[dict], list[dict]]:
    """Return ``(new_ledger, exits_to_record, repairs)``."""
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

        if held <= _QTY_EPS:
            exits_to_record.append(
                {"symbol": symbol, "entry": entry, "reason": "closed_at_broker"})
            del new_ledger[symbol]
            continue

        if abs(held - _num(entry.get("qty_current"))) > _QTY_EPS:
            repairs.append({
                "action": "resize_to_broker",
                "symbol": symbol,
                "from_qty": _num(entry.get("qty_current")),
                "to_qty": held,
            })
            entry["qty_current"] = held

        protective = [o for o in orders if _is_protective_sell(o, symbol)]
        if not protective:
            repairs.append({"action": "flatten_orphan", "symbol": symbol,
                            "qty": held})

    return new_ledger, exits_to_record, repairs
