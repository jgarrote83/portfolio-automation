# SPY close-flow — pre-registration of the backtest and its go/no-go rule

**Status: LOCKED at this document's first commit.** Written 2026-10-05, before any close-flow code and before any close-flow result exists, on top of commit `b6edd42b7a19c16d5a30f35b30e075395a46b6b3` (branch `feat/closeflow-backtest`, created from `master` at `c7eb783`). Context: ORB v1 is NO-GO (`.review/phase2-results.md`) and gets no holdout run; SPY close-flow is the next candidate. `CLAUDE.md` rules 1–10 ("ORB program", rule 4 as amended on this branch's first commit) apply.

**The idea:** if SPY has moved clearly up or down by 15:30, trade in that direction and exit at the close. The rationale is mechanical end-of-day flows (leveraged and inverse ETF rebalancing, dealer hedging, closing-auction rebalancing) that push in the direction of the day's move. One instrument, at most one trade a day, a 30-minute hold.

Nothing below may change after the first real run. A change means a new pre-registration (a new file with its own first commit) and a fresh test period; the 2024–2025 window is then no longer an out-of-sample test of the changed rule. Every run's header records this file's path, the SHA of the commit that introduced it, and whether its contents still match their first-commit hash.

## 1. The primary variant (the go/no-go is judged on this one only)

The rules and thresholds below are Jorge's, copied word for word from the instruction, except that the Instrument row states his decision of 2026-10-05.

| Rule | Value |
| --- | --- |
| Window | 2024-01-02 to 2025-12-31 trading days; SIP data; 2026 is never read |
| Instrument | SPY only. Day trading runs in its own Alpaca account, independent of Core (CLAUDE.md rule 4 as amended), so the Core-roster check does not apply. |
| Signal | r = (close of the 1-min bar stamped 15:29) ÷ (previous session's daily-bar close) − 1. Raw prices |
| Direction | r > 0 → long; r < 0 → short; r = 0 → no trade. Trade every day; **no threshold** |
| Entry | Open of the 1-min bar stamped 15:30, plus slippage |
| Exit | The day's SIP daily-bar close (proxy for the official closing auction, i.e. a market-on-close order). No slippage on the exit; commission still applies |
| Half-days | Signal from the bar stamped 30 minutes before the calendar close, entry at that time, exit at the close (use the early-close table verified in Phase 2) |
| Sizing | Fixed $25,000 sleeve, non-compounding; shares = floor(25,000 ÷ entry price); never levered |
| Stop | None |
| Costs | $0.0035/share commission on entry and exit, plus **1¢/share slippage on the entry** |
| Metrics | Daily sleeve return = day P&L ÷ $25,000; no-trade days count as 0; Sharpe = mean ÷ sample std × √252, risk-free 0 |

## 2. The go/no-go (mechanical)

GO only if net Sharpe ≥ **1.0** over 2024–2025 **and** net P&L > $0 in **both** 2024 and 2025. Anything else is NO-GO.

"Net" means after commission and slippage. The Sharpe is compared unrounded. The verdict is computed on the primary variant only; no other number and no other variant enters it.

## 3. Reported sensitivities (never decide the verdict)

(a) first-half-hour signal: r from the previous close to the close of the bar stamped 09:59, with the same entry and exit; (b) trade only when |r| ≥ 0.5%; (c) long-only; (d) entry slippage of 0, 2 and 5¢. Nothing else, no sweeps.

Each sensitivity changes exactly one thing relative to the primary variant; (d) is three variants (0¢, 2¢, 5¢). Six sensitivity runs in all, plus the primary.

## 4. Known limitation and the disclosure it requires

**Known limitation, stated up front:** raw prices mean that on SPY's ex-dividend days, the overnight dividend drop (about 0.3%) is part of r. List those days in the results and report the verdict's sensitivity to excluding them, as a disclosure only.

**The ex-dividend list.** Fetched once, read-only, with one GET to Alpaca's market-data corporate-actions endpoint (`https://data.alpaca.markets/v1/corporate-actions?symbols=SPY&types=cash_dividend&start=2023-12-01&end=2025-12-31`, the existing `.env` keys, no other call) on **2026-10-05** and saved as `data/reference/spy_dividends.json` (gitignored; 2,880 bytes; indent-2 sorted-key JSON; SHA-256 `d9bc5bb84e9f87e00b41c474171cb278f21c57f42e3768615114c41dfda08fdf`). The endpoint returned eight cash dividends; the seven inside the window are:

| Ex-date | Rate ($/share) |
| --- | --- |
| 2024-03-15 | 1.594937 |
| 2024-06-21 | 1.759024 |
| 2024-09-20 | 1.745531 |
| 2024-12-20 | 1.965548 |
| 2025-03-21 | 1.695528 |
| 2025-06-20 | 1.761117 |
| 2025-09-19 | 1.831114 |

(The eighth, ex-date 2023-12-15, is before the window.) **Gap in the source:** SPY pays quarterly, and the response contains no ex-date for the fourth quarter of 2025 although the requested range ran to 2025-12-31. The disclosure uses the seven dates above and nothing else; the results will say that one further ex-date is expected in late December 2025 and is not in the source.

**What the disclosure is:** (i) the list of the seven days with the primary variant's net P&L and r on each; (ii) the primary variant's net Sharpe, 2024 net P&L and 2025 net P&L recomputed with those seven days removed from the daily series (so the Sharpe's mean and standard deviation, and the per-year totals, exclude them). It is reported next to the verdict and never replaces it.

## 5. The IEX vs. SIP check (answers whether live trading could run on the free feed)

For each day, compute r from IEX's 15:29 bar and from SIP's, then report the sign agreement in percent, the mean and max absolute difference in r in basis points, and the days where the sign differs. Reported as numbers only; it does not enter the verdict and decides nothing. The two denominators, the treatment of missing bars and the listing rule are fixed in section 6, items 24–27.

## 6. Implementation details interpreted (locked with the rest)

The instruction fixes the rules above but not every detail a deterministic engine needs. These choices are made now, before any result, and are locked like the rest. Each is an interpretation, listed so it can be challenged before the first real run.

**Calendar and data**

1. **Trading days** are the days SPY has a SIP daily bar in the window (the Phase 1/2 convention). The previous session is the previous day in that same SPY daily series; for 2024-01-02 it is 2023-12-29, so daily bars are fetched from 2023-12-01.
2. **Data** comes only through `backtest.data.get_bars`: SPY `1Day` and `1Min`, SIP, raw (unadjusted); one-minute bars are requested for the regular-session window 09:30–16:00 ET. The IEX check also reads SPY `1Min` and `1Day` from the IEX feed through the same function. The only other network read is the single dividend GET in section 4.
3. **Time zone and stamps:** all minute stamps are US/Eastern. A bar "stamped 15:29" is the bar whose start is 15:29:00 (it covers 15:29:00–15:29:59), so its close is the last price of that minute and its information is complete before 15:30:00.
4. **The holdout:** the code refuses any date on or after 2026-01-01 and has no override flag.

**The signal and the entry**

5. **Entry time.** The entry bar is the one stamped *calendar close − 30 minutes*: 15:30 on a full day. The signal bar is the bar stamped one minute before the entry bar: 15:29. The signal never uses the entry bar or anything after it.
6. **Half-days** (the NYSE early closes in `backtest/sessions.py`, verified in Phase 2: 2024-07-03, 2024-11-29, 2024-12-24, 2025-07-03, 2025-11-28, 2025-12-24; calendar close 13:00): the entry bar is stamped 12:30 and the signal bar 12:29, the exact analogue of 15:29 and 15:30. The literal reading of the instruction ("signal from the bar stamped 30 minutes before the calendar close, entry at that time") would take the signal from the same 12:30 bar the entry opens, which is lookahead; it is rejected on that ground. The exit is the half-day's own daily-bar close.
7. **r** = (close of the signal bar) ÷ (previous session's SIP daily-bar close) − 1, in decimal, from raw prices. **r = 0** means the signal bar's close equals the previous session's close exactly (the two prices are compared directly, no rounding).
8. **Direction:** r > 0 long, r < 0 short, r = 0 no trade (a "flat" day, counted separately from skipped days). Shorts are assumed available and free of borrow cost.
9. **Entry fill:** the open of the entry bar, moved against the trade by the slippage: a long buys at open + slippage, a short sells at open − slippage (slippage = cents ÷ 100 per share).
10. **Shares** = floor(25,000 ÷ the entry bar's open), computed on the un-slipped open, with a 1e-9 tolerance against float noise. Slippage does not enter sizing. Fewer than one share would be a skip (it cannot happen for SPY).

**The exit and the P&L**

11. **Exit price** = the `close` of that day's SIP raw daily bar. No slippage on the exit; commission applies. The exit is assumed fillable at that price with no market impact.
12. **P&L.** Long: shares × (exit − entry fill). Short: shares × (entry fill − exit). Commission = $0.0035 × shares on the entry and again on the exit. Gross P&L is measured at the un-slipped open; slippage cost = shares × slippage per share (entry only); net = gross − slippage − commission, reported separately as in Phase 2.
13. **One trade a day at most**, flat by the close, no stop, no compounding: the sleeve is $25,000 every day.

**Skips**

14. A day is **skipped and counted** (never filled from another bar, never interpolated) if the signal bar or the entry bar is missing, or the previous session's or the day's daily-bar close is missing. A skipped day has return 0 and is listed with its reason. For sensitivity (a) the signal bar is the 09:59 bar instead, so a missing 09:59 bar skips the day in (a) only.

**Variants and metrics**

15. **Sensitivity (a)** replaces only the signal: r = (close of the bar stamped 09:59) ÷ (previous session's daily-bar close) − 1, on every day including half-days. The entry (15:30; 12:30 on half-days), exit, sizing and costs are the primary's. Direction rules unchanged (no threshold).
16. **Sensitivity (b)** trades only when |r| ≥ 0.005 (0.5%, inclusive) on the primary signal; every other day is a no-trade day.
17. **Sensitivity (c)** takes long signals only; short signals are no-trade days (not replaced).
18. **Sensitivity (d)** sets the entry slippage to 0¢, 2¢ and 5¢ (three variants); the primary is 1¢.
19. **Sharpe** = mean ÷ sample standard deviation (ddof = 1) × √252 of the daily sleeve returns, over every trading day in the window including no-trade and skipped days (as 0); risk-free 0. Undefined (zero variance) is reported as n/a and is NO-GO.
20. **Per-year net P&L** is the sum of net daily P&L over the calendar year. **Max drawdown** is on the cumulative net P&L added to the $25,000 sleeve. **Hit ratio** = share of trades with net P&L > 0.
21. **Average net return per trade (bps)** = the mean over trades of net P&L ÷ (shares × the un-slipped entry open) × 10,000.
22. **t-statistic of the mean daily return** = mean ÷ (sample standard deviation ÷ √N), N = every trading day in the window (the same series as the Sharpe).
23. **Long vs. short:** trades, net P&L, hit ratio and average net return per trade (bps), separately.

**The IEX vs. SIP check**

24. **r_sip** is the primary signal. **r_iex_A** = (close of IEX's bar stamped 15:29 — 12:29 on half-days) ÷ (the *SIP* previous-session daily close) − 1, which isolates the difference in the minute bar. **r_iex_B** = (the same IEX bar's close) ÷ (the *IEX* previous-session daily close) − 1, which is what a live engine reading only the free feed would compute. Both are reported; neither enters the verdict.
25. **Sign agreement** (percent of compared days where sign(r_iex) = sign(r_sip), with zero a sign of its own), the **mean and max absolute difference in r** (basis points), and the **days where the sign differs** (all of them in the run's CSV; in the results file all if 50 or fewer, otherwise the 50 with the largest absolute difference plus the count).
26. A day with no IEX signal bar (or, for B, no IEX previous-day close) is excluded from that comparison and counted. The two feeds' days are compared on the same SPY-derived trading-day set.
27. The IEX numbers describe the signal only; they do not model IEX-feed fills or the exit.

**Sanity checks and reporting**

28. **Daily close vs. the last minute bar:** the mean and max absolute difference, in basis points, between the SIP daily-bar close and the close of the last regular-session one-minute bar (15:59; 12:59 on half-days), over days with both; and the days skipped for missing data, with reasons.
29. **Run validity:** only the exact pre-registered invocation (window 2024-01-02..2025-12-31, default configuration, the full variant set, this file unchanged since its first commit) produces a verdict; any other invocation is labelled **NOT A PRE-REGISTERED RUN** and prints none.
30. **No financing or borrow cost, no dividends received, no taxes, no market impact** are modeled. The exit at the daily-bar close is a proxy for a market-on-close order.

## 7. Machine-readable parameters of the primary variant

`tests/test_closeflow.py` asserts that `backtest.closeflow.CloseFlowConfig()` equals this block, and that the sensitivities are exactly those in section 3, so the code cannot drift from the document unnoticed.

```json
{
  "spec_version": "closeflow-1.0",
  "symbol": "SPY",
  "sleeve_capital_usd": 25000.0,
  "entry_minutes_before_close": 30,
  "signal_minutes_before_entry": 1,
  "signal_mode": "pre_entry",
  "first_half_hour_signal_minute": "09:59",
  "min_abs_r": 0.0,
  "allow_shorts": true,
  "entry_slippage_cents": 1.0,
  "commission_per_share": 0.0035
}
```

Window and holdout: `2024-01-02` to `2025-12-31`. The code refuses any date on or after `2026-01-01`.

## 8. What a run reports

For every variant: net Sharpe, total and per-year net P&L, max drawdown, trades, hit ratio, average net return per trade (bps), the t-statistic of the mean daily return, and long vs. short; the days flat, skipped (with reasons) and traded; plus, for the primary, the mechanical verdict, the ex-dividend disclosure (section 4), the IEX vs. SIP check (section 5) and the sanity checks (item 28). The verdict is reported and the work stops: no tuning, no added variants, no 2026 data, however the numbers look.
