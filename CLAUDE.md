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
├── src/                      # func-pfauto: all Azure Functions + shared libs
│   ├── function_app.py       # single entry point — registers every trigger (v2 model)
│   ├── host.json             # functionTimeout 00:40:00 (Flex Consumption)
│   ├── collector/            # Timer-triggered (09:00 ET), collects from all APIs
│   ├── analyzer/             # Blob-triggered (EventGrid), assembles context, calls Claude
│   ├── executor/             # HTTP + timer, Phase 2 / auto paper execution + retries
│   ├── flex/                 # Intraday catalyst Flex engine (timer, FLEX_ENABLED-gated)
│   ├── seeder/               # One-time idempotent Alpaca paper seeding (HTTP)
│   ├── shared/               # Common utilities + API clients + pure decision modules
│   │   ├── clients/          # alpaca, fmp, fred, quiver, finnhub, foundry, (etrade dead)
│   │   ├── quadrants.py      # block model: rosters, per-quadrant concentrate lists
│   │   ├── reference_execution.py  # Finding 2 — reconcile (band enforcement, D1-D3)
│   │   ├── trade_validation.py     # #28 — Tier-1 trade validator (V1-V4)
│   │   ├── overrides.py            # OVERRIDE_SCHEMA_V1_1 validate_overrides (Tier-2)
│   │   ├── timeutil.py             # today_et / now_et (zoneinfo; #29)
│   │   ├── storage.py, keyvault.py # blob/table I/O + KV via Managed Identity
│   └── config/               # NOTE: config lives UNDER src/, not at repo root
│       ├── project-instructions.md    # Claude system prompt for analysis
│       ├── macro-series.json          # FRED series IDs
│       ├── risk-limits.json           # canonical risk limits — source of truth for reference_weights + override/exec/validator configs
│       ├── divergence-config.json     # thresholds for the divergences[] tension detector
│       ├── fomc-stance.json           # manually-maintained FOMC policy stance (as_of gates the manual override layer)
│       ├── flex-candidates.json       # seed non-held flex watchlist
│       └── portfolio.json             # Alpaca-unreachable fallback (positions only)
├── web/                      # Static Web App: single pane of glass
│   ├── today.html/js, performance.html/js, history.html/js, portfolio.html/js, index.html
│   ├── app.js, styles.css
│   ├── staticwebapp.config.json  # Entra ID auth + route protection
│   └── api/                  # SWA managed Python Functions (HTTP only)
├── docs/
│   └── specs/                # Architecture spec + companion docs (.md + .docx) — NO runbooks/ dir; ops notes live in CLAUDE.md "Deployment lessons"
├── scripts/                  # dev helpers: docx_to_md.py, run_fmp_smoke.py
├── tests/                    # pytest suite (PYTHONPATH=src)
├── .github/workflows/
│   ├── ci.yml               # pytest + ruff on PR/push
│   ├── deploy-infra.yml
│   ├── deploy-code.yml      # func-pfauto (collector/analyzer/executor/flex/seeder)
│   └── deploy-web.yml       # swa-pfauto (frontend + managed API)
├── FOLLOWUPS.md              # open work / where we left off
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
3. Collector calls Alpaca paper account (positions + balances drive the canonical `portfolio.positions[]` / `portfolio.balances`; `paper_account` block also retained with cash, buying_power, equity, last_equity, position list — falls back to `src/config/portfolio.json` if Alpaca unreachable; NOTE `paper_account.positions[]` uses the Alpaca-native `qty` field, while `portfolio.positions[]` uses `quantity` — code reading positions must accept both, see the 2026-07-07 held_qty incident, FOLLOWUPS #33), FMP (fundamentals, earnings, EOD prices, ETF data, stock news, fallback congressional), FRED (35 macro series, deep-history for bond + labor scorecards), Quiver (congressional trades, lobbying, gov contracts — **lobbying and gov_contracts are filtered client-side to portfolio tickers ∪ ETF watchlist and last 90 days; raw responses are ~20K rows/~16 MB and would blow past Claude's 1 M-token limit**), Finnhub (market news, company news)
4. Collector writes full JSON snapshot to `daily-snapshots/YYYY-MM-DD.json` blob
5. Collector writes denormalized rows to 6 Table Storage tables (PortfolioHistory, FundamentalsHistory, MacroHistory, ETFLookthroughHistory, SentimentHistory, TradeHistory)
6. Blob trigger fires analyzer
7. Analyzer reads today's snapshot + queries tables for historical trends + loads last 5 reports for continuity
8. Analyzer calls Claude via Foundry (Sonnet 4.6, temp 0.2, max_tokens 8000). Responses are **streamed** (`shared/clients/foundry.py`) to survive Azure's ~4-min outbound idle drop on long generations (PR #7, 2026-07-02).
9. Analyzer parses response: markdown report + structured trade recommendations JSON. Then, deterministically and post-model: validates `overrides[]` (Tier-2, `shared/overrides.py`), runs **band enforcement** (`shared/reference_execution.reconcile` — synthesizes de-risk shortfall trades toward reference), then the **Tier-1 trade validator** (`shared/trade_validation.validate_trades` — V1-V4 gate/exemption/window/mechanical). A validator crash sets `validation_error` and the auto-executor refuses the file (fail-closed).
10. Writes report to `daily-reports/YYYY-MM-DD.md`, trades to `daily-trades/YYYY-MM-DD.json` (with `trade_validation`/`reference_execution` blocks), OverrideHistory rows
11. Outputs surfaced in `swa-pfauto` (no Logic App delivery; user pulls report via web UI). Optional email/OneDrive copies can be added later if needed.

## Data flow — Phase 2 (paper execution)
There are TWO execution paths into the same `executor.execute_approvals`:

**Auto path (currently live — `AUTO_EXECUTE_ENABLED=true`, paper only):**
1. `auto_executor` timer fires 09:35 ET; retry nets at 10:05 + 11:05 ET (`auto_executor_retry`, #29) cover variable analyzer latency and missed opens
2. Reads `daily-trades/{ET-today}.json` (ET trading date via `shared/timeutil.today_et` — never UTC), treats every recommendation as approved, but **refuses** any file flagged by the Tier-1 validator (`validation_error`, rejected-stamped trades). Gated by the Alpaca market clock (defers if closed). Idempotent by a cache asymmetry (terminal outcomes cached; failures left uncached so retries re-attempt) + date-scoped `client_order_id`.
3. Executor connects to Alpaca paper API, places orders (sells before buys), applies the defensive sell filter (drops sells of unheld symbols; trims oversize qty). Writes `daily-executions/{date}.json` + TradeHistory.

**Manual path (Static Web App — for when auto is off or for live trading later):**
1. User signs into `swa-pfauto` with Entra ID work account (Easy Auth; allowed-role `owner`)
2. `/today` renders the report + trade table with per-row checkboxes
3. User selects 1…N trades and clicks **Approve selected**
4. SWA managed API (`web/api/`) records the decision in `approvals/YYYY-MM-DD.json` and calls the `func-pfauto` executor endpoint (function master key fetched via SWA MI → Key Vault reference)
5-6. Same executor → same `daily-executions/{date}.json` + TradeHistory; SWA `/today` polls and shows status inline

## Table Storage schemas (7 tables)
- **PortfolioHistory**: PK=ticker, RK=date — positions, weights, P/L
- **FundamentalsHistory**: PK=ticker, RK=date — P/E, DCF, rating, earnings date
- **MacroHistory**: PK=series_id, RK=date — FRED values with deltas
- **ETFLookthroughHistory**: PK=etf_ticker, RK=date — holdings, country/sector allocation
- **SentimentHistory**: PK=date, RK=indicator — VIX, spreads, P/C ratios, percentiles
- **TradeHistory**: PK=year-month, RK=trade_id — full lifecycle from recommendation to 30/60/90d outcome. Phase C adds (write-once at recommendation, flex trades) the §7 reasoning enums `primary_trigger`/`thesis_type`/`trigger_evidence`/`catalyst_date`, and (stamped later by the collector at maturity) `price_at_rec`/`spy_at_rec`/`ret_Nd_pct`/`spy_ret_Nd_pct`/`excess_Nd_pp`/`call_correct_Nd`/`outcome_status`. Keys are **lowercase** across analyzer + executor + collector writes — Azure Tables is case-sensitive and upserts MERGE onto one entity (Phase C §9 casing fix).
- **OverrideHistory**: PK=year-month, RK=`OV-YYYYMMDD-NNN` — one row per override record the analyzer emitted (Phase 4, write-once): `outcome` (accepted/downsized/rejected), `validator_reasons`, `premise_challenged`, `sleeve` (V1_1), `direction`, `magnitude_pp`, `downsized`, `evidence`, `evidence_count`, `falsifier`, `falsifier_date`, `clean_data_only`, `enforced` (Finding-2 band enforcement fired on this sleeve). **Phase 5 (merged 2026-07-05) now STAMPS** `outcome_status`/`resolved_correct` at each record's `falsifier_date`, graded against the reference-path counterfactual ("did disagreeing beat obeying"); the collector maturity pass writes them.

## Snapshot analytics blocks (collector pre-computes; analyzer consumes)
Beyond raw API data, the collector injects pre-computed analysis blocks into each `daily-snapshots/{date}.json` so the analyzer reads conclusions, not raw series: `regional_rotation`, `bond_signals`, `labor_signals`, `market_shock`, `growth_axis`/`inflation_axis`/`regime_gate` (the deterministic quadrant axes + deployment gate, echoed not re-derived), and (Phase C) `performance` (account equity vs fully-invested SPY since inception + rolling 30/60/90d + `cash_pct`) and `track_record` (hit-rate by layer/trigger/thesis at the 60d headline + confidence calibration, aggregated from stamped TradeHistory rows). Phase C 7a also maintains a compact `performance/equity-series.json` blob (collector-owned cache: backfilled once from snapshots, then append-only).

**`regional_rotation.rotation_score`** is a composite 0–10 (dollar 30 / relative-strength 30 / policy 20 / valuation 20) with a `category` (`us_leadership_intact` ≤3 / `transition_window` 4–6 / `rotation_underway` 7–10). The category is bucketed on the **rounded** `composite` (via the pure `_rotation_composite_category`), not the raw weighted score — so the displayed number and the label can never disagree (fixes the 2026-07-09 seam where an unrounded 3.049 displayed 3.0 but was labelled `transition_window`).

**`divergences`** (responsiveness brief Phase 2) is a deterministic list of **tensions between signals that should agree but don't** — it points the analyzer's judgment at high-value zones but **only describes; it never resolves, ranks, or acts** (resolution is the LLM's job in Phase 4). Built by `collector._build_divergences`, echoing values already in the snapshot. Four detectors: `leading_vs_lagging_inflation` (breakevens + oil vs realized core direction), `credit_complacency` (HY OAS at a ≤10th-pct-rank complacency extreme with no corroborating stress → `fragility`), `price_vs_regime` (SPY vs its 200-day SMA vs the deterministic `active_quadrant`), `dollar_vs_intl_tilt` (the DXY switch vs the book's aggregate amplifier-intl weight). Each entry is `{id, description, signals[], direction_implied, status}`; an input that is **stale or absent → `status: "indeterminate"`**, never a false `active` (missing data = WATCH, never REJECT). Thresholds in `src/config/divergence-config.json`. Two precomputed inputs feed it: a **SPY 200-day SMA** (reduced by the pure `_sma_from_rows` from already-fetched FMP rows; the fetch is in the orchestration layer so the detector stays no-network/testable) and the **aggregate international weight** (holdings × the `AMPLIFIER_INTL` roster). The Phase-4 prompt now adjudicates it (Section 2 divergence loop; an `active` one can be override evidence, an `indeterminate` one cannot).

**Override protocol + enforcement** (responsiveness brief Phase 4/Finding 2/#28 — the phases that **change report behavior and make the reference enforceable**). The analyzer prompt **consumes `reference_weights` / `divergences` / `transition_watch`** and executes toward the reference: Section 2 has a **Reference column** and **Current-vs-Reference gap**, and **Recommended = Reference ± logged overrides**. Deviating — *including a "hold" of a sleeve more than `gap_band_pp` off reference* — requires a structured **`overrides[]`** record (**OVERRIDE_SCHEMA_V1_1**, with a mandatory per-sleeve **`sleeve`** field): `{sleeve, premise_challenged, direction (de_risk|re_risk), magnitude_pp, evidence[], falsifier, falsifier_date, clean_data_only}`. `analyzer.assert_override_prompt_schema` gates the prompt on load (sentinels `OVERRIDE_SCHEMA_V1_1`/`reference_weights`/`overrides`), mirroring the flex gate. Three deterministic layers run post-model:
- **Tier-2 override validation** (`shared/overrides.py::validate_overrides`): structural gates (sleeve + falsifier + date + clean non-empty evidence + within `max_magnitude_pp` band + valid direction) reject; the **de-risk/re-risk asymmetry** (spec §6) accepts a de-risk override on one clean item, **downsizes** an under-evidenced re-risk override, **rejects** one with no evidence.
- **Band enforcement** (Finding 2, `shared/reference_execution.py::reconcile`): D1 an override caps the *residual* not the move (`required_move_total = max(0, |gap| − max(allowed_residual, gap_band_pp))`); D2 tranche pace (`required_move_today = min(required_move_total, tranche_pp_max)`); D3 a de-risk shortfall (sell overweight amplifier / buy underweight damper-or-SGOV) is **synthesized** as a `source:"band_enforcement"` trade — re-risk shortfalls are only flagged (never auto-added). This kills the 2026-06-30 silent-hold pathology (the model could previously just not-trade an out-of-band sleeve).
- **Tier-1 trade validator** (#28, `shared/trade_validation.py::validate_trades`, runs AFTER the reconcile merge): V1 gate/roster (no amplifier buy while gate closed; core-only in `trades[]`), V2 exemption (no exempt-hold sell), V3 window (post-trade weight inside `reference ± max(residual, band)`, floor-protected), V4 mechanical (sell ≤ held, buy ≤ cash-after-sells, integer shares, min-notional). Rejected trades move to `rejected[]` (never submitted); clamped trades trimmed to the window edge. **This is what makes "enforced downstream" true** — before #28 a hallucinated trade flowed from LLM JSON straight to Alpaca.

An override can never loosen the deployment gate or breach a Tier-1 limit (floor/ceiling/exempt). Override decisions persist write-once to **`OverrideHistory`** (`analyzer._write_override_history`, layer-tagged, PK=year-month); **Phase 5 (merged) stamps their matured outcomes** vs the reference counterfactual. Config: `src/config/risk-limits.json` → `override_protocol` (`max_magnitude_pp` 15, `re_risk_min_evidence` 2, `gap_band_pp` 5) + `reference_execution` (`tranche_pp_max` 10, `enforce` true, `enforcement_turnover_max_pct` 20, `min_notional_usd` 115). **Auto-execute is paper-only (`AUTO_EXECUTE_ENABLED=true`); a `validation_error` file is refused by the auto-executor (fail-closed); live trading still requires human approval.**

**`transition_watch`** (responsiveness brief Phase 3) is a deterministic **pre-staging** signal: realized inflation is laggy, so when the Phase-2 `leading_vs_lagging_inflation` divergence is `active`, `collector._build_transition_watch` projects the quadrant the *leading* signal points to and emits a **bounded, partial lean** that `reference_weights` applies — **without moving the binding `active_quadrant` / `regime_gate` / realized inflation axis** (spec §6). It **reuses** the Phase-2 divergence (never re-derives breakevens/oil). Output: `{active, projected_quadrant, direction (de_risk|re_risk), staged_fraction, basis[], status}`. **Asymmetry (the safety):** a *de-risk* transition (projecting a more defensive quadrant) stages readily at the full fraction; a *re-risk* transition (more offensive) needs ≥`re_risk_min_confirmations` leading signals agreeing (breakevens AND oil) and a smaller fraction, else it does not activate. Leading data stale/absent → `status:"indeterminate"`, no lean. `reference_weights` applies it as a **convex blend** (`(1−f)·base + f·projected`, `f`≤0.30 — never a full flip) and surfaces it in its `transition_lean` field. Config in `risk-limits.json` → `transition_watch`. **Phase 3 changes `reference_weights`** but is still report-inert until Phase 4.

**`reference_weights`** (strategy-spec §10 — "precomputed target weights the LLM executes toward") is the deterministic per-ticker REFERENCE allocation the analyzer reasons *against* — the layer that anchors the call→target→trades leap where the book previously rationalized silent inaction. Built by `collector._build_reference_weights` from the active quadrant × a deterministic **conviction proxy** (0–10, stands in for the analyzer's Risk Score, which isn't available at collect time) × the **DXY dollar switch** (US-vs-intl amplifier tilt), constrained by `src/config/risk-limits.json` (90%-of-core ceiling, 0.1% sleeve floor, single-name caps, cash band 5–15%/shock-3 25%, AMZN/GOOGL exempt holds). It also applies the Phase-3 **`transition_watch`** lean as a bounded convex blend (surfaced in `transition_lean`). Emits `target_weights_pct` (per-ticker % of equity), the conviction proxy + drivers, `active_quadrant`/`favored_bucket`/`borderline`, `dollar_tilt`, `transition_lean`, and `binding` constraints. It is a **reference, not a mandate** — the analyzer may deviate only via a falsifiable, magnitude-bounded, logged override (Phase 4, merged), and any gap it neither trades nor overrides is now force-closed at tranche pace by Finding-2 band enforcement. The `shared/quadrants.py` block model (Amplifier/Damper + §3 per-quadrant concentrate lists) is the single source of truth shared by collector and prompt. *(Supersedes the never-built `flex_stops` trailing-stop plan and the interim `concentration_gap` precursor.)*

## International holdings requiring special treatment
IDVO (international dividend + covered call overlay), IDMO (international momentum), AIA (Asia 50). Need: ETF look-through from FMP, international macro from FRED (EUR/USD, USD/JPY, USD/CNY, ECB rate, China PMI, Japan 10Y).

## Rules
- Never store secrets in code — always Key Vault with Managed Identity
- All functions emit custom metrics to App Insights
- Blob is source of truth; tables can be rebuilt from blobs (the rebuild procedure is not yet written up as a standalone runbook — there is no `docs/runbooks/` dir; the reconstruction logic lives in the collector's table-write path)
- Human approval required for ALL **live** trade execution — no autonomous live trading. **Paper-only auto-execute** is enabled via app setting `AUTO_EXECUTE_ENABLED=true`: a 09:35 ET timer (`auto_executor`, NCRONTAB `0 35 9 * * 1-5`, ET-local via `TZ=America/New_York`) reads `daily-trades/{ET-today}.json` and submits every recommendation to Alpaca paper, gated by Alpaca market clock (defers if closed). **Retry timers** at 10:05 + 11:05 ET (`auto_executor_retry`, #29) cover variable analyzer latency / missed opens; idempotent via a cache asymmetry (terminal outcomes cached, failures not) + date-scoped `client_order_id`; a `no_trades` outcome at ≥11:00 ET logs ERROR (analyzer never produced the file). The executor **refuses** any file the Tier-1 validator flagged (`validation_error` or a rejected-stamped trade — fail-closed). It also applies a **defensive sell filter**: any sell against a symbol not held in the Alpaca paper account is dropped (status `not_held_in_paper_account`); oversize qty is trimmed. Drops recorded in `daily-executions/{date}.json` `skipped[]`.
- **One-time paper seeder** (`POST /api/seeder`): idempotent mirror of `src/config/portfolio.json` into Alpaca paper. Use `whole_shares_only=true` for off-hours seeding (Alpaca rejects fractional day-orders queued outside market hours — we floor qty to int, drop sub-1 tickers). Symbols already held OR with a pending open buy order are skipped (`skipped_already_held` / `skipped_open_order_pending`) so re-runs never duplicate. Per-run report written to `seeding/{utc_ts}.json`.
- Phase 1 must run clean 30+ days before Phase 2 is enabled
- Temperature 0.2 for Claude analysis calls (consistency)
- Sells execute before buys in multi-trade recommendations (free up cash)

## Deployment lessons (hard-won — see infra/modules/storage-roles.bicep + .github/workflows/deploy-code.yml)
- Function App MI requires **Storage Account Contributor** on the storage account in addition to Blob Data Owner / Queue Data Contributor / Table Data Contributor. Host startup calls `BlobServiceClient.GetPropertiesAsync()` which needs `blobServices/read`, not in the data-plane roles. Without it: persistent `AuthorizationPermissionMismatch 403`, host faults, zero functions registered.
- Workflow pip install MUST pin manylinux2014 wheels (`--platform manylinux2014_x86_64 --python-version 3.11 --implementation cp --only-binary=:all:`). GitHub `ubuntu-latest` ships GLIBC 2.39; Functions Linux Consumption image is older. Native wheels (e.g. `cryptography`) otherwise fail with `GLIBC_2.33 not found` and the Python worker silently fails to load.
- Deploy model: run-from-package via blob (`WEBSITE_RUN_FROM_PACKAGE=<blob URL>`). `func azure functionapp publish` does not work with identity-based `AzureWebJobsStorage`. App runs in read-only mode — portal Test/Run hits CORS; invoke via admin REST instead: `POST https://func-pfauto.azurewebsites.net/admin/functions/<name>` with master key from `az functionapp keys list`.
- Workflow path filter is `src/**`; workflow-only changes need manual `gh workflow run "Deploy function code" --ref master`.
- Quiver `/beta/live/lobbying` and `/beta/live/gov_contracts` return ~20K rows of all-market activity per call (~12 MB + ~4 MB JSON). Collector MUST filter client-side to portfolio tickers ∪ ETF watchlist AND last 90 days — otherwise the daily snapshot balloons to ~20 MB and the analyzer prompt blows past Claude's 1 M-token context window (observed: 6.2 M tokens → permanent 400). See `_row_ticker`/`_row_date` filter in `src/collector/handler.py` after the Quiver fetch. Commit `20cb2b0`.
- `host.json` `functionTimeout` is `00:40:00`. The old `00:10:00` was the **Consumption** plan max; on **Flex Consumption** the cap is far higher and the Foundry client allows up to 600 s/attempt × 3 retries, so 10 min could hard-kill the analyzer mid-Claude-call (no `Executed`, no exception, no report — just a silent recycle at the 10-min boundary). Large June snapshots (~1.2 MB / ~240K tokens) exposed this. Can also be overridden live without redeploy via the `AzureFunctionsJobHost__functionTimeout` app setting. Commits `743b5ad` (original), 2026-06-09 fix.
- **EventGrid blob-trigger `functionName` (Flex Consumption)**: the webhook URL in `deploy-code.yml` MUST use `functionName=Host.Functions.analyzer`, NOT bare `functionName=analyzer`. The bare form is accepted by `/runtime/webhooks/blobs` (HTTP 200, EventGrid reports DeliverySuccess) but cannot be routed to the registered listener, so the analyzer **never fires** and nothing errors — looks healthy end-to-end while silently dropping every event. Also requires the `Microsoft.EventGrid` resource provider to be **`Registered`** (not `Registering`) or storage publishes zero events (PublishSuccessCount=0). Diagnosed 2026-06-09.
- Runtime knobs `TZ=America/New_York`, `AUTO_EXECUTE_ENABLED=true`, and `AzureFunctionsJobHost__functionTimeout=00:40:00` now live **in `functionapp.bicep`** (IaC), so an `infra/**` deploy sets rather than wipes them. They were previously applied post-deploy, and an `az deployment group create` replaces the function app's app-setting set wholesale — which silently dropped them after the Flex migration and disabled the pipeline. Without `TZ`, crons run in UTC (collector 09:00 UTC = 05:00 ET; auto_executor 09:35 UTC = pre-market → defers forever). `AUTO_EXECUTE_ENABLED=true` in IaC intentionally overrides the "Phase 1 clean 30+ days" gate. Note: setting any app setting restarts the host — which is also what (re)connects Application Insights if telemetry has gone silent.
- **Editing `deploy-infra.yml` or `deploy-web.yml` self-triggers that deploy.** Both include their **own filename** in the push path filter (e.g. `paths: ['infra/**', '.github/workflows/deploy-infra.yml']`), so a workflow-only edit to either fires a full deploy on push. `deploy-code.yml` is the exception — it filters on `src/**` only, so editing it does NOT trigger (needs manual `gh workflow run`). Consequence observed 2026-06-15: bumping action versions in all three YAMLs triggered an unintended infra + web deploy. The infra deploy re-applied unchanged Bicep and — per the SWA secret-wipe issue (FOLLOWUPS #2) — wiped the SWA's `STORAGE_CONNECTION_STRING` + `FUNC_MASTER_KEY`, breaking `/today` until restored via the #2 runbook. This was the **second** occurrence of that wipe; the permanent fix is moving those secrets to Key Vault references in `staticwebapp.bicep`. The trading pipeline was unaffected (func-app knobs are in Bicep). Also note: portfolio resources live in the **EasyGridsProduction** subscription under the **jgarrote@easygrids.com** identity — a DIFFERENT Entra tenant from a Quirch-default az session. With the wrong identity even `--auth-mode login` blob reads fail with `InvalidAuthenticationInfo: Issuer validation failed` (observed 2026-07-07). The working sequence: `az login --use-device-code` as jgarrote@easygrids.com, then `az account set --subscription EasyGridsProduction`. Only then do blob reads, `az functionapp keys list`, and KV secret reads succeed.

## Spec documents (in docs/specs/ — each has a paired .docx generated by scripts/docx_to_md.py; the .md is authoritative)
Full details in these companion documents — read them for implementation specifics:
- **`growth_strategy_spec_v1.md` — the north-star strategy spec (regime-concentration machine; the source of truth all automation is downstream of). Read this first.**
- `Data_Sources_Reference_v1.3.md` — all API endpoints, schemas, budget (supersedes v1.2, which is retained for history)
- `Storage_Architecture.md` — blob containers, table schemas, retention
- `Analyzer_Pipeline.md` — context assembly, memory, response parsing, Alpaca mapping
- `Phase_C_Performance_Feedback_v1.0.md` — self-measurement vs SPY + decision-outcome learning (Phase C, largely implemented; see FOLLOWUPS #7 + Done)
- `Flex_Catalyst_Engine_v1.0.md` — the intraday catalyst Flex engine (`src/flex/`, FLEX_ENABLED-gated; the active flex design)
- `Flex_Conviction_Sleeve_v1.0.md`, `Flex_Trailing_Stop_v1.0.md` — SUPERSEDED earlier flex designs, retained for history (see FOLLOWUPS #10)
- `Future_Project_Wheel_Strategy.md` — a future options-wheel project, not yet started

(There is **no** standalone "Architecture Spec" doc — system design/security/deployment lives in `growth_strategy_spec_v1.md`, this file's "Deployment lessons", and the Bicep in `infra/`.)
