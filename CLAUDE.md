# Portfolio Automation System

## Project overview
Azure-native automated portfolio analysis and paper trade execution pipeline. Single-user personal system. NOT for live trading. All trade decisions require human approval via Teams adaptive card.

## Architecture decisions (do not deviate without discussion)
- **Azure AI Foundry** for Claude API — project: Portfolio-automation, resource: portfolio-automation-resource (rg-portfolio-automation-prod, East US). API key auth (FoundryApiKey in KV). Endpoints in Function App settings: FOUNDRY_ENDPOINT, FOUNDRY_OPENAI_ENDPOINT. Model: Claude Sonnet 4.6, temp 0.2
- **E*TRADE API** for portfolio data only (real holdings, balances, option chains) — OAuth 1.0a, tokens expire midnight ET
- **Alpaca** for Phase 2 paper trading execution only — REST API, no VM needed
- **FMP (Financial Modeling Prep)** for fundamentals, earnings, ETF look-through, congressional trades, stock news — free tier, 250 req/day
- **FRED** for macro indicators (18 series: US + international) — free, no practical limit
- **Massive** (formerly Massive.io) for EOD prices — free tier, 5 calls/min
- **Finnhub** for financial market news and company news — free tier, 60 calls/min
- **Azure AI Search** (Free tier) for semantic memory recall — Phase 1.5, after 60-90 days of data

## Tech stack
- Language: Python 3.11
- IaC: Bicep (no manual portal config for prod)
- CI/CD: GitHub Actions with OIDC federated credentials (no secrets in GitHub)
- Linting: ruff
- Testing: pytest
- Azure Functions: Consumption plan, Linux, timer + blob triggers
- Logic Apps: Consumption plan, Office 365 + Teams connectors
- Storage: single account (stpfautoprod) with Blob + Table Storage
- Key Vault: kv-pfauto-prod, RBAC auth, Managed Identity only — no service principals or connection strings in code
- Monitoring: Application Insights (workspace-based)

## Resource naming
- Resource group: rg-portfolio-automation-prod
- Storage account: stpfautoprod
- Key Vault: kv-pfauto-prod
- Function App: func-pfauto (hosts collector + analyzer)
- Logic App delivery: logic-pfauto-delivery
- Logic App approval: logic-pfauto-approval (Phase 2)
- App Insights: appi-pfauto-prod
- Log Analytics: log-pfauto-prod

## Repository structure
```
portfolio-automation/
├── infra/                    # Bicep templates
│   ├── main.bicep
│   ├── modules/              # storage, keyvault, functionapp, logicapp, monitoring
│   ├── parameters.prod.json
│   └── deploy.sh
├── src/
│   ├── collector/            # Timer-triggered, collects from all APIs
│   ├── analyzer/             # Blob-triggered, assembles context, calls Claude
│   ├── executor/             # HTTP-triggered, Phase 2 Alpaca execution
│   └── shared/               # Common utilities, API clients, schemas
├── logic-apps/
│   ├── delivery.json
│   └── approval.json         # Phase 2
├── config/
│   ├── project-instructions.md    # Claude system prompt for analysis
│   ├── macro-series.json          # FRED series IDs
│   ├── news-keywords.json         # News filtering keywords
│   └── etf-watchlist.json         # IDVO, IDMO, AIA
├── docs/
│   ├── specs/                # Architecture spec + companion docs
│   └── runbooks/             # Operational runbooks
├── .github/workflows/
│   ├── deploy-infra.yml
│   └── deploy-code.yml
├── CLAUDE.md                 # This file
└── README.md
```

## Key Vault secrets (11 total)
EtradeConsumerKey, EtradeConsumerSecret, EtradeAccessToken, EtradeAccessTokenSecret, FmpApiKey, FredApiKey, MassiveApiKey, AlpacaApiKey, AlpacaApiSecret, FoundryApiKey, FinnhubApiKey

## Data flow — Phase 1
1. Timer fires collector at 06:00 ET weekdays (NCRONTAB: `0 0 6 * * 1-5`)
2. Collector reads secrets from Key Vault via Managed Identity
3. Collector calls E*TRADE (positions, balances, option chains), FMP (fundamentals, earnings, congressional trades, ETF data, stock news), FRED (18 macro series), Massive (EOD prices), Finnhub (market news, company news)
4. Collector writes full JSON snapshot to `daily-snapshots/YYYY-MM-DD.json` blob
5. Collector writes denormalized rows to 6 Table Storage tables (PortfolioHistory, FundamentalsHistory, MacroHistory, ETFLookthroughHistory, SentimentHistory, TradeHistory)
6. Blob trigger fires analyzer
7. Analyzer reads today's snapshot + queries tables for historical trends + loads last 5 reports for continuity
8. Analyzer calls Claude via Foundry (Sonnet 4.6, temp 0.2, max_tokens 8000)
9. Analyzer parses response: markdown report + structured trade recommendations JSON
10. Writes report to `daily-reports/YYYY-MM-DD.md`, trades to `daily-trades/YYYY-MM-DD.json`
11. Delivery Logic App posts to Teams, sends email, copies to OneDrive

## Data flow — Phase 2 (paper execution)
1. Trades blob triggers approval Logic App
2. Adaptive card sent to Teams with trade details + Approve/Reject buttons
3. On approve: Logic App calls executor Function with HMAC-signed payload
4. Executor validates, connects to Alpaca paper API, places orders
5. Writes results to `daily-executions/YYYY-MM-DD.json` + TradeHistory table
6. Confirmation posted to Teams

## Table Storage schemas (6 tables)
- **PortfolioHistory**: PK=ticker, RK=date — positions, weights, P/L
- **FundamentalsHistory**: PK=ticker, RK=date — P/E, DCF, rating, earnings date
- **MacroHistory**: PK=series_id, RK=date — FRED values with deltas
- **ETFLookthroughHistory**: PK=etf_ticker, RK=date — holdings, country/sector allocation
- **SentimentHistory**: PK=date, RK=indicator — VIX, spreads, P/C ratios, percentiles
- **TradeHistory**: PK=year-month, RK=trade_id — full lifecycle from recommendation to 30/60/90d outcome

## International holdings requiring special treatment
IDVO (international dividend + covered call overlay), IDMO (international momentum), AIA (Asia 50). Need: ETF look-through from FMP, international macro from FRED (EUR/USD, USD/JPY, USD/CNY, ECB rate, China PMI, Japan 10Y).

## Rules
- Never store secrets in code — always Key Vault with Managed Identity
- All functions emit custom metrics to App Insights
- Blob is source of truth; tables can be rebuilt from blobs (RUNBOOK-007)
- Human approval required for ALL trade execution — no autonomous trading
- Phase 1 must run clean 30+ days before Phase 2 is enabled
- Temperature 0.2 for Claude analysis calls (consistency)
- Sells execute before buys in multi-trade recommendations (free up cash)

## Spec documents (in docs/specs/)
Full details in these companion documents — read them for implementation specifics:
- Architecture Spec v1.0 — system design, security, deployment
- Data Sources Reference v1.2 — all API endpoints, schemas, budget
- Storage Architecture v1.0 — blob containers, table schemas, retention
- Analyzer Pipeline v1.0 — context assembly, memory, response parsing, Alpaca mapping
