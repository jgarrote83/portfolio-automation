"""Tunable knobs for the intraday catalyst Flex engine.

Defaults are the decision-locked values; every knob is overridable via a
``FLEX_*`` environment variable (Function App setting) without a code change,
mirroring how the rest of the system is tuned. The engine itself is gated by the
separate ``FLEX_ENABLED`` app setting (read in ``function_app.py``), not here.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Bad float for %s=%r — using default %s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Bad int for %s=%r — using default %s", name, raw, default)
        return default


@dataclass(frozen=True)
class FlexConfig:
    # --- risk + sizing (N4, session 2026-09-12) ---
    # At the new 1.5% stop the risk budget wants 0.40/1.5 ≈ 26.7% of equity per
    # name, so `per_name_cap_pct` is the BINDING governor on essentially every
    # entry and `risk_budget_pct` is effectively INERT. Realized risk per trade
    # falls to 6% × 1.5% = 0.09% — well under the 0.40% budget. That is intended:
    # with a fixed tight stop, single-name CONCENTRATION is the real exposure, not
    # per-trade risk.
    #
    # `per_name_cap_pct` 12.0 → 6.0 (amendment §2.1): a 1.5% stop caps INTRADAY
    # loss and gives ZERO protection against an overnight GAP — and a 2-day hold
    # IS an overnight hold. At 12% a −10% gap costs 1.20pp of equity on one name;
    # the entire invested shortfall under diagnosis is −1.307pp. Halving the cap
    # halves idiosyncratic gap loss and turns a ~2-name book into a ~4-name book.
    # It does NOT reduce aggregate overnight exposure (still ~24% at 4 names) — a
    # broad market gap costs the same. Secondary benefit: ~2 closed trades/day
    # instead of ~1 halves the kill-switch fuse.
    risk_budget_pct: float = 0.40      # % of equity risked per trade (INERT at a 1.5% stop — see above)
    per_name_cap_pct: float = 6.0      # max notional per flex name, % of equity (the BINDING governor)
    sleeve_cap_pct: float = 25.0       # aggregate flex cap, % of equity (a budget, not a target)
    # --- stop (N2) ---
    # The stop is still ATR-DERIVED, but the 1.5% cap governs in practice: at a
    # typical ATR the ATR distance is wider, so `max_stop_pct` clamps it. There is
    # no trail any more (see `build_flex_exit_state`) — `atr_mult` survives only
    # as the stop-distance basis, and `stop_epsilon_atr` only for the repair path.
    atr_mult: float = 3.0              # stop distance = atr_mult × ATR(14), then capped by max_stop_pct
    max_stop_pct: float = 1.5          # N2: skip entry if the ATR stop is wider than this % of entry
    stop_epsilon_atr: float = 0.25     # min stop move (× ATR) before a cancel/replace is issued
    # --- exit (N2) — fixed bracket, no trail, no scale-out ---
    # take_profit_pct / max_stop_pct are the two resting legs of a NATIVE Alpaca
    # OCO bracket, so BOTH exits are continuous at the broker rather than
    # 15-minute-quantized by the engine's tick. Breakeven win rate on the pair
    # alone is 1.5/(2.0+1.5) = 43% — but that is a FLOOR, not the bar: the time
    # stop adds a third outcome at market, which raises the true breakeven by an
    # unknown amount. S2 measures realized expectancy rather than assuming it.
    take_profit_pct: float = 2.0       # resting limit sell at entry × (1 + this/100)
    time_stop_days: int = 2            # trading days before a non-working trade is cut at market
    # --- entry window + confirmation (N3) ---
    # No morning-only cutoff: news arrives all day, and a news-driven sleeve that
    # could only act between 10:00 and 11:00 ET missed most of its own signal.
    # `vwap_window_min` STAYS — session VWAP is meaningless in the first minutes,
    # and the VWAP hold is the main structural confirmation left after regime
    # removal. `entry_late_cutoff_min` replaces the old `entry_cutoff_min`: no NEW
    # entry inside the last N minutes, so a position is never opened with no time
    # to work before the close. Management continues in both windows.
    vwap_window_min: int = 30          # minutes after open before the VWAP hold is read / entry allowed
    entry_late_cutoff_min: int = 30    # no NEW entries within this many minutes of the close
    gap_adr_mult: float = 2.0          # gap above this × ADR raises the confirmation bar (not auto-skip)
    # --- liquidity screen (tied to IEX-VWAP validity) ---
    min_adv_usd: float = 50_000_000.0  # min average daily dollar volume for entry
    # --- conviction path: DORMANT (G-8, session 2026-09-12) ------------------
    # Scoped OUT of the news-momentum profile, not deleted. The conviction path
    # is built around `p_up` vs an empirical `base_rate_up` over a ~2-year
    # lookback for a 15-30 DAY horizon, gated by a 2-session confirm/release
    # hysteresis inherited from a multi-week thematic overlay. None of that means
    # anything for a ~2-day news trade: the confirm delay plus the collector's own
    # one-session lag consumes essentially the whole holding period (FOLLOWUPS
    # #107). The fix is SCOPING, not retuning `confirm_sessions` — retuning would
    # keep an ill-fitting mechanism and merely shorten it. These are two different
    # strategies sharing a sleeve; only the catalyst/news path belongs on a 2-day
    # horizon. Left intact and dormant: if a slower flex sub-strategy is ever
    # wanted, this is the right substrate.
    conviction_path_enabled: bool = False
    # --- conviction-path profile (Task E, 2026-08-14 flex-conviction-path cycle) ---
    # A wider stop than the catalyst profile's 4.0% — a multi-week thesis needs room
    # the LLM's own invalidation level defines, not a tight intraday ATR distance.
    conviction_max_stop_pct: float = 10.0
    # "No-chase" entry cap: reject if price already sits more than this many ATRs
    # above session VWAP — the conviction path is patient (no VWAP-rising/gap logic
    # at all), so it must not buy into a name that already ran hard today.
    conviction_no_chase_atr: float = 1.0
    # --- cash accommodation (B5) — mirrors risk-limits.json's core-level cash-sleeve
    # doctrine so a flex conviction buy can never drain literal cash below the SAME
    # floor the core book protects (must not rebuild the M5 bug: a lift/overlay must
    # never fund itself out of the cash floor). Duplicated here (not imported from
    # risk-limits.json) because flex is deliberately decoupled from core config —
    # see FlexConfig's module doctrine; kept in sync by hand, values as of 2026-08-14.
    cash_sleeve_floor_pct: float = 5.0
    literal_cash_floor_pct: float = 0.75

    def stop_epsilon(self, atr: float) -> float:
        """Absolute minimum stop move before a cancel/replace, in price units."""
        return self.stop_epsilon_atr * atr


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() == "true"


def load_flex_config() -> FlexConfig:
    """Build a FlexConfig from FLEX_* env overrides (falls back to locked defaults)."""
    d = FlexConfig()
    return FlexConfig(
        risk_budget_pct=_env_float("FLEX_RISK_BUDGET_PCT", d.risk_budget_pct),
        per_name_cap_pct=_env_float("FLEX_PER_NAME_CAP_PCT", d.per_name_cap_pct),
        sleeve_cap_pct=_env_float("FLEX_SLEEVE_CAP_PCT", d.sleeve_cap_pct),
        atr_mult=_env_float("FLEX_ATR_MULT", d.atr_mult),
        max_stop_pct=_env_float("FLEX_MAX_STOP_PCT", d.max_stop_pct),
        stop_epsilon_atr=_env_float("FLEX_STOP_EPSILON_ATR", d.stop_epsilon_atr),
        take_profit_pct=_env_float("FLEX_TAKE_PROFIT_PCT", d.take_profit_pct),
        time_stop_days=_env_int("FLEX_TIME_STOP_DAYS", d.time_stop_days),
        vwap_window_min=_env_int("FLEX_VWAP_WINDOW_MIN", d.vwap_window_min),
        entry_late_cutoff_min=_env_int("FLEX_ENTRY_LATE_CUTOFF_MIN", d.entry_late_cutoff_min),
        gap_adr_mult=_env_float("FLEX_GAP_ADR_MULT", d.gap_adr_mult),
        min_adv_usd=_env_float("FLEX_MIN_ADV_USD", d.min_adv_usd),
        conviction_path_enabled=_env_bool("FLEX_CONVICTION_PATH_ENABLED", d.conviction_path_enabled),
        conviction_max_stop_pct=_env_float("FLEX_CONVICTION_MAX_STOP_PCT", d.conviction_max_stop_pct),
        conviction_no_chase_atr=_env_float("FLEX_CONVICTION_NO_CHASE_ATR", d.conviction_no_chase_atr),
        cash_sleeve_floor_pct=_env_float("FLEX_CASH_SLEEVE_FLOOR_PCT", d.cash_sleeve_floor_pct),
        literal_cash_floor_pct=_env_float("FLEX_LITERAL_CASH_FLOOR_PCT", d.literal_cash_floor_pct),
    )
