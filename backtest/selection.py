"""Universe -> Relative Volume -> top-N Stocks in Play -> direction, from cached SIP bars.

Every decision is delegated to the pure `orb.universe` / `orb.signals` functions; this module only
fetches the data (through the Phase 1 data layer, repo rule 6) and feeds them. For each trading day
in the window it produces the picks for BOTH universes -- ETFs excluded (the primary variant) and
ETFs included (a reported sensitivity) -- from one data fetch.

What is used on day D (nothing later): the 14 PRIOR daily bars (ATR, average volume) and D's own
open; the 09:30-09:35 five-minute bar of D and of the 14 prior days (Relative Volume, direction,
the trigger levels). The simulation after 09:35 is the engine's job.
"""
from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from orb import signals, universe
from orb.config import OrbConfig

from .engine import Pick, assert_not_holdout
from .iex_vs_sip import needed_open_symbols, panels_from_daily

FETCH_FROM = "2023-12-01"      # so the first window day has a full 14-day lookback (+1 for prior close)


@dataclass
class Selection:
    window_days: list[date]
    # day -> {include_etfs: picks (top-N by RVOL, with direction; doji picks carry side=None)}
    picks_by_day: dict[date, dict[bool, list[Pick]]]
    stats: dict = field(default_factory=dict)


def _log_default(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def _as_date(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x))


def _opening_panels(bars: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Long 5Min window bars (one 09:30 bar per symbol-day) -> wide day x symbol panels."""
    if bars.empty:
        return {k: pd.DataFrame() for k in ("open", "high", "low", "close", "volume")}
    b = bars.assign(day=bars["ts"].dt.date)
    return {k: b.pivot_table(index="day", columns="symbol", values=k, aggfunc="first").sort_index()
            for k in ("open", "high", "low", "close", "volume")}


def build_selection(layer, symbols: Iterable[str], etfs: frozenset[str], cfg: OrbConfig,
                    start, end, *, fetch_from: str = FETCH_FROM, daily_batch: int = 1000,
                    offline: bool = False, log: Callable[[str], None] | None = None) -> Selection:
    """`symbols` is the candidate universe BEFORE ETF handling (Core roster already removed by the
    caller). `etfs` is the ETF set; ETFs are dropped at ranking time for the ETF-excluded universe."""
    log = log or _log_default
    s_day, e_day = _as_date(start), _as_date(end)
    assert_not_holdout(s_day, e_day)
    symbols = sorted(set(symbols))
    n = cfg.lookback_days

    window_days = layer.trading_days(s_day, e_day, offline=offline)
    frames = []
    for i in range(0, len(symbols), daily_batch):
        chunk = symbols[i:i + daily_batch]
        frames.append(layer.get_bars(chunk, fetch_from, e_day, "1Day", "sip", offline=offline))
        log(f"SIP daily bars: {min(i + daily_batch, len(symbols))}/{len(symbols)} symbols")
    frames = [f for f in frames if not f.empty]
    if not frames:
        return Selection(window_days, {}, {"days": len(window_days), "note": "no daily bars"})
    daily = pd.concat(frames, ignore_index=True)

    panels = panels_from_daily(daily)
    all_days = list(panels["close"].index)
    syms_p = list(panels["close"].columns)
    col = {s: j for j, s in enumerate(syms_p)}
    pos = {d: i for i, d in enumerate(all_days)}
    opn, hi, lo_px, cls, vol = (panels[k].to_numpy(dtype=float)
                                for k in ("open", "high", "low", "close", "volume"))

    # ---- 1) universe, per window day (prior-14-day history + the day's own open) ---------------
    eligible: dict[date, list[tuple[str, float]]] = {}
    for D in window_days:
        i = pos.get(D)
        if i is None or i < n:
            continue
        lo = i - n
        row = opn[i]
        out: list[tuple[str, float]] = []
        for j in np.nonzero(~np.isnan(row))[0]:
            if not universe.open_ok(row[j], cfg):
                continue
            prior_close = cls[lo - 1, j] if lo >= 1 else None
            chk = universe.check_universe(row[j], hi[lo:i, j].tolist(), lo_px[lo:i, j].tolist(),
                                          cls[lo:i, j].tolist(), vol[lo:i, j].tolist(), prior_close, cfg)
            if chk.ok:
                out.append((syms_p[j], chk.atr))
        eligible[D] = out
    mean_elig = float(np.mean([len(v) for v in eligible.values()])) if eligible else 0.0
    log(f"universe: mean {mean_elig:.0f} names/day over {len(eligible)} days")

    # ---- 2) opening-window bars: candidates on each day D need D and the 14 days before ---------
    need = needed_open_symbols({D: [s for s, _ in v] for D, v in eligible.items()}, all_days, lookback=n)
    by_month: dict[tuple[int, int], dict[date, set[str]]] = {}
    for d, syms in need.items():
        by_month.setdefault((d.year, d.month), {})[d] = syms
    parts = []
    window = (cfg.opening_window_start, cfg.opening_window_end)
    for k, ym in enumerate(sorted(by_month)):
        days = sorted(by_month[ym])
        union = sorted(set().union(*by_month[ym].values()))
        parts.append(layer.get_bars(union, days[0], days[-1], "5Min", "sip", window=window,
                                    offline=offline))
        log(f"SIP opening bars: month {k + 1}/{len(by_month)} ({ym[0]}-{ym[1]:02d}, {len(union)} symbols)")
    parts = [p for p in parts if not p.empty]
    ob = _opening_panels(pd.concat(parts, ignore_index=True) if parts else pd.DataFrame())
    ob_ix = {k: (v.reindex(index=all_days, columns=syms_p).to_numpy(dtype=float)
                 if not v.empty else np.full((len(all_days), len(syms_p)), np.nan))
             for k, v in ob.items()}

    # ---- 3) Relative Volume, ranking, direction ------------------------------------------------
    picks_by_day: dict[date, dict[bool, list[Pick]]] = {}
    qualified_counts, qualified_noetf, picks_total, etf_in_top = [], [], 0, 0
    days_lt_n = 0
    for D in window_days:
        if D not in eligible:
            continue
        i = pos[D]
        atr_of = dict(eligible[D])
        rvols: dict[str, float | None] = {}
        for sym in atr_of:
            j = col[sym]
            rvols[sym] = universe.relative_volume(ob_ix["volume"][i, j],
                                                  ob_ix["volume"][i - n:i, j].tolist(), cfg)
        ranked = {True: universe.rank_stocks_in_play(rvols, cfg),
                  False: universe.rank_stocks_in_play({s: r for s, r in rvols.items() if s not in etfs}, cfg)}
        per: dict[bool, list[Pick]] = {}
        for include_etfs, names in ranked.items():
            picks = []
            for sym in names:
                j = col[sym]
                o, h, lo_, c = (ob_ix[k][i, j] for k in ("open", "high", "low", "close"))
                if np.isnan(o) or np.isnan(c):
                    continue
                picks.append(Pick(sym, float(rvols[sym]), signals.direction_from_bar(o, c),
                                  float(o), float(h), float(lo_), float(c), float(atr_of[sym])))
            per[include_etfs] = picks
        picks_by_day[D] = per
        q_all = sum(1 for r in rvols.values() if r is not None and r >= cfg.rvol_min)
        q_noetf = sum(1 for s, r in rvols.items() if s not in etfs and r is not None and r >= cfg.rvol_min)
        qualified_counts.append(q_all)
        qualified_noetf.append(q_noetf)
        picks_total += len(per[False])
        etf_in_top += sum(1 for p in per[True] if p.symbol in etfs)
        days_lt_n += int(q_noetf < cfg.top_n)

    stats = {
        "window_days": len(window_days), "days_with_selection": len(picks_by_day),
        "symbols_considered": len(symbols), "symbols_with_daily_bars": len(syms_p),
        "mean_universe_size_incl_etfs": mean_elig,
        "mean_qualified_rvol_incl_etfs": float(np.mean(qualified_counts)) if qualified_counts else 0.0,
        "mean_qualified_rvol_excl_etfs": float(np.mean(qualified_noetf)) if qualified_noetf else 0.0,
        "days_with_fewer_than_top_n_qualified_excl_etfs": days_lt_n,
        "picks_total_excl_etfs": picks_total,
        "etf_picks_in_top_n_incl_etfs": etf_in_top,
    }
    return Selection(window_days, picks_by_day, stats)
