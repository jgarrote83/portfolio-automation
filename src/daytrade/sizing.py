"""Position sizing (pure) — spec §4.

``risk_usd = risk_pct/100 × (flex_sleeve_cap_pct/100 × equity)`` — the lab risks
a fraction of the flex SLEEVE, not of equity. Shares are then capped by the lab
notional cap AND the joint sleeve headroom (sleeve cap minus BOTH engines' open
notional — the separation contract's arbitration seam, spec §1). ``binding``
names the governor, mirroring the flex sizer's reporting.
"""
from __future__ import annotations

import math


def size_daytrade_entry(
    equity: float,
    entry_price: float,
    stop_price: float,
    cfg,
    catalyst_open_notional: float = 0.0,
    daytrade_open_notional: float = 0.0,
    half_risk: bool = False,
) -> dict:
    out = {
        "size_shares": 0,
        "notional_usd": 0.0,
        "risk_usd": 0.0,
        "binding": None,
    }
    stop_distance = entry_price - stop_price
    if equity <= 0 or entry_price <= 0 or stop_distance <= 0:
        return out

    sleeve_usd = cfg.flex_sleeve_cap_pct / 100.0 * equity
    risk_usd = cfg.risk_pct / 100.0 * sleeve_usd
    if half_risk:
        risk_usd /= 2.0
    out["risk_usd"] = risk_usd

    risk_shares = math.floor(risk_usd / stop_distance)
    cap_shares = math.floor((cfg.notional_cap_pct / 100.0 * equity) / entry_price)
    headroom_usd = max(0.0, sleeve_usd - catalyst_open_notional - daytrade_open_notional)
    sleeve_shares = math.floor(headroom_usd / entry_price)

    candidates = [
        ("risk_budget", risk_shares),
        ("notional_cap", cap_shares),
        ("joint_sleeve", sleeve_shares),
    ]
    shares = max(0, min(v for _, v in candidates))
    binding = next(label for label, v in candidates if v == min(v for _, v in candidates))
    out.update({
        "size_shares": shares,
        "notional_usd": shares * entry_price,
        "binding": binding,
    })
    return out
