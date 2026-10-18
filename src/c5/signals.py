"""C5 (trend + credit): the ONE implementation of its decision logic.

Pure Python, no I/O, standard library only (it may import `shared.*`, and needs nothing from it). Both the research backtests
(`research/core_candidates`) and the live engine (`c5.handler`) call these functions, so the rule that was tested is, by construction,
the rule that trades. Spec: `docs/specs/Core_Candidates_Preregistration.md`, section 2, items 7, 9 and 11.

* **Trend test** (item 7): the latest month-end adjusted close is STRICTLY above the mean of the `months` most recent month-end closes,
  the latest included.
* **Credit test** (item 9): the credit value of a day is the latest `BAA10Y` observation dated STRICTLY BEFORE that day (a one-day
  publication lag; a same-day value is never used). The test is healthy when the latest such value is <= the mean of the values for the
  `window` most recent trading days, the latest included.
* **Holding** (item 11): both healthy -> leveraged 2x S&P; exactly one -> 1x S&P; neither -> T-bills.
"""
from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from datetime import date

TREND_MONTHS = 10
CREDIT_WINDOW = 126
LEVERED, ONE_X, TBILLS = "2X", "1X", "TBILLS"


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def trend_healthy(month_end_closes: Sequence[float], months: int = TREND_MONTHS) -> bool:
    """`month_end_closes` in time order, the decision month-end LAST."""
    if len(month_end_closes) < months:
        raise ValueError(f"the trend test needs {months} month-end closes, got {len(month_end_closes)}")
    window = list(month_end_closes[-months:])
    return bool(window[-1] > _mean(window))


def credit_value_before(obs_dates: Sequence[date], obs_values: Sequence[float], day: date) -> tuple[float, date] | None:
    """The latest observation dated strictly before `day` (`obs_dates` ascending, no blanks), with its date; None if there is none."""
    k = bisect_left(list(obs_dates), day) - 1
    if k < 0:
        return None
    return float(obs_values[k]), obs_dates[k]


def credit_calm(credit_values: Sequence[float], window: int = CREDIT_WINDOW) -> bool:
    """`credit_values`: one credit value per trading day (each already `credit_value_before` that day), the decision day LAST."""
    if len(credit_values) < window:
        raise ValueError(f"the credit test needs {window} daily values, got {len(credit_values)}")
    w = list(credit_values[-window:])
    return bool(w[-1] <= _mean(w))


def holding(trend_ok: bool, credit_ok: bool) -> str:
    if trend_ok and credit_ok:
        return LEVERED
    if trend_ok or credit_ok:
        return ONE_X
    return TBILLS
