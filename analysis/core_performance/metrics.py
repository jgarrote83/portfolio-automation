"""Pure performance statistics for the Core-vs-SPY analysis (no I/O). Daily series in, numbers out.

Conventions: returns are simple daily returns; annualisation is x252 / sqrt(252); the Sharpe ratio uses a zero risk-free rate and the
sample standard deviation (ddof = 1). With under two years of data every one of these is an estimate with a wide error band, which
`sharpe_ci95` makes explicit.
"""
from __future__ import annotations

import math
from statistics import mean, stdev

TRADING_DAYS = 252


def returns(values: list[float]) -> list[float]:
    """Simple daily returns of a value series (len - 1 numbers)."""
    return [b / a - 1.0 for a, b in zip(values, values[1:])]


def total_return(values: list[float]) -> float:
    return values[-1] / values[0] - 1.0


def max_drawdown(values: list[float]) -> dict:
    """Largest peak-to-trough fall of a value series, as a negative fraction, with the indices of the peak and the trough."""
    peak_i, worst, w_peak, w_trough = 0, 0.0, 0, 0
    for i, v in enumerate(values):
        if v > values[peak_i]:
            peak_i = i
        dd = v / values[peak_i] - 1.0
        if dd < worst:
            worst, w_peak, w_trough = dd, peak_i, i
    return {"max_drawdown": worst, "peak_index": w_peak, "trough_index": w_trough}


def annualized_vol(rets: list[float]) -> float | None:
    return stdev(rets) * math.sqrt(TRADING_DAYS) if len(rets) > 1 else None


def sharpe(rets: list[float], rf_daily: float = 0.0) -> float | None:
    """Annualised Sharpe from daily returns; None when it is undefined (fewer than two points, zero dispersion)."""
    if len(rets) < 2:
        return None
    ex = [r - rf_daily for r in rets]
    sd = stdev(ex)
    return mean(ex) / sd * math.sqrt(TRADING_DAYS) if sd > 0 else None


def sharpe_ci95(sr_annual: float, n: int) -> tuple[float, float]:
    """Approximate 95% band for an annualised Sharpe estimated from `n` daily returns (Lo 2002, i.i.d. normal returns):
    standard error = sqrt((1 + SR_d^2 / 2) / n) per day, scaled by sqrt(252)."""
    sr_d = sr_annual / math.sqrt(TRADING_DAYS)
    half = 1.96 * math.sqrt((1.0 + 0.5 * sr_d * sr_d) / n) * math.sqrt(TRADING_DAYS)
    return sr_annual - half, sr_annual + half


def regression(book: list[float], bench: list[float]) -> dict:
    """OLS of the book's daily returns on the benchmark's: beta, annualised alpha (daily intercept x 252), correlation and R^2."""
    n = len(book)
    if n != len(bench) or n < 3:
        raise ValueError("need two equal-length series of at least 3 returns")
    mb, mx = mean(book), mean(bench)
    sxx = sum((x - mx) ** 2 for x in bench)
    syy = sum((y - mb) ** 2 for y in book)
    sxy = sum((x - mx) * (y - mb) for x, y in zip(bench, book))
    if sxx <= 0 or syy <= 0:
        return {"beta": None, "alpha_annual": None, "corr": None, "r2": None, "n": n}
    beta = sxy / sxx
    corr = sxy / math.sqrt(sxx * syy)
    return {"beta": beta, "alpha_annual": (mb - beta * mx) * TRADING_DAYS, "corr": corr, "r2": corr * corr, "n": n}


def value_index(prices: dict[str, dict[str, float]], weights: dict[str, float], dates: list[str], rebalance_on: set[str] | None = None) -> list[float]:
    """Value of a portfolio that starts at 1.0 on dates[0] with `weights` (summing to 1) and is rebalanced back to those weights at
    the close of every date in `rebalance_on` (an empty set = buy and hold). `prices[sym][date]` must exist for every date."""
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ValueError(f"weights sum to {sum(weights.values())}, not 1")
    rebalance_on = rebalance_on or set()
    units = {s: w / prices[s][dates[0]] for s, w in weights.items()}
    out = [1.0]
    for d in dates[1:]:
        v = sum(units[s] * prices[s][d] for s in units)
        out.append(v)
        if d in rebalance_on:
            units = {s: weights[s] * v / prices[s][d] for s in weights}
    return out


def month_end_dates(dates: list[str]) -> set[str]:
    """The last trading date of each calendar month present in `dates` (a month still in progress at the end is excluded, so the last
    date is not treated as a month end unless the next month has begun)."""
    ends = set()
    for a, b in zip(dates, dates[1:]):
        if a[:7] != b[:7]:
            ends.add(a)
    return ends


def volatility_scaled(bench_rets: list[float], cash_rets: list[float], target_vol: float) -> dict:
    """Scale the benchmark's daily returns by k = target / its own volatility, holding (1 - k) in cash (a negative remainder is
    financed at the cash return). k is estimated over the same window (ex post), so this is a yardstick, not a strategy."""
    own = annualized_vol(bench_rets)
    if not own:
        raise ValueError("benchmark volatility is zero")
    k = target_vol / own
    scaled = [k * r + (1.0 - k) * c for r, c in zip(bench_rets, cash_rets)]
    return {"k": k, "returns": scaled}


def compound(rets: list[float]) -> float:
    v = 1.0
    for r in rets:
        v *= 1.0 + r
    return v - 1.0
