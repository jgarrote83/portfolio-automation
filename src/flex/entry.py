"""Deterministic flex-entry confirmation pipeline (pure).

The LLM nominates a catalyst candidate; **this module computes the trigger,
the stop, and the size** — the model never eyeballs intraday data. Run the
gates in order; the first failure short-circuits with a ``skip_reason`` and
``entry_trigger == "fail"``. Missing data never raises and never forces a
trade.

Pipeline: regime fit (computed + surfaced, INFORMATIONAL only as of session
2026-08-10 — see ``regime_fit`` below) → liquidity (ADV) → entry window →
gap-vs-ADR (raises the bar, never auto-skips) → VWAP hold + slope → ATR stop /
max-stop / risk-budget sizing.
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


def _size_conviction_position(
    equity: float,
    entry_price: float,
    stop_distance: float,
    risk_budget_pct: float,
    per_name_cap_pct: float,
    sleeve_room_usd: float | None = None,
) -> dict:
    """Shared three-way sizing governor (risk-budget → per-name cap → sleeve
    cap, smallest wins, `binding` names it) — used by both `size_flex_position`
    (catalyst profile, reads its pcts off a `FlexConfig`) and
    `build_conviction_entry` (conviction profile, passes an already-scaled
    `risk_budget_pct` = `cfg.risk_budget_pct * size_mult`). Extracted so the
    two profiles' sizing math can never drift apart by hand-copy."""
    out = {
        "size_shares": 0,
        "notional_usd": 0.0,
        "notional_pct": 0.0,
        "realized_risk_pct": 0.0,
        "binding": None,
    }
    if stop_distance <= 0 or entry_price <= 0 or equity <= 0:
        return out

    risk_shares = math.floor((risk_budget_pct / 100.0 * equity) / stop_distance)
    cap_shares = math.floor((per_name_cap_pct / 100.0 * equity) / entry_price)
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
    return _size_conviction_position(
        equity, entry_price, stop_distance,
        cfg.risk_budget_pct, cfg.per_name_cap_pct, sleeve_room_usd,
    )


def build_flex_entry(
    candidate: dict,
    intraday_bars: list[dict],
    daily_bars: list[dict],
    quadrant: str,
    equity: float,
    session_minutes_elapsed: int,
    cfg: FlexConfig,
    sleeve_room_usd: float | None = None,
    quadrant_basis: str = "",
) -> dict:
    symbol = str(candidate.get("symbol") or "").upper()
    sector = candidate.get("sector")

    out: dict = {
        "symbol": symbol,
        "sector": sector,
        "quadrant": quadrant,
        "quadrant_basis": quadrant_basis,
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

    # Regime fit (the shared quadrant input) — DEMOTED from a hard entry veto to
    # an informational field (session 2026-08-10, catalyst-sleeve-funnel Task E).
    # A monthly-vintage macro quadrant has no business vetoing a 5-day catalyst
    # trade — cadence mismatch. Still computed and still surfaced (the basis —
    # active / borderline_5d_tiebreak / favored_single / unresolved — shows WHY a
    # quadrant was or wasn't in force) so `flex_state` and the catalyst scorer can
    # consume it, but a miss no longer short-circuits the pipeline; liquidity,
    # window, VWAP, and sizing all still run. Preserves the D1 (2026-07-21) fix:
    # an unresolved quadrant (`quadrant` falsy) still yields `regime_fit=False`
    # here, but that no longer resurrects the old G1 freeze either.
    out["regime_fit"] = regime_fit(sector, quadrant)

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


def cash_accommodation_shares(
    proposed_shares: int,
    entry_price: float,
    literal_cash_usd: float,
    sgov_usd: float,
    equity: float,
    cfg: FlexConfig,
) -> dict:
    """B5 — clamp a conviction-path entry's share count so it can never drain
    literal cash below `literal_cash_floor_pct` of equity, nor the whole cash
    sleeve (literal cash + SGOV) below `cash_sleeve_floor_pct` of equity.

    A CLAMP, never an outright rejection — "size-floored ≠ impossible" is the
    existing doctrine everywhere else in this system (core reference execution,
    thematic conviction), applied here too. Explicitly the M5 callback: M5 was
    a lift silently spending cash the reduction pool should have protected;
    the fix there excluded `__cash__` from the pool it could drain. The
    parallel bug here would be a conviction entry sized purely off the risk-
    budget/per-name/sleeve-cap chain with NO awareness that literal cash is
    already thin — this function is that missing awareness, checked at the
    point of sizing rather than trusted to some upstream exclusion list.

    Returns ``{"shares": int, "funding_clamped": bool}``. Non-positive
    inputs (equity/entry_price) degrade to 0 shares, clamped — fail-closed,
    never a divide-by-zero or a fabricated allowance.
    """
    if proposed_shares <= 0 or entry_price <= 0 or equity <= 0:
        return {"shares": 0, "funding_clamped": proposed_shares > 0}

    notional = proposed_shares * entry_price
    literal_floor_usd = cfg.literal_cash_floor_pct / 100.0 * equity
    sleeve_floor_usd = cfg.cash_sleeve_floor_pct / 100.0 * equity

    literal_room = max(0.0, literal_cash_usd - literal_floor_usd)
    sleeve_room = max(0.0, (literal_cash_usd + sgov_usd) - sleeve_floor_usd)
    room = min(literal_room, sleeve_room)

    if notional <= room:
        return {"shares": proposed_shares, "funding_clamped": False}

    affordable = int(room // entry_price)
    affordable = max(0, min(affordable, proposed_shares))
    return {"shares": affordable, "funding_clamped": True}


def build_conviction_entry(
    candidate: dict,
    intraday_bars: list[dict],
    daily_bars: list[dict],
    quadrant: str,
    equity: float,
    session_minutes_elapsed: int,
    cfg: FlexConfig,
    size_mult: float,
    sleeve_room_usd: float | None = None,
    quadrant_basis: str = "",
    literal_cash_usd: float | None = None,
    sgov_usd: float | None = None,
) -> dict:
    """Task E — the conviction-path Layer 2 entry pipeline, a SEPARATE gate
    sequence from `build_flex_entry` (the catalyst path stays byte-identical;
    nothing here is shared logic beyond the common indicator/sizing helpers).

    Differences from the catalyst profile (all deliberate, per the 2026-08-14
    cycle's decision to demote a dated catalyst from gate to amplifier):
      - No gap/VWAP-rising-slope entry trigger. Instead a "no-chase" cap:
        entry is refused if price already sits more than
        `conviction_no_chase_atr` ATRs above session VWAP (`entry_above_
        no_chase_limit`) — patient by design, never chasing an intraday
        breakout the way the catalyst profile deliberately does.
      - Stop is the NOMINATION's own `invalidation` price level (the LLM's
        stated thesis-invalidation point), not an ATR-derived distance —
        still bounded by `conviction_max_stop_pct` (10.0 vs the catalyst
        profile's 4.0) as a sanity backstop, never a runaway stop.
      - No time stop at all (Layer 2 has none for this path — the calendar
        clock is replaced entirely by the collector-side `release_sessions`
        hysteresis decay; see `build_flex_exit_state`'s `path`-aware skip).
      - Sizing multiplies the risk-BUDGET itself by `size_mult` (the ladder's
        confirmed size_mult) before the existing risk-budget/per-name-cap/
        sleeve-cap chain runs — the SAME three-way governor, just fed a
        smaller effective risk budget for a lower-conviction nomination —
        then B5's cash-accommodation clamp runs last.
    """
    symbol = str(candidate.get("symbol") or "").upper()
    sector = candidate.get("sector")

    out: dict = {
        "symbol": symbol,
        "sector": sector,
        "quadrant": quadrant,
        "quadrant_basis": quadrant_basis,
        "path": "conviction",
        "regime_fit": None,
        "adv_usd": None,
        "vwap": None,
        "no_chase_limit": None,
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
        "funding_clamped": False,
        "entry_trigger": "fail",
        "skip_reason": None,
    }

    def _skip(reason: str) -> dict:
        out["skip_reason"] = reason
        return out

    if not intraday_bars or not daily_bars:
        return _skip("no_bars")

    out["regime_fit"] = regime_fit(sector, quadrant)

    adv = avg_dollar_volume(daily_bars)
    out["adv_usd"] = adv
    if adv is None or adv < cfg.min_adv_usd:
        return _skip("liquidity_below_min")

    if session_minutes_elapsed < cfg.vwap_window_min:
        return _skip("pre_window")
    if session_minutes_elapsed >= cfg.entry_cutoff_min:
        return _skip("after_cutoff")

    entry_price = _last_close(intraday_bars)
    if entry_price is None or entry_price <= 0:
        return _skip("no_price")
    out["entry_price"] = entry_price

    vwap = session_vwap(intraday_bars)
    out["vwap"] = vwap
    if vwap is None:
        return _skip("no_vwap")

    atr = atr14(daily_bars)
    out["atr14"] = atr
    if atr is None or atr <= 0:
        return _skip("no_atr")

    no_chase_limit = vwap + cfg.conviction_no_chase_atr * atr
    out["no_chase_limit"] = no_chase_limit
    if entry_price > no_chase_limit:
        return _skip("entry_above_no_chase_limit")

    invalidation = candidate.get("invalidation")
    try:
        stop_price = float(invalidation)
    except (TypeError, ValueError):
        return _skip("no_invalidation_level")
    if stop_price <= 0 or stop_price >= entry_price:
        return _skip("invalid_invalidation_level")
    stop_distance = entry_price - stop_price
    out["stop_price"] = stop_price
    out["stop_distance"] = stop_distance
    out["stop_pct"] = stop_distance / entry_price * 100.0
    if out["stop_pct"] > cfg.conviction_max_stop_pct:
        return _skip("stop_too_wide")

    effective_risk_budget_pct = cfg.risk_budget_pct * max(0.0, size_mult)
    sizing = _size_conviction_position(
        equity, entry_price, stop_distance, effective_risk_budget_pct,
        cfg.per_name_cap_pct, sleeve_room_usd,
    )
    shares, binding = sizing["size_shares"], sizing["binding"]
    funding_clamped = False
    if literal_cash_usd is not None and sgov_usd is not None:
        accommodation = cash_accommodation_shares(
            shares, entry_price, literal_cash_usd, sgov_usd, equity, cfg,
        )
        shares = accommodation["shares"]
        funding_clamped = accommodation["funding_clamped"]
        if funding_clamped:
            binding = "cash_floor"

    notional = shares * entry_price
    out.update({
        "size_shares": shares,
        "notional_usd": notional,
        "notional_pct": notional / equity * 100.0 if equity else 0.0,
        "realized_risk_pct": shares * stop_distance / equity * 100.0 if equity else 0.0,
        "binding": binding,
        "funding_clamped": funding_clamped,
    })
    if shares < 1:
        return _skip("size_zero" if not funding_clamped else "cash_floor_breach")

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
