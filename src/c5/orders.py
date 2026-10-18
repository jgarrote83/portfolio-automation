"""C5 orders, reconciliation and verification (pure).

Orders are market-on-close (`time_in_force: cls`), whole shares, sells first then buys, with client order ids
`C5-<session yyyymmdd>-<side>-<symbol>`. The id is keyed on the SESSION the order executes in, not on the decision that produced it, so
two decisions acting on the same session (first enable plus a re-align, a pause and a resume) produce the same id and Alpaca itself
rejects the second as a duplicate. The target gets 100% of equity less a small cash buffer (`CASH_BUFFER_PCT`, because an MOC order fills
at a closing price not known at submission); whatever whole shares leave over stays as cash.
"""
from __future__ import annotations

import math
from datetime import date

CASH_BUFFER_PCT = 0.5
ID_PREFIX = "C5-"
OPEN_STATUSES = ("new", "accepted", "pending_new", "partially_filled", "held", "accepted_for_bidding", "pending_replace")


def session_tag(day: date) -> str:
    return day.strftime("%Y%m%d")


def client_id(tag: str, side: str, symbol: str) -> str:
    return f"{ID_PREFIX}{tag}-{side}-{symbol}"


def is_engine_order(order: dict) -> bool:
    return str(order.get("client_order_id") or "").startswith(ID_PREFIX)


def is_open(order: dict) -> bool:
    return str(order.get("status") or "").lower() in OPEN_STATUSES


def plan(positions: dict[str, float], target: str, equity: float, price: float, tag: str, buffer_pct: float = CASH_BUFFER_PCT) -> list[dict]:
    """Orders to move the account to 100% `target`: every other holding sold in full, the target bought (or trimmed) to
    floor(equity x (1 - buffer) / price) shares. Sells are listed before buys."""
    if equity <= 0 or price <= 0:
        raise ValueError("equity and price must be positive")
    want = math.floor(equity * (1.0 - buffer_pct / 100.0) / price)
    sells, buys = [], []
    for sym, qty in sorted(positions.items()):
        q = int(round(qty))
        if sym != target and q > 0:
            sells.append({"symbol": sym, "side": "sell", "qty": q})
    have = int(round(positions.get(target, 0)))
    if want > have:
        buys.append({"symbol": target, "side": "buy", "qty": want - have})
    elif want < have:
        sells.append({"symbol": target, "side": "sell", "qty": have - want})
    out = sells + buys
    for o in out:
        o.update({"type": "market", "time_in_force": "cls", "client_order_id": client_id(tag, o["side"], o["symbol"])})
    return out


def reconcile(positions: dict[str, float], ledger: dict) -> list[dict]:
    """Positions vs the ledger's expected holdings ({symbol: qty}); every difference is a mismatch."""
    expected = {s: float(q) for s, q in (ledger.get("holdings") or {}).items()}
    out = []
    for s in sorted(set(positions) | set(expected)):
        a, b = float(positions.get(s, 0.0)), expected.get(s, 0.0)
        if abs(a - b) > 1e-6:
            out.append({"symbol": s, "account": a, "ledger": b})
    return out


def holds_target(positions: dict[str, float], target: str) -> bool:
    live = {s: q for s, q in positions.items() if abs(q) > 1e-9}
    return set(live) == {target} and live[target] > 0
