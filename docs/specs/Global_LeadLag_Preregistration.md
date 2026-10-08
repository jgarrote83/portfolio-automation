# Global lead-lag study — pre-registration: do Asia, Europe and the UK predict the US day?

**Status: LOCKED at this document's first commit.** Written 2026-10-07, before any lead-lag code and before any lead-lag result exists, on top of commit `2e8e491dbbe3ae64fcf30321cf1ec88b228e9f7c` (branch `feat/global-leadlag`, created from `master` at that commit). Day trading runs in its own Alpaca account, independent of Core (`CLAUDE.md` ORB rule 4 as amended), so the US instruments below, which are Core roster names, are valid here.

Nothing below may change after the first real run. A change means a new pre-registration (a new file with its own first commit) and a fresh test period; the 2024–2025 window is then no longer an out-of-sample test of the changed rule. A dated note headed "Post-run clarification, no rule change" may be appended below; it is not part of the locked rule text (`backtest/provenance.py` hashes only the text above such a heading).

Data permissions for this study only (read-only): Alpaca market data through `backtest.data.get_bars`; public daily history from Yahoo Finance (`yfinance`). Stooq's free daily CSVs were also permitted; see item 15 for what happened to them. Nothing from 2026 is read from any source. No LLM is used anywhere in the study.

## 1. Questions

- **(Context, not tradeable)** How strongly do foreign overnight moves correlate with the US opening gap? US index futures trade overnight, so this is expected to be high and is **not** a trading signal.
- **(Tradeable, decides the verdict)** Does the foreign overnight move predict the US **open-to-close** return, i.e. continuation or reversal after the open?

## 2. The primary rule (the go/no-go is judged on this one only)

The groups, definitions and rules below are Jorge's, copied word for word from the instruction.

**Groups.** Fixed in advance; the US instrument is the only thing traded.

| Group | US instrument | Asia members: session close on US date D | Europe/UK members: open on US date D |
| --- | --- | --- | --- |
| Broad | SPY | ^N225, ^HSI, ^KS11, ^TWII, ^AXJO | ^GDAXI, ^FCHI, ^STOXX50E, ^FTSE |
| Semis | SMH | 2330.TW, 005930.KS, 8035.T | ASML.AS, IFX.DE |
| Banks | XLF | 8306.T, 0005.HK | HSBA.L, BNP.PA, DBK.DE |
| Energy | XLE | (none) | SHEL.L, BP.L, TTE.PA |

**Definitions** (US date D = a US trading day, 2024-01-02 to 2025-12-31):

- **Asia member return** = (close of the local session dated D) ÷ (close of its previous session) − 1. Every listed Asian session dated D closes before 09:30 ET on D; verify this per market, including daylight-saving changes, and record it.
- **Europe/UK member return** = (open on D) ÷ (close of its previous session) − 1. **Never use a European close on D** (those markets are still trading at the US open).
- **Group signal s** = the mean of the Asia-member mean and the Europe-member mean (each region weighted equally). If a region has no available member that day, use the other region alone. If neither has one, there is no signal.
- **US gap g** = (US open D) ÷ (US close D−1) − 1 and **US open-to-close** = (US close D) ÷ (US open D) − 1, from SIP daily bars (raw).

**Trading rule (per group, $25,000 each, independent).** s > 0 → long the US instrument at the opening auction (daily-bar open, i.e. a market-on-open order); s < 0 → short; s = 0 or no signal → no trade. Exit at the closing auction (daily-bar close). Shares = floor(25,000 ÷ open). No slippage on auction fills; $0.0035/share commission on both. Metrics as in earlier studies (no-trade days = 0, sample std, √252).

## 3. The go/no-go (mechanical, per group)

GO only if net Sharpe ≥ **1.0** **and** net P&L > $0 in **both** 2024 and 2025. Four groups are tested, so any GO is provisional: it still needs the 2026 holdout, with the multiple-testing note recorded (section 7, item 21).

## 4. Reported diagnostics (never decide the verdict)

- **(a) Correlations**, Pearson and Spearman with n, overall and per year, per group: s vs. g; s vs. open-to-close; the Asia-only and Europe-only signals vs. both; and g vs. open-to-close (does the US gap itself continue or reverse?).
- **(b) Regression** per group: open-to-close = a + b1·s + b2·g. Report b1 and b2 with **Newey-West (HAC)** t-statistics, which isolate whether foreign information adds anything **beyond** the US gap.
- **(c) Sensitivities:** Asia-only signal; Europe-only signal; long-only; 2¢/share slippage on both fills.

Nothing else.

## 5. Data (from the instruction)

- **Foreign daily OHLC** from Yahoo Finance (`yfinance`, added to `backtest/requirements.txt` only) for 2023-12-01 to 2025-12-31. Cache under `data/reference/foreign/` (gitignored). Use **unadjusted** opens and closes; record each member's **split events** and exclude member-days with a split.
- **Two-source check:** for 30 random member-days, compare open and close against Stooq's free daily CSVs. Report agreement (within 0.1%) and any member that fails; a member with under 90% agreement is excluded and listed.
- **Stale-open check:** per Europe/UK member, the share of days where open = previous close exactly. A member over 5% is excluded and listed (Yahoo index opens are sometimes stale).
- **Holidays:** a member missing on D is simply unavailable that day; count these.
- **US daily bars** through `backtest.data.get_bars` (SIP, raw, `1Day`). Refuse 2026 everywhere.

## 6. Known limitations, stated up front

* Yahoo is a free aggregator: its index opens can be stale, its history can be revised, and for the stocks the unadjusted close still carries ex-dividend drops (not corrected). Its quality is checked only as far as the rules in section 5 reach.
* **The second source could not be queried by script** (item 15), so the two-source check is, at the time of writing, NOT RUN. The rule below makes that impossible to miss in any verdict.
* The "auction" fills are the SIP daily bar's open and close, which are exactly the prices a market-on-open / market-on-close order is meant to get; a real auction fill in SMH, XLF or XLE can differ and is not modeled. No borrow cost for shorts.
* Four groups are tested on one 2-year sample with a Sharpe bar applied to each: any single GO is likely to be partly luck (item 21).
* Local-currency returns, no FX. Index members are price indices; stock members are not total-return adjusted.
* 2024–2025 is the only sample; the 2026 holdout is untouched and is closed to this study.

## 7. Implementation details interpreted (locked with the rest)

The instruction does not settle every detail; these are the readings made before any data was cached or any result existed.

1. **US calendar.** US trading days D are the days with SIP daily bars for SPY in 2024-01-02..2025-12-31 (the data layer's own calendar). D−1 is the previous US trading day (2023-12-29 for D = 2024-01-02). US prices come only from `backtest.data.get_bars(..., "1Day", "sip")` (raw). "US open" is the daily bar's `open`; "US close" its `close`.
2. **Foreign series.** One `yfinance` `Ticker(symbol).history(start="2023-11-25", end="2026-01-01", interval="1d", auto_adjust=False, actions=True)` per member (`end` is exclusive, so no 2026 bar is requested); rows dated before 2023-12-01 are discarded after download. The `Open`, `Close`, `Stock Splits` columns are used; `Adj Close` never. "Unadjusted" means `auto_adjust=False`: dividends are never applied, and Yahoo may still back-adjust Open/Close for splits, which the split rule (item 3) makes immaterial. A row's **local session date** is its calendar date in the exchange time zone Yahoo reports for that series. Downloads are cached as parquet with a metadata file (download time, `yfinance` version, rows, split events); a run reads only the cache. Any row dated on or after 2026-01-01 is a failure, never kept.
3. **Member-day and availability.** A member-day is (member, US date D). A member is **available** on D iff (i) it has a row whose local session date is D, (ii) the prices it needs are present and > 0 on that row and on its previous row (Asia: both closes; Europe/UK: the open on D and the previous row's close), (iii) no split event of that member has an ex-date s with previous-row date < s ≤ D, and (iv) the member has not been excluded by a data-quality rule (item 14). Otherwise it is unavailable on D; the reason is counted (no row dated D, missing price, split, excluded).
4. **"Previous session"** is the member's own previous row, however many calendar days earlier (weekends, holidays and long closures such as Lunar New Year are legitimate gaps; no gap guard is applied).
5. **Returns** are ratios minus one in decimal local currency. Asia: close(D) ÷ close(previous) − 1. Europe/UK: open(D) ÷ close(previous) − 1. A European close on D is never read.
6. **Regional means and the signal.** A region's value is the arithmetic mean of its available members' returns (equal weights within a region): s_A for Asia, s_E for Europe/UK. **s** = the mean of the available regional values (both: each weighted 1/2; one: that one alone; none: no signal). Energy has no Asian member, so its s is always s_E. Members excluded or unavailable are never replaced by another member. A signal with |s| below 5e-13 (s rounded to 12 decimals equals 0) is s = 0: no trade.
7. **US numbers.** g and the open-to-close return o2c are decimals from the SIP daily bars; both exist for every US trading day.
8. **Trades.** Long if s > 0, short if s < 0. Entry at the day's daily-bar open, exit at its daily-bar close; shares = floor(25,000 ÷ open) (a day with 0 shares is skipped and counted; it cannot occur for these ETFs). Gross P&L = ±shares × (close − open); commission = 0.0035 × shares × 2; net = gross − commission. Each group is an independent $25,000 sleeve, non-compounding, never levered.
9. **Slippage sensitivity.** 2¢ per share, adverse, on both fills (a long buys at open + 0.02 and sells at close − 0.02; a short the mirror). Shares are still sized on the un-slipped open.
10. **Metrics.** Daily sleeve return = day net P&L ÷ 25,000; every one of the 502 US trading days enters (no-trade days are 0); Sharpe = mean ÷ sample std (ddof 1) × √252, risk-free 0; also the t-statistic of the mean daily return, total and per-year net P&L (year = calendar year of D), max drawdown, trades, hit ratio (net P&L > 0), average net bps per trade (net P&L ÷ (shares × open) × 10,000), long and short counts, and the number of days with no signal and with no trade.
11. **Correlations (4a).** Pearson r and Spearman ρ (Pearson on average ranks, ties averaged) with n, on the days where both quantities exist, overall (2024 and 2025 pooled), for 2024 and for 2025. Pairs per group: (s, g); (s, o2c); (s_A, g); (s_A, o2c); (s_E, g); (s_E, o2c); (g, o2c). The (g, o2c) pair uses every US trading day of the group's instrument, not only signal days. n below 3 is reported as undefined.
12. **Regression (4b).** OLS of o2c on a constant, s and g over the days where s exists, in decimals. HAC (Newey-West) covariance with the Bartlett kernel, L = floor(4·(n/100)^(2/9)) lags, no small-sample correction; t = coefficient ÷ HAC standard error. Reported: b1, b2 and their HAC t, the constant, n, L and R².
13. **Sensitivities (4c).** Each is the primary rule with one change and is reported with the same metrics, never against the thresholds: Asia-only (s_A is the signal; no signal when the Asia region is empty), Europe-only (s_E), long-only (shorts skipped), 2¢ slippage (item 9). No other variant exists.
14. **Data-quality exclusions** are applied before any signal is built and exclude a member from every use in every variant. (i) *Stale open* (Europe/UK members only, since only they use the open): over the member's rows whose local date is in 2024-01-02..2025-12-31 and that have a previous row, the share where open equals the previous close after rounding both to 6 decimals; a share above 5% excludes the member. (ii) *Two-source check*, item 15. (iii) Splits, item 3. Holidays (no row dated D) and missing prices are counted per member and per reason.
15. **Two-source check.** For each member, 30 dates are drawn at random (a fixed seed, 7) from the US dates in the window on which the member has a row; Yahoo's open and close for that row are compared with Stooq's daily CSV for the same local date. A field **agrees** if |Yahoo − Stooq| ÷ Stooq ≤ 0.001; a sampled day **agrees** if every field that member's return uses agrees (Asia: the close; Europe/UK: the open and the close); the member's **agreement** is the share of its sampled days that agree; below 90% excludes the member and it is listed. Both fields are reported for every member. **Stooq access (probed 2026-10-07, before any study data was cached):** Stooq's CSV endpoint answers every request, including for a known-good symbol such as `aapl.us`, with an HTTP 200 page that says "This site requires JavaScript to verify your browser" and carries a proof-of-work script, not a CSV. That is an anti-bot gate; **it is not solved or worked around.** The check therefore reads Stooq CSV files placed by hand in `data/reference/foreign/stooq/<Yahoo symbol>.csv` (columns Date, Open, High, Low, Close, optionally Volume, as Stooq's own "download data" produces in a browser) and fetches nothing. A member with no such file is **unchecked**: it is kept (a check that cannot run cannot fail a member) and listed as unchecked. Every group verdict states how many of its members were checked; a group that meets the section 3 thresholds while any member it used is unchecked is reported as **"GO, UNVERIFIED DATA"** and is not to be acted on until the check has run. A NO-GO is a NO-GO regardless.
16. **Session timing (the instruction's "verify this per market").** For each market the regular-session close (Asia) or open (Europe/UK) is converted to UTC with `zoneinfo` for **every** US date D in the window, so daylight saving on both sides is covered, and compared with 09:30 ET on D. Asia passes iff the close is before the US open on every D; Europe/UK passes iff the open is before the US open on every D. The nominal local times used are the conservative ones and are printed in the report: Tokyo 15:30 (the exchange moved its close from 15:00 to 15:30 on 2024-11-05; 15:30 is used for all dates), Hong Kong 16:10 (closing auction), Seoul 15:30, Taipei 13:30, Sydney 16:20 (closing auction), London 08:00, Frankfurt 09:00, Paris 09:00, Amsterdam 09:00, Zurich 09:00 (the STOXX 50 index; Yahoo reports that zone). The same table shows, for each European market, that its close on D falls AFTER the US open on every D, which is why a European close is never used. This verifies nominal exchange hours, not Yahoo's timestamps (daily rows carry only a date).
17. **Groups and members are fixed.** An excluded or unavailable member lowers availability; it is never substituted, and no member or group is added or dropped after the fact.
18. **Window and holdout.** Window 2024-01-02..2025-12-31, foreign data from 2023-12-01. The code refuses any date on or after 2026-01-01 for US and foreign data alike, with no override flag.
19. **Only the exact pre-registered run yields a verdict**: default config, the full window, all four groups, all sensitivities, an unmodified pre-registration. Any other invocation is labeled NOT A PRE-REGISTERED RUN.
20. **Randomness.** The only random draws are the two-source sample (seed 7) and the post-run verifier's trade-day sample (30 trade-days of the primary rule across the four groups, seed 11).
21. **Multiple-testing note.** Four groups are tested at the same thresholds on the same two years, each group's Sharpe bar uncorrected. A group that meets the bar is one of four draws; the report prints the note beside every GO, and no GO is final without the 2026 holdout, which is closed to this study.
22. **Context versus decision.** The correlation of s with g (section 4a) is context: US futures trade overnight, so a high value there is expected and not a signal. The verdict rests only on section 3.
23. **Plain-language summary** states how strongly foreign moves line up with the US open (the s–g correlations) and whether they predict anything after the open (the s–o2c correlations, the regression's b1 and the verdicts), without a recommendation.

## 8. Machine-readable parameters

`tests/test_leadlag.py` asserts that `backtest.leadlag.LeadLagConfig()` equals this block and that its groups equal the table in section 2, so the code cannot drift from the document unnoticed.

```json
{
  "spec_version": "leadlag-1.0",
  "window_start": "2024-01-02",
  "window_end": "2025-12-31",
  "data_from": "2023-12-01",
  "sleeve_capital_usd": 25000.0,
  "commission_per_share": 0.0035,
  "slippage_cents": 0.0,
  "sensitivity_slippage_cents": 2.0,
  "min_net_sharpe": 1.0,
  "stale_open_max_share": 0.05,
  "agreement_tolerance": 0.001,
  "agreement_min_share": 0.9,
  "agreement_days_per_member": 30,
  "agreement_seed": 7,
  "verifier_trade_days": 30,
  "verifier_seed": 11,
  "groups": {
    "Broad": {"us": "SPY", "asia": ["^N225", "^HSI", "^KS11", "^TWII", "^AXJO"], "europe": ["^GDAXI", "^FCHI", "^STOXX50E", "^FTSE"]},
    "Semis": {"us": "SMH", "asia": ["2330.TW", "005930.KS", "8035.T"], "europe": ["ASML.AS", "IFX.DE"]},
    "Banks": {"us": "XLF", "asia": ["8306.T", "0005.HK"], "europe": ["HSBA.L", "BNP.PA", "DBK.DE"]},
    "Energy": {"us": "XLE", "asia": [], "europe": ["SHEL.L", "BP.L", "TTE.PA"]}
  }
}
```

## 9. What a run reports

The verdict per group (with its data-verification status); the correlation tables (4a); the regression (4b); the primary strategy table and the three sensitivities per group (4c); the session-timing table; the data-quality results (two-source agreement or "unchecked", stale-open shares, split events and exclusions, holiday and unavailability counts by reason); and a short plain-language summary. Full outputs go to `reports/global_leadlag/<run-id>/` (gitignored), with the code commit SHA and the pre-registration hash in the header.
