**Data Sources Reference**

Azure-Native Portfolio Automation System

v1.3 — May 2026 — Companion to Architecture Spec v1.0

# 1. Overview

This document catalogs every data point the analysis pipeline consumes, organized by source API. It serves as the definitive reference for the Collector Function implementation and for estimating API budget consumption.

The pipeline uses six external data sources (five free, one paid) to feed the Claude-powered analyzer with portfolio positions, fundamentals, macro indicators, market prices, sentiment signals, congressional trading intelligence, and economic news.

**Sources: **E*TRADE (portfolio data, deferred to v1.1), Financial Modeling Prep (fundamentals, earnings, ETF data, congressional trades, stock news), FRED (macro, volatility, credit, FX), Polygon.io (prices), Finnhub (financial news, sentiment), Alpaca (paper trading), and Claude via Azure Foundry (analysis engine).

# 2. Data Sources — Complete Reference

Each table section below represents one API source. The Endpoint column shows the specific API path or FRED series code. Phase indicates whether the data is needed for v1 (analysis pipeline) or v2 (paper execution pipeline).

| Data Point | Endpoint / Series | Cost | Phase | Notes |
| --- | --- | --- | --- | --- |
| E*TRADE API  —  Portfolio Data (OAuth 1.0a) | E*TRADE API  —  Portfolio Data (OAuth 1.0a) | E*TRADE API  —  Portfolio Data (OAuth 1.0a) | E*TRADE API  —  Portfolio Data (OAuth 1.0a) | E*TRADE API  —  Portfolio Data (OAuth 1.0a) |
| Account positions (tickers, shares, cost basis, current value) | /v1/accounts/{id}/portfolio | Free | v1 | Replaces static portfolio-holdings.json |
| Account balances (cash, total equity) | /v1/accounts/{id}/balance | Free | v1 | Real-time cash and margin data |
| Option chains (put/call volumes, pricing per strike) | /v1/market/optionchains | Free | v1 | Pull for SPY, QQQ, top holdings; compute P/C ratio and IV |
| Option expiration dates | /v1/market/optionexpiredate | Free | v1 | Needed to query the right expiration month |
| Financial Modeling Prep  —  Valuation & Fundamentals | Financial Modeling Prep  —  Valuation & Fundamentals | Financial Modeling Prep  —  Valuation & Fundamentals | Financial Modeling Prep  —  Valuation & Fundamentals | Financial Modeling Prep  —  Valuation & Fundamentals |
| P/E ratio (trailing, TTM) | /stable/ratios-ttm | Free | v1 | Per holding; compare to sector avg |
| P/S, P/B, EV/EBITDA | /stable/ratios-ttm | Free | v1 | Full valuation multiples suite |
| Dividend yield | /stable/profile | Free | v1 | Important for IDVO income analysis |
| DCF valuation | /stable/discounted-cash-flow | Free | v1 | Intrinsic value estimate vs market price |
| Company rating / financial score | /stable/rating | Free | v1 | Composite health score per holding |
| Enterprise value | /stable/enterprise-values | Free | v1 | For EV-based multiples |
| Financial growth rates | /stable/financial-growth | Free | v1 | Revenue and earnings growth trends |
| Financial Modeling Prep  —  Earnings & Estimates | Financial Modeling Prep  —  Earnings & Estimates | Financial Modeling Prep  —  Earnings & Estimates | Financial Modeling Prep  —  Earnings & Estimates | Financial Modeling Prep  —  Earnings & Estimates |
| Earnings calendar (date + EPS estimate) | /stable/earning-calendar | Free | v1 | Flag holdings reporting within 7 days |
| Actual vs estimated EPS (surprise) | /stable/earning-calendar | Free | v1 | Post-earnings beat/miss tracking |
| Revenue estimates (consensus) | /stable/analyst-estimates | Free | v1 | Top-line growth expectations |
| Analyst price targets | /stable/price-target | Free | v1 | Consensus upside/downside vs current |
| Insider trades | /stable/insider-trading | Free | v1 | Insider buying = bullish signal |
| Financial Modeling Prep  —  Congressional & Smart Money Trading | Financial Modeling Prep  —  Congressional & Smart Money Trading | Financial Modeling Prep  —  Congressional & Smart Money Trading | Financial Modeling Prep  —  Congressional & Smart Money Trading | Financial Modeling Prep  —  Congressional & Smart Money Trading |
| Senate trading activity (by ticker) | /stable/senate-trading | Free | v1 | Senator trades for your holdings; detect political interest |
| Senate latest disclosures | /stable/senate-latest | Free | v1 | Most recent Senate trade filings across all tickers |
| Senate disclosure RSS feed | /stable/senate-disclosure-rss | Free | v1 | Real-time feed of new Senate filings |
| House trading activity (by ticker) | /stable/house-trading | Free | v1 | House member trades for your holdings |
| House latest disclosures | /stable/house-latest | Free | v1 | Most recent House trade filings across all tickers |
| House disclosure RSS feed | /stable/house-disclosure-rss | Free | v1 | Real-time feed of new House filings |
| Financial Modeling Prep  —  Company & Sector Profile | Financial Modeling Prep  —  Company & Sector Profile | Financial Modeling Prep  —  Company & Sector Profile | Financial Modeling Prep  —  Company & Sector Profile | Financial Modeling Prep  —  Company & Sector Profile |
| Sector and industry classification | /stable/profile | Free | v1 | Portfolio sector concentration analysis |
| Market capitalization | /stable/profile | Free | v1 | Size-weighted risk assessment |
| Sector average P/E | /v4/sector_price_earning_ratio | Free | v1 | Relative valuation benchmark |
| Financial Modeling Prep  —  News & Events | Financial Modeling Prep  —  News & Events | Financial Modeling Prep  —  News & Events | Financial Modeling Prep  —  News & Events | Financial Modeling Prep  —  News & Events |
| Stock-specific news (filtered by ticker) | /stable/stock-news | Free | v1 | Replaces generic RSS; more targeted |
| Press releases | /stable/press-releases | Free | v1 | Official company announcements |
| Dividend calendar | /stable/dividends | Free | v1 | Ex-dates, payment dates, yield |
| Stock splits calendar | /stable/stock-split-calendar | Free | v1 | Adjust position tracking |
| Economic events calendar | /v3/economic_calendar | Free | v1 | FOMC, CPI release dates, etc. |
| Financial Modeling Prep  —  International ETF Look-Through (IDVO, IDMO, AIA) | Financial Modeling Prep  —  International ETF Look-Through (IDVO, IDMO, AIA) | Financial Modeling Prep  —  International ETF Look-Through (IDVO, IDMO, AIA) | Financial Modeling Prep  —  International ETF Look-Through (IDVO, IDMO, AIA) | Financial Modeling Prep  —  International ETF Look-Through (IDVO, IDMO, AIA) |
| Top holdings per ETF (tickers, weights) | /stable/etf-holdings | Free | v1 | See inside international ETFs |
| Country allocation (%) | /stable/etf-country-allocation | Free | v1 | Geographic concentration risk |
| Sector allocation (%) | /stable/etf-sector-allocation | Free | v1 | Sector exposure across regions |
| ETF info (expense ratio, AUM, strategy) | /stable/etf-info | Free | v1 | Fund-level metadata |
| Finnhub  —  Financial News & Market Intelligence | Finnhub  —  Financial News & Market Intelligence | Finnhub  —  Financial News & Market Intelligence | Finnhub  —  Financial News & Market Intelligence | Finnhub  —  Financial News & Market Intelligence |
| Market news (general financial headlines) | /api/v1/news?category=general | Free | v1 | Breaking financial news; replaces NYT for market coverage |
| Company news (by ticker + date range) | /api/v1/company-news | Free | v1 | Ticker-specific news; complements FMP stock news |
| SEC filings search (keyword in filings) | /api/v1/stock/search-in-filing | Free | v1 | Search 10-K, 10-Q, 8-K for specific terms |
| Social sentiment (Reddit, Twitter) | /api/v1/stock/social-sentiment | Free | v1 | Retail investor sentiment signal |
| FRED (Federal Reserve)  —  US Macro Indicators | FRED (Federal Reserve)  —  US Macro Indicators | FRED (Federal Reserve)  —  US Macro Indicators | FRED (Federal Reserve)  —  US Macro Indicators | FRED (Federal Reserve)  —  US Macro Indicators |
| Fed funds rate | FEDFUNDS | Free | v1 | Monetary policy stance |
| US 10-year treasury yield | DGS10 | Free | v1 | Risk-free rate benchmark |
| CPI year-over-year | CPIAUCSL | Free | v1 | Headline inflation |
| Core CPI year-over-year | CPILFESL | Free | v1 | Inflation ex food & energy |
| US dollar index (DXY proxy) | DTWEXBGS | Free | v1 | Dollar strength impacts int'l returns |
| WTI crude oil | DCOILWTICO | Free | v1 | Energy/inflation signal |
| Unemployment rate | UNRATE | Free | v1 | Labor market health |
| GDP growth (quarterly) | A191RL1Q225SBEA | Free | v1 | Economic growth trend |
| FRED  —  Volatility & Credit Sentiment | FRED  —  Volatility & Credit Sentiment | FRED  —  Volatility & Credit Sentiment | FRED  —  Volatility & Credit Sentiment | FRED  —  Volatility & Credit Sentiment |
| VIX (CBOE Volatility Index, EOD) | VIXCLS | Free | v1 | Primary fear gauge |
| High-yield credit spread | BAMLH0A0HYM2 | Free | v1 | Risk appetite indicator |
| Investment-grade credit spread | BAMLC0A0CM | Free | v1 | Credit market stress |
| FRED  —  International Macro (for IDVO, IDMO, AIA) | FRED  —  International Macro (for IDVO, IDMO, AIA) | FRED  —  International Macro (for IDVO, IDMO, AIA) | FRED  —  International Macro (for IDVO, IDMO, AIA) | FRED  —  International Macro (for IDVO, IDMO, AIA) |
| Euro area CPI | EA19CPALTT01GYM | Free | v1 | European inflation context |
| ECB interest rate | ECBMRRFR | Free | v1 | European monetary policy |
| China manufacturing PMI | MPMICTCN | Free | v1 | Asia growth signal (AIA driver) |
| Japan 10-year yield | IRLTLT01JPM156N | Free | v1 | BOJ policy impact on Asian markets |
| EUR/USD exchange rate | DEXUSEU | Free | v1 | European holdings FX impact |
| USD/JPY exchange rate | DEXJPUS | Free | v1 | Japan holdings FX impact |
| USD/CNY exchange rate | DEXCHUS | Free | v1 | China holdings FX impact |
| Polygon.io  —  Market Prices | Polygon.io  —  Market Prices | Polygon.io  —  Market Prices | Polygon.io  —  Market Prices | Polygon.io  —  Market Prices |
| EOD prices (SPY, QQQ, GLD, all holdings) | /v2/aggs/ticker/{t}/prev | Free | v1 | Daily close prices for all tickers |
| Emerging markets proxy (EEM) | /v2/aggs/ticker/EEM/prev | Free | v1 | EM benchmark for AIA comparison |
| Alpaca  —  Paper Trading (Phase 2) | Alpaca  —  Paper Trading (Phase 2) | Alpaca  —  Paper Trading (Phase 2) | Alpaca  —  Paper Trading (Phase 2) | Alpaca  —  Paper Trading (Phase 2) |
| Paper account positions & P&L | /v2/positions | Free | v2 | Track simulated trades |
| Paper account cash balance | /v2/account | Free | v2 | Paper portfolio value |
| Deferred  —  Future Enhancements (v2+) | Deferred  —  Future Enhancements (v2+) | Deferred  —  Future Enhancements (v2+) | Deferred  —  Future Enhancements (v2+) | Deferred  —  Future Enhancements (v2+) |
| AAII sentiment survey | AAII website / Nasdaq Data Link | Free* | v2+ | Weekly; add when core pipeline stable |
| ETF fund flows | VettaFi / ETF.com | Paid | v2+ | Requires paid subscription |
| CFTC positioning (COT report) | cftc.gov CSV download | Free | v2+ | Weekly CSV parse; niche signal |

# 3. Key Vault Secrets

All API credentials are stored in Azure Key Vault (kv-pfauto-prod) and accessed via Managed Identity. No secrets in code or configuration files.

| Secret Name | Purpose | Rotation | Notes |
| --- | --- | --- | --- |
| EtradeConsumerKey | E*TRADE OAuth consumer key | Manual / yearly | Individual key tied to your account |
| EtradeConsumerSecret | E*TRADE OAuth consumer secret | Manual / yearly | Paired with consumer key |
| EtradeAccessToken | E*TRADE session access token | Auto / daily | Renewed programmatically before each run |
| EtradeAccessTokenSecret | E*TRADE session token secret | Auto / daily | Paired with access token |
| FmpApiKey | Financial Modeling Prep API key | Manual / quarterly | Free tier; 250 requests/day |
| FredApiKey | FRED API key | Manual / annually | Free; no rate limit at this volume |
| PolygonApiKey | Polygon.io API key | Manual / quarterly | Free tier; 5 calls/min |
| AlpacaApiKey | Alpaca paper trading key | Manual / quarterly | Phase 2 only |
| AlpacaApiSecret | Alpaca paper trading secret | Manual / quarterly | Phase 2 only |
| AnthropicApiKey | Claude API key | Manual / quarterly | Most sensitive; enable billing alerts |
| FinnhubApiKey | Finnhub API key | Manual / annually | Free: 60 calls/min; market news + sentiment |

**Note: **E*TRADE access tokens expire at midnight ET daily. A scheduled Azure Function (running at 11:30 PM ET) renews the token and updates Key Vault automatically. If renewal fails, the system alerts via Teams for manual re-authentication.

# 4. Daily API Budget

Estimated daily API call volume assuming a portfolio of approximately 15-20 holdings (including 3 international ETFs) running once per weekday at 06:00 ET.

| Source | Daily Calls | Monthly Calls | Cost/mo | Limit |
| --- | --- | --- | --- | --- |
| E*TRADE | ~8 (acct + options) | ~160 | $0 | No daily limit; OAuth session limit |
| FMP | ~45 (fundamentals + ETFs) | ~900 | $0 | Free tier: 250/day (sufficient) |
| FRED | ~18 (macro series) | ~360 | $0 | No practical limit |
| Polygon.io | ~25 (EOD prices) | ~500 | $0 | Free tier: 5 calls/min |
| Anthropic (Claude) | 1 analysis call | ~20 | $6-10 | ~50K tokens/run |
| TOTAL |  |  | $6-10 | All within free tier limits |

**FMP budget detail: **~20 holdings x 1 call each (profile+ratios combined) = 20 calls. Plus 3 ETF look-throughs x 4 calls each = 12 calls. Plus earnings calendar, news, sector averages, economic calendar = ~13 calls. Plus congressional trading (Senate latest + House latest + per-ticker checks) = ~8 calls. Total ~53/day, well within the 250/day free tier limit. Finnhub adds ~10 calls/day (60 calls/min limit — effectively unlimited for daily cadence).

# 5. Data Flow Summary

The Collector Function executes the following sequence at 06:00 ET on weekdays:

**Step 1 — E*TRADE: **Renew OAuth token, pull account positions and balances. This determines the current portfolio and the list of tickers for all subsequent API calls.

**Step 2 — FMP: **For each ticker from Step 1, pull fundamentals (ratios, profile, rating, DCF). For ETF tickers (IDVO, IDMO, AIA), also pull holdings, country allocation, and sector weights. Pull earnings calendar, insider trades, and news for all tickers. Pull economic events calendar.

**Step 3 — FRED: **Pull all macro series (US + international) from the macro-series.json config file. Includes rates, inflation, VIX, credit spreads, and FX rates.

**Step 4 — Polygon: **Pull EOD prices for all holdings plus benchmark ETFs (SPY, QQQ, GLD, EEM).

**Step 5 — E*TRADE (options): **Pull option chains for SPY, QQQ, and top 3 holdings by weight. Compute put/call volume ratios.

**Step 6 — Assemble: **Normalize all data into the daily snapshot JSON and write to Blob Storage at daily-snapshots/YYYY-MM-DD.json. The Analyzer Function picks it up via blob trigger.

# 6. International Holdings Coverage

Your international ETF holdings require additional data layers beyond what domestic equities need:

**IDVO (Amplify International Enhanced Dividend Income ETF): **Holds ~54 international dividend-paying ADRs with a covered call overlay. The ETF look-through from FMP reveals the underlying stocks, while EUR/USD and USD/JPY from FRED capture the FX risk. ECB rate and Euro area CPI provide the European macro context.

**IDMO (Invesco S&P International Developed Momentum ETF): **Tracks international developed-market momentum stocks. Country allocation from FMP shows geographic tilt; combined with DXY and individual FX rates from FRED to assess currency headwinds.

**AIA (iShares Asia 50 ETF): **Concentrated in 50 large-cap Asian stocks. China PMI and USD/CNY from FRED, plus Japan 10Y yield, provide the macro backdrop. EEM from Polygon serves as the broader EM benchmark.

*This document is a companion to the Azure-Native Portfolio Automation System specification (v1.0). It details the data inputs required by the Collector Function and should be updated whenever new data sources are added or API endpoints change.*
