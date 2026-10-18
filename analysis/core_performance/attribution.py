"""Daily, exactly-additive decomposition of the book's lag behind SPY (pure; no I/O).

For each day t, with the book's start-of-day weights (cash C, SGOV S, equity E, all at the PREVIOUS close) and the day's returns:

  excess_t   = r_book_t - r_spy_t
  cash_t     = [ S*(r_sgov_t - r_spy_t) + C*(0 - r_spy_t) ] / E          (a) the cash sleeve's contribution; literal cash earns 0
  invested_t = excess_t - cash_t                                           the invested book's contribution (an exact residual)
  allocation_t = (1 - (C+S)/E) * (r_policy_t - r_spy_t)                    (b) only if a policy-portfolio return series is supplied
  tactical_t = invested_t - allocation_t                                   (c)

so the terms add to `excess_t` on every day by construction, and their sums add to the cumulative ARITHMETIC excess. The compounded
excess (book total return minus SPY total return) differs from that sum by a compounding residual, reported separately and never
hidden. Because the terms are written as contributions to the book's return there is no division by an invested weight, so a day on
which the book is 100% cash needs no special case. Leverage (negative cash) enters naturally with a negative C.
"""
from __future__ import annotations


def daily_terms(equity_prev: float, cash_prev: float, sgov_value_prev: float, r_book: float, r_spy: float, r_sgov: float,
                r_policy: float | None = None) -> dict:
    """One day's terms (fractions of the book's return, i.e. not yet x100)."""
    sleeve = cash_prev + sgov_value_prev
    excess = r_book - r_spy
    cash_literal = (cash_prev * (0.0 - r_spy)) / equity_prev
    cash_sgov = (sgov_value_prev * (r_sgov - r_spy)) / equity_prev
    cash_term = cash_literal + cash_sgov
    invested = excess - cash_term
    out = {"excess": excess, "cash": cash_term, "cash_literal": cash_literal, "cash_sgov": cash_sgov, "invested": invested,
           "allocation": None, "tactical": None, "sleeve_weight": sleeve / equity_prev}
    if r_policy is not None:
        invested_weight = 1.0 - sleeve / equity_prev
        out["allocation"] = invested_weight * (r_policy - r_spy)
        out["tactical"] = invested - out["allocation"]
    return out


def decompose(days: list[dict], book_total_return: float, spy_total_return: float) -> dict:
    """Sum the daily terms. `days` are `daily_terms` outputs in order. Returns the cumulative terms in percentage points, the
    arithmetic excess they add to, the compounded excess, and the compounding residual between the two."""
    keys = ("excess", "cash", "cash_literal", "cash_sgov", "invested")
    sums = {k: sum(d[k] for d in days) for k in keys}
    with_policy = [d for d in days if d["allocation"] is not None]
    if with_policy:
        # the policy may start part-way through the window (no reference existed before it): the invested term of the earlier days is
        # reported on its own, so cash + invested_before_policy + allocation + tactical = excess still holds exactly
        sums["allocation"] = sum(d["allocation"] for d in with_policy)
        sums["tactical"] = sum(d["tactical"] for d in with_policy)
        sums["invested_before_policy"] = sum(d["invested"] for d in days if d["allocation"] is None)
    else:
        sums["allocation"] = sums["tactical"] = sums["invested_before_policy"] = None
    compounded = book_total_return - spy_total_return
    pp = {k: (None if v is None else v * 100.0) for k, v in sums.items()}
    pp["compounded_excess"] = compounded * 100.0
    pp["compounding_residual"] = (compounded - sums["excess"]) * 100.0
    pp["days"] = len(days)
    pp["policy_days"] = len(with_policy)
    pp["avg_sleeve_weight_pct"] = 100.0 * sum(d["sleeve_weight"] for d in days) / len(days) if days else None
    return pp


def existing_method(avg_cash_pct: float, r_cash_pct: float, r_spy_pct: float) -> dict:
    """The collector's `_excess_attribution` formula, for reproducing its published number: cash term = average cash weight x
    (cash return - SPY return) over the window; invested term = total excess minus the cash term."""
    cash = avg_cash_pct / 100.0 * (r_cash_pct - r_spy_pct)
    return {"cash_contribution_pp": cash}
