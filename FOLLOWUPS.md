# Follow-up items

Running backlog of known-open work. Newest context at top. When you pick an
item up, move it to **Done** with the date + commit so the history is visible.

**Last session: 2026-06-12** — overhauled the analyzer system prompt
(`src/config/project-instructions.md`, commit `1de4624`): fixed the stale E*TRADE
inputs description, added weight→shares conversion recipe, cash floor,
earnings-window policy, flex exit discipline, the **flex gatekeeper** (six gates,
BUY/WATCH/REJECT verdicts, kill criteria), the **thematic capex cascade** +
theme ledger, and an input-hygiene (prompt-injection) guard. Items #5–#9 below
are the agreed roadmap that builds on it (Phases B/C from the improvement plan +
collector enablers). Also corrected stale CLAUDE.md (repo structure, max_tokens).

**Session 2026-06-09** — diagnosed and restored the auto analyzer/execution
pipeline (dead since the Flex Consumption migration ~2026-06-01). Root causes and
the permanent fixes are in the commit messages (`36bd465`, `6171eeb`) and in
CLAUDE.md → "Deployment lessons". Live Azure changes applied out-of-band that
session: `az provider register Microsoft.EventGrid`; EG webhook `functionName`
→ `Host.Functions.analyzer`; app settings `TZ` / `AUTO_EXECUTE_ENABLED` /
`AzureFunctionsJobHost__functionTimeout` (now also in `functionapp.bicep`); SWA
`STORAGE_CONNECTION_STRING` / `FUNC_MASTER_KEY` restored after an infra deploy
wiped them.

---

## Open

### 1. Verify the first fully-unattended weekday run (HIGH — time-sensitive)
The chain (collector → EventGrid → analyzer → `daily-trades` → `auto_executor`
→ Alpaca) is fixed and verified **through report/trades generation**, but
`auto_executor` (09:35 ET timer) was **never live-tested** — the market was
closed when the fix landed. On the next weekday, confirm without intervening:
- `daily-reports/{date}.md` + `daily-trades/{date}.json` appear ~09:00–09:10 ET
- `daily-executions/{date}.json` appears after 09:35 ET with **submitted** Alpaca
  paper orders (this is the first real auto-execute)

Telemetry now flows to App Insights (`appi-pfauto-prod`, `cloud_RoleName ==
'func-pfauto'`) — watch `traces`/`exceptions` there if anything is missing.

### 2. SWA secret app settings are wiped by every infra deploy (HIGH)
`web/api` reads blob storage via `STORAGE_CONNECTION_STRING` and calls the
func-pfauto executor via `FUNC_MASTER_KEY`. These (plus `AAD_CLIENT_ID` /
`AAD_CLIENT_SECRET`) are **post-deploy-only** secrets — see the note in
`infra/modules/staticwebapp.bicep`. Because `az deployment group create` replaces
the SWA's app-setting set wholesale, any `infra/**` deploy wipes them and the
`/today` page breaks (`/api/dates → 500`, table stuck on "Loading…").
- Observed + restored live on 2026-06-09.
- **Fix:** move these to **Key Vault references** in `staticwebapp.bicep` (mirror
  how `functionapp.bicep` handles secrets), so deploys set rather than wipe them.
  Requires the secret values to live in `kv-pfauto-prod` first.
- **Until then:** after any infra deploy, re-apply:
  `az staticwebapp appsettings set --name swa-pfauto -g rg-portfolio-automation-prod --setting-names STORAGE_CONNECTION_STRING=<conn> FUNC_MASTER_KEY=<masterkey>`

### 3. Entra ID auth is currently OFF on the SWA (MEDIUM — security)
`web/staticwebapp.config.json` has `allowedRoles: ["anonymous"]` on `/api/*` and
`/*`, so the site is **publicly reachable** — this deviates from the documented
"Entra ID Easy Auth, owner role" design. (Pre-existing in the committed config,
not changed this session.) Note: `AAD_CLIENT_ID/SECRET` were wiped by the infra
deploy and the client **secret is not recoverable**.
- **If re-enabling:** mint a new client secret on the app registration, restore
  `AAD_CLIENT_ID` / `AAD_CLIENT_SECRET` (via KV ref per #2), and set
  `allowedRoles` back to the authenticated/owner role in `staticwebapp.config.json`.

### 4. Migrate the SWA API off the storage connection string (LOW)
CLAUDE.md mandates "Managed Identity only — no connection strings." `web/api`
still uses `STORAGE_CONNECTION_STRING` (account key). Switching it to
`DefaultAzureCredential` + the already-present `STORAGE_ACCOUNT_NAME` would align
with the rule and **eliminate the secret entirely** — which also resolves the
storage half of #2.

### 5. Verify the first report under the v1.1 prompt ✅ DONE 2026-06-13 (PASS)
Verified against the `2026-06-12` report+trades blobs. Parser intact (valid JSON,
5 trades, all echo fields). All 9 sections in new order; section 6 "Themes & Flex
Pipeline" rendered. Flex exit discipline fired live (ADBE sold on kill criterion).
Sells before buys; core trimmed not zeroed; data discipline held (deferred to FRED
over a contradictory ECB headline). **Theme ledger working** — AI capex cascade
with tier migration, watching MU (memory) June 24; correctly declined to nominate
Tier 3 names (ETN/NEE/XLU) for lack of fundamentals → confirms #8 is the binding
constraint on real flex nominations. Moved to Done.

### 5b. Shock framework is crisis-biased — no positive-shock path (LOW)
On 2026-06-12 `shock_level 3` fired on a *bullish* shock (Iran peace, SPY +1.7%).
The model used `regime_override: "tilt_lifted"` (a level-2 enum value) because at
level 3 the only defined value is `"acute_de_risk"`, which assumes de-risking; the
"always pair an acute call with a defensive trade" rule also assumes crisis. It
handled it sensibly (kept ~45% cash, tiny adds) but the prompt has no clean acute-
bullish path. Consider an enum value / narrative branch for positive acute shocks.
(Cosmetic also noted: rotation 3.6 labeled "transition_window (4–6)" — 3.6 is in
the 3–4 gap; no trade resulted.)

### 6. Phase B — stop_loss / take_profit doctrine ✅ DONE 2026-06-13
Resolved as advisory daily-checked levels (not broker orders), per-layer:
- **Flex** `stop_loss` = the published kill-criteria price trigger; analyzer
  compares it to the snapshot price each run and proposes a full exit if breached.
  Flex names can be liquidated fully.
- **Core** stops are null — core is never sold to zero; new **~0.1% / ≥1-share
  weight floor** replaces the old "trim to 0%" rule (All-Weather backbone always
  held). Decided with the account holder.
- Executor unchanged behaviorally — clarifying comment added in `_place_one` that
  the fields are intentionally NOT sent as bracket/OCO legs (a resting broker stop
  would make the executor stateful and collide with the daily re-recommendation
  loop). True broker brackets remain future work and belong with the wheel-monitor
  component, not Phases 1–2.
Moved to Done below.

### 7. Phase C — performance feedback loop (HIGH value, largest effort)
**Spec: `docs/specs/Phase_C_Performance_Feedback_v1.0.md`** (data contract +
design rationale; the three open decisions were locked 2026-06-15: fully-invested
SPY benchmark with cash_pct surfaced, 60d headline horizon, enum buckets confirmed).
The mission is "beat SPY over 12 months" but the analyzer never sees its own
results. Sub-steps, shippable independently:
- **§9 prerequisite ✅ (2026-06-18):** executor `_write_trade_history` now writes
  lowercase keys aligned with the analyzer's recommendation row, so the upsert
  MERGES into one coherent TradeHistory entity (no more duplicate mixed-case
  columns). `status` transitions recommended → submitted/error; adds `exec_qty`,
  `executed_at`, `alpaca_*`.
- **7b. Outcome stamping ✅ (2026-06-18):** collector `_stamp_trade_outcomes(fmp)`
  runs each day (non-fatal, wrapped): finds recommendation rows whose 30/60/90d
  marks passed and stamps `ret_Nd_pct` / `spy_ret_Nd_pct` / `excess_Nd_pp` /
  `call_correct_Nd` (buy beat SPY / sell lagged SPY) + `outcome_status`. One FMP
  `get_historical_price_light` call per maturing symbol + SPY; nearest-trading-day
  snap for weekends/holidays. Added `storage.query_entities()` to read aged rows.
  *Not yet verifiable live until the first rows hit their 30d mark (~late June, the
  account began ~2026-05-26) — check a stamped row then.*
- **7a. `performance` block (REMAINING):** collector computes equity vs
  fully-invested SPY since inception + rolling 30/60/90d + `cash_pct` into the
  snapshot. Reuse the web `performance` endpoint math (`04343b4`); data already in
  every snapshot (`paper_account.equity` + `prices.SPY.c`).
- **7c. `track_record` + prompt wiring (REMAINING):** aggregate stamped rows into
  the compact `track_record` block (hit-rate by layer/trigger/thesis, calibration,
  sample sizes) and add a prompt section telling the analyzer to review it as a
  calibration signal (not a per-name veto). Depends on the §7 reasoning-capture
  enum fields (`primary_trigger`/`thesis_type`) also being emitted — not yet added.

### 8. Collector: fetch data for flex candidate names — static v1 ✅ DONE 2026-06-15
**Static v1 shipped:** `config/flex-candidates.json` (seed: ETN, NEE, XLU, MU) is
loaded by the collector (`_load_flex_candidates`), deduped vs holdings, capped at
20, and its tickers get FMP profiles (→ new `flex_candidates` snapshot block) +
EOD prices (merged into `prices`). Gatekeeper G2 + the inputs list now point at
`flex_candidates`, so a seeded non-held name can clear G2 and reach BUY. Edit the
config to pin more names.
- **v2 (remaining, MEDIUM):** make the candidate list **dynamic** — have the
  analyzer emit a `watch_candidates` array in the trades JSON and have the
  collector merge the previous run's list, so the AI self-requests data for names
  it surfaces (e.g. a fresh congressional cluster) without a manual config edit.
  2-day latency (name → data next run → actionable run after); acceptable.
- Shares the `get_historical_price_light` path with Phase C §5 outcome stamping.

### 9. Collector: data tier for the deferred gatekeeper gates (LOW — after #8)
The gatekeeper explicitly defers signals we don't collect: balance-sheet
survivability (net debt/EBITDA, maturities), consensus estimate revisions,
insider buying, 8-quarter gross-margin trend. Candidate sources on existing
keys: FMP quarterly income statements + key ratios + insider transactions
(verify free-tier availability + the 250 req/day budget before building).
Optional in the same area: scan the **full** Finnhub news response (collector
currently keeps only the first 50 general headlines) for sector-agnostic
capital-flow fingerprints (capex, backlog, shortage, subsidy) into a
`news.capex` block — feeds the thematic cascade if 50 headlines prove too
narrow an aperture.

---

## Done
- **2026-06-15** — #8 static v1: `config/flex-candidates.json` (ETN/NEE/XLU/MU) +
  collector fetch (`flex_candidates` snapshot block, prices merged) + gatekeeper
  G2/inputs pointers. Unblocks flex nominations for seeded non-held names. Dynamic
  v2 (analyzer-emitted list) remains open under #8.
- **2026-06-13** — Verified first v1.1 run (#5, PASS — see above) against the
  2026-06-12 blobs.
- **2026-06-13** — Phase B (#6): stop_loss/take_profit settled as flex-only
  advisory levels checked daily by the analyzer (= the published kill trigger);
  core stops null; added ~0.1% / ≥1-share **core weight floor** (core never sold
  to zero); clarifying comment in executor `_place_one`. Prompt + CLAUDE.md +
  executor comment. Decided 0.1% floor with the account holder. Also: **$200
  minimum-trade floor now exempts flex** — flex can be opened/trimmed/sold-complete
  regardless of notional (a fired kill criterion must always close the position);
  floor still applies to core dust nudges.
- **2026-06-12** (`1de4624`) — Phase A prompt fixes (E*TRADE staleness, weight→
  shares recipe, cash floor, earnings window, flex exit discipline, output
  budget guard) + flex gatekeeper v1.1 + thematic capex cascade + input hygiene.
  From the improvement plan discussed that session; Phases B/C became #6/#7 above.
