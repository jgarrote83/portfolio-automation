"""Cost model: adverse slippage on every fill plus a per-share commission.

Pre-registration section 5, item 12: a buy fills `cents` higher and a sell `cents` lower, whether it
is an entry, a stop, the time exit or a loss-limit flatten; commission is charged per share on every
fill (so twice per round trip). Gross P&L is measured at the un-slipped fill prices, so slippage is
booked as a cost per fill (`shares x cents`) rather than by moving the fill price: algebraically the
same net P&L, with gross, slippage and commission reported separately.
"""
from __future__ import annotations


def side_costs(shares: int, slippage_cents: float, commission_per_share: float) -> tuple[float, float]:
    """(slippage dollars, commission dollars) for ONE fill of `shares`."""
    return shares * slippage_cents / 100.0, shares * commission_per_share
