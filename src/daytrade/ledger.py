"""Persisted open-position state for the DayTrade Lab (spec §8).

A single blob ``daytrade-ledger/ledger.json`` mirroring the lab's slice of the
live Alpaca paper account — one row per open lab symbol, NEVER shared with the
catalyst engine's ``flex-ledger`` (separation contract, spec §1). Rebuildable
from the daytrade log if lost.
"""
from __future__ import annotations

from shared.storage import read_json_blob, write_json_blob

_CONTAINER = "daytrade-ledger"
_BLOB = "ledger.json"

COID_PREFIX = "FLEXD"   # every lab order's client_order_id starts with this


def read_ledger() -> dict:
    data = read_json_blob(_CONTAINER, _BLOB)
    return data if isinstance(data, dict) else {}


def write_ledger(ledger: dict) -> None:
    write_json_blob(_CONTAINER, _BLOB, ledger)


def new_entry(
    symbol: str,
    slot: int,
    entry_price: float,
    entry_date: str,
    stop_price: float,
    target_price: float,
    qty: int,
    pattern: str,
    catalyst_class: str | None,
    tone: str,
    entered_min: float,
    order_ids: list[str] | None = None,
    scaled_out: bool = False,
) -> dict:
    return {
        "symbol": symbol.upper(),
        "slot": int(slot),
        "entry_price": float(entry_price),
        "entry_date": entry_date,
        "stop_price": float(stop_price),
        "target_price": float(target_price),
        "risk_per_share": float(entry_price) - float(stop_price),
        "qty_initial": int(qty),
        "qty_current": int(qty),
        "scaled_out": bool(scaled_out),
        "pattern": pattern,
        "catalyst_class": catalyst_class,
        "tone": tone,
        "entered_min": float(entered_min),
        "order_ids": list(order_ids or []),
    }
