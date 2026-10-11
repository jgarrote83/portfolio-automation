# ORB on QQQ — pre-registration of the backtest and its go/no-go rule

**Status: LOCKED at this document's first commit.** Written 2026-10-12, before any QQQ-ORB code and before any QQQ-ORB result exists, on top of `master` commit `1d4a07ed107a55d5b216b811d7c3bc203be22c60` (branch `feat/orb-qqq` was created from that commit). Spec: `docs/specs/ORB_Engine_v1.0.md`. Rules 6 and 9 of `CLAUDE.md` ("ORB program") apply, and rule 4 as amended: day trading runs in its own Alpaca account, independent of Core, so QQQ (a Core roster name) is allowed here.

Nothing below may change after the first real run. A change means a new pre-registration (a new file, with its own first commit) and a fresh test period; the 2024–2025 window is then no longer an out-of-sample test of the changed rule. A dated note headed "Post-run clarification, no rule change" may be appended below; it is not part of the locked rule text (`backtest/provenance.py` hashes only the text above such a heading). Every run's header records this file's path, the SHA of the commit that introduced it, and whether its rule text still matches its first-commit hash.

## 0. Why, and what is inherited

ORB v1 on Stocks in Play was NO-GO (`docs/specs/ORB_Phase2_Preregistration.md`, `.review/phase2-results.md`). Two features of that test stand out: 55% of trades were stopped in their entry minute (decided by the conservative same-bar rule; `.review/phase2-samebar-resolution.md` bounds this), and costs were about 0.36R per trade because a 10%-of-ATR stop on a typical stock is only 10–20 cents. On QQQ (around $500) the same stop is roughly 50–80 cents, so costs shrink to a few percent of R. This tests the same strategy on one liquid, high-priced ETF.

**Everything not listed in section 1 is inherited unchanged from `docs/specs/ORB_Phase2_Preregistration.md`**, including its section 5 (implementation clarifications 1–18, except where section 5 below says a clarification is replaced or not applicable) and the daily loss limit of 3% of the sleeve. The verified engine is reused, not rewritten: `backtest.engine.simulate_day` with `orb.signals`, `orb.sizing`, `orb.universe.atr_simple` and `orb.config.OrbConfig`. (The instruction names `orb.sim`; no module of that name exists, and the engine is the one above.) The engine's optional `entry_bar_stop` hook (merged to `master` with FOLLOWUPS #121; `None` is the pre-registered same-bar rule byte for byte, proved on all 7,862 Phase 2 trades) is used only for sensitivity (c).

## 1. The primary variant (the go/no-go is judged on this one only)

The rules and thresholds below are Jorge's, copied word for word from the instruction.

| Rule | Value |
| --- | --- |
| Window | Trading days 2024-01-02 to 2025-12-31. SIP data, raw prices. 2026 is never read |
| Instrument | **QQQ only**: no universe filter, no Relative Volume ranking. Trade every day the rules allow |
| Direction | First 5-min bar (09:30) up → long only; down → short only; doji → no trade. Shorts allowed |
| Entry | Stop at the 5-min high (long) or low (short), live from the 09:35 bar through 15:45; fill at the level, or at the bar's open on a gap |
| Stop | 10% of the 14-day ATR (simple mean of the prior 14 true ranges) from the trigger price, rounded away from the entry; entry and stop in the same minute bar ⇒ stop hit |
| Exit | Inherited from Phase 2: close of the last 1-min bar at or before 15:59; half-days per the verified early-close table |
| Sizing | Fixed $25,000 sleeve, non-compounding; shares = floor(min(1% of sleeve ÷ stop distance, 25,000 ÷ trigger)); integer shares; never levered |
| Costs | $0.0035/share commission per fill plus **1¢/share slippage per side**, adverse on every fill |
| Metrics | As in Phase 2 (daily sleeve return, no-trade days 0, sample std, √252) |

## 2. The go/no-go (mechanical)

GO only if, on the primary variant, net Sharpe ≥ **1.0** over 2024–2025 **and** net P&L is positive in **both** 2024 and 2025. Anything else is NO-GO. "Net" means after commission and slippage; "positive" means strictly greater than zero dollars; the Sharpe is compared unrounded. No other number, and no other variant, enters the verdict.

## 3. Reported sensitivities (never decide the verdict)

- (a) slippage of 0, 2 and 5¢ per side;
- (b) long-only;
- (c) **same-bar optimistic:** in the entry bar, the stop is not checked; it is checked from the next bar onward. This **bounds** the same-minute ambiguity; it is not a rule.

Nothing else. Each changes exactly one thing relative to the primary variant.

## 4. Reported statistics and checks (never decide the verdict)

The results report: the verdict; a headline table per variant (net Sharpe, t-statistic of the mean daily return, per-year net P&L, max drawdown, trades, hit ratio, average net R and average net bps, long vs. short, exits by reason, gap entries); **the share of trades stopped in the entry minute** for the primary and for sensitivity (c); the mean stop distance in dollars and in basis points, and costs as a fraction of R; and an **IEX vs. SIP check** (per day, does the IEX 09:30 five-minute bar give the same direction, and how far apart are the opening-range high and low, in cents). Definitions are in section 5, items 13–16.

## 5. Implementation clarifications (locked with the rest)

The instruction fixes the rules above but not every detail a deterministic run needs. These choices are made now, before any result, and are locked like the rest. Each is an interpretation, listed so it can be challenged before the first real run.

1. **One pick per day.** QQQ is the only candidate: no price, volume or ATR filter, no ETF exclusion, no Core-roster exclusion (rule 4 as amended), no Relative Volume (the `rvol` field the engine carries is set to 0.0 and is never read). Phase 2 clarifications 2, 3, 5 and 7 (top-20 selection, ranking, Relative Volume, the asset list) therefore do not apply; 1, 4, 6, 8–13 and 15–18 do.
2. **Trading days** are the days SPY has a SIP daily bar in the window (the Phase 1 convention). The first window day needs 14 prior trading days, so QQQ daily bars are fetched from 2023-12-01.
3. **ATR** is `orb.universe.atr_simple` over QQQ's 14 prior daily bars (all 14 must exist; the previous close of the oldest may be missing, in which case that true range is high − low), exactly as in Phase 2 clarification 6. A day on which the ATR cannot be computed is skipped and counted; nothing is substituted.
4. **The opening five-minute bar** is built from QQQ's SIP one-minute bars stamped 09:30, 09:31, 09:32, 09:33 and 09:34 (the instruction names the one-minute data): open = the 09:30 bar's open, high = the maximum of the five highs, low = the minimum of the five lows, close = the 09:34 bar's close. If any of the five is absent the day is skipped and counted; no bar is substituted. A non-gating consistency check compares this aggregate with Alpaca's own SIP five-minute bar stamped 09:30 for every day and reports any difference.
5. **Direction** is `orb.signals.direction_from_bar` on that bar: close above open → long; below → short; equal (a doji) → no trade, counted. One entry attempt per day; there is no re-entry after a stop-out (Phase 2 clarification 1).
6. **Trigger, stop, entry and exit mechanics** are `orb.signals` and `backtest.engine.simulate_day` unchanged (Phase 2 clarifications 8, 9 and 11): entry orders live for the bars stamped 09:35 through 15:45 (12:45 on a half-day), fill at the level or the bar's open on a gap; the stop is `trigger ∓ 0.1 × ATR` rounded to the cent away from the entry, checked in the entry bar itself (stopped at the stop level) and from the next bar on (at the level, or the bar's open if it opens beyond it); an open position exits at the close of the last one-minute bar stamped at or before 15:59 (12:59 on a half-day), a stale last close if the 15:59 bar is absent. The half-day table is `backtest.sessions.EARLY_CLOSE_DATES` (2024-07-03, 2024-11-29, 2024-12-24, 2025-07-03, 2025-11-28, 2025-12-24).
7. **Sizing** is `orb.sizing.position_size` with the sleeve as the position cap: `position_slots = 1`, so the cap is `25,000 ÷ trigger` shares and the risk limit is `1% × 25,000 ÷ stop distance`. Price = the trigger level; stop distance = |trigger − rounded stop| (Phase 2 clarification 10). **Arithmetic worth knowing, stated before the run:** at a stop distance of 50–80 cents the risk limit allows 300–500 shares while the cap allows about 50 shares, so the position cap should bind on essentially every trade, and the realized risk per trade is about 0.10–0.16% of the sleeve rather than 1%. This is the rule as instructed, not a bug; which limit binds is recorded per trade and reported.
8. **Costs.** Slippage of 1¢ per share is applied adversely to every fill (a buy fills 1¢ higher, a sell 1¢ lower), whether entry, stop, time exit or loss-limit flatten; commission is $0.0035 per share on every fill. Gross P&L is measured at the un-slipped fill prices; net = gross − slippage − commission (Phase 2 clarification 12).
9. **Daily loss limit** is inherited unchanged (−3% of the sleeve, −$750, evaluated at every bar's close). With one position of at most 0.2% of the sleeve at risk it can fire only after a gap through the stop; if it never fires the report says so.
10. **Metrics** are `backtest.reports.compute_metrics` unchanged: every window day enters (days without a trade, including skipped days, count as 0), Sharpe with the sample standard deviation (ddof = 1) × √252, risk-free 0, per-year P&L by calendar year, max drawdown on the $25,000 sleeve plus cumulative net P&L (Phase 2 clarification 15). The t-statistic of the mean daily return is mean ÷ (std ÷ √n).
11. **Sensitivities.** (a) changes only `slippage_cents_per_side`; (b) changes only `allow_shorts`; (c) passes `simulate_day` an `entry_bar_stop` rule under which the entry bar's stop is never checked (from the next bar onward it is, at the level or the bar's open on a gap), with the primary configuration otherwise. Entering and being stopped in the same bar is then impossible by construction, so (c)'s share of trades **stopped in the entry minute is zero by definition**; the quantity worth reporting beside it is the share of entries whose entry bar reaches the stop level (which the rule ignores), reported for both the primary and (c).
12. **Only the exact pre-registered run yields a verdict**: the window, the default configuration, all six variants and an unmodified pre-registration. Any other invocation is labelled NOT A PRE-REGISTERED RUN. The code refuses any date on or after 2026-01-01 with no override flag.
13. **Share stopped in the entry minute** = trades whose exit reason is `stop_same_bar` ÷ trades. **Entry bar reaches the stop** = entries whose entry-minute bar has low ≤ stop (long) or high ≥ stop (short), ÷ trades.
14. **Stop distance** is reported as the mean of |trigger − stop| in dollars and as the mean of |trigger − stop| ÷ trigger in basis points, over the primary's trades. **Costs as a fraction of R** is reported two ways: total costs (slippage + commission, entry and exit) ÷ total risk (Σ shares × stop distance), and the mean over trades of (slippage + commission) ÷ (shares × stop distance). **Average net R** is the mean of net P&L ÷ (shares × stop distance) (R is not capped); **average net bps** is the mean of net P&L ÷ (shares × entry fill) × 10,000.
15. **IEX vs. SIP check.** IEX one-minute bars for QQQ for the window 09:30–09:35 are fetched through `get_bars(feed="iex")` and aggregated like the SIP bar from the minutes present (open of the earliest, close of the latest, highest high, lowest low). A day with no IEX bar in that window is counted separately. **Direction agrees** when the IEX bar's sign (up, down, flat) equals the SIP bar's. The range comparison reports IEX high − SIP high and IEX low − SIP low in cents (an IEX bar sees a subset of trades, so its high cannot exceed and its low cannot undercut the SIP bar's): mean, median, 90th and 99th percentile of the absolute difference, the maximum, and the share of days that are exactly equal; and how many minute bars of the five IEX supplies. It is descriptive only: IEX data never enters any trade.
16. **Missing data.** A day with no QQQ one-minute bars at all cannot trade and is counted; days skipped for the ATR (item 3), the opening bar (item 4) or a doji (item 5) are listed in the report with their counts.
17. **Short availability and borrow cost are not modeled.** QQQ is among the most easily borrowed securities, and this is stated rather than assumed away.
18. **Verifier.** The post-run verifier re-derives 50 random primary trades (a fixed seed, 11; all of them if there are fewer) from the raw cached bars with naive code that imports nothing from the strategy modules. Full outputs go to `reports/orb_qqq/<run-id>/` (gitignored).
19. **Capital is fixed** at $25,000 every day, whatever the past P&L (Phase 2 clarification 16).

## 6. Machine-readable parameters of the primary variant

`tests/test_orb_qqq.py` asserts that `backtest.orb_qqq.qqq_config()` equals this block, that every field not in `changed_from_phase2` equals the Phase 2 pre-registered value, and that the variants are exactly those in sections 1 and 3, so the code cannot drift from the document unnoticed.

```json
{
  "spec_version": "orb-qqq-1.0",
  "symbol": "QQQ",
  "config": {
    "lookback_days": 14,
    "min_price": 5.0,
    "min_avg_volume": 1000000,
    "min_atr": 0.5,
    "rvol_min": 1.0,
    "top_n": 20,
    "opening_window_start": "09:30",
    "opening_window_end": "09:35",
    "entry_start": "09:35",
    "entry_cutoff_minutes_before_close": 15,
    "exit_minutes_before_close": 1,
    "stop_atr_fraction": 0.1,
    "sleeve_capital_usd": 25000.0,
    "risk_per_trade_pct": 1.0,
    "position_slots": 1,
    "daily_loss_limit_pct": 3.0,
    "commission_per_share": 0.0035,
    "slippage_cents_per_side": 1.0,
    "allow_shorts": true,
    "exclude_etfs": true,
    "tick_size": 0.01
  },
  "changed_from_phase2": ["position_slots", "slippage_cents_per_side"],
  "unused_in_qqq_run": ["min_price", "min_avg_volume", "min_atr", "rvol_min", "top_n", "exclude_etfs"],
  "sensitivities": ["slippage_0c", "slippage_2c", "slippage_5c", "long_only", "same_bar_optimistic"]
}
```

Window and holdout: `2024-01-02` to `2025-12-31`. The engine and this run refuse any date on or after `2026-01-01`.

## 7. What a run reports

Section 4's list, for every variant, plus the mechanical go/no-go verdict for the primary variant, the sizing-limit counts, the skipped-day counts and the consistency check of item 4. The verdict is reported and the work stops: no tuning, no added variants, no 2026 data, however the numbers look.
