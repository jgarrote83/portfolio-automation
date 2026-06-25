**Flex Trailing Stop — Ratcheting Exit for the Flex Sleeve**

Azure-Native Portfolio Automation System

v1.0 (DRAFT) — June 2026 — One-directional, volatility-scaled trailing stop for flex positions

*STATUS: Draft for review. Not yet implemented. Scopes a change to how flex `stop_loss` levels behave on held positions. No executor change — flex stops remain advisory, daily, EOD-granularity levels (see Architecture). Core-layer stops are unaffected (they stay null by doctrine).*

# 1. Purpose & Scope

Today a flex position's `stop_loss` is set **once at entry** and never moves — the analyzer only *compares* the current price to that fixed number each run. So a winner that runs from $1,250 → $1,500 still carries its original $900 stop, giving back the entire move on a reversal; and the published level is paired with a `take_profit` that often sits absurdly tight (the live MU buy: stop −28% at $900, take-profit +4% at $1,300 — a ~1:7 reward:risk the wrong way).

This spec replaces the static flex stop with a **ratcheting (one-directional) trailing stop** whose width is set by the stock's **own, outlier-robust volatility**: as a position makes new highs, its stop steps up behind it to protect gains, and it **never moves down**. It fixes the asymmetry (lets winners run with a protected floor instead of capping them) without converting the flex sleeve into a tight trend-follower, and without getting whipsawed by a single earnings-gap day.

**Scope:** flex layer only. Core stops stay null (core is governed by quadrant weight + the ~0.1%/1-share floor, never stopped out). The wheel project is unrelated.

# 2. Current Behavior & The Gap

- `project-instructions.md` flex doctrine: *"stop_loss MUST equal the numeric price trigger you publish in that name's kill criteria… On every later run, compare the current snapshot price… if stop_loss is breached, propose the full exit."* — a static compare, no ratchet.
- The analyzer *could* republish a higher stop when it re-affirms a position, but nothing governs it — so it's unreliable and equally free to **lower** the stop (rationalizing a loser). There is no high-water-mark tracking; the analyzer only sees the last 5 reports, so it cannot know the peak since entry on a name held longer than a week.
- `take_profit` on flex is advisory-only and not enforced; published values have been arbitrary and have contradicted the momentum thesis.

# 3. Design Principles

1. **One-directional ratchet.** The published stop is monotonic non-decreasing: `published = max(prior_published, computed)`. It can only rise. This is the whole point — the moment the stop is allowed to fall, protection is lost.
2. **Trail the peak, not spot.** The trail follows the highest *close* since entry, so it does not loosen when the stock pulls back.
3. **Width set by the stock's own volatility, robustly.** The trail distance is a multiple of a **robust** volatility measure — the 95th percentile of the stock's recent daily moves — so it adapts per name (a beta-2.17 semi gets a wide trail, a staple a tight one) and is **immune to the earnings-gap outlier** (see §6). No fixed-percent trail, no per-name tuning.
4. **Volatility governs the price stop; the thesis floor is a deeper max-loss cap.** The entry stop and the trail are both volatility-derived (`peak − 1.5V`, so the entry stop is `entry − 1.5V`). The fundamental kill price is retained *underneath* as a max-loss backstop that binds only if volatility is so large the vol-stop would sit below the thesis-death price (§5.1). A winner is never allowed to round-trip to a loss (emergent break-even, §5).
5. **Computation in the collector, action in the analyzer.** The collector owns the ratchet math deterministically (it has the history + persisted state); the analyzer consumes the level and proposes the exit. The model never invents or drifts the stop.
6. **No new risk surface.** Stops stay advisory daily EOD levels evaluated by the analyzer — never resting broker orders (preserves the stateless-executor design, FOLLOWUPS #6). Intraday/gap risk is unchanged and explicitly out of scope.

# 4. Decisions

| Decision | Resolution | Rationale |
| --- | --- | --- |
| Replace vs. layer | **Layer**: trailing stop sits *above* a retained hard thesis-break floor | Keeps fundamental-break protection while adding give-back protection |
| Volatility measure | **P95 of \|Δclose\| over the last 60 trading days** (dollars) | Robust statistic — the earnings-gap day sits above P95 and barely moves it, so no brittle earnings-date exclusion is needed (see §6) |
| Trail width | **1.5 × V** | Locked. ~1.5 "abnormal-edge" days of give-back below the peak |
| Breakeven | **Emergent** — no separate fixed-% trigger | With a 1.5×V trail, the trailing band crosses entry once `peak ≥ entry + 1.5V`; an explicit fixed-% breakeven step would be both redundant and another arbitrary, vol-blind parameter, so it is removed |
| `take_profit` on flex | **null** going forward | The trail captures upside; a fixed take-profit caps winners and contradicts the thesis |
| Initial/entry stop | **Volatility-derived**: `entry − 1.5V` (emerges from the trail at peak = entry); fundamental kill price retained as a deeper max-loss backstop | Consistent with the trail; the fundamental level binds only if volatility is so large the vol-stop would sit below the thesis-death price (see §5.1) |

# 5. The Ratcheting Stop — Algorithm

For each **held flex** position, each run:

```
thesis_floor   = original kill-criteria stop at entry            (e.g. 900)
V              = P95 of |Δclose| over the last 60 trading days   (robust daily range, $)
trail_distance = TRAIL_MULT × V                                  (TRAIL_MULT = 1.5)
trail_band     = peak_close_since_entry - trail_distance

computed_stop  = max(thesis_floor, trail_band)
published_stop = max(prior_published_stop, computed_stop)         # monotonic — never decreases
breached       = current_close < published_stop                  # → propose full exit
```

- The `max(prior_published_stop, …)` step makes the ratchet one-directional: even if `V` rises (volatility expands) and `trail_band` drops, the published stop holds at its prior level.
- **Entry stop is volatility-derived and emergent:** at entry `peak_close = entry_price`, so `trail_band = entry_price − 1.5V` — the position's initial stop is set by its own volatility, with no separate seeding step. `thesis_floor` sits underneath as a max-loss backstop (§5.1).
- **Breakeven is emergent, not a rule:** `trail_band ≥ entry_price` exactly when `peak_close ≥ entry_price + trail_distance`. So a winner is automatically protected at break-even once it is up one trail-width — no separate parameter.
- `breached` is the exit signal the analyzer acts on (full exit, same mechanism as today).

## 5.1 The initial/entry stop — volatility-derived (resolved)

The entry stop is **volatility-derived**: `entry_price − 1.5V`. This is not a separate rule — it falls straight out of the trail formula, because at entry `peak_close = entry_price`, so `trail_band = entry_price − 1.5V`. The position is protected by its own volatility from day one.

The fundamental kill price (`thesis_floor`, e.g. $900) is **retained as a deeper backstop / max-loss cap** via the `max()` in §5. Its behaviour:

- **Normal case (inert).** The vol stop is tighter than the fundamental-death price — e.g. entry $1,250 − 1.5V ($105) = $1,145, well above $900 — so `max()` picks the vol stop and the floor never binds. You exit on volatility long before reaching the thesis-death price, which is the intent.
- **High-volatility case (binds as a cap).** If V is large enough that `entry − 1.5V` would sit *below* the fundamental-death price (here V > ~$233 → vol stop < $900), `max()` picks the floor — i.e. it refuses to grant vol-room past the level where the thesis is already dead, capping the loss.

So the fundamental level is a **max-loss guard that rarely triggers**, not the primary stop. The genuinely *qualitative* side of thesis death (e.g. "HBM demand collapses") stays the **catalyst kill trigger** evaluated by the analyzer on fundamentals/news — unchanged, and separate from any price level.

## 5.2 Exit criteria beyond the trailing stop

The trailing stop is *absolute* — it never fires on a name drifting upward. The guiding principle for everything else: **let your SPY-beaters run (the trailing stop); cut the ones that aren't earning their idiosyncratic risk.** Three exits cover the gaps the trail leaves.

### A. Catalyst-gated relative exit (the core profit/exit criterion)

The flex sleeve exists to beat SPY, so a name lagging the index is failing its only job — and this is precisely the trailing stop's blind spot (a stock can drift *up* while badly lagging SPY, and the absolute stop never fires). Raw short-window relative return is noisy and beta-confounded, so the trigger is deliberately gated. An **exit candidate** is raised only when **all three** hold:

- **Magnitude** — the position lags SPY by **≥ 5pp** (excess return since entry, or trailing 60d). Matches the existing `regional_rotation` ±5pp laggard threshold, so trivial lags don't count.
- **Persistence** — the lag is sustained over **60 days, or across two consecutive reports.** A 30d single-name relative return is noise (Phase C's own reason the headline horizon is 60d, not 30d).
- **Catalyst passed** — the position's `catalyst_date` is in the past (or the catalyst fired and didn't move the name). Lagging *before* the catalyst is expected and is **not** a trigger; lagging *after* the catalyst should have delivered is genuine dead money. This gate is what turns a noisy calendar signal into "the thesis had its shot and didn't deliver."

When all three hold, the analyzer raises an **exit candidate** — not a blind auto-sell — and confirms: thesis dead → exit, citing *"relative-strength exit — lagged SPY ≥5pp post-catalyst"*; or re-affirm with an explicit *new* thesis. A **30d** lag with the catalyst passed surfaces as an early **WATCH flag**, escalating to the exit candidate at 60d — mirroring the gatekeeper's WATCH→REJECT escalation. Analyst confirmation (plus optional beta-adjustment, v1.1) prevents whipsaw on noisy, beta-driven moves.

This is fully consistent with "let it run": a genuine winner beats SPY by definition and is *never* flagged — only dead money is cut.

### B. Concentration trim (risk, not greed)

If a winner grows past its single-name size band (the ~15% book soft cap, or a tighter flex cap), trim back to target weight — banking gains because the position became too large a *risk*, independent of thesis. Partial sell, governed by the existing weight logic.

### C. Thesis-complete (mispricing only)

When a `thesis_type: "mispricing"` name reaches its published recognition price, the analyzer re-evaluates: harvest (edge gone) or re-affirm with a new thesis and let the trail carry it. Qualitative; no hard numeric target.

### Deferred — extension tightening (v1.1 or dropped)

Tightening the trail (1.5V → ~0.75V) on a parabolic extension was considered and **deferred**: it targets a rarer event (blow-off give-back), is tuning-sensitive, and risks ejecting a strong momentum name mid-trend — mildly conflicting with "let it run." The V-scaled trail already adapts give-back to volatility.

# 6. Robust Volatility Measure (V)

```
V = P95( { |close_t − close_{t-1}|  for the last 60 trading days } )      # dollars
trail_distance = 1.5 × V
```

- **Why P95, not ATR.** ATR is a *mean* of the daily range and is dragged up by a single earnings-gap day. A **percentile is outlier-robust**: an earnings move lands in the top 1–2% of the distribution, *above* P95, so it barely affects V. This gets the "ignore the spike after earnings" behaviour for free — no need to detect earnings dates or exclude a window (which would be brittle and need an accurate per-name calendar).
- **Why P95 specifically.** For a stop *buffer* you want the upper edge of normal — only ~1 day in 20 moves more than P95 — so ordinary busy days don't trip the stop; the median ("most common" move) would be too tight and whipsaw. P95 is the noise ceiling, not the typical day.
- **Why a 60-day window.** A high percentile needs samples; the P95 of 14 points is ~the max and defeats the robustness. 60 trading days is enough for a stable estimate.
- **Use the stock's own price history, not just since-entry.** V is a rolling 60-day window of the stock's closes regardless of when we bought (peak tracking is since-entry; volatility is not). `get_historical_price_light` returns ~5 years of closes, so 60 days is always available even for a name held only a few days.
- **Data-safe by construction.** It uses only **closes** (which we already have in `PortfolioHistory` and from `get_historical_price_light`), sidestepping the OHLC/true-range availability question entirely. If high/low are later confirmed available, the percentile can be taken over *true range* instead of |Δclose| (a v1.1 refinement).
- **Sanity floor only.** Keep a minimal `V_floor` (e.g. a small % of price) purely so a freakishly calm stretch can't produce an absurdly tight stop — a data-sanity guard, not a primary control (it replaces the old fixed 12–30% clamp).

Parameters live in `config/flex-stops.json` (mirrors `flex-candidates.json`) so they can be tuned without a code change:
```json
{ "percentile": 0.95, "vol_lookback_days": 60, "trail_mult": 1.5, "v_floor_pct": 0.03,
  "rel_exit_lag_pp": 5.0, "rel_exit_lookback_days": 60, "rel_watch_days": 30 }
```

# 7. Data Sources (all already collected)

| Input | Source | Notes |
| --- | --- | --- |
| `entry_price` | `paper_account.positions[].avg_entry` (Alpaca) | Recomputed by Alpaca on adds |
| `entry_date` | earliest `TradeHistory` flex buy `recommended_at` for the symbol; fallback earliest `PortfolioHistory` row | |
| `thesis_floor` | the flex buy's original `stop_loss` in `TradeHistory` | Persisted in state on first sight |
| `peak_close_since_entry` | `max(close_price)` over `PortfolioHistory` rows (PK=symbol, RK ≥ entry_date) ∪ today's `prices[sym].c` | |
| `V` (volatility) | P95 of \|Δclose\| over 60 trading days, from `get_historical_price_light(sym)` closes (or `PortfolioHistory`) | Closes-only; no OHLC dependency |
| `current_close` | `prices[sym].c` | |
| `spy_at_entry` / `spy_current` | `get_historical_price_light("SPY")` close on `entry_date`; `prices.SPY.c` | One SPY fetch, shared with Phase C outcome stamping — for the relative exit (§5.2A) |
| `catalyst_date` | the flex buy's `catalyst_date` in `TradeHistory` (Phase C §7 enum) | Gates the relative exit |

No new API source is required.

# 8. New State + Snapshot Block

**Persisted state** — `flex-stops/state.json` blob, maintained by the collector each run (mirrors the `performance/equity-series.json` cache pattern). One entry per held flex ticker; entries are removed when a position's qty returns to 0 (so a re-buy starts fresh).

**Snapshot block** — the collector injects a `flex_stops` block into each daily snapshot for the analyzer to consume:

```jsonc
"flex_stops": {
  "as_of": "2026-06-26",
  "positions": {
    "MU": {
      "entry_date": "2026-06-25",
      "entry_price": 1250.37,
      "thesis_floor": 900.0,
      "peak_close_since_entry": 1355.00,
      "current_close": 1340.00,
      "vol_p95_60d": 70.0,            // V — robust daily range ($)
      "trail_distance": 105.0,        // 1.5 × V
      "trail_band": 1250.0,           // peak − trail_distance (here ≈ breakeven)
      "computed_stop": 1250.0,        // max(thesis_floor, trail_band)
      "published_stop": 1250.0,       // max(prior_published, computed) — monotonic
      "stop_distance_pct": -6.7,      // published_stop vs current_close
      "breached": false,              // current_close < published_stop → propose exit
      "spy_at_entry": 642.10,
      "spy_current": 655.00,
      "excess_vs_spy_since_entry_pp": 3.1,   // position return − SPY return since entry
      "excess_vs_spy_60d_pp": -0.4,
      "catalyst_date": "2026-06-24",
      "catalyst_passed": true,
      "rel_exit_flag": "none"         // none | watch_30d | exit_candidate_60d (§5.2A)
    }
  }
}
```

# 9. Architecture — Where Each Piece Runs

| Piece | Location | Notes |
| --- | --- | --- |
| Ratchet math + V + state | **Collector** (`_build_flex_stops`) | Deterministic; reads PortfolioHistory + TradeHistory + Alpaca entry; persists `flex-stops/state.json`; also computes excess-vs-SPY (since entry / 60d) + the catalyst-gated `rel_exit_flag` (§5.2A); non-fatal (a failure must not block the snapshot, like `_stamp_trade_outcomes`) |
| `flex_stops` block | Collector → snapshot | Compact; analyzer reads it |
| Exit decision | **Analyzer + prompt** | Reads `flex_stops`; proposes a full exit on `breached` (trailing stop) or a *confirmed* `exit_candidate_60d` (relative-strength, §5.2A); on entry seeds the initial stop (§5.1) |
| Order placement | **Executor — unchanged** | Stops stay advisory; no bracket/OCO legs ever sent |

# 10. Prompt Changes (`project-instructions.md`)

1. **Inputs list:** add `flex_stops` (the collector-maintained ratcheting stop per held flex name).
2. **Flex exit discipline:** replace "compare price to the fixed stop_loss" with "read `flex_stops.positions[T].published_stop`; if `breached` is true, propose the full exit this run citing 'trailing stop breached'." The analyzer no longer computes or drifts the stop — it acts on the collector's level.
3. **At entry (new flex buy):** the trade's `stop_loss` = `entry − 1.5V` (the volatility-derived stop, = `trail_band` at entry); the fundamental kill price is recorded as `thesis_floor` in `flex_stops` state as the deeper max-loss backstop. `take_profit` = **null** for flex (the trail owns the upside).
4. **Kill criteria prose** must state the published trailing stop and the thesis floor, so the report and the structured field agree.
5. **Relative exit (§5.2A):** read `flex_stops.positions[T].rel_exit_flag`. On `exit_candidate_60d`, confirm thesis-dead → propose the full exit citing *"relative-strength exit — lagged SPY ≥5pp post-catalyst"*, or re-affirm with an explicit new thesis. `watch_30d` is carried as a WATCH note, not a trade. Also apply the concentration trim (past the size band) and thesis-complete for `mispricing` names (§5.2 B/C).
6. Unchanged: the thesis-break (fundamental) kill trigger and the 60-day thesis-expiry exit all still apply *in addition* — the trailing stop is the tightest price-based exit, the relative exit is the opportunity-cost exit, and neither is the only one.

# 11. Worked Example (MU — V = $70, trail = 1.5×V = $105, floor $900)

Entry $1,250.37; suppose over 60 days MU's daily moves cluster around $20–60 with one $300 earnings day. Mean/ATR is pulled up by the $300 day; **P95 ≈ $70** (the $300 day is above P95, so it doesn't distort V). Trail distance = 1.5 × $70 = **$105**.

| Peak close | trail_band (peak − 105) | published_stop | meaning |
| --- | --- | --- | --- |
| $1,250 (entry) | $1,145 | **$1,145** | entry stop = entry − 1.5V (vol-derived); floor $900 inert |
| $1,300 | $1,195 | **$1,195** | loss capped at −4% vs −28% |
| $1,355 (entry + 105) | $1,250 | **$1,250** | break-even reached (emergent) |
| $1,500 | $1,395 | **$1,395** | locks in +$145/sh |
| pulls back to $1,420, never new high | last peak holds | **$1,395** | stop never drops; exit on next close < $1,395 |

The fundamental floor ($900) never binds here — the vol stop is always tighter. It would only take over if V exceeded ~$233 (1.5V > $350), i.e. a max-loss cap that triggers only in extreme volatility.

# 12. Edge Cases

- **Alpaca unreachable (fallback day):** skip update, retain prior state; surface `available: false`.
- **Position trimmed then re-added:** `entry_price` follows Alpaca's blended `avg_entry`; `entry_date` = first acquisition; peak tracked from `entry_date`.
- **Sold to zero then re-bought:** state entry removed at qty 0; new buy reinitializes (fresh entry/floor/peak).
- **New flex buy today:** initialize `peak = today's close`, seed the initial stop per §5.1.
- **< 60 days of price history:** use whatever closes exist (≥ ~20) and flag the estimate as provisional; `get_historical_price_light` normally supplies far more than 60.
- **Gap through the stop:** detected at next daily close; exit at next open — unchanged EOD/gap limitation, documented, not solved here.

# 13. Sequencing & Shippable Increments

1. **v0 (prompt-only, approximate) — optional quick win.** Doctrine tells the analyzer to ratchet `stop_loss` upward each run using `recent_reports` (never lower), set `take_profit=null`. Cheap, but blind to peaks older than 5 reports and non-deterministic — interim only.
2. **v1 (full, recommended).** Collector `_build_flex_stops` + `flex-stops/state.json` + `flex_stops` snapshot block (trailing stop **and** the catalyst-gated relative exit §5.2A) + the prompt wiring in §10, using V = P95 of |Δclose| over 60d.
3. **v1.1.** Switch V to a percentile of *true range* if `get_historical_price_light` is confirmed to carry high/low (else closes-only stands); beta-adjust the relative exit; reconsider extension tightening.

# 14. Testing

Pure functions, mirroring `tests/test_outcome_stamping.py` / `test_track_record.py`:
- `robust_vol(closes)`: P95 ignores a single large earnings-gap day; stable over 60d; respects `v_floor`.
- `compute_trailing_stop(...)`: monotonic ratchet (never decreases when V widens), thesis-floor never undercut, emergent break-even at `peak = entry + 1.5V`, breach detection.
- High-water-mark: peak holds through a pullback; updates on a new high.
- State lifecycle: init on new buy, removal at qty 0, re-init on re-buy.
- `relative_exit_flag(...)`: fires `exit_candidate_60d` only when lag ≥5pp AND 60d/2-report persistence AND catalyst passed; `watch_30d` at 30d; never fires pre-catalyst; high-beta lag in a down tape doesn't auto-trigger.

# 15. Out of Scope (v1)

- Intraday / gap protection (EOD daily by design).
- Resting broker stop/bracket orders (would make the executor stateful — explicitly rejected, FOLLOWUPS #6).
- Implied volatility (no options-data source exists; V is realized only).
- Core-layer stops (remain null).
- Extension tightening on parabolic moves (considered; deferred to v1.1 — §5.2).
- Scale-out ladders / progressive partial profit-taking (the only partial sell is the concentration trim §5.2B; a ladder clips the right tail and is out for v1).
- Beta-adjusted relative return for the exit (v1.1 — raw excess-vs-SPY for v1, analyst confirmation absorbs the beta confound).
