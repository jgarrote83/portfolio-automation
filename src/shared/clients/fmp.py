"""Financial Modeling Prep client — Starter plan, `/stable/*` endpoints.

Auth: apikey query parameter on every request (header also supported, but query is
simpler for caching diagnostics). All methods return [] / None on error and log the
failure rather than raising, so a single endpoint outage cannot kill the collector.

Verified endpoints (Starter plan, May 2026):
- Company profile:      /stable/profile?symbol=...
- Batch quote short:    /stable/batch-quote-short?symbols=A,B,C
- Historical EOD light: /stable/historical-price-eod/light?symbol=...
- Earnings calendar:    /stable/earnings-calendar?from=...&to=...
- Stock news search:    /stable/news/stock?symbols=A,B,C
- ETF holdings:         /stable/etf/holdings?symbol=SPY
- ETF country weights:  /stable/etf/country-weightings?symbol=SPY
- ETF sector weights:   /stable/etf/sector-weightings?symbol=SPY
- Senate trades:        /stable/senate-trades?symbol=AAPL
- House trades:         /stable/house-trades?symbol=AAPL
- Latest senate flow:   /stable/senate-latest?page=0&limit=100
- Latest house flow:    /stable/house-latest?page=0&limit=100
- DCF valuation:        /stable/discounted-cash-flow?symbol=AAPL
- Ratings snapshot:     /stable/ratings-snapshot?symbol=AAPL
"""
from __future__ import annotations

import logging
import requests

logger = logging.getLogger(__name__)

_BASE = "https://financialmodelingprep.com/stable"
_TIMEOUT = 30


class FMPClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()

    # ---- low-level ---------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict | list | None:
        p = {"apikey": self.api_key}
        if params:
            p.update(params)
        try:
            r = self.session.get(f"{_BASE}{path}", params=p, timeout=_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            logger.error("FMP %s failed: %s", path, e)
            return None

    # ---- profile / fundamentals -------------------------------------------
    def get_profile(self, ticker: str) -> dict | None:
        result = self._get("/profile", {"symbol": ticker})
        if isinstance(result, list) and result:
            return result[0]
        return None

    def get_profiles(self, tickers: list[str]) -> list[dict]:
        """No batch on /stable/profile — fan out per ticker (~1 call each)."""
        out: list[dict] = []
        for t in tickers:
            p = self.get_profile(t)
            if p:
                out.append(p)
        return out

    def get_dcf(self, ticker: str) -> dict | None:
        result = self._get("/discounted-cash-flow", {"symbol": ticker})
        if isinstance(result, list) and result:
            return result[0]
        return None

    def get_ratings_snapshot(self, ticker: str) -> dict | None:
        result = self._get("/ratings-snapshot", {"symbol": ticker})
        if isinstance(result, list) and result:
            return result[0]
        return None

    # ---- quotes / prices ---------------------------------------------------
    def get_batch_quote_short(self, tickers: list[str]) -> list[dict]:
        if not tickers:
            return []
        result = self._get("/batch-quote-short", {"symbols": ",".join(tickers)})
        return result if isinstance(result, list) else []

    def get_eod_prices(self, tickers: list[str]) -> dict[str, dict]:
        """Return {ticker: {c, v, t}} for each ticker.

        Uses /stable/historical-price-eod/light (one call per ticker) and reads the
        latest row — batch-quote endpoints are above Starter tier. Cost: N calls.
        """
        out: dict[str, dict] = {}
        for t in tickers:
            rows = self.get_historical_price_light(t)
            if not rows:
                continue
            latest = rows[0]
            out[t] = {
                "c": latest.get("price") or latest.get("close"),
                "v": latest.get("volume"),
                "t": latest.get("date"),
            }
        return out

    def get_historical_price_light(self, ticker: str) -> list[dict]:
        """End-of-day OHLC + volume (last ~5 years on Starter)."""
        result = self._get("/historical-price-eod/light", {"symbol": ticker})
        if isinstance(result, dict) and "historical" in result:
            return result["historical"] or []
        return result if isinstance(result, list) else []

    # ---- Movers (N1, session 2026-09-12) -----------------------------------
    # VERIFIED AVAILABLE ON STARTER by live probe 2026-09-12 (resolves the
    # endpoint-availability half of FOLLOWUPS #34, open since 2026-07-04):
    # /most-actives, /biggest-gainers and /biggest-losers each returned 50 rows
    # of {symbol, name, price, change, changesPercentage, exchange}. TWO calls
    # per collector run total — these are market-wide lists, not per-symbol.
    #
    # NOTE what these rows do NOT carry: no volume, no market cap, no ADV. The
    # liquidity floor and the price floor are applied DOWNSTREAM by the caller
    # against real price history (`_build_catalyst_screen`'s hard screen) — a
    # mover row is a candidate NAME, never evidence that the name is tradeable.
    # The live probe found the union is ~19% sub-$1 and ~42% sub-$5.

    def get_most_actives(self) -> list[dict]:
        """Today's most-active names (market-wide). ``[]`` on any failure."""
        result = self._get("/most-actives", {})
        return result if isinstance(result, list) else []

    def get_biggest_gainers(self) -> list[dict]:
        """Today's biggest gainers (market-wide). ``[]`` on any failure."""
        result = self._get("/biggest-gainers", {})
        return result if isinstance(result, list) else []

    # ---- global_overnight (session 2026-09-13, FOLLOWUPS #34) --------------
    # Probed live on this Starter key 2026-09-13 — what is and is not available:
    #   /quote?symbol=^N225 / ^HSI / ^FTSE / ^STOXX50E  -> 200
    #   /quote?symbol=^KS11 / ^GDAXI                    -> 402 Restricted Endpoint
    #   /batch-quote-short (any symbols)                -> 402 Restricted Endpoint
    #   /aftermarket-quote?symbol=TSM|SHEL|AZN|RIO|HSBC -> 200 (all five)
    # So index reads are ONE CALL PER SYMBOL (no batch on this tier), two of the
    # seven configured regions fall back to a US-listed country ETF, and the
    # sector read runs through ADRs because no index is sectoral.
    #
    # `/quote` rows carry {symbol, price, previousClose, changePercentage,
    # timestamp}; `/aftermarket-quote` rows carry only {symbol, bidPrice,
    # askPrice, bidSize, askSize, volume, timestamp} — NO price and NO previous
    # close, so a change % is not computable from an aftermarket row alone. That
    # is why the collector's region/sector reads go through `get_quote` below;
    # the pre-existing `get_aftermarket_quote` (DayTrade Lab section) stays what
    # it already was and is NOT used as a price here.

    def get_quote(self, ticker: str) -> dict | None:
        """Full quote row for one symbol (index symbols use a `^` prefix).

        ``None`` on any failure INCLUDING a 402 — a plan-restricted symbol is
        indistinguishable from an outage here by design, and both resolve the
        same way upstream: fall back to the configured proxy, or drop the row.
        """
        result = self._get("/quote", {"symbol": ticker})
        if isinstance(result, list) and result:
            return result[0]
        return result if isinstance(result, dict) else None

    # ---- DayTrade Lab (all verified on Starter 2026-07-07 — spec §2) --------
    def get_shares_float(self, ticker: str) -> dict | None:
        """``{floatShares, outstandingShares, freeFloat, date, ...}`` or None."""
        result = self._get("/shares-float", {"symbol": ticker})
        if isinstance(result, list) and result:
            return result[0]
        return None

    def get_sec_filings(self, ticker: str, from_date: str, to_date: str,
                        limit: int = 100) -> list[dict] | None:
        """SEC filings ``{formType, filingDate, ...}`` for a symbol in [from, to].

        Returns None (NOT []) when the endpoint itself fails, so callers can
        distinguish "no filings" from "cannot measure" — the lab fails closed on
        the latter for sub-$2B names (spec §3 gate 4).
        """
        result = self._get("/sec-filings-search/symbol", {
            "symbol": ticker, "from": from_date, "to": to_date,
            "page": 0, "limit": limit,
        })
        return result if isinstance(result, list) else None

    def get_aftermarket_quote(self, ticker: str) -> dict | None:
        """Extended-hours quote ``{bidPrice, askPrice, volume, timestamp}``.

        Candidate consolidated pre-market volume source (spec §2 DECISION 0):
        endpoint returns 200 on Starter, but the ``volume`` field's session
        semantics at 09:20 ET are UNVERIFIED — do not gate on it until
        ``consolidated_source`` is flipped to "fmp" after a live-morning check.
        """
        result = self._get("/aftermarket-quote", {"symbol": ticker})
        if isinstance(result, list) and result:
            return result[0]
        return None

    # ---- calendars ---------------------------------------------------------
    def get_earnings_calendar(self, from_date: str, to_date: str) -> list[dict]:
        result = self._get("/earnings-calendar", {"from": from_date, "to": to_date})
        return result if isinstance(result, list) else []

    # ---- news --------------------------------------------------------------
    def get_stock_news(self, tickers: list[str], limit: int = 30) -> list[dict]:
        if not tickers:
            return []
        result = self._get("/news/stock", {
            "symbols": ",".join(tickers),
            "limit": limit,
        })
        return result if isinstance(result, list) else []

    # ---- ETF look-through --------------------------------------------------
    def get_etf_holdings(self, ticker: str) -> list[dict]:
        """NOT AVAILABLE on Starter tier (returns 402). Kept for forward-compat;
        always returns []. Use country + sector weights instead."""
        return []

    def get_etf_country_weights(self, ticker: str) -> list[dict]:
        result = self._get("/etf/country-weightings", {"symbol": ticker})
        return result if isinstance(result, list) else []

    def get_etf_sector_weights(self, ticker: str) -> list[dict]:
        result = self._get("/etf/sector-weightings", {"symbol": ticker})
        return result if isinstance(result, list) else []

    # ---- political flow (FMP — backup; Quiver is primary) -----------------
    def get_senate_trades(self, ticker: str) -> list[dict]:
        result = self._get("/senate-trades", {"symbol": ticker})
        return result if isinstance(result, list) else []

    def get_house_trades(self, ticker: str) -> list[dict]:
        result = self._get("/house-trades", {"symbol": ticker})
        return result if isinstance(result, list) else []

    def get_latest_senate(self, limit: int = 100) -> list[dict]:
        result = self._get("/senate-latest", {"page": 0, "limit": limit})
        return result if isinstance(result, list) else []

    def get_latest_house(self, limit: int = 100) -> list[dict]:
        result = self._get("/house-latest", {"page": 0, "limit": limit})
        return result if isinstance(result, list) else []

    def get_congressional_trading(self, from_date: str | None = None) -> list[dict]:
        """Backward-compat wrapper: combined latest senate + house flow.

        `from_date` clips client-side using `transactionDate` or `disclosureDate`.
        """
        senate = self.get_latest_senate(limit=100)
        house  = self.get_latest_house(limit=100)
        for r in senate:
            r["chamber"] = "senate"
        for r in house:
            r["chamber"] = "house"
        combined = senate + house
        if from_date:
            combined = [
                r for r in combined
                if (r.get("transactionDate") or r.get("disclosureDate") or "") >= from_date
            ]
        return combined
