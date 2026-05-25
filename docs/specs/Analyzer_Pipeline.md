**Analyzer Pipeline & Memory Architecture**

Azure-Native Portfolio Automation System

v1.0 — May 2026 — Context Assembly, Foundry Integration, AI Search, News, Execution

# 1. Overview

This document specifies how the Analyzer Function transforms raw collected data into a structured context package for Claude, how Claude's response is parsed and routed to execution, and how the system maintains analytical memory across daily runs.

The architecture addresses three core challenges: assembling the right context without overwhelming the model, maintaining continuity across stateless API calls, and ensuring Claude's trade recommendations translate directly into executable Alpaca orders.

# 2. Claude via Azure Foundry

Claude is deployed through Microsoft Foundry, providing Azure-native billing, Managed Identity authentication, and seamless integration with the existing Azure infrastructure.

**Model selection: **Claude Sonnet 4.6 for daily analysis (cost-effective, 1M token context window). Claude Opus 4.6 for optional weekly deep-dive analysis requiring deeper reasoning. Both models are available in Foundry with identical API interfaces.

**Authentication: **Managed Identity on the Function App with Azure RBAC role assignment to the Foundry resource. No API keys needed for Claude; the Azure Function authenticates via Entra ID. This eliminates the AnthropicApiKey from Key Vault.

**Billing: **Claude usage is billed through your Azure subscription via the Microsoft Marketplace. This means a single Azure invoice covers all infrastructure, storage, and AI model costs. Eligible for Microsoft Azure Consumption Commitment (MACC).

**Context window: **Sonnet 4.6 and Opus 4.6 both offer a 1M token context window on Foundry. This is far more than needed for daily analysis (~30-45K tokens), providing significant headroom for richer historical context.

# 3. Context Assembly Pipeline

The Analyzer Function reads from blobs, tables, and (in Phase 1.5) Azure AI Search to build a structured context package. The goal is to give Claude everything it needs for today's analysis without sending raw data that adds noise without signal.

## 3.1 Context Budget

The following table shows each layer of context, its estimated token cost, and its priority. The total context budget stays under 50K tokens, leaving ample room for Claude's 8K token output within the 1M context window.

| Context Layer | Est. Tokens | Priority | Content |
| --- | --- | --- | --- |
| Permanent Context (every run) | Permanent Context (every run) | Permanent Context (every run) | Permanent Context (every run) | Permanent Context (every run) | Permanent Context (every run) | Permanent Context (every run) | Permanent Context (every run) | Permanent Context (every run) | Permanent Context (every run) |
| System prompt (project-instructions.md) | 3,000-5,000 | Required | Investment framework, risk tolerance, portfolio goals, output schema requirements |
| Full Detail Layer (today's data) | Full Detail Layer (today's data) | Full Detail Layer (today's data) | Full Detail Layer (today's data) | Full Detail Layer (today's data) | Full Detail Layer (today's data) | Full Detail Layer (today's data) | Full Detail Layer (today's data) | Full Detail Layer (today's data) | Full Detail Layer (today's data) |
| Today's portfolio snapshot | 2,000-4,000 | Required | All positions, balances, weights, P/L from E*TRADE. No summarization. |
| Today's fundamentals (all holdings) | 3,000-5,000 | Required | Full P/E, P/B, DCF, rating, earnings date, analyst target per holding from FMP |
| Today's macro dashboard | 1,000-2,000 | Required | All FRED series current values + day-over-day change from tables |
| Today's international ETF look-through | 1,500-2,500 | Required | IDVO/IDMO/AIA holdings, country allocation, sector weights from FMP |
| Today's news digest | 2,000-4,000 | Required | Filtered headlines from NYT, FMP, Fed/ECB RSS. 15-25 articles max. |
| Sentiment indicators | 500-1,000 | Required | VIX, credit spreads, put/call ratios, FX rates + 30-day percentiles |
| Continuity Layer (recent history) | Continuity Layer (recent history) | Continuity Layer (recent history) | Continuity Layer (recent history) | Continuity Layer (recent history) | Continuity Layer (recent history) | Continuity Layer (recent history) | Continuity Layer (recent history) | Continuity Layer (recent history) | Continuity Layer (recent history) |
| Last 5 daily reports (Claude's prior analysis) | 5,000-10,000 | High | Claude's own conclusions from the past week. Maintains analytical continuity. |
| Recent trade recommendations + outcomes | 1,000-2,000 | High | Last 10 trades from TradeHistory table. Prevents contradictions. |
| 30/60/90 day trend summaries | 1,500-3,000 | High | Pre-computed from tables: moving averages, trend direction, threshold crossings |
| Semantic Recall Layer (AI Search, Phase 1.5+) | Semantic Recall Layer (AI Search, Phase 1.5+) | Semantic Recall Layer (AI Search, Phase 1.5+) | Semantic Recall Layer (AI Search, Phase 1.5+) | Semantic Recall Layer (AI Search, Phase 1.5+) | Semantic Recall Layer (AI Search, Phase 1.5+) | Semantic Recall Layer (AI Search, Phase 1.5+) | Semantic Recall Layer (AI Search, Phase 1.5+) | Semantic Recall Layer (AI Search, Phase 1.5+) | Semantic Recall Layer (AI Search, Phase 1.5+) |
| Semantically retrieved historical context | 2,000-5,000 | Phase 1.5 | AI Search returns the most relevant past snapshots, reports, and news for today's conditions |
| Total Context Budget | Total Context Budget | Total Context Budget | Total Context Budget | Total Context Budget | Total Context Budget | Total Context Budget | Total Context Budget | Total Context Budget | Total Context Budget |
| TOTAL (v1 without AI Search) | 20K-38K tokens |  | Well within Sonnet 4.6 (1M) or even 200K context. Leaves room for 8K output. |
| TOTAL (v1.5 with AI Search) | 25K-45K tokens |  | Adds semantic recall. Still under 50K tokens total per run. |

**Key principle: **Today's data is never summarized. The Analyzer sends full fundamentals, full macro data, and full portfolio positions for the current day. Summarization only applies to historical context (trend summaries computed from tables) and is supplemented by semantic recall from AI Search in Phase 1.5.

# 4. News Strategy

The collector aggregates news from three tiers: company-specific (FMP), economic editorial (New York Times), and institutional (Fed/ECB RSS feeds). The collector filters for recency (last 24 hours) and relevance (keyword matching) before including headlines in the daily snapshot.

| Source | Endpoint / Feed | Type | Cost | What It Provides |
| --- | --- | --- | --- | --- |
| Company & Market News | Company & Market News | Company & Market News | Company & Market News | Company & Market News | Company & Market News | Company & Market News | Company & Market News | Company & Market News | Company & Market News |
| FMP Stock News | /stable/stock-news?symbol={t} | API | Free | Company-specific news filtered by your tickers |
| FMP General News | /stable/general-news | API | Free | Broad market headlines and analysis |
| FMP Press Releases | /stable/press-releases?symbol={t} | API | Free | Official company announcements for holdings |
| Economic & Editorial News | Economic & Editorial News | Economic & Editorial News | Economic & Editorial News | Economic & Editorial News | Economic & Editorial News | Economic & Editorial News | Economic & Editorial News | Economic & Editorial News | Economic & Editorial News |
| New York Times | Article Search + Times Wire API | API | Free | Premium economic analysis; search by keywords (Fed, inflation, tariffs) |
| FMP Economic Calendar | /v3/economic_calendar | API | Free | Upcoming FOMC, CPI, jobs report dates |
| Central Bank & Institutional | Central Bank & Institutional | Central Bank & Institutional | Central Bank & Institutional | Central Bank & Institutional | Central Bank & Institutional | Central Bank & Institutional | Central Bank & Institutional | Central Bank & Institutional | Central Bank & Institutional |
| Federal Reserve RSS | federalreserve.gov/feeds/ | RSS | Free | FOMC statements, speeches, press releases |
| ECB Press Releases | ecb.europa.eu RSS feed | RSS | Free | ECB rate decisions, policy statements (IDVO/IDMO context) |

## 4.1 NYT API Integration

**Registration: **Free developer account at developer.nytimes.com. Obtain API key, enable Article Search and Times Wire APIs.

**Daily collection: **The collector queries the Article Search API with keywords: 'Federal Reserve', 'interest rates', 'inflation', 'tariffs', 'trade policy', 'recession', 'GDP', 'employment', filtered to Business and Economy sections, last 24 hours. Typically returns 5-15 relevant articles per day.

**Rate limits: **500 requests/day, 5 requests/minute. The collector uses approximately 3-5 requests per run, well within limits.

**Key Vault: **Add NytApiKey secret to kv-pfauto-prod.

## 4.2 News Filtering

Not all collected news is relevant. The collector applies a two-stage filter. Stage 1: keyword matching against a configurable list in config/news-keywords.json (terms like 'Federal Reserve', 'interest rate', 'inflation', 'tariff', etc.). Stage 2 (optional, Phase 1.5): a cheap Claude Haiku call scores each headline for portfolio relevance (0-10) and only headlines scoring 7+ are included in the briefing. Haiku can scan 50 headlines for under $0.01.

# 5. Memory Architecture

The Claude API is stateless. Every call is independent with no built-in memory. The system creates effective memory through three mechanisms, each deployed in sequence as the pipeline matures.

## 5.1 Phase 1: Structured Continuity (day one)

The Analyzer includes Claude's last 5 daily reports in the context. This gives Claude its own analytical thread to follow. If it said 'watch IDVO if EUR/USD breaks 1.05' three days ago, and today EUR/USD is at 1.04, it sees both the prior conclusion and the new data point. The trade ledger from the TradeHistory table prevents contradictions and duplicates.

**Implementation: **The Analyzer reads the 5 most recent blobs from daily-reports/ and the last 10 rows from TradeHistory table. These are appended to the user message as a 'Recent Analysis Context' section.

## 5.2 Phase 1: Pre-Computed Trends (day one)

The Analyzer queries the MacroHistory, FundamentalsHistory, and PortfolioHistory tables to compute rolling statistics: 30-day and 60-day moving averages, standard deviations, trend direction (rising/falling/flat), and threshold alerts. These are presented as a 'Trend Summary' section in the context. Claude doesn't need to see 60 raw data points to know that VIX has been trending up for 3 weeks; the pre-computed trend tells it that in one line.

**Implementation: **Python code in the Analyzer computes these statistics using pandas. The output is a structured JSON summary appended to the user message.

## 5.3 Phase 1.5: Semantic Recall via Azure AI Search (after 60-90 days)

Once the system has accumulated 2-3 months of data, Azure AI Search adds genuine pattern memory. The Analyzer queries the search index with today's key market signals and retrieves the most semantically relevant historical data points, past reports, and news articles. This is fundamentally different from the table-based approach: it finds relevant history by meaning, not by pre-coded rules.

**Example: **Today VIX is at 28 and HY spreads widened 40bps in a week. The search index returns a report from 3 months ago when the same pattern occurred, what Claude recommended then, and the 30-day outcome. Claude can reference this directly in its analysis: 'A similar volatility spike in February preceded a 5% drawdown; I recommended trimming equity exposure then, which proved correct.'

**Implementation: **Azure AI Search with built-in blob indexer on daily-reports/ and daily-snapshots/ containers. Vector embeddings generated via Azure OpenAI embedding model (text-embedding-ada-002). The Analyzer performs a hybrid search (vector + keyword) and includes the top 5-10 results in the context.

## 5.4 AI Search Index Design

| Index Name | Source | What Gets Indexed |
| --- | --- | --- |
| idx-daily-reports | daily-reports/ blobs | Full Claude analysis reports with vector embeddings. Enables: 'find the last time I analyzed a similar market setup' |
| idx-trade-history | TradeHistory table | All trade recommendations with reasoning text. Enables: 'what did I recommend last time VIX was above 30?' |
| idx-news-archive | Collected news articles | NYT and FMP articles. Enables: 'what was the market narrative last time credit spreads widened this fast?' |

**Free tier viability: **Azure AI Search Free tier offers 50MB storage and 3 indexes. At ~2KB per daily report and ~5KB per snapshot summary, you'd accumulate roughly 1.5MB per year. The free tier would last for years. If you need more indexes or the 50MB limit becomes constraining, the Basic tier at $75/month provides 2GB and 15 indexes.

# 6. Claude Response Schema & Parsing

The system prompt instructs Claude to return its analysis in two clearly separated sections: a narrative markdown report for human consumption, and a structured JSON block for machine processing. The Analyzer function parses both from the response.

## 6.1 Response Structure

**Narrative section: **Claude's full analysis in markdown format. This becomes the daily-reports/YYYY-MM-DD.md blob and is delivered to Teams, email, and OneDrive. It includes the portfolio assessment, macro outlook, per-holding analysis, risk score, and any recommendations with reasoning.

**Structured JSON section: **A code-fenced JSON block at the end of the response, containing the trade recommendations array, risk scores, and execution notes. The Analyzer extracts this block, validates it against the trade recommendation schema defined in the architecture spec, and writes it to daily-trades/YYYY-MM-DD.json.

## 6.2 Validation Rules

The Analyzer applies strict validation before writing the trades blob. Every recommendation must have a valid action (BUY, SELL, TRIM, HOLD), a recognized ticker, a confidence level, and estimated shares/dollars that don't exceed the available cash balance. If validation fails, the Analyzer writes an empty recommendations array and flags the issue in the daily report metadata. No malformed trade recommendation ever reaches the approval Logic App.

## 6.3 Schema Enforcement

The system prompt includes the exact JSON schema Claude must follow (as defined in section 4.3.4 of the architecture spec). Temperature is set to 0.2 for consistency. If Claude returns output that doesn't match the schema, the Analyzer retries once with a follow-up message asking Claude to reformat. If the second attempt also fails, the system proceeds with the narrative report only and alerts via Teams.

# 7. Alpaca Execution Mapping (Phase 2)

When a trade recommendation is approved via the Teams Adaptive Card, the Executor Function translates Claude's structured output into Alpaca API calls. The mapping is deterministic, with no AI involved in the execution step.

| Claude Output Field | Alpaca API Parameter | Mapping Logic |
| --- | --- | --- |
| action: BUY | side: buy | Direct mapping |
| action: SELL / TRIM | side: sell | TRIM calculates shares from current vs target weight; SELL is full position |
| ticker | symbol | Direct mapping; validated against Alpaca asset list before submission |
| estimated_shares | qty | Integer; recalculated by Executor based on current price to match estimated_dollars |
| order_type: MARKET | type: market | Direct mapping; default for most recommendations |
| order_type: LIMIT | type: limit, limit_price: N | Limit price from Claude's recommendation; TIF defaults to DAY |
| (all orders) | time_in_force: day | All paper orders expire end of day; no GTC orders in automated system |

## 7.1 Execution Flow

**Step 1 — Approval received: **Logic App calls Executor Function with the approved trades payload, including HMAC signature for verification.

**Step 2 — Validation: **Executor verifies HMAC, checks approval_id against pending trades, confirms no replay. Queries Alpaca account balance to confirm sufficient buying power.

**Step 3 — Price recalculation: **Since time may have passed between recommendation and approval, the Executor fetches current market price from Alpaca and recalculates share quantities to match the estimated_dollars from Claude's recommendation.

**Step 4 — Order submission: **For each approved trade, submit order to Alpaca paper API (POST /v2/orders). Execute sells first if the recommendation set includes both buys and sells (to free up cash).

**Step 5 — Confirmation: **Wait for fill confirmation (30-second timeout per order). Write results to daily-executions/ blob and TradeHistory table. Post confirmation to Teams.

**Step 6 — Failure handling: **If any order fails, halt remaining orders. Write partial execution results. Alert via Teams with details of what succeeded and what failed.

# 8. Cost Summary

The analyzer pipeline cost depends on whether Azure AI Search is deployed. In both cases, the dominant cost is the Claude API usage.

| Component | v1 (no AI Search) | v1.5 (with AI Search) | Notes |
| --- | --- | --- | --- |
| Claude via Foundry (Sonnet daily) | $6-10/mo | $6-10/mo | ~50K tokens/run x 20 runs |
| Claude via Foundry (Opus weekly deep-dive) | $4-8/mo | $4-8/mo | Optional: 4 runs/mo with more context |
| Azure AI Search | $0 (not used) | $0 (Free tier) or $75 (Basic) | Free tier: 50MB, 3 indexes |
| NYT API | $0 | $0 | Free: 500 req/day |
| TOTAL (analysis layer only) | $10-18/mo | $10-18/mo (free) or $85-93/mo (basic) |  |

**Recommendation: **Start with v1 (no AI Search) at $10-18/month total analysis cost. Add AI Search Free tier at Phase 1.5 after 60-90 days of accumulated data. Only upgrade to AI Search Basic ($75/month) if the Free tier's 50MB limit is reached, which is unlikely within the first 2-3 years.

# 9. Implementation Sequence

**Week 1-2: **Deploy Foundry resource, configure Claude Sonnet 4.6 deployment, test API calls from Azure Function with Managed Identity.

**Week 3: **Build the Analyzer context assembly pipeline: blob reads, table queries, trend computation, news collection (FMP + NYT + RSS).

**Week 4: **Implement Claude response parsing, schema validation, and report delivery via Logic App.

**Week 5-8 (Phase 2): **Build approval Logic App, Executor Function, Alpaca integration. Test end-to-end with paper trades.

**After 60-90 days (Phase 1.5): **Deploy Azure AI Search Free tier. Configure blob indexers. Add semantic recall queries to the Analyzer. Evaluate whether the additional context improves recommendation quality.

*This document is a companion to the Architecture Spec (v1.0), Data Sources Reference (v1.1), and Storage Architecture (v1.0). It specifies the intelligence layer connecting raw data collection to actionable trade recommendations.*
