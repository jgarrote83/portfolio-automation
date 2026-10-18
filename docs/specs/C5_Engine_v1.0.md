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
3. **Execution** on the next session, once the market is open and before the cutoff:
   - **Market-on-close orders** (`time_in_force: cls`), whole shares, **sells first, then buys**, with client ids
     `C5-<yyyymm>-<side>-<symbol>`. Ids already present at the broker are skipped, so the step is idempotent.
   - **Cutoff:** per Alpaca's "Orders at Alpaca" page, *"CLS orders submitted after 3:50pm but before 7:00pm ET will be rejected."*
     The page does not cover half-days, so the engine applies the same 10-minute lead to each session's own close (12:50 on a 13:00
     half-day).
   - **Sizing:** the target gets 100% of equity less a **0.5% cash buffer** (`orders.CASH_BUFFER_PCT`). An MOC order fills at a closing
     price unknown at submission, and without a buffer a rising close could leave the account slightly negative in cash. The whole-share
     remainder stays as cash.
4. **After that session's settled close:** the engine verifies that the account holds only the target. It then updates the ledger and
   records each fill with its slippage against that day's close, plus the previous period's return against SPY's. A failed
   verification is an ERROR and marks the month `verify_failed`. It is not retried automatically.
5. **After every settled close:** the engine appends `{date, equity, cash, holding}` to `c5-equity-series/series.json` and writes
   `c5-state/preview.json`. The preview shows "if the month ended today": the previous 9 month-end closes plus today's, and today's credit
   test. It is labelled as a preview, never a decision.

**First enable:** when no state exists, the engine decides for the most recent completed month-end. It executes at today's close if the
market is open and before the cutoff, otherwise at the next session's.

## Safety

- **Fail closed.** A missing or stale input (the calendar, the clock, SPY bars, a month-end close, or a `BAA10Y` value older than 7 days)
  means no decision and no trade. The engine logs an ERROR and keeps the current holdings.
- **Kill switch:** `c5-state/kill-switch.json` `{"tripped": true}` blocks every new order. Azure container names need at least 3
  characters, so the requested `c5/kill-switch.json` lives in `c5-state`.
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
