"""Deterministic flex-entry confirmation pipeline (pure).

The LLM nominates a catalyst candidate and asserts regime fit; **this module
computes the trigger, the stop, and the size** — the model never eyeballs
intraday data. Run the gates in order; the first failure short-circuits with a
``skip_reason`` and ``entry_trigger == "fail"``. Missing data never raises and
never forces a trade.

Pipeline: regime fit → liquidity (ADV) → entry window → gap-vs-ADR (raises the
bar, never auto-skips) → VWAP hold + slope → ATR stop / max-stop / risk-budget
sizing.
"""
from __future__ import annotations

import math

from flex.config import FlexConfig
from flex.indicators import (
    atr14,
    avg_daily_range,
    avg_dollar_volume,
    gap_in_adr,
    gap_pct,
    opening_range_low,
    session_vwap,
    vwap_slope,
)
from flex.regime import regime_fit

# For a big gap (already priced in), require price to hold this far above VWAP
# (× ATR) before entering — the "stronger VWAP hold" that distinguishes
# repriced-and-accumulated from a fade. Not a tunable knob; a structural guard.
_BIG_GAP_HOLD_ATR = 0.10
_VWAP_SLOPE_LOOKBACK = 5


def size_flex_position(
    equity: float,
    entry_price: float,
    stop_distance: float,
    cfg: FlexConfig,
    sleeve_room_usd: float | None = None,
) -> dict:
    """Size a flex entry under three constraints; report which one binds.

    Order: risk-budget sizing (constant dollar risk) → per-name notional cap
    (concentration backstop) → sleeve cap (aggregate flex room). The smallest
    wins, and ``binding`` names the governor — the whole point, so the
    risk-budget-vs-cap interaction is visible in logs/tests rather than buried.
    """
    out = {
        "size_shares": 0,
        "notional_usd": 0.0,
        "notional_pct": 0.0,
        "realized_risk_pct": 0.0,
        "binding": None,
    }
    if stop_distance <= 0 or entry_price <= 0 or equity <= 0:
        return out

    risk_shares = math.floor((cfg.risk_budget_pct / 100.0 * equity) / stop_distance)
    cap_shares = math.floor((cfg.per_name_cap_pct / 100.0 * equity) / entry_price)
    sleeve_shares = (
        math.floor(sleeve_room_usd / entry_price)
        if sleeve_room_usd is not None
        else None
    )

    candidates = [("risk_budget", risk_shares), ("per_name_cap", cap_shares)]
    if sleeve_shares is not None:
        candidates.append(("sleeve_cap", sleeve_shares))

    shares = min(v for _, v in candidates)
    # On a tie, name the governor by priority (risk_budget → per_name_cap → sleeve_cap).
    binding = next(label for label, v in candidates if v == shares)
    shares = max(shares, 0)

    notional = shares * entry_price
    out.update({
        "size_shares": shares,
        "notional_usd": notional,
        "notional_pct": notional / equity * 100.0 if equity else 0.0,
        "realized_risk_pct": shares * stop_distance / equity * 100.0 if equity else 0.0,
        "binding": binding,
    })
    return out


def build_flex_entry(
    candidate: dict,
    intraday_bars: list[dict],
    daily_bars: list[dict],
    quadrant: str,
    equity: float,
    session_minutes_elapsed: int,
    cfg: FlexConfig,
    sleeve_room_usd: float | None = None,
) -> dict:
    symbol = str(candidate.get("symbol") or "").upper()
    sector = candidate.get("sector")

    out: dict = {
        "symbol": symbol,
        "sector": sector,
        "quadrant": quadrant,
        "regime_fit": None,
        "adv_usd": None,
        "gap_pct": None,
        "gap_in_adr": None,
        "vwap": None,
        "vwap_slope": None,
        "above_vwap": None,
        "atr14": None,
        "stop_distance": None,
        "stop_price": None,
        "stop_pct": None,
        "entry_price": None,
        "size_shares": 0,
        "notional_usd": 0.0,
        "notional_pct": 0.0,
        "realized_risk_pct": 0.0,
        "binding": None,
        "entry_trigger": "fail",
        "skip_reason": None,
    }

    def _skip(reason: str) -> dict:
        out["skip_reason"] = reason
        return out

    if not intraday_bars or not daily_bars:
        return _skip("no_bars")

    # G1 — regime fit (the shared quadrant input).
    out["regime_fit"] = regime_fit(sector, quadrant)
    if not out["regime_fit"]:
        return _skip(f"regime_fit:{sector!r} not in {quadrant or 'unknown'}")

    # Liquidity screen — tied to IEX-VWAP validity.
    adv = avg_dollar_volume(daily_bars)
    out["adv_usd"] = adv
    if adv is None or adv < cfg.min_adv_usd:
        return _skip("liquidity_below_min")

    # Entry window (morning-only). Computed from the real session open upstream.
    if session_minutes_elapsed < cfg.vwap_window_min:
        return _skip("pre_window")
    if session_minutes_elapsed >= cfg.entry_cutoff_min:
        return _skip("after_cutoff")

    entry_price = _last_close(intraday_bars)
    prev_close = _last_close(daily_bars)
    sess_open = _first_open(intraday_bars)
    if entry_price is None or entry_price <= 0:
        return _skip("no_price")
    out["entry_price"] = entry_price

    # Gap vs ADR — does NOT auto-skip; raises the confirmation bar.
    out["gap_pct"] = gap_pct(sess_open, prev_close)
    adr = avg_daily_range(daily_bars)
    out["gap_in_adr"] = gap_in_adr(out["gap_pct"], adr)
    big_gap = out["gap_in_adr"] is not None and out["gap_in_adr"] > cfg.gap_adr_mult

    # VWAP hold + slope — the entry trigger.
    vwap = session_vwap(intraday_bars)
    slope = vwap_slope(intraday_bars, _VWAP_SLOPE_LOOKBACK)
    out["vwap"] = vwap
    out["vwap_slope"] = slope
    if vwap is None or slope is None:
        return _skip("no_vwap")
    out["above_vwap"] = entry_price > vwap
    if not out["above_vwap"]:
        return _skip("below_vwap")
    if slope <= 0:
        return _skip("vwap_not_rising")

    atr = atr14(daily_bars)
    out["atr14"] = atr
    if atr is None or atr <= 0:
        return _skip("no_atr")

    # Big gap → require a stronger hold (price comfortably above VWAP).
    if big_gap and (entry_price - vwap) < _BIG_GAP_HOLD_ATR * atr:
        return _skip("big_gap_weak_hold")

    # ATR stop, structure-aware: take the LARGER distance below entry (lower stop)
    # of (a) atr_mult × ATR or (b) below the session VWAP / opening-range low.
    atr_dist = cfg.atr_mult * atr
    orl = opening_range_low(intraday_bars)
    structure_low = min(x for x in (vwap, orl) if x is not None)
    stop_price = min(entry_price - atr_dist, structure_low)
    stop_distance = entry_price - stop_price
    if stop_distance <= 0:
        return _skip("bad_stop")
    out["stop_price"] = stop_price
    out["stop_distance"] = stop_distance
    out["stop_pct"] = stop_distance / entry_price * 100.0
    if out["stop_pct"] > cfg.max_stop_pct:
        return _skip("stop_too_wide")

    # Risk-budget sizing: fixed dollar risk ⟹ a volatile name auto-sizes smaller.
    sizing = size_flex_position(equity, entry_price, stop_distance, cfg, sleeve_room_usd)
    out.update({
        "size_shares": sizing["size_shares"],
        "notional_usd": sizing["notional_usd"],
        "notional_pct": sizing["notional_pct"],
        "realized_risk_pct": sizing["realized_risk_pct"],
        "binding": sizing["binding"],
    })
    if sizing["size_shares"] < 1:
        return _skip("size_zero")

    out["entry_trigger"] = "pass"
    out["skip_reason"] = None
    return out


def _last_close(bars: list[dict]) -> float | None:
    for b in reversed(bars):
        try:
            return float(b.get("c"))
        except (TypeError, ValueError):
            continue
    return None


def _first_open(bars: list[dict]) -> float | None:
    for b in bars:
        try:
            return float(b.get("o"))
        except (TypeError, ValueError):
            continue
    return None
