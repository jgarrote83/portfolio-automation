"""ORB Phase 0 -- read-only "is Flex flat?" check.

Reports FLAT / NOT FLAT / NOT SAFE / INCONCLUSIVE for the Flex catalyst engine and
the DayTrade Lab, so Jorge knows when it is safe to retire them (Part B).

STRICTLY READ-ONLY: Alpaca GET requests only (positions, open orders) and blob
downloads only. It never submits, replaces or cancels an order and never writes
a blob. Credentials come from the environment and are never printed.

Run (PowerShell), after `az login` as jgarrote@easygrids.com on EasyGridsProduction:

    $env:ALPACA_API_KEY    = "<paper key>"
    $env:ALPACA_API_SECRET = "<paper secret>"
    $env:STORAGE_ACCOUNT_NAME = "stpfautoprod"
    $env:PYTHONPATH = "src"
    python scripts/orb_phase0_flat_check.py

Exit code: 0 = FLAT, 1 = NOT FLAT or NOT SAFE, 2 = INCONCLUSIVE (missing
credentials or an unreadable source -- never treated as flat).

FLAT requires ALL of:
  1. the Flex kill switch is tripped (`flex-ledger/kill-switch.json`: tripped true,
     cleared_at absent/empty). Otherwise `NOT SAFE`: an untripped engine can open a
     position the instant after this check passes.
  2. no ledger rows in either ledger;
  3. no attributed open orders or positions (below);
  4. no unattributed non-Core position and no unattributed resting stop (below).

Attribution (an order is Flex's if ANY of these holds):
  * its client_order_id starts with FLEXC- / FLEXD- / flex- (case-insensitive);
  * its id, or its parent_order_id, appears in a ledger row's ``order_ids``
    -- bracket/OTO child legs carry broker-assigned UUIDs, not the prefix
    (CLAUDE.md, B2 s8.2);
  * it rests on a symbol that a ledger row still tracks.
A position is Flex's if its symbol is held in a ledger row or sits under an
attributed order.

Why checks 4 exist (false-FLAT hole): if a Flex tick's broker read fails,
`flex/handler.py` STEP 0 falls back to `positions, open_orders = [], []`, reconcile
treats every ledger row as closed and DROPS it. The position stays open at Alpaca,
its bracket legs carry broker UUIDs, and no ledger names it -- so nothing above
can attribute it. Two independent nets catch that:
  * any position outside the Core universe (`shared.quadrants.CORE_ROSTER` = every
    role's full pool + LEGACY_EXITS) that is not already attributed;
  * any open stop / stop_limit / trailing_stop order not otherwise attributed. Core
    never rests stops: the executor places market/limit only
    (`executor/handler.py:_place_one`, order_type per the prompt schema
    "market" | "limit"), and its stops are advisory levels, never sent to Alpaca.
"""
from __future__ import annotations

import os
import sys

_FLEX_PREFIXES = ("flexc-", "flexd-", "flex-")
_PAPER_BASE = "https://paper-api.alpaca.markets"
_STOP_TYPES = ("stop", "stop_limit", "trailing_stop")
_KILL_SWITCH = ("flex-ledger", "kill-switch.json")
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


def kill_switch_status(state: dict | None) -> dict:
    """Pure. Mirrors the engine's own rule (`flex/killswitch.py`: tripped and no
    truthy `cleared_at`). Absent/empty state is NOT tripped."""
    state = state or {}
    tripped = bool(state.get("tripped")) and not state.get("cleared_at")
    return {
        "safe": tripped,
        "tripped": bool(state.get("tripped")),
        "cleared_at": state.get("cleared_at") or None,
        "trip_reason": state.get("trip_reason"),
        "tripped_at": state.get("tripped_at"),
    }


def classify_flatness(
    positions: list[dict],
    open_orders: list[dict],
    ledgers: dict[str, dict],
    kill_switch: dict | None = None,
    core_universe: set[str] | frozenset[str] | None = None,
) -> dict:
    """Pure classification. No I/O.

    ``ledgers`` maps engine name -> ledger dict (symbol -> row, optional
    ``order_ids``). ``kill_switch`` is the parsed `kill-switch.json` (None/{} =
    absent = not tripped). ``core_universe`` is the set of symbols Core may hold;
    None disables the unattributed-position net (unit tests of attribution only).

    Returns {verdict, flat, safe, kill_switch, ledger_rows, orders, positions}
    where `verdict` is "FLAT", "NOT FLAT" or "NOT SAFE" and the lists hold only
    offending items. NOT SAFE (untripped switch) takes precedence only when
    nothing else is wrong; if something is also open, NOT FLAT is reported (and
    `safe` is still False).
    """
    ks = kill_switch_status(kill_switch)

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
        otype = str(o.get("type") or o.get("order_type") or "").lower()
        reasons = []
        if cid.startswith(_FLEX_PREFIXES):
            reasons.append("client_order_id prefix")
        if str(o.get("id") or "") in ledger_order_ids:
            reasons.append("id in ledger order_ids")
        if o.get("parent_order_id") and str(o["parent_order_id"]) in ledger_order_ids:
            reasons.append("parent in ledger order_ids")
        if sym in ledger_symbols:
            reasons.append("symbol tracked by ledger")
        if not reasons and otype in _STOP_TYPES:
            reasons.append("resting stop (Core never rests stops)")
        if reasons:
            order_symbols.add(sym)
            bad_orders.append({
                "id": o.get("id"), "client_order_id": o.get("client_order_id"),
                "symbol": sym, "side": o.get("side"), "type": otype,
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
        elif core_universe is not None and sym not in core_universe:
            bad_positions.append({
                "symbol": sym, "qty": p.get("qty"),
                "via": "unattributed non-Core position",
            })

    nothing_open = not (ledger_rows or bad_orders or bad_positions)
    if not nothing_open:
        verdict = "NOT FLAT"
    elif not ks["safe"]:
        verdict = "NOT SAFE"
    else:
        verdict = "FLAT"
    return {
        "verdict": verdict,
        "flat": verdict == "FLAT",
        "safe": ks["safe"],
        "kill_switch": ks,
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


def _read_json_blob_strict(container: str, name: str) -> dict:
    """Download a JSON blob. Genuinely absent -> {}. Any other error raises, so an
    auth/transport failure is never mistaken for an empty ledger or an absent
    kill switch (`shared.storage.read_json_blob` swallows ALL exceptions and
    returns None, which would read as empty)."""
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


def _core_universe() -> frozenset[str]:
    """Every symbol Core may hold: every role's full pool + LEGACY_EXITS
    (`shared.quadrants.CORE_ROSTER`, built by `_build_core_roster`). Read-only
    import; needs PYTHONPATH=src."""
    from shared.quadrants import CORE_ROSTER

    return frozenset(str(s).upper() for s in CORE_ROSTER)


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
        universe = _core_universe()
        positions = _alpaca_get(session, "/v2/positions")
        orders = _alpaca_get(
            session, "/v2/orders", {"status": "open", "nested": "true", "limit": 500}
        )
        ledgers = {
            engine: _read_json_blob_strict(container, name) for engine, container, name in _LEDGERS
        }
        kill_switch = _read_json_blob_strict(*_KILL_SWITCH)
    except Exception as exc:  # noqa: BLE001 -- report the type only, never a header/credential
        print(f"INCONCLUSIVE: a read failed ({type(exc).__name__}). Not treated as flat.")
        return 2

    result = classify_flatness(positions, orders, ledgers, kill_switch, universe)
    ks = result["kill_switch"]
    print(f"Flex kill switch: tripped={ks['tripped']} cleared_at={ks['cleared_at']} "
          f"trip_reason={ks['trip_reason']} tripped_at={ks['tripped_at']}")
    print(f"Alpaca positions (all): {len(positions)}   open orders (incl. legs): "
          f"{len(_flatten_orders(orders))}   Core universe: {len(universe)} symbols")
    for engine, ledger in ledgers.items():
        print(f"{engine}-ledger rows: {len(ledger)}")

    if result["verdict"] == "FLAT":
        print("\nVERDICT: FLAT -- kill switch tripped; no Flex/DayTrade positions, orders or "
              "ledger rows remain.")
        return 0
    if result["verdict"] == "NOT SAFE":
        state = (f"tripped={ks['tripped']}, cleared_at={ks['cleared_at']}"
                 if kill_switch else "blob absent")
        print(f"\nVERDICT: NOT SAFE: Flex kill switch is not tripped ({state}). "
              "Nothing is open right now, but the engine can enter again on its next tick. "
              "Trip it (runbook step 2) before relying on this reading.")
        return 1
    print("\nVERDICT: NOT FLAT")
    if not result["safe"]:
        print("  (also NOT SAFE: the Flex kill switch is not tripped)")
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
