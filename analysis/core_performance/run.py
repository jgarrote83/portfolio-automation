"""Core book vs SPY: assemble the inputs fetched by `fetch.py`, compute every statistic, write `.review/core-vs-spy-analysis.md`.

    python -m analysis.core_performance.run [--snapshot data/replay/2026-10-02.json] [--inception-snapshot PATH] [--out .review/core-vs-spy-analysis.md]

Offline: reads only `analysis/core_performance/data/*.json` (written by `fetch`), an optional local snapshot, and, for the policy
portfolio, an optional inception-day snapshot. It performs no network or Azure access and changes nothing under `src/`.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import attribution, execution, ledger, metrics

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(__file__).resolve().parent / "data"
START_EQUITY = 100_000.0
ALL_WEATHER = {"VTI": 0.30, "TLT": 0.40, "IEF": 0.15, "GLD": 0.075, "DBC": 0.075}
SIXTY_FORTY = {"SPY": 0.60, "AGG": 0.40}


# ======================================================================================================== loading
def load_raw(data_dir: Path = DATA_DIR) -> dict:
    names = ("account", "portfolio_history", "activities", "positions", "bars_all", "bars_raw", "fetch_meta")
    return {n: json.loads((data_dir / f"{n}.json").read_text(encoding="utf-8")) for n in names}


def core_universe() -> tuple[set[str], set[str]]:
    """(Core roster = every role's full pool, LEGACY_EXITS) from the live config via shared.quadrants (read-only import)."""
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from shared import quadrants as q
    return set(q.CORE_ROSTER), set(q.LEGACY_EXITS)


def bars_by_date(bars: dict[str, list[dict]]) -> dict[str, dict[str, float]]:
    """{symbol: {session date: close}} from Alpaca daily bars (the bar timestamp is midnight New York time in UTC, so [:10] is the date)."""
    return {s: {b["t"][:10]: float(b["c"]) for b in rows} for s, rows in bars.items()}


def equity_by_day(history: dict) -> dict[str, float]:
    """{session date: equity} from portfolio history. Timestamps are midnight UTC of the NEXT day, so the New York date is the session."""
    out = {}
    for t, e in zip(history["timestamp"], history["equity"]):
        if e:
            out[ledger.et_date(datetime.fromtimestamp(t, timezone.utc).isoformat())] = float(e)
    return out


# ===================================================================================================== computation
def window_stats(values: list[float], spy: list[float]) -> dict:
    r, rs = metrics.returns(values), metrics.returns(spy)
    mdd = metrics.max_drawdown(values)
    sr = metrics.sharpe(r)
    reg = metrics.regression(r, rs)
    return {"n_returns": len(r), "total_return": metrics.total_return(values), "ann_vol": metrics.annualized_vol(r), "sharpe": sr,
            "sharpe_ci95": metrics.sharpe_ci95(sr, len(r)) if sr is not None else None, "max_drawdown": mdd, **reg}


def build(raw: dict, *, inception: str = "2026-05-26", validate_as_of: str | None = "2026-10-01", snapshot: dict | None = None,
          inception_snapshot: dict | None = None, policy_bars: dict | None = None, policy_snapshot: dict | None = None,
          policy_base: str | None = None, trades_by_date: dict | None = None, trade_bars: dict | None = None) -> dict:
    core, legacy = core_universe()
    adj, rawp = bars_by_date(raw["bars_all"]), bars_by_date(raw["bars_raw"])
    spy_days = sorted(adj["SPY"])
    base = max(d for d in spy_days if d < inception)
    eq = equity_by_day(raw["portfolio_history"])
    last = min(spy_days[-1], max(eq))
    days = [d for d in spy_days if base <= d <= last]
    gaps = [d for d in days if d not in eq]
    if gaps:
        raise ValueError(f"portfolio history has no equity for {gaps[:5]} ({len(gaps)} gaps); refusing to interpolate")
    parts = ledger.split_activities(raw["activities"])
    flows_in_window = [f for f in parts["flows"] if f["day"] > base]
    cash = ledger.daily_cash(parts, days)
    sgov_sh = ledger.daily_shares(parts["fills"], "SGOV", days)
    sgov_val = {d: sgov_sh[d] * rawp["SGOV"][d] for d in days}
    E = [eq[d] for d in days]
    spy_raw_v = [rawp["SPY"][d] for d in days]
    spy_adj_v = [adj["SPY"][d] for d in days]
    sgov_raw_v = [rawp["SGOV"][d] for d in days]
    sgov_adj_v = [adj["SGOV"][d] for d in days]
    r_book, r_spy, r_sgov = metrics.returns(E), metrics.returns(spy_raw_v), metrics.returns(sgov_raw_v)

    def upto(as_of: str) -> int:
        return max(i for i, d in enumerate(days) if d <= as_of)

    res: dict = {"base": base, "first": days[1], "last": last, "days": days, "gaps": gaps, "external_flows_in_window": flows_in_window,
                 "trading_days_in_window": len(days) - 1, "inception_equity": E[0]}

    # ---- reconciliation of the reconstruction with the account
    final_cash_acct = float(raw["account"]["cash"])
    total_cash_all = ledger.daily_cash(parts, [max(days[-1], "9999-12-31")])["9999-12-31"]
    res["reconciliation"] = {"cash_reconstructed_all_activities": total_cash_all, "cash_account": final_cash_acct,
                             "cash_diff": total_cash_all - final_cash_acct,
                             "net_qty_from_fills": ledger.net_quantities(parts["fills"]),
                             "positions_qty": {p["symbol"]: float(p["qty"]) for p in raw["positions"]}}
    rq, pq = res["reconciliation"]["net_qty_from_fills"], res["reconciliation"]["positions_qty"]
    res["reconciliation"]["qty_mismatches"] = {s: (rq.get(s, 0.0), pq.get(s, 0.0)) for s in set(rq) | set(pq) if abs(rq.get(s, 0.0) - pq.get(s, 0.0)) > 1e-6}

    # ---- scoreboards (latest and the snapshot-validation date)
    def board(as_of: str) -> dict:
        i = upto(as_of)
        e, s_raw, s_adj = E[: i + 1], spy_raw_v[: i + 1], spy_adj_v[: i + 1]
        b = window_stats(e, s_raw)
        return {"as_of": days[i], "book": b, "spy_price": window_stats(s_raw, s_raw), "spy_total": window_stats(s_adj, s_raw),
                "excess_price_pp": (b["total_return"] - metrics.total_return(s_raw)) * 100.0,
                "excess_total_pp": (b["total_return"] - metrics.total_return(s_adj)) * 100.0, "i": i}
    res["board_latest"] = board(last)
    if validate_as_of:
        res["board_validate"] = board(validate_as_of)

    # ---- weights over time
    res["weights"] = []
    for i, d in enumerate(days):
        res["weights"].append({"day": d, "equity": E[i], "cash": cash[d], "sgov": sgov_val[d], "cash_pct": 100 * cash[d] / E[i],
                               "sgov_pct": 100 * sgov_val[d] / E[i], "sleeve_pct": 100 * (cash[d] + sgov_val[d]) / E[i],
                               "holdings_pct": 100 * (E[i] - cash[d]) / E[i], "risk_assets_pct": 100 * (E[i] - cash[d] - sgov_val[d]) / E[i]})

    # ---- daily decomposition (cash term exact; policy terms only when an inception snapshot and its bars are supplied)
    # The policy portfolio is the first snapshot that carries `reference_weights`, held buy-and-hold from `policy_base` (the close at or
    # before that snapshot was collected). Days up to and including the base have no policy: no reference existed then.
    policy_r: dict[str, float] = {}
    policy_info = {"available": False}
    if policy_snapshot is not None and policy_bars is not None and policy_base is not None:
        w = policy_weights(policy_snapshot)
        pb = bars_by_date(policy_bars)
        pdays = [d for d in days if d >= policy_base]
        missing = [(s, d) for d in pdays for s in w if d not in pb.get(s, {})]
        if missing:
            raise ValueError(f"policy constituents lack bars: {missing[:5]}")
        vals = metrics.value_index(pb, w, pdays)
        policy_r = {pdays[i]: vals[i] / vals[i - 1] - 1.0 for i in range(1, len(pdays))}
        policy_info = {"available": True, "snapshot_date": policy_snapshot.get("date"), "collected_at": policy_snapshot.get("collected_at"),
                       "base": policy_base, "weights": w, "first_policy_day": pdays[1] if len(pdays) > 1 else None,
                       "total_return": vals[-1] / vals[0] - 1.0, "excluded": sorted(k for k in policy_snapshot["reference_weights"]["target_weights_pct"] if k not in w)}
    terms = []
    for t in range(1, len(days)):
        d0 = days[t - 1]
        terms.append(attribution.daily_terms(E[t - 1], cash[d0], sgov_val[d0], r_book[t - 1], r_spy[t - 1], r_sgov[t - 1], policy_r.get(days[t])))
    res["terms"] = terms

    def decomp(as_of: str) -> dict:
        i = upto(as_of)
        return attribution.decompose(terms[:i], metrics.total_return(E[: i + 1]), metrics.total_return(spy_raw_v[: i + 1]))
    res["decomp_latest"] = decomp(last)
    if validate_as_of:
        res["decomp_validate"] = decomp(validate_as_of)
        i = upto(validate_as_of)
        avg_lit = sum(w["cash_pct"] for w in res["weights"][1: i + 1]) / i
        avg_sleeve = sum(w["sleeve_pct"] for w in res["weights"][1: i + 1]) / i
        r_cash_cum = (sgov_raw_v[i] / sgov_raw_v[0] - 1) * 100
        r_spy_cum = (spy_raw_v[i] / spy_raw_v[0] - 1) * 100
        res["existing_method_validate"] = {
            "avg_literal_cash_pct": avg_lit, "avg_sleeve_pct": avg_sleeve, "sgov_return_pct": r_cash_cum, "spy_return_pct": r_spy_cum,
            "with_literal_cash": attribution.existing_method(avg_lit, r_cash_cum, r_spy_cum)["cash_contribution_pp"],
            "with_sleeve": attribution.existing_method(avg_sleeve, r_cash_cum, r_spy_cum)["cash_contribution_pp"]}
        ea = (((snapshot or {}).get("performance") or {}).get("excess_attribution") or {}).get("inception")
        if ea:
            spy_snap = (snapshot["performance"]).get("spy_return_since_inception_pct")
            res["existing_method_validate"].update({
                "snapshot_avg_cash_pct": ea["avg_cash_pct"], "snapshot_cash_contribution_pp": ea["cash_contribution_pp"],
                "snapshot_spy_return_pct": spy_snap,
                "recomputed_with_snapshot_inputs": attribution.existing_method(ea["avg_cash_pct"], r_cash_cum, spy_snap)["cash_contribution_pp"]})

    months: dict[str, list[int]] = {}
    for t in range(1, len(days)):
        months.setdefault(days[t][:7], []).append(t)
    res["monthly_terms"] = []
    for m, idx in sorted(months.items()):
        lo, hi = idx[0] - 1, idx[-1]
        res["monthly_terms"].append({"month": m, "days": len(idx), "book_pct": (E[hi] / E[lo] - 1) * 100, "spy_pct": (spy_raw_v[hi] / spy_raw_v[lo] - 1) * 100,
                                     "excess_sum_pp": 100 * sum(terms[t - 1]["excess"] for t in idx), "cash_pp": 100 * sum(terms[t - 1]["cash"] for t in idx),
                                     "invested_pp": 100 * sum(terms[t - 1]["invested"] for t in idx)})
    sgov_total_pct = (sgov_adj_v[-1] / sgov_adj_v[0] - 1) * 100
    sgov_price_pct = (sgov_raw_v[-1] / sgov_raw_v[0] - 1) * 100
    avg_sgov_w = sum(w["sgov_pct"] for w in res["weights"][1:]) / (len(days) - 1)
    res["sgov_yield_artifact"] = {"sgov_price_return_pct": sgov_price_pct, "sgov_total_return_pct": sgov_total_pct, "distribution_gap_pp": sgov_total_pct - sgov_price_pct,
                                  "avg_sgov_weight_pct": avg_sgov_w, "missing_yield_pp_of_book": avg_sgov_w / 100 * (sgov_total_pct - sgov_price_pct)}

    # ---- benchmarks over the full window (price and total return where the symbol pays distributions)
    month_ends = metrics.month_end_dates(days)
    bench = {}

    def add(name, values, note=""):
        r = metrics.returns(values)
        sr = metrics.sharpe(r)
        bench[name] = {"total_return": metrics.total_return(values), "ann_vol": metrics.annualized_vol(r), "sharpe": sr,
                       "max_drawdown": metrics.max_drawdown(values)["max_drawdown"], "note": note}
        bench[name]["corr_with_book"] = metrics.regression(r_book, r)["corr"]
    add("Book (Alpaca equity)", E, "paper account: no dividends credited")
    add("SPY, price return", spy_raw_v, "like-for-like with the book (no dividends)")
    add("SPY, total return", spy_adj_v, "split- and dividend-adjusted")
    add("60/40 SPY/AGG, monthly rebalanced", metrics.value_index(adj, SIXTY_FORTY, days, month_ends), "total return")
    add("All-weather proxy, monthly rebalanced", metrics.value_index(adj, ALL_WEATHER, days, month_ends), "30 VTI / 40 TLT / 15 IEF / 7.5 GLD / 7.5 DBC, total return")
    vs = metrics.volatility_scaled(metrics.returns(spy_adj_v), metrics.returns(sgov_adj_v), metrics.annualized_vol(r_book))
    sv = [1.0]
    for r in vs["returns"]:
        sv.append(sv[-1] * (1 + r))
    add("SPY scaled to the book's volatility", sv, f"k = {vs['k']:.3f} of SPY total return, remainder in SGOV total return (ex-post k)")
    res["benchmarks"], res["vol_scale_k"] = bench, vs["k"]
    res["sgov_cash_return_pct"] = (sgov_raw_v[-1] / sgov_raw_v[0] - 1) * 100
    res["spy_dividend_gap_pp"] = (metrics.total_return(spy_adj_v) - metrics.total_return(spy_raw_v)) * 100

    # ---- contribution by holding (FIFO lots valued at the position prices of the fetch, which reconcile to the account's equity then)
    marks = {p["symbol"]: float(p["current_price"]) for p in raw["positions"]}
    fifo = ledger.fifo_pnl(parts["fills"], marks)
    total_fees = sum(f["amount"] for f in parts["fees"])
    mv_now = sum(v["market_value"] for v in fifo.values())
    equity_now = float(raw["account"]["equity"])
    res["holdings"] = {"fifo": fifo, "fees_total": total_fees, "market_value_now": mv_now, "fetched_utc": raw["fetch_meta"]["fetched_utc"],
                       "identity": {"equity_now": equity_now, "cash_now": final_cash_acct, "cash_plus_mv": final_cash_acct + mv_now,
                                    "diff": final_cash_acct + mv_now - equity_now,
                                    "pnl_total_after_fees": sum(v["total"] for v in fifo.values()) + total_fees,
                                    "equity_minus_start": equity_now - E[0]},
                       "groups": {}}
    groups = {"core_current": 0.0, "legacy_exits": 0.0, "off_roster": 0.0}
    gcount = {"core_current": 0, "legacy_exits": 0, "off_roster": 0}
    for s, v in fifo.items():
        g = "legacy_exits" if s in legacy else ("core_current" if s in core else "off_roster")
        groups[g] += v["total"]
        gcount[g] += 1
    res["holdings"]["groups"] = {g: {"pnl": groups[g], "symbols": gcount[g]} for g in groups}
    res["non_core_fills"] = sorted({f["symbol"] for f in parts["fills"] if f["symbol"] not in core and f["symbol"] not in legacy})
    nca: dict[str, dict] = {}
    for f in parts["fills"]:
        if f["symbol"] in res["non_core_fills"]:
            d_ = nca.setdefault(f["symbol"], {"first": f["day"], "last": f["day"], "bought": 0.0, "sold": 0.0, "fills": 0})
            d_["last"] = f["day"]
            d_["fills"] += 1
            d_["bought" if f["side"] == "buy" else "sold"] += f["qty"] * f["price"]
    open_qty = {p["symbol"]: (float(p["qty"]), float(p["market_value"])) for p in raw["positions"]}
    for sym, d_ in nca.items():
        d_["open_qty"], d_["open_value"] = open_qty.get(sym, (0.0, 0.0))
    res["non_core_activity"] = nca
    res["turnover"] = ledger.monthly_turnover([f for f in parts["fills"] if f["day"] <= last], {d: eq[d] for d in days[1:]})
    res["fees"] = {"count": len(parts["fees"]), "total": total_fees}
    res["fill_count"] = len(parts["fills"])
    res["buys_sells"] = {"buys": sum(1 for f in parts["fills"] if f["side"] == "buy"), "sells": sum(1 for f in parts["fills"] if f["side"] == "sell")}
    by_month: dict[str, list[dict]] = {}
    for w in res["weights"][1:]:
        by_month.setdefault(w["day"][:7], []).append(w)
    res["monthly_weights"] = [{"month": m, "days": len(ws), **{k: sum(w[k] for w in ws) / len(ws) for k in ("cash_pct", "sgov_pct", "sleeve_pct", "holdings_pct", "risk_assets_pct")}} for m, ws in sorted(by_month.items())]
    res["leverage_days"] = [w["day"] for w in res["weights"] if w["cash"] < 0]

    # ---- the SPY baseline question: the pre-trade equity is the 5/22 close level; the existing scoreboard's SPY base is the first-day close
    first_close = spy_raw_v[1]
    res["baseline"] = {"base_date": days[0], "base_spy": spy_raw_v[0], "first_day": days[1], "first_day_spy": first_close,
                       "spy_move_base_to_first_day_pct": (first_close / spy_raw_v[0] - 1) * 100,
                       "spy_to_last_from_base_pct": (spy_raw_v[-1] / spy_raw_v[0] - 1) * 100,
                       "spy_to_last_from_first_day_pct": (spy_raw_v[-1] / first_close - 1) * 100}
    res["baseline"]["excess_flattery_pp"] = res["baseline"]["spy_to_last_from_base_pct"] - res["baseline"]["spy_to_last_from_first_day_pct"]

    # ---- snapshot echo and validation
    if snapshot is not None:
        res["snapshot"] = snapshot_echo(snapshot)
        perf = snapshot.get("performance") or {}
        if validate_as_of and perf.get("available"):
            i = upto(validate_as_of)
            pa = snapshot.get("paper_account") or {}
            res["validation"] = {
                "snapshot_date": snapshot.get("date"), "as_of_close": days[i],
                "snapshot_return_pct": perf.get("return_since_inception_pct"), "snapshot_spy_return_pct": perf.get("spy_return_since_inception_pct"),
                "snapshot_excess_pp": perf.get("excess_vs_spy_pp"), "snapshot_max_drawdown_pct": perf.get("max_drawdown_pct"),
                "snapshot_equity": pa.get("equity"), "snapshot_last_equity": pa.get("last_equity"),
                "alpaca_equity_at_close": E[i], "book_return_close_pct": (E[i] / E[0] - 1) * 100,
                "book_return_from_snapshot_equity_pct": (float(pa["equity"]) / E[0] - 1) * 100 if pa.get("equity") else None,
                "book_return_from_snapshot_last_equity_pct": (float(pa["last_equity"]) / E[0] - 1) * 100 if pa.get("last_equity") else None,
                "spy_return_from_base_close_pct": (spy_raw_v[i] / spy_raw_v[0] - 1) * 100,
                "spy_return_from_first_day_close_pct": (spy_raw_v[i] / spy_raw_v[1] - 1) * 100,
                "max_drawdown_close_pct": metrics.max_drawdown(E[: i + 1])["max_drawdown"] * 100}
    res["policy"] = policy_info
    res["inception_snapshot"] = {"given": inception_snapshot is not None,
                                 "has_reference_weights": bool((inception_snapshot or {}).get("reference_weights"))}
    if trades_by_date is not None and trade_bars is not None:
        res["execution"] = execution.fills_vs_reference(trades_by_date, parts["fills"], bars_by_date(trade_bars), spy_days)
    return res


POLICY_EXCLUDED = ("SGOV", "__cash__")


def policy_weights(snapshot: dict) -> dict[str, float]:
    """Reference weights of a snapshot as a fully invested RISK-ASSET policy: the per-ticker targets, normalised to 1, without the cash
    sleeve (SGOV and literal cash). The cash sleeve is already measured by term (a); keeping SGOV in the policy would count it twice."""
    tw = {s: float(w) for s, w in snapshot["reference_weights"]["target_weights_pct"].items() if float(w) > 0 and s not in POLICY_EXCLUDED}
    total = sum(tw.values())
    return {s: w / total for s, w in tw.items()}


def snapshot_echo(s: dict) -> dict:
    keys = ("date", "performance", "quadrant_performance", "track_record", "override_record", "paper_account", "quadrant_allocation", "pnl_decomposition")
    out = {k: s.get(k) for k in keys}
    if out.get("paper_account"):
        out["paper_account"] = {k: v for k, v in out["paper_account"].items() if k != "positions"}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="analysis.core_performance.run", description=__doc__.split("\n")[0])
    ap.add_argument("--snapshot", default=str(REPO_ROOT / "data" / "replay" / "2026-10-02.json"))
    ap.add_argument("--inception-snapshot", help="daily-snapshots/<inception>.json; used for (b)/(c) when it carries reference_weights")
    ap.add_argument("--policy-snapshot", default=str(REPO_ROOT / "data" / "replay" / "2026-07-01.json"),
                    help="the first snapshot that carries reference_weights (used when the inception snapshot has none)")
    ap.add_argument("--trades-dir", default=str(REPO_ROOT / "data" / "replay" / "daily-trades"), help="daily-trades/*.json, for fills vs reference")
    ap.add_argument("--out", default=str(REPO_ROOT / ".review" / "core-vs-spy-analysis.md"))
    args = ap.parse_args(argv)
    from . import report
    raw = load_raw()
    snap = json.loads(Path(args.snapshot).read_text(encoding="utf-8")) if args.snapshot and Path(args.snapshot).is_file() else None
    inc = json.loads(Path(args.inception_snapshot).read_text(encoding="utf-8")) if args.inception_snapshot else None
    pol = inc if inc is not None and inc.get("reference_weights") else None
    if pol is None and args.policy_snapshot and Path(args.policy_snapshot).is_file():
        pol = json.loads(Path(args.policy_snapshot).read_text(encoding="utf-8"))
    start, end = raw["fetch_meta"]["bars_start"], raw["fetch_meta"]["bars_end"]
    policy_bars = policy_base = None
    if pol is not None:
        policy_bars = fetch_policy_bars(list(policy_weights(pol)), start, end)
        policy_base = policy_base_close(raw, pol)
    trades = trade_bars = None
    td = Path(args.trades_dir)
    if td.is_dir():
        trades = {f.stem: (json.loads(f.read_text(encoding="utf-8")).get("trades") or []) for f in sorted(td.glob("2026-*.json"))}
        trades = {d: t for d, t in trades.items() if start <= d <= end}
        syms = sorted({str(t["symbol"]).upper() for ts in trades.values() for t in ts})
        trade_bars = fetch_policy_bars(syms, start, end) if syms else {}
    res = build(raw, snapshot=snap, inception_snapshot=inc, policy_bars=policy_bars, policy_snapshot=pol, policy_base=policy_base,
                trades_by_date=trades, trade_bars=trade_bars, validate_as_of=(snap or {}).get("date") and _prev_day(raw, snap["date"]))
    try:
        shown = str(Path(args.snapshot).resolve().relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        shown = str(args.snapshot)
    text = report.render(res, raw["fetch_meta"], snap_path=shown, inception_given=inc is not None)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


def fetch_policy_bars(symbols: list[str], start: str, end: str, api=None) -> dict[str, list[dict]]:
    """RAW daily closes (price return, like the book) for the policy portfolio's constituents, read-only through the same GET-only client."""
    from .alpaca_ro import ReadOnlyAlpaca, load_keys
    api = api or ReadOnlyAlpaca(*load_keys())
    bars = api.daily_bars(symbols, start, end, adjustment="raw")
    missing = [s for s in symbols if not bars.get(s)]
    if missing:
        raise ValueError(f"no bars returned for policy constituents {missing}")
    return bars


def policy_base_close(raw: dict, snapshot: dict) -> str:
    """The session close at or before the moment the policy snapshot was collected: its own date if collected after the 16:00 ET close,
    otherwise the previous session (the policy is held buy-and-hold from that close)."""
    from zoneinfo import ZoneInfo
    days = sorted(bars_by_date(raw["bars_all"])["SPY"])
    at = datetime.fromisoformat(snapshot["collected_at"].replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
    d = at.date().isoformat()
    if d in days and (at.hour, at.minute) >= (16, 0):
        return d
    return max(x for x in days if x < d)


def _prev_day(raw: dict, snapshot_date: str) -> str:
    days = sorted(bars_by_date(raw["bars_all"])["SPY"])
    return max(d for d in days if d < snapshot_date)


if __name__ == "__main__":
    sys.exit(main())
