# Day-trading research summary, 2026

**Status: the day-trading research program is paused.** Six pre-registered studies and two diagnostics are closed; none earned a holdout run, and none is a reason to trade. This page summarises the existing records (FOLLOWUPS #117–#124 and the locked pre-registrations in `docs/specs/`); it adds no new result.

## The six studies

All five day-trading studies use the 2024-01-02 to 2025-12-31 window, SIP prices for US instruments and a $25,000 sleeve; the wheel is a modeled 2005–2025 simulation. "Cost" is slippage plus commission (plus modeled spread haircut and fees for the wheel).

| Study (record) | Hypothesis | Verdict | Net Sharpe | Net P&L | Gross vs. cost |
| --- | --- | --- | ---: | ---: | --- |
| **ORB, Stocks in Play** (#117, #121; `ORB_Phase2_Preregistration`) | A 5-minute opening-range breakout on the 20 highest-relative-volume stocks has an edge | NO-GO | -10.41 | -$32,633 (7,862 trades; 55% stopped in the entry minute) | Gross -$15,858 vs. cost $16,775 |
| **SPY close-flow** (#119; `CloseFlow_SPY_Preregistration`) | SPY's move to 15:29 continues into the close | NO-GO | -1.39 | -$2,675 (502 trades) | Gross -$2,308; cost about $367 (derived) |
| **News gap** (#120; `News_Gap_Preregistration`) | A stock gapping 2%+ on overnight news continues in the gap direction | NO-GO | -0.12 | -$2,246 (2,464 trades) | Gross +$6,299 vs. cost $8,546 |
| **Global lead-lag** (#122; `Global_LeadLag_Preregistration`) | Asian and European moves predict the US day | NO-GO, all four groups | -0.68 to -0.96 | -$4,799 to -$12,457 | Broad: gross -$5,498, cost $151 (negative before costs in every group) |
| **ORB on QQQ** (#123; `ORB_QQQ_Preregistration`) | The same ORB on one liquid, high-priced ETF escapes the cost problem | NO-GO (Sharpe alone) | 0.46 (t 0.65) | +$1,478 (469 trades; +$1,116 in 2024, +$362 in 2025) | Gross $2,104 vs. cost $625 |
| **Safe wheel** (#124; `Safe_Wheel_Model_Preregistration`) | A modeled defined-risk SPY put-credit spread passes a screening gate | NOT WORTH REAL DATA | 0.18 | +$6,584 over 21 years (1.1% a year) | Gross of costs $32,353 vs. cost $25,769 |

## The diagnostics

* **ORB same-minute resolution (#121).** Replaying SIP trade prints for the 4,323 entry-minute stops moved the ORB result from -$32,633 to **-$8,737** (Sharpe -10.41 to -1.98): the original size was mis-measured, the verdict was not. **Gross +$8,038 against costs of $16,775.** The ambiguity explained 73% of the reported loss; the remaining gap is cost.
* **Options-data probe (#123).** Free Alpaca options data has no historical bid/ask (the quotes endpoint answers 404), only 13% (QQQ) and 18% (SPY) of the relevant puts print on a given day, and history starts in 2024. A defensible put-spread backtest needs a paid source; this is why the wheel was modeled.

## The lessons

1. **Costs decide everything at this scale.** ORB's tick-resolved gross edge (+$8,038) is half its cost ($16,775); the news gap's +$6,299 becomes -$2,246 after $8,546; the wheel's costs take about four fifths of its premium; close-flow and lead-lag are negative before costs. Only QQQ ORB is cost-light (costs are 3.4% of the risk taken), and its edge is thin.
2. **Tight-stop strategies on cheap stocks cannot survive realistic slippage.** A stop of 10–20 cents puts the round-trip cost at about 0.36R per trade and stops 55% of trades in their entry minute.
3. **News presence added nothing.** The same rule on gaps without news lost as much; the difference was +11.97 bps per trade, Welch t 0.67.
4. **Foreign moves predict the US open but not what follows.** The signal correlates with the US opening gap at +0.20 to +0.43, and with the rest of the day at zero or slightly negative (a weak, year-dependent reversal that was not tested as a rule).
5. **The model flatters option sellers by about 4 points a year.** Against Cboe's PUT index the modeled at-the-money put-writing earns 4.2 points a year more (compounded 631% vs. 255%, correlation 0.98), so a real-data test would be expected to look worse.
6. **IEX is fine for SPY/QQQ direction, not for stock selection.** IEX agrees with SIP on the sign of SPY's 15:29 return 99.8% of the time and on the direction of QQQ's opening bar 95.2% of the time, but the mean overlap of IEX and SIP top-20 stock lists is 9.1 of 20 (Phase 1 report, #116).

## What stays on record

* **ORB's gross edge on stocks** (+$8,038 on 7,862 trades after the tick-level resolution, about $1 per trade) and **QQQ ORB's thin positive** (Sharpe 0.46, t 0.65; Sharpe 0.60 with zero slippage). They are worth revisiting only with much larger capital or cheaper execution; QQQ ORB's t-statistic is 0.65, and no test of the ORB gross edge was pre-registered.
* **The 2026 January–September holdout remains unread** by every study (the data layer and every study refuse 2026 dates with no override). Each rule is locked by its pre-registration, so any change is a new pre-registration with a fresh test period.
  *Disclosure (2026-10-12):* a separate, non-research performance analysis of the Core book (`analysis/core-vs-spy`, FOLLOWUPS #125) read daily bars for SPY and other benchmark ETFs from 2026-05-20 onward through its own read-only function, so SPY's daily path in June–September 2026 is no longer unseen by this program. The data layer's refusal is unchanged, and no study has read the holdout.
* Live prerequisites for day trading (a dedicated Alpaca account and Key Vault secrets, #118) remain undefined and are not needed while the program is paused.
