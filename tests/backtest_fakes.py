"""Deterministic fake Alpaca market served through a fake HTTP session (no network, no keys).

The REAL `AlpacaDataClient` runs against `FakeSession`, so pagination, retry, the rate limiter and
URL construction are all exercised. Not a data source for any real analysis.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
ASSETS_URL = "https://paper-api.alpaca.markets/v2/assets"


def utc_iso(dt_et: datetime) -> str:
    return dt_et.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class FakeClock:
    """Injectable clock + sleep: sleeping just advances time."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


class FakeResponse:
    def __init__(self, status: int, body, headers: dict | None = None) -> None:
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self):
        return self._body


class FakeMarket:
    """Daily / 1Min / 5Min bars for any symbol on every weekday not in `holidays`."""

    def __init__(self, holidays=(), halted=(), page_size: int = 10_000, intraday_fn=None,
                 daily_fn=None) -> None:
        self.holidays = {date.fromisoformat(h) if isinstance(h, str) else h for h in holidays}
        self.halted = {(s, date.fromisoformat(d) if isinstance(d, str) else d) for s, d in halted}
        self.page_size = page_size
        self.intraday_fn = intraday_fn
        self.daily_fn = daily_fn
        self.assets = {"active": [], "inactive": []}

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    @staticmethod
    def _idx(sym: str) -> int:
        return sum(ord(c) for c in sym) % 50

    def _daily(self, sym: str, d: date) -> dict:
        if self.daily_fn:
            o, h, lo, c, v = self.daily_fn(sym, d)
        else:
            o = 100.0 + self._idx(sym) + d.day % 5
            o, h, lo, c, v = o, o + 2.0, o - 1.5, o + 0.5, 2_000_000 + self._idx(sym) * 1000
        t = datetime(d.year, d.month, d.day, tzinfo=ET)
        return {"t": utc_iso(t), "o": o, "h": h, "l": lo, "c": c, "v": v}

    def _intraday(self, sym: str, tf: str, feed: str, d: date) -> list[dict]:
        step = 1 if tf == "1Min" else 5
        out = []
        for m in range(0, 390, step):
            t = datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET) + timedelta(minutes=m)
            if self.intraday_fn:
                o, h, lo, c, v = self.intraday_fn(feed, sym, d, m)
            else:
                o = 100.0 + self._idx(sym) + m * 0.01
                o, h, lo, c = o, o + 0.2, o - 0.2, o + (0.1 if feed == "sip" else 0.05)
                v = (1000 if feed == "sip" else 300) + m
            out.append({"t": utc_iso(t), "o": o, "h": h, "l": lo, "c": c, "v": v})
        return out

    def query(self, params: dict) -> dict:
        syms = [s for s in params["symbols"].split(",") if s]
        tf, feed = params["timeframe"], params["feed"]
        start, end = _parse(params["start"]), _parse(params["end"])
        rows: list[tuple[str, dict]] = []
        d = start.astimezone(ET).date()
        last = end.astimezone(ET).date()
        while d <= last:
            if self.is_trading_day(d):
                for sym in sorted(syms):
                    if (sym, d) in self.halted:
                        continue
                    bars = [self._daily(sym, d)] if tf == "1Day" else self._intraday(sym, tf, feed, d)
                    rows.extend((sym, b) for b in bars if start <= _parse(b["t"]) <= end)
            d += timedelta(days=1)
        rows.sort(key=lambda r: (r[0], r[1]["t"]))
        offset = int(params.get("page_token") or 0)
        page = rows[offset: offset + min(self.page_size, int(params.get("limit", 10_000)))]
        nxt = offset + len(page)
        out: dict[str, list[dict]] = {}
        for sym, b in page:
            out.setdefault(sym, []).append(b)
        return {"bars": out, "next_page_token": str(nxt) if nxt < len(rows) else None}


class FakeSession:
    def __init__(self, market: FakeMarket, script: list | None = None) -> None:
        self.market = market
        self.script = list(script or [])          # statuses (or (status, headers)) returned first
        self.calls: list[dict] = []

    @property
    def bar_calls(self) -> list[dict]:
        return [c for c in self.calls if c["url"] == BARS_URL]

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "method": "GET"})
        if self.script:
            item = self.script.pop(0)
            status, hdrs = item if isinstance(item, tuple) else (item, {})
            return FakeResponse(status, {"message": "scripted failure"}, hdrs)
        if url == BARS_URL:
            return FakeResponse(200, self.market.query(params))
        if url == ASSETS_URL:
            return FakeResponse(200, self.market.assets.get(params["status"], []))
        return FakeResponse(404, {"message": "not found"})


class RejectingSession(FakeSession):
    """Mimics the real bars endpoint: ONE unknown symbol in a multi-symbol request fails the whole
    request with HTTP 400 `invalid symbol: X` (naming only the first offender)."""

    def __init__(self, market, bad, **kw):
        super().__init__(market, **kw)
        self.bad = set(bad)

    def get(self, url, params=None, headers=None, timeout=None):
        if url == BARS_URL:
            asked = [s for s in params["symbols"].split(",") if s]
            offenders = [s for s in asked if s in self.bad]
            if offenders:
                self.calls.append({"url": url, "params": dict(params), "method": "GET"})
                return FakeResponse(400, {"message": f"invalid symbol: {offenders[0]}"})
        return super().get(url, params=params, headers=headers, timeout=timeout)


NEWS_URL = "https://data.alpaca.markets/v1beta1/news"


class FakeNews:
    """A fixed list of news articles served like `GET /v1beta1/news`: filtered to [start, end], oldest first,
    paged by `page_size` with an opaque `page_token` (the offset)."""

    def __init__(self, articles: list[dict], page_size: int = 3) -> None:
        self.articles = sorted(articles, key=lambda a: (a["created_at"], a["id"]))
        self.page_size = page_size

    def query(self, params: dict) -> dict:
        start, end = _parse(params["start"]), _parse(params["end"])
        rows = [a for a in self.articles if start <= _parse(a["created_at"]) <= end]
        offset = int(params.get("page_token") or 0)
        limit = min(self.page_size, int(params.get("limit", 50)))
        page = rows[offset: offset + limit]
        nxt = offset + len(page)
        return {"news": page, "next_page_token": str(nxt) if nxt < len(rows) else None}


def article(i: int, created_at: str, symbols=("AAA",), headline: str | None = None, updated_at: str | None = None,
            source: str = "benzinga") -> dict:
    return {"id": i, "headline": headline or f"headline {i}", "summary": "s", "author": "a", "url": "u",
            "images": [], "content": "", "created_at": created_at, "updated_at": updated_at or created_at,
            "symbols": list(symbols), "source": source}


class NewsSession(FakeSession):
    """FakeSession that also serves the news endpoint (bars and assets still work)."""

    def __init__(self, market: FakeMarket, news: FakeNews, script: list | None = None) -> None:
        super().__init__(market, script)
        self.news = news

    @property
    def news_calls(self) -> list[dict]:
        return [c for c in self.calls if c["url"] == NEWS_URL]

    def get(self, url, params=None, headers=None, timeout=None):
        if url == NEWS_URL:
            self.calls.append({"url": url, "params": dict(params or {}), "method": "GET"})
            if self.script:
                item = self.script.pop(0)
                status, hdrs = item if isinstance(item, tuple) else (item, {})
                return FakeResponse(status, {"message": "scripted failure"}, hdrs)
            return FakeResponse(200, self.news.query(params))
        return super().get(url, params=params, headers=headers, timeout=timeout)
