"""Persisted open-position state for the Flex engine.

A single blob ``flex-ledger/ledger.json`` mirroring the live Alpaca paper account
(one row per open flex symbol). STEP 0 reconciliation keeps it true to the broker;
it is rebuildable from ``TradeHistory`` if lost. ``qty_current`` is the
authoritative remaining-share count after any partial scale-out, and the resting
stop is always sized to it.
"""
from __future__ import annotations

from shared.storage import read_json_blob, write_json_blob

_CONTAINER = "flex-ledger"
_BLOB = "ledger.json"


def read_ledger() -> dict:
    data = read_json_blob(_CONTAINER, _BLOB)
    return data if isinstance(data, dict) else {}


def write_ledger(ledger: dict) -> None:
    write_json_blob(_CONTAINER, _BLOB, ledger)


def new_entry(
    symbol: str,
    entry_price: float,
    entry_date: str,
    initial_stop: float,
    qty: int,
    order_ids: list[str] | None = None,
) -> dict:
    """Build a fresh ledger row for a newly opened flex position."""
    return {
        "symbol": symbol.upper(),
        "entry_price": float(entry_price),
        "entry_date": entry_date,
        "initial_stop": float(initial_stop),
        "risk_per_share": float(entry_price) - float(initial_stop),
        "qty_initial": int(qty),
        "qty_current": int(qty),
        "scaled_out": False,
        "current_stop": float(initial_stop),
        "order_ids": list(order_ids or []),
    }
