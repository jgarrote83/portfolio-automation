"""Read-only probe of Alpaca's historical options data (report only; NO strategy code).

    PYTHONPATH=src python -m backtest.options_probe run [--review-file .review/options-data-probe.md]

Question: is free Alpaca options data good enough to backtest a defined-risk put-spread wheel honestly (bid/ask, or at least
dense daily prices, for both legs)? Everything here is a GET:

  * contracts   GET {paper host}/v2/options/contracts          (listing, incl. status=inactive / expired)
  * bars        GET {data host}/v1beta1/options/bars            (daily bars)
  * trades      GET {data host}/v1beta1/options/trades          (one sample, to see what the plan serves)
  * quotes      GET {data host}/v1beta1/options/quotes          (historical quotes, if the endpoint exists on this plan)

Keys come from the gitignored `.env` through `backtest.data.credentials` and are never printed or logged; the probe makes no
POST/PUT/DELETE and never touches an order, account or position endpoint. 2026 is never queried (the last date probed is
2025-12-31 and every expiry window is clipped to it). A 401/403 is recorded and reported, never worked around.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

from .data.config import HOLDOUT_START, HTTP_TIMEOUT_S, REPO_ROOT
from .data.credentials import load_credentials
from .data.ratelimit import RateLimiter

TRADING_URL = "https://paper-api.alpaca.markets"            # read-only GETs of the contracts list only (paper host, repo rule 7)
DATA_URL = "https://data.alpaca.markets"
CONTRACTS_PATH = "/v2/options/contracts"
BARS_PATH = "/v1beta1/options/bars"
TRADES_PATH = "/v1beta1/options/trades"
QUOTES_PATH = "/v1beta1/options/quotes"
UNDERLYINGS = ("QQQ", "SPY")
PROBE_START, PROBE_END = date(2024, 1, 2), date(2025, 12, 31)
N_DATES = 20
DTE_LO, DTE_HI = 25, 35
BELOW_SPOT = 0.15
BARS_CHUNK = 100
OCC = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")
BUCKETS = ("0-5% below spot", "5-10% below spot", "10-15% below spot")
_RETRYABLE = {429, 500, 502, 503, 504}


# ===================================================================================== pure helpers
def occ_parse(symbol: str) -> dict | None:
    """OCC option symbol -> {root, expiration, type, strike}, or None if it does not parse."""
    m = OCC.match(symbol or "")
    if not m:
        return None
    yy, mm, dd = int(m.group(2)[:2]), int(m.group(2)[2:4]), int(m.group(2)[4:])
    try:
        exp = date(2000 + yy, mm, dd)
    except ValueError:
        return None
    return {"root": m.group(1), "expiration": exp, "type": "put" if m.group(3) == "P" else "call", "strike": int(m.group(4)) / 1000.0}


def evenly_spaced(days: Sequence[date], n: int) -> list[date]:
    """`n` dates evenly spaced over `days` (first and last included), without duplicates."""
    if not days:
        return []
    idx = sorted({int(round(x)) for x in np.linspace(0, len(days) - 1, min(n, len(days)))})
    return [days[i] for i in idx]


def expiry_window(day: date, lo: int = DTE_LO, hi: int = DTE_HI) -> tuple[date, date]:
    return day + timedelta(days=lo), day + timedelta(days=hi)


def strike_bounds(spot: float, below: float = BELOW_SPOT) -> tuple[float, float]:
    """Strikes from `below` under the spot up to the spot itself (inclusive), rounded to the cent."""
    return round(spot * (1 - below), 2), round(spot, 2)


def bucket_of(spot: float, strike: float) -> str:
    d = (spot - strike) / spot
    return BUCKETS[0] if d < 0.05 else BUCKETS[1] if d < 0.10 else BUCKETS[2]


def third_friday(year: int, month: int) -> date:
    """The standard monthly expiration: the third Friday of the month."""
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def most_liquid(contracts: Sequence[dict], n: int = BARS_CHUNK) -> list[str]:
    """The `n` OCC symbols with the highest open interest (a missing or unparsable value counts as 0); ties by symbol."""
    def oi(c):
        try:
            return float(c.get("open_interest") or 0)
        except (TypeError, ValueError):
            return 0.0
    return [c["symbol"] for c in sorted((c for c in contracts if c.get("symbol")), key=lambda c: (-oi(c), c["symbol"]))[:n]]


def chunks(items: Sequence, n: int) -> Iterable[list]:
    for i in range(0, len(items), n):
        yield list(items[i:i + n])


def day_bounds_utc(day: date) -> tuple[str, str]:
    """RFC3339 start/end spanning the US/Eastern calendar day's daily bar (stamped at ET midnight, 04:00/05:00 UTC)."""
    return f"{day.isoformat()}T00:00:00Z", f"{day.isoformat()}T23:59:59Z"


def coverage_table(rows: Sequence[dict]) -> dict:
    """rows: {symbol, spot, strike, has_bar, has_quote (optional)} -> counts and shares overall and by distance-below-spot bucket."""
    out = {"all": {"contracts": 0, "with_bar": 0, "with_quote": 0}, **{b: {"contracts": 0, "with_bar": 0, "with_quote": 0} for b in BUCKETS}}
    for r in rows:
        for key in ("all", bucket_of(r["spot"], r["strike"])):
            out[key]["contracts"] += 1
            out[key]["with_bar"] += int(bool(r.get("has_bar")))
            out[key]["with_quote"] += int(bool(r.get("has_quote")))
    for v in out.values():
        v["bar_share"] = (v["with_bar"] / v["contracts"]) if v["contracts"] else None
        v["quote_share"] = (v["with_quote"] / v["contracts"]) if v["contracts"] else None
    return out


# ============================================================================== read-only HTTP
class Probe:
    """GET-only client. Every call is logged (endpoint, parameters, status, a short non-secret note); keys never are."""

    def __init__(self, key: str, secret: str, *, session: requests.Session | None = None, limiter: RateLimiter | None = None,
                 sleep: Callable[[float], None] = time.sleep, max_retries: int = 4) -> None:
        self.__headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Accept": "application/json"}
        self.session = session or requests.Session()
        self.limiter = limiter or RateLimiter(150, 5, sleep=sleep)
        self.sleep, self.max_retries = sleep, max_retries
        self.log: list[dict] = []

    def __repr__(self) -> str:
        return f"Probe(requests={len(self.log)})"

    @staticmethod
    def _summarise(params: dict) -> dict:
        out = {}
        for k, v in params.items():
            if k == "symbols" and isinstance(v, str) and "," in v:
                out[k] = f"<{v.count(',') + 1} OCC symbols, first {v.split(',')[0]}>"
            else:
                out[k] = v
        return out

    def get(self, base: str, path: str, params: dict) -> tuple[int, object]:
        """-> (HTTP status, parsed JSON or {}). Retries 429/5xx; never retries or works around a 4xx."""
        url = f"{base}{path}"
        attempt = 0
        while True:
            self.limiter.acquire()
            try:
                resp = self.session.get(url, params=params, headers=self.__headers, timeout=HTTP_TIMEOUT_S)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt >= self.max_retries:
                    self.log.append({"path": path, "params": self._summarise(params), "status": 0, "note": type(exc).__name__})
                    return 0, {}
                self.sleep(min(30.0, 2.0 ** attempt))
                attempt += 1
                continue
            if resp.status_code in _RETRYABLE and attempt < self.max_retries:
                self.sleep(min(30.0, 2.0 ** attempt))
                attempt += 1
                continue
            try:
                body = resp.json()
            except ValueError:
                body = {}
            note = ""
            if resp.status_code != 200 and isinstance(body, dict):
                note = str(body.get("message", ""))[:200]
            self.log.append({"path": path, "params": self._summarise(params), "status": resp.status_code, "note": note})
            return resp.status_code, body


# ====================================================================================== contracts
def list_contracts(p: Probe, underlying: str, *, max_pages: int | None = None, **filters) -> tuple[int, list[dict]]:
    """Contracts matching the filters (pages of up to 10,000; at most `max_pages` pages if given). -> (last status, contracts)."""
    out: list[dict] = []
    params = {"underlying_symbols": underlying, "limit": 10000, **filters}
    token = None
    pages = 0
    while True:
        page = dict(params)
        if token:
            page["page_token"] = token
        status, body = p.get(TRADING_URL, CONTRACTS_PATH, page)
        if status != 200 or not isinstance(body, dict):
            return status, out
        out += body.get("option_contracts") or []
        pages += 1
        token = body.get("next_page_token")
        if not token or (max_pages is not None and pages >= max_pages):
            return status, out


def any_contract(p: Probe, underlying: str, lo: date, hi: date, status: str) -> tuple[int, bool]:
    st, body = p.get(TRADING_URL, CONTRACTS_PATH, {"underlying_symbols": underlying, "status": status, "expiration_date_gte": lo.isoformat(),
                                                   "expiration_date_lte": hi.isoformat(), "limit": 1})
    return st, bool(st == 200 and isinstance(body, dict) and (body.get("option_contracts") or []))


def earliest_expiration(p: Probe, underlying: str, status: str, first_year: int = 2016, last_year: int = 2025) -> dict:
    """The earliest expiration the contracts endpoint lists for this underlying and status, found by narrowing year -> month -> day
    with limit-1 existence queries. -> {earliest, first_status, queries}."""
    n0 = len(p.log)
    first_status = None
    for y in range(first_year, last_year + 1):
        st, hit = any_contract(p, underlying, date(y, 1, 1), date(y, 12, 31), status)
        first_status = first_status or st
        if st != 200:
            return {"earliest": None, "first_status": st, "queries": len(p.log) - n0}
        if hit:
            for m in range(1, 13):
                lo = date(y, m, 1)
                hi = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
                if any_contract(p, underlying, lo, hi, status)[1]:
                    d = lo
                    while d <= hi:
                        if any_contract(p, underlying, d, d, status)[1]:
                            return {"earliest": d.isoformat(), "first_status": first_status, "queries": len(p.log) - n0}
                        d += timedelta(days=1)
    return {"earliest": None, "first_status": first_status, "queries": len(p.log) - n0}


# ========================================================================================== bars
def bars_for_day(p: Probe, symbols: Sequence[str], day: date, **extra) -> tuple[dict[str, int], list[int]]:
    """Daily bars dated `day` for the symbols (chunks of 100). -> ({symbol: bars returned}, statuses of the requests)."""
    start, end = day_bounds_utc(day)
    got: dict[str, int] = {}
    statuses: list[int] = []
    for chunk in chunks(list(symbols), BARS_CHUNK):
        token = None
        while True:
            params = {"symbols": ",".join(chunk), "timeframe": "1Day", "start": start, "end": end, "limit": 10000, **extra}
            if token:
                params["page_token"] = token
            st, body = p.get(DATA_URL, BARS_PATH, params)
            statuses.append(st)
            if st != 200 or not isinstance(body, dict):
                break
            for sym, rows in (body.get("bars") or {}).items():
                got[sym] = got.get(sym, 0) + len(rows or [])
            token = body.get("next_page_token")
            if not token:
                break
    return got, statuses


def first_bar_date(p: Probe, symbols: Sequence[str], lo: date, hi: date, **extra) -> tuple[date | None, int]:
    """The earliest bar date among `symbols` between lo and hi (one chunk of up to 100 symbols). -> (date or None, status)."""
    st, body = p.get(DATA_URL, BARS_PATH, {"symbols": ",".join(symbols[:BARS_CHUNK]), "timeframe": "1Day", "start": f"{lo.isoformat()}T00:00:00Z",
                                           "end": f"{hi.isoformat()}T23:59:59Z", "limit": 10000, "sort": "asc", **extra})
    if st != 200 or not isinstance(body, dict):
        return None, st
    ts = [r.get("t", "")[:10] for rows in (body.get("bars") or {}).values() for r in (rows or []) if r.get("t")]
    return (date.fromisoformat(min(ts)) if ts else None), st


def bars_range(p: Probe, symbols: Sequence[str], lo: date, hi: date) -> tuple[dict[str, set[str]], list[int]]:
    """Dates (ISO, from the bar's own timestamp) on which each symbol has a daily bar between lo and hi inclusive. -> ({symbol: {dates}}, statuses)."""
    out: dict[str, set[str]] = {}
    statuses: list[int] = []
    for chunk in chunks(list(symbols), BARS_CHUNK):
        token = None
        while True:
            params = {"symbols": ",".join(chunk), "timeframe": "1Day", "start": f"{lo.isoformat()}T00:00:00Z", "end": f"{hi.isoformat()}T23:59:59Z",
                      "limit": 10000, "sort": "asc"}
            if token:
                params["page_token"] = token
            st, body = p.get(DATA_URL, BARS_PATH, params)
            statuses.append(st)
            if st != 200 or not isinstance(body, dict):
                break
            for sym, rows in (body.get("bars") or {}).items():
                out.setdefault(sym, set()).update(r["t"][:10] for r in (rows or []) if r.get("t"))
            token = body.get("next_page_token")
            if not token:
                break
    return out, statuses


def continuity_sample(p: Probe, underlying: str, day: date, spot: float, after_days: Sequence[date]) -> dict:
    """SUPPLEMENT (not one of the instruction's four questions). Of the put contracts in the coverage set that DID have a bar on `day`, how
    continuously do they print on the following trading days up to their own expiry? A defined-risk position held toward expiry needs a mark
    most days, and this is the best case (it conditions on the contract having traded on the entry date). It also asks the trades endpoint for
    three of those contracts, to see what a trade print carries (exchange and condition codes)."""
    lo, hi = expiry_window(day)
    hi = min(hi, PROBE_END)
    k_lo, k_hi = strike_bounds(spot)
    _st, cons = list_contracts(p, underlying, status="inactive", type="put", expiration_date_gte=lo.isoformat(), expiration_date_lte=hi.isoformat(),
                               strike_price_gte=k_lo, strike_price_lte=k_hi)
    syms = [c["symbol"] for c in cons if c.get("symbol")]
    got, _ = bars_for_day(p, syms, day)
    traded = [s for s in syms if got.get(s)]
    after = [d for d in after_days if d > day][:20]
    out = {"underlying": underlying, "day": day.isoformat(), "spot": spot, "contracts": len(syms), "traded_on_day": len(traded), "after_days": len(after)}
    if not traded or not after:
        return {**out, "note": "no contract traded that day or no later day in range"}
    rng, _sts = bars_range(p, traded, after[0], after[-1])
    shares, next_day, ge_half, zero = [], 0, 0, 0
    for s in traded:
        meta = occ_parse(s)
        possible = [d for d in after if d <= meta["expiration"]] if meta else after
        have = [d for d in possible if d.isoformat() in rng.get(s, set())]
        shares.append(len(have) / len(possible) if possible else 0.0)
        next_day += int(after[0].isoformat() in rng.get(s, set()))
        ge_half += int(bool(possible) and len(have) >= len(possible) / 2)
        zero += int(not have)
    out.update({"next_day_share": next_day / len(traded), "median_share_of_days_with_a_bar": float(np.median(shares)),
                "mean_share_of_days_with_a_bar": float(np.mean(shares)), "share_with_bars_on_half_the_days": ge_half / len(traded),
                "share_never_again": zero / len(traded)})
    sample = []
    for s in traded[:3]:
        start, end = day_bounds_utc(day)
        st, body = p.get(DATA_URL, TRADES_PATH, {"symbols": s, "start": start, "end": end, "limit": 1000})
        rows = (body.get("trades") or {}).get(s, []) if st == 200 and isinstance(body, dict) else []
        sample.append({"symbol": s, "status": st, "rows": len(rows), "fields": sorted(rows[0].keys()) if rows else [],
                       "exchanges": sorted({r.get("x") for r in rows if r.get("x")}), "conditions": sorted({c for r in rows for c in (r.get("c") or "")})})
    out["trades_sample"] = sample
    return out


# ===================================================================================== the probe
def run_probe(p: Probe, spots: dict[str, dict[date, float]], days: Sequence[date], *, all_days: Sequence[date] | None = None,
              log: Callable[[str], None] = lambda m: None) -> dict:
    """Everything the report needs. `spots[underlying][day]` is the US close that day (from SIP daily bars). No 2026 date is ever used."""
    assert all(d < HOLDOUT_START for d in days), "the probe never queries 2026"
    res: dict = {"contracts": {}, "coverage": {}, "quotes": {}, "trades": {}, "depth": {}, "feeds": {}, "continuity": []}

    # ---- 1) contracts: can expired contracts be listed, and how far back? --------------------------------------------
    for u in UNDERLYINGS:
        entry = {}
        for status in ("inactive", "active"):
            entry[status] = earliest_expiration(p, u, status)
            log(f"{u} {status}: earliest listed expiration {entry[status]['earliest']}")
        st, rows = list_contracts(p, u, status="inactive", type="put", expiration_date_gte="2024-01-01", expiration_date_lte="2025-12-31")
        years = Counter(occ_parse(r.get("symbol", ""))["expiration"].year for r in rows if occ_parse(r.get("symbol", "")))
        entry["expired_puts_2024_2025"] = {"status": st, "listed": len(rows), "by_year": dict(sorted(years.items())),
                                           "fields": sorted(rows[0].keys()) if rows else []}
        res["contracts"][u] = entry
        log(f"{u}: {len(rows)} expired puts listed with expiry in 2024-2025 (HTTP {st})")

    # ---- 2) coverage on 20 evenly spaced dates --------------------------------------------------------------------------------
    sample_quote_symbols: list[tuple[str, date]] = []
    for u in UNDERLYINGS:
        per_date, all_rows = [], []
        for d in days:
            spot = spots[u].get(d)
            lo, hi = expiry_window(d)
            hi = min(hi, PROBE_END)
            if spot is None or lo > hi:
                per_date.append({"day": d.isoformat(), "spot": spot, "contracts": 0, "skipped": True})
                continue
            k_lo, k_hi = strike_bounds(spot)
            st, cons = list_contracts(p, u, status="inactive", type="put", expiration_date_gte=lo.isoformat(), expiration_date_lte=hi.isoformat(),
                                      strike_price_gte=k_lo, strike_price_lte=k_hi)
            syms = [c["symbol"] for c in cons if c.get("symbol")]
            got, sts = bars_for_day(p, syms, d)
            rows = [{"symbol": c["symbol"], "spot": spot, "strike": float(c["strike_price"]), "has_bar": got.get(c["symbol"], 0) > 0} for c in cons if c.get("symbol")]
            all_rows += rows
            if syms and len(sample_quote_symbols) < 6:
                sample_quote_symbols.append((syms[len(syms) // 2], d))
            expiries = sorted({occ_parse(s)["expiration"] for s in syms if occ_parse(s)})
            per_date.append({"day": d.isoformat(), "spot": spot, "contracts": len(rows), "with_bar": sum(r["has_bar"] for r in rows),
                             "expiries": len(expiries), "contract_status": st, "bar_statuses": dict(Counter(sts))})
            log(f"{u} {d}: {len(rows)} contracts, {sum(r['has_bar'] for r in rows)} with a daily bar")
        res["coverage"][u] = {"per_date": per_date, "table": coverage_table(all_rows)}

    # ---- 3) quotes and trades endpoints: do they exist on this plan, and what do they return? ----------------------------
    for sym, d in sample_quote_symbols[:3]:
        start, end = day_bounds_utc(d)
        qs, qb = p.get(DATA_URL, QUOTES_PATH, {"symbols": sym, "start": start, "end": end, "limit": 10})
        ts, tb = p.get(DATA_URL, TRADES_PATH, {"symbols": sym, "start": start, "end": end, "limit": 10})
        res["quotes"][f"{sym} {d}"] = {"status": qs, "rows": sum(len(v or []) for v in (qb.get("quotes") or {}).values()) if isinstance(qb, dict) else 0,
                                       "message": (qb.get("message") if isinstance(qb, dict) else None)}
        res["trades"][f"{sym} {d}"] = {"status": ts, "rows": sum(len(v or []) for v in (tb.get("trades") or {}).values()) if isinstance(tb, dict) else 0,
                                       "message": (tb.get("message") if isinstance(tb, dict) else None)}

    # ---- 3b) supplement: continuity of marks for contracts that traded on the entry date (best case), on the best and the median coverage date ----
    if all_days:
        for u in UNDERLYINGS:
            usable = [r for r in res["coverage"][u]["per_date"] if not r.get("skipped") and r["contracts"] and r["day"] >= "2024-02-01"]
            ranked = sorted(usable, key=lambda r: (r["with_bar"] / r["contracts"], r["day"]))
            picks = (("best coverage date", ranked[-1]), ("median coverage date", ranked[len(ranked) // 2])) if ranked else ()
            for label, r in picks:
                d = date.fromisoformat(r["day"])
                cs = continuity_sample(p, u, d, spots[u][d], all_days)
                cs["selected_as"] = label
                res["continuity"].append(cs)
                log(f"{u} {d} ({label}): {cs.get('traded_on_day')} traded that day; median share of later days with a bar {cs.get('median_share_of_days_with_a_bar')}")

    # ---- 4) depth: the earliest date with any historical options bars ---------------------------------------------------------
    # For the March and September monthly expiry (the third Friday) of every year from 2016, take the 100 contracts with the highest open
    # interest and ask for their daily bars from 120 days before the expiry; the first year/month that returns any bar bounds the start.
    for u in UNDERLYINGS:
        first = None
        for y in range(2016, 2026):
            for month in (3, 9):
                exp = third_friday(y, month)
                st, cons = list_contracts(p, u, max_pages=1, status="inactive", expiration_date_gte=exp.isoformat(), expiration_date_lte=exp.isoformat())
                syms = most_liquid(cons)
                if not syms:
                    continue
                fb, st2 = first_bar_date(p, syms, exp - timedelta(days=120), exp)
                if fb:
                    first = {"first_bar_seen": fb.isoformat(), "from_contracts_expiring": exp.isoformat(), "status": st2}
                    break
            if first:
                break
        res["depth"][u] = first or {"first_bar_seen": None}
        log(f"{u}: earliest option bar seen {res['depth'][u].get('first_bar_seen')}")

    # ---- 5) feed: does the bars endpoint accept an explicit feed? ---------------------------------------------------------------
    sym = next((s for s, _d in sample_quote_symbols), None)
    if sym:
        d = next(d for s, d in sample_quote_symbols if s == sym)
        start, end = day_bounds_utc(d)
        for feed in (None, "opra", "indicative"):
            extra = {"feed": feed} if feed else {}
            st, body = p.get(DATA_URL, BARS_PATH, {"symbols": sym, "timeframe": "1Day", "start": f"{(d - timedelta(days=7)).isoformat()}T00:00:00Z",
                                                  "end": end, "limit": 10, **extra})
            res["feeds"][feed or "(none)"] = {"status": st, "rows": sum(len(v or []) for v in (body.get("bars") or {}).values()) if isinstance(body, dict) else 0,
                                              "message": body.get("message") if isinstance(body, dict) and st != 200 else None}
    res["access_errors"] = [dict(e) for e in p.log if e["status"] in (401, 403)]
    res["requests"] = len(p.log)
    res["non_200"] = dict(Counter(e["status"] for e in p.log if e["status"] != 200))
    return res


# ============================================================================================ report
def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def render_findings(res: dict, p: Probe, days: Sequence[date]) -> str:
    out = ["<!-- generated by backtest.options_probe; the verdict section after the line below was written by hand -->", "# Alpaca historical options data: probe findings", "",
           f"Read-only GETs only ({res['requests']:,} requests; HTTP status counts other than 200: {res['non_200'] or 'none'}). No 2026 date was queried. No keys appear in this file.", ""]
    out += ["## 1. Contracts: can expired QQQ and SPY contracts be listed?", "",
            "| Underlying | Status | Earliest listed expiration | Expired puts listed, expiry 2024-2025 | By expiry year | Fields per contract |", "| --- | --- | --- | --- | --- | --- |"]
    for u, e in res["contracts"].items():
        x = e["expired_puts_2024_2025"]
        out.append(f"| {u} | inactive (expired) | {e['inactive']['earliest']} (HTTP {e['inactive']['first_status']}) | {x['listed']:,} (HTTP {x['status']}) | "
                   f"{', '.join(f'{k}: {v:,}' for k, v in x['by_year'].items())} | {', '.join(x['fields'])} |")
        out.append(f"| {u} | active | {e['active']['earliest']} (HTTP {e['active']['first_status']}) | | | |")
    out += ["", "## 2. Coverage on 20 evenly spaced dates", "",
            f"For each date D between {days[0]} and {days[-1]}: the **put** contracts expiring {DTE_LO}-{DTE_HI} days after D with strikes from 15% below the spot (that day's SIP "
            "close) up to the spot, then the daily bar dated D for each (`/v1beta1/options/bars`, `timeframe=1Day`). A contract with no bar had no trade that day; the endpoint "
            "builds bars from trades, so a missing bar is a missing print, not a data outage. Puts only, because the study is a put-spread wheel.", ""]
    for u, c in res["coverage"].items():
        out += [f"### {u}", "", "| Distance below spot | Contracts | With a daily bar | Share | With a quote | Share |", "| --- | --- | --- | --- | --- | --- |"]
        for k, v in c["table"].items():
            out.append(f"| {'**all**' if k == 'all' else k} | {v['contracts']:,} | {v['with_bar']:,} | {_pct(v['bar_share'])} | {v['with_quote']:,} | {_pct(v['quote_share'])} |")
        out += ["", "| Date | Spot | Contracts | With a bar | Share | Distinct expiries |", "| --- | --- | --- | --- | --- | --- |"]
        for r in c["per_date"]:
            if r.get("skipped"):
                out.append(f"| {r['day']} | {r['spot']} | skipped | | | |")
            else:
                out.append(f"| {r['day']} | {r['spot']:.2f} | {r['contracts']:,} | {r['with_bar']:,} | {_pct(r['with_bar'] / r['contracts']) if r['contracts'] else 'n/a'} | {r['expiries']} |")
        out.append("")
    out += ["## 3. Quotes and trades endpoints", "", "| Contract and date | `/options/quotes` status | rows | message | `/options/trades` status | rows |", "| --- | --- | --- | --- | --- | --- |"]
    for k in res["quotes"]:
        q, t = res["quotes"][k], res["trades"][k]
        out.append(f"| {k} | {q['status']} | {q['rows']} | {q['message'] or ''} | {t['status']} | {t['rows']} |")
    if res.get("continuity"):
        out += ["", "### 3b. Supplement: do the contracts that traded on the entry date keep printing? (best case, not one of the four questions)", "",
                "For the dates with the best and the median daily-bar coverage (from 2024-02-01, after the data starts), the put contracts in the coverage set that HAD a bar "
                "on the entry date, followed over the next 20 trading days up to each contract's own expiry. This conditions on the contract having traded on the entry date, "
                "so it is a best case for a position opened on a printed price.", "",
                "| Underlying | Entry date | Selected as | Contracts | Traded on the entry date | Bar on the next trading day | Median share of later days with a bar | Mean | "
                "Bars on at least half the later days | Never printed again |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for c in res["continuity"]:
            if "next_day_share" not in c:
                out.append(f"| {c['underlying']} | {c['day']} | {c['selected_as']} | {c['contracts']:,} | {c['traded_on_day']:,} | {c.get('note', '')} | | | | |")
                continue
            out.append(f"| {c['underlying']} | {c['day']} | {c['selected_as']} | {c['contracts']:,} | {c['traded_on_day']:,} | {_pct(c['next_day_share'])} | "
                       f"{_pct(c['median_share_of_days_with_a_bar'])} | {_pct(c['mean_share_of_days_with_a_bar'])} | {_pct(c['share_with_bars_on_half_the_days'])} | "
                       f"{_pct(c['share_never_again'])} |")
        out += ["", "Trade prints for three contracts that did trade (`/v1beta1/options/trades`, the entry date):", "",
                "| Contract | HTTP | Rows | Fields | Exchange codes | Condition codes |", "| --- | --- | --- | --- | --- | --- |"]
        for c in res["continuity"]:
            for t_ in c.get("trades_sample", []):
                out.append(f"| {t_['symbol']} | {t_['status']} | {t_['rows']} | {', '.join(t_['fields'])} | {', '.join(t_['exchanges'])} | {', '.join(t_['conditions'])} |")
    out += ["", "## 4. Depth: the earliest historical options bar", "", "| Underlying | Earliest bar seen | Found from contracts expiring | HTTP |", "| --- | --- | --- | --- |"]
    for u, d in res["depth"].items():
        out.append(f"| {u} | {d.get('first_bar_seen')} | {d.get('from_contracts_expiring', '')} | {d.get('status', '')} |")
    out += ["", "The search looked at the March and September monthly expiry (third Friday) of each year from 2016: the 100 contracts with the highest open interest, "
            "bars from 120 days before the expiry. The true start can therefore be earlier than the date shown by up to several months.", ""]
    out += ["## 5. Feed and access", "", "| Bars request | HTTP | rows | message |", "| --- | --- | --- | --- |"]
    for k, v in res["feeds"].items():
        out.append(f"| `feed={k}` | {v['status']} | {v['rows']} | {v['message'] or ''} |")
    out += ["", "401/403 answers: " + (", ".join(f"{e['status']} on `{e['path']}` ({e['note']})" for e in res["access_errors"][:6]) if res["access_errors"] else "none"), ""]
    out += ["## 6. Exact endpoints and parameters used", "", "| Endpoint | Calls | HTTP statuses | Example parameters |", "| --- | --- | --- | --- |"]
    by: dict[str, list[dict]] = defaultdict(list)
    for e in p.log:
        by[e["path"]].append(e)
    for path, es in by.items():
        host = TRADING_URL if path == CONTRACTS_PATH else DATA_URL
        out.append(f"| `GET {host}{path}` | {len(es):,} | {dict(Counter(e['status'] for e in es))} | `{es[0]['params']}` |")
    return "\n".join(out) + "\n"


# ================================================================================================ CLI
def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def cmd_run(args) -> int:
    from .data import DataLayer
    if PROBE_END >= HOLDOUT_START:
        raise ValueError("the probe window must end before 2026")
    layer = DataLayer(args.cache_dir)
    days_all = layer.trading_days(PROBE_START, PROBE_END)
    days = evenly_spaced(days_all, N_DATES)
    spots: dict[str, dict[date, float]] = {}
    for u in UNDERLYINGS:
        df = layer.get_bars([u], "2023-12-01", PROBE_END, "1Day", "sip")
        spots[u] = {ts.date(): float(c) for ts, c in zip(df["ts"], df["close"])}
    key, secret = load_credentials()
    p = Probe(key, secret)
    res = run_probe(p, spots, days, all_days=days_all, log=_log)
    text = render_findings(res, p, days)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out / f"{stamp}-findings.md").write_text(text, encoding="utf-8", newline="\n")
    (out / f"{stamp}-requests.json").write_text(__import__("json").dumps(p.log, indent=1, default=str), encoding="utf-8")
    (out / f"{stamp}-results.json").write_text(__import__("json").dumps(res, indent=1, default=str), encoding="utf-8")
    if args.review_file:
        review = Path(args.review_file)
        review.parent.mkdir(parents=True, exist_ok=True)
        review.write_text(text, encoding="utf-8", newline="\n")
    print(f"probe done: {res['requests']} requests; findings in {out / (stamp + '-findings.md')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backtest.options_probe", description=__doc__.split("\n")[0])
    ap.add_argument("--cache-dir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="probe Alpaca's historical options data (read-only)")
    r.add_argument("--out", default=str(REPO_ROOT / "reports" / "options_probe"))
    r.add_argument("--review-file", help="also write the findings here (.review/options-data-probe.md)")
    args = ap.parse_args(argv)
    try:
        return cmd_run(args)
    except Exception as exc:                                    # a probe reports its failure; it does not hide it
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
