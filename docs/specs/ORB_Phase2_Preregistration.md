# ORB Phase 2 — Pre-registration of the backtest and its go/no-go rule

**Status: LOCKED at this document's first commit.** Written 2026-10-04, before any Phase 2 code and before any Phase 2 result exists, on top of `master` commit `b76c4af921786bd307a793a17b60eea1a476d8ff` (branch `feat/orb-phase2-backtest` was created from that commit). Spec: `docs/specs/ORB_Engine_v1.0.md`, "Phase 2: Backtest engine". Rules 6 and 9 of `CLAUDE.md` ("ORB program") apply.

Nothing below may change after the first real run. A change means a new pre-registration (a new file, with its own first commit) and a fresh test period (spec, Phase 2); the 2024–2025 window is then no longer an out-of-sample test of the changed rule. Every run's header records this file's path, the SHA of the commit that introduced it, and whether the file's contents still match their first-commit hash.

## 1. The primary variant (the go/no-go is judged on this one only)

The rules and thresholds below are Jorge's, copied word for word from the Phase 2 instruction.

| Rule | Value |
| --- | --- |
| Window | 2024-01-02 to 2025-12-31, SIP data; 2026 is never read |
| Universe | Open > $5, 14-day average volume ≥ 1M, 14-day ATR > $0.50 (averages from prior days only), Core roster excluded, **ETFs excluded** |
| ATR | **Simple mean of the prior 14 true ranges**, the same definition Phase 1 uses, for both the filter and the stop |
| Stocks in Play | Relative Volume (9:30–9:35 volume ÷ prior-14-day average of the same window) ≥ 100%, top 20 |
| Direction | First 5-min bar up → long only; down → short only; doji → no trade. **Shorts allowed** |
| Entry | Stop at the 5-min high (long) or low (short), live from the 9:35 bar through 15:45; fill at the level, or at the bar's open if it gaps through |
| Stop | 10% of ATR from the **trigger price** (the live rule); fills at the level, or at the bar's open on a gap; entry and stop in the same minute bar ⇒ stop hit |
| Exit | Close of the 15:59 bar if not stopped. Half-days use the calendar close |
| Daily loss limit | Sleeve P&L (realized + marked) ≤ −3% of sleeve capital ⇒ cancel entries and flatten at that bar's close (mirrors live) |
| Sizing | Sleeve capital **$25,000** (25% of a $100k account), fixed and non-compounding. Shares = floor(min(1% of sleeve ÷ stop distance, sleeve ÷ 20 ÷ price)). Integer shares; never levered |
| Costs | $0.0035/share commission plus slippage of **2¢ per share per side** for the go/no-go |
| Metrics | Daily sleeve return = day P&L ÷ $25,000; no-trade days count as 0. Sharpe = mean ÷ std × √252, risk-free 0 |

## 2. The go/no-go (mechanical)

GO only if, on the primary variant, net Sharpe ≥ **1.0** over 2024–2025 **and** net P&L is positive in **both** 2024 and 2025. Anything else is NO-GO.

"Net" means after commission and slippage. "Positive" means strictly greater than zero dollars. The Sharpe is compared unrounded. No other number, and no other variant, enters the verdict.

## 3. Reported sensitivities (never used for the go/no-go)

Slippage of 0, 1 and 5¢ per side; long-only; ETFs included. Nothing else. No parameter sweeps, no alternative ranges, no other filters. Each sensitivity changes exactly one thing relative to the primary variant (slippage; or shorts removed; or ETFs left in the universe).

## 4. The ETF list

Derived from Nasdaq Trader's public symbol directory files, `nasdaqlisted.txt` and `otherlisted.txt`, each of which carries an ETF Y/N column. Downloaded once, read-only, into `data/reference/` (gitignored). This is the only non-Alpaca network access permitted for Phase 2.

| File | URL | Downloaded | Bytes | SHA-256 | Rows (ETF = Y) |
| --- | --- | --- | --- | --- | --- |
| `nasdaqlisted.txt` | `https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt` | **2026-10-04** (20:38 ET; 2026-10-05T00:38Z) | 349,780 | `171f3d1a4ef7f6d5ebc2c8e64e77e02bcb661a7573b5f1ff0ff182cf68ca0ccd` | 5,634 (1,293) |
| `otherlisted.txt` | `https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt` | **2026-10-04** (20:38 ET; 2026-10-05T00:38Z) | 542,961 | `858406d16b357c83a02d6c63b8b7a3ee21bde53b85c3a29d7b85d28cdcf07bec` | 7,661 (4,467) |

Both files state `File Creation Time: 1002202621:31` (10/02/2026 21:31). A symbol is an ETF if it appears with ETF = Y under any of the symbol columns (`Symbol` in `nasdaqlisted.txt`; `ACT Symbol`, `CQS Symbol` and `NASDAQ Symbol` in `otherlisted.txt`), matched exactly after upper-casing. A symbol found in neither file is treated as not an ETF.

Known limitation, stated up front: the files list securities that are listed **today**, so an ETF that was delisted before 2026-10-04 is not flagged and stays in the "ETFs excluded" universe.

## 5. Implementation clarifications (locked with the rest)

The instruction fixes the rules above but not every detail a deterministic engine needs. These choices are made now, before any result, and are locked like the rest. Each is an interpretation, listed so it can be challenged before the first real run.

1. **One entry attempt per symbol per day.** After a stop-out there is no re-entry.
2. **Top 20 first, direction second.** The top 20 are picked by Relative Volume before direction is applied; a doji pick (and, in the long-only sensitivity, a short-direction pick) is dropped and **not replaced** by the 21st name.
3. **Ranking:** Relative Volume descending, ties broken by symbol ascending.
4. **Trading days** are the days SPY has a SIP daily bar in the window (the Phase 1 convention). The window's first day needs 14 prior trading days, so daily bars are fetched from 2023-12-01.
5. **Relative Volume** uses the SIP five-minute bar stamped 09:30. A symbol-day with no such bar counts as zero volume, and the prior-14-day average must be positive. All 14 prior trading days on the calendar must be inside the history, as in Phase 1.
6. **ATR / universe history:** the 14 prior trading days must all have a daily bar. The previous close of the oldest of them may be missing, in which case that bar's true range is high − low (this is exactly what the Phase 1 report does). "Open" is the day's SIP daily-bar open.
7. **Assets:** active and inactive US equities on NYSE, NASDAQ, AMEX, ARCA and BATS (OTC excluded), minus the Core roster (`shared.quadrants.CORE_ROSTER`), minus ETFs (Section 4) in the primary variant.
8. **Trigger and entry bars.** Entry orders are live for the one-minute bars stamped from 09:35 up to and including the bar stamped 15:45. A long triggers in a bar whose high ≥ the 5-min high; a short in a bar whose low ≤ the 5-min low. The fill is at the level, or at the bar's open if the open is already beyond it. On a half-day the last entry bar is stamped 15 minutes before the calendar close (12:45 on a 13:00 close).
9. **Stop level** = trigger − 0.1 × ATR (long) or trigger + 0.1 × ATR (short), rounded to the cent **away from the entry** (long: down; short: up), so it is never tighter than the rule. A long's stop triggers in a bar whose low ≤ the stop; a short's in a bar whose high ≥ the stop; the fill is at the stop level, or at the bar's open if the open is already beyond it. The stop is checked in the entry bar itself, and a hit there is a stop-out (the rule above).
10. **Sizing price and stop distance.** Price = the trigger level. Stop distance = |trigger − rounded stop|. Shares are floored with a 1e-9 tolerance so a float artefact such as 2499.9999999999995 cannot drop a share. Fewer than one share ⇒ no trade, counted as a zero-share skip. Which limit binds (risk or position cap) is recorded per trade.
11. **Exit.** An open position exits at the close of the last one-minute bar stamped at or before 15:59 (a stale last price if the 15:59 bar is absent; those cases are counted). On a half-day the exit bar is the one stamped one minute before the calendar close. Half-days are the NYSE early closes **2024-07-03, 2024-11-29, 2024-12-24, 2025-07-03, 2025-11-28, 2025-12-24 (13:00 ET)**; the real run cross-checks them against SPY's own one-minute bars and reports any mismatch rather than changing the table.
12. **Costs.** Slippage is applied adversely to every fill (a buy fills s higher, a sell s lower), whether entry, stop, time exit or loss-limit flatten. Commission is $0.0035 per share on every fill (so twice per round trip). Gross P&L is measured at the un-slipped fill prices; net P&L = gross − slippage − commission.
13. **R multiples** = net P&L ÷ (shares × stop distance). Gross R is reported too. R is not capped, so a gap through the stop can print below −1R.
14. **Daily loss limit.** Evaluated at the close of every one-minute bar from 09:35 on, after that bar's fills. Sleeve P&L = costs-inclusive realized P&L plus every open position marked at that bar's close (the last close seen, if the symbol has no bar that minute). At ≤ −3% × $25,000 = −$750: pending entries are cancelled, every open position is flattened at that bar's close (with slippage and commission), and nothing trades again that day.
15. **Metrics.** Sharpe uses every trading day in the window (no-trade days are 0) with the **sample** standard deviation (ddof = 1). Per-year P&L is the sum of net daily P&L over that calendar year. Max drawdown is on the cumulative net P&L curve added to the $25,000 sleeve. Contribution to total equity = sleeve return × 0.25.
16. **Capital is fixed.** $25,000 every day, whatever the past P&L.
17. **Missing data.** A pick with no one-minute bars that day cannot trade and is counted; other picks are unaffected. A pick's 5-minute high/low is taken from the 5-minute bar stamped 09:30.
18. **Short availability and borrow cost are not modeled** (the primary variant allows shorts on every name, as instructed); the assets list's `shortable` flags describe today, not 2024–2025, and are not used.

## 6. Machine-readable parameters of the primary variant

`tests/test_orb_prereg.py` asserts that `orb.config.OrbConfig()` equals this block, so the code cannot drift from the document unnoticed.

```json
{
  "spec_version": "orb-1.0",
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
  "position_slots": 20,
  "daily_loss_limit_pct": 3.0,
  "commission_per_share": 0.0035,
  "slippage_cents_per_side": 2.0,
  "allow_shorts": true,
  "exclude_etfs": true,
  "tick_size": 0.01
}
```

Window and holdout: `2024-01-02` to `2025-12-31`. The engine refuses any date on or after `2026-01-01` and has no override flag in Phase 2.

## 7. What a run reports

For every variant: net Sharpe, total and per-year P&L, max drawdown, trades, hit ratio, average R, the R distribution, long vs. short, days with no trades, how often each sizing limit binds, and contribution to total equity (sleeve return × 0.25); plus the mechanical go/no-go verdict for the primary variant. The verdict is reported and the work stops: no tuning, no added variants, no 2026 data, however the numbers look.
