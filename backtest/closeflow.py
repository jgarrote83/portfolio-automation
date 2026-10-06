"""SPY close-flow backtest: if SPY has moved clearly by 15:30, trade in that direction and exit at the close.

Rules, thresholds and the mechanical go/no-go are pre-registered in
`docs/specs/CloseFlow_SPY_Preregistration.md` (locked at its first commit). This module holds the pure logic
(signal, direction, fills, P&L), the day loop, the metrics, the IEX-vs-SIP check and the CLI.

    PYTHONPATH=src python -m backtest.closeflow run [--review-file .review/closeflow-results.md]

Data comes ONLY through `backtest.data` (`get_bars`, repo rule 6): SPY `1Day` and `1Min`, raw, SIP (and IEX for
the feed check), plus the one dividend list recorded in the pre-registration. Any date on or after 2026-01-01 is
refused, and there is NO override flag. Only the exact pre-registered invocation produces a verdict.
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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from . import provenance
from .data import DataLayer
from .data.bars import CacheMissError, HoldoutError
from .data.client import AlpacaDataError
from .data.config import HOLDOUT_START, REPO_ROOT
from .data.credentials import MissingCredentialsError
from .sessions import close_minute

SPEC_VERSION = "closeflow-1.0"
PREREG_PATH = "docs/specs/CloseFlow_SPY_Preregistration.md"
PREREG_START, PREREG_END = "2024-01-02", "2025-12-31"
FETCH_FROM = "2023-12-01"                     # so 2024-01-02 has a previous session (2023-12-29)
WINDOW_1MIN = ("09:30", "16:00")
DEFAULT_OUT = REPO_ROOT / "reports" / "closeflow"
GO_MIN_SHARPE = 1.0
GO_YEARS = (2024, 2025)
_EPS_SHARES = 1e-9
_R_ROUND = 12                                 # decimals: a float-noise guard for the |r| >= threshold test

Bar = tuple[float, float, float, float, float]            # open, high, low, close, volume

# SPY cash-dividend ex-dates inside the window, from the single corporate-actions GET recorded in the
# pre-registration (section 4). The source has NO fourth-quarter-2025 ex-date: a known gap, disclosed there.
SPY_EX_DIVIDEND_DATES = (
    date(2024, 3, 15), date(2024, 6, 21), date(2024, 9, 20), date(2024, 12, 20),
    date(2025, 3, 21), date(2025, 6, 20), date(2025, 9, 19),
)


# ================================================================================== configuration
@dataclass(frozen=True)
class CloseFlowConfig:
    """The primary variant; each sensitivity changes exactly one field. Pinned to the pre-registration's
    machine-readable block by `tests/test_closeflow.py`."""
    spec_version: str = SPEC_VERSION
    symbol: str = "SPY"
    sleeve_capital_usd: float = 25_000.0
    entry_minutes_before_close: int = 30
    signal_minutes_before_entry: int = 1
    signal_mode: str = "pre_entry"                 # "pre_entry" | "first_half_hour"
    first_half_hour_signal_minute: str = "09:59"
    min_abs_r: float = 0.0
    allow_shorts: bool = True
    entry_slippage_cents: float = 1.0
    commission_per_share: float = 0.0035

    def replace(self, **changes) -> "CloseFlowConfig":
        return dataclasses.replace(self, **changes)

    def diff_from_default(self) -> dict:
        base = CloseFlowConfig()
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self)
                if getattr(self, f.name) != getattr(base, f.name)}


@dataclass(frozen=True)
class Variant:
    name: str
    overrides: tuple[tuple[str, object], ...] = ()

    def config(self, base: CloseFlowConfig) -> CloseFlowConfig:
        return base.replace(**dict(self.overrides)) if self.overrides else base


PRIMARY = Variant("primary")
SENSITIVITIES = (
    Variant("signal_first_half_hour", (("signal_mode", "first_half_hour"),)),
    Variant("threshold_0.5pct", (("min_abs_r", 0.005),)),
    Variant("long_only", (("allow_shorts", False),)),
    Variant("slippage_0c", (("entry_slippage_cents", 0.0),)),
    Variant("slippage_2c", (("entry_slippage_cents", 2.0),)),
    Variant("slippage_5c", (("entry_slippage_cents", 5.0),)),
)
ALL_VARIANTS = (PRIMARY,) + SENSITIVITIES


def assert_not_holdout(*days: date) -> None:
    """Rule 6 / pre-registration item 4: nothing on or after 2026-01-01 is ever read; no override flag."""
    for d in days:
        if d >= HOLDOUT_START:
            raise HoldoutError(f"{d} is on/after {HOLDOUT_START}: the 2026 holdout is closed and this code has "
                               "no override. It needs Jorge's explicit approval, after a strategy is chosen.")


def hhmm_to_minutes(hhmm: str) -> int:
    hh, mm = hhmm.split(":")
    return int(hh) * 60 + int(mm)


# ====================================================================================== pure logic
def signal_minutes(close_min: int, cfg: CloseFlowConfig) -> tuple[int, int]:
    """(signal bar stamp, entry bar stamp) in minutes since midnight ET. The entry bar is stamped
    calendar close - 30 minutes (15:30; 12:30 on a half-day); the primary signal bar is the one before it."""
    entry = close_min - cfg.entry_minutes_before_close
    if cfg.signal_mode == "pre_entry":
        return entry - cfg.signal_minutes_before_entry, entry
    if cfg.signal_mode == "first_half_hour":
        return hhmm_to_minutes(cfg.first_half_hour_signal_minute), entry
    raise ValueError(f"unknown signal_mode {cfg.signal_mode!r}")


def signal_return(signal_close: float, prev_close: float) -> float:
    """r = close of the signal bar / previous session's daily close - 1 (raw prices)."""
    return signal_close / prev_close - 1.0


def decide(signal_close: float, prev_close: float, cfg: CloseFlowConfig) -> tuple[str | None, str]:
    """-> (side or None, reason). r = 0 means the two prices are exactly equal (compared directly)."""
    if signal_close == prev_close:
        return None, "flat"
    r = signal_return(signal_close, prev_close)
    if abs(round(r, _R_ROUND)) < cfg.min_abs_r:
        return None, "below_threshold"
    side = "long" if r > 0 else "short"
    if side == "short" and not cfg.allow_shorts:
        return None, "short_excluded"
    return side, "trade"


def share_count(entry_open: float, sleeve: float) -> int:
    """floor(sleeve / entry price), on the un-slipped open, tolerant of float noise."""
    if not (entry_open > 0) or not (sleeve > 0):
        return 0
    return int(math.floor(sleeve / entry_open + _EPS_SHARES))


def entry_fill(entry_open: float, side: str, slippage_cents: float) -> float:
    """A long buys at open + slippage, a short sells at open - slippage."""
    s = slippage_cents / 100.0
    return entry_open + s if side == "long" else entry_open - s


@dataclass(frozen=True)
class Trade:
    day: date
    side: str
    r: float
    shares: int
    entry_open: float
    entry_fill: float
    exit_price: float
    gross_pnl: float
    slippage_cost: float
    commission: float
    net_pnl: float
    net_ret_bps: float


def make_trade(day: date, side: str, r: float, entry_open: float, exit_price: float,
               cfg: CloseFlowConfig) -> Trade | None:
    shares = share_count(entry_open, cfg.sleeve_capital_usd)
    if shares < 1:
        return None
    sign = 1.0 if side == "long" else -1.0
    gross = sign * shares * (exit_price - entry_open)               # at the un-slipped open
    slippage = shares * cfg.entry_slippage_cents / 100.0            # entry only
    commission = 2.0 * shares * cfg.commission_per_share            # entry fill and exit fill
    net = gross - slippage - commission
    return Trade(day, side, r, shares, entry_open, entry_fill(entry_open, side, cfg.entry_slippage_cents),
                 exit_price, gross, slippage, commission, net, net / (shares * entry_open) * 1e4)


# ================================================================================== the day loop
@dataclass(frozen=True)
class Day:
    day: date
    close_minute: int
    prev_close: float | None
    daily_close: float | None
    bars: Mapping[int, Bar]                          # minute-of-day -> one-minute bar


@dataclass(frozen=True)
class DayOutcome:
    day: date
    status: str                                      # traded | flat | below_threshold | short_excluded | skipped
    reason: str                                      # skip reason, else the status again
    r: float | None
    trade: Trade | None = None


def simulate_day(d: Day, cfg: CloseFlowConfig) -> DayOutcome:
    """One day for one config. A missing signal/entry bar or close SKIPS the day: it is never filled from
    another bar. The signal uses only the signal bar (stamped before the entry bar) and the previous close."""
    assert_not_holdout(d.day)
    sig_m, ent_m = signal_minutes(d.close_minute, cfg)
    if d.prev_close is None:
        return DayOutcome(d.day, "skipped", "no_prev_close", None)
    sig, ent = d.bars.get(sig_m), d.bars.get(ent_m)
    if sig is None:
        return DayOutcome(d.day, "skipped", "no_signal_bar", None)
    if ent is None:
        return DayOutcome(d.day, "skipped", "no_entry_bar", None)
    if d.daily_close is None:
        return DayOutcome(d.day, "skipped", "no_daily_close", None)
    r = signal_return(sig[3], d.prev_close)
    side, why = decide(sig[3], d.prev_close, cfg)
    if side is None:
        return DayOutcome(d.day, why, why, r)
    trade = make_trade(d.day, side, r, ent[0], d.daily_close, cfg)
    if trade is None:
        return DayOutcome(d.day, "skipped", "zero_shares", r)
    return DayOutcome(d.day, "traded", "traded", r, trade)


def run_variants(days: list[Day], variants: Iterable[Variant], cfg: CloseFlowConfig) -> dict[str, list[DayOutcome]]:
    if days:
        assert_not_holdout(*[d.day for d in days])
    return {v.name: [simulate_day(d, v.config(cfg)) for d in days] for v in variants}


# ========================================================================================== data
def _day_bars(df) -> dict[date, dict[int, Bar]]:
    out: dict[date, dict[int, Bar]] = {}
    if df.empty:
        return out
    ts = df["ts"]
    days = ts.dt.date.tolist()
    mins = (ts.dt.hour * 60 + ts.dt.minute).tolist()
    for d, m, o, h, lo, c, v in zip(days, mins, df["open"].tolist(), df["high"].tolist(), df["low"].tolist(),
                                    df["close"].tolist(), df["volume"].tolist()):
        out.setdefault(d, {})[m] = (o, h, lo, c, v)
    return out


def load_days(layer, cfg: CloseFlowConfig, start: str, end: str, *, feed: str = "sip",
              offline: bool = False) -> list[Day]:
    """SPY days for the window from cached/fetched bars: previous session close and the day's daily close
    from the 1Day series, one-minute bars for the regular session."""
    s_day, e_day = date.fromisoformat(start), date.fromisoformat(end)
    assert_not_holdout(s_day, e_day)
    days = layer.trading_days(s_day, e_day, offline=offline)
    daily = layer.get_bars([cfg.symbol], FETCH_FROM, e_day, "1Day", feed, offline=offline)
    closes: dict[date, float] = {}
    if not daily.empty:
        closes = dict(zip(daily["ts"].dt.date.tolist(), daily["close"].tolist()))
    series = sorted(closes)
    prev_of = {d: (series[i - 1] if i > 0 else None) for i, d in enumerate(series)}
    minutes = _day_bars(layer.get_bars([cfg.symbol], s_day, e_day, "1Min", feed, window=WINDOW_1MIN,
                                       offline=offline))
    out = []
    for d in days:
        p = prev_of.get(d)
        out.append(Day(d, close_minute(d), closes.get(p) if p is not None else None, closes.get(d),
                       minutes.get(d, {})))
    return out


# ======================================================================================= metrics
def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _block(trades: list[Trade]) -> dict:
    n = len(trades)
    if not n:
        return {"trades": 0, "net_pnl": 0.0, "hit_ratio": None, "avg_net_ret_bps": None}
    return {"trades": n, "net_pnl": sum(t.net_pnl for t in trades),
            "hit_ratio": sum(1 for t in trades if t.net_pnl > 0) / n,
            "avg_net_ret_bps": sum(t.net_ret_bps for t in trades) / n}


def compute_metrics(outcomes: list[DayOutcome], cfg: CloseFlowConfig) -> dict:
    """Everything the pre-registration says a run reports, for ONE variant (sections 2, 6 items 19-23)."""
    sleeve = cfg.sleeve_capital_usd
    outs = sorted(outcomes, key=lambda o: o.day)
    daily = [o.trade.net_pnl if o.trade else 0.0 for o in outs]
    rets = [x / sleeve for x in daily]
    n = len(rets)
    mean = statistics.fmean(rets) if rets else None
    std = statistics.stdev(rets) if n > 1 else None
    sharpe = (mean / std * math.sqrt(252)) if (mean is not None and std and std > 0) else None
    tstat = (mean / (std / math.sqrt(n))) if (mean is not None and std and std > 0) else None
    equity, peak, max_dd, max_dd_pct = sleeve, sleeve, 0.0, 0.0
    for x in daily:
        equity += x
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        max_dd_pct = max(max_dd_pct, (peak - equity) / peak if peak > 0 else 0.0)
    trades = [o.trade for o in outs if o.trade]
    by_year = {}
    for yr in sorted({o.day.year for o in outs}):
        yo = [o for o in outs if o.day.year == yr]
        yt = [o.trade for o in yo if o.trade]
        by_year[str(yr)] = {"days": len(yo), "trades": len(yt), "gross_pnl": sum(t.gross_pnl for t in yt),
                            "net_pnl": sum(t.net_pnl for t in yt)}
    status = Counter(o.status for o in outs)
    skips = Counter(o.reason for o in outs if o.status == "skipped")
    return {
        "days": n, "net_pnl": sum(daily), "gross_pnl": sum(t.gross_pnl for t in trades),
        "slippage_cost": sum(t.slippage_cost for t in trades), "commission": sum(t.commission for t in trades),
        "mean_daily_return_pct": None if mean is None else mean * 100, "std_daily_return_pct": None if std is None else std * 100,
        "sharpe_net": sharpe, "t_stat_mean_daily_return": tstat,
        "max_drawdown_usd": max_dd, "max_drawdown_pct_of_peak": max_dd_pct * 100,
        "by_year": by_year, "trades": len(trades),
        "hit_ratio": (sum(1 for t in trades if t.net_pnl > 0) / len(trades)) if trades else None,
        "avg_net_ret_bps": (sum(t.net_ret_bps for t in trades) / len(trades)) if trades else None,
        "long": _block([t for t in trades if t.side == "long"]),
        "short": _block([t for t in trades if t.side == "short"]),
        "days_traded": status.get("traded", 0), "days_flat": status.get("flat", 0),
        "days_below_threshold": status.get("below_threshold", 0), "days_short_excluded": status.get("short_excluded", 0),
        "days_skipped": status.get("skipped", 0), "skip_reasons": dict(sorted(skips.items())),
    }


def go_no_go(m: dict) -> dict:
    """The pre-registered mechanical verdict, on the primary variant only."""
    sharpe = m.get("sharpe_net")
    pnl = {y: m.get("by_year", {}).get(str(y), {}).get("net_pnl") for y in GO_YEARS}
    checks = {f"net_sharpe >= {GO_MIN_SHARPE}": sharpe is not None and sharpe >= GO_MIN_SHARPE,
              **{f"net_pnl_{y} > 0": (pnl[y] is not None and pnl[y] > 0) for y in GO_YEARS}}
    return {"verdict": "GO" if all(checks.values()) else "NO-GO", "checks": checks, "net_sharpe": sharpe,
            "net_pnl_by_year": pnl}


def exdiv_disclosure(outcomes: list[DayOutcome], cfg: CloseFlowConfig,
                     exdiv: Iterable[date] = SPY_EX_DIVIDEND_DATES) -> dict:
    """Disclosure only (pre-registration section 4): the ex-dividend days with the primary's P&L and r, and the
    verdict's three numbers recomputed with those days removed from the daily series."""
    ex = set(exdiv)
    rows = []
    for o in sorted(outcomes, key=lambda x: x.day):
        if o.day in ex:
            rows.append({"day": o.day.isoformat(), "status": o.status, "r_pct": None if o.r is None else o.r * 100,
                         "side": o.trade.side if o.trade else None,
                         "net_pnl": o.trade.net_pnl if o.trade else 0.0})
    kept = [o for o in outcomes if o.day not in ex]
    m = compute_metrics(kept, cfg)
    return {"days": rows, "n_listed": len(ex), "n_in_series": len(rows), "metrics_excluding": {
        "days": m["days"], "sharpe_net": m["sharpe_net"], "net_pnl": m["net_pnl"],
        "net_pnl_2024": m["by_year"].get("2024", {}).get("net_pnl"),
        "net_pnl_2025": m["by_year"].get("2025", {}).get("net_pnl"),
        "verdict_if_excluded": go_no_go(m)["verdict"]}}


def close_vs_last_bar(days: list[Day]) -> dict:
    """Sanity check: |SIP daily-bar close - close of the last regular-session one-minute bar| in basis points."""
    diffs = []
    for d in days:
        last = d.bars.get(d.close_minute - 1)
        if d.daily_close is not None and last is not None:
            diffs.append((abs(d.daily_close / last[3] - 1.0) * 1e4, d.day))
    return {"days_compared": len(diffs), "mean_abs_bps": statistics.fmean(x for x, _ in diffs) if diffs else None,
            "max_abs_bps": max(diffs)[0] if diffs else None, "max_abs_day": max(diffs)[1].isoformat() if diffs else None}


# ================================================================================= IEX vs SIP check
def _compare(pairs: list[tuple[date, float, float]]) -> dict:
    n = len(pairs)
    diffs = [abs(a - b) * 1e4 for _, a, b in pairs]
    differ = [(d, a * 1e4, b * 1e4) for d, a, b in pairs if _sign(a) != _sign(b)]
    return {"days_compared": n,
            "sign_agreement_pct": (100.0 * (n - len(differ)) / n) if n else None,
            "mean_abs_diff_bps": statistics.fmean(diffs) if diffs else None,
            "max_abs_diff_bps": max(diffs) if diffs else None,
            "sign_differs_n": len(differ),
            "sign_differs": [{"day": d.isoformat(), "r_sip_bps": a, "r_iex_bps": b} for d, a, b in differ]}


def iex_vs_sip(sip_days: list[Day], iex_days: list[Day], cfg: CloseFlowConfig) -> dict:
    """r from IEX's signal bar vs SIP's (pre-registration section 5, items 24-27). A: IEX bar / the SIP previous
    close (isolates the minute bar). B: IEX bar / the IEX previous close (what a free-feed-only engine computes)."""
    iex = {d.day: d for d in iex_days}
    a_pairs, b_pairs = [], []
    missing_bar = missing_prev = 0
    for s in sip_days:
        sig_m, _ = signal_minutes(s.close_minute, cfg)
        sb = s.bars.get(sig_m)
        if sb is None or s.prev_close is None:
            continue
        r_sip = signal_return(sb[3], s.prev_close)
        i = iex.get(s.day)
        ib = i.bars.get(sig_m) if i else None
        if ib is None:
            missing_bar += 1
            continue
        a_pairs.append((s.day, r_sip, signal_return(ib[3], s.prev_close)))
        if i.prev_close is None:
            missing_prev += 1
            continue
        b_pairs.append((s.day, r_sip, signal_return(ib[3], i.prev_close)))
    return {"A_iex_bar_over_sip_prev_close": _compare(a_pairs), "B_iex_bar_over_iex_prev_close": _compare(b_pairs),
            "days_without_iex_signal_bar": missing_bar, "days_without_iex_prev_close": missing_prev,
            "days_in_sip_series": len(sip_days)}


# ================================================================================== run and output
def validity(start: str, end: str, cfg: CloseFlowConfig, variants, prereg: dict) -> list[str]:
    """Reasons this run is NOT the pre-registered one (empty list = valid for a verdict)."""
    why = []
    if (start, end) != (PREREG_START, PREREG_END):
        why.append(f"window {start}..{end} is not the pre-registered {PREREG_START}..{PREREG_END}")
    if cfg != CloseFlowConfig():
        why.append(f"config differs from the pre-registered defaults: {cfg.diff_from_default()}")
    if tuple(v.name for v in variants) != tuple(v.name for v in ALL_VARIANTS):
        why.append("not the full pre-registered variant set")
    if prereg.get("unchanged") is False:
        why.append("the pre-registration file no longer matches its first-commit hash")
    elif prereg.get("unchanged") is None:
        why.append("the pre-registration's first-commit hash could not be verified (no git history)")
    return why


def _f(x, nd=2, pct=False, usd=False) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    s = f"{x:,.{nd}f}"
    return ("$" + s if usd else s) + ("%" if pct else "")


def headline_table(metrics: dict[str, dict], order: list[str]) -> str:
    cols = ["Variant", "Net Sharpe", "t-stat", "Net P&L", "2024 net", "2025 net", "Max DD", "Trades", "Hit ratio",
            "Avg net / trade (bps)", "Long / Short trades", "Flat / skipped days"]
    lines = ["| " + " | ".join(cols) + " |", "|" + " --- |" * len(cols)]
    for name in order:
        m = metrics[name]
        by = m["by_year"]
        lines.append("| " + " | ".join([
            f"**{name}**" if name == "primary" else name, _f(m["sharpe_net"], 2), _f(m["t_stat_mean_daily_return"], 2),
            _f(m["net_pnl"], 0, usd=True), _f(by.get("2024", {}).get("net_pnl"), 0, usd=True),
            _f(by.get("2025", {}).get("net_pnl"), 0, usd=True),
            f"{_f(m['max_drawdown_usd'], 0, usd=True)} ({_f(m['max_drawdown_pct_of_peak'], 1, pct=True)})",
            f"{m['trades']:,}", _f(None if m["hit_ratio"] is None else m["hit_ratio"] * 100, 1, pct=True),
            _f(m["avg_net_ret_bps"], 2), f"{m['long']['trades']:,} / {m['short']['trades']:,}",
            f"{m['days_flat']} / {m['days_skipped']}"]) + " |")
    return "\n".join(lines)


def variant_detail(name: str, m: dict) -> str:
    out = [f"### {name}", ""]
    out.append(f"* Net P&L {_f(m['net_pnl'], 0, usd=True)} (gross {_f(m['gross_pnl'], 0, usd=True)}, slippage "
               f"{_f(m['slippage_cost'], 0, usd=True)}, commission {_f(m['commission'], 0, usd=True)}); {m['trades']:,} trades "
               f"over {m['days']} trading days (traded {m['days_traded']}, flat {m['days_flat']}, below threshold "
               f"{m['days_below_threshold']}, short excluded {m['days_short_excluded']}, skipped {m['days_skipped']}"
               + (f": {m['skip_reasons']}" if m["skip_reasons"] else "") + ").")
    out.append(f"* Net Sharpe {_f(m['sharpe_net'], 3)}; t-statistic of the mean daily return {_f(m['t_stat_mean_daily_return'], 2)}; "
               f"mean daily return {_f(m['mean_daily_return_pct'], 4, pct=True)}, std {_f(m['std_daily_return_pct'], 4, pct=True)}; "
               f"max drawdown {_f(m['max_drawdown_usd'], 0, usd=True)} ({_f(m['max_drawdown_pct_of_peak'], 1, pct=True)} of peak).")
    hr = None if m["hit_ratio"] is None else m["hit_ratio"] * 100
    out.append(f"* Hit ratio {_f(hr, 1, pct=True)}; average net return per trade {_f(m['avg_net_ret_bps'], 2)} bps.")
    for side in ("long", "short"):
        b = m[side]
        hr_s = None if b["hit_ratio"] is None else b["hit_ratio"] * 100
        out.append(f"* {side.capitalize()}: {b['trades']:,} trades, net {_f(b['net_pnl'], 0, usd=True)}, hit ratio "
                   f"{_f(hr_s, 1, pct=True)}, average {_f(b['avg_net_ret_bps'], 2)} bps.")
    out += ["", "| Year | Days | Trades | Gross P&L | Net P&L |", "| --- | --- | --- | --- | --- |"]
    for y, b in m["by_year"].items():
        out.append(f"| {y} | {b['days']} | {b['trades']:,} | {_f(b['gross_pnl'], 0, usd=True)} | {_f(b['net_pnl'], 0, usd=True)} |")
    return "\n".join(out) + "\n"


def render_exdiv(d: dict) -> str:
    rows = ["| Ex-date | Status | r (SPY, to the signal bar) | Side | Net P&L |", "| --- | --- | --- | --- | --- |"]
    for r in d["days"]:
        rows.append(f"| {r['day']} | {r['status']} | {_f(r['r_pct'], 3, pct=True)} | {r['side'] or '-'} | {_f(r['net_pnl'], 2, usd=True)} |")
    me = d["metrics_excluding"]
    return ("## Ex-dividend disclosure (never replaces the verdict)\n\n"
            f"Raw prices put SPY's overnight dividend drop (about 0.3%) into r on its ex-dividend days. The pre-registered list "
            f"has {d['n_listed']} days (one corporate-actions GET; **the source has no Q4-2025 ex-date, so one further ex-date "
            f"expected in late December 2025 is not listed**); {d['n_in_series']} of them are trading days in the series.\n\n"
            + "\n".join(rows) + "\n\n"
            f"With those days removed from the daily series ({me['days']} days left): net Sharpe {_f(me['sharpe_net'], 3)}, "
            f"net P&L {_f(me['net_pnl'], 0, usd=True)} (2024 {_f(me['net_pnl_2024'], 0, usd=True)}, 2025 {_f(me['net_pnl_2025'], 0, usd=True)}); "
            f"the mechanical verdict on that series would be **{me['verdict_if_excluded']}**.\n")


def render_iex(d: dict, cap: int = 50) -> str:
    lines = ["## IEX vs. SIP check: could live trading run on the free feed?", "",
             "Numbers only; this does not enter the verdict and decides nothing. "
             f"{d['days_without_iex_signal_bar']} of {d['days_in_sip_series']} days had no IEX signal bar and are excluded "
             f"(and {d['days_without_iex_prev_close']} more had no IEX previous close, excluded from B).", "",
             "| Comparison | Days | Sign agreement | Mean abs diff in r | Max abs diff in r | Sign differs |",
             "| --- | --- | --- | --- | --- | --- |"]
    for key, label in (("A_iex_bar_over_sip_prev_close", "A: IEX 15:29 bar ÷ SIP previous close"),
                       ("B_iex_bar_over_iex_prev_close", "B: IEX 15:29 bar ÷ IEX previous close")):
        c = d[key]
        lines.append(f"| {label} | {c['days_compared']} | {_f(c['sign_agreement_pct'], 2, pct=True)} | "
                     f"{_f(c['mean_abs_diff_bps'], 2)} bps | {_f(c['max_abs_diff_bps'], 2)} bps | {c['sign_differs_n']} days |")
    for key, label in (("A_iex_bar_over_sip_prev_close", "A"), ("B_iex_bar_over_iex_prev_close", "B")):
        c = d[key]
        differs = c["sign_differs"]
        if not differs:
            lines += ["", f"**{label}: the sign never differs.**"]
            continue
        shown = differs if len(differs) <= cap else sorted(
            differs, key=lambda x: abs(x["r_sip_bps"] - x["r_iex_bps"]), reverse=True)[:cap]
        head = (f"**{label}: days where the sign differs ({len(differs)})**" if len(differs) <= cap else
                f"**{label}: the {cap} of {len(differs)} sign-differing days with the largest difference** (all are in iex_check.csv)")
        lines += ["", head, "", "| Day | r (SIP, bps) | r (IEX, bps) |", "| --- | --- | --- |"]
        lines += [f"| {x['day']} | {x['r_sip_bps']:.2f} | {x['r_iex_bps']:.2f} |" for x in sorted(shown, key=lambda x: x["day"])]
    return "\n".join(lines) + "\n"


def render_summary_md(header: dict, metrics: dict, verdict: dict | None, exdiv: dict, iex: dict, sanity: dict) -> str:
    order = list(header["variants"])
    top = [f"# SPY close-flow backtest results — run `{header['run_id']}`", ""]
    if not header["valid_for_verdict"]:
        top += ["> **NOT A PRE-REGISTERED RUN — no go/no-go verdict.** " + "; ".join(header["invalid_reasons"]) + ".", ""]
    if verdict:
        top += [f"## Mechanical verdict (primary variant): **{verdict['verdict']}**", "",
                "| Pre-registered check | Result |", "| --- | --- |"]
        top += [f"| {k} | {'PASS' if ok else 'FAIL'} |" for k, ok in verdict["checks"].items()]
        top += ["", f"Net Sharpe {_f(verdict['net_sharpe'], 3)}; net P&L "
                + ", ".join(f"{y}: {_f(p, 0, usd=True)}" for y, p in verdict["net_pnl_by_year"].items()) + ".", ""]
    top += ["## Headline table", "", headline_table(metrics, order), "",
            "## Variant detail", ""] + [variant_detail(n, metrics[n]) for n in order]
    top += [render_exdiv(exdiv), render_iex(iex)]
    top += ["## Sanity checks", "",
            f"* SIP daily-bar close vs. the close of the last regular-session one-minute bar (15:59; 12:59 on half-days): "
            f"{sanity['close_vs_last_bar']['days_compared']} days compared, mean {_f(sanity['close_vs_last_bar']['mean_abs_bps'], 2)} bps, "
            f"max {_f(sanity['close_vs_last_bar']['max_abs_bps'], 2)} bps (on {sanity['close_vs_last_bar']['max_abs_day']}).",
            f"* Days skipped for missing data (primary): {sanity['primary_skipped_days']}"
            + (f" — {sanity['primary_skipped_list']}" if sanity["primary_skipped_list"] else "") + ".", ""]
    return "\n".join(top)


def _csv(x):
    if isinstance(x, float):
        return f"{x:.8f}"
    if isinstance(x, date):
        return x.isoformat()
    return x


TRADE_COLUMNS = ["day", "side", "r", "shares", "entry_open", "entry_fill", "exit_price", "gross_pnl",
                 "slippage_cost", "commission", "net_pnl", "net_ret_bps"]


def write_run(out_dir: Path, header: dict, results: dict[str, list[DayOutcome]], metrics: dict, verdict: dict | None,
              exdiv: dict, iex: dict, sanity: dict) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def _w(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)

    _w(out_dir / "header.json", json.dumps(dict(header, metrics_verdict=verdict), indent=2, sort_keys=True, default=str) + "\n")
    _w(out_dir / "summary.md", render_summary_md(header, metrics, verdict, exdiv, iex, sanity))
    _w(out_dir / "exdividend.json", json.dumps(exdiv, indent=2, sort_keys=True, default=str) + "\n")
    _w(out_dir / "iex_check.json", json.dumps(iex, indent=2, sort_keys=True, default=str) + "\n")
    with (out_dir / "iex_check.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["comparison", "day", "r_sip_bps", "r_iex_bps"])
        for key in ("A_iex_bar_over_sip_prev_close", "B_iex_bar_over_iex_prev_close"):
            for x in iex[key]["sign_differs"]:
                w.writerow([key[0], x["day"], f"{x['r_sip_bps']:.4f}", f"{x['r_iex_bps']:.4f}"])
    written.append(out_dir / "iex_check.csv")
    for name, outs in results.items():
        vd = out_dir / name
        vd.mkdir(exist_ok=True)
        _w(vd / "metrics.json", json.dumps(metrics[name], indent=2, sort_keys=True, default=str) + "\n")
        with (vd / "trades.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(TRADE_COLUMNS)
            for o in outs:
                if o.trade:
                    d = dataclasses.asdict(o.trade)
                    w.writerow([_csv(d[c]) for c in TRADE_COLUMNS])
        written.append(vd / "trades.csv")
        with (vd / "daily.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["day", "status", "reason", "r", "net_pnl"])
            for o in outs:
                w.writerow([o.day.isoformat(), o.status, o.reason, "" if o.r is None else f"{o.r:.10f}",
                            f"{(o.trade.net_pnl if o.trade else 0.0):.8f}"])
        written.append(vd / "daily.csv")
    return written


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def execute(layer, start: str, end: str, cfg: CloseFlowConfig = CloseFlowConfig(),
            variants: tuple[Variant, ...] = ALL_VARIANTS, *, offline: bool = False,
            log: Callable[[str], None] = _log) -> dict:
    """Load SIP and IEX days, run every variant, and gather the disclosures. No files are written here."""
    assert_not_holdout(date.fromisoformat(start), date.fromisoformat(end))
    log("loading SPY SIP daily and one-minute bars")
    sip = load_days(layer, cfg, start, end, feed="sip", offline=offline)
    log("loading SPY IEX daily and one-minute bars (feed check)")
    iex_days = load_days(layer, cfg, start, end, feed="iex", offline=offline)
    results = run_variants(sip, variants, cfg)
    metrics = {v.name: compute_metrics(results[v.name], v.config(cfg)) for v in variants}
    primary = results["primary"]
    sanity = {"close_vs_last_bar": close_vs_last_bar(sip),
              "primary_skipped_days": sum(1 for o in primary if o.status == "skipped"),
              "primary_skipped_list": [f"{o.day.isoformat()} ({o.reason})" for o in primary if o.status == "skipped"][:30]}
    return {"days": sip, "results": results, "metrics": metrics, "exdiv": exdiv_disclosure(primary, cfg),
            "iex": iex_vs_sip(sip, iex_days, cfg), "sanity": sanity}


def cmd_run(args) -> int:
    cfg = CloseFlowConfig()
    assert_not_holdout(date.fromisoformat(args.start), date.fromisoformat(args.end))
    layer = DataLayer(args.cache_dir)
    t0 = time.time()
    res = execute(layer, args.start, args.end, cfg, ALL_VARIANTS, offline=args.offline)
    prereg = provenance.prereg_info(path=PREREG_PATH)
    why = validity(args.start, args.end, cfg, ALL_VARIANTS, prereg)
    commit = provenance.code_commit()
    now = datetime.now(timezone.utc)
    run_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{(commit['sha'] or 'nogit')[:7]}"
    verdict = go_no_go(res["metrics"]["primary"]) if not why else None
    header = {
        "run_id": run_id, "created_utc": now.isoformat(), "spec_version": cfg.spec_version,
        "window": {"start": args.start, "end": args.end, "trading_days": len(res["days"]), "fetch_from": FETCH_FROM},
        "config": dataclasses.asdict(cfg), "config_diff_from_default": cfg.diff_from_default(),
        "variants": {v.name: dict(v.overrides) for v in ALL_VARIANTS},
        "code_commit": commit, "preregistration": prereg, "valid_for_verdict": not why, "invalid_reasons": why,
        "ex_dividend_source": {"file": "data/reference/spy_dividends.json", "dates": [d.isoformat() for d in SPY_EX_DIVIDEND_DATES]},
        "data_layer": layer.cache_stats(), "wall_time_s": round(time.time() - t0, 1),
        "holdout": "2026 was never read (this code refuses any date on/after 2026-01-01)",
    }
    out = Path(args.out) / run_id
    write_run(out, header, res["results"], res["metrics"], verdict, res["exdiv"], res["iex"], res["sanity"])
    summary = (out / "summary.md").read_text(encoding="utf-8")
    if args.review_file:
        review = Path(args.review_file)
        review.parent.mkdir(parents=True, exist_ok=True)
        pre = header["preregistration"]
        review.write_text(
            f"<!-- generated by backtest.closeflow from {run_id}; do not edit by hand -->\n{summary.rstrip()}\n\n## Run provenance\n\n"
            f"* Run `{run_id}` on code commit `{commit['sha']}` (tree dirty: {commit['dirty']}); spec_version `{cfg.spec_version}`.\n"
            f"* Pre-registration `{pre['path']}` first committed in `{pre['first_commit']}`; unchanged since: {pre['unchanged']}.\n"
            f"* Window {args.start}..{args.end}, {len(res['days'])} trading days; the 2026 holdout was never read.\n"
            f"* Data layer: {header['data_layer'].get('requests_made', 0):,} API requests, wall time {header['wall_time_s'] / 60:.1f} min.\n"
            f"* Full outputs: `{out}` (gitignored).\n", encoding="utf-8")
    print(f"run {run_id}: {'verdict ' + verdict['verdict'] if verdict else 'NOT a pre-registered run'}; outputs in {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backtest.closeflow", description=__doc__.split("\n")[0])
    ap.add_argument("--cache-dir", help="override data/cache")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the pre-registered SPY close-flow backtest (all variants)")
    r.add_argument("--start", default=PREREG_START)
    r.add_argument("--end", default=PREREG_END)
    r.add_argument("--out", default=str(DEFAULT_OUT))
    r.add_argument("--review-file", help="also write the results markdown here (.review/closeflow-results.md)")
    r.add_argument("--offline", action="store_true", help="fail on a cache miss instead of calling the API")
    args = ap.parse_args(argv)
    try:
        return cmd_run(args)
    except (CacheMissError, HoldoutError, MissingCredentialsError, AlpacaDataError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
