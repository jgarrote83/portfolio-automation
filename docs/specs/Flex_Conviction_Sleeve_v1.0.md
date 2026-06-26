# Flex Conviction Sleeve — v1.0

**Status:** implemented (collector `_build_flex_review` + `_classify_flex_review`,
analyzer entry-metadata persistence, prompt "Step 2 — Gatekeeper" + "Flex review").
Supersedes the alpha-sleeve gatekeeper (catalyst gate G4 / mispricing gate G5).

## Why we changed it

The prior flex gatekeeper was an **alpha-sleeve** design: enter on a dated catalyst
(G4) and a stated mispricing (G5), judged vs SPY (G6). Two failure modes:

1. **Fails closed on absent data, narrated as a finding.** NEE was REJECTED at G4 for
   "no earnings within 14 days" — but the collector only fetches a ±2-week earnings
   window, so NEE's next-quarter print simply was not in the snapshot. A data-window
   artifact became a hard rejection.
2. **Single benchmark.** Beating SPY is the mission, but it does not tell you whether
   a name is a *good expression of the regime call* (vs the quadrant sleeve ETF).

v1.0 switches to a **conviction sleeve**: enter high-quality, regime-fit names you
would hold through a drawdown; hold them to a **dual-benchmark performance review**;
cut and replace the ones that do not earn their slot. No catalyst clock, no hard
price stop.

## Entry gate (conviction quality — all hard, evaluated in order)

- **G1 Regime fit.** The name's sector/factor must want the **active quadrant**
  (consumed from `growth_axis`/`inflation_axis` → quadrant; never re-derived).
- **G2 Quality.** Profitability + balance-sheet survivability + durable franchise.
  Because there is no hard stop, it must be holdable through a drawdown.
- **G3 Opportunity cost vs the active-quadrant ETF.** *"Why this name instead of more
  of the sleeve ETF?"* Reject unless expected risk-adjusted excess over that ETF is
  clearly positive.

**Deleted:** the catalyst gate (G4) and mispricing gate (G5) — a conviction sleeve
needs neither. This removes the NEE-class false rejection at the root.

**Missing data → WATCH, never REJECT.** REJECT is only for a name evaluated on the
merits and found wanting (bad regime fit / weak quality / no edge vs the ETF). If a
gate's *input is absent from the snapshot*, cap at WATCH and name the missing field.

## Exit / review (dual benchmark, regime-asymmetric)

Computed deterministically in the collector (`flex_review` snapshot block); the LLM
echoes `review_status` and writes only the `review_due` narrative.

A held flex name fully earns its slot only if it beats **both** SPY and its
active-quadrant ETF. Which one **binds** flips with the tape:

- `spy_direction ∈ {rising, flat}` → **SPY binds** (mission: beat a rising SPY).
- `spy_direction == falling` → **the quadrant ETF binds** (SPY is a low bar in a
  drawdown; the honest test is value added over the sleeve).

`spy_direction` = sign of `spy_return_since_entry_pct` with a `DEADBAND_PP` band.
AHEAD := `excess ≥ LAG_TOL_PP`; BEHIND otherwise.

### `review_status` matrix (≥ `REVIEW_DAYS`; `< REVIEW_DAYS` → `ok`)

| vs ETF | vs SPY | binding (spy_dir) | status | action |
|---|---|---|---|---|
| AHEAD | AHEAD | any | `ok` | re-affirm |
| BEHIND binding, beyond `BREAK_PP` | — | — | `breaking` | sell, cite benchmark |
| BEHIND binding, within `BREAK_PP` | — | — | `review_due` | thesis-broken→replace; noise→one `EXTENSION_DAYS` extension |
| AHEAD binding, BEHIND non-binding | — | SPY binds (rising/flat) | `ok_flagged` | mission met, selection weak — bump candidate, no active cut |
| AHEAD binding, BEHIND non-binding | — | ETF binds (falling) | `ok` | drawdown: beating the sleeve is the win |
| — | — | regime fit lost (entry quadrant ≠ active) | `breaking` | sell regardless of performance/window |
| missing entry/benchmark/price data | — | — | `unknown` | hold; state what is missing |

**Replacement rule (default = return to the sleeve).** Cut dollars go to the
active-quadrant ETF by default. A replacement single name is allowed only if it clears
G1–G3 at **strictly higher `confidence`** than the name it replaces. `ok_flagged` and
`review_due` names are the pool a higher-conviction nominee bumps first.

## Data contract

**Entry metadata** (write-once on a flex BUY, computed by the analyzer, not the LLM;
persisted in `TradeHistory`): `entry_date`, `entry_price` (snapshot price),
`entry_quadrant` (active quadrant at entry), `flex_benchmark_etf`.

**Quadrant → benchmark ETF** (`src/shared/quadrants.py`): Q1→QQQ, Q2→XLI, Q3→GLD,
Q4→TLT (all held core names).

**`flex_review` snapshot block** — per held flex name: `days_held`,
`return_since_entry_pct`, `benchmark_etf`, `benchmark_return_since_entry_pct`,
`spy_return_since_entry_pct`, `excess_vs_etf_pp`, `excess_vs_spy_pp`, `spy_direction`,
`binding_benchmark`, `regime_fit_lost`, `review_status`, `reason`.

## Config (`src/config/flex-review.json`)

| knob | default | meaning |
|---|---|---|
| `REVIEW_DAYS` | 60 | first performance review of a held flex name |
| `LAG_TOL_PP` | −2.0 | within this of a benchmark = AHEAD (absorbs noise) |
| `BREAK_PP` | −5.0 | lagging the binding benchmark beyond this → `breaking` |
| `EXTENSION_DAYS` | 30 | one-time extension for a `review_due` name judged noise |
| `DEADBAND_PP` | 1.0 | |SPY move| within this over the window → `flat` (SPY-binding) |

## Guardrails preserved

Flex aggregate ≤ 25% of equity, ≤ 10 names; single-name 3–4% entry, ~15% soft cap;
sell-before-buy; integer shares; no options/margin/short; human approves every trade.
A price kill-trigger / EOD stop remains *available* but is advisory — the primary exit
is this performance review, not a stop.

## Deviations from the proposal (documented)

- **`regime_fit_lost` proxy.** "The name's sector left the active quadrant" is
  implemented deterministically as `entry_quadrant != active_quadrant` (both known).
  A single stored entry quadrant, not a multi-quadrant compatibility set — v1
  simplicity. The human approves the resulting sell.
- **Regime-fit-lost overrides the holding window** (`breaking` even `< REVIEW_DAYS`):
  a void entry thesis is not a performance question.
- **`unknown` (not a crash) on missing data**, with an `entry_price` fallback to the
  stamped `price_at_rec` when the write-once `entry_price` is absent (older rows).
- **§7 reasoning-capture enums retained** (`thesis_type` etc.) for the `track_record`
  loop; `macro_fit` is the default thesis type for conviction entries. Not rewired to
  avoid an unrelated `track_record` schema change.

## Tests

`tests/test_flex_review.py` — full classifier matrix, the integration builder
(beats-ETF-lags-SPY-bull → `review_due`, missing-data → `unknown`, regime-fit-lost →
`breaking`, core-not-reviewed), entry-metadata round-trip, and prompt-contract
regressions (missing-data → WATCH; catalyst/mispricing gates removed).
