# Roster Revision 2026-07 — role-based selection, exempt-hold retirement, international governance

**Status:** approved (account holder, 2026-07). Supersedes the fixed 24-ticker core
roster described in `growth_strategy_spec_v1.md` §2–§3 and the exempt-hold doctrine in
§8. Those spec sections keep their historical text; this document is the authoritative
record of what changed and why. Resolves **FOLLOWUPS #36** (international governance
redesign).

Implemented on branch `feat/quadrant-roles`. The deterministic machinery lives in
`src/config/sleeve-roles.json` (the role/pool config) + `src/shared/quadrants.py`
(resolution) + the collector blocks `sleeve_selection` and `intl_governance`.

---

## 1. What changed (three structural changes)

### 1.1 Fixed tickers → ROLES with candidate pools

The core is no longer a fixed list of 24 tickers. It is a set of **roles** (a job the
book needs done — e.g. "US growth", "gold", "long duration"), each backed by a
**candidate pool** of interchangeable ETFs and one **selected** incumbent. A
deterministic **selection scorecard** (`sleeve_selection`, Task E) ranks pool members
and may raise a `switch_signal`; a **human config commit** to `sleeve-roles.json`
disposes. **The LLM never free-picks a ticker** — it executes toward whatever is
`selected`, and may only surface a switch_signal for human review.

One exception: the **international leader slot** follows the rotation score
automatically (§4), because that is a rotation decision, not a quality-of-fund decision.

### 1.2 Exempt-hold doctrine RETIRED

The permanent AMZN/GOOGL exempt hold (never trimmed below current weight) is **retired**.
Both become **legacy exits** — liquidated in tranches, never re-bought into core. The
mega-cap growth exposure they provided is retained through **QQQ** (us_growth role),
which already holds AMZN/GOOGL at index weight, so the book keeps the exposure without
the single-name idiosyncratic risk. `EXEMPT_HOLDS` becomes an empty tuple; the
validator's exemption rule (V2) and the reference-weight pinning become no-ops rather
than being deleted (so the machinery survives if a future hold is ever designated).

### 1.3 International governance (FOLLOWUPS #36)

International exposure is now governed by the **rotation score + the DXY dollar switch**,
NOT by the US quadrant. The design is **leader-selective, not broad-overweight**:
current data shows international outperformance is narrow — **AIA is +11pp vs SPY while
the average international excess is −7.5pp (2026-07-09)** — so a broad international
overweight would buy the −7.5pp average to reach the +11pp leader. Instead a small
broad base (`intl_broad`) carries policy weight and a rotation-sized **leader slot**
(`intl_leader`) concentrates into the actual leader, with DXY anti-chase and gate
modifiers (§4). This **replaces** the 2026-07 INTERIM "closed gate → suppress rotation
tilt to zero" stop-gap.

---

## 2. Approved role changes + rationale

| Change | Role(s) | Rationale |
|---|---|---|
| Remove single-name idio risk from core | AMZN, GOOGL, INTC, MCK → legacy exit / flex | Core is an ETF backbone; single-name blow-up risk belongs in the flex sleeve with kill criteria. QQQ retains AMZN/GOOGL exposure at index weight. |
| TIPS duration fix | TIP → **VTIP** (pool STIP) `tips_short` | TIP is long-duration TIPS — it carries real-rate duration that fought the Q4 duration roles. Short TIPS (VTIP/STIP) isolate the inflation carry without the duration. |
| Missing reflation assets | add `financials` (XLF), `cyclical_value` (COWZ/XLB) | The Q2 reflation sleeve lacked financials and a cash-flow/value cyclical tilt. |
| Trend-following convexity | add `trend` (KMLM/DBMF/CTA) | Managed-futures trend is a cross-tail convexity sleeve (positive in both stagflation and deflation crises) the roster lacked. |
| Duration barbell | split `duration_long` (TLT) + `duration_mid` (IEF) | A barbell (long + intermediate) rather than one long-duration bet — separate roles, NOT a pool, so both are always held. |
| Defensive-equity style diversification | add `defensive_equity` (USMV/SPLV), `healthcare_def` (XLV/IHE) | Low-vol + defensive healthcare diversify the Q4 defensive-equity ballast beyond staples alone. |
| Exempt-hold retirement | AMZN, GOOGL exit | See §1.2 — QQQ retains the exposure. |
| Semis role | XSD → **SMH** (pool XSD, SOXX) | SMH is the deeper/liquid semis proxy; XSD retained in-pool. XSD becomes a legacy exit as a held name. |
| International leader-selective governance | `intl_broad` (VXUS) + `intl_leader` (AIA) | See §1.3 + §4. Leader-selective on the 2026-07-09 AIA +11pp vs bloc −7.5pp evidence. |

**Legacy exits (held names liquidated, never re-bought into core):** AMZN, GOOGL, INTC,
MCK, DBA, TIP, XSD, PPA, EUAD. INTC / MCK / PPA / EUAD are added to
`flex-candidates.json` so they can be re-entered as *flex* theses (with kill criteria),
never as core.

---

## 3. Role / pool governance rule

- Each role has a `pool` (interchangeable candidates) and one `selected` incumbent.
- The collector's `sleeve_selection` scorecard (Task E) scores pool members
  deterministically (momentum blend − expense-ratio penalty, benchmark-correlation
  eligibility) and raises a `switch_signal` only under hysteresis (a challenger must
  lead by ≥ 2.0 for ≥ 10 consecutive runs).
- **A `switch_signal` never auto-trades and never edits `selected`.** A human disposes
  by committing the new `selected` to `sleeve-roles.json`. This keeps fund-selection a
  human decision while making the *proposal* deterministic and auditable.
- The **only** auto-selection is the `intl_leader` slot, which follows the rotation
  `leader_pick` (§4) — a rotation decision, logged to OverrideHistory for Phase C.

---

## 4. International sizing ladder (implemented in `intl_governance`, Task F)

The TOTAL international sleeve (broad + leader) is sized off the rotation composite,
then modified by the dollar switch and the gate:

| Rotation composite | Sleeve target |
|---|---|
| ≤ 3 | `intl_base_pp` only (default 2.0pp, all in `intl_broad`; leader at floor) |
| 4–6 | base + **1pp** into `leader_pick` |
| ≥ 7 | base + **3pp** into `leader_pick` (up to 2 leaders if two qualify) |

Modifiers (applied in order):
- **DXY anti-chase (spec §4):** `dxy_tailwind_for_intl == "headwind"` → leader tilt **0**
  regardless of score; `"neutral"` → leader tilt **halved**.
- **Gate modifier:** `regime_gate` CLOSED → leader tilt **halved again** (never zeroed —
  this REPLACES the INTERIM suppress-to-zero rule). `market_shock` level 2–3 tilt-limit
  lifts still apply on top.
- **De-rotation:** the existing ≥7 → ≤5 over two consecutive reports unwind now acts on
  this block; the leader slot also unwinds to floor when the pick loses leader status
  (< +5pp vs SPY) or its `ratio_ma_cross` turns `bearish_intl` (the report echoes which
  trigger fired).

`leader_pick` = the strongest `regional_rotation.leaders_vs_spy` name **restricted to the
`intl_leader` pool**, tie-broken by `ratio_ma_cross` (`bullish_intl` > `mixed` >
`bearish`); null when no pool member is a leader (≥ +5pp). `reference_weights` consumes
`intl_governance.sleeve_target_pp` + `leader_pick` for the two intl roles **instead of**
quadrant math; intl names no longer carry a US-quadrant label (removed from
`QUADRANT_CONCENTRATE`), and `by_quadrant` aggregates them into an `intl` bucket.

---

## 5. Tuning follow-up

The hysteresis threshold (≥ 2.0 / ≥ 10 runs) and the intl ladder parameters
(`intl_base_pp`, the 1pp/3pp tilts) are initial values. Revisit once Phase C has graded
**≥ 10** switch/rotation decisions against their incumbent counterfactuals.
