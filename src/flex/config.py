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
    # --- risk + sizing ---
    # Reconciled pair (risk-budget governs; per-name cap is a concentration backstop):
    # stops in the ~3.3%–4% band → risk budget binds (constant 0.40% dollar risk, ~10–12%
    # notional); tighter stops → the 12% per-name cap binds (capping concentration, risk
    # below budget). See size_flex_position + Flex_Catalyst_Engine_v1.0 spec.
    risk_budget_pct: float = 0.40      # % of equity risked per trade (dollar risk held constant)
    per_name_cap_pct: float = 12.0     # max notional per flex name, % of equity (concentration backstop)
    sleeve_cap_pct: float = 25.0       # aggregate flex cap, % of equity (a budget, not a target)
    # --- stop / trail ---
    atr_mult: float = 3.0              # stop + trail distance = atr_mult × ATR(14)
    max_stop_pct: float = 4.0          # skip entry if ATR stop is wider than this % of entry
    stop_epsilon_atr: float = 0.25     # min trail move (× ATR) before a cancel/replace is issued
    # --- exit ---
    time_stop_days: int = 5            # trading days before a non-working catalyst trade is cut
    first_target_r: float = 2.0        # first scale-out target, in R-multiples
    scale_out_fraction: float = 0.5    # fraction of qty_current sold at the first target
    # --- entry window + confirmation ---
    vwap_window_min: int = 30          # minutes after open before the VWAP hold is read / entry allowed
    entry_cutoff_min: int = 90         # minutes after open past which no NEW entries (management continues)
    gap_adr_mult: float = 2.0          # gap above this × ADR raises the confirmation bar (not auto-skip)
    # --- liquidity screen (tied to IEX-VWAP validity) ---
    min_adv_usd: float = 50_000_000.0  # min average daily dollar volume for entry

    def stop_epsilon(self, atr: float) -> float:
        """Absolute minimum stop move before a cancel/replace, in price units."""
        return self.stop_epsilon_atr * atr


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
        time_stop_days=_env_int("FLEX_TIME_STOP_DAYS", d.time_stop_days),
        first_target_r=_env_float("FLEX_FIRST_TARGET_R", d.first_target_r),
        scale_out_fraction=_env_float("FLEX_SCALE_OUT_FRACTION", d.scale_out_fraction),
        vwap_window_min=_env_int("FLEX_VWAP_WINDOW_MIN", d.vwap_window_min),
        entry_cutoff_min=_env_int("FLEX_ENTRY_CUTOFF_MIN", d.entry_cutoff_min),
        gap_adr_mult=_env_float("FLEX_GAP_ADR_MULT", d.gap_adr_mult),
        min_adv_usd=_env_float("FLEX_MIN_ADV_USD", d.min_adv_usd),
    )
