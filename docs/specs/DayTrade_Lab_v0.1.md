# Flex Day-Trade Lab — v0.1 (paper)

*Committed version of the account holder's "Flex Day-Trade Strategy — v0.1" document,
amended per the 2026-07-07 build session (DECISION-0 measurement-basis resolution, the
stale "#33" backlog reference corrected to #34, and IEX-coverage caveats recorded).
Spec version string: `v0.1` — **any threshold change bumps this string and resets the
pre-registered grading counts by design (§7).**
Implementation: `src/daytrade/`; this document is the source of truth for its rules.*

## 0. What this is

A **third strategy on a third engine**, sharing the paper account and the flex sleeve
budget with the other two — and NOTHING else:

| Engine | Horizon | Loop | Universe |
|---|---|---|---|
| Core (quadrant) | monthly | daily batch (collector→analyzer→executor) | 24 fixed ETFs/names |
| Flex-catalyst | days | 15-min (`flex_intraday`) | LLM-nominated catalyst names |
| **Flex-daytrade (this lab)** | **intraday, flat by 11:15 ET** | **1-min (`daytrade_manage`)** | **manually nominated gap-ups** |

It is a **lab**: the point is to find out, with pre-registered graded rules, whether
this style has positive expectancy in this implementation — not to make money yet.
Long-only, US common stock, one position at a time (plus the §3a sequential second
slot), paper account only. `DAYTRADE_ENABLED` ships `false` (IaC-managed in Bicep).
The expected modal outcome of a session is `no_setup`.

## 1. Separation (three-way; enforced in code and tests)

- Package `src/daytrade/` — NOT inside `src/flex/`. Own config, own ledger blob
  (`daytrade-ledger/ledger.json`), own log, own timers. No imports from `flex.*`
  except shared clients (`shared.clients.*`, `shared.storage`).
- Order namespacing: every lab order carries `client_order_id` prefix **`FLEXD-`**;
  the catalyst engine's orders carry **`FLEXC-`**. Each engine's reconcile manages
  only symbols in its own ledger.
- Symbol exclusivity: the lab discards any candidate present in `CORE_TICKERS` or
  the catalyst ledger; the catalyst engine does not nominate a symbol currently in
  the daytrade ledger.
- Sleeve arbitration: lab notional ≤ `DAYTRADE_NOTIONAL_CAP_PCT` (default 6% of
  equity) AND the flex sleeve cap (`FLEX_SLEEVE_CAP_PCT`, default 25%) holds across
  BOTH engines — each engine subtracts the other's open notional from its sleeve
  headroom.
- The daily macro batch never sees this lab: no snapshot changes, no analyzer echo
  in v0.1. (A read-only `daytrade_performance` block may be added to reporting later.)
- Circuit breakers (§6) are lab-scoped only — they never touch the catalyst engine
  or Core.

## 2. DECISION 0 — measurement basis (resolved 2026-07-07)

Every volume gate below is a **consolidated-tape** concept; the repo's market-data
feed is Alpaca **IEX (~2–3% of tape)**. Grading gates on the wrong basis produces
fiction, so every gate result is logged as `{value, threshold, basis, pass}` and a
future feed upgrade must not silently re-grade history.

| Gate | Basis | Resolution |
|---|---|---|
| RVOL ≥ 3× own 30-day pre-market average | `iex_ratio` | Valid as an IEX/IEX ratio (both sides same feed). Caveat: IEX pre-market prints are SPARSE (verified 2026-07-07: AAPL had 2 pre-market IEX minute bars all morning) — the ratio is honest but low-n; the consolidated gates below are the volume backstop. |
| Pre-market $-volume ≥ $3M | per `consolidated_source` | Consolidated-only concept. **Verified live 2026-07-07:** FMP Starter exposes `/stable/aftermarket-quote` (bid/ask + cumulative `volume`) — 200 OK on the current plan — but the `volume` field's session semantics at 09:20 ET are unverified until observed against a known pre-market print one live morning. Alpaca SIP (~$99/mo) not enabled. **Ships `consolidated_source: "unavailable"`** — the two consolidated gates are marked `basis: unmeasured` and the candidate is **discarded** (the lab does not trade on gates it cannot measure), surfaced loudly in the log. Flip to `"fmp"` after one-morning verification of the volume semantics; `"sip"` if the feed is ever bought. |
| Float rotation ≥ 5% | per `consolidated_source` | Same as above (consolidated volume ÷ float). Float itself from FMP `/stable/shares-float` (verified 200 on Starter). |
| Spread ≤ 0.15% at ~09:20 | `iex_quote` | Alpaca latest-quote endpoint (IEX book) — an approximation of NBBO, recorded as such. Verified 2026-07-07. |
| Breakout-candle vs opening-candle volume (§4 Pattern 1 conf. b) | `iex_ratio` | Same-session IEX/IEX — acceptable. |

FMP endpoints verified on the current Starter plan 2026-07-07: `shares-float` ✅,
`sec-filings-search/symbol` ✅ (form types included), `aftermarket-quote` ✅
(semantics pending), legacy v3/v4 pre/post-market ❌ (403/404).

## 3. Candidate sourcing + validation gates (09:25, engine-computed)

v0.1 sourcing is **manual + optional focused LLM classify** — NOT the daily analyzer.

- HTTP route `daytrade_nominate` (auth=FUNCTION): up to `max_candidates` (5) posted
  before 09:00 ET as `{date, tone, candidates: [{symbol, catalyst_note,
  catalyst_class: "A|B|C|D"|null}]}` → `daytrade-nominations/{date}.json`.
  `tone ∈ risk_on|neutral|risk_off|carry_stress` is **manual until backlog #34**
  (global overnight tone block) automates it; `carry_stress` ⇒ the engine refuses
  the whole day.
- Optional classify hook (`DAYTRADE_LLM_CLASSIFY`, ships `false`): when a
  candidate's `catalyst_class` is null, a small Sonnet call classifies A–D and flags
  dilution risk from `{symbol, FMP headlines <24h, recent S-3/424B5 flag}` — strict
  JSON out. The LLM classifies; it never picks, sizes, or times; the engine enforces
  the class rules regardless (**D never trades; C ⇒ half size, ORB-15 only, never
  VWAP-pullback**).

Validation runs on the first live tick ≥ open−5min (nominally 09:25; the spread
quote is fetched then — "~09:20" in the source spec), per candidate, in order;
first failure discards with `discard_reason` (discard-by-default):

1. Not in `CORE_TICKERS` / catalyst ledger; US common stock; prior close within
   the $5–$100 price band.
2. Gap: pre-market last vs prior close ≥ +4% AND direction up (gap-downs go to the
   avoid-list log, reason `gap_down`).
3. RVOL ≥ 3× (basis `iex_ratio`); consolidated $-volume ≥ $3M AND float within
   20M–100M AND float rotation ≥ 5% (basis per §2). Missing float on a sub-$2B
   name ⇒ **discard** `missing_data` — deliberately the OPPOSITE of the flex WATCH
   rule: a scanner with surplus candidates fails closed; the logged reason keeps a
   data gap from masquerading as a merits finding. (Missing float on a ≥$2B name:
   rotation recorded `basis: unmeasured`, tie-break falls back to RVOL.)
4. Dilution overhang (sub-$2B only): S-3 / 424B5 / ATM filing within 180 days via
   FMP `get_sec_filings` ⇒ discard `dilution_overhang`; endpoint unavailable ⇒
   sub-$2B candidates discarded `filings_unavailable`, loudly.
5. Spread ≤ 0.15% (basis `iex_quote`).
6. Levels computed and stored pre-open: prior day H/L, pre-market H/L, ORB pending.
7. Survivor tie-break by float rotation (RVOL if rotation unmeasured); #2 logged as
   BACKUP.
8. Zero survivors ⇒ `no_setup` logged and the session ends. Expected modal outcome.

### 3a. Second slot (sequential, never concurrent in v0.1)

Only if slot 1 RESOLVED before open+30min (10:00) as **win or scratch** — a loss
ends the day (the circuit breaker outranks). Entry window open+30…open+60
(10:00–10:30), BACKUP name only, re-validated live (gates 2/3/5), logged `slot: 2`.

## 4. Execution loop — timer `daytrade_manage`, every 1 minute, weekdays

Cron `0 * * * * 1-5` with **no market hours encoded** (repo doctrine); first
statements gate on `DAYTRADE_ENABLED`, the Alpaca clock, and the session window
**[open−5min, open+110min]** (09:25–11:20 on a normal day) derived from the
calendar/clock — never hardcoded ET. Outside the window: fast no-op (~115 live
ticks/day; free tier).

- **Reconcile first** (daytrade ledger only, STEP-0 pattern): stop/target filled
  between ticks → record outcome + clear; an orphaned position with no protective
  order is flattened (no naked long past reconcile).
- **Open…open+5min (09:30–09:35):** record the opening range (5-min; 15-min for
  C-class). No entries.
- **Pattern 1 — ORB:** a completed 1-min bar closes above the range high with
  (a) price > session VWAP, (b) breakout-bar volume > opening-candle volume
  (`iex_ratio`), (c) tone ≠ `risk_off` (risk_off ⇒ Pattern 2 only, half size).
  Stop = the nearer of range low / session VWAP; implied stop > 2% of price ⇒ skip.
- **Pattern 2 — VWAP pullback:** only after an opening drive up (≥1% above session
  open while holding above VWAP — structural constant); FIRST VWAP touch only;
  pullback volume lighter than drive volume; entry on reclaim of the prior 1-min
  high; stop below the pullback low. Third touch ⇒ pattern dead for the day.
- **Stale-print halt guard:** no new 1-min bar for >60s on the candidate ⇒ no entry
  that tick (cheap halt proxy — IEX has no halt feed).
- **Sizing:** `risk_usd = DAYTRADE_RISK_PCT(0.5)/100 × (FLEX_SLEEVE_CAP_PCT/100 ×
  equity)`; `shares = floor(risk_usd / (entry − stop))`; capped by
  `DAYTRADE_NOTIONAL_CAP_PCT` and joint sleeve headroom; `binding` reported like
  the flex sizer. C-class ⇒ half `risk_usd`.
- **Orders:** `DAYTRADE_SCALE_MODE="none"` (default) ⇒ native Alpaca **bracket**
  (market entry + 2R limit + stop, full qty). `"half_at_1r"` ⇒ managed pair (entry
  OTO stop; at 1R sell half, stop→breakeven, runner target 2R) reusing the
  cancel/replace idioms.
- **open+105min (11:15) flat:** any open lab position ⇒ cancel legs, market-sell,
  reason `time_flat_1115`.
- One entry per name per day; no re-entries.

## 5. Grading (`daytrade-log/{date}.jsonl` + `daytrade-grades/latest.json`)

Log EVERY slot including `no_setup` days: `{date, slot, symbol|null, outcome,
catalyst_class, pattern, tone, rvol, rvol_basis, float_rotation, gap_pct,
spread_pct, entry, stop, exit, qty, r_multiple_raw, slippage_haircut_pp (0.10/side,
0.20 round trip), r_multiple_net, mfe, mae, hold_min, discard_reasons[], bases{}}`.
MFE/MAE from the session's 1-min bars at close-out.

Pure `build_daytrade_grades(rows)` → expectancy **net of haircut** (overall + per
`catalyst_class × pattern` cell + per slot), win%, avg win/loss, n per cell, and the
**pre-registered rules encoded as constants with their trigger counts**:

- **n=20 graded trades:** expectancy > 0 ⇒ `concurrency_unlock=true` (two
  concurrent at 0.25% risk each, never same GICS sector or same class, tone
  neutral+ — the unlock is a *flag* in v0.1; concurrent execution itself is a
  future version). Expectancy ≤ 0 ⇒ `kill=true` — the engine refuses further
  entries until the spec version string changes (any threshold change resets the
  count by design).
- **n=40:** negative `class × pattern` cells are removed — the engine refuses that
  cell.

The engine ENFORCES these; they are not advisory. Grades are recomputed at each
session end (≥ weekly guaranteed) and written to `daytrade-grades/latest.json` for
the portal; no LLM involvement.

## 6. Circuit breakers (lab-scoped only)

- Daily: one loss ⇒ done for the day (max −1R/day).
- Weekly: cumulative −3R ⇒ `daytrade-state/halt.json` until Monday; the handler
  refuses entries while halted and logs why.

## 7. Config (`src/daytrade/config.py`, env-overridable, bounds-checked)

`DAYTRADE_ENABLED(false, Bicep)`, `DAYTRADE_RISK_PCT=0.5`,
`DAYTRADE_NOTIONAL_CAP_PCT=6.0`, `DAYTRADE_SCALE_MODE="none"`,
`DAYTRADE_LLM_CLASSIFY=false`, gap 4.0, rvol 3.0, pm $vol 3,000,000, float band
20M–100M, rotation 0.05, price band 5–100, spread 0.0015, ORB 5/15 min, entry
cutoff open+60, slot-2 window open+30…open+60, flat open+105, haircut 0.10/side,
breakers −1R day / −3R week, n=20/40 triggers, `consolidated_source`
(`fmp|sip|unavailable`, ships `unavailable`), `spec_version="v0.1"`.

## 8. Storage

| Container | Blob | Content |
|---|---|---|
| `daytrade-nominations` | `{date}.json` | posted candidates + tone |
| `daytrade-ledger` | `ledger.json` | open lab positions (broker-mirrored) |
| `daytrade-state` | `{date}.json` | day state: validation results, slots, day R |
| `daytrade-state` | `halt.json` | weekly circuit-breaker halt |
| `daytrade-log` | `{date}.jsonl` | one row per slot incl. `no_setup` + discards |
| `daytrade-grades` | `latest.json` | grades + enforced unlock/kill/cell flags |

## 9. Honest caveats (read paper results against these)

1. IEX pre-market prints are sparse — RVOL is an honest ratio on thin data.
2. The IEX quote approximates NBBO; spread gating on it is approximate.
3. 1-min exit resolution — a stop and target touched inside the same minute grades
   by the broker's actual fill, not the bar.
4. Paper fills model neither queue position nor real slippage; the 0.10 pp/side
   haircut is a stand-in, applied in grading only.
5. The consolidated gates ship `unavailable` ⇒ **every candidate discards at gate 3
   until `consolidated_source` is verified and flipped** — the lab is deliberately
   inert on volume gates it cannot measure.
