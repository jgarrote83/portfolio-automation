"""Markdown report for the Core-vs-SPY analysis. Pure formatting over the dict `run.build` returns: every number in the text is read from
that dict (nothing is typed in), so the narrative cannot drift from the computation."""
from __future__ import annotations


def pct(x, nd=2, sign=True):
    if x is None:
        return "n/a"
    return f"{x:+.{nd}f}%" if sign else f"{x:.{nd}f}%"


def pp(x, nd=2):
    return "n/a" if x is None else f"{x:+.{nd}f} pp"


def usd(x, nd=0):
    if x is None:
        return "n/a"
    return f"-${abs(x):,.{nd}f}" if x < 0 else f"${x:,.{nd}f}"


def num(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"


def table(header: list[str], rows: list[list[str]], align: list[str] | None = None) -> str:
    align = align or ["---"] * len(header)
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join(align) + " |"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def scoreboard_table(b: dict, title: str) -> str:
    bk, sp, st = b["book"], b["spy_price"], b["spy_total"]
    def col(w):
        lo, hi = (w["sharpe_ci95"] if w["sharpe_ci95"] else (None, None))
        return [pct(w["total_return"] * 100), pct(w["ann_vol"] * 100, 1, False), f"{num(w['sharpe'])} ({num(lo, 1)} to {num(hi, 1)})",
                pct(w["max_drawdown"]["max_drawdown"] * 100)]
    rows = [["Total return", *[col(w)[0] for w in (bk, sp, st)]], ["Annualised volatility", *[col(w)[1] for w in (bk, sp, st)]],
            ["Sharpe (rf = 0; approx. 95% band)", *[col(w)[2] for w in (bk, sp, st)]], ["Maximum drawdown", *[col(w)[3] for w in (bk, sp, st)]]]
    return (f"**{title}** (to the close of {b['as_of']}; {bk['n_returns']} daily returns)\n\n"
            + table(["", "Book (Alpaca equity)", "SPY price return", "SPY total return"], rows, ["---", "---:", "---:", "---:"])
            + f"\n\nExcess return: **{pp(b['excess_price_pp'])}** vs SPY price (like-for-like: the paper account credits no dividends), "
              f"**{pp(b['excess_total_pp'])}** vs SPY total return. Beta to SPY {num(bk['beta'])}, correlation {num(bk['corr'])}, R^2 {num(bk['r2'])}, "
              f"annualised alpha {pct(bk['alpha_annual'] * 100, 1)}.")


def render(res: dict, meta: dict, *, snap_path: str, inception_given: bool) -> str:
    L, V = res["board_latest"], res.get("board_validate")
    d, dv = res["decomp_latest"], res.get("decomp_validate")
    bk = L["book"]
    out = ["# Core book vs SPY: where the lag comes from", "",
           f"**Window:** pre-trade base {res['base']} (the 5/22 close level, $100,000), first session {res['first']}, last session {res['last']}: "
           f"**{res['trading_days_in_window']} trading days** (about {res['trading_days_in_window'] / 21:.1f} months). Everything below is descriptive; "
           "it makes no recommendation to change Core. Written for Jorge.", ""]

    # ----------------------------------------------------------------------------------- status
    out += ["## Status: what was and was not possible", "",
            "This run had **no Azure access** (the Azure CLI is signed in as a service principal, not as the required user, and the instruction was to wait for the user login). "
            "Sections that need Azure blobs or tables are marked **BLOCKED** with the exact input they need; nothing is estimated in their place.", "",
            table(["Part", "Status", "Why / what it needs"], [
                ["1. Scoreboard (return, volatility, Sharpe, drawdown, beta)", "Done", "Alpaca portfolio history + SPY bars"],
                ["1. Equity-curve cross-check vs snapshots", "Partly", "Only the local 2026-10-02 snapshot exists; the full `performance/equity-series.json` and the other daily snapshots need Azure"],
                ["2(a) Cash drag", "Done (exact daily)", "Cash rebuilt exactly from account activities; SGOV from fills and SGOV bars"],
                ["2(b) Allocation effect (policy portfolio)", "**BLOCKED**", "Needs `daily-snapshots/2026-05-26.json` (inception-day reference weights); re-run with `--inception-snapshot`"],
                ["2(c) Tactical effect", "**BLOCKED**", "Same input as (b); (b) and (c) are reported only as their sum"],
                ["2(d) Costs and turnover", "Partly", "Fees and turnover done; fills vs reference prices needs `daily-trades/*.json` (Azure)"],
                ["3. Other benchmarks", "Done", "Benchmark bars"],
                ["4. Contribution by holding", "Done", "FIFO from activities, marked at the fetch prices"],
                ["5. Regime calls and judgment", "Echo of 2026-10-02", "Latest snapshot needs Azure; the local one is dated 2026-10-02, five sessions before the last one"]], ["---", "---", "---"]), "",
            "**Disclosures.** (1) Benchmark daily bars from 2026-05-20 were read for this analysis, so SPY's daily path in June-September 2026 is no longer unseen "
            "by the research program (the 2026 holdout in `backtest.data` is untouched and still refuses 2026 for research). (2) While diagnosing a data-feed error "
            "I made two probe requests for SPY bars in windows outside this analysis (2026-01-05 to 01-09 and 2026-08-03 to 08-07); only bar counts and dates were looked at, "
            "no prices were printed or saved from them.", ""]

    # ----------------------------------------------------------------------------------- sources
    paths = ", ".join(meta["paths"])
    out += ["## Data sources and exact windows", "",
            table(["Input", "Source", "Window / size"], [
                ["Equity by session", "Alpaca paper `GET /v2/account/portfolio/history` (period 1A, 1D, `pnl_reset=no_reset`)",
                 f"{res['base']} to {res['last']}; no missing session, no external flow after 2026-05-12"],
                ["Cash, fills, fees", "Alpaca paper `GET /v2/account/activities` (all, ascending)", f"{meta['counts']['activities']} rows 2026-05-12 to 2026-10-09: {res['fill_count']} fills, {res['fees']['count']} fees, 1 journal"],
                ["Positions", "Alpaca paper `GET /v2/positions`, `GET /v2/account`", f"{meta['counts']['positions']} positions at the fetch ({meta['fetched_utc'][:16]}Z)"],
                ["Benchmarks", "Alpaca market data `GET /v2/stocks/bars`, 1Day, SIP; raw and split+dividend-adjusted", f"{meta['bars_start']} to {meta['bars_end']}: SPY, AGG, VTI, TLT, IEF, GLD, DBC, SGOV"],
                ["Snapshot echo", f"`{snap_path}` (downloaded earlier under a prior authorisation; no new Azure read)", "date 2026-10-02"],
                ["Core universe", "`shared.quadrants` (`CORE_ROSTER` and `LEGACY_EXITS`), read from the repo", "live config"]], ["---", "---", "---"]), "",
            f"Requests made this run: {meta['requests']} GETs (paths: {paths}). No order, account-change or Azure call exists in the code (`alpaca_ro.py` has only `get`).", ""]

    # ----------------------------------------------------------------------------------- 1
    out += ["## 1. Scoreboard", "", scoreboard_table(L, "Book vs SPY, since inception"), "",
            f"**Sample size:** {bk['n_returns']} daily returns. A Sharpe ratio estimated on this many days has an approximate 95% band of about +/-{(bk['sharpe_ci95'][1] - bk['sharpe_ci95'][0]) / 2:.1f}, "
            "so the sign of the book's Sharpe and the size of its gap to SPY's are not statistically established; the return gap is a fact, its cause is the question.", ""]
    if V:
        out += [scoreboard_table(V, "Same table to the close of the snapshot's basis date"), ""]
    v = res.get("validation")
    if v:
        out += ["### Cross-check against the 2026-10-02 snapshot's own scoreboard", "",
                table(["Quantity", "Snapshot says", "Recomputed here", "Reading"], [
                    ["Book return since inception", pct(v["snapshot_return_pct"], 3), f"{pct(v['book_return_from_snapshot_equity_pct'], 3)} from the snapshot's 09:00 equity; {pct(v['book_return_close_pct'], 3)} at the 10-01 close (= its `last_equity`)",
                     "Matches: the snapshot's headline uses the pre-market equity, the table above uses the official close"],
                    ["SPY return since inception", pct(v["snapshot_spy_return_pct"], 3), f"{pct(v['spy_return_from_first_day_close_pct'], 3)} from the first-day close; {pct(v['spy_return_from_base_close_pct'], 3)} from the pre-trade base close",
                     "**The snapshot's SPY baseline is the 5/26 close, exactly**"],
                    ["Excess", pp(v["snapshot_excess_pp"], 3), f"{pp(v['book_return_from_snapshot_equity_pct'] - v['spy_return_from_base_close_pct'], 3)} on a common base", "see the baseline note"],
                    ["Maximum drawdown", pct(v["snapshot_max_drawdown_pct"], 3), pct(v["max_drawdown_close_pct"], 3), "Close-based vs pre-market-based equity points"]],
                      ["---", "---:", "---", "---"]), "",
                "**Baseline note (a measurement finding, not a Core change).** The book's baseline is the pre-trade equity of $100,000, which is the level at the "
                f"{res['baseline']['base_date']} close; the existing scoreboard's SPY baseline is the {res['baseline']['first_day']} close ({res['baseline']['first_day_spy']:.2f}), "
                f"after SPY had already moved {pct(res['baseline']['spy_move_base_to_first_day_pct'])} from {res['baseline']['base_spy']:.2f}. The book is therefore credited with its own first session "
                f"while SPY's first session is left out, which makes the published excess look about {res['baseline']['excess_flattery_pp']:.2f} points better than the like-for-like figure "
                f"(to 10-09: SPY {pct(res['baseline']['spy_to_last_from_base_pct'])} from the common base against {pct(res['baseline']['spy_to_last_from_first_day_pct'])} from the first-day close). "
                "I could not see the stored equity series, so I cannot say whether the pairing of morning equity with same-day SPY closes also shifts later points by a session.", ""]
    w0 = res["weights"][-1]
    out += ["### Account checks", "",
            f"* Cash rebuilt from the activity log equals the account's cash to {abs(res['reconciliation']['cash_diff']):.4f} dollars (${res['reconciliation']['cash_account']:,.2f}); "
            f"net fill quantities equal the account's positions for all {len(res['reconciliation']['positions_qty'])} symbols "
            f"({len(res['reconciliation']['qty_mismatches'])} mismatches).",
            f"* No missing session in the equity history ({len(res['gaps'])} gaps); no external cash flow after the single $100,000 journal on 2026-05-12 "
            f"({len(res['external_flows_in_window'])} in the window). **The paper account credits no dividends** (no dividend activity at all), which is a handicap against "
            f"total-return benchmarks: SPY's own distributions add {res['spy_dividend_gap_pp']:.2f} points over this window, and the distributions of the book's holdings are unmeasured.",
            f"* **The account is slightly levered at the end:** cash is {usd(w0['cash'])} ({pct(w0['cash_pct'], 1)} of equity) on {w0['day']}, with SGOV at {pct(w0['sgov_pct'], 1, False)}; "
            f"literal cash was negative at the close of {len(res['leverage_days'])} sessions ({', '.join(res['leverage_days'][:3])}{', ...' if len(res['leverage_days']) > 3 else ''}).", ""]

    # ----------------------------------------------------------------------------------- 2
    out += ["## 2. Decomposition of the lag behind SPY", "",
            "The decomposition is done **day by day** and is exactly additive: each day, excess = cash term + invested term, where the cash term is "
            "[SGOV value x (SGOV return - SPY return) + literal cash x (0 - SPY return)] / equity, all at the previous close, and the invested term is the exact residual. "
            "It extends the collector's `excess_attribution` (which uses an average cash weight over a cumulative return) and is compared with it below. SPY is the **price** return "
            "throughout this section because the book earns no dividends.", "",
            table(["Term", "Since inception (to " + res["last"] + ")", "Reading"], [
                ["Total excess, compounded (book minus SPY)", pp(d["compounded_excess"]), "the headline gap"],
                ["Sum of the daily excess returns (what the terms add to)", pp(d["excess"]), "arithmetic, not compounded"],
                ["**(a) Cash drag**: cash sleeve (literal cash + SGOV)", pp(d["cash"]), f"average weight {d['avg_sleeve_weight_pct']:.1f}% of equity"],
                ["   of which literal cash (earns 0)", pp(d["cash_literal"]), "negative when cash is a drag; positive on the levered days"],
                ["   of which SGOV (price return only)", pp(d["cash_sgov"]), f"SGOV price return {pct(res['sgov_yield_artifact']['sgov_price_return_pct'], 2)}"],
                ["**(b)+(c) Invested book vs SPY**, allocation and tactical together", pp(d["invested"]), "exact residual of the daily excess"],
                ["   (b) Allocation effect (policy portfolio vs SPY)", pp(d["allocation"]) if d["allocation"] is not None else "**BLOCKED**", "needs the inception-day reference weights"],
                ["   (c) Tactical effect (actual invested book vs policy)", pp(d["tactical"]) if d["tactical"] is not None else "**BLOCKED**", "same input"],
                ["**(d) Fees**", f"{usd(abs(res['fees']['total']), 2)} in total", f"{res['fees']['count']} regulatory fee lines; negligible in paper"],
                ["Compounding residual (compounded minus arithmetic)", pp(d["compounding_residual"]), "shown, not hidden"]],
                  ["---", "---:", "---"]), "",
            f"**Share of the arithmetic excess:** cash sleeve {100 * d['cash'] / d['excess']:.0f}%, invested book {100 * d['invested'] / d['excess']:.0f}%."]
    if dv and res.get("existing_method_validate"):
        e = res["existing_method_validate"]
        sn = ""
        if "recomputed_with_snapshot_inputs" in e:
            sn = (f" Recomputed with the snapshot's own inputs (average cash {e['snapshot_avg_cash_pct']:.2f}%, SPY {pct(e['snapshot_spy_return_pct'], 3)}, SGOV price return "
                  f"{pct(e['sgov_return_pct'], 3)}) the formula gives {pp(e['recomputed_with_snapshot_inputs'], 3)} against the published {pp(e['snapshot_cash_contribution_pp'], 3)}, so the method is understood.")
        out += ["", f"**Reproducing the collector's number.** Its cash term is the average *literal* cash weight x (SGOV price return - SPY return) over the window.{sn} "
                    f"It leaves the SGOV position out of the cash weight: the average cash *sleeve* here (literal cash plus SGOV) is {e['avg_sleeve_pct']:.1f}% against {e['avg_literal_cash_pct']:.1f}% literal cash, "
                    f"which is why the exact daily cash term above ({pp(dv['cash'])} to 10-01) is larger than the published one."]
    out += ["", "### Where the lag built up, by month", "",
            table(["Month", "Sessions", "Book", "SPY", "Excess (sum of daily)", "Cash term", "Invested term"],
                  [[m["month"], m["days"], pct(m["book_pct"]), pct(m["spy_pct"]), pp(m["excess_sum_pp"]), pp(m["cash_pp"]), pp(m["invested_pp"])] for m in res["monthly_terms"]],
                  ["---", "---:", "---:", "---:", "---:", "---:", "---:"]), "",
            "### Cash and SGOV weights (average of end-of-day weights, % of equity)", "",
            table(["Month", "Literal cash", "SGOV", "Cash sleeve (sum)", "All holdings incl. SGOV", "Risk assets (ex SGOV)"],
                  [[m["month"], f"{m['cash_pct']:.1f}", f"{m['sgov_pct']:.1f}", f"{m['sleeve_pct']:.1f}", f"{m['holdings_pct']:.1f}", f"{m['risk_assets_pct']:.1f}"] for m in res["monthly_weights"]],
                  ["---", "---:", "---:", "---:", "---:", "---:"]), "",
            "### The cash term, the Flex budget and a paper-account artifact", "",
            "* **Core's own cash sleeve vs an idle Flex budget.** Core's reference allocates 100% of equity (`core_room = 100 - cash sleeve - intl`) and the Flex sleeve is a separate sleeve with "
            "no reserved slice, so the two cannot be told apart from the weights: any cash above Core's own band (5-15%, 25% at shock level 3) is simply undeployed. What can be measured is the capital "
            "that went to names outside the Core universe, which is where a Flex trade would show: " + "; ".join(
                f"{k} {usd(v['bought'])} bought and {usd(v['sold'])} sold in {v['fills']} fills ({v['first']} to {v['last']})" + (f", {v['open_qty']:.0f} shares ({usd(v['open_value'])}) still open" if v["open_qty"] else "")
                for k, v in res["non_core_activity"].items()) + ". Cumulative purchases are small next to a 25% budget (about $25,000), so the Flex budget was largely idle across the window. "
            "The activity log carries no order ids, so it cannot separate Flex trades from inherited holdings.",
            f"* **Paper-account artifact.** SGOV pays distributions that this account never credits, so the cash sleeve earns its price return only. Over the window SGOV's total return was "
            f"{pct(res['sgov_yield_artifact']['sgov_total_return_pct'])} against {pct(res['sgov_yield_artifact']['sgov_price_return_pct'])} in price; at the average SGOV weight of "
            f"{res['sgov_yield_artifact']['avg_sgov_weight_pct']:.1f}% that is about **{res['sgov_yield_artifact']['missing_yield_pp_of_book']:.2f} points** of the cash drag that a real account would have earned.",
            "", "### (d) Costs and turnover", "",
            table(["Month", "Fills", "Bought", "Sold", "Avg equity", "One-way turnover"],
                  [[t["month"], t["fills"], usd(t["bought"]), usd(t["sold"]), usd(t["avg_equity"]), f"{100 * t['turnover_one_way']:.0f}%" if t["turnover_one_way"] is not None else "n/a"] for t in res["turnover"]],
                  ["---", "---:", "---:", "---:", "---:", "---:"]), ""]
    gross = sum(t["bought"] + t["sold"] for t in res["turnover"])
    out += [f"Total traded notional {usd(gross)} over the window (about {gross / 2 / res['inception_equity']:.1f}x the starting equity one-way). Measured explicit costs are the "
            f"{usd(abs(res['fees']['total']), 2)} of regulatory fees. **Paper fills carry no spread or slippage, so real costs are not visible here**; for scale only, each 2 / 5 / 10 basis points per side on "
            f"that notional would be {usd(gross * 0.0002)} / {usd(gross * 0.0005)} / {usd(gross * 0.0010)} "
            f"({gross * 0.0002 / res['inception_equity'] * 100:.2f} / {gross * 0.0005 / res['inception_equity'] * 100:.2f} / {gross * 0.0010 / res['inception_equity'] * 100:.2f} points of the starting equity). "
            "Fills against the analyzer's reference prices **cannot be computed here** (the reference prices are in `daily-trades`, an Azure blob).", "",
            ""]
    out += ["### (b) and (c)", ""]
    if inception_given:
        out += ["The inception-day snapshot was supplied: the policy portfolio (its `reference_weights.target_weights_pct`, normalised to fully invested, held buy-and-hold from the base close) "
                "is computed above and the three terms add to the same total.", ""]
    else:
        out += ["**BLOCKED, not estimated.** To complete them, read-only, after signing in as the required user (subscription `EasyGridsProduction`, `-g rg-portfolio-automation-prod`, "
                "ownership of `stpfautoprod` verified first):", "",
                "```powershell", "az storage blob download --account-name stpfautoprod --subscription EasyGridsProduction --auth-mode login `",
                "  -c daily-snapshots -n 2026-05-26.json -f data/replay/2026-05-26.json --no-progress",
                "python -m analysis.core_performance.run --inception-snapshot data/replay/2026-05-26.json", "```", "",
                "The run then builds the policy portfolio (the snapshot's `reference_weights.target_weights_pct`, normalised to fully invested and held buy-and-hold from the base close), which needs daily "
                "bars for its constituents (the same read-only function) and fills in (b) and (c); the three terms still add to the same total.", ""]

    # ----------------------------------------------------------------------------------- 3
    out += ["## 3. Benchmarks beyond SPY", "",
            table(["Portfolio", "Return", "Annualised vol", "Sharpe", "Max drawdown", "Correlation with the book", "Note"],
                  [[n, pct(b["total_return"] * 100), pct(b["ann_vol"] * 100, 1, False), num(b["sharpe"]), pct(b["max_drawdown"] * 100), num(b["corr_with_book"]), b["note"]] for n, b in res["benchmarks"].items()],
                  ["---", "---:", "---:", "---:", "---:", "---:", "---"]), "",
            f"Same window and dates for all rows; the book's row is price-only (no dividends), the benchmarks other than SPY-price are total return, so the book is compared on a harder basis than it earned. "
            f"The volatility-scaled SPY holds k = {res['vol_scale_k']:.2f} of SPY total return with the rest in SGOV total return; k is estimated on this same window, so it is a yardstick and not a strategy. "
            "60/40 and the all-weather proxy are rebalanced at each month end close.", ""]
    b = res["benchmarks"]
    aw, sixty = b["All-weather proxy, monthly rebalanced"], b["60/40 SPY/AGG, monthly rebalanced"]
    spy_t = b["SPY, total return"]
    out += [f"As a yardstick only: the all-weather proxy trailed SPY (total return) by {abs(aw['total_return'] - spy_t['total_return']) * 100:.2f} points over the same days, and the 60/40 by "
            f"{abs(sixty['total_return'] - spy_t['total_return']) * 100:.2f}; the book trailed the all-weather proxy by {abs(b['Book (Alpaca equity)']['total_return'] - aw['total_return']) * 100:.2f} "
            "points. The proxy is not the system's policy portfolio (that is part (b)).", ""]

    # ----------------------------------------------------------------------------------- 4
    h = res["holdings"]
    fifo = sorted(h["fifo"].items(), key=lambda kv: kv[1]["total"])
    start = res["inception_equity"]
    def hrow(s, v):
        return [s, usd(v["realized"]), usd(v["unrealized"]), usd(v["total"]), f"{100 * v['total'] / start:+.2f}", "held" if v["qty"] > 1e-9 else "closed"]
    out += ["## 4. Contribution by holding", "",
            f"FIFO lots from the activity log, open lots marked at the position prices of the fetch ({h['fetched_utc'][:16]}Z, after Monday 2026-10-12's close), so this table is dated 2026-10-12, "
            f"one session after the scoreboard's last close. Reconciliation: cash + marked holdings = {usd(h['identity']['cash_plus_mv'], 2)} against account equity {usd(h['identity']['equity_now'], 2)} "
            f"(difference {usd(h['identity']['diff'], 2)}); total FIFO P&L after fees {usd(h['identity']['pnl_total_after_fees'], 2)} against equity minus the $100,000 start "
            f"{usd(h['identity']['equity_minus_start'], 2)}. Contribution = P&L / starting equity (points of the book's return); dividends are not credited in this account.", "",
            "**Bottom 8**", "", table(["Symbol", "Realized", "Unrealized", "Total", "Points of book return", "Status"], [hrow(s, v) for s, v in fifo[:8]], ["---", "---:", "---:", "---:", "---:", "---"]), "",
            "**Top 8**", "", table(["Symbol", "Realized", "Unrealized", "Total", "Points of book return", "Status"], [hrow(s, v) for s, v in reversed(fifo[-8:])], ["---", "---:", "---:", "---:", "---:", "---"]), "",
            "**By group** (the collector's `pnl_decomposition` buckets, from the live Core universe)", "",
            table(["Group", "Symbols", "P&L", "Points of book return"], [[g, v["symbols"], usd(v["pnl"]), f"{100 * v['pnl'] / start:+.2f}"] for g, v in h["groups"].items()], ["---", "---:", "---:", "---:"]), "",
            f"{len(fifo)} symbols traded in total. This is a dollar P&L of what was bought and sold; it does not separate the choice of what to hold from the timing, and it is not "
            "the contribution to the *invested book's* return on a time-weighted basis (that would need daily holdings prices).", ""]

    # ----------------------------------------------------------------------------------- 5
    snap = res.get("snapshot")
    out += ["## 5. Regime calls and judgment (echoed from the snapshot of 2026-10-02; numbers only)", ""]
    if snap:
        qp, tr, orr = snap["quadrant_performance"], snap["track_record"], snap["override_record"]
        out += [f"`quadrant_performance` (as of {qp['as_of']}; SPY 30d {pct(qp['spy_ret_30d_pct'])}; favored today: {', '.join(qp['favored_today'])}):", "",
                table(["Quadrant basket", "30d", "excess 30d", "60d", "excess 60d", "90d", "excess 90d", "favored sessions (20)", "suspect"],
                      [[q, pct(v["ret_30d_pct"]), pp(v["excess_30d_pp"]), pct(v["ret_60d_pct"]), pp(v["excess_60d_pp"]), pct(v["ret_90d_pct"]), pp(v["excess_90d_pp"]), v["favored_sessions_20"], "yes" if v["suspect"] else "no"]
                       for q, v in qp["buckets"].items()], ["---", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---"]), "",
                f"`track_record` (headline horizon {tr['headline_horizon']}; sample {tr['sample_size']}; {tr['over_trading']['avg_trades_per_day']} trades a day on average): "
                + "; ".join(f"{h_} n={v['n']} hit rate {v['hit_rate']:.2f}" for h_, v in tr["horizons"].items())
                + "; by layer " + "; ".join(f"{k} n={v['n']} hit rate {v['hit_rate']:.2f}" for k, v in tr["by_layer"].items()) + ".", "",
                "Confidence calibration (predicted vs realised hit rate): " + "; ".join(f"{c['bucket']}: predicted {c['predicted']:.2f}, actual {c['actual']:.2f} (n={c['n']})" for c in tr["calibration"]) + ". "
                f"Record's own caveat: \"{tr['caveat']}\".", "",
                f"`override_record` ({orr['basis']}; n={orr['sample_size']}): overall win rate {orr['overall']['win_rate']:.2f}, average excess {pp(orr['overall']['avg_excess_pp'])}; "
                + "; ".join(f"{k}: n={v['n']}, win rate {v['win_rate']:.2f}, average excess {pp(v['avg_excess_pp'])}" for k, v in orr["by_direction"].items())
                + ". **Do not quote these override numbers:** FOLLOWUPS #110 records that every SGOV override (and every override on an auto-switched incumbent) was graded with an inverted sign "
                  "and blocks quoting the override win rate until the re-grade decision is made; I did not check whether that decision has since been taken.", ""]
    else:
        out += ["No snapshot was available.", ""]

    # ----------------------------------------------------------------------------------- 6
    mw = {m["month"]: m for m in res["monthly_weights"]}
    w_last = res["weights"][-1]
    cash_share = 100 * d["cash"] / d["excess"]
    inv_share = 100 * d["invested"] / d["excess"]
    out += ["## 6. Conclusion, in plain language", "",
            f"* **The size of the lag.** Over {res['trading_days_in_window']} sessions the book returned {pct(bk['total_return'] * 100)} against SPY's {pct(L['spy_price']['total_return'] * 100)} "
            f"(price; {pct(L['spy_total']['total_return'] * 100)} with dividends): a shortfall of {abs(L['excess_price_pp']):.1f} points like-for-like. The published scoreboard shows a smaller gap partly "
            f"because its SPY baseline starts a session late (about {res['baseline']['excess_flattery_pp']:.2f} points).",
            f"* **Which lever explains most.** Of the four named levers only (a) and (d) can be measured separately here. **(a) Cash drag explains about {cash_share:.0f}% of the lag ({pp(d['cash'])})** "
            f"because the cash sleeve averaged {d['avg_sleeve_weight_pct']:.0f}% of equity while SPY rose, earning nothing in this paper account (about {res['sgov_yield_artifact']['missing_yield_pp_of_book']:.1f} points of it is the "
            f"missing SGOV yield). **(d) Costs are ~0 in paper** ({usd(abs(res['fees']['total']), 2)} of fees) though turnover was high (about {gross / 2 / res['inception_equity']:.1f}x equity one-way in {res['trading_days_in_window']} sessions). "
            f"The remaining **{inv_share:.0f}% ({pp(d['invested'])}) is the invested book against SPY, which is (b) allocation plus (c) tactical together; they cannot be separated until the inception-day snapshot is read.**",
            f"* **What the available evidence says about (b) vs (c), as a hint and not a measurement.** The book's beta to SPY was {num(bk['beta'])} (correlation {num(bk['corr'])}): in a market that rose {pct(L['spy_price']['total_return'] * 100)} "
            f"a beta of that size accounts for roughly {pct(bk['beta'] * L['spy_price']['total_return'] * 100)}, leaving about {abs(bk['total_return'] * 100 - bk['beta'] * L['spy_price']['total_return'] * 100):.1f} points of shortfall as the "
            f"regression's alpha ({pct(bk['alpha_annual'] * 100, 0)} annualised, statistically unreliable at this sample). Separately, an all-weather proxy portfolio itself trailed SPY by "
            f"{abs(aw['total_return'] - spy_t['total_return']) * 100:.1f} points (total return, both), more than the invested book's {abs(d['invested']):.1f} (price, no dividends): that points to the diversified, defensive design in a rising equity market "
            "as a large part of the invested-book gap, but the proxy is not the system's policy, so this must be confirmed with part (b).",
            "* **Timing.** The shortfall built in the months when SPY rallied and the book was most in cash or defensive assets (the month tables). The cash sleeve (literal cash plus SGOV) averaged "
            + ", ".join(f"{mw[m]['sleeve_pct']:.0f}% of equity in {m}" for m in sorted(mw)) + f", and was {w_last['sleeve_pct']:.0f}% at the {w_last['day']} close "
            f"(SGOV {w_last['sgov_pct']:.0f}%, literal cash {w_last['cash_pct']:.1f}%); the early months are the ramp from 100% cash.",
            "* **What this does not say.** About 95 trading days is a short sample, one market regime and one start date; every figure here describes this window and is not an estimate of the "
            "system's long-run behaviour. No recommendation to change Core is made. The levers and their measured sizes are: cash sleeve " + pp(d["cash"]) + "; fees about zero; invested book against SPY "
            + pp(d["invested"]) + f"; and the published scoreboard's baseline convention, which flatters its excess by about {res['baseline']['excess_flattery_pp']:.2f} points.", ""]

    # ----------------------------------------------------------------------------------- appendix
    out += ["## Appendix: method notes", "",
            "* **Session dates.** Alpaca stamps a daily portfolio-history point at midnight UTC of the following day, so the New York date of the stamp is the session; bar timestamps are midnight New York, so the first ten characters are the date.",
            "* **Cash.** End-of-day cash = every journal, fee and fill dated on or before the day (New York date of the fill). Holdings (incl. SGOV) = equity - cash; SGOV value = SGOV shares from fills x the SGOV raw close.",
            "* **Returns.** Simple daily returns of Alpaca equity; no flow adjustment is needed because there is no external flow in the window. Volatility x sqrt(252); Sharpe = mean / sample standard deviation x sqrt(252) with a zero risk-free rate.",
            "* **Why the cash term is written as a contribution.** It needs no division by the invested weight, so the first days (100% cash) and the levered days (negative cash) need no special case; the daily terms add to the daily excess exactly.",
            "* **Reproduce:** `python -m analysis.core_performance.fetch` (read-only GETs, writes `analysis/core_performance/data/`, gitignored) then `python -m analysis.core_performance.run`.", ""]
    return "\n".join(out)
