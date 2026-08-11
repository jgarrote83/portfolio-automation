# Flex Catalyst Engine — Intraday, Separated from the Core Quadrant Book

Azure-Native Portfolio Automation System · v1.0 — June 2026

*STATUS: Implemented. Replaces the conviction-sleeve Flex model and **supersedes
`Flex_Trailing_Stop_v1.0.md`** (FOLLOWUPS #10). This sleeve places **live (paper)
broker orders** — a deliberate reversal of the previously stateless-executor design,
scoped to the flex path only.*

## 1. Purpose

Flex is now a **separate strategy on a separate engine**: a days-long single-name
**catalyst** trade, entered on intraday confirmation (VWAP hold + ATR-sized risk),
managed continuously through the session, and exited by a mechanical triple (ATR
stop / scale-out + breakeven / ATR-trail / time-stop). The Core 24-name quadrant
book is unchanged.

## 2. The Separation Contract

The **only** shared input is the **active quadrant** (regime fit). The engine reads
it deterministically from the precomputed `growth_axis`/`inflation_axis` →
`active_quadrant` (`shared/quadrants.py`), not the LLM's narrative call.

| | Core (quadrant) | Flex (catalyst) |
|---|---|---|
| Drives off | slow macro axes → quadrant weights | one name's catalyst + intraday price/volume |
| Universe | fixed 24, weight-only, 0.1% floor | rotatable single names, fully bought/sold |
| Cadence | daily macro batch + event | every 15 min, `is_open`-gated |
| Horizon | months | days (hard time-stop) |
| Exit | weight trim on regime turn | ATR stop + scale-out + trail + time-stop |
| Order path | `trades[]` (advisory) | live OTO entry + resting GTC stop, cancel/replace |

**Forbidden blending:** Flex never uses conviction-scaled concentration, the 0.1%
floor, the cash-sleeve band, or a review-based hold; Core never uses VWAP/ATR/gap/
catalyst/intraday. A flex idea is never a core weight change, and vice-versa.
Enforced in `tests/test_flex_separation.py`.

## 3. The LLM's role vs the engine's

- **LLM (daily):** emits `flex_nominations[]` — candidate, `sector`, dated
  `catalyst_date`, asserted regime fit, and the Phase C enums (`flex_source`,
  `primary_trigger`, `thesis_type` (typically `catalyst`), `trigger_evidence`). No
  price/stop/size. Tagged **`FLEX_SCHEMA_V1`** in `project-instructions.md`; the
  analyzer asserts the prompt carries that contract on load (CI-tested) and refuses
  to run otherwise.
- **Engine (intraday):** computes reconciliation, entry trigger, stop, size, and
  exit actions, and executes. The LLM only **echoes** the engine's `flex_state` in
  the next day's report (collector merges the latest `flex-state` blob into the
  snapshot). It never eyeballs intraday data.

## 4. The loop (`run_flex_intraday`) — mandatory order

Cron `0 */15 * * * 1-5` (timezone-independent by design — gated on Alpaca's clock,
not encoded market hours). Gated by `FLEX_ENABLED`.

0. **Reconcile FIRST** (`reconcile_ledger`) — ledger ⇄ broker before any compute.
   Repairs, `place_missing_stop` first: **no-naked-long** (a held position without a
   resting stop gets one immediately), filled-stop → record exit + clear row,
   partial-fill → resize `qty_current`, phantom order → clear. *Invariant: a flex
   long and its stop are atomic-or-repaired — never an unstopped position past STEP 0.*
1. **Clock gate** — `is_open` false → return (free no-op).
2. **Quadrant + nominations** — deterministic quadrant + `flex_nominations[]`.
3. **Fetch bars** — Alpaca IEX minute (today) + daily (~90d) for held ∪ nominated.
4. **Management** (every tick) — `build_flex_exit_state` → act on `next_action`.
5. **Entry** (morning window only) — `≥ VWAP_WINDOW_MIN` and `< ENTRY_CUTOFF_MIN`
   past the **real session open** (Alpaca calendar). `build_flex_entry` → OTO entry.
6. **Idempotent issuance** — an order issues only if target ≠ current; trailing is
   monotonic + epsilon-gated (`≥ 0.25×ATR`). A quiet day issues zero orders.
7. **Persist** — `flex-state/{date}.json` (latest), `flex-decisions/{date}.jsonl`
   (per-tick audit incl. **suppressed** actions + reason), `flex-executions`, ledger.

## 5. Entry pipeline (`build_flex_entry`, pure)

regime fit → liquidity (`adv_usd ≥ MIN_ADV_USD`) → entry window → gap-vs-ADR (raises
the bar via a stronger-VWAP-hold requirement; never auto-skips) → VWAP hold + slope →
ATR stop (structure-aware: larger distance below entry of `ATR_MULT×ATR` or the
session-VWAP/opening-range-low) → `stop_pct > MAX_STOP_PCT` skip → risk-budget sizing.

## 6. Sizing model (`size_flex_position`) — the reconciled config

The headline mechanic is **constant dollar risk per trade**. Order of constraints:
risk-budget → per-name notional cap → sleeve cap; the smallest wins and the governor
is reported in **`binding`** (so the interaction is visible in logs/tests, not buried).

Shipped pair (`src/flex/config.py`): `RISK_BUDGET_PCT=0.40`, `PER_NAME_CAP_PCT=12.0`,
`MAX_STOP_PCT=4.0`, `SLEEVE_CAP_PCT=25.0`.

| Stop width | Binding | Behavior |
|---|---|---|
| ~3.3%–4% | `risk_budget` | constant **0.40%** dollar risk, ~10–12% notional |
| < ~3.3% | `per_name_cap` | 12% notional (concentration backstop), risk **below** budget (e.g. 2% stop → 0.24%) |
| sleeve near full | `sleeve_cap` | sized to remaining room, or skipped |

Worked sizing (equity $1M, $100 name): 2% stop → 1,200 sh, 12% notional, 0.24% risk,
`per_name_cap`; 3.5% stop → 1,142 sh, 0.40% risk, `risk_budget`; 4% stop → 1,000 sh,
0.40% risk, `risk_budget`. Single-name max 12% (≈2 concurrent in the 25% sleeve).

## 7. Exit triple (`build_flex_exit_state`, pure)

All quantities key off `qty_current` (the remaining shares after any scale-out), never
`qty_initial`. Precedence: **time-stop** (`≥ TIME_STOP_DAYS` trading days) →
**scale-out** (`r_multiple ≥ FIRST_TARGET_R` and not yet scaled → sell
`SCALE_OUT_FRACTION`, move stop to breakeven on the runner) → **trail** (monotonic,
epsilon-gated; rides VWAP support only when it sits below price) → **hold**. Missing
data ⟹ `unknown` (never a forced trade). `stopped` is decided by STEP-0 reconciliation.

## 8. Order construction (Alpaca paper)

Alpaca has **no native partial scale-out**, and `trailing_stop` cannot be a bracket
leg — so the exit cannot be one native order. The engine uses a **managed pair**:
- **Entry:** native **OTO** (entry buy + `stop_loss` child that arms on fill → no
  naked-long window).
- **Amendments:** scale-out = market-sell partial + cancel/replace the stop to
  breakeven resized to `qty_current`; trail = cancel/replace the stop; time-stop =
  cancel stop + market-sell `qty_current`. The resting stop qty always = `qty_current`.

## 9. Config knobs (`FLEX_*` env overrides; defaults in `src/flex/config.py`)

`RISK_BUDGET_PCT=0.40`, `PER_NAME_CAP_PCT=12.0`, `SLEEVE_CAP_PCT=25.0`, `ATR_MULT=3.0`,
`MAX_STOP_PCT=4.0`, `STOP_EPSILON_ATR=0.25`, `TIME_STOP_DAYS=5`, `FIRST_TARGET_R=2.0`,
`SCALE_OUT_FRACTION=0.5`, `VWAP_WINDOW_MIN=30`, `ENTRY_CUTOFF_MIN=90`, `GAP_ADR_MULT=2.0`,
`MIN_ADV_USD=50_000_000`. Engine on/off: `FLEX_ENABLED` (Bicep, ships `false`).

## 10. Measurement caveats (read paper results against these)

1. **~15-min exit resolution, not continuous.** A first-target hit and reversal inside
   a 15-min bucket can be missed; the resting GTC stop is the only continuous
   protection.
2. **IEX-VWAP is a thin-feed proxy** (~2–3% of the consolidated tape), strongest on
   liquid names — hence the `MIN_ADV_USD` floor. SIP feed is the upgrade path for true
   consolidated VWAP.
3. **Overnight-gap risk is uncapped.** A GTC stop becomes a market order on a
   gap-through and fills at the gap, not the stop price, so realized loss can exceed the
   0.40% budget. The risk budget fixes *intended* per-trade risk; gap risk sits on top.
   Management runs in-hours only — overnight the resting stop is the sole protection.

## 11. Storage artifacts (additive)

`flex-ledger/ledger.json` (open-position state), `flex-state/{date}.json` (latest
computed state echoed to the analyzer), `flex-decisions/{date}.jsonl` (per-tick audit
incl. suppressed actions), `flex-executions/{date}.json` (order results). Flex trades
also write `TradeHistory` rows (`layer:"flex"`, `engine:"flex_intraday"`) so Phase C
outcome-stamping + `track_record` keep measuring them.

**Performance persistence (session 2026-08-10, Flex Sleeve Performance Ledger —
`src/flex/trades.py`).** Two more blobs, same `flex-ledger` container:

- **`closed-trades.json`** — append-only, one row per REALIZED trade (not per fill —
  a 2R scale-out plus its eventual stop/time-stop close are ONE trade with a `fills[]`
  array). Written through a single funnel, `flex/handler.py::_finalize_closed_trade`,
  that every close path (`time_stop`, the reconcile-driven broker-stop-fill path, and
  the entry-that-never-filled no-op) routes through — `scale_out` itself only APPENDS
  a fill to the still-open ledger row, it doesn't close the trade. Fills are
  reconciled against `AlpacaClient.get_activities("FILL", ...)` — broker truth, never
  the engine's own intent — so `pnl_usd`/`r_multiple` are `null` (with a
  `pnl_unavailable_reason`) rather than fabricated whenever a fill price can't be
  confirmed. Idempotent on `trade_id` (generated once at open in `flex/ledger.py::
  new_entry`, unlike `order_ids` which a stop replace overwrites). Each row also
  carries the `catalyst_score`/`score_components` the name had at entry (from the
  catalyst-sleeve-funnel's `catalyst_screen.ledger`) and `nomination_thesis` — the
  raw material for future weight-tuning (FOLLOWUPS #58) and sleeve-appropriate Phase C
  grading (FOLLOWUPS #61, not yet built — the existing `_OUTCOME_HORIZONS`
  30/60/90-day core-book grading is the wrong shape for a 5-day-time-stop sleeve).
- **`equity-series.json`** — one row per trading day (`sleeve_notional_usd`,
  `unrealized_usd`, `cumulative_realized_usd`, `total_equity`, `open_positions`,
  `closed_trades_to_date`), upserted by date on every in-hours tick (converges to
  the last successful tick of the day without needing to detect which tick is last).
  Consumed by `GET /api/performance` (`sleeve_contribution_pp`/`sleeve_trade_count`,
  cumulative from the window start, never a start=100 index — the sleeve is
  intermittently deployed, unlike the always-invested core book) and rendered as a
  separate panel on `web/performance.html` below N=30 closed trades shown dashed/
  greyed, no skill statistic ever computed on it.

## 12. Tests

Pure-module suites: `test_flex_indicators`, `test_flex_sizing`, `test_flex_entry`,
`test_flex_exit`, `test_flex_reconcile`, `test_flex_separation`, `test_flex_prompt_schema`.
The handler's orchestration loop (`run_flex_intraday` itself) is still validated by
the dry-run path (`POST /api/flex {"dry_run":true}`) and live-paper verification, not
by unit tests — but as of session 2026-08-10, its individually-callable helper
functions ARE unit tested with a stub Alpaca client: `test_flex_trades` (the pure
`flex/trades.py` builders — `build_closed_trade`, `merge_broker_fills`,
`fills_from_activities`, `build_sleeve_mark` — plus idempotency on `record_
closed_trade`) and `test_flex_close_paths` (every close path — `time_stop`,
`scale_out`, the broker-stop-fill reconcile path, and the entry-never-filled
no-record case — called directly against `flex/handler.py`'s `_act_on_exit`/
`_open_position`/`_finalize_closed_trade`/`_catalyst_score_lookup`, storage
monkeypatched to an in-memory blob store). This caught a real bug pre-merge: an
unpriced just-submitted fill was shadowing a broker-confirmed price in `merge_
broker_fills` until the PnL assertion in `test_time_stop_pnl_present_when_broker_
confirms_both_fills` failed against it.
