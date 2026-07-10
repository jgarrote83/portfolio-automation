"""DayTrade Lab configuration (spec §7).

Defaults are the v0.1 spec values; every knob is overridable via a ``DAYTRADE_*``
environment variable (Function App setting) without a code change. The engine
on/off gate is the separate ``DAYTRADE_ENABLED`` app setting (read in
``function_app.py``), not here. ``flex_sleeve_cap_pct`` deliberately reads the
catalyst engine's ``FLEX_SLEEVE_CAP_PCT`` env name — the sleeve budget is the ONE
shared knob (spec §1) — without importing ``flex.*``.

``spec_version`` is part of the pre-registered grading contract: any threshold
change must bump it, which resets the n=20/40 rule counts by design (spec §5).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_SCALE_MODES = ("none", "half_at_1r")
_CONSOLIDATED_SOURCES = ("fmp", "sip", "unavailable")


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


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    return raw if raw else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() == "true"


@dataclass(frozen=True)
class DayTradeConfig:
    # --- risk + sizing (spec §4) ---
    risk_pct: float = 0.5              # % of the flex SLEEVE (not equity) risked per trade
    notional_cap_pct: float = 6.0      # max lab notional, % of account equity
    flex_sleeve_cap_pct: float = 25.0  # the shared sleeve budget (FLEX_SLEEVE_CAP_PCT)
    max_stop_pct: float = 2.0          # implied stop wider than this % of price ⇒ skip
    # --- validation gates (spec §3) ---
    gap_min_pct: float = 4.0
    rvol_min: float = 3.0
    pm_dollar_vol_min: float = 3_000_000.0
    float_min_shares: float = 20_000_000.0
    float_max_shares: float = 100_000_000.0
    rotation_min: float = 0.05
    price_min: float = 5.0
    price_max: float = 100.0
    spread_max: float = 0.0015
    small_cap_usd: float = 2_000_000_000.0   # sub-$2B ⇒ float/dilution fail closed
    dilution_lookback_days: int = 180
    max_candidates: int = 5
    consolidated_source: str = "unavailable"  # "fmp" | "sip" | "unavailable" (spec §2)
    # --- session geometry, minutes relative to the calendar open (spec §4) ---
    window_pre_open_min: int = 5       # live from open−5 (validation tick)
    window_end_min: int = 110          # ...to open+110 (11:20 on a normal day)
    orb_minutes: int = 5
    orb_minutes_c: int = 15            # C-class opening range
    entry_cutoff_min: int = 60         # no NEW entries past open+60
    slot1_resolve_by_min: int = 30     # slot 2 requires slot 1 resolved before this
    slot2_start_min: int = 30
    slot2_end_min: int = 60
    flat_min: int = 105                # open+105 = 11:15 flat
    stale_print_max_s: int = 60        # no bar for >60s ⇒ no entry that tick
    # --- execution ---
    scale_mode: str = "none"           # "none" (bracket) | "half_at_1r" (managed)
    llm_classify: bool = False
    # --- global sector pulse (spec §10) ---
    pulse_strong_pct: float = 0.6      # |avg %| for strong_up/strong_down…
    pulse_up_pct: float = 0.2          # …and for plain up/down
    pulse_strong_breadth: float = 0.75  # breadth needed alongside a strong avg
    pulse_volume_ratio: float = 2.0    # member ≥2× its 30d pm ratio ⇒ volume_flag
    pulse_stale_after_s: int = 3600    # no print in the last 60 min ⇒ stale
    pulse_foreign_source: str = "off"  # v2: "fmp" foreign quotes (^N225 etc.) — not built
    # --- breakers + grading (spec §5/§6, pre-registered) ---
    haircut_pp_per_side: float = 0.10
    day_max_loss_r: float = 1.0        # one loss ends the day (max −1R/day)
    week_halt_r: float = -3.0
    unlock_n: int = 20
    cell_n: int = 40
    spec_version: str = "v0.1"

    def __post_init__(self) -> None:
        if not (0.0 < self.risk_pct <= 2.0):
            raise ValueError(f"risk_pct out of bounds (0, 2]: {self.risk_pct}")
        if not (0.0 < self.notional_cap_pct <= self.flex_sleeve_cap_pct):
            raise ValueError(
                f"notional_cap_pct must be in (0, sleeve cap {self.flex_sleeve_cap_pct}]: "
                f"{self.notional_cap_pct}")
        if not (0.0 < self.max_stop_pct <= 5.0):
            raise ValueError(f"max_stop_pct out of bounds (0, 5]: {self.max_stop_pct}")
        if self.gap_min_pct <= 0 or self.rvol_min < 1.0 or self.rotation_min <= 0:
            raise ValueError("gap_min_pct/rvol_min/rotation_min out of bounds")
        if not (0 < self.price_min < self.price_max):
            raise ValueError(f"price band invalid: {self.price_min}–{self.price_max}")
        if not (0 < self.float_min_shares < self.float_max_shares):
            raise ValueError("float band invalid")
        if not (0.0 < self.spread_max <= 0.01):
            raise ValueError(f"spread_max out of bounds (0, 0.01]: {self.spread_max}")
        if self.scale_mode not in _SCALE_MODES:
            raise ValueError(f"scale_mode must be one of {_SCALE_MODES}: {self.scale_mode}")
        if self.consolidated_source not in _CONSOLIDATED_SOURCES:
            raise ValueError(
                f"consolidated_source must be one of {_CONSOLIDATED_SOURCES}: "
                f"{self.consolidated_source}")
        if not (0 < self.orb_minutes <= self.orb_minutes_c <= self.entry_cutoff_min):
            raise ValueError("ORB/cutoff geometry invalid")
        if not (self.entry_cutoff_min <= self.flat_min <= self.window_end_min):
            raise ValueError("cutoff/flat/window geometry invalid")
        if not (self.slot2_start_min <= self.slot2_end_min <= self.entry_cutoff_min):
            raise ValueError("slot-2 window invalid")
        if self.day_max_loss_r <= 0 or self.week_halt_r >= 0:
            raise ValueError("breaker signs invalid (day > 0, week < 0)")
        if not (0 < self.unlock_n <= self.cell_n):
            raise ValueError("grading n-triggers invalid")
        if not (0 < self.pulse_up_pct <= self.pulse_strong_pct):
            raise ValueError("pulse thresholds invalid (0 < up ≤ strong)")
        if not (0.5 <= self.pulse_strong_breadth <= 1.0):
            raise ValueError("pulse_strong_breadth out of bounds [0.5, 1]")
        if self.pulse_foreign_source not in ("off", "fmp"):
            raise ValueError(
                f"pulse_foreign_source must be off|fmp: {self.pulse_foreign_source}")


def load_daytrade_config() -> DayTradeConfig:
    return DayTradeConfig(
        risk_pct=_env_float("DAYTRADE_RISK_PCT", 0.5),
        notional_cap_pct=_env_float("DAYTRADE_NOTIONAL_CAP_PCT", 6.0),
        flex_sleeve_cap_pct=_env_float("FLEX_SLEEVE_CAP_PCT", 25.0),
        max_stop_pct=_env_float("DAYTRADE_MAX_STOP_PCT", 2.0),
        gap_min_pct=_env_float("DAYTRADE_GAP_MIN_PCT", 4.0),
        rvol_min=_env_float("DAYTRADE_RVOL_MIN", 3.0),
        pm_dollar_vol_min=_env_float("DAYTRADE_PM_DOLLAR_VOL_MIN", 3_000_000.0),
        float_min_shares=_env_float("DAYTRADE_FLOAT_MIN_SHARES", 20_000_000.0),
        float_max_shares=_env_float("DAYTRADE_FLOAT_MAX_SHARES", 100_000_000.0),
        rotation_min=_env_float("DAYTRADE_ROTATION_MIN", 0.05),
        price_min=_env_float("DAYTRADE_PRICE_MIN", 5.0),
        price_max=_env_float("DAYTRADE_PRICE_MAX", 100.0),
        spread_max=_env_float("DAYTRADE_SPREAD_MAX", 0.0015),
        small_cap_usd=_env_float("DAYTRADE_SMALL_CAP_USD", 2_000_000_000.0),
        dilution_lookback_days=_env_int("DAYTRADE_DILUTION_LOOKBACK_DAYS", 180),
        max_candidates=_env_int("DAYTRADE_MAX_CANDIDATES", 5),
        consolidated_source=_env_str("DAYTRADE_CONSOLIDATED_SOURCE", "unavailable"),
        orb_minutes=_env_int("DAYTRADE_ORB_MINUTES", 5),
        orb_minutes_c=_env_int("DAYTRADE_ORB_MINUTES_C", 15),
        entry_cutoff_min=_env_int("DAYTRADE_ENTRY_CUTOFF_MIN", 60),
        flat_min=_env_int("DAYTRADE_FLAT_MIN", 105),
        scale_mode=_env_str("DAYTRADE_SCALE_MODE", "none"),
        llm_classify=_env_bool("DAYTRADE_LLM_CLASSIFY", False),
        pulse_strong_pct=_env_float("DAYTRADE_PULSE_STRONG_PCT", 0.6),
        pulse_up_pct=_env_float("DAYTRADE_PULSE_UP_PCT", 0.2),
        pulse_strong_breadth=_env_float("DAYTRADE_PULSE_STRONG_BREADTH", 0.75),
        pulse_volume_ratio=_env_float("DAYTRADE_PULSE_VOLUME_RATIO", 2.0),
        pulse_stale_after_s=_env_int("DAYTRADE_PULSE_STALE_AFTER_S", 3600),
        pulse_foreign_source=_env_str("DAYTRADE_PULSE_FOREIGN_SOURCE", "off"),
        haircut_pp_per_side=_env_float("DAYTRADE_HAIRCUT_PP_PER_SIDE", 0.10),
        day_max_loss_r=_env_float("DAYTRADE_DAY_MAX_LOSS_R", 1.0),
        week_halt_r=_env_float("DAYTRADE_WEEK_HALT_R", -3.0),
        unlock_n=_env_int("DAYTRADE_UNLOCK_N", 20),
        cell_n=_env_int("DAYTRADE_CELL_N", 40),
        spec_version=_env_str("DAYTRADE_SPEC_VERSION", "v0.1"),
    )
