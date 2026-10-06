# News event study — pre-registration: does a news-driven gap continue through the day?

**Status: LOCKED at this document's first commit.** Written 2026-10-05, before any news-gap code and before any news-gap result exists, on top of commit `e7d7a88b0c241678861a01eaec567762f6c66e62` (branch `feat/news-event-study`, created from `master` at that commit). Day trading runs in its own Alpaca account, independent of Core (CLAUDE.md rule 4 as amended), so the Core-roster exclusion does not apply. **No LLM is used anywhere in this study.**

Nothing below may change after the first real run. A change means a new pre-registration (a new file with its own first commit) and a fresh test period; the 2024–2025 window is then no longer an out-of-sample test of the changed rule. A dated note headed "Post-run clarification, no rule change" may be appended after a run; the hash check in every run header covers the text above such a heading, so any edit to a rule is detected. Every run's header records this file's path, the SHA of the commit that introduced it, and whether its rule text still matches its first-commit hash.

## 1. Hypothesis

For large, liquid stocks, a price gap at the open that is accompanied by overnight company news continues in the gap's direction from 10:00 to the close. Gaps without news do not, and may reverse.

## 2. The primary rule (the go/no-go is judged on this one only)

The rules and thresholds below are Jorge's, copied word for word from the instruction.

| Rule | Value |
| --- | --- |
| Window | Trading days 2024-01-02 to 2025-12-31. SIP bars, raw prices. Alpaca news (Benzinga). Nothing from 2026 is read |
| Universe (day D) | Day-D open ≥ $10; prior-20-day average dollar volume (close × volume, prior days only) ≥ $50M; not an ETF (the Phase 2 Nasdaq Trader list); has daily bars for D−1 and D |
| Gap | g = (day-D open) ÷ (day-(D−1) close) − 1; qualifies if \|g\| ≥ 2% |
| Overnight news | At least one Alpaca news article whose `symbols` list contains the stock **and has at most 2 symbols**, with `created_at` in [16:00 ET on D−1, 09:30 ET on D). Use `created_at` only, never `updated_at` |
| Event | Universe ∧ qualifying gap ∧ overnight news |
| Selection | Rank day D's events by \|g\| descending (ties by symbol ascending); take the **top 5** |
| Direction | Sign of g: gap up → long, gap down → short. **Shorts allowed** (borrow is not modeled; disclose) |
| Entry | Open of the 1-min bar stamped 10:00, plus slippage. No bar at 10:00 → skip and count; never substitute another bar |
| Exit | The day's daily-bar close (market-on-close proxy); no slippage on the exit |
| Sizing | Fixed $25,000 sleeve, non-compounding, 5 slots of $5,000; shares = floor(5,000 ÷ entry price); unused slots stay idle; never levered |
| Stop | None |
| Costs | $0.0035/share commission on entry and exit, plus **2¢/share slippage on the entry** |
| Metrics | Daily sleeve return = day net P&L ÷ $25,000; days with no event count as 0; Sharpe = mean ÷ sample std × √252, risk-free 0 |

## 3. The go/no-go (mechanical)

GO only if net Sharpe ≥ **1.0** over 2024–2025 **and** net P&L > $0 in **both** years. Anything else is NO-GO.

"Net" means after commission and slippage. The Sharpe is compared unrounded; "> $0" means strictly greater than zero dollars. The verdict is computed on the primary variant only; no diagnostic enters it.

## 4. Reported diagnostics (never decide the verdict)

- **(a) The control.** The identical rule on qualifying gaps with **no** overnight news (top 5 by \|g\|). Report it side by side with the primary, and report the **difference** in average net return per trade between news and no-news gaps, with a t-statistic. This is the core test of whether news adds information.
- **(b) Confirmation filter.** Trade an event only if the 09:30–10:00 move (10:00 bar open vs. day open) has the same sign as g.
- **(c) Long-only.**
- **(d) Entry slippage** of 0, 1 and 5¢.
- **(e) Breakdown by event type,** reporting only: classify each event's overnight headlines with a fixed, case-insensitive keyword list, written into the pre-registration before any run. Earnings: earnings, EPS, results, revenue, quarter, Q1–Q4. Guidance: guidance, outlook, forecast, raises, lowers, cuts. Analyst: upgrade, downgrade, price target, initiates. Deal: acquire, acquisition, merger, buyout, takeover. Regulatory: FDA, approval, trial, phase. Offering: offering, priced, dilution. Anything else is "other"; an event can carry several types. Report trades, average net bps and hit ratio per type.

Nothing else: no sweeps, no other thresholds.

Each diagnostic changes exactly one thing relative to the primary (the control changes the news condition; (b), (c) add one filter; (d) is three variants at 0¢, 1¢ and 5¢). Seven variants run in all: the primary, the control, (b), (c) and the three slippage levels; (e) is a breakdown of the primary's trades, not a variant.

## 5. Known limitations, stated up front

News text may have been edited after publication (report the share of articles whose `updated_at` differs from `created_at`); survivorship in the asset list; ETF list as of 2026-10-04; borrow not modeled.

Further limitations that follow from the rules as written (not additions to them): prices are **raw**, so stock splits and reverse splits appear as very large "gaps" and are not removed (section 6, item 2; the results list the ten largest \|g\| events with their P&L so the effect is visible); the t-statistic in (a) treats trades as independent although the trades of one day share the market (item 25).

## 6. Implementation details interpreted (locked with the rest)

The instruction fixes the rules above but not every detail a deterministic study needs. These choices are made now, before any result, and are locked like the rest. Each is an interpretation, listed so it can be challenged before the first real run.

**Calendar and data**

1. **Trading days** are the days SPY has a SIP daily bar (the Phase 1/2 and close-flow convention). D−1 is the previous trading day in that series; for 2024-01-02 it is 2023-12-29, so daily bars are fetched from 2023-12-01 (20 prior trading days) and news from 2023-12-29.
2. **Raw prices.** All bars are unadjusted. A split or reverse split on day D makes day-D's open differ from day-(D−1)'s close by the split ratio; that is a "gap" under the rule as written, is ranked by \|g\| like any other, and is **not** removed. The intraday move from 10:00 to the close is unaffected (both prices are post-split).
3. **Data** comes only through `backtest.data.get_bars` (SIP, raw: daily bars, and one-minute bars requested with a window of 10:00–10:01 for the selected symbols and days only) and `backtest.data.get_news` (Alpaca `GET /v1beta1/news`, read-only). The only other inputs are the Nasdaq Trader ETF files already in `data/reference/` (SHA-256 `171f3d1a4ef7f6d5ebc2c8e64e77e02bcb661a7573b5f1ff0ff182cf68ca0ccd` for `nasdaqlisted.txt` and `858406d16b357c83a02d6c63b8b7a3ee21bde53b85c3a29d7b85d28cdcf07bec` for `otherlisted.txt`, downloaded 2026-10-04, re-verified before this document was written) and the cached assets list (fetched 2026-10-05).
4. **The holdout:** the code refuses any bar or news date on or after 2026-01-01 and has no override flag in this study.
5. **News fetch.** Per ET calendar day from 2023-12-29 through 2025-12-31 (weekends and holidays included, because an overnight window can span them), no symbol filter, `sort=asc`, the maximum page size, `include_content=false`; each article is stored with only `id`, `created_at`, `updated_at`, `headline`, `symbols`, `source`, de-duplicated by `id`. No filter on `source` is applied (the share by source is reported). If the endpoint answers 401 or 403 the work stops and is reported; it is not worked around.

**Universe, gap and news**

6. **The asset universe** is every active and inactive US equity on NYSE, NASDAQ, AMEX, ARCA and BATS (the Phase 1/2 definition; OTC excluded) minus ETFs (the Phase 2 Nasdaq Trader list; a symbol in neither file is treated as not an ETF). The Core-roster exclusion of the earlier backtests does not apply here (rule 4 as amended), so AMZN, GOOGL, INTC and MCK, which those backtests excluded, are in the universe; their daily bars are fetched if absent from the cache.
7. **Day-D open** is the `open` of the SIP raw daily bar of D; **day-(D−1) close** is the `close` of the SIP raw daily bar of D−1. Both daily bars must exist. "Open ≥ $10" is inclusive.
8. **Average dollar volume** is the arithmetic mean, over the 20 trading days D−20 … D−1, of (daily close × daily volume). All 20 daily bars must exist, otherwise the stock is not in the universe that day. "≥ $50M" is inclusive ($50,000,000).
9. **g** = open_D ÷ close_(D−1) − 1, and the gap qualifies if \|g\| ≥ 0.02 (inclusive); \|g\| is rounded to 12 decimals before the comparison so float noise at exactly 2% cannot flip it.
10. **The overnight window** is [16:00:00 ET on D−1, 09:30:00 ET on D): the start is inclusive and the end exclusive, so an article stamped exactly 16:00:00 on D−1 counts and one stamped exactly 09:30:00 on D does not. D−1 is the previous *trading* day, so a Monday's window includes the weekend. `created_at` is stored in UTC and converted to ET with the date's own offset (daylight-saving aware).
11. **A matching article** has the stock's ticker as an exact (upper-cased) member of its `symbols` list and a `symbols` list of at most 2 entries, counted as delivered (an empty list never matches). Tickers are matched as written, with no normalisation of class-share punctuation.
12. **Event** = in the universe ∧ qualifying gap ∧ at least one matching article. **Control** = in the universe ∧ qualifying gap ∧ no matching article.

**Selection and trading**

13. **Selection** is per day and per set (events; separately, controls): \|g\| descending, ties by symbol ascending, the first 5. Fewer than 5 means fewer trades; unused slots stay idle.
14. **The top-5 cut comes first.** An event that is then skipped (no 10:00 bar, fewer than one share) or removed by a diagnostic filter ((b), (c)) is **not** replaced by the sixth.
15. **Direction:** g > 0 long, g < 0 short (g = 0 cannot qualify). Shorts are assumed available and free of borrow cost.
16. **Entry:** the `open` of the one-minute bar stamped 10:00, moved against the trade by the slippage (a long buys at open + slippage, a short sells at open − slippage; slippage = cents ÷ 100 per share). On half-days 10:00 is an ordinary minute. Only that bar's open is used for entry; nothing later in the session informs a decision.
17. **Shares** = floor(5,000 ÷ the 10:00 bar's un-slipped open), with a 1e-9 tolerance against float noise; slippage does not enter sizing; fewer than one share is a skip.
18. **Exit** at the `close` of the day's SIP raw daily bar, no slippage; commission $0.0035 per share on the entry and again on the exit. Gross P&L is measured at the un-slipped open; slippage cost = shares × cents ÷ 100 (entry only); net = gross − slippage − commission, reported separately.
19. **One position per stock per day**, flat by the close, no stop, no compounding: the sleeve is $25,000 every day.

**Diagnostics**

20. **(a) the control** applies the primary rule unchanged (the top-5 of no-news gaps, the same entry, exit, sizing and costs). **Average net return per trade (bps)** = the mean over executed trades of net P&L ÷ (shares × un-slipped entry open) × 10,000. The reported **difference** is (news mean − no-news mean). Its **t-statistic is Welch's two-sample t** on the per-trade values: (m1 − m2) ÷ √(s1²/n1 + s2²/n2), with sample variances, and both trade counts shown.
21. **(b) the confirmation filter** is applied after the top-5 cut: an event trades only if sign(10:00 bar open − day-D daily open) equals sign(g) (equal prices count as not confirming). Removed events are not replaced. The control in (a) is the primary rule's control and does not use (b).
22. **(c) long-only** turns short-direction events into no-trade (not replaced).
23. **(d) entry slippage** 0¢, 1¢ and 5¢ replace the primary's 2¢ (three variants).
24. **(e) the event-type breakdown** classifies the primary's executed trades using the headlines of **every matching article in the overnight window** (item 11). Matching is case-insensitive on the exact keywords listed in section 4: the all-capital tokens EPS, FDA and Q1, Q2, Q3, Q4 match as **whole words**; every other keyword matches at the **start of a word** and may be followed by further letters (so "acquire" matches "acquires" and "acquired", "quarter" matches "quarterly", "upgrade" matches "upgraded", "price target" matches "price targets", "trial" matches "trials"; there is no stemming beyond that, and "cut" is not "cuts"). An event with several types is counted once in each; "other" means none of the six types matched. Per type: trades, average net bps and hit ratio (net P&L > 0). The control has no articles and so no breakdown.
25. **Statistical caveat:** the t-statistic in (a) and the t-statistic of the mean daily return treat their observations as independent; trades on the same day share the market, so both overstate precision. This is disclosed, not corrected.

**Metrics and reporting**

26. **Sharpe** = mean ÷ sample standard deviation (ddof = 1) × √252 of the daily sleeve returns over **every trading day in the window**, days without an event or trade counting as 0; risk-free 0. Undefined (zero variance) is reported as n/a and is NO-GO. **Per-year net P&L** is the sum over the calendar year; **max drawdown** is on the cumulative net P&L added to the $25,000 sleeve; **hit ratio** = share of executed trades with net P&L > 0; **t-statistic of the mean daily return** = mean ÷ (sample std ÷ √N), N = every trading day in the window.
27. **Days with no event** = trading days on which no stock was an event (before the top-5 cut); days with no trade are counted separately. **Events per day** (mean and median) counts events before the top-5 cut, over all trading days in the window.
28. **Data statistics** reported: articles fetched (total, by source, with at most 2 symbols, with an empty symbols list), the share with `updated_at` ≠ `created_at` over all fetched articles **and** over the matching overnight articles of the primary's events, events per day, and every skip with its reason (no 10:00 bar, zero shares, no daily-bar data).
29. **Large gaps:** the ten primary events with the largest \|g\|, with direction and net P&L (disclosure only, see section 5).
30. **Run validity:** only the exact pre-registered invocation (window 2024-01-02..2025-12-31, default configuration, the full variant set, this file's rule text unchanged since its first commit) produces a verdict; any other invocation is labelled **NOT A PRE-REGISTERED RUN** and prints none.

## 7. Machine-readable parameters of the primary rule

`tests/test_news_gap.py` asserts that `backtest.news_gap.NewsGapConfig()` equals this block, and that the variants are exactly those in section 4, so the code cannot drift from the document unnoticed.

```json
{
  "spec_version": "newsgap-1.0",
  "kind": "news",
  "min_open": 10.0,
  "dollar_volume_days": 20,
  "min_avg_dollar_volume": 50000000.0,
  "min_abs_gap": 0.02,
  "max_news_symbols": 2,
  "news_window_start": "16:00",
  "news_window_end": "09:30",
  "top_n": 5,
  "sleeve_capital_usd": 25000.0,
  "slots": 5,
  "entry_time": "10:00",
  "entry_slippage_cents": 2.0,
  "commission_per_share": 0.0035,
  "allow_shorts": true,
  "require_confirmation": false
}
```

Window and holdout: `2024-01-02` to `2025-12-31`; news from `2023-12-29`. The code refuses any date on or after `2026-01-01`.

## 8. What a run reports

For every variant: net Sharpe, t-statistic of the mean daily return, total and per-year net P&L, max drawdown, trades, hit ratio, average net bps per trade, long vs. short, and days with no event; for the primary, the mechanical verdict; the news vs. no-news comparison (a), prominently; the event-type breakdown (e); the data statistics (item 28) and the largest-gap disclosure (item 29); and a post-run verifier, with code separate from the study's, that re-derives 50 random primary trades and 50 random control trades from the raw cached bars and news. The verdict is reported and the work stops: no tuning, no added variants, no 2026 data, however the numbers look.
