"""ORB on QQQ: the Phase 2 strategy on one liquid, high-priced ETF (pre-registered, then run).

    PYTHONPATH=src python -m backtest.orb_qqq run [--review-file .review/orb-qqq-results.md]
    PYTHONPATH=src python -m backtest.orb_qqq_verify                      # re-derive 50 random trades, separate code

Rules, sensitivities and the go/no-go are in `docs/specs/ORB_QQQ_Preregistration.md` (locked at its first commit;
`tests/test_orb_qqq.py` pins `qqq_config()` and the variants to it). Everything not stated there is inherited from
`docs/specs/ORB_Phase2_Preregistration.md`. The verified engine is REUSED, not rewritten: `backtest.engine.simulate_day`
with `orb.signals`, `orb.sizing`, `orb.universe.atr_simple`, `orb.config.OrbConfig` and `backtest.reports`. What this module
adds is the QQQ-only selection path (one pick a day, no universe filter, no Relative Volume), the reported statistics the
instruction asks for, and the IEX-vs-SIP check.

Data comes ONLY through `backtest.data.get_bars` (SIP QQQ 1Min and 1Day; IEX QQQ 1Min for the check; one SIP 5Min
consistency query). Every date on or after 2026-01-01 is refused with no override flag. The CLI reports; it never tunes.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import math
import statistics
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
from orb import universe
from orb.config import OrbConfig
from orb.signals import LONG, direction_from_bar

from . import provenance, reports, samebar
from .data import DataLayer
from .data.bars import CacheMissError, HoldoutError
from .data.client import AlpacaDataError
from .data.config import REPO_ROOT
from .data.credentials import MissingCredentialsError
from .engine import BacktestRun, DayInput, DayResult, MinuteBar, Pick, Trade, assert_not_holdout, simulate_day
from .engine import Variant as EngineVariant
from .sessions import close_minute

SYMBOL = "QQQ"
SPEC_VERSION = "orb-qqq-1.0"
PREREG_PATH = "docs/specs/ORB_QQQ_Preregistration.md"
PREREG_START, PREREG_END = "2024-01-02", "2025-12-31"
FETCH_FROM = "2023-12-01"
SESSION_WINDOW = ("09:30", "16:00")
OPEN_WINDOW = ("09:30", "09:35")
OPEN_MINUTES = tuple(range(9 * 60 + 30, 9 * 60 + 35))                # the one-minute bars stamped 09:30 .. 09:34
DEFAULT_OUT = REPO_ROOT / "reports" / "orb_qqq"
Bar4 = tuple[float, float, float, float]


# ======================================================================================== config
def qqq_config() -> OrbConfig:
    """The pre-registered primary configuration: the Phase 2 primary with the position cap set to the whole sleeve
    (`position_slots = 1`) and 1 cent of slippage per side."""
    return OrbConfig().replace(position_slots=1, slippage_cents_per_side=1.0)


@dataclass(frozen=True)
class QVariant:
    """The primary rule or ONE pre-registered change to it (sections 1 and 3)."""
    name: str
    overrides: tuple[tuple[str, object], ...] = ()
    optimistic: bool = False                      # (c): the entry bar's stop is not checked

    def config(self, base: OrbConfig) -> OrbConfig:
        return base.replace(**dict(self.overrides)) if self.overrides else base

    def rule(self):
        return samebar.ignore_stop_rule if self.optimistic else None


PRIMARY = QVariant("primary")
SENSITIVITIES = (
    QVariant("slippage_0c", (("slippage_cents_per_side", 0.0),)),
    QVariant("slippage_2c", (("slippage_cents_per_side", 2.0),)),
    QVariant("slippage_5c", (("slippage_cents_per_side", 5.0),)),
    QVariant("long_only", (("allow_shorts", False),)),
    QVariant("same_bar_optimistic", optimistic=True),
)
ALL_VARIANTS = (PRIMARY,) + SENSITIVITIES


# ============================================================================== pure helpers
def sign(open_: float, close: float) -> int:
    return (close > open_) - (close < open_)


def opening_bar(bars: Mapping[int, MinuteBar]) -> Bar4 | None:
    """The 09:30 five-minute bar from the one-minute bars stamped 09:30..09:34; None unless all five exist."""
    if any(m not in bars for m in OPEN_MINUTES):
        return None
    seq = [bars[m] for m in OPEN_MINUTES]
    return seq[0][0], max(b[1] for b in seq), min(b[2] for b in seq), seq[-1][3]


def partial_bar(bars: Mapping[int, MinuteBar]) -> tuple[float, float, float, float, int] | None:
    """The same aggregate from whichever of the five minutes exist (the IEX check): (o, h, l, c, minutes present)."""
    present = [m for m in OPEN_MINUTES if m in bars]
    if not present:
        return None
    seq = [bars[m] for m in present]
    return seq[0][0], max(b[1] for b in seq), min(b[2] for b in seq), seq[-1][3], len(present)


def atr_for_day(i: int, cal: Sequence[date], daily: Mapping[date, tuple[float, float, float, float]], cfg: OrbConfig) -> float | None:
    """`orb.universe.atr_simple` over QQQ's `cfg.lookback_days` prior daily bars (calendar index i is the day itself).
    All prior bars must exist; the previous close of the OLDEST may be missing (then that true range is high - low)."""
    n = cfg.lookback_days
    if i < n:
        return None
    rows = [daily.get(d) for d in cal[i - n:i]]
    if any(r is None for r in rows):
        return None
    prior = daily.get(cal[i - n - 1]) if i - n - 1 >= 0 else None
    return universe.atr_simple([r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows],
                               prior[3] if prior else None, cfg)


def build_pick(i: int, cal: Sequence[date], daily: Mapping[date, tuple[float, float, float, float]],
               bars: Mapping[int, MinuteBar], cfg: OrbConfig) -> tuple[Pick | None, str | None]:
    """QQQ's pick for calendar day i, or (None, reason). A doji comes back as a Pick with side None (the engine counts it)."""
    if not bars:
        return None, "no_minute_bars"
    atr = atr_for_day(i, cal, daily, cfg)
    if atr is None:
        return None, "atr_unavailable"
    ob = opening_bar(bars)
    if ob is None:
        return None, "opening_bar_incomplete"
    o, h, lo, c = ob
    return Pick(SYMBOL, 0.0, direction_from_bar(o, c), o, h, lo, c, atr), None


def simulate_variant(variant: QVariant, base: OrbConfig, days: Sequence[date], picks: Mapping[date, Pick],
                     bars_by_day: Mapping[date, Mapping[int, MinuteBar]]) -> list[DayResult]:
    """One DayResult per window day: the engine on the day's pick, or an empty day (counted as zero P&L)."""
    cfg = variant.config(base)
    out = []
    for d in days:
        pick = picks.get(d)
        if pick is None:
            out.append(DayResult(d, 0))
        else:
            out.append(simulate_day(DayInput(d, close_minute(d), (pick,), {SYMBOL: bars_by_day[d]}), cfg,
                                    entry_bar_stop=variant.rule()))
    return out


def entry_bar_reaches_stop(t: Trade, bar: MinuteBar) -> bool:
    """Does the entry-minute bar reach the stop level (the quantity rule (c) ignores)?"""
    return bar[2] <= t.stop if t.side == LONG else bar[1] >= t.stop


def extras(results: Sequence[DayResult], bars_by_day: Mapping[date, Mapping[int, MinuteBar]]) -> dict:
    """The statistics section 5, items 13-14 define, beyond `reports.compute_metrics`."""
    trades = [t for r in results for t in r.trades]
    n = len(trades)
    if not n:
        return {"trades": 0}
    cost = [t.slippage_cost + t.commission for t in trades]
    risk = [t.shares * t.stop_distance for t in trades]
    reach = sum(1 for t in trades if entry_bar_reaches_stop(t, bars_by_day[t.day][t.entry_minute]))
    stopped = sum(1 for t in trades if t.exit_reason == "stop_same_bar")
    rets = [r.net_pnl / 25000.0 for r in results]
    sd = statistics.stdev(rets) if len(rets) > 1 else None
    return {
        "trades": n, "stopped_entry_minute": stopped, "stopped_entry_minute_share": stopped / n,
        "entry_bar_reaches_stop": reach, "entry_bar_reaches_stop_share": reach / n,
        "mean_stop_distance_usd": statistics.fmean(t.stop_distance for t in trades),
        "mean_stop_distance_bps": statistics.fmean(abs(t.trigger - t.stop) / t.trigger * 1e4 for t in trades),
        "costs_total_usd": sum(cost), "risk_total_usd": sum(risk), "costs_over_total_risk": sum(cost) / sum(risk),
        "mean_costs_over_R": statistics.fmean(c / r for c, r in zip(cost, risk)),
        "avg_net_R": statistics.fmean(t.r_net for t in trades),
        "avg_net_bps": statistics.fmean(t.net_pnl / (t.shares * t.entry_fill) * 1e4 for t in trades),
        "mean_shares": statistics.fmean(t.shares for t in trades), "mean_notional_usd": statistics.fmean(t.notional for t in trades),
        "mean_atr": statistics.fmean(t.atr for t in trades),
        "t_stat_mean_daily_return": (statistics.fmean(rets) / (sd / math.sqrt(len(rets)))) if sd else None,
    }


# ============================================================================ IEX vs SIP, 5-minute check
def _pctl(sorted_vals: list[float], q: float) -> float | None:
    return float(np.percentile(sorted_vals, q)) if sorted_vals else None


def iex_check(sip_open: Mapping[date, Bar4 | None], iex_bars: Mapping[date, Mapping[int, MinuteBar]], days: Sequence[date]) -> dict:
    """Per day: does the IEX opening bar give the same direction as the SIP one, and how far apart are the opening-range
    high and low (IEX minus SIP, in cents)? Descriptive only."""
    rows = []
    for d in days:
        s, i = sip_open.get(d), partial_bar(iex_bars.get(d, {}))
        row = {"day": d.isoformat(), "sip_bar": s is not None, "iex_minutes": i[4] if i else 0}
        if s and i:
            row.update(sip_dir=sign(s[0], s[3]), iex_dir=sign(i[0], i[3]), high_diff_cents=(i[1] - s[1]) * 100,
                       low_diff_cents=(i[2] - s[2]) * 100)
            row["agree"] = row["sip_dir"] == row["iex_dir"]
        rows.append(row)
    cmp_ = [r for r in rows if "agree" in r]
    hi = sorted(abs(r["high_diff_cents"]) for r in cmp_)
    lo = sorted(abs(r["low_diff_cents"]) for r in cmp_)

    def block(vals):
        return {"mean_abs": statistics.fmean(vals) if vals else None, "median_abs": _pctl(vals, 50), "p90_abs": _pctl(vals, 90),
                "p99_abs": _pctl(vals, 99), "max_abs": max(vals) if vals else None,
                "share_exactly_equal": (sum(1 for v in vals if v < 5e-7) / len(vals)) if vals else None}
    dis = [r for r in cmp_ if not r["agree"]]
    return {"days": len(days), "sip_bar_days": sum(1 for r in rows if r["sip_bar"]), "no_iex_bar_days": sum(1 for r in rows if r["iex_minutes"] == 0),
            "compared": len(cmp_), "direction_agree": sum(1 for r in cmp_ if r["agree"]),
            "direction_agree_share": (sum(1 for r in cmp_ if r["agree"]) / len(cmp_)) if cmp_ else None,
            "disagreements": [{"day": r["day"], "sip_dir": r["sip_dir"], "iex_dir": r["iex_dir"]} for r in dis[:20]],
            "iex_minutes_present": dict(sorted(Counter(r["iex_minutes"] for r in rows).items())),
            "high": block(hi), "low": block(lo),
            "iex_range_outside_sip": sum(1 for r in cmp_ if r["high_diff_cents"] > 5e-7 or r["low_diff_cents"] < -5e-7),
            "mean_signed_high_cents": statistics.fmean(r["high_diff_cents"] for r in cmp_) if cmp_ else None,
            "mean_signed_low_cents": statistics.fmean(r["low_diff_cents"] for r in cmp_) if cmp_ else None, "rows": rows}


def five_min_check(sip_open: Mapping[date, Bar4 | None], sip5: Mapping[date, Bar4], tol: float = 1e-6) -> dict:
    """The non-gating consistency check of section 5, item 4: the aggregate of five one-minute bars vs Alpaca's own
    five-minute bar stamped 09:30."""
    cmp_ = [(d, sip_open[d], sip5[d]) for d in sorted(sip5) if sip_open.get(d)]
    bad = [(d, a, b) for d, a, b in cmp_ if any(abs(x - y) > tol for x, y in zip(a, b))]
    return {"compared": len(cmp_), "equal": len(cmp_) - len(bad), "mismatches": len(bad),
            "first_mismatches": [{"day": d.isoformat(), "aggregate": a, "five_min": b} for d, a, b in bad[:10]],
            "five_min_days_without_aggregate": sum(1 for d in sip5 if not sip_open.get(d))}


# ================================================================================== data loading
def _group_minutes(df) -> dict[date, dict[int, MinuteBar]]:
    out: dict[date, dict[int, MinuteBar]] = {}
    if df.empty:
        return out
    ts = df["ts"]
    for d, m, o, h, lo, c, v in zip(ts.dt.date.tolist(), (ts.dt.hour * 60 + ts.dt.minute).tolist(), df["open"].tolist(), df["high"].tolist(),
                                    df["low"].tolist(), df["close"].tolist(), df["volume"].tolist()):
        out.setdefault(d, {})[m] = (o, h, lo, c, v)
    return out


@dataclass
class Inputs:
    cal: list[date]
    days: list[date]
    daily: dict[date, Bar4]
    minute: dict[date, dict[int, MinuteBar]]
    iex: dict[date, dict[int, MinuteBar]]
    sip5: dict[date, Bar4]


def load_inputs(layer, start: str, end: str, *, offline: bool = False) -> Inputs:
    s_day, e_day = date.fromisoformat(start), date.fromisoformat(end)
    assert_not_holdout(s_day, e_day)
    cal = layer.trading_days(FETCH_FROM, e_day, offline=offline)
    days = [d for d in cal if s_day <= d <= e_day]
    ddf = layer.get_bars([SYMBOL], FETCH_FROM, e_day, "1Day", "sip", offline=offline)
    daily = {ts.date(): (o, h, lo, c) for ts, o, h, lo, c in zip(ddf["ts"], ddf["open"], ddf["high"], ddf["low"], ddf["close"])}
    minute = _group_minutes(layer.get_bars([SYMBOL], s_day, e_day, "1Min", "sip", window=SESSION_WINDOW, offline=offline))
    iex = _group_minutes(layer.get_bars([SYMBOL], s_day, e_day, "1Min", "iex", window=OPEN_WINDOW, offline=offline))
    f5 = layer.get_bars([SYMBOL], s_day, e_day, "5Min", "sip", window=OPEN_WINDOW, offline=offline)
    sip5 = {ts.date(): (o, h, lo, c) for ts, o, h, lo, c in zip(f5["ts"], f5["open"], f5["high"], f5["low"], f5["close"])}
    return Inputs(cal, days, daily, minute, iex, sip5)


# ===================================================================================== the study
def analyse(inp: Inputs, cfg: OrbConfig, variants: Sequence[QVariant] = ALL_VARIANTS) -> dict:
    """Every number of a run, from already-loaded inputs."""
    pos = {d: i for i, d in enumerate(inp.cal)}
    picks: dict[date, Pick] = {}
    skipped: dict[str, list[str]] = {}
    sip_open: dict[date, Bar4 | None] = {}
    for d in inp.days:
        bars = inp.minute.get(d, {})
        sip_open[d] = opening_bar(bars)
        pick, why = build_pick(pos[d], inp.cal, inp.daily, bars, cfg)
        if pick is not None:
            picks[d] = pick
        else:
            skipped.setdefault(why, []).append(d.isoformat())
    results = {v.name: simulate_variant(v, cfg, inp.days, picks, inp.minute) for v in variants}
    run = BacktestRun(cfg, list(inp.days), results, tuple(EngineVariant(v.name, v.overrides) for v in variants),
                      {"picks": len(picks)}, {})
    metrics = reports.compute_all(run)
    ex = {v.name: extras(results[v.name], inp.minute) for v in variants}
    return {"results": results, "metrics": metrics, "extras": ex, "run": run, "skipped_days": skipped, "picks": picks,
            "iex": iex_check(sip_open, inp.iex, inp.days), "five_min": five_min_check(sip_open, inp.sip5)}


# ============================================================================= rendering, outputs
def _f(x, nd=2, pct=False, usd=False) -> str:
    return reports._fmt(x, nd, pct=pct, usd=usd)


def validity(start: str, end: str, cfg: OrbConfig, variants, prereg: dict) -> list[str]:
    """Reasons this run is NOT the pre-registered one (empty list = valid for a verdict)."""
    why = []
    if (start, end) != (PREREG_START, PREREG_END):
        why.append(f"window {start}..{end} is not the pre-registered {PREREG_START}..{PREREG_END}")
    if cfg != qqq_config():
        why.append(f"config differs from the pre-registered QQQ configuration: {cfg.diff_from_default()}")
    if tuple(v.name for v in variants) != tuple(v.name for v in ALL_VARIANTS):
        why.append("not the full pre-registered variant set")
    if prereg.get("unchanged") is False:
        why.append("the pre-registration's rule text no longer matches its first-commit hash")
    elif prereg.get("unchanged") is None:
        why.append("the pre-registration's first-commit hash could not be verified (no git history)")
    return why


def headline_table(res: dict) -> str:
    cols = ["Variant", "Net Sharpe", "t-stat", "Net P&L", "2024 net", "2025 net", "Max DD", "Trades", "Hit ratio", "Avg net R", "Avg net bps",
            "Long / Short trades", "Gap entries", "Exits"]
    lines = ["| " + " | ".join(cols) + " |", "|" + " --- |" * len(cols)]
    for v in ALL_VARIANTS:
        m, ex = res["metrics"][v.name], res["extras"][v.name]
        by = m["by_year"]
        lines.append("| " + " | ".join([
            f"**{v.name}**" if v is PRIMARY else v.name, _f(m["sharpe_net"], 2), _f(ex.get("t_stat_mean_daily_return"), 2), _f(m["net_pnl"], 0, usd=True),
            _f(by.get("2024", {}).get("net_pnl"), 0, usd=True), _f(by.get("2025", {}).get("net_pnl"), 0, usd=True),
            f"{_f(m['max_drawdown_usd'], 0, usd=True)} ({_f(m['max_drawdown_pct_of_peak'], 1, pct=True)})", f"{m['trades']:,}",
            _f(reports._pct(m["hit_ratio"]), 1, pct=True), _f(m["avg_r_net"], 3), _f(ex.get("avg_net_bps"), 2),
            f"{m['long']['trades']:,} / {m['short']['trades']:,}", f"{m['gap_entries']:,}",
            ", ".join(f"{k} {n:,}" for k, n in m["exit_reasons"].items())]) + " |")
    return "\n".join(lines)


def render_summary_md(header: dict, res: dict, verdict: dict | None) -> str:
    m, ex = res["metrics"], res["extras"]
    p, c = ex["primary"], ex["same_bar_optimistic"]
    out = [f"# ORB on QQQ: backtest results, run `{header['run_id']}`", ""]
    if not header["valid_for_verdict"]:
        out += ["> **NOT A PRE-REGISTERED RUN. No go/no-go verdict.** " + "; ".join(header["invalid_reasons"]) + ".", ""]
    if verdict:
        out += [f"## Mechanical verdict (primary variant): **{verdict['verdict']}**", "", "| Pre-registered check | Result |", "| --- | --- |"]
        out += [f"| {k} | {'PASS' if ok else 'FAIL'} |" for k, ok in verdict["checks"].items()]
        out += ["", f"Net Sharpe {_f(verdict['net_sharpe'], 3)}; net P&L " + ", ".join(f"{y}: {_f(v, 0, usd=True)}" for y, v in verdict["net_pnl_by_year"].items()) + ".", ""]
    out += ["## Headline table", "", headline_table(res), ""]
    if not p["trades"]:
        out += ["The primary variant made no trades.", ""]
        return "\n".join(out)
    out += ["## Stopped in the entry minute (the same-minute ambiguity)", "",
            "| | Primary | Same-bar optimistic (c) |", "| --- | --- | --- |",
            f"| Trades | {p['trades']:,} | {c['trades']:,} |",
            f"| Stopped in the entry minute (`stop_same_bar`) | {p['stopped_entry_minute']:,} ({_f(p['stopped_entry_minute_share'] * 100, 1, pct=True)}) | "
            f"{c['stopped_entry_minute']:,} ({_f(c['stopped_entry_minute_share'] * 100, 1, pct=True)}), zero by definition |",
            f"| Entries whose entry-minute bar reaches the stop level | {p['entry_bar_reaches_stop']:,} ({_f(p['entry_bar_reaches_stop_share'] * 100, 1, pct=True)}) | "
            f"{c['entry_bar_reaches_stop']:,} ({_f(c['entry_bar_reaches_stop_share'] * 100, 1, pct=True)}), ignored by the rule |",
            f"| Net P&L | {_f(m['primary']['net_pnl'], 0, usd=True)} | {_f(m['same_bar_optimistic']['net_pnl'], 0, usd=True)} |",
            f"| Net Sharpe | {_f(m['primary']['sharpe_net'], 2)} | {_f(m['same_bar_optimistic']['sharpe_net'], 2)} |", "",
            "(c) bounds the ambiguity; it is not a rule and does not enter the verdict.", "",
            "## Stop distance, sizing and costs (primary)", "",
            f"* Mean stop distance **${p['mean_stop_distance_usd']:.3f}** ({p['mean_stop_distance_bps']:.1f} bps of the trigger); mean ATR ${p['mean_atr']:.2f}.",
            f"* Costs as a fraction of R: **{p['costs_over_total_risk'] * 100:.2f}%** of total risk (total costs {_f(p['costs_total_usd'], 0, usd=True)} over "
            f"total risk {_f(p['risk_total_usd'], 0, usd=True)}); mean per trade {p['mean_costs_over_R'] * 100:.2f}% of that trade's R.",
            f"* Average net R {_f(p['avg_net_R'], 3)}; average net {_f(p['avg_net_bps'], 2)} bps per trade; mean {p['mean_shares']:.1f} shares, mean notional "
            f"{_f(p['mean_notional_usd'], 0, usd=True)} per trade.",
            f"* Sizing limit that bound: position cap {m['primary']['sizing_binding']['cap']:,}, risk {m['primary']['sizing_binding']['risk']:,}, "
            f"tie {m['primary']['sizing_binding']['tie']:,}.", ""]
    sk = res["skipped_days"]
    out += ["## Days without a trade", "", "| Reason | Days |", "| --- | --- |"]
    out += [f"| {k} | {len(v):,} |" for k, v in sorted(sk.items())] + [f"| doji (the engine counts it) | {m['primary']['skips'].get('doji', 0):,} |",
            f"| never triggered | {m['primary']['skips'].get('never_triggered', 0):,} |", ""]
    i = res["iex"]
    out += ["## IEX vs. SIP: the 09:30 five-minute bar", "",
            f"{i['compared']:,} of {i['days']:,} days compared ({i['no_iex_bar_days']:,} days with no IEX bar in 09:30-09:35; IEX minute bars present per day: "
            + ", ".join(f"{k}: {v:,}" for k, v in i["iex_minutes_present"].items()) + ").", "",
            f"* **Direction agrees on {i['direction_agree']:,} of {i['compared']:,} days ({_f((i['direction_agree_share'] or 0) * 100, 2, pct=True)}).**"
            + (f" Disagreements: {', '.join(d['day'] for d in i['disagreements'])}." if i["disagreements"] else ""),
            "", "| Opening-range edge | Mean abs diff | Median | 90th pct | 99th pct | Max | Exactly equal |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for nm, k in (("High", "high"), ("Low", "low")):
        b = i[k]
        out.append(f"| {nm} (IEX minus SIP, cents) | {_f(b['mean_abs'], 2)} | {_f(b['median_abs'], 2)} | {_f(b['p90_abs'], 2)} | {_f(b['p99_abs'], 2)} | "
                   f"{_f(b['max_abs'], 2)} | {_f((b['share_exactly_equal'] or 0) * 100, 1, pct=True)} |")
    out += ["", f"Mean signed difference: high {_f(i['mean_signed_high_cents'], 2)} cents, low {_f(i['mean_signed_low_cents'], 2)} cents (an IEX bar sees a subset of the "
            f"trades, so its range cannot exceed the SIP range); days where it does: {i['iex_range_outside_sip']:,}.", ""]
    f5 = res["five_min"]
    out += ["## Consistency check: one-minute aggregate vs. Alpaca's own five-minute bar (non-gating)", "",
            f"{f5['compared']:,} days compared; {f5['equal']:,} equal on open, high, low and close; **{f5['mismatches']:,} mismatches**."
            + (" First: " + "; ".join(f"{x['day']} aggregate {x['aggregate']} vs {x['five_min']}" for x in f5["first_mismatches"]) + "." if f5["mismatches"] else ""), ""]
    out += ["## Variant detail", ""] + [reports.variant_detail(v.name, m[v.name]) for v in ALL_VARIANTS]
    return "\n".join(out)


def _csv_value(x):
    return f"{x:.6f}" if isinstance(x, float) else (x.isoformat() if isinstance(x, date) else x)


def write_run(out: Path, header: dict, res: dict, verdict: dict | None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "header.json").write_text(json.dumps(dict(header, metrics_verdict=verdict), indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(render_summary_md(header, res, verdict), encoding="utf-8", newline="\n")
    (out / "extras.json").write_text(json.dumps(res["extras"], indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    (out / "skipped_days.json").write_text(json.dumps(res["skipped_days"], indent=1, sort_keys=True), encoding="utf-8")
    with (out / "iex_vs_sip.csv").open("w", newline="", encoding="utf-8") as f:
        rows = res["iex"]["rows"]
        w = csv.DictWriter(f, fieldnames=["day", "sip_bar", "iex_minutes", "sip_dir", "iex_dir", "agree", "high_diff_cents", "low_diff_cents"], lineterminator="\n")
        w.writeheader()
        w.writerows({k: (f"{v:.4f}" if isinstance(v, float) else v) for k, v in r.items()} for r in rows)
    for v in ALL_VARIANTS:
        d = out / v.name
        d.mkdir(exist_ok=True)
        (d / "metrics.json").write_text(json.dumps(res["metrics"][v.name], indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        with (d / "trades.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(reports.TRADE_COLUMNS)
            for r in res["results"][v.name]:
                for t in r.trades:
                    row = dataclasses.asdict(t)
                    w.writerow([_csv_value(row[c]) for c in reports.TRADE_COLUMNS])
        with (d / "daily.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["day", "picks", "trades", "gross_pnl", "slippage_cost", "commission", "net_pnl", "loss_limit_hit"])
            for r in res["results"][v.name]:
                w.writerow([r.day.isoformat(), r.picks_n, len(r.trades), f"{r.gross_pnl:.6f}", f"{r.slippage_cost:.6f}", f"{r.commission:.6f}",
                            f"{r.net_pnl:.6f}", int(r.loss_limit_hit)])


# ============================================================================================ CLI
def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def cmd_run(args) -> int:
    cfg = qqq_config()
    assert_not_holdout(date.fromisoformat(args.start), date.fromisoformat(args.end))
    layer = DataLayer(args.cache_dir)
    t0 = time.time()
    inp = load_inputs(layer, args.start, args.end, offline=args.offline)
    _log(f"{len(inp.days)} window days; {len(inp.minute)} days of SIP one-minute bars; {len(inp.iex)} days of IEX opening bars")
    res = analyse(inp, cfg)
    prereg = provenance.prereg_info(path=PREREG_PATH)
    why = validity(args.start, args.end, cfg, ALL_VARIANTS, prereg)
    commit = provenance.code_commit()
    now = datetime.now(timezone.utc)
    run_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{(commit['sha'] or 'nogit')[:7]}"
    verdict = reports.go_no_go(res["metrics"]["primary"]) if not why else None
    header = {"run_id": run_id, "created_utc": now.isoformat(), "spec_version": SPEC_VERSION, "symbol": SYMBOL,
              "window": {"start": args.start, "end": args.end, "trading_days": len(inp.days), "fetch_from": FETCH_FROM},
              "config": dataclasses.asdict(cfg), "config_diff_from_phase2": cfg.diff_from_default(),
              "variants": [v.name for v in ALL_VARIANTS], "code_commit": commit, "preregistration": prereg,
              "valid_for_verdict": not why, "invalid_reasons": why, "picks": len(res["picks"]),
              "skipped_days": {k: len(v) for k, v in res["skipped_days"].items()},
              "data_layer": layer.cache_stats(), "wall_time_s": round(time.time() - t0, 1),
              "holdout": "2026 was never read (this code refuses any date on/after 2026-01-01)"}
    out = Path(args.out) / run_id
    write_run(out, header, res, verdict)
    if args.review_file:
        pre = header["preregistration"]
        review = Path(args.review_file)
        review.parent.mkdir(parents=True, exist_ok=True)
        review.write_text(
            f"<!-- generated by backtest.orb_qqq from {run_id}; do not edit by hand -->\n{(out / 'summary.md').read_text(encoding='utf-8').rstrip()}\n\n"
            f"## Run provenance\n\n* Run `{run_id}` on code commit `{commit['sha']}` (tree dirty: {commit['dirty']}); spec_version `{SPEC_VERSION}`.\n"
            f"* Pre-registration `{pre['path']}` first committed in `{pre['first_commit']}`; rule text unchanged since: {pre['unchanged']}.\n"
            f"* Window {args.start}..{args.end}, {len(inp.days)} trading days; the 2026 holdout was never read.\n"
            f"* Data layer: {header['data_layer'].get('requests_made', 0):,} API requests in this run; wall time {header['wall_time_s'] / 60:.1f} min.\n"
            f"* Full outputs: `{out}` (gitignored).\n", encoding="utf-8")
    print(f"run {run_id}: {'verdict ' + verdict['verdict'] if verdict else 'NOT a pre-registered run'}; outputs in {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backtest.orb_qqq", description=__doc__.split("\n")[0])
    ap.add_argument("--cache-dir", help="override data/cache")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the pre-registered ORB-on-QQQ backtest (all variants)")
    r.add_argument("--start", default=PREREG_START)
    r.add_argument("--end", default=PREREG_END)
    r.add_argument("--out", default=str(DEFAULT_OUT))
    r.add_argument("--review-file", help="also write the results markdown here (.review/orb-qqq-results.md)")
    r.add_argument("--offline", action="store_true", help="fail on a cache miss instead of calling the API")
    args = ap.parse_args(argv)
    try:
        return cmd_run(args)
    except (CacheMissError, HoldoutError, MissingCredentialsError, AlpacaDataError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

