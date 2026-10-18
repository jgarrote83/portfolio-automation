"""C5 decision inputs (pure): month-end closes, the strict prior-day credit series, the decision and the daily preview. The tests
themselves live in `c5.signals` only; this module just assembles their inputs and FAILS CLOSED (`MissingInput`) on any gap.
"""
from __future__ import annotations

from datetime import date

from c5 import signals
from c5.schedule import Session, month_ends_until

SYMBOL = {signals.LEVERED: "SSO", signals.ONE_X: "SPY", signals.TBILLS: "BIL"}
INSTRUMENTS = frozenset(SYMBOL.values())
MAX_CREDIT_STALE_DAYS = 7


class MissingInput(ValueError):
    """An input C5 needs is absent or stale: no decision, no trade (fail closed)."""


def month_end_closes(closes: dict[date, float], ss: list[Session], day: date, months: int = signals.TREND_MONTHS) -> list[tuple[date, float]]:
    me = month_ends_until(ss, day)[-months:]
    if len(me) < months:
        raise MissingInput(f"only {len(me)} month-end sessions up to {day}; the trend test needs {months}")
    out = []
    for s in me:
        if s.day not in closes:
            raise MissingInput(f"no SPY close for the month-end {s.day}")
        out.append((s.day, closes[s.day]))
    return out


def credit_series(obs: list[tuple[date, float]], ss: list[Session], day: date, window: int = signals.CREDIT_WINDOW) -> list[tuple[date, float, date]]:
    """For each of the `window` sessions up to and including `day`: (session, value, the observation's date), the observation being the
    latest one dated STRICTLY BEFORE the session (signals.credit_value_before). Stale (> 7 calendar days) or missing -> MissingInput."""
    obs = sorted((d, v) for d, v in obs if v == v)
    dates, vals = [d for d, _ in obs], [v for _, v in obs]
    days = [s.day for s in ss if s.day <= day][-window:]
    if len(days) < window:
        raise MissingInput(f"only {len(days)} sessions up to {day}; the credit test needs {window}")
    out = []
    for d in days:
        got = signals.credit_value_before(dates, vals, d)
        if got is None:
            raise MissingInput(f"no BAA10Y observation before {d}")
        v, od = got
        if (d - od).days > MAX_CREDIT_STALE_DAYS:
            raise MissingInput(f"the latest BAA10Y before {d} is dated {od} (stale)")
        out.append((d, v, od))
    return out


def decide(day: date, closes: dict[date, float], ss: list[Session], baa: list[tuple[date, float]]) -> dict:
    """The decision at the close of month-end session `day`."""
    me = month_end_closes(closes, ss, day)
    if me[-1][0] != day:
        raise MissingInput(f"{day} is not a month-end session")
    cs = credit_series(baa, ss, day)
    return _result(day, [c for _, c in me], cs, label="decision") | {"month_end_closes": [(d.isoformat(), c) for d, c in me]}


def preview(day: date, closes: dict[date, float], ss: list[Session], baa: list[tuple[date, float]]) -> dict:
    """'If the month ended today': the previous 9 month-end closes plus today's close, and today's credit test. A PREVIEW, never a decision."""
    if day not in closes:
        raise MissingInput(f"no SPY close for {day}")
    prior = [x for x in month_ends_until(ss, day) if x.day < day][-(signals.TREND_MONTHS - 1):]
    if len(prior) < signals.TREND_MONTHS - 1:
        raise MissingInput("not enough month-end closes for the preview")
    series = []
    for s in prior:
        if s.day not in closes:
            raise MissingInput(f"no SPY close for the month-end {s.day}")
        series.append(closes[s.day])
    cs = credit_series(baa, ss, day)
    return _result(day, series + [closes[day]], cs, label="preview") | {
        "note": "If the month ended today (preview, not a decision)."}


def _result(day: date, trend_closes: list[float], cs: list[tuple[date, float, date]], label: str) -> dict:
    t = signals.trend_healthy(trend_closes)
    c = signals.credit_calm([v for _, v, _ in cs])
    h = signals.holding(t, c)
    sma = sum(trend_closes[-signals.TREND_MONTHS:]) / signals.TREND_MONTHS
    avg = sum(v for _, v, _ in cs) / len(cs)
    return {"kind": label, "date": day.isoformat(),
            "trend": {"spy_close": trend_closes[-1], "sma10": sma, "distance_pct": (trend_closes[-1] / sma - 1.0) * 100.0, "ok": t},
            "credit": {"baa10y": cs[-1][1], "baa10y_date": cs[-1][2].isoformat(), "avg126": avg, "ok": c},
            "holding": h, "target": SYMBOL[h]}
