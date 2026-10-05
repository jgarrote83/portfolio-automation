"""Every ORB strategy parameter, in one frozen dataclass.

Rules (CLAUDE.md "ORB program" #2): strategy code reads its thresholds from here, and any threshold
change bumps `SPEC_VERSION`. Each field can be overridden with an `ORB_<FIELD_NAME>` environment
variable via `OrbConfig.from_env()`; the defaults are the Phase 2 pre-registered primary variant
(`docs/specs/ORB_Phase2_Preregistration.md`, section 6), and `tests/test_orb_prereg.py` pins the two
together.

`spec_version` is deliberately NOT env-overridable: an overridden config must never be able to
claim to be `orb-1.0`. `diff_from_default()` names every field that differs, for run headers.
"""
from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping
from dataclasses import dataclass

SPEC_VERSION = "orb-1.0"
ENV_PREFIX = "ORB_"
FULL_DAY_CLOSE_MIN = 16 * 60          # 16:00 ET, minutes since midnight


def hhmm_to_minutes(hhmm: str) -> int:
    """'09:35' -> 575."""
    hh, mm = hhmm.split(":")
    return int(hh) * 60 + int(mm)


@dataclass(frozen=True)
class OrbConfig:
    spec_version: str = SPEC_VERSION

    # --- universe -----------------------------------------------------------------------
    lookback_days: int = 14                 # prior trading days for ATR, average volume and RVOL
    min_price: float = 5.0                  # the day's open must exceed this
    min_avg_volume: float = 1_000_000       # prior-14-day average daily volume, shares
    min_atr: float = 0.50                   # prior-14-day simple-mean ATR, dollars
    exclude_etfs: bool = True

    # --- stocks in play -----------------------------------------------------------------
    rvol_min: float = 1.0                   # Relative Volume >= 100%
    top_n: int = 20
    opening_window_start: str = "09:30"
    opening_window_end: str = "09:35"

    # --- entry / exit -------------------------------------------------------------------
    entry_start: str = "09:35"              # first one-minute bar an entry order is live
    entry_cutoff_minutes_before_close: int = 15      # last entry bar: 15:45 on a full day
    exit_minutes_before_close: int = 1               # exit at the close of the 15:59 bar
    stop_atr_fraction: float = 0.10         # stop distance = 10% of ATR from the trigger price
    allow_shorts: bool = True
    tick_size: float = 0.01

    # --- sizing / risk ------------------------------------------------------------------
    sleeve_capital_usd: float = 25_000.0    # backtest: fixed, non-compounding (live: 25% of equity)
    risk_per_trade_pct: float = 1.0         # % of the sleeve risked per trade
    position_slots: int = 20                # each position <= sleeve / position_slots
    daily_loss_limit_pct: float = 3.0       # % of the sleeve

    # --- costs --------------------------------------------------------------------------
    commission_per_share: float = 0.0035
    slippage_cents_per_side: float = 2.0

    # ---------------------------------------------------------------------- derived values
    @property
    def opening_window_minutes(self) -> tuple[int, int]:
        return hhmm_to_minutes(self.opening_window_start), hhmm_to_minutes(self.opening_window_end)

    @property
    def entry_start_minute(self) -> int:
        return hhmm_to_minutes(self.entry_start)

    def last_entry_minute(self, close_minute: int = FULL_DAY_CLOSE_MIN) -> int:
        """Stamp of the last one-minute bar an entry may trigger in (15:45 on a full day)."""
        return close_minute - self.entry_cutoff_minutes_before_close

    def exit_minute(self, close_minute: int = FULL_DAY_CLOSE_MIN) -> int:
        """Stamp of the bar whose close is the end-of-day exit (15:59 on a full day)."""
        return close_minute - self.exit_minutes_before_close

    @property
    def slippage_per_share(self) -> float:
        return self.slippage_cents_per_side / 100.0

    @property
    def risk_budget_usd(self) -> float:
        return self.sleeve_capital_usd * self.risk_per_trade_pct / 100.0

    @property
    def max_position_usd(self) -> float:
        return self.sleeve_capital_usd / self.position_slots

    @property
    def daily_loss_limit_usd(self) -> float:
        """Positive dollars; the limit trips when sleeve P&L <= -this."""
        return self.sleeve_capital_usd * self.daily_loss_limit_pct / 100.0

    # --------------------------------------------------------------------------- overrides
    def replace(self, **changes) -> "OrbConfig":
        return dataclasses.replace(self, **changes)

    def diff_from_default(self) -> dict[str, object]:
        base = OrbConfig()
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self)
                if getattr(self, f.name) != getattr(base, f.name)}

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "OrbConfig":
        """Defaults overridden by `ORB_<FIELD>` variables. An unparseable value raises ValueError."""
        env = os.environ if environ is None else environ
        if f"{ENV_PREFIX}SPEC_VERSION" in env:
            raise ValueError("ORB_SPEC_VERSION cannot be overridden: a changed threshold needs a "
                             "new spec_version in code, not an environment variable")
        base = cls()
        changes: dict[str, object] = {}
        for f in dataclasses.fields(cls):
            if f.name == "spec_version":
                continue
            raw = env.get(f"{ENV_PREFIX}{f.name.upper()}")
            if raw is None or str(raw).strip() == "":
                continue
            changes[f.name] = _parse(f.name, raw, getattr(base, f.name))
        return dataclasses.replace(base, **changes) if changes else base


def _parse(name: str, raw: str, default):
    text = str(raw).strip()
    try:
        if isinstance(default, bool):          # bool before int: bool is an int subclass
            if text.lower() in ("1", "true", "yes", "on"):
                return True
            if text.lower() in ("0", "false", "no", "off"):
                return False
            raise ValueError(text)
        if isinstance(default, int):
            return int(text)
        if isinstance(default, float):
            return float(text)
        return text
    except ValueError as exc:
        raise ValueError(f"{ENV_PREFIX}{name.upper()}={raw!r} is not a valid "
                         f"{type(default).__name__}") from exc
