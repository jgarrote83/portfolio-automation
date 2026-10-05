"""Minute-by-minute ORB simulation (Phase 2 backtest engine).

`simulate_day` replays ONE trading day for ONE variant from already-selected picks and their
one-minute bars; every strategy decision (levels, fills, sizing) is delegated to the pure
`orb.*` modules, so the logic that is tested is the logic that will trade. `run_backtest` loops the
window, loading each day's minute bars once and simulating every variant against them.

Data comes ONLY through `backtest.data` (`get_bars`, repo rule 6). The engine refuses any date on or
after 2026-01-01 and has NO override flag in Phase 2 (the holdout needs Jorge's explicit approval).

Event order inside one minute (pre-registration section 5): stops on positions opened in EARLIER
bars; then entries (an entry whose own bar also reaches the stop is stopped out in that bar); then
the daily-loss-limit check on the bar's close; then, on the exit bar, the end-of-day flatten.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date

from orb import signals
from orb.config import OrbConfig
from orb.signals import LONG, SHORT
from orb.sizing import position_size

from .data.bars import HoldoutError
from .data.config import HOLDOUT_START
from .sessions import close_minute
from .slippage import side_costs

MinuteBar = tuple[float, float, float, float, float]       # open, high, low, close, volume
WINDOW_1MIN = ("09:30", "16:00")                           # regular session, one-minute bars


# ============================================================================== data classes
@dataclass(frozen=True)
class Pick:
    """A Stock in Play with its first five-minute bar (09:30-09:35) and its ATR."""
    symbol: str
    rvol: float
    side: str | None              # LONG / SHORT, None = doji (no trade)
    bar_open: float
    bar_high: float
    bar_low: float
    bar_close: float
    atr: float


@dataclass(frozen=True)
class DayInput:
    day: date
    close_minute: int
    picks: tuple[Pick, ...]
    minute_bars: Mapping[str, Mapping[int, MinuteBar]]      # symbol -> minute-of-day -> bar


@dataclass(frozen=True)
class Trade:
    day: date
    symbol: str
    side: str
    shares: int
    binding: str                  # which sizing limit set the share count
    rvol: float
    atr: float
    trigger: float
    stop: float
    stop_distance: float
    entry_fill: float             # un-slipped
    entry_minute: int
    entry_gap: bool               # filled at the bar's open because it gapped through the trigger
    exit_fill: float              # un-slipped
    exit_minute: int
    exit_reason: str              # stop | stop_same_bar | time | loss_limit
    exit_stale: bool              # no bar at the exit minute: the last known close was used
    gross_pnl: float
    slippage_cost: float
    commission: float
    net_pnl: float
    r_gross: float
    r_net: float
    notional: float


@dataclass(frozen=True)
class Skip:
    day: date
    symbol: str
    reason: str    # doji | short_excluded | no_minute_data | zero_shares | never_triggered | cancelled_loss_limit


@dataclass
class DayResult:
    day: date
    picks_n: int
    trades: list[Trade] = field(default_factory=list)
    skips: list[Skip] = field(default_factory=list)
    loss_limit_hit: bool = False
    loss_limit_minute: int | None = None

    @property
    def gross_pnl(self) -> float:
        return sum(t.gross_pnl for t in self.trades)

    @property
    def slippage_cost(self) -> float:
        return sum(t.slippage_cost for t in self.trades)

    @property
    def commission(self) -> float:
        return sum(t.commission for t in self.trades)

    @property
    def net_pnl(self) -> float:
        return sum(t.net_pnl for t in self.trades)


@dataclass(frozen=True)
class Variant:
    """A named change to the primary config. Each pre-registered sensitivity changes ONE thing."""
    name: str
    overrides: tuple[tuple[str, object], ...] = ()

    def config(self, base: OrbConfig) -> OrbConfig:
        return base.replace(**dict(self.overrides)) if self.overrides else base


PRIMARY = Variant("primary")
SENSITIVITIES = (
    Variant("slippage_0c", (("slippage_cents_per_side", 0.0),)),
    Variant("slippage_1c", (("slippage_cents_per_side", 1.0),)),
    Variant("slippage_5c", (("slippage_cents_per_side", 5.0),)),
    Variant("long_only", (("allow_shorts", False),)),
    Variant("etfs_included", (("exclude_etfs", False),)),
)
ALL_VARIANTS = (PRIMARY,) + SENSITIVITIES


# ================================================================================ holdout guard
def assert_not_holdout(*days: date) -> None:
    """Rule 6 / pre-registration section 6: nothing on or after 2026-01-01 is ever read."""
    for d in days:
        if d >= HOLDOUT_START:
            raise HoldoutError(
                f"{d} is on/after {HOLDOUT_START}: the 2026 holdout is closed in Phase 2 and the "
                "engine has no override. It is run once, after a strategy is chosen, with Jorge's "
                "explicit approval.")


# ===================================================================================== one day
@dataclass
class _Pending:
    pick: Pick
    plan: signals.EntryPlan
    shares: int
    binding: str


@dataclass
class _Position:
    pick: Pick
    plan: signals.EntryPlan
    shares: int
    binding: str
    entry_fill: float
    entry_minute: int
    entry_gap: bool
    last_close: float


def simulate_day(day: DayInput, cfg: OrbConfig) -> DayResult:
    """Replay one day for one config. Deterministic: same inputs give identical outputs."""
    assert_not_holdout(day.day)
    res = DayResult(day.day, len(day.picks))
    last_entry = cfg.last_entry_minute(day.close_minute)
    exit_min = cfg.exit_minute(day.close_minute)
    slip_c, comm = cfg.slippage_cents_per_side, cfg.commission_per_share

    # ---- turn picks into pending entry orders -------------------------------------------------
    pending: dict[str, _Pending] = {}
    for p in day.picks:
        if p.side is None:
            res.skips.append(Skip(day.day, p.symbol, "doji"))
            continue
        if p.side == SHORT and not cfg.allow_shorts:
            res.skips.append(Skip(day.day, p.symbol, "short_excluded"))
            continue
        if not day.minute_bars.get(p.symbol):
            res.skips.append(Skip(day.day, p.symbol, "no_minute_data"))
            continue
        plan = signals.entry_plan(p.side, p.bar_high, p.bar_low, p.atr, cfg)
        size = position_size(plan.trigger, plan.stop_distance, cfg)
        if size.shares < 1:
            res.skips.append(Skip(day.day, p.symbol, "zero_shares"))
            continue
        pending[p.symbol] = _Pending(p, plan, size.shares, size.binding)

    open_pos: dict[str, _Position] = {}
    realized = 0.0                   # net P&L of trades closed so far (costs included)

    def close(pos: _Position, exit_fill: float, minute: int, reason: str, stale: bool = False) -> None:
        nonlocal realized
        sign = 1.0 if pos.pick.side == LONG else -1.0
        gross = sign * (exit_fill - pos.entry_fill) * pos.shares
        s1, c1 = side_costs(pos.shares, slip_c, comm)
        slippage, commission = 2 * s1, 2 * c1                   # entry fill + exit fill
        net = gross - slippage - commission
        risk = pos.shares * pos.plan.stop_distance
        trade = Trade(
            day=day.day, symbol=pos.pick.symbol, side=pos.pick.side, shares=pos.shares,
            binding=pos.binding, rvol=pos.pick.rvol, atr=pos.pick.atr, trigger=pos.plan.trigger,
            stop=pos.plan.stop, stop_distance=pos.plan.stop_distance, entry_fill=pos.entry_fill,
            entry_minute=pos.entry_minute, entry_gap=pos.entry_gap, exit_fill=exit_fill,
            exit_minute=minute, exit_reason=reason, exit_stale=stale, gross_pnl=gross,
            slippage_cost=slippage, commission=commission, net_pnl=net,
            r_gross=gross / risk, r_net=net / risk, notional=pos.shares * pos.entry_fill)
        res.trades.append(trade)
        realized += net

    def unrealized(pos: _Position) -> float:
        # marked at the last close; only the ENTRY-side costs have been paid
        sign = 1.0 if pos.pick.side == LONG else -1.0
        s1, c1 = side_costs(pos.shares, slip_c, comm)
        return sign * (pos.last_close - pos.entry_fill) * pos.shares - s1 - c1

    def flatten_all(minute: int, reason: str) -> None:
        for sym in sorted(open_pos):
            pos = open_pos.pop(sym)
            bar = day.minute_bars[sym].get(minute)
            close(pos, bar[3] if bar else pos.last_close, minute, reason, stale=bar is None)

    minutes = sorted({m for bars in day.minute_bars.values() for m in bars
                      if cfg.entry_start_minute <= m <= exit_min})
    limit = -cfg.daily_loss_limit_usd
    for m in minutes:
        # 1) stops on positions opened in earlier bars
        for sym in sorted(open_pos):
            bar = day.minute_bars[sym].get(m)
            if bar is None:
                continue
            o, h, lo, c, _v = bar
            pos = open_pos[sym]
            fill = signals.stop_fill(pos.pick.side, o, h, lo, pos.plan.stop)
            if fill is not None:
                del open_pos[sym]
                close(pos, fill, m, "stop")
            else:
                pos.last_close = c
        # 2) entries (stop orders live from the 09:35 bar through the last entry bar)
        if m <= last_entry:
            for sym in sorted(pending):
                bar = day.minute_bars[sym].get(m)
                if bar is None:
                    continue
                o, h, lo, c, _v = bar
                pend = pending[sym]
                fill = signals.trigger_fill(pend.plan.side, o, h, lo, pend.plan.trigger)
                if fill is None:
                    continue
                del pending[sym]
                pos = _Position(pend.pick, pend.plan, pend.shares, pend.binding, fill, m,
                                fill != pend.plan.trigger, c)
                if signals.stop_hit_in_entry_bar(pend.plan.side, h, lo, pend.plan.stop):
                    close(pos, pend.plan.stop, m, "stop_same_bar")
                else:
                    open_pos[sym] = pos
        # 3) daily loss limit, on this bar's close
        if realized + sum(unrealized(p) for p in open_pos.values()) <= limit:
            res.loss_limit_hit, res.loss_limit_minute = True, m
            for sym in sorted(pending):
                res.skips.append(Skip(day.day, sym, "cancelled_loss_limit"))
            pending.clear()
            flatten_all(m, "loss_limit")
            break
        # 4) end-of-day exit at the close of the exit bar
        if m == exit_min:
            flatten_all(m, "time")
            break
    if open_pos:                      # no bar at the exit minute for anyone: stale last close
        flatten_all(exit_min, "time")
    for sym in sorted(pending):
        res.skips.append(Skip(day.day, sym, "never_triggered"))
    res.trades.sort(key=lambda t: (t.entry_minute, t.symbol))
    return res


# ================================================================================ whole window
@dataclass
class BacktestRun:
    cfg: OrbConfig
    window_days: list[date]
    results: dict[str, list[DayResult]]          # variant name -> one DayResult per window day
    variants: tuple[Variant, ...]
    selection_stats: dict
    minute_data_stats: dict


def load_minute_bars(layer, day: date, symbols: Iterable[str]) -> dict[str, dict[int, MinuteBar]]:
    """One day's regular-session one-minute SIP bars, as symbol -> {minute-of-day: bar}."""
    syms = sorted(set(symbols))
    if not syms:
        return {}
    df = layer.get_bars(syms, day, day, "1Min", "sip", window=WINDOW_1MIN)
    out: dict[str, dict[int, MinuteBar]] = {s: {} for s in syms}
    if df.empty:
        return out
    ts = df["ts"]
    mins = (ts.dt.hour * 60 + ts.dt.minute).tolist()
    for sym, m, o, h, lo, c, v in zip(df["symbol"].tolist(), mins, df["open"].tolist(),
                                      df["high"].tolist(), df["low"].tolist(),
                                      df["close"].tolist(), df["volume"].tolist()):
        out[sym][m] = (o, h, lo, c, v)
    return out


def run_backtest(layer, selection, cfg: OrbConfig, variants: Iterable[Variant] = ALL_VARIANTS, *,
                 progress: Callable[[str], None] | None = None) -> BacktestRun:
    """Simulate every variant over the selection's window. `selection` comes from
    `backtest.selection.build_selection`; its `picks_by_day[day][include_etfs]` holds the top-N
    picks for the ETF-excluded / ETF-included universes."""
    variants = tuple(variants)
    days = list(selection.window_days)
    if days:
        assert_not_holdout(*days)
    results: dict[str, list[DayResult]] = {v.name: [] for v in variants}
    cfgs = {v.name: v.config(cfg) for v in variants}
    t0 = time.time()
    n_missing_pick_days = 0
    for i, d in enumerate(days):
        per_universe = selection.picks_by_day.get(d, {})
        need = {p.symbol for v in variants for p in per_universe.get(not cfgs[v.name].exclude_etfs, ())
                if p.side is not None}
        bars = load_minute_bars(layer, d, need)
        n_missing_pick_days += sum(1 for s in need if not bars.get(s))
        cm = close_minute(d)
        for v in variants:
            vcfg = cfgs[v.name]
            picks = tuple(per_universe.get(not vcfg.exclude_etfs, ()))
            sub = {p.symbol: bars.get(p.symbol, {}) for p in picks if p.side is not None}
            results[v.name].append(simulate_day(DayInput(d, cm, picks, sub), vcfg))
        if progress and (i + 1) % 25 == 0:
            progress(f"simulated {i + 1}/{len(days)} days ({time.time() - t0:.0f}s)")
    return BacktestRun(cfg, days, results, variants, dict(selection.stats),
                       {"picks_without_minute_bars": n_missing_pick_days})
