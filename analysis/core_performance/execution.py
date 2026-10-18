"""Fills against the price the analyzer reasoned with (pure; no I/O).

`daily-trades/{D}.json` holds the analyzer's proposed trades for session D but **no price**: core trades are market orders
(`limit_price` null) and the file carries no reference or gap price. The analyzer prices from the snapshot it reads at 09:00 ET, whose
prices are end-of-day closes, so the price it reasoned with is the **previous session's close**. That close is used here as the reference,
explicitly as a proxy (the snapshots that hold the exact figures were not authorised for download beyond the two used for the policy).

For each proposed trade (validation status not `rejected`), the fills of the same symbol and side on session D are averaged
(quantity-weighted). Cost in basis points is signed so that positive = worse than the reference: (fill / ref - 1) for a buy and
(ref / fill - 1) for a sell; the dollar cost is the same sign times |fill - ref| x filled quantity. Because the reference is the prior close
and the orders fill after the next open, this is mostly the **overnight move between the decision's prices and the fill**, the
implementation cost of deciding on yesterday's close and trading this morning, not a measure of execution quality (paper fills have no
spread). Fills on a symbol and side with no proposed trade that day (e.g. the separate Flex engine's orders) are counted, not attributed.
"""
from __future__ import annotations


def _prev_day(days: list[str], d: str) -> str | None:
    earlier = [x for x in days if x < d]
    return earlier[-1] if earlier else None


def fills_vs_reference(trades_by_date: dict[str, list[dict]], fills: list[dict], closes: dict[str, dict[str, float]],
                       trading_days: list[str]) -> dict:
    """trades_by_date: {session date: trades[] from daily-trades}; fills: ledger fills (each with day, symbol, side, qty, price);
    closes: RAW daily closes {symbol: {date: close}}; trading_days: ascending session dates."""
    by_key: dict[tuple, list[dict]] = {}
    for f in fills:
        by_key.setdefault((f["day"], f["symbol"], f["side"]), []).append(f)
    rows, unfilled, used = [], [], set()
    for d in sorted(trades_by_date):
        for t in trades_by_date[d] or []:
            status = (t.get("validation") or {}).get("status")
            if status == "rejected":
                continue
            key = (d, str(t["symbol"]).upper(), str(t["side"]).lower())
            fs = by_key.get(key, [])
            if not fs:
                unfilled.append({"date": d, "symbol": key[1], "side": key[2], "proposed_qty": t.get("quantity"), "status": status})
                continue
            used.add(key)
            qty = sum(f["qty"] for f in fs)
            vwap = sum(f["qty"] * f["price"] for f in fs) / qty
            pd_ = _prev_day(trading_days, d)
            ref = closes.get(key[1], {}).get(pd_) if pd_ else None
            if ref is None:
                rows.append({"date": d, "symbol": key[1], "side": key[2], "proposed_qty": t.get("quantity"), "filled_qty": qty, "fill_price": vwap,
                             "ref_date": pd_, "ref_price": None, "cost_bps": None, "cost_usd": None, "notional": qty * vwap})
                continue
            sign_bps = (vwap / ref - 1.0) if key[2] == "buy" else (ref / vwap - 1.0)
            cost_usd = (vwap - ref) * qty * (1 if key[2] == "buy" else -1)
            rows.append({"date": d, "symbol": key[1], "side": key[2], "proposed_qty": t.get("quantity"), "filled_qty": qty, "fill_price": vwap,
                         "ref_date": pd_, "ref_price": ref, "cost_bps": sign_bps * 1e4, "cost_usd": cost_usd, "notional": qty * vwap})
    priced = [r for r in rows if r["cost_bps"] is not None]
    notional = sum(r["notional"] for r in priced)
    unattributed = [k for k in by_key if k not in used and k[0] in trades_by_date]

    def agg(rs):
        n = sum(r["notional"] for r in rs)
        return {"trades": len(rs), "notional": n, "cost_usd": sum(r["cost_usd"] for r in rs),
                "weighted_bps": (sum(r["cost_bps"] * r["notional"] for r in rs) / n) if n else None}
    srt = sorted(r["cost_bps"] for r in priced)
    return {"rows": rows, "unfilled": unfilled, "matched": len(rows), "priced": len(priced),
            "unattributed_fill_groups": len(unattributed), "total": agg(priced),
            "buys": agg([r for r in priced if r["side"] == "buy"]), "sells": agg([r for r in priced if r["side"] == "sell"]),
            "median_bps": (srt[len(srt) // 2] if len(srt) % 2 else (srt[len(srt) // 2 - 1] + srt[len(srt) // 2]) / 2) if srt else None,
            "proposed": sum(1 for d in trades_by_date for t in (trades_by_date[d] or []) if (t.get("validation") or {}).get("status") != "rejected"),
            "notional_total": notional}
