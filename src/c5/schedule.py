"""C5 schedule helpers (pure): sessions from Alpaca's calendar, month-ends, the near-close submission window. Never a hard-coded market
hour: every session's open and close come from `GET /v2/calendar` (which carries half-days), and the live state from `GET /v2/clock`.

Submission window (decided 2026-10-20, replacing market-on-close). Alpaca's PAPER account has no closing auction: a `cls` order is
treated as a market order at the close and can expire unfilled (forum.alpaca.markets/t/paper-moc-orders-partially-fill-remainder-
expires-same-setup-on-live-works/19155), and the first live C5 order did exactly that (2026-10-19: 1,421 SSO, expired 16:00:37, 0
filled). The engine now sends a plain market DAY order inside [close - 20 min, close - 2 min) of each session's OWN close. With the
15-minute tick that is exactly one tick: 15:45 on a 16:00 close, 12:45 on a 13:00 half-day (15:30 / 12:30 fall before the window).
If that tick is missed, the decision stays `decided` and executes in the next session's window (recorded as late).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SUBMIT_FROM = timedelta(minutes=20)               # the window opens this long before the session's close ...
SUBMIT_UNTIL = timedelta(minutes=2)               # ... and shuts this long before it, so a market order still meets an open market
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


def submit_opens(s: Session) -> datetime:
    return s.close - SUBMIT_FROM


def submit_deadline(s: Session) -> datetime:
    """After this, today's window is gone: a decision made now executes in the next session's window."""
    return s.close - SUBMIT_UNTIL


def in_submit_window(s: Session, now: datetime) -> bool:
    return submit_opens(s) <= now < submit_deadline(s)


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
