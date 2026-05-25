**Azure Storage Architecture**

Azure-Native Portfolio Automation System

v1.0 — May 2026 — Blob Storage + Table Storage Design

# 1. Design Philosophy

The storage layer serves three purposes: archival (complete audit trail of every data collection and analysis), queryable history (fast access to time-series data for trend analysis), and trade accountability (full lifecycle tracking from recommendation through outcome).

The architecture uses both Blob Storage and Table Storage within a single Azure Storage Account (stpfautoprod). Blobs store complete, immutable snapshots and reports. Tables store denormalized, queryable rows optimized for the specific time-series queries the analyzer performs.

The collector writes to both layers in a single run: first the full JSON snapshot to blob (audit trail), then denormalized rows to tables (queryable index). The blob is the source of truth; the tables can always be rebuilt from blobs if needed.

# 2. Blob Storage Containers

All blobs are stored in the stpfautoprod storage account under containers organized by function. Lifecycle management policies automatically transition blobs from Hot to Cool tier after 90 days.

| Blob Path | Content | Retention | Tier | Notes |
| --- | --- | --- | --- | --- |
| Raw Data Snapshots | Raw Data Snapshots | Raw Data Snapshots | Raw Data Snapshots | Raw Data Snapshots | Raw Data Snapshots | Raw Data Snapshots | Raw Data Snapshots | Raw Data Snapshots | Raw Data Snapshots |
| daily-snapshots/YYYY-MM-DD.json | Full collector output (all APIs) | Indefinite | Hot 90d / Cool | Primary audit trail; backtesting source |
| daily-snapshots/diffs/YYYY-MM-DD.json | Delta from previous day | 1 year | Cool | Pre-computed diff for analyzer efficiency |
| Analysis Reports | Analysis Reports | Analysis Reports | Analysis Reports | Analysis Reports | Analysis Reports | Analysis Reports | Analysis Reports | Analysis Reports | Analysis Reports |
| daily-reports/YYYY-MM-DD.md | Claude analysis markdown report | Indefinite | Hot 90d / Cool | Delivered to Teams/email/OneDrive |
| daily-reports/YYYY-MM-DD.json | Structured analysis metadata | Indefinite | Cool | Token count, cost, duration, model used |
| Trade Recommendations & Execution | Trade Recommendations & Execution | Trade Recommendations & Execution | Trade Recommendations & Execution | Trade Recommendations & Execution | Trade Recommendations & Execution | Trade Recommendations & Execution | Trade Recommendations & Execution | Trade Recommendations & Execution | Trade Recommendations & Execution |
| daily-trades/YYYY-MM-DD.json | Trade recommendations from Claude | Indefinite | Hot 90d / Cool | Triggers approval Logic App |
| daily-approvals/YYYY-MM-DD.json | Approval/rejection record | 7 years | Cool | Who approved, when, which trades |
| daily-executions/YYYY-MM-DD.json | Alpaca paper execution results | 7 years | Cool | Fill prices, commissions, order IDs |
| daily-rejections/YYYY-MM-DD.json | Rejected trade records | 7 years | Cool | Rejection reason and timestamp |
| Configuration | Configuration | Configuration | Configuration | Configuration | Configuration | Configuration | Configuration | Configuration | Configuration |
| config/project-instructions.md | Claude system prompt | Versioned | Hot | Analyzer reads this as system prompt |
| config/macro-series.json | FRED series IDs to collect | Versioned | Hot | Add new series here, no code change |
| config/news-feeds.json | RSS feed URLs | Versioned | Hot | Supplemental to FMP stock news |
| config/etf-watchlist.json | International ETFs for look-through | Versioned | Hot | IDVO, IDMO, AIA |

# 3. Table Storage Schemas

Six tables provide fast, queryable access to historical data. Each table is optimized for the specific access patterns the analyzer uses. PartitionKey and RowKey are chosen to support efficient range queries (e.g. all macro data for VIX over the last 60 days).

**Query pattern: **The analyzer typically queries by PartitionKey (specific ticker or series) with a RowKey range filter (date window). Azure Table Storage executes these as single-partition scans, which return results in milliseconds regardless of total table size.

## 3.1 PortfolioHistory

Tracks every holding's position data daily. Enables queries like: show me how IDVO's weight has changed over 90 days, or what was my total portfolio value trend this quarter.

**Example query: **PartitionKey eq 'IDVO' and RowKey ge '2026-02-01' and RowKey le '2026-05-01'

**Estimated rows: **~20 holdings x 250 trading days = ~5,000 rows/year

| Column | Type | Key | Description |
| --- | --- | --- | --- |
| PartitionKey | String | PK | Ticker symbol (e.g. IDVO, SPY, SGOV) |
| RowKey | String | RK | Date as YYYY-MM-DD (enables range queries) |
| Shares | Double |  | Number of shares held |
| CostBasis | Double |  | Total cost basis in USD |
| CurrentValue | Double |  | Market value at close |
| WeightPct | Double |  | Position weight as % of total portfolio |
| UnrealizedPL | Double |  | Unrealized profit/loss in USD |
| UnrealizedPLPct | Double |  | Unrealized P/L as percentage |
| DailyChangePct | Double |  | Day-over-day price change % |
| ClosePrice | Double |  | EOD closing price from Polygon |
| TotalPortfolioValue | Double |  | Entire portfolio value on this date |
| CashBalance | Double |  | Cash available (from E*TRADE balance) |

## 3.2 FundamentalsHistory

Daily valuation metrics from FMP for each holding. Enables trend analysis: is this stock getting cheaper or more expensive over time? How does its P/E compare to the sector average historically?

**Example query: **PartitionKey eq 'AAPL' and RowKey ge '2026-03-01'

**Estimated rows: **~20 holdings x 250 trading days = ~5,000 rows/year

| Column | Type | Key | Description |
| --- | --- | --- | --- |
| PartitionKey | String | PK | Ticker symbol |
| RowKey | String | RK | Date as YYYY-MM-DD |
| PE_TTM | Double |  | Price-to-earnings (trailing 12 months) |
| PS_TTM | Double |  | Price-to-sales |
| PB_TTM | Double |  | Price-to-book |
| EV_EBITDA | Double |  | Enterprise value / EBITDA |
| DividendYield | Double |  | Annual dividend yield % |
| DCF_Value | Double |  | Discounted cash flow intrinsic value |
| RatingScore | Double |  | FMP composite rating (1-5) |
| AnalystTarget | Double |  | Consensus analyst price target |
| MarketCap | Int64 |  | Market capitalization in USD |
| Sector | String |  | GICS sector classification |
| SectorAvgPE | Double |  | Average P/E for the sector (benchmark) |
| NextEarningsDate | String |  | Next earnings report date (YYYY-MM-DD) |
| EPS_EstimateNext | Double |  | Consensus EPS estimate for next quarter |

## 3.3 MacroHistory

All FRED macro series (US and international) stored with pre-computed deltas. Enables: what has VIX done over the last 30 days? Are credit spreads widening or tightening? How has EUR/USD moved since the ECB decision?

**Example query: **PartitionKey eq 'VIXCLS' and RowKey ge '2026-04-01'

**Estimated rows: **~18 series x 250 trading days = ~4,500 rows/year

| Column | Type | Key | Description |
| --- | --- | --- | --- |
| PartitionKey | String | PK | FRED series ID (e.g. VIXCLS, DGS10, BAMLH0A0HYM2) |
| RowKey | String | RK | Date as YYYY-MM-DD |
| Value | Double |  | Observation value for this date |
| SeriesName | String |  | Human-readable name (e.g. VIX Close, US 10Y Yield) |
| Category | String |  | Grouping: US_RATES, US_INFLATION, CREDIT, VOLATILITY, FX, INTL_MACRO |
| PreviousValue | Double |  | Prior observation for quick delta calculation |
| DeltaAbs | Double |  | Absolute change from previous value |
| DeltaPct | Double |  | Percentage change from previous value |

## 3.4 ETFLookthroughHistory

Tracks the internal composition of your international ETFs (IDVO, IDMO, AIA) over time. Holdings and allocations change gradually, so this captures the drift. Enables: has IDVO increased its Japan exposure? Did AIA's sector mix shift after the China PMI drop?

**Example query: **PartitionKey eq 'AIA' and RowKey ge '2026-01-01'

**Estimated rows: **3 ETFs x 250 trading days = 750 rows/year

| Column | Type | Key | Description |
| --- | --- | --- | --- |
| PartitionKey | String | PK | ETF ticker (IDVO, IDMO, AIA) |
| RowKey | String | RK | Date as YYYY-MM-DD |
| TopHoldingsJson | String (JSON) |  | Top 10 holdings as JSON array [{ticker, weight, name}] |
| CountryAllocJson | String (JSON) |  | Country weights as JSON [{country, weightPct}] |
| SectorAllocJson | String (JSON) |  | Sector weights as JSON [{sector, weightPct}] |
| ExpenseRatio | Double |  | Fund expense ratio |
| AUM | Int64 |  | Assets under management in USD |
| HoldingsCount | Int32 |  | Total number of holdings in the ETF |

## 3.5 SentimentHistory

Daily sentiment and risk indicators aggregated from multiple sources: VIX from FRED, credit spreads from FRED, put/call ratios computed from E*TRADE option chains, and FX rates. Partitioned by date for cross-indicator analysis on any given day.

**Example query: **PartitionKey eq '2026-05-06' (all indicators for today)

**Estimated rows: **~12 indicators x 250 trading days = ~3,000 rows/year

| Column | Type | Key | Description |
| --- | --- | --- | --- |
| PartitionKey | String | PK | Date as YYYY-MM-DD |
| RowKey | String | RK | Indicator name (e.g. SPY_PutCallRatio, VIX, HY_Spread) |
| Value | Double |  | Indicator value |
| Category | String |  | VOLATILITY, CREDIT, OPTIONS, FX |
| Signal | String |  | RISK_ON, RISK_OFF, NEUTRAL (computed by collector) |
| Percentile30d | Double |  | Where this value sits vs last 30 days (0-100) |

## 3.6 TradeHistory

The complete trade ledger. Every recommendation Claude makes gets a row, tracking the full lifecycle: recommendation, approval/rejection, execution, and outcome at 30/60/90 days. This is the primary table for validating the system's hit rate and for learning from past decisions.

**Example query: **PartitionKey eq '2026-05' (all trades recommended in May)

**Outcome tracking: **A scheduled function runs daily and backfills Price30d, Price60d, Price90d, and Outcome for trades that have reached those milestones. This automates the validation period analysis the spec requires.

**Estimated rows: **~2-5 recommendations/week = ~100-250 rows/year

| Column | Type | Key | Description |
| --- | --- | --- | --- |
| PartitionKey | String | PK | Year-month (e.g. 2026-05) |
| RowKey | String | RK | Unique trade ID (YYYY-MM-DD_TICKER_ACTION) |
| RecommendedDate | String |  | Date Claude recommended this trade |
| Ticker | String |  | Security ticker symbol |
| Action | String |  | BUY, SELL, TRIM, HOLD |
| Confidence | String |  | HIGH, MEDIUM, LOW |
| Urgency | String |  | IMMEDIATE, TODAY, THIS_WEEK |
| Reasoning | String |  | Claude's explanation for the recommendation |
| TargetWeightPct | Double |  | Target portfolio weight after trade |
| EstimatedShares | Int32 |  | Estimated number of shares to trade |
| EstimatedDollars | Double |  | Estimated dollar amount of trade |
| PriceAtRecommendation | Double |  | Stock price when trade was recommended |
| ApprovalStatus | String |  | APPROVED, REJECTED, EXPIRED, PENDING |
| ApprovedAt | String |  | Timestamp of approval/rejection |
| ExecutionPrice | Double |  | Actual fill price on Alpaca paper (Phase 2) |
| ExecutedShares | Int32 |  | Actual shares filled |
| Price30d | Double |  | Stock price 30 days after recommendation |
| Price60d | Double |  | Stock price 60 days after recommendation |
| Price90d | Double |  | Stock price 90 days after recommendation |
| Outcome | String |  | WIN, LOSS, NEUTRAL (computed at 30d mark) |
| RiskScoreBefore | Double |  | Portfolio risk score before trade |
| RiskScoreAfter | Double |  | Expected portfolio risk score after trade |

# 4. Collector Write Flow

After collecting data from all external APIs, the collector function writes to storage in two phases:

**Phase A — Blob write: **The complete normalized snapshot is written to daily-snapshots/YYYY-MM-DD.json as a single atomic operation. This is the immutable audit record.

**Phase B — Table writes: **The collector denormalizes the snapshot into batch inserts for each table. Azure Table Storage supports batch operations of up to 100 entities per partition, so each ticker's data goes in one batch. All six tables are populated in parallel using async operations.

**Failure handling: **If the blob write succeeds but table writes fail, the system alerts via Teams. The tables can be rebuilt from blobs using a recovery function (see Runbook-007 below). The blob is always the source of truth.

# 5. Analyzer Read Flow

The analyzer function reads from both storage layers to build the context for Claude:

**Today's snapshot: **Read from blob (daily-snapshots/YYYY-MM-DD.json) for the complete current-day data.

**Historical context: **Query tables for rolling windows. The analyzer pulls the last 5 days for short-term trends and the last 60 days for medium-term context. This is where tables dramatically outperform reading 60 separate blob files.

**Trade history: **Query TradeHistory for recent recommendations to provide continuity. Claude should know it recommended buying GLDM three days ago and shouldn't repeat the same recommendation without new information.

**System prompt: **Read from blob (config/project-instructions.md) as the Claude system prompt.

# 6. Lifecycle and Retention

Azure Blob Storage lifecycle management policies handle tier transitions automatically:

**0-90 days: Hot tier. **Fast access for the analyzer's rolling window queries and recent report retrieval.

**90+ days: Cool tier. **Lower storage cost, slightly higher access cost. Suitable for occasional backtesting or auditing.

**Trade-related blobs: 7-year retention. **Approvals, executions, and rejections are retained for compliance and accountability purposes.

**Table Storage: No automatic lifecycle. **Rows persist indefinitely at negligible cost. A cleanup function can be scheduled annually to archive rows older than 3 years if needed, but at ~20K rows/year the cost is under $0.50/year total.

# 7. Storage Cost Estimate

Storage is the cheapest component of the entire system. The combined blob and table storage cost is negligible compared to the Anthropic API cost ($6-10/month).

| Storage Component | Est. Daily Volume | Est. Annual Size | Est. Annual Cost |
| --- | --- | --- | --- |
| Blob Storage (snapshots, reports) | ~150 KB/day | ~40 MB/year | < $0.50/year (Hot) + $0.10/year (Cool archive) |
| Table Storage (all 6 tables) | ~200 rows/day | ~50K rows/year | < $0.50/year (storage) + $0.10/year (transactions) |
| TOTAL |  | ~90 MB/year | < $1.50/year |

# 8. Storage-Related Runbooks

**RUNBOOK-003: **Restoring a snapshot from soft-deleted blob (already in spec).

**RUNBOOK-007: **Rebuilding tables from blob archive. If table data becomes corrupted or lost, a recovery function iterates through blob snapshots and repopulates all six tables. Estimated runtime: ~5 minutes per month of data.

**RUNBOOK-008: **Manual trade outcome backfill. If the outcome tracking function misses entries, manually query TradeHistory for rows where Outcome is null and Price30d/60d/90d dates have passed, then backfill from Polygon price history.

**RUNBOOK-009: **Storage account failover. If stpfautoprod becomes unavailable, deploy a new storage account from Bicep, restore blobs from soft delete or geo-redundant backup, and rebuild tables via RUNBOOK-007.

*This document is a companion to the Azure-Native Portfolio Automation System specification (v1.0) and the Data Sources Reference (v1.1). It defines the storage layer architecture and should be updated when new tables or blob containers are added.*
