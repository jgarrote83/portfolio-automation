"""Metrics, the mechanical go/no-go verdict, and run output for the Phase 2 backtest.

Definitions are the pre-registered ones (`docs/specs/ORB_Phase2_Preregistration.md`, sections 2 and 5):
daily sleeve return = day net P&L / sleeve capital with no-trade days as 0; Sharpe = mean / sample std
(ddof = 1) x sqrt(252), risk-free 0; per-year P&L = net dollars summed over the calendar year; the
drawdown curve is the sleeve capital plus cumulative net P&L; contribution to total equity = sleeve
return x 0.25.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
from collections import Counter
from dataclasses import asdict
from datetime import date
from pathlib import Path

from orb.config import OrbConfig
from orb.signals import LONG, SHORT

from .engine import BacktestRun, DayResult, Trade, Variant
from .invalid import render_invalid_md

SLEEVE_SHARE_OF_EQUITY = 0.25          # sleeve = 25% of a $100k account (pre-registration, section 1)
GO_MIN_SHARPE = 1.0
GO_YEARS = (2024, 2025)
R_BINS = ((-math.inf, -2.0, "< -2R"), (-2.0, -1.0, "-2R .. -1R"), (-1.0, 0.0, "-1R .. 0R"),
          (0.0, 1.0, "0R .. 1R"), (1.0, 2.0, "1R .. 2R"), (2.0, 3.0, "2R .. 3R"),
          (3.0, math.inf, ">= 3R"))
TRADE_COLUMNS = ["day", "symbol", "side", "shares", "binding", "rvol", "atr", "trigger", "stop",
                 "stop_distance", "entry_fill", "entry_minute", "entry_gap", "exit_fill",
                 "exit_minute", "exit_reason", "exit_stale", "gross_pnl", "slippage_cost",
                 "commission", "net_pnl", "r_gross", "r_net", "notional"]


def _pct(x: float | None) -> float | None:
    return None if x is None else x * 100.0


def _quantile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _trade_block(trades: list[Trade]) -> dict:
    n = len(trades)
    if not n:
        return {"trades": 0, "net_pnl": 0.0, "hit_ratio": None, "avg_r_net": None, "avg_r_gross": None}
    return {"trades": n, "net_pnl": sum(t.net_pnl for t in trades),
            "hit_ratio": sum(1 for t in trades if t.net_pnl > 0) / n,
            "avg_r_net": sum(t.r_net for t in trades) / n,
            "avg_r_gross": sum(t.r_gross for t in trades) / n}


def compute_metrics(results: list[DayResult], cfg: OrbConfig) -> dict:
    """Everything the pre-registration says a run reports, for ONE variant."""
    sleeve = cfg.sleeve_capital_usd
    days = sorted(results, key=lambda r: r.day)
    daily_net = [r.net_pnl for r in days]
    rets = [x / sleeve for x in daily_net]
    mean = statistics.fmean(rets) if rets else None
    std = statistics.stdev(rets) if len(rets) > 1 else None
    sharpe = (mean / std * math.sqrt(252)) if (mean is not None and std and std > 0) else None

    equity, peak, max_dd, max_dd_pct = sleeve, sleeve, 0.0, 0.0
    for x in daily_net:
        equity += x
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        max_dd_pct = max(max_dd_pct, (peak - equity) / peak if peak > 0 else 0.0)

    trades = [t for r in days for t in r.trades]
    by_year: dict[str, dict] = {}
    for yr in sorted({r.day.year for r in days}):
        ydays = [r for r in days if r.day.year == yr]
        net = sum(r.net_pnl for r in ydays)
        by_year[str(yr)] = {"days": len(ydays), "trades": sum(len(r.trades) for r in ydays),
                            "gross_pnl": sum(r.gross_pnl for r in ydays), "net_pnl": net,
                            "sleeve_return_pct": _pct(net / sleeve),
                            "contribution_to_equity_pct": _pct(net / sleeve * SLEEVE_SHARE_OF_EQUITY)}
    total_net = sum(daily_net)
    rs = sorted(t.r_net for t in trades)
    dist = []
    for lo, hi, label in R_BINS:
        dist.append({"bin": label, "trades": sum(1 for x in rs if lo <= x < hi)})
    binding = Counter(t.binding for t in trades)
    skips = Counter(s.reason for r in days for s in r.skips)
    exits = Counter(t.exit_reason for t in trades)
    n_days = len(days)
    return {
        "days": n_days,
        "net_pnl": total_net,
        "gross_pnl": sum(r.gross_pnl for r in days),
        "slippage_cost": sum(r.slippage_cost for r in days),
        "commission": sum(r.commission for r in days),
        "sleeve_return_pct": _pct(total_net / sleeve),
        "contribution_to_equity_pct": _pct(total_net / sleeve * SLEEVE_SHARE_OF_EQUITY),
        "mean_daily_return_pct": _pct(mean), "std_daily_return_pct": _pct(std),
        "sharpe_net": sharpe,
        "max_drawdown_usd": max_dd, "max_drawdown_pct_of_peak": _pct(max_dd_pct),
        "by_year": by_year,
        "trades": len(trades),
        "hit_ratio": (sum(1 for t in trades if t.net_pnl > 0) / len(trades)) if trades else None,
        "avg_r_net": (sum(t.r_net for t in trades) / len(trades)) if trades else None,
        "avg_r_gross": (sum(t.r_gross for t in trades) / len(trades)) if trades else None,
        "r_net_quantiles": {q: _quantile(rs, p) for q, p in
                            (("min", 0.0), ("p05", 0.05), ("p25", 0.25), ("p50", 0.5),
                             ("p75", 0.75), ("p95", 0.95), ("max", 1.0))},
        "r_net_distribution": dist,
        "long": _trade_block([t for t in trades if t.side == LONG]),
        "short": _trade_block([t for t in trades if t.side == SHORT]),
        "days_with_no_trades": sum(1 for r in days if not r.trades),
        "days_loss_limit_hit": sum(1 for r in days if r.loss_limit_hit),
        "trades_per_day": (len(trades) / n_days) if n_days else None,
        "sizing_binding": {"risk": binding.get("risk", 0), "cap": binding.get("cap", 0),
                           "tie": binding.get("tie", 0),
                           "cap_share": (binding.get("cap", 0) / len(trades)) if trades else None},
        "skips": dict(sorted(skips.items())),
        "exit_reasons": dict(sorted(exits.items())),
        "exits_at_stale_price": sum(1 for t in trades if t.exit_stale),
        "gap_entries": sum(1 for t in trades if t.entry_gap),
        "mean_notional_usd": (sum(t.notional for t in trades) / len(trades)) if trades else None,
    }


def go_no_go(primary_metrics: dict) -> dict:
    """The pre-registered mechanical verdict, on the primary variant only."""
    sharpe = primary_metrics.get("sharpe_net")
    years = primary_metrics.get("by_year", {})
    pnl = {y: years.get(str(y), {}).get("net_pnl") for y in GO_YEARS}
    checks = {
        f"net_sharpe >= {GO_MIN_SHARPE}": sharpe is not None and sharpe >= GO_MIN_SHARPE,
        **{f"net_pnl_{y} > 0": (pnl[y] is not None and pnl[y] > 0) for y in GO_YEARS},
    }
    return {"verdict": "GO" if all(checks.values()) else "NO-GO", "checks": checks,
            "net_sharpe": sharpe, "net_pnl_by_year": pnl}


def compute_all(run: BacktestRun) -> dict:
    metrics = {}
    for v in run.variants:
        metrics[v.name] = compute_metrics(run.results[v.name], v.config(run.cfg))
    return metrics


# ===================================================================================== output
def _fmt(x, nd=2, pct=False, usd=False) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float) and math.isnan(x):
        return "n/a"
    s = f"{x:,.{nd}f}"
    return ("$" + s if usd else s) + ("%" if pct else "")


def headline_table(metrics: dict[str, dict], order: list[str]) -> str:
    cols = ["Variant", "Net Sharpe", "Net P&L", "2024 net", "2025 net", "Max DD", "Trades", "Hit ratio",
            "Avg R (net)", "Days no trade", "Sleeve return", "Contribution to equity"]
    lines = ["| " + " | ".join(cols) + " |", "|" + " --- |" * len(cols)]
    for name in order:
        m = metrics[name]
        by = m["by_year"]
        lines.append("| " + " | ".join([
            f"**{name}**" if name == "primary" else name,
            _fmt(m["sharpe_net"], 2), _fmt(m["net_pnl"], 0, usd=True),
            _fmt(by.get("2024", {}).get("net_pnl"), 0, usd=True),
            _fmt(by.get("2025", {}).get("net_pnl"), 0, usd=True),
            f"{_fmt(m['max_drawdown_usd'], 0, usd=True)} ({_fmt(m['max_drawdown_pct_of_peak'], 1, pct=True)})",
            f"{m['trades']:,}", _fmt(_pct(m["hit_ratio"]), 1, pct=True), _fmt(m["avg_r_net"], 3),
            f"{m['days_with_no_trades']} of {m['days']}", _fmt(m["sleeve_return_pct"], 2, pct=True),
            _fmt(m["contribution_to_equity_pct"], 3, pct=True)]) + " |")
    return "\n".join(lines)


def variant_detail(name: str, m: dict) -> str:
    out = [f"### {name}", ""]
    out.append(f"* Net P&L {_fmt(m['net_pnl'], 0, usd=True)} (gross {_fmt(m['gross_pnl'], 0, usd=True)}, "
               f"slippage {_fmt(m['slippage_cost'], 0, usd=True)}, commission {_fmt(m['commission'], 0, usd=True)}); "
               f"{m['trades']:,} trades over {m['days']} days ({_fmt(m['trades_per_day'], 1)}/day), "
               f"{m['days_with_no_trades']} days with no trade, {m['days_loss_limit_hit']} loss-limit days.")
    out.append(f"* Net Sharpe {_fmt(m['sharpe_net'], 3)}; mean daily return {_fmt(m['mean_daily_return_pct'], 4, pct=True)}, "
               f"std {_fmt(m['std_daily_return_pct'], 4, pct=True)}; max drawdown "
               f"{_fmt(m['max_drawdown_usd'], 0, usd=True)} ({_fmt(m['max_drawdown_pct_of_peak'], 1, pct=True)} of peak).")
    out.append(f"* Hit ratio {_fmt(_pct(m['hit_ratio']), 1, pct=True)}; average R net {_fmt(m['avg_r_net'], 3)} "
               f"(gross {_fmt(m['avg_r_gross'], 3)}); R quantiles "
               + ", ".join(f"{k} {_fmt(v, 2)}" for k, v in m["r_net_quantiles"].items()) + ".")
    out.append("* Long: " + _side_line(m["long"]) + ". Short: " + _side_line(m["short"]) + ".")
    sb = m["sizing_binding"]
    out.append(f"* Sizing limit that bound: position cap {sb['cap']:,}, risk {sb['risk']:,}, tie {sb['tie']:,} "
               f"(cap share {_fmt(_pct(sb['cap_share']), 1, pct=True)}); zero-share skips {m['skips'].get('zero_shares', 0):,}. "
               f"Mean notional {_fmt(m['mean_notional_usd'], 0, usd=True)} per trade.")
    out.append("* Exits: " + ", ".join(f"{k} {v:,}" for k, v in m["exit_reasons"].items())
               + f"; gap entries {m['gap_entries']:,}; exits at a stale price {m['exits_at_stale_price']:,}.")
    out.append("* Skipped picks: " + (", ".join(f"{k} {v:,}" for k, v in m["skips"].items()) or "none") + ".")
    out += ["", "| Year | Days | Trades | Gross P&L | Net P&L | Sleeve return | Contribution to total equity |",
            "| --- | --- | --- | --- | --- | --- | --- |"]
    for y, b in m["by_year"].items():
        out.append(f"| {y} | {b['days']} | {b['trades']:,} | {_fmt(b['gross_pnl'], 0, usd=True)} | "
                   f"{_fmt(b['net_pnl'], 0, usd=True)} | {_fmt(b['sleeve_return_pct'], 2, pct=True)} | "
                   f"{_fmt(b['contribution_to_equity_pct'], 3, pct=True)} |")
    out += ["", "| Net R | Trades |", "| --- | --- |"]
    out += [f"| {d['bin']} | {d['trades']:,} |" for d in m["r_net_distribution"]]
    return "\n".join(out) + "\n"


def _side_line(b: dict) -> str:
    if not b["trades"]:
        return "no trades"
    return (f"{b['trades']:,} trades, net {_fmt(b['net_pnl'], 0, usd=True)}, hit ratio "
            f"{_fmt(_pct(b['hit_ratio']), 1, pct=True)}, avg R {_fmt(b['avg_r_net'], 3)}")


def render_summary_md(header: dict, metrics: dict[str, dict], verdict: dict | None) -> str:
    order = [v for v in header["variants"]]
    valid = header["valid_for_verdict"]
    top = [f"# ORB Phase 2 backtest results — run `{header['run_id']}`", ""]
    if not valid:
        top += ["> **NOT A PRE-REGISTERED RUN — no go/no-go verdict.** " + "; ".join(header["invalid_reasons"]) + ".", ""]
    if verdict:
        c = verdict["checks"]
        top += [f"## Mechanical verdict (primary variant): **{verdict['verdict']}**", "",
                "| Pre-registered check | Result |", "| --- | --- |"]
        top += [f"| {k} | {'PASS' if ok else 'FAIL'} |" for k, ok in c.items()]
        top += ["", f"Net Sharpe {_fmt(verdict['net_sharpe'], 3)}; net P&L "
                + ", ".join(f"{y}: {_fmt(p, 0, usd=True)}" for y, p in verdict["net_pnl_by_year"].items()) + ".", ""]
    top += ["## Headline table", "", headline_table(metrics, order), ""]
    top += ["## Variant detail", ""] + [variant_detail(n, metrics[n]) for n in order]
    inv = render_invalid_md(header.get("invalid_symbols"))
    if inv:
        top += [inv]
    return "\n".join(top)


def _csv_value(x):
    if isinstance(x, float):
        return f"{x:.6f}"
    if isinstance(x, date):
        return x.isoformat()
    return x


def write_run(out_dir: Path, header: dict, run: BacktestRun, metrics: dict[str, dict],
              verdict: dict | None) -> list[Path]:
    """Write header.json, summary.md and, per variant, trades.csv / daily.csv / metrics.json."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    def _w(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)

    full_header = dict(header, metrics_verdict=verdict)
    _w(out_dir / "header.json", json.dumps(full_header, indent=2, sort_keys=True, default=str) + "\n")
    _w(out_dir / "summary.md", render_summary_md(header, metrics, verdict))
    for v in run.variants:
        vd = out_dir / v.name
        vd.mkdir(exist_ok=True)
        _w(vd / "metrics.json", json.dumps(metrics[v.name], indent=2, sort_keys=True, default=str) + "\n")
        results = run.results[v.name]
        with (vd / "trades.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(TRADE_COLUMNS)
            for r in results:
                for t in r.trades:
                    d = asdict(t)
                    w.writerow([_csv_value(d[c]) for c in TRADE_COLUMNS])
        written.append(vd / "trades.csv")
        with (vd / "daily.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["day", "picks", "trades", "gross_pnl", "slippage_cost", "commission", "net_pnl",
                        "loss_limit_hit"])
            for r in results:
                w.writerow([r.day.isoformat(), r.picks_n, len(r.trades), f"{r.gross_pnl:.6f}",
                            f"{r.slippage_cost:.6f}", f"{r.commission:.6f}", f"{r.net_pnl:.6f}",
                            int(r.loss_limit_hit)])
        written.append(vd / "daily.csv")
    return written


def variants_header(variants: tuple[Variant, ...]) -> dict:
    return {v.name: dict(v.overrides) for v in variants}
