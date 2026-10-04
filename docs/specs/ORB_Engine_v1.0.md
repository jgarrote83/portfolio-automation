# ORB Day Trading System on Azure — Build Plan

Oct 2, 2026 · @Jorge Garrote

## Overview and strategy rules

ORB replaces the Flex sleeve in portfolio-automation. The catalyst engine and the DayTrade Lab are retired, and a new `src/orb/` engine trades the 5-minute Opening Range Breakout on Stocks in Play from Zarattini, Barbon & Aziz (2024) inside the same 25% sleeve, unlevered. It is developed on 2024–2025 data, confirmed once on a January–September 2026 holdout, then run in the existing Alpaca paper account.

| Rule | Paper's specification | Our version |
| --- | --- | --- |
| Universe filter | Open > $5, 14-day average volume ≥ 1M shares, 14-day ATR > $0.50 | Same, excluding every Core roster ticker |
| Relative Volume | Volume 9:30–9:35 ÷ average 9:30–9:35 volume over the prior 14 days | Same; the live basis (SIP or IEX) is set by the Phase 1 test |
| Stocks in Play | Relative Volume ≥ 100%, then the top 20 | Same |
| Direction | First 5-min bar up → long only; down → short only; doji → no trade | Same; shorts only on shortable, easy-to-borrow names |
| Entry | Stop order at the 5-min high (long) or low (short) | Same |
| Stop loss | 10% of 14-day ATR from the fill price | 10% of ATR from the trigger price |
| Exit | Flat at the close, no profit target | Same |
| Sizing | 1% of equity at risk per trade, up to 4x leverage | 1% of the sleeve at risk, each position ≤ sleeve ÷ 20, never levered |
| Costs | $0.0035/share commission, no slippage | Commission-free at Alpaca; slippage modeled and measured |

Every value lives in `src/orb/config.py`, env-overridable with a `spec_version` string, and the backtest imports the same pure modules, so what you test is what trades. Unlevered sizing is about 1/16 of the paper's exposure: as a rough guide, its 41.6% a year scales to about 10% a year on the sleeve, or about 2.5% on total equity, before slippage.

## Architecture

One new ORB timer inside `func-pfauto` trades through Alpaca; the existing daily batch (collector → analyzer) only reports on it.

*Diagram: Alpaca (market data and trading API) connects to func-pfauto (pre-open step, the `orb_manage` engine, and the existing daily batch), which persists to `stpfautoprod`; the analyzer sends computed tables to Claude, which writes the report and never places ORB orders.*

Alpaca holds each position's stop until the close, while the 1-minute loop reconciles, checks risk and flattens. The analyzer reads ORB's state for the daily report; nothing it writes ever reaches the ORB engine.

## How Claude Code builds this

Claude Code builds ORB inside the portfolio-automation repo one phase at a time and stops at each gate for your approval. Commit this plan as `docs/specs/ORB_Engine_v1.0.md`, the source of truth like the repo's other specs, and add an ORB section to the existing `CLAUDE.md` with these rules:

1. Work one phase at a time; a phase is done only when its checklist is ticked, `pytest` passes, `ruff` is clean and `FOLLOWUPS.md` records it.
2. Read every strategy parameter from `src/orb/config.py`; any threshold change bumps `spec_version`.
3. Follow repo doctrine: no market hours in cron, gate on Alpaca's clock and calendar, reconcile first, idempotent `ORB-` client order IDs.
4. Keep `src/orb/` separate: import only `shared.*`, never trade a Core roster ticker, and reconcile or flatten only ORB's own ledger.
5. Load keys from the gitignored `.env` locally and Key Vault in Azure; never commit, log or print them.
6. Backtests get data only through `backtest.data.get_bars`; tune only on 2024–2025 and never run the 2026 holdout without your approval.
7. Use the paper endpoint only; `ORB_ENABLED` ships `false` in Bicep and only you flip it.
8. Make Azure changes only inside the resource group `rg-portfolio-automation-prod` (subscription EasyGridsProduction). Every `az` command and Bicep deployment names that resource group explicitly; anything outside it, including subscription-, tenant- or Entra-level changes, needs Jorge's explicit approval first.

| Gate | When | You decide |
| --- | --- | --- |
| Retire | Flex and DayTrade Lab confirmed flat | Whether their code is deleted |
| Go/no-go | After Phase 2 results on 2024–2025 | Whether ORB earns a holdout run |
| Holdout | One run on January–September 2026 | Whether it moves to paper trading |
| Enable | After Phase 4 dry runs | Whether `ORB_ENABLED` is flipped on |
| Live | After the Phase 5 gates | Whether real money is used |

## Phase 0: Retire Flex and the DayTrade Lab

Both Flex engines stop entering, run until flat, and only then lose their code. Deleting an engine with positions open would leave resting orders that nothing reconciles.

- [ ] Confirm how the Flex kill switch behaves: it must block new entries while the engine keeps managing exits and time stops
- [ ] Trip it and keep `DAYTRADE_ENABLED=false` (the lab already ships off)
- [ ] Wait until no `FLEXC-` or `FLEXD-` positions or orders remain at Alpaca and both ledgers are empty, then set `FLEX_ENABLED=false`
- [ ] Archive the history blobs (`flex-ledger` closed trades and equity series, `daytrade-log`, `daytrade-grades`) rather than deleting them
- [ ] Record the pre-deletion commit SHA; Phase 4 ports adapted copies of the closed-trade ledger builders, reconcile-first pattern, kill switch and order helpers from it
- [ ] Remove in one PR:
    - `src/flex/`, `src/daytrade/`, and their timers and routes in `function_app.py`
    - The analyzer's `flex_nominations[]` schema check and CI assertion, the Flex section of `project-instructions.md` and its sentinel tests
    - The collector's flex state, kill-switch and orphan echoes, and the catalyst screen if nothing else uses it
    - The portal's sleeve panel and `/api/performance` sleeve fields (rebuilt for ORB in Phase 4)
    - `FLEX_ENABLED` and `DAYTRADE_*` settings in Bicep, and the flex, daytrade and separation tests
- [ ] Rename `FLEX_SLEEVE_CAP_PCT` to `ORB_SLEEVE_CAP_PCT` (25), keeping one sleeve budget
- [ ] Mark Flex-related `FOLLOWUPS.md` items as superseded

```
portfolio-automation/
  docs/specs/ORB_Engine_v1.0.md   # this plan, the source of truth
  src/orb/                        # replaces src/flex/ and src/daytrade/
    config.py  universe.py  signals.py  sizing.py
    reconcile.py  ledger.py  trades.py  killswitch.py  handler.py
  backtest/                       # runs locally, never deployed
    data/                         # get_bars + cache + manifest (Phase 1)
    engine.py  slippage.py  reports.py
  data/cache/                     # Parquet cache, gitignored
  reports/orb/                    # backtest results with config + commit hash
  tests/test_orb_*.py
```

The backtest imports `src/orb/` pure modules (universe, signals, sizing), so live and tested logic are one implementation.

## Phase 1: Data layer (fetch on demand with a cache)

Every backtest gets data through one function, `backtest.data.get_bars`, which serves bars from a local Parquet cache and calls Alpaca only for what's missing. There is no upfront download: the cache grows as testing reaches new symbols and dates, and repeat runs never wait on the API.

```python
get_bars(symbols, start, end, timeframe="1Min", feed="sip") -> pandas.DataFrame
# columns: symbol, ts (US/Eastern), open, high, low, close, volume
```

- **Layout:** `data/cache/{feed}/{timeframe}/{symbol}/{YYYY-MM}.parquet`, one file per symbol-month so file counts stay manageable.
- **Manifest:** a SQLite file records every feed, timeframe, symbol and day already fetched, including days with no bars, so halted or untraded days are never requested twice.
- **Prices:** raw and unadjusted, as in the paper; the adjustment setting is part of the cache key.
- **Freshness:** never cache today or the last 15 minutes, which the free plan doesn't serve and which may still change.
- **Rate limits:** batch many symbols per request, stay under 200 requests a minute, and retry 429 errors with backoff.
- **Safe writes:** write to a temp file, then rename, so an interrupted run never leaves a half-written file.
- **Offline mode:** an `--offline` flag fails on a cache miss instead of calling the API, for exact reruns.

| Dataset | Feed | Used for |
| --- | --- | --- |
| Daily bars, all symbols including inactive | SIP | ATR, average volume, universe filter |
| 9:30–9:35 five-minute bars, eligible symbols | SIP | Relative Volume and trade direction |
| 1-minute bars, the day's top 20 | SIP | Simulating entries, stops and exits |
| 9:30–9:35 five-minute bars, eligible symbols | IEX | Testing whether the free feed can rank stocks live |

Test windows: develop on January 2024 to December 2025; hold out January–September 2026 and run it once, after a strategy is chosen.

- [ ] `backtest/data/` with `get_bars`, the manifest and the rate limiter
- [ ] Tests with a mocked Alpaca client: a repeated call makes zero API calls, an interrupted write leaves no partial file, empty days are recorded
- [ ] IEX vs. SIP report: daily overlap of the two top-20 lists across 2024–2025; high overlap means the free real-time feed could replace the $99 plan
- [ ] Optional `scripts/warm_cache.py` to prefill a date range overnight

Live trading needs no separate data store: the ORB engine builds its candidate list from Alpaca each morning (Phase 4), using whichever volume basis the IEX vs. SIP report supports.

## Phase 2: Backtest engine

The go/no-go question is whether the edge survives realistic slippage in 2024–2025 and again on the 2026 holdout. Slippage is the threat: on a stock with a $2 ATR, R is $0.20, so 2 cents per side already costs 0.2R against the paper's 0.1–0.4R average edge.

Engine rules, applied minute by minute after 9:35:

1. Entry fills at the stop price, or at the bar's open if price gaps through it.
2. The stop loss fills the same way; if entry and stop fall in the same minute bar, assume the stop hit.
3. Positions still open exit at the 4:00 pm close.
4. Shares = the smaller of (1% of the sleeve ÷ stop distance) and (sleeve ÷ 20 ÷ price), where the sleeve is 25% of equity and never levered.
5. Costs = $0.0035/share commission plus slippage of 0, 1, 2 and 5 cents per share per side, each run separately.
6. A long-only run as a stress test, since short availability on news-driven names isn't in the historical data.

Reports per run: equity curve, Sharpe, max drawdown, hit ratio, average R, R distribution, results by year and long vs. short, sleeve return and contribution to total equity, and how often each sizing limit binds.

- [ ] Build the engine and test it on synthetic price paths with known answers, such as a day built to return exactly +3R
- [ ] Run it locally through the Phase 1 data layer
- [ ] Write the go/no-go thresholds down before seeing results; a starting point is net Sharpe ≥ 1.0 at 2 cents per side and a positive result in each calendar year
- [ ] Tune only on 2024–2025, then run the 2026 holdout once; any change after the holdout needs a fresh test period

## Phase 3: Claude API analysis layer

Claude analyzes and reports; it never sits in the 9:35 order path. The strategy is fully rule-based, an API call would add seconds when orders must go in at once, and an LLM's decisions can't be backtested because the model may already know how historical days turned out.

ORB picks its own stocks by relative volume, so the analyzer no longer nominates anything; Claude only reports.

| Job | Where it runs | Output |
| --- | --- | --- |
| Daily ORB section | Existing collector → analyzer run | The collector echoes a read-only `orb_state` block (prior day's trades, R, slippage, kill-switch state); Claude writes the narrative |
| Monthly review | Existing first-Saturday learning cycle | Live vs. backtest R distribution, slippage trend, items to investigate |

Python computes every number in `orb_state`; Claude narrates it, as it did the old flex state echo. A sentinel test asserts the collector and analyzer read ORB's blobs but never import `orb.*`.

Catalyst tagging from headlines is dropped for v1; it can return later as forward-only research logging.

Guardrails: Claude has no trading permissions, and nothing it writes is read by the ORB engine. If the analyzer fails, ORB trades normally and only the report is late.

## Phase 4: ORB engine (paper trading)

One 1-minute timer, `orb_manage`, runs the whole trading day inside `func-pfauto`, on the same pattern as the retired DayTrade Lab loop. It ships disabled (`ORB_ENABLED=false` in Bicep) with a `/api/orb` dry-run route, and fails closed: missing or stale data means no orders and an alert.

Each tick, in order:

1. Reconcile first, ORB ledger only: record stop fills, and give any ORB position without a resting stop one immediately.
2. Gate on Alpaca's clock and calendar; outside the session window the tick is a fast no-op.
3. Pre-open: build the candidate list from daily bars and 14 days of opening-range volume, applying the price, volume and ATR filters and removing Core roster tickers.
4. First tick where all five 1-minute bars from 9:30–9:34 exist: compute Relative Volume, keep the top 20 at ≥ 100%, and drop dojis and shorts that aren't shortable and easy to borrow.
5. Size each from current equity: 1% of the sleeve at risk, capped at sleeve ÷ 20, never above the sleeve in total.
6. Submit each as an OTO order: a stop entry at the range high or low plus a stop-loss child 10% of ATR away, time in force `day`, client order ID `ORB-<date>-<symbol>`.
7. Every tick, check the kill switch and a daily loss limit, e.g. −3% of the sleeve; a breach cancels entries and flattens.
8. Fifteen minutes before the close, cancel unfilled entries; close what remains with market-on-close orders before Alpaca's cutoff, market orders two minutes before the close as fallback, then verify flat.
9. Persist `orb-ledger`, `orb-state/{date}.json`, `orb-log/{date}.jsonl` (every candidate and decision, suppressed ones included) and closed trades with R-multiples in `orb-trades`.

The stop is set from the trigger price, not the fill; logging trigger vs. fill on every trade measures entry slippage. If a dry run shows Alpaca rejects a stop-type parent in an OTO order, the fallback is attaching the stop on the next tick after the fill, leaving at most one minute unprotected.

The portal's Flex sleeve panel is replaced as specified in the next section.

## Portal: Day Trading tab

ORB gets its own Day Trading tab in the portal: cumulative and daily P&L at the top, then, for any day you click, that day's trades and a selection record showing why each stock was chosen or rejected. The Performance page keeps only a compact ORB contribution line where the Flex chart was, linking to the tab.

*Wireframe of the Day Trading tab: summary line, cumulative P&L line, daily P&L bars, day picker, trades table, selection funnel, and the table showing why each stock was chosen or rejected.*

Clicking a day in the bars loads its trades, funnel and selection table; the selection table is the new view that answers why a stock was or wasn't picked.

**Charts** (Chart.js, following the page's existing conventions):

- Cumulative P&L line in dollars, with % of equity in the tooltip. It reuses the already-validated sleeve color and turns dashed grey below 30 closed trades, as today.
- Daily P&L bars beneath it on the same dates, in the page's existing gain and loss colors, so no new palette validation is needed. A day with no setup shows as zero; a day the engine didn't run shows as a gap.
- Hovering a bar lists that day's tickers with each one's P&L; clicking it loads the day below. Quadrant shading stays, so ORB can be read against the regime.
- No win rate, Sharpe or other skill statistic, keeping the panel's existing sample-size rule.

**Trades that day:** one row per trade with ticker, side, trigger, fill, slippage, stop, exit, exit reason (stop, close, kill switch or daily loss limit), shares, P&L and R, plus a total row.

**Selection record:**

- A funnel line with the count at each step: universe → price, volume and ATR filters → not Core → Relative Volume ≥ 100% → top 20 → orders placed → triggered → traded.
- A table with one row per candidate: the top 50 by Relative Volume, plus any ticker you search for among all names evaluated that day.
- Each requirement cell shows the measured value with ✓ or ✗, and the column header shows the threshold from that day's config, so a later threshold change never rewrites history.
- Outcome is "Traded", "Order not triggered" or "Rejected: <first failed requirement>", such as "Rejected: rank 23". The Relative Volume column also notes its basis (SIP or IEX).

**Performance page:** the Flex chart is replaced by a compact cumulative line of ORB's contribution to total equity, in percentage points as today, with a "Details →" link to the tab.

**New page:** `web/daytrading.html` and `daytrading.js`, reusing `app.js` for sign-in and API calls and the existing stylesheet, with a "Day Trading" link added to every page's top nav.

| Engine writes | Contents | Served by |
| --- | --- | --- |
| `orb-equity-series.json` | One row per trading day: P&L in $ and % of equity, cumulative P&L, trade count, tickers | `/api/performance`, with `orb_*` fields replacing `sleeve_*` |
| `orb-trades/closed-trades.json` | Every closed trade: trigger, fill, stop, exit, reason, shares, P&L, R | New `/api/orb/day?date=` |
| `orb-state/{date}.json` | Config snapshot, funnel counts, each evaluated candidate's requirement values, pass or fail, and outcome | `/api/orb/day?date=&q=` |

The engine decides every outcome and first failed requirement; the portal only displays them, the page's existing rule.

- [ ] Build `web/daytrading.html` and `daytrading.js` with the layout above, and add a Day Trading link to every page's top nav
- [ ] Replace the Flex chart on the Performance page with the compact ORB contribution line linking to the tab
- [ ] Add `/api/orb/day` and swap `sleeve_*` for `orb_*` in `/api/performance`
- [ ] Have the engine write `orb-state/{date}.json` and `orb-equity-series.json` as above
- [ ] Tests: the API passes outcome strings through unchanged, and a ticker search finds names outside the top 50

## Phase 5: Monitoring and go-live criteria

Each paper day is checked against a shadow backtest of that same day, and real money waits until the two agree for about three months.

Daily checks, computed by the ORB engine after the close and echoed in the next morning's report:

- **Reconciliation:** every intended order matches an Alpaca order, every fill is logged, and the account was flat at the close.
- **Shadow backtest:** each week, the local backtest replays the paper days on the same bars and compares trade by trade; gaps are slippage or a bug.
- **Alerts:** Application Insights alerts to email or Teams on any function failure, a missed run on a trading day, or ORB positions still open after the close.

Go-live gates (starting thresholds; set your own before paper trading begins):

- [ ] At least 60 trading days of paper trading
- [ ] No unreconciled day in the last 30
- [ ] Median entry slippage at or below the level assumed in the passing backtest
- [ ] Paper R distribution consistent with the shadow backtest
- [ ] Live account meets margin and day-trading requirements

Alpaca's paper fills ignore queue position and market impact, so they flatter results. Start live at a fraction of the paper's risk, such as 0.25% per trade, and scale up only as live slippage confirms the backtest.

## Cost estimate

ORB adds almost nothing to the existing bill. Backtesting runs free on your machine, the 1-minute timer fits in the Function App's free grant, and Claude reports through the analyzer run you already have. The only possible new cost is real-time SIP data at about $99 a month, and only if the IEX test fails.

| Item | Approx. added monthly cost | Notes |
| --- | --- | --- |
| Real-time SIP data (Alpaca paid plan) | $0 or ~$99 | Only if the IEX vs. SIP report shows IEX can't rank Stocks in Play |
| Function App executions | ~$0 | About 390 one-minute ticks per trading day, roughly 8,000 a month |
| Storage for ORB blobs | < $1 | Ledger, daily state, logs and closed trades |
| Claude API | ~$0 | ORB adds a section to the existing daily report |

The 1-minute cadence is the same pattern the DayTrade Lab already used, so no new hosting setup is needed.

These figures are approximate and weren't looked up today. Confirm them in the [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/), Alpaca's market data plans, and [Anthropic's pricing docs](https://docs.claude.com).

## Risks and open questions

The biggest risk is that slippage or edge decay erases the result; Phases 2 and 5 exist to find that out cheaply.

- **Edge decay:** the paper was published in February 2024 and widely shared, and public edges often shrink. The 2026 holdout measures this directly.
- **Shared account:** Alpaca nets positions per symbol, so the Core-roster exclusion and ORB-only reconcile are what keep ORB and Core apart; both get regression tests ported from the old separation suite.
- **Small by design:** unlevered, ORB runs at about 1/16 of the paper's exposure, so even a working strategy adds roughly 2.5% a year to total equity before slippage. Judge the go/no-go at that scale.
- **Short-sale restrictions:** after a stock drops 10% in a day, SEC Rule 201 allows shorts only on an uptick, so stop-sell entries on the biggest movers may fill late or not at all.
- **Day-trading rules:** US margin day trading has historically required $25,000 in equity, and FINRA has been working on changes. Check the current rule before going live.
- **Survivorship:** Alpaca's asset list leans toward current tickers; a full-universe test with delisted names needs Polygon or Databento data.
- **Taxes:** hundreds of short-term trades a year, plus wash-sale rules, make record-keeping heavier; worth a word with a tax professional.
- **Open question:** which book you're adding, and whether its rules should change anything in this plan.
- **Open question:** the account size for live trading, which sets the sleeve and whether the margin rules apply.
