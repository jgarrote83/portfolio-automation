# Safe wheel (SPY put-credit spread), modeled 20-year screening backtest: pre-registration of the rules and the screening gate

**Status: LOCKED at this document's first commit.** Written 2026-10-11, before any wheel-model code and before any wheel-model result exists, on top of `master` commit `be06111d21e21764ac0b35b0fd2d6f04746fd8de` (branch `feat/safe-wheel-model` was created from that commit). Rules 6 and 9 of `CLAUDE.md` ("ORB program") apply, and rule 4 as amended (day-trading and options studies run in their own account, independent of Core, so SPY and QQQ are allowed). This study uses no live account and no order endpoint.

Nothing below may change after the first real run. A change means a new pre-registration (a new file, with its own first commit); the 2005–2025 window is then no longer an unseen test of the changed rule. A dated note headed "Post-run clarification, no rule change" may be appended below; it is not part of the locked rule text (`backtest/provenance.py` hashes only the text above such a heading). Every run's header records this file's path, the SHA of the commit that introduced it, and whether its rule text still matches its first-commit hash.

## 0. What this step is, and is not

The options-data probe (`.review/options-data-probe.md`, FOLLOWUPS #123) showed that free Alpaca options data has no historical bid/ask, sparse daily prints and a history that starts in January 2024. Step 1 therefore **models** option prices with Black-Scholes from the VIX and SPY's daily prices over 2005–2025, which covers 2008, 2020 and 2022, the events that decide whether a short-put strategy is survivable.

**This is a screening test.** Its only possible outcomes are **WORTH REAL DATA** or **NOT WORTH REAL DATA**. It can never approve trading. A pass leads to step 2: the same pre-registered rules on paid historical bid/ask data, under a new pre-registration.

**What was looked at before this document was written (availability only; no strategy number of any kind had been computed).** FRED's CSV endpoint accepts `cosd`/`coed` date parameters, so the series can be requested up to 2025-12-31 and never beyond. `yfinance` returns 5,535 daily rows for each of SPY and QQQ for 2004-01-02 to 2025-12-31 with no stock split in either. Cboe's `PUT_History.csv` downloads from the public CDN without any gate, but its **daily rows start on 2007-01-03** (earlier rows are isolated dates years apart), so the PUT-index calibration can cover 2007–2025 only, not 2005–2025. `VIXCLS` has a value on every SPY trading day; `VXNCLS` lacks only 2004-09-10 (outside the window); `DTB3` is blank on 41 SPY trading days (bond-market holidays such as Columbus Day and Veterans Day) and is carried forward. The third Fridays of 2005–2025 that are not SPY trading days are exactly 2008-03-21, 2014-04-18, 2019-04-19, 2022-04-15 and 2025-04-18 (Good Fridays). These are descriptions of the data, not tuning inputs; the rules below were fixed by the instruction.

## 1. The primary variant (the screening gate is judged on this one only)

The rules and thresholds below are Jorge's, copied word for word from the instruction.

**Strategy: defined-risk SPY put-credit spread** (the "safe wheel": the long put caps every loss; there is never assignment or share ownership).

| Rule | Value |
| --- | --- |
| Window | 2005-01-03 to 2025-12-31 (development); 2026 never read |
| Underlying | SPY. Options modeled as European; positions are closed before expiry (below), so early assignment is out of scope and disclosed |
| Expiries | Standard monthly expiries (third Friday; if that's a market holiday, the prior trading day) |
| Entry day | The trading day closest to 35 calendar days before the expiry (ties go to the earlier day). One position at a time; if one is still open, skip that cycle |
| Short put | Strike with model delta closest to −0.20, rounded to the nearest $1 |
| Long put | Short strike minus a width w = 2% of spot, rounded to the nearest $1 (minimum $1) |
| Pricing | Black-Scholes. Spot = SPY unadjusted close. Risk-free = FRED `DTB3` (3-month T-bill) ÷ 100. Dividend yield q = trailing 12-month SPY dividends ÷ spot. **IV(K) = VIX/100 + skew × max(0, ln(S/K)) × 100 / 100**, with **skew = 0.5 vol points per 1% out of the money** (primary). VIX = FRED `VIXCLS` close |
| Costs | Each leg, each side: $0.03 per share haircut against the model price (sell below, buy above) plus $0.65 per contract fees. So entry credit = model credit − 0.06, and exit debit = model debit + 0.06 |
| Exit | First of: (a) spread marked at ≤ 50% of the entry credit (profit-take, at that day's model price + costs); (b) the close **7 calendar days before expiry** (or the prior trading day) |
| Sizing | Fixed $25,000 sleeve, non-compounding. Risk budget = **10% of the sleeve ($2,500) per position**. Contracts = floor(2,500 ÷ ((w − entry credit) × 100)); 0 → skip and count |
| Kill switch | If marked sleeve equity falls **20% below its running peak**, open no new positions for 3 calendar months (an open position runs to its normal exit), then resume |
| Marking | Daily, at the model price, for profit-take, drawdown and the kill switch |

## 2. The screening gate (mechanical)

All four must hold, over 2005–2025, net of costs, on the primary variant:

1. Net Sharpe (daily marked returns, √252, sample std) ≥ **0.5**
2. Maximum drawdown ≤ **30%** of the sleeve
3. Worst calendar year ≥ **−20%** of the sleeve
4. Net P&L positive in **at least two-thirds** of calendar years

All four hold → **WORTH REAL DATA**; anything else → **NOT WORTH REAL DATA**. No other number, and no other variant, enters the verdict. The Sharpe and the ratios are compared unrounded.

## 3. Reported sensitivities (never decide the gate)

Skew 0 and 1.0; cost haircut $0.01 and $0.06 per leg per side; no profit-take (hold to the 7-day exit); short delta −0.10 and −0.30; no kill switch; **QQQ with VXN** (FRED `VXNCLS`) instead of SPY with VIX. Nothing else. Each changes exactly one thing relative to the primary (the QQQ variant changes the underlying and its volatility index together, which is one change).

## 4. Data, checks and what a run reports

**Sources.** FRED CSV (`https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>&cosd=2004-01-01&coed=2025-12-31`) for `VIXCLS`, `VXNCLS` and `DTB3`; `yfinance` (`auto_adjust=False`, `actions=True`, `end` exclusive 2026-01-01) for SPY and QQQ daily OHLC and dividends from 2004-01-01. Cached under `data/reference/wheel/` (gitignored). Nothing dated on or after 2026-01-01 is requested, stored or used, anywhere.

**Checks (reported; none gates the verdict).** (i) A two-source price check: yfinance SPY closes against Alpaca SIP daily closes (through `backtest.data.get_bars`) over 2016–2025: agreement within 0.05%, with every disagreement listed. (ii) A calendar check: the expiry list against third Fridays, with every holiday move listed. (iii) A model sanity check against Cboe's **PUT index** (cash-secured monthly at-the-money put writing): downloaded read-only from Cboe's public data file if the file is available; the same simple strategy is modeled and the model's yearly returns are reported against PUT's, with the correlation and the mean annual difference; if the file is not downloadable the report says so and no substitute is used.

**A run reports:** the verdict and the four gate checks; the headline table for every variant (net Sharpe, CAGR on the sleeve, total net P&L, maximum drawdown and when it happened, worst and best calendar year, share of profitable years, number of positions, win rate, average credit and average maximum loss per contract, kill-switch activations with dates and duration); a year-by-year table for the primary; a crisis close-up; the PUT calibration; the checks above; and a limitations section.

## 5. Implementation clarifications (locked with the rest)

The instruction fixes the rules above but not every detail a deterministic run needs. These choices are made now, before any result, and are locked like the rest. Each is an interpretation, listed so it can be challenged before the first real run.

1. **Trading days** are the dates of the underlying's yfinance daily rows (the exchange-local session date). The window is the days from 2005-01-03 to 2025-12-31; rows from 2004-01-01 are used only for look-backs (dividends). The primary's days are SPY's; the QQQ variant's are QQQ's (identical in the data).
2. **Expiries.** For every month from February 2005 to December 2025 the third Friday; if it is not a trading day, the latest trading day before it. A cycle takes part only if its entry day is inside the window **and** its scheduled time-exit day (item 9) is on or before 2025-12-31, so that nothing after 2025-12-31 is ever needed. This drops the January 2005 expiry (its entry would be in December 2004) and the January 2026 expiry (its exit would be in January 2026, and 2026 is never read); 251 cycles remain. Cycles dropped for the window are listed, not counted as skips.
3. **Entry day** = the trading day, chosen among **all** trading days with data (including those of 2004, so that a cycle whose target falls before the window is dropped by item 2 rather than slid to the first window day), minimising |day − (expiry − 35 calendar days)|, ties to the earlier day. The entry happens at that day's close, using that day's closes.
4. **Order of events on a day.** Every trading day, in this order: (1) the open position (if any) is marked at that day's model price; (2) it is exited if a rule fires (item 9); (3) equity is updated, the running peak is updated and the kill switch is evaluated (item 12); (4) if the day is an entry day, the entry decision is taken. Exits are therefore processed **before** the entry decision of the same day, so a position that exits at the close of an entry day does not block the next cycle's entry on that day. This matters: when two consecutive expiries are 28 days apart, the next cycle's entry day (expiry − 35) is exactly the old position's time-exit day (expiry − 7). "If one is still open, skip that cycle" therefore applies only to a position still open **after** step (2); with the 7-day exit it should never fire, and the report counts it (an expected zero).
5. **Model.** Black-Scholes-Merton for a European put with a continuous dividend yield: P = K·e^(−rT)·N(−d2) − S·e^(−qT)·N(−d1), d1 = [ln(S/K) + (r − q + σ²/2)·T] / (σ·√T), d2 = d1 − σ·√T. T = (expiry date − day) in calendar days ÷ 365. r = DTB3 ÷ 100 as a continuously compounded annual rate; q = (sum of cash dividends with ex-date in the 365 days ending on the day, inclusive of the day) ÷ the day's unadjusted close. S = the underlying's unadjusted close that day (`auto_adjust=False` 'Close'). The model delta of a put is Δ = −e^(−qT)·N(−d1), computed with the strike's own σ(K). Model prices are not rounded to cents.
6. **Implied volatility.** σ(K) = V/100 + skew × max(0, ln(S/K)), where V is the day's VIX (or VXN) close and skew is in volatility units per unit of ln(S/K). The instruction's "× 100 / 100" converts ln(S/K) to percent out of the money and the volatility points to decimals: with skew = 0.5 vol points per 1% out of the money, a strike 10% out of the money (ln(S/K) = 0.10536) adds 0.5 × 10.536 = 5.27 vol points = 0.0527. A strike at or above the spot adds nothing. The same σ(K) is used for the delta search at entry and for every daily mark, with the **current** day's S and V; there is no term structure (one 30-day volatility index is applied to every maturity from 35 to 7 days) and no smile beyond this linear term.
7. **Short strike.** Whole-dollar strikes K = 1, 2, …, floor(S) are scanned at the entry day; the short strike is the one whose delta is closest to the target (−0.20 primary), ties to the lower strike. Scanning whole dollars is the instruction's "rounded to the nearest $1".
8. **Long strike and width.** w = max(1, round-half-up(0.02 × S)) in whole dollars, where S is the entry-day close; the long strike is the short strike − w. If the long strike would be below $1 the cycle is skipped and counted.
9. **Entry credit and exits.** With h = $0.03 (primary), the **entry credit per share** is c = P(short) − P(long) − 2h, evaluated at the entry day; if c ≤ 0 or c ≥ w the cycle is skipped and counted. The daily **mark** of the spread is M = P(short) − P(long) at that day's model prices (no haircut). From the first trading day **after** the entry: **profit-take** fires when M ≤ 0.5 × c (the trigger is judged on the model mark, as the instruction says marking is at the model price; the fill then costs the haircut); the **time exit** is the latest trading day on or before (expiry − 7 calendar days) and happens at that day's close whether or not the profit-take fired (reason `time_exit`; if both conditions hold the reason is `profit_take`). The exit debit per share is M + 2h.
10. **Sizing, fees and P&L.** Contracts n = floor(2,500 ÷ ((w − c) × 100)) with c net of the haircut; n = 0 is skipped and counted. Fees are $0.65 per contract per leg per side: 2 × 0.65 × n at entry and the same at exit. A position's **net P&L** = (c − (M_exit + 2h)) × 100 × n − 4 × 0.65 × n. Average **max loss per contract** is reported as (w − c) × 100 (fees excluded) and average credit per contract as c × 100.
11. **Equity, daily returns and metrics.** Equity E_t = 25,000 + cumulative realized net P&L (net of all costs, entry fees paid on the entry day) + the open position's unrealized P&L (c − M_t) × 100 × n at the model mark (the entry haircut is therefore a loss on the entry day; exit costs fall on the exit day). The sleeve is fixed and non-compounding; idle cash earns nothing. **Daily return** = (E_t − E_(t−1)) ÷ 25,000 for every trading day in the window, with E before the first day = 25,000, so days without a position are zeros and the first day is a zero. **Net Sharpe** = mean ÷ sample standard deviation (ddof = 1) × √252, risk-free 0. **Max drawdown** = the largest (running peak of E, including the initial 25,000 − E_t) ÷ 25,000; the running peak used for the reported drawdown is **never reset**; it is reported in dollars, as a share of the sleeve (the gate quantity) and as a share of the peak, with the peak date and the trough date. **Calendar-year P&L** = the sum of the daily changes in E within that calendar year (marked, so a position open across New Year is split by its marks); worst and best year, and "positive" means strictly greater than zero dollars, are judged on it; the gate compares each year's P&L ÷ 25,000 with −20% and requires 3 × (positive years) ≥ 2 × (number of calendar years, 21). **CAGR on the sleeve** = (E_end ÷ 25,000)^(365.25 ÷ calendar days from the first to the last day) − 1. **Win rate** = positions with net P&L > 0 ÷ positions. The t-statistic of the mean daily return is reported as information only.
12. **Kill switch.** After steps (1)–(2) of item 4, the running peak P = max(P, E_t) (P starts at 25,000). The switch **activates** on the first day with E_t ≤ 0.80 × P while no pause is running. From the activation day a the pause lasts until the resume date a + 3 calendar months (same day of the month, clipped to the month's end): no entry is taken on any entry day d with a ≤ d < the resume date (those cycles are skipped and counted; they are not deferred); an open position runs to its normal exit. On the first trading day on or after the resume date, **after steps (1)–(2), P is reset to E_t** (otherwise the unchanged peak would re-activate the switch on the same day, so "then resume" would be empty), and the switch can activate again only on a fresh 20% fall below the new P. The switch is not evaluated during a pause. This kill-switch peak is separate from the never-reset peak of item 11 that defines the reported drawdown.
13. **Missing values.** A trading day on which `VIXCLS`/`VXNCLS` or `DTB3` has no value takes the last earlier value (carry forward), and every such day is listed in the report with the number of days stale; a carry-forward of more than 5 calendar days stops the run with an error (no verdict). Dividends missing from a window are zero. A day on which a computation cannot be carried out (a non-positive close, σ ≤ 0) stops the run.
14. **Variants.** Each sensitivity changes only the named field of the primary configuration (section 6). The QQQ variant uses QQQ's closes, dividends and trading days and `VXNCLS`, with the same rules (width 2% of the QQQ spot, rounded, minimum $1; so in 2005, at about $37, the $1 minimum makes the width about 2.7% of spot).
15. **Only the exact pre-registered run yields a verdict**: the window, the default configuration, all ten variants (the primary and nine sensitivities) and an unmodified pre-registration. Any other invocation is labelled NOT A PRE-REGISTERED RUN. The code refuses any date on or after 2026-01-01 with no override flag.
16. **Two-source price check.** yfinance SPY closes are compared with Alpaca SIP daily closes (raw) for every date in 2016-01-01 to 2025-12-31 present in both; "agree" means |a − b| ÷ b ≤ 0.0005. The report gives the number of dates compared, the maximum difference, and lists every disagreement (date, both closes, the difference) and every date present in one source only. The Alpaca side is fetched through `get_bars` with a holdout-guarded end of 2025-12-31.
17. **Calendar check.** The report lists, for every expiry, the third Friday, the expiry used and whether it was moved, and states whether the moved set equals the Good-Friday set (March 2008, April 2014, April 2019, April 2022, April 2025). Over 2016–2025 the trading days are also compared with the Alpaca-derived SPY days. This is a check, not a rule: the expiry rule is item 2.
18. **PUT-index calibration.** The Cboe file is a whole-history file with no date parameter; its rows dated on or after 2026-01-01 are received in the response and **dropped by the parser at once, never stored, printed or used** (disclosed, since the file cannot be requested by range). Its daily rows start on 2007-01-03. The simple strategy modeled (no skew change, **no costs**, since the index has none): at the close of each standard monthly expiry date s (item 2's expiry list, from the first with a PUT value on both the roll date and the next one), sell one at-the-money put: strike K = the underlying's close rounded half-up to the whole dollar, expiring at the next expiry e, priced by items 5–6 with the primary skew; cash-secured by K in T-bills. The period return is R = r × T + (P − max(K − S_e, 0)) ÷ K with r = DTB3 ÷ 100 at s, T = (e − s) ÷ 365, S_e the underlying's close on e (the premium's own interest is ignored). PUT's return for the same period is PUT(e) ÷ PUT(s) − 1. Periods run from the first expiry on or after 2007-01-03 to the last whose end is on or before 2025-12-31. **Yearly return** of a series for year Y = the compounded product of (1 + R) over the periods whose end date falls in Y, minus 1, for both series over the same periods; PUT's own December-to-December return is shown beside it for context. Reported: the yearly table, the Pearson correlation of the yearly returns and of the period returns, the mean annual difference (model − PUT) and the mean absolute annual difference, and the compounded total of each. Descriptive only; it never enters the verdict and nothing is retuned from it.
19. **Crisis close-up.** The three windows are fixed here by calendar date: 2008-09-01 to 2009-03-31, 2020-02-01 to 2020-04-30, and 2022-01-03 to 2022-10-14 (the 2022 drawdown, defined by the first trading day of 2022 and mid-October). A position is "open during" a window if its entry date ≤ the window's end and its exit date ≥ the window's start. For each: entry, expiry, the strikes, spot and VIX at entry, contracts, credit per share, the worst marked loss (the lowest unrealized P&L over the holding days, dollars, before exit costs), the exit date and reason, and the net P&L. Cycles skipped inside the window are listed with their reasons.
20. **Verifier.** The post-run verifier re-prices **30 random positions** (a fixed seed, 11; all of them if there are fewer) from the raw cached inputs with separate code that imports nothing from the strategy module and computes the normal distribution function by a different formula. Per position it re-derives the expiry, the entry day, the strikes by its own scan, the credit, the contracts, the daily marks, the exit day and reason and the net P&L, and compares them with the run's output. Full outputs go to `reports/safe_wheel_model/<run-id>/` (gitignored).
21. **Idle-cash and rate treatment.** No interest is credited on the idle part of the sleeve (conservative); the Sharpe is computed with a zero risk-free rate, as in the earlier studies. Margin and collateral are not modeled: the maximum loss of a position is at most $2,500 before fees and is assumed to be funded from the sleeve.

## 6. Machine-readable parameters of the primary variant

`tests/test_safe_wheel.py` asserts that `backtest.safe_wheel.WheelConfig()` equals this block, that the gate constants equal the gate block, and that the variants are exactly the primary and those in section 3, each changing exactly the fields listed, so the code cannot drift from the document unnoticed.

```json
{
  "spec_version": "safe-wheel-model-1.0",
  "config": {
    "underlying": "SPY",
    "vol_index": "VIXCLS",
    "window_start": "2005-01-03",
    "window_end": "2025-12-31",
    "data_from": "2004-01-01",
    "entry_calendar_days_before_expiry": 35,
    "exit_calendar_days_before_expiry": 7,
    "short_delta": -0.2,
    "width_pct_of_spot": 2.0,
    "min_width_usd": 1.0,
    "skew_vol_points_per_pct_otm": 0.5,
    "haircut_per_share_per_leg": 0.03,
    "fee_per_contract": 0.65,
    "profit_take_enabled": true,
    "profit_take_fraction": 0.5,
    "sleeve_capital_usd": 25000.0,
    "risk_budget_pct_of_sleeve": 10.0,
    "multiplier": 100,
    "kill_switch_enabled": true,
    "kill_switch_drawdown_pct": 20.0,
    "kill_switch_pause_months": 3,
    "days_in_year": 365
  },
  "gate": {
    "min_net_sharpe": 0.5,
    "max_drawdown_pct_of_sleeve": 30.0,
    "min_worst_year_pct_of_sleeve": -20.0,
    "min_positive_year_fraction_numerator": 2,
    "min_positive_year_fraction_denominator": 3
  },
  "sensitivities": [
    {"name": "skew_0", "changes": {"skew_vol_points_per_pct_otm": 0.0}},
    {"name": "skew_1", "changes": {"skew_vol_points_per_pct_otm": 1.0}},
    {"name": "haircut_0.01", "changes": {"haircut_per_share_per_leg": 0.01}},
    {"name": "haircut_0.06", "changes": {"haircut_per_share_per_leg": 0.06}},
    {"name": "no_profit_take", "changes": {"profit_take_enabled": false}},
    {"name": "delta_0.10", "changes": {"short_delta": -0.1}},
    {"name": "delta_0.30", "changes": {"short_delta": -0.3}},
    {"name": "no_kill_switch", "changes": {"kill_switch_enabled": false}},
    {"name": "qqq_vxn", "changes": {"underlying": "QQQ", "vol_index": "VXNCLS"}}
  ],
  "verifier": {"seed": 11, "positions": 30},
  "calibration": {"index": "PUT", "first_daily_row": "2007-01-03"}
}
```

Window and holdout: `2005-01-03` to `2025-12-31`. The code refuses any date on or after `2026-01-01` for every source.

## 7. What a run reports

Section 4's list. The verdict is reported and the work stops: no tuning, no extra variants, no 2026 data, however the numbers look.
