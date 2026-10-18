"""C5 schedule helpers (pure): sessions from Alpaca's calendar, month-ends, the MOC cutoff. Never a hard-coded market hour: every
session's open and close come from `GET /v2/calendar` (which carries half-days), and the live state from `GET /v2/clock`.

MOC cutoff. Alpaca, "Orders at Alpaca" (docs.alpaca.markets/docs/orders-at-alpaca), time in force `cls`: "CLS orders submitted after
3:50pm but before 7:00pm ET will be rejected." That is ten minutes before a 16:00 close; the page does not address early-close days, so
the engine applies the same ten-minute lead to each session's own close (12:50 on a 13:00 half-day) and, in practice, submits shortly
after the open, far from either.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MOC_LEAD = timedelta(minutes=10)
SETTLE_AFTER_CLOSE = timedelta(minutes=20)        # the daily bar is final and past the free plan's 15-minute SIP delay


@dataclass(frozen=True)
class Session:
    day: date
    open: datetime
    close: datetime


def _hm(s: str) -> time:
    h, m = s.split(":")[:2]
    return time(int(h), int(m))


def sessions(calendar_rows: list[dict]) -> list[Session]:
    """Alpaca calendar rows `{date: 'YYYY-MM-DD', open: 'HH:MM', close: 'HH:MM'}` (New York time) -> sessions, ascending."""
    out = []
    for r in calendar_rows:
        d = date.fromisoformat(r["date"])
        out.append(Session(d, datetime.combine(d, _hm(r["open"]), ET), datetime.combine(d, _hm(r["close"]), ET)))
    return sorted(out, key=lambda s: s.day)


def session_on(ss: list[Session], d: date) -> Session | None:
    return next((s for s in ss if s.day == d), None)


def next_session(ss: list[Session], d: date) -> Session | None:
    return next((s for s in ss if s.day > d), None)


def is_month_end(ss: list[Session], d: date) -> bool:
    """d is a session and the next session falls in another month. Raises if the calendar does not reach past d."""
    if session_on(ss, d) is None:
        return False
    nxt = next_session(ss, d)
    if nxt is None:
        raise ValueError(f"the calendar ends at {d}; cannot tell whether it is a month-end")
    return (nxt.day.year, nxt.day.month) != (d.year, d.month)


def moc_cutoff(s: Session) -> datetime:
    return s.close - MOC_LEAD


def settled(s: Session, now: datetime) -> bool:
    return now >= s.close + SETTLE_AFTER_CLOSE


def last_completed_month_end(ss: list[Session], now: datetime) -> Session | None:
    """The latest month-end session whose close has settled by `now`."""
    best = None
    for s in ss:
        if s.close + SETTLE_AFTER_CLOSE > now:
            break
        if next_session(ss, s.day) is not None and is_month_end(ss, s.day):
            best = s
    return best


def month_ends_until(ss: list[Session], d: date) -> list[Session]:
    return [s for s in ss if s.day <= d and next_session(ss, s.day) is not None and is_month_end(ss, s.day)]


def yyyymm(d: date) -> str:
    return f"{d.year:04d}{d.month:02d}"
