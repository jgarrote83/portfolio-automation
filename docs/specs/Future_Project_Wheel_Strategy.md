**Future Project: Options Wheel Strategy**

Azure-Native Portfolio Automation System

PLACEHOLDER — May 2026 — Separate project, not part of current Phases 1-2

*STATUS: This document is a placeholder capturing the concept and requirements for a future options income strategy. It is explicitly NOT part of the current Phase 1 (analysis pipeline) or Phase 2 (paper execution pipeline). This project should only be considered after the core system has been running successfully for 90+ days and the account holder has obtained margin and options approval.*

# 1. Concept Overview

The wheel strategy is an options income strategy where you systematically sell cash-secured puts and covered calls to collect premium income at every stage of a stock's movement. You act as the insurance company — collecting premiums from other traders who are buying protection.

The strategy works in a continuous cycle (the 'wheel'): sell puts to get paid while waiting to buy a stock at a price you choose, then if assigned, sell calls on the shares you now own to collect more premium while waiting to sell at a higher price. When shares are called away, the cycle restarts.

The appeal is consistent income generation regardless of market direction — you collect premiums whether the stock goes up, down, or sideways. The risk is being assigned shares of a stock that drops significantly, leaving you holding a losing position. This is why stock selection and strike price discipline are critical.

# 2. How the Wheel Works

## 2.1 Stage 1: Selling cash-secured puts

**The setup: **You pick a stock you'd be happy to own (e.g., Tesla at $250). You sell a put option with a strike price below the current price (e.g., $230 strike). Someone pays you a premium (e.g., $5/share = $500 per contract) for the right to sell you the stock at $230.

**If the stock stays above $230: **The contract expires worthless. You keep the $500 premium. You can sell another put next week and collect another premium. This is the 'getting paid to wait' phase.

**If the stock drops below $230: **You get assigned — you must buy 100 shares at $230. But you already collected $500, so your effective cost basis is $225. You bought the stock at a discount to where it was trading, and you wanted to own it anyway. Move to Stage 2.

## 2.2 Stage 2: Selling covered calls

**The setup: **You now own 100 shares (at $225 effective cost). You sell a call option with a strike price above your cost basis (e.g., $260 strike). Someone pays you another $5/share = $500 for the right to buy your shares at $260.

**If the stock stays below $260: **The contract expires worthless. You keep the shares AND the $500 premium. You can sell another call next week. This is the 'getting paid while holding' phase — you generate income even if the stock doesn't move.

**If the stock rises above $260: **Your shares get called away (sold at $260). Your total profit: $35/share stock gain ($260 - $225) + $5 put premium + $5 call premium = $45/share = $4,500 per contract. Go back to Stage 1 and start the wheel again.

## 2.3 The continuous cycle

Each complete rotation of the wheel generates income at every stage. In sideways markets, you collect premiums repeatedly without assignment. In rising markets, you capture both stock gains and premiums. In falling markets, you acquire stocks at discount prices with premium cushion. The only scenario that hurts is a severe, sustained decline in a stock you were assigned — which is why you only run the wheel on stocks you'd genuinely want to own long-term.

# 3. Prerequisites

This project requires several conditions to be met before any development begins. All prerequisites must show 'Complete' status before proceeding.

| Prerequisite | Status | Details |
| --- | --- | --- |
| Margin account approved | Not started | E*TRADE margin application; requires separate approval from cash account |
| Options trading approval (Level 2+) | Not started | Must be approved for selling cash-secured puts and covered calls |
| Phase 1 running clean for 60+ days | Not started | Core analysis pipeline must be validated before adding execution complexity |
| Phase 2 paper trading validated for 30+ days | Not started | Alpaca paper execution must demonstrate reliable operation |
| Sufficient capital for 100-share lots | To evaluate | Wheel requires buying 100 shares per contract; ~$20K-$30K per position at typical prices |
| Options knowledge and comfort level | Learning | Must understand assignment risk, Greeks, rolling, early assignment before going live |
| Alpaca options paper trading available | To verify | Confirm Alpaca paper account supports options orders; may need alternative broker |

**Critical: **Do not attempt to automate options trading without a thorough understanding of assignment risk, early exercise, dividend risk, and the Greeks (delta, theta, gamma, vega). Claude can help with the mechanics, but the risk management decisions must be yours.

# 4. Risk Comparison

The wheel strategy has a fundamentally different risk profile from the current portfolio management system. This table highlights the key differences to inform the decision about whether and when to proceed.

| Aspect | Current System (Phases 1-2) | Wheel Strategy (Future) |
| --- | --- | --- |
| Position sizing | Flexible; buy any number of shares | Fixed 100-share lots per contract; larger capital commitment |
| Risk type | Directional risk on holdings | Assignment risk + directional risk on assigned shares |
| Income model | Capital appreciation + dividends | Premium income at every stage; income even in flat markets |
| Management frequency | Daily analysis; trades when needed | Weekly contract management; rolling, strike selection, expiration monitoring |
| Worst case | Stock declines; unrealized loss on position | Assigned shares at strike; stock drops significantly below cost basis |
| Complexity | Buy/sell/trim equity positions | Put selling, covered call selling, rolling, assignment handling, Greeks monitoring |
| Automation difficulty | Straightforward; REST API equity orders | Complex; options chain analysis, strike selection, roll timing, assignment detection |

## 4.1 Key risks specific to the wheel

**Assignment risk: **When you sell a put and the stock drops significantly, you're obligated to buy 100 shares at the strike price regardless of how far the stock has fallen. If you sold a $230 put on Tesla and it drops to $150, you're buying at $230 — an immediate $8,000 loss per contract, offset only partially by the premium collected.

**Opportunity cost: **When you sell a covered call and the stock rockets upward, your shares are called away at the strike price. You miss the upside above the strike. If you sold a $260 call and Tesla goes to $400, you sold at $260 and missed $140/share in additional gains.

**Capital lock-up: **Each wheel position ties up enough capital to buy 100 shares. At $250/share, that's $25,000 per position. Running the wheel on 3 stocks means $75,000+ in committed capital that can't be used for other opportunities.

**Early assignment: **American-style options can be exercised at any time before expiration. While rare, early assignment can disrupt your planned timing, especially around ex-dividend dates.

**Liquidity risk: **If you sell options on a stock with low options volume, you may get poor fills (wide bid-ask spreads) and have difficulty closing positions early.

# 5. Preliminary Architecture

If this project proceeds, it would be built as a separate set of Azure resources alongside the existing portfolio system, sharing the same storage account and Key Vault but using dedicated Function Apps and Logic Apps for options-specific logic.

| Component | Purpose | Notes |
| --- | --- | --- |
| func-pfauto-wheel-analyzer | Strike selection + contract analysis | Claude evaluates optimal strike prices and expirations based on IV, delta, and fundamentals |
| func-pfauto-wheel-executor | Options order placement | Places sell-to-open puts and calls via broker API; handles assignment detection |
| func-pfauto-wheel-monitor | Position monitoring + rolling | Checks positions every 15 min; closes at 50% profit; rolls contracts approaching expiration |
| logic-pfauto-wheel-approval | Human approval gate | Teams adaptive card showing proposed contract, premium, max risk, Greeks before execution |
| WheelCycleHistory (table) | Cycle tracking + performance | Tracks each wheel rotation: premiums collected, assignment events, total return per cycle |

## 5.1 What Claude would do

**Stock selection: **Using the same fundamentals, macro, sentiment, and congressional data from the existing pipeline, Claude would identify stocks suitable for the wheel — strong companies you'd want to own long-term, with sufficient options liquidity and favorable volatility characteristics.

**Strike selection: **Claude would analyze the options chain (from E*TRADE) to select optimal strike prices based on delta (probability of assignment), implied volatility (premium richness), and your desired risk/reward profile. Typical targets: 0.20-0.30 delta puts (70-80% probability of expiring worthless), 0.20-0.30 delta calls for covered calls.

**Expiration selection: **2-4 weeks out typically offers the best theta decay (time value erosion) relative to risk. Claude would evaluate weekly vs monthly expirations based on current IV environment.

**Roll decisions: **When a contract approaches expiration in-the-money, Claude would recommend whether to let it expire (accept assignment or shares called away) or roll to a later date and/or different strike to collect additional premium and avoid assignment.

**Profit-taking: **If a sold option reaches 50% of max profit before expiration, Claude would recommend closing it early (buying it back) to capture most of the premium while freeing capital for the next contract.

**Risk management: **Claude would never recommend selling a put without sufficient cash to cover assignment, never sell a call below cost basis (which would lock in a loss), and would flag when a position's loss exceeds a configurable threshold.

## 5.2 What you would do

**Approve every contract: **Just like Phase 2 equity trades, every options contract would require your explicit approval via Teams adaptive card before execution. The card would show: underlying stock, strike price, expiration date, premium to collect, max risk, probability of profit, and Claude's reasoning.

**Review daily summaries: **Claude would send a daily wheel status report showing: open positions, premium collected this cycle, days to expiration, current probability of assignment, and total wheel income year-to-date.

**Decide when to stop: **If a stock's fundamentals deteriorate or market conditions change significantly, you decide to stop the wheel on that position — not Claude.

# 6. Data Requirements (already available)

The good news is that nearly all data required for the wheel strategy is already being collected by the existing pipeline. No new API sources are needed.

**Options chains: **Already being collected from E*TRADE for SPY, QQQ, and top holdings as sentiment signals. For the wheel, the collector would expand to pull full chains (all strikes and expirations) for wheel candidate stocks.

**Fundamentals and valuation: **Already available from FMP. Used for stock selection — only run the wheel on fundamentally sound companies.

**Implied volatility and Greeks: **Derivable from the options chain data already collected from E*TRADE. IV rank and IV percentile would be computed by the Analyzer to identify favorable premium environments.

**Earnings calendar: **Already available from FMP. Critical for the wheel — never sell a put or call through an earnings date unless the premium compensates for the event risk.

**Congressional and insider trading: **Already available from FMP. Additional conviction signal for stock selection.

# 7. Estimated Additional Cost

**Infrastructure: **$0-5/month additional. The wheel functions would run on the same Consumption plan as existing functions. A few additional Logic App runs for approval workflows.

**Claude API: **$3-5/month additional. Weekly options analysis calls are smaller than daily portfolio analysis. Strike selection requires less context than full portfolio review.

**Options commissions: **Varies by broker. E*TRADE charges $0.65/contract. For a 3-stock wheel with weekly contracts, expect ~$8-12/month in commissions.

**Total additional: **~$5-20/month on top of the existing Phase 1+2 costs.

# 8. Decision Framework

Use this checklist when evaluating whether to proceed with this project:

**1. Has the core system been running for 90+ days? **The wheel adds execution complexity that should only be layered on a proven, stable system.

**2. Is the hit rate on equity recommendations above 55%? **If Claude's analysis isn't producing good equity calls, options amplify losses, not gains.

**3. Do you have margin and options approval? **Regulatory prerequisite. Cannot be skipped.

**4. Do you have $50K+ in available capital? **Running the wheel on 2-3 stocks requires $40-75K in committed capital, separate from your existing portfolio.

**5. Are you comfortable with assignment risk? **If the idea of being forced to buy 100 shares of a stock that just dropped 20% keeps you up at night, the wheel isn't for you — regardless of how good the math looks on paper.

**6. Can you commit to weekly review? **The wheel requires more active oversight than the daily portfolio analysis. Weekly contract decisions need your attention.

*If the answer to all six is yes, create a dedicated project spec for the wheel strategy with its own Phase 1 (paper validation) and Phase 2 (live execution) milestones. Do not bolt it onto the existing phases.*

*This document is a placeholder companion to the Azure-Native Portfolio Automation System specification. It captures the concept and requirements for a future options income project and should be revisited only after the prerequisites in Section 3 are fully met. Options trading involves significant risk of loss. The wheel strategy can result in substantial losses if the underlying stock declines materially. Paper trade extensively before committing real capital.*
