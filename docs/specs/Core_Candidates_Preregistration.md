# Core replacement research: five rules-based candidates to beat SPY outright — pre-registration of the rules and the win criteria

**Status: LOCKED at this document's first commit.** Written 2026-10-17, before any candidate code and before any candidate result exists, on top of `master` commit `dfaed0b25aff5ec5134ea933931705c9c742dd58` (branch `research/core-candidates` was created from that commit). `CLAUDE.md` rules 1–10 apply. This study uses no Azure resource, no Alpaca account and no order endpoint, and it changes nothing in `src/`. Its code lives in `research/core_candidates/` and is never deployed.

Nothing below may change after the first real run. A change means a new pre-registration (a new file, with its own first commit); the 2005–2025 window is then no longer an unseen test of the changed rule. A dated note headed "Post-run clarification, no rule change" may be appended below; it is not part of the locked rule text (`backtest/provenance.py` hashes only the text above such a heading). Every run's header records this file's path, the SHA of the commit that introduced it, and whether its rule text still matches its first-commit hash.

## 0. What this step is, and is not

Jorge has set Core's goal: **beat SPY's total return outright.** The Core vs. SPY analysis (`.review/core-vs-spy-analysis.md`, FOLLOWUPS #125) showed the current quadrant/LLM book at −2.68% against SPY's +4.42% over 96 sessions. This study tests five rules-based replacements over 21 years. A pass makes a candidate eligible for **one** run on the reserved holdout (2026-01-02 to 2026-05-19) and then a paper forward test; Core is replaced only after both. Nothing in this study can replace Core by itself.

**The 2026 window is this study's holdout.** No source is ever asked for a date on or after 2026-01-01, and any received row dated on/after it is a failure, never kept.

**What was looked at before this document was written (availability only; no strategy number of any kind had been computed).** `yfinance` (1.7.0) returns daily rows from 2003-01-02 to 2025-12-31 for SPY (5,787 rows) and EFA (5,787); AGG starts 2003-09-29, SSO starts 2006-06-21 and ACWX starts 2008-04-01. Inside the window none of them misses a SPY trading day after its own first date. Yahoo records splits for EFA (3:1, 2005-06-09) and SSO (2:1 on 2015-05-20, 2020-08-18, 2022-01-13 and 2025-11-20); SPY, AGG and ACWX have none. FRED returns `DTB3` and `BAA10Y` from 2003-01-02 to 2025-12-31; on SPY trading days from 2004-06-01 to 2025-12-31, `DTB3` is blank on 41 and `BAA10Y` on 43 (bond-market holidays), which the rules carry forward. The window holds 5,283 SPY trading days, 2005-01-03 to 2025-12-31. These are descriptions of the data, not tuning inputs; the rules below were fixed by the instruction.

## 1. The rules (Jorge's, word for word)

**Window:** month-ends and trading days from 2005-01-03 to 2025-12-31. Signals use only data available at the decision close; trades execute at the **next** session's close. **All returns are total return** (dividend-adjusted).

**Benchmark:** SPY total return, buy-and-hold.

**Candidates.** Each starts with $100,000, compounding, independently.

| ID | Rule |
| --- | --- |
| **C1: Trend-filtered 2x S&P** | At each month-end: if SPY's adjusted close > its 10-month simple moving average (of month-end closes), hold **2x daily-reset S&P** for the next month; otherwise hold **T-bills** |
| **C2: Volatility-targeted S&P** | At each week's last close: exposure = min(1.5, 0.15 ÷ σ), where σ = annualised standard deviation of SPY's last 21 daily returns. Hold that exposure in S&P (above 1.0, the excess is financed) and the remainder in T-bills |
| **C3: Dual momentum** | At each month-end: if SPY's 12-month total return > the T-bill 12-month return, hold whichever of **SPY** or **EFA** (international developed) had the higher 12-month return; otherwise hold **AGG** (US bonds) |
| **C4: Credit-filtered 2x S&P** | At each month-end: let c = FRED `BAA10Y` (Moody's Baa corporate yield minus the 10-year Treasury) **as published for the prior trading day**. If c ≤ its own 126-trading-day simple moving average (credit calm or improving), hold **2x daily-reset S&P** for the next month; otherwise **T-bills** |
| **C5: Trend + credit** | At each month-end, using C1's trend test and C4's credit test: both healthy → **2x S&P**; exactly one healthy → **1x SPY**; neither → **T-bills** |

**Instruments and modeling:**

- **2x S&P (C1):** simulate daily as 2 × SPY daily total return − (1 × the daily T-bill rate + 0.5%/yr spread)/252 − 0.91%/yr expense/252 (the cost of financing the extra 1x). **Validate** against the real **SSO** total return from its inception (June 2006) to 2025: report tracking difference per year and the correlation. If the annual tracking difference averages beyond ±1.5 points, say so prominently.
- **Leverage above 1.0 (C2):** financing = (exposure − 1) × (T-bill rate + 0.5%/yr), charged daily.
- **T-bills:** FRED `DTB3` (3-month rate), accrued daily.
- **Data:** `yfinance` daily adjusted closes for SPY, EFA, AGG and SSO from 2003-01-01 (lookbacks need prior data); FRED `DTB3` and **`BAA10Y`**. Cache under `data/reference/core_candidates/` (gitignored). Refuse dates on or after 2026-01-01 everywhere.
- **Credit timing (C4, C5):** a month-end decision may use `BAA10Y` values dated **strictly before** the decision date (a one-day publication lag), never the same day's value. Missing FRED days carry the last published value forward; count them.

**Costs:** 2 bps per dollar traded, on every rebalance, on both sides. **Taxes are not modeled** (disclose: monthly switching would realize gains in a taxable account).

**Win criteria (mechanical, per candidate, all three required):**

1. CAGR **>** SPY total-return CAGR over 2005–2025, net of costs and financing.
2. Maximum drawdown **no worse than** SPY's own maximum drawdown over the same window.
3. Beats SPY's total return in **at least 2 of 3** sub-periods: 2005-01 to 2011-12, 2012-01 to 2018-12, 2019-01 to 2025-12.

Pass → **CANDIDATE** (eligible for one holdout run on 2026-01-02 to 2026-05-19, then a paper forward test; no replacement of Core without both). Otherwise → **REJECTED**.

**Reported sensitivities (never decide):** C1 with a daily 200-day moving-average rule instead of monthly; C1 at 1.5x instead of 2x; C2 with a 12% or 18% target; C3 with ACWX in place of EFA where data exists; C4 with a 63-day or 252-day credit average instead of 126; costs of 0 and 10 bps. Nothing else.

**The macro question, reported explicitly:** compare C5 with C1 (does credit add anything beyond the price trend?) and C4 with C1 (is a credit filter alone better or worse than a trend filter?), on CAGR, max drawdown and the crisis close-ups.

**Multiple testing:** five candidates are tested, so a pass is weaker evidence than it looks. Record this next to any CANDIDATE verdict. Only the strongest passing candidate (highest CAGR among passes) goes to the holdout.

## 2. Implementation clarifications (locked with the rest)

The rules above do not fix every detail a deterministic run needs. These choices are made now, before any result, and are locked like the rest. Each is an interpretation, listed so it can be challenged before the first real run.

1. **Trading days** are the dates of SPY's yfinance rows (the exchange-local session date). The window is the SPY trading days from 2005-01-03 to 2025-12-31 (5,283 days). Rows before 2005-01-03 are used only for look-backs. A candidate instrument missing a price on a window day it is needed stops the run (nothing is filled).
2. **Total return** is computed from Yahoo's adjusted close (`auto_adjust=False`, column 'Adj Close', which is adjusted for dividends and splits): a symbol's daily total return on day t is adj_t ÷ adj_(t−1) − 1 over consecutive SPY trading days. Yahoo's adjustment factors include dividends paid after 2025; a later dividend rescales every earlier price by the same factor, so no return or moving-average comparison inside the window changes. This is disclosed, not corrected.
3. **Decision days.** A *month-end* is the last SPY trading day of a calendar month; a *week's last close* is the last SPY trading day of a Monday–Sunday calendar week. A decision is taken at its decision day's close with data up to and including that close (`BAA10Y` excepted, item 9) and **executes at the close of the next SPY trading day**. Until it executes, the previous holding is kept. The first decision of every candidate (and of every sensitivity) is taken at the close of 2004-12-31 and executes at the close of 2005-01-03, which is when the $100,000 is first invested. A decision whose execution day would fall after 2025-12-31 (the December 2025 month-end, the last week of 2025, the last day of the 200-day variant) is not taken. The benchmark is $100,000 × adj_t ÷ adj(2005-01-03), with no cost.
4. **Holdings and drift.** A candidate's book is a set of dollar balances: SPY, EFA, AGG, the simulated 2x fund (`2X`), and a T-bill balance `TB` that may be negative only under C2 (borrowing). Between executions every balance grows by its own daily return: SPY/EFA/AGG by their total return, `2X` by item 6, a positive `TB` by the T-bill accrual of item 5, a negative `TB` by (rate + 0.5%/yr) ÷ 252 (it owes more). Equity E_t = the sum of the balances at day t's close. At an execution close, after that day's returns, the target weights are applied to E_t.
5. **T-bills.** The annual rate used for day t is r_t = `DTB3` ÷ 100 from the latest non-blank FRED row dated **on or before the previous SPY trading day** (known before day t's session); its daily accrual is r_t ÷ 252 per SPY trading day (Friday to Monday is one day). `DTB3` is a discount-basis yield used as published. A window day on which the previous trading day's `DTB3` row is blank uses the last earlier value; such days are counted and listed; a carry-forward of more than 7 calendar days stops the run (no verdict).
6. **The simulated 2x fund.** Its daily return is 2 × SPY_t − (r_t + 0.005) ÷ 252 − 0.0091 ÷ 252, with r_t from item 5 (the instruction's "daily T-bill rate … /252" is read as the annual rate ÷ 252). Under the **1.5x** sensitivity the return is 1.5 × SPY_t − 0.5 × (r_t + 0.005) ÷ 252 − 0.0091 ÷ 252: the financing applies to the extra 0.5x only and the expense ratio is unchanged.
7. **C1 trend test.** At a month-end d: the 10-month SMA is the mean of SPY's adjusted close at the 10 most recent month-ends **including d**; the test is healthy when adj_d > SMA (strict). The **200-day** sensitivity decides at every trading day: healthy when adj_d > the mean of the 200 most recent adjusted closes including d; 2x when healthy, T-bills otherwise; it executes at the next close like every other decision.
8. **C2 exposure.** σ at a week's last close d = the sample standard deviation (ddof = 1) of SPY's 21 most recent daily total returns ending at d, × √252. Exposure e = min(1.5, target ÷ σ) with target 0.15 (sensitivities 0.12 and 0.18). The target weights are SPY = e and `TB` = 1 − e (negative when e > 1). C2 re-executes every week, so between executions its weights drift.
9. **C4 credit test.** For each SPY trading day t, c_t = the latest non-blank `BAA10Y` value whose FRED date is **strictly before** t. At a month-end d the test is healthy when c_d ≤ the mean of c_t over the 126 most recent SPY trading days ending at d, **including** d (sensitivities 63 and 252). A same-day `BAA10Y` value is never read. Window days whose previous SPY trading day has a blank `BAA10Y` row are counted and listed (carry-forward); a carry-forward of more than 7 calendar days stops the run.
10. **C3 momentum.** At a month-end d, a symbol's 12-month total return = adj_d ÷ adj at the month-end of the same calendar month one year earlier − 1. The T-bill 12-month return = the product of (1 + r_t ÷ 252) over the SPY trading days after that earlier month-end through d, minus 1. If SPY's 12-month return > the T-bill's (strict), hold the one of SPY and EFA with the higher 12-month return (a tie goes to SPY); otherwise AGG. Under the **ACWX** sensitivity, ACWX replaces EFA at every month-end where ACWX has both prices the 12-month return needs (from the April 2009 month-end on); EFA is used before that.
11. **C5.** Trend test = item 7's monthly test, credit test = item 9's 126-day test, both at the same month-end: both healthy → `2X` 100%; exactly one → SPY 100%; neither → `TB` 100%.
12. **Costs.** At an execution close the trade in each of SPY, EFA, AGG and `2X` is |target dollars − current dollars|, and the trade in T-bills is |max(target `TB`, 0) − max(current `TB`, 0)| (buying or selling T-bills is a trade; changing a borrowing is financing, not a trade). Cost C = rate × (sum of those trades), rate 2 bps (sensitivities 0 and 10 bps, applied to each of the five candidates). Targets are computed on E_t; C is then taken out by scaling every target balance by (E_t − C) ÷ E_t. The first execution (2005-01-03) buys from 100% `TB` and pays cost. A month-end that keeps the same single holding trades nothing and costs nothing.
13. **Metrics.** Daily return = E_t ÷ E_(t−1) − 1 for the window days after 2005-01-03. **CAGR** = (E_end ÷ E_start)^(365.25 ÷ calendar days from 2005-01-03 to 2025-12-31) − 1. **Total return** = E_end ÷ E_start − 1. **Volatility** = sample standard deviation of daily returns × √252. **Sharpe** = mean(daily return − r_t ÷ 252) ÷ sample standard deviation of the same × √252. **Maximum drawdown** = max over t of 1 − E_t ÷ (running maximum of E up to t), with the peak date, the trough date and the recovery date (the first close back at or above the peak; "not recovered" otherwise). **Calendar-year return** = E at the last close of the year ÷ E at the last close of the previous year − 1 (2005 from the 2005-01-03 close); the worst year is the lowest. **Time in T-bills or bonds** = the share of window days whose holding after execution is 100% `TB` or 100% AGG (for C2: the share of days with exposure < 1, and the mean exposure). **Trades per year** = executions with a non-zero traded amount ÷ 20.99 years (calendar days ÷ 365.25); **switches per year** (a change of holding) is reported for the monthly candidates. **Costs paid** = the sum of C in dollars.
14. **Criteria arithmetic.** Criterion 1 compares the CAGRs strictly. Criterion 2 is met when the candidate's maximum drawdown (a positive fraction) is ≤ SPY's. Criterion 3: a sub-period's return = E at its last trading day ÷ E at the last trading day before it − 1 (for the first sub-period, ÷ E at 2005-01-03); "beats" is strictly greater than SPY's over the same dates; at least 2 of the 3 are required.
15. **Only the exact pre-registered run yields a verdict:** the window, the default configuration of the five candidates, all 17 sensitivity runs (C1-200d, C1-1.5x, C2-12%, C2-18%, C3-ACWX, C4-63, C4-252, and each candidate at 0 and at 10 bps), and an unmodified pre-registration. Any other invocation is labelled NOT A PRE-REGISTERED RUN. The code refuses any date on or after 2026-01-01 with no override flag. Sensitivities report the same metrics and whether the three criteria would hold, for information only; they never change a verdict.
16. **SSO validation.** Over the SPY trading days from 2006-06-22 (SSO's first daily return) to 2025-12-31: the daily correlation of the simulated 2x return with SSO's adjusted-close return; per calendar year, the compounded simulated return − SSO's (2006 partial, reported but outside the average); the average of the 2007–2025 annual differences and of their absolute values, and the compounded totals. If the average annual difference lies outside ±1.5 points, the report says so in its first section. Descriptive only; nothing is retuned from it.
17. **Crisis close-ups.** Three windows fixed by calendar date: 2007-10-01 to 2009-12-31 (2008–09), 2020-02-01 to 2020-04-30 (Feb–Mar 2020 and its rebound), 2022-01-03 to 2022-12-30 (2022). For each candidate and SPY: the holding at each execution inside the window, the maximum drawdown inside the window (running peak reset at the window start), the return over the window, and the dates on which risk was cut (to T-bills/AGG, or C2's exposure below 0.5) and restored.
18. **The macro question** is answered with the default runs only: C5 vs C1 and C4 vs C1 on CAGR, maximum drawdown and the three close-ups, side by side, in words and numbers; no new run is made for it.
19. **Verifier.** A separate post-run program, importing nothing from the candidate modules, picks **12 random month-end decisions** (seed 11) among those of the window and re-derives from the raw cached files, for C1, C3, C4 and C5, the holding decided and the candidate's return from that execution close to the next month-end's execution close; for C2, the exposures decided at each week's last close in that span and the same span return. It compares them with the run's output (holding exactly; returns to 1e-9 relative). Full outputs go to `reports/core_candidates/<run-id>/` (gitignored).
20. **Holdout.** This study never runs it. If any candidate passes, the report names the one with the highest CAGR among the passes as the only one eligible, and stops.

## 3. Machine-readable parameters

```json
{
  "window": ["2005-01-03", "2025-12-31"],
  "first_decision": "2004-12-31",
  "start_equity": 100000,
  "cost_bps": 2.0,
  "financing_spread": 0.005,
  "lev_expense": 0.0091,
  "periods_per_year": 252,
  "c1": {"sma_months": 10, "leverage": 2.0},
  "c2": {"target_vol": 0.15, "cap": 1.5, "vol_window": 21},
  "c3": {"lookback_months": 12, "intl": "EFA", "bonds": "AGG"},
  "c4": {"credit_window": 126, "leverage": 2.0},
  "c5": {"sma_months": 10, "credit_window": 126, "leverage": 2.0},
  "sub_periods": [["2005-01-01", "2011-12-31"], ["2012-01-01", "2018-12-31"], ["2019-01-01", "2025-12-31"]],
  "sensitivities": ["C1-200d", "C1-1.5x", "C2-12", "C2-18", "C3-ACWX", "C4-63", "C4-252",
                    "C1-0bp", "C1-10bp", "C2-0bp", "C2-10bp", "C3-0bp", "C3-10bp", "C4-0bp", "C4-10bp", "C5-0bp", "C5-10bp"],
  "max_carry_forward_calendar_days": 7,
  "verifier": {"month_ends": 12, "seed": 11, "rel_tol": 1e-9},
  "holdout": ["2026-01-02", "2026-05-19"]
}
```
