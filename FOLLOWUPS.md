# Follow-up items

Running backlog of known-open work. Newest context at top. When you pick an
item up, move it to **Done** with the date + commit so the history is visible.

**▶ START HERE — last session 2026-08-21, later same day (SWA regime-lean visibility: transition-lean rail on the performance chart, regime-call accountability scorecard, reachable-suspect badge, branch `feat/20260821-swa-lean-visibility`).**
See entry **#81** below for the full per-task design (Tasks A/B) — the
instrumentation that should land BEFORE decision **D-3** resolves, so the
first live joint lean (once it stages) is watched on the chart rather than
inferred from JSON. Includes a real premise correction found during
implementation (the SWA API has zero access to `risk-limits.json`, so
`suspect_path` needed a small collector addition, not a pure passthrough)
and a real regression the literal task spec would have missed (B1a: the
backfill guard's naive `"closes" in existing` check would have silently
never backfilled `lean` onto any pre-existing perf-series point). One new
low-priority tooling gap logged as entry **#82**. Suite 1330→1354, ruff
clean. **Auto-merge: NO, human review required.**

**▶ Prior session 2026-08-21 (quadrant selection reachability: diagonal projection, inflation-confirmation semantics, structural tape scores, per-axis divergence eligibility, reachable accountability, inert-lean diagnostic, branch `fix/20260821-quadrant-reachability`) — merged, PR #42.**
See entry **#77** below for the full per-task design (Tasks A/C/D/B/E/F) and the
required end-to-end probe showing exactly what decision **D-3** (entry **#78**)
buys — at the current default the reachability fix has nothing to compose with
(inflation side inactive, 1-of-3 sources); at the alternative it reaches Q2 as
designed. Two more open decision/deferral items from this cycle: **#79** (D-6
follow-on — suppress the inert lean once its live frequency is known) and
**#80** (GDPNow within-quarter smoothing, deferred, cross-refs #54). Suite
1291 → 1330 passed, ruff clean. **Auto-merge: NO, human review required.**

**▶ Prior session 2026-08-14 (flex-conviction-path cycle: a SECOND flex-nomination path requiring no dated catalyst, base-rate-relative sizing, `flex_eligibility`, applicable-set rankability, Layer 2 profile split, branch `feat/20260814-flex-conviction-path`).**
See entry **#70** below for the full per-task design (Tasks A0/B/C/D/E/F), the
corrected A0 probe premise (08-11 filed real nominations — the "5-session drought"
framing was wrong), the three unresolved decision gates (**#63** re-scoped/**#71**/
**#72**), and the two post-merge watch items (**#73**/**#74**). Two small prerequisite
fixes merged first, each its own PR, neither previously bookkept here until now: PR
#39 (**#68** — the M5 thematic-cash-drain fix this cycle was gated on) and PR #40
(**#69** — the flex-state stale-read bug the A0 probe surfaced as an unplanned
finding). Suite baseline (post-PR #40) 1176 → final 1272 passed, ruff clean.
**Auto-merge: NO, human review required.**

**▶ Prior session 2026-08-14, earlier same day (08-11→08-14 report audit: report-to-broker fidelity, oil signal correctness, transition_watch hysteresis, thematic conviction overlay, branch `feat/20260814-thematic-conviction-oil-fidelity`) — merged, PR #38 (`12e49fb`), plus two follow-up fixes PR #39/#40 (entries #68/#69).**
Four-task PR triggered by a chain of defects the 08-11→08-14 reports exposed: a 6-order
"Final trade plan" that collapsed to 1 submitted order with no warning anywhere (Task A);
`transition_watch` activating for one session on a stale FRED oil leg the axis itself had
already correctly bypassed in favor of a fresh USO proxy, blowing the Q2 reference block
~45x and reversing it the next day (Task B); the transition_watch mechanism itself having
no memory at all to prevent exactly that whipsaw (Task C); and four sessions of a live
Hormuz energy-supply theme narrated in prose with zero path to an actual position size,
pinning VDE at a 1-share 0.097% floor (Task D). **A0 blocking probe (done first, per
instruction) found Case 1 — LLM self-inconsistency, not validator rejection or executor
drop:** the 08-12 model's own `trades[]` JSON never contained the 6 narrated trades at all
(only a single band-enforcement-synthesized COWZ buy); `daily-trades.json` and
`daily-executions.json` matched each other exactly that day. This reclassifies which fix
matters most — A2's `plan_vs_submitted` (JSON-vs-broker) structurally CANNOT catch a
prose-vs-JSON divergence; A1's canonical addendum (rendered from `trades[]` itself, placed
directly under the narrative) is what actually closes the observed incident. **A**:
A1 canonical submitted-order addendum + a malformed-JSON debug-capture gap found during
the probe itself (App Insights retention had already expired, blocking full root-cause
certainty on WHY the JSON came up empty); A2 `plan_vs_submitted` (JSON-to-broker
fidelity, a complementary but different guard); A3 prompt hardening (the exact 08-12→08-13
false "executed as planned" reconciliation, closed with an anti-fabrication rule).
**B**: B1 divergence detector now reads `inflation_axis.oil_20d_pct_governing` (whichever
source the axis itself used) instead of the raw stale FRED leg directly — reconstructing
the 08-12 inputs (stale FRED 17.8% vs fresh USO 6.2%) now correctly yields `indeterminate`,
which is the whole fix; B2 real per-leg `as_of` + staleness gating on divergences 1/2/5
(the realized-inflation leg uses the freshness block's 45d monthly threshold, not the flat
7d daily one — a deliberate deviation from the literal 7d-everywhere reading, since core
CPI/PCE genuinely only refreshes monthly); B3 prompt doctrine requiring one governing oil
number, cited with source/as-of, every time. **C**: full confirm/release hysteresis (new
`TransitionWatchState` table, mirrors the existing `AxisDirectionState` pattern) plus a
per-session staged-fraction ramp — `confirm_sessions`/`release_sessions`=2,
`max_session_delta_frac`=0.10. **D**: the thematic conviction overlay — new
`thematic_conviction` snapshot block (collector-built scaffold: ladder, caps, eligibility,
per-symbol hysteresis via a new `ThematicConvictionState` table), integrated into
`_build_reference_weights` as a floor-lift (never a ceiling) carved from the
non-active-quadrant remainder, with `ceiling_pressure` surfacing when it doesn't fit; a new
`ThematicHistory` table + Brier-score/hit-rate calibration grading (`_stamp_thematic_outcomes`,
mirrors `_stamp_switch_outcomes`'s per-horizon pattern) that mechanically damps the ladder
when the model's own track record is poor. **Deviation from the literal spec, on record:**
D5's "insert as step 3c, before step 4" doesn't fit this codebase's actual units
(`raw_core`/`core_target` are %-of-CORE at that point; `thematic_conviction` targets are
%-of-EQUITY by design, and the core→equity `scale` factor isn't known until step 5) — the
floor-lift is applied immediately after step 5's equity-denominated `weights` dict instead,
verified empirically to produce the intended behavior (floor lift never reduces a
quadrant-driven weight; a large lift correctly trims the active quadrant and flags
`ceiling_pressure`). Suite 1026→1126 green (100 new tests), ruff clean, every new/modified
test confirmed failing on pre-fix source via `git stash` isolation on the specific changed
file. **Two decision gates for Jorge, unresolved — see entries #63/#64 below.**
**Auto-merge: NO, human review required.**

**▶ Prior session 2026-08-10, later same day (Flex Sleeve Performance Ledger + SWA panel, branch `feat/20260810-flex-performance-ledger`).**
Seven-task PR building the closed-trade record the sleeve never had — before this, all four close/realization paths (`time_stop`, `scale_out`, a broker stop fill via `reconcile_ledger`'s `exits_to_record`, and an entry that never actually filled) dropped the trade's outcome on the floor; `_record_trade_history` wrote a TradeHistory row with `extra={}` (no price, no P&L — an audit trail, not a performance record). **Task A**: new `src/flex/trades.py` (`closed-trade ledger + equity-series helpers, mirrors flex/ledger.py's I/O-plus-pure-builders pattern) + a single `_finalize_closed_trade` funnel in `flex/handler.py` every close path routes through. **Reconciliation is broker truth, not engine intent** — `merge_broker_fills` reconstructs the ACTUAL fill history from `AlpacaClient.get_activities("FILL", ...)` rather than trusting what the engine submitted; an entry with zero confirmed broker buy fills (the entry-side-failure case) writes NO closed-trade record at all — recording one would fabricate an entry price for shares never bought. `record_closed_trade` is idempotent on `trade_id` (generated once at open, carried through every fill — unlike `order_ids`, which DOES get overwritten on stop replacement). **A genuine bug caught by the empirical-verification doctrine, not by inspection:** an unpriced `extra_fill` (get_order hadn't confirmed a fill yet) was being folded into the "already accounted for" qty in `merge_broker_fills`, silently shadowing the broker's own later-confirmed price — `tests/test_flex_close_paths.py::test_time_stop_pnl_present_when_broker_confirms_both_fills` failed against the first cut of the fix and caught it before it shipped. **Task B**: `catalyst_score`/`score_components` stamped onto the ledger row at entry, read directly from the snapshot's `catalyst_screen.ledger` (catalyst-sleeve-funnel PR, entry **#57**) rather than trusting the model's nomination JSON to echo it back — the highest-value field in the schema for the eventual weight-tuning question (did `news_tone` actually predict anything?). **Task C**: `flex-ledger/equity-series.json`, upserted on every in-hours tick (decision gate 1: converges to "last successful tick" without needing to detect which tick is last). **Task D**: `/api/performance` gains `sleeve_contribution_pp`/`sleeve_trade_count` per point plus top-level `sleeve_available`/`sleeve_closed_trade_count_total` — cumulative from the WINDOW START, deliberately never normalized to a start=100 buy-and-hold index (the sleeve is intermittently deployed and flat much of the time; indexing it would render opening a position as a "return"). **Task E**: a second `<canvas>` on `web/performance.html` (never a 7th dataset — different units), sharing the x-axis/window selector/`regimeBands` plugin; color `#b8348f`, validated via the dataviz skill's `validate_palette.js` against the panel surface and all 6 existing series colors (contrast ≥3:1 on every pair, CVD ΔE clean-PASS ≥8 on every pair — worst is 9.5 vs Q1 green, short of the ≥13 stated target but a real PASS not a WARN; normal-vision ΔE ≥15 on every pair — full numbers in the PR body). Below N=30 closed trades the line renders dashed/greyed with a visible caption; no Sharpe/win-rate/skill stat anywhere on the panel at any N (decision gate 3: 30 is proposed, not confirmed). **Task F**: `_OUTCOME_HORIZONS=[30,60,90]`/`_HEADLINE_HORIZON=60` are wrong for a 5-day-time-stop sleeve (a catalyst trade is closed and gone before its first outcome stamp matures) — recorded as new entry **#61**, NOT redesigned this cycle; flex TradeHistory rows were already correctly `layer: "flex"` tagged (`_record_trade_history` hardcodes it) so no code change was needed there, just confirmation. Suite 983→1024 green (41 new tests across 4 new test files), ruff clean, every new test confirmed failing on pre-fix source (`git stash` isolation on `src/flex/handler.py`+`src/flex/ledger.py`+`web/api/function_app.py`; `src/flex/trades.py` via import-level failure). **Both counts are measured, not carried forward — see the CLAUDE.md baseline-measurement rule this correction added; the original 979/45 figures were copied from entry #57's own (also wrong) 979, propagating one bad number into a second.** **Three decision gates surfaced in the PR body, unresolved:** (1) daily mark timing — last-in-hours-tick chosen and implemented, not yet confirmed; (2) no backfill — the ledger starts empty at first write, an explicit inception date on the panel rather than reconstructing from incomplete `TradeHistory`; (3) N=30 sample-size floor, proposed not confirmed. **Auto-merge: NO, human review required.**

**▶ Prior session 2026-08-10 (Catalyst Sleeve Funnel: candidate discovery + scoring, branch `feat/20260810-catalyst-sleeve-funnel`) — merged, PR #36.**
Six-task PR closing the three structural gaps that meant the Flex Catalyst Engine could only ever trade names it already knew about (G1 market-wide earnings calendar discarded outright, G2 news fetch held-only, G3 self-referential candidate pool) plus the `regime_fit` hard-veto demotion. See entry **#57** below for the full per-task design.

**▶ Prior session 2026-08-06 (08-03/04/05 report audit: market_implied_quadrant votes/confidence, market_shock z-score, DXY fallback, deployable-envelope shortfalls, day-P/L identity trigger, override falsifier adjudication, inflation/oil/labor signal freshness, hybrid band, MU ingestion guard, branch `fix/20260806-signal-integrity-audit`).**
13-task PR (B1+B2, B3, B4, B6, B7, M1+M2, O1, O2, O3, O4, R1) fixing dead/miscalibrated deterministic signals the LLM was reasoning against every session — a strongly-wired 2-vote basket pair swinging `market_implied_quadrant` confidence to `high` while 6 other votes were structurally null (B1+B2); a persistent news theme alone pinning `market_shock` at level 3 for weeks on a benign tape, lifting the cash ceiling to 25% (B3); the DXY dollar-proxy fallback dark at exactly 5d stale AND whenever DTWEXBGS returns zero observations at all (B4); every sleeve in a multi-sleeve de-cash program flagged `non_compliant_flagged` even when the model deployed its full aggregate tranche pro-rata (B6); a frozen-quote day-P/L symptom slipping through on small positions because the old trigger required a large delta too (B7); a filed override's falsifier never adjudicated on a later run, and an identical sub-min-notional setup flip-flopping between hold and floor-sell with no override either day (M1+M2); a 60-65d inflation-data blind spot with no bridge (O1); an 8-9d-stale FRED oil series on the exact channel that flips the inflation axis (O2); an ADP miss caught only via a forex-news parse (O3); a small strategic target (VXUS) permanently inside the fixed absolute band, never funded (O4); and MU printing ~10x its real price every session, merely re-quarantined never corrected, plus a closed position's historical P&L narrated as an ongoing drag (R1). Two items deferred by explicit decision: B5 (growth-axis recency slope) and R2 (Risk Score sensitivity, investigation stub only) — see entries **#54**/**#55**; R1's strategy half (what to actually do about the MU position) is Jorge's call, tracked separately as entry **#56**. Suite 901→922 green, ruff clean, every new/modified test confirmed failing on pre-fix source via `git stash` isolation. **Auto-merge: NO, human review required** — see entry **#53** below for the full per-task design.

**▶ Prior session 2026-08-01 (07-30/07-31 report audit: pass-1 clamp visibility, SGOV carve-out reconciliation, zero-watch resize awareness, cash-ceiling deployment doctrine, report hygiene, branch `fix/20260801-clamp-visibility-cash-deadlock`).**
Five-task PR: A (pass-1 Tier-1 clamps now survive pass 2 instead of silently reverting to "passed" — the KMLM 182→179 clamp vanished from the combined summary and the report addendum never fired), B (decision D-B1: `reconcile()` no longer flags the sanctioned literal-cash→SGOV carve-out sweep as the model having "traded AWAY" from reference), C (`_build_day_pl_zero_watch` gains share-count awareness so a resize — a sale/buy — no longer misflags as a P/L anomaly, while the genuine frozen-quote signal is kept), D (decision D-D1, prompt-only: a cash sleeve stuck above its operative ceiling makes in-band underweight sleeves discretionarily buy-eligible, breaking the ~30pp stranded-cash deadlock), E (7-item prompt-hygiene batch: floor-shares arithmetic, post-trade cash formula, prior-session flex-nomination adjudication, trade-direction vocabulary, symmetric catalyst-date discipline, no visible deliberation chatter, symmetric oil-overlay wording). **PR #33 review round (same day, same branch):** M1 fixed a Task B regression (the carve-out exclusion applied regardless of gap direction, zeroing out a genuine underweight-SGOV sweep's credit and triggering a redundant synthesized buy) and M2 fixed a Task D trigger that could never fire (`cash_sleeve_target_pct` is ceiling-clamped by construction — reworded to key on `reference_weights.binding`'s `cash_above_band` flag). Suite 842→861 green, ruff clean, every new/modified test confirmed failing on pre-fix source via `git stash` isolation. **Auto-merge: NO, human review required** — see entry **#50** below for the full design, both original decision gates (D-B1, D-D1), and the review-round addendum.

**▶ Prior session 2026-07-28 (axis confirmation + F1 fix + report hygiene + KMLM diagnostics, branch `fix/20260728-axis-confirm-f1-hygiene`).**
Five-task PR: A (axis-direction N=2 confirmation + GDPNow rolloff diagnostic + policy-stance confirmation), B (F1 series-deltas weekend-walkback fix — net-new, the previously-named fix branch was never created), C (5 prompt-hygiene edits: gate labeling precision, SGOV sweep budget, Recommended Weight provenance, axis-flip adjudication, dashboard stale count), D (KMLM day-P/L zero-watch diagnostics only), E (analyzer `effective_selected` failure-day hardening from the #47 merge audit). Suite 817→842 green, ruff clean. **Auto-merge: NO, human review required** — see entry **#49** below for the full design, decision gates (D-A1/D-A2/D-D1/D-1a), and the FOMC 07-29 ships-hot timing note (the market-implied policy-stance confirmation needs 2 runs post-FOMC to move the gate unless the manual `fomc_stance` file is refreshed same-day).

**▶ Prior session 2026-07-27 (blanket autonomous sleeve switching, branch `feat/20260727-sleeve-auto-switch`) — MERGED + DEPLOYED, PR #31.**
Scorecard role switches are now fully autonomous — a `switch_signal` on an unpinned role auto-advances the effective incumbent via `SleeveSelectionState`, mirroring the existing `intl_leader` pattern. `sleeve-roles.json`'s `selected` is now the baseline/pin, not the live authority. Suite 805→817 green, ruff clean. Ships-hot prediction **confirmed live** on the 2026-07-28 run: semis SMH→SOXX and healthcare_def XLV→IHE both auto-fired at streak 12 exactly as designed (reference weights reproduced to the third decimal, XLV sold to zero via the D-G1 floor bypass, SOXX buy correctly rejected by the effective-aware amplifier gate). See entry **#47** below and the **#48** update for the empirical verification.

**▶ Prior session 2026-07-23 (regime responsiveness cycle, branch `feat/20260723-leading-growth-market-implied`).**
Tasks: A (#17 leading-growth composite + growth-side transition_watch), B (#18 market_implied_quadrant + daily dollar proxy), C (pnl_decomposition inception-shortfall block), D (F6 sweep sizing / cash-floor guard), E (F7 price-sanity quarantine), F (F8 A4 watch_candidates wording). Suite 721→789 green, ruff clean. **Merged to master** (see entry **#46** below — its features are live in the 2026-07-24 report).

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

### 82. `scripts/validate_palette.js` referenced by `web/performance.js` but absent from the repo (LOW — tooling gap, cross-refs #81)
`web/performance.js`'s file-header comment (originally added for the Flex
Sleeve Performance Ledger's `SLEEVE_COLOR`, session 2026-08-10) documents a
palette-validation standard — contrast ≥3:1 and CVD ΔE ≥8 against the panel
surface and every existing series color — and says it was "validated with
`scripts/validate_palette.js`." That script does not exist anywhere in this
repo; it was referenced but never committed. The 2026-08-21 SWA
lean-visibility cycle (entry **#81**) hit this gap directly (Task B3a) and
deliberately sidestepped it by reusing existing QCOLOR/QTINT hues at a
different opacity/treatment for the transition-lean rail, rather than
introducing a new color that would need validation this script doesn't
exist to run. Either write the script for real (a small contrast +
CVD-simulated-ΔE checker, per the `dataviz` skill's stated method) or stop
citing it in comments until it exists — a comment claiming a validation ran
that cannot actually be re-run is worse than no claim at all.

### 61. Sleeve-appropriate Phase C grading — realized R-multiple + component regression, not calendar drift (MEDIUM — data-gated, cross-refs #23 + #58)
From the 2026-08-10 Flex Sleeve Performance Ledger session (entry **#60**),
Task F — recorded, deliberately NOT fixed this cycle (redesigning Phase C
grading was explicitly out of scope for that PR). `collector/handler.py`'s
`_OUTCOME_HORIZONS = [30, 60, 90]` / `_HEADLINE_HORIZON = 60` stamp a
recommendation's outcome vs SPY at 30/60/90 calendar days out — a design
built for the CORE book's monthly/event cadence. The flex catalyst sleeve has
a hard 5-day time stop (`FlexConfig.time_stop_days`): a trade is closed and
gone long before its first outcome stamp (30d) even matures. Stamping a
60-day market drift onto a position that was held 4 days measures the
market's subsequent wander, not the quality of the decision — the horizon
mismatch means every flex row's `outcome_status`/`resolved_correct` (once
stamped) is measuring the wrong thing entirely, not just imprecisely.
- **What "sleeve-appropriate" means, concretely:** grade a closed flex trade
  on its OWN realized `r_multiple` at `closed_date` (already captured by
  `flex.trades.build_closed_trade`, entry **#60**) — the trade's actual
  outcome, at the actual time it ended — not a fixed-calendar-horizon
  drift stamped later. The `catalyst_score` weight-tuning question entry
  **#58** exists to ask ("did `news_tone` actually predict anything") is a
  component-vs-realized-r_multiple regression over `closed-trades.json`
  rows, NOT anything routed through `_OUTCOME_HORIZONS`.
- **Prereqs:** a meaningful sample of closed-trades rows (same evidence bar
  as #58 — do not build a grading pipeline to run on a handful of trades);
  ideally after the #23 point-in-time backtest harness exists so a proposed
  grading change can be validated against history rather than against
  whatever this cycle happened to produce.
- **Acceptance when picked up:** flex rows segregate cleanly by `layer:
  "flex"` (already true, verified this session — `_record_trade_history`
  hardcodes it, no change needed) from core `layer: "core"` rows in whatever
  new grading path is built; the existing `_OUTCOME_HORIZONS` core-book
  grading is UNTOUCHED (this is an additive sleeve-specific path, not a
  replacement); a human-reviewed PR per the Learning Loop's proposer≠approver
  invariant if the grading feeds any autonomous decision.
- **Related but NOT a resolution (2026-08-14, entry #70):** the flex-
  conviction-path cycle added a SEPARATE, sleeve-appropriate grading track
  (`ThematicHistory` rows tagged `path: "flex_conviction"`, resolved at each
  nomination's OWN `horizon_days` rather than a fixed 30/60/90 ladder) — but
  this grades the p_up PREDICTION (Brier/hit-rate calibration for the
  conviction-path nomination itself), not a closed trade's realized
  `r_multiple`. It does not touch `_OUTCOME_HORIZONS`/`TradeHistory` at all,
  so this item (the catalyst path's calendar-drift mismatch) remains fully
  open and unaddressed.

### 63. Ladder numbers — pending Jorge's sanity check, NOW COVERS TWO LADDERS (HIGH — sizing decision, cross-refs #64/#71/#72)
From the 2026-08-14 session (entry above). The `p_up_min` band edges and
`target_pct_of_equity` values in `risk-limits.json → thematic_conviction.ladder`
(0.52/0.58/0.65/0.75 → 0.50/1.25/2.5/4.0pp) and the `per_ticker_cap_pct_of_equity`
(4.0)/`aggregate_cap_pct_of_equity` (8.0)/`max_session_delta_pp` (1.5) figures are a
**starting proposal, not yet reviewed** — flagged explicitly in the PR description per
instruction. They were chosen to mirror the shape of the existing
`conviction_ladder_pct_of_core` ladder (coarse, monotonic bands) but the actual pp
values are a judgment call about how much capital a thematic conviction call should
be allowed to move, which is Jorge's to make, not inferred from precedent.

**Re-scoped 2026-08-14 (flex-conviction-path cycle, entry #70) — this item now covers
a SECOND ladder too.** `risk-limits.json → conviction.ladder` (`edge_min` 0.0/0.04/
0.07/0.12/0.18 → `size_mult` 0.0/0.25/0.45/0.70/1.00) is the SAME kind of unreviewed
starting proposal, for the flex sleeve's conviction path — see entry #70 for the full
design. It has the same base shape (coarse, monotonic bands) but is NOT the same
numbers as the thematic ladder (different units — `size_mult` scales an existing
risk-budget/cap chain, not a direct %-of-equity target — and a different domain,
`edge` = p_up minus an empirically-measured base rate, not raw `p_up`). Review both
together — they share a design lineage but must not be assumed to share numbers.
`base_rate_lookback_days` (504) and `base_rate_min_windows` (60) are also unreviewed
— see entry #70 for why 504/60 were chosen (roughly 2 trading years; ~60 overlapping
windows was judged the minimum for a stable empirical fraction, not derived from any
formal significance test — there is no backtest harness, per #23, to validate this
against). `catalyst_size_mult` (1.5) and `catalyst_promotes_band` (both on by
default) are covered separately — see entry #71 (roughly a 2x combined effect when
both fire together).

**BLOCKED on entry #75 (PR #41 review, M-A) for the `conviction.ladder` half only.**
Every band currently clamps to the SAME share count on a thin-literal-cash day (the
cash-accommodation funding pool collapse) — sanity-checking `size_mult` values against
a symptom that can't reach the book yet would be reviewing numbers in a vacuum.
Resolve #75 first, then revisit this ladder's numbers. The `thematic_conviction.ladder`
half is unaffected and can be reviewed independently.

### 64. D-6 decision gate — thematic lift on a non-selected pool member (HIGH — architecture decision, cross-refs #63)
From the 2026-08-14 session. Task D4 rule 3 excludes a thematic nomination on a
non-selected pool member (e.g. SOXX while `semis.selected = SMH`) with reason
`non_selected_pool_member`, because `_build_reference_weights` step 4 zeroes every
non-selected pool member by design (B1/decision D2, 2026-07-21) and a thematic lift
would fight that loop, producing a permanently unfillable reference.
- **Option A (implemented, current default):** the exclusion stands as specified — a
  theme pointing at a non-selected pool member surfaces in `thematic_conviction.excluded`
  with the reason, and the correct response is understood to be a sleeve-selection
  switch (already autonomous per the 2026-07-27 blanket-switch doctrine), not a
  thematic lift.
- **Option B (not built):** let a thematic nomination on a non-selected pool member
  *force* a sleeve-selection switch for that role, bypassing the existing hysteresis
  streak/lead-margin gate that `sleeve_selection`/`SleeveSelectionState` already
  enforces. This is a materially larger change — it would let the LLM's own
  probability estimate override the deterministic momentum-blend selection scorecard
  for a role, with real interaction effects against PR #31's auto-switch machinery
  (which sleeve-selection state field wins, does a thematic-forced switch reset the
  hysteresis streak, does it get its own OverrideHistory layer for grading). Deliberately
  not built this cycle — present both options, let Jorge choose before ever considering
  Option B.

### 65. Post-merge watch — confirm `plan_vs_submitted` renders on the first live mismatch (LOW — verification only, cross-refs #62)
From the 2026-08-14 session's Task A2. The mismatch/qty_mismatch/extra_in_submission
classification logic is unit-tested against hand-built fixtures (`test_plan_vs_submitted.py`)
but has never observed a REAL divergence in production — the 08-12 incident that
motivated it turned out to be a Case-1 prose/JSON split that `plan_vs_submitted`
structurally cannot see (see the A0 probe finding above). Watch the next several
`execution_review.plan_vs_submitted` blocks in live snapshots; the first genuine
`status: "mismatch"` (a real qty clamp mid-execution, a genuine executor drop) is the
first real-world confirmation that the Data Integrity Warning surfacing (A3) actually
fires as designed end-to-end, not just in unit tests.

### 66. Post-merge watch — first thematic nomination reaching `confirm_sessions` (LOW — verification only, cross-refs #63/#64)
From the 2026-08-14 session's Task D. The per-symbol hysteresis
(`_confirm_thematic_entry`) and the reference_weights floor-lift integration are both
unit-tested against hand-built fixtures, but the FULL round trip — a real LLM-emitted
`thematic_conviction[]` nomination surviving one session's lag, confirming after
`confirm_sessions`=2 consecutive matching nominations, and actually appearing as a
floor-lift in a live `reference_weights.thematic_lean` — has never been observed
end-to-end. Watch for the first live activation and verify: the applied percentage
ramped in at `max_session_delta_pp`=1.5pp rather than jumping, the lifted symbol's
`target_weights_pct` actually moved, and no unexpected `ceiling_pressure` fired on a
modest single-name lift.

### 67. Periodic quadrant roster review — semi-annual out-of-band task (MEDIUM — architecture, data-gated on screening universe)
**Decision (2026-08-14, Jorge).** AI-chosen core selection is **rejected for the
daily path.** The core roster stays deterministic session-to-session and
`CORE_ROSTER` / `sleeve-roles.json` remain the authoritative source every
consumer resolves through. Instead, the "is this still the best ticker for this
quadrant?" question moves to a **separate task that runs twice a year** (interval
6 or 12 months — TBD, see decision gate below).

**Why out-of-band rather than daily.** A roster question is a slow-moving
decision and the daily loop is where whipsaw risk lives. Three separate
one-session flip-flops were found in two audit cycles (VDE reference 0.091% →
4.118% → 0.091%, `transition_watch` on/off, thematic jitter) — every one caused
by a fast signal driving a slow allocation without hysteresis. Daily LLM
re-selection of core would be the maximal version of that failure mode, applied
to the All-Weather backbone. It also contradicts two standing principles:
"24 CORE, weight-only changes, never sold to zero" and "new tickers enter only
via FLEX."

Running it semi-annually gives the review a horizon long enough for performance
evidence to mean something, and keeps the daily book deterministic.

**Scope of the review task.** For each quadrant (Q1/Q2/Q3/Q4) and each role:
- Assemble a candidate universe for the quadrant (see data gap below).
- Rank the incumbent against candidates on trailing performance, expense ratio,
  liquidity/ADV, tracking consistency, and quadrant fit.
- Emit a **ranked proposal**, not a trade. Output is a structured amendment
  proposal in the same shape the Learning Loop already uses (#40-adjacent) —
  reviewed by Jorge, applied via a `sleeve-roles.json` PR, never auto-applied.
- Any adopted change enters the roster as a **pool addition** and is then subject
  to the existing streak-based auto-switch machinery (PR #31,
  `feat/20260727-sleeve-auto-switch`), so adoption still requires the N-of-10
  streak. The review proposes candidates; it does not hot-swap incumbents.

**Data gap (blocking — must be resolved before implementation).** FMP Starter has
**no company-screener endpoint**. Wired endpoints are: `profile`,
`batch-quote-short`, `historical-price-eod`, `earnings-calendar`, `news/stock`,
`etf/holdings`, `etf/country-weightings`, `etf/sector-weightings`, senate/house
trades, `discounted-cash-flow`, `ratings-snapshot`. Today's discovery universe
(earnings calendar + congressional flow + news) is not a quadrant screen.

Feasible path with endpoints already paid for: **quadrant → representative ETF →
`etf/holdings` look-through → candidate universe**, ranked with
`discounted-cash-flow` and `ratings-snapshot`. Because this runs twice a year
rather than daily, the 250 req/day FMP budget is a much softer constraint than it
would be for a daily screen — a single review run can afford a wide look-through.
This is the main practical argument for the semi-annual cadence beyond whipsaw
avoidance.

**Decision gates (for Jorge, at implementation time):**
- **67a — Interval:** 6 months or 12 months. 6mo gives more adaptation and more
  chances to churn a working roster; 12mo is more conservative and matches the
  "process discipline over outcome-chasing" principle. No recommendation yet —
  wants at least one full cycle of Phase C grading data to inform it.
- **67b — Trigger:** calendar-scheduled (e.g. every January/July) vs. manually
  invoked by Jorge. Calendar is auditable and unmissable; manual avoids running a
  review during a regime the book is mid-transition through.
- **67c — Scope per run:** all four quadrants in one run, or one quadrant per
  quarter on a rotation. Rotation spreads the API cost and the review burden.

**Explicitly out of scope for this item:**
- Off-quadrant sleeve picks and the `role_quadrant_drift` accounting — separate
  item, needs `primary_quadrant()` to bucket by ticker rather than role or Table A
  silently desynchronizes (2026-07-09 "Q3 42.9% vs footnote 58%" failure class).
- `LEGACY_EXITS` doctrine — unchanged. A legacy exit is not a roster-review
  candidate for core under any interval.
- The conviction-path flex sizing work (probability + catalyst amplifier) — that
  is a separate cycle and must not ride along with a data-layer change.

**Sequencing.** Do not start until PR #38's M5 (thematic lift draining literal
cash below `literal_cash_floor_pct`) is closed and merged. **Satisfied** — see
entry **#68** (M5 merged, PR #39, `dc20130`). This item is otherwise unrelated to
the flex-conviction-path cycle (entry **#70**) that also depended on M5 — they
share a prerequisite, not a scope.

### 71. G-2 decision gate — catalyst amplifier's combined effect on the flex conviction path (HIGH — sizing decision, cross-refs #63/#70)
From the 2026-08-14 flex-conviction-path session (entry **#70**). `risk-limits.json →
conviction.catalyst_size_mult` (1.5) and `conviction.catalyst_promotes_band` (`true`)
are BOTH on by default and compose (see `_conviction_catalyst_amplifier` — the
promoted band's own `size_mult` is the multiplication base, not the original band's),
so a conviction nomination that also carries a real dated catalyst within
`horizon_days` gets roughly a **2x** combined size effect (one band promotion,
typically ~1.4-1.6x on its own depending which two adjacent rungs, THEN x1.5 again) —
never audited against how large a same-session swing that could produce on a single
flex name relative to the `per_name_cap_pct`/`sleeve_cap_pct` backstops it still runs
through. Present, not decided: (a) keep both defaults as specified, (b) turn off
`catalyst_promotes_band` and keep only the size_mult multiplier (roughly halves the
combined effect), (c) lower `catalyst_size_mult` toward ~1.2. No recommendation —
this is a risk-appetite call, not a correctness bug; the existing per-name/sleeve
caps still bind regardless of which option is chosen.

### 72. G-3 decision gate — momentum/relative-strength protection deliberately absent from the conviction path (MEDIUM — architecture decision, cross-refs #70)
From the 2026-08-14 flex-conviction-path session (entry **#70**). The catalyst path's
Layer 2 (`build_flex_entry`) requires the entry to be above a RISING session VWAP
(`above_vwap` + `vwap_slope > 0`) before triggering — a momentum/price-confirmation
gate. The conviction path's Layer 2 (`build_conviction_entry`) deliberately has NO
such gate — only a "no-chase" ceiling (`entry_price <= vwap + conviction_no_chase_atr
× ATR`) that prevents buying too far ABOVE the day's VWAP, but nothing that requires
the name to be showing ANY positive intraday momentum at all. This was a deliberate
design choice (a multi-week thesis should not need today's tape to already agree with
it — the entire point of this path is entering EARLY, before a catalyst-style
momentum confirmation would exist), but it was never explicitly weighed against the
alternative of requiring at least a neutral-or-better intraday read. Present, not
decided: (a) leave as specified — no momentum gate, only the no-chase ceiling: (b)
add a soft floor (e.g. `entry_price >= vwap - N × ATR`, rejecting an entry into an
actively-falling tape even before the no-chase ceiling would ever bind) — this would
be a NEW third gate, not a reuse of the catalyst profile's rising-VWAP requirement,
since requiring RISING momentum would defeat the "enter early" design goal entirely.
No recommendation.

### 73. Post-merge watch — first flex-conviction nomination reaching `confirm_sessions` (LOW — verification only, cross-refs #70)
From the 2026-08-14 flex-conviction-path session (entry **#70**). Mirrors entry
**#66**'s thematic-conviction watch item, for the sibling flex path. The per-symbol
hysteresis (`_confirm_flex_conviction_entry`) and the `flex_conviction` snapshot
block are both unit-tested against hand-built fixtures, but the FULL round trip — a
real LLM-emitted `path: "conviction"` nomination surviving one session's lag,
computing a real `base_rate_up` off live price history, confirming after
`confirm_sessions`=2 consecutive matching nominations, and appearing in
`flex_conviction.active[]` — has never been observed end-to-end. Watch for the first
live confirmation and verify: `base_rate_up`/`edge` land in a sane range (base rate
roughly 0.5-0.6 for a normal equity name over 15-30d), the ladder lookup lands on the
expected band, and the applied `size_mult` ramped in at `max_session_delta_pct_of_
equity`=1.5 rather than jumping.

### 74. Post-merge watch — first flex-conviction entry through Layer 2, and the release-driven exit (LOW — verification only, cross-refs #70/#73)
From the 2026-08-14 flex-conviction-path session (entry **#70**). Once entry **#73**
confirms a live active conviction nomination, the NEXT unobserved step is the actual
`build_conviction_entry` firing intraday: the no-chase limit passing, the
invalidation-level stop sizing correctly (bounded by `conviction_max_stop_pct`=10.0),
and B5's cash-accommodation clamp behaving correctly against real Alpaca cash/SGOV
figures (never observed against a live account, only hand-built fixtures in
`test_flex_conviction_entry.py`/`test_flex_conviction_wiring.py`). Separately, watch
for the FIRST release-driven exit (`_act_on_release_exit` firing because a held
conviction position's symbol dropped out of `flex_conviction.active[]`) — confirm it
sells the full remaining `qty_current`, cancels the resting stop first, and writes a
`conviction_released`-tagged closed-trade record exactly like the existing
`time_stop`/`scale_out` paths (never observed live; unit-tested against a fake
client only).

### 75. M-A decision gate — the flex conviction path's cash-accommodation clamp collapses the entire ladder (HIGH — architecture decision, blocking, cross-refs #63/#70)
From the PR #41 review round (M-A, blocking finding). `_cash_accommodation_
shares` (`flex/entry.py`) funds a conviction entry from `min(literal_room,
sleeve_room)` — but the flex engine has NO mechanism to sell SGOV to realize
`sleeve_room`: SGOV is a permanent member of `flex_separation_set` (the same
separation contract that keeps flex from ever touching a core pool member),
and there is no same-session cross-engine trade path from the ~15-min
intraday flex tick to the once-daily core executor (09:35 ET). Confirmed via
a blocking sub-probe before any fix was attempted, per instruction.

**Consequence, reproduced empirically against a representative book**
(equity ~$101,922, literal cash ~$1,499 [1.47%], SGOV ~$7,828 [7.68%]):
`literal_room` ($735) is smaller than every conviction band's pre-clamp
notional and is the ONLY term that ever binds, so **every band — low/
moderate/high/very_high — collapses to the identical clamped share count**
(7 shares / $700 in the reproduction). The entire ladder — base rates, edge
computation, hysteresis, the catalyst amplifier — resolves to one number
whenever literal cash is this thin. Pinned as a known-limitation test
(`tests/test_flex_conviction_entry.py::
test_KNOWN_LIMITATION_ladder_collapses_to_one_share_count_thin_literal_cash`)
so a future session doesn't assume this already works; the eventual fix must
consciously update or delete that test and close this entry.

**Deliberately NOT fixed this cycle** — redesigning the funding pool is a
real architecture decision, not something to infer silently (per explicit
instruction: "stop and report" rather than re-scope on its own authority).
Three real options, presented without a recommendation:
- **Option 1 — cross-engine SGOV pre-fund.** A new, ONCE-DAILY, core-side
  step (NOT same-session, NOT flex-triggered) that sweeps a small SGOV
  amount to literal cash BEFORE the flex engine's morning entry window,
  whenever `flex_conviction.active[]` carries a nonzero-target nomination.
  Preserves the Separation Contract (core still only trades on its own daily
  cadence; flex still never places an SGOV order itself) but is real new
  scope — needs its own design pass (who decides the sweep size, does it
  compete with the core SGOV carve-out doctrine already in `risk-limits.json
  → cash_sleeve_band_pct`, what happens on a day the core executor is
  skipped).
- **Option 2 — re-scope the ladder to fit literal-cash-only funding.**
  Shrink `conviction.ladder`'s `size_mult` values (or `risk_budget_pct`) so a
  `very_high` band's pre-clamp notional realistically fits inside a typical
  literal-cash balance without needing SGOV capacity at all. Simplest, but
  makes the conviction path materially smaller than currently designed, and
  changes the SAME numbers already flagged unreviewed in entry #63 above —
  do not sanity-check #63's ladder numbers until this is resolved, since
  every band clamps to the same value regardless of what they're set to.
- **Option 3 — accept the collapse as a rare/thin-cash-day edge case.** If
  literal cash is routinely NOT this thin in practice (the reproduction used
  a specific historical snapshot), the collapse may be uncommon enough that
  the existing clamp — now with the visibility fix (`binding: "cash_floor"`
  narrated explicitly, S-2's sibling M-A "also fix") — is an acceptable
  interim state. Needs empirical data on how often literal cash sits below
  the threshold where every band starts colliding, which does not exist yet.

**What WAS fixed this cycle (independent of the funding-pool decision):**
`_cash_accommodation_shares` renamed from `cash_accommodation_shares` (now
correctly private, matching `_size_conviction_position`'s convention — it has
no external callers outside `flex/entry.py` itself and tests). Prompt
doctrine now requires narrating `binding == "cash_floor"` explicitly whenever
it fires, whether the resulting position is zero or nonzero shares (closes
the "silent size reduction" visibility gap the M5 incident already
established as this project's recurring failure mode).

### 76. Process item — "what else is in this collection?" PR review gate (MEDIUM — process, cross-refs #62/#68/#70)
From the PR #41 review round. Six instances of ONE pattern across two cycles:
a fix correct about the thing it changed, wrong about the collection it
joined.
1. PR #38 — a ceiling-pressure test used 40pp where the real cap was 8.
2. PR #38 — a pool-member test passed an override map where production
   passes an empty one.
3. PR #38 — a swept invariant summed a total that excluded the exact field
   being drained.
4. PR #39 — the M1 exclusion list named `"SGOV"` and missed its sibling
   `"__cash__"`.
5. PR #40 — clean (no instance).
6. PR #41 — `path` was added to the two `ThematicHistory` writers and the two
   NEW query call sites, but missed on the two PRE-EXISTING queries
   (`_stamp_thematic_outcomes`, `_build_thematic_calibration`) — fixed as M-B
   in this same PR.

Each fix was correct about the thing it changed and wrong about the
collection it joined. The code has improved every round while the pattern
held — not a competence issue, a missing review step. **Add to the PR
template as an explicit gate:**

> **What else is in this collection, and did I check all of it?**
> List every member of any list, dict, table, or query set this change
> touches, and state for each whether it was verified.

For M-B specifically, the full inventory of every
`query_entities("ThematicHistory"...)` call site (there were exactly four)
and its filter, as this gate would have required:
1. `_stamp_thematic_outcomes` — was UNFILTERED (the bug) → now
   `path eq 'core_thematic'`.
2. `_build_thematic_calibration` — was `outcome_status eq 'resolved'` only
   (the bug) → now `path eq 'core_thematic' and outcome_status eq
   'resolved'`.
3. `_stamp_flex_conviction_outcomes` — already correct,
   `path eq 'flex_conviction'`.
4. `_build_flex_conviction_calibration` — already correct,
   `path eq 'flex_conviction' and outcome_status eq 'resolved'`.

### 78. D-3 decision gate — `re_risk_min_confirmations` 2-of-3 vs 1-of-3 for the inflation side (HIGH — sizing/sensitivity decision, cross-refs #77)
From the 2026-08-21 quadrant-selection-reachability cycle (entry **#77**).
Task B added a THIRD confirmation source (`market_implied_quadrant.
structural_inflation_score`) to the inflation side's re-risk bar, but kept
`re_risk_min_confirmations` at its existing value of 2 — now counted against
3 possible sources instead of 2. **Empirically verified via the required
end-to-end probe (entry #77's PR body): at `2`, the CURRENT 2026-08-21
readings give the inflation side only 1 of 3 (breakeven/oil both below their
own thresholds; only the tape agrees) — it does not activate at all, so
Task A's joint (diagonal) composition never gets a second re-risk side to
compose with, and the system stays projecting Q1 (growth-only, orthogonal).
At `1`, the inflation side activates (Q3, re-risk) alongside the growth side
(Q1, re-risk) and Task A composes the diagonal correctly to Q2.** This is the
literal difference between "the reachability fix ships but is currently
inert" and "the reachability fix actually reaches Q2 in the observed
window." Recommendation on record (the prompt's own): lean `1`, but only
*with* Task C's basket-momentum-exclusion fix shipped (it is) — mitigating
factors: `confirm_sessions=2` and `max_session_delta_frac=0.10` still throttle
any resulting lean; against: the 2-of-2 bar was partly what the 2026-08-12
VDE whipsaw remediation leaned on (though that whipsaw's root cause — the
stale-oil-leg bug — is separately fixed). **Genuinely open — needs Jorge's
call before or shortly after merge; shipped at the conservative default (2)
so merging does not silently activate a new, differently-sized live lean.**

### 79. D-6 follow-on — suppress the inert lean once diagnosed frequency is known (MEDIUM — architecture decision, data-gated, cross-refs #77)
From the 2026-08-21 quadrant-selection-reachability cycle (entry **#77**).
Task F's inert-lean diagnostic (`transition_watch.inert`/`lean_blocked_names`/
`lean_deployable_fraction`) is diagnose-only this cycle by explicit decision
(D-6) — no allocation change. Once the diagnostic has run live for a while
and the actual inert-lean frequency is known, a follow-up PR should decide
whether an inert (or partially-inert) lean should be automatically
suppressed (fall back to the pre-lean base allocation, or redistribute the
lean's budget across only the DEPLOYABLE names in the projected concentrate)
rather than merely narrated. Track the live frequency before designing the
fix — do not guess at a suppression rule from zero observed sessions.

### 80. GDPNow within-quarter smoothing / inflation de-staling candidates (MEDIUM — deliberately deferred, cross-refs #54)
From the 2026-08-21 quadrant-selection-reachability cycle (entry **#77**) —
explicitly out of scope for that cycle (§7), logged here per instruction
rather than attempted inline. The raw oldest→newest GDPNow vintage path
(±0.1 band, `_confirm_axis_direction`) over-reads early-quarter nowcast
noise — related to, but more specific than, entry **#54**'s "growth-axis
recency slope" deferral from the 2026-08-06 audit. Candidate de-staling
inputs for a later cycle: the Cleveland Fed / NY Fed nowcasts (independent
of the Atlanta Fed GDPNow this system already tracks) and the Cleveland Fed
median/trimmed-mean CPI (a genuinely different inflation-persistence
estimator, not just a fresher print of the same core CPI/PCE series). Do
not implement without a specific empirical probe showing the current
±0.1 band actually misfires on a real vintage-boundary case — the diagnosis
here is a hypothesis, not yet independently confirmed the way this session's
other findings (F1-F7) were.

### 58. `catalyst_score` weight tuning — gated on graded outcome rows (LOW — data-gated, do not touch early)
From the 2026-08-10 catalyst-sleeve-funnel session (entry **#57**). `src/collector/catalyst_screen.py`'s
composite is deliberately EQUAL-WEIGHTED across its 6 components in v1 — the
same #23 doctrine already applied to `sleeve_selection`'s momentum blend and
every other scored-not-tuned composite in this system: with no point-in-time
backtest harness, a hand-picked weight is an unfalsifiable prior dressed as
signal, and once shipped it becomes very hard to distinguish "this component
matters" from "we assumed this component matters." **Do not tune before:**
(a) a meaningful sample of `catalyst_screen.ledger` rows have matured into
graded flex outcomes (the Phase C `track_record`/`OverrideHistory` stamping
pattern is the template — a `catalyst_score` layer would need the same
falsifier-date + counterfactual grading, not yet built), and (b) ideally the
#23 ALFRED-style point-in-time harness exists so a proposed weight change can
be validated against history rather than against this cycle's few outcomes.
**Acceptance when picked up:** a specific evidence_n threshold (mirror the
Learning Loop's `evidence_n >= 10` bar for parameter-class proposals), a
before/after comparison on graded rows, and — per the Learning Loop's own
proposer≠approver invariant — a human-reviewed PR, never an autonomous edit.

### 59. Overnight/sector read-through investigation — gated on #34 + the `catalyst_screen` ledger (LOW — data-gated)
From the 2026-08-10 catalyst-sleeve-funnel session (entry **#57**). Once (a)
FOLLOWUPS **#34** (`global_overnight` tone block) has an actual answer from
`scripts/probe_fmp_tier.py` run with a live key, and (b) `catalyst_screen.ledger`
has accumulated enough runs to show which SECTORS/quadrants the discovery
universe keeps surfacing candidates from, this is worth a dedicated
investigation: does overnight Asia/Europe tone read through into which US
sectors the NEXT-DAY earnings-calendar/congressional-flow discovery pool
favors (e.g. a risk-off Asia session correlating with the discovery pool
skewing defensive the same morning)? This is explicitly NOT the same question
as #34 itself (#34 is a pre-open risk-tone instrument for flex ENTRY/ABSTENTION
decisions on already-nominated names; this item is about whether overnight tone
predicts what DISCOVERY surfaces in the first place) — keep the two separate
investigations, don't conflate them. No design proposed yet; this is a park
note, not a spec. Do not start without both prerequisites — a sector
read-through claim without the ranking-ledger history to test it against is
exactly the kind of unfalsifiable pattern-matching #23 exists to prevent.

### 54. Growth-axis head-to-tail slope mislabels a rolled-over trajectory as "rising" (B5, deferred by decision 2026-08-06)
From the 2026-08-06 signal-integrity audit (entry **#53**) — deliberately NOT
fixed this cycle (Jorge's decision: leave `_build_growth_axis`'s direction
logic untouched). The current head-to-tail slope method (first vs last
vintage in the rolling window) reads a trajectory that has already ROLLED
OVER and is now decelerating as still "rising," because the endpoints alone
don't see the intervening peak. **Live evidence:** 08-03/04 Q2 tail
`1.36→…→1.74→1.68→1.58→1.54` (peaked at 1.74, now falling for 3 straight
vintages) read "rising"; 08-05 Q3 tail `4.95→6.18→5.86` (peaked at 6.18, now
falling) also read "rising." **Fix candidate (not implemented):** a
recency-aware slope — compare the terminal 2-3 vintages' own trend (not just
first-vs-last) and require AGREEMENT with the head-to-tail sign before
confirming "rising"; when they disagree (head-to-tail says rising but the
terminal segment is falling), classify as `flat`/`indeterminate` with an
explicit rollover flag, mirroring how `direction_change_diagnostics`
already flags a `window_rolloff` attribution for the D-2 axis-confirmation
gate. Revisit once a session is scoped to touch axis classification
directly — do not fold into an unrelated cycle given the D-2 confirmation
hysteresis's sensitivity to this exact field.

### 55. Risk Score sensitivity investigation (R2, 2026-08-06 audit)
The 08-03/04/05 reports held Risk Score at a flat 4/10 across materially
different underlying conditions: growth confidence moved medium→high,
the `leading_vs_lagging_growth`/`market_vs_macro_quadrant` divergence read
high→medium, and 08-05 carried a fresh ADP forward-softening signal (see
**O3**, entry #53) — none of which moved the Risk Score at all. **Not a
fix yet — an investigation stub.** Check whether the Risk Score computation
(wherever it lives — appears to be model-computed, not
`collector._conviction_proxy`, since the conviction proxy is a SEPARATE
deterministic figure) incorporates axis confidence, divergence strength, or
forward labor risk at all, or whether it's effectively anchored on lagging
inputs that rarely move session-to-session. If it's model-computed
(prompt-driven, not deterministic), the investigation is really "does the
prompt give the model enough moving parts to justify a Risk Score change,
and does it ever actually change one in practice" — pull a longer history
of `risk_score_reading` across recent reports before concluding anything.

### 56. MU orphan position — strategy decision (R1 strategy half, 2026-08-06 audit)
The 2026-08-06 signal-integrity audit's R1 finding split into two halves: the
CODE half (the 10× ingestion-correction guard + `pnl_decomposition`
realized/unrealized labeling) shipped in entry **#53**. This is the
remaining STRATEGY half, Jorge's call, not something to implement
speculatively: what to actually DO about MU as a name — whether the
off-roster position (or its wind-down remnant) should be closed out
entirely, left as-is under flex-engine management, or something else. The
code fix makes MU's data trustworthy (a corrected price feed, a clearly
labeled realized-vs-unrealized P&L) so this decision can be made on real
numbers next time it's revisited — it does not make the decision itself.

### 51. Tape-vs-axes divergence grading ledger — promote-or-demote `market_implied_quadrant` (MEDIUM — data-gated, ~2 months)
From the 2026-08-01 report audit (entry **#50**). On every session where
`market_implied_quadrant.confidence == "high"` and the implied quadrant differs
from the macro call (active or borderline bucket), append a deterministic row
to a new `divergence-history` blob: date, implied quadrant, macro call, what
the book did (amplifier net deployment pp that session), and stamp it for
Phase C grading at 30/60/90d (which quadrant's benchmark basket actually won
the window). Decision rule after ~2 months of rows: if high-confidence tape
divergence beats the realized axes at turns, give it a BOUNDED mechanical seat
(e.g. sustained N-session divergence caps per-session amplifier deployment, or
shifts the reference a staged fraction, mirroring `transition_watch`); if not,
demote it to context-only prose. Kills the current limbo where the signal is
computed daily and muzzled daily (describe-only, no allocation authority).

### 52. Style-rotation composite — value/growth leadership from already-fetched prices (MEDIUM — cheap, zero new API calls)
From the 2026-08-01 report audit (entry **#50**). Deterministic collector
block: relative 20d/60d momentum of the book's own value/cyclical proxies
(COWZ, XLF, XLI, VDE) vs growth proxies (QQQ, SOXX, SPY), plus the existing
XLY/XLP ratio — emitted as a `style_rotation` block with direction + streak,
N=2-confirmed like the axes (mirrors `_confirm_axis_direction`). Purpose: give
`transition_watch` a leadership-change input that fires earlier than realized
macro. Grade it through the same divergence ledger (**#51**) before it earns
any allocation authority — do not wire it into `reference_weights` in the same
change that introduces it.

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
- **Task C probe (2026-08-10, catalyst-sleeve-funnel session, entry #57):** the
  FMP-tier verification this item has been gated on since 2026-07-04 is now a
  committed, runnable script — `scripts/probe_fmp_tier.py` — instead of an
  open question. It hits `^N225`/`^KS11` (Asia), `^GDAXI`/`^STOXX50E` (Europe),
  and `USDJPY` (forex) via `/stable/quote` and reports raw status/payload for
  each, treating a 402 the same way `get_etf_holdings` is already treated
  (unavailable on this tier, park it). **NOT YET RUN** — this session had no
  live `FMP_API_KEY` and no access to the EasyGridsProduction Azure
  tenant/subscription the real key lives in (a different identity from this
  dev environment's default `az` session). Whoever picks this item up next:
  run the script first (`$env:FMP_API_KEY = "..."; python
  scripts/probe_fmp_tier.py`) — its exit code and printed verdict per symbol
  answer the gating question directly; only then decide build vs. park.

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

### 48. XLV/IHE forced-band-buy interlock (2026-07-27 audit F4) — verify post-deploy, closed if clean
With blanket auto-switch (#47), the healthcare_def rotation (XLV→IHE) now EXECUTES
(sell-down XLV + buy-up IHE via band enforcement) instead of the pre-47 world where a
switch_signal was merely a proposal and the model could have kept topping up the
switched-OUT incumbent (XLV) toward its old reference — the "top-up-the-loser" tension
#47's design is meant to resolve by construction (D-G1: XLV's reference goes to 0 the
same run IHE becomes effective). **Action:** confirm post-deploy, once the override
flips, that XLV's gap row shows `reference_pct: 0.0` and is sold down via
`band_enforcement` (never band-bought back up) in the actual `daily-trades`/
`reference_execution` output. If clean, close this item; if a residual top-up path is
found, it needs an explicit interlock (not designed in #47, since the deterministic
reference/D2-zeroing/validator layers were verified pure-function correct — this item
is about confirming their live interaction, not their unit logic).

**Update 2026-07-28: proposal-level confirmation is DONE.** The 07-28 report confirms
the design by construction — XLV's reference is 0.0 and it is sold via
`band_enforcement`, never topped up; the "top-up-the-loser" tension is resolved.
**Still open:** closure waits on the 2026-07-29 `execution_review` (the FILL-level
confirmation — did the proposed orders actually execute, not just get proposed
correctly). Expected from the 07-28 proposal: XLV sell 55 shares, IHE buy 3 shares,
SGOV buy 6 shares (**not 77** — see the 2026-07-28 session's Task C item 2, the
carve-out clamp: the 77-share sweep was never fundable from pre-trade literal cash).
Close this item once 07-29's `execution_review.filled`/`.failed` confirms those three
fills (or explains a deviation).

---

## Done
### 81. 2026-08-21 session: SWA regime-lean visibility — transition-lean rail on the performance chart, regime-call accountability scorecard, reachable-suspect badge — Done, branch `feat/20260821-swa-lean-visibility` (auto-merge: NO, human review required)
Mission: PR #42 (entry **#77**) made Q2 reachable and made the quadrant call
accountable — neither change was visible anywhere in the UI (grep across
`web/` found exactly one field read from anything #42 touched:
`favored_bucket`; zero references to `quadrant_performance`,
`transition_watch`, or `market_implied_quadrant`). The specific hazard: the
performance chart's regime bands shade from realized axes only — once
decision **D-3** (entry **#78**) resolves and a joint lean stages toward a
projected quadrant, that lean would move real reference weight and real
dollars with the chart still showing no indication anything changed. This
cycle instruments the fix so the first live joint lean can be WATCHED, not
inferred from JSON. Shipped ahead of D-3 per instruction.

- **Task A — regime-call accountability panel.** `quadrant_performance`
  already lands in the daily snapshot; `_attach_quadrant_accountability`
  (`web/api/function_app.py`, mirrors `_attach_sleeve_series`'s degrade-to-
  unchanged contract exactly) attaches a `quadrant_accountability` block to
  the `/api/performance` response — `cumulative_favored_excess_pp`,
  `trailing_excess_pp`, `favored_sessions`, `favored_streak`, `suspect`,
  `suspect_path` per bucket. **Premise correction (empirically verified, not
  assumed):** `web/api/*.py` has ZERO references to `risk-limits.json` or
  `src/config` anywhere — it is a separate deployment with no filesystem
  access to that config, so the ORIGINAL plan ("no collector change needed,
  it's a passthrough") was wrong for `suspect_path` specifically: the API
  cannot itself determine which of `suspect`'s two OR'd conditions fired
  without the thresholds it doesn't have. Fixed with a small, justified
  collector addition — `_build_quadrant_performance` now also emits
  `suspect_path` (`"streak"`/`"rolling"`/`"both"`/`None`), computed from the
  SAME two booleans `suspect` itself combines, so it can never disagree with
  `suspect`. The trailing-window key (`trailing_excess_pp_{N}`/
  `favored_sessions_{N}`) is discovered by prefix match, never hardcoded to
  20, and normalized to fixed output names (`trailing_excess_pp`/
  `favored_sessions`) with the discovered `N` surfaced once at the top level
  (`trailing_window_sessions`) for the renderer's "8/20 sessions favored"
  copy. The web panel (`performance.html`/`.js`) renders this as a
  VISUALLY SEPARATE, dashed-border, boxed row beneath the existing
  basket-return chips — those answer "how did the basket do?", this answers
  "how did our picking do?" — with a deliberately LOUD red suspect badge
  (PR #42's Task E made `suspect` reachable for the first time; it has no
  live precedent) whose tooltip names which path fired without ever
  mislabeling a rolling-path fire as a consecutive streak.
- **Task B — the D-3 observation surface: transition-lean rail on the
  chart.** `_perf_point` gains an optional `lean` field
  (`{projected_quadrant, direction, staged_fraction, inert}`), stamped from
  the CONFIRMED `transition_watch` block (post-`_confirm_transition_watch`,
  so `staged_fraction` is the APPLIED value). **B1a (a real regression the
  literal spec wording would have missed):** the historical backfill loop's
  guard was `"closes" in existing: continue` — adding `lean` this way would
  have meant NO pre-existing perf-series point ever got backfilled with a
  lean, ever (the chart would only show the rail for sessions after
  deploy). Fixed: the guard now checks `"closes" in existing AND "lean" in
  existing`, and the patch branch sets `existing["lean"]` alongside the
  fields it already patches. **B1b (never fabricate):** a snapshot
  predating FOLLOWUPS #17 (no `transition_watch` key at all) backfills
  `lean: None` — key PRESENT (preserving the at-most-once-more re-read
  property) but value `None`, distinct from a KNOWN no-lean day (a dict
  with `projected_quadrant: None` — still a real, checked value). This
  three-state requirement (omitted / explicitly `None` / a real dict)
  needed a sentinel default (`_LEAN_UNSET`) in `_perf_point`, since a plain
  `None` default can't distinguish "caller didn't pass lean" from "caller
  passed lean=None on purpose." B2: `web/api/function_app.py`'s series
  comprehension passes `lean` through (`p.get("lean")`, tolerates absence).
  B3: a NEW, separate `leanRail` Chart.js plugin (deliberately NOT folded
  into the existing `regimeBands` plugin, and added ONLY to the main
  chart's plugin list, never the sleeve panel) draws a thin ~9px rail along
  the chart-area bottom — solid fill at higher opacity than the realized
  band's tint for an active lean, a dashed OUTLINE (not hatching — the
  simpler alternative the spec explicitly permitted) for an inert one, and
  blank for no lean (known or unknown history alike). **B3a:**
  `scripts/validate_palette.js`, referenced by this file's own header
  comment, does not exist in the repo (logged separately as entry **#82**);
  sidestepped by reusing the ALREADY-validated QCOLOR/QTINT hues at a
  different opacity/treatment for the rail — no new hue introduced, so no
  new validation is needed. B4: the tooltip footer callback gains a second
  line — "Lean: Q2 (re_risk, 10%)", or "... — gate-blocked, not buyable"
  when inert.
- **Verification.** Suite 1330→1354 (24 new: 4 collector `suspect_path` +
  9 API `_attach_quadrant_accountability` + 9 collector `lean`
  stamping/backfill + 2 API `lean` passthrough), `ruff check .` clean (0
  before, 0 after — delta zero). Every new/modified test confirmed FAILING
  on post-#42 `master` source before implementation (KeyError/AttributeError/
  assertion mismatches — see the PR body for the exact output). A real
  before/after probe (not just the unit tests) confirmed the B1a backfill
  guard: 1/3 pre-existing points carrying a `lean` key before the fix, 3/3
  after, with the two previously-partial points correctly patched from their
  snapshot's `transition_watch`. **A rendered screenshot** (a standalone
  Playwright-driven harness loading the REAL `performance.js`/`styles.css`
  against a synthetic 30-session payload — never a mockup) confirmed all
  three rail states render distinguishably: blank (unknown history, days
  1-10), solid orange fill (active Q2 lean, days 11-20), dashed green
  outline (inert Q1 lean, days 21-30) — see the PR body for the image.
- **PR #43 review, Finding M1 (blocking, fixed same PR before merge) — a
  THIRD history epoch, not two.** `transition_watch` has existed since
  FOLLOWUPS #17 (~2026-07-23); PR #42's Task F is what started stamping
  `inert` onto it, merging three weeks later (2026-08-21). The original
  `bool(tw.get("inert"))` collapsed the resulting in-between epoch (block
  present, no `inert` key) into `False` — "known deployable" — for the
  entire #17-to-#42 window, including sessions that were in fact
  gate-blocked (Q1 leans under a closed gate, 2026-08-19 through 08-21,
  confirmed 3/3 amplifier-blocked per PR #42's Task F probe). That window
  is the most recent third of the YTD chart and the direct "before"
  picture the first live joint Q2 lean will be judged against. Fixed:
  `_lean_from_transition_watch` now reads `inert` as `bool(tw["inert"]) if
  "inert" in tw else None` — the SAME rule also covers a live-path
  degradation (`_transition_lean_diagnostics` runs inside a non-fatal
  `try`; if it ever raises, the confirmed block reaches this function
  with no `inert` key either). `web/performance.js`'s `leanRail` plugin
  gained a THIRD rail state (`unknown` — a faint fill at its own lower
  opacity, no outline, keeping the quadrant hue since the projection
  itself is known even when deployability isn't), gated on an explicit
  `lean.inert == null` check rather than a bare falsy test (a bare test
  is exactly the bug that produced this finding). Legend and tooltip
  gained matching third-state wording — never reusing "gate-blocked" for
  a session that was never evaluated, which would assert a fact the data
  doesn't support (the same class of error as asserting "deployed").
  **Explicitly not done, per instruction:** historical inertness was NOT
  re-derived from `projected_quadrant` + `regime_gate.status`, even though
  it would usually come out right — that would violate echo-never-re-derive
  and paper over a roster state (semis `SMH`/`SOXX`) that genuinely changed
  mid-window. `None` is the honest answer. Suite 1354→1361 (+7: 5 new M1
  tests + 2 regression guards), `ruff check .` delta zero vs master,
  screenshot replaced (not appended) with a 4-state version (blank / faint
  unknown / solid active / dashed inert, all four distinguishable at a
  glance) — see the PR body.
- **Out of scope, confirmed untouched:** `transition_watch`/
  `quadrant_performance`/`market_implied_quadrant` COMPUTATION (this cycle
  is display-only — the one exception, `suspect_path`, is a pure echo of
  logic `suspect` already computed, not new computation); decision **D-3**
  itself (this is the instrumentation that should precede it, not the
  decision); `structural_*` scores in the UI (deferred, worth doing later);
  `today.js`/`portfolio.js`/`history.js`/`learning.js` (verified zero
  references to any changed block).

### 77. 2026-08-21 session: Quadrant selection reachability — diagonal projection, inflation-confirmation semantics, structural tape scores, per-axis divergence eligibility, reachable accountability, inert-lean diagnostic — Done, branch `fix/20260821-quadrant-reachability` (auto-merge: NO, human review required)
Mission: the book lost to SPY on SELECTION, not cash drag, over 2026-05-26→08-21
(portfolio +0.33% vs SPY +1.60%; Q2 Reflation +4.82%/+3.22pp excess and Q3
Stagflation +4.06%/+2.46pp excess carried all the alpha while the book sat in
Q1/Q4 the whole window). This is a defect-repair cycle fixing the STRUCTURAL
reasons the machinery could never reach Q2/Q3 — no momentum overlay, no
probability-vector regime model (both explicitly out of scope, §7 of the
prompt). **Renumbering note:** the originating prompt specified entries
`#71`/`#72`, but those numbers were already in use (the 2026-08-14
flex-conviction-path cycle's own decision gates) — used the next free
numbers instead (`#77` main entry, `#78`/`#79`/`#80` for the decision-gate
and deferral follow-ups) rather than overwrite existing entries.

- **Task A (F1 root cause) — joint (diagonal) quadrant projection.**
  `_project_quadrant`/`_project_quadrant_growth` each move along ONE axis
  only, holding the other fixed — from a realized quadrant, only the two
  ORTHOGONALLY-ADJACENT quadrants were ever reachable; the diagonal (e.g.
  Q4→Q2) was structurally unreachable no matter how strongly both leading
  signals agreed. New `_project_quadrant_joint` (thin `active_quadrant()`
  wrapper) + `_build_transition_watch` now composes the diagonal when BOTH
  sides are activatable AND BOTH resolve `re_risk` (D-2: any de-risk side
  present bypasses composition entirely — spec §6 safety bias unchanged;
  only-one-side-activatable is bit-identical to pre-Task-A). New config
  `staged_fraction_re_risk_joint` (0.10, below the single-axis 0.15 per D-7 —
  a two-axis posture change is bigger, start conservative). The existing
  confirm/release hysteresis (`_confirm_transition_watch`, keyed on the
  `(projected_quadrant, direction)` pair) needed NO changes — a composed pair
  is automatically a new pair requiring its own 2-session confirmation,
  verified by test rather than assumed.
- **Task C (mandatory circularity guard) — structural (non-basket) tape
  scores.** `_build_market_implied_quadrant`'s legacy `growth_score`/
  `inflation_score` include `basket_momentum_20d`/`_60d` — literally the
  quadrant baskets' own relative performance — so feeding them into
  `transition_watch`/`reference_weights` would be performance-chasing
  wearing a macro costume ("Q2 basket outperforming ⇒ inflation rising ⇒
  lean into Q2"). New `structural_growth_score`/`structural_inflation_score`
  (+ `structural_implied_quadrant`/`_growth`/`_inflation`/`_vote_count`/
  `_confidence`) computed by IDENTICAL accumulation/threshold logic but with
  parallel running totals incremented ONLY in the six per-signal-vote blocks
  (copper/gold, XLY/XLP, DXY, breakevens, HY OAS, 2s10s) — never in the
  basket-momentum loop — so they are basket-free BY CONSTRUCTION, not by
  convention. Legacy fields are byte-identical (their code paths literally
  untouched; regression-tested against captured pre-fix values). Only
  `structural_*` may be consumed downstream for weighting (decision D-4,
  mandatory) — basket momentum stays strictly describe-only, permanently.
- **Task D (F5 fix) — per-axis divergence eligibility.** `_div_market_vs_
  macro_quadrant`'s top-level `status` requires BOTH axes to jointly resolve
  a concrete quadrant, so a decisive read on ONE axis was discarded whenever
  the other happened to be quiet — 2026-08-21: growth flat (−0.073, inside
  the 0.10 deadband) → quadrant "borderline" → the WHOLE divergence
  indeterminate, discarding an inflation tape read (+0.418) running ~8x its
  own 0.05 threshold. New ADDITIVE `axis_status: {growth, inflation}` — each
  `{status, implied, realized, score}` computed independently from Task C's
  `structural_*` scores vs each axis's own realized direction + staleness
  (growth: flat `staleness_days`=7 threshold; inflation: the `_FRESHNESS_
  MONTHLY_THRESHOLD_D`=45 monthly threshold, mirroring the existing B2
  doctrine `_div_leading_vs_lagging_inflation` already uses for its realized
  leg). Top-level `status` is completely unchanged (verified byte-identical
  on every existing test) — this is what Task B's third confirmation source
  reads.
- **Task B (F2 fix) — inflation-side confirmation gains a third source,
  honors OR semantics.** `divergence-config.json:leading_vs_lagging_
  inflation` documents OR semantics (breakeven delta ≥15bp OR oil 20d move
  ≥10%), but the CONSUMER (`_build_transition_watch`'s `_evaluate_inflation_
  side`, via `re_risk_min_confirmations`) re-applied it as effectively AND —
  counting exactly 2 possible sources and requiring both. 2026-08-21
  readings: breakeven +7bp (needs 15), oil −3.5% (needs 10) — zero
  confirmations from the original pair; the inflation side had NEVER staged
  a re-risk lean in this regime. Added a THIRD source — `market_implied_
  quadrant.structural_inflation_score` (Task C) sign-agreeing with the
  leading direction and clearing its own threshold — with the confirmation
  count now against 3 possible sources; a missing/unavailable structural
  score shrinks the denominator to 2, never fabricates a confirmation (a
  pre-Task-B caller with no `market_implied_quadrant` reproduces the exact
  old 2-of-2 behavior, verified by test). `re_risk_min_confirmations`
  KEPT AT 2 (of 3 now) pending decision **D-3** (entry **#78**) — see the
  required end-to-end probe below for exactly what that decision buys.
- **Task E (F7 fix) — reachable quadrant-call accountability.** The legacy
  `lagging_sessions >= suspect_after_sessions` path requires ONE UNBROKEN
  run of negative-streak sessions; a regime call flipping favored on/off
  every 3-5 sessions (the observed pattern) can never survive to
  `suspect_after_sessions` (10) even while genuinely losing every time it's
  checked — the guardrail meant to catch a bad regime call was defeated by
  the exact whipsaw it should detect. `suspect` is now the OR of that legacy
  path and a NEW rolling path (`favored_sessions_N >= min_favored_sessions
  AND trailing_excess_pp_N < suspect_excess_threshold_pp`, N =
  `trailing_window_sessions` — a plain fixed-N-session window, independent
  of favored-bucket continuity) plus a new never-reset, whole-series
  `cumulative_favored_excess_pp` (the daily "is our quadrant picking adding
  value?" scorecard). **Backward-compat gate found during implementation
  (not in the original spec):** the rolling path is now gated on
  `"min_favored_sessions" in cfg` being explicitly present — without this, a
  caller passing a partial/legacy cfg dict (several pre-existing tests, and
  any config predating this key) would silently pick up the new default
  rolling trigger, breaking `test_nine_lagging_sessions_not_yet_suspect`.
  Production config (`risk-limits.json`) always carries all four keys, so
  this only affects ad-hoc partial dicts, never live behavior. Defaults
  (window 20 / min favored 5 / threshold 0.0pp, decision D-5) accepted as a
  starting proposal — tune after ~30 sessions of live data.
- **Task F (F6 fix, decision D-6) — inert-lean diagnostic, DIAGNOSE ONLY.**
  `_build_reference_weights` step 3b applies the transition_watch lean as an
  EQUAL-WEIGHT split across the projected quadrant's concentrate names with
  NO gate awareness. F6 probe: gate closed → Q1's concentrate (SPY/QQQ/SMH,
  all amplifiers) is 3/3 Tier-1-rejected; Q2 (0/6)/Q3 (0/8)/Q4 (0/7) are all
  dampers, fully buyable. The ONLY lean the pre-Task-A system could ever
  generate from a defensive realized quadrant (the orthogonally-adjacent
  Q1) was therefore 100% unbuyable under a closed gate — the 2026-08-19
  report carried QQQ/SPY/SOXX references the gate forbade buying, corrupting
  the gap tables with an unreachable target. New pure `_transition_lean_
  diagnostics(projected_quadrant, gate_status, effective_selected)` mirrors
  the SAME equal-weight split the lean itself applies, so a name-count
  fraction is exactly the dollar fraction blocked. Adds `lean_gate_status`/
  `lean_blocked_names`/`lean_deployable_fraction`/`inert` to `transition_
  watch` (enriched post-hoc in `run()`, same pattern as other collector
  echo-fields) + `transition_lean_inert` echoed onto `reference_weights`.
  Prompt doctrine (project-instructions.md, sentinel-tested): an inert lean
  MUST be named with its blocked tickers and MUST NOT be presented as a
  closable gap. **Deliberately NOT suppressed this cycle** (decision D-6) —
  see entry **#79** for the follow-on suppression decision, gated on live
  inert-lean frequency.
- **Required end-to-end probe (§5, PR body verbatim)** — 2026-08-21
  conditions (growth falling, inflation falling, leading growth rising
  score=0.667/confidence=medium, breakeven +7bp, oil −3.5%, structural tape
  inflation +0.418) under both `re_risk_min_confirmations` settings:
  - **`2` (current default):** inflation side does NOT activate (1 of 3 —
    tape agrees, breakeven/oil don't clear their thresholds) → only the
    growth side fires → `projected_quadrant: "Q1"`, `staged_fraction: 0.15`,
    `composed: false`. **Task A's fix has nothing to compose with under
    today's actual confirmation bar — Q2 stays unreached.**
  - **`1`:** inflation side activates too (`projected_quadrant: "Q3"`,
    `confirmations: 1/3`) alongside growth (`"Q1"`) — BOTH re-risk → Task A
    composes the diagonal → `projected_quadrant: "Q2"`,
    `staged_fraction: 0.10`, `composed: true`. **This is the exact,
    empirically-measured difference decision D-3 controls.**
- **Suite:** `PYTHONPATH=src pytest` on `master` (`21b31a8`) = **1291
  passed**, branch tip = **1330 passed** (+39: 7 Task A + 4 Task C + 6 Task
  D + 7 Task B + 6 Task E + 7 Task F + 2 prompt-sentinel). Every new/modified
  test confirmed failing on pre-fix source before implementation (`git
  stash` isolation for Tasks A/C; direct KeyError/TypeError failures for
  D/B/E/F since each added a genuinely new field/parameter). `ruff` clean
  throughout.
- **CLAUDE.md** gains two architecture-decision entries: (1) `transition_
  watch` may compose a diagonal projection when both leading sides agree on
  re-risk (Task A); (2) only non-basket-momentum tape scores
  (`structural_*`) may influence weights — basket momentum is permanently
  describe-only (Task C, the circularity guard).
- **Out of scope, confirmed untouched:** `_QUADRANT_DEFENSIVENESS` ordering,
  the `regime_gate` rule (F8 — audited, 0/6 Q2 and 0/8 Q3 names gate-blocked,
  not the problem), the inflation-axis oil rate-of-change overlay (F8 —
  correct, base-effects rationale unchanged), any momentum/relative-strength
  overlay, probability-vector regime allocation, cash-sleeve/flex-gatekeeper/
  catalyst-screen/executor changes. GDPNow within-quarter smoothing logged
  separately as entry **#80** rather than attempted inline.
- **Three decision gates surfaced for Jorge, unresolved** — entry **#78**
  (D-3, genuinely open: `re_risk_min_confirmations` 2-of-3 vs 1-of-3 — see
  the probe above), entry **#79** (D-6 follow-on: suppress the inert lean
  once live frequency is known), entry **#80** (GDPNow smoothing, deferred).
  D-1/D-2/D-4/D-5/D-7 were adopted as shipped defaults per the prompt's own
  recommendations (all implemented as described above).

### 62. 2026-08-14 session: report-to-broker fidelity, oil signal correctness, transition_watch hysteresis, thematic conviction overlay — Done, branch `feat/20260814-thematic-conviction-oil-fidelity` (auto-merge: NO, human review required)
See the START HERE entry at the top of this file for the full per-task design summary
(A0 probe classification, B1's exact reconstruction of the 2026-08-12 stale-oil-leg
whipsaw, C's hysteresis mechanics, D's thematic-conviction overlay and its D5 unit-mismatch
deviation from the literal spec). Key numbers and artifacts for future reference:
- **Baseline:** `PYTHONPATH=src pytest` on `master` (`326e0d9`) = **1026 passed**.
- **Per-task test counts (all confirmed failing on pre-fix source via `git stash` isolation
  on the specific changed file, per the empirical-verification doctrine):** Task A
  1026→1045 (+19: 6 addendum + 3 malformed-debug-capture + 8 plan_vs_submitted + 2 prompt
  sentinels); Task B 1045→1052 (+7: 1 governing-value + 5 staleness + 1 sentinel); Task C
  1052→1063 (+11: 10 hysteresis + 1 sentinel); Task D 1063→1126 (+63, across
  `test_thematic_conviction_pure.py`, `test_thematic_hysteresis.py`,
  `test_build_thematic_conviction.py`, `test_thematic_reference_weights_integration.py`,
  `test_thematic_grading.py`, `test_write_thematic_history.py`, + 2 prompt sentinels).
  **Final: 1126 passed, ruff clean project-wide.**
- **New tables:** `TransitionWatchState` (PK=`state`, RK=`transition_watch`, single row),
  `ThematicConvictionState` (PK=`state`, RK=symbol, one row per nominated ticker),
  `ThematicHistory` (PK=year-month, RK=`THM-YYYYMMDD-NNN`) — all three registered in
  `shared/storage.py`'s `_TABLES` list (auto-created by `ensure_tables()`).
- **Decision D-1 (rejected alternative, on record):** a mechanical oil-level trigger
  (e.g. "Brent > $90 for N sessions") was explicitly rejected in favor of an LLM-emitted
  probability-of-increase (`p_up`) driving a quantized conviction band — see Task D1's five
  mandatory safety properties (quantized / evidence-bound / bounded / hysteretic / graded) in
  `risk-limits.json → thematic_conviction._note` and `project-instructions.md`'s
  "Thematic capex cascade" section.
- **Two decision gates surfaced for Jorge, unresolved this cycle** — entries **#63**
  (thematic-ladder numbers) and **#64** (D-6: thematic lift on a non-selected pool member).
  Two post-merge watch items — entries **#65** and **#66**.
- **PR #38 pre-merge remediation (same day, four findings, three blocking — all in Task D,
  none in A/B/C).** An inline-Python re-audit of the merged PR found: **M1** the D5
  floor-lift was purely additive against an already-normalized `weights` dict — at the
  configured `aggregate_cap_pct_of_equity` (8.0) the reference asked the book to hold
  ~107.9% of equity, with `ceiling_pressure` (M2) gated on a theoretical "living hedge"
  size that ignored floor protection and so never fired in-cap; **M3** `_thematic_
  classify_symbol`'s non-selected-pool-member check read `(effective_selected or
  {}).get(role_id)` directly instead of `shared.quadrants.selected_for_role` — falsy on
  the ordinary day (no auto-switch live), leaking 18 of 18 probed non-selected pool
  members through as `core_eligible`; **M4** step 5b applied `thematic_conviction.active[]`
  with no re-validation, so a legacy exit or any other ineligible symbol injected directly
  reached a non-zero reference weight if it ever got past M3's now-fixed gate. Fixed:
  the lift is now budget-conserving (reduces non-active-quadrant core names first,
  spills into the active quadrant only as a genuine, now-reachable `ceiling_pressure`,
  clamps the thematic lifts themselves — `budget_clamped`/`budget_dropped_pp` — if even
  that's exhausted, always floor-protected); the classifier resolves through
  `selected_for_role`; step 5b re-runs the classifier on every entry before it can reach
  a reference weight (`rejected_at_apply[]`, logged at WARNING). Suite 1126→1170 (44 new/
  rewritten tests), every one confirmed failing against the PR-as-submitted commit via
  `git stash` isolation. Full per-task probe before/after output is in the PR #38
  description.

### 69. Flex-state stale-read fix — Done, branch `fix/20260814-flex-state-stale-read`, PR #40 merged `9768da5` (auto-merge: NO, human review required)
Found empirically while probing the "5-session flex nomination drought" premise for
the conviction-path cycle (entry **#70**): 2026-08-11 actually filed two real
nominations (AVGO, ENTG), each evaluated 26 times by the intraday engine per the
append-only `flex-decisions/2026-08-11.jsonl` log — legitimate, correctly functioning
Layer 2 declines (AVGO: `pre_window`→`no_bars`→`below_vwap`/`vwap_not_rising`→
`stop_too_wide`→`after_cutoff`; ENTG: `liquidity_below_min` all day). Yet
`daily-snapshots/2026-08-12.json`'s `flex_state.entries` showed `[]`, as if nothing
had ever been evaluated. **Root cause:** `flex-state/{date}.json` is a single blob
overwritten on EVERY ~15-min tick, and every trading day's LAST tick is unavoidably a
post-close `market_closed` tick (the clock-gate early return, before entry/exit
evaluation runs) — so the blob's final state for ANY day was always the empty
closed-tick stub, regardless of how much real activity happened intraday. **Fix**
(write-side only, per Jorge's explicit direction — evaluate the intraday-timer
overwrite itself, not just the collector-side reader that consumes it):
`_persist()`'s `market_closed` branch now reads back today's EXISTING blob (if any
real tick already wrote one) and carries its `entries`/`exits`/`quadrant` forward
into the closed-tick write, instead of overwriting them with an empty stub; if NO
real tick has run yet today (blob absent), it skips the write entirely rather than
writing a premature stub. `read_json_blob` already returns `None` (never raises) on
a missing blob, so no reader-side change was needed — analyzed and confirmed, not
assumed. 4 new tests (`test_flex_state_persistence.py`), all confirmed failing
against pre-fix source via `git stash` isolation. **Jorge's explicit scoping
decision: NOT folded into the conviction-path cycle as a "Task G"** — landed as its
own small dedicated PR merging before that cycle started, per instruction.

### 68. PR #38 finding M5 — thematic lift draining literal cash below its floor — Done, branch `fix/20260814-thematic-cash-drain-m5`, PR #39 merged `dc20130` (auto-merge: NO, human review required)
The M1 remediation's reduction-pool exclusion list (entry **#62**) named `"SGOV"` by
ticker but not the `"__cash__"` placeholder key that holds the literal-cash buffer
(`_CASH_BUFFER_PCT`) — a separate key in `weights` at the same point, not in
`active_names` either, so it stayed silently eligible as pool-1 reduction capacity.
Reproduced empirically: a thematic lift of just 2.5pp on a floored core name drained
`literal_cash_target_pct` from a 1.5% baseline down to 0.096% — below
`literal_cash_floor_pct` (0.75%) — while `cash_sleeve_target_pct` (computed upstream,
independently of the thematic step) stayed frozen at 12.0 and `by_quadrant.cash_
sleeve` disagreed with both. **Fix:** exclude `"__cash__"` alongside `"SGOV"` from the
thematic reduction pool — literal cash has its OWN floor (`literal_cash_floor_pct`),
an entirely separate protection from the sleeve-level cash-sleeve floor, and neither
should ever be spent to fund a thematic lift. This was the hard sequencing gate for
the flex-conviction-path cycle (entry **#70**) — "do not start until M5 is fixed,
re-audited, and merged" — now satisfied.

### 70. 2026-08-14 flex-conviction-path cycle: SECOND flex-nomination path (no dated catalyst required) + flex_eligibility + applicable-set rankability + Layer 2 profile split — Done, branch `feat/20260814-flex-conviction-path` (auto-merge: NO, human review required)
Six-task cycle fixing the flex sleeve's structural inability to trade a real, live
thesis with no scheduled event — the catalyst path (`build_flex_entry`) REQUIRES a
dated catalyst, so EUAD (European defense-capex re-rating, +16-22pp 60d excess vs SPY
for four straight sessions) was correctly declined every single day, filing zero
flex nominations on 4 of 5 sessions (08-10/08-12/08-13/08-14) for exactly this
reason. **Task A0 blocking probe (done first, per instruction) corrected the spec's
own premise**: the "zero nominations for five sessions" framing was FALSE — 08-11
filed two real nominations (AVGO, ENTG), both correctly evaluated and declined by
Layer 2 (see entry **#69** for the unplanned stale-read bug this probe surfaced and
that got fixed as its OWN prerequisite PR, per Jorge's explicit direction not to fold
it in here as a "Task G"). AVGO's `vwap_not_rising`/`stop_too_wide` decline is direct
empirical support for Task E's profile split below.

- **Task B (the design's central point) — base-rate-relative conviction ladder.**
  `edge = p_up - base_rate_up`, NEVER `p_up` alone: over a 15-30 trading-day window a
  broad, liquid equity name rises unconditionally ~55-58% of the time, so an absolute
  `p_up >= 0.52` threshold is BELOW the base rate and would fire on nearly every
  candidate. `_base_rate_up` (pure, `collector/handler.py`) computes each candidate's
  own trailing empirical fraction of overlapping `horizon_days`-length windows with
  positive total return over `base_rate_lookback_days` (504 sessions, ~2y) —
  fail-closed below `base_rate_min_windows` (60): `base_rate_up: None`, NEVER a
  substituted 0.50. `_conviction_edge` clamps at 0. `_conviction_ladder_lookup` maps
  the edge to a `{conviction, size_mult}` band (config `conviction.ladder` — a
  SEPARATE ladder from `thematic_conviction.ladder`, unreviewed numbers, see entry
  **#63**). `_conviction_catalyst_amplifier` applies a REAL dated catalyst (if the
  nomination has one, within `horizon_days`) as an amplifier — `catalyst_size_mult`
  and an optional one-band promotion — never a gate; see entry **#71** for the
  unreviewed combined-effect decision gate. `_confirm_flex_conviction_entry` mirrors
  `_confirm_thematic_entry`'s exact confirm/release hysteresis algorithm (new
  `FlexConvictionState` table) applied to `size_mult` instead of a %-of-equity
  target. `_build_flex_conviction` (the collector orchestrator, mirrors `_build_
  thematic_conviction`'s architecture exactly) reads back the PRIOR day's
  `path == "conviction"` nominations (one-session lag — deliberate, same reason as
  thematic_conviction: `base_rate_up` needs the collector's OWN price cache, which a
  same-day flex tick doesn't have) and writes the `flex_conviction` snapshot block;
  `flex/handler.py` reads `flex_conviction.active[]` same-day as ADDITIONAL entry
  candidates alongside the unchanged catalyst-path `flex_nominations[]` read.
- **Task C — `flex_eligibility` deterministic block (C1) + prompt rules (C2).**
  `_build_flex_eligibility` covers every `LEGACY_EXITS` name and every live flex
  candidate with `{symbol, core_re_entry, flex_nominatable, reason}`, derived live
  from the SAME `flex_separation_set`/`FLEX_REENTERABLE` machinery the engine itself
  uses — never hand-maintained. Prompt doctrine (project-instructions.md): a report
  may not assert a symbol "cannot" be flex-nominated without citing the matching row;
  must distinguish "the system could not nominate this" (cited, deterministic) from
  "I am choosing not to nominate this" (a live judgment call) — the EUAD false-
  prohibition failure mode this closes; `regional_rotation`'s top-named rotation
  candidate must be adjudicated every session.
- **Task D — rankability fixes.** D-priority-1: `relative_strength` (60d total-return
  excess vs SPY) is the 7th `catalyst_score` component — EUAD's own headline metric,
  previously computed daily and displayed in `regional_rotation` but fed into nothing.
  D-priority-2: static+dynamic `flex_candidates` (never in the discovery-fetch loop)
  now get their own `{date: close}` history fetch (~14 calls/day, well within FMP's
  250/day budget per the A1 probe) — the blocker for `relative_strength`/`base_rate_up`
  on names that were never part of the discovery universe. D-priority-3: applicable-
  set split — `earnings_proximity`/`political_flow` are `not_applicable` (not merely
  `missing_data`) for a fund/ETF; rankability is now a DOUBLE-CLAUSE guard
  (`components_applicable >= 4` AND `components_available >= 4`) closing the "2-of-2
  applicable passes" weak-bar failure mode a single available-only clause would have
  left open. D-priority-4: `isEtf`/`isFund` FMP profile booleans (not sector-string
  inference) determine the applicable set.
- **Task E — Layer 2 entry/exit profile split.** `build_conviction_entry` (new,
  `flex/entry.py`) is a SEPARATE gate sequence from `build_flex_entry` (byte-identical,
  untouched): no gap/VWAP-rising trigger, instead a "no-chase" ceiling
  (`conviction_no_chase_atr`); stop is the nomination's own `invalidation` price level,
  bounded by `conviction_max_stop_pct` (10.0, vs the catalyst profile's 4.0) — never
  ATR-derived; sizing scales the risk BUDGET itself by `size_mult` before the same
  risk-budget/per-name-cap/sleeve-cap governor runs. `build_flex_exit_state` gains a
  `path`-aware skip: NO time stop at all for a conviction-path entry (replaced by the
  release-driven exit in `flex/handler.py`, firing when a held conviction symbol drops
  out of `flex_conviction.active[]`). B5 cash accommodation (`cash_accommodation_
  shares`) clamps a conviction entry's shares so it can never drain literal cash below
  `literal_cash_floor_pct` or the whole cash sleeve below `cash_sleeve_floor_pct` — the
  explicit M5 callback (entry **#68**): a clamp, never a rejection, mirroring "size-
  floored ≠ impossible."
- **Task F — grading extension, reused not duplicated.** `ThematicHistory` gains a
  `path` field (`"core_thematic"` vs `"flex_conviction"`) rather than a new table.
  `_write_flex_conviction_history` (analyzer) writes one row per `path == "conviction"`
  nomination (RowKey `FLEXCV-…`, vs thematic's `THM-…`); `_stamp_flex_conviction_
  outcomes` resolves each row at its OWN `horizon_days` (single-shot, not the 30/60/90
  ladder core-thematic uses) via the identical perf-series/FMP-fallback pricing;
  `_build_flex_conviction_calibration` computes a SEPARATE Brier/hit-rate/damping
  track filtered on `path == "flex_conviction"`, never blended with core-thematic's.
  Reuses `_thematic_brier`/`_thematic_damping_factor` verbatim.
- **Three decision gates surfaced for Jorge, unresolved this cycle** — entry **#63**
  (re-scoped: BOTH ladders' numbers), entry **#71** (G-2: catalyst amplifier's ~2x
  combined effect), entry **#72** (G-3: no momentum-protection gate on the conviction
  path, deliberate). Two post-merge watch items — entries **#73**/**#74**.

**Baseline:** `PYTHONPATH=src pytest` on branch tip before this cycle (post-PR #40,
`9768da5`, measured via a full `git stash -u` isolation, not carried forward from
memory — see the entry #60/#61 baseline-measurement correction this doctrine exists
to prevent) = **1176 passed**. **Final: 1272 passed** (96 new, across 6 new test
files — `test_flex_conviction_pure.py`, `test_flex_conviction_hysteresis.py`,
`test_flex_conviction_entry.py`, `test_flex_conviction_exit_state.py`,
`test_flex_conviction_wiring.py`, `test_write_flex_conviction_history.py` — plus
extensions to `test_catalyst_screen.py`, `test_build_catalyst_screen.py`, and
`test_prompt_hygiene_sentinels.py`), ruff clean project-wide. Every new/modified
test confirmed failing against pre-fix source via `git stash` isolation on the
specific changed file, per the empirical-verification doctrine.

### 60. 2026-08-10 session (later same day): Flex Sleeve Performance Ledger + SWA panel — Done, branch `feat/20260810-flex-performance-ledger` (auto-merge: NO, human review required)
The Flex Catalyst Engine had no performance record — `src/flex/ledger.py` was
open-position-only (`entry_price`/`initial_stop`/`qty_current`/`current_stop`/
`order_ids`), and every one of the four ways a position stops being tracked
(engine-initiated `time_stop`, a partial `scale_out`, a broker-side stop fill
surfacing via `reconcile_ledger`'s `exits_to_record`, and an entry order that
never actually filled) dropped the trade's outcome with no exit price, no
P&L, no record. `_record_trade_history` wrote a TradeHistory audit row per
sell, but with `extra={}` — status/qty only, nothing to build a curve from.
This PR builds the closed-trade record once, correctly, and serves three
consumers from it: this SWA panel, Phase C grading (once #61 lands), and the
`catalyst_score` weight-tuning question (#58).

- **Task A — closed-trade ledger, one funnel, four callers.** New
  `src/flex/trades.py` (mirrors `flex/ledger.py`'s I/O-plus-pure-builders
  shape; `reconcile.py`/`exit_state.py` untouched and still pure). A single
  `flex/handler.py::_finalize_closed_trade` every close path routes through:
  it re-derives the trade's fills from `AlpacaClient.get_activities("FILL",
  after=entry_date)` (**reconciliation is broker truth, not engine intent**
  — `flex.trades.merge_broker_fills` treats the engine's own recorded fills
  as a floor, not a ceiling, and fills in whatever broker activity the engine
  didn't itself witness, labeling the LAST unaccounted sell the trade's
  `exit_reason` and any earlier one a broker-confirmed `scale_out` the engine
  missed). A ledger row with **zero confirmed broker buy fills** (the
  entry-side-failure path — the OTO order never actually filled) writes
  **no closed-trade record** — recording one would fabricate an entry price
  for shares that were never bought; this is the resolution to the fourth
  "close path" the task spec named without a line reference. `record_closed_
  trade` is idempotent on `trade_id` (`flex.ledger.new_entry` now generates
  one ONCE at open and carries it unchanged through every fill/repair — unlike
  `order_ids`, which a stop replace DOES overwrite). `_record_trade_history`'s
  `extra={}` is backfilled with fill price/proceeds/pnl/trade_id on every
  close path so the TradeHistory audit trail and the closed-trade ledger can
  never quietly disagree.
- **A real bug, caught by running the tests, not by reading the code.**
  The first cut folded a just-submitted-but-unconfirmed `extra_fill`
  (`price: None` — `get_order` hadn't confirmed a fill yet) into
  `merge_broker_fills`'s "already accounted for" quantity — which meant a
  LATER broker-confirmed price for that exact fill, already sitting in
  `get_activities`'s response, got silently discarded in favor of the null.
  `tests/test_flex_close_paths.py::test_time_stop_pnl_present_when_broker_
  confirms_both_fills` failed (`pnl_usd` came back `None` instead of the
  expected `-600.0`) against that first cut, which is exactly what caught
  it — fixed by only folding a PRICED `extra_fill` into the recorded set,
  leaving an unpriced one entirely to broker-truth reconciliation.
- **Task B — `catalyst_score` stamped at entry.** `_open_position` now
  reads `snapshot["catalyst_screen"]["ledger"]` (catalyst-sleeve-funnel PR,
  entry **#57**) directly via a new `_catalyst_score_lookup`, keyed by
  symbol, and stamps `catalyst_score`/`score_components` onto the ledger row
  at open — reachable directly from the snapshot already loaded in
  `run_flex_intraday`, so no plumbing through the model's nomination JSON
  was needed (the fallback the task spec allowed for turned out to be
  unnecessary). `nomination_thesis` comes from the nomination's `rationale`
  field. Both carry through unchanged to the eventual closed-trade record —
  the field #58's weight-tuning work depends on entirely.
- **Task C — daily sleeve mark.** New `flex-ledger/equity-series.json`
  (`flex.trades.build_sleeve_mark` + `upsert_equity_point`, upserted by date
  on every in-hours tick of `run_flex_intraday`). **Decision gate 1
  (last-in-hours-tick vs 09:00-collector-against-prior-close): implemented
  as an idempotent per-date overwrite every tick**, which converges to
  "whatever the last successful tick of the day computed" without needing to
  detect which tick is actually last — simpler and more robust than explicit
  last-tick detection, not yet confirmed by the account holder. Broker state
  basis is STEP 0's positions read (this tick's start), the same basis
  `reconcile_ledger` itself uses.
- **Task D — `/api/performance` extension.** `web/api/function_app.py` gains
  `_sleeve_series`/`_attach_sleeve_series`, attached to BOTH the fast-path
  (cache) and legacy-fallback response branches. Per point: `sleeve_
  contribution_pp` = `(cumulative_realized_usd + unrealized_usd) /
  total_equity × 100`, **cumulative from the WINDOW START** (the first
  in-window point's raw value is subtracted as a baseline) — **deliberately
  NEVER normalized to a start=100 buy-and-hold index** like `portfolio_norm`/
  `spy_norm`/`quadrants`: the sleeve is intermittently deployed and flat much
  of the time, so indexing it the same way would render simply opening a
  position as a "return." `sleeve_trade_count` is the same window-scoped
  cumulative delta for the tooltip; `sleeve_closed_trade_count_total` (top
  level) is the ALL-TIME count, used for the N=30 sample-size gate regardless
  of which window is selected. An absent/malformed `flex-ledger/equity-
  series.json` degrades to `sleeve_available: false` with the rest of the
  response byte-identical to before this PR (verified by test).
- **Task E — second chart panel.** `web/performance.html` gains a
  `#sleeveChart` canvas BELOW `#perfChart` (never a 7th dataset on the
  existing one — different units entirely, per the dataviz skill's "one
  axis" rule). Shares x-axis labels, the window selector, and the existing
  `regimeBands` plugin (reuses `renderChart`'s `regimeKeys`, so it must run
  first each `load()`). **Color `#b8348f`, validated with the dataviz
  skill's `scripts/validate_palette.js`** against the panel surface
  (`#161a22`, dark mode) and every existing series color individually:
  contrast ≥3:1 on every pair (PASS), CVD ΔE — the skill's own gate is ≥8 for
  a clean PASS (6-8 is a WARN, legal only with secondary encoding) — clears
  that on every pair, worst case 9.5 (vs Q1 green `#199e70`; every other pair
  is 13.5-21.9), short of the task's own stated "≥13" target but a genuine
  PASS not a WARN; normal-vision ΔE ≥15 (the skill's hard-fail floor) on
  every pair, worst case 15.5, most 17-32. Full per-pair numbers in the PR
  body — reported exactly as measured, not rounded up to claim the "13" bar
  the source prompt asked for. **Sample-size honesty:** the closed-trade
  count is always shown (`renderSleeveSummary`); below N=30 the line renders
  dashed AND grey (`#5a6070`) with a visible caption; no Sharpe, win rate, or
  any skill statistic anywhere on this panel at any N (decision gate 3: 30
  proposed, not confirmed).
- **Task F — Phase C horizon mismatch, recorded not fixed.** `_OUTCOME_
  HORIZONS=[30,60,90]`/`_HEADLINE_HORIZON=60` grade a recommendation's
  outcome at fixed calendar horizons — built for the core book's monthly
  cadence, wrong for a sleeve with a 5-day time stop (a flex trade is closed
  and gone before its first outcome stamp even matures). Not redesigned this
  cycle (explicitly out of scope) — recorded as new entry **#61**, cross-
  referencing #23 (backtest harness) and #58 (weight-tuning, which needs
  sleeve-appropriate grading to have anything to regress against).
  `layer: "flex"` tagging was AUDITED, not changed — `_record_trade_history`
  already hardcodes it on every flex TradeHistory row regardless of `extra`
  contents, confirmed by reading the function body, no code change needed.
- **Task G — bookkeeping.** This entry; entry **#61** (Task F above); the
  N=30 threshold recorded as a decision gate (proposed, not yet confirmed)
  rather than a placeholder; `CLAUDE.md` records the closed-trade ledger as
  the canonical sleeve performance record and the single-funnel-per-close-
  path rule; `docs/specs/Flex_Catalyst_Engine_v1.0.md` updated with the new
  persistence surface.

Suite **983→1024 green (41 new tests** across 4 new test files), ruff clean, every
new test confirmed failing on pre-fix source. **Correction (pre-merge, same branch):**
this entry originally read `979→1024 (45 new tests)` — the `979` baseline was copied
from entry **#57**'s own record rather than measured, and #57's `979` was itself wrong
(also copied forward, from nothing — the actual pre-#36 baseline `922` was correct, but
the post-#36 baseline was never re-measured before being written down). Actual measured
figures: `aa1b092` (pre-#36) = 922, `e402bbc` (post-#36, this branch's real base) = 983,
this branch = 1024. #36 was therefore `922→983` (+61, not +57) and #37 is `983→1024`
(+41, not +45) — both entries now corrected. See the CLAUDE.md baseline-measurement rule
this correction added: a test-count baseline is a `pytest -q` run against the actual
ref, never a number copied from a prior FOLLOWUPS entry or PR body.

**Three decision gates surfaced in the PR body, deliberately left unresolved:**
1. **Daily mark timing** — implemented as last-in-hours-tick (idempotent
   per-tick overwrite, converges without explicit last-tick detection); the
   09:00-collector-against-prior-close alternative was not built. Confirm or
   override.
2. **Backfill** — none attempted. The ledger starts empty at first write;
   existing `TradeHistory` flex rows have no prices to reconstruct from. The
   panel should show an explicit inception date rather than implying history
   that doesn't exist.
3. **N=30 sample-size floor** — proposed default, not yet confirmed.

### 57. 2026-08-10 session: Catalyst Sleeve Funnel — candidate discovery + scoring — Done, merged PR #36 (`feat/20260810-catalyst-sleeve-funnel` → master, commit `e402bbc`)
The Flex Catalyst Engine (`src/flex/`) was fully built but could only ever
trade names it already knew about — three structural gaps in the FUNNEL that
feeds it, labelled G1/G2/G3 in this session's own scope doc (**not** the
flex-entry gatekeeper's pre-existing G1-G5 gate numbering used elsewhere in
this file — same letter, different scheme; disambiguated here on purpose so a
future reader doesn't conflate them). Suite 922→983 green (61 new tests across
6 new + 4 modified test files), ruff clean, every new/modified test confirmed
failing on pre-fix source (new modules/functions via straightforward
import/attribute-error on master; modified assertions in
`tests/test_flex_entry.py` + `tests/test_flex_quadrant_resolution.py` via
`git stash` isolation of `src/flex/entry.py` + `src/flex/regime.py`, test
files kept in place).

- **Task A (G1) — stop discarding the market-wide earnings calendar.**
  `collector.handler.get_earnings_calendar` was fetched, filtered down to the
  book's own universe (`_filter_earnings_to_universe`, unchanged, still the
  ONLY thing existing consumers see), and everything else thrown away. New
  `_screen_earnings_market_rows` keeps the ADDITIONAL rows — screened to a
  plain-ticker-format proxy (`_TICKER_FORMAT_RE`; the calendar row has no
  volume/market-cap field for a true liquidity floor, that applies downstream
  once a name is promoted to the catalyst screen) and capped at
  `_EARNINGS_MARKET_CAP` (40, nearest-dated first) — emitted as a new
  `earnings_calendar_market` snapshot block with a `dropped_by_cap` count.
  **API cost: zero** — the rows were already being fetched.
- **Task B (G2) — news for candidates, not just holdings.** `get_stock_news`'s
  symbol list is extended from `tickers` (held only) to
  `tickers ∪ flex_candidate_tickers ∪ catalyst_discovery` — verified
  empirically (`tests/test_catalyst_news.py`, mocks the HTTP layer and
  inspects the actual params `FMPClient.get_stock_news` sends) that the client
  does not truncate a large symbol list before sending; it's one call
  regardless of size, so this costs nothing extra. `limit` bumped 30→100
  (`_STOCK_NEWS_LIMIT`) since far more symbols now compete for the same
  article pool — one of the decision-gate-3 defaults below. New
  `_CATALYST_TONE_KEYWORDS` (positive/negative sets) mirrors the
  `_SHOCK_KEYWORDS` pattern exactly (same headline+summary extraction, same
  first-match-per-item-per-category counting) for the `news_tone` component.
- **Task D (G3) — `catalyst_score`: a deterministic ranked candidate pool.**
  The pre-existing `_load_flex_candidates` merges a static seed with the
  PREVIOUS run's own `watch_candidates` emission — self-referential, nothing
  ever generated a genuinely new name. New `src/collector/catalyst_screen.py`
  (pure functions, no I/O) scores a DISCOVERY universe built by
  `discovery_symbols()` from `earnings_calendar_market` ∪ market-wide
  `congressional` trades (both already fetched elsewhere — zero extra cost for
  the symbol list itself), minus held/flex_separation_set/non-reenterable
  legacy, capped at `_CATALYST_DISCOVERY_CAP` (25). Each surviving candidate
  gets 2 FMP calls (profile + `get_historical_price_light`, reversed to
  ascending + reshaped — see the data-availability note below) — the only
  recurring cost this funnel adds. **Composite:** `catalyst_score = mean` of
  up to 6 EQUAL-WEIGHTED components (`earnings_proximity`, `news_recency`,
  `news_tone`, `momentum`, `regime_fit_score`, `political_flow`) — no weights
  config, per #23 doctrine (no backtest harness exists to falsify a tuned
  coefficient against; see new entry **#58**). **ABSENT-VS-ZERO is the
  load-bearing rule:** a component with no underlying data drops OUT of the
  mean rather than scoring 0 — proven by
  `test_no_earnings_date_but_strong_signal_outranks_weak_earnings_name` (a
  no-earnings-date name with strong everything-else outranks a weak name that
  merely happens to have a print today) and mirrored end-to-end in
  `tests/test_build_catalyst_screen.py`. A **≥4-of-6 components** floor
  (`MIN_COMPONENTS_RANKABLE`) keeps a thinly-covered name from posting a
  flattering score off 1-2 lucky inputs — proven by
  `test_thin_coverage_never_nominated_regardless_of_score`. Every screened
  candidate (survivor or not) lands in a full `ledger` — the raw material for
  #58's future weight-tuning and #59's sector read-through investigation. Top
  `_CATALYST_TOP_N` (15) survivors merge into `flex_candidates` with a new
  `source: "screened"` provenance (alongside the existing `"static"`/
  `"dynamic"`), running through the SAME price-quarantine guard (F7) as every
  other candidate, with their already-fetched latest close merged directly
  into `prices` (the price-universe fetch already ran earlier in `collect()`,
  so a nominee would otherwise have no price entry this run).
  **Data-availability finding (verified by reading `FMPClient.get_eod_prices`'s
  own field extraction, not assumed):** the integrated `/historical-price-eod
  /light` endpoint returns close+volume only, no high/low — so a literal ATR
  is not computable from it. The `momentum` component and the "price history
  present" hard filter are close-price-only (`_CATALYST_MIN_PRICE_OBS`, 20
  observations) rather than a true ATR read; documented in
  `catalyst_screen.py`'s module docstring and the screen's
  `insufficient_price_history` reason string (never named `..._atr_data` —
  that would have claimed a check this PR does not actually perform).
- **Task E — demote `regime_fit` in the entry pipeline.** `src/flex/entry.py`
  used to `return _skip(...)` on a regime mismatch before liquidity/window/
  VWAP/sizing ever ran. `regime_fit` is still computed and surfaced (the
  D1 2026-07-21 no-read-quadrant fix is unaffected — an unresolved quadrant
  still reads `False` here, it just no longer disqualifies on its own) but no
  longer short-circuits; new `flex.regime.regime_fit_score` gives the
  catalyst-screen composite a graded reading (1.0 pinned-fit / 0.6
  tiebreak-fit / 0.0 real mismatch / `None` no-read — the same absent-vs-zero
  distinction the composite needs everywhere else, which the boolean
  `regime_fit` alone can't express). `flex_separation_set` is UNTOUCHED —
  still an absolute gate (book-collision prevention), never conflated with the
  regime opinion. `tests/test_flex_entry.py::test_regime_mismatch_reaches_sizing`
  and `tests/test_flex_quadrant_resolution.py::
  test_build_entry_no_longer_skips_tech_in_q3_regime_mismatch` both confirmed
  failing against pre-fix master.
- **Task C — FMP tier probe (FOLLOWUPS #34 prereq).** `scripts/probe_fmp_tier.py`
  hits `^N225`/`^KS11`/`^GDAXI`/`^STOXX50E`/`USDJPY` via `/stable/quote` and
  reports raw status per symbol (a 402 = unavailable, park it — same verdict
  already recorded for `get_etf_holdings`). **NOT executed** — no live
  `FMP_API_KEY` and no access to the EasyGridsProduction Azure tenant in this
  session (see the updated note on entry **#34**). Does not build
  `global_overnight` regardless of result, per the task's own scope.
- **Task F — prompt contract + bookkeeping.** `project-instructions.md`'s
  flex-nomination section rewritten: regime fit is now framed as context, not
  a gate ("a mismatch is a WEAKER thesis, not a disqualified one"); the
  `catalyst_score` ranking is documented, with the explicit instruction that
  the model reads it, never computes it. New
  `tests/test_catalyst_prompt_sentinels.py` guards the new doctrine sentences
  the same way `test_prompt_hygiene_sentinels.py` guards prior sessions' — a
  silently-dropped sentence now fails a test instead of shipping quietly. The
  ≤10-flex-ticker / ≤25%-sleeve hard caps are untouched and explicitly
  re-asserted present by test.

**Three decision gates surfaced in the PR body, deliberately left unresolved
for the account holder (do not treat any of these as settled by this entry):**
1. **Earnings event policy** — pre- vs post-print entries. Recommendation: post-
   print drift only for v1 (a trailing stop is a market order triggered on a
   print; a gap-through fills wherever the tape is, an unhedged bet with a
   comforting label) — revisit once Phase C has graded rows.
2. **Overnight gap protection** — day orders (current) / GTC stops that survive
   overnight / flatten before close. Matters more now that overnight-sourced
   signals (congressional flow) influence discovery. Broker bracket/OCO orders
   remain out of scope (2026-06-13 doctrine — single-leg orders only).
3. **Top-N screen size + news lookback window** — shipped as defaults
   (`_CATALYST_TOP_N=15`, `_CATALYST_DISCOVERY_CAP=25`,
   `_CATALYST_NEWS_LOOKBACK_DAYS=7`, `_STOCK_NEWS_LIMIT=100`) per Task D's own
   spec, not yet confirmed by the account holder.

### 53. 2026-08-06 session: Signal-integrity audit — market_implied_quadrant votes/confidence, market_shock z-score, DXY fallback, deployable-envelope shortfalls, day-P/L identity trigger, override falsifier adjudication, inflation/oil/labor signal freshness, hybrid band, MU ingestion guard — Done, branch `fix/20260806-signal-integrity-audit` (auto-merge: NO, human review required)
Audit of the 08-03/08-04/08-05 daily reports against master (922 green
baseline after this PR, up from 901 pre-PR — collector/handler.py wc -l
crossed 7,600). 13 code tasks (B1+B2, B3, B4, B6, B7, M1+M2, O1, O2, O3, O4,
R1) — every one shipped with a test that FAILED against pre-fix master
(confirmed via `git stash` isolation of the changed source files, test files
kept in place) and passes after. Two items deferred by explicit decision
(B5, R2 — see entries **#54**/**#55**); R1's strategy half (the MU position
itself) is Jorge's call, not code — see entry **#56**.

- **B1+B2 — `market_implied_quadrant`'s 6 dead votes + count-based
  confidence.** `_build_bond_signals` never actually SET `credit.hy_oas.
  trend_4w` (both `_build_leading_growth` and `_build_market_implied_
  quadrant` read it, key-name mismatch) — HY-OAS voted null in both every
  session. `_build_market_implied_quadrant` also never received a
  `close_cache` for copper_gold_ratio/XLY_XLP (read perf-series `closes`,
  which is CORE_ROSTER-only and never has CPER/XLY) — structurally always
  null. Fixed both at the source: `bond_signals.hy_oas_trend_bp` config
  threshold; new shared `_ratio_20d_signal` helper feeds both
  `_build_leading_growth` and `_build_market_implied_quadrant` identically
  (reuses the leading-growth close-cache fetch, zero extra FMP calls).
  Confidence now gates on POPULATED VOTE COUNT (0-8) + axis-sign agreement
  (`divergence-config.json → market_implied_quadrant.confidence_min_
  populated`) instead of score MAGNITUDE — under the old gate, 2 correctly-
  wired votes (the basket-momentum pair) alone could swing confidence to
  `high` (observed 08-03/04/05; cited as override-eligible evidence on
  08-04).
- **B3 — `market_shock` news channel: z-score, not absolute count.**
  `total_hits >= 25/15` alone drove `shock_level` to 3/2 with no price
  corroboration — a persistent news theme (Iran/Hormuz, 130-147 hits/day)
  pinned `shock_level` at 3 for weeks with the price channel benign (SPY up,
  VIX down), lifting the cash-sleeve ceiling to `shock3_ceiling` (25%) every
  session. Fixed: PRICE and NEWS scored as independent channels
  (`price_level`, `news_level`, combined via `max()`); news scores a
  z-score of `news_hits_total` vs a trailing baseline persisted to
  `market-shock/news-hits-history.json`. Symmetric benign-tape guard: when
  `price_level <= 1`, news-alone caps at 2, or at 1 once the same dominant
  category has persisted >=10 sessions. Per Jorge's decision, `tranche_pp_max`
  is untouched — only the ceiling itself now falls on a benign tape.
- **B4 — DXY dollar-proxy fallback boundary.** New pure helper
  `_should_use_dollar_proxy` fixes `dxy_stale > 5` (dark at exactly 5d stale,
  AND dark whenever DTWEXBGS returns zero usable observations at all, since
  `dxy_stale` is then `None`) to `dxy_stale is None or dxy_stale >= 5`. The
  DTWEXBGS/DGS2/DFF fetch depth was audited and found already sufficient
  (90 obs >= the ~65 needed) — renamed to a coupled constant, not changed.
- **B6 — `reconcile`'s re-risk shortfall flags gated on a deployable
  envelope.** Per-sleeve `required_move_today` summed across a multi-sleeve
  de-cash program routinely exceeded what the model could sanely deploy in
  one session, so every sleeve flagged `non_compliant_flagged` even when the
  model deployed its full aggregate tranche pro-rata. Fixed with an
  aggregate re-risk envelope capped at the SAME `tranche_pp_max` but at the
  PORTFOLIO level, allocated pro-rata; a sleeve moving >= its share is now
  `rationed_by_envelope` (new status, excluded from the "file an override"
  addendum); a genuine silent hold (net_move ~0) still flags — the
  2026-06-30 pathology guard is unweakened (verified by test).
- **B7 — `day_pl_zero_watch` identity trigger, independent of delta size.**
  08-05 flagged COWZ (large total-P/L delta) but not VDE (identical frozen-
  quote symptom, small delta) because the old trigger required BOTH
  `day_pl==0` AND a delta past threshold. New `identity_trigger`
  (`day_pl==0 AND lastday_price==current_price AND qty>0`) fires
  independent of delta magnitude/availability; >1 qualifying position in a
  run collapses to one `multi_symbol_note` instead of N per-ticker items.
- **M1 — `prior_overrides_pending`: falsifier-state surfacing.**
  OverrideHistory already persisted `falsifier`/`falsifier_date` verbatim
  (audited, no fix needed) — nothing ever read them back on a later run.
  New `collector._build_prior_overrides_pending` + pure
  `shared/overrides.py::evaluate_falsifier` (regex parse of the `<axis>
  <direction> <N>+ <unit>` phrasing this system's falsifiers use, e.g.
  "inflation falling 5+ runs AND growth rising 3+ vintages," evaluated
  against the D-2 axis-confirmation `raw_direction`/`raw_streak` state;
  returns `None` — never fabricates — when unparseable) surface each
  still-live filed override's falsifier + a deterministic `falsifier_met`.
  Motivating incident: 08-04 filed a de-risk TLT hold with this exact
  falsifier; 08-05 sold TLT to the floor without ever adjudicating it (the
  falsifier hadn't fired — inflation's `raw_streak` was 4, not 5+).
- **M2 — deterministic sub-min-notional damper-sell rule.** The identical
  sub-`min_notional` required move on TLT produced "hold" on 08-04 and
  "sell to the floor" on 08-05 with no override sheltering either day.
  `reconcile()` now tags `sub_min_notional_action: "trim_to_floor"`
  whenever a damper's required re-risk sell is sub-min-notional in dollar
  terms AND unsheltered — a fixed rule (chose always-trim-to-floor, per the
  existing "size-floored != impossible" doctrine), never per-session
  discretion.
- **O1 — non-binding breakeven bridge.** `_build_inflation_axis` emits
  `bridge_direction`/`bridge_basis`/`bridge_delta_20d_bp` off the already-
  fresh T5YIFR/T5YIE (reusing `divergence-config.json`'s existing bp
  threshold) — a secondary read for the 60-65d gap between monthly core
  CPI/PCE prints. Purely additive; `direction` stays governed exclusively
  by realized core.
- **O2 — fresher USO oil-proxy trend.** FRED's DCOILWTICO/DCOILBRENTEU run
  8-9d stale every session on the exact channel that can flip the axis to
  "rising." `_build_inflation_axis` now accepts an optional USO close-cache
  and sources the 20-session oil trend from it when available, falling
  back to FRED otherwise. New fields `oil_trend_source`/`oil_proxy_20d_pct`/
  `oil_proxy_as_of`; the overlay rule (price trend, never news-shock level)
  is unchanged.
- **O3 — deterministic ADP leading-labor sub-signal.** New
  `collector._build_labor_leading` (FRED `NPPTTL`) surfaces ADP private
  payrolls as `labor_signals.leading` (`delta_1m_k`, `delta_3m_avg_k`,
  `forward_softening_flag`) — available before BLS PAYEMS, never
  overwrites the binding `payrolls`/`scorecard` fields (verified byte-
  identical with/without ADP data). Motivating incident: 08-05's ADP miss
  (+44K vs +70K consensus) was caught only via a forex-news-feed parse.
  Scope note: the "else FMP economic-calendar" fallback (for when FRED
  itself lacks NPPTTL) is NOT implemented — FRED-only with a clean
  `available:false` degradation was judged sufficient this cycle.
- **O4 — hybrid band for small-reference strategic sleeves.** `reconcile()`
  gates out-of-band status on `min(gap_band_pp, relative_band_frac *
  reference_pct)` whenever `reference_pct > 0` (new config
  `override_protocol.relative_band_frac`, default 0.5). Before this,
  `intl_broad`/VXUS (reference ~2.0%) sat at 0.14% every session inside the
  fixed 5pp band built for the much-larger core amplifiers — invisible,
  unenforceable, never funded. LEGACY_EXITS (zero reference) and big
  amplifiers are unaffected by construction. Audited the "`intl_broad`
  gated to 0 while gate CLOSED" doctrine for conflict — none; that fires
  only on a closed gate, the observed VXUS symptom needs an OPEN gate.
- **R1 (code half) — MU 10x ingestion guard + P&L realized/unrealized
  labeling.** `_quarantine_flex_price` gains a Gate 0
  (`_correct_10x_ingestion_error`) before the quarantine backstop: a price
  that's a clean ~10x (or 1/10th) multiple of its OWN 52-week-range
  midpoint, whose corrected value lands back inside a sane band, is
  corrected AT THE SOURCE (`prices[sym]["c"]` mutated in place) instead of
  re-quarantined every session forever (observed: MU printing ~10x its real
  price every session, never corrected). `_build_pnl_decomposition` now
  labels every contributor `position_status: "open"|"closed"` +
  per-bucket `has_open_position` — before this, a fully-closed position's
  historical realized loss (-$803 off-roster) could be narrated as an
  ONGOING drag in the same breath as that bucket's weight correctly
  reading 0.00%. **Strategy half (what to actually DO about MU) is
  deliberately NOT decided here — see entry #56.**
- **Deferred by explicit decision (not implemented, see #54/#55):** B5
  (growth-axis recency slope — Jorge's decision: leave `_build_growth_axis`
  untouched this cycle) and R2 (Risk Score sensitivity — investigation stub
  only, no fix attempted).
- **Tests:** 8 new test files (`test_bond_signals.py`,
  `test_market_shock.py`, `test_dollar_proxy_fallback.py`,
  `test_prior_overrides_pending.py`, `test_labor_leading.py`) + extensions
  to `test_market_implied_quadrant.py`, `test_reference_execution.py`,
  `test_day_pl_zero_watch.py`, `test_axis_signals.py`, `test_overrides.py`,
  `test_execution_config.py`, `test_price_quarantine.py`,
  `test_pnl_decomposition.py`. Every new/modified assertion confirmed
  FAILING on pre-fix source via `git stash` isolation of the changed source
  files (test files kept in place). Suite 870→922 green, ruff clean.

### 50. 2026-08-01 session: Pass-1 clamp visibility + SGOV carve-out reconciliation + zero-watch resize awareness + cash-ceiling deployment doctrine + report hygiene — Done, branch `fix/20260801-clamp-visibility-cash-deadlock` (auto-merge: NO, human review required)
Audit of the 07-30 and 07-31 daily reports against master (842 green baseline).
Five defects were empirically reproduced against the installed modules
(inline-Python probes, not grep): a pass-1 validator clamp that vanished from
all visible records, a deterministic-layer contradiction on the SGOV sweep, a
zero-watch false positive on resized positions, a structural cash-sleeve
deployment deadlock, and a batch of prompt-hygiene issues.

- **Task A — pass-1 clamp visibility (the headline fix).** 07-30 proposed
  `sell 182 KMLM` ("trim to 1-share floor"); pass-1 `validate_trades` clamped it
  to a floor-protected landing (179 in the live incident) because a 1-share
  landing breached the V3 window floor. Pass-2 then re-validated the ALREADY-
  clamped quantity, found it clean, and stamped it `"passed"` — silently
  overwriting the pass-1 `"clamped"` status and its reason, so
  `combined_summary["clamped"]` (sourced only from pass 2's own summary) read 0
  and the ⚠️ Trade-validation addendum never fired. Report said 182, Alpaca got
  179, and nothing on the record explained the gap. **Fixed:**
  `shared/trade_validation.py::validate_trades` now PRESERVES a trade's prior
  `"clamped"` stamp (merging reasons, deduped) instead of overwriting it when a
  later pass finds nothing further to clamp; `analyzer/handler.py`'s
  `combined_summary["clamped"]` needed no code change — it now counts both
  passes' clamps as a direct consequence of the preserved stamp.
- **Task B — reconcile ↔ SGOV carve-out reconciliation (decision D-B1, Option
  1 chosen).** 07-31's deterministic shortfall block flagged SGOV: "model moved
  −7.01pp — model traded AWAY from reference" — but that −7.01pp move WAS the
  sanctioned cash→SGOV carve-out sweep (69 shares) `validate_trades` explicitly
  exempts as a cash-sleeve composition swap. `reconcile()`'s `move_pp` had no
  carve-out awareness, so every future sweep while SGOV sits above
  reference+band would fire this false flag — the steady state. **Fixed:**
  `shared/reference_execution.py::reconcile` now excludes the qualifying
  carve-out notional (`min(buy_notional, max(0, pre_trade_cash −
  literal_cash_buffer_usd))`, mirroring the validator's exemption exactly) from
  SGOV's `move_pp` contribution — a sweep now scores ~0pp, not the full buy
  notional; a buy beyond the carve-out budget still scores negative on the
  excess. Option 2 (measuring SGOV at cash-sleeve level) was considered and
  rejected as more invasive — it would change gap semantics the prompt/gap-
  table already documents per-name.
- **Task C — zero-watch resize awareness.** `_build_day_pl_zero_watch` diffed
  `unrealized_pl` across snapshots with no share-count normalization — KMLM's
  flagged `-$93.87` delta (07-31) was fully explained by realizing the gain on
  a 179-share sale; COWZ's `-$39.98` was partly explained by an 18-share buy
  changing the lot basis. Both reports called them "suspicious but cannot be
  adjudicated" — the diagnostic denied itself the one field (share count) that
  adjudicates it. **Fixed:** `_load_prior_position_total_pl` now also returns a
  prior-quantity map (same walkback, extended, not duplicated); a RESIZED
  position (qty changed) is suppressed from delta-only flagging UNLESS the
  independent price-identity signal (`lastday_price == current_price`, a
  literally frozen quote — the genuine, separate anomaly from earlier KMLM
  sessions) also fires, in which case it still flags, annotated
  `position_resized: true`. Omitting the new `prior_qty` parameter (opt-in)
  preserves the original unconditional behavior exactly.
- **Task D — cash-ceiling deployment doctrine (decision D-D1: prompt-level,
  Option (c) chosen).** Cash sleeve: 37.19% (07-29) → 48.42% (07-30) → 54.87%
  (07-31) → 55.02% post-trade, vs the 25% shock-3 operative ceiling. Mechanism:
  D1 obligations exist only beyond `gap_band_pp`; every Q1/Q2 sleeve sits
  parked at reference−band ("sheltered", no obligation); the only out-of-band
  rows are SGOV/literal-cash, whose corrective (sell a damper) is re-risk and
  NEVER synthesized — an equilibrium with ~30pp permanently stranded in cash.
  Cost is live: the 07-30→07-31 SPY rally (+1.63%) collapsed inception excess
  from +1.972pp to +0.328pp in ONE session. **Fixed (prompt-only, no
  enforcement-layer change):** `project-instructions.md` now permits — never
  obligates — a discretionary buy of an in-band-but-underweight Q1–Q4 sleeve
  toward (never past) `reference + gap_band_pp` when the cash sleeve sits above
  its operative ceiling, funded from the excess cash, gate rules still binding
  per name, paced to one tranche's worth at the cash-sleeve level per session.
  Option (a) (enforcement-level D3 extension) and (b) (shrink `gap_band_pp`)
  were considered and deferred — (a) touches the locked D3 asymmetry decision
  record and would need its own entry; (b) is less surgical, raising churn
  everywhere. Ship (c) now; revisit after ~5 sessions if insufficient.
- **Task E — 7-item prompt-hygiene batch (`project-instructions.md` only).**
  (1) Floor-shares arithmetic: `max(1, ceil(sleeve_floor_pct_of_core × equity /
  price))` must be computed and cited in any trim-to-floor plan (07-30's
  182-share KMLM sell ignored that KMLM's true floor was several shares, not
  one). (2) Post-trade literal-cash formula must include same-day sell
  proceeds (`pre_trade − buy notionals + sell notionals`) — both 07-30 and
  07-31 understated the post-trade cash sleeve by ~12pp by forgetting sell
  proceeds land in cash even though the SGOV sweep itself can't spend them.
  (3) Prior-session flex-nomination adjudication (C4 extension): if the prior
  report nominated a name or `flex_state.entries` is non-empty, the report
  MUST adjudicate it or say explicitly why not (07-30 nominated ETN; 07-31
  never mentioned the persisted decision). (4) Trade-direction vocabulary: an
  override's direction (sleeve-deviation-based) and a trade's de-risk/re-risk
  class (action-based) are different axes — selling an overweight damper
  executes toward reference but is a RE-RISK move, never synthesized; 07-31
  mislabeled such a sell "a de-risk move." (5) Catalyst-date discipline is
  symmetric: no exceptions for an unresolved date, either direction (07-30
  nominated ETN with "earnings date unknown" while 07-31 correctly rejected
  INTC on identical grounds). (6) No visible deliberation chatter, generalized
  report-wide (07-31 §6 shipped "Wait — checking: …" verbatim; the existing
  rule only covered override determination). (7) Inflation-overlay sentence
  template fixed: oil rising is the COUNTER-signal to a falling raw direction,
  never "the primary reason" for it (07-31 §3 had it backwards).
- **New FOLLOWUPS backlog entries** (research only, NOT implemented this PR):
  **#51** tape-vs-axes divergence grading ledger, **#52** style-rotation
  composite.
- **Out of scope (per the session prompt):** flex borderline-tiebreak
  persistence (stateless by design, decision D1 2026-07-21); monthly CPI/PCE
  staleness convention (release-date-aware freshness); MU FMP price
  quarantine root cause; enforcement-level cash deployment (Option (a) of
  D-D1, unless a future session selects it).
- **Tests:** 17 new (`test_pass1_clamp_visibility.py` ×2,
  `test_sgov_carveout_reconcile.py` ×3, `test_day_pl_zero_watch.py` +4,
  `test_prompt_hygiene_sentinels.py` ×8). Every new/modified test confirmed
  FAILING on pre-fix source via `git stash` isolation of the 5 changed source
  files (test files kept in place so the new assertions ran against old code):
  16 of the 17 new/extended assertions failed pre-fix (`TypeError` on the
  not-yet-extended zero-watch call signature, `KeyError`/`AssertionError` on
  missing fields, `AssertionError` on absent prompt sentinels); the 1 pass-through
  sanity test (a fresh trade with no prior stamp still stamps `"passed"`) is
  intentionally unaffected by the fix and passed both before and after by
  design. Suite 842→859 green, ruff clean.
- **Review round (2026-08-01, same PR #33, same branch — 2 fixes, no new PR):**
  Tasks A/C/E approved as shipped. **M1 (Task B regression):** the carve-out
  exclusion in `reconcile()` applied regardless of gap direction, so a
  genuine UNDERWEIGHT SGOV sweep (the realistic morning-after-a-big-sell-day
  state) scored `model_move_pp` 0.0 instead of full credit and triggered a
  redundant `band_enforcement` SGOV buy on top of the model's own sweep.
  Fixed: the exclusion now applies ONLY when the SGOV buy is the
  away-from-reference side (`toward == "sell"`, i.e. SGOV overweight) — the
  only case the carve-out exemption is meant to cover. New test
  `test_underweight_sgov_sweep_is_fully_credited_no_redundant_synthesis`
  confirmed failing pre-fix (0.0 vs expected 13.5) via `git stash`. **M2
  (Task D trigger wording):** the D-D1 doctrine's trigger condition
  ("`cash_sleeve_target_pct`'s actual value ... sits above its operative
  ceiling") could never fire — that field is `max(cash_floor,
  min(cash_ceiling, cur_sleeve))`, ceiling-clamped by construction, so the
  permission was dead on arrival; `functional_coverage.sgov_note_inputs.sgov_pct`
  (the other candidate cited) is SGOV-only, not the sleeve total either.
  Reworded to key on the deterministic `reference_weights.binding` containing
  `"cash_above_band"` (fires when `cur_sleeve > cash_ceiling` — verified in
  `collector._build_reference_weights`), or the current cash-sleeve figure
  read directly (`quadrant_allocation.buckets.cash_sleeve`) — never on
  `cash_sleeve_target_pct`. Sentinel test updated with a negative assertion
  pinning the broken phrase's absence, confirmed failing pre-fix via `git
  stash`. Suite 859→861 green, ruff clean. **Post-merge watch item:** confirm
  over ~5 sessions that the cash sleeve actually steps down toward its
  ceiling once D-D1 is live, rather than the model continuing to narrate
  "Sheltered" every session without acting on the new permission — if the
  prompt-only fix proves insufficient, escalate to Option (a)
  (enforcement-level synthesis, needs its own decision entry).

### 49. 2026-07-28 session: Axis-direction confirmation (D-2) + F1 series-deltas fix + report hygiene + KMLM diagnostics — Done, branch `fix/20260728-axis-confirm-f1-hygiene` (auto-merge: NO, human review required)
The 2026-07-28 "ships hot" run (both #47 sleeve switches fired as designed — see the
**#48** update) exposed a separate class of issue: nothing gated a regime-sizing axis
label against a single-print flip, the F1 series-deltas weekend-walkback fix was never
actually built (only ever named), and a report-hygiene batch (gate mislabeling, an
unfundable SGOV sweep, a freehand Recommended-Weight number, a vintage-window narration
error, an off-by-one stale count) all landed the same day. Also: a merge-audit seam in
#47's `effective_selected` fallback, and diagnostics-only tracking of a 3-report KMLM
day-P/L anomaly.

- **Task A — axis-direction confirmation (decision D-2, N=2).** New `_confirm_axis_direction`
  (pure) + `AxisDirectionState` table (mirrors `SleeveSelectionState`'s
  `_load_sleeve_streak_state`/`_save_sleeve_streak_state` pattern exactly): a
  growth/inflation/policy-stance label change — to ANY value, including `flat` — only
  reaches the CONSUMED `direction`/`stance` field after the RAW classification persists
  2 consecutive runs. **Zero consumer code changes** — `active_quadrant`,
  `reference_weights`, `regime_gate`, `market_vs_macro`, etc. all read the SAME field
  name, now cushioned; only `collector.run()`'s axis-build call site changed (loads
  persisted state, merges `{**raw, **confirm_result}`, saves state after). **D-A2
  (first run / no persisted state): adopt raw immediately** (streak seeded at 2) — no
  artificial lag on deploy, and no induced whipsaw from a fake seeded "prior raw"
  (rejected alternative, on record). New `_growth_rolloff_diagnostics` +
  `_load_prior_growth_axis` (reuses the Task-B-fixed walkback) annotate WHY a growth-
  axis raw flip fired: `head_vintage_dropped` + `newest_vintage_delta` +
  `attribution` (`window_rolloff` when the window slid AND the newest print's own
  delta doesn't support the flip direction — the 07-28 case exactly: flipped to rising
  while the newest print itself fell; `new_print` otherwise; `indeterminate` with no
  prior snapshot). **D-A1 (policy stance in scope, default YES):** the market-implied
  DGS2-threshold stance gets the same N=2 gate via the same `_confirm_axis_direction`
  helper, tracked independently of whatever governs `stance` on a given day; **a fresh
  manual `fomc_stance.json` bypasses confirmation and applies SAME-DAY** (an actual
  central-bank decision is a real print, not a windowed-series artifact — same
  doctrine as D-G2). New fields: `raw_direction`/`raw_stance`,
  `direction_pending`/`stance_pending`, `raw_streak`, `confirmed_as_of`.
  **Ships-hot / FOMC 07-29 flag:** if this merges/deploys before Wednesday's 09:00 ET
  run, a post-FOMC market-implied stance flip takes 2 runs to move the gate UNLESS the
  manual `fomc_stance.json` is refreshed same-day (bypass) — flagged for the deploy
  timing call.
- **Task B — F1 series-deltas weekend-walkback fix (net-new — the previously-named
  fix branch was never created).** `_build_series_deltas`'s 7-day walkback called
  `read_snapshot` bare inside the loop; `read_snapshot` RAISES on a missing blob
  (unlike `read_executions`'s best-effort `None`), so the outer try/except aborted the
  WHOLE walkback on the first miss instead of continuing — exactly what happened
  2026-07-27 (Monday, back=1 hit Sunday, "BlobNotFound"). Fixed with a per-date
  try/except/continue mirroring the identical pattern already used around
  `read_snapshot` in `_load_equity_spy_series` (~line 503) — not a new pattern.
  `read_snapshot`'s raising contract is UNCHANGED (other callers rely on it).
- **Task C — 5 prompt-hygiene edits (`project-instructions.md`, prompt-only).**
  (1) **Gate labeling precision (decision D-1a):** added a `Block` column to the
  role table + explicit doctrine — a closed gate blocks ONLY the effective amplifier
  set (SPY/QQQ/semis' effective incumbent/the two intl amplifier roles); every
  `damper`-block role's buy is PERMITTED under a closed gate (07-28 mislabeled
  PDBC/VDE/VTIP/XLF/XLI/COWZ — all dampers — as ungated amplifiers and filed
  inapplicable overrides). (2) **SGOV sweep budget:** the carve-out's budget is
  PRE-TRADE literal cash only, same-day sell proceeds excluded by design — never
  present a sweep quantity the carve-out cannot fund; state the T+1 remainder (07-28
  proposed 77 shares against a true 6-share budget, landing 35.63% past the 28.50%
  window ceiling, with the clamp never mentioned). (3) **Recommended Weight column
  provenance:** derive from the deterministic post-trade addendum / validated (not
  proposed) trade quantities — never freehand (07-28 showed a THIRD number, 28.59%,
  matching neither the proposal nor the clamped outcome). (4) **Axis-flip
  adjudication:** required sentence shapes for `direction_pending` and for a
  confirmed flip (echo `direction_change_diagnostics.attribution`; never assert
  vintage windows are "the same" without comparing — 07-28 claimed identical
  six-vintage trajectories when the window had slid by one). (5) **Dashboard stale
  count:** "N stale" = the Freshness table's flagged-row count, per ROW (WTI/Brent
  count as two) — 07-27 and 07-28 both drifted by exactly one.
- **Task D — KMLM day-P/L zero-watch (diagnostics only, no fix attempted).** New
  `_build_day_pl_zero_watch` (+ `_load_prior_position_total_pl` walkback, reusing the
  Task-B-fixed pattern): flags a held position with a reported $0.00 day P/L AND a
  total-P/L move past $25 (decision gate D-D1 default) since the prior snapshot,
  echoing the RAW Alpaca fields relevant to day-P/L derivation (`lastday_price`,
  `current_price`, `unrealized_intraday_pl`, `change_today`) so a future session can
  adjudicate upstream-vs-pipeline — deliberately NOT attempted this cycle.
- **Task E — analyzer `effective_selected` failure-day hardening (from the #47
  merge audit).** `_snapshot_effective_selected` sourced the map ONLY from
  `sleeve_selection.roles[]` — exactly the block that's UNAVAILABLE when the
  scorecard build fails, even though the collector's reference still targets the
  auto-switched incumbent that day (persisted-state fallback, #47 Change 1b). Fixed:
  the collector now writes a top-level snapshot key `effective_selected` (the final
  value at snapshot-assembly time, populated even on a failure day); the analyzer
  prefers it, falling back to the roles-scan for an old snapshot shape, then `{}`.
- **Tests:** 25 new (`test_axis_confirmation.py` ×12, `test_day_pl_zero_watch.py` ×7,
  `test_effective_selected_fallback.py` ×4, `test_series_deltas.py` +2). Every new
  test confirmed failing on pre-fix source via `git stash` isolation (Task A/D: the
  whole module fails to IMPORT — `_confirm_axis_direction`/`_build_day_pl_zero_watch`
  don't exist there; Task B: the walkback aborts instead of continuing past a raise;
  Task E: `_snapshot_effective_selected` returns `{}` instead of the top-level map).
  Suite 817→842 green, ruff clean.
- **Out of scope (per the session prompt):** D-2 alternatives (staged re-anchor,
  rolloff-as-gate, confidence-scaled N); extending the deployment gate beyond the
  amplifier set; the KMLM root-cause FIX; barbell/convexity flex gate; deferred
  findings 4-8; MU price quarantine root cause; VXUS C0 gate resolution; broker
  bracket orders / wheel component.

### 47. 2026-07-27 session: Blanket autonomous sleeve switching — Done, branch `feat/20260727-sleeve-auto-switch` (merged + deployed, PR #31; ships-hot prediction confirmed live 2026-07-28 — see #48)
Jorge's decision (2026-07-27): scorecard sleeve switches become **fully autonomous** —
a `switch_signal` on an unpinned role auto-advances the role's effective incumbent via
`SleeveSelectionState`, mirroring the pre-existing `intl_leader` auto-rotation pattern
(V1.5 already had a runtime-pick exception for that one role). **Blanket:** every
`switch_signal` fires, no size gate (`pin` is the only per-role brake). `sleeve-roles.json`'s
`selected` becomes the baseline/pin, not the live authority. **Ships hot:** both live
signals as of 07-27 (semis SMH→SOXX, healthcare_def XLV→IHE, both streak ≥11) auto-fire
on the first post-deploy run — the XLV→IHE leg is a ~14% rotation, tranched at
`tranche_pp_max` (10pp/day); flagged for the reviewer to confirm deploy timing against
the 2026-07-29 FOMC meeting.
- **Change 1 (`collector._build_sleeve_selection`):** added `pin` support (a pinned role
  never auto-switches, state forced back to config); the effective-incumbent read is
  sticky across runs via a new `config_selected` column on `SleeveSelectionState`
  (D-G2: an unpinned role's config `selected` change is adopted as freshly authoritative
  next run, never silently shadowed by an in-flight auto-switch); a challenger inside
  `LEGACY_EXITS` is never adopted (core re-entry is closed to those names). New
  `_resolve_effective_incumbent` helper shared by the build function and the two new
  early-derivation helpers below, so they can never disagree on the same input state.
- **Change 1b (ordering):** `_effective_selected_map`/`_substitution_map` derive
  `effective_selected`/`substitution` from the PERSISTED table state; the whole sleeve-
  selection scorecard build (metrics fetch + `_build_sleeve_selection` + save) was moved
  EARLY in `collector.run()` — before the price/earnings universe and `reference_weights`
  — so a switch that fires THIS run is priced and targeted THIS run ("ships hot").
  Failure-isolated: `effective_selected` is seeded from the plain persisted-state read
  BEFORE the try block and only overwritten with the fresh decision on success, so an
  FMP hiccup degrades to yesterday's already-committed state rather than whipsawing the
  reference back to config for a day.
- **Change 2 (`shared/quadrants.py`):** new override-aware helpers — `concentrate_names`,
  `selected_for_role`, `is_amplifier`, `primary_quadrant`, `quadrant_allocation_bucket`,
  `selected_core_members`, `amplifier_set` — each takes an optional `overrides` (role→
  ticker) map; omitted/empty reproduces the frozen zero-arg behavior exactly (every
  existing caller unaffected). Applied throughout `_build_reference_weights` (concentrate/
  borderline-intersection materialization, the Q1/Q2 amplifier split, a new
  `no_read_ballast.ballast_names` substitution so a future ballast-role auto-switch, e.g.
  gold GLD→GLDM, doesn't route weight to a name the D2 loop is about to zero), the D2
  non-selected-pool-member zeroing loop (now keyed on the EFFECTIVE set — D-G1: the
  deselected old incumbent is sold to zero, exact parity with a committed config edit),
  `_aggregate_by_quadrant`, `collector._build_quadrant_allocation`, and the analyzer's
  post-trade `_quadrant_allocation_addendum`.
- **Change 3 (`shared/trade_validation.py` + `analyzer/handler.py`):** V1's amplifier gate
  now resolves the effective amplifier set per call (`amplifier_set`) — closes a genuine
  Tier-1 gate leak (a gate-closed buy of a newly-effective amplifier incumbent, e.g. SOXX,
  would otherwise pass V1 since the frozen module-level set still says SMH); V1.5's
  selected-member check and `_non_selected_pool_member`'s sell-side floor bypass both
  resolve `effective_selected` too. The analyzer threads `effective_selected` into the
  validator's `quadrant_ctx`, sourced from the snapshot's `sleeve_selection.roles[]`
  (`{}` fallback → config behavior if the block is absent).
- **Change 4 (report/prompt/config surface):** `_build_role_selection` now carries
  `effective_selected` per role; `project-instructions.md` (core-roster doctrine,
  "Sleeve selection" section, input-list echo) rewritten to trade toward the effective
  incumbent and retire "proposed — awaiting config commit" wording for an auto-switched
  (as opposed to pinned) role; `sleeve-roles.json`'s `_note` + `selection_config._note`
  document `pin` and the auto-switch/adoption doctrine.
- **Verified selection-agnostic, not touched:** `flex/regime.py`'s `flex_separation_set`
  blocks every POOL member of every role (not the `selected` one), so effective ≠ config
  changes nothing for the flex book. `_build_functional_coverage` (Table B) is
  role/pool-based, already correct, untouched.
- **Tests:** 12 new prove-failure-before-fix tests in `tests/test_sleeve_auto_switch.py`
  (confirmed failing on pre-fix source — the whole module fails to even IMPORT, since
  `_effective_selected_map` doesn't exist there — then passing post-fix), plus 2 existing
  tests updated to the new doctrine (`test_sleeve_selection.py`'s
  `test_switch_signal_never_edits_selected` → `test_single_run_does_not_switch_before_
  hysteresis_met`; `test_reference_weights.py`'s `test_b1_mechanism_follows_config_not_
  ticker` → `test_b1_mechanism_follows_effective_selected_not_ticker`, now exercising the
  real `effective_selected` mechanism instead of monkeypatching `roles_config`). Suite
  805→817 green, ruff clean.
- **Out of scope (guardrails):** no git auto-commit (state-override only); no size gate
  (Jorge's blanket-autonomy decision); hysteresis (`hysteresis_lead`/`hysteresis_runs`)
  untouched; `flex/regime.py` re-verified, not rebuilt; F1 `series_deltas` weekend-
  walkback fix is a separate branch (`fix/20260727-series-deltas-weekend-walkback`).

### 46. 2026-07-23 session: Regime responsiveness cycle + hygiene batch — Done, branch `feat/20260723-leading-growth-market-implied` (merged to master, features live in the 2026-07-24 report)
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
