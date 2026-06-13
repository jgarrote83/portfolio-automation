# Portfolio Automation System

## Project overview
Azure-native automated portfolio analysis and paper trade execution pipeline. Single-user personal system. NOT for live trading. All trade decisions require human approval via Teams adaptive card.

> **Open work / where we left off:** see [`FOLLOWUPS.md`](FOLLOWUPS.md) at the repo root.

## Architecture decisions (do not deviate without discussion)
- **Azure AI Foundry** for Claude API — project: Portfolio-Analysis, resource: resource-portfolio-analysis (East US 2, Claude not available in East US). API key auth (FoundryApiKey in KV). Endpoints in Function App settings: FOUNDRY_ENDPOINT, FOUNDRY_OPENAI_ENDPOINT. Model: claude-sonnet-4-6, temp 0.2
- **Alpaca paper account** is the canonical source of truth for portfolio positions and balances (`portfolio.positions[]`, `portfolio.balances`, `paper_account` block). Also used for Phase 2 paper-trade execution. REST API, no VM needed. (E*TRADE was removed in commit `bc60604` — its OAuth-1.0a tokens expired daily and the integration was dropped; `src/shared/clients/etrade.py` is dead code retained only as historical reference.)
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
│   ├── shared/               # Common utilities, API clients, schemas
│   └── config/               # Packaged with the function app
│       ├── project-instructions.md    # Claude system prompt for analysis
│       ├── macro-series.json          # FRED series IDs
│       └── portfolio.json             # Fallback positions if Alpaca unreachable
├── web/                      # Static Web App: single pane of glass
│   ├── *.html, app.js, styles.css
│   ├── staticwebapp.config.json  # Entra ID auth + route protection
│   └── api/                  # SWA managed Python Functions (HTTP only)
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

## Key Vault secrets (7 active)
FmpApiKey, FredApiKey, AlpacaApiKey, AlpacaApiSecret, FoundryApiKey, FinnhubApiKey, QuiverApiKey

(Note: `MassiveApiKey` and the four `Etrade*` secrets may still exist in KV as soft-deletable leftovers from deprecated integrations — safe to delete manually. The collector no longer fetches them.)

If Alpaca is unreachable the collector falls back to `src/config/portfolio.json` (positions only — dollar gains will be zero in that mode).

## Data flow — Phase 1
1. Timer fires collector at 09:00 ET weekdays (NCRONTAB: `0 0 9 * * 1-5`, ET-local via `TZ=America/New_York` app setting — no DST drift). **NOTE**: this is Linux Consumption, so the IANA `TZ` setting is required. The Windows-only `WEBSITE_TIME_ZONE` is silently ignored on Linux and caused crons to fire 4.5h early (at 09:00 UTC = 05:00 ET) until commit 6f42f1a.
2. Collector reads secrets from Key Vault via Managed Identity
3. Collector calls Alpaca paper account (positions + balances drive the canonical `portfolio.positions[]` / `portfolio.balances`; `paper_account` block also retained with cash, buying_power, equity, last_equity, position list — falls back to `config/portfolio.json` if Alpaca unreachable), FMP (fundamentals, earnings, EOD prices, ETF data, stock news, fallback congressional), FRED (35 macro series, deep-history for bond + labor scorecards), Quiver (congressional trades, lobbying, gov contracts — **lobbying and gov_contracts are filtered client-side to portfolio tickers ∪ ETF watchlist and last 90 days; raw responses are ~20K rows/~16 MB and would blow past Claude's 1 M-token limit**), Finnhub (market news, company news)
4. Collector writes full JSON snapshot to `daily-snapshots/YYYY-MM-DD.json` blob
5. Collector writes denormalized rows to 6 Table Storage tables (PortfolioHistory, FundamentalsHistory, MacroHistory, ETFLookthroughHistory, SentimentHistory, TradeHistory)
6. Blob trigger fires analyzer
7. Analyzer reads today's snapshot + queries tables for historical trends + loads last 5 reports for continuity
8. Analyzer calls Claude via Foundry (Sonnet 4.6, temp 0.2, max_tokens 16000)
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
- Human approval required for ALL **live** trade execution — no autonomous live trading. **Paper-only auto-execute** is enabled via app setting `AUTO_EXECUTE_ENABLED=true`: a 09:35 ET timer (`auto_executor`, NCRONTAB `0 35 9 * * 1-5`, ET-local via `TZ=America/New_York`) reads `daily-trades/{today}.json` and submits every recommendation to Alpaca paper, gated by Alpaca market clock (defers if closed). Executor applies a **defensive sell filter**: any sell against a symbol not currently held in the Alpaca paper account is dropped (status `not_held_in_paper_account`); requested qty larger than held is trimmed. Drops are recorded in `daily-executions/{date}.json` `skipped[]`.
- **One-time paper seeder** (`POST /api/seeder`): idempotent mirror of `config/portfolio.json` into Alpaca paper. Use `whole_shares_only=true` for off-hours seeding (Alpaca rejects fractional day-orders queued outside market hours — we floor qty to int, drop sub-1 tickers). Symbols already held OR with a pending open buy order are skipped (`skipped_already_held` / `skipped_open_order_pending`) so re-runs never duplicate. Per-run report written to `seeding/{utc_ts}.json`.
- Phase 1 must run clean 30+ days before Phase 2 is enabled
- Temperature 0.2 for Claude analysis calls (consistency)
- Sells execute before buys in multi-trade recommendations (free up cash)
- **Core vs flex exit asymmetry** (analyzer/prompt-level only — the executor is layer-agnostic): the 24 **core** tickers are the All-Weather backbone and may never be sold to zero — trimmed only to a token floor of ~0.1% of equity / ≥1 share. **Flex** names (≤10, ≤25% of equity) may be fully liquidated when the thesis breaks or a kill level fires.
- **`stop_loss` / `take_profit` are advisory, not broker orders.** The executor only ever places single-leg market/limit orders; it never sends bracket/OCO legs (a resting broker stop would make the executor stateful and collide with the daily re-recommendation loop). Instead the **analyzer** evaluates them each run: a flex `stop_loss` equals the published kill-criteria price trigger, and on the next run the analyzer compares it to the snapshot price and proposes an exit if breached. Core trades carry null stops. This is an EOD-granularity stop with no intraday protection by design. See the comment in `_place_one` (`src/executor/handler.py`).

## Deployment lessons (hard-won — see infra/modules/storage-roles.bicep + .github/workflows/deploy-code.yml)
- Function App MI requires **Storage Account Contributor** on the storage account in addition to Blob Data Owner / Queue Data Contributor / Table Data Contributor. Host startup calls `BlobServiceClient.GetPropertiesAsync()` which needs `blobServices/read`, not in the data-plane roles. Without it: persistent `AuthorizationPermissionMismatch 403`, host faults, zero functions registered.
- Workflow pip install MUST pin manylinux2014 wheels (`--platform manylinux2014_x86_64 --python-version 3.11 --implementation cp --only-binary=:all:`). GitHub `ubuntu-latest` ships GLIBC 2.39; Functions Linux Consumption image is older. Native wheels (e.g. `cryptography`) otherwise fail with `GLIBC_2.33 not found` and the Python worker silently fails to load.
- Deploy model: run-from-package via blob (`WEBSITE_RUN_FROM_PACKAGE=<blob URL>`). `func azure functionapp publish` does not work with identity-based `AzureWebJobsStorage`. App runs in read-only mode — portal Test/Run hits CORS; invoke via admin REST instead: `POST https://func-pfauto.azurewebsites.net/admin/functions/<name>` with master key from `az functionapp keys list`.
- Workflow path filter is `src/**`; workflow-only changes need manual `gh workflow run "Deploy function code" --ref master`.
- Quiver `/beta/live/lobbying` and `/beta/live/gov_contracts` return ~20K rows of all-market activity per call (~12 MB + ~4 MB JSON). Collector MUST filter client-side to portfolio tickers ∪ ETF watchlist AND last 90 days — otherwise the daily snapshot balloons to ~20 MB and the analyzer prompt blows past Claude's 1 M-token context window (observed: 6.2 M tokens → permanent 400). See `_row_ticker`/`_row_date` filter in `src/collector/handler.py` after the Quiver fetch. Commit `20cb2b0`.
- `host.json` `functionTimeout` is `00:40:00`. The old `00:10:00` was the **Consumption** plan max; on **Flex Consumption** the cap is far higher and the Foundry client allows up to 600 s/attempt × 3 retries, so 10 min could hard-kill the analyzer mid-Claude-call (no `Executed`, no exception, no report — just a silent recycle at the 10-min boundary). Large June snapshots (~1.2 MB / ~240K tokens) exposed this. Can also be overridden live without redeploy via the `AzureFunctionsJobHost__functionTimeout` app setting. Commits `743b5ad` (original), 2026-06-09 fix.
- **EventGrid blob-trigger `functionName` (Flex Consumption)**: the webhook URL in `deploy-code.yml` MUST use `functionName=Host.Functions.analyzer`, NOT bare `functionName=analyzer`. The bare form is accepted by `/runtime/webhooks/blobs` (HTTP 200, EventGrid reports DeliverySuccess) but cannot be routed to the registered listener, so the analyzer **never fires** and nothing errors — looks healthy end-to-end while silently dropping every event. Also requires the `Microsoft.EventGrid` resource provider to be **`Registered`** (not `Registering`) or storage publishes zero events (PublishSuccessCount=0). Diagnosed 2026-06-09.
- Runtime knobs `TZ=America/New_York`, `AUTO_EXECUTE_ENABLED=true`, and `AzureFunctionsJobHost__functionTimeout=00:40:00` now live **in `functionapp.bicep`** (IaC), so an `infra/**` deploy sets rather than wipes them. They were previously applied post-deploy, and an `az deployment group create` replaces the function app's app-setting set wholesale — which silently dropped them after the Flex migration and disabled the pipeline. Without `TZ`, crons run in UTC (collector 09:00 UTC = 05:00 ET; auto_executor 09:35 UTC = pre-market → defers forever). `AUTO_EXECUTE_ENABLED=true` in IaC intentionally overrides the "Phase 1 clean 30+ days" gate. Note: setting any app setting restarts the host — which is also what (re)connects Application Insights if telemetry has gone silent.

## Spec documents (in docs/specs/)
Full details in these companion documents — read them for implementation specifics:
- Architecture Spec v1.0 — system design, security, deployment
- Data Sources Reference v1.2 — all API endpoints, schemas, budget
- Storage Architecture v1.0 — blob containers, table schemas, retention
- Analyzer Pipeline v1.0 — context assembly, memory, response parsing, Alpaca mapping
