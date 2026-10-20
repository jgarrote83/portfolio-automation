# C5 / "EasyGrid" engine v1.0

C5 (trend + credit) passed the 2005–2025 study (`Core_Candidates_Preregistration.md`), the 1989–2004 out-of-sample validation
(`Core_Candidates_OOS_Preregistration.md`) and the 2026-01-02 to 2026-05-19 holdout smoke test (`C5_Holdout_Preregistration.md`).
This engine trades it on its own Alpaca **paper** account, which Jorge calls **"EasyGrid"**. It ships **disabled** (`C5_ENABLED=false`).

## The rule (one implementation: `src/c5/signals.py`)

At the settled close of the **last trading day of each month**:

- **Trend:** SPY's adjusted month-end close > the mean of the last 10 month-end closes (the latest included).
- **Credit:** `BAA10Y` as published for the prior day (the latest value dated **strictly before** the decision day) ≤ the mean of the
  same strictly-prior values over the last 126 sessions.
- **Holding:** both ✓ → **SSO** (2x S&P); exactly one ✓ → **SPY**; neither → **BIL** (T-bills).

The research backtests import the same module. Moving the logic there was proven byte-identical on the 2005–2025 and 1989–2004 runs
(`scripts/c5_parity_fingerprint.py`).

## Independence (non-negotiable)

- C5 trades only the EasyGrid paper account, through the app settings `C5_ALPACA_KEY_ID` and `C5_ALPACA_SECRET`. These are Key Vault
  references to `Alpaca-Easygrid-API-Key` and `Alpaca-Easygrid-API-Secret`, the names Jorge used in `kv-pfauto-prod`, confirmed on
  2026-10-18 in place of the originally requested `AlpacaC5ApiKey` and `AlpacaC5ApiSecret`.
- It never reads or writes Core's account, positions, orders, ledgers, blobs or settings. The only secret it reads itself is
  `FredApiKey`, by name. It does not call `shared.keyvault.load_secrets`, which would load Core's Alpaca keys.
- Core never imports `c5`. `tests/test_c5_engine.py` enforces both directions with a source scan.

## Schedule (`c5_tick`, every 15 minutes on weekdays; no hard-coded market hour)

Every session's open and close come from Alpaca's `GET /v2/calendar`, which carries half-days and holidays, and live state from
`GET /v2/clock`.

1. **Reconcile** on every tick: the account's positions against `c5-ledger/ledger.json`. Any mismatch is an ERROR alert, recorded in
   `c5-state/health.json`.
2. **Decision:** 20 minutes after the close of a month's last session, the daily bar is final and past the free plan's 15-minute SIP
   delay. The engine computes the decision from Alpaca SIP daily bars (`adjustment=all`) and FRED `BAA10Y`, then writes
   `c5-state/<yyyymm>.json` with the inputs, the decision and the target.
3. **Execution** on the next session, with the market open, on the tick inside the **near-close window**:
   - **Plain market DAY orders** (`type: market`, `time_in_force: day`), whole shares, **sells first, then buys**, with client ids
     `C5-<session yyyymmdd>-<side>-<symbol>`, keyed on the session the orders execute in. Ids already present at the broker are skipped,
     so the step is idempotent.
   - **Window:** `[close − 20 min, close − 2 min)` of each session's own close (`schedule.in_submit_window`). With the 15-minute tick
     that is exactly one tick: 15:45 on a 16:00 close, 12:45 on a 13:00 half-day. A missed window tick leaves the decision `decided`;
     it executes in the next session's window and is recorded as `late`.
   - **Why not market-on-close (changed 2026-10-20).** The engine first sent `time_in_force: cls` orders. Alpaca's paper account has no
     closing auction: it treats a `cls` order as a market order at the close, and such orders can expire unfilled
     (https://forum.alpaca.markets/t/paper-moc-orders-partially-fill-remainder-expires-same-setup-on-live-works/19155). The first live
     order did exactly that: `C5-20261019-buy-SSO`, 1,421 SSO, sent 09:45 ET, expired 16:00:37 ET with 0 filled. The fill is now about
     15 minutes before the close, not at it; the slippage against that day's close is recorded per fill (step 4), so the difference
     from the backtest's close-to-close assumption stays measured. Market-on-close remains the natural choice for a future live account.
   - **Sizing:** the target gets 100% of equity less a **0.5% cash buffer** (`orders.CASH_BUFFER_PCT`). The size comes from the latest
     SIP price, about 15 minutes old on the free plan, while the order fills at the live price; the buffer absorbs that move. The
     whole-share remainder stays as cash.
4. **After that session's settled close:** the engine verifies that the account holds only the target. It then updates the ledger and
   records each fill with its slippage against that day's close, plus the previous period's return against SPY's. A failed
   verification is an ERROR and marks the month `verify_failed`. It is not retried automatically.
5. **After every settled close:** the engine appends `{date, equity, cash, holding}` to `c5-equity-series/series.json` and writes
   `c5-state/preview.json`. The preview shows "if the month ended today": the previous 9 month-end closes plus today's, and today's credit
   test. It is labelled as a preview, never a decision.

**First enable:** when no state exists, the engine decides for the most recent completed month-end. It executes in today's near-close
window if that window has not yet passed, otherwise in the next session's.

## Safety

- **Fail closed.** A missing or stale input (the calendar, the clock, SPY bars, a month-end close, or a `BAA10Y` value older than 7 days)
  means no decision and no trade. The engine logs an ERROR and keeps the current holdings.
- **Control states** (`c5-state/kill-switch.json`, `{"state", "changed_at", "changed_by", "reason"}`; Azure container names need
  at least 3 characters, so the originally requested `c5/kill-switch.json` lives in `c5-state`):
  - `active` trades normally;
  - `paused` places no orders and keeps the holding;
  - `paused_to_tbills` sells to BIL at the next close (once, sells first, the same 0.5% buffer), then places nothing.

  Month-end decisions are still **recorded** while paused, never executed. An unknown state value is treated as `paused`, and the legacy
  `{"tripped": true}` reads as `paused`.
- **Re-align:** `c5-state/realign-request.json` is a one-shot request. While `active`, it is honoured at the next close: the engine
  aligns to the latest completed month-end decision exactly as on first enable, supersedes any not-yet-executed monthly decision, then
  clears the request. While paused it is ignored. Resuming from the portal writes `active` **and** a re-align request.
- **In-flight orders (no duplicate buy; three independent layers):**
  - a re-align request stays **pending** while any decision is `submitted` or this engine has an open order at Alpaca, and is honoured
    only after that close has been verified (until the fill, the positions it would plan from are stale);
  - the engine never sends a buy for a symbol on which it already has an open order;
  - client ids are keyed on the execution session, so Alpaca itself rejects a same-day duplicate. **Any** submission error is resolved
    by looking the order up by client id (`GET /v2/orders:by_client_order_id`, https://docs.alpaca.markets/reference/getorderbyclientorderid;
    the doc lists only 200, and an unknown id was measured to return 404): found → `already_submitted` (WARNING); not found → ERROR.
    The 409/422 error text is only a log hint, never the decision. Nothing is retried.
- **Pausing with orders in flight:** a change to `paused` or `paused_to_tbills` asks Alpaca to cancel this engine's still-OPEN orders
  while the session is open and marks that decision `cancel_requested`; `paused_to_tbills` then places its own BIL orders. A market
  order usually fills within seconds, so there is often nothing open: the decision then stays `submitted` and is verified at the close
  like any other. "Nothing open" never means "cancelled", which would hide a real fill. Later ticks read each order's final status by id: canceled → `cancelled` (strip: "Pause: cancel confirmed"); filled first →
  the fills are recorded and the decision is `executed`, with the ledger written from the account so no mismatch alert fires (strip:
  "Pause: order filled before cancel; acting next session"); still not final once that session has closed → ERROR, once a day. After
  the close nothing can be cancelled: the engine logs that, verifies the orders as usual, and the new state acts from the next session.
- **Reconcile** against the ledger runs at the END of each tick, after any verification, so the tick that records a fill reads
  "Positions match ledger: Yes".
- **Change log:** the engine appends every control change (who, when, why) and every action it takes on one to
  `c5-state/kill-switch-log.jsonl`, exactly once, within one tick.
- **Holding check:** on every tick when no order set is in flight, the account must hold the target of the last executed state.
  Otherwise the engine logs the ERROR "C5 HOLDING MISMATCH", at most once a day (App Insights, the same channel as every other ERROR),
  and `health.json` carries `holding_mismatch {expected, actual}`, which the portal shows in red. There is no automatic retry: re-align
  from the portal.
- **Portal controls** (`web/api`, `POST /api/easygrid/controls`):
  - **Who:** the `owner` role AND a SWA `userId` listed in `EASYGRID_ADMIN_IDS`. An empty list admits nobody.
  - **Every request** needs a reason, a typed confirmation (`PAUSE`, `PAUSE TO T-BILLS`, `RESUME`, `REALIGN`) and the header
    `X-EasyGrid-Control: 1` (CSRF).
  - **What it can write:** only the two control documents, through two blob-scoped SAS URLs signed against the revocable stored access
    policy `easygrid-controls` (`scripts/seed-c5-control-sas.sh`). SWA managed functions have no managed identity, so an RBAC role is
    not possible.
- **Disabled** (`C5_ENABLED` not `true`, the Bicep default): the tick never trades. Hourly, it only refreshes `c5-state/dry-run.json`
  and the preview.
- **Dry run:** `GET/POST /api/c5` (function key) returns the inputs, the decision and the intended orders without trading.
- **FRED key hygiene:** the engine's FRED errors never include the request URL, which carries the key.

## Disclosures

- **Distributions.** Like Core's, this paper account probably credits **no distributions**: SPY's dividends and BIL's T-bill yield are
  missing from its paper equity. The T-bill leg's paper return will be understated, and so, slightly, will the SPY leg's.
- **SSO is not the simulated 2x fund.** It tracked the simulation within 0.04 points a year on average over 2006–2025, but it carries
  swap costs and a 0.91% expense ratio.
- **Taxes.** Monthly switching realizes gains; the record above ignores taxes.

## Infra

Bicep adds three app settings to `func-pfauto` (`infra/modules/functionapp.bicep`): `C5_ALPACA_KEY_ID`, `C5_ALPACA_SECRET` (both Key
Vault references) and `C5_ENABLED='false'`. The app's managed identity already holds Key Vault Secrets User on the vault, so no role
changes. Containers (`c5-state`, `c5-ledger`, `c5-equity-series`) are created on first write, like Flex's. All of it stays inside
`rg-portfolio-automation-prod`.
