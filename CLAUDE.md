# Portfolio Automation System

## Project overview
Azure-native automated portfolio analysis and paper trade execution pipeline. Single-user personal system. NOT for live trading. All trade decisions require human approval via Teams adaptive card.

## Architecture decisions (do not deviate without discussion)
- **Azure AI Foundry** for Claude API — project: Portfolio-Analysis, resource: resource-portfolio-analysis (East US 2, Claude not available in East US). API key auth (FoundryApiKey in KV). Endpoints in Function App settings: FOUNDRY_ENDPOINT, FOUNDRY_OPENAI_ENDPOINT. Model: claude-sonnet-4-6, temp 0.2
- **E*TRADE API** for portfolio data only (real holdings, balances, option chains) — OAuth 1.0a, tokens expire midnight ET
- **Alpaca** for Phase 2 paper trading execution only — REST API, no VM needed
- **FMP (Financial Modeling Prep)** for fundamentals, earnings, ETF look-through, EOD prices, stock news, and fallback congressional trades (senate/house) — free tier, 250 req/day
- **FRED** for macro indicators (35 series collected, including 6 labor-market series feeding `labor_signals` scorecard; `CHPMINDXM` deprecated — to revisit) — free, no practical limit
- **Quiver Quantitative** (primary alternative-data source) for congressional trades, lobbying, government contracts, wikipedia attention — hobbyist tier, `Authorization: Token <key>` header
- **Finnhub** for financial market news and company news — free tier, 60 calls/min
- **Azure AI Search** (Free tier) for semantic memory recall — Phase 1.5, after 60-90 days of data

## Tech stack
- Language: Python 3.11
- IaC: Bicep (no manual portal config for prod)
- CI/CD: GitHub Actions with OIDC federated credentials (no secrets in GitHub)
- Linting: ruff
- Testing: pytest
- Azure Functions: Consumption plan, Linux, timer + blob triggers
- Static Web App: Free SKU, Entra ID Easy Auth, managed Python /api functions
- Storage: single account (stpfautoprod) with Blob + Table Storage
- Key Vault: kv-pfauto-prod, RBAC auth, Managed Identity only — no service principals or connection strings in code
- Monitoring: Application Insights (workspace-based)

## Resource naming
- Resource group: rg-portfolio-automation-prod
- Storage account: stpfautoprod
- Key Vault: kv-pfauto-prod
- Function App: func-pfauto (hosts collector + analyzer; future executor endpoint)
- Static Web App: swa-pfauto (single pane of glass — report viewer + per-trade approval, Entra ID auth)
- App Insights: appi-pfauto-prod
- Log Analytics: log-pfauto-prod

## Repository structure
```
portfolio-automation/
├── infra/                    # Bicep templates
│   ├── main.bicep
│   ├── modules/              # storage, keyvault, functionapp, staticwebapp, monitoring
│   ├── parameters.prod.json
│   └── deploy.sh
├── src/
│   ├── collector/            # Timer-triggered, collects from all APIs
│   ├── analyzer/             # Blob-triggered, assembles context, calls Claude
│   ├── executor/             # HTTP-triggered, Phase 2 Alpaca execution
│   └── shared/               # Common utilities, API clients, schemas
├── web/                      # Static Web App: single pane of glass
│   ├── *.html, app.js, styles.css
│   ├── staticwebapp.config.json  # Entra ID auth + route protection
│   └── api/                  # SWA managed Python Functions (HTTP only)
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
│   ├── deploy-code.yml      # func-pfauto (collector/analyzer)
│   └── deploy-web.yml       # swa-pfauto (frontend + managed API)
├── CLAUDE.md                 # This file
└── README.md
```

## Key Vault secrets (11 total)
EtradeConsumerKey, EtradeConsumerSecret, EtradeAccessToken, EtradeAccessTokenSecret, FmpApiKey, FredApiKey, AlpacaApiKey, AlpacaApiSecret, FoundryApiKey, FinnhubApiKey, QuiverApiKey

(Note: `MassiveApiKey` may still exist in KV as a soft-deletable leftover from the deprecated Polygon/Massive integration — safe to delete manually.)

E*TRADE tokens expire midnight ET and must be refreshed via OAuth dance; the collector falls back to `src/config/portfolio.json` when missing.

## Data flow — Phase 1
1. Timer fires collector at 06:00 ET weekdays (NCRONTAB: `0 0 6 * * 1-5`)
2. Collector reads secrets from Key Vault via Managed Identity
3. Collector calls E*TRADE (positions, balances, option chains — falls back to `config/portfolio.json` if creds missing), FMP (fundamentals, earnings, EOD prices, ETF data, stock news, fallback congressional), FRED (35 macro series, deep-history for bond + labor scorecards), Quiver (congressional trades, lobbying, gov contracts — **lobbying and gov_contracts are filtered client-side to portfolio tickers ∪ ETF watchlist and last 90 days; raw responses are ~20K rows/~16 MB and would blow past Claude's 1 M-token limit**), Finnhub (market news, company news)
4. Collector writes full JSON snapshot to `daily-snapshots/YYYY-MM-DD.json` blob
5. Collector writes denormalized rows to 6 Table Storage tables (PortfolioHistory, FundamentalsHistory, MacroHistory, ETFLookthroughHistory, SentimentHistory, TradeHistory)
6. Blob trigger fires analyzer
7. Analyzer reads today's snapshot + queries tables for historical trends + loads last 5 reports for continuity
8. Analyzer calls Claude via Foundry (Sonnet 4.6, temp 0.2, max_tokens 8000)
9. Analyzer parses response: markdown report + structured trade recommendations JSON
10. Writes report to `daily-reports/YYYY-MM-DD.md`, trades to `daily-trades/YYYY-MM-DD.json`
11. Outputs surfaced in `swa-pfauto` (no Logic App delivery; user pulls report via web UI). Optional email/OneDrive copies can be added later if needed.

## Data flow — Phase 2 (paper execution via Static Web App)
1. User signs into `swa-pfauto` with Entra ID work account (Easy Auth; allowed-role `owner`)
2. `/today` page renders the latest report (markdown) + a table of trade recommendations with per-row checkboxes
3. User selects 1…N trades and clicks **Approve selected** (bulk Approve button) or rejects
4. SWA managed API (`web/api/`) records the decision in `approvals/YYYY-MM-DD.json` and calls the `func-pfauto` executor endpoint (HMAC-signed payload, function master key fetched via SWA MI → Key Vault reference)
5. Executor validates, connects to Alpaca paper API, places orders (sells before buys)
6. Writes results to `daily-executions/YYYY-MM-DD.json` + TradeHistory table; SWA `/today` polls and shows execution status inline

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

## Deployment lessons (hard-won — see infra/modules/storage-roles.bicep + .github/workflows/deploy-code.yml)
- Function App MI requires **Storage Account Contributor** on the storage account in addition to Blob Data Owner / Queue Data Contributor / Table Data Contributor. Host startup calls `BlobServiceClient.GetPropertiesAsync()` which needs `blobServices/read`, not in the data-plane roles. Without it: persistent `AuthorizationPermissionMismatch 403`, host faults, zero functions registered.
- Workflow pip install MUST pin manylinux2014 wheels (`--platform manylinux2014_x86_64 --python-version 3.11 --implementation cp --only-binary=:all:`). GitHub `ubuntu-latest` ships GLIBC 2.39; Functions Linux Consumption image is older. Native wheels (e.g. `cryptography`) otherwise fail with `GLIBC_2.33 not found` and the Python worker silently fails to load.
- Deploy model: run-from-package via blob (`WEBSITE_RUN_FROM_PACKAGE=<blob URL>`). `func azure functionapp publish` does not work with identity-based `AzureWebJobsStorage`. App runs in read-only mode — portal Test/Run hits CORS; invoke via admin REST instead: `POST https://func-pfauto.azurewebsites.net/admin/functions/<name>` with master key from `az functionapp keys list`.
- Workflow path filter is `src/**`; workflow-only changes need manual `gh workflow run "Deploy function code" --ref master`.
- Quiver `/beta/live/lobbying` and `/beta/live/gov_contracts` return ~20K rows of all-market activity per call (~12 MB + ~4 MB JSON). Collector MUST filter client-side to portfolio tickers ∪ ETF watchlist AND last 90 days — otherwise the daily snapshot balloons to ~20 MB and the analyzer prompt blows past Claude's 1 M-token context window (observed: 6.2 M tokens → permanent 400). See `_row_ticker`/`_row_date` filter in `src/collector/handler.py` after the Quiver fetch. Commit `20cb2b0`.
- `host.json` `functionTimeout` MUST be `00:10:00` (Consumption-plan max). Default is 5 min; analyzer Foundry call with full snapshot (~240K input tokens) takes ~45–150 s and was being killed silently mid-call with no completion log in App Insights. Commit `743b5ad`.

## Spec documents (in docs/specs/)
Full details in these companion documents — read them for implementation specifics:
- Architecture Spec v1.0 — system design, security, deployment
- Data Sources Reference v1.2 — all API endpoints, schemas, budget
- Storage Architecture v1.0 — blob containers, table schemas, retention
- Analyzer Pipeline v1.0 — context assembly, memory, response parsing, Alpaca mapping
