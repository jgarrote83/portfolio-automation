"""Same-minute resolution of ORB v1's ambiguous entry-minute stops (a DIAGNOSTIC, not a verdict).

The Phase 2 engine reads one-minute bars. When a bar opens short of an entry trigger, reaches the
trigger and ALSO reaches the stop, the bar cannot say which came first, and the pre-registration
(section 5) assumed the stop came AFTER the entry ("stop_same_bar": a -1R loss). If the dip to the
stop level actually came BEFORE the entry order triggered, no position existed and there was no stop-out.

This module settles it for the trades where it matters:

  1. `ignore_stop_rule` is the OPTIMISTIC bound: the entry bar's stop is never checked (from the next bar
     onward it is, exactly as in the engine). Everything else is the engine, unchanged.
  2. For the ambiguous trades whose result then changes (the "changed set"), `resolve_entry_minute`
     replays the SIP trade prints of that one symbol and that one minute in exchange-timestamp order.
  3. `ResolvedRule` feeds those per-trade answers back into the engine for the tick-resolved result.

Nothing here touches `docs/specs/ORB_Phase2_Preregistration.md`, `src/orb/` or the Phase 2 results: the
pre-registered rule is the engine's default and `simulate_day(..., entry_bar_stop=None)` is byte for byte
the Phase 2 behaviour (proved on the real trades in the run header: every Phase 2 primary trade is
reproduced). Pure logic is separate from I/O so it can be tested on constructed print sequences.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from orb import signals
from orb.config import OrbConfig
from orb.signals import LONG

from .engine import DayInput, DayResult, EntryBarStopRule, MinuteBar, Trade, load_minute_bars, simulate_day
from .sessions import close_minute

# ======================================================================================== print filter
# A print "sets the regular-session price" only if none of its sale-condition codes is in this table. The
# codes are Alpaca's own (`/v2/stocks/meta/conditions/trade`); each reason says why the print does not
# represent an in-sequence execution at a regular-session price. The set was fixed from the code
# descriptions BEFORE any result was computed and then checked against Alpaca's own one-minute bars
# (`bar_matches`): dropping just {I, 4} already reproduces open/high/low/close exactly, and the rest
# of the table only ever removes prints that cannot be at a price a stop order would see.
EXCLUDED_CODES: dict[str, str] = {
    "I": "odd lot (< 100 shares): does not update the consolidated high/low/last (and Alpaca's bars drop it)",
    "4": "derivatively priced: the price is derived by formula, not set in the market (Alpaca's bars drop it)",
    "Z": "sold out of sequence: reported late, not in time order",
    "U": "extended-hours sold out of sequence: late-reported and not a regular-session print",
    "L": "sold last (late reporting): reported after the fact, not in time order",
    "T": "extended-hours trade: not a regular-session print",
    "B": "average price trade: the price is an average, not an execution price",
    "W": "average price trade (UTP code; absent from Alpaca's table): the price is an average",
    "7": "qualified contingent trade: priced as part of a package",
    "V": "contingent trade: priced as part of a package",
    "C": "cash trade (same-day clearing): special settlement terms, price is not comparable",
    "N": "next-day trade: special settlement terms, price is not comparable",
    "R": "seller option: special settlement terms, price is not comparable",
    "P": "prior reference price: the price refers to an earlier time",
    "H": "price variation trade: does not follow the normal price-setting rules",
}
MINIMAL_EXCLUDED: frozenset[str] = frozenset({"I", "4"})      # the smallest set that reproduces Alpaca's bars
PRIMARY_EXCLUDED: frozenset[str] = frozenset(EXCLUDED_CODES)

# outcomes of `resolve_entry_minute`
DIP_FIRST = "dip_first"                     # the stop level traded only BEFORE the trigger print: not a stop-out
REAL_STOP = "real_stop"                     # a stop print AFTER the trigger print: a real -1R stop
TIE_REAL_STOP = "tie_real_stop"             # stop and trigger prints share a timestamp: order unknown, scored real
NO_TRIGGER_PRINT = "no_trigger_print"       # no counted print reaches the trigger: the ticks do not confirm the entry
STOP_NEVER_TOUCHED = "stop_never_touched"   # no counted print reaches the stop: only an excluded print did
UNRESOLVED = "unresolved"                   # no prints came back for the minute
STOP_OUTCOMES = frozenset({REAL_STOP, TIE_REAL_STOP, NO_TRIGGER_PRINT, UNRESOLVED})   # scored as the primary scored them


def counted(conditions: Iterable[str], excluded: Iterable[str] = PRIMARY_EXCLUDED) -> bool:
    """True when none of the print's condition codes is excluded."""
    ex = set(excluded)
    return not any(c in ex for c in conditions)


def counted_prints(df, excluded: Iterable[str] = PRIMARY_EXCLUDED) -> list[tuple[int, float, int]]:
    """`(timestamp_ns, price, seq)` of the prints in a `get_trades` frame that set the regular-session price."""
    if df.empty:
        return []
    ns = [t.value for t in df["ts"]]
    return [(n, float(p), int(sq)) for n, p, sq, c in zip(ns, df["price"], df["seq"], df["conditions"])
            if counted(c, excluded)]


# =============================================================================== pure tick resolution
@dataclass(frozen=True)
class Resolution:
    outcome: str
    n_prints: int                   # counted prints in the minute
    trigger_ts: int | None          # ns timestamp of the first counted print that reaches the trigger
    trigger_price: float | None
    stop_before_ts: int | None      # first counted print at/through the stop BEFORE the trigger print
    stop_before_price: float | None
    stop_after_ts: int | None       # first counted print at/through the stop AFTER (or at the time of) the trigger print
    stop_after_price: float | None
    tie_would_be_dip: bool          # a same-timestamp stop print sorts BEFORE the trigger print in Alpaca's order


def _reaches_trigger(side: str, price: float, trigger: float) -> bool:
    return price >= trigger if side == LONG else price <= trigger


def _reaches_stop(side: str, price: float, stop: float) -> bool:
    return price <= stop if side == LONG else price >= stop


def resolve_entry_minute(side: str, trigger: float, stop: float,
                         prints: Iterable[tuple[int, float, int]]) -> Resolution:
    """Replay ONE minute of COUNTED prints (`(timestamp_ns, price, seq)`, any order) in exchange-timestamp
    order for a long (a short is the mirror).

    The entry fills on the first print at or beyond the trigger. A print at or through the stop AFTER that
    moment is a real stop. A stop print that shares the trigger print's exact timestamp cannot be ordered
    against it, so it is scored as a real stop (conservative) and flagged. A stop print only BEFORE the
    trigger print happened while the order was still waiting: no position, no stop-out."""
    ps = sorted(prints, key=lambda p: (p[0], p[2]))
    n = len(ps)
    k = next((i for i, p in enumerate(ps) if _reaches_trigger(side, p[1], trigger)), None)
    if k is None:
        return Resolution(NO_TRIGGER_PRINT if n else UNRESOLVED, n, None, None, None, None, None, None, False)
    t_k, p_k = ps[k][0], ps[k][1]
    after = next((p for p in ps if p[0] > t_k and _reaches_stop(side, p[1], stop)), None)
    same = [(i, p) for i, p in enumerate(ps) if p[0] == t_k and i != k and _reaches_stop(side, p[1], stop)]
    before = next((p for p in ps if p[0] < t_k and _reaches_stop(side, p[1], stop)), None)
    if after is not None:
        return Resolution(REAL_STOP, n, t_k, p_k, before and before[0], before and before[1], after[0], after[1], False)
    if same:
        i0, p0 = same[0]
        return Resolution(TIE_REAL_STOP, n, t_k, p_k, before and before[0], before and before[1], p0[0], p0[1],
                          any(i < k for i, _p in same))
    if before is not None:
        return Resolution(DIP_FIRST, n, t_k, p_k, before[0], before[1], None, None, False)
    return Resolution(STOP_NEVER_TOUCHED, n, t_k, p_k, None, None, None, None, False)


def bar_from_prints(prints: Iterable[tuple[int, float, int]]) -> tuple[float, float, float, float] | None:
    """(open, high, low, close) of counted prints in timestamp order, or None."""
    ps = sorted(prints, key=lambda p: (p[0], p[2]))
    if not ps:
        return None
    px = [p[1] for p in ps]
    return px[0], max(px), min(px), px[-1]


def bar_matches(derived: tuple[float, float, float, float] | None, bar: MinuteBar, tol: float = 1e-6) -> dict[str, bool]:
    """Per field, does the tick-derived bar equal Alpaca's own one-minute bar (open, high, low, close)?"""
    if derived is None:
        return {"open": False, "high": False, "low": False, "close": False}
    return {"open": abs(derived[0] - bar[0]) < tol, "high": abs(derived[1] - bar[1]) < tol,
            "low": abs(derived[2] - bar[2]) < tol, "close": abs(derived[3] - bar[3]) < tol}


def is_ambiguous_entry(side: str, trigger: float, bar_open: float) -> bool:
    """The entry bar OPENED short of the trigger, so the order was still waiting when the bar began and the
    bar's own low could have come before the fill. A bar that opens at/through the trigger fills on its
    first print, so a later low is unambiguously after the entry."""
    return bar_open < trigger if side == LONG else bar_open > trigger


# ====================================================================================== entry-bar rules
def assume_stop_rule(_day: date, _symbol: str, plan: signals.EntryPlan, bar: MinuteBar) -> bool:
    """The pre-registered rule (identical to the engine default): an entry bar that reaches the stop is stopped."""
    return signals.stop_hit_in_entry_bar(plan.side, bar[1], bar[2], plan.stop)


def ignore_stop_rule(_day: date, _symbol: str, _plan: signals.EntryPlan, _bar: MinuteBar) -> bool:
    """The OPTIMISTIC bound: the entry bar's stop is never checked (the next bar onward it is)."""
    return False


class Recorder:
    """Wraps a rule and remembers the (plan, entry bar) of every entry it is asked about."""

    def __init__(self, base: EntryBarStopRule) -> None:
        self.base = base
        self.calls: dict[tuple[date, str], tuple[signals.EntryPlan, MinuteBar]] = {}

    def __call__(self, day: date, symbol: str, plan: signals.EntryPlan, bar: MinuteBar) -> bool:
        self.calls[(day, symbol)] = (plan, bar)
        return self.base(day, symbol, plan, bar)


class ResolvedRule:
    """Tick-resolved decisions per trade. `stops[(day, symbol)]` is True for a real stop and False for a
    dip that came before the entry; any entry not listed keeps the pre-registered assumption."""

    def __init__(self, stops: Mapping[tuple[date, str], bool]) -> None:
        self.stops = dict(stops)

    def __call__(self, day: date, symbol: str, plan: signals.EntryPlan, bar: MinuteBar) -> bool:
        decided = self.stops.get((day, symbol))
        return assume_stop_rule(day, symbol, plan, bar) if decided is None else decided


# ============================================================================== window simulation
def simulate_window(layer, window_days: Sequence[date], picks_by_day: Mapping[date, Sequence],
                    cfg: OrbConfig, rules: Mapping[str, EntryBarStopRule | None],
                    progress: Callable[[str], None] | None = None) -> dict[str, list[DayResult]]:
    """The engine's loop (`run_backtest`) for ONE config with several entry-bar rules over the same loaded
    bars: each day's minute bars are read once and every rule simulates against them."""
    out: dict[str, list[DayResult]] = {name: [] for name in rules}
    for i, d in enumerate(window_days):
        picks = tuple(picks_by_day.get(d, ()))
        need = {p.symbol for p in picks if p.side is not None}
        bars = load_minute_bars(layer, d, need)
        sub = {p.symbol: bars.get(p.symbol, {}) for p in picks if p.side is not None}
        di = DayInput(d, close_minute(d), picks, sub)
        for name, rule in rules.items():
            out[name].append(simulate_day(di, cfg, entry_bar_stop=rule))
        if progress and (i + 1) % 100 == 0:
            progress(f"simulated {i + 1}/{len(window_days)} days")
    return out


def trades_by_key(results: Iterable[DayResult]) -> dict[tuple[date, str], Trade]:
    """One trade per (day, symbol): the engine takes one entry per symbol per day."""
    return {(t.day, t.symbol): t for r in results for t in r.trades}


# ================================================================================ the changed set
@dataclass(frozen=True)
class Candidate:
    """One Phase 2 primary trade stopped in its entry minute."""
    day: date
    symbol: str
    side: str
    trigger: float
    stop: float
    entry_minute: int
    bar: MinuteBar                  # the entry minute's bar
    ambiguous: bool
    r_gross_optimistic: float | None   # None: the optimistic run has no such trade
    changed: bool                   # ambiguous AND the optimistic R differs from -1.00 by more than the tolerance


R_TOLERANCE = 0.01


def build_candidates(primary: Mapping[tuple[date, str], Trade], optimistic: Mapping[tuple[date, str], Trade],
                     calls: Mapping[tuple[date, str], tuple[signals.EntryPlan, MinuteBar]]) -> list[Candidate]:
    """Every primary `stop_same_bar` trade, with whether it is ambiguous and whether the optimistic rule
    changes its result. The comparison is on GROSS R (cost-free): the primary's is exactly -1.00."""
    out = []
    for key in sorted(primary, key=lambda k: (k[0], k[1])):
        tr = primary[key]
        if tr.exit_reason != "stop_same_bar":
            continue
        plan, bar = calls[key]
        amb = is_ambiguous_entry(plan.side, plan.trigger, bar[0])
        opt = optimistic.get(key)
        r_opt = None if opt is None else opt.r_gross
        changed = amb and (r_opt is None or abs(r_opt - tr.r_gross) > R_TOLERANCE)
        out.append(Candidate(key[0], key[1], plan.side, plan.trigger, plan.stop, tr.entry_minute, bar, amb, r_opt, changed))
    return out


def tick_decisions(resolutions: Mapping[tuple[date, str], Resolution]) -> dict[tuple[date, str], bool]:
    """Resolution -> the engine decision: True (a real stop, scored as the primary scored it) for every
    outcome in `STOP_OUTCOMES`; False for a dip before the entry or a stop level no counted print reached."""
    return {key: res.outcome in STOP_OUTCOMES for key, res in resolutions.items()}


def outcome_counts(resolutions: Mapping[tuple[date, str], Resolution]) -> dict[str, int]:
    return dict(Counter(r.outcome for r in resolutions.values()))
