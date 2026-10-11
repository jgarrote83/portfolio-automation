"""Safe wheel: a modeled 20-year screening backtest of a defined-risk SPY put-credit spread (step 1 of 2).

Pre-registered in `docs/specs/Safe_Wheel_Model_Preregistration.md` (locked at its first commit; `WheelConfig()`, the
gate and the variants are pinned to its machine-readable block by `tests/test_safe_wheel.py`). Option prices are
MODELED (Black-Scholes from the VIX and the underlying's daily closes), so the only possible outcomes are WORTH REAL
DATA and NOT WORTH REAL DATA: this can never approve trading.

Pure functions plus one simulation loop; reads nothing itself (the aligned daily inputs come from `wheel_data`). The
daily order of events is fixed (pre-registration item 4): (1) mark the open position, (2) exit it if a rule fires,
(3) update equity / peak / kill switch, (4) take the entry decision. Every date on/after 2026-01-01 is refused.
"""
from __future__ import annotations

import calendar
import dataclasses
import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import date, timedelta

from .data.bars import HoldoutError
from .wheel_data import WheelInputs

SPEC_VERSION = "safe-wheel-model-1.0"
PREREG_PATH = "docs/specs/Safe_Wheel_Model_Preregistration.md"
PREREG_START, PREREG_END = "2005-01-03", "2025-12-31"
HOLDOUT_START = date(2026, 1, 1)

# The screening gate (pre-registration section 2). All four must hold.
GATE_MIN_NET_SHARPE = 0.5
GATE_MAX_DRAWDOWN_PCT = 30.0
GATE_MIN_WORST_YEAR_PCT = -20.0
GATE_POSITIVE_YEARS_NUM, GATE_POSITIVE_YEARS_DEN = 2, 3
WORTH, NOT_WORTH = "WORTH REAL DATA", "NOT WORTH REAL DATA"

# Crisis close-up windows (pre-registration item 19), fixed by calendar date.
CRISIS_WINDOWS = (("Sept 2008 - Mar 2009", date(2008, 9, 1), date(2009, 3, 31)),
                  ("Feb - Apr 2020", date(2020, 2, 1), date(2020, 4, 30)),
                  ("2022 drawdown (Jan 3 - Oct 14)", date(2022, 1, 3), date(2022, 10, 14)))
CALIBRATION_FIRST_ROW = date(2007, 1, 3)
TRADING_DAYS_PER_YEAR = 252


def assert_not_holdout(*days: date) -> None:
    """Nothing on or after 2026-01-01 is ever used."""
    for d in days:
        if d >= HOLDOUT_START:
            raise HoldoutError(f"{d} is on/after {HOLDOUT_START}: the 2026 holdout is closed to the wheel study "
                               "(no override exists).")


@dataclass(frozen=True)
class WheelConfig:
    spec_version: str = SPEC_VERSION
    underlying: str = "SPY"
    vol_index: str = "VIXCLS"
    window_start: str = PREREG_START
    window_end: str = PREREG_END
    data_from: str = "2004-01-01"
    entry_calendar_days_before_expiry: int = 35
    exit_calendar_days_before_expiry: int = 7
    short_delta: float = -0.20
    width_pct_of_spot: float = 2.0
    min_width_usd: float = 1.0
    skew_vol_points_per_pct_otm: float = 0.5
    haircut_per_share_per_leg: float = 0.03
    fee_per_contract: float = 0.65
    profit_take_enabled: bool = True
    profit_take_fraction: float = 0.5
    sleeve_capital_usd: float = 25_000.0
    risk_budget_pct_of_sleeve: float = 10.0
    multiplier: int = 100
    kill_switch_enabled: bool = True
    kill_switch_drawdown_pct: float = 20.0
    kill_switch_pause_months: int = 3
    days_in_year: int = 365

    def replace(self, **changes) -> "WheelConfig":
        return dataclasses.replace(self, **changes)

    def diff_from_default(self) -> dict:
        base = WheelConfig()
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self) if getattr(self, f.name) != getattr(base, f.name)}

    @property
    def risk_budget_usd(self) -> float:
        return self.sleeve_capital_usd * self.risk_budget_pct_of_sleeve / 100.0


@dataclass(frozen=True)
class WVariant:
    name: str
    changes: tuple[tuple[str, object], ...] = ()

    def config(self, base: WheelConfig | None = None) -> WheelConfig:
        return (base or WheelConfig()).replace(**dict(self.changes))


PRIMARY = WVariant("primary")
SENSITIVITIES: tuple[WVariant, ...] = (
    WVariant("skew_0", (("skew_vol_points_per_pct_otm", 0.0),)),
    WVariant("skew_1", (("skew_vol_points_per_pct_otm", 1.0),)),
    WVariant("haircut_0.01", (("haircut_per_share_per_leg", 0.01),)),
    WVariant("haircut_0.06", (("haircut_per_share_per_leg", 0.06),)),
    WVariant("no_profit_take", (("profit_take_enabled", False),)),
    WVariant("delta_0.10", (("short_delta", -0.10),)),
    WVariant("delta_0.30", (("short_delta", -0.30),)),
    WVariant("no_kill_switch", (("kill_switch_enabled", False),)),
    WVariant("qqq_vxn", (("underlying", "QQQ"), ("vol_index", "VXNCLS"))),
)
ALL_VARIANTS: tuple[WVariant, ...] = (PRIMARY, *SENSITIVITIES)


# ===================================================================================== Black-Scholes
def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def iv(vol_index: float, spot: float, strike: float, skew: float) -> float:
    """sigma(K) = V/100 + skew * max(0, ln(S/K)): skew is in volatility units per unit of ln(S/K), so 0.5 vol
    points per 1% out of the money is skew = 0.5 (pre-registration item 6)."""
    return vol_index / 100.0 + skew * max(0.0, math.log(spot / strike))


def _d1_d2(s: float, k: float, t: float, r: float, q: float, sigma: float) -> tuple[float, float]:
    if t <= 0 or sigma <= 0 or s <= 0 or k <= 0:
        raise ValueError(f"cannot price: S={s} K={k} T={t} sigma={sigma}")
    st = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / st
    return d1, d1 - st


def put_price(s: float, k: float, t: float, r: float, q: float, sigma: float) -> float:
    d1, d2 = _d1_d2(s, k, t, r, q, sigma)
    return k * math.exp(-r * t) * norm_cdf(-d2) - s * math.exp(-q * t) * norm_cdf(-d1)


def call_price(s: float, k: float, t: float, r: float, q: float, sigma: float) -> float:
    d1, d2 = _d1_d2(s, k, t, r, q, sigma)
    return s * math.exp(-q * t) * norm_cdf(d1) - k * math.exp(-r * t) * norm_cdf(d2)


def put_delta(s: float, k: float, t: float, r: float, q: float, sigma: float) -> float:
    d1, _ = _d1_d2(s, k, t, r, q, sigma)
    return -math.exp(-q * t) * norm_cdf(-d1)


def leg_price(s: float, k: float, t: float, r: float, q: float, vol_index: float, skew: float) -> float:
    return put_price(s, k, t, r, q, iv(vol_index, s, k, skew))


def spread_mark(s: float, k_short: float, k_long: float, t: float, r: float, q: float, vol_index: float, skew: float) -> float:
    """The model price of the put spread (debit to close, mid): short put minus long put, each at its own sigma(K)."""
    return leg_price(s, k_short, t, r, q, vol_index, skew) - leg_price(s, k_long, t, r, q, vol_index, skew)


def round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def pick_short_strike(s: float, t: float, r: float, q: float, vol_index: float, skew: float, target_delta: float) -> int:
    """The whole-dollar strike K in 1..floor(S) whose model put delta is closest to the target, ties to the lower
    strike (pre-registration item 7)."""
    best_k, best = None, math.inf
    for k in range(1, int(math.floor(s)) + 1):
        dist = abs(put_delta(s, float(k), t, r, q, iv(vol_index, s, float(k), skew)) - target_delta)
        if dist < best:                                   # strict: an exact tie keeps the lower strike
            best_k, best = k, dist
    if best_k is None:
        raise ValueError(f"no whole-dollar strike at or below the spot {s}")
    return best_k


# ============================================================================================ calendar
def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def add_months(d: date, months: int) -> date:
    """The same day of the month `months` later, clipped to that month's last day."""
    m0 = d.month - 1 + months
    y, m = d.year + m0 // 12, m0 % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


@dataclass(frozen=True)
class Expiry:
    third_friday: date
    expiry: date
    moved: bool


def month_range(first: tuple[int, int], last: tuple[int, int]):
    y, m = first
    while (y, m) <= last:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def expiry_range(cfg: "WheelConfig") -> tuple[tuple[int, int], tuple[int, int]]:
    """The months whose expiries are listed: the window's first month to its last (January 2005 to December 2025 for the
    pre-registered window; January 2005 is then dropped as entering before the window)."""
    a, b = date.fromisoformat(cfg.window_start), date.fromisoformat(cfg.window_end)
    return (a.year, a.month), (b.year, b.month)


def monthly_expiries(days: list[date], first: tuple[int, int] = (2005, 1), last: tuple[int, int] = (2025, 12)) -> list[Expiry]:
    """Standard monthly expiries: the third Friday, or the latest trading day before it when it is not a trading day."""
    ds = set(days)
    out = []
    for y, m in month_range(first, last):
        tf = third_friday(y, m)
        if tf > days[-1]:                                    # a missing day must not be mistaken for a holiday
            raise ValueError(f"the third Friday {tf} is after the last trading day with data ({days[-1]})")
        if tf in ds:
            out.append(Expiry(tf, tf, False))
        else:
            i = bisect_left(days, tf)
            if i == 0:
                raise ValueError(f"no trading day before the third Friday {tf}")
            out.append(Expiry(tf, days[i - 1], True))
    return out


def entry_day_for(expiry: date, days: list[date], n: int = 35) -> date:
    """The trading day closest to `n` calendar days before the expiry, ties to the earlier day."""
    target = expiry - timedelta(days=n)
    i = bisect_left(days, target)
    cands = [days[j] for j in (i - 1, i) if 0 <= j < len(days)]
    if not cands:
        raise ValueError("no trading days")
    return min(cands, key=lambda d: (abs((d - target).days), d))


def time_exit_day(expiry: date, days: list[date], n: int = 7) -> date:
    """The latest trading day on or before `n` calendar days before the expiry."""
    i = bisect_right(days, expiry - timedelta(days=n))
    if i == 0:
        raise ValueError("no trading day on or before the time-exit date")
    return days[i - 1]


@dataclass(frozen=True)
class Cycle:
    third_friday: date
    expiry: date
    moved: bool
    entry_day: date
    exit_day: date
    status: str                      # in_window | before_window | after_window


def build_cycles(days: list[date], cfg: WheelConfig, first: tuple[int, int] | None = None,
                 last: tuple[int, int] | None = None) -> list[Cycle]:
    """Every expiry in the list with its entry day, scheduled time-exit day and whether the cycle takes part
    (entry day inside the window and time-exit day on or before the window end; pre-registration item 2)."""
    start, end = date.fromisoformat(cfg.window_start), date.fromisoformat(cfg.window_end)
    rng = expiry_range(cfg)
    out = []
    for e in monthly_expiries(days, first or rng[0], last or rng[1]):
        entry = entry_day_for(e.expiry, days, cfg.entry_calendar_days_before_expiry)
        ex = time_exit_day(e.expiry, days, cfg.exit_calendar_days_before_expiry)
        status = "before_window" if entry < start else "after_window" if ex > end else "in_window"
        out.append(Cycle(e.third_friday, e.expiry, e.moved, entry, ex, status))
    return out


# ============================================================================================ simulation
@dataclass
class Position:
    entry_day: date
    expiry: date
    third_friday: date
    moved: bool
    sched_exit_day: date
    spot: float
    vol_index: float
    rate: float
    q: float
    t_entry: float
    k_short: int
    k_long: int
    width: int
    p_short: float
    p_long: float
    mid_credit: float
    credit: float                    # per share, net of the entry haircut
    contracts: int
    worst_unreal: float
    exit_day: date | None = None
    exit_reason: str = ""
    mark_exit: float = 0.0
    debit_exit: float = 0.0
    net_pnl: float = 0.0
    fees: float = 0.0
    marks: list[tuple[date, float]] = field(default_factory=list)

    @property
    def max_loss_per_contract(self) -> float:
        return (self.width - self.credit) * 100.0


@dataclass
class WheelRun:
    cfg: WheelConfig
    positions: list[Position]
    skips: list[dict]
    cycles: list[Cycle]
    daily: list[tuple[date, float, float]]         # (day, end-of-day equity, daily P&L)
    kill_switch: list[dict]
    window_days: int


def profit_take_hit(mark: float, credit: float, fraction: float) -> bool:
    """The profit-take rule (pre-registration item 9): the spread's model mark is at or below `fraction` of the entry credit."""
    return mark <= fraction * credit


def kill_switch_hit(equity: float, peak: float, drawdown_pct: float) -> bool:
    """Marked equity has fallen `drawdown_pct` below the running peak (item 12): equity <= (1 - pct/100) x peak."""
    return equity <= (1.0 - drawdown_pct / 100.0) * peak


class KillSwitch:
    """Pre-registration item 12. `update(day, equity)` is called once per trading day after the day's marks and exits; while
    `paused` is true no entry may be taken. It activates on the first day equity is 20% below the running peak and pauses
    until `activation + 3 calendar months`; on the first day on or after that resume date the peak is reset to that day's
    equity (otherwise the old peak would re-activate it at once). It is not evaluated during a pause."""

    def __init__(self, enabled: bool, drawdown_pct: float, pause_months: int, start_equity: float):
        self.enabled, self.drawdown_pct, self.pause_months = enabled, drawdown_pct, pause_months
        self.peak = start_equity
        self.paused_until: date | None = None
        self.events: list[dict] = []

    @property
    def paused(self) -> bool:
        return self.paused_until is not None

    def update(self, day: date, equity: float) -> None:
        if not self.enabled:
            return
        if self.paused_until is not None:
            if day >= self.paused_until:
                self.paused_until, self.peak = None, equity
                self.events[-1]["resumed_on"] = day
            return
        self.peak = max(self.peak, equity)
        if kill_switch_hit(equity, self.peak, self.drawdown_pct):
            self.paused_until = add_months(day, self.pause_months)
            self.events.append({"activated": day, "equity": equity, "peak": self.peak, "resume_date": self.paused_until, "resumed_on": None})


def open_position(cfg: WheelConfig, inputs: WheelInputs, day: date, cyc: Cycle) -> tuple[Position | None, str | None]:
    """The entry decision on `day` for `cyc` (pre-registration items 6-10): (the position, None) or (None, skip reason)."""
    s, v, r = inputs.close[day], inputs.vol[day], inputs.rate_pct[day] / 100.0
    try:
        q = inputs.div_yield[day]
    except KeyError:
        raise ValueError(f"{day}: no dividend yield (the 365-day look-back is not inside the data)") from None
    t = (cyc.expiry - day).days / cfg.days_in_year
    skew = cfg.skew_vol_points_per_pct_otm
    k_short = pick_short_strike(s, t, r, q, v, skew, cfg.short_delta)
    width = int(max(cfg.min_width_usd, round_half_up(cfg.width_pct_of_spot / 100.0 * s)))
    k_long = k_short - width
    if k_long < 1:
        return None, "long_strike_below_1"
    p_short = leg_price(s, float(k_short), t, r, q, v, skew)
    p_long = leg_price(s, float(k_long), t, r, q, v, skew)
    mid = p_short - p_long
    credit = mid - 2.0 * cfg.haircut_per_share_per_leg
    if credit <= 0:
        return None, "no_positive_credit"
    if credit >= width:
        return None, "credit_at_or_above_width"
    n = int(math.floor(cfg.risk_budget_usd / ((width - credit) * cfg.multiplier)))
    if n < 1:
        return None, "zero_contracts"
    pos = Position(day, cyc.expiry, cyc.third_friday, cyc.moved, cyc.exit_day, s, v, r, q, t, k_short, k_long, width,
                   p_short, p_long, mid, credit, n, worst_unreal=(credit - mid) * cfg.multiplier * n)
    pos.marks.append((day, mid))
    return pos, None


def simulate(cfg: WheelConfig, inputs: WheelInputs) -> WheelRun:
    """One variant over the window. Deterministic; reads only `inputs`."""
    start, end = date.fromisoformat(cfg.window_start), date.fromisoformat(cfg.window_end)
    assert_not_holdout(start, end)
    if inputs.symbol != cfg.underlying or inputs.vol_name != cfg.vol_index:
        raise ValueError(f"inputs are {inputs.symbol}/{inputs.vol_name}, the configuration wants {cfg.underlying}/{cfg.vol_index}")
    days_all = list(inputs.days)
    assert_not_holdout(days_all[-1])
    window = [d for d in days_all if start <= d <= end]
    if not window:
        raise ValueError("no trading days in the window")
    cycles = build_cycles(days_all, cfg)
    by_entry: dict[date, Cycle] = {}
    for c in cycles:
        if c.status == "in_window":
            if c.entry_day in by_entry:
                raise ValueError(f"two cycles share the entry day {c.entry_day}")
            by_entry[c.entry_day] = c

    cap, mult, fee, h = cfg.sleeve_capital_usd, cfg.multiplier, cfg.fee_per_contract, cfg.haircut_per_share_per_leg
    skew = cfg.skew_vol_points_per_pct_otm
    realized, prev_equity = 0.0, cap
    ks = KillSwitch(cfg.kill_switch_enabled, cfg.kill_switch_drawdown_pct, cfg.kill_switch_pause_months, cap)
    pos: Position | None = None
    positions: list[Position] = []
    skips: list[dict] = []
    daily: list[tuple[date, float, float]] = []

    for d in window:
        unreal = 0.0
        # (1) mark the open position; (2) exit it if a rule fires
        if pos is not None:
            t = (pos.expiry - d).days / cfg.days_in_year
            mark = spread_mark(inputs.close[d], float(pos.k_short), float(pos.k_long), t, inputs.rate_pct[d] / 100.0,
                               inputs.div_yield[d], inputs.vol[d], skew)
            unreal = (pos.credit - mark) * mult * pos.contracts
            pos.worst_unreal = min(pos.worst_unreal, unreal)
            pos.marks.append((d, mark))
            if d > pos.entry_day:
                take = cfg.profit_take_enabled and profit_take_hit(mark, pos.credit, cfg.profit_take_fraction)
                if take or d >= pos.sched_exit_day:
                    debit = mark + 2.0 * h
                    n = pos.contracts
                    realized += (pos.credit - debit) * mult * n - 2.0 * fee * n
                    pos.exit_day, pos.exit_reason = d, "profit_take" if take else "time_exit"
                    pos.mark_exit, pos.debit_exit, pos.fees = mark, debit, 4.0 * fee * n
                    pos.net_pnl = (pos.credit - debit) * mult * n - 4.0 * fee * n
                    positions.append(pos)
                    pos, unreal = None, 0.0
        # (3) equity, running peak and kill switch (steps 1-2 only; today's entry costs come after)
        ks.update(d, cap + realized + unreal)
        # (4) the entry decision
        cyc = by_entry.get(d)
        if cyc is not None:
            reason = None
            if pos is not None:
                reason = "position_still_open"
            elif ks.paused:
                reason = "kill_switch"
            else:
                new, reason = open_position(cfg, inputs, d, cyc)
                if new is not None:
                    pos = new
                    realized -= 2.0 * fee * new.contracts
                    unreal = (new.credit - new.mid_credit) * mult * new.contracts
            if reason:
                skips.append({"cycle_expiry": cyc.expiry, "entry_day": d, "reason": reason})
        end_equity = cap + realized + unreal
        daily.append((d, end_equity, end_equity - prev_equity))
        prev_equity = end_equity

    if pos is not None:
        raise RuntimeError(f"a position entered {pos.entry_day} is still open at the window end: the cycle filter is broken")
    ks_events = ks.events
    for ev in ks_events:
        end_ev = ev["resumed_on"] or ev["resume_date"]
        ev["cycles_skipped"] = [s["cycle_expiry"] for s in skips if s["reason"] == "kill_switch" and ev["activated"] <= s["entry_day"] < end_ev]
    return WheelRun(cfg, positions, skips, cycles, daily, ks_events, len(window))


# ================================================================================================ metrics
def _mean(x: list[float]) -> float:
    return sum(x) / len(x)


def _std(x: list[float]) -> float:
    m = _mean(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))


def pearson(x: list[float], y: list[float]) -> float | None:
    if len(x) != len(y) or len(x) < 3:
        return None
    mx, my = _mean(x), _mean(y)
    sxx, syy = sum((a - mx) ** 2 for a in x), sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sxx * syy)


def compute_metrics(run: WheelRun) -> dict:
    cap = run.cfg.sleeve_capital_usd
    days = [d for d, _, _ in run.daily]
    pnl = [p for _, _, p in run.daily]
    rets = [p / cap for p in pnl]
    n = len(rets)
    sd = _std(rets) if n > 1 else 0.0
    sharpe = _mean(rets) / sd * math.sqrt(TRADING_DAYS_PER_YEAR) if sd > 0 else None
    tstat = _mean(rets) / (sd / math.sqrt(n)) if sd > 0 else None
    final = run.daily[-1][1]
    span = (days[-1] - days[0]).days
    cagr = (final / cap) ** (365.25 / span) - 1.0 if span > 0 and final > 0 else (-1.0 if final <= 0 else None)
    # drawdown against the never-reset running peak (starting at the initial sleeve)
    peak, peak_day, mdd, mdd_peak_day, mdd_day = cap, days[0], 0.0, days[0], days[0]
    for d, e, _ in run.daily:
        if e > peak:
            peak, peak_day = e, d
        if peak - e > mdd:
            mdd, mdd_peak_day, mdd_day = peak - e, peak_day, d
    mdd_peak_value = cap + 0.0
    for d, e, _ in run.daily:                                   # the peak value that mdd was measured from
        if d == mdd_peak_day:
            mdd_peak_value = e
            break
    years: dict[int, float] = {}
    for d, p in zip(days, pnl):
        years[d.year] = years.get(d.year, 0.0) + p
    ypos = sum(1 for v in years.values() if v > 0)
    worst_y = min(years, key=lambda y: years[y])
    best_y = max(years, key=lambda y: years[y])
    ps = run.positions
    wins = sum(1 for p in ps if p.net_pnl > 0)
    reasons: dict[str, int] = {}
    for p in ps:
        reasons[p.exit_reason] = reasons.get(p.exit_reason, 0) + 1
    skip_counts: dict[str, int] = {}
    for s in run.skips:
        skip_counts[s["reason"]] = skip_counts.get(s["reason"], 0) + 1
    return {
        "name": None, "days": n, "first_day": days[0].isoformat(), "last_day": days[-1].isoformat(),
        "net_sharpe": sharpe, "t_stat": tstat, "cagr": cagr, "final_equity": final, "total_net_pnl": final - cap,
        "max_drawdown_usd": mdd, "max_drawdown_pct_of_sleeve": mdd / cap * 100.0,
        "max_drawdown_pct_of_peak": (mdd / mdd_peak_value * 100.0) if mdd_peak_value > 0 else None,
        "max_drawdown_peak_day": mdd_peak_day.isoformat(), "max_drawdown_trough_day": mdd_day.isoformat(),
        "years": {y: years[y] for y in sorted(years)}, "n_years": len(years), "positive_years": ypos,
        "positive_year_share": ypos / len(years),
        "worst_year": worst_y, "worst_year_pnl": years[worst_y], "worst_year_pct_of_sleeve": years[worst_y] / cap * 100.0,
        "best_year": best_y, "best_year_pnl": years[best_y], "best_year_pct_of_sleeve": years[best_y] / cap * 100.0,
        "positions": len(ps), "win_rate": (wins / len(ps)) if ps else None,
        "avg_credit_per_contract": (_mean([p.credit * 100.0 for p in ps]) if ps else None),
        "avg_max_loss_per_contract": (_mean([p.max_loss_per_contract for p in ps]) if ps else None),
        "avg_contracts": (_mean([float(p.contracts) for p in ps]) if ps else None),
        "avg_hold_days": (_mean([float((p.exit_day - p.entry_day).days) for p in ps]) if ps else None),
        "avg_net_pnl": (_mean([p.net_pnl for p in ps]) if ps else None),
        "worst_position_pnl": min((p.net_pnl for p in ps), default=None),
        "best_position_pnl": max((p.net_pnl for p in ps), default=None),
        "total_fees": sum(p.fees for p in ps),
        "exit_reasons": reasons, "skips": skip_counts, "cycles_in_window": sum(1 for c in run.cycles if c.status == "in_window"),
        "cycles_not_in_window": [{"expiry": c.expiry.isoformat(), "status": c.status} for c in run.cycles if c.status != "in_window"],
        "kill_switch_activations": [{"activated": e["activated"].isoformat(), "resume_date": e["resume_date"].isoformat(),
                                     "resumed_on": e["resumed_on"].isoformat() if e["resumed_on"] else None,
                                     "pause_days": ((e["resumed_on"] or e["resume_date"]) - e["activated"]).days,
                                     "equity": e["equity"], "peak": e["peak"],
                                     "cycles_skipped": [c.isoformat() for c in e["cycles_skipped"]]} for e in run.kill_switch],
    }


def screening_gate(m: dict) -> dict:
    """The mechanical screening gate (section 2): all four checks must hold; compared unrounded, in dollars where the
    instruction states a share of the sleeve."""
    cap = WheelConfig().sleeve_capital_usd
    sh = m["net_sharpe"]
    checks = {
        "net_sharpe": {"value": sh, "threshold": GATE_MIN_NET_SHARPE, "pass": sh is not None and sh >= GATE_MIN_NET_SHARPE},
        "max_drawdown": {"value_usd": m["max_drawdown_usd"], "value_pct_of_sleeve": m["max_drawdown_pct_of_sleeve"],
                         "threshold_pct_of_sleeve": GATE_MAX_DRAWDOWN_PCT,
                         "pass": m["max_drawdown_usd"] <= cap * GATE_MAX_DRAWDOWN_PCT / 100.0},
        "worst_year": {"year": m["worst_year"], "value_usd": m["worst_year_pnl"], "value_pct_of_sleeve": m["worst_year_pct_of_sleeve"],
                       "threshold_pct_of_sleeve": GATE_MIN_WORST_YEAR_PCT,
                       "pass": m["worst_year_pnl"] >= cap * GATE_MIN_WORST_YEAR_PCT / 100.0},
        "positive_years": {"positive": m["positive_years"], "years": m["n_years"],
                           "threshold": f"{GATE_POSITIVE_YEARS_NUM}/{GATE_POSITIVE_YEARS_DEN}",
                           "pass": GATE_POSITIVE_YEARS_DEN * m["positive_years"] >= GATE_POSITIVE_YEARS_NUM * m["n_years"]},
    }
    ok = all(c["pass"] for c in checks.values())
    return {"verdict": WORTH if ok else NOT_WORTH, "checks": checks}


# ===================================================================================== PUT calibration
def calibrate_put(inputs: WheelInputs, put: dict[date, float], cfg: WheelConfig,
                  expiries: list[Expiry]) -> dict:
    """Pre-registration item 18: sell one at-the-money put (K = the close rounded to the whole dollar) at the close of each
    standard expiry s, expiring at the next one e, cash-secured by K in T-bills, no costs. Period return
    R = r*T + (P - max(K - S_e, 0)) / K against PUT(e)/PUT(s) - 1 over the same periods."""
    end = date.fromisoformat(cfg.window_end)
    assert_not_holdout(end)
    skew = cfg.skew_vol_points_per_pct_otm
    rows, dropped = [], []
    for a, b in zip(expiries, expiries[1:]):
        s, e = a.expiry, b.expiry
        if s < CALIBRATION_FIRST_ROW or e > end:
            continue
        if s not in put or e not in put:
            dropped.append({"start": s.isoformat(), "end": e.isoformat(), "reason": "no PUT value on the roll date or the next one"})
            continue
        spot, v, r, q = inputs.close[s], inputs.vol[s], inputs.rate_pct[s] / 100.0, inputs.div_yield[s]
        t = (e - s).days / cfg.days_in_year
        k = round_half_up(spot)
        p = leg_price(spot, float(k), t, r, q, v, skew)
        payoff = max(k - inputs.close[e], 0.0)
        r_model = r * t + (p - payoff) / k
        r_put = put[e] / put[s] - 1.0
        rows.append({"start": s, "end": e, "year": e.year, "strike": k, "spot": spot, "vol": v, "premium": p, "payoff": payoff,
                     "model_return": r_model, "put_return": r_put, "put_start": put[s], "put_end": put[e]})
    by_year: dict[int, dict] = {}
    for rw in rows:
        y = by_year.setdefault(rw["year"], {"year": rw["year"], "periods": 0, "model": 1.0, "put": 1.0})
        y["periods"] += 1
        y["model"] *= 1.0 + rw["model_return"]
        y["put"] *= 1.0 + rw["put_return"]
    years = [{**y, "model": y["model"] - 1.0, "put": y["put"] - 1.0, "diff": y["model"] - y["put"]} for y in sorted(by_year.values(), key=lambda z: z["year"])]
    mt, pt = 1.0, 1.0
    for rw in rows:
        mt *= 1.0 + rw["model_return"]
        pt *= 1.0 + rw["put_return"]
    yd = [y["diff"] for y in years]
    return {"periods": rows, "dropped": dropped, "years": years, "n_periods": len(rows),
            "corr_yearly": pearson([y["model"] for y in years], [y["put"] for y in years]),
            "corr_period": pearson([r["model_return"] for r in rows], [r["put_return"] for r in rows]),
            "mean_annual_diff": _mean(yd) if yd else None, "mean_abs_annual_diff": _mean([abs(v) for v in yd]) if yd else None,
            "mean_period_diff": _mean([r["model_return"] - r["put_return"] for r in rows]) if rows else None,
            "model_total": mt - 1.0, "put_total": pt - 1.0,
            "first_period": rows[0]["start"].isoformat() if rows else None, "last_period_end": rows[-1]["end"].isoformat() if rows else None}


# ============================================================================================ checks
def calendar_check(days: list[date], cfg: WheelConfig, alpaca_days: list[date] | None = None) -> dict:
    """Pre-registration item 17: the expiry list against third Fridays, with every holiday move listed."""
    exp = monthly_expiries(days, *expiry_range(cfg))
    moved = [{"third_friday": e.third_friday.isoformat(), "expiry": e.expiry.isoformat()} for e in exp if e.moved]
    good_fridays = [(2008, 3), (2014, 4), (2019, 4), (2022, 4), (2025, 4)]
    expected = {third_friday(y, m).isoformat() for y, m in good_fridays}
    out = {"expiries": len(exp), "moved": moved, "moved_equals_good_friday_set": {m["third_friday"] for m in moved} == expected,
           "expected_good_friday_third_fridays": sorted(expected),
           "not_third_friday": [e.expiry.isoformat() for e in exp if not e.moved and e.expiry.weekday() != 4]}
    if alpaca_days is not None:
        lo, hi = date(2016, 1, 1), date.fromisoformat(cfg.window_end)
        a, b = {d for d in days if lo <= d <= hi}, {d for d in alpaca_days if lo <= d <= hi}
        out["alpaca_day_check"] = {"yfinance_days": len(a), "alpaca_days": len(b), "only_yfinance": sorted(x.isoformat() for x in a - b),
                                   "only_alpaca": sorted(x.isoformat() for x in b - a)}
    return out


def crisis_closeup(run: WheelRun) -> list[dict]:
    """Pre-registration item 19: every position open during each fixed window (entry <= window end and exit >= window
    start), and the cycles skipped inside it."""
    out = []
    for name, lo, hi in CRISIS_WINDOWS:
        pos = [p for p in run.positions if p.entry_day <= hi and p.exit_day >= lo]
        skip = [s for s in run.skips if lo <= s["entry_day"] <= hi]
        out.append({"window": name, "start": lo.isoformat(), "end": hi.isoformat(), "positions": pos, "skips": skip})
    return out


def analyse(inputs_by_key: dict[tuple[str, str], WheelInputs], put: dict[date, float] | None, variants=ALL_VARIANTS,
            base: WheelConfig | None = None) -> dict:
    """Run every variant and the PUT calibration. `inputs_by_key` maps (underlying, volatility index) to the aligned inputs."""
    base = base or WheelConfig()
    runs, metrics = {}, {}
    for v in variants:
        cfg = v.config(base)
        run = simulate(cfg, inputs_by_key[(cfg.underlying, cfg.vol_index)])
        m = compute_metrics(run)
        m["name"] = v.name
        runs[v.name], metrics[v.name] = run, m
    primary_inputs = inputs_by_key[(base.underlying, base.vol_index)]
    cal = None
    if put is not None:
        cal = calibrate_put(primary_inputs, put, base, monthly_expiries(list(primary_inputs.days), *expiry_range(base)))
    prim = metrics.get("primary")
    return {"runs": runs, "metrics": metrics, "gate": screening_gate(prim) if prim else None, "calibration": cal,
            "calendar": calendar_check(list(primary_inputs.days), base)}
