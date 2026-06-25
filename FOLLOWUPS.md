# Follow-up items

Running backlog of known-open work. Newest context at top. When you pick an
item up, move it to **Done** with the date + commit so the history is visible.

**▶ START HERE — last session 2026-06-25.** All work committed & pushed; GitHub is
the source of truth. **Phase C is complete (closes Open #7):** 7a (`performance`
scoreboard) + 7c (`track_record` + §7 reasoning enums + "Track record" prompt
section) shipped in `c41ea6c`, **deployed to func-pfauto**, and **live-verified** —
the snapshot now carries both blocks (account **+0.88pp vs SPY** since inception
2026-05-26; 30d hit-rate 0.46 on n=13, which also confirms 7b outcome-stamping is
live). Also refined the **flex gatekeeper G4/G5** this session: catalyst timing
loosened to the flex horizon (~1–2 quarters) with a thematic-milestone path, paired
with a G5 anti-chase guard (a name already at a 52-wk high on its cited theme has
re-rated → fails G5). See the two newest Done entries. Prior session shipped Phase C
step 1 (`6b4e355`), the CI pipeline (`d11236d`), and the barbell doctrine (`295f5b9`).

**Next priorities (in order):**
1. **Finish Phase C live verification (mostly done).** Confirmed 2026-06-25 via a
   manual collector run: `performance` block populates (no pre-funding equity
   step; cache built clean), `track_record` populates, and 7b stamping is live
   (30d n=13). **Still unverified:** that a *real flex buy* emits the §7 enums
   (`primary_trigger`/`thesis_type`/`trigger_evidence`/`catalyst_date`) into
   `daily-trades` + TradeHistory — today's MU buy predated the deploy and the
   afternoon re-run only re-affirmed MU, so the path hasn't run under the new
   code yet. Also watch the **first 60d maturation (~late July)** to confirm the
   headline `track_record` hit-rate fills in.
2. **25-ETF roster swap + KMLM ballast bucket** — analyzed & agreed in principle
   (all-weather ETF core, single names move to flex). Needs migrating the held
   single names (INTC/AMZN/GOOGL/MCK) into flex + a new convexity/ballast bucket
   for KMLM (token floor, scale up on stress). Not yet drafted.
3. **Verify first stamped 30d outcomes** (~late June, account began ~2026-05-26) —
   check a TradeHistory row has `ret_30d_pct`/`call_correct_30d` populated.

**Environment notes (read before editing):** repo is mirrored to a fresh clone at
`C:\dev\portfolio-automation` to escape OneDrive — if you're working from the
OneDrive path still, the **OneDrive silent-revert hazard** applies (it clobbered
the prompt and executor working copies twice — verify `git status` / line counts
before committing; `[[repo-onedrive-revert-hazard]]`). Local dev: Python 3.11 via
`py`; a venv with deps lives in `%TEMP%\pfvenv` (run `ruff check .` + `PYTHONPATH=src
pytest -q`). Azure mgmt: portfolio resources are in the **EasyGridsProduction**
subscription (`az account set --subscription EasyGridsProduction`).

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
- **7a. `performance` block ✅ (2026-06-25):** collector now computes equity vs
  fully-invested SPY since inception + rolling 30/60/90d (null until enough
  history) + `max_drawdown_pct` + `account.cash_pct` into the snapshot
  (`performance` block, non-fatal). Reuses the web endpoint basis (a day counts
  only with both `paper_account.equity` and `prices.SPY.c`) but is backed by a
  compact self-healing cache blob `performance/equity-series.json` (each ~1 MB
  snapshot downloaded at most once ever, not re-read daily — collector-runtime
  safe). Prompt: `performance` added to the Inputs list + a Summary scoreboard
  line (surfaces cash drag). 10 unit tests on the pure builder. Still no live row
  until the cache first populates on the next collector run.
- **7c. `track_record` + prompt wiring ✅ (2026-06-25):** collector
  `_aggregate_track_record` rolls all TradeHistory rows into the compact
  `track_record` snapshot block — hit-rate `by_layer` / `by_trigger` / `by_thesis`
  at the 60d headline (with `horizons` 30/90d for context), confidence
  `calibration`, `over_trading`, `sample_size` + `caveat`. Capture-fine/report-
  coarse with the n≥10 promotion rule (§8). The §7 reasoning enums
  (`primary_trigger`/`thesis_type`/`trigger_evidence`/`catalyst_date`) are now
  emitted in the trades JSON (prompt schema + rules) and persisted write-once by
  the analyzer (`_write_trade_history`); a new "Track record — calibrate against
  your own results" prompt section tells the analyzer to use it as a calibration
  signal, not a per-name veto. Non-fatal in the collector; 12 unit tests on the
  pure aggregator. **This closes Open #7 (Phase C).** Remaining = live verification
  (priority #1 above) + the v1 caveats in the spec (price-return only, core-layer
  taxonomy deferred).

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
- **2026-06-25** — Flex gatekeeper G4/G5 refinement in `project-instructions.md`:
  G4's "earnings within 14 days" was being read as a blanket near-term-catalyst
  requirement, rejecting `thematic` Tier-2/3 nominations whose recognition event is
  a quarter or two out (systematically late vs the cascade's "catch it before it
  re-rates" intent — surfaced by the 2026-06-23 ETN reject). G4 now requires a
  *dated* catalyst within the flex horizon (~1–2 quarters / 60-day re-affirm), with
  14-day earnings as the *tightest example, not the bar*; the next scheduled print
  or a specific demand-visibility milestone qualifies if dated. Paired with a G5
  tightening: a name at/near a 52-week high on the cited theme has already re-rated
  → fails G5 (no edge left), preserving the anti-hype discipline. Loosens catalyst
  *timing*, not the *requirement*. No code change; takes effect next analyzer run.
- **2026-06-25** — Phase C 7a (`performance` scoreboard) + 7c (`track_record` +
  §7 reasoning enums + prompt wiring) shipped & deployed (`c41ea6c`); closes #7.
  Verified live: snapshot carries both blocks (account +0.88pp vs SPY since
  inception; 30d hit-rate 0.46 n=13 confirming 7b stamping is live). Details in #7.
- **2026-06-18** — Barbell doctrine in `project-instructions.md`: (1) conviction-
  scaled concentration — core tilt into the favored quadrant scales with the Risk
  Score (0–2 → ~80–90%, … 9–10 → capital preservation); 0.1% floors reframed as
  optionality ("all-weather toolkit, tactical deployment"). (2) Regime-adaptive flex
  — flex is the alpha sleeve in *every* quadrant (offense Q1, cyclicals Q2, defense/
  value Q3, stands down toward cash in acute Q4/shock-3); aggregate size scales with
  conviction. (3) Constant quality gate — gatekeeper bar never relaxes in a bull,
  only activity/size varies. Reconciled the ≤2pp guardrail (applies to low-conviction
  only; cadence rule is the anti-whipsaw, not a weight cap). Roster-agnostic (works
  on the current 24). **Still pending:** 25-ETF roster swap + KMLM ballast bucket
  (separate, needs migration of held single names); Phase C 7a/7c (the measurement
  that validates the aggression).
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
