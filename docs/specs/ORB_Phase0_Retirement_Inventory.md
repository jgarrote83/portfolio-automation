# ORB Phase 0 — Flex / DayTrade Lab Retirement Inventory

**Status:** Part A deliverable (prepare and map). No runtime behavior changed; nothing deleted. Part B (the removal) is a separate prompt, issued only after review and after production is confirmed flat (see `docs/runbooks/ORB_Phase0_Flex_Shutdown.md`).
**Spec:** `docs/specs/ORB_Engine_v1.0.md`, Phase 0. **Base:** `master` @ `1b6dc3a`. **Branch:** `feat/orb-phase0a-retirement-prep`.

**Class legend.** DELETE = used only by Flex/DayTrade. DECOUPLE = shared with Core; keep it, remove only the Flex dependency. PORT = worth carrying into `src/orb/` (copied, never imported). KEEP = history or false positive. UNSURE = a question for Jorge.

**How the evidence was gathered.** Four read-only passes (collector+shared; analyzer/executor/learning; web/infra/docs/scripts; flex+daytrade modules and tests) over a case-insensitive grep for `flex|daytrade|catalyst|watch_candidates|FLEXC-|FLEXD-|sleeve_contribution|sleeve_trade_count|FLEX_SLEEVE_CAP_PCT` across `src/`, `tests/`, `web/`, `infra/`, `.github/`, `scripts/`, `docs/specs/`, `CLAUDE.md`, `FOLLOWUPS.md`, `README.md`. Everything was read from source; **no tests or code were run against these claims** except the baseline below. Line numbers are against this branch and will drift as Part B lands; re-grep before editing. A few "reader/writer" cells were inferred from grep rather than read through and are marked where the passes said so.

## 1. Measured baseline (on `master`, before any change)

```
PYTHONPATH=src python -m pytest -q | tail -1   ->  1609 passed in 8.44s
ruff check .                                   ->  All checks passed!
```
After Part A the branch is measured again in `.review/phase0a-summary.md`.

## 2. Headline findings (read first)

1. **Hard import coupling — deleting `src/flex/` or `src/daytrade/` first breaks everything.** `src/function_app.py:10,13` imports both packages and it is the single entry point, so the collector, analyzer and executor registrations would fail too. Core also imports Flex directly, in one file: `src/collector/handler.py:50-53` (+ function-local re-import at `:467`) pulls `flex.trades` (only `read_kill_switch_state`, `:3625`), `flex.config.load_flex_config` (only `min_adv_usd`, `:3519`), `flex.indicators.avg_dollar_volume` (`:1123`) and `flex.separation` (`FLEX_REENTERABLE`, `flex_separation_set`, 13 sites). Nothing in Core imports `daytrade`. Part B must cut these before deleting the packages.
2. **The analyzer refuses to start if the prompt loses its Flex section.** `analyzer/handler.py:101,111-118,185` (`assert_flex_prompt_schema`) raises unless `project-instructions.md` contains `FLEX_SCHEMA_V1` and `flex_nominations`. The assertion and the prompt text must go in the **same commit**.
3. **`stock_news` is shared with Core's market-shock detector — the one place a Flex removal can move a Core number.** `collector/handler.py:2820-2823` widens the FMP stock-news symbol list to held ∪ flex candidates ∪ movers (limit 100). The same `stock_news` feeds `_build_market_shock` (`:2999`), whose `news_hits_total` z-score (baseline persisted in `market-shock/news-hits-history.json`) drives `shock_level` and the cash-sleeve ceiling. Shrinking the list changes that count against its baseline. **Decided (U2, §8a):** restore held-only symbols with a fresh `basis: "held_only"` baseline.
4. **Core's thematic overlay borrows two Flex things.** Its quarantine set `_tc_quarantined` (`:3329-3332`) is built from flex candidate profiles; it routes non-roster tickers "to flex" (`:7227`, `:7510-7517`, prompt `routed_to_flex`). Both need a Core-side disposition (resolved for the second by reviewer decision R4 below).
5. **Core never subtracts a Flex budget.** `core_room = 100 − cash_sleeve_target − intl_total_pct` (`collector/handler.py:9502`). The 25% is prompt prose plus Flex/DayTrade code defaults only; deleting it changes no Core weight.
6. **Old snapshots stay replayable.** No code reads `flex_state`, `flex_quadrant`, `flex_candidates`, `flex_eligibility`, `flex_conviction`, `flex_kill_switch`, `catalyst_screen` or `earnings_calendar_market` back from a snapshot — they are only written (`collector/handler.py:3923-3965`).
7. **Core's accounting does not attribute Flex.** `execution_review`, equity reconciliation and `external_flows` have no Flex dependency. `_build_pnl_decomposition` buckets by symbol, so Flex fills in non-roster names land in `off_roster_flex`; ORB fills in a Core pool symbol would mix into `core_current` FIFO cost basis (see Phase 4 requirements, §5).
8. **A pre-existing defect surfaced.** `src/learning/bundle.py:47` and `src/learning/schema.py:25` allowlist `config/flex-candidates.json`, but the real path is `src/config/flex-candidates.json` (there is no repo-root `config/`). `fetch_live_config` calls `raise_for_status()`, so that entry would 404. Not in FOLLOWUPS; Part B removes the entry and records it (reviewer decision R6).
9. **`FLEX_ENABLED` is `'true'` in `infra/modules/functionapp.bicep:117`; `DAYTRADE_ENABLED` is `'false'` (`:122`).** Both gates default to `"false"` in code (`src/function_app.py:107,159`), so deleting the Bicep lines means off. No other `FLEX_*`/`DAYTRADE_*` setting exists in Bicep — everything else runs on code defaults. Per CLAUDE.md, change it in Bicep, never `az` alone.

## 3. Reviewer decisions folded in (from the interim review of the analyzer/executor/learning questions)

| # | Decision | Where it lands |
| --- | --- | --- |
| R1 | ORB does **not** write TradeHistory, its reasoning enums, or `track_record`; its record is the `orb-trades` ledger. Part B stops writing the flex-only TradeHistory columns (`flex_source`, `entry_date`, `entry_price`, flex reasoning enums); **never drop historical rows or columns.** | Part B commit B4 |
| R2 | Core ignores ORB by construction (ORB is flat at every close; Core snapshots at 09:00). Recorded as **Phase 4 requirements, not Part B** — see §5. | §5 |
| R3 | `_cancel_conflicting_orders`: **no change in Part B.** Phase 4 requirement — see §5. | §5 |
| R4 | Thematic non-roster tickers: keep behavior (no Core reference); rename the reason for **new** rows from `routed_to_flex` to `non_roster_report_only`; leave historical rows untouched. | Part B commit B2 |
| R5 | `"top-named rotation candidate"` is Flex text (`project-instructions.md:424`, under the conviction-path heading at `:334`, inside `252-568`). **Delete that sentinel**; `regional_rotation` itself is Core. | Part B commit B4 |
| R6 | The `learning/bundle.py:47` + `learning/schema.py:25` path defect is new. Part B removes the entry and records the defect in FOLLOWUPS. **ORB config does not join the learning allowlist.** | Part B commit B5 |

## 4. The ten items, resolved (evidence in the appendices)

**(a) Catalyst screen, `flex_candidates`, `watch_candidates`, `_build_price_universe`.** Nothing Core-*decisioning* consumes the screen output: `catalyst_screen.py` is imported only at `collector/handler.py:61` and used only in `_build_catalyst_screen` (`:1063-1210`) and the discovery block; no code in `src/analyzer`, `src/executor`, `src/shared` or `web` reads it — only the LLM prompt does (`project-instructions.md:428-525`). `flex_candidates` is likewise prompt-only, but its tickers flow into five collector places. **DELETE:** `catalyst_screen`, `earnings_calendar_market`, `watch_candidates` (analyzer sanitizer `:1343-1381`, next-day reader `collector:495`), the `flex_candidates` profiles. **DECOUPLE:** `_build_price_universe` (`:876-898` — drop the `flex_candidate_tickers` term; it only adds price rows for non-Core names, so Core is unaffected; `tests/test_price_universe.py` pins the signature), `_filter_earnings_to_universe` (`:1021-1034`, drop only the flex term), the `stock_news` symbol list (finding 3) and `_tc_quarantined` (finding 4). `watch_candidates` is read by exactly one thing — the next day's `_load_flex_candidates`; the executor and validator ignore it.

**(b) `flex_conviction` vs `thematic_conviction`, `ThematicHistory`.** Two systems sharing one table and three pure helpers. `thematic_conviction` is **Core** (floor lift in `_build_reference_weights`; writer `analyzer/handler.py:1526-1590`, `path:"core_thematic"`; stamper `_stamp_thematic_outcomes` `:2113-2210`; calibration `:2212-2245`). `flex_conviction` is **Flex** (`FlexConvictionState`; writer `_write_flex_conviction_history` `:1592-1644`, `path:"flex_conviction"`, RK `FLEXCV-`; stamper `:2247-2316`; calibration `:2318-2345`). **Keep** `_thematic_brier`, `_thematic_damping_factor`, `_thematic_ladder_lookup` (Flex calibration calls them; Core never calls Flex). Core isolation is already enforced by `path eq 'core_thematic'` filters (`collector:2139,2228`) plus the core stamper keying off each row's own stored horizons — so **removing the Flex stamper, calibration and writer cannot change Core calibration**. Do **not** delete those filters, and keep `_backfill_thematic_history_path` (an idempotent no-op once no untagged rows remain) until that is confirmed. Existing `flex_conviction` rows stay as inert history.

**(c) Reconciliation, `execution_review`, equity reconciliation, P&L decomposition.** `_build_flex_reconciliation` (`:4295-4320`) and `_flex_pos_qty` are Flex-only → DELETE (this removes the only "broker holds something no engine tracks" alarm; ORB needs its own equivalent — §5). `execution_review` reads only `daily-executions`, and `_build_equity_reconciliation` / `external_flows` are account-level → KEEP, no Flex dependency. `_build_pnl_decomposition` (`:10636-10730`) is symbol-based and uses all Alpaca fills since inception → a closed Flex position's realized P&L stays in `off_roster_flex` forever (history, fine); renaming that bucket is U5. Historical snapshots replay unchanged (finding 6).

**(d) Executor.** No reliance on `FLEXC-`/`FLEXD-`/`flex-` prefixes and no flex ledger reads anywhere in `executor/handler.py` (one docstring at `:310`; its own order id is `{date}-{trade_id}`, `:393`). `_cancel_conflicting_orders` (`:305-348`) cancels every open order on the symbol, symbol match only; the defensive sell filter (`:163-200`) trims to total held qty. Neither is Flex-specific. **No Part B change** (R3); the ORB-collision rule is a Phase 4 requirement (§5).

**(e) Analyzer.** The `FLEX_SCHEMA_V1` load-time assertion (finding 2) and its test file `tests/test_flex_prompt_schema.py` go together with the prompt text. CI has no flex-specific step (`ci.yml` is ruff + `pytest -q`; the "CI assertion" is that test file). No analyzer-side parse of `flex_nominations[]` exists (validation lives in `src/flex`); `_split_response` only sanitizes `watch_candidates`. `project-instructions.md` (2582 lines): Flex-only blocks `252-568` (conviction path `334-409`, eligibility `411-427`, catalyst_screen `428-493`, flex_state `495-568`), `2358-2414` (`flex_nominations` schema), `2299-2315` (`watch_candidates`), input bullets `1621-1626` and `1736-1763`; about 25 interleaved places need surgical edits (list in Appendix B). Sentinel tests to delete: `test_flex_prompt_schema.py`, `test_catalyst_prompt_sentinels.py`, and seven flex-only tuple+test pairs in `test_prompt_hygiene_sentinels.py` (adjudication, catalyst-date symmetry, conviction path, eligibility incl. R5's "top-named rotation candidate", applicable-set, cash-floor, `flex_state.as_of`).

**(f) `/api/performance`.** Every `sleeve_*` field is Flex-derived; none carries the Core "sleeve roles"/quadrant meaning. `sleeve_available`, `sleeve_closed_trade_count_total` and the per-point `sleeve_contribution_pp`/`sleeve_trade_count` come from `_sleeve_series` (`web/api/function_app.py:715`) and `_attach_sleeve_series` (`:754`, call sites `:968`, `:1036`), reading only `flex-ledger/equity-series.json` (`:764`). Front end: `web/performance.html:42-46` (`#sleeveChart`, `#sleeve-summary`) and `web/performance.js:19-47,395-466`. An absent blob degrades to `sleeve_available:false` with the response otherwise unchanged. The Core "sleeve" in this API = quadrant baskets/roles — KEEP. The page nav has no Flex/DayTrade link. Per the spec, Phase 0 removes the panel and fields; Phase 4 rebuilds them as `orb_*`.

**(g) Learning Loop.** The bundle never reads `flex-ledger` or flex-state; Flex data reaches the reviewer only via all TradeHistory rows (including historical `layer:"flex"`), the 35 days of report markdown, live config and FOLLOWUPS. Removing `flex-candidates.json` breaks no code (the allowlist is a tuple membership check) but needs edits: `learning/bundle.py:47`, `learning/schema.py:25`, `tests/test_learning_schema.py:141-150` (`test_allowlist_accepts_all_four_files`), `learning-review-instructions.md:53,151` and its worked example `:92-103`, and `Learning_Loop_v1.0.md`. No allowlist copy was found in `web/api/learning_github.py` (re-verify in Part B). See R6 and finding 8.

**(h) `FLEX_*` / `DAYTRADE_*` settings.** Bicep sets only `FLEX_ENABLED='true'` (`functionapp.bicep:117`) and `DAYTRADE_ENABLED='false'` (`:122`); `main.bicep`, `parameters.prod.json`, the workflows and the compiled `main.json` contain none. The rest are env knobs with code defaults: about 20 `FLEX_*` in `src/flex/config.py` (~146-169) and about 30 `DAYTRADE_*` in `src/daytrade/config.py` (~153-187), plus `DAYTRADE_SECTOR_PULSE_MAP` (`daytrade/pulse.py:80`); all run on defaults. The full table is in Appendix C. Other "Flex" hits in `infra/`, `deploy-code.yml` and `README` refer to the *Flex Consumption* hosting plan — **KEEP, false positives**.

**(i) Containers and tables (archive, never delete).** None is defined in Bicep (`storage.bicep:4-10` lists only the daily blobs and `deployment`); all are created at runtime. Blob containers — Flex: `flex-ledger` (`ledger.json`, `closed-trades.json`, `equity-series.json`, `kill-switch.json`), `flex-state`, `flex-decisions`, `flex-executions`; DayTrade: `daytrade-ledger`, `daytrade-nominations`, `daytrade-state` (incl. `halt.json`), `daytrade-log`, `daytrade-grades`. Tables: `FlexConvictionState` (Flex-only); `TradeHistory` (rows `layer:"flex"`) and `ThematicHistory` (rows `path:"flex_conviction"`) are **shared with Core — never drop**. Writers and readers per name are in Appendix D and in runbook step 6. Cross-reads to note: `daytrade/handler.py:44` reads `flex-ledger/ledger.json`; `flex/handler.py:642` reads `daytrade-ledger/ledger.json`; the collector reads `flex-ledger/kill-switch.json` and `flex-state/{date}.json`; the web API reads `flex-ledger/equity-series.json`.

**(j) `FLEX_SLEEVE_CAP_PCT`.** Readers: `flex/config.py:59,148`, `daytrade/config.py:64,112-114,155` (+ `daytrade/sizing.py:3,33`), `flex/handler.py:198,206,232`, `flex/entry.py:77-80`; `flex/killswitch.py:3` is a docstring. **It is not set anywhere in `infra/` or `.github/`** — the 25.0 code default always applies. Config-file literals with **no code reader**: `risk-limits.json` `flex_sleeve_cap_pct` (soft 15 / hard 25) and `single_name_cap_pct.flex` 4.0 (mirrored, also unread, in `collector/handler.py:111,113`). Prose: `project-instructions.md:282,648`. Tests pinning it: `test_daytrade_separation.py:70-76`, `test_daytrade_sizing.py:80-84`, `test_flex_news_momentum_b1.py:262`, `test_flex_sizing.py:50-54`, `test_catalyst_prompt_sentinels.py:81`. **Do not confuse** it with Core's cash-sleeve `shock3_ceiling 25.0`. The spec's rename to `ORB_SLEEVE_CAP_PCT` (25) is therefore a *new ORB config value*, not an edit to a deployed setting; after Part B nothing reads the old name.

## 5. Phase 4 requirements recorded from review (NOT Part B; do not act on these now)

- **R2 — Core/ORB separation.** ORB's exclusion set = all Core pool members ∪ **every `LEGACY_EXITS` name (U1: INTC/MCK/PPA/EUAD are plain legacy exits)** ∪ symbols held or with open orders at pre-open ∪ symbols in today's `daily-trades` file. The collector labels any overnight ORB-ledger position an **ORB exception + alert**, never Core off-roster. ORB realized P&L becomes an explicit equity-bridge attribution line (this is also what protects `core_current` FIFO from ORB fills in a Core pool symbol).
- **R3 — order ownership.** ORB-owned = `ORB-` prefix **or** order id in the ORB ledger's `order_ids` (bracket/OTO child legs carry broker UUIDs, not the prefix — CLAUDE.md, B2 §8.2). A Core trade on an ORB-owned symbol is **skipped** (status `orb_symbol_conflict`, ERROR log) and never cancels ORB orders. Same rule for the defensive sell filter.
- **Port-adaptation list found during the module review (inputs to Phase 4):**
  1. Flex/DayTrade P&L, `risk_per_share`, the reconcile stop side and `held <= eps` are **long-only**; ORB shorts need side-signed P&L, buy-side stops and negative `held`.
  2. Reconcile models no **pending-entry** state, so an open ORB stop *entry* would be treated as closed-at-broker.
  3. `flex/handler.py` falls back to `[]` when the broker read fails, which makes every ledger row look closed; copy DayTrade's `broker_unreachable` early return instead.
  4. `_coid` appends a random uuid, so a retry is **not** idempotent; ORB ids must be deterministic `ORB-{date}-{symbol}-{kind}`.
  5. `trading_days_between` is weekday-count and holiday-blind (ORB holds intraday, so likely unneeded).
  6. `record_closed_trade` is read-modify-write without an ETag: fine for one writer, revisit at a 1-minute cadence.
  7. Kill-switch drawdown is measured on account equity (decision G-11); consider sleeve notional for ORB.

## 6. PORT candidates (Phase 4 ports adapted copies — never imported, never copied in Part B)

**PORT source: commit `1b6dc3a3f95a2639f2b5434322ff9d6da3055fbb`** (`master` when Part A began; it contains all of `src/flex/` and `src/daytrade/`, and Part A changes only docs, one script and its tests). **Part B is purely subtractive (U10) — nothing is copied into `src/orb/` in Part B.** Phase 4 ports *adapted* copies from this SHA, applying the §5 adaptation list. **When Part B is cut, replace this SHA with Part B's actual merge-base** (the last commit still containing the engines) if `master` has moved on to anything that touches `src/flex/`, `src/daytrade/` or their tests; every `file:line` in this section refers to that SHA.

| Source | Carry | Change for ORB |
| --- | --- | --- |
| `flex/trades.py:42-181` closed-trade builders (`record_closed_trade`, `fills_from_activities`, `merge_broker_fills`, `build_closed_trade`) + `flex/handler.py:695-744` `_finalize_closed_trade` | Broker-truth fills, idempotency on `trade_id`, null-safe P&L (never fabricated), **no record when zero confirmed entry fills** | Shorts (side-signed), `risk_per_share` from the 10%-ATR stop, fields → `rel_volume/direction/atr14/spec_version/slippage`, exit vocab `stop|eod_flat|manual|kill`, container `orb-trades` |
| `flex/reconcile.py:36-151`, `daytrade/reconcile.py:35-75` | Reconcile-first skeleton, phantom order-id clearing, no-naked-long repair, orphan scan with `engine_owned` | Pending-entry state, shorts, `broker_unreachable` early return (DayTrade's, not Flex's), ledger-keyed ownership with prefix + `order_ids` as the two proofs |
| `flex/killswitch.py:58-201` + `flex/trades.py:186-201` | Pure two-trip switch, sticky, absent-vs-zero hit rate | Thresholds from ORB config; sleeve-notional drawdown basis; caveats in the runbook |
| `flex/handler.py:966-970` `_coid`, `:310-362` prefix test + `_sweep_orphan_orders` | Strict own-prefix scoping | Deterministic ids (see §5) |
| `daytrade/handler.py:630-662` `_session_minutes`, `_utc`; `flex/handler.py:832-859` `_session_minutes_remaining`; clock gate `flex/handler.py:95-105` | **Calendar-derived** session window (correct on half days), minutes-to-close from calendar `close` | Window −5..+close; the clock gate must not block the flatten-before-close tick; use `zoneinfo`/`shared.timeutil`, not `astimezone()` |
| `daytrade/patterns.py:49,62,222` `cumulative_vwap`, `opening_range`, `is_print_stale`; `flex/indicators.py` `atr14`, `avg_dollar_volume`, `session_vwap` | Pure indicators | ORB is 5-min with the stop at 10% of ATR and no VWAP/volume confirm; ATR needs daily high/low (Alpaca bars have them; FMP light does not) |
| `daytrade/grading.py` `net_r`, `outcome_of`, haircut; `daytrade/state.py` weekly breaker | R-multiple grading, halt pattern | Daily loss limit per spec |
| `daytrade/config.py`, `flex/config.py` pattern | Env-overridable frozen dataclass with `spec_version` + `__post_init__` validation | ORB `config.py` per spec |
| Test helpers: `tests/test_flex_close_paths.py:20-62` (`blob_store` fixture, `_StubAlpaca`), `test_flex_order_hygiene.py:26-36`, `test_flex_state_persistence.py:28-115`, `test_daytrade_separation.py:29-126` (prefix disjointness, source-scan "never imports X"), S1 kill-switch tests `test_flex_news_momentum_b1.py:440-528` | Stub clients, blob monkeypatch fixtures | Add `get_clock`, `get_calendar`, `list_positions`, `list_orders`, `get_account`, short positions |
| Collector `catalyst_screen.py` `mean_abs_daily_move_pct`, `volume_surge_from_bars`, `hours_since_latest_news`; FMP `get_most_actives`/`get_biggest_gainers`; `get_aftermarket_quote`/float | Only if ORB wants a screen | The spec builds ORB's universe from Alpaca daily bars — **decided: port none** (U4) |

The shared Alpaca client methods added for these engines (`get_bars`, `get_latest_quote`, `replace_order`, `get_calendar`, `get_activities`, `get_order`) live in `shared/clients/alpaca.py` and several are used by Core — **KEEP, nothing to remove**. The `shared/storage.py` JSON/JSONL helpers likewise (Core uses them; fix the "used by the flex engine" comments only).

## 7. Test inventory

Baseline: 1609 tests (§1).

- **DELETE with the packages (19 files):** `test_daytrade_breakers`, `_gates`, `_grading`, `_patterns`, `_separation`, `_sizing`; `test_flex_close_paths`, `_conviction_entry`, `_conviction_exit_state`, `_conviction_wiring`, `_entry`, `_exit`, `_indicators`, `_news_momentum_b1`, `_order_hygiene`, `_reconcile`, `_separation`, `_sizing`, `_state_persistence`, `_trades`. The helpers and S1 tests worth reusing stay retrievable at the PORT source SHA (top of §6); nothing is ported in Part B. `test_flex_separation.py` is **RETARGET, not delete,** (U1: `separation` moves to `shared/`) (ORB needs the isolation regression tests — spec "Shared account" risk).
- **EDIT (Core code under test):** `test_flex_dynamic_candidates.py` (imports `flex.separation` at 141, 150, 162, 170, 221; also holds unrelated reference-weights/VXUS gate tests at ~406-478 that must be **kept or moved**); `test_flex_eligibility.py` (imports `flex.separation:21`; delete with `_build_flex_eligibility`); `test_prompt_hygiene_sentinels.py` (remove the 7 flex tuples+tests, keep the rest); `test_learning_schema.py:141-150`; `test_thematic_history_path_isolation.py` (**EDIT, not delete** — it covers the Core M-B fix); `test_price_universe.py` and `test_sleeve_auto_switch.py:285` (the `flex_candidate_tickers` kwarg); `test_pnl_decomposition.py` (if the bucket is renamed); `test_track_record.py` (13 flex hits); `test_sleeve_performance_api.py` (EDIT/DELETE with the panel).
- **DELETE with the collector/analyzer code they exercise:** `test_catalyst_screen.py`, `test_catalyst_news.py`, `test_build_catalyst_screen.py`, `test_catalyst_prompt_sentinels.py`, `test_flex_prompt_schema.py`, `test_write_flex_conviction_history.py`, `test_flex_review.py` (dead code), `test_flex_reconciliation.py`, `test_flex_conviction_hysteresis.py`, `test_flex_conviction_pure.py`, `test_conviction_damping.py`.
- **KEEP (incidental vocabulary):** `test_axis_signals.py` and `test_transition_watch_confirmation_sources.py` (the `FLEXCPIM159SFRBATL` series), `test_cash_floor_guard.py`, `test_price_quarantine.py` (while those functions stay), `test_earnings_market.py`, `test_earnings_universe.py`, `test_equity_reconciliation.py`, `test_executor_order_conflict.py`, `test_functional_coverage.py`, `test_off_roster_gaps.py`, `test_quadrant_*`, `test_reconcile_validate_sequencing.py`, `test_reference_weights.py`, `test_series_deltas.py`, the thematic tests, `test_validation_addendum.py`, `test_orb_phase0_flat_check.py` (ORB).

## 8. Decisions and open questions

### 8a. U1–U11 — resolved by the reviewer (fix-up round)

| # | Decision | Effect on Part B / Phase 4 |
| --- | --- | --- |
| **U1** | Move `flex/separation.py` **verbatim** to `src/shared/separation.py`, no semantic change in Part B. INTC/MCK/PPA/EUAD become **plain legacy exits**. | Part B commit B1. Phase 4 requirement added to §5: ORB's exclusion set also contains every `LEGACY_EXITS` name. |
| **U2** | Restore held-only news symbols **and start a fresh baseline with no production write.** Every new `news-hits-history` row carries `basis: "held_only"`; the baseline counts only rows with that basis; older rows stay in the blob, unused, until they roll out of the 20-session window. This deliberately enters the detector's designed no-baseline mode (watch floor, `news_baseline_min_sessions` = 10 — `risk-limits.json:191`, read at `collector/handler.py:11638`; history read/written at `:2992`, `:3026`), which errs defensive; the price channel is unaffected. | Part B commit B2. Called out in its PR evidence. This is the one **additive** field in Part B. |
| **U3** | Delete the quarantine and 10× correction; pass an empty quarantine set to the thematic overlay. | B2/B3. Part B's replay must show thematic outputs **unchanged** for a recent snapshot. |
| **U4** | No ports from the catalyst screen. | — |
| **U5** | Keep the `off_roster_flex` name in Part B; revisit with the Phase 4 equity-bridge line. | — |
| **U6** | Delete the inert Flex keys in `risk-limits.json`, **provided** the runbook's `LearningProposals` query (step 6) shows no proposal targets them. | B8, gated on that query. |
| **U7** | Delete `scripts/probe_dynamic_candidates.py`; keep `scripts/probe_fmp_tier.py`. | B7. |
| **U8** | SUPERSEDED banners / notes, not rewrites (`growth_strategy_spec_v1.md` §7, `roster_revision_2026-07.md`, `Learning_Loop_v1.0.md`, `Phase_C…`). | B10. |
| **U9** | Remove the sleeve panel; the archived `flex-ledger/equity-series.json` stays. | B6. |
| **U10 (changed)** | **No ports in Part B — Part B is purely subtractive.** The pre-deletion commit SHA is the "PORT source" (top of §6); Phase 4 ports adapted copies from it, applying the §5 adaptation list. `test_flex_separation.py` still moves with `shared/separation.py`. | The old B2 (ports) is dropped from §9. |
| **U11** | Becomes the runbook's `LearningProposals` query (step 6). | Runbook only. |

### 8b. New UNSURE flags — Azure changes outside `rg-portfolio-automation-prod` (standing rule 8 / 10)

Reviewed `infra/`, `.github/workflows/`, and `scripts/` for anything that deploys or writes outside the resource group. **Nothing was changed.** Every `az deployment`, `az functionapp`, `az eventgrid`, `az staticwebapp` call in the workflows and `infra/deploy.ps1` names the resource group explicitly (`deploy-code.yml:33,81,97,119,128`, `deploy-infra.yml:21,48,56`, `deploy-web.yml:15-17,38-40`, `infra/deploy.ps1:14,35-49`); all Bicep modules deploy at resource-group scope (no `targetScope` override). Items that touch scope above the RG, each **UNSURE — Jorge to decide whether to act**:

1. `infra/deploy.ps1:30` — `az group create --name $RG --location $LOCATION`. Creates the resource group itself when absent: a **subscription-scope write**, although idempotent and scoped to the named RG (the file's header, `:2`, says "no other resource groups are touched"). `deploy-infra.yml:40-41` takes the opposite stance for CI (it fails if the RG is missing; "CI is scoped Contributor on the RG, not the subscription").
2. `infra/modules/keyvault-roles.bicep:13` and `infra/modules/storage-roles.bicep:13,24,35,48` — `subscriptionResourceId('Microsoft.Authorization/roleDefinitions', …)`. These **reference** subscription-scoped built-in role *definitions*; the assignments themselves are scoped to the vault / storage account (`scope: keyVault` / `scope: storageAccount`), inside the RG. Not a write outside the RG; listed only because role assignments were on the review list.
3. `infra/modules/keyvault.bicep:12` — `subscription().tenantId`: a read-only tenant lookup, no write.
4. **Resource-provider registration.** CLAUDE.md ("EventGrid blob-trigger" lesson) says `Microsoft.EventGrid` must be `Registered` or storage publishes zero events. Registration is a **subscription-level action**, and no `az provider register` appears anywhere in `infra/`, `.github/` or `scripts/` (searched) — it is an undocumented manual step done outside the repo. Flagged so it is never done implicitly by ORB work.
5. **CI identity setup.** The workflows log in with OIDC (`AZURE_CLIENT_ID`/`AZURE_TENANT_ID`/`AZURE_SUBSCRIPTION_ID` secrets, `deploy-*.yml`). The Entra app registration, federated credential and the RG-scoped Contributor role assignment are **Entra/subscription-level** and live outside this repo (not verifiable here). Any ORB deploy change that needs a new role (e.g. for a new Function) would be above RG scope for the CI identity — needs approval first.
6. `scripts/seed-swa-secrets.sh:55-70` — `az keyvault secret set --vault-name kv-pfauto-prod` (data-plane writes inside the RG's vault). It pins the vault name but **does not pass `--subscription` or `-g`**, so it writes to whichever subscription the shell is currently on; the header comment gives `-g rg-portfolio-automation-prod` only for the *source* commands. With a wrong default subscription it fails to find the vault rather than writing elsewhere, but it does not satisfy "every `az` command names the RG/subscription explicitly". Not changed (out of scope).
7. `scripts/probe_*.py` / `scripts/run_baseline.py` docstrings — `az storage blob download* --account-name stpfautoprod` (read-only data-plane, no `--subscription`). Reads only.

### 8c. Still open

None of U1–U11 remains open. The only new items needing a Jorge call are the 8b flags (default: leave as is; they pre-date ORB and none is touched by Phase 0).

## 9. Revised Part B plan — ordered commits, each leaving the suite green and ruff clean

**Precondition.** Part B **coding** may start before FLAT; **merging and deploying wait for two FLAT readings with the kill-switch trip confirmed** (runbook steps 3-5), history archived (step 7) and the `LearningProposals` query (step 6) showing no proposal targets the Flex files/keys.

Part B is **purely subtractive** (no ports). The only non-deletions are the verbatim file move in B1 and U2's `basis` tag in B2.

1. **B1 — move the separation module.** `flex/separation.py` → `shared/separation.py` verbatim; repoint the collector (`:53`, `:467`, the 13 sites) and the tests that import it (`test_flex_separation`, `test_flex_dynamic_candidates`, `test_flex_eligibility`). Behavior unchanged.
2. **B2 — decouple Core-adjacent collector code.** Drop `flex_candidate_tickers` from `_build_price_universe` and the earnings universe; restore held-only `stock_news` symbols with the U2 `basis: "held_only"` tag and basis-filtered baseline (unit tests for the no-baseline fallback and for old-basis rows being ignored); delete the quarantine/10× helpers and pass an empty `_tc_quarantined` (U3); rename the reason for **new** thematic rows `routed_to_flex` → `non_roster_report_only` (R4; historical rows untouched) with the prompt phrase and tests.
3. **B3 — remove collector Flex code.** `catalyst_screen.py`, `_build_catalyst_screen`, `_load_flex_candidates`, flex candidate profiles, `earnings_calendar_market`, `_build_flex_eligibility`, the flex-conviction path (`_conviction_*`, `_base_rate_up`, state load/save/confirm, `_build_flex_conviction`, `_stamp_flex_conviction_outcomes`, `_build_flex_conviction_calibration`), `_build_flex_reconciliation`, the flex_state and kill-switch echoes, `_build_flex_review` (dead), the flex keys in `_RISK_LIMITS_DEFAULTS`, and with them every remaining `flex.*` import (their only callers are being deleted, so nothing needs copying). Stop writing the seven snapshot keys. **Keep** the `_thematic_*` helpers, the `path eq 'core_thematic'` filters and `_backfill_thematic_history_path`. Delete the matching tests (§7).
4. **B4 — analyzer + prompt (one commit for the assertion and the text).** Remove `assert_flex_prompt_schema`/`_FLEX_SCHEMA_SENTINELS`, `_write_flex_conviction_history`, the `watch_candidates` sanitizer, and the flex-only TradeHistory columns (R1: stop writing `flex_source`, `entry_date`, `entry_price` and the flex reasoning enums; **drop no historical rows or columns**); remove the Flex-only prompt blocks and surgically edit the interleaved places (Appendix B); delete the sentinel tests, including the R5 sentinel.
5. **B5 — Learning Loop.** Remove `config/flex-candidates.json` from `learning/bundle.py:47`, `learning/schema.py:25`, `learning-review-instructions.md` and `test_learning_schema.py`; record the path defect in FOLLOWUPS (R6). ORB config does **not** join the allowlist.
6. **B6 — web.** Remove `_sleeve_series`/`_attach_sleeve_series` and both call sites, the `#sleeveChart` panel and its JS, and `test_sleeve_performance_api.py`; `/api/performance` otherwise byte-identical.
7. **B7 — delete the engines.** `src/flex/`, `src/daytrade/`, the five registrations and two imports in `function_app.py`, `flex-candidates.json`, `flex-review.json`, `scripts/probe_dynamic_candidates.py`, the remaining flex/daytrade tests.
8. **B8 — config.** Remove the inert Flex keys from `risk-limits.json` (U6, gated on the runbook query). `ORB_SLEEVE_CAP_PCT` (25) is introduced by ORB's `config.py`, not here.
9. **B9 — infra.** Remove `FLEX_ENABLED` and `DAYTRADE_ENABLED` from `functionapp.bicep` (comments included). Editing `infra/**` self-triggers the infra deploy — expected; the deployment names `rg-portfolio-automation-prod` explicitly (rule 10). Never `az` alone.
10. **B10 — docs.** SUPERSEDED banners on `Flex_Catalyst_Engine_v1.0.md` and `DayTrade_Lab_v0.1.md`; notes per U8; `README.md:12,18`; edit the Flex sections of `CLAUDE.md`; mark the Flex-related FOLLOWUPS entries superseded (#58, #59, #61, #63, #71-76, #90, #92, #104, #107, #108, plus the Flex parts of #98, #82, #34 and #67 — Appendix C lists them; the Core "sleeve" entries #95, #96, #37, #86 are **not** Flex).

Each commit's PR-body evidence: measured before/after test counts (CLAUDE.md rule); for B2/B3 a replayed-snapshot check that Core outputs (`reference_weights`, `regime_gate`, thematic outputs) are unchanged for a recent snapshot — except `market_shock`'s news channel, whose U2 baseline restart is called out explicitly.

---

# Appendices — evidence tables from the four read-only passes

Row format: file · symbol and lines · what it does · what consumes it · class. Appendix A = collector/shared; B = analyzer/executor/learning/prompt; C = web/infra/workflows/scripts/docs/FOLLOWUPS; D = `src/flex` + `src/daytrade` modules, kill switch, test files, blobs and order-id prefixes. **`Q1`, `Q2`… labels inside the appendices are each pass's own question numbers and are superseded by §3 (answered) and §8 (open).** Counts such as "13 sites" and all line numbers were taken from the working tree on this branch and will drift during Part B.

## Appendix A — Collector and shared

### 0. Headline hazards (read first)

1. **Hard import coupling.** `H:50-53` imports `flex.trades`, `flex.config`, `flex.indicators`, `flex.separation` at module top, and `H:467` re-imports `flex.separation` inside `_load_flex_candidates`. `src/function_app.py:10,13` imports `daytrade.handler` and `flex.handler`. Deleting `src/flex/` or `src/daytrade/` before cutting these breaks collector import AND every function registration (analyzer/executor included, since `function_app.py` is the single entry point).
2. **`stock_news` is shared with Core's market-shock detector.** `H:2820-2823` widens the FMP stock-news symbol list to `tickers + flex_candidate_tickers + mover symbols` (limit 100, `H:338`); that same `stock_news` is passed to `_build_market_shock(stock_news=...)` (`H:2999`) and pooled into the news keyword count (`H:11567`) that drives `news_hits_total` -> z-score -> `shock_level` -> cash-sleeve ceiling (Core). It is also a snapshot key (`H:3927`) the analyzer sees. Shrinking it back to held-only changes `news_hits_total` against the persisted baseline `market-shock/news-hits-history.json` (`H:2992`). This is the one place a Flex removal can move a Core number.
3. **Core thematic overlay reads Flex's quarantine set.** `H:3329-3332` builds `_tc_quarantined` from `flex_candidate_profiles[].price_quarantined` and feeds it to `_build_thematic_conviction`. With no flex candidates the set is empty (see 1b-DECOUPLE).
4. **Core thematic overlay routes unmapped tickers "to flex".** `H:7227` (`status: "flex_route"`) and `H:7510-7517` (`reason: routed_to_flex, flex_source: "thematic"`). Prompt text echoes `routed_to_flex` (`project-instructions.md:1724,2449`). Needs a new disposition when Flex is gone.
5. **Analyzer prompt-load gate.** `src/analyzer/handler.py:101,111-116,185` raises at load if `project-instructions.md` lacks `FLEX_SCHEMA_V1`/`flex_nominations`. Not collector scope but it will hard-fail the analyzer the moment the prompt's Flex section is removed.
6. **Core reference weights do NOT reserve a Flex budget in code.** `core_room = 100 - cash_sleeve_target - intl_total_pct` (`H:9502`); the "reserve room for flex / 25%" is prompt prose only (`project-instructions.md:282,648`). Removing Flex does not change any Core number; it only changes prose.

### 1. Row table

| file | symbol & lines | what it does | what consumes it | class |
|---|---|---|---|---|
| H | imports `H:50-53`, `H:61`, `H:467` | pulls flex.trades/config/indicators/separation + catalyst_screen | only Flex-feeding collector code below (see per-symbol rows) | DELETE (remove after dependents are cut; `flex_separation_set` semantics UNSURE, see Q1) |
| H | `_FLEX_CANDIDATES_FILE` `H:68`, `_FLEX_REVIEW_FILE` `H:70`, `_FLEX_REVIEW_DEFAULTS` `H:278-285`, `_load_flex_review_config` `H:564-580` | config loaders for flex candidates / flex review | `_load_flex_candidates`; `_load_flex_review_config` has zero call sites (grep: only its def) | DELETE (flex-review.json too: only reader is the dead loader) |
| H | `_FLEX_TIEBREAK_WINDOW_D` `H:82-84`, `_r5_from_closes` `H:6012-6025` | 5d-return helper for the retired flex_quadrant tiebreak | no live caller (`_build_flex_quadrant` no longer exists; only a docstring mention in catalyst_screen.py:210) | DELETE (dead code) |
| H | `_RISK_LIMITS_DEFAULTS` flex keys `H:111` (`single_name_cap_pct.flex`), `H:113` (`flex_sleeve_cap_pct`) | in-module mirror of risk-limits.json | no reader: only `any_name_soft` (`H:9205`) is ever read; `flex_sleeve_cap_pct` has no reader anywhere in src (grep) | DELETE the two flex keys (keep `any_name_soft`) |
| H | `_RISK_LIMITS_DEFAULTS["conviction"]` `H:127-146` (edge ladder, `catalyst_size_mult`, brier damping) | config for the flex conviction path | `_build_flex_conviction`, `_build_flex_conviction_calibration` (`H:2324`), `_conviction_*` helpers | DELETE (NOT `thematic_conviction` at `H:155-160`, which is Core) |
| src/config/risk-limits.json | `single_name_cap_pct.flex` (l.11), `flex_sleeve_cap_pct` (l.29-33), `conviction` block (~l.100+), `price_quarantine` (l.210 note) | flex caps/ladders/quarantine thresholds | flex caps: no code reader. `conviction`: flex path only. `price_quarantine`: `_quarantine_flex_price` only | DELETE flex caps + `conviction`; `price_quarantine` DELETE unless Core quarantine wanted (Q3) |
| src/config/flex-candidates.json | whole file | static non-held seed list (ETN, NEE, XLU, MU, INTC, MCK, PPA, EUAD) | `_load_flex_candidates` (`H:441-560`); `scripts/probe_dynamic_candidates.py` patches it | DELETE |
| src/config/flex-review.json | whole file | review knobs | only dead `_load_flex_review_config` | DELETE |
| src/config/macro-series.json | `FLEXCPIM159SFRBATL` l.46 | Atlanta Fed **flexible-price CPI**, name collision only | `H:6444` inflation-quality (Core) | KEEP (false positive) |
| H | `_load_flex_candidates` `H:441-560` | static seed + prior-day analyzer `watch_candidates`, minus held / separation set / non-reenterable legacy, cap 20 | `run()` `H:2650`; feeds `_build_price_universe`, earnings universe `H:2770`, news symbols `H:2822`, catalyst exclude `H:2800`, profiles `H:2664`, quarantine loop `H:2955` | DELETE (all consumers either Flex-only or DECOUPLE rows below) |
| H | `flex_candidate_profiles` build + `source` tag `H:2664-2668` and snapshot key `"flex_candidates"` `H:3923` | FMP profiles for candidates | analyzer prompt only (`project-instructions.md:1621`); no analyzer code reads it (grep of src/analyzer, executor, web: none) | DELETE |
| H | `_build_price_universe` `H:876-898` (param `flex_candidate_tickers`) | EOD price fetch list = held + selected-core + ETF watchlist + leading-growth extras + flex candidates | `prices` -> everything Core | **DECOUPLE**: drop the `+ flex_candidate_tickers` term and param (call `H:2911`); Core members are unaffected because only non-core names are dropped. `tests/test_price_universe.py` pins the signature |
| H | `_correct_10x_ingestion_error` `H:904-935` + `_quarantine_flex_price` `H:940-1018` (call `H:2955`, `H:3537`) | 10x correction (mutates `prices[sym]["c"]`) + 52w/1-day quarantine, applied ONLY to flex candidate profiles | flex_candidates, thematic `_tc_quarantined`, flex eligibility | DELETE unless Core wants it (it never touched Core names: it iterates flex profiles only). See Q3 |
| H | `_tc_quarantined` `H:3329-3332` | Core thematic overlay exclusion set sourced from flex quarantine | `_build_thematic_conviction` (Core, `H:7500+` eligibility) | **DECOUPLE**: replace with empty set or a Core-side quarantine (Q3) |
| H | `_filter_earnings_to_universe` `H:1021-1034`, `_earn_universe` `H:2769-2770` | clip market-wide earnings to held U selected U **flex candidates** U held legacy | snapshot `earnings_calendar` (Core analyzer prompt) | **DECOUPLE**: remove `set(flex_candidate_tickers)` term only; function itself is Core |
| H | `_screen_earnings_market_rows` `H:1037-1060`, `_EARNINGS_MARKET_CAP`, `earnings_calendar_market` `H:2776-2788`, snapshot `H:3925` | additional capped market-wide earnings rows | only `_build_catalyst_screen` + prompt `project-instructions.md:432` | DELETE |
| src/collector/catalyst_screen.py | whole file (pure scoring funcs: rankability, movers_discovery_symbols, components) | catalyst_score + discovery | only `H` Flex path (`H:61,1099-1205,2824-2836`) + tests; no Core import | DELETE (PORT candidates: `mean_abs_daily_move_pct`, `volume_surge_from_bars`, `hours_since_latest_news` if ORB wants a screen; Jorge decides) |
| H | catalyst constants `H:296-362` (`_CATALYST_*`, `_FLEX_NEWS_WINDOW_H`, `_FLEX_MIN_PRICE_USD`, `_CATALYST_TONE_KEYWORDS`, `_COARSE_TRIGGER` `H:386-388`) | tuning for the screen | `_build_catalyst_screen`; `_COARSE_TRIGGER` also by `_build_track_record` (see row below) | DELETE (keep `_COARSE_TRIGGER` only if track_record flex layer is kept) |
| H | `_build_catalyst_screen` `H:1063-1210`, run() block `H:3443-3555`, snapshot `"catalyst_screen"` `H:3926` | scores discovery universe, ranks, nominates into flex_candidates (`source:"movers"`) | prompt `project-instructions.md:428-525`; flex engine reads `flex_nominations` not this; NO Core consumer (reference_weights, quarantine, price universe all read `flex_candidate_tickers` only, which are the movers merged AFTER price fetch at `H:3544`, so prices for them come from a separate bars fetch `H:3546-3548`) | DELETE |
| H | mover discovery + news in run() `H:2790-2840` | `get_most_actives`/`get_biggest_gainers`, widened `get_stock_news` | **`stock_news` also feeds Core `_build_market_shock`** | **DECOUPLE** (hazard 2): restore `_news_symbols = tickers` (or keep widened + document baseline shift; Q2). Remove movers fetch |
| src/shared/clients/fmp.py | `get_most_actives`/`get_biggest_gainers` (l.~126-135), `get_shares_float`/`get_sec_filings`/`get_aftermarket_quote` (l.~136-170, "DayTrade Lab") | movers + DayTrade-Lab endpoints | movers: collector `H:2811`; DayTrade fns: `src/daytrade/*` only | movers DELETE-or-PORT (ORB may want movers; Q4); DayTrade fns DELETE (PORT `get_aftermarket_quote`/float if ORB uses them) |
| src/shared/clients/alpaca.py | `get_bars` l.154-195, `get_latest_quote` l.197-214 (docstrings cite Flex/DayTrade spec), `replace_order` l.126 | intraday bars/quotes/order replace | flex + daytrade engines; `get_activities`/`get_order`/`get_calendar` are used by Core collector | **PORT** (ORB needs minute bars/quotes); no change needed to client. Docstring mentions only |
| src/shared/storage.py | l.247 comment ("used by the flex engine"), `read_json_blob/write_json_blob/append_jsonl_blob` l.247-300 | generic JSON/JSONL blob helpers | flex, daytrade, collector (`H:2992` market-shock, `H:4238` external flows, `performance` blobs) | KEEP (Core uses them; fix comment only) |
| src/shared/storage.py | `_TABLES` l.10-27 (has `ThematicHistory`, no `FlexConvictionState`) | table ensure list | `ensure_tables` | KEEP; note `FlexConvictionState` is NOT in the list (created lazily by upsert? unverified) |
| src/shared/quadrants.py | `QUADRANT_BENCHMARK_ETF` l.14 + `benchmark_etf_for` l.~41 (docstring cites flex G3) | quadrant -> benchmark ETF | `H:3685` publishes it to `performance/quadrant-config` blob for web chart (Core). No flex reader remains (flex review benchmark re-pointed to SPY) | KEEP (Core web chart); docstring cleanup |
| src/shared/quadrants.py | `LEGACY_EXITS` l.144, `CORE_ROSTER` l.250-262, `quadrant_allocation_bucket` `off_roster` l.391-401, comment l.143 ("re-enterable as FLEX") | roster + bucketing | Core everywhere; `off_roster` = any held name outside CORE_ROSTER (so ORB positions would land here generically) | KEEP; comments only. `INTC/MCK/PPA/EUAD` legacy semantics: Q5 |
| src/shared/reference_execution.py | l.579-592 comments + `off_roster` filter; l.842, 859 (`flex_source: None`, `catalyst_date: None` in synthesized trade dict) | band enforcement ignores off-roster names; synthesized trades carry null flex fields | Core `reconcile` + executor; fields are schema placeholders in `trades[]` rows | KEEP the off_roster handling (generic); `flex_source`/`catalyst_date` keys: harmless nulls, DECOUPLE later only if trades schema loses them (analyzer writes `flex_source` to TradeHistory `src/analyzer/handler.py:1452`) |
| src/shared/trade_validation.py | l.20-21, 285-291 messages "(flex only)" / "flex goes through flex_nominations" | V1 rejection reason strings for legacy/off-roster buys | Core validator | DECOUPLE: reject logic stays (still correct: ORB goes through its own path); reword messages only |
| src/function_app.py | l.10,13 imports; `flex_intraday` timer l.~100-115, `flex` HTTP l.118-140, `daytrade_manage` l.157, `daytrade_nominate` l.171-190, `daytrade` l.193-215 | registers Flex/DayTrade triggers (gated by FLEX_ENABLED / DAYTRADE_ENABLED) | Azure runtime | DELETE the 5 registrations + 2 imports (collector/analyzer/executor/learning registrations untouched) |
| H | `_build_flex_eligibility` `H:7860-7918`, call `H:3575`, snapshot `"flex_eligibility"` `H:3957` | per-symbol flex nominatability from `flex_separation_set` | `_build_flex_conviction` (`H:7998`) + prompt (`project-instructions.md:411-425,1736`) | DELETE |
| H | flex conviction path `H:7566-8100`: `_base_rate_up`, `_conviction_edge`, `_conviction_ladder_lookup`, `_conviction_catalyst_amplifier`, `_FLEX_CONVICTION_STATE_TABLE`/`_load/_save/_confirm_flex_conviction_entry`, `_load_prior_flex_conviction_nominations`, `_build_flex_conviction`, call `H:3569-3594`, snapshot `"flex_conviction"` `H:3958` | the second flex nomination path | flex/handler.py same-day read; prompt; no Core consumer (see 1b) | DELETE (PORT `_base_rate_up` only if ORB wants a base-rate gate; unlikely) |
| H | `_stamp_flex_conviction_outcomes` `H:2247-2316`, `_build_flex_conviction_calibration` `H:2318-2345`, call `H:4037-4042` | grades flex_conviction ThematicHistory rows | flex_conviction calibration only | DELETE (see 1b for the shared helpers it uses) |
| H | `_backfill_thematic_history_path` `H:2086-2112`, call `H:4017-4027` | one-time tag of legacy rows as `core_thematic` | exists solely because a 2nd path was added | KEEP for now (idempotent no-op once 0 untagged rows); safe to delete AFTER Core writer + stampers are confirmed to always set `path` (analyzer writes `"core_thematic"` at `src/analyzer/handler.py:1585`) |
| H | flex_state read `H:3614-3658`, snapshot `"flex_state"` `H:3960` | reads `flex-state/{date}.json` (7-day walkback) + attaches `reconciliation` | prompt (`project-instructions.md:285,495-562,1759,2250-2256,2485`) only; no code reads `flex_state` | DELETE |
| H | `_build_flex_reconciliation` `H:4295-4320`, `_flex_pos_qty` `H:4150-4157` | compare engine `held` to broker positions not in CORE_ROSTER | `flex_state.reconciliation`; prompt orphan-exit doctrine (`project-instructions.md:285-290`). `_flex_pos_qty` is only used here | DELETE both. NB: the "orphan position not tracked by anything" safety net disappears; ORB needs its own equivalent (Q6) |
| H | `_build_equity_reconciliation` `H:4260-4292`, `paper_account.reconciliation` | equity == cash + net_mv check | Core; mirrors flex pattern but has no flex dependency | KEEP (docstring mentions flex pattern only) |
| H | `flex_kill_switch` read `H:3620-3632`, snapshot `H:3965` | echoes `flex-ledger/kill-switch.json` | prompt `project-instructions.md:510-520` only | DELETE |
| H | `_build_pnl_decomposition` `H:10636-10730`, bucket `off_roster_flex` `H:10646,10696,10701`, log `H:3747-3750` | FIFO realized (from ALL Alpaca fills since inception) + unrealized by bucket core_current/legacy_exits/off_roster_flex | prompt (Core P&L attribution) | **DECOUPLE**: classification is by SYMBOL not by order origin, so ORB fills in non-roster symbols already land in `off_roster_flex` (rename to `off_roster`/`orb`); ORB fills in a CORE pool symbol (e.g. SPY/QQQ) would silently mix into `core_current` FIFO cost basis and corrupt it (Q7). Also compare `tests/test_pnl_decomposition.py` |
| H | `_build_track_record` layer split `H:1729-1790`, `_COARSE_TRIGGER` `H:386-388` | hit-rate by layer (`core`,`flex`), flex-only by_trigger/by_thesis from TradeHistory | snapshot `track_record` -> prompt | DECOUPLE: keep core layer cell; flex cells simply empty when no new `layer:"flex"` rows (existing rows still have them: KEEP history, don't rewrite). Drop flex-only aggregates only if prompt drops them |
| H | `_build_flex_review`/`_classify_flex_review` `H:11222-11400` | dead code (never called from src; tests only) | `tests/test_flex_review.py` | DELETE |
| H | `thematic_conviction` `_thematic_classify_symbol` `H:7187-7230` + `flex_route` `H:7510-7517` | Core overlay; non-roster ticker -> "routed_to_flex" | Core `thematic_conviction.excluded`, prompt | **DECOUPLE** (hazard 4, Q8) |
| H | `_ETF_WATCHLIST`, `_LEADING_GROWTH_EXTRAS` in `_build_price_universe` | not flex | Core | KEEP |
| H | `_load_prior_position_total_pl`/`day_pl_zero_watch` | grep hits only for the word "Flex" in comments (e.g. `H:210`, `H:5083`, `H:9700`, `H:9765`) | none | KEEP; comment-only (`H:9765` echoes "flex is a separate sleeve" in `reference_weights.rule` string; reword) |
| H | `_perf_point`, `_load_equity_spy_series`, `_build_performance`, `_excess_attribution`, `_build_external_flows` `H:4220-4257`, `_EXTERNAL_FLOWS_CONTAINER` `H:4169` | account-level equity/flows | Core, SWA chart | KEEP (no flex attribution; see section 3) |
| H | `_build_execution_review` `H:4325-` | reads `daily-executions/{date}.json` (Core executor) + Alpaca `get_order` | Core | KEEP (no flex dependency; flex has its own `flex-executions` blob, not read here) |
| web/api/function_app.py | `_sleeve_series`/`_attach_sleeve_series` l.~725-770 reads `flex-ledger/equity-series.json` | sleeve panel | SWA `/api/performance`, `web/performance.js/html` | out of scope here (web); DELETE with Flex. Degrades to `sleeve_available:false` per CLAUDE.md |

### 2. Required sections

#### (a) Catalyst screen / flex_candidates / watch_candidates / price universe / earnings

Verdict: **nothing Core-decisioning consumes the screen output**; there are four Core-adjacent couplings that must be unpicked, none of which change a number if handled as below.

- `catalyst_screen.py`: imported only at `H:61`, used only inside `_build_catalyst_screen` (`H:1099-1205`) and the discovery block (`H:2824`). No analyzer code reads `catalyst_screen` (grep of `src/analyzer`, `src/executor`, `src/shared`, `web`: zero hits outside prompt text). Only the LLM prompt (`project-instructions.md:428-525`) reads it, and only to inform `flex_nominations`.
- `flex_candidates`: snapshot key `H:3923`; read by the prompt (`project-instructions.md:1621`) only. Its tickers DO flow into: (1) `_build_price_universe` -> `prices` (`H:2911`), (2) `_earn_universe` (`H:2769`), (3) `_news_symbols` (`H:2822`, hazard 2), (4) `_tc_quarantined` (`H:3329`), (5) `_catalyst_exclude` (`H:2800`). (1) and (2) are additive-only to Core data (extra price rows / earnings rows for non-core names); Core code looks prices up by Core symbol so dropping them changes nothing. (3) and (4) are the real couplings.
- Reference weights / `_build_reference_weights`: reads no flex_candidates (grep). `reference_gaps` only creates `off_roster` rows for HELD non-roster names (`src/analyzer/handler.py:1028`), not candidates.
- Quarantine: `_quarantine_flex_price` iterates ONLY flex profiles (`H:2955`, `H:3537`); `_correct_10x_ingestion_error` can mutate `prices[sym]["c"]` but only for those symbols. Core consumer: the thematic overlay's `_tc_quarantined` (`H:3329`).
- `watch_candidates`: analyzer sanitizes and persists it in `daily-trades/{date}.json` (`src/analyzer/handler.py:1343-1381`); the ONLY reader is the next day's `_load_flex_candidates` (`H:495`, reads the prior trades blob). Executor and trade validator ignore it (CLAUDE.md contract, confirmed by grep: no other readers). DELETE the sanitizer too (analyzer scope).
- `earnings_calendar_market` / `_screen_earnings_market_rows`: only `_build_catalyst_screen` + prompt. DELETE.
- `_filter_earnings_to_universe`: Core; only the `flex_candidate_tickers` term in `_earn_universe` is Flex.
- `_build_price_universe`: DECOUPLE as in table (and `tests/test_price_universe.py`).

#### (b) flex_conviction vs thematic_conviction; ThematicHistory

- Two separate systems sharing one table and four pure helpers. **thematic_conviction = Core** (floor lift in `_build_reference_weights`; state table `ThematicConvictionState`; config `risk-limits.json -> thematic_conviction`; writer `src/analyzer/handler.py:1528-1589` row `path:"core_thematic"` at l.1585; stamper `_stamp_thematic_outcomes` `H:2113-2210`, calibration `_build_thematic_calibration` `H:2212-2245`). **flex_conviction = Flex** (state table `FlexConvictionState` `H:7707`, config `risk-limits.json -> conviction`, writer `analyzer _write_flex_conviction_history` `src/analyzer/handler.py:1592-1644` with `path:"flex_conviction"`, called l.465; stamper `_stamp_flex_conviction_outcomes` `H:2247`; calibration `_build_flex_conviction_calibration` `H:2318`).
- Shared helpers the flex code reuses and Core MUST keep: `_thematic_brier` (`H:7251`), `_thematic_damping_factor` (`H:7266`), `_thematic_ladder_lookup` (`H:7166`) and the price-resolution used by `_stamp_thematic_outcomes`. Flex calibration calls `_thematic_brier`/`_thematic_damping_factor` (`H:2338-2344`); the reverse direction (Core calling flex code) does not exist.
- Core isolation is already enforced: both Core queries use `path eq 'core_thematic'` (`H:2139`, `H:2228`), and the core stamper additionally keys off row-stored `horizon_30d/60d/90d` (belt-and-braces, `H:2125-2175`). **Removing the two flex functions and the `flex_conviction` writer cannot change Core calibration**; pre-existing `flex_conviction` rows simply become inert (never queried by Core). Risk to avoid: do NOT delete the `path eq 'core_thematic'` filters, and do not delete `_backfill_thematic_history_path` until you verify zero untagged rows remain.
- Delete list: `_stamp_flex_conviction_outcomes`, `_build_flex_conviction_calibration`, `_build_flex_conviction`, all `_conviction_*`, `_base_rate_up`, `_confirm_flex_conviction_entry`, `_load/_save_flex_conviction_state`, `_load_prior_flex_conviction_nominations`, analyzer `_write_flex_conviction_history`. Keep: everything `thematic`. Keep existing `flex_conviction` ThematicHistory rows (history; Q9 on archive).
- Coupling to resolve: thematic `flex_route` (hazard 4 / Q8).

#### (c) Reconciliation, execution review, P&L decomposition, flows, equity series

- `_build_flex_reconciliation`: Flex-only; compares engine ledger vs broker positions outside CORE_ROSTER. Not used by Core accounting. Removing it removes the only "untracked broker position" alarm (Q6).
- `execution_review` (`H:4325+`): reads `daily-executions` (Core executor blob) only; **no Flex dependency**; flex orders are in `flex-executions`/Alpaca not in that file. KEEP unchanged.
- Equity reconciliation (`H:4260`): equity vs cash+net_mv over ALL positions (Core + flex + any ORB). Flex-agnostic. KEEP.
- `_build_pnl_decomposition` (`H:10636`): attributes by symbol, uses ALL Alpaca fills since inception. Flex fills currently appear under `off_roster_flex`; a closed flex position's realized P&L stays in that bucket forever (history). Core attribution is NOT flex-aware, so removing Flex breaks nothing; but ORB's future fills will pollute `core_current` FIFO if ORB trades any core-pool symbol (Q7), and will land in a bucket still named `off_roster_flex`.
- `external_flows` (`H:4220`): CSD/CSW/JNLC only. Unaffected.
- Performance equity series (`performance/equity-series.json`, `H:3660-3690`, `_load_equity_spy_series`): account-level equity; flex gains/losses are included in "invested" and in `excess_attribution` with no flex tag. Not separable after the fact; unchanged by retirement. (Flex has its OWN separate `flex-ledger/equity-series.json`, consumed only by web/api.)
- Replay of historical snapshots: no collector/shared/analyzer code reads `flex_state`, `flex_quadrant`, `flex_candidates`, `flex_eligibility`, `flex_conviction`, `flex_kill_switch`, `catalyst_screen`, `earnings_calendar_market` back from an old snapshot (grep shows they are only WRITTEN at `H:3923-3965`). Readers of old snapshots (`_load_prior_growth_axis`, `_load_equity_spy_series`, `_load_prior_position_total_pl`, series_deltas, execution/plan walkbacks, `scripts/replay_*.py`) touch other keys. `scripts/replay_*.py`/`point_in_time.py` mention "flex" only in "never imported by flex runtime" docstrings. Old snapshots remain replayable; the analyzer prompt (not code) is what would reference missing keys on a replayed old snapshot, so a replay against an OLD snapshot with a NEW prompt is fine, and vice-versa is the only mismatch (new snapshot w/o flex keys + old prompt that expects them = prose only, but see hazard 5).
- Also: `quadrant_allocation_bucket` `off_roster` and the reference-gap `off_roster:True` rows are generic (any held non-roster symbol), so an ORB position held at snapshot time would be handled safely (never synthesized by `reconcile`, `src/shared/reference_execution.py:592`). ORB is intraday-flat, collector runs 09:00 pre-open, so none expected.

#### (j) FLEX_SLEEVE_CAP_PCT and the 25% sleeve budget (src, tests, web, infra, scripts, .github)

Readers of the env var `FLEX_SLEEVE_CAP_PCT`:
- `src/flex/config.py:59` (`sleeve_cap_pct = 25.0`) and `:148` (`_env_float("FLEX_SLEEVE_CAP_PCT", ...)`).
- `src/daytrade/config.py:7-8` (docstring), `:64` (`flex_sleeve_cap_pct = 25.0`), `:112-114` (validation notional_cap <= sleeve cap), `:155` (`_env_float("FLEX_SLEEVE_CAP_PCT", 25.0)`); consumed at `src/daytrade/sizing.py:3,33` (risk budget = sleeve% x equity).
- `src/flex/handler.py:198,206,232` (sleeve_cap_usd/sleeve_room); `src/flex/entry.py:77-80` (`sleeve_cap` governor); `src/flex/killswitch.py:3` (docstring).
- NOT set in infra: no `FLEX_SLEEVE_CAP_PCT` in `infra/` or `.github` (grep) -> always the 25.0 code default.
Literal 25% in config/prose (no code reader): `src/config/risk-limits.json:29-33` `flex_sleeve_cap_pct {soft 15, hard 25}` (read by NOTHING; mirrored in `H:113`, also unread); `src/config/risk-limits.json:11` `single_name_cap_pct.flex 4.0` (unread); `project-instructions.md:282,648` (prose). Core cash-sleeve `shock3_ceiling 25.0` is a DIFFERENT 25% (cash band) - do not touch.
Tests that pin it: `tests/test_daytrade_separation.py:70-76`, `tests/test_daytrade_sizing.py:80-84`, `tests/test_flex_news_momentum_b1.py:262`, `tests/test_flex_sizing.py:50-54` (`sleeve_cap` governor), `tests/test_catalyst_prompt_sentinels.py:81` (prompt sentinel for flex ticker/sleeve caps), `tests/test_flex_trades.py:216-246` (`sleeve_notional_usd`, a different field).
`sleeve_contribution_pp`/`sleeve_trade_count`: web only (`web/api/function_app.py` `_sleeve_series`, `web/performance.js/html`).
**Core never subtracts a flex budget from `core_room`** (`H:9502`), so deleting the 25% changes no Core weight. If ORB gets a budget, it is a new knob (e.g. `ORB_*`), not a rename: DayTrade Lab already couples to the flex env name, so a shared name would re-create the coupling.

#### (i-part) Blob containers / tables the collector touches that relate to Flex

| name | writer | readers | class |
|---|---|---|---|
| blob `flex-state/{date}.json` | `src/flex/handler.py:928-957` (end of every ~15-min tick; closed-tick carries entries/exits forward) | collector `H:3636` (7-day walkback) | DELETE (collector reader removed) |
| blob `flex-ledger/ledger.json` | `src/flex/ledger.py:15` | flex engine; `src/daytrade/handler.py:44` (`_CATALYST_LEDGER`, read-only for separation); `scripts/orb_phase0_flat_check.py` (Phase 0A) | KEEP until flatness verified, then retire |
| blob `flex-ledger/closed-trades.json`, `equity-series.json`, `kill-switch.json` | `src/flex/trades.py:32`, `src/flex/killswitch.py` | collector reads only `kill-switch.json` (`H:3625` via `flex_trades.read_kill_switch_state`); web/api `_attach_sleeve_series` reads `equity-series.json` (`web/api/function_app.py:764`) | KEEP (history: only realized-performance record of the sleeve, `closed-trades.json` is the high-value one) |
| blob `flex-decisions/{date}.jsonl` | `src/flex/handler.py:957` | none in collector/shared | KEEP (history) |
| blob `flex-executions` | flex handler | none in collector (execution_review reads `daily-executions` only) | KEEP (history) |
| blobs `daytrade-nominations`, `daytrade-state`, `daytrade-log`, `daytrade-grades`, `daytrade-ledger/ledger.json` | `src/daytrade/handler.py:39-44`, `ledger.py:12` | `src/flex/handler.py:642` reads `daytrade-ledger/ledger.json` (cross-engine separation); NO collector/shared reader | KEEP (history) |
| blob `daily-trades/{date}.json` keys `flex_nominations[]`, `watch_candidates` | analyzer | `H:7934` (`_load_prior_flex_conviction_nominations`), `H:495` (dynamic candidates), flex handler | DELETE the two collector readers; KEEP historical files |
| blob `daily-snapshots/{date}.json` flex keys (`flex_candidates`, `catalyst_screen`, `earnings_calendar_market`, `flex_eligibility`, `flex_conviction`, `flex_state`, `flex_kill_switch`) | collector `H:3923-3965` | prompt only | stop writing; KEEP history |
| blob `market-shock/news-hits-history.json` | collector (`H:2992`) | collector | KEEP (CORE; affected by hazard 2) |
| blob `performance/quadrant-config`, `performance/equity-series.json`, `performance/external-flows-scan.json` | collector | web/api, collector | KEEP (Core) |
| table `ThematicHistory` (PK year-month) | analyzer (both paths), collector stampers | collector calibration/stamps | KEEP table; delete only flex_conviction code paths; keep flex rows as history |
| table `FlexConvictionState` | collector `H:7734` (`_save_flex_conviction_state`) | collector `H:7719` | DELETE code; KEEP table/rows (not in `storage._TABLES`; create path unverified) |
| table `ThematicConvictionState`, `AxisDirectionState`, `SettlingWindowState`, `TransitionWatchState`, `SleeveSelectionState` | collector | collector | KEEP (Core) |
| table `TradeHistory` rows `layer:"flex"` (fields `primary_trigger`, `thesis_type`, `trigger_evidence`, `catalyst_date`, `flex_source`, `entry_price`...) | analyzer `src/analyzer/handler.py:1441-1480`, flex handler `_record_trade_history` | collector maturity stamping + `_build_track_record` (`H:1729+`) | KEEP (history, must keep stamping already-open flex rows until matured) |
| table `OverrideHistory` | analyzer/collector | n/a to flex | KEEP |
| infra: containers created lazily (not in `infra/modules/storage.bicep` list: only daily-snapshots/reports/trades/executions/deployment); app settings `FLEX_ENABLED='true'`, `DAYTRADE_ENABLED='false'` in `infra/modules/functionapp.bicep:117,122` | bicep | functionapp | DELETE both settings (BOTH in Bicep, per CLAUDE.md "change in both places") |

## Appendix B — Analyzer, executor, learning, prompt, function_app, CI

### Inventory table

| file | symbol & line range | what it does | what consumes it | class |
|---|---|---|---|---|
| analyzer/handler.py | `_FLEX_SCHEMA_SENTINELS` L97-101 | Tuple ("FLEX_SCHEMA_V1","flex_nominations") | only `assert_flex_prompt_schema` | DELETE |
| analyzer/handler.py | `assert_flex_prompt_schema` L111-118; call L185 | Load-time gate: raises (analyzer refuses to run) if prompt lacks the two markers | analyzer `handler()` startup; tests/test_flex_prompt_schema.py | DELETE (in the SAME commit as the prompt text, else analyzer hard-fails every run; see (e)) |
| analyzer/handler.py | `_OVERRIDE_SCHEMA_SENTINELS` / `assert_override_prompt_schema` L102-130, call L186 | Core override-schema gate, adjacent to flex gate | Core | KEEP |
| analyzer/handler.py | `_write_flex_conviction_history` L1592-1644; call L463-467 | Writes ThematicHistory rows (`path:"flex_conviction"`, RK `FLEXCV-`) per `flex_nominations[]` entry with path=="conviction" | collector `_stamp_flex_conviction_outcomes` / `_build_flex_conviction_calibration` (query on that path tag); tests/test_write_flex_conviction_history.py | DELETE (analyzer side). Collector consumers are other agent's scope; ThematicHistory `path` field + backfill stay for Core (`core_thematic`) |
| analyzer/handler.py | `watch_candidates` sanitizer in `_split_response` L1343-1381 | Validates/trims optional `watch_candidates` (max 6) | Only the next-day collector flex dynamic candidates (`flex_candidates source:"dynamic"`); executor and validator ignore it | DELETE (plus prompt text L2299-2315 and the "dynamic" bullet L1621-1630). No Core consumer; remove collector reader first/same change |
| analyzer/handler.py | `_split_response` malformed-JSON fallback + `write_debug_raw` ~L1335-1341 | Core parsing safety | Core | KEEP |
| analyzer/handler.py | `_write_trade_history` flex bits: comment L1420-1426, `is_flex_buy` L1441, `flex_source` L1452, reasoning enums L1461-1466 (`primary_trigger`,`thesis_type`,`trigger_evidence`,`catalyst_date`), `entry_date`/`entry_price` L1471-1474 | Writes TradeHistory rows; flex-only columns are empty for core trades | collector maturity stamper / `track_record` (by_trigger, by_thesis); Learning bundle (all TradeHistory columns) | DECOUPLE: drop `is_flex_buy` branch and `flex_source`; `layer` (L1450) stays. Reasoning enums/`catalyst_date`: UNSURE Q1 |
| analyzer/handler.py | comments L1028, L1032, L1117 (off_roster "flex leftover like MU") in `_build_reference_gaps` | Explains `off_roster` gap rows | The off_roster logic itself is Core (price/sell clamp for any non-roster held name) | KEEP logic, reword comments. See UNSURE Q2 |
| analyzer/handler.py | `_submitted_order_addendum` L701 (call L447) | Renders final validated `trades[]` table | Core; reads no flex field | KEEP |
| analyzer/handler.py | `_write_thematic_history` L1526-1590, call L460-464 | Core `thematic_conviction` ThematicHistory rows (`THM-`) | Core | KEEP |
| analyzer/handler.py | `entry_quadrant` / `flex_benchmark_etf` stamps | Already removed 2026-09-12; only a comment L1423-1426 remains | nothing | KEEP (comment can go) |
| executor/handler.py | `_cancel_conflicting_orders` L305-348 | Cancels EVERY open Alpaca order on the symbol before core submits (symbol match only L335-337) | Core executor | DECOUPLE (docstring L310 cites flex; logic generic). See (d) |
| executor/handler.py | defensive sell filter L163-200 | Drops sells of symbols not held, trims qty to held (live `list_positions`) | Core executor | KEEP |
| executor/handler.py | `_place_one` L353-404, `client_order_id=f"{date_str}-{trade_id}"[:48]` L393 | Core order id, no FLEX prefix | Core | KEEP |
| learning/bundle.py | `LIVE_CONFIG_FILES` L43-48 (entry L47 "config/flex-candidates.json"), fetch loop L141-145 | Fetches each file from GitHub raw at master HEAD with `raise_for_status` | learning handler / reviewer bundle | DECOUPLE: remove entry. Path is already wrong (see (g)) |
| learning/schema.py | `TARGET_FILE_ALLOWLIST` L21-26 (L25) | Class 1-2 diff target allowlist | `validate_cycle_output`; tests/test_learning_schema.py L141-150 | DECOUPLE: remove entry |
| learning/bundle.py | `fetch_trade_history` L176-179 | All TradeHistory rows, all layers (includes historical `layer:"flex"`) | reviewer | KEEP |
| config/project-instructions.md | see (e) | | analyzer system prompt | mixed |
| config/learning-review-instructions.md | L53, L151 (file list/allowlist), L92-103 (worked example "Raise flex confidence bar...") | Reviewer prompt | Learning reviewer | DECOUPLE: drop flex-candidates.json from L53/L151; rewrite example to a Core one |
| function_app.py | imports L10 (`daytrade.handler`), L13 (`flex.handler`) | | | DELETE |
| function_app.py | `flex_intraday` timer ~L95-115 (FLEX_ENABLED gate L107), `flex` HTTP route L117-140 | Intraday flex tick / manual dry-run | Azure host | DELETE |
| function_app.py | `daytrade_manage` timer ~L155-168 (DAYTRADE_ENABLED), `daytrade_nominate` HTTP L170-190, `daytrade` HTTP L192-215 | DayTrade Lab | Azure host | DELETE |
| function_app.py | analyzer blob trigger, executor timers, learning registrations | Core | Core | KEEP (only match is L41 comment "Flex Consumption", the Azure SKU) |
| .github/workflows/deploy-code.yml | L3, L9, L18, L77, L84 | All are the Azure "Flex Consumption" hosting plan, NOT the engine | n/a | KEEP (false positives) |
| .github/workflows/ci.yml | whole file | ruff + `pytest -q`; no flex-specific step | n/a | KEEP. The "CI assertion" is only test_flex_prompt_schema.py collected by pytest |
| infra (out of scope, noted) | FLEX_ENABLED / DAYTRADE_ENABLED in functionapp.bicep | | | Remember CLAUDE.md lesson: change in Bicep, not az alone |

### (d) Executor: order-ID prefixes and ledgers

- No reliance on `FLEXC-`, `FLEXD-`, or `flex-` prefixes anywhere in executor/handler.py. Its only flex hit is the docstring at L310. It never reads `flex-ledger`, `flex-state`, or any flex blob.
- Its own id is `{date}-{trade_id}` (L393), no prefix.
- `_cancel_conflicting_orders` (L305-348) is symbol-scoped, with no prefix or ownership check. Written for the MU stale-flex-stop incident but generic. ORB implication: if Core submits on a symbol where ORB holds a resting bracket (TP/stop legs), the executor cancels the ORB legs and leaves ORB's position naked. Today flex names are walled off from Core by the separation set; ORB likely trades liquid names that ARE Core pool members, so collision becomes plausible. Minimal change: skip orders with the ORB client_order_id prefix (prefix to be chosen; note bracket child legs carry broker UUIDs, not the prefix, per CLAUDE.md B2 section 8.2, so a parent-order/position-based exemption may be needed). UNSURE Q3.
- Defensive sell filter (L163-200) uses live Alpaca positions only; a Core sell is trimmed to TOTAL held qty, which would include ORB-held shares. Not flex-specific but relevant.
- tests/test_executor_order_conflict.py: docstring and fixture use `flex-2026-07-07-MU-rep-...` as sample data only; tests generic behavior. KEEP (fixture string may stay).

### (e) Analyzer / prompt

#### project-instructions.md map (2582 lines): Flex-only vs interleaved

Flex-only blocks (deletable wholesale):
- L252-568 `### Flex (up to 10 tickers, rotatable) - an intraday CATALYST engine`, containing: FLEX_SCHEMA_V1 banner L254-260; `#### The Separation Contract` L262-293 (L284-293 is the "orphaned-flex-exit" exception, which lets Core `trades[]` exit a broker-held orphan: delete with it); `#### Your job on Flex` L295-333; `#### The conviction path` L334-409; `flex_eligibility` L411-427; `catalyst_screen` L428-493; `#### Reading flex_state` L495-568 (flex_kill_switch L510-520, reconciliation doctrine L538-551, orphan orders L557-566).
- L2358-2414 `flex_nominations` JSON schema + FLEX_SCHEMA_V1 contract paragraph (L2407-2414).
- L2299-2315 `watch_candidates` + price-quarantine paragraphs (inside Section 6).
- Input bullets: L1621-1626 `flex_candidates`, L1736-1741 `flex_eligibility`, L1743-1763 `flex_conviction` / `flex_state`.

Interleaved with Core doctrine (surgical edit, not delete):
- L135-139 heading "Portfolio structure - role-based core + flex" and intro; L183 legacy-exit line (INTC/MCK/PPA/EUAD "flex-nominatable while flat").
- L641-648, L681: concentration/cash-sleeve text reserving room for flex 25%.
- L1223-1278 Thematic capex cascade: L1258-1261 and L1275 route theme candidates to `flex_nominations[]`; the `thematic_conviction[]` path (L1262-1278) is CORE and stays. `routed_to_flex` in thematic excluded entries (L1724 region, L2449): Core field whose target disappears (UNSURE Q4).
- L1298-1320 Track record: by_trigger/by_thesis flex language (L1315-1318).
- L1403 regime-accountability "no cash, no flex sleeve".
- L1536-1595 P&L decomposition prompt text for `off_roster_flex` bucket (name owned by collector).
- L1628 earnings_calendar universe mentions flex candidates.
- L1806 dashboard row `| **Flex** | {n}/10 held |`; L1884 Table A row "Off-roster (flex leftovers)"; L1923; L2153; L2179; L2185 `[CORE]`/`[FLEX]` tags; L2246-2262 Section 6 "Themes & flex pipeline" (theme ledger is Core; flex nominations/state and the mandatory prior-nomination adjudication L2254-2262 are flex).
- L2342-2343 trade schema `"layer": "core" | "flex"`, `flex_source`; L2352-2355 reasoning enums, `catalyst_date`; L2481-2511 trade rules re layer/flex_source/enums; L2535-2538 min-notional "does not apply to flex trades"; L2551, L2561, L2573 guardrails.

#### Load-time assertion and CI
- `assert_flex_prompt_schema` (handler.py L185) requires BOTH `FLEX_SCHEMA_V1` and `flex_nominations` in the prompt. Removing the prompt text without removing the assertion makes every analyzer run raise RuntimeError. Remove assertion + test in the same change.
- ci.yml has no explicit step; only pytest, which collects tests/test_flex_prompt_schema.py (L22-34). DELETE that file.

#### flex_nominations parse/validate
No analyzer-side parse or validation of `flex_nominations[]`: `_split_response` does not touch it; the only analyzer read is `_write_flex_conviction_history` (L465). Validation lives in src/flex (out of scope). Trade validator and executor ignore the key.

#### Sentinel tests (all do plain-text `in` checks on project-instructions.md)
- tests/test_flex_prompt_schema.py: DELETE whole file.
- tests/test_catalyst_prompt_sentinels.py: entirely flex/catalyst_screen doctrine (regime removal L18-31, catalyst_screen L35-41, absent-vs-zero L43-47, caps test L81-84): DELETE whole file.
- tests/test_prompt_hygiene_sentinels.py: delete these flex-only tuples AND their test functions:
  - `_FLEX_ADJUDICATION_SENTINELS` L49-54 + `test_flex_nomination_adjudication_present` L250-253
  - `_CATALYST_DATE_SENTINELS` L62-65 + `test_catalyst_date_symmetry_present` L262-265 (text in Flex nomination step 1, L301-302)
  - `_FLEX_CONVICTION_PATH_SENTINELS` L126-131 + test L320-323
  - `_FLEX_ELIGIBILITY_SENTINELS` L137-142 + test L326-329 (contains "top-named rotation candidate": verify it is in flex text, not Core regional-rotation text, UNSURE Q5)
  - `_APPLICABLE_SET_SENTINELS` (~L152-160, catalyst_screen rankability) + its test
  - `_CASH_FLOOR_VISIBILITY_SENTINELS` (`binding == "cash_floor"`) + its test
  - `_FLEX_STATE_AS_OF_STALENESS_SENTINELS` L172-176 + test L344-347
  Keep all Core sentinels (E4 direction vocab, E6 no-chatter, oil overlay, thematic retired-phrase, joint projection, etc.). Check `_OIL_OVERLAY_SENTINELS` phrase "Two independent conviction mechanisms" survives the edit if it sits near flex/thematic text.
- Other analyzer-flex tests: tests/test_write_flex_conviction_history.py (DELETE); tests/test_flex_dynamic_candidates.py (watch_candidates + flex-candidates.json; DELETE/trim with collector side).

#### Other
- `_submitted_order_addendum` has no flex dependency. No `entry_quadrant` stamps remain.

### (g) Learning Loop

- Bundle never reads `flex-ledger`, `flex-state`, or any flex blob (no hits in learning/*.py). Flex data reaches the reviewer only indirectly: (1) all TradeHistory rows (`fetch_trade_history` L176-179), including `layer:"flex"` rows and reasoning enums; (2) 35d of daily-report markdown narrating flex; (3) live config incl. flex-candidates.json; (4) FOLLOWUPS.md; (5) `track_record` snapshot content in reports.
- EXISTING DEFECT: bundle.py L47 and schema.py L25 say `config/flex-candidates.json`, but the file is `src/config/flex-candidates.json` (`config/` does not exist at repo root). `fetch_live_config` calls `raise_for_status()` per path (L141-145), so that entry would 404 and raise. Either bundle assembly fails today or the Loop has not run past phase 1 in prod; not verifiable offline. Removing the entry fixes it.
- Does deleting flex-candidates.json break the allowlist/schema/tests? Code: no, allowlist is a tuple membership check. Tests to edit: tests/test_learning_schema.py L141-150 `test_allowlist_accepts_all_four_files` (loops 4 paths; becomes 3). grep found no "flex-candidates" in test_learning_bundle.py or test_learning_handler.py, but check for any count/len assertion on `LIVE_CONFIG_FILES`. web/api/learning_github.py: grep found no allowlist copy (relies on schema.py) - re-verify. Reviewer prompt: learning-review-instructions.md L53, L151, example L92-103.
- Learning handler has no flex logic (L86 amendment filter; L146-147 section labels) and no TradeHistory layer filter, so ORB rows flow in automatically.
- Historical LearningProposals targeting flex-candidates.json (if any) remain; forced re-review could point at a missing file (UNSURE Q6).

### (b-part) Analyzer-side ThematicHistory writes
- `_write_thematic_history` L1526-1590 (RK `THM-`, path core_thematic): Core. KEEP.
- `_write_flex_conviction_history` L1592-1644 (RK `FLEXCV-`, path flex_conviction): Flex. DELETE. Collector-side M-B `path` scoping and `_backfill_thematic_history_path` must STAY (they protect Core grading).

## Appendix C — Web, infra, workflows, scripts, docs/specs, FOLLOWUPS

### Main table

| file | symbol & line range | what it does | what consumes it | class |
|---|---|---|---|---|
| web/api/function_app.py | `_sleeve_series` L715-751 | Computes per-point `sleeve_contribution_pp` / `sleeve_trade_count` from flex equity-series rows (cumulative from window start) | `_attach_sleeve_series` L770 only | DELETE (Flex-only) or PORT if ORB writes an equivalent series |
| web/api/function_app.py | `_attach_sleeve_series` L754-782; call sites L968 (non-quadrant path) and L1036 | Reads blob `flex-ledger/equity-series.json` (L764); sets `sleeve_available`, `sleeve_closed_trade_count_total`, per-point `sleeve_*` on `/api/performance` payload | web/performance.js renderSleeveChart/Summary (L395-466) | PORT (repoint container/blob to ORB ledger) or DELETE. Degrades to `sleeve_available:false` if blob absent, so safe to leave temporarily |
| web/api/function_app.py | L788 docstring in `_attach_quadrant_accountability` ("Mirrors the `_attach_sleeve_series` contract") | Comment only | nothing | KEEP (reword if sleeve fn removed) |
| web/performance.html | L42-46 `<h2>Flex catalyst sleeve</h2>`, `#sleeve-summary`, `<canvas id="sleeveChart">` | Separate panel for Flex sleeve contribution | performance.js | PORT (retitle to ORB) or DELETE |
| web/performance.js | L19-29 consts `SLEEVE_COLOR/SLEEVE_THIN_COLOR/SLEEVE_MIN_N`, L32 `sleeveChart`, L42 `renderSleeveChart(data)`, L47 `renderSleeveSummary(data)` | Wiring of the Flex panel into load() | load() | PORT/DELETE together with html |
| web/performance.js | L395-466 `renderSleeveSummary`, `renderSleeveChart` (label "Flex sleeve contribution" L429; reads `p.sleeve_contribution_pp`, `pt.sleeve_trade_count`, `data.sleeve_available`, `data.sleeve_closed_trade_count_total`) | Renders chart, N<30 dashed/grey caveat | performance.html | PORT/DELETE |
| web/performance.js | L101 caption "These baskets carry no cash, flex, or international sleeve" | Core quadrant-basket disclaimer wording | UI text only; "flex" = the retiring sleeve but sentence is about basket composition | KEEP (optionally reword "flex" -> "satellite/ORB") |
| web/performance.js | L209 comment ("so it is added ONLY to the main chart, never the sleeve") | comment | none | KEEP |
| web/styles.css | `flex` hits (L19,22,50,72,77,79,92,93,112,122) | CSS flexbox | layout | KEEP (false positives; no sleeve/flex-sleeve CSS exists) |
| web/index.html L13-16, web/app.js L49 | Nav links: Today/History/Portfolio/Performance (+ dynamic Learning) | Navigation | users | KEEP - no nav entry exists for flex/daytrade/sleeve |
| web/today.*, history.*, portfolio.*, learning.*, staticwebapp.config.json | no flex/daytrade references (grepped) | - | - | KEEP |
| web/api/function_app.py | routes L183-891 (dates, report, trades, snapshot, executions, trades approve/reject, me, learning/*, performance) | No route reads flex-state/daytrade-* blobs; no `/api/flex`, `/api/daytrade` proxy | - | KEEP. `/api/executions/{date}` reads `daily-executions` (Core+auto executor; flex writes its own `flex-executions` which web does NOT read) |
| web/api/learning_github.py, learning_diffcheck.py | grep: zero flex/daytrade hits | - | - | KEEP |
| src/learning/bundle.py L47, src/learning/schema.py L25 (outside scope but linked) | allowlist includes `config/flex-candidates.json` as an amendable target file | Learning Loop target allowlist + bundle live-config fetch from GitHub raw | learning reviewer | UNSURE: see Q1 |
| infra/modules/functionapp.bicep | L95-117 comment block + L117 `FLEX_ENABLED` = `'true'` | Gate for `flex_intraday` | src/function_app.py:107 | DELETE setting (or flip to `'false'` first). Must change in Bicep AND live (CLAUDE.md lesson) |
| infra/modules/functionapp.bicep | L118-122 comment + L122 `DAYTRADE_ENABLED` = `'false'` | Gate for `daytrade_manage` (src/function_app.py:159) | function_app | DELETE |
| infra/modules/functionapp.bicep | other "Flex" hits L1,3,5,36,88,131,133-136 | Mean Flex *Consumption* hosting plan, NOT the Flex sleeve | platform | KEEP (false positives) |
| infra/modules/storage.bicep | `containers` L4-10: only daily-snapshots, daily-reports, daily-trades, daily-executions, deployment | NO flex-*/daytrade-* container is defined in Bicep | - | KEEP. Flex/DayTrade containers are created at runtime by `write_json_blob`/`append_jsonl_blob` (src/shared/storage.py L255-263, L303-306). Nothing to remove in IaC |
| infra/modules/eventgrid.bicep | only "Flex Consumption" (L3) + analyzer note | no Flex-sleeve content | - | KEEP |
| infra/main.bicep | L50, L83 "Flex Consumption" comments; no FLEX_*/DAYTRADE_* params | none | - | KEEP |
| infra/parameters.prod.json | no FLEX_/DAYTRADE_ keys | - | - | KEEP |
| infra/main.json | compiled ARM; grep for FLEX_ENABLED/DAYTRADE = 0 hits | stale compiled output, does not contain the settings | not deployed by workflow (verify) | KEEP / ignore (mention only) |
| infra/deploy.ps1 | no hits | - | - | KEEP |
| .github/workflows/deploy-code.yml | L3,9,18,77,84 "Flex Consumption" | hosting plan wording | - | KEEP. No flex/daytrade path filters or function names; EventGrid webhook is analyzer only |
| .github/workflows/deploy-infra.yml, deploy-web.yml, ci.yml | no hits | - | - | KEEP |
| scripts/probe_dynamic_candidates.py | whole file (L16-55): monkeypatches `collector.handler._FLEX_CANDIDATES_FILE`, `_load_flex_candidates`, asserts FLEX_REENTERABLE | Offline probe of dynamic flex candidate loader | manual use only; breaks if collector's flex candidate code is removed | DELETE (with the Flex candidate path) or UNSURE Q2 |
| scripts/probe_fmp_tier.py | L1 docstring "catalyst-sleeve-funnel" | FMP tier probe (throwaway, FOLLOWUPS #34) | manual | KEEP (history) / UNSURE Q3 |
| scripts/admission.py L13, point_in_time.py L34, replay_axes.py L30, replay_parity.py L35 | docstring mentions ("flexible CPI", "never imported by ... flex runtime code") | Core regime backtest harness | none | KEEP (no real dependency; the "flex" word is incidental) |
| scripts/alfred_cache.py, probe_gdpnow_*, probe_quadrant_basket_bias.py, run_*.py, config/* | no hits | - | - | KEEP |
| README.md L12, L18 | L12 "flex engine" spec listed; L18 `src/` lists `flex` among functions | Docs | readers | DECOUPLE: edit L18 to `orb` and L12 to ORB spec when retiring |

### (f) /api/performance and the web "sleeve" fields

Everything named `sleeve_*` in web/api is Flex-derived; none is the Core "sleeve roles"/quadrant meaning.
- `sleeve_available`, `sleeve_closed_trade_count_total` (payload top-level, web/api/function_app.py:761-762, 777-778, 781-782) and per-point `sleeve_contribution_pp`, `sleeve_trade_count` (L750, L775-776): sourced ONLY from blob `flex-ledger/equity-series.json` (L764), keys `total_equity`, `cumulative_realized_usd`, `unrealized_usd`, `closed_trades_to_date` (L736-739; written by src/flex/trades.py). Last-row `closed_trades_to_date` -> `sleeve_closed_trade_count_total` (L778). Pure Flex.
- `_sleeve_series` (L715) and `_attach_sleeve_series` (L754) called at L968 and L1036: both Flex. Failure path is non-fatal; absence of the blob yields `sleeve_available:false` and otherwise unchanged payload, so web does not break if the blob stops updating.
- Core "sleeve" in this API = quadrant baskets (`_quadrant_series`, `quadrant_config`, `quadrant_accountability`) and role names: KEEP, untouched.
- Front end: web/performance.html L42-46 (`#sleeve-summary`, `#sleeveChart`), web/performance.js L19-47, L395-466. No other page uses sleeve fields. Nav (web/index.html L13-16, web/app.js L49) has no sleeve/flex/daytrade link.
- No API route reads `flex-state`, `flex-decisions`, `flex-executions`, `daytrade-*`, `flex-ledger/ledger.json`, `closed-trades.json`, or `kill-switch.json`. Only `flex-ledger/equity-series.json` is read by web.
- Action: if ORB will have its own panel, PORT (repoint blob + relabel); otherwise DELETE 3 files' worth of code (function_app L715-782 + 2 call sites, html panel, js functions). Retiring without editing leaves the panel showing "No sleeve activity recorded yet" (or a frozen last series), harmless.

### (h) FLEX_* and DAYTRADE_* settings

Bicep (infra/modules/functionapp.bicep) sets ONLY two: `FLEX_ENABLED='true'` (L117) and `DAYTRADE_ENABLED='false'` (L122). main.bicep, parameters.prod.json, deploy workflows, main.json: none. All other settings below rely on code defaults (no Bicep override).

| setting | bicep value | code default | reader file:line |
|---|---|---|---|
| FLEX_ENABLED | 'true' (functionapp.bicep:117) | "false" | src/function_app.py:107 (gate on flex_intraday); also referenced in comments src/function_app.py:101,126, src/collector/handler.py:3963 (collector deliberately independent of it) |
| DAYTRADE_ENABLED | 'false' (functionapp.bicep:122) | "false" | src/function_app.py:159 |
| FLEX_RISK_BUDGET_PCT | none | FlexConfig.risk_budget_pct (0.40 per CLAUDE.md) | src/flex/config.py load_flex_config (~L146) |
| FLEX_PER_NAME_CAP_PCT | none | per_name_cap_pct (6.0) | src/flex/config.py ~L147 |
| FLEX_SLEEVE_CAP_PCT | none | sleeve_cap_pct (25.0) | src/flex/config.py ~L148; ALSO src/daytrade/config.py `flex_sleeve_cap_pct` (default 25.0) |
| FLEX_ATR_MULT / FLEX_MAX_STOP_PCT / FLEX_STOP_EPSILON_ATR / FLEX_TAKE_PROFIT_PCT / FLEX_TIME_STOP_DAYS | none | atr_mult 3.0 / 1.5 / n/a / 2.0 / 2 | src/flex/config.py ~L149-153 |
| FLEX_VWAP_WINDOW_MIN / FLEX_ENTRY_LATE_CUTOFF_MIN / FLEX_GAP_ADR_MULT / FLEX_MIN_ADV_USD | none | 30 / 30 / dataclass default / 50M | src/flex/config.py ~L154-157 |
| FLEX_KILL_SWITCH_ENABLED / _MIN_CLOSED_TRADES / _HIT_RATE_FLOOR / _MAX_DRAWDOWN_PCT | none | True / 20 / 0.45 / 2.0 | src/flex/config.py ~L158-164 (reader of the result: src/flex/killswitch.py) |
| FLEX_CONVICTION_PATH_ENABLED / _MAX_STOP_PCT / _NO_CHASE_ATR | none | False / 10.0 / 1.0 | src/flex/config.py ~L165-167 |
| FLEX_CASH_SLEEVE_FLOOR_PCT / FLEX_LITERAL_CASH_FLOOR_PCT | none | 5.0 / 0.75 | src/flex/config.py ~L168-169 |
| DAYTRADE_* (RISK_PCT .5, NOTIONAL_CAP_PCT 6, MAX_STOP_PCT 2, GAP_MIN_PCT 4, RVOL_MIN 3, PM_DOLLAR_VOL_MIN 3M, FLOAT_MIN/MAX 20M/100M, ROTATION_MIN .05, PRICE_MIN/MAX 5/100, SPREAD_MAX .0015, SMALL_CAP_USD 2B, DILUTION_LOOKBACK_DAYS 180, MAX_CANDIDATES 5, CONSOLIDATED_SOURCE "unavailable", ORB_MINUTES 5, ORB_MINUTES_C 15, ENTRY_CUTOFF_MIN 60, FLAT_MIN 105, SCALE_MODE "none", LLM_CLASSIFY False, PULSE_STRONG_PCT .6, PULSE_UP_PCT .2, PULSE_STRONG_BREADTH .75, PULSE_VOLUME_RATIO 2, PULSE_STALE_AFTER_S 3600, PULSE_FOREIGN_SOURCE "off", HAIRCUT_PP_PER_SIDE .10, DAY_MAX_LOSS_R 1, WEEK_HALT_R -3, UNLOCK_N 20, CELL_N 40, SPEC_VERSION "v0.1") | none | as listed | src/daytrade/config.py `load_daytrade_config` ~L153-187 |
| DAYTRADE_SECTOR_PULSE_MAP | none | unset (falls back to built-in map) | src/daytrade/pulse.py:80 (os.getenv) |
| FLEX_SCHEMA_V / FLEX_SCHEMA_SENTINELS | n/a - code constants, not env | - | src/analyzer prompt-gate (assert flex prompt schema) |
| FLEX_REENTERABLE, FLEX_NEWS_WINDOW_H, FLEX_MIN_PRICE_USD, FLEX_COUNT_CAP, FLEX_CANDIDATES_MAX/FILE, FLEX_REVIEW_FILE/DEFAULTS, FLEX_TIEBREAK_WINDOW_D, FLEX_CONVICTION_STATE_TABLE, FLEX_CONVICTION_HORIZON_DEFAULT | n/a - Python module constants, NOT env settings | - | src/collector/handler.py, src/flex/separation.py, src/shared/quadrants (some are Core-adjacent: FLEX_REENTERABLE feeds Core `flex_separation_set`, see Q4) |

Trap (CLAUDE.md lesson): removing/flipping `FLEX_ENABLED`/`DAYTRADE_ENABLED` must happen in functionapp.bicep, not only via `az`, or the next `infra/**` deploy reverts it. If settings are simply deleted from Bicep, the code default is "false" for both, so deletion = off.

### (i-part) Blob containers and tables written by Flex / DayTrade

Bicep (infra/modules/storage.bicep L4-10) defines NO flex/daytrade container; all below are created lazily at runtime (src/shared/storage.py write_json_blob L255-263, append_jsonl_blob L303-306). Tables: none are defined in Bicep either (no `tables` list in storage.bicep); created at runtime by the Python upsert helpers.

| name | kind | writer | readers |
|---|---|---|---|
| flex-ledger / ledger.json | blob | src/flex/ledger.py (_CONTAINER L15) | src/flex/handler.py, flex reconcile; src/daytrade/handler.py L44 `_CATALYST_LEDGER` (read-only cross-read) |
| flex-ledger / closed-trades.json | blob | src/flex/trades.py (L32) | src/flex/killswitch.py (slow arm); collector perhaps via flex ledger helpers |
| flex-ledger / equity-series.json | blob | src/flex/trades.py | web/api/function_app.py:764 (/api/performance); src/flex/killswitch.py (drawdown) |
| flex-ledger / kill-switch.json | blob | src/flex/killswitch.py | src/flex/handler.py; src/collector/handler.py:3620-3632 (echo into snapshot `flex_kill_switch`) |
| flex-state / {date}.json | blob | src/flex/handler.py:928-953 | src/collector/handler.py:3636 (`flex_state` block, walks back 7d); analyzer via snapshot; tests/test_flex_state_persistence.py |
| flex-decisions / {date}.jsonl | blob | src/flex/handler.py:957 (append_jsonl_blob) | none found in src/web (audit only) |
| flex-executions / {date}.json | blob | src/flex/handler.py:960 | none found in src/web (audit only) |
| daytrade-ledger / ledger.json | blob | src/daytrade/ledger.py:12 | src/daytrade/*; src/flex/handler.py:642 (flex reads it, to avoid collisions) |
| daytrade-nominations | blob container | src/daytrade/handler.py:39 (via HTTP daytrade_nominate) | src/daytrade/handler.py |
| daytrade-state | blob | src/daytrade/handler.py:40, state.py | src/daytrade |
| daytrade-log | blob (jsonl) | src/daytrade/handler.py:41 | audit only |
| daytrade-grades | blob | src/daytrade/handler.py:42, grading.py | src/daytrade |
| TradeHistory (table, shared with Core) | table | src/flex/handler.py:804 upsert (layer "flex"); analyzer writes flex rows | collector maturity stamping (collector/handler.py ~L1731-1739, L11304 flex-specific filters), track_record; Core shares it - DO NOT drop |
| FlexConvictionState (table) | table | collector `_FLEX_CONVICTION_STATE_TABLE` (collector/handler.py:7707) | collector `_build_flex_conviction` |
| ThematicHistory (table, `path:"flex_conviction"` rows, RK `FLEXCV-...`) | table | analyzer `_write_flex_conviction_history` | collector `_stamp_flex_conviction_outcomes`, `_build_flex_conviction_calibration`. Shared table with Core thematic rows (`path:"core_thematic"`) - keep |
| OverrideHistory | table | no flex-specific rows (regime_suspect etc. are Core) | - | 

All of the above are to be ARCHIVED in place, never deleted (account-holder instruction).
Other storage.py mention: src/shared/storage.py L247 and L298 comments say "used by the flex engine" - the generic JSON/JSONL helpers; KEEP (ORB can reuse them).

### docs/specs classification

| spec | flex/daytrade mentions | class |
|---|---|---|
| Flex_Catalyst_Engine_v1.0.md (44) | whole document | KEEP + add SUPERSEDED-by-ORB banner at top (it already has a STATUS line) |
| DayTrade_Lab_v0.1.md (40) | whole document | KEEP + add RETIRED/SUPERSEDED banner |
| Flex_Conviction_Sleeve_v1.0.md (13), Flex_Trailing_Stop_v1.0.md (31) | already SUPERSEDED | KEEP (already bannered; optionally note ORB) |
| ORB_Engine_v1.0.md (22) | new replacement spec (appeared during this session) | KEEP; source of truth |
| growth_strategy_spec_v1.md (12; §7 "Flex sleeve", L166-189, L231-235) | Flex sleeve as satellite, single-name cap 3-4% | EDIT NEEDED: add note that §7 Flex is superseded by ORB (strategy doc, so a one-line banner on §7 rather than rewrite). UNSURE Q5 |
| roster_revision_2026-07.md (2: L58, L70) | legacy exits re-enterable "as flex theses" via flex-candidates.json | EDIT NEEDED (minor): ORB universe excludes Core roster; flex re-entry path disappears. Add note, UNSURE Q4 |
| Phase_C_Performance_Feedback_v1.0.md (7) | "flex-first" scope, `by_layer.flex`, flex tags | KEEP w/ banner (historical; Core+flex grading) |
| Learning_Loop_v1.0.md (6: L103,129-140,165) | example proposal text and `flex-candidates.json` allowlisted target | EDIT NEEDED if the allowlist changes (Q1); examples are illustrative |
| Analyzer_Pipeline.md, Storage_Architecture.md, Data_Sources_Reference_v1.2/1.3, Future_Project_Wheel_Strategy.md | 0 meaningful mentions (Wheel 1 = "Flexible") | KEEP. NOTE Storage_Architecture.md has 0 flex mentions, so it doesn't document the flex containers; consider adding ORB containers there |

### FOLLOWUPS.md - OPEN entries mentioning flex/daytrade/catalyst/sleeve-performance (do NOT edit)
(Open section begins L664; Done at L2978.)
- #108 B2 remainder; S1 shipped, S2 at-close grading / realized expectancy still open (L877); #108a (L888) superseded-in-part record
- #107 `confirm_sessions` hysteresis vs ~2-day horizon, flex-conviction path (L949)
- #104 Trailing-stop flex design superseded, retained (L918)
- #98 analyzer fabricated per-ticker ADV from ledger row (L ~) - narration integrity touching flex ledger
- #92 F-3 MU 10x price quarantine + flex-orphan reconciliation (L1116)
- #90 Catalyst screen 0/25 nominated three sessions (L1091)
- #82 `scripts/validate_palette.js` absent, referenced by web/performance.js sleeve colour comment (L ~)
- #75 M-A conviction-path cash accommodation clamp collapses ladder (L1476)
- #74 / #73 post-merge watches on flex-conviction entry/exit (L1461, L1447)
- #76 process gate "what else is in this collection?" (cross-refs flex conviction PRs)
- #72 / #71 G-3 / G-2 decision gates, flex conviction path (L1428, L1412)
- #63 Ladder numbers (thematic + flex conviction) pending Jorge (HIGH)
- #61 Sleeve-appropriate Phase C grading (flex realized R-multiple) (L1215)
- #59 Overnight/sector read-through, gated on `catalyst_screen` ledger (L1691)
- #58 `catalyst_score` weight tuning (L1643)
- #34 `global_overnight` tone block, "flex-facing" (L2587)
- #67 Periodic roster review - gated on screening universe (mentions flex)
- Not flex (Core sleeve meaning; ignore): #95 settling window per sleeve, #96 SGOV carve-out, #37 sleeve-selection hysteresis, #86 flexible CPI
- Done/SUPERSEDED (not open): #10 trailing stop, #8, #36, #43-#45, #57, #60, #62, #69, #70.

## Appendix D — `src/flex` and `src/daytrade`, kill switch, tests, blobs, prefixes

### 0. HEADLINE: core -> flex/daytrade coupling (the DECOUPLE list)

Only ONE core file imports flex; NOTHING in core imports daytrade (src/function_app.py is the only other importer of both).

| Importer | Line | Symbol | Minimal decouple |
|---|---|---|---|
| src/collector/handler.py | 50 | `from flex import trades as flex_trades` | Sole use: `flex_trades.read_kill_switch_state()` at 3625 (echo `flex_kill_switch` snapshot key, key emitted at 3965). Replace with inline `read_json_blob("flex-ledger","kill-switch.json") or {}` (keeps the key + analyzer prompt behaviour during/after retirement), or delete the echo block 3620-3632 + key 3965 together with the prompt text. |
| src/collector/handler.py | 51, 3455, 3519 | `load_flex_config` -> `_flex_cfg.min_adv_usd` | Only `min_adv_usd` is used (3519, passed to `_build_catalyst_screen`). Replace by a module constant / `float(os.getenv("FLEX_MIN_ADV_USD", 50_000_000))` (default 50e6 per flex/config.py:73-ish `min_adv_usd`). Delete line 51 and 3455 `try:`-local use. |
| src/collector/handler.py | 52, 1123 | `from flex.indicators import avg_dollar_volume` | 10-line pure fn (flex/indicators.py:124-135). Copy into collector/catalyst_screen.py (or shared/) and repoint 52/1123. |
| src/collector/handler.py | 53, 467 (function-local re-import), 492, 524, 2801-2802, 3518, 7890, 7907 | `FLEX_REENTERABLE`, `flex_separation_set` (13 occurrences) | Move `src/flex/separation.py` verbatim to `src/shared/separation.py` (it imports only `shared.quadrants`), repoint lines 53 and 467. This is also what ORB needs ("never trade a Core roster ticker"). Alternative if the catalyst screen/flex candidates are being deleted in the same PR: delete the dependent collector code (the spec's "Remove" list says collector flex state/kill-switch/orphan echoes + catalyst screen) - but that is a Core change, so it needs the user's sign-off. |
| src/function_app.py | 10, 13 | `from daytrade.handler import run_daytrade_manage, save_daytrade_nominations`; `from flex.handler import run_flex_intraday` | Delete imports + timer `flex_intraday` (91-115), route `flex` (117-145), timer `daytrade_manage` (147-168), routes `daytrade_nominate` (170-190), `daytrade` (192-220). |
| tests | see section 4 | `flex.separation` imported by tests/test_flex_dynamic_candidates.py:141,150,162,170,221 and tests/test_flex_eligibility.py:21 (these test COLLECTOR code) | repoint to the new module path. |

Non-import (blob/string/concept) coupling in core - these do NOT break on deletion of the packages but remain as dead/confusing code unless removed (spec Phase 0 lists them):
- collector: `_build_catalyst_screen` (1063), `_load_flex_candidates` (441), `_quarantine_flex_price` (940), `_build_flex_conviction*`, `_stamp_flex_conviction_outcomes` (2247), `_build_flex_conviction_calibration` (2318), `_build_flex_eligibility` (7860), `_build_flex_reconciliation` (4295, reads `flex_state["held"]`), `_build_flex_review`/`_classify_flex_review` (11282/11222, already DEAD per CLAUDE.md), flex-state echo 3615-3660 (reads blob `flex-state/{date}.json`, 8-day walkback), `FlexConviction*` state tables.
- analyzer/handler.py: `assert_flex_prompt_schema` (111) + `_FLEX_SCHEMA_SENTINELS = ("FLEX_SCHEMA_V1","flex_nominations")` (101) - **if the Flex section of project-instructions.md is removed this guard raises and the analyzer refuses to run**; `_write_flex_conviction_history` (1592, RowKey `FLEXCV-…` at 1621), `watch_candidates` sanitizer (1343-1381).
- executor/handler.py:310 comment only; shared/{quadrants,reference_execution,trade_validation}.py comments only ("flex leftover"); `shared/clients/alpaca.py` (`get_latest_quote` 197, `get_calendar` 216, `get_activities` 227) and `shared/clients/fmp.py:136-158` (`get_shares_float`, `get_sec_filings`, `get_aftermarket_quote`) were added for DayTrade but live in shared - KEEP (ORB can use them; `get_calendar`/`get_activities`/`get_order` are also used by core: collector 4248, 4382, 10651).
- learning: src/learning/bundle.py:47 and schema.py:25 allow-list `config/flex-candidates.json` as an amendment target; tests/test_learning_schema.py:145 asserts it. Removing the file requires editing both + that test.
- web: web/api/function_app.py:716-770 (`_sleeve_series`/`_attach_sleeve_series`, reads blob `flex-ledger/equity-series.json` at 764); web/performance.js (~19, 429 "Flex sleeve contribution"), web/performance.html:42 ("Flex catalyst sleeve"). Archive, not delete, the blob (spec says archive).
- config: src/config/flex-candidates.json, src/config/flex-review.json; risk-limits.json mentions.
- infra/modules/functionapp.bicep:117 `FLEX_ENABLED = 'true'`, :122 `DAYTRADE_ENABLED = 'false'` (CLAUDE.md: change in BOTH Bicep and via az, in both directions). No `FLEX_KILL_SWITCH_*`/`FLEX_SLEEVE_CAP_PCT` settings in Bicep (grep of infra/): all defaults from code.
- scripts/orb_phase0_flat_check.py (+ tests/test_orb_phase0_flat_check.py): already exists; references flex/daytrade only as blob names/prefixes (data), no imports. KEEP.
- scripts/probe_dynamic_candidates.py: mentions FLEX_REENTERABLE in strings only (no import).
- .github/workflows: no flex/daytrade references.

### 1. Module-by-module (src/flex, src/daytrade)

#### src/flex (4,159... lines total incl. daytrade 4,782)

| Module (lines) | Purpose | Public functions | Importers outside package | Verdict |
|---|---|---|---|---|
| `__init__.py` (12) | docstring | - | - | DELETE |
| `config.py` (170) | `FlexConfig` frozen dataclass of FLEX_* knobs (bracket 2%/1.5%, 2-day time stop, caps 6%/25%, kill-switch thresholds 20 trades/0.45/2.0%) | `FlexConfig`, `load_flex_config`, `_env_*` | collector 51/3455; tests (flex_*) | DECOUPLE (collector uses only `min_adv_usd`), then DELETE. PORT pattern only (env-overridable frozen dataclass with `spec_version`). |
| `entry.py` (505) | pure entry gates + sizing (`build_flex_entry` catalyst, `build_conviction_entry`), bracket take-profit/stop construction | `size_flex_position`, `build_flex_entry`, `build_conviction_entry`, `_size_conviction_position`, `_cash_accommodation_shares` | flex.handler only; tests | DELETE |
| `exit_state.py` (134) | time-stop-only exit state; `trading_days_between` (weekday count, NOT holiday aware) | `build_flex_exit_state`, `trading_days_between`, `NEXT_ACTIONS` | flex.trades:27, flex.handler; tests | DELETE; copy `trading_days_between` (25 lines, 36-48) if ORB `holding_days` wants it |
| `handler.py` (984) | orchestration `run_flex_intraday` (STEP 0 reconcile, clock gate, nominations, bars, management, kill switch, entry, persist); order helpers | `run_flex_intraday`, `_finalize_closed_trade`, `_sweep_orphan_orders`, `_is_flex_catalyst_order_id`, `_coid`, `_persist`, `_session_minutes*`, ... | src/function_app.py:13 (only) ; tests | DELETE (PORT ideas: finalize/sweep/persist) |
| `indicators.py` (140) | pure VWAP/ATR/ADR/gap/ADV math | `session_vwap`, `vwap_slope`, `atr14`, `avg_daily_range`, `gap_pct`, `gap_in_adr`, `avg_dollar_volume`, `opening_range_low` | collector 52/1123 (`avg_dollar_volume`); flex.entry/exit_state; tests | DECOUPLE (`avg_dollar_volume`), then DELETE. PORT-candidate: `atr14` (14-day ATR, spec needs ATR) and `avg_dollar_volume`/`session_vwap`. NOTE: ORB's 14-day ATR via daily bars works since FMP/Alpaca daily has h/l (Alpaca bars do; only FMP light is close+vol). |
| `killswitch.py` (204) | pure two-trip kill switch | `evaluate_kill_switch`, `hit_rate`, `cumulative_drawdown_usd` | flex.handler:24 only; tests/test_flex_news_momentum_b1.py:452-528 | PORT (section 2.3) then DELETE |
| `ledger.py` (71) | open-position ledger blob `flex-ledger/ledger.json`; `new_entry` w/ stable `trade_id` | `read_ledger`, `write_ledger`, `new_entry` | flex.handler, flex.trades? (no), tests | PORT pattern then DELETE |
| `reconcile.py` (151) | pure STEP-0 reconcile: closed-at-broker, qty resize, phantom order ids, no-naked-long repair, orphan orders w/ `engine_owned` | `reconcile_ledger` | flex.handler; tests | PORT then DELETE |
| `separation.py` (61) | `flex_separation_set` (every role pool member + non-reenterable legacy + held legacy), `FLEX_REENTERABLE` | `flex_separation_set`, `FLEX_REENTERABLE` | **collector (13 sites)**, flex.handler, tests | **DECOUPLE** -> move to `src/shared/separation.py`; ORB reuses (or just `CORE_ROSTER`). |
| `trades.py` (259) | closed-trade ledger builders, kill-switch persistence, sleeve equity series | `read/write/record_closed_trade`, `fills_from_activities`, `merge_broker_fills`, `build_closed_trade`, `read/write_kill_switch_state`, `read/write/upsert_equity_*`, `build_sleeve_mark` | **collector 50** (`read_kill_switch_state`), flex.handler; tests | PORT (section 2.1) + DECOUPLE collector, then DELETE |

#### src/daytrade (separate third engine; DAYTRADE_ENABLED=false in Bicep:122, spec docs/specs/DayTrade_Lab_v0.1.md)

No module in core imports any of these. Only src/function_app.py:10 imports `daytrade.handler`. tests/test_daytrade_separation.py:108,116 assert collector/analyzer never import daytrade and daytrade never imports flex.

| Module (lines) | Purpose | Public functions | Verdict |
|---|---|---|---|
| `__init__.py` (6) | docstring | - | DELETE |
| `classify.py` (54) | optional LLM catalyst class A-D (ships OFF) | `classify_catalyst_llm` | DELETE |
| `config.py` (188) | `DayTradeConfig` (window -5..+110 min, flat_min 105, week_halt_r -3.0, spec_version "v0.1", reads `FLEX_SLEEVE_CAP_PCT`) | `DayTradeConfig`, `load_daytrade_config` | DELETE; PORT the `spec_version` + `__post_init__` validation idea |
| `gates.py` (202) | pure validation gates for nominated candidates (fail-closed on missing data) | `run_validation_gates`, `select_survivors` | DELETE (ORB universe filter is different; `_gate()` result shape `{gate,value,threshold,basis,passed}` is a nice pattern) |
| `grading.py` (172) | net-R, outcome classes, pre-registered N=20/40 rules, `entry_refusal` (kill flag per spec_version) | `haircut_r`, `net_r`, `outcome_of`, `build_daytrade_grades`, `entry_refusal`, `contra_pulse_half` | DELETE; PORT-candidate `net_r`/`outcome_of`/haircut for R-multiples |
| `handler.py` (680) | 1-min loop `run_daytrade_manage` + `save_daytrade_nominations` | those two + `_session_minutes` (630), `_utc` (656), `_flatten_all` (439), `_apply_repair` (458), `_record_closure` (475), `_coid` (665) | DELETE; PORT session-window + `_utc` + flatten pattern |
| `ledger.py` (58) | `daytrade-ledger/ledger.json`, `COID_PREFIX="FLEXD"` | `read_ledger`, `write_ledger`, `new_entry`, `COID_PREFIX` | DELETE |
| `patterns.py` (236) | pure ORB (1-min) + VWAP-pullback signals | `allowed_patterns`, `cumulative_vwap` (49), `opening_range` (62), `orb_signal` (79), `vwap_pullback_signal`, `is_print_stale` (222) | DELETE; **PORT-candidate** `cumulative_vwap`, `opening_range`, `is_print_stale` (ORB 5-min differs: stop at ATR*10%, no VWAP/volume confirm) |
| `pulse.py` (260) | global sector pulse (spec addendum); NOT imported by handler (dead) | `build_sector_pulse`, ... | DELETE |
| `reconcile.py` (75) | STEP-0 reconcile for lab ledger; no-naked-long = `flatten_orphan` (market sell) | `reconcile_daytrade` | PORT (stricter than flex: intraday => flatten, which fits ORB flat-at-close) |
| `sizing.py` (56) | risk-budget + notional cap + joint-sleeve sizing, `binding` label | `size_daytrade_entry` | DELETE; pattern for ORB 1%-of-sleeve / sleeve/20 cap |
| `state.py` (104) | day-state machine, slots, weekly breaker -> `halt.json` | `new_day_state`, `record_outcome`, `can_enter_slot1/2`, `week_monday`, `apply_weekly_breaker`, `is_halted` | DELETE; PORT-candidate for ORB daily/weekly loss halt |

### 2. PORT evaluation (copy-never-import into src/orb/)

#### 2.1 Closed-trade builders - src/flex/trades.py
- Carry: `read_closed_trades`/`write_closed_trades` (42-48), `record_closed_trade` (51-63; idempotent append keyed on `trade_id`, read-modify-write - note NOT atomic/ETag, fine for a single 1-min writer but ORB's 1-minute cadence could overlap runs; consider lease/ETag or `use_monitor`), `fills_from_activities` (66-87; pure, drops malformed rows), `merge_broker_fills` (90-122; recorded fills are a floor, broker sells beyond cumulative qty appended, last gets `closing_reason`, earlier ones "scale_out"), `build_closed_trade` (125-181; null-safe P&L: reasons `no_fills_recorded|missing_entry_price|missing_fill_price|zero_qty_realized`; `r_multiple = pnl_per_share / risk_per_share`).
- Change for ORB: (a) **SHORTS** - P&L math is long-only (`proceeds - cost`, `risk_per_share = entry - stop`); ORB trades shorts so sign by `side`, entry fills are sells and exits are buys, `fills_from_activities(...,"buy")/("sell")` must swap; (b) `risk_per_share` comes from ledger `new_entry` (`entry_price - initial_stop`, flex/ledger.py:60) - ORB sets it from the 10%-ATR stop distance; (c) drop `catalyst_score`/`score_components`/`nomination_thesis` fields (178-180) -> `rel_volume`, `direction`, `atr14`, `spec_version`, `slippage`; (d) `exit_reason` vocab -> `stop|eod_flat|manual|kill`; scale-out concept goes away (flat at close, no target) so `merge_broker_fills`' "scale_out" labelling should become simply "unaccounted broker fills"; (e) `holding_days` uses `flex.exit_state.trading_days_between` (27) - replace with minutes held; (f) `_CONTAINER="flex-ledger"` constants (32-35) -> `orb-ledger`; (g) `build_sleeve_mark`/equity series (213-259) optional.
- Dependencies on flex-specific config: none (pure), only the import of `flex.exit_state` and `shared.storage`. Handler-side finalizer `_finalize_closed_trade` (flex/handler.py:695-744): re-derives fills from `client.get_activities("FILL", after=entry_date)` (711), **writes NO record when zero confirmed broker buy fills** (718-724; keep - entry never filled), uses broker VWAP of buys as entry price (726-728), only a PRICED extra_fill is folded into recorded fills (736-737; keep, comment 730-735 explains the shadowing bug), then `build_closed_trade` + `record_closed_trade` (740-743). Also `_order_fill_price` (664-676), `_record_closed_trade_write_failure` (679-692: surfaces failures into the decisions log), `_trade_history_extra` (747-767; TradeHistory backfill - optional for ORB).
- Test helpers to carry: tests/test_flex_trades.py (248 lines), tests/test_flex_close_paths.py (`blob_store` fixture 20-34, `_StubAlpaca` 37-62: records `submitted`/`cancelled`, `get_order` by `fills` map, canned `get_activities`).

#### 2.2 Reconcile-first pattern
- flex/reconcile.py:36-151 (`reconcile_ledger`): returns `(new_ledger, exits_to_record, repairs, orphan_orders)`. Carry: closed-at-broker -> record + drop (73-88), qty resize (91-98), phantom order-id clearing (101-107, uses open-order id set), no-naked-long `place_missing_stop` repair emitted first via `_PRIORITY` sort (12-17, 109-122, 124), orphan scan of every open order whose symbol is not in the post-reconcile ledger (126-149) with `engine_owned` flag from the closed row's own `order_ids` (bracket child legs carry broker UUID client ids, verified live 2026-09-12; 91-95, 145).
- daytrade/reconcile.py:35-75: same skeleton but repair = `flatten_orphan` (market sell) instead of re-stop; ledger-only scoping ("a symbol the lab did not open is never touched").
- Change for ORB: ORB entry is a STOP order at the 5-min high/low (pre-fill, resting), so the ledger needs a pending state (entry order live, no position yet) that these two don't model: a symbol with open entry order and no position must NOT be treated as "closed_at_broker". Shorts: `held` is negative; both implementations use `_num(p.get("qty"))` and `held <= _QTY_EPS` -> would treat a short as gone. `_is_resting_stop` hardcodes `side == "sell"`; shorts need `buy` stop. Prefix attribution: ORB- ids; keep ledger-keyed (not prefix-keyed) ownership, with prefix + ledger order_ids as the two proofs (see scripts/orb_phase0_flat_check.py `classify_flatness`, already written, same logic).
- Handler ordering to copy (flex/handler.py:64-93): read positions + open orders; on failure log and **skip reconcile** (flex sets both to []; daytrade instead returns `broker_unreachable` at 104-105 - daytrade is the safer one: flex's `[]` fallback makes every ledger row look "closed_at_broker" if the read fails! Copy the daytrade behaviour). Then record exits -> apply repairs (not in dry_run) -> sweep orphans -> `write_ledger`, all BEFORE the clock gate.
- Persist-on-entry (flex/handler.py:478-485): write ledger immediately after order submit (MU orphan incident).

#### 2.3 Kill switch - see section 3 for mechanics. Carry `evaluate_kill_switch`, `hit_rate`, `cumulative_drawdown_usd` (killswitch.py:58-201) and `read/write_kill_switch_state` (trades.py:186-201). Change: thresholds from FlexConfig attrs (`getattr(cfg,"kill_switch_*")`, 117-120) -> ORB config; `closed_trades` rows need `pnl_usd`, `closed_date`, `trade_id` (same schema as 2.1, so keep field names); drawdown vs `equity_usd` - for a 25% sleeve consider the sleeve notional not account equity (decision G-11 flex used account equity, 2.0%). Two caveats to FIX when porting (section 3.4).

#### 2.4 Order / client_order_id helpers
- flex/handler.py:966-970 `_coid`: `f"FLEXC-{today}-{sym}-{kind}-{uuid.uuid4().hex[:6]}"[:48]` (Alpaca limit 48). **Random suffix means a retried submission is NOT idempotent** (new uuid each tick); ORB spec wants "idempotent ORB- ids" -> make deterministic `ORB-{date}-{sym}-{kind}` (no uuid) so Alpaca rejects duplicates. daytrade/handler.py:665-666 same pattern with `COID_PREFIX`.
- `_is_flex_catalyst_order_id` (310-319) prefix test; `_sweep_orphan_orders` (322-362) cancels only own-prefix or `engine_owned` orders (strict scoping - must keep).
- `_apply_repair` (280-307), `_cancel_stops` (512-517), `_issued` (524-532), `_cancel_conflicting_orders` lives in executor (core) - untouched.
- Alpaca client `submit_order` supports `order_class` bracket/oto/oco, `take_profit`, `stop_loss`, `stop_price` (shared/clients/alpaca.py:53-68) - shared, KEEP.

#### 2.5 Calendar-derived session-window gating
- daytrade/handler.py:630-640 `_session_minutes(client, today, now_et)`: `client.get_calendar(today, today)` -> `cal[0]["open"]` "HH:MM" -> minutes since open (works on half days). Window gate 117-122: `-window_pre_open_min <= minutes <= window_end_min` else `outside_window` (config.py:81-82, 5 and 110). Flex mirror: flex/handler.py:832-859 incl. `_session_minutes_remaining` using calendar `close` (correct on half-days). Cron is TZ-independent `0 * * * * 1-5` (function_app.py:152); never encode market hours.
- Also `_utc(today,"HH:MM")` (daytrade/handler.py:656-662, ET wall clock -> UTC string, DST-safe) for bar queries; `_now_et` (674-680) fallback is wrong-ish (`astimezone()` = server local) - use zoneinfo only / `shared.timeutil`.
- Carry: `_session_minutes`, `_session_minutes_remaining`, `_utc`, clock gate (flex 95-105: `client.get_clock()["is_open"]`; failure => treated closed). Change: ORB window -5..+close; flat at close - minutes-to-close from calendar `close`; `is_open` check should not block the flatten-before-close tick.

#### 2.6 Test helpers with stub Alpaca clients
- tests/test_flex_close_paths.py:20-62 (`blob_store` monkeypatch fixture patching `trades_mod.read_json_blob/write_json_blob`; `_StubAlpaca`).
- tests/test_flex_order_hygiene.py:26-36 (`_FakeClient` cancel/submit recorder), :104 `_FailingClient` subclass.
- tests/test_flex_conviction_wiring.py:22 (`_FakeClient`), :114/:147 (`_FailingClient`).
- tests/test_flex_news_momentum_b1.py:286, :316 (`_Client` ad hoc).
- tests/test_flex_state_persistence.py:28-115 (monkeypatching `fh.read_json_blob/write_json_blob/append_jsonl_blob`, carry-forward test pattern).
- tests/test_daytrade_separation.py:29-126 (prefix disjointness, sleeve-cap arbitration, "package never imports X" source-scan tests).
- tests/test_orb_phase0_flat_check.py (91 lines; already ORB; KEEP).
Copy these into tests/ for `src/orb` (e.g. a shared `tests/_orb_stubs.py`) before deletion; ORB stubs must add `get_clock`, `get_calendar`, `list_positions`, `list_orders`, `get_account`, short-side positions.

### 3. KILL SWITCH precise mechanics (runbook)

#### 3.1 Persistence
- Blob: container **`flex-ledger`**, name **`kill-switch.json`** (src/flex/trades.py:32, 34). Read `read_kill_switch_state()` trades.py:186-195 -> `read_json_blob(...) or {}`; write `write_kill_switch_state(state)` trades.py:198-201 -> `write_json_blob` (storage.py:255-263; **full overwrite**, `overwrite=True`, container auto-created).
- JSON written each time by `evaluate_kill_switch` (killswitch.py:127-144 and branches) - complete key set: `enabled` (bool), `tripped` (bool), `trip_reason` (str|null: `max_drawdown`|`hit_rate_floor`|carried reason|`prior_trip`), `hit_rate` (float|null), `gradeable_trades` (int), `closed_trades` (int), `drawdown_usd`, `drawdown_pct`, `drawdown_basis` ("peak_to_trough_cumulative_realized_pnl"), `thresholds` {`min_closed_trades` 20, `hit_rate_floor` 0.45, `max_drawdown_pct` 2.0}, `tripped_at` (str date|null|"PENDING"), `note` (str).
- `cleared_at` / `cleared_by` are **never written by code**; they are read only as `prior.get("cleared_at")` (killswitch.py:151). `cleared_by` is not read anywhere. Therefore after the first re-write by the engine, any `cleared_at`/`cleared_by`/other custom keys in the blob are dropped (the engine rewrites only the keys above).
- "PENDING" tripped_at is replaced by the ET date in flex/handler.py:183-184 (`today`).

#### 3.2 When it runs and what it gates
- flex/handler.py order: STEP 0 reconcile+repairs+orphan sweep (64-93) -> STEP 1 clock gate; closed => `_persist(market_closed=True)` and return (95-105) -> STEP 2 nominations (107-137) -> STEP 3 bars (139-143) -> **STEP 4 management of every held name incl. time stop (145-165)** -> **kill-switch evaluate+persist at 170-194** -> STEP 5 entries: catalyst loop `for nom in (nominations if not kill_state.get("tripped") else [])` (199), conviction loop `({} if tripped else conviction_candidates)` (226) -> persist (254-273).
- Read path used by the engine: `evaluate_kill_switch(flex_trades.read_closed_trades(), equity, cfg, flex_trades.read_kill_switch_state())` (179-182) then `write_kill_switch_state(kill_state)` (185) - i.e. the blob is read and rewritten on EVERY in-hours tick (~every 15 min).
- Therefore a trip suppresses NEW entries only. Exits are managed BEFORE the check (STEP 4), so existing positions continue to be managed normally: time stop executes via `_act_on_exit` (365-393: cancels bracket legs `_cancel_stops`, market sell DAY with `FLEXC-...-tstop-` id, finalizes closed trade); repairs/orphan sweep (STEP 0) also continue; conviction release exits too.
- Time stop: `build_flex_exit_state` (exit_state.py:60-127): `tdays = trading_days_between(entry_date, now)` = **weekday count, entry day = 0, no holiday calendar** (36-48); fires `next_action="time_stop"` when `tdays >= cfg.time_stop_days` (config.py `time_stop_days: int = 2`; env FLEX_TIME_STOP_DAYS) for non-conviction rows (123); requires `current_price` (last 1-min close) and `atr14` non-None and `qty_current >= 1` else `unknown` (never forces) (111-113). So a position opened Mon is cut at market on Wed's first in-hours tick with bars. The bracket TP (+2%) / stop (-1.5%) legs rest at the broker and keep working regardless of the engine/kill switch (they only exit, never enter).
- `FLEX_ENABLED` is separate (function_app.py:107, `os.getenv("FLEX_ENABLED","false")`; Bicep functionapp.bicep:117 = 'true'). **If FLEX_ENABLED is set false the engine does not run at all: no time stop, no reconcile, no stop repair, no orphan sweep** - the bracket legs at the broker remain the only protection, and the 2-day time stop will not fire. So order of retirement must be: trip -> wait until flat (all positions closed by bracket/time stop) -> only then FLEX_ENABLED=false (this matches spec Phase 0 checklist).

#### 3.3 VERDICT: is a hand-written trip honored and carried forward?
Hand-written state `{"tripped": true, "trip_reason": "manual_retirement_orb", "tripped_at": "<ts>"}` with no `cleared_at` -> **HONORED, carried forward**, with caveats.
Evidence:
- killswitch.py:151-158: `if prior.get("tripped") and not prior.get("cleared_at"):` returns `tripped=True`, `trip_reason = prior.get("trip_reason") or "prior_trip"`, note "TRIPPED (carried forward, manual_retirement_orb). Re-enabling is a human action - this never clears itself, even if the numbers recover." This branch comes BEFORE both the fast (161) and slow (176) arms, so metrics (improved or not, even with zero closed trades) cannot clear it. `tripped_at` is preserved (line 142 `prior.get("tripped_at")`, so a handwritten timestamp survives; the "PENDING" substitution at handler:183 only fires on the literal string).
- Handler then sets `decisions["kill_switch"]=kill_state` (188) and, because `tripped`, logs ERROR (190-191) and appends `{"reason": "kill_switch:manual_retirement_orb","note": ...}` to `orders_suppressed` (192-194); both entry loops skipped (199, 226).
- Persisted forward: handler:185 re-writes the carried state on each tick (without `cleared_at`), so it stays sticky across ticks.
- Test evidence: tests/test_flex_news_momentum_b1.py:496-511 (`test_s1_a_trip_is_STICKY_and_never_clears_itself`): prior {tripped True, max_drawdown, tripped_at 2026-09-14} + perfect numbers -> still tripped, tripped_at preserved, "human action" in note; with `cleared_at` set -> tripped False. No test uses a custom `trip_reason`, but the code path is reason-agnostic (any truthy `trip_reason` string is echoed).
Caveats that matter for a MANUAL blob edit:
1. **Omit `cleared_at`, or set it null/""** (any truthy value releases: `not prior.get("cleared_at")`).
2. **`FLEX_KILL_SWITCH_ENABLED=false` bypasses it AND erases it.** The `enabled` check at killswitch.py:146-148 returns before the sticky branch with `tripped False`, and the handler then overwrites the blob (185). Not set in Bicep today (grep infra/ shows no FLEX_KILL_SWITCH_*), default True (config.py:95/158).
3. **Silent read failure erases the trip.** `read_json_blob` -> `_read_json_blob` (storage.py:233-244) catches ALL exceptions (not just 404) and returns None -> `read_kill_switch_state()` returns `{}` (trades.py:192) -> evaluate treats as "no prior trip" -> numbers fine -> `tripped False` -> **written back over the manual trip** (handler:185). Likelihood low (transient storage/auth error) but real, and the handler comment 175-176 ("the PERSISTED sticky trip still binds through read_kill_switch_state") is only true when the read succeeds. Mitigation: after the first in-hours tick re-read the blob and confirm `tripped: true` persists; check again after any deployment/restart.
4. **Fail-OPEN on evaluation exception**: if `evaluate_kill_switch`/`read_closed_trades` raises, `kill_state = {}` (177-187) -> `.get("tripped")` falsy -> entries are NOT suppressed that tick (blob not rewritten, so the trip itself survives, but entries proceed). `read_closed_trades` goes through the same swallow-all read (returns [] on None) so an exception is unlikely in practice; `equity` of 0.0 only affects `drawdown_pct` (None), not stickiness.
5. Engine overwrites the blob every in-hours tick: extra keys (`cleared_by`, operator notes) are discarded after the first tick. Put notes elsewhere. `enabled`, `hit_rate`, `thresholds`, ... will be filled by the engine.
6. It is evaluated only on **in-hours ticks** (after STEP 1 clock gate). Closed ticks (market closed, and the cron fires 24h on weekdays, `0 */15 * * * 1-5`) neither evaluate nor write kill-switch. Entries only occur in-hours, so no gap. A dry run (`POST /api/flex {"dry_run":true}`) still evaluates AND writes the blob (the write is not dry_run gated, handler:185) but only when the market is open.
7. Belt-and-braces idea (not verified by test): `FLEX_SLEEVE_CAP_PCT=0` makes `sleeve_room_usd=0` (handler:198, 206) which feeds sizing at entry.py:70-71 (min with floor(room/price)); not exercised by me.

#### 3.4 What to look at to confirm the trip took effect
- Blob `flex-ledger/kill-switch.json`: `tripped: true`, `trip_reason: "manual_retirement_orb"`, `note` begins "TRIPPED (carried forward, manual_retirement_orb)." (rewritten by the first in-hours tick).
- Blob `flex-state/{ET-date}.json` -> key `kill_switch` (the full state; written by `_persist` at flex/handler.py:944-951, only on in-hours ticks; market-closed ticks keep the existing file via carry-forward 926-941 and do not refresh `kill_switch`).
- Blob `flex-decisions/{date}.jsonl` (append per tick, handler:957): each tick's record contains `"kill_switch": {...}` (188) and `orders_suppressed` entries `{"reason": "kill_switch:manual_retirement_orb", "note": "TRIPPED (carried forward, ...)"}` (192-194).
- Function log (App Insights, ERROR severity) message text: `FLEX KILL SWITCH TRIPPED (manual_retirement_orb) -- no new entries. TRIPPED (carried forward, manual_retirement_orb). Re-enabling is a human action — this never clears itself, even if the numbers recover.` (handler.py:190-191).
- Next collector run (09:00 ET): snapshot key `flex_kill_switch` = `{available: true, **state}` (collector/handler.py:3623-3627, 3965) and a collector WARNING `Flex kill switch TRIPPED (%s): %s` (3628-3630); analyzer prompt must raise it under Data Integrity Warning (CLAUDE.md "Flex kill switch, S1").
- `daily-executions`/flex-executions: no `entry_bracket`/`entry_oto` records (`_issued` kind, handler:447/457) after the trip; `flex-executions/{date}.json` only written if any order was issued (958).
- Time stops post-trip still appear as `exits` entries with `next_action: "time_stop"` and `orders_issued` kind `time_stop`.

#### 3.5 DayTrade Lab: kill switch?
- No dedicated kill switch. Gating: `DAYTRADE_ENABLED` app setting (function_app.py:157-163; Bicep functionapp.bicep:122 = 'false'); if false the timer returns before any code runs (also no reconcile!).
- Internal brakes (all inactive while disabled): weekly breaker -> blob `daytrade-state/halt.json` `{halted_on, week_r, until, reason: "weekly_breaker_-3.0R"}` (state.py:83-100; written handler:515; read 128-129 `is_halted`) - halts until next Monday; day-level breakers in `daytrade-state/{date}.json` (`day_done`, state.py); pre-registered rule `grading.entry_refusal` (grading.py:145-160) reads `daytrade-grades/latest.json` `rules.kill` / `blocked_cells` (handler:310, 334-340) - "kill" is automatic per spec_version, not manual. Writing `halt.json` with `until` in the future also halts it (is_halted: `until > date_str`), but only matters if enabled.
- Because DAYTRADE_ENABLED=false, its ledger will not be reconciled, flattened, or its orphans swept: verify `daytrade-ledger/ledger.json` is empty/absent and no `FLEXD-` orders/positions exist at Alpaca BEFORE deleting (scripts/orb_phase0_flat_check.py does this).

### 4. Test inventory (tests/*.py, excluding __pycache__)

Legend: DELETE = tests only flex/daytrade; EDIT = tests Core but contains flex assertion/import (named); KEEP = Core test where flex appears only as incidental vocabulary ("flex leftover", MU).
Decision-dependent note: the core-side catalyst/flex helpers listed in section 0 (non-import coupling) are Core code; the plan lists them for removal in the same PR but the user said Core must not change - so tests of those functions are marked KEEP-IF-CORE-STAYS / DELETE-IF-CORE-REMOVED.

#### 4.1 DELETE (import flex.* / daytrade.* or exercise only those packages)
- tests/test_daytrade_breakers.py (imports daytrade)
- tests/test_daytrade_gates.py
- tests/test_daytrade_grading.py
- tests/test_daytrade_patterns.py (PORT idea: copy opening_range/is_print_stale tests into orb tests first)
- tests/test_daytrade_separation.py (incl. "collector/analyzer never import daytrade" guard 108-116)
- tests/test_daytrade_sizing.py
- tests/test_flex_close_paths.py (imports flex.handler, flex.trades, flex.ledger; PORT stubs)
- tests/test_flex_conviction_entry.py
- tests/test_flex_conviction_exit_state.py
- tests/test_flex_conviction_wiring.py (imports `flex.handler` `_flex_conviction_candidates`...)
- tests/test_flex_entry.py
- tests/test_flex_exit.py
- tests/test_flex_indicators.py
- tests/test_flex_news_momentum_b1.py (528 lines: flex.config/entry/separation/handler/reconcile/killswitch; includes the S1 kill-switch tests 440-528 - PORT to tests for orb killswitch first)
- tests/test_flex_order_hygiene.py
- tests/test_flex_reconcile.py
- tests/test_flex_separation.py (imports flex.separation: if separation moves to shared, RETARGET instead of delete)
- tests/test_flex_sizing.py
- tests/test_flex_state_persistence.py
- tests/test_flex_trades.py
#### 4.2 EDIT (Core code under test, but imports flex.separation or asserts flex behaviour)
- tests/test_flex_dynamic_candidates.py - tests collector `_load_flex_candidates` + analyzer watch_candidates; `from flex.separation import FLEX_REENTERABLE`/`flex_separation_set` at lines 141, 150, 162, 170, 221 (tests 140-175 + 216/256-262 depend on it). Repoint to shared module, or DELETE with the feature if the dynamic-candidate path is removed. Also contains unrelated gate-zeroed-row tests (406-478, `reference_weights`, VXUS) that must be KEPT/moved.
- tests/test_flex_eligibility.py - collector `_build_flex_eligibility`; `from flex.separation import FLEX_REENTERABLE, flex_separation_set` (21), cross-check test 118-128. Repoint import (or delete if `_build_flex_eligibility` is removed).
- tests/test_build_catalyst_screen.py - collector `_build_catalyst_screen`; assertion `sep_row["screen_reason"] == "flex_separation_set"` (172). No flex import; string constant lives in collector/catalyst_screen.py:139. KEEP if catalyst screen stays; DELETE if removed.
- tests/test_catalyst_screen.py (assert reason == "flex_separation_set" at 60; all of file tests collector/catalyst_screen.py) - same fate as above.
- tests/test_catalyst_news.py (collector catalyst news/tone keywords; `_CATALYST_TONE_KEYWORDS`) - same.
- tests/test_catalyst_prompt_sentinels.py (85 lines; asserts doctrine sentences in project-instructions.md) - DELETE together with the prompt's Flex/catalyst section (spec says "sentinel tests").
- tests/test_prompt_hygiene_sentinels.py (371 lines, 34 flex/catalyst hits) - EDIT: remove the flex-related sentinel assertions only if the prompt Flex section is removed; keep the rest.
- tests/test_flex_prompt_schema.py (analyzer `assert_flex_prompt_schema`, `FLEX_SCHEMA_V1`/`flex_nominations`) - DELETE with the analyzer check (CI assertion per spec) else KEEP.
- tests/test_flex_reconciliation.py (collector `_build_flex_reconciliation`, flex_state held vs broker) - tied to collector echo; KEEP/DELETE with it.
- tests/test_flex_review.py (collector `_build_flex_review` - already dead code per CLAUDE.md) - DELETE candidate if collector function removed.
- tests/test_flex_conviction_hysteresis.py, test_flex_conviction_pure.py, test_conviction_damping.py, test_write_flex_conviction_history.py, test_thematic_history_path_isolation.py (flex_conviction path rows in ThematicHistory), test_track_record.py (13 flex/catalyst hits) - collector/analyzer flex-conviction code; no flex package import; KEEP unless the conviction path is removed from Core (then DELETE; thematic_history_path_isolation partly tests the core-thematic M-B fix and must be EDITed not deleted).
- tests/test_sleeve_performance_api.py - web/api `/api/performance` sleeve series (reads `flex-ledger/equity-series.json`) - EDIT/DELETE when portal sleeve panel removed (spec Phase 0).
- tests/test_learning_schema.py:145 (allow-list includes `config/flex-candidates.json`) - EDIT if the file/allow-list entry is removed (learning/bundle.py:47, schema.py:25).
#### 4.3 KEEP (flex only incidental vocabulary)
test_axis_signals.py (FLEXCPIM159SFRBATL, "Flexible CPI" - unrelated), test_transition_watch_confirmation_sources.py (same series), test_cash_floor_guard.py (uses collector `_quarantine_flex_price`, Core price guard; KEEP while function stays), test_price_quarantine.py, test_earnings_market.py, test_earnings_universe.py ("flex candidate" fixture comment 18), test_equity_reconciliation.py, test_executor_order_conflict.py (stale `flex-...` COID fixture; core executor `_cancel_conflicting_orders`), test_functional_coverage.py, test_off_roster_gaps.py, test_pnl_decomposition.py (`off_roster_flex` bucket is a Core bucket), test_price_universe.py (`flex_candidate_tickers` param), test_quadrant_allocation.py, test_quadrant_performance.py, test_reconcile_validate_sequencing.py, test_reference_weights.py, test_series_deltas.py, test_sleeve_auto_switch.py (285: `_build_price_universe(... flex_candidate_tickers=[] ...)`), test_thematic_conviction_pure.py / test_thematic_reference_weights_integration.py / test_build_thematic_conviction.py / test_write_thematic_history.py (flex_route text), test_validation_addendum.py, test_orb_phase0_flat_check.py (ORB; keep).
- Note: if `flex_candidate_tickers` kwarg of `_build_price_universe` etc. is removed from Core, tests 285/35-47 must be edited.

### 5. Blobs, tables, order-id prefixes

#### Blob containers written by src/flex
| Container / blob | Writer (file:line) | Readers |
|---|---|---|
| `flex-ledger/ledger.json` (dict symbol->row) | flex/ledger.py:23-24 (`write_ledger`; called flex/handler.py:93, 255, 483) | flex.handler (62), **daytrade/handler.py:44,580-582** (blob read for exclusivity/sleeve), scripts/orb_phase0_flat_check.py |
| `flex-ledger/closed-trades.json` (list) | flex/trades.py:48 (via `record_closed_trade` 51-63) | flex.handler, flex.killswitch input (handler:180), web/api? (no) |
| `flex-ledger/kill-switch.json` | flex/trades.py:201 | flex.handler:181; **collector/handler.py:3625** |
| `flex-ledger/equity-series.json` (list) | flex/trades.py:210 (`upsert_equity_point` 213-224, called handler:265) | **web/api/function_app.py:764** (`_download_json`) |
| `flex-state/{date}.json` | flex/handler.py:939 (closed-tick carry-forward), :953 (in-hours) | **collector/handler.py:3636** (walkback 8 days), flex.handler:928 |
| `flex-decisions/{date}.jsonl` | flex/handler.py:957 (`append_jsonl_blob`) | none in code (audit) |
| `flex-executions/{date}.json` | flex/handler.py:960 (only when orders issued) | none in code |
| `daily-snapshots/{date}.json` (read only) | - | flex.handler:110 |
| `daily-trades/{date}.json` (read only) | - | flex.handler:113 via `read_trades` |
| `daytrade-ledger/ledger.json` (read only by flex) | - | flex.handler:642 (`_daytrade_ledger_symbols`) |

#### Tables written by src/flex
- `TradeHistory` (PK year-month, RK `FLEX-{date}-{symbol}-{side}-{uuid8}`) via `upsert_entity` - flex/handler.py:804-811 (`_record_trade_history`; fields `layer:"flex"`, `engine:"flex_intraday"`, status submitted/time_stop/scale_out/closed_at_broker/conviction_released + `_enums` primary_trigger/thesis_type/...). Readers: collector outcome stamping (collector reads `layer`), analyzer track_record, learning bundle.
- Core-side flex tables (written by collector/analyzer, not src/flex): FlexConvictionState (collector 7710-7748), `ThematicHistory` rows `path:"flex_conviction"` RK `FLEXCV-…` (analyzer 1621).

#### src/daytrade writes
| Container / blob | Writer | Readers |
|---|---|---|
| `daytrade-nominations/{date}.json` | daytrade/handler.py:77 (`save_daytrade_nominations`, route `/api/daytrade_nominate`) | daytrade/handler.py:125 |
| `daytrade-ledger/ledger.json` | daytrade/ledger.py:23-24 (handler 115, 153) | daytrade handler; flex/handler.py:642; orb_phase0_flat_check |
| `daytrade-state/{date}.json` | handler.py:576-577 | handler:571-573 |
| `daytrade-state/halt.json` | handler.py:515 | handler:128, 511 |
| `daytrade-log/{date}.jsonl` | handler.py:558 (`_log_row`) | handler:502-503 (`_grade_and_halt` reads ALL) |
| `daytrade-grades/latest.json` | handler.py:505 | handler:310 |
No Table Storage writes from daytrade. (Archive per spec: flex-ledger closed trades/equity series, daytrade-log, daytrade-grades.)

#### Order ID prefixes
- `FLEXC-` : flex catalyst engine - defined at flex/handler.py:970 (`_coid`), recognised at :318-319; also hardcoded in scripts/orb_phase0_flat_check.py (prefix attribution, tests/test_orb_phase0_flat_check.py:44).
- legacy `flex-` : pre-split naming (e.g. real `flex-2026-07-07-MU-rep-302e8f`), recognised only (handler:319; test fixtures tests/test_flex_order_hygiene.py, tests/test_executor_order_conflict.py:4). Not generated any more.
- `FLEXD-` : DayTrade Lab - `COID_PREFIX = "FLEXD"` daytrade/ledger.py:15, built `f"{COID_PREFIX}-{today}-{sym}-{kind}-{uuid6}"[:48]` handler.py:665-666, matched in `_fill_price` handler.py:541.
- Daily executor ids are un-prefixed `{date}-{trade_id}` (executor, core) - never touched by the flex sweep.
- Bracket/OTO child legs carry broker-assigned UUID client_order_ids (no prefix) - ownership only provable via ledger `order_ids` (`engine_owned`, reconcile.py:91-95,145; handler.py:347).
- TradeHistory RowKey prefix `FLEX-` (handler.py:806) and ledger `trade_id` `FLEX-{entry_date}-{SYM}-{uuid8}` (flex/ledger.py:60) - not Alpaca order ids.
- ORB will use `ORB-` (to define in src/orb; make deterministic, see 2.4).
