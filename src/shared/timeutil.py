"""ET-local date helpers (#29 — the auto-exec chain's latent UTC-date bug).

Blob paths (`daily-trades/{date}`, `daily-snapshots/{date}`, …) are keyed by the
TRADING date, which is Eastern Time. Computing "today" in UTC coincides with ET only
until ~19:00/20:00 ET — an evening timer (or any retry added naively) rolls the UTC
date and reads tomorrow's empty file forever. Blob-path dates must always derive
from America/New_York, independent of the host clock or the `TZ` app setting.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def now_et(now: datetime | None = None) -> datetime:
    """Current time in America/New_York. A naive `now` is assumed UTC — the safe
    reading for host-clock callers."""
    if now is None:
        return datetime.now(tz=ET)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ET)


def today_et(now: datetime | None = None) -> str:
    """YYYY-MM-DD in America/New_York — the trading date for blob paths."""
    return now_et(now).strftime("%Y-%m-%d")
