"""Pure intraday/daily indicator math for the Flex engine.

All functions are deterministic and side-effect-free so they can be unit-tested
without a broker or network. Bars are plain dicts in Alpaca shape:
``{"t": iso, "o": float, "h": float, "l": float, "c": float, "v": float}``.
Every function returns ``None`` on insufficient/garbage data rather than raising —
the engine treats ``None`` as "unknown" and never forces a trade off missing data.
"""
from __future__ import annotations

from typing import Sequence

Bar = dict


def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def session_vwap(bars: Sequence[Bar]) -> float | None:
    """Volume-weighted average of typical price ((h+l+c)/3) over the bars."""
    num = 0.0
    den = 0.0
    for b in bars:
        h, lo, c, v = _f(b.get("h")), _f(b.get("l")), _f(b.get("c")), _f(b.get("v"))
        if None in (h, lo, c, v) or v <= 0:
            continue
        typical = (h + lo + c) / 3.0
        num += typical * v
        den += v
    if den <= 0:
        return None
    return num / den


def _cumulative_vwap(bars: Sequence[Bar]) -> list[float]:
    """Running session VWAP after each bar (skips zero-volume bars)."""
    out: list[float] = []
    num = 0.0
    den = 0.0
    for b in bars:
        h, lo, c, v = _f(b.get("h")), _f(b.get("l")), _f(b.get("c")), _f(b.get("v"))
        if None not in (h, lo, c, v) and v > 0:
            num += ((h + lo + c) / 3.0) * v
            den += v
        if den > 0:
            out.append(num / den)
    return out


def vwap_slope(bars: Sequence[Bar], lookback_bars: int = 5) -> float | None:
    """Change in the cumulative session VWAP over the last ``lookback_bars``.

    Positive ⟹ VWAP is rising (institutions accumulating); ≤ 0 ⟹ flat/rolling
    over. Returns ``None`` if there aren't enough priced bars.
    """
    series = _cumulative_vwap(bars)
    if len(series) <= lookback_bars:
        return None
    return series[-1] - series[-1 - lookback_bars]


def _true_ranges(daily_bars: Sequence[Bar]) -> list[float]:
    trs: list[float] = []
    prev_close: float | None = None
    for b in daily_bars:
        h, lo, c = _f(b.get("h")), _f(b.get("l")), _f(b.get("c"))
        if None in (h, lo, c):
            prev_close = c if c is not None else prev_close
            continue
        if prev_close is None:
            trs.append(h - lo)
        else:
            trs.append(max(h - lo, abs(h - prev_close), abs(lo - prev_close)))
        prev_close = c
    return trs


def atr14(daily_bars: Sequence[Bar], period: int = 14) -> float | None:
    """Average True Range over the last ``period`` daily bars (dollars)."""
    trs = _true_ranges(daily_bars)
    if len(trs) < period:
        return None
    window = trs[-period:]
    return sum(window) / len(window)


def avg_daily_range(daily_bars: Sequence[Bar], n: int = 14) -> float | None:
    """Mean fractional high-low range over the last ``n`` daily bars.

    Returned as a fraction of close so it is unit-consistent with ``gap_pct``
    (both dimensionless), letting ``gap_in_adr`` divide cleanly.
    """
    ranges: list[float] = []
    for b in daily_bars[-n:]:
        h, lo, c = _f(b.get("h")), _f(b.get("l")), _f(b.get("c"))
        if None in (h, lo, c) or c <= 0:
            continue
        ranges.append((h - lo) / c)
    if not ranges:
        return None
    return sum(ranges) / len(ranges)


def gap_pct(open_price: float | None, prev_close: float | None) -> float | None:
    """Today's session open vs prior close, as a fraction (0.03 == +3%)."""
    o, p = _f(open_price), _f(prev_close)
    if o is None or p is None or p <= 0:
        return None
    return o / p - 1.0


def gap_in_adr(gap_fraction: float | None, adr_fraction: float | None) -> float | None:
    """How many average-daily-ranges the open gapped. Sign-agnostic magnitude."""
    if gap_fraction is None or adr_fraction is None or adr_fraction <= 0:
        return None
    return abs(gap_fraction) / adr_fraction


def avg_dollar_volume(daily_bars: Sequence[Bar], n: int = 20) -> float | None:
    """Mean close × volume over the last ``n`` daily bars (the liquidity screen)."""
    vals: list[float] = []
    for b in daily_bars[-n:]:
        c, v = _f(b.get("c")), _f(b.get("v"))
        if c is None or v is None or c <= 0 or v < 0:
            continue
        vals.append(c * v)
    if not vals:
        return None
    return sum(vals) / len(vals)


def opening_range_low(bars: Sequence[Bar]) -> float | None:
    """Lowest low across the bars (structure-aware stop reference)."""
    lows = [lv for b in bars if (lv := _f(b.get("l"))) is not None]
    return min(lows) if lows else None
