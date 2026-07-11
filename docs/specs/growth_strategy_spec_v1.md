# Growth Portfolio — Strategy Specification (v1)

> **⚠️ SUPERSEDED IN PART (2026-07) by [`roster_revision_2026-07.md`](roster_revision_2026-07.md).**
> The fixed 24-ticker core roster (§2–§3) and the AMZN/GOOGL exempt-hold doctrine (§8)
> are superseded: the core is now ROLE-BASED (candidate pools + a human-gated `selected`
> incumbent), AMZN/GOOGL are retired to legacy exits (QQQ retains the exposure), and the
> international sleeve is rotation/DXY-governed, not quadrant-governed. The rest of this
> spec (the regime machine, the deployment gate, conviction-scaled concentration, the
> reference/override protocol) stands. This document keeps its original text for history.

*North-star for this project. This document is the source of truth for the strategy;
the GitHub automation (collector precomputes + system prompt + gate) is downstream of it
and should be refined to implement it. Written from the conclusions reached over the
regime-engine debugging and the four model-revision rounds (Flex, regime headwind,
international, dynamic rotation).*

---

## 0. What this strategy actually is — the validated thesis

This is **not** a static All-Weather book that happens to tilt. It is a **regime-
concentration machine**: an All-Weather four-quadrant core that dynamically concentrates
into the *active* quadrant (trimming inactive quadrants toward a 0.1% floor) and supplements
with a small single-name Flex sleeve.

Three conclusions define everything below:

1. **The edge and the downside both live in the regime call.** Once aggressive rotation is
   authorized (a sleeve can go from full weight to 0.1%), there is no longer a passive
   diversification cushion quietly protecting the book. A correct, timely quadrant call
   wins in *any* regime; a wrong or late call loses — and in a drawdown a wrong call makes
   the book lose *to SPY* because it concentrated into the quadrant that is falling.
   **Investing in regime-call accuracy and timeliness is therefore not optional polish — it
   is the entire strategy.**

2. **The 0.1% floor is the primary risk control, not a rounding convenience.** Because no
   sleeve goes fully to zero, a wrong call always leaves a residual hedge that caps the
   damage. The floor is the one thing that makes aggressive rotation survivable. It is
   load-bearing and must never be removed to "clean up" the book.

3. **This is a regime/timing bet, not a structural premium.** Be honest about it. The book
   does not beat SPY by structural design; it beats SPY *in the regimes it calls correctly*
   and lags in the regimes it misses or in uninterrupted US-growth bulls if it fails to
   concentrate in time.

---

## 1. Mission & benchmark — the decision to make explicit

**Primary benchmark:** SPY total return, rolling 12-month window (unchanged from current
mission).

**The honest caveat that must be encoded:** benchmarking against 100% SPY is winnable *with
full rotation* but is a regime bet, not a structural edge. To avoid fooling ourselves, score
on **two axes every period**, not one:

- **Absolute:** Book total return − SPY total return (the stated mission).
- **Risk-adjusted:** Book Sharpe vs SPY Sharpe, and Book max-drawdown as a % of SPY's.

**Success / kill thresholds (pre-registered, evaluated over a full cycle including ≥1
drawdown):**
- *Keep* if the book beats SPY on Sharpe **and** holds max drawdown below ~70% of SPY's.
- *Keep the absolute-beat mission* only if the book also beats SPY's total return over
  rolling 12-month windows net of costs/taxes once a full cycle is observed.
- *Demote to risk-adjusted mission* if, after a full cycle, it cannot beat SPY's absolute
  return — i.e., accept "match SPY with smaller drawdowns" as the win.

---

## 2. The core is two opposite blocks + a switch

The 24-name core is not monolithic. Model it as two economically opposite blocks plus a
currency switch (this is the refinement from the international discussion):

| Block | Members | Role | Wins when |
|---|---|---|---|
| **Amplifier (equity beta)** | US growth (QQQ, XSD, AMZN, GOOGL, SPY anchor) + International (IDMO, AIA, EWJ, IEMG, EWZ, VSS, EUAD) | Return engine — *can beat SPY* | Risk-on regimes; international leg wins specifically on a **falling dollar** |
| **Damper (ballast)** | GLD, TLT, TIP, DBA, PDBC, SGOV, XLP, MCK | Capital preservation | Drawdowns / stagflation / deflation |
| **Switch** | DXY (`DTWEXBGS`) direction | Governs the *internal* tilt of the amplifier | Falling DXY → international; rising/stable DXY → US growth |

The 2025 data point (intl +29–34% vs SPY +16–18%, ~half from a −9.4% dollar) is the proof
case: when the dollar falls, the amplifier's international leg can be the single best thing in
the book. **The dollar is a cleaner, more falsifiable regime hinge than the quadrant alone,
and we already collect it.**

---

## 3. Quadrant → allocation rotation rules (the dynamic weighting)

In each regime, concentrate into the active block and trim the rest toward the floor.

| Quadrant (growth/inflation) | Concentrate into | Trim toward 0.1% floor |
|---|---|---|
| **Q1 Goldilocks** (↑/↓) | Amplifier: US growth + (intl if DXY falling) | GLD, TLT, TIP, DBA, PDBC, XLP |
| **Q2 Reflation** (↑/↑) | Energy, materials, industrials, EM, commodities, TIPS | TLT, long-duration, deep defensives |
| **Q3 Stagflation** (↓/↑) | GLD, energy/commodities, TIPS, defensives (XLP, MCK) | US growth, long bonds |
| **Q4 Deflation** (↓/↓) | TLT, SGOV/cash, defensive equity | Commodities, EM, cyclicals, growth |

**Concentration ceiling (the living-hedge rule):** the active quadrant may take a large
share but is **capped at ~90% of the core block** (confirmed 2026-06-30; was a ~80%
suggestion) so the remaining ~10% stays spread across the other quadrants at/near the floor.
Flex and cash are separate sleeves outside the core block. This is the deliberate cost of
insurance against a wrong call —
do not let conviction drive the active quadrant to ~100%. Size *within* the ceiling by
conviction (risk score): high conviction (low risk score) → toward the ceiling; mixed → keep
the spread wider.

---

## 4. The dollar overlay (US-growth vs international tilt)

Within the amplifier block, the US-growth vs international split is governed by the dollar:

- **DXY falling (20-day or 60-day trend down):** overweight international (IDMO, AIA, EWJ,
  IEMG, VSS, EUAD); the currency tailwind plus the valuation gap (intl ~15× vs S&P ~23–29×
  forward) is the edge.
- **DXY rising / stable:** overweight US growth (QQQ, mega-cap); US earnings leadership
  reasserts.
- Treat the dollar as a **first-class signal**, echoed deterministically — not a discretionary
  afterthought. Caveat to encode: the international trade is currently *consensus* after a +30%
  year and ~half of 2025's gain was currency; require the DXY trend to confirm, don't chase.

---

## 5. Regime classification = load-bearing (must be deterministic)

Because rotation now bets the book on the call, the classification engine controls ~80% of
equity, not just a deploy/hold gate. It must be deterministic precompute, **echoed not
re-derived** by the LLM (this is the fix already implemented; it is now strategically
critical, not hygiene):

- **Growth axis** = slope of GDPNow's *within-quarter vintage trajectory* (never cross-quarter
  actuals, never equity price action or a single earnings beat). Indeterminate if <3 vintages.
- **Inflation axis** = realized **headline + core** CPI/PCE direction first; breakevens
  secondary; an active energy/Hormuz shock forces "rising."
- **Policy** = FOMC stance/dot-plot; **fail-closed** (gate CLOSED, Q1 not assertable) if
  absent.
- **Dollar** = DXY trend (new first-class signal per §4).
- **Gate** = deterministic; CLOSED whenever growth not rising, inflation rising, policy
  hawkish/unconfirmed, or energy shock active — regardless of stated quadrant.

---

## 6. Re-entry discipline — the weakest, highest-leverage skill

The literature and our analysis agree: getting *defensive* is the easy half; getting
*offensive again near the bottom* is where regime strategies fail, because recoveries are
violent and front-loaded and an over-defensive book gives back the entire relative gain. The
current design only has a defensive gate — **it needs explicit re-entry rules.** Encode at
least:

- **Partial, staged re-entry** rather than waiting for an all-clear: e.g., restore the
  amplifier in tranches as confirming signals arrive, not in one step.
- **Concrete re-entry triggers** (deterministic where possible): GDPNow vintage slope turning
  up, the inflation axis turning down, credit spreads (HY OAS) tightening off a wide, price
  reclaiming a long-trend filter (e.g., index back above its 200-day), and DXY confirming the
  equity tilt.
- **Asymmetry rule:** the gate should be quicker to *reduce* risk than to *re-add* it, but not
  so slow to re-add that it structurally misses recoveries — tune this with the paper record.

This section is currently the biggest gap between the strategy and the automation; prioritize
it in the GitHub refinement.

---

## 7. Flex sleeve — satellite, not engine

Conclusion from the barbell math: Flex doesn't fix the drag, it relocates it onto a high alpha
bar, and in a drawdown a high-beta growth Flex sleeve *amplifies the loss*. Therefore:

- **Small and risk-budgeted** (≤ ~15% until it demonstrably clears its hurdle in paper
  trading; hard cap ≤25%). Shrink toward cash in `shock_level 3`.
- **Opportunity-cost test, enforced numerically:** every Flex name must clear an explicit
  "why is this better than the same dollars in the *active-quadrant* beta / more SPY?" gate.
- **Regime-appropriate rotation is mandatory:** do not hold beta-2 AI growth names (e.g. MU)
  into a Q3 stagflation drawdown — Flex must rotate to the active quadrant's winners
  (energy/defense/materials/value) or stand down to cash, exactly as the quadrant rules say.
- Keep kill criteria + EOD stops + the 6-gate gatekeeper; treat congressional/lobbying/contract
  signals as low-weight tie-breakers (closet beta, weak risk-adjusted edge), not primary
  theses.

---

## 8. Risk controls / guardrails

- **0.1% floor on every core sleeve** (living hedge — §0.2). Never zero.
- **Active-quadrant ceiling ~90% of the core block** (living hedge — §3; confirmed
  2026-06-30, was ~80%). Flex and cash are separate sleeves outside the core block.
- **Single-name cap** 3–4% (Flex), ~15% soft cap any one name.
- **Cadence: monthly base re-balance, event-driven exceptions only** (hard data release,
  shock level 3, a gate flip). Avoid daily re-decision — it maximizes turnover, tax drag, and
  the LLM's room to rationalize, for no documented edge.
- **Sell-before-buy**, integer shares, no options/margin/short in Phase 1.
- **Quarantine implausible fundamentals** before they touch a regime or thesis signal
  (extend the deterministic-precompute pattern to a fundamentals-integrity flag).

---

## 9. Expected outcome model (regime × call-quality) — the real model

This replaces the static core-vs-SPY model. The decisive variable is no longer just the
regime — it is **whether the regime call is right, late, or wrong.** Illustrative, directional
(plug in real paper-trading numbers to calibrate):

| Regime (SPY) | Call RIGHT & timely | Call LATE | Call WRONG |
|---|---|---|---|
| **Strong bull (+20)** | concentrate growth early → book ≈ SPY to +1 | capture half → ~ −3 to −4 vs SPY | stayed defensive → ~ −7 to −9 vs SPY |
| **Sideways (+5)** | tilt + ballast both help → ~ +1 to +3 vs SPY | ~ 0 vs SPY | ~ −2 to −3 vs SPY |
| **Stagflation DD (−12)** | rotate to Q3 winners → ~ +13 to +15 vs SPY | half-rotated → ~ +8 vs SPY | **stayed in growth → ~ −4 vs SPY (loses to SPY; floor caps worst case)** |
| **Deflation crash (−25)** | TLT/gold/cash → ~ +15 to +18 vs SPY | ~ +10 vs SPY | concentrated wrong → ~ −5 to −8 vs SPY |

Read the matrix: **when the call is right, the strategy wins in every regime, by a lot in
drawdowns; when the call is wrong, it loses — and in a drawdown the wrong call is the
dangerous cell because rotation concentrated into the falling quadrant.** The 0.1% floor is
what keeps the WRONG column from being catastrophic. Everything in the strategy is in service
of moving probability mass from the LATE/WRONG columns to the RIGHT column, and of shrinking
the penalty in the WRONG column (the floor, the ceiling, the stops).

---

## 10. Handoff map — how this spec becomes the GitHub automation

For the later refinement pass, each strategy element maps to a layer:

| Strategy element | Automation layer |
|---|---|
| Growth/inflation/dollar axes, gate, concentration targets | **Deterministic Python precompute** (collector) — echoed, not re-derived |
| 0.1% floor, ~90%-of-core ceiling, single-name caps, sell-before-buy | **Hard guardrails** in the system prompt + trade-validation rules (`config/risk-limits.json`) |
| Quadrant→block rotation table (§3), dollar overlay (§4) | **Precomputed target weights** the LLM executes toward |
| Re-entry triggers (§6) | **New precompute** — biggest current gap; build next |
| Flex gatekeeper, kill criteria, opportunity-cost test | LLM research + deterministic enforcement |
| Dual-axis scoring, Deflated Sharpe, right/late/wrong tracking | **Track-record / performance module** |
| Final trade approval | **Human in the loop** (unchanged) |

The LLM's role shrinks to: research, thesis drafting, Flex gatekeeping narrative, and report
writing. It must **not** have discretion over the axes, the gate, the concentration targets,
or the dollar tilt — those are computed and echoed.

---

### One-line summary
The Growth book is a **regime-concentration machine** benchmarked against SPY: it can beat
SPY in any regime it calls correctly and in time, it loses mainly when the call is wrong or
late, the 0.1% floor and ~90%-of-core ceiling are the survival mechanisms for wrong calls, the dollar
is the cleanest tilt switch, and **re-entry timing is the next thing to build** because it is
the weakest link in capturing the wins the strategy is designed for.
