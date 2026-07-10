# Follow-up items

Running backlog of known-open work. Newest context at top. When you pick an
item up, move it to **Done** with the date + commit so the history is visible.

**▶ START HERE — last session 2026-07-02 (outage diagnosis + streaming hotfix, PR #7;
merged `abd1538`, deployed, live-verified).** The 2026-07-02 morning run produced NO
report (`/today` stuck on 07-01). Root cause found and FIXED:
- **Root cause:** `shared/clients/foundry.py` called Claude **non-streaming** — zero bytes
  flow while the model generates, and **Azure's outbound SNAT/LB idle timeout silently
  drops connections idle ~4 min**. Post-Phase-4 reports generate 13–16K+ output tokens
  (~4–5 min at ~60 tok/s), so calls started dying mid-generation. Foundry metrics on
  07-02: **13/13 calls HTTP 499**, TimeToLastByte avg **240–270 s** (the 4-min wall, not
  the client's 600 s timeout). 07-01's *morning* run failed identically (12×499 at
  13:00Z); its 20:28Z report only exists because a later attempt finished just under
  4 min. Ruled OUT: SWA secret wipe (settings intact), EventGrid (DeliverySuccess=1),
  token quota (zero 429s). The PR #5 max_tokens bump 16K→24K + verbose Phase-4 format is
  what pushed generation over the wall.
- **Fix (PR #7):** `FoundryClient.complete` now requests SSE (`"stream": true`) and
  accumulates `content_block_delta` text — the connection never idles. Timeout is now
  (connect 30 s, read 180 s **inter-chunk**); a stream that ends without `message_stop`
  raises and the retry loop re-attempts. `complete()` signature unchanged, analyzer
  untouched. 8 new tests pin the SSE contract; **suite 194 green, ruff clean.**
- **Live-verified end-to-end:** re-uploaded `daily-snapshots/2026-07-02.json` (21:02Z) to
  re-fire EventGrid → analyzer; **`daily-reports/2026-07-02.md` (35 KB) +
  `daily-trades/2026-07-02.json` landed 21:17Z** — a >4-min generation completed, which
  was impossible pre-fix. The report proposes the **19-trade reference-driven de-risk
  rotation** (sell SPY/QQQ + 15 others, buy GLD/TLT ~0.8/0.72 conf) — i.e. Phase 4
  executing toward reference instead of the silent hold; review it against **Finding 2
  (still the NEXT TASK)** below. auto_executor had already run at 09:35 ET, so today's
  trades were NOT auto-executed; tomorrow generates fresh ones.
- **Watch tomorrow's 09:00 ET run end-to-end** (report ~09:05–09:20 ET, auto_executor
  09:35 ET). Co-symptom that hid the outage: **App Insights telemetry was dark
  ~04:00→19:15Z** (known flakiness — an app-setting touch/host restart reconnects it).
- **Ops note (dev box):** the az CLI default subscription kept flipping back to
  QuirchFoodsSubscription mid-session and the `jgarrote@easygrids.com` login was
  eventually wiped from the profile entirely (suspect: VS Code Azure extension re-auth).
  Verify `az account show` before every az block; re-login + `az account set
  --subscription EasyGridsProduction` as needed.
- **Also shipped: quadrant-vs-SPY performance chart (account holder request).** The
  Performance tab now charts each Dalio quadrant as an **equal-weight basket of its
  `QUADRANT_CONCENTRATE` names** (Option B, decided with the account holder) vs SPY,
  with **regime shading** (background bands tinted by the day's `favored_bucket`) and a
  per-quadrant summary strip (window return + α vs SPY, best-quadrant ★). **Sequencing
  decided with the account holder:** self-rethinking roadmap = new Open **#12 → #13 → #14**
  (quadrant-performance feedback to the analyzer, monthly strategy-review amendment
  channel, intra-quadrant selection freedom) — **deliberately waiting ~1 week** (release
  verification + data accrual; n too small now) with **Finding 2 first**, then Phase 5.
  This week = verify: Jul 3 unattended report (streaming fix), `/performance.html`
  eyeball from home, **Mon Jul 6 full chain incl. first unattended auto-execution
  (closes Open #1)**. Plumbing:
  collector `_load_equity_spy_series` now hydrates each cache point with `closes`
  (CORE_ROSTER EOD) + `favored_bucket` (self-healing: v1 points re-read once) and
  publishes `performance/quadrant-config.json` (quadrants.py → blob → API → chart, no
  duplication); `/api/performance` gained a **cache fast path** (1 small blob instead of
  ~250 × 1.2 MB snapshot downloads per page load — legacy scan kept as fallback) +
  `_quadrant_series` equal-weight index. Palette validated (dataviz 6-check, CVD ΔE
  13.6). **Cache backfilled live** from the dev box (28 points since 2026-05-26
  inception, all hydrated; quadrant indices Q1 96.9/Q2 97.5/Q3 94.9/Q4 97.9 vs SPY
  99.4). Shading is sparse for now (axes only in recent snapshots; flat-growth days
  correctly yield no bucket) and fills in daily. Tests: +11 (205 green). **Eyeball
  `/performance.html` after the SWA deploy** — renders were verified by data-path only.

**▶ Prior session 2026-07-01. Responsiveness brief: Phases 1–4 ALL MERGED
(PR #1/#2/#3/#4) + analyzer context-overflow hotfix (PR #5). Phase 4 is LIVE (first
behavior-changing phase). The live checkpoint exposed 2 reference/override TUNING issues
(next task) — the protocol MECHANISM itself is verified working.**
- **Analyzer outage + hotfix (PR #5, merged+deployed):** after Phase 4 deployed, the analyzer
  produced NO report and `/today` went blank. Root cause = the assembled prompt was **318K input
  tokens vs the claude-sonnet-4-6 deployment's 50K tokens/min quota** → every call throttled →
  silent fail (App Insights telemetry was ALSO down, hiding it). Fixed: (1) **raised the
  deployment token quota 50K→80K ITPM** (Foundry, in-place capacity bump, no approval); (2)
  **trimmed the analyzer prompt 318K→~72K tokens** (`_trim_snapshot`/`_build_user_message`: macro
  allow-list + latest-3-obs, fundamentals field-slim, alt-data caps, recent-report excerpts,
  compact JSON) — no deterministic block lost; (3) **max_tokens 16K→24K** (verbose Phase-4 report
  was hitting the output cap). Verified via live Foundry probe (72K in, full report w/ marker).
  Notes for future: model context window is **1M** (not 200K); the real limit is the per-minute
  **token quota**; Sonnet 5 quota is LOWER (40K); keep Thinking DISABLED (burns quota); model is
  in **East US 2**. Details in `memory/analyzer-context-overflow-fix.md`.
- **Finding 1 ✅ FIXED (no-read reference degeneracy) — PR #6:** in a no-read/low-conviction
  regime (`conviction_proxy >= 7`) `_build_reference_weights` now routes `no_read_ballast`
  (GLD+TLT, 55% of core split) so the reference reads capital-preservation, AND the AMZN/GOOGL
  exemption is applied as a FIXED carve-out (pinned at current, excluded from the renormalize
  scale) so it can no longer balloon. Verified on today's real snapshot: **GLD 32% / TLT 32% /
  SGOV 23.5% / AMZN 3.2% / GOOGL 5.4% / SPY,QQQ→floor** (was GOOGL 38%/AMZN 22%). Config
  `risk-limits.json → no_read_ballast`. 4 new tests, 186 green.
- **Finding 2 (override band vs large legit de-risk rotation) ✅ FIXED 2026-07-03 (PR #11):**
  even with the sane reference, the 2026-07-02/03 gaps (buy GLD/TLT ~−30pp, trim SPY/QQQ
  +17/+14pp) exceeded the 15pp Tier-2 band, and **a rejected override still didn't force an
  action** (silent-hold gap — the exact failure Phase 4 targets). Resolved as a combination
  of design options (b)+(c)+(d): per-sleeve overrides that cap the RESIDUAL (D1), formalized
  tranches (D2), and deterministic de-risk-only enforcement of shortfalls (D3). Details in
  the Done entry. **Brief Phase 5 is now UNBLOCKED.**
- **Phase 4 ✅ MERGED (PR #4, `a47d2e7` on master) — the PAYOFF phase, FIRST that changes report
  behavior; NOT merged):** the analyzer prompt now **consumes `reference_weights`/`divergences`/
  `transition_watch`** and executes toward the reference. §2 gains a Reference column + a
  Current-vs-Reference gap; **Recommended = Reference ± logged overrides**; **inaction is now
  accountable** (a "hold" of a sleeve >`gap_band_pp` off reference requires an override record,
  and if it leaves defense < reference it must clear the higher re-risk bar). New **`overrides[]`**
  JSON contract (OVERRIDE_SCHEMA_V1), gated on prompt load by `assert_override_prompt_schema`
  (mirrors the flex gate). Pure `shared/overrides.py::validate_overrides` enforces Tier-2:
  structural gates reject (missing falsifier/date, empty/dirty evidence, over-band magnitude,
  bad direction); the **de-risk/re-risk asymmetry** (spec §6) accepts de-risk on 1 clean item,
  **downsizes** (halves) an under-evidenced re-risk, **rejects** a no-evidence re-risk. Decisions
  persist write-once to the new **`OverrideHistory`** table (Phase-5 outcome hooks null). Config
  `risk-limits.json`→`override_protocol` (max_magnitude_pp 15 / re_risk_min_evidence 2 /
  gap_band_pp 5). 21 new tests; **full suite 182 green, ruff clean.** Auto-execute stays
  OFF-gated, human approval unchanged, executor untouched, deterministic layer echoed not
  re-derived. **CHECKPOINT PENDING:** cannot run the model locally (no Foundry creds on the box),
  so the checkpoint = deploy the branch to func-pfauto + trigger the analyzer on today's snapshot
  (real Foundry call) + fetch the report to show the real §2 — verifying the 2026-06-30 pathology
  (correct call, zero trades, "appropriately positioned") is FIXED. Design in
  `memory/override-protocol-phase4-design.md`.
Building the **Responsiveness brief** — the missing strategy-spec §10 "precomputed target
weights the LLM executes toward" layer — to kill the *under-trading-rationalized-as-discipline*
failure (2026-06-30 report held SPY 17.25% + QQQ 13.91% in a falling-growth Q3/Q4 regime,
favored bucket at ~9% vs target, proposed zero trades, called it "discipline"). North-star
**`docs/specs/growth_strategy_spec_v1.md`** committed to the repo. Approach = deterministic
**reference, not a mandate**: the LLM reasons against it and may deviate only via a falsifiable,
magnitude-bounded, asymmetric, logged override (de-risk cheap / re-risk dear). Three-tier model
(T1 hard floor / T2 reference+logged override / T3 pure judgment). Brief = 5 phases.
- **Phase 3 ✅ MERGED (PR #3, commit `acda3e4` on master):**
  `collector._build_transition_watch` → new **`transition_watch`** snapshot block, and
  `_build_reference_weights` now **consumes** it. Realized inflation is laggy → this lets the
  LEADING signal pre-stage a bounded partial lean toward the projected quadrant WITHOUT moving
  the binding active_quadrant/regime_gate/realized axis (spec §6). **Reuses** the Phase-2
  `leading_vs_lagging_inflation` divergence (never re-derives). Asymmetry: de-risk stages at the
  full fraction (0.30); re-risk needs ≥2 leading confirmations + smaller fraction (0.15) else
  inactive. Convex blend `(1−f)·base + f·projected` (f≤0.30, never a full flip); surfaced in
  `reference_weights.transition_lean`. Missing leading data → indeterminate. Config in
  `risk-limits.json` → `transition_watch`. Handles the **borderline realized** case (flat
  inflation → the leading signal resolves which side of the Q3/Q4 border). Build-order in the
  collector reworked to divergences→transition_watch→reference_weights (divergences takes a
  minimal binding-quad dict to avoid a cycle). 14 tests; **full suite 161 green, ruff clean.**
  Verified vs today's snapshot: `transition_watch` ACTIVE, projected **Q4**, **de_risk**,
  fraction 0.30, basis breakevens −28bp + oil −21%; the lean lifts **TLT 2.95%→7.38%** in the
  reference while binding fields (active_quadrant None, bucket [Q3,Q4], borderline, conviction
  7.0) are **unchanged**. **Report-inert until Phase 4.** Design in
  `memory/transition-watch-phase3-design.md`.
- **Phase 2 ✅ MERGED (PR #2, commit `55775da` on master):** `collector._build_divergences`
  → new **`divergences`** snapshot list. Deterministic detector of TENSIONS between signals that
  should agree but don't — **describes only, never resolves/ranks/acts** (Phase 4 / the LLM adjudicates).
  Four: `leading_vs_lagging_inflation` (breakevens + oil vs realized core), `credit_complacency`
  (HY OAS ≤10th-pct-rank + no stress → `fragility`), `price_vs_regime` (SPY vs 200d SMA vs
  `active_quadrant`), `dollar_vs_intl_tilt` (DXY switch vs aggregate amplifier-intl weight).
  Stale/absent input → `status:"indeterminate"`, never a false `active`. Two new precomputed inputs:
  SPY 200-day SMA (pure `_sma_from_rows` over fetched rows) + aggregate intl weight. Thresholds in
  `config/divergence-config.json`. 23 tests; **full suite 146 green, ruff clean.** Verified vs today's
  real snapshot: `leading_vs_lagging_inflation` fires ACTIVE ("falling" — breakevens −28bp + oil −21%
  vs flat realized core); the other three correctly `indeterminate` (credit pct-rank 49 not ≤10th;
  price-vs-regime needs a concrete quadrant, today borderline; dollar neutral + intl 10.6% aligned).
  **Behavior-neutral until Phase 4.** Design in `memory/divergences-phase2-design.md`.
- **Commit `8e22912` (ceiling drift closed):** active-quadrant ceiling **canonicalized to 90% of
  CORE** (account-holder decision 2026-06-30, was an 80% spec default / 90–95% prompt drift) across
  new `config/risk-limits.json` (single source of truth) + spec §3/§8 + the prompt conviction
  ladder. **Ceiling decision = CLOSED/locked (not pending).**
- **Phase 1 ✅ MERGED (PR #1, commit `8e22912`+`9da6f8d` on master):** `collector._build_reference_weights`
  + `_conviction_proxy` (deterministic 0–10 stand-in for the LLM's Risk Score, since that isn't
  available at collect time) + `shared/quadrants.py` block model (Amplifier/Damper + §3 per-quadrant
  concentrate lists, `EXEMPT_HOLDS` AMZN/GOOGL, `favored_bucket`/`intersection_names`, DXY US/intl
  split) → new **`reference_weights`** snapshot block. Tier-1 constraints (90%-of-core ceiling, 0.1%
  floor, single-name cap on stocks only, cash band 5–15%/shock-3 25%, exempt holds never forced
  down), borderline intersection blend (never a freeze). 18 unit tests; **full suite 123 green, ruff
  clean.** Verified vs today's real snapshot: it trims SPY/QQQ 17%/14% → floor, concentrates
  GLD/XLP/MCK, conviction proxy 7.0 "low", cash sleeve held at band, sums to ~100%.
- **Backlog (Phase 1 deliberate deferral, not an oversight):** the borderline blend is a
  fixed 60/20 (intersection/divergent) split that does NOT flex with conviction. It errs safe
  (slightly more concentrated into the 3 best cross-regime defensives in a defensive regime).
  Optionally **widen the divergent-ballast share at low conviction** — revisit only after a few
  real reports / once the override + track-record loop shows whether it needs tuning. Also
  parked: per-name intersection weighting (gold multiplier) — equal-weight is correct for now
  (GLD anchors via being in the intersection at ~6x any divergent name, not by out-weighting
  XLP/MCK).
- **Brief Phase 5 ✅ DONE 2026-07-05 (PR #14)** — override-outcome stamping shipped:
  matured `OverrideHistory` rows are graded against the **reference-path
  counterfactual** ("did disagreeing beat obeying" — locked decision 2026-07-04, NOT
  vs SPY) and the aggregate `override_record` block feeds the snapshot + prompt.
  **The responsiveness brief (Phases 1–5) is COMPLETE. #12 → #13/#14 are unblocked.**
  Details in Done.
- **Interim `concentration_gap` work** (earlier same day) is **stashed** (`git stash` "concentration_gap WIP")
  and **superseded** by `reference_weights` — its reusable bits (EXEMPT_HOLDS, favored_bucket) were
  folded in; drop the stash once Phase 4 lands.

**▶ Prior session 2026-06-29 (ops-only, no code change).** The `/today`
page broke with `Error loading report: /api/dates → 500`. Root cause = the **3rd
recurrence of Open #2**: the 2026-06-28 infra deploy re-applied `staticwebapp.bicep`
(declares only 3 non-secret settings) and wiped the SWA's `STORAGE_CONNECTION_STRING`
+ `FUNC_MASTER_KEY`, so `web/api` `_blobs()` raised → 500. **Fixed live** in
`rg-portfolio-automation-prod` by re-applying both via
`az staticwebapp appsettings set` (see Open #2 runbook) and re-running **Deploy web
(SWA)**. Separately investigated a func-pfauto log warning
(`webjobs.storage: Unhealthy — Unable to create client for AzureWebJobsStorage`):
**false alarm** — transient health-probe flap on a worker instance draining at 15:36
UTC; host `Running`, all 8 functions registered, MI has all 4 storage roles + KV,
storage network open, zero such traces in App Insights over the prior 3h. **This fix
is ephemeral — the next infra deploy wipes it again.** Permanent fix still open:
**implement Open #4** (switch `web/api` to `DefaultAzureCredential` via the present
`STORAGE_ACCOUNT_NAME`, eliminating the secret) — this is the recommended next task.
Caveat for whoever verifies: dev-box DNS resolves `*.azurestaticapps.net` to a
captive `192.168.x` IP, so verify `/today` from a normal browser, not curl on the box.

**▶ Prior session 2026-06-25.** All work committed & pushed; GitHub is
the source of truth. **Phase C is complete (closes Open #7):** 7a (`performance`
scoreboard) + 7c (`track_record` + §7 reasoning enums + "Track record" prompt
section) shipped in `c41ea6c`, **deployed to func-pfauto**, and **live-verified** —
the snapshot now carries both blocks (account **+0.88pp vs SPY** since inception
2026-05-26; 30d hit-rate 0.46 on n=13, which also confirms 7b outcome-stamping is
live). Also refined the **flex gatekeeper G4/G5** this session: catalyst timing
loosened to the flex horizon (~1–2 quarters) with a thematic-milestone path, paired
with a G5 anti-chase guard (a name already at a 52-wk high on its cited theme has
re-rated → fails G5). See the Done entries. This session also **specced (not yet
built) the flex trailing stop + catalyst-gated relative exit** —
`docs/specs/Flex_Trailing_Stop_v1.0.md`, committed `e78e25a`, fully decision-locked;
**implementing it is tomorrow's task (Open #10).** And **reviewed the wheel-strategy
spec** (`Future_Project_Wheel_Strategy.md`), found it stale (E*TRADE-dependent data
layer, Logic-App approval, short-vol mandate) and **parked it** (see Done). Prior
session shipped Phase C step 1 (`6b4e355`), the CI pipeline (`d11236d`), and the
barbell doctrine (`295f5b9`).

**Next priorities (in order):**
1. **Implement Flex Trailing Stop v1 (Open #10) — TOMORROW'S TASK.** Spec is done &
   committed (`docs/specs/Flex_Trailing_Stop_v1.0.md`, `e78e25a`), all decisions
   locked. Build: collector `_build_flex_stops` (V = P95 of |Δclose| over 60d,
   trail = 1.5×V, monotonic ratchet, vol-derived entry stop, catalyst-gated relative
   exit) + `flex-stops/state.json` + `flex_stops` snapshot block + prompt wiring
   (spec §10) + pure-function tests. Collector-computed, analyzer acts, executor
   unchanged.
2. **Finish Phase C live verification (mostly done).** Confirmed 2026-06-25 via a
   manual collector run: `performance` + `track_record` populate, 7b stamping live
   (30d n=13). **Still unverified:** a *real flex buy* emitting the §7 enums
   (`primary_trigger`/`thesis_type`/`trigger_evidence`/`catalyst_date`) — today's MU
   buy predated the deploy and the afternoon re-run only re-affirmed MU. Also watch
   the **first 60d maturation (~late July)** for the headline hit-rate to fill in.
3. **25-ETF roster swap + KMLM ballast bucket** — analyzed & agreed in principle
   (all-weather ETF core, single names move to flex). Needs migrating the held
   single names (INTC/AMZN/GOOGL/MCK) into flex + a new convexity/ballast bucket
   for KMLM (token floor, scale up on stress). Not yet drafted.

Forecasting track added (#15–#23): #15/#16 are standalone data-integrity fixes safe to
do any session; #17/#18 follow Finding 2 + Phase 5 alongside #12; #23 gates the tuning
of everything in the track.

Intl track added (#24–#27): #25 is standalone and cheap (any session); #24/#26/#27
after Finding 2, alongside #17/#18; all describe-only, gate stays senior.

Execution-chain hardening added from the 2026-07-03 audit (#28–#31): #28 and #29
before the next unattended auto-exec run if possible; #30/#31 any session. Theme:
deterministic promises currently exceed deterministic enforcement — reference
construction is airtight, the LLM-output→broker path is trusting.

#32 (improvement ledger + /improvements tab) added — spec with #13, ship with/after
it; monthly-only by decision (2026-07-03); daily analyzer untouched.

#34 (global overnight tone, flex-facing) added — independent track, gated on FMP
tier verification for index/forex quotes; describe-only v1, gatekeeper promotion
only via #13/#23 evidence discipline.

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
- Observed + restored live on 2026-06-09, again 2026-06-15, and a **3rd time
  2026-06-29** (the 2026-06-28 infra deploy was the trigger). Still not permanently
  fixed — escalating recurrence; do Open #4 next.
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

### 10. Implement Flex Trailing Stop v1 — ❌ SUPERSEDED 2026-06-28
**Replaced by the intraday catalyst Flex engine** (`docs/specs/Flex_Catalyst_Engine_v1.0.md`,
`src/flex/`). The Flex sleeve pivoted from a conviction-hold sleeve with daily advisory
stops to a days-long *catalyst* trade with live (paper) broker orders (OTO entry + resting
GTC stop + cancel/replace trail/scale-out/time-stop). This **reverses the stateless-executor
principle for the flex path only** (a deliberate, account-holder-approved decision; Core
stays advisory/stateless). The trailing-stop spec below was never built. See the Done entry.

~~**Spec: `docs/specs/Flex_Trailing_Stop_v1.0.md`** (v1.0, decision-locked, committed
`e78e25a`). A volatility-scaled, one-directional ratcheting stop for the flex sleeve~~
+ the catalyst-gated relative exit. Locked design:
- **Volatility unit V** = P95 of |Δclose| over 60 trading days (outlier-robust — the
  earnings-gap day sits above P95, so no earnings-date exclusion needed).
- **Trail / entry stop** = `peak_close_since_entry − 1.5V`; published stop is
  **monotonic** (never decreases). Entry stop is vol-derived (`entry − 1.5V`,
  emergent at peak = entry); the fundamental kill price is a deeper max-loss cap.
  Break-even is emergent; `take_profit = null` for flex (let winners run).
- **Catalyst-gated relative exit** (core exit beyond the trail): an analyst-confirmed
  exit *candidate* when a held flex name lags SPY by ≥5pp, sustained 60d/two reports,
  AND its catalyst has passed (30d = WATCH flag). Fills the absolute trail's blind
  spot (rising-but-lagging dead money). Plus concentration trim + thesis-complete.
- **Build (v1):** collector `_build_flex_stops` + `flex-stops/state.json` cache +
  `flex_stops` snapshot block (trailing stop **and** relative-exit fields) + prompt
  wiring (spec §10) + pure-function tests (spec §14). Reuses the Phase C SPY series +
  `catalyst_date` enum. Non-fatal in the collector; executor unchanged (advisory
  daily EOD levels, no broker stops). Params in `config/flex-stops.json`.
- **Deferred to v1.1:** extension tightening, beta-adjusted relative return,
  true-range V (if OHLC confirmed).

### 11. Refresh the v1.0 design specs to match the implemented system (MEDIUM — doc debt)
The `docs/specs/*` v1.0 docs (May 2026) describe the *original* design and have drifted
from reality over ~2 months of implementation. Worst offender `Storage_Architecture.md`:
- references **E*TRADE** (CashBalance, put/call option chains) and **Polygon** (ClosePrice)
  — neither is used (E*TRADE removed `bc60604`; Polygon never integrated; prices are FMP);
- **Logic Apps / Teams / email / OneDrive** delivery — dropped for the SWA single-pane;
- a **mixed-case `Ticker`/`Action`/`Outcome`** TradeHistory schema with `Confidence` as
  HIGH/MED/LOW — the code uses lowercase `symbol`/`side`/`outcome_status`, `confidence` as
  a float, plus Phase C columns + §7 enums (now documented in CLAUDE.md);
- blob paths (`daily-approvals/`, `daily-rejections/`, `diffs/`, `daily-reports/*.json`)
  that don't match the actual (`approvals/`, `daily-executions/`, `performance/`,
  `daily-reports/_debug/`); no `performance`/`track_record`/`flex_stops` snapshot blocks.
`Analyzer_Pipeline.md` and `Data_Sources_Reference` likely carry similar drift. **CLAUDE.md
is the current source of truth** and is maintained; these companion specs need a v1.1 pass
(or a deprecation header pointing at CLAUDE.md). Pre-existing doc debt, not caused by
today's work — flagged 2026-06-25 while updating storage docs for Phase C.

### 12. `quadrant_performance` snapshot block — regime-call accountability (MEDIUM, ~Jul 10+)
Feed the quadrant-vs-SPY data (built for the web chart, PR #8) back to the **analyzer**:
a compact block with each basket's 30/60/90d return vs SPY **plus** "favored-bucket
performance while favored" (did the quadrant the system favored actually win during the
favored window?). One prompt rule: if the favored bucket has lagged SPY for N consecutive
sessions while favored, the regime read is suspect — the report must confront it and the
bar for concentrating further into it rises. Data already accrues daily in
`performance/equity-series.json` (closes + favored_bucket per point); this is a small
collector aggregation + prompt section. Motivating live example (2026-07-02): the model
is rotating into Q3/Q4 while the Q3 basket is the worst performer since inception
(−7.1%, GLD −10.5%) — currently nothing forces it to engage with that tension.
**Deliberately deferred ~1 week** (decided with the account holder 2026-07-02): let the
streaming fix + quadrant chart + Phase 4 prove out unattended first, and let
shading/history accrue. Do after Finding 2.

### 13. Monthly self-initiated strategy review + amendment channel (HIGH value, spec first)
The LLM currently calibrates *trades* (track_record) but has no channel to rethink the
*strategy* (quadrant membership, ladder shape, tunable params are fixed code/config).
Design (discussed 2026-07-02, account holder likes it): a scheduled monthly deep-dive
prompt section — structured retrospective (attribution by sleeve, favored-vs-realized
quadrant divergence via #12, confidence calibration drift, override win-rate via Phase 5)
— that may emit **`proposed_amendments[]`**: structured, falsifiable proposals against
*named tunable parameters* (mirror the override protocol: evidence, bounded magnitude,
falsifier + review date), write-once to a `StrategyAmendments` table, **approved by the
account holder in the SWA like trades**, then applied as config/PR changes (git = audit
trail). Guardrails: T1 constraints untouchable; one live amendment at a time; nothing
adopted under ~n=30; every adopted amendment gets its own outcome stamp. Includes
**quadrant-membership change proposals** (e.g. "EWZ off Q3, evidence: correlation flip")
— membership stays deterministic, the LLM gets *proposal* rights, never direct edit.
**Prereqs: Finding 2 fix → brief Phase 5 (override-outcome stamping) → #12.** Spec
before building.
**Model decision (account holder, 2026-07-02): the review runs on a stronger model than
the daily analyzer** — two-tier design: daily stays claude-sonnet-4-6 (80K ITPM fits the
~72K prompt; guarded execution doesn't need frontier reasoning), review uses the best
deployable frontier model via a new `FOUNDRY_REVIEW_MODEL` app setting (Bicep) +
`FoundryClient(model=...)` (already parametrized). Foundry facts (verified in MS Learn
2026-07-02): `claude-fable-5` IS in the Foundry catalog (Anthropic-hosted, Global
Standard, East US 2) but **PAYG default quota is 0** — needs the quota-increase form;
`claude-opus-4-8` has 40K ITPM by default → **deploy Opus 4.8 as the floor, request
Fable 5 quota in parallel, flip the setting when granted.** Review prompt must be built
compact (aggregates/trends, not the raw snapshot) to fit 40K ITPM. Caveat recorded: a
stronger proposer makes the falsifier/approval guardrails MORE important, not less.
The monthly review's output now includes the #32 Improvement Ledger entries (≤5,
evidence-triggered) — spec the two together; the ledger is the review's visible
product, the amendment channel remains its only enforcement path.
**Cadence note (2026-07-03, spec §8 event-driven exceptions):** `transition_watch`
activation and a newly-active `market_vs_macro_quadrant` divergence (#18) should be
explicit event-driven rebalance-exception triggers, so an early staged lean is never
stranded until the monthly rebalance — evidence 2026-07-03: a 30pp GLD/TLT gap executing
in tranches has no cadence guarantee if the calendar and the turn disagree.

### 14. Intra-quadrant selection freedom (MEDIUM, spec with #13)
Loosen the reference *within* a quadrant only: the deterministic layer keeps setting the
quadrant-level target (the anchor + the measuring stick stay stable), but the analyzer
may choose **which of that quadrant's concentrate names carry the weight** — floors and
single-name caps still hold, tilt logged like an override with evidence (e.g. "VDE over
PDBC: contango + refiner margins"). Uses LLM judgment at the altitude where it has daily
data without letting it redefine its own benchmark (rejected: LLM-defined basket
membership — circular anchor, breaks attribution). Small extension of the override
protocol; spec alongside #13. **Cadence + model:** lean toward setting the intra-quadrant
tilts at the #13 review cadence on the stronger model (slow-moving composition decisions
get the deeper reasoner), with daily Sonnet executing toward them — also sidesteps the
40K-ITPM ceiling that blocks frontier models from the ~72K daily prompt.
**Deterministic input identified (2026-07-03):** the #24 `regional_signals` scorecard is
the intended evidence base for intl-sleeve intra-quadrant selection
(IDMO/AIA/EWJ/IEMG/EWZ/VSS/EUAD tilts). Selection freedom without #24 is
momentum-chasing with extra steps; #24 without #14 is a scorecard nobody can act on.
Sequence them together in the monthly-review (#13) framework.

### 15. GDPNow vintage fetch goes blind at every quarter boundary ✅ DONE 2026-07-03 (PR #9)
Fixed the same week it was filed: the ALFRED vintage window now extends back one
quarter (`GDPNOW_VINTAGES_PRIOR` rides along in the snapshot) and `_build_growth_axis`
splices the prior quarter's vintage tail (`basis: "prior_quarter_tail"`, medium
confidence) when the new quarter has <3 vintages — never an empty trajectory while
FRED has vintages. Moved to Done.

### 16. Automate the policy axis — market-implied stance ✅ DONE 2026-07-03 (PR #10)
Fixed the same week it was filed: new deterministic `policy_axis` block resolves a
fresh manual SEP stance (`fomc-stance.json` `as_of` within 45d — the override layer)
else the market-implied stance from DGS2 20d momentum; the gate + conviction proxy
consume the RESOLVED stance; `unconfirmed` now requires BOTH layers unavailable.
The manual file remains the SEP override channel — **still update it after the
2026-07-28/29 FOMC.** Moved to Done.

### 17. Leading-growth composite + growth-side `transition_watch` (HIGH — the biggest forecasting gap)
The inflation axis has a leading layer (breakevens + oil → `leading_vs_lagging_inflation`
→ transition lean). The growth axis has **none** — GDPNow is a coincident nowcast and its
confirming inputs (payrolls, retail) are lagging and revision-prone, so the growth axis
flips ~1–3 months after markets reprice. Mirror the proven inflation-side pattern onto
growth — which simultaneously builds the spec §6 re-entry triggers ("the biggest gap
between strategy and automation"): the same composite turning up while realized growth
is flat *is* the staged re-entry signal.
- **New FRED series (add to `macro-series.json`):** `WEI` (Weekly Economic Index —
  weekly GDP tracker, turns weeks before GDPNow), `PERMIT` (building permits),
  `NEWORDER` (core capex orders, nondefense ex-aircraft), `NOCDFSA066MSFRBPHI` (Philly
  Fed new orders), `GACDISA066MSFRBNY` (Empire State general activity) — the regional
  Fed surveys print mid-month for the *current* month, the earliest monthly growth data
  that exists — and `NFCI` (weekly financial conditions).
- **Market-derived inputs (FMP prices already fetched):** copper/gold ratio (CPER/GLD
  proxies), cyclicals/defensives (XLY/XLP) 20/60d, plus HY OAS 20d *direction* (level
  already collected; direction currently unused).
- **Design:** diffusion score in [−1, +1] (fraction of signals improving, weighted;
  claims 4w-avg trend promoted from display-only "confirming" to an input).
  Disagreement with the realized growth axis → new divergence
  `leading_vs_lagging_growth` (thresholds in `divergence-config.json`; stale input →
  `indeterminate`, never a false active — house rule). Generalize
  `_build_transition_watch` to consume growth-side divergences symmetrically with
  inflation-side (project the quadrant on the growth axis; same de-risk/re-risk
  asymmetry and staged fractions; reuse, never re-derive). LLM adjudicates in §2 per
  the Phase-4 pattern.
- **Prereqs:** Finding 2 fixed (transition leans must be executable, not silently
  held); sequence alongside #12. **Acceptance:** unit tests for the diffusion +
  divergence + growth-side projection; on a replayed 2026-06 snapshot the composite
  produces a directional read where the binary axis said flat.

### 18. `market_implied_quadrant` block + `market_vs_macro_quadrant` divergence (HIGH)
**Live evidence (2026-07-03 report):** the book proposed selling ~$51K of equities into
a tape above its 200d SMA, on a *low-confidence* flat growth read — while
`price_vs_regime` sat `indeterminate` because it requires a concrete `active_quadrant`
and the regime was borderline. I.e. the one tape-vs-macro check goes blind exactly at
borderline regimes, when it is most needed. Also: DTWEXBGS was 7d stale, blinding the
dollar switch.
- **Design:** compute which quadrant the cross-asset tape is pricing, from data already
  collected: relative 20/60d momentum of the four equal-weight `QUADRANT_CONCENTRATE`
  baskets (plumbing exists — `performance/equity-series.json` closes +
  `_quadrant_series`), plus per-signal votes: copper/gold, XLY/XLP, DXY trend,
  breakevens direction, HY OAS direction, 2s10s re-steepening. Emit
  `market_implied_quadrant` + confidence + the per-vote table. New divergence
  `market_vs_macro_quadrant` fires on disagreement with
  `active_quadrant`/`favored_bucket` — it **works at borderline regimes by design** (the
  implied quadrant needs no macro axis), superseding `price_vs_regime`'s blind spot
  (keep the old detector; note the overlap). Describe-only; the LLM adjudicates
  (Phase-4 pattern). Thresholds in `divergence-config.json`, no magic numbers in code.
  Rationale to record: *the system cannot be later than the market if the market's own
  vote is one of its inputs* — historically when tape and realized macro disagree at
  turns, the tape is early more often than wrong (2022 the canonical case).
- **Sub-item — dollar staleness:** when DTWEXBGS is >5d stale, derive a daily dollar
  proxy from the already-collected daily FX pairs (DEXUSEU/DEXJPUS/DEXCHUS,
  trade-weight-ish fixed blend) or UUP via FMP, so the switch and `dollar_vs_intl_tilt`
  never run blind.
- **Prereqs:** after Finding 2; natural companion to #12 (same basket data).
  **Acceptance:** on the 2026-07-03 snapshot the block emits a concrete implied
  quadrant with votes, and the new divergence fires `active` (tape risk-on vs macro
  defensive) rather than `indeterminate`.

### 19. Inflation-quality FRED adds — sticky/flexible CPI, trimmed-mean PCE, expectations (MEDIUM — trivial)
Four lines in `macro-series.json` + small axis-payload additions:
`CORESTICKM159SFRBATL` (sticky core CPI — persistence), `FLEXCPIM159SFRBATL` (flexible
CPI — turns first; a natural extra leading confirmation for the re-risk bar in
`transition_watch`), `PCETRIM12M159SFRBDAL` (Dallas trimmed-mean PCE — cleaner
underlying trend than core), `MICH` (1y household expectations). Wire as secondary
confirmations into `_build_inflation_axis` diagnostics and as an optional third
confirmation signal in the leading-inflation divergence.
- **Prereqs:** none. **Acceptance:** series in snapshot; flexible-CPI direction
  surfaced in the divergence basis.

### 20. Poor-man's economic surprise index from the FMP economic calendar (MEDIUM)
Both axes measure rate-of-change of *data*; markets reprice on data vs *consensus*. A
surprise measure is the closest direct read on "what isn't priced yet."
- **Design:** FMP's economic-calendar endpoint carries consensus estimate + actual
  (**verify the current FMP tier exposes it within the 250 req/day budget — if not,
  park this item with that note**). Compute rolling 30/60d surprise diffusions split
  growth-series vs inflation-series; emit a `surprise_index` block. Consumers: extra
  confirmation input to both `transition_watch` sides; a `data_vs_expectations` context
  line in §2; input to the #13 monthly review.
- **Prereqs:** #17 (so it has a growth-side consumer). **Acceptance:** block populates
  with ≥10 releases scored; graceful `indeterminate` when the endpoint is unavailable.

### 21. Shelter lead for the inflation axis (MEDIUM)
Shelter is ~35% of CPI and lags new-lease reality by 9–12 months — the best-documented
single inflation lead available. It called both the 2021 upturn and 2023 downturn
quarters early.
- **Design:** small fetcher for the BLS New Tenant Rent Index (quarterly) and/or the
  Apartment List national index / Zillow ZORI (free CSV downloads; new client under
  `shared/clients/`, respect the no-secrets rule — these are unauthenticated). Emit
  `shelter_lead` (new-lease YoY vs CPI shelter YoY + implied direction) and add it as a
  third basis signal to `leading_vs_lagging_inflation`. Non-FRED sources are
  lower-reliability — staleness handling mandatory (>45d → indeterminate).
- **Prereqs:** none hard; natural after #19. **Acceptance:** signal present with an
  as-of date; divergence basis includes it when fresh.

### 22. Probabilistic quadrant vector (MEDIUM-HIGH — after #17/#18)
Binary `rising/falling/flat` axes freeze at the borderline exactly when the transition
is happening, then snap. Continuous scores let positioning scale with confidence —
mechanically how a system gets *ahead* of a hard flip. The convex-blend machinery
(borderline intersection, transition leans) already exists; it lacks a continuous
driver.
- **Design:** each axis emits a score in [−1, +1] (from the #17 growth composite / the
  realized+leading inflation stack), combined into P(Q1..Q4). `_build_reference_weights`
  blends across quadrant targets proportional to P (borderline handling becomes the
  natural special case, not a separate code path); `transition_watch` staged fractions
  become functions of the P-shift, still capped by the existing config maxima and the
  de-risk/re-risk asymmetry. Binding `active_quadrant`/`regime_gate` stay binary and
  untouched (echo contract unchanged) — P drives only the reference blend. Explicitly a
  *weighted composite*, not an HMM — auditability over sophistication, per the
  deterministic-echo doctrine.
- **Prereqs:** #17 + #18 shipped; tune only under #23. **Acceptance:** reference
  weights vary smoothly with P in tests; today's binary outputs reproduce as the
  degenerate case (P concentrated on one quadrant).

### 23. ALFRED point-in-time backtest harness + signal-admission rule (HIGH value — gates the whole track)
The system cannot improve classifier lag it cannot measure. #12 measures forward from
inception only; without point-in-time reconstruction every proposed signal is vibes —
and revised data makes naive backtests lie (payrolls revisions especially).
- **Design:** offline script(s) in `scripts/` (NOT the collector) using FRED's ALFRED
  realtime parameters (`fred.get_series_vintages` already exists) to reconstruct, for
  each historical date, what `growth_axis` / `inflation_axis` / `active_quadrant`
  *would have said with only the data known that day*. Score median flip lag (days) vs
  the known regime turns: 2007–08, Feb–Mar 2020, the 2020–21 reflation, the 2022
  stagflation flip, the 2023 disinflation. **Pre-registered admission rule: no new
  signal enters a composite unless it demonstrably reduces median point-in-time flip
  lag without materially increasing false flips.** Output feeds the #13 monthly review
  as its yardstick; also produces the calibration data #22 needs. Market-derived inputs
  (#18) need point-in-time prices — FMP historical EOD suffices (prices aren't
  revised).
- **Prereqs:** none to build the harness; it becomes the gate for tuning #17/#18/#22
  and for #13 amendment proposals touching classifier params. **Acceptance:** harness
  reproduces the current axes on recent live dates (parity check) and emits a lag table
  for ≥3 historical turns.

### 24. `regional_signals` per-region scorecard (HIGH — intl track parent)
The system has one global quadrant and one DXY switch; it has **no per-region read**,
so "which regions get the intl allocation" is decided by relative momentum alone — a
confirming signal, not a leading one. Every sustained intl regime (1971–78, 1985–88,
2002–07, 2017, 2020H2–21, 2025) rode the dollar cycle *plus* regional fundamentals the
system doesn't collect. **Live evidence (2026-07-03 report):** intl sleeve at floor on
no-read while AIA sits +11.45pp excess vs SPY 60d; EWJ carries the Rengo 5.01% wage
confirmation (BoJ-normalization / yen-appreciation catalyst) while JPY sits 161.67 and
DTWEXBGS is 7d stale — the system cannot see that the equity story and the FX story
point opposite ways.
- **Design:** copy the bond_signals/labor_signals pattern — a deterministic scorecard
  per region (Europe, Japan, EM-Asia, LatAm), each emitting favor/neutral/avoid +
  confidence + a per-component table, **describe-only** (the LLM adjudicates; the
  deterministic layer never trades on it directly). Components:
  - **Currency trend vs USD** 20/60d: existing pairs + add `DEXUSAL` (AUD —
    China/commodity canary), `DEXBZUS` (BRL — EWZ), `DEXKOUS` (KRW —
    global-trade/semis canary) to `macro-series.json`.
  - **Rate differential vs US:** DGS10 minus `IRLTLT01DEM156N` (Germany 10y, monthly —
    new) / `IRLTLT01JPM156N` (Japan 10y, already collected). Monthly cadence is
    acceptable — the daily speed lives in the FX legs. Fixes the one-sided
    policy-divergence read (`regional_rotation.policy.us_2y_60d_bp_change` alone
    penalizes intl even when foreign yields rise faster).
  - **Equity relative strength vs SPY** 20/60d — already computed in
    `regional_rotation`; reuse, don't re-derive.
  - **Region-specific anchor:** ECB path (`ECBDFR`, collected) for Europe; wage/JGB
    normalization for Japan; the #27 China proxy for EM-Asia; the commodity complex
    for LatAm.
- **Hierarchy (record verbatim):** global quadrant stays senior (intl outperformance
  is a risk-on phenomenon; EM correlation to US spikes toward 1 in crises — regional
  signals NEVER override the regime gate or the floor posture). DXY switch stays the
  US-vs-intl sizing hinge (spec §4). `regional_signals` decides only the WHICH-region
  tilt inside the intl sleeve — i.e., it is the deterministic input for #14's
  intra-quadrant selection freedom. Anti-chase rule from §4 applies: scorecard favor
  without DXY-trend confirmation sizes nothing.
- **Prereqs:** after Finding 2; pairs naturally with #17/#18 (inherits its value from
  the regime layer being timely). #18's daily dollar proxy is a soft prereq (DTWEXBGS
  staleness otherwise blinds the hinge). **Acceptance:** scorecards in snapshot with
  per-component bases; stale component → indeterminate, never a false favor (house
  rule); unit tests per component.

### 25. Currency decomposition via hedged/unhedged ETF ratios (MEDIUM — cheap, do early)
**Live evidence (2026-07-03):** EWJ's equity thesis (Rengo 5.01%) and its FX exposure
(JPY 161.67) point opposite directions; the system sees only the blended USD return.
2025's intl win was ~half currency (spec §4) — local-vs-FX attribution is
decision-grade information the book already pays for but doesn't extract.
- **Design:** FMP prices only, no new sources: the HEWJ/EWJ ratio isolates the yen
  effect (same index, hedged vs unhedged; DXJ works but adds an export tilt — prefer
  HEWJ), HEZU/EZU for the euro. Emit per-region
  `{local_return_60d, fx_return_60d, blend}` inside `regional_rotation`. Consumer rule
  for the LLM: scorecard favor + FX headwind → the HEDGED variant is the legitimate
  flex-watchlist candidate — "Japan working, yen killing you" and "Japan failing"
  become distinguishable states.
- **Prereqs:** none — independent of #24, can ship any session. **Acceptance:** ratios
  in snapshot with 20/60d trends; report template line added.

### 26. Earnings-revision breadth per region (MEDIUM — monthly cadence)
Relative earnings revisions are the #2 predictor of sustained regional outperformance
(the 2025 European defense/fiscal run is the live case) and a total blind spot today.
- **Design:** monthly job (budget: spread FMP calls across days within the 250/day
  cap): for each regional ETF (IDMO, AIA, EWJ, IEMG, EWZ, VSS) pull the top-10
  holdings look-through (endpoint already used for concentration) + analyst-estimate
  direction per holding vs a SPY top-10 baseline; emit a revision-breadth score per
  region into `regional_signals`. Freshness ≤35d, else indeterminate.
- **Prereqs:** #24 exists (this is a component of it). **Acceptance:** breadth scores
  with as-of dates; budget accounting note in the collector logs.

### 27. China proxy basket (MEDIUM — EM-Asia anchor)
China credit impulse leads EM/commodities ~9–12m but isn't freely available;
`CHPMINDXM` is deprecated on FRED. The EM-Asia row of #24 needs a China vote.
- **Design:** market-derived deterministic proxy, daily, free: copper trend (CPER, or
  `PCOPPUSDM` monthly fallback), AUD trend (`DEXUSAL`, from #24), KWEB-or-FXI relative
  strength vs SPY (FMP). Equal-weight diffusion → `china_proxy` ∈
  {tailwind, neutral, headwind}. The block note must state plainly it is a **proxy
  basket, not credit-impulse data**.
- **Prereqs:** folds into #24. **Acceptance:** proxy emitted with per-leg basis; any
  leg stale → drop the leg, note it, degrade confidence.

### 28. Trade-level Tier-1 validator — make "enforced downstream" true ✅ DONE 2026-07-04 (PR #12)
Fixed the day after the audit filed it: new pure `shared/trade_validation.py::
validate_trades` runs after the Finding-2 reconcile merge — V1 gate/roster, V2
exemption, V3 window rule (`reference ± max(residual, gap_band_pp)`, floor-protected),
V4 held/cash/integer clamps, aggregate ceiling belt. Fail-closed: a validator crash
flags the file and the auto-executor refuses it. Details in Done.

### 29. Harden the auto-exec chain: retries + ET-date fix ✅ DONE 2026-07-04 (PR #13)
Fixed the day after the audit filed it: new `shared/timeutil.py::today_et/now_et`
(zoneinfo, `tzdata` pinned), `auto_executor_retry` timer at 10:05 + 11:05 ET sharing
`executor.run_auto_execute` with the primary 09:35 shot, escalation (no_trades
WARNING at 10:05 → ERROR at ≥11:00 ET; refused_validation ERROR on any retry), and
cache-asymmetry comments guarding the idempotency mechanism. Also closed #31(i).
Details in Done.

### 30. Analyzer blob-trigger backfill guard (MEDIUM — history integrity)
**Evidence (audit):** the analyzer blob trigger fires for **any** blob landing in
`daily-snapshots/` — a seeder backfill or manual re-upload of an old snapshot re-runs
the analyzer for that date, burning tokens and **overwriting the historical report +
trades file with regenerated content**, corrupting the track-record data #12 depends
on. Execution is protected (date-scoped executor, `no_match` approvals guard); history
is not.
- **Design:** analyzer skips (log + return) when `daily-trades/{date}.json` already
  exists, unless env `ANALYZER_ALLOW_REGENERATE=true`. Optional: also skip when blob
  date ≠ today unless the flag is set (explicit backfill intent).
- **Prereqs:** none. **Acceptance:** unit test: existing trades file ⇒ skip; flag set ⇒
  regenerate; fresh date ⇒ normal run.

### 31. Config/comment hygiene from the audit (LOW — batch with any session)
Three one-liners — **only (ii) remains**: (i) ✅ CLOSED 2026-07-04 (PR #13, with #29):
`function_app.py` cron comments now cite `TZ=America/New_York` and explicitly warn
that `WEBSITE_TIME_ZONE` is Windows-only / silently ignored on Linux (the pre-6f42f1a
4.5h-early bug). (ii) `staleness_days: 7` exists only as a code fallback — promote to
`divergence-config.json` per the no-magic-numbers rule. (iii) ✅ CLOSED 2026-07-04:
`gap_band_pp` is consumed by both Finding 2's `reconcile` (merged PR #11) and the #28
Tier-1 validator's window rule (PR #12) — verified.

### 32. Improvement Ledger — monthly self-improvement proposals + `/improvements` tab (MEDIUM-HIGH — spec WITH #13, ship with/after it)
**Decided with the account holder 2026-07-03.** The system learns through three loops —
daily outcome stamping (Phase C/5), regime-call accountability (#12), and the monthly
amendment channel (#13) — but none of it is *observable as a pipeline*: there is no
place where the system's own evidence-backed hypotheses about improving its forecasting
are recorded, adjudicated, and traced to shipped changes. A free-form daily "ideas feed"
was explicitly **REJECTED** (unfalsifiable noise, mild self-prompting risk, competes
with the amendment channel). Adopted design: a structured ledger produced **only** by
the monthly #13 review — Loop 3 made visible.
- **Cadence & generation:** entries are emitted exclusively by the #13 monthly review
  run (frontier model per the `747c0c3` two-tier decision). Cap ≤5 new entries per
  review. Every entry must be **evidence-triggered from the accumulated record** — an
  override falsified, a divergence that resolved against the classifier, a quadrant
  call graded late/wrong by #12, a #23 lag measurement, a data-integrity incident. No
  trigger, no entry. **The daily analyzer emits NOTHING to this ledger.**
- **Schema (IMPROVEMENT_SCHEMA_V1, sibling of the OVERRIDE_SCHEMA record discipline):**
  per entry: `observation` (dated, from the system's own record), `hypothesis` (what
  change improves forecasting), `proposed_instrument` (concrete signal/config/rule),
  `expected_effect` (measurable: lag days, hit rate, false-flip cost),
  `falsifier_or_test` (how the #23 harness or a forward window would kill it),
  `status: proposed | reviewed | promoted | adopted | rejected`, plus links (FOLLOWUPS
  item / commit when promoted or adopted).
- **Governance (the critical constraint, record verbatim):** ledger entries NEVER
  change behavior directly. Promotion path: entry → #13 amendment proposal → account
  holder approval → FOLLOWUPS item → implementation session → status `adopted` with
  commit link. The ledger is a proposal surface for the EXISTING amendment channel,
  not a second channel. Rejected entries stay visible with the rejection reason — the
  negative record is part of the learning.
- **Storage & UI:** `improvements/ledger.json` (or per-review files) in blob alongside
  reports; new `/improvements` tab on the SWA next to `/today` and `/performance`:
  pipeline view (proposed → promoted → adopted), a "graduated" section linking adopted
  entries to their FOLLOWUPS item + commit, rejected entries greyed with reasons. New
  read endpoint in `web/api` (Open #2 secrets-wipe hazard applies to any SWA work;
  pairing with #4 preferred). UI is read-only v1 — adjudication happens in the #13
  review + git, not in the browser.
- **Prereqs:** #13 spec'd first (this item is spec'd WITH it — same session);
  meaningful only once Phase 5 + #12 have accrued data; #23 is the preferred test
  instrument for entries touching classifier signals. **Explicit non-goals:** daily
  emission; auto-adoption; any write path from the LLM to config.
- **Acceptance:** schema doc + validator tests; the first #13 review produces a ledger
  with ≥1 evidence-triggered entry or an explicit "no qualifying evidence this cycle"
  record; `/improvements` renders the pipeline; one entry demonstrably traced
  observation → promotion → FOLLOWUPS → commit within two review cycles.

### 33. INCIDENT 2026-07-06/07: validator rejected every sell ("not held") — ✅ RESOLVED 2026-07-07 (PR #15), residuals open
**What happened:** from the #28 validator's first weekday run (07-06) every sell was
V4-rejected "not held — nothing to sell" (07-06: 1 passed/6 rejected; 07-07: 0/10),
which starved every buy of proceeds → clamped to zero. Net effect: the trade pipeline
was frozen for two sessions (only a $1.5K GLD buy that fit literal cash escaped) and
the "band_enforcement trade rejected — reconcile bug" ERROR fired (same root cause,
not a reconcile bug).
**Root cause:** `analyzer._build_reference_gaps` read `pos["quantity"]` but the
collector writes `paper_account.positions[]` with Alpaca-native **`qty`** → `held_qty`
was 0 for every position. `ticker`/`market_value` match both shapes, so `current_pct`
stayed correct — only held quantities broke. The pure-module tests built `gaps` rows by
hand (`held_qty` pre-populated) and the plumbing fixture omitted quantity fields
entirely, so the seam was never exercised.
**Fix (PR #15):** read `qty` first, `quantity` fallback (mirrors
`trade_validation._norm`); fixtures now carry collector field names; new end-to-end
seam regression (`_build_reference_gaps` → `validate_trades`). Same-day recovery:
backfill re-ran 07-07 (11 passed/0 rejected — model tranche + 3 band-enforcement
trades, $19.7K enforced notional), auto-exec submitted, all 11 filled at Alpaca.
**Residuals from the 07-07 audit (open):**
- (i) **Prompt can't see the operative config values** — `project-instructions.md`
  names `tranche_pp_max`/`gap_band_pp`/`max_magnitude_pp` symbolically but neither the
  snapshot nor `_build_user_message` carries the numbers; the model guessed "~2-3pp"
  tranches (vs the configured 10) so D3 synthesis fires every session. Fix: append an
  "operative risk-limit config" block (from `_load_reference_execution_cfg()`) to the
  user message. HIGH.
- (ii) **Report publishes arithmetic scratchwork** (07-07: "wait, let me recompute
  carefully", three versions of Table A). Add an output-hygiene rule: compute silently,
  emit each table once, final numbers only. MEDIUM.
- (iii) **Model/validator price-basis mismatch** — prompt says use
  `paper_account.current_price` on >1% divergence; `_build_reference_gaps._price()`
  prefers `prices.X.c` (FMP EOD). Up to ~5% sizing drift (MU 07-07); boundary clamps
  can differ by shares. Align both on one basis (prefer Alpaca current_price, EOD
  fallback). MEDIUM.
- (iv) **MU flex position is unmanaged** — `FLEX_ENABLED='false'` in IaC, no
  flex-state blob ≥8 days, MU -25% with record insider selling flagged 07-07. Needs an
  account-holder decision: manual exit vs dry-run then enable the engine. MEDIUM.
- (v) **`fomc-stance.json` never populated** (`as_of: null`) — market-implied governs
  (by design) but the daily data-trust flag won't clear until the manual layer is
  filled after a SEP. LOW.

### 34. `global_overnight` tone block — pre-open tactical signal (MEDIUM — flex-facing)
**Motivation (account holder, 2026-07-04):** the collector's 09:00 ET run is the ideal
capture point for the overnight global session — Asia closed (final), Europe five
hours into its day, US pre-market pricing the sum — and none of it currently reaches
the analyzer or the flex layer. Honest scope: overnight signals mostly price the OPEN,
not the day (overnight/intraday correlation is weak); the value is (a) gap-risk
context for flex entries/stops, (b) TAIL detection — carry-unwind mornings à la
Aug-2024 (Nikkei −12% + violent JPY strengthening) where the correct flex action is
abstention, (c) the KOSPI/Nikkei semis-and-global-tech read-through for XSD/INTC-type
flex names. A risk-tone instrument, not an alpha predictor — record this framing.
- **Cadence dedupe (record verbatim):** #18 = daily cross-asset quadrant vote (EOD
  trends); #24 = strategic regional scorecards (20/60d); #34 = tactical overnight
  tone (hours, pre-open). Three cadences, three consumers; #34 never feeds the
  quadrant axes or the regional tilt — it feeds the flex layer and §2 market context
  only.
- **Inputs (verify availability BEFORE implementation; degrade gracefully):**
  US pre-market: SPY + QQQ pre-market last vs prior close via the Alpaca data API
  (IEX feed, 4:00 AM+ coverage) — preferred over futures (no CME data needed).
  Asia close: FMP `^N225` + `^KS11` quotes (VERIFY tier exposes index quotes; FRED
  `NIKKEI225` as Nikkei fallback, KOSPI dropped if unavailable — note it).
  Europe mid-session: FMP `^GDAXI` + `^STOXX50E` (same verification).
  Carry stress: USDJPY overnight %Δ via FMP forex quote (FRED `DEXJPUS` is next-day
  lagged — unusable pre-open). 10y Bund: EXCLUDED from v1 — no free real-time
  source (FRED's German 10y is monthly); revisit only with a data-tier change.
- **Block design (describe-only, bond_signals pattern):** per-input `{value,
  pct_change_overnight, as_of, stale}` + two derived reads, thresholds in config:
  `overnight_risk_tone: risk_on|neutral|risk_off` (weighted diffusion of the five
  inputs) and `carry_stress: true|false` (USDJPY %Δ beyond threshold AND Nikkei
  beyond threshold, same sign — the Aug-2024 signature). Any stale/missing input →
  drop it, degrade confidence, never a false tone. LLM consumers: a §2 context line
  + the flex-watchlist adjudication section (explicit rule: `carry_stress` or
  strong `risk_off` argues for flex ABSTENTION that morning, not for shorts).
  Optional later sub-item: feed `carry_stress` to the deterministic flex gatekeeper
  as a hard input (pair with #9's data tier) — NOT in v1; promotion requires
  evidence per the #13/#23 admission discipline.
- **Prereqs:** none hard (independent of the #17/#18 and #24 tracks); FMP tier
  verification is the gating unknown — if index/forex quotes are unavailable on the
  current tier, park the item with that note rather than building on proxies of
  proxies. **Acceptance:** block present in the 09:00 snapshot with all as-of
  timestamps between 04:00–09:00 ET same day; tone/carry unit tests on fixtures;
  prompt section added; a deliberately-degraded fixture (two inputs missing) yields
  reduced confidence, never a fabricated tone.

### 35. Fresher commodity quote for `market_shock` corroboration (LOW–MEDIUM)
The collector's oil inputs come from FRED (`DCOILWTICO` / `DCOILBRENTEU`), which lag
1–2 business days, so on a spike day the freshest WTI print predates the event and
cannot corroborate it. This is what forced the 2026-07-09 freshness-discipline rule
(the report cited WTI $69.60 as-of 07-06 as evidence the 07-08 spike "reversed").
**Do:** source an **intraday / EOD-today** WTI (and Brent) quote via **FMP** (verify
the tier exposes a commodity/futures quote — e.g. `CL=F` / a WTI symbol) and feed it
into the `market_shock` energy read as same-day corroboration, keeping the FRED series
for history. Degrade gracefully (FMP miss → fall back to FRED + the freshness label).
**Acceptance:** snapshot carries a same-day WTI `as_of` on trading days; the analyzer
can confirm an oil spike with a print dated on/after the event; unit test on a fixture
where FMP is present vs absent. Independent track; FMP-tier verification is the gating
unknown (park with a note if commodity quotes aren't on the current tier).

---

## Done
- **2026-07-05** (PR #14, branch `feat/phase5-override-outcomes`) — **Brief Phase 5:
  override-outcome stamping (reference-path counterfactual) — the responsiveness
  brief is COMPLETE.** Overrides were falsifiable bet slips nobody ever collected
  on: the `outcome_status`/`resolved_correct` hooks sat empty since Phase 4d.
  **Locked decision (account holder, 2026-07-04; this session's prompt is the
  decision record — memory design docs not on this box): overrides are graded
  against the REFERENCE PATH — "did disagreeing beat obeying" — not vs SPY.**
  Built, mirroring the Phase-C stamper conventions exactly:
  **(1) `_stamp_override_outcomes(fmp)`** (daily, non-fatal, after
  `_stamp_trade_outcomes`): queries `falsifier_date le today` + unstamped (synthetic
  enforcement rows without a falsifier_date are auto-excluded — the OData property
  is absent; those bets are graded via their `band_enforcement` trades in
  TradeHistory). Counterfactual per row over [filed=`recommended_at`,
  `falsifier_date`]: `ret_sleeve` vs `ret_reference = Σ target_weights_pct[i]/100 ×
  ret_i` using the FILED-DATE vector reconstructed from
  `daily-snapshots/{filed}.json` (no schema change, works retroactively); the
  vector is SGOV-denominated cash (verified: `weights["SGOV"]` is in
  `target_weights_pct`, `__cash__` is popped to `literal_cash_target_pct`) so SGOV
  earns its real return and literal cash implicitly earns 0.0.
  `excess_pp = sign × (ret_sleeve − ret_reference)` where sign = +1 held-MORE /
  −1 held-LESS, derived deterministically from direction × block membership
  (re_risk+amplifier or de_risk+damper ⇒ MORE; the other two cells ⇒ LESS).
  `resolved_correct = excess_pp > 0`; `indeterminate_data` on any missing material
  input (no filed-date snapshot, unpriced sleeve, any ≥1% reference component
  unpriced, <90% of vector weight priced) — never guess; sub-1% floor sleeves are
  de minimis. Prices from `performance/equity-series.json` closes (last close ≤
  boundary — falsifier dates land on weekends), FMP fallback one call per unique
  missing symbol. Stamps `ret_sleeve_pct`/`ret_reference_pct`/`excess_pp`/
  `resolved_correct`/`outcome_status`/`resolved_at`. Free-text falsifier
  interpretation EXPLICITLY out of scope — mechanical grading only; falsifier
  QUALITY is the #13 monthly review's job.
  **(2) `override_record` snapshot block** (pure `_aggregate_override_record`,
  sibling of track_record: capture-fine/report-coarse, same n≥10 promotion —
  `by_premise` promotes at `_TRIGGER_PROMOTION_MIN`): `overall` win rate + avg
  `excess_pp`, `by_direction` (the §6 asymmetry doctrine predicts de_risk ≠
  re_risk), `by_status` (accepted/downsized/rejected), with **`enforced: true`
  rows aggregated SEPARATELY** (they grade the enforcement system, not the model's
  judgment — blending poisons both lessons), `sample_size` + `caveat`
  (price-return-only v1, small-n).
  **(3) Prompt**: "Track record — calibrate against your own results" extended with
  the override record under the same guardrails — a CALIBRATION signal
  (humbler/bolder about deviating), never a per-sleeve veto, never a reason to stop
  filing honest overrides (an unfiled silent hold is enforced anyway per Finding 2
  and learns nothing); inputs list gains `override_record`. 14 new tests (sign
  convention all four cells, counterfactual hand-math incl. SGOV cash pricing,
  every indeterminate guard, aggregator splits + enforced separation + premise
  promotion); **suite 290 green, ruff clean.** Closes the judgment loop the way
  Phase C closed the trade loop — **responsiveness brief Phases 1–5 all shipped;
  #12 → #13/#14 unblocked.** First real stamps land when the earliest
  `falsifier_date` records mature (~mid-July).
- **2026-07-04** (PR #13, branch `feat/auto-exec-retries`) — **#29 auto-exec chain
  hardened: retry timers + ET-date fix.** The gap: collector 09:00 → blob-trigger
  analyzer (variable LLM latency; the 07-02 outage produced >4-min generations) →
  auto-exec at a FIXED 09:35 reading today's file — analyzer >35 min or failed ⇒
  `no_trades`, no retry, the day silently never executes; `deferred_market_closed`
  deferred to NOTHING (no re-invocation existed — retries give it meaning); and
  "today" was computed in UTC, which coincides with ET at 09:35 but rolls the date
  for any evening/retry fire. Built: **(1)** `shared/timeutil.py::today_et/now_et`
  (`zoneinfo("America/New_York")`; `tzdata` pinned in requirements — needed on
  Windows dev boxes, harmless on Linux); the UTC-date grep found and fixed the two
  real date-for-blob-path computations (`function_app.auto_executor`,
  `seeder._load_holdings` snapshot mode); collector `date.today()` calls are
  ET-correct via the `TZ` app setting (documented contract) and UTC timestamps
  (`generated_at`/`executed_at`/`submitted_at`) are correct as-is. **(2)** New
  `auto_executor_retry` timer, NCRONTAB `0 5 10,11 * * 1-5` (10:05 + 11:05 ET),
  same gating; both timers are thin wrappers over the new
  `executor.run_auto_execute(label, now)` (in executor/handler.py rather than
  function_app.py so the logic is unit-testable without azure.functions).
  **(3)** Escalation in the retry fires: `no_trades` at ≥11:00 ET → ERROR
  ("analyzer never produced daily-trades/{date}.json — day will not auto-execute",
  App Insights alertable), 10:05 → WARNING; `refused_validation` → ERROR at any
  retry hour (file exists but quarantined — different post-mortem). **(4)** No
  status/caching behavior change. **Two discoveries recorded:** (i) the CACHE
  ASYMMETRY is the idempotency mechanism — `write_executions` fires ONLY on
  `ok`/`all_filtered` (terminal), while `no_trades`/`refused_validation`/
  `no_approvals`/`no_match`/`deferred_market_closed` return UNCACHED, so a retry on
  a cached day is one blob read + exit and on an uncached day is a genuine
  re-attempt (comments now guard both call sites against a future session
  "helpfully" caching the failure paths); (ii) the date+trade-id-scoped
  `client_order_id` (verified: `f"{date_str}-{trade_id}"[:48]`) is the double-submit
  backstop — a crash mid-submission cannot double-fill on retry (Alpaca rejects
  duplicates). Also closed **#31(i)** (cron comments now cite `TZ=America/New_York`
  + warn WEBSITE_TIME_ZONE is Windows-only). 12 new tests (evening-clock ET date,
  cached-retry-touches-nothing proof, no_trades re-attempt, 10:05/11:05 escalation
  boundary, primary-fire-no-escalation, quarantine ERROR, evening retry reads
  today's file); **suite 288 green, ruff clean.** Live verification Mon 2026-07-06:
  09:35 executes; 10:05/11:05 fire, hit the cached result, exit in one read (App
  Insights traces).
- **2026-07-04** (PR #12, branch `feat/trade-validator`) — **#28 Tier-1 trade validator:
  "enforced downstream" is now literal.** The gap: the prompt promised Tier-1 bounds
  "enforced downstream", but nothing downstream checked the TRADES — Finding 2's
  `reconcile` polices what the model FAILED to do (silent-hold shortfalls); a
  hallucinated gate-closed "BUY 500 QQQ" or a SELL through the AMZN exemption or the
  0.1% floor flowed from LLM JSON to Alpaca untouched. New pure
  `shared/trade_validation.py::validate_trades(gaps, trades, override_decisions, cfg,
  quadrant_ctx)` (same gap rows/config/decisions as `reconcile`; fields normalized
  exactly as the executor normalizes them; sells-first sorted so proceeds fund buys):
  **V1** gate rule — gate not `open` ⇒ reject amplifier buys (Damper/SGOV pass); plus
  any off-CORE_ROSTER buy rejected regardless of gate (trades[] is core-only; flex
  goes through nominations). **V2** exemption — EXEMPT_HOLDS sells rejected outright
  (per risk-limits semantics + Phase B null core stops, no legitimate exit path
  exists). **V3 window rule (the core; D1's mirror image)** — post-trade weight must
  land in `[max(ref − W, sleeve_floor), ref + W]`, `W = max(allowed_residual,
  gap_band_pp)` from the SAME shared `allowed_residuals` helper reconcile uses (new,
  refactored out — the two layers cannot disagree); deviation-reducing trades always
  pass (tranche-paced partial trims stay first-class), overshoots CLAMP to the window
  edge (float-epsilon so rounding never costs a share), already-outside-moving-further
  ⇒ reject; the explicit floor bound covers ref−W dipping below 0.1% and integer
  shares leave ≥1 share on clamped core sells. **V4** — sell ≤ held, buy ≤
  cash-after-sells (both clamp), fractional qty floored, clamped remainders under
  `min_notional_usd` rejected. **Aggregate belt:** post-all-trades amplifier share of
  core > max(ceiling, PRE-trade share) ⇒ ERROR log + marginal amplifier buys stripped
  (pre-trade threshold so an already-concentrated book — or a partial fixture
  universe — is logged, never punished for state the trades didn't cause). Every
  surviving trade stamped `validation: {status: passed|clamped, reasons}`; rejected
  trades move to `trade_validation.rejected` in the daily-trades JSON + a report
  addendum (OverrideHistory rows deliberately NOT written — the JSON + addendum carry
  the record; that table stays override-semantics-only). **Fail-closed wiring
  (deliberate contrast to reconcile's non-fatal wrapper):** a validator crash still
  writes report+trades but sets `validation_error: true`; the executor's AUTO path
  (`_validation_refusal`, pure) refuses a file with that flag, with any
  rejected-stamped trade in trades[] (any date — its presence means tampering/bug),
  or with unstamped trades dated ≥ 2026-07-05; manual approval path unaffected.
  `_build_reference_gaps` rows gained `held_qty`; cfg loader gained the floor/ceiling
  scalars; prompt step 7 now states enforcement is literal. Also closes **#31(iii)**
  (`gap_band_pp` consumed by both layers). 25 new tests incl. the malicious-file
  replay (gate-closed QQQ buy / exempt AMZN sell / off-roster MEME buy stripped,
  floor-breach SPY sell clamped to leave 1 share — zero submittable violations) and
  the band_enforcement pass-through (reconcile's synthesized trades validate
  untouched); **suite 276 green, ruff clean.** Live verification: Mon 2026-07-06
  trades file carries validation stamps, expected zero rejections.
- **2026-07-03** (PR #11, branch `feat/finding2-band-enforcement`) — **Finding 2 FIXED:
  the silent-hold gap is closed (OVERRIDE_SCHEMA_V1_1 + deterministic band
  enforcement).** The gap: a hold of an out-of-band sleeve required an override; an
  override >15pp was structurally rejected; a rejected override authorized nothing —
  but nothing then FORCED a trade, so for any gap >15pp the protocol was unenforceable
  (2026-06-30: correct defensive call, zero trades, "appropriately positioned";
  2026-07-02/03: 30pp GLD/TLT gaps traded only because the model chose to). Three
  locked decisions (the session prompt is the decision record — the memory design docs
  are not on this box):
  **D1 — overrides cap the RESIDUAL, not the move:** per out-of-band sleeve,
  `required_move_total = max(0, gap − max(allowed_residual, gap_band_pp))` where the
  residual comes only from an ACCEPTED/DOWNSIZED override for THAT sleeve (never
  >15pp; rejected/absent ⇒ 0). Overrides became per-sleeve: mandatory `sleeve` field,
  sentinel bumped to `OVERRIDE_SCHEMA_V1_1` (prompt + `assert_override_prompt_schema`
  + validator in lockstep — a sleeve-less record is rejected).
  **D2 — tranche formalization:** `required_move_today = min(required_move_total,
  tranche_pp_max=10)`; a trade at ≥ tranche pace is CONFIRMING, first-class — this
  makes the 2026-07-03 partial rotation legitimate by rule (replay test pins it:
  zero synthesis).
  **D3 — de-risk-only enforcement (option b + spec §6 asymmetry):** new PURE
  `shared/reference_execution.py::reconcile` runs in the analyzer after
  `validate_overrides`; where trades fall short of the tranche AND the corrective move
  is de-risk (sell overweight Amplifier / buy underweight Damper-or-SGOV, classified
  off `quadrants.py`), the shortfall is synthesized as a `source:"band_enforcement"`
  trade appended to `trades[]` (executor untouched — it already reads the list; the
  tag flows to daily-trades JSON + TradeHistory). Re-risk shortfalls are NEVER
  synthesized, only `non_compliant_flagged` — quick to cut risk deterministically,
  deliberate to add it. Synthesized trades respect integer shares, $115 min-notional,
  sells-before-buys (sell proceeds fund the buys), cash-after-sells, the deployment
  gate, EXEMPT_HOLDS (never force-sold), and a 20%-of-equity per-session enforcement
  turnover cap. Config `risk-limits.json → reference_execution` (+ D1 semantics noted
  in `_override_protocol_note`). OverrideHistory rows now carry `sleeve` +
  `enforced: true` (rejected record enforced-against, or a synthetic `outcome:
  "enforced"` row when no record existed) — the Phase-5 outcome loop will want both.
  Prompt "Execute toward the reference" steps 4–5 rewritten (tranche default,
  residual-shelter math, per-sleeve records, enforcement warning); asymmetry + Tier-1
  bounds kept verbatim. 27 new tests incl. replays of the 2026-06-30 pathology (now
  emits 3 enforcement trades inside the turnover cap) and the 2026-07-03 rotation
  (confirming, zero synthesis); **suite 232 green, ruff clean.** The stale
  `concentration_gap` stash was not found on this clone (it lived on the retired
  OneDrive working copy) — nothing to drop. **Next: brief Phase 5 (override-outcome
  stamping), now unblocked.**
- **2026-07-03** (PR #10, branch `feat/policy-axis`) — **#16 policy axis automated
  (market-implied stance).** The classifier's policy leg was structurally dead:
  `fomc-stance.json` sat `unconfirmed` / `as_of: null` since inception, the gate could
  never confirm Q1, and "policy unconfirmed" inflated the conviction proxy daily. New
  pure `_build_policy_axis(macro_data, manual_stance, cfg, today)` (echo-not-re-derive;
  DGS2/DFF already fetched at limit=90, no fetch changes) emits a `policy_axis`
  snapshot block: **market-implied stance** from the DGS2 20d delta (≥ +20bp →
  `hawkish`, ≤ −20bp → `dovish`, else `neutral`; DGS2−DFF `spread_bp` as context;
  <21 obs → unavailable) **layered under the manual file** — a fresh `as_of` (≤45d)
  GOVERNS (`source: manual_fresh`, a real SEP/dot-plot beats a market proxy), stale/
  null → `market_implied`, both unavailable → `unconfirmed` (now rare by construction).
  Emits both layers + `agreement` flag (disagreement surfaced in `note`, deliberately
  NOT a new divergence entry — candidate for later). `_build_regime_gate` consumes the
  RESOLVED stance (fail-closed on hawkish unchanged; `derived_from` gains
  `policy_source`), which flows to `_conviction_proxy` via `derived_from.policy_stance`.
  Config `risk-limits.json` → `policy_axis` (hawkish/dovish bp + `manual_fresh_days`).
  Prompt updated echo-only (policy bullet, gate rule, freshness table, inputs list,
  dashboard row — no new LLM discretion); `fomc_stance` stays in the snapshot as the
  raw manual echo. **EXPECTED BEHAVIOR CHANGE:** policy resolves instead of
  `unconfirmed` → conviction proxy can drop ~1pt → reference weights may shift. 13 new
  tests (thresholds inclusive-boundary, <21-obs, manual-fresh-wins/stale-loses,
  agreement, config freshness window, gate integration); **suite 218 green, ruff
  clean.** The manual file remains the SEP override channel — **update it after the
  2026-07-28/29 FOMC.** Live verification: next 09:00 ET report shows Policy resolved
  with `source: market_implied`.
- **2026-07-03** (PR #9, branch `fix/gdpnow-quarter-boundary`) — **#15 GDPNow
  quarter-boundary blind window FIXED.** The ALFRED vintage fetch now starts at the
  PRIOR quarter start (was current-quarter-only, which guaranteed an empty
  `GDPNOW_VINTAGES` for weeks at every quarter turn — observed 2026-07-01..03: growth
  axis degraded to `cross_quarter_fallback`, regime indeterminate). New pure
  `_gdpnow_vintage_rows` splits the one ALFRED response into `GDPNOW_VINTAGES` +
  `GDPNOW_VINTAGES_PRIOR`; `_build_growth_axis` (pure — splice decision lives here,
  fetch stays in orchestration) reads the prior quarter's TAIL (last 6 vintages,
  `basis: "prior_quarter_tail"`, confidence medium, explanatory note) when the current
  quarter has <3 vintages and the prior has ≥3 — never an empty trajectory while FRED
  has vintages in the window. ≥3-current (`within_quarter_vintages`/high), both-thin
  (`cross_quarter_fallback`/low), and no-data (indeterminate) paths unchanged; no other
  snapshot block, gate rule, or prompt touched. 6 new tests pin the boundary (0/1/2
  current vintages, tail-slope-not-whole-quarter, current-wins-over-prior, both-thin
  fallback, row splitter); **suite 211 green, ruff clean.** **Live verification = Mon
  2026-07-06 09:00 ET run:** the growth axis should read the Q2 vintage tail
  (`prior_quarter_tail`) instead of the fallback.
- **2026-06-29** (ops-only, no code) — Diagnosed + restored the `/today` page after
  it broke with `/api/dates → 500`. **3rd recurrence of Open #2:** the 2026-06-28
  infra deploy wiped the SWA's `STORAGE_CONNECTION_STRING` + `FUNC_MASTER_KEY`.
  Re-applied both live (`az staticwebapp appsettings set`, in
  `rg-portfolio-automation-prod`) + re-ran **Deploy web (SWA)**. Also ruled out a
  func-pfauto `webjobs.storage: Unhealthy` log warning as a **transient
  drain/recycle flap** (host Running, 8 functions registered, MI roles + storage
  network all intact, no App Insights traces). No repo files changed — the fix lives
  in Azure only and the **next infra deploy will wipe it again**. **Next task: Open
  #4** (MI-based `web/api`, removes the secret for good).
- **2026-06-28** — Built the **intraday catalyst Flex engine** (`src/flex/`,
  `docs/specs/Flex_Catalyst_Engine_v1.0.md`), replacing the conviction sleeve +
  `flex_review` and **superseding #10**. New `flex_intraday` timer (every 15 min,
  `is_open`-gated, `FLEX_ENABLED` ships OFF) + `/api/flex` dry-run route. Pure modules
  (`indicators`/`regime`/`entry`/`exit_state`/`reconcile`) with 36 unit tests; the LLM
  emits `flex_nominations[]` (FLEX_SCHEMA_V1, asserted at analyzer load + CI), the engine
  computes/executes via live OTO entry + resting GTC stop (Alpaca has no native
  scale-out/trailing-bracket → managed cancel/replace pair). Reconcile-FIRST with a
  no-naked-long repair; idempotent epsilon-gated trailing; per-tick decision audit
  (`flex-decisions/*.jsonl`). **Sizing config reconciled** to `RISK_BUDGET_PCT=0.40` /
  `PER_NAME_CAP_PCT=12.0` (was 0.75/4.0, where the cap silently dominated the budget) —
  the `binding` constraint is now surfaced. Flex trades still feed `TradeHistory` → Phase
  C. ruff clean, 105 tests pass. **Open follow-ups:** live-paper verification after flipping
  `FLEX_ENABLED=true` (dry-run first); delete the dead `_build_flex_review` builder; SIP feed
  for true VWAP. Priority #2's "real flex buy emits §7 enums" now routes through the engine.
- **2026-06-25** — Specced the **flex trailing stop + catalyst-gated relative exit**
  (`docs/specs/Flex_Trailing_Stop_v1.0.md`, `e78e25a`); decision-locked, not yet
  built — tracked as Open #10 for implementation. Design summary in #10.
- **2026-06-25** — Reviewed the **wheel-strategy** placeholder
  (`Future_Project_Wheel_Strategy.md`) at the account holder's request and **parked
  it**: data foundation is stale (assumes E*TRADE options chains/IV/Greeks, but
  E*TRADE was removed — the system collects zero options data and would need a new,
  likely paid, source), the approval design predates the SWA single-pane (proposes
  Logic Apps/Teams), and the wheel structurally caps upside (short-vol) so it trails
  SPY in a bull — a different mandate than "beat SPY". Account holder not convinced
  for now; revisit only per the spec's §3 prerequisites.
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
