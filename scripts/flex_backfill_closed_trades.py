"""One-time repair of `flex-ledger/closed-trades.json` from broker truth.

Why: `flex/handler.py::_finalize_closed_trade` reconciles a closed trade against Alpaca FILL activity exactly ONCE, at the tick that marks it
closed, and never again. When the exit fill has not posted yet at that instant (a time-stop sell submitted that same tick; a stop that filled
seconds earlier; a position the reconcile treated as gone before its exit filled), the record is written with `fills: []`,
`pnl_usd: null`, `pnl_unavailable_reason: "no_fills_recorded"`, and stays that way. Observed for all four closed trades 2026-09-14..10-08.

What this does: for every record with no fills and no P&L, it re-runs the engine's OWN pure functions (`flex.trades.fills_from_activities`,
`merge_broker_fills`, `build_closed_trade`) on the now-complete activity log, using the sell fills of that symbol from the trade's entry date up
to (not including) the next entry of the same symbol. A record is repaired only when the sells it finds add up exactly to the shares its entry
bought; anything else is left untouched and reported. `closed_date` becomes the date of the last exit fill (the date the trade really
closed), and a `repaired` block records what changed. Idempotent: an already-repaired or already-priced record is never touched.

    # read-only: download the blob, fetch activity, write the repaired copy and a report; uploads nothing
    PYTHONPATH=src python scripts/flex_backfill_closed_trades.py --closed closed-trades.json --ledger ledger.json \
        --out closed-trades.repaired.json --after 2026-09-01
Alpaca keys come from the ALPACA_API_KEY / ALPACA_API_SECRET environment variables (never printed). Uploading the repaired file is a separate,
explicit step.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from flex import trades as flex_trades
from flex.exit_state import trading_days_between

PAPER = "https://paper-api.alpaca.markets"


def _next_entry_dates(closed: list[dict], ledger: dict) -> dict[str, list[str]]:
    by: dict[str, list[str]] = {}
    for t in closed:
        by.setdefault(str(t.get("symbol", "")).upper(), []).append(t.get("entry_date") or "")
    for k, row in (ledger or {}).items():
        by.setdefault(str(row.get("symbol") or k).upper(), []).append(row.get("entry_date") or "")
    return {s: sorted(set(v)) for s, v in by.items()}


def _window(activities: list[dict], symbol: str, start: str, end: str | None) -> list[dict]:
    """Activities of `symbol` whose transaction date is >= start and < end (end None = open-ended)."""
    out = []
    for a in activities:
        if str(a.get("symbol", "")).upper() != symbol:
            continue
        d = str(a.get("transaction_time") or a.get("date") or "")[:10]
        if d >= start and (end is None or d < end):
            out.append(a)
    return out


def repair(closed: list[dict], ledger: dict, activities: list[dict], now: str | None = None) -> tuple[list[dict], list[dict]]:
    """(repaired closed-trades list, report rows). Pure."""
    now = now or datetime.now(timezone.utc).isoformat()
    entries = _next_entry_dates(closed, ledger)
    out, report = [], []
    for t in closed:
        sym = str(t.get("symbol", "")).upper()
        if t.get("fills") or t.get("pnl_usd") is not None or t.get("repaired"):
            out.append(t)
            report.append({"trade_id": t.get("trade_id"), "symbol": sym, "action": "untouched (already has fills or P&L)"})
            continue
        start = t.get("entry_date") or ""
        later = [d for d in entries.get(sym, []) if d > start]
        end = later[0] if later else None
        acts = _window(activities, sym, start, end)
        buys = flex_trades.fills_from_activities(acts, sym, "buy")
        sells = flex_trades.fills_from_activities(acts, sym, "sell")
        bought = round(sum(b["qty"] for b in buys), 6)
        sold = round(sum(s["qty"] for s in sells), 6)
        if not buys or not sells or abs(bought - sold) > 1e-6:
            out.append(t)
            report.append({"trade_id": t.get("trade_id"), "symbol": sym, "action": f"untouched (bought {bought}, sold {sold} in {start}..{end or 'open'})"})
            continue
        entry_price = round(sum(b["qty"] * b["price"] for b in buys) / bought, 4)
        fills = flex_trades.merge_broker_fills([], sells, t.get("exit_reason") or "unknown")
        closed_date = max(f["date"] for f in fills)
        rebuilt = flex_trades.build_closed_trade({**t, "entry_price": entry_price}, fills, t.get("exit_reason"), closed_date)
        rebuilt["holding_days"] = trading_days_between(t.get("entry_date"), closed_date)
        rebuilt["repaired"] = {"at": now, "source": "Alpaca FILL activities (broker truth), scripts/flex_backfill_closed_trades.py",
                               "previous": {k: t.get(k) for k in ("closed_date", "entry_price", "pnl_usd", "pnl_unavailable_reason", "holding_days")}}
        out.append(rebuilt)
        report.append({"trade_id": t.get("trade_id"), "symbol": sym, "action": "repaired", "entry_price": entry_price, "shares": bought,
                       "exit_fills": fills, "closed_date": f"{t.get('closed_date')} -> {closed_date}", "pnl_usd": rebuilt["pnl_usd"],
                       "r_multiple": rebuilt["r_multiple"]})
    return out, report


def fetch_activities(after: str) -> list[dict]:
    import requests
    h = {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_API_SECRET"]}
    rows, params = [], {"activity_type": "FILL", "page_size": 100, "after": after}
    for _ in range(200):
        r = requests.get(f"{PAPER}/v2/account/activities/FILL", headers=h, params=params, timeout=30)
        r.raise_for_status()
        page = r.json()
        if not page:
            break
        rows += page
        if len(page) < 100:
            break
        params = {"activity_type": "FILL", "page_size": 100, "page_token": page[-1]["id"]}
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--closed", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--after", required=True, help="fetch FILL activity after this date (YYYY-MM-DD), before the first trade's entry")
    a = ap.parse_args(argv)
    closed = json.load(open(a.closed, encoding="utf-8"))
    ledger = json.load(open(a.ledger, encoding="utf-8"))
    fixed, report = repair(closed, ledger, fetch_activities(a.after))
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(fixed, f, indent=1)
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
