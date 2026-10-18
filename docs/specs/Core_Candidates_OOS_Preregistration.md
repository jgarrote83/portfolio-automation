# Core candidates, out-of-sample validation on 1989–2004 (rules unchanged): pre-registration

**Status: LOCKED at this document's first commit.** Written 2026-10-17, before any out-of-sample code and before any 1989–2004 result exists, on top of `master` commit `a52c66b` (`a52c66b` is the fast-forward of `research/core-candidates`; branch `research/core-candidates-oos` was created from it). `CLAUDE.md` rules 1–10 apply. No Azure resource, no Alpaca account, nothing in `src/`.

Nothing below may change after the first real run. A change means a new pre-registration (a new file, with its own first commit). A dated note headed "Post-run clarification, no rule change" may be appended below; it is not part of the locked rule text (`backtest/provenance.py` hashes only the text above such a heading).

**Nothing dated after 2004-12-31 is read.** 2005–2025 has already been used by the parent study, and 2026 is reserved.

**What was looked at before this document was written (availability only; no strategy number of any kind had been computed on 1988–2004).** `yfinance` returns `^SP500TR` daily rows from 1988-01-04 to 2004-12-31 (4,289 rows; 4,036 in the window from 1989-01-03). Its adjusted close equals its close on every row and it carries no dividends, as expected for a total-return index. SPY's rows start 1993-01-29, and from 1993-02-01 to 2004-12-31 the two share exactly the same trading days. FRED `DTB3` and `BAA10Y` (requested with `cosd=1988-01-01&coed=2004-12-31`) start 1988-01-04. On the index's days from 1988-06-01, `DTB3` is blank on 40 and `BAA10Y` on 41, both carried forward under the parent rules. The only gap of more than four calendar days between consecutive index days is the September 2001 closure (2001-09-10 to 2001-09-17).

## 1. The rules (Jorge's, word for word)

**Rules: identical to `docs/specs/Core_Candidates_Preregistration.md`** for C1, C4 and C5: the same signals, the same 2x formula, financing, costs (2 bps), next-close execution, the `BAA10Y` one-day publication lag, and the 126-day credit average. **No parameter changes of any kind.**

**Only substitution:** SPY doesn't exist before 1993, so the equity series is the **S&P 500 Total Return index** (Yahoo `^SP500TR`) everywhere SPY's total return was used, including the trend test's 10-month average and the 2x simulation. Before relying on it, report its tracking against SPY's adjusted total return over 1993-02-01 to 2004-12-31 (yearly differences and correlation). A gap near SPY's expense ratio is expected.

**Window:** evaluation from **1989-01-03 to 2004-12-31**. Data is fetched from 1988-01-04 (the first `^SP500TR` day) so the 10-month and 126-day lookbacks are complete by the first decision. Benchmark: `^SP500TR` buy-and-hold.

**Judged candidates:** **C4** (the holdout-eligible one) and **C5** (the steadier alternative). **C1** is reported for reference only and has no verdict.

**Criteria (per judged candidate, all three required):**

1. CAGR **>** `^SP500TR` CAGR over 1989–2004.
2. Maximum drawdown **no worse than** `^SP500TR`'s over the same window.
3. Beats `^SP500TR` in **at least 2 of 3** sub-periods: 1989-01 to 1994-12, 1995-01 to 1999-12, 2000-01 to 2004-12.

Outcomes: **VALIDATED** (all three) or **NOT VALIDATED**. Two candidates are judged, so note the multiple-testing caveat beside any pass.

**Reported, never deciding:** a stitched 1989–2025 view (this window plus the 2005–2025 run's daily series) of CAGR, max drawdown and worst year for each, against the S&P 500. Descriptive only; it adds no new judgment.

**No sensitivities.** The point is to test the exact rules.

## 2. Interpretations (locked with the rest)

Each is listed so it can be challenged before the first real run. Item numbers in parentheses refer to section 2 of the parent pre-registration, whose interpretations all carry over unchanged unless restated here.

1. **Trading days** are the dates of `^SP500TR`'s yfinance rows. The evaluation window is those days from 1989-01-03 to 2004-12-31 (4,036 days); 1988 rows are look-back only.
2. **The substitution.** The index's adjusted close takes SPY's place in every computation: the 10-month trend test (parent item 7), the simulated 2x fund's daily return (parent item 6), C5's "1x SPY" state, and the benchmark. The index carries no expense ratio, so the 1x leg and the benchmark are an index nobody could hold at zero cost. Both sides of every comparison carry the same advantage, and the 2x fund keeps its 0.91%/yr expense, so it is disclosed, not adjusted.
3. **Engine reuse.** The parent study's functions in `research/core_candidates/engine.py` (market building, decisions, signals, `lev_return`, `trade_cost`, `simulate`, `benchmark`, `metrics`, `verdict`, `crisis`) are imported and called **unchanged**; no function body is copied or edited. Two of that module's constants are tied to the 2005–2025 window: `FIRST_DECISION` (the earliest decision day) and `SUB_PERIODS`. For this run they are set, inside a scoped override restored on exit, to 1988-12-30 and to the three sub-periods of section 1. Every other constant (costs, spread, expense, 252, the 7-day carry limit) is untouched.
4. **Decision timing.** The first decision of each candidate is taken at the close of 1988-12-30 (the last index day of 1988) and executes at the close of 1989-01-03, when the $100,000 is first invested. The last decision taken is the 2004-11-30 month-end, executing 2004-12-01; the December 2004 month-end would execute in 2005 and is not taken.
5. **Warm-up.** At the first decision the 10 most recent month-ends (1988-03 to 1988-12) and the 126 most recent credit values must all exist. The parent functions raise if they do not, and no decision is ever taken before both are complete.
6. **T-bills and credit** follow parent items 5 and 9 exactly: r_t from the latest `DTB3` dated on or before the previous trading day, c_t from the latest `BAA10Y` dated strictly before t, carry-forward counted, more than 7 calendar days stops the run.
7. **Metrics and criteria arithmetic** follow parent items 13 and 14 exactly, with this window, the sub-periods of section 1 and `^SP500TR` as the benchmark. The benchmark pays no cost.
8. **Tracking check.** Over index days from 1993-02-01 to 2004-12-31: the correlation of daily returns, and per calendar year the compounded SPY return minus the compounded index return in points (1993 from 1993-02-01). Also reported: the average of the 1994–2004 yearly differences and the compounded totals. Descriptive only.
9. **Crisis close-ups.** Fixed windows: 1990-07-02 to 1990-10-31, 1998-07-01 to 1998-10-30, and 2000-03-01 to 2002-10-31. Each reports, with the parent `crisis` function, the window return, the drawdown inside the window, the holding at its start, and every risk cut or restore.
10. **Stitched 1989–2025 view.** For the index and for C1, C4 and C5, the 1989-01-03 to 2004-12-31 equity is chained to the parent run's (`reports/core_candidates/20261018T012418Z/`) daily equity from 2005-01-03 on, by multiplying the parent series by (ending 2004 equity ÷ 100,000). The S&P side switches from `^SP500TR` to SPY's adjusted total return on 2005-01-03. The return of 2005-01-03 itself (from the 2004-12-31 close to the 2005-01-03 close) is unknown to both runs and is taken as zero for every series; the parent run's initial-purchase cost on that day is kept. Reported: CAGR, max drawdown with dates, and worst calendar year, all descriptive.
11. **Refusal.** No source is asked for a date after 2004-12-31 (Yahoo `end` exclusive 2005-01-01; FRED `coed=2004-12-31`), any received or cached row dated after it is a failure, and the run refuses a window ending after it. The stitched view reads the parent run's saved outputs only, never any market source.
12. **Verifier.** A separate post-run program, importing nothing from the engine or the out-of-sample modules, re-derives from the raw cached files, for **12 random month-end decisions** (seed 11), each of C1, C4 and C5's holding decided and its return from that execution close to the next month-end's execution close. Holdings must match exactly, returns to 1e-9 relative.
13. **Only the exact pre-registered run yields a verdict:** this window, the parent default configurations of C1, C4 and C5, and an unmodified pre-registration. Anything else is labelled NOT A PRE-REGISTERED RUN. Outputs go to `reports/core_candidates_oos/<run-id>/` (gitignored).
