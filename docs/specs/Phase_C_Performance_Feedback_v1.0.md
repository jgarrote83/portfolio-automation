**Phase C — Performance Feedback Loop**

Azure-Native Portfolio Automation System

v1.0 — June 2026 — Self-measurement vs SPY + decision-outcome learning

# 1. Purpose & Scope

Phase C gives the analyzer a feedback loop: the ability to **measure progress
against SPY** and to **learn which of its own recommendations work**. Today the
analyzer recommends blind — it never sees whether prior calls succeeded. The
mission ("beat SPY total return over a rolling 12-month window") is the scoreboard;
the decision-outcome data is how the analyzer improves.

Two distinct feedback signals, served by different data:

- **Scoreboard** — is the account beating SPY? (answers the mission)
- **Learning signal** — which calls worked, and what kind of reasoning produces
  them? (drives improvement — the heart of the phase)

**Scope: flex-first.** Flex is where stock-picking skill lives, so the reasoning
taxonomy and hit-rate measurement earn their keep there. Core trades are
quadrant-weight tweaks and get a lighter treatment in a later iteration.

# 2. Design Principles

1. **Measure the call, not the position P&L.** Continuous rebalancing means there
   is no clean per-trade round-trip to attribute. Instead score each
   *recommendation* against SPY over the horizon: a **buy/add** is "correct" if the
   symbol beat SPY; a **sell/trim** is "correct" if it lagged SPY. This is
   computable from EOD prices and directly answers "is my analysis adding alpha
   vs just holding SPY?"
2. **Confidence calibration is the centerpiece.** Saving confidence alone teaches
   nothing; the value is confidence × outcome — "when I said 0.8, was I right ~80%
   of the time?"
3. **Aggregate patterns, never per-name vetoes.** Short-horizon single-name returns
   are noise. Feedback targets systematic patterns (hit-rate by layer / trigger /
   confidence) with explicit sample sizes. A calibration nudge, not a blacklist.
4. **Surface cash drag.** Report current cash % so a lag is attributed correctly
   ("behind because heavily in cash," not "bad picks").
5. **Capture reasoning structured, not just prose.** Free-text rationale cannot be
   aggregated. Small enum tags turn "why" into something measurable.
6. **Write-once, no hindsight.** Original reasoning and confidence are stamped at
   recommendation time and never edited; outcomes land in separate fields. The
   contrast between stated intent and realized result is the entire learning signal.
7. **Capture fine, report coarse.** Store the specific reasoning value; the
   aggregator rolls up into a few coarse buckets while samples are thin, promoting a
   finer bucket to its own line only once it crosses n ≥ 10.
8. **Measurement is computation, not retrieval.** The metrics are exact aggregates
   over complete records — computed in-pipeline, not via RAG. Azure AI Search is the
   later *memory-recall* layer (Phase 1.5), complementary, and made outcome-aware by
   this phase's stamps.

# 3. Decisions Locked

| Decision | Resolution |
| --- | --- |
| Benchmark fairness | **Fully-invested SPY**, with **`cash_pct` surfaced** alongside so cash drag is visible and attributable |
| Official hit-rate horizon | **60 days** is the headline; 30d and 90d shown for context (30d noisy, 90d slow to mature) |
| `primary_trigger` buckets | Capture fine; report coarse → **catalyst / valuation / thematic** |
| `thesis_type` buckets | **catalyst / mispricing / macro_fit** (gatekeeper gates G4 / G5 / G1) |

# 4. Data Block 1 — `performance` (scoreboard)

Computed in the collector from existing snapshot history (every daily snapshot
already carries `paper_account.equity` and `prices.SPY.c`; with no external cash
flows, normalized equity %-change is the true time-weighted return vs SPY — same
basis as the web `performance` endpoint). Injected into each day's snapshot.

```jsonc
"performance": {
  "inception_date": "2026-05-26",
  "days_live": 20,
  "account": { "equity": 99261.62, "cash_pct": 44.7 },
  "return_since_inception_pct": -0.7,
  "spy_return_since_inception_pct": 1.9,
  "excess_vs_spy_pp": -2.6,
  "rolling": {
    "30d": { "account_pct": 0.0, "spy_pct": 0.0, "excess_pp": 0.0 },
    "60d": { "account_pct": 0.0, "spy_pct": 0.0, "excess_pp": 0.0 },
    "90d": { "account_pct": 0.0, "spy_pct": 0.0, "excess_pp": 0.0 }
  },
  "max_drawdown_pct": -3.1,
  "note": "12-month rolling not yet available (only 20 days live)"
}
```

Benchmark is fully-invested SPY; `cash_pct` is mandatory context.

# 5. Data Block 2 — TradeHistory outcome stamp

A collector step finds TradeHistory rows whose 30/60/90-day marks have arrived and
stamps the outcome. Cheap: `get_historical_price_light(symbol)` returns the full
~5-year dated EOD series in one call, so one call per maturing symbol + one for SPY
yields every close needed. Well within the 250 req/day FMP budget.

Fields added (separate from the write-once recommendation fields):

```jsonc
"price_at_rec": 198.50,
"spy_at_rec": 642.10,
"ret_30d_pct": 0.0, "spy_ret_30d_pct": 0.0, "excess_30d_pp": 0.0,
"ret_60d_pct": 0.0, "spy_ret_60d_pct": 0.0, "excess_60d_pp": 0.0,
"ret_90d_pct": 0.0, "spy_ret_90d_pct": 0.0, "excess_90d_pp": 0.0,
"call_correct_60d": true,   // headline: buy→beat SPY; sell/trim→lagged SPY
"call_correct_30d": null, "call_correct_90d": null,
"outcome_status": "open|30d|60d|90d|closed"
```

`call_correct_60d` is the headline learning metric.

# 6. Data Block 3 — `track_record` (what the analyzer reads)

Compact aggregates ONLY — never raw trade logs in the prompt (token discipline; the
analyzer reads this rollup, not the table). Computed from the stamped rows.

```jsonc
"track_record": {
  "sample_size": 47,
  "headline_horizon": "60d",
  "by_layer":  { "core": {"n":31,"hit_rate":0.55}, "flex": {"n":16,"hit_rate":0.44} },
  "by_trigger": { "catalyst": {"n":12,"hit_rate":0.58}, "valuation": {"n":3,"hit_rate":0.33}, "thematic": {"n":1,"hit_rate":1.0} },
  "by_thesis":  { "catalyst": {"n":9,"hit_rate":0.55}, "mispricing": {"n":5,"hit_rate":0.40}, "macro_fit": {"n":2,"hit_rate":0.50} },
  "calibration": [
    { "bucket": "0.5-0.6", "n":12, "predicted":0.55, "actual":0.42 },
    { "bucket": "0.7-0.8", "n":8,  "predicted":0.75, "actual":0.50 }
  ],
  "over_trading": { "avg_trades_per_day": 3.2 },
  "caveat": "n=47 is anecdotal; treat as calibration signal, not per-name veto"
}
```

`calibration` and `by_trigger`/`by_thesis` are the core learning surface. The
`sample_size`/`caveat` fields enforce Principle 3.

# 7. Reasoning-Capture Fields (write-once, at recommendation time)

Already stored today by the analyzer ([handler.py](../../src/analyzer/handler.py)
TradeHistory write): `confidence`, `rationale`, `flex_source`, `quadrant_current`,
`risk_score`. Phase C adds the structured tags below to each **flex** trade in the
trades JSON, so they flow through the existing analyzer→TradeHistory write. These
are stamped once and never edited.

| Field | Type | Purpose |
| --- | --- | --- |
| `primary_trigger` | enum (fine, see §8) | What caught attention — measures "are news-triggered picks actually good?" |
| `thesis_type` | enum: catalyst / mispricing / macro_fit | Why it cleared the gatekeeper (gate G4 / G5 / G1) — reveals skill by reasoning type |
| `trigger_evidence` | short text | The specific headline + source + date (or data point) that triggered it — provenance for recall and verification |
| `catalyst_date` | date / null | If event-driven, when the catalyst was expected — lets the loop check timing and whether the catalyst materialized |

# 8. Enum Granularity — capture fine, report coarse

Flex picks are rare (≤10 slots; ~20-40 in the first 6 months), so buckets must be
sample-aware. At n=10 a 60% hit rate carries a ~±30pp 95% CI — hence the discipline.

- **Capture (fine)** — `primary_trigger` stores the specific value:
  `news_catalyst` / `earnings` / `congressional_cluster` / `thematic_tier` /
  `valuation` / `technical`.
- **Report (coarse)** — the `track_record` aggregator rolls these into
  **catalyst / valuation / thematic** while samples are thin.
- **Promotion rule** — a fine bucket gets its own reported line only once it
  reaches **n ≥ 10**; until then it rolls up into its coarse parent.
- `thesis_type` stays coarse from the start (3 values) — these map to distinct
  gatekeeper gates and are genuinely different skills worth measuring separately.

Coarse buckets also classify more *consistently* (the model assigns the enum at
temp 0.2; fuzzy/overlapping buckets get tagged inconsistently and poison the
aggregates).

# 9. Prerequisite — TradeHistory key-casing fix

The analyzer writes TradeHistory rows with lowercase keys (`side`, `symbol`,
`quantity`); the executor upserts the **same** PK/RK row with capitalized keys
(`Side`, `Symbol`, `Quantity`) — Azure Tables are case-sensitive on property names,
so a row ends up with duplicate mixed-case columns. This must be standardized (pick
one casing, or normalize on read) before the outcome computation reads these rows,
or hit-rate math reads half-populated fields. Do this first.

# 10. Architecture — where each piece runs

| Piece | Location | Notes |
| --- | --- | --- |
| `performance` block | Collector | Derive from existing snapshots; reuse web `performance` endpoint math |
| Outcome stamping | Collector step | Batch reconciliation over TradeHistory; 1 FMP historical call per maturing symbol + SPY |
| `track_record` block | Collector (aggregate) → snapshot | Compact rollup the analyzer reads |
| Feedback consumption | Analyzer + prompt | New prompt section: review track_record before recommending — framed as calibration, not name blacklist |

# 11. Sequencing & Dependencies

`#8 (fetch prices for non-held names) → 5 (outcome stamping) → 4 (scoreboard) →
6/12 (track_record + prompt loop)`.

FOLLOWUPS **#8** is the first domino: it adds price fetches for names not currently
held, which both unblocks new flex nominations (gatekeeper gate G2) **and** is the
same plumbing outcome stamping needs (a name sold 60 days ago is no longer in the
held-ticker price fetch). Do #8 and §5 together. §4 (scoreboard) is independently
shippable and gives an early web-visible win.

# 12. Out of Scope (v1)

- Per-day raw trade logs in the snapshot (token bloat — aggregates only).
- SPY total-return / dividend adjustment (price return for v1; note the ~1.3%/yr
  dividend drag as a known small bias; add later).
- Intraday / MAE-MFE data (no intraday feed exists).
- Core-layer reasoning taxonomy (flex-first; core gets `quadrant_fit` + confidence
  in a later iteration).
- Azure AI Search memory recall (Phase 1.5; this phase's outcome stamps make those
  future memories outcome-aware).
