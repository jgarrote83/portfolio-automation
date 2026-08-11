"""Flex sleeve performance record — the closed-trade ledger and the daily
sleeve mark series (session 2026-08-10, Flex Sleeve Performance Ledger).

Two persisted blobs, both in the ``flex-ledger`` container:

- ``closed-trades.json`` — an append-only list of REALIZED trades, each with
  every fill that contributed to it (a 2R scale-out means a single trade can
  realize across 2+ fills), a null-safe ``pnl_usd``/``r_multiple`` (never
  fabricated — see ``build_closed_trade``), and the ``catalyst_score`` the
  name carried at entry (the highest-value field in the schema: without it
  you can only ask "did the sleeve make money"; with it you can ask "did
  ``news_tone`` actually predict anything").
- ``equity-series.json`` — one row per trading day marking the sleeve's
  notional/unrealized/realized state, upserted by date (never interpolated;
  a day the engine didn't run is a gap, not a guess).

I/O here mirrors ``flex/ledger.py``'s established pattern (module-level
``read_*``/``write_*`` around a fixed blob, plus pure builder functions);
``reconcile.py``/``exit_state.py`` stay pure and untouched — this module is
where that I/O belongs, in the handler-adjacent persistence layer, not inside
the pure decision modules.
"""
from __future__ import annotations

from flex.exit_state import trading_days_between
from shared.storage import read_json_blob, write_json_blob

_CONTAINER = "flex-ledger"
_TRADES_BLOB = "closed-trades.json"
_EQUITY_BLOB = "equity-series.json"

_QTY_EPS = 1e-6


# --- closed-trade ledger (Task A) -------------------------------------------

def read_closed_trades() -> list[dict]:
    data = read_json_blob(_CONTAINER, _TRADES_BLOB)
    return data if isinstance(data, list) else []


def write_closed_trades(trades: list[dict]) -> None:
    write_json_blob(_CONTAINER, _TRADES_BLOB, trades)


def record_closed_trade(trade: dict) -> bool:
    """Idempotent append keyed on ``trade_id`` — the single funnel every close
    path routes through. Returns True if newly written, False if a record
    with this ``trade_id`` already exists (a re-entrant reconcile tick must
    never double-count P&L or corrupt the curve).
    """
    trade_id = trade.get("trade_id")
    trades = read_closed_trades()
    if any(t.get("trade_id") == trade_id for t in trades):
        return False
    trades.append(trade)
    write_closed_trades(trades)
    return True


def fills_from_activities(activities: list[dict], symbol: str, side: str) -> list[dict]:
    """Pure: broker FILL activities (``AlpacaClient.get_activities``) -> a
    chronological ``[{date, qty, price}, ...]`` list for one symbol+side.
    Malformed rows (unparseable qty/price) are dropped, not fabricated."""
    out: list[dict] = []
    for a in activities or ():
        if str(a.get("symbol", "")).upper() != symbol.upper():
            continue
        if str(a.get("side", "")).lower() != side.lower():
            continue
        try:
            qty = float(a.get("qty"))
            price = float(a.get("price"))
        except (TypeError, ValueError):
            continue
        out.append({
            "date": str(a.get("transaction_time") or a.get("date") or "")[:10],
            "qty": qty,
            "price": price,
        })
    out.sort(key=lambda f: f["date"])
    return out


def merge_broker_fills(
    recorded_fills: list[dict], broker_sells: list[dict], closing_reason: str,
) -> list[dict]:
    """Reconcile the engine's own fill record against broker-truth sell
    activity for the same symbol since entry (pure — "reconciliation is
    broker truth," not what the engine intended to do).

    ``recorded_fills`` is whatever the engine itself already logged as it
    happened (e.g. a scale_out it submitted and got a fill price back for).
    ``broker_sells`` is the FULL sell-fill history for the symbol since
    entry. Any broker sell already covered by ``recorded_fills``'s cumulative
    qty is skipped (already accounted for); anything beyond that is
    broker-confirmed and appended — the LAST one chronologically labeled
    ``closing_reason`` (the close the engine didn't itself witness — almost
    always the resting protective stop), any earlier unaccounted one labeled
    ``"scale_out"`` (a scale-out the engine's own record missed).
    """
    recorded_qty = round(sum(f.get("qty") or 0 for f in recorded_fills), 6)
    cumulative = 0.0
    extra: list[dict] = []
    for s in sorted(broker_sells or (), key=lambda f: f.get("date") or ""):
        cumulative += s.get("qty") or 0
        if cumulative <= recorded_qty + _QTY_EPS:
            continue
        extra.append(s)
    merged = list(recorded_fills)
    for i, s in enumerate(extra):
        reason = closing_reason if i == len(extra) - 1 else "scale_out"
        merged.append({
            "date": s.get("date"), "qty": s.get("qty"), "price": s.get("price"),
            "reason": reason,
        })
    return merged


def build_closed_trade(
    ledger_entry: dict, fills: list[dict], exit_reason: str, closed_date: str,
) -> dict:
    """Pure: aggregate a ledger row's entry + accumulated fills into a closed-
    trade record. ``pnl_usd``/``r_multiple`` are ``None`` (never fabricated —
    "a trade whose exit price can't be determined is recorded with pnl_usd:
    null and a reason") whenever ANY fill lacks a price, or there are no
    fills at all, or ``entry_price``/``risk_per_share`` is missing.
    """
    entry_price = ledger_entry.get("entry_price")
    risk_per_share = ledger_entry.get("risk_per_share")
    sell_qty_total = sum(f.get("qty") or 0 for f in fills)

    pnl_usd = None
    pnl_unavailable_reason = None
    if not fills:
        pnl_unavailable_reason = "no_fills_recorded"
    elif entry_price is None:
        pnl_unavailable_reason = "missing_entry_price"
    elif any(f.get("price") is None for f in fills):
        pnl_unavailable_reason = "missing_fill_price"
    elif sell_qty_total <= _QTY_EPS:
        pnl_unavailable_reason = "zero_qty_realized"
    else:
        proceeds = sum(f["qty"] * f["price"] for f in fills)
        cost = float(entry_price) * sell_qty_total
        pnl_usd = round(proceeds - cost, 2)

    r_multiple = None
    if (
        pnl_usd is not None
        and risk_per_share
        and float(risk_per_share) > 0
        and sell_qty_total > 0
    ):
        pnl_per_share = pnl_usd / sell_qty_total
        r_multiple = round(pnl_per_share / float(risk_per_share), 4)

    return {
        "trade_id": ledger_entry.get("trade_id"),
        "symbol": ledger_entry.get("symbol"),
        "entry_date": ledger_entry.get("entry_date"),
        "entry_price": entry_price,
        "qty_initial": ledger_entry.get("qty_initial"),
        "initial_stop": ledger_entry.get("initial_stop"),
        "risk_per_share": risk_per_share,
        "fills": fills,
        "closed_date": closed_date,
        "exit_reason": exit_reason,
        "pnl_usd": pnl_usd,
        "pnl_unavailable_reason": pnl_unavailable_reason,
        "r_multiple": r_multiple,
        "holding_days": trading_days_between(ledger_entry.get("entry_date"), closed_date),
        "catalyst_score": ledger_entry.get("catalyst_score"),
        "score_components": ledger_entry.get("score_components"),
        "nomination_thesis": ledger_entry.get("nomination_thesis"),
    }


# --- daily sleeve mark series (Task C) --------------------------------------

def read_equity_series() -> list[dict]:
    data = read_json_blob(_CONTAINER, _EQUITY_BLOB)
    return data if isinstance(data, list) else []


def write_equity_series(series: list[dict]) -> None:
    write_json_blob(_CONTAINER, _EQUITY_BLOB, series)


def upsert_equity_point(point: dict) -> None:
    """Replace-or-append by date, keeping the series sorted. Called on every
    in-hours tick (idempotent overwrite) so the day's row always reflects the
    most recent successful tick — converges naturally to "last in-hours
    tick" without needing to detect which tick is actually last (decision
    gate 1, PR body)."""
    series = read_equity_series()
    d = point.get("date")
    series = [p for p in series if p.get("date") != d]
    series.append(point)
    series.sort(key=lambda p: p.get("date") or "")
    write_equity_series(series)


def build_sleeve_mark(
    date_str: str, positions: list[dict], ledger: dict,
    closed_trades: list[dict], equity: float | None,
) -> dict:
    """Pure: today's sleeve mark from broker positions (filtered to the
    flex-managed symbol set) + the closed-trade ledger's running realized
    total. Never fabricates — a position/equity read that fails upstream
    just yields 0.0/None here, same as the rest of this engine's degrade-
    gracefully doctrine."""
    flex_syms = set(ledger.keys())
    notional = 0.0
    unrealized = 0.0
    for p in positions or ():
        sym = str(p.get("symbol", "")).upper()
        if sym not in flex_syms:
            continue
        try:
            notional += abs(float(p.get("market_value") or 0))
            unrealized += float(p.get("unrealized_pl") or 0)
        except (TypeError, ValueError):
            continue
    cumulative_realized = sum(
        float(t["pnl_usd"]) for t in (closed_trades or ()) if t.get("pnl_usd") is not None
    )
    return {
        "date": date_str,
        "sleeve_notional_usd": round(notional, 2),
        "unrealized_usd": round(unrealized, 2),
        "cumulative_realized_usd": round(cumulative_realized, 2),
        "total_equity": round(float(equity), 2) if equity else None,
        "open_positions": len(ledger),
        "closed_trades_to_date": len(closed_trades or ()),
    }
