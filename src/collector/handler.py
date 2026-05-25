import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from shared.keyvault import load_secrets
from shared.storage import ensure_tables, upsert_entity, write_snapshot
from shared.clients.etrade import ETradeClient
from shared.clients.fmp import FMPClient
from shared.clients.fred import FREDClient
from shared.clients.finnhub import FinnhubClient
from shared.clients.quiver import QuiverClient

logger = logging.getLogger(__name__)

_SRC = Path(__file__).parent.parent   # src/
_MACRO_SERIES_FILE = _SRC / "config" / "macro-series.json"
_PORTFOLIO_FALLBACK = _SRC / "config" / "portfolio.json"
_ETF_WATCHLIST = ["IDVO", "IDMO", "AIA"]

# Regional rotation universe: SPY benchmark + international ETFs in Core.
# Used to compute 60-trading-day relative strength + 50/200d MA cross so the
# analyzer can call US-vs-international rotation independently of the quadrant.
_ROTATION_TICKERS = ["SPY", "IDMO", "AIA", "IEMG", "VSS", "EUAD", "EWZ", "EWJ"]
_ROTATION_WINDOW_DAYS = 60
_MA_LONG_DAYS = 200
_MA_SHORT_DAYS = 50
# Pure-international subset used for MA-cross signals against SPY.
_INTL_RATIO_TICKERS = ["IDMO", "AIA", "IEMG", "EWJ"]

# Market shock detection: short-horizon move windows and keyword sets.
# The analyzer uses the resulting shock_level to optionally override the 60d
# rotation windows and lift tilt limits when a structural event hits the tape.
_SHOCK_SHORT_WINDOW_DAYS = 5
_SHOCK_VOL_LOOKBACK_DAYS = 60
_SHOCK_KEYWORDS: dict[str, list[str]] = {
    "geopolitical": [
        "tariff", "tariffs", "sanction", "sanctions", "embargo", "export ban",
        "war", "invasion", "missile", "strike", "attack", "airstrike",
        "ceasefire", "escalation", "retaliation", "trade war",
    ],
    "policy_shock": [
        "emergency cut", "emergency hike", "surprise cut", "surprise hike",
        "intervention", "devaluation", "capital controls", "shutdown",
        "debt ceiling", "default", "downgrade", "impeach", "resign",
        "bailout", "liquidity facility",
    ],
    "market_stress": [
        "crash", "plunge", "collapse", "contagion", "recession",
        "bankruptcy", "insolvency", "halt", "circuit breaker",
        "freeze", "run on", "margin call", "liquidation",
    ],
}


def run() -> None:
    today = date.today().isoformat()
    logger.info("=== Collector starting for %s ===", today)

    secrets = load_secrets()
    ensure_tables()

    # --- Portfolio -----------------------------------------------------------
    etrade = ETradeClient(
        consumer_key=secrets.get("EtradeConsumerKey"),
        consumer_secret=secrets.get("EtradeConsumerSecret"),
        access_token=secrets.get("EtradeAccessToken"),
        access_token_secret=secrets.get("EtradeAccessTokenSecret"),
    )
    positions = etrade.get_portfolio()
    balances = etrade.get_balances()

    if positions is None:
        logger.warning("E*TRADE unavailable — loading config/portfolio.json fallback")
        with open(_PORTFOLIO_FALLBACK) as f:
            fb = json.load(f)
        positions = fb.get("positions", [])
        balances = fb.get("balances", {})

    tickers = [p["ticker"] for p in positions if p.get("ticker")]
    logger.info("Portfolio tickers (%d): %s", len(tickers), tickers)

    # --- FMP -----------------------------------------------------------------
    fmp = FMPClient(secrets["FmpApiKey"])
    profiles = fmp.get_profiles(tickers)

    from_2w = (date.today() - timedelta(days=1)).isoformat()
    to_2w   = (date.today() + timedelta(days=14)).isoformat()
    from_30d = (date.today() - timedelta(days=30)).isoformat()

    earnings           = fmp.get_earnings_calendar(from_2w, to_2w)
    stock_news         = fmp.get_stock_news(tickers, limit=30)
    etf_holdings: dict = {etf: fmp.get_etf_holdings(etf) for etf in _ETF_WATCHLIST}
    etf_country: dict  = {etf: fmp.get_etf_country_weights(etf) for etf in _ETF_WATCHLIST}
    etf_sector: dict   = {etf: fmp.get_etf_sector_weights(etf) for etf in _ETF_WATCHLIST}

    logger.info("FMP: %d profiles, %d earnings, %d news",
                len(profiles), len(earnings), len(stock_news))

    # --- Quiver (primary congressional source) ------------------------------
    quiver = QuiverClient(secrets.get("QuiverApiKey"))
    if quiver.ready:
        congressional = quiver.get_live_congress_trades()
        if from_30d:
            congressional = [
                r for r in congressional
                if (r.get("TransactionDate") or r.get("Date") or r.get("transactionDate") or "") >= from_30d
            ]
        lobbying      = quiver.get_live_lobbying()
        gov_contracts = quiver.get_live_gov_contracts()
    else:
        logger.warning("Quiver key missing — falling back to FMP senate/house latest")
        congressional = fmp.get_congressional_trading(from_30d)
        lobbying      = []
        gov_contracts = []
    logger.info("Quiver/FMP: %d congressional, %d lobbying, %d gov contracts",
                len(congressional), len(lobbying), len(gov_contracts))

    # --- FRED ----------------------------------------------------------------
    with open(_MACRO_SERIES_FILE) as f:
        macro_meta: dict = json.load(f)

    fred = FREDClient(secrets["FredApiKey"])
    macro_data = fred.get_all_series(list(macro_meta.keys()))
    # Series that need deeper history for the rotation + bond-signals pre-compute
    # (get_all_series only fetches the latest 5 observations per series).
    macro_data["DTWEXBGS"] = fred.get_series_latest("DTWEXBGS", limit=90)
    macro_data["DGS2"]     = fred.get_series_latest("DGS2",     limit=90)
    macro_data["DFF"]      = fred.get_series_latest("DFF",      limit=90)
    # Bond-signals pre-compute needs ~90d for percentiles + 4w deltas.
    for _bond_sid in (
        "DGS10", "DGS30", "DGS3MO", "T10Y2Y", "T10Y3M",
        "BAMLH0A0HYM2", "BAMLC0A0CM",
        "DFII10", "T10YIE", "T5YIE", "T5YIFR",
        "MORTGAGE30US",
    ):
        macro_data[_bond_sid] = fred.get_series_latest(_bond_sid, limit=90)
    logger.info("FRED: %d series collected", sum(1 for v in macro_data.values() if v))

    # --- EOD prices (FMP batch-quote, single call) --------------------------
    all_tickers = list(dict.fromkeys(tickers + _ETF_WATCHLIST))  # preserve order, dedupe
    prices = fmp.get_eod_prices(all_tickers)
    logger.info("FMP prices: %d/%d collected", len(prices), len(all_tickers))

    # --- Regional rotation pre-compute --------------------------------------
    regional_rotation = _build_regional_rotation(fmp, macro_data)
    logger.info(
        "Regional rotation: %d tickers scored, DXY 60d=%s",
        len(regional_rotation.get("tickers", {})),
        regional_rotation.get("dxy_60d_pct_change"),
    )

    # --- Finnhub -------------------------------------------------------------
    finnhub = FinnhubClient(secrets["FinnhubApiKey"])
    market_news = finnhub.get_market_news("general")
    forex_news  = finnhub.get_market_news("forex")

    week_ago = (date.today() - timedelta(days=7)).isoformat()
    company_news: dict = {}
    # Cap at 10 tickers to stay within 60 calls/min free tier
    for ticker in tickers[:10]:
        company_news[ticker] = finnhub.get_company_news(ticker, week_ago, today)

    logger.info("Finnhub: %d market news, %d company news items",
                len(market_news), sum(len(v) for v in company_news.values()))

    # --- Market shock detector (short-horizon moves + news keyword scan) ----
    bond_signals = _build_bond_signals(macro_data)
    logger.info(
        "Bond signals: composite=%s label=%s hy_oas=%s recession_prob=%s",
        bond_signals.get("scorecard", {}).get("composite"),
        bond_signals.get("scorecard", {}).get("label"),
        bond_signals.get("credit", {}).get("hy_oas", {}).get("latest"),
        bond_signals.get("yield_curve", {}).get("recession_prob_12m"),
    )

    market_shock = _build_market_shock(
        fmp=fmp,
        macro_data=macro_data,
        market_news=market_news,
        forex_news=forex_news,
        stock_news=stock_news,
        company_news=company_news,
        bond_signals=bond_signals,
    )
    logger.info(
        "Market shock: level=%s, spy_1d_z=%s, news_hits=%s",
        market_shock.get("shock_level"),
        market_shock.get("spy", {}).get("return_1d_zscore"),
        market_shock.get("news_hits_total"),
    )

    # --- Assemble snapshot ---------------------------------------------------
    snapshot = {
        "date": today,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "portfolio": {
            "positions": positions,
            "balances": balances,
            "source": "etrade" if etrade.ready else "fallback",
        },
        "fundamentals": profiles,
        "earnings_calendar": earnings,
        "stock_news": stock_news,
        "congressional_trades": congressional,
        "lobbying": lobbying,
        "gov_contracts": gov_contracts,
        "etf_holdings": etf_holdings,
        "etf_country_weights": etf_country,
        "etf_sector_weights": etf_sector,
        "macro": {
            "series_meta": macro_meta,
            "data": macro_data,
        },
        "prices": prices,
        "regional_rotation": regional_rotation,
        "bond_signals": bond_signals,
        "market_shock": market_shock,
        "news": {
            "market": market_news[:50],
            "forex": forex_news[:20],
            "company": company_news,
        },
    }

    # --- Persist -------------------------------------------------------------
    write_snapshot(today, snapshot)
    _write_portfolio_history(today, positions, prices)
    _write_fundamentals_history(today, profiles)
    _write_macro_history(today, macro_data, macro_meta)
    _write_etf_history(today, etf_holdings, prices)
    _write_sentiment_history(today, snapshot)

    logger.info("=== Collector completed for %s ===", today)


# ---------------------------------------------------------------------------
# Table writers
# ---------------------------------------------------------------------------

def _write_portfolio_history(today: str, positions: list[dict], prices: dict) -> None:
    total_value = sum(p.get("market_value", 0) for p in positions) or 1.0
    for pos in positions:
        ticker = pos.get("ticker")
        if not ticker:
            continue
        price_data = prices.get(ticker, {})
        upsert_entity("PortfolioHistory", {
            "PartitionKey": ticker,
            "RowKey": today,
            "quantity":     pos.get("quantity", 0),
            "market_value": pos.get("market_value", 0),
            "cost_basis":   pos.get("cost_basis", 0),
            "weight":       round(pos.get("market_value", 0) / total_value, 4),
            "day_gain":     pos.get("day_gain", 0),
            "total_gain":   pos.get("total_gain", 0),
            "close_price":  price_data.get("c", 0),
            "volume":       price_data.get("v", 0),
            "security_type": pos.get("security_type", "EQ"),
        })


def _write_fundamentals_history(today: str, profiles: list[dict]) -> None:
    for p in profiles:
        ticker = p.get("symbol")
        if not ticker:
            continue
        upsert_entity("FundamentalsHistory", {
            "PartitionKey":    ticker,
            "RowKey":          today,
            "price":           p.get("price"),
            "market_cap":      p.get("mktCap"),
            "pe_ratio":        p.get("pe"),
            "beta":            p.get("beta"),
            "dcf_value":       p.get("dcf"),
            "analyst_rating":  p.get("rating"),
            "div_yield":       p.get("lastAnnualDividendYield"),
            "sector":          p.get("sector", ""),
            "industry":        p.get("industry", ""),
        })


def _write_macro_history(today: str, macro_data: dict, meta: dict) -> None:
    for series_id, observations in macro_data.items():
        if not observations:
            continue
        latest = observations[0]
        prev   = observations[1] if len(observations) > 1 else None
        try:
            val = float(latest.get("value", "nan"))
        except (ValueError, TypeError):
            val = None
        try:
            delta = (val - float(prev["value"])) if (prev and val is not None
                                                     and prev.get("value") not in (".", "")) else None
        except (ValueError, TypeError):
            delta = None
        upsert_entity("MacroHistory", {
            "PartitionKey": series_id,
            "RowKey":       today,
            "obs_date":     latest.get("date", ""),
            "value":        val,
            "delta":        delta,
            "series_name":  meta.get(series_id, {}).get("name", series_id),
            "unit":         meta.get(series_id, {}).get("unit", ""),
        })


def _write_etf_history(today: str, etf_holdings: dict, prices: dict) -> None:
    for etf, holdings in etf_holdings.items():
        price_data = prices.get(etf, {})
        upsert_entity("ETFLookthroughHistory", {
            "PartitionKey":    etf,
            "RowKey":          today,
            "holdings_count":  len(holdings),
            "top_10":          json.dumps(holdings[:10], default=str),
            "close_price":     price_data.get("c", 0),
            "volume":          price_data.get("v", 0),
        })


def _build_regional_rotation(fmp: FMPClient, macro_data: dict) -> dict:
    """Pre-compute the US-vs-international rotation signal block.

    Produces, for the analyzer to consume directly:
      - per-ticker 60-trading-day return + excess vs SPY
      - leaders / laggards vs SPY (>= +/-5 percentage-point cutoff)
      - 50/200-day moving-average cross for {IDMO,AIA,IEMG,EWJ}/SPY ratios
      - DXY 60d % change (FRED DTWEXBGS) with tailwind/headwind tag at +/-3%
      - policy divergence sub-score from US 2Y yield trend
      - composite Rotation Score 0-10 (dollar 30 / RS 30 / policy 20 / valuation 20)

    Components we cannot compute from current data sources (ETF flows from
    Bloomberg/ICI, regional earnings revision breadth) are marked
    'unavailable' and held at the neutral score of 5.
    """
    out: dict = {
        "window_trading_days": _ROTATION_WINDOW_DAYS,
        "ma_short_days": _MA_SHORT_DAYS,
        "ma_long_days": _MA_LONG_DAYS,
        "benchmark": "SPY",
        "tickers": {},
        "leaders_vs_spy": [],
        "laggards_vs_spy": [],
        "ratio_ma_cross": {},
        "dxy_60d_pct_change": None,
        "dxy_tailwind_for_intl": None,
        "policy": {},
        "rotation_flags": {},
        "rotation_score": {},
    }

    def _close(row: dict) -> float | None:
        v = row.get("price") if row.get("price") is not None else row.get("close")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    # --- 1. Fetch full history once per rotation ticker (newest-first) -------
    histories: dict[str, list[dict]] = {}
    for t in _ROTATION_TICKERS:
        try:
            rows = fmp.get_historical_price_light(t)
        except Exception as e:  # noqa: BLE001
            logger.warning("Rotation: history fetch failed for %s: %s", t, e)
            continue
        if rows:
            histories[t] = rows

    # --- 2. 60d returns + excess vs SPY --------------------------------------
    spy_return: float | None = None
    per_ticker_ret: dict[str, float] = {}
    for t, rows in histories.items():
        if len(rows) < _ROTATION_WINDOW_DAYS + 1:
            continue
        latest = _close(rows[0])
        past = _close(rows[_ROTATION_WINDOW_DAYS])
        if not latest or not past or past == 0:
            continue
        ret_pct = (latest / past - 1.0) * 100.0
        per_ticker_ret[t] = round(ret_pct, 2)
        out["tickers"][t] = {
            "return_60d_pct": round(ret_pct, 2),
            "latest_close": round(latest, 4),
            "latest_date": rows[0].get("date"),
            "window_start_close": round(past, 4),
            "window_start_date": rows[_ROTATION_WINDOW_DAYS].get("date"),
        }
        if t == "SPY":
            spy_return = ret_pct

    if spy_return is not None:
        for t, ret in per_ticker_ret.items():
            if t == "SPY":
                continue
            excess = round(ret - spy_return, 2)
            out["tickers"][t]["excess_vs_spy_pp"] = excess
            if excess >= 5.0:
                out["leaders_vs_spy"].append({"ticker": t, "excess_pp": excess})
            elif excess <= -5.0:
                out["laggards_vs_spy"].append({"ticker": t, "excess_pp": excess})
        out["leaders_vs_spy"].sort(key=lambda x: x["excess_pp"], reverse=True)
        out["laggards_vs_spy"].sort(key=lambda x: x["excess_pp"])

    # --- 3. 50/200-day MA cross on intl/SPY ratios ---------------------------
    spy_hist = histories.get("SPY") or []
    if len(spy_hist) >= _MA_LONG_DAYS:
        # Build a date->close map for SPY so we can align on common trading dates.
        spy_by_date = {
            r.get("date"): _close(r) for r in spy_hist
            if r.get("date") and _close(r)
        }
        for t in _INTL_RATIO_TICKERS:
            int_hist = histories.get(t) or []
            if len(int_hist) < _MA_LONG_DAYS:
                continue
            # Build aligned ratio series, newest-first.
            ratios: list[float] = []
            for r in int_hist:
                d = r.get("date")
                ic = _close(r)
                sc = spy_by_date.get(d)
                if ic and sc:
                    ratios.append(ic / sc)
                if len(ratios) >= _MA_LONG_DAYS:
                    break
            if len(ratios) < _MA_LONG_DAYS:
                continue
            ratio_now = ratios[0]
            ma_short = sum(ratios[:_MA_SHORT_DAYS]) / _MA_SHORT_DAYS
            ma_long = sum(ratios[:_MA_LONG_DAYS]) / _MA_LONG_DAYS
            out["ratio_ma_cross"][f"{t}/SPY"] = {
                "ratio_now": round(ratio_now, 6),
                "ma_50d": round(ma_short, 6),
                "ma_200d": round(ma_long, 6),
                "ma50_above_ma200": ma_short > ma_long,
                "ratio_above_ma200": ratio_now > ma_long,
                "signal": (
                    "bullish_intl" if (ma_short > ma_long and ratio_now > ma_long)
                    else "bearish_intl" if (ma_short < ma_long and ratio_now < ma_long)
                    else "mixed"
                ),
            }

    # --- 4. DXY 60-trading-day % change --------------------------------------
    dxy_rows = macro_data.get("DTWEXBGS") or []
    valid_dxy = [
        (r.get("date"), float(r["value"])) for r in dxy_rows
        if r.get("value") not in (None, ".", "")
    ]
    dxy_pct: float | None = None
    if len(valid_dxy) >= _ROTATION_WINDOW_DAYS + 1:
        latest_dxy = valid_dxy[0][1]
        past_dxy = valid_dxy[_ROTATION_WINDOW_DAYS][1]
        if past_dxy:
            dxy_pct = round((latest_dxy / past_dxy - 1.0) * 100.0, 2)
            out["dxy_60d_pct_change"] = dxy_pct
            out["dxy_latest_date"] = valid_dxy[0][0]
            if dxy_pct <= -3.0:
                out["dxy_tailwind_for_intl"] = "tailwind"
            elif dxy_pct >= 3.0:
                out["dxy_tailwind_for_intl"] = "headwind"
            else:
                out["dxy_tailwind_for_intl"] = "neutral"

    # --- 5. Policy divergence (US 2Y yield trend as a proxy) -----------------
    dgs2_rows = macro_data.get("DGS2") or []
    valid_dgs2 = [
        float(r["value"]) for r in dgs2_rows
        if r.get("value") not in (None, ".", "")
    ]
    us2y_bp_change: float | None = None
    if len(valid_dgs2) >= _ROTATION_WINDOW_DAYS + 1:
        latest_y = valid_dgs2[0]
        past_y = valid_dgs2[_ROTATION_WINDOW_DAYS]
        us2y_bp_change = round((latest_y - past_y) * 100.0, 1)  # bp
    fed_funds = next(
        (float(r["value"]) for r in (macro_data.get("DFF") or [])
         if r.get("value") not in (None, ".", "")),
        None,
    )
    ecb_rate = next(
        (float(r["value"]) for r in (macro_data.get("ECBDFR") or [])
         if r.get("value") not in (None, ".", "")),
        None,
    )
    out["policy"] = {
        "fed_funds_latest": fed_funds,
        "ecb_deposit_latest": ecb_rate,
        "us_2y_60d_bp_change": us2y_bp_change,
        # Falling US 2Y => market pricing Fed easing => USD weakness => intl tailwind.
        "stance_for_intl": (
            "supportive" if us2y_bp_change is not None and us2y_bp_change <= -25
            else "adverse" if us2y_bp_change is not None and us2y_bp_change >= 25
            else "neutral" if us2y_bp_change is not None
            else "unknown"
        ),
    }

    # --- 6. Legacy boolean flags (kept for backward compat) ------------------
    rs_flag = len(out["leaders_vs_spy"]) > 0
    dxy_tail = out["dxy_tailwind_for_intl"] == "tailwind"
    dxy_head = out["dxy_tailwind_for_intl"] == "headwind"
    out["rotation_flags"] = {
        "intl_rs_leader": rs_flag,
        "dxy_tailwind": dxy_tail,
        "dxy_headwind": dxy_head,
        "rotate_to_international": sum([rs_flag, dxy_tail]) >= 2,
        "rotate_back_to_us": sum([(not rs_flag and bool(per_ticker_ret)), dxy_head]) >= 2,
    }

    # --- 7. Composite Rotation Score 0-10 ------------------------------------
    # Weights: dollar 30 / relative strength 30 / policy 20 / valuation 20.
    # Each component is scored 0-10 then weight-averaged. Missing components
    # default to neutral=5 and are flagged in 'components_missing'.
    components: dict[str, dict] = {}
    missing: list[str] = []

    # Dollar momentum (lower DXY = higher score for intl).
    if dxy_pct is not None:
        if dxy_pct <= -8: d_score = 10.0
        elif dxy_pct <= -5: d_score = 8.5
        elif dxy_pct <= -3: d_score = 7.0
        elif dxy_pct <= -1: d_score = 6.0
        elif dxy_pct <  1: d_score = 5.0
        elif dxy_pct <  3: d_score = 4.0
        elif dxy_pct <  5: d_score = 3.0
        elif dxy_pct <  8: d_score = 1.5
        else: d_score = 0.0
    else:
        d_score = 5.0
        missing.append("dollar_momentum")
    components["dollar_momentum"] = {"score": d_score, "weight": 30, "input_dxy_60d_pct": dxy_pct}

    # Relative strength: average excess vs SPY across intl tickers in universe.
    intl_excess = [
        info.get("excess_vs_spy_pp") for tk, info in out["tickers"].items()
        if tk != "SPY" and info.get("excess_vs_spy_pp") is not None
    ]
    if intl_excess:
        avg_excess = sum(intl_excess) / len(intl_excess)
        # +10pp avg -> 10, 0pp -> 5, -10pp -> 0; clamp.
        rs_score = max(0.0, min(10.0, 5.0 + avg_excess * 0.5))
        rs_input = round(avg_excess, 2)
    else:
        rs_score = 5.0
        rs_input = None
        missing.append("relative_strength")
    components["relative_strength"] = {"score": round(rs_score, 2), "weight": 30, "input_avg_excess_pp": rs_input}

    # Policy divergence: based on US 2Y bp change.
    if us2y_bp_change is not None:
        # Falling >=50bp -> 10; +/-25bp band -> 5; rising >=50bp -> 0.
        p_score = max(0.0, min(10.0, 5.0 - us2y_bp_change / 10.0))
    else:
        p_score = 5.0
        missing.append("policy_divergence")
    components["policy_divergence"] = {"score": round(p_score, 2), "weight": 20, "input_us_2y_60d_bp": us2y_bp_change}

    # Valuation gap: not computable from current feeds (ETF P/E aggregation absent).
    v_score = 5.0
    missing.append("valuation_gap")
    components["valuation_gap"] = {"score": v_score, "weight": 20, "input": None, "note": "ETF forward-P/E aggregation not available on current data tier"}

    weighted = sum(c["score"] * c["weight"] for c in components.values()) / 100.0
    if weighted <= 3:
        category = "us_leadership_intact"
    elif weighted <= 6:
        category = "transition_window"
    else:
        category = "rotation_underway"

    out["rotation_score"] = {
        "composite": round(weighted, 1),
        "category": category,
        "components": components,
        "components_missing": missing,
        "scoring_rubric": "0-3 US leadership intact; 4-6 transition window; 7-10 rotation underway",
    }

    return out


def _build_bond_signals(macro_data: dict) -> dict:
    """Pre-compute a four-signal bond market scorecard for the analyzer.

    Inputs come from the FRED ``macro_data`` block (deep-history fetched in
    ``run()``). Output sections:

      yield_curve:   3m10y / 2s10s / 5s30s spreads + 5d deltas, curve regime
                     label, Estrella-Mishkin 12-month recession probability
      credit:        HY OAS, IG OAS — levels, 5d/20d deltas, 90d percentile,
                     ``credit_stress`` flag (HY OAS +50bp 4w OR >=90th pct OR
                     IG OAS +25bp 4w)
      breakevens:    5y, 10y, 5y5y — levels + 20d deltas
      systemic:      MBS-Treasury spread proxy (MORTGAGE30US - DGS10) + 20d
                     delta, real 10Y yield (DFII10) level

    Composite scorecard: each of the four signals scored -2..+2 (negative =
    bearish risk assets); composite -8..+8 with label
    ``risk_on`` / ``neutral`` / ``defensive`` / ``acute_defensive``.

    All deltas use trading-day approximations (1d, ~5d, ~20d index offsets in
    the descending-order FRED responses). Percentile is computed over the
    available 90d window. None propagates through cleanly when data missing.
    """
    out: dict = {
        "yield_curve": {},
        "credit": {},
        "breakevens": {},
        "systemic": {},
        "scorecard": {},
        "caveat": (
            "2025-2026 bond signals may be partially distorted by QT and "
            "Treasury issuance patterns. Require confluence (>=3 of 4 signals "
            "agreeing) before acting on the composite alone."
        ),
    }

    def _vals(sid: str) -> list[float]:
        """Latest-first list of floats, skipping missing observations."""
        rows = macro_data.get(sid) or []
        out_vals: list[float] = []
        for r in rows:
            v = r.get("value")
            if v in (None, ".", ""):
                continue
            try:
                out_vals.append(float(v))
            except (TypeError, ValueError):
                continue
        return out_vals

    def _delta_bp(vals: list[float], n: int) -> float | None:
        """Change in basis points over n trading days."""
        if len(vals) > n:
            return round((vals[0] - vals[n]) * 100.0, 1)
        return None

    def _percentile(vals: list[float], v: float | None) -> int | None:
        if v is None or not vals:
            return None
        below = sum(1 for x in vals if x <= v)
        return int(round(100.0 * below / len(vals)))

    def _latest(vals: list[float]) -> float | None:
        return round(vals[0], 4) if vals else None

    # --- 1. Yield curve ----------------------------------------------------
    dgs2  = _vals("DGS2")
    dgs10 = _vals("DGS10")
    dgs30 = _vals("DGS30")
    dgs3m = _vals("DGS3MO")
    t10y2y = _vals("T10Y2Y")
    t10y3m = _vals("T10Y3M")

    # 5s30s spread we compute ourselves (FRED doesn't ship it as a series)
    # using DGS5 isn't in our set; approximate "belly-to-long" via 10s30s.
    spread_10s30s = None
    if dgs10 and dgs30:
        spread_10s30s = round(dgs30[0] - dgs10[0], 3)

    curve_2s10s_latest = _latest(t10y2y) or (
        round(dgs10[0] - dgs2[0], 3) if dgs10 and dgs2 else None
    )
    curve_3m10y_latest = _latest(t10y3m) or (
        round(dgs10[0] - dgs3m[0], 3) if dgs10 and dgs3m else None
    )

    # Curve regime (bull/bear * steepen/flatten) from 5d deltas in 2Y and 10Y
    regime = "unknown"
    d10_5d = _delta_bp(dgs10, 5)
    d2_5d  = _delta_bp(dgs2, 5)
    if d10_5d is not None and d2_5d is not None:
        # Steepening = 2s10s widened; Flattening = 2s10s narrowed
        # Bull = yields falling on average; Bear = yields rising
        avg = (d10_5d + d2_5d) / 2.0
        steepening = (d10_5d - d2_5d) > 5.0   # 10Y rose more (or fell less) than 2Y
        flattening = (d10_5d - d2_5d) < -5.0
        if steepening and avg < -5.0:
            regime = "bull_steepening"
        elif steepening and avg > 5.0:
            regime = "bear_steepening"
        elif flattening and avg < -5.0:
            regime = "bull_flattening"
        elif flattening and avg > 5.0:
            regime = "bear_flattening"
        else:
            regime = "stable"

    # Estrella-Mishkin probit: P(recession 12m) = Phi(-0.5333 - 0.6629 * spread3m10y)
    recession_prob = None
    if curve_3m10y_latest is not None:
        import math
        z = -0.5333 - 0.6629 * curve_3m10y_latest
        # Normal CDF via erf
        recession_prob = round(0.5 * (1.0 + math.erf(z / math.sqrt(2.0))) * 100.0, 1)

    out["yield_curve"] = {
        "dgs3m": _latest(dgs3m),
        "dgs2":  _latest(dgs2),
        "dgs10": _latest(dgs10),
        "dgs30": _latest(dgs30),
        "spread_2s10s": curve_2s10s_latest,
        "spread_2s10s_delta_5d_bp": _delta_bp(t10y2y, 5) if t10y2y else None,
        "spread_3m10y": curve_3m10y_latest,
        "spread_3m10y_delta_5d_bp": _delta_bp(t10y3m, 5) if t10y3m else None,
        "spread_10s30s": spread_10s30s,
        "dgs10_delta_5d_bp": d10_5d,
        "dgs2_delta_5d_bp":  d2_5d,
        "regime": regime,
        "recession_prob_12m": recession_prob,
        "regime_notes": (
            "bull_steepening: Fed-cuts-into-weakness; bear_steepening: "
            "inflation/fiscal concern; bull_flattening: growth fading; "
            "bear_flattening: Fed-hike risk"
        ),
    }

    # --- 2. Credit spreads -------------------------------------------------
    hy = _vals("BAMLH0A0HYM2")
    ig = _vals("BAMLC0A0CM")

    hy_latest = _latest(hy)
    ig_latest = _latest(ig)
    hy_d20 = _delta_bp(hy, 20)
    ig_d20 = _delta_bp(ig, 20)
    hy_pct = _percentile(hy, hy_latest)
    ig_pct = _percentile(ig, ig_latest)

    credit_reasons: list[str] = []
    if hy_d20 is not None and hy_d20 >= 50.0:
        credit_reasons.append(f"HY OAS +{hy_d20}bp over 4w (>=+50bp)")
    if hy_pct is not None and hy_pct >= 90:
        credit_reasons.append(f"HY OAS at {hy_pct}th pct of 90d (>=90th)")
    if ig_d20 is not None and ig_d20 >= 25.0:
        credit_reasons.append(f"IG OAS +{ig_d20}bp over 4w (>=+25bp)")

    out["credit"] = {
        "hy_oas": {
            "latest": hy_latest,
            "delta_5d_bp": _delta_bp(hy, 5),
            "delta_20d_bp": hy_d20,
            "pct_rank_90d": hy_pct,
        },
        "ig_oas": {
            "latest": ig_latest,
            "delta_5d_bp": _delta_bp(ig, 5),
            "delta_20d_bp": ig_d20,
            "pct_rank_90d": ig_pct,
        },
        "credit_stress": {
            "flag": bool(credit_reasons),
            "reasons": credit_reasons,
        },
        "hy_threshold_notes": (
            "<3.5 complacency; 3.5-5.0 normal; 5.0-7.0 stress; "
            "7.0-10.0 crisis; >10.0 panic (units: %)"
        ),
    }

    # --- 3. Breakevens -----------------------------------------------------
    t5y   = _vals("T5YIE")
    t10y  = _vals("T10YIE")
    t5y5y = _vals("T5YIFR")

    out["breakevens"] = {
        "be_5y":  {"latest": _latest(t5y),   "delta_20d_bp": _delta_bp(t5y, 20)},
        "be_10y": {"latest": _latest(t10y),  "delta_20d_bp": _delta_bp(t10y, 20)},
        "be_5y5y": {"latest": _latest(t5y5y), "delta_20d_bp": _delta_bp(t5y5y, 20)},
    }

    # --- 4. Systemic stress proxies ----------------------------------------
    mortg = _vals("MORTGAGE30US")
    real10 = _vals("DFII10")

    mbs_spread_latest = None
    mbs_spread_d20 = None
    if mortg and dgs10:
        mbs_spread_latest = round(mortg[0] - dgs10[0], 3)
        if len(mortg) > 4 and len(dgs10) > 20:
            # mortgage is weekly so ~4 obs ≈ 4 weeks; pair with 20d DGS10
            prior = mortg[4] - dgs10[20]
            mbs_spread_d20 = round((mbs_spread_latest - prior) * 100.0, 1)

    out["systemic"] = {
        "mbs_spread_proxy": mbs_spread_latest,
        "mbs_spread_delta_20d_bp": mbs_spread_d20,
        "real_yield_10y": _latest(real10),
        "real_yield_10y_delta_20d_bp": _delta_bp(real10, 20),
        "mbs_notes": "MORTGAGE30US - DGS10; historical avg 50-80bp, >130bp = stretched",
    }

    # --- 5. Four-signal scorecard -----------------------------------------
    def _score_curve() -> int:
        if curve_3m10y_latest is None:
            return 0
        # Recession warning territory
        if curve_3m10y_latest < 0 and curve_2s10s_latest is not None and curve_2s10s_latest < 0.20:
            return -2
        if curve_3m10y_latest < 0:
            return -1
        if curve_2s10s_latest is not None and curve_2s10s_latest > 1.0 and regime in ("bull_steepening",):
            return 2
        if curve_2s10s_latest is not None and curve_2s10s_latest > 0.5:
            return 1
        return 0

    def _score_credit() -> int:
        if hy_latest is None:
            return 0
        # HY OAS thresholds in %: <3.5 complacency, 3.5-5 normal, >5 stress
        if credit_reasons:
            return -2 if hy_d20 is not None and hy_d20 >= 75.0 else -1
        if hy_latest >= 7.0:
            return -2
        if hy_latest >= 5.0:
            return -1
        if hy_latest < 3.5 and hy_d20 is not None and hy_d20 > 10.0:
            return -1  # complacency + starting to widen
        if hy_latest < 3.5:
            return 0   # tight & stable -- no juice, but no warning yet
        return 1       # normal range, stable

    def _score_breakevens() -> int:
        b = _latest(t5y5y) or _latest(t10y)
        d = _delta_bp(t5y5y, 20) if t5y5y else _delta_bp(t10y, 20)
        if b is None or d is None:
            return 0
        if abs(d) >= 30.0:
            return -2  # fast move in either direction = regime shift risk
        if abs(d) >= 15.0:
            return -1
        if 2.0 <= b <= 2.6:
            return 1
        return 0

    def _score_systemic() -> int:
        if mbs_spread_latest is None:
            return 0
        if mbs_spread_d20 is not None and mbs_spread_d20 >= 30.0:
            return -2
        if mbs_spread_latest >= 1.5:
            return -1
        if mbs_spread_latest <= 0.8:
            return 1
        return 0

    s_curve  = _score_curve()
    s_credit = _score_credit()
    s_be     = _score_breakevens()
    s_sys    = _score_systemic()
    composite = s_curve + s_credit + s_be + s_sys

    if composite >= 4:
        label = "risk_on"
    elif composite >= 0:
        label = "neutral"
    elif composite >= -4:
        label = "defensive"
    else:
        label = "acute_defensive"

    out["scorecard"] = {
        "yield_curve":  s_curve,
        "credit":       s_credit,
        "breakevens":   s_be,
        "systemic":     s_sys,
        "composite":    composite,
        "label":        label,
        "scale":        "-8..+8; <=-5 acute_defensive, -4..-1 defensive, 0..+3 neutral, >=+4 risk_on",
    }

    return out


def _build_market_shock(
    fmp: FMPClient,
    macro_data: dict,
    market_news: list,
    forex_news: list,
    stock_news: list,
    company_news: dict,
    bond_signals: dict | None = None,
) -> dict:
    """Detect short-horizon market shocks so the analyzer can override the 60d
    rotation windows and lift tilt limits when a structural event hits.

    Combines hard price signals (1d / 5d returns and z-scores for SPY, DXY,
    VIX) with a keyword scan over the day's news. Outputs a composite
    ``shock_level`` 0-3:

      0 = none        — business as usual; use the 60d framework verbatim
      1 = watch       — single elevated indicator; flag in narrative only
      2 = elevated    — multiple indicators fire; allow window shortening
      3 = acute       — broad shock (e.g. tariff weekend); permit aggressive
                        tilts and immediate de-risking

    The analyzer prompt defines exactly what each level unlocks.
    """
    out: dict = {
        "shock_level": 0,
        "shock_label": "none",
        "triggers": [],
        "spy": {},
        "dxy": {},
        "vix": {},
        "news_hits_total": 0,
        "news_hits_by_category": {},
        "news_examples": [],
        "scoring_rubric": (
            "0=none 1=watch 2=elevated (window override permitted) "
            "3=acute (aggressive tilts + de-risking permitted)"
        ),
    }

    def _close(row: dict) -> float | None:
        v = row.get("price") if row.get("price") is not None else row.get("close")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    triggers: list[str] = []

    # --- 1. SPY 1d / 5d returns + 1d z-score vs 60d realized vol -----------
    try:
        spy_rows = fmp.get_historical_price_light("SPY")
    except Exception as e:  # noqa: BLE001
        logger.warning("Market shock: SPY history fetch failed: %s", e)
        spy_rows = []
    spy_closes = [_close(r) for r in spy_rows[: _SHOCK_VOL_LOOKBACK_DAYS + 2]]
    spy_closes = [c for c in spy_closes if c]
    spy_1d_pct: float | None = None
    spy_5d_pct: float | None = None
    spy_1d_z: float | None = None
    if len(spy_closes) >= 2:
        spy_1d_pct = round((spy_closes[0] / spy_closes[1] - 1.0) * 100.0, 2)
    if len(spy_closes) >= _SHOCK_SHORT_WINDOW_DAYS + 1:
        spy_5d_pct = round(
            (spy_closes[0] / spy_closes[_SHOCK_SHORT_WINDOW_DAYS] - 1.0) * 100.0, 2
        )
    if len(spy_closes) >= _SHOCK_VOL_LOOKBACK_DAYS + 1:
        daily_rets = [
            (spy_closes[i] / spy_closes[i + 1] - 1.0) * 100.0
            for i in range(_SHOCK_VOL_LOOKBACK_DAYS)
        ]
        mean = sum(daily_rets) / len(daily_rets)
        var = sum((r - mean) ** 2 for r in daily_rets) / len(daily_rets)
        sd = var ** 0.5
        if sd > 0 and spy_1d_pct is not None:
            spy_1d_z = round((spy_1d_pct - mean) / sd, 2)
    out["spy"] = {
        "return_1d_pct": spy_1d_pct,
        "return_5d_pct": spy_5d_pct,
        "return_1d_zscore": spy_1d_z,
        "vol_lookback_days": _SHOCK_VOL_LOOKBACK_DAYS,
        "latest_date": spy_rows[0].get("date") if spy_rows else None,
    }
    if spy_1d_z is not None and abs(spy_1d_z) >= 3.5:
        triggers.append(f"SPY 1d z-score {spy_1d_z} (|z|>=3.5, acute)")
    elif spy_1d_z is not None and abs(spy_1d_z) >= 2.5:
        triggers.append(f"SPY 1d z-score {spy_1d_z} (|z|>=2.5, elevated)")
    elif spy_1d_z is not None and abs(spy_1d_z) >= 1.5:
        triggers.append(f"SPY 1d z-score {spy_1d_z} (|z|>=1.5, watch)")

    # --- 2. DXY 1d / 5d % change ------------------------------------------
    dxy_rows = macro_data.get("DTWEXBGS") or []
    dxy_vals = [
        float(r["value"]) for r in dxy_rows
        if r.get("value") not in (None, ".", "")
    ]
    dxy_1d_pct: float | None = None
    dxy_5d_pct: float | None = None
    if len(dxy_vals) >= 2:
        dxy_1d_pct = round((dxy_vals[0] / dxy_vals[1] - 1.0) * 100.0, 2)
    if len(dxy_vals) >= _SHOCK_SHORT_WINDOW_DAYS + 1:
        dxy_5d_pct = round(
            (dxy_vals[0] / dxy_vals[_SHOCK_SHORT_WINDOW_DAYS] - 1.0) * 100.0, 2
        )
    out["dxy"] = {
        "return_1d_pct": dxy_1d_pct,
        "return_5d_pct": dxy_5d_pct,
    }
    if dxy_5d_pct is not None and abs(dxy_5d_pct) >= 3.0:
        triggers.append(f"DXY 5d move {dxy_5d_pct}% (|>=3%|, elevated)")

    # --- 3. VIX level + 1d change ------------------------------------------
    vix_rows = macro_data.get("VIXCLS") or []
    vix_vals: list[float] = []
    for r in vix_rows:
        v = r.get("value")
        if v in (None, ".", ""):
            continue
        try:
            vix_vals.append(float(v))
        except (TypeError, ValueError):
            continue
    vix_latest = vix_vals[0] if vix_vals else None
    vix_1d_pct = (
        round((vix_vals[0] / vix_vals[1] - 1.0) * 100.0, 2)
        if len(vix_vals) >= 2 and vix_vals[1] else None
    )
    out["vix"] = {
        "latest": vix_latest,
        "return_1d_pct": vix_1d_pct,
    }
    if vix_latest is not None and vix_latest >= 35.0:
        triggers.append(f"VIX {vix_latest} >=35 (elevated absolute level)")
    if vix_1d_pct is not None and vix_1d_pct >= 30.0:
        triggers.append(f"VIX 1d jump {vix_1d_pct}% (>=30%, elevated)")

    # --- 4. News keyword scan ----------------------------------------------
    def _text(item: dict) -> str:
        parts = [
            item.get("headline") or item.get("title") or "",
            item.get("summary") or item.get("text") or "",
        ]
        return " ".join(p for p in parts if p).lower()

    pool: list[dict] = []
    pool.extend(market_news or [])
    pool.extend(forex_news or [])
    pool.extend(stock_news or [])
    for items in (company_news or {}).values():
        pool.extend(items or [])

    hits_by_cat: dict[str, int] = {cat: 0 for cat in _SHOCK_KEYWORDS}
    examples: list[dict] = []
    seen_titles: set[str] = set()
    for item in pool:
        body = _text(item)
        if not body:
            continue
        for cat, kws in _SHOCK_KEYWORDS.items():
            for kw in kws:
                if kw in body:
                    hits_by_cat[cat] += 1
                    title = item.get("headline") or item.get("title") or ""
                    if title and title not in seen_titles and len(examples) < 8:
                        examples.append({
                            "category": cat,
                            "keyword": kw,
                            "headline": title[:240],
                            "source": item.get("source") or item.get("site") or "",
                            "date": item.get("datetime") or item.get("date") or "",
                        })
                        seen_titles.add(title)
                    break  # avoid double-counting same item under same category
    total_hits = sum(hits_by_cat.values())
    out["news_hits_total"] = total_hits
    out["news_hits_by_category"] = hits_by_cat
    out["news_examples"] = examples
    if total_hits >= 20:
        triggers.append(f"News keyword hits {total_hits} (>=20, elevated)")
    elif total_hits >= 10:
        triggers.append(f"News keyword hits {total_hits} (>=10, watch+)")
    elif total_hits >= 5:
        triggers.append(f"News keyword hits {total_hits} (>=5, watch)")

    # --- 4b. Credit-stress signal from bond_signals ------------------------
    credit_stress = False
    if bond_signals:
        cs = (bond_signals.get("credit") or {}).get("credit_stress") or {}
        if cs.get("flag"):
            credit_stress = True
            for reason in cs.get("reasons", []):
                triggers.append(f"Credit stress: {reason}")

    # --- 5. Composite shock level -----------------------------------------
    level = 0
    # Acute: extreme tape OR (elevated tape + heavy news cluster)
    if (spy_1d_z is not None and abs(spy_1d_z) >= 3.5) or total_hits >= 25:
        level = 3
    elif (
        (spy_1d_z is not None and abs(spy_1d_z) >= 2.5)
        or (vix_latest is not None and vix_latest >= 35.0)
        or (vix_1d_pct is not None and vix_1d_pct >= 30.0)
        or total_hits >= 15
        or (dxy_5d_pct is not None and abs(dxy_5d_pct) >= 3.0 and total_hits >= 8)
    ):
        level = 2
    elif (
        (spy_1d_z is not None and abs(spy_1d_z) >= 1.5)
        or total_hits >= 5
        or (vix_1d_pct is not None and vix_1d_pct >= 15.0)
        or credit_stress
    ):
        level = 1

    # Credit stress paired with any equity-side signal escalates to L2.
    if credit_stress and level == 1 and (
        (spy_1d_z is not None and abs(spy_1d_z) >= 1.5)
        or total_hits >= 5
        or (vix_1d_pct is not None and vix_1d_pct >= 15.0)
    ):
        level = 2

    out["shock_level"] = level
    out["shock_label"] = {0: "none", 1: "watch", 2: "elevated", 3: "acute"}[level]
    out["triggers"] = triggers

    return out


def _write_sentiment_history(today: str, snapshot: dict) -> None:
    news = snapshot.get("news", {})
    upsert_entity("SentimentHistory", {
        "PartitionKey":         today,
        "RowKey":               "market_overview",
        "market_news_count":    len(news.get("market", [])),
        "forex_news_count":     len(news.get("forex", [])),
        "company_news_count":   sum(len(v) for v in news.get("company", {}).values()),
        "positions_count":      len(snapshot.get("portfolio", {}).get("positions", [])),
        "etrade_source":        snapshot.get("portfolio", {}).get("source", "unknown"),
    })
