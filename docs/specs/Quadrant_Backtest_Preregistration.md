# Quadrant-tilt backtest: does Core's quadrant framework work? Pre-registration of the rules and the verdicts

**Status: LOCKED at this document's first commit.** Written 2026-10-17, before any data for this study is fetched and before any return exists, on top of `master` commit `51bed06a` (`51bed06` = the fast-forward of `research/core-candidates-oos`; branch `research/quadrant-backtest` was created from it). `CLAUDE.md` rules 1–10 apply. This study writes nothing to production and changes nothing in `src/`; its code lives in `research/quadrant_backtest/` and is never deployed.

Nothing below may change after the first real run. A change means a new pre-registration (a new file, with its own first commit). Two appendices may be added after this first commit and before any return is computed: **Appendix A** (the basket history and proxy table, generated mechanically by the rule in section 2, item 6) and **Appendix B** (the replay's window start date). A dated note headed "Post-run clarification, no rule change" may be appended after the run; it is not part of the locked rule text.

**Read no date on or after 2026-01-01**, from any source.

**What was looked at before this document was written.** No price, no macro vintage and no quadrant replay for this study. Known from the repo only: the production quadrant baskets (section 2, item 5), that `shared.quadrants.active_quadrant` returns "" (indeterminate) whenever either confirmed axis is flat, unknown or indeterminate, and that GDPNow, the growth axis's primary input, starts in 2011 (which is why the window starts no earlier than 2011-07-01).

## 1. The rules (Jorge's, word for word)

**Quadrant signal.** Run `scripts/replay_axes.py` (the real production classifier on ALFRED point-in-time data, stateful with the N=2 confirmation chain, weekdays) over business days from 2010-01-04 to 2025-12-31, building the vintage cache with `scripts/alfred_cache.py`. The **window starts** at the first business day from which the confirmed quadrant has been determinate (not indeterminate) for 20 consecutive business days, and **not earlier than 2011-07-01**. Record the start date it yields. Each day's quadrant is the replay's confirmed `active_quadrant`; a day that's indeterminate keeps the previous determinate quadrant (or, before the first one, holds Q-ALL).

**Baskets.** Each quadrant's basket is the **current production mapping** of quadrant → member ETFs (from `shared.quadrants` / the roster config; cite the source), equally weighted within the basket.

**Portfolios** (each starts at $100,000, total return, compounding):

- **Q-TILT:** 100% in the active quadrant's basket. On a quadrant change, trade at the **next** session's close.
- **Q-ALL** (no-call baseline): all four baskets, each 25%, equal-weighted within, rebalanced at each month-end.
- **SPY** buy-and-hold, total return.
- **C5** (trend + credit) on the same window, using the existing `research/core_candidates` code **unchanged**.

**Costs:** 2 bps per dollar traded. No taxes.

**Verdicts (mechanical):**

1. **Do the quadrant calls add value?** "CALLS ADD VALUE" only if Q-TILT's CAGR > Q-ALL's CAGR **and** Q-TILT beats Q-ALL in at least 2 of 3 sub-periods (window start–2015-12, 2016-01–2020-12, 2021-01–2025-12). Otherwise "CALLS ADD NO VALUE."
2. **Does the framework beat SPY?** "BEATS SPY" only if Q-TILT's CAGR > SPY's, Q-TILT's max drawdown ≤ SPY's, **and** it wins 2 of 3 sub-periods vs. SPY. Otherwise "DOES NOT BEAT SPY."

**Reported, never deciding:** a softer tilt (50% active basket + 50% Q-ALL, closer to production's reference weights); monthly instead of on-change rebalancing for Q-TILT; Q-TILT vs. C5 side by side.

**Basket history and proxies.** For every basket member, find its first trading date. Where a member didn't trade at some point in the window, use a **proxy chosen by asset class only** (e.g. a T-bill ETF → FRED `DTB3` accrual; a managed-futures ETF → none, so drop it; a commodity ETF → DBC; a sector ETF → its oldest same-sector ETF), applied until the member exists. If no defensible proxy exists, **drop** the member until it exists and reweight the rest. Write the full table (member, first date, proxy or "dropped until", reason) to the pre-registration as an appendix commit. **No proxy may be chosen after any return is seen.**

## 2. Interpretations (locked with the rest)

Each is listed so it can be challenged before the first real run.

1. **Replay.** `scripts/replay_axes.replay(start, end, cache)` is called unchanged, with start 2010-01-04, end 2025-12-31, and the cache built by `scripts/alfred_cache.py --start 2008-01-01 --end 2025-12-31`: every series in `src/config/macro-series.json`, vintages known between 2008-01-01 and 2025-12-31 only (FRED's `realtime_end` is 2025-12-31, so no 2026 revision can enter). The FRED key is the collector's `FredApiKey` Key Vault secret (`src/shared/keyvault.py:12`, used at `src/collector/handler.py:2849`), passed in the `FRED_API_KEY` environment variable only. No classifier code is reimplemented or changed.
2. **Determinate** means the replay's `active_quadrant` is one of Q1, Q2, Q3, Q4 (the function returns "" otherwise). The **window start** is the earliest business day d ≥ 2011-07-01 such that d and the 19 business days after it are all determinate. That date is recorded in Appendix B. The window ends 2025-12-31.
3. **Trading calendar.** Trading days are SPY's yfinance rows. The replay is on weekdays, so it also produces exchange holidays; a holiday's quadrant is used only if it changes the in-force quadrant (rule below), and its effect is traded at the next trading day's close after the next trading day it becomes known on. Concretely: the quadrant **in force at the close of trading day t** is the replay's effective quadrant for the last weekday ≤ t (with the indeterminate-day rule applied: an indeterminate day takes the previous determinate quadrant). A change first visible at trading day t is executed at the close of the **next** trading day t+1. The quadrant replay of day d uses only vintages with `realtime_start ≤ d`, all of which are public by d's close.
4. **The portfolios start** at the close of the window's first trading day (on or after the window start), with $100,000 invested at that close per the quadrant in force at the previous trading day's close (the start day's own signal executes the next day like any other). The first purchase pays costs. SPY is $100,000 × adj_t ÷ adj(start), no cost.
5. **Baskets (source).** The config mapping `shared.quadrants.QUADRANT_CONCENTRATE`, built by `_build_quadrant_concentrate()` from `src/config/sleeve-roles.json` (each role's `selected`): Q1 = SPY, QQQ, SMH; Q2 = XLI, XLF, COWZ, VDE, PDBC, VTIP; Q3 = COWZ, VDE, GLD, PDBC, XLP, XLV, VTIP, KMLM; Q4 = GLD, XLP, XLV, KMLM, TLT, IEF, USMV. **Disclosed:** production's live map follows auto-switched incumbents (`SleeveSelectionState`); the latest local snapshot (2026-10-02) had SOXX in place of SMH and IHE in place of XLV. The config mapping is used because it is the reproducible source in the repo; the live one changes with state.
6. **Proxy rule (by asset class only, fixed here before any first date is looked up):**
   - US broad, growth or sector equity ETF → the oldest US ETF of the same index family or sector (e.g. semiconductors → the oldest semiconductor ETF in the data).
   - US value / cash-flow factor equity (COWZ) → IWD (Russell 1000 Value, the oldest large-cap value ETF).
   - US minimum-volatility equity (USMV) → SPLV (the oldest low-volatility US equity ETF).
   - Broad commodities (PDBC) → DBC.
   - Short-maturity TIPS (VTIP) → STIP (the oldest short-TIPS ETF).
   - Managed futures (KMLM) → none: **dropped** until it exists.
   - Gold, Treasuries, sector ETFs that already exist → no proxy needed.
   A proxy is used only if its own first date is before the period it covers; otherwise the member is dropped for the uncovered part. Appendix A applies this rule mechanically from the first dates alone.
7. **Holdings, drift and events.** Every portfolio is a set of dollar balances that drift with each holding's total return (adjusted close) between events. At an event close the target weights are applied to that day's equity: **Q-TILT** at the execution of a quadrant change (equal weights across the new basket's available members); **Q-ALL** at each month-end close (25% per basket, equal weights within each basket; a name in two baskets is held once, at the sum of its shares). A **composition event** occurs whenever a basket member's availability changes inside a held basket (a proxy is replaced by the real member on the member's first trading date; a dropped member appears): the affected basket is reset to equal weights over its new member set at that close. A member's first trading date is the date of its first adjusted close; the switch happens at that close, so the member's own returns start the next day.
8. **Costs.** At each event close: 2 bps × Σ |target dollars − current dollars| over all holdings; the cost is taken out by scaling every target by (E − C) ÷ E.
9. **Sub-periods and metrics** follow the Core-candidates conventions (`docs/specs/Core_Candidates_Preregistration.md`, section 2, items 13–14): sub-period return from the last close before its start (the first sub-period from the start close) to its last close; "beats" means strictly greater; CAGR = (E_end ÷ E_start)^(365.25 ÷ calendar days) − 1; volatility from daily returns × √252; Sharpe on daily excess over DTB3 ÷ 252 (DTB3 from the latest value on or before the previous trading day; the Core-candidates cache); maximum drawdown with peak, trough and recovery dates; calendar years from the previous year's last close; switches per year = executed quadrant changes ÷ years.
10. **C5** is `research.core_candidates.engine` called unchanged on the same window: its trend test on SPY, its credit test on `BAA10Y`, its 2x simulation and costs. As in the out-of-sample study, the engine's two window-tied constants (`FIRST_DECISION`, `SUB_PERIODS`) are set inside a scoped override (to the trading day before the window start, and to the three sub-periods above), which is always restored; no function is changed. C5 starts from its own first month-end decision rule, so it is invested from the first trading day after the window's first decision day.
11. **Sensitivities (report only).** Softer tilt: 50% in Q-TILT's target basket and 50% in Q-ALL's target weights, rebalanced at every Q-TILT event and every month-end. Monthly Q-TILT: the quadrant is read only at month-end closes and executed at the next close, with equal weights reset then.
12. **Hit table.** For each quadrant, its basket's standalone equal-weight index (rebalanced at month-ends, composition events as in item 7, no cost) is compared on the days that quadrant was in force for Q-TILT against the days it was not: annualised mean daily return, and the cumulative return over each set.
13. **Verifier.** A separate post-run program, importing nothing from the backtest modules, re-derives from the raw cached files **12 random quadrant switches** (seed 11): the switch date from the cached replay, the execution date and the new basket; and **12 random calendar months** (seed 11): Q-TILT's and Q-ALL's monthly returns. Exact for dates and baskets, 1e-9 relative for returns.
14. **Only the exact pre-registered run yields verdicts:** this window rule, these baskets and proxies (Appendix A), these portfolios, and an unmodified pre-registration. Outputs go to `reports/quadrant_backtest/<run-id>/` (gitignored). Report and stop: no tuning, no new variants.
