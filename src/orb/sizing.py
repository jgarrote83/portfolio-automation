"""Position sizing. Pure Python.

Shares = floor(min(risk shares, cap shares)) with
  risk shares = (risk_per_trade_pct % of the sleeve) / stop distance
  cap shares  = sleeve / position_slots / price
The sleeve is never levered: every position is capped at sleeve / position_slots, so with at most
`position_slots` positions the book cannot exceed the sleeve.

Arithmetic worth knowing: with a stop distance of 0.1 x ATR the risk limit binds only when
price <= 0.5 x ATR, which essentially never happens for a stock, so the position cap is the
binding limit on almost every trade. `binding` records which one actually did, per trade.
"""
from __future__ import annotations

import math
from typing import NamedTuple

from .config import OrbConfig

_EPS = 1e-9


class SizeResult(NamedTuple):
    shares: int
    risk_shares: float
    cap_shares: float
    binding: str            # "risk" | "cap" | "tie" | "invalid"


def position_size(price: float, stop_distance: float, cfg: OrbConfig,
                  sleeve_capital: float | None = None) -> SizeResult:
    """Integer shares for one position; 0 shares means "skip the trade"."""
    sleeve = cfg.sleeve_capital_usd if sleeve_capital is None else sleeve_capital
    if not (price > 0) or not (stop_distance > 0) or not (sleeve > 0):
        return SizeResult(0, 0.0, 0.0, "invalid")
    risk_shares = sleeve * cfg.risk_per_trade_pct / 100.0 / stop_distance
    cap_shares = sleeve / cfg.position_slots / price
    if abs(risk_shares - cap_shares) <= _EPS * max(1.0, risk_shares, cap_shares):
        binding = "tie"
    else:
        binding = "risk" if risk_shares < cap_shares else "cap"
    shares = int(math.floor(min(risk_shares, cap_shares) + _EPS))
    return SizeResult(max(shares, 0), risk_shares, cap_shares, binding)
