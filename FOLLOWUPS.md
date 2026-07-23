# Follow-up items

Running backlog of known-open work. Newest context at top. When you pick an
item up, move it to **Done** with the date + commit so the history is visible.

**▶ START HERE — last session 2026-07-23 (regime responsiveness cycle, branch `feat/20260723-leading-growth-market-implied`).**
Tasks: A (#17 leading-growth composite + growth-side transition_watch), B (#18 market_implied_quadrant + daily dollar proxy), C (pnl_decomposition inception-shortfall block), D (F6 sweep sizing / cash-floor guard), E (F7 price-sanity quarantine), F (F8 A4 watch_candidates wording). Suite 721→789 green, ruff clean. PR pending review (see entry **#46** below). **Merge timing note:** 07-24 carries the SOXX/IHE sleeve-switch event; review/merge AFTER that day's report is graded so switch behavior is observed on unchanged code.

**▶ Prior session 2026-07-22 post-merge (prompt-completion, branch `fix/20260722-prompt-completion`).**
PR #28 merged to master (`7be613a`); decision gates A-G1 (last-emission-only persistence)
and B-G1 (gate-zeroed VXUS gap row) shipped as defaults. This PR lands three deferred
prompt tasks: A4 (`watch_candidates` emission contract), C (override-paragraph hygiene),
D (narrative-vs-addendum consistency). See entries **#45** + **#44** below.

**▶ Prior session 2026-07-22 (Flex funnel v2 + 07-22 report-hygiene batch, PR #28, merged `7be613a`).**
#8 v2 (dynamic `watch_candidates`) + prompt-hygiene findings F1–F5 shipped. See entry **#45**.

**▶ Prior session 2026-07-21 (flex reactivation + deferred findings 4–8, PR #27, merged `fba431b`).**
Reactivated the Flex engine (G1 borderline tiebreak, D2 non-selected floor zeroing, D3 roster
separation set), landed deferred 07-13 findings 4–8. Suite 690 green. See entry **#44**.

**▶ Prior session 2026-07-02 (outage diagnosis + streaming hotfix, PR #7;
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

### 2. SWA secret app settings are wiped by every infra deploy ✅ KILLED 2026-07-11 (`fix/swa-hardening`)
Recurred **4 times** (2026-06-09, 06-15, 06-29, 07-10) before this fix. Root
cause was as documented: `az deployment group create` replaces the SWA's
app-setting set wholesale, and the secrets (`STORAGE_CONNECTION_STRING`,
`FUNC_MASTER_KEY`) were only ever applied post-deploy via `az staticwebapp
appsettings set`.
- **The originally-proposed fix ("Key Vault references, mirror
  functionapp.bicep") turned out not to be buildable** — verified against
  Microsoft Learn (2026-07-11): Azure Static Web Apps *managed functions*
  (what `web/api` is) support **neither Key Vault app-setting references nor
  managed identity, on any plan** (Standard included) — both are explicitly
  listed as unavailable; only Bring-Your-Own-Functions gets them. Separately,
  `functionapp.bicep` doesn't even use that pattern itself — it reads secrets
  via the SDK + managed identity at runtime (`KEY_VAULT_URI` +
  `shared/keyvault.py`), a different mechanism than a native `@Microsoft.KeyVault(...)`
  app-setting reference.
- **Actual fix:** bicep's `getSecret()` function, which resolves a Key Vault
  secret at DEPLOY TIME (via the deploying principal, not the app's runtime
  identity) and bakes it into a plain app-setting value. `keyvault.bicep` sets
  `enabledForTemplateDeployment: true`; `main.bicep` calls
  `keyVaultRef.getSecret('swa-storage-connection-string' | 'swa-func-master-key')`
  and passes the results as `@secure()` params into `staticwebapp.bicep`'s
  `swaSettings` resource. Every infra deploy now SETS the current secret value
  instead of wiping it. Secrets seeded via `scripts/seed-swa-secrets.sh`.
- **Verified live:** ran a real `az deployment group create` against
  `rg-portfolio-automation-prod` (after a clean `az bicep build` + a what-if
  showing only pre-existing unrelated drift); `az staticwebapp appsettings
  list` showed both secrets correctly set from Key Vault, and
  `curl https://kind-sea-07d4d1b0f.7.azurestaticapps.net/api/dates` → 200.
- No more manual post-deploy `az staticwebapp appsettings set` runbook step —
  rotate a secret in Key Vault and redeploy infra to pick up the new value.

### 3. Entra ID auth is currently OFF on the SWA ✅ FIXED 2026-07-11 (`fix/swa-hardening`)
Was `allowedRoles: ["anonymous"]` on `/api/*` and `/*` — publicly reachable.
- **Did NOT revive the old custom AAD app registration** (that path requires
  the Standard plan and a client secret to manage/rotate/lose again — it's
  what broke in the first place). Instead: SWA's **preconfigured** Microsoft
  Entra ID provider (available on every plan, no app registration, no client
  secret, ever) + the built-in invitation system's custom `owner` role
  (also available on Free, up to 25 users). `/*` and `/api/*` now require
  `allowedRoles: ["owner"]`; `401` redirects to `/.auth/login/aad` with
  `post_login_redirect_uri=.referrer`; `/.auth/*` stays anonymous so the login
  flow itself is reachable. This matches CLAUDE.md's own documented design
  ("Free SKU, Entra ID Easy Auth") more closely than the original registration
  ever did.
- Also added `/login` / `/logout` friendly-route redirects — `web/app.js`
  already hardcoded links to both, but neither existed in the config;
  `/logout` in particular would have silently done nothing (SPA fallback
  instead of `/.auth/logout`) once auth was enforced. Found by grepping
  `web/*.js` before assuming nothing would break.
- **Rollout note:** the config change ships in this branch, but production
  deploy is gated on an operator accepting an `owner` role invitation first
  (`az staticwebapp users invite ... --roles owner`) — otherwise nobody
  satisfies `allowedRoles:["owner"]` and `/today` locks out everyone,
  including the operator, the moment it deploys.

### 4. Migrate the SWA API off the storage connection string — CONSTRAINT VERIFIED 2026-07-11, not currently actionable (`fix/swa-hardening`)
CLAUDE.md mandates "Managed Identity only — no connection strings." Verified
against Microsoft Learn (2026-07-11) rather than attempting the migration:
Azure Static Web Apps **managed functions do not support managed identity at
all**, on any plan — the platform's own API-support matrix lists it as
unavailable for managed functions (available only for Bring-Your-Own
Functions). So `DefaultAzureCredential` in `web/api/function_app.py` has
nothing to authenticate with; forcing it would just fail at runtime.
- **Not fixed, by design** (per the task: verify the constraint, don't force a
  broken migration). `STORAGE_CONNECTION_STRING` stays — but #2's fix means it
  is now sourced from Key Vault at deploy time rather than a manually-applied
  secret, so the operational pain this item was chasing is already resolved.
- **The real future fix** is migrating `web/api` to a **Bring-Your-Own
  Functions** backend (a separate Azure Functions app, like `func-pfauto`,
  linked to the SWA) — that unlocks managed identity, Key Vault references,
  and the full Azure Functions trigger/binding surface. This is a real
  platform migration (new Function App resource, linking config, a second
  deploy pipeline), out of scope for this hardening batch; revisit only if
  the connection-string secret itself becomes a live problem again (it
  shouldn't, now that #2 is fixed) or the Learning tab needs a capability
  managed functions can't provide.

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

### 8. Collector: fetch data for flex candidate names — static v1 ✅ DONE 2026-06-15 / v2 ✅ DONE 2026-07-22 (`feat/20260722-flex-dynamic-candidates`)
**Static v1 shipped:** `config/flex-candidates.json` (seed: ETN, NEE, XLU, MU) is
loaded by the collector (`_load_flex_candidates`), deduped vs holdings, capped at
20, and its tickers get FMP profiles (→ new `flex_candidates` snapshot block) +
EOD prices (merged into `prices`). Gatekeeper G2 + the inputs list now point at
`flex_candidates`, so a seeded non-held name can clear G2 and reach BUY. Edit the
config to pin more names.
- **v2 ✅ DONE 2026-07-22 (`feat/20260722-flex-dynamic-candidates`):** The analyzer now
  emits a `watch_candidates` array in the trades JSON (≤6 entries, `{symbol, reason}`);
  the collector merges the PREVIOUS run's list with the static seed so the AI self-requests
  data for names it surfaces. Sanitization rules drop: invalid symbol format, currently held,
  core-roster separation-set members (new `flex.regime.flex_separation_set(held)`), non-
  reenterable LEGACY_EXITS; INTC/MCK/PPA/EUAD (FLEX_REENTERABLE) are carved out when flat.
  Static names have priority; cap stays at 20. Each `flex_candidates` profile gains a
  `source: "static"|"dynamic"` field. Persistence = last-emission-only (A-G1 default).
  31 new tests, ruff clean. Probes confirmed. See #45 for full details.

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

### 12. `quadrant_performance` snapshot block — regime-call accountability ✅ DONE 2026-07-11 (`feat/quadrant-performance`)
Fed the quadrant-vs-SPY data (built for the web chart, PR #8) back to the **analyzer**.
New non-fatal, describe-only `quadrant_performance` block (`collector._build_quadrant_performance`,
built right after the `performance` scoreboard so it reuses the same in-run perf
series): per Q1-Q4 bucket, 30/60/90d basket return + excess vs SPY (mirrors the web
chart's `_quadrant_series` window-return semantics via a deliberate pure copy,
`_quadrant_perf_series`, since the SWA API can't import this module), plus a
`favored_streak`/`streak_excess_pp`/`lagging_sessions` hysteresis scan (recomputed
AS-OF each session, not just read off today) and a `suspect` flag — true when a
FAVORED bucket has lagged SPY every session it's been checked for
`suspect_after_sessions` (config, default 10) consecutive sessions. The prompt
(Section 1 echo + a new "Regime-call accountability" section near Track record + a
Dashboard "Regime P&L" row) mandates one explicit paragraph confronting a `suspect`
favored bucket and raises the evidentiary bar for INCREASING (never
reducing/holding) that bucket's weight — a **prose/judgment gate only**, never a
validator rule or a `reference_weights` change. A `regime_suspect` OverrideHistory
row is written per suspect bucket per report day (`analyzer._write_regime_suspect_history`)
for #13's monthly review, though **no stamper grades it yet** — verified (not
rebuilt, per the task) that `_stamp_override_outcomes` requires override-shaped
`falsifier_date`/`sleeve`/`direction` fields and `_stamp_switch_outcomes` hardcodes
an allow-list of `layer` values that excludes `regime_suspect`; a future grading
pass needs a third stamping path mirroring `_grade_switch`'s shape (bucket forward
return vs SPY from the flagged date). 12 new pure-builder tests (462 total green,
ruff clean). Motivating live example the block now surfaces (2026-07-02 real
snapshot): Q3 was the favored bucket while being the worst performer since
inception (−7.1%, GLD −10.5%) — this is exactly the case `suspect` is built to
catch. Details in the PR description.

### 13. Monthly self-initiated strategy review + amendment channel ✅ DONE 2026-07-12 (`feat/learning-loop`) — Learning Loop v1.0
Built as **`docs/specs/Learning_Loop_v1.0.md`** ("Learning Loop", combining this item
and #32 into one spec — the amendment channel and the improvement ledger turned out
to be the same mechanism, not two). `src/learning/` (bundle builder, deterministic
schema validator, pure diff-apply checker, the reviewer function) +
`web/api/learning_github.py` (GitHub PR mechanics) + the Learning tab (`web/learning.*`).
Ships `LEARNING_PHASE=1` (dry-run only — reviewer runs monthly, output lands in
blob/table, no tab, no decisions) per the spec's 3-phase rollout (§11); flip to 2 then
3 manually after each phase's gate is met.
- **Model decision SUPERSEDED from what's below:** the two-tier "review runs on a
  stronger model than the daily analyzer" plan (2026-07-02) was revised 2026-07-11 —
  the reviewer **launches on the EXISTING `claude-sonnet-4-6` deployment** (no new
  Foundry deployment, no new quota request) and upgrades to Claude Fable 5 later via a
  pure config flip (`LEARNING_MODEL` + raising the two token-budget settings) once its
  quota lands — see new item below. Simpler and cheaper than standing up Opus 4.8 as an
  interim floor while waiting on Fable 5 quota.
- **Guardrails as actually built:** proposals are capped ≤3/cycle (≤1 structural),
  classed 0-3 with escalating evidence bars (class 2 parameters require ≥10 graded rows
  — this is exactly how #37's do-not-tune-early rule resolves, see the note there),
  target-file allowlisted to 4 config files (code/validator/infra are never a diff
  target — a class-3 structural proposal is a SPEC DRAFT for a separate human-driven
  build, never code), and every approval opens a GitHub PR (never a direct write) that
  the automation credential cannot merge (branch protection). The forced re-review rule
  (§9) makes an amendment's `review_by` date a real re-review trigger, not a suggestion.
- **Not yet done (tracked separately below):** the mechanical amendment grader (deferred
  until ≥5 amendments exist) and the GitHub App replacement for the v1 PAT.

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
**#12 update (2026-07-11):** the `regime_suspect` OverrideHistory layer now exists
(one row per suspect favored bucket per report day: bucket, `favored_streak`,
`streak_excess_pp`, and whether that session's trades increased/held/reduced it) —
this is a ready-made input for the "favored-vs-realized quadrant divergence"
retrospective above. It is not yet graded by any stamper (see #12's Done entry);
the monthly review's design should account for whether it needs graded rows or can
work from the raw action/streak log directly.
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

### 17. Leading-growth composite + growth-side `transition_watch` ✅ DONE 2026-07-23 (`feat/20260723-leading-growth-market-implied`)
See entry #46 for full details. The leading-growth composite (`leading_growth` snapshot block) is built, the `leading_vs_lagging_growth` divergence fires, and `_build_transition_watch` generalizes to consume both inflation and growth sides symmetrically. Remaining after this PR: #23 (backtest harness to verify signal lag), #22 (probabilistic quadrant vector that uses the composite as input).
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

### 18. `market_implied_quadrant` block + `market_vs_macro_quadrant` divergence ✅ DONE 2026-07-23 (`feat/20260723-leading-growth-market-implied`)
See entry #46. The block works at borderline regimes; the daily dollar proxy covers DTWEXBGS staleness; `market_vs_macro_quadrant` divergence fires at high/medium confidence only. Sub-item (dollar proxy from FX pairs when DTWEXBGS stale) also shipped. Live test case: DTWEXBGS was 6d stale on 07-23. The original task's FOLLOWUPS entry stated the 07-03 tape-above-200d-while-macro-defensive case would fire active — validated in the new divergence test suite.
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

### 32. Improvement Ledger — monthly self-improvement proposals + `/improvements` tab ✅ DONE 2026-07-12 (`feat/learning-loop`) — folded into Learning Loop v1.0
**Merged into #13's build rather than shipping as a separate ledger + tab.** Once spec'd
together it became clear the "ledger" and the "amendment channel" were the SAME
governance discipline wearing two names: `docs/specs/Learning_Loop_v1.0.md`'s
`proposals[]` (schema §6) ARE the evidence-triggered entries this item asked for —
`class 0` (Observation) is exactly the "no config change, just a FOLLOWUPS-worthy
finding" entry type this item described, and the pipeline view (proposed → decided →
applied/rejected → graded) lives on the single Learning tab (`web/learning.*`) instead
of a separate `/improvements` page. The governance constraint this item called out as
"critical, record verbatim" — entries never change behavior directly, always route
through human approval — is exactly spec §1's "proposer ≠ approver" principle. No
functionality from this item's design was dropped; it just didn't need its own surface.

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
  (**#12 update 2026-07-11:** the concrete plumbing for the "quadrant call graded
  late/wrong" trigger is the `regime_suspect` OverrideHistory layer — see #12's
  Done entry and the #13 note above; it is not yet graded by a stamper.)
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

### 36. International governance redesign (dollar/rotation-governed intl sleeve, flex migration, gate precedence) — ✅ RESOLVED 2026-07-10 (`feat/quadrant-roles`)
**Resolved by the roster revision v2** — see `docs/specs/roster_revision_2026-07.md`.
The international sleeve is now governed by `intl_governance` (collector, deterministic):
a leader-selective sizing ladder driven by the rotation composite + the DXY dollar
switch, with a gate modifier that **halves** (never zeroes) the leader tilt — this
REPLACES the Task-8 INTERIM suppress-to-zero rule (now deleted). The design is
leader-selective (small `intl_broad` base + a rotation-sized `intl_leader` slot) on the
2026-07-09 evidence that intl outperformance is narrow (AIA +11pp vs SPY while the bloc
average is −7.5pp). Flex migration: intl single-name exposure stays in flex; the two
intl ETF roles (`intl_broad`/`intl_leader`) are the core sleeve. `reference_weights`
consumes the block for the intl roles instead of quadrant math. Original PENDING scope
(kept for history):
- a **dollar/rotation-governed intl sleeve** — a deterministic target for the
  international allocation driven by the DXY switch + rotation score, folded into
  `reference_weights` (not an LLM freehand tilt);
- **flex migration** of the single-name intl exposure vs the core ETF intl sleeve
  (which names live in core vs flex);
- **explicit gate precedence** for international (replacing the interim size-0 rule
  with a governed interaction between the gate, the rotation score, and the sleeve).

### 37. Tune the sleeve-selection hysteresis + intl ladder params (LOW — data-gated)
The roster revision v2 (`feat/quadrant-roles`, `docs/specs/roster_revision_2026-07.md`)
ships with **initial** tunables in `sleeve-roles.json`: the selection hysteresis
(challenger must lead by ≥ 2.0 for ≥ 10 consecutive runs) and the intl sizing ladder
(`intl_base_pp` 2.0, leader tilts 1pp/3pp, `leader_min_excess_pp` 5.0). **Revisit these
once Phase C has graded ≥ 10 switch/rotation decisions** (OverrideHistory layers
`sleeve_switch` / `intl_leader_rotation`, graded vs the incumbent counterfactual at
30/60/90d) — if switches whipsaw or the leader tilt is mis-sized, adjust the thresholds.
Do not tune before the sample exists.
**Learning Loop note (2026-07-12):** this "do-not-tune-early" rule is now enforced
automatically rather than by discipline alone — the Learning Loop's class-2 (Parameter)
bar requires `evidence_n >= 10` graded rows directly bearing on the parameter before a
proposal touching `sleeve-roles.json`'s hysteresis or intl-ladder values can even pass
schema validation (`docs/specs/Learning_Loop_v1.0.md` §6/§12 point 5). No special-case
code was needed — the same bar every other class-2 parameter proposal must clear.

### 38. Mechanical amendment grader (LOW — data-gated, deferred until ≥5 amendments applied)
Spec §9's third grading function (sibling of `_grade_switch`, per the shape sketched in
the `regime_suspect` docstring): every applied Learning Loop amendment writes an
OverrideHistory row (layer `amendment`, `proposal_id`, `falsifier`, `review_by`) with
its grading hooks (`outcome_status`/`resolved_correct`) left null — nothing stamps them
yet. Verified (not built) during the Learning Loop batch that neither `_stamp_override_outcomes`
(requires `sleeve`/`direction`, override-shaped) nor `_stamp_switch_outcomes` (hardcodes
an allow-list of `layer` values that doesn't include `amendment`) would grade these rows
even by accident. **Build once ≥5 amendments have been applied** — mirror the mechanical,
falsifier-at-its-own-terms grading approach already used for `regime_suspect`
(FOLLOWUPS #12): evaluate at the amendment's `review_by` date whether its falsifier
condition held. Until then, the Learning Loop's own forced re-review rule (spec §9 — every
amendment gets an explicit keep/revert/amend at its `review_by` cycle) is the only
grading mechanism, and it is human judgment, not a stamped grade.

### 39. GitHub App to replace the Learning Loop's fine-grained PAT (LOW — hardening follow-up)
v1 ships with a fine-grained PAT (`github-learning-pat`, KV-stored, `contents:write` +
`pull_requests:write`, no merge/admin — spec §8) as the credential behind the approval
mechanics' branch/commit/PR calls. A GitHub App installation is the cleaner long-term
replacement (short-lived installation tokens instead of a long-lived PAT, scoped
permissions enforced by GitHub's own installation model rather than by convention,
no manual rotation). Noted as a follow-up in the spec, not required for v1 — the PAT's
blast radius is already bounded (single repo, no merge rights, branch protection as the
backstop).

### 40. Upgrade the Learning Loop reviewer to Claude Fable 5 (MEDIUM — gated on Foundry quota)
The reviewer launches on the analyzer's existing `claude-sonnet-4-6` Foundry deployment
(no new deployment, no new quota — decided 2026-07-11, spec §3). Claude Fable 5's 1M-token
context would ingest a full month of daily reports + the complete graded record + all
live config verbatim, instead of the ~8-12 most recent reports the 150K-token launch
budget fits (`LEARNING_BUNDLE_MAX_TOKENS`, chars/4 estimate) — the graded record is the
primary evidence engine at launch; full-month verbatim prose is what this upgrade
unlocks. **The upgrade is a config flip, never a code change:** set `LEARNING_MODEL=
claude-fable-5` and raise `LEARNING_BUNDLE_MAX_TOKENS`/`LEARNING_MAX_TOKENS` once the
requested Fable 5 quota lands on the `Portfolio-Analysis` Foundry project. Verify the
deployment exists and quota is non-zero (`az` / Foundry portal) before flipping — do not
assume quota approval happened silently.

### 42. 2026-07-15 daily-report audit: execution-fill visibility, reconcile sequencing, VXUS deadlock, legacy-exit enforcement, override direction — ✅ DONE, merged 2026-07-17 (PR #25, `fix/20260715-exec-fills-reconcile-seams`)
Post-PR-#24 observation of the 07-14/07-15 reports exposed five new systemic findings.
All confirmed against the code (Finding A diagnosed live against the Alpaca paper API)
and fixed on this branch:
- **Finding A (HIGH) — a validated MU sell was never executed, and execution failures
  were invisible to the next day's report.** Diagnosed live (Task A0): the 07-14
  `daily-trades` file DID contain a validated MU sell (`layer: "flex"`, passed);
  `daily-executions/2026-07-14.json` shows it 403'd (`Forbidden`) and the SAME 403 hit
  again on 07-15. Root cause: a stale **GTC stop order** placed by the flex engine on
  2026-07-08 (`client_order_id: flex-2026-07-07-MU-rep-302e8f`, stop $628.48, still
  `status: "new"`, `expires_at: 2026-10-06`) had locked both MU shares as order
  collateral (`qty_available: 0`) ever since — every subsequent sell attempt for that
  symbol was rejected by Alpaca, invisibly, forever. **Fixed:** Task A1 adds a collector
  `execution_review` snapshot block (Alpaca-only, non-fatal) that reads back the prior
  trading day's `daily-executions/{date}.json` and reconciles each order's actual
  terminal Alpaca state, surfacing `failed`/`unfilled` entries for the prompt to name
  in the Data Integrity Warning and never assume executed. Task A2 codifies the
  orphaned-flex-exit exception already implicit in the reconciliation doctrine
  (`trades[]`, `layer: "core"`, `flex_source: null`) in both the Separation Contract and
  the trades[] schema sections of `project-instructions.md`. Task A3 fixes the
  executor-level bug: `_place_one` now cancels any pre-existing OPEN Alpaca order on a
  symbol before submitting a new one for it (`_cancel_conflicting_orders`) — today's
  recommendation is the authoritative decision and supersedes a stale resting order.
  The MU position itself is NOT force-resolved by this session (account holder's call);
  A1 makes a second failure visible instead of silent.
- **Finding B (HIGH) — `reconcile()` sized enforcement BEFORE the validator ran, and its
  cash model excluded off-roster sell proceeds.** Exact to the share: the 07-14
  synthesized KMLM buy was 57 shares (doctrine math said ~126-135 affordable) because
  (1) `analyzer/handler.py` ran `reconcile` on the model's RAW trades, so the
  soon-to-be-rejected $1,927 VXUS buy was still counted as spent, and (2) `reconcile`'s
  `rows` dict excluded MU's off-roster sell entirely, so its ~$1,967 proceeds never
  entered `cash_avail`. **Fixed:** Task B1 restructures the analyzer into two Tier-1
  passes — pass 1 validates the model's raw trades and drops what Tier-1 would reject;
  `reconcile` runs against the pass-1 SURVIVORS; pass 2 re-validates the full merged
  list (survivors + synthesized trades) so cumulative checks see the final list.
  Rejections from both passes are combined into the addendum, deduped by trade id. Task
  B2 adds an `all_rows` lookup (includes off-roster rows for PRICING only) so an
  off-roster sell's proceeds count toward `cash_avail` while off-roster names remain
  excluded from the synthesis working set (`rows`) — never an enforcement TARGET, only
  a cash SOURCE. Task B3 pins the exact scenario (`tests/test_reconcile_validate_sequencing.py`)
  and asserts `_post_validation_cash` agrees with reconcile's cash view.
- **Finding C (MEDIUM-HIGH) — VXUS structural deadlock.** `intl_broad`'s reference
  target (2.0pp) is unconditional, but VXUS is `block: amplifier_intl`, so V1 rejects
  its buy on every closed-gate day regardless of the rotation score — confirmed
  rejected 07-14 AND 07-15 ("amplifier buy VXUS forbidden"), wasting a trade slot daily
  and (until Finding B landed) starving enforcement cash. **C0 decision (account
  holder, this session): Option 1 — gate `intl_broad` to 0 in the reference builder
  while the deployment gate is closed.** Rationale: doctrine-consistent ("the gate
  outranks everything"), self-healing (rebuilt daily, restores the day the gate opens),
  and a held VXUS position isn't force-sold (a 2pp gap sits inside the 5pp band). The
  leader slot is untouched (already halved, never zeroed, on a closed gate — rotation-
  governed per roster_revision_2026-07 §4). **Fixed:** collector `_build_reference_weights`
  pops the `intl_broad` selected ticker from `intl_targets` (and its pp from
  `intl_total_pct`) when `regime_gate.status == "closed"`, folding the freed room into
  normal core renormalization.
- **Finding D (MEDIUM) — legacy-exit sells were invisible to D3 enforcement; the model
  slow-walked MCK unpoliced.** `is_de_risk_move` recognized only amplifier sells as
  de-risk; legacy exits (no block) fell to "re-risk shortfall — never synthesized" with
  zero backstop on the book's largest overweight. MCK: 1.65pp traded of a 6.56pp
  required tranche (07-14), 0.82pp of 4.79pp (07-15), no override filed either day.
  **D0 decision (account holder, this session): yes to both D1 and D2.** **Fixed:** D1
  extends `is_de_risk_move` so a SELL of a `LEGACY_EXITS` name counts as de-risk,
  letting D3 synthesize legacy-exit shortfall sells at tranche pace (a real behavior
  change — D3 will now sell MCK/AMZN/GOOGL down deterministically whenever the model
  under-trades the tranche). D2 (unconditional, no gate) surfaces `reconcile`'s
  `non_compliant_flagged` sleeves in a new report addendum
  (`analyzer._flagged_sleeves_addendum`) — symbol/gap/required-move/model-move/reason
  — so slow-walking is visible in the report itself, not just the JSON.
- **Finding E (MEDIUM) — override `direction` was self-declared and flip-flopped
  between days.** 07-14 correctly filed the GLD-above-reference hold as `de_risk`;
  07-15 filed the SAME situation — plus XLP and TLT, all dampers held above reference —
  as `re_risk` (backwards; would have imposed the HARDER evidence bar on a cheap,
  legitimate override). Consequential beyond labeling: the collector's
  `_override_sign`/`_grade_override` (Phase 5 outcome stamping) derive the weight
  direction FROM the persisted `direction` + the sleeve's block — a mislabeled
  `direction` would have inverted the counterfactual grading sign. **Fixed:** Task E1
  adds `shared.reference_execution.derive_override_direction` (pure, shares the block
  model with `is_de_risk_move`); `shared.overrides.validate_override`/`validate_overrides`
  now accept `gaps`, derive the direction deterministically, use the DERIVED direction
  for the asymmetry bar, and **correct-and-flag** a disagreement (append a reason,
  never reject solely for a mislabeled direction) — both `direction` (effective) and
  `declared_direction` (the model's original claim) persist to OverrideHistory so
  Phase C can measure the misclassification rate. Task E2 adds a concrete GLD/XLP/TLT
  example + the derivation note to the override section of `project-instructions.md`.
- Task F1-F4 (prompt-only, `project-instructions.md`): F1 — state a "new print" when a
  series' value OR `as_of` changed vs. the prior report, and adjudicate a previously-
  flagged same-day catalyst's outcome in Section 5 (07-14 flagged the June CPI print as
  today's catalyst; 07-15 never adjudicated it, calling it "no new print" despite a
  materially different value). F2 — echo the snapshot's `as_of` verbatim (observation
  period), never a computed/release date (07-14 showed CPI/PCE as-of dates that don't
  follow the monthly first-of-period convention 07-15's did on the same underlying
  prints). F3 — the DXY cadence check is a specific 10-trading-day window; say so
  explicitly when the snapshot lacks that observation rather than substituting a
  shorter/longer delta. F4 — only call a role switch "proposed / awaiting config
  commit" when `switch_signal` is actually true; below the hysteresis threshold, state
  lead/streak/threshold status only (07-14 called three streak-2 roles "proposed").
- **Shipped:** Tasks A (A0 diagnosis, A1, A2, A3), B (B1, B2, B3), C (C0=Option 1), D
  (D0=yes to D1+D2), E (E1, E2), F (F1-F4). Suite +25 tests (564→589), ruff clean. **Out
  of scope on this branch** (deferred 07-13-audit findings 4–8, unchanged): FMP
  earnings-calendar held-position filtering, performance-lag attribution, quadrant
  Table A cell arithmetic, the misleading Recommended-weight column, the shock-3 "15%
  ceiling" phrasing, the Q2 per-sleeve band-granularity observation, tranche-config
  visibility (#33(i)), and the model's KMLM 43-vs-44-share table slip.

### 43. 2026-07-17 post-merge audit: price-basis coherence, config visibility, deterministic quadrant/series blocks, flex order hygiene — ✅ DONE, branch `fix/20260717-price-basis-config-determinism`
Post-PR-#25 observation of the 07-16/07-17 daily reports exposed the residual seams
below. One decision gate (**G1**, Task F3): **account holder chose YES — switch flex
repair/entry stop orders from GTC to DAY**, re-placed every in-hours tick by the
existing no-naked-long path (identical protection during regular hours, no immortal
stale orders; the tradeoff — no stop coverage on a day the flex run itself never
ticks — is covered by the new F2 orphan sweep + the very next tick's repair).
- **Task A (HIGH) — phantom V3 clamp: the gap-row price basis contradicted
  `current_pct`'s basis.** `_build_reference_gaps._price` let the FMP EOD close win
  over the paper-account position's `current_price` for a HELD name, while
  `current_pct` was computed from the paper-priced `market_value` — any FMP-vs-paper
  divergence over ~3% mixed the two bases in V3's landing-percentage math and could
  phantom-clamp a legitimate full exit to a 1-share stub (confirmed 2026-07-16, MU:
  a 5.9% divergence clamped 2→1 share; a 2.8% divergence on 07-17 happened to slip
  under the `_EPS_PP` epsilon and pass — luck, not correctness). **Fixed:** `_price`
  now prefers the paper position's `current_price` for any held symbol, falling back
  to the FMP close only for an unheld reference target. New coherence invariant
  pinned permanently (`tests/test_price_basis_coherence.py`): for every gap row with
  `held_qty > 0`, `current_pct` must agree with `held_qty * price / equity * 100`.
- **Task B (HIGH leverage) — execution_config snapshot block (closes #33(i)).** Four
  consecutive sessions guessed operative config numbers (assumed `tranche_pp_max`
  3-5pp against a true 10.0; assumed `gap_band_pp` 1.0pp against a true 5.0, which
  alone filed three unnecessary in-band overrides on GLD/XLP/TLT on 07-17). **Fixed:**
  `shared.reference_execution.effective_execution_config()` resolves the exact
  numbers `reconcile`/`validate_trades` use; the collector echoes it into the
  snapshot's new `execution_config` block; the prompt now quotes every tranche/band/
  floor/min-notional/evidence-bar figure from it verbatim and states the in-band
  shelter rule up front (never assume or guess a config value).
- **Task C — static `selected` vs runtime `leader_pick` doctrine (echo only, no
  validator change).** `sleeve_selection` only ranks `selection: "scorecard"` roles,
  so the `intl_leader` role (`selection: "rotation"`) never appeared there at all —
  nothing distinguished a runtime `leader_pick` de-rotating to null (normal daily
  modulation) from an actual deselection. 07-17: the model proposed selling AIA's
  1-share floor on exactly that confusion; Tier-1 correctly rejected it (the existing
  2026-07-13-audit floor-bypass design was already right). **Fixed:** new collector
  `role_selection` block echoes every role's static `selected` (including
  `intl_leader` + its current `leader_pick`), and the prompt states the doctrine with
  the 07-17 AIA case as the worked example.
- **Task D (MEDIUM-HIGH) — deterministic `quadrant_allocation` block (retires
  deferred findings 7+8).** 07-17 published two contradictory Table A's in the same
  report (Q1 0.77% vs a corrected 1.46%; Q2 5.37% vs 3.72%), with a literal "wait —
  let me recompute carefully" leaking into the markdown (07-16 leaked similarly).
  **Fixed:** collector `_build_quadrant_allocation` precomputes Table A's Current
  column from the paper account, using the SAME static `primary_quadrant()` tagging
  the Reference column (`_aggregate_by_quadrant`) already uses (shared
  `quadrants.quadrant_allocation_bucket`) — Q1-Q4, `intl`, dedicated `legacy_exits` /
  `off_roster` rows, `cash_sleeve`, `unmapped` safety net. The Recommended (post-trade)
  column is computed deterministically POST-model (`analyzer._quadrant_allocation_addendum`,
  applied to the FINAL validated `trades[]`) since trades don't exist at collect
  time. Freehand quadrant arithmetic is now prohibited in the prompt entirely.
- **Task E (MEDIUM) — deterministic `series_deltas` block (hardens F1).** 07-17's
  catalyst adjudication fired but attributed a CPI flag to the wrong prior report and
  the wrong prior value (named 07-14's value while claiming it was 07-15's), and
  hand-waved a third-party press figure that plainly disagreed with FRED. **Fixed:**
  collector `_build_series_deltas` reads back the prior trading day's snapshot
  (same non-fatal "look back up to 7 days" pattern as `execution_review`) and
  computes `{value, as_of, prior_value, prior_as_of, delta, new_print}` per tracked
  series; the prompt must cite this — never recollection — for every new-print /
  cadence / catalyst-resolution statement, and report a disagreeing press figure as
  unreconciled rather than massaging it into agreement.
- **Task F — flex order hygiene (root-cause closure of the MU failure class).**
  The MU saga's true root cause: a GTC repair stop survived a ledger-row loss
  invisibly, locking the shares as collateral for 8+ sessions with nothing to notice
  or cancel it (the merged executor-side fix, `_cancel_conflicting_orders`, was a
  collision-point backstop, not prevention). **Fixed:** F1 — `reconcile_ledger` now
  also returns `orphan_orders` (broker open orders for a symbol the ledger doesn't
  track), surfaced in `flex_state.reconcile.orphan_orders` and the Data Integrity
  section. F2 — the engine sweeps its own orphans every tick
  (`flex/handler._sweep_orphan_orders`), STRICTLY scoped to its own client_order_id
  family (current `FLEXC-` + legacy pre-split `flex-`) — never a DayTrade Lab
  (`FLEXD-`) or daily-executor order. F3 (**G1 = yes**) — repair/trail stop orders
  switched from `time_in_force="gtc"` to `"day"`, re-placed every in-hours tick by
  the existing no-naked-long check.
- **Task G — small items.** G-a (prompt-only): a legacy exit whose ENTIRE remaining
  position fits within one tranche and clears min-notional must be finished outright,
  not dripped (07-17 proposed 1 of MCK's 5 remaining shares, a 4.33pp in-band
  position, for no doctrinal reason). G-b: `learning.bundle.fetch_override_history`
  now annotates each `layer: "override"` accepted/downsized row with
  `_direction_suspect` (true when `declared_direction` is absent — possible only for
  a pre-Task-E1 record, since every accepted/downsized decision since 2026-07-15
  always populates both `direction` and `declared_direction`) so the monthly review
  never treats a 07-15-era GLD/XLP/TLT-style backwards direction as reliable signal
  without accounting for it.
- **Shipped:** Tasks A, B, C, D, E, F (F1/F2/F3 with G1=yes), G (G-a, G-b). New test
  files: `test_price_basis_coherence.py`, `test_execution_config.py`,
  `test_role_selection.py`, `test_quadrant_allocation.py`,
  `test_quadrant_allocation_addendum.py`, `test_series_deltas.py`,
  `test_flex_order_hygiene.py`; extended `test_flex_reconcile.py`,
  `test_learning_bundle.py`, `test_reference_execution.py`,
  `test_daytrade_separation.py`. Full suite green, ruff clean. **Out of scope on
  this branch** (per the session prompt, unchanged): the Finding-5 equity-bridge
  attribution batch (unexplained +$330/+$66/+$536 residuals across three sessions),
  the Finding-4 earnings-calendar held-position filtering, and the live MU
  position/stale-order state (expected already resolved by the merged executor fix
  at the 07-17 09:35 ET run — confirm via the next `execution_review`).

---

## Done
### 46. 2026-07-23 session: Regime responsiveness cycle + hygiene batch — Done, branch `feat/20260723-leading-growth-market-implied` (PR pending review)
Six-task PR: Tasks A (#17), B (#18), C (pnl_decomposition), D (F6), E (F7), F (F8). Suite 721→789, ruff clean.

**Completion note (2026-07-24):** The 2026-07-23 push was `0e5258c`. A follow-up
"completion v1" session claimed done but pushed nothing new (the no-op session).
This completion v2 (2026-07-24) addresses the three verified gaps from the v1 session:
(1) prompt `leading_growth`/`market_implied_quadrant`/`pnl_decomposition`/`dollar_proxy`
were one-liner bullets — needed proper `###` describe-only sections; (2) `tests/test_price_quarantine.py`
was absent; (3) the hardcoded "(ETN, NEE, XLU, MU)" seed list appeared twice in the
watch_candidates prompt — replaced with a check against the snapshot's own `flex_candidates`
list. Suite 789→805, ruff clean. Verification gate output in session summary.

- **Task A (#17): Leading-growth composite + growth-side transition_watch.** New `leading_growth` snapshot block: 9-signal diffusion score in [−1,+1] from 6 FRED series (WEI, NFCI-inv, PERMIT, NEWORDER, NOCDFSA066MSFRBPHI, GACDISA066MSFRBNY) + 3 market-derived signals (CPER/GLD 20d ratio, XLY/XLP 20d ratio, HY OAS trend). New `_div_leading_vs_lagging_growth` detector (fires when composite disagrees with realized growth_axis at medium+ confidence). `_build_transition_watch` generalized to consume BOTH `leading_vs_lagging_inflation` AND `leading_vs_lagging_growth` divergences symmetrically — new `_project_quadrant_growth` helper + nested evaluator functions; when both sides fire, more defensive projection wins. New FRED series added to `macro-series.json`; XLY/CPER added to price universe + fetched as historical closes. FMP call budget: +4 historical fetches (XLY, CPER, GLD, XLP).
- **Task B (#18): market_implied_quadrant + market_vs_macro_quadrant divergence + daily dollar proxy.** New `market_implied_quadrant` snapshot block: relative 20/60d basket momentum (reuses `_quadrant_perf_series`) + 6 per-signal cross-asset votes (copper/gold, XLY/XLP, DXY trend, breakevens, HY OAS, 2s10s). Works at borderline regimes. New `_div_market_vs_macro_quadrant` detector — fires at high/medium confidence only; fires at borderline regime when implied outside favored bucket. New `dollar_proxy` snapshot block when DTWEXBGS >5d stale (the live test case on 07-23 — DTWEXBGS was 6d stale). Loads perf series from storage (`read_perf_series`) so it can run before the in-memory `series` is computed. Decision gates: D-G1 (0.75% cash floor default) and E-G1 (20%/50% quarantine thresholds) in `risk-limits.json`.
- **Task C (pnl_decomposition): FIFO realized + current unrealized P&L split.** New `get_activities` method on `AlpacaClient` (paginated). New `_fifo_realized_pnl` pure function + `_build_pnl_decomposition` builder. Three buckets: `core_current` (CORE_ROSTER or any role pool member), `legacy_exits` (LEGACY_EXITS), `off_roster_flex` (everything else — MU lands here). Non-fatal. `performance.pnl_decomposition` in snapshot. Prompt: §1 scoreboard sentence + "P&L decomposition" `###` section with attribution discipline. Tests on buys/sells/partial-lots/FIFO cross multiple symbols.
- **Task D (F6 sweep sizing / cash-floor guard):** Prompt rule: size cash→SGOV sweep on `surplus = literal_cash − target − Σ(same-session buy notionals)` with arithmetic shown in §9. New `_apply_cash_floor_guard` in `analyzer/handler.py` + `_load_risk_limits()` helper: trims or removes the SGOV sweep if post-all-trades literal cash would fall below `literal_cash_floor_pct` (0.75% default, `risk-limits.json`). Surfaces in validation addendum. Applied after pass-2 validation.
- **Task E (F7 price-sanity quarantine):** New `_quarantine_flex_price` collector function: quarantines flex-candidate prices >20% outside the 52-week high/low range OR >50% intraday move without news corroboration. Profile carries `price_quarantined: true` + `quarantine_reason`. Prior snapshot prices loaded at collect time for the delta check. Prompt: quarantined names fail G2 deterministically; LLM no longer adjudicates plausibility itself. `tests/test_price_quarantine.py`: 16 tests covering all quarantine cases (MU 10× case, boundary, with/without news, stale prior snapshot). Proved fail-on-master (`_quarantine_flex_price` absent from `origin/master`).
- **Task F (F8 A4 wording tighten):** Watch_candidates prompt: check the snapshot's own `flex_candidates` list before emitting; hardcoded "(ETN, NEE, XLU, MU)" removed (0 occurrences). Non-re-enterable legacy list (AMZN/GOOGL/DBA/TIP/XSD) kept — that is doctrine.
- **Prompt sections added (completion v2):** `### Leading growth + market-implied quadrant` (adjudication rules, 9-signal descriptions, historical rationale for market-implied verbatim); `### P&L decomposition` (bucket semantics + attribution discipline); `### Regime-call accountability` already existed; Input-list bullets expanded to full describe-only entries with cross-references. All four new blocks now hit the ≥3 / ≥2 mention thresholds.
- **Bookkeeping:** `divergence-config.json` += `leading_vs_lagging_growth` + `market_vs_macro_quadrant` entries. `risk-limits.json` += `literal_cash_floor_pct` (0.75%) + `price_quarantine` thresholds. `macro-series.json` += 6 leading-growth series. `_DIVERGENCE_DEFAULTS` in handler += new divergences. All new blocks in snapshot dict. CLAUDE.md updated (new blocks in data flow). FOLLOWUPS #17/#18 moved to Done (full); #46 added.
### 45. 2026-07-22 session: Flex funnel v2 (dynamic `watch_candidates`) + report-hygiene batch — Done, branch `feat/20260722-flex-dynamic-candidates` (PR pending review)
#8 v2 (dynamic analyzer-emitted `watch_candidates`) + prompt-hygiene findings F1-F5 shipped in one branch.
- **Task A (#8 v2):** Analyzer emits `watch_candidates[]` (<=6 entries, `{symbol, reason}`). Collector merges previous run's list (walk-back 7 days) with static seed; sanitization drops invalid symbol format, currently held, core-roster `flex_separation_set(held)` members (new `flex.regime.FLEX_REENTERABLE` + `flex.regime.flex_separation_set`), non-reenterable LEGACY_EXITS; INTC/MCK/PPA/EUAD survive when flat. Static names have priority; cap stays 20. Each `flex_candidates` profile gains `source: "static"|"dynamic"`. Executor `_extract_trades`/`_validation_refusal` provably unaffected (test added). Decision gate **A-G1** (default = last-emission-only persistence) flagged in PR for veto.
- **Task B (F1, B-2):** When `regime_gate.status == "closed"`, `_build_reference_gaps` emits a gate-zeroed gap row for the `intl_broad` selected name (VXUS) at `reference_pct: 0.0, gate_zeroed: True` if absent from the normal universe — inert to reconcile (gap=0, held_qty=0). Confirmed by test. C0 doctrine caveat added to prompt (B-1). Decision gate **B-G1** (default = ship B-2) flagged in PR.
- **Tasks C-F (prompt-only, F2-F5):** C — override determination resolved before Recommendations section, no mid-paragraph reversals; D — post-trade totals must quote deterministic addendum; E — legacy re-entry wording fixed ("core re-entry prohibited; INTC/MCK/PPA/EUAD flex-nominatable while flat"); F — when `execution_review.date` is not prior trading session, say so explicitly.
- **Suite:** 651 + 31 new = 682 green, ruff clean. Empirical probes passed (A sanitization + B-2 gate-zeroed row).
- **Post-merge completion (2026-07-22, `fix/20260722-prompt-completion`):** PR #28 merged
  (`7be613a`); deferred prompt tasks landed here: A4 (`watch_candidates` emission contract
  added to `project-instructions.md` — without it the dynamic funnel is dead code); C
  (override-determination hygiene rule); D (narrative-vs-addendum consistency rule). START
  HERE region repaired (splice artifact removed, 07-21 prior-session block restored).
### 44. 2026-07-21 audit: flex reactivation + deferred findings 4–8 — ✅ DONE, branch `fix/20260721-flex-reactivation-audit`
Two post-PR-#24 reports (2026-07-20/21) validated the merged fixes and surfaced a new
set. Headline: the Flex engine was structurally offline (G1 hard-blocks every entry
while the quadrant is indeterminate — which it has been since 2026-07-02), the
nomination filter used a stale pre-roster-revision ticker set, and the reference floored
~1.01% of equity in names the validator forbids buying. Decisions (2026-07-21, Jorge):
**D1** borderline quadrant resolves via a 5-day benchmark tiebreak (not a freeze);
**D2** zero non-selected pool-member reference floors (completes PR #24 Option 1);
**D3** flex separation set derives from `sleeve-roles.json` pools + legacy doctrine.
- **A1 (D1) — borderline 5-day tiebreak.** New pure `flex.regime.resolve_quadrant`
  (basis `active`/`borderline_5d_tiebreak`/`favored_single`/`unresolved`) + collector
  `_build_flex_quadrant` → new `flex_quadrant` snapshot block (5d benchmark returns
  reuse the sleeve-scorecard closes cache — zero extra FMP calls; QQQ/XLI/GLD/TLT are
  pool members). The engine reads `flex_quadrant.resolved`, falling back to strict axes
  when absent (old snapshots). Exit logic unchanged (unknown quadrant never forces an
  exit). Prompt: `flex_quadrant` input + the `_SECTOR_QUADRANTS` map reproduced (fixes
  the 07-20 "NEE/XLU better in Q1/Q2" error). Window constant 5, not an env knob.
- **A2 (D3) — flex separation set.** Deleted the retired fixed-24 `CORE_TICKERS`; new
  `flex.regime.flex_separation_set(held)` (every role pool member + non-re-enterable
  legacy + any held legacy) and `FLEX_REENTERABLE = {INTC, MCK, PPA, EUAD}` per the
  quadrants.py doctrine. `_flex_nominations` now takes the broker-held set.
- **B1 (D2) — non-selected floors zeroed.** `_build_reference_weights` zeroes every
  non-selected pool member (SOXX/PAVE/XLB/GLDM/IAU/IHE/STIP/DBMF/CTA/SPLV/…); the
  selected incumbent keeps its floor, so a `selected` commit transfers it automatically.
  Kills the phantom `unclassified` bucket and the ~1.01% unfillable pad.
- **B2 (deferred finding 4) — earnings-calendar universe filter.** `_filter_earnings_to_universe`
  filters the market-wide FMP calendar to held ∪ selected ∪ flex candidates ∪ held legacy
  before writing `earnings_calendar` (GOOGL 07-22 was being missed).
- **B3 (deferred finding 7) — `functional_coverage` block.** Deterministic Table-B
  (each name in every quadrant its role covers; SGOV Q4+Q3; `sgov_note_inputs`), echoed
  verbatim (07-20/21 Table B arithmetic was broken).
- **B4 (new + 07-17 PCE precedent) — `growth_axis.as_of` + `freshness` block.**
  growth_axis emits `as_of` = the newest USED vintage row's realtime `asof` (vintage
  recency, not observation-quarter age — kills the GDPNow 3d↔81d flip); new `freshness`
  block dates every tracked series deterministically with a cadence-aware threshold and
  `convention` (observation_date vs vintage_date), echoed verbatim.
- **B5 (deferred finding 5) — `performance.excess_attribution`.** Two-term
  decomposition (cash vs invested contribution to the vs-SPY excess) for inception + 30d;
  the prompt must cite it for any excess attribution (the "cash drag" sign is routinely
  backwards — when SPY is negative, flat cash ADDS excess).
- **C1–C5 (prompt-only):** basis enums echoed verbatim (C1); gap-table renders every
  target∪held row incl. unheld targets like COWZ (C2); operative cash ceiling cited from
  `reference_weights`/`risk-limits` binding, shock-3⇒25%, not `recent_reports` — deferred
  finding 6 (C3); prior stated next-session intents adjudicated (C4); size-floored gap
  honesty — a tranche-min floor is not total impossibility (C5, the 07-21 XLV case).
- **Deferred 2026-07-13 findings 4–8 closed:** finding 4→B2, finding 5→B5, finding
  6→C3, finding 7→B3 (Table B; Table A's findings 7+8 arithmetic were already retired by
  the 07-17 Task D `quadrant_allocation` block). **#8 v2 (dynamic analyzer-emitted
  candidate list) stays open** — A1/A2 unblock the STATIC funnel (a seeded Q3/Q4 name can
  now clear G1 and reach entry); dynamic self-nomination is still future work.
- **Tests:** new `test_flex_quadrant_resolution.py`, `test_earnings_universe.py`,
  `test_functional_coverage.py`, `test_freshness.py`; rewrote `test_flex_separation.py`;
  extended `test_reference_weights.py`, `test_performance_block.py`, `test_daytrade_separation.py`.
  Full suite **690 green**, ruff clean; empirical probe on a constructed 2026-07-21
  snapshot passed (flex_quadrant→Q3, Utilities admitted, PPA survives, no unclassified
  mass, earnings filtered). **No auto-merge — human review before merge; D2/D3 flagged
  in the PR body for veto.**


- **2026-07-13** (PR #24, branch `fix/20260713-audit-price-universe-validator`, merged
  2026-07-13) — **2026-07-13 daily-report audit: price universe, intl-pool floor,
  off-roster validation seam.** The 07-13 report exposed three systemic gaps, all
  confirmed against the code and fixed:
  - **Finding 1 (HIGH) — reference buys were impossible, not deferred.** The collector's
    EOD price universe (`tickers + _ETF_WATCHLIST + flex_candidate_tickers`) never
    included an unheld role's `selected` incumbent (KMLM, IEF, VXUS, XLV, USMV, COWZ,
    VTIP, SMH, XLF — the names the Q3/Q4 underweights needed), so they had no price, no
    gap row, and band enforcement could never synthesize the buy. **Fixed (Task A):**
    new `shared/quadrants.selected_core_members()` + collector `_build_price_universe()`
    add every role's selected member to the fetch list. FMP `get_eod_prices` cost rose
    from ~29 to ~38 tickers/day (well inside the 250 req/day Starter budget — the
    alternative full-`CORE_ROSTER` universe would be ~46 tickers, also affordable, but
    the minimal selected-members version was shipped per the task's own preference).
  - **Finding 2 — non-selected `intl_leader` pool members (EWZ/VSS/IEMG/IDMO/EWJ)
    couldn't be unwound.** They're `CORE_ROSTER` but not `LEGACY_EXITS`, so V3 floor-
    clamped every attempted full exit to a 0.1%/1-share dust stub. **B0 decision (that
    session, per the task's explicit decision gate): Option 1 — allow sell-to-zero.**
    Rationale: the roster revision made intl leader-selective (only VXUS + `leader_pick`
    should be held), the reference already targets non-selected members at 0, and V1.5
    already blocks BUYING them — the sell-side floor bypass is the mirror image. A
    member can always come back later via a human `selected`/`leader_pick` commit.
    **Fixed (Task B1):** `trade_validation._non_selected_pool_member()` mirrors V1.5's
    role/leader_pick logic exactly; `floor_lb` is 0 for `LEGACY_EXITS` **or** a non-
    selected pool member, never keyed off `reference_pct == 0` (a selected out-of-favor
    name can legitimately show ref 0 and still owes its floor). Also fixed a cosmetic
    bug (**Task B2**, unconditional on B0): the sell-clamp math could compute a
    negative share count ("sell clamped 1→-1") when `cur` sat fractionally inside the
    floor epsilon — now floors at 0 with a clean "already at/below the window floor —
    nothing sellable" reason. **Task B3:** project-instructions.md now distinguishes
    "intl pool unwinds" (`[CORE — intl pool]`) from legacy exits — the 07-13 report had
    mislabeled these five names `[LEGACY EXIT]`.
  - **Finding 3 — off-roster held names (flex leftovers like MU) were invisible to the
    deterministic layer.** `_build_reference_gaps`'s universe excluded them, so (a)
    `_post_validation_cash` undercounted post-validation literal cash by the flex
    position's proceeds (07-13: printed ≈$4,597 vs a true ≈$6,440), and (b) an
    off-roster SELL skipped V3/V4 entirely and could reach the executor unvalidated.
    **Fixed:** Task C1 adds a paper-position `current_price` fallback to
    `_post_validation_cash` (gap-row price still wins when both exist); Task C2 makes
    `_build_reference_gaps` append a `reference_pct: 0.0, off_roster: True` row for
    every held off-roster name, priced via the existing position fallback — visible to
    the validator's sell-side V3/V4 checks (a full exit passes, an oversell clamps to
    held) but filtered out of `reconcile`'s working set (band enforcement must never
    synthesize a trade for a flex leftover — that's the flex engine's + human
    approval's job). Off-roster BUYs are unaffected (V1 already rejected them, still
    does with the row present).
  - **Shipped:** Tasks A, B (B0=Option 1, B1, B2, B3), C (C1, C2). Suite +19 tests
    (545→564), ruff clean.
- **2026-07-10** (branch `feat/quadrant-roles`) — **Roster revision v2: role-based core,
  exempt-hold retirement, international governance (Tasks A–H).** The core moved from a
  fixed 24-ticker list to ROLES with candidate pools (`sleeve-roles.json`); deterministic
  `sleeve_selection` scorecards propose member switches (human config-commit disposes).
  The AMZN/GOOGL exempt-hold doctrine is RETIRED (`EXEMPT_HOLDS=()`) → both are
  LEGACY_EXITS (target 0, tranche-liquidated, buys rejected; QQQ retains the exposure).
  International is now rotation/DXY-governed (`intl_governance`), leader-selective, with a
  gate modifier that HALVES (never zeroes) the leader tilt — this **resolves FOLLOWUPS #36**
  and deleted the interim suppress-to-zero rule. See `docs/specs/roster_revision_2026-07.md`.
  Tuning follow-up is #37.
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
