"""Persisted open-position state for the Flex engine.

A single blob ``flex-ledger/ledger.json`` mirroring the live Alpaca paper account
(one row per open flex symbol). STEP 0 reconciliation keeps it true to the broker;
it is rebuildable from ``TradeHistory`` if lost. ``qty_current`` is the
authoritative remaining-share count after any partial scale-out, and the resting
stop is always sized to it.
"""
from __future__ import annotations

import uuid

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
    trade_id: str | None = None,
    catalyst_score: float | None = None,
    score_components: dict | None = None,
    nomination_thesis: str = "",
) -> dict:
    """Build a fresh ledger row for a newly opened flex position.

    ``trade_id`` is generated once here (unless explicitly supplied, e.g. by a
    test) and carried unchanged through every fill and repair for this
    position's lifetime — it is the idempotency key
    ``flex.trades.record_closed_trade`` keys on, so it must never be
    regenerated later (unlike ``order_ids``, which DOES get overwritten on
    every stop replace). ``catalyst_score``/``score_components`` (session
    2026-08-10, Flex Sleeve Performance Ledger Task B) are stamped at entry
    from the funnel's ranking ledger so they carry through to the eventual
    closed-trade record — the single highest-value field for later
    weight-tuning (was `news_tone` predictive? was `momentum`?).
    """
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
        "trade_id": trade_id or f"FLEX-{entry_date}-{symbol.upper()}-{uuid.uuid4().hex[:8]}",
        "fills": [],
        "catalyst_score": catalyst_score,
        "score_components": score_components,
        "nomination_thesis": nomination_thesis,
    }
