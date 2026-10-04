"""ORB Phase 0 -- read-only "is Flex flat?" check.

Reports FLAT / NOT FLAT / INCONCLUSIVE for the Flex catalyst engine and the
DayTrade Lab, so Jorge knows when it is safe to retire them (Part B).

STRICTLY READ-ONLY: Alpaca GET requests only (positions, open orders) and blob
downloads only. It never submits, replaces or cancels an order and never writes
a blob. Credentials come from the environment and are never printed.

Run (PowerShell), after `az login` as jgarrote@easygrids.com on EasyGridsProduction:

    $env:ALPACA_API_KEY    = "<paper key>"
    $env:ALPACA_API_SECRET = "<paper secret>"
    $env:STORAGE_ACCOUNT_NAME = "stpfautoprod"
    $env:PYTHONPATH = "src"
    python scripts/orb_phase0_flat_check.py

Exit code: 0 = FLAT, 1 = NOT FLAT, 2 = INCONCLUSIVE (missing credentials or an
unreadable source -- never treated as flat).

Attribution (an order is Flex's if ANY of these holds):
  * its client_order_id starts with FLEXC- / FLEXD- / flex- (case-insensitive);
  * its id, or its parent_order_id, appears in a ledger row's ``order_ids``
    -- bracket/OTO child legs carry broker-assigned UUIDs, not the prefix
    (CLAUDE.md, B2 s8.2);
  * it rests on a symbol that a ledger row still tracks.
A position is Flex's if its symbol is held in a ledger row or sits under an
attributed order. Core positions are never reported.
"""
from __future__ import annotations

import os
import sys

_FLEX_PREFIXES = ("flexc-", "flexd-", "flex-")
_PAPER_BASE = "https://paper-api.alpaca.markets"
_LEDGERS = (
    ("flex", "flex-ledger", "ledger.json"),
    ("daytrade", "daytrade-ledger", "ledger.json"),
)


def _flatten_orders(orders: list[dict]) -> list[dict]:
    """Expand nested bracket/OTO ``legs`` into a flat list (parent first)."""
    out: list[dict] = []
    for o in orders or []:
        out.append(o)
        for leg in o.get("legs") or []:
            out.append({**leg, "parent_order_id": leg.get("parent_order_id") or o.get("id")})
    return out


def classify_flatness(
    positions: list[dict],
    open_orders: list[dict],
    ledgers: dict[str, dict],
) -> dict:
    """Pure classification. ``ledgers`` maps engine name -> ledger dict
    (symbol -> row with optional ``order_ids``). No I/O.

    Returns {flat, ledger_rows, orders, positions} where each list holds only
    the offending items.
    """
    ledger_symbols: dict[str, str] = {}   # symbol -> engine
    ledger_order_ids: set[str] = set()
    ledger_rows = []
    for engine, ledger in (ledgers or {}).items():
        for symbol, row in (ledger or {}).items():
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or symbol).upper()
            ledger_symbols[sym] = engine
            ledger_order_ids.update(str(i) for i in (row.get("order_ids") or []) if i)
            ledger_rows.append({
                "engine": engine, "symbol": sym,
                "qty_current": row.get("qty_current"),
                "entry_date": row.get("entry_date"),
            })

    bad_orders = []
    order_symbols: set[str] = set()
    for o in _flatten_orders(open_orders):
        cid = str(o.get("client_order_id") or "").lower()
        sym = str(o.get("symbol") or "").upper()
        reasons = []
        if cid.startswith(_FLEX_PREFIXES):
            reasons.append("client_order_id prefix")
        if str(o.get("id") or "") in ledger_order_ids:
            reasons.append("id in ledger order_ids")
        if o.get("parent_order_id") and str(o["parent_order_id"]) in ledger_order_ids:
            reasons.append("parent in ledger order_ids")
        if sym in ledger_symbols:
            reasons.append("symbol tracked by ledger")
        if reasons:
            order_symbols.add(sym)
            bad_orders.append({
                "id": o.get("id"), "client_order_id": o.get("client_order_id"),
                "symbol": sym, "side": o.get("side"), "type": o.get("type"),
                "status": o.get("status"), "reasons": reasons,
            })

    bad_positions = []
    for p in positions or []:
        sym = str(p.get("symbol") or "").upper()
        if sym in ledger_symbols or sym in order_symbols:
            bad_positions.append({
                "symbol": sym, "qty": p.get("qty"),
                "via": "ledger" if sym in ledger_symbols else "attributed order",
            })

    return {
        "flat": not (ledger_rows or bad_orders or bad_positions),
        "ledger_rows": ledger_rows,
        "orders": bad_orders,
        "positions": bad_positions,
    }


# --------------------------------------------------------------------------
# I/O (read-only)
# --------------------------------------------------------------------------

def _alpaca_get(session, path: str, params: dict | None = None):
    r = session.get(f"{_PAPER_BASE}{path}", params=params or {}, timeout=20)
    r.raise_for_status()
    return r.json()


def _read_ledger_blob(container: str, name: str) -> dict:
    """Download a ledger blob. Absent -> {} (genuinely empty). Any other error
    raises, so an auth failure is never mistaken for an empty ledger."""
    import json

    from azure.core.exceptions import ResourceNotFoundError
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    account = os.environ["STORAGE_ACCOUNT_NAME"]
    svc = BlobServiceClient(
        f"https://{account}.blob.core.windows.net", credential=DefaultAzureCredential()
    )
    try:
        raw = svc.get_blob_client(container, name).download_blob().readall()
    except ResourceNotFoundError:
        return {}
    data = json.loads(raw) if raw else {}
    return data if isinstance(data, dict) else {}


def main() -> int:
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_API_SECRET")
    if not key or not secret or not os.environ.get("STORAGE_ACCOUNT_NAME"):
        print("INCONCLUSIVE: set ALPACA_API_KEY, ALPACA_API_SECRET and STORAGE_ACCOUNT_NAME first.")
        print("Nothing was queried. A missing credential is never reported as flat.")
        return 2

    import requests

    session = requests.Session()
    session.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})

    try:
        positions = _alpaca_get(session, "/v2/positions")
        orders = _alpaca_get(
            session, "/v2/orders", {"status": "open", "nested": "true", "limit": 500}
        )
        ledgers = {
            engine: _read_ledger_blob(container, name) for engine, container, name in _LEDGERS
        }
    except Exception as exc:  # noqa: BLE001 -- report the type only, never a header/credential
        print(f"INCONCLUSIVE: a read failed ({type(exc).__name__}). Not treated as flat.")
        return 2

    result = classify_flatness(positions, orders, ledgers)
    print(f"Alpaca positions (all): {len(positions)}   open orders (incl. legs): "
          f"{len(_flatten_orders(orders))}")
    for engine, ledger in ledgers.items():
        print(f"{engine}-ledger rows: {len(ledger)}")
    if result["flat"]:
        print("\nVERDICT: FLAT -- no Flex/DayTrade positions, orders or ledger rows remain.")
        return 0
    print("\nVERDICT: NOT FLAT")
    for row in result["ledger_rows"]:
        print(f"  ledger   [{row['engine']}] {row['symbol']} qty={row['qty_current']} "
              f"entered={row['entry_date']}")
    for pos in result["positions"]:
        print(f"  position {pos['symbol']} qty={pos['qty']} (via {pos['via']})")
    for o in result["orders"]:
        print(f"  order    {o['symbol']} {o['side']} {o['type']} status={o['status']} "
              f"coid={o['client_order_id']} ({'; '.join(o['reasons'])})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
