"""Universe filter and Stocks-in-Play ranking. Pure Python: sequences in, numbers out.

Definitions (Phase 2 pre-registration, sections 1 and 5; identical to the Phase 1 report so the
IEX-vs-SIP numbers and the backtest describe the same universe):

  * ATR = the SIMPLE MEAN of the prior `lookback` true ranges (not Wilder-smoothed), where
    TR = max(high - low, |high - prev_close|, |low - prev_close|). All `lookback` prior daily
    bars must be present; only the previous close of the OLDEST of them may be missing, in which
    case that bar's TR is high - low.
  * 14-day average volume = the plain mean of the same `lookback` prior daily volumes.
  * An averaged window never includes the day being evaluated: only that day's OPEN is used.
  * Relative Volume = the day's 09:30-09:35 volume / the mean of the same window over the
    `lookback` prior trading days; a missing bar is ZERO volume; a non-positive average -> None.
  * Stocks in Play = the `top_n` names with RVOL >= `rvol_min`, ties broken by symbol.

A missing value is `None` or NaN; both are handled identically.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NamedTuple

from .config import OrbConfig


def is_missing(x) -> bool:
    return x is None or x != x          # NaN != NaN


def true_range(high: float, low: float, prev_close: float | None) -> float | None:
    """TR for one daily bar; a missing previous close degrades to high - low."""
    if is_missing(high) or is_missing(low):
        return None
    tr = high - low
    if not is_missing(prev_close):
        tr = max(tr, abs(high - prev_close), abs(low - prev_close))
    return tr


def atr_simple(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
               prior_close: float | None, cfg: OrbConfig) -> float | None:
    """Simple-mean ATR over exactly `cfg.lookback_days` prior bars (oldest first).

    `prior_close` is the close of the trading day BEFORE the oldest bar (may be missing)."""
    n = cfg.lookback_days
    if len(highs) != n or len(lows) != n or len(closes) != n:
        return None
    trs = []
    prev = prior_close
    for h, lo, c in zip(highs, lows, closes):
        tr = true_range(h, lo, prev)
        if tr is None:
            return None
        trs.append(tr)
        prev = c
    return sum(trs) / n


def average_volume(volumes: Sequence[float], cfg: OrbConfig) -> float | None:
    """Mean of exactly `cfg.lookback_days` prior daily volumes; any missing day -> None."""
    if len(volumes) != cfg.lookback_days or any(is_missing(v) for v in volumes):
        return None
    return sum(volumes) / cfg.lookback_days


def open_ok(open_price: float | None, cfg: OrbConfig) -> bool:
    return (not is_missing(open_price)) and open_price > cfg.min_price


def volume_ok(avg_volume: float | None, cfg: OrbConfig) -> bool:
    return (not is_missing(avg_volume)) and avg_volume >= cfg.min_avg_volume


def atr_ok(atr: float | None, cfg: OrbConfig) -> bool:
    return (not is_missing(atr)) and atr > cfg.min_atr


class UniverseCheck(NamedTuple):
    ok: bool
    atr: float | None
    avg_volume: float | None


def check_universe(open_price: float | None, highs: Sequence[float], lows: Sequence[float],
                   closes: Sequence[float], volumes: Sequence[float], prior_close: float | None,
                   cfg: OrbConfig) -> UniverseCheck:
    """Does the symbol pass the universe filter on a day, from the prior-`lookback` history and the
    day's own open? (ETF and Core-roster exclusion are applied by the caller, which owns those lists.)"""
    atr = atr_simple(highs, lows, closes, prior_close, cfg)
    avg_vol = average_volume(volumes, cfg)
    ok = open_ok(open_price, cfg) and volume_ok(avg_vol, cfg) and atr_ok(atr, cfg)
    return UniverseCheck(ok, atr, avg_vol)


def relative_volume(window_volume: float | None, prior_window_volumes: Sequence[float | None],
                    cfg: OrbConfig) -> float | None:
    """RVOL = today's opening-window volume / mean of the prior `lookback` days' (missing = 0)."""
    if len(prior_window_volumes) != cfg.lookback_days:
        return None
    prior = [0.0 if is_missing(v) else float(v) for v in prior_window_volumes]
    avg = sum(prior) / cfg.lookback_days
    if not avg > 0:
        return None
    today = 0.0 if is_missing(window_volume) else float(window_volume)
    return today / avg


def rank_stocks_in_play(rvols: Mapping[str, float | None], cfg: OrbConfig) -> list[str]:
    """Top `cfg.top_n` symbols with RVOL >= `cfg.rvol_min`; RVOL descending, ties by symbol."""
    live = [(s, r) for s, r in rvols.items() if not is_missing(r) and r >= cfg.rvol_min]
    live.sort(key=lambda kv: (-kv[1], kv[0]))
    return [s for s, _ in live[:cfg.top_n]]


def core_roster() -> frozenset[str]:
    """The Core roster tickers ORB must never trade (every pool member of every Core role)."""
    from shared.quadrants import CORE_ROSTER
    return frozenset(str(s).upper() for s in CORE_ROSTER)
