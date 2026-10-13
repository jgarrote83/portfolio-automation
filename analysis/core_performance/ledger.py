"""Reconstruct the book's cash, share counts, per-holding P&L and turnover from Alpaca account activities (pure; no I/O).

Why activities instead of prices: the analysis is allowed benchmark bars but not bars for every holding, so holdings are never
repriced day by day. Cash is rebuilt exactly from the activity log; the market value of the holdings on any day is then
`equity - cash`, taking the equity from Alpaca's own portfolio history. Share counts come from fills, per-holding P&L from FIFO lots
valued at the last close, and every figure is reconciled against the account (cash to the cent, share counts to the share).

Activity rows used: FILL (symbol, qty, price, side, transaction_time), FEE (net_amount, date), and every other type (JNLC, CSD, CSW,
DIV, ...) as an external cash flow with its `net_amount` and `date`.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CASH_ONLY_TYPES = ("FEE",)


def et_date(ts: str) -> str:
    """The New York calendar date of an ISO timestamp (UTC 'Z' or offset)."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ET).date().isoformat()


def split_activities(acts: list[dict]) -> dict:
    """{'fills': [...sorted by time, each with et_date], 'fees': [...], 'flows': [...everything else, with 'day']}."""
    fills, fees, flows = [], [], []
    for a in acts:
        t = a.get("activity_type")
        if t == "FILL":
            fills.append({**a, "day": et_date(a["transaction_time"]), "qty": float(a["qty"]), "price": float(a["price"])})
        elif t == "FEE":
            fees.append({**a, "day": a["date"][:10], "amount": float(a["net_amount"])})
        else:
            flows.append({**a, "day": (a.get("date") or a.get("transaction_time", ""))[:10], "amount": float(a.get("net_amount") or 0.0)})
    fills.sort(key=lambda a: a["transaction_time"])
    return {"fills": fills, "fees": fees, "flows": flows}


def fill_cash(f: dict) -> float:
    n = f["qty"] * f["price"]
    return -n if f["side"] == "buy" else n


def daily_cash(parts: dict, days: list[str]) -> dict[str, float]:
    """End-of-day cash on each of `days` (ascending ET dates): every flow, fee and fill dated on or before the day."""
    events: dict[str, float] = defaultdict(float)
    for f in parts["fills"]:
        events[f["day"]] += fill_cash(f)
    for f in parts["fees"]:
        events[f["day"]] += f["amount"]
    for f in parts["flows"]:
        events[f["day"]] += f["amount"]
    keys = sorted(events)
    out, run, j = {}, 0.0, 0
    for d in days:
        while j < len(keys) and keys[j] <= d:
            run += events[keys[j]]
            j += 1
        out[d] = run
    return out


def daily_shares(fills: list[dict], symbol: str, days: list[str]) -> dict[str, float]:
    """End-of-day share count of one symbol on each of `days`."""
    ev: dict[str, float] = defaultdict(float)
    for f in fills:
        if f["symbol"] == symbol:
            ev[f["day"]] += f["qty"] if f["side"] == "buy" else -f["qty"]
    keys = sorted(ev)
    out, run, j = {}, 0.0, 0
    for d in days:
        while j < len(keys) and keys[j] <= d:
            run += ev[keys[j]]
            j += 1
        out[d] = run
    return out


def net_quantities(fills: list[dict]) -> dict[str, float]:
    q: dict[str, float] = defaultdict(float)
    for f in fills:
        q[f["symbol"]] += f["qty"] if f["side"] == "buy" else -f["qty"]
    return {s: v for s, v in q.items() if abs(v) > 1e-9}


def fifo_pnl(fills: list[dict], marks: dict[str, float]) -> dict[str, dict]:
    """Per-symbol FIFO realized P&L and the unrealized P&L of the remaining lots at `marks[symbol]` (the last close).
    A sell with no lots left (a short) is recorded in `unmatched_sell_qty` rather than invented."""
    lots: dict[str, deque] = defaultdict(deque)
    out: dict[str, dict] = defaultdict(lambda: {"realized": 0.0, "bought": 0.0, "sold": 0.0, "unmatched_sell_qty": 0.0})
    for f in fills:
        s, q, p = f["symbol"], f["qty"], f["price"]
        if f["side"] == "buy":
            lots[s].append([q, p])
            out[s]["bought"] += q * p
        else:
            out[s]["sold"] += q * p
            left = q
            while left > 1e-9 and lots[s]:
                lot = lots[s][0]
                take = min(left, lot[0])
                out[s]["realized"] += take * (p - lot[1])
                lot[0] -= take
                left -= take
                if lot[0] <= 1e-9:
                    lots[s].popleft()
            if left > 1e-9:
                out[s]["unmatched_sell_qty"] += left
    for s, d in out.items():
        qty = sum(lot[0] for lot in lots[s])
        cost = sum(lot[0] * lot[1] for lot in lots[s])
        mark = marks.get(s)
        d["qty"], d["cost_remaining"] = qty, cost
        d["market_value"] = qty * mark if (mark is not None and qty) else 0.0
        d["unrealized"] = d["market_value"] - cost if qty else 0.0
        d["total"] = d["realized"] + d["unrealized"]
    return dict(out)


def monthly_turnover(fills: list[dict], equity_by_day: dict[str, float]) -> list[dict]:
    """Per calendar month: bought and sold notional, the average of the month's daily equity, and one-way turnover =
    (bought + sold) / 2 / average equity."""
    months: dict[str, dict] = defaultdict(lambda: {"bought": 0.0, "sold": 0.0, "fills": 0})
    for f in fills:
        m = months[f["day"][:7]]
        m["bought" if f["side"] == "buy" else "sold"] += f["qty"] * f["price"]
        m["fills"] += 1
    eq_by_month: dict[str, list[float]] = defaultdict(list)
    for d, e in equity_by_day.items():
        eq_by_month[d[:7]].append(e)
    rows = []
    for m in sorted(months):
        avg = sum(eq_by_month[m]) / len(eq_by_month[m]) if eq_by_month.get(m) else None
        gross = months[m]["bought"] + months[m]["sold"]
        rows.append({"month": m, "fills": months[m]["fills"], "bought": months[m]["bought"], "sold": months[m]["sold"], "avg_equity": avg,
                     "turnover_one_way": (gross / 2.0 / avg) if avg else None})
    return rows
