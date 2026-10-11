"""Markdown report for the safe-wheel model study (pre-registration section 4 / item list). Pure formatting: it
computes nothing that is not already in the metrics, the runs or the calibration."""
from __future__ import annotations


from . import safe_wheel as sw

LIMITATIONS = """\
* **Modeled, not real, prices.** Every option price here is a Black-Scholes number built from the VIX and the
  underlying's daily closes. No bid, ask, quote, trade or open interest of any real option was used. The result says
  what a stylized strategy would have done in a stylized market, not what any account could have earned.
* **The skew is approximate.** One linear term (0.5 vol points per 1% out of the money; 0 and 1.0 as sensitivities)
  stands in for a smile that changed shape every day, and it is applied to a flat term structure: the one 30-day index
  prices every maturity from 35 to 7 days. In stress, real skew and term structure steepen well beyond this.
* **No real bid/ask.** Costs are a fixed $0.03 per share per leg per side plus $0.65 per contract. Real spreads on
  SPY options were wider in 2008-2009 and in March 2020 and the exit happens exactly when they are widest.
* **European, not American, exercise.** SPY options are American; the model prices them as European, and a short put
  can be assigned early (deep in the money, around dividends). Positions are closed seven days before expiry, which
  limits but does not remove that risk; early assignment is out of scope.
* **The VIX is a proxy for SPY option implied volatility.** The VIX is a 30-day index of S&P 500 index options built
  across strikes, not the at-the-money implied volatility of SPY monthly options at each strike and maturity. The
  VIX has usually stood above at-the-money implied volatility, which overprices the modeled puts (it favors the
  seller); the calibration against the PUT index shows by how much for the at-the-money case.
* **Daily closes only.** Entries, marks and exits happen at one price per day. Intraday gaps, intraday stops and the
  fills available at the close are not modeled; the profit-take is tested once a day.
* **A single path.** There is one 2005-2025 history, with three major drawdowns. A pass or a fail on one path is weak
  evidence either way: it does not give a distribution of outcomes.
* **No interest on idle cash, no margin model, no taxes.** The sleeve is fixed and non-compounding, the unused part
  earns nothing, and the position's maximum loss is assumed funded from the sleeve.
* **A screening test, never an approval.** The two possible verdicts are WORTH REAL DATA and NOT WORTH REAL DATA.
  A pass only says the modeled strategy was not obviously uninteresting; step 2 (the same pre-registered rules on
  paid historical bid/ask data) is what could say more."""


def _f(x, nd=2, pct=False, usd=False):
    if x is None:
        return "n/a"
    if usd:
        return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"
    if pct:
        return f"{x * 100:.{nd}f}%"
    if isinstance(x, int):
        return f"{x}"
    return f"{x:.{nd}f}"


def _usd2(x):
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def gate_table(gate: dict) -> str:
    c = gate["checks"]
    rows = [
        ("1. Net Sharpe >= 0.5", _f(c["net_sharpe"]["value"], 3), "PASS" if c["net_sharpe"]["pass"] else "FAIL"),
        ("2. Max drawdown <= 30% of the sleeve", f"{_f(c['max_drawdown']['value_usd'], usd=True)} = {c['max_drawdown']['value_pct_of_sleeve']:.1f}%",
         "PASS" if c["max_drawdown"]["pass"] else "FAIL"),
        ("3. Worst calendar year >= -20% of the sleeve", f"{c['worst_year']['year']}: {_f(c['worst_year']['value_usd'], usd=True)} = {c['worst_year']['value_pct_of_sleeve']:.1f}%",
         "PASS" if c["worst_year"]["pass"] else "FAIL"),
        ("4. Net P&L positive in >= 2/3 of calendar years", f"{c['positive_years']['positive']} of {c['positive_years']['years']} years",
         "PASS" if c["positive_years"]["pass"] else "FAIL"),
    ]
    out = ["| Check | Value | Result |", "| --- | --- | --- |"]
    out += [f"| {a} | {b} | **{r}** |" for a, b, r in rows]
    return "\n".join(out)


def headline_tables(metrics: dict[str, dict], order: list[str]) -> str:
    a = ["| Variant | Net Sharpe | t-stat | CAGR on sleeve | Total net P&L | Max drawdown | Max DD % of sleeve | Peak -> trough |",
         "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    b = ["| Variant | Worst year | Best year | Profitable years | Positions | Win rate | Avg credit / contract | Avg max loss / contract | Exits (profit-take / time) | Kill-switch activations |",
         "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |"]
    for n in order:
        m = metrics[n]
        a.append(f"| {n} | {_f(m['net_sharpe'], 3)} | {_f(m['t_stat'])} | {_f(m['cagr'], pct=True)} | {_f(m['total_net_pnl'], usd=True)} | "
                 f"{_f(m['max_drawdown_usd'], usd=True)} | {m['max_drawdown_pct_of_sleeve']:.1f}% | {m['max_drawdown_peak_day']} -> {m['max_drawdown_trough_day']} |")
        er = m["exit_reasons"]
        b.append(f"| {n} | {m['worst_year']}: {_f(m['worst_year_pnl'], usd=True)} ({m['worst_year_pct_of_sleeve']:.1f}%) | "
                 f"{m['best_year']}: {_f(m['best_year_pnl'], usd=True)} ({m['best_year_pct_of_sleeve']:.1f}%) | {m['positive_years']} of {m['n_years']} | "
                 f"{m['positions']} | {_f(m['win_rate'], pct=True, nd=1)} | {_f(m['avg_credit_per_contract'], usd=True)} | "
                 f"{_f(m['avg_max_loss_per_contract'], usd=True)} | {er.get('profit_take', 0)} / {er.get('time_exit', 0)} | {len(m['kill_switch_activations'])} |")
    return "\n".join(a) + "\n\n" + "\n".join(b)


def kill_switch_section(metrics: dict[str, dict], order: list[str]) -> str:
    lines = []
    for n in order:
        acts = metrics[n]["kill_switch_activations"]
        if not acts:
            continue
        lines.append(f"**{n}** ({len(acts)} activation(s)):")
        for e in acts:
            skipped = ", ".join(e["cycles_skipped"]) or "none"
            lines.append(f"* activated {e['activated']} (equity {_f(e['equity'], usd=True)} vs peak {_f(e['peak'], usd=True)}); "
                         f"paused {e['pause_days']} days to {e['resume_date']} (resumed on {e['resumed_on'] or 'not within the window'}); "
                         f"cycles skipped: {skipped}")
        lines.append("")
    return "\n".join(lines) if lines else "No variant activated the kill switch."


def skip_section(metrics: dict[str, dict], order: list[str]) -> str:
    out = ["| Variant | Cycles in window | Positions | Skipped: kill switch | Skipped: zero contracts | Skipped: no positive credit | Skipped: position still open | Other |",
           "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for n in order:
        m, s = metrics[n], metrics[n]["skips"]
        other = sum(v for k, v in s.items() if k not in ("kill_switch", "zero_contracts", "no_positive_credit", "position_still_open"))
        out.append(f"| {n} | {m['cycles_in_window']} | {m['positions']} | {s.get('kill_switch', 0)} | {s.get('zero_contracts', 0)} | "
                   f"{s.get('no_positive_credit', 0)} | {s.get('position_still_open', 0)} | {other} |")
    return "\n".join(out)


def yearly_table(run: sw.WheelRun, m: dict, spy_year_returns: dict[int, float] | None) -> str:
    by_year: dict[int, list[sw.Position]] = {}
    for p in run.positions:
        by_year.setdefault(p.exit_day.year, []).append(p)
    cap = run.cfg.sleeve_capital_usd
    out = ["| Year | Net P&L | % of sleeve | Positions closed | Win rate | Underlying price return (context) |", "| ---: | ---: | ---: | ---: | ---: | ---: |"]
    for y, pnl in m["years"].items():
        ps = by_year.get(y, [])
        w = sum(1 for p in ps if p.net_pnl > 0)
        ctx = spy_year_returns.get(y) if spy_year_returns else None
        out.append(f"| {y} | {_f(pnl, usd=True)} | {pnl / cap * 100:.1f}% | {len(ps)} | {_f(w / len(ps) if ps else None, pct=True, nd=0)} | {_f(ctx, pct=True, nd=1) if ctx is not None else 'n/a'} |")
    return "\n".join(out)


def crisis_section(blocks: list[dict]) -> str:
    out = []
    for b in blocks:
        out.append(f"### {b['window']} ({b['start']} to {b['end']})")
        if b["positions"]:
            out += ["", "| Entry | Expiry | Short / long | Spot | VIX | Contracts | Credit (share) | Worst marked loss | Exit | Reason | Net P&L | Outcome |",
                    "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | --- |"]
            for p in b["positions"]:
                out.append(f"| {p.entry_day} | {p.expiry} | {p.k_short} / {p.k_long} | {p.spot:.2f} | {p.vol_index:.2f} | {p.contracts} | "
                           f"{p.credit:.2f} | {_f(p.worst_unreal, usd=True)} | {p.exit_day} | {p.exit_reason} | {_f(p.net_pnl, usd=True)} | "
                           f"{'win' if p.net_pnl > 0 else 'loss'} |")
        else:
            out.append("\nNo position was open in this window.")
        if b["skips"]:
            out.append("\nCycles skipped in this window: " + "; ".join(f"{s['entry_day']} ({s['reason']})" for s in b["skips"]))
        out.append("")
    return "\n".join(out)


def calibration_section(cal: dict | None, reason: str | None = None) -> str:
    if cal is None:
        return f"**Not available:** {reason or 'the Cboe PUT index file is not cached'}. No substitute is used."
    out = [f"Periods: {cal['n_periods']} monthly rolls from {cal['first_period']} to {cal['last_period_end']} (the Cboe file has daily rows only from "
           f"2007-01-03). Model: sell one at-the-money SPY put at each standard expiry, cash-secured, no costs, same pricing model as the study; "
           f"PUT: Cboe PUT index over the same periods.", "",
           "| Year | Periods | Model | PUT | Model - PUT |", "| ---: | ---: | ---: | ---: | ---: |"]
    for y in cal["years"]:
        out.append(f"| {y['year']} | {y['periods']} | {y['model'] * 100:.1f}% | {y['put'] * 100:.1f}% | {y['diff'] * 100:+.1f} pp |")
    c1, c2 = cal["corr_yearly"], cal["corr_period"]
    out += ["", f"* Correlation of yearly returns: {_f(c1, 3)}; of monthly period returns: {_f(c2, 3)}.",
            f"* Mean annual difference (model - PUT): {_f(cal['mean_annual_diff'] * 100 if cal['mean_annual_diff'] is not None else None)} pp; "
            f"mean absolute annual difference: {_f(cal['mean_abs_annual_diff'] * 100 if cal['mean_abs_annual_diff'] is not None else None)} pp; "
            f"mean monthly difference: {_f(cal['mean_period_diff'] * 100 if cal['mean_period_diff'] is not None else None, 3)} pp.",
            f"* Compounded over all periods: model {cal['model_total'] * 100:.0f}%, PUT {cal['put_total'] * 100:.0f}%."]
    if cal["dropped"]:
        out.append(f"* Periods dropped for a missing PUT value: {len(cal['dropped'])}.")
    return "\n".join(out)


def checks_section(price: dict | None, price_reason: str | None, cal: dict, data_notes: dict) -> str:
    out = []
    if price is None:
        out.append(f"**Two-source price check:** not run ({price_reason}).")
    else:
        out.append(f"**Two-source price check (yfinance SPY vs Alpaca SIP daily close, {price['start']} to {price['end']}):** "
                   f"{price['compared']} dates compared, {price['agree']} agree within {price['tolerance_pct']:.2f}%, "
                   f"{len(price['disagreements'])} disagree; largest difference {price['max_diff_pct']:.4f}%; "
                   f"{len(price['only_in_a'])} dates only in yfinance, {len(price['only_in_b'])} only in Alpaca.")
        if price["disagreements"]:
            out += ["", "| Date | yfinance | Alpaca | Difference |", "| --- | ---: | ---: | ---: |"]
            out += [f"| {d['day']} | {d['a']:.4f} | {d['b']:.4f} | {d['diff_pct']:.4f}% |" for d in price["disagreements"]]
        if price["only_in_a"] or price["only_in_b"]:
            out.append(f"\nDates in one source only: yfinance only {price['only_in_a'][:20]}; Alpaca only {price['only_in_b'][:20]}.")
    out.append("")
    moved = ", ".join(f"{m['third_friday']} -> {m['expiry']}" for m in cal["moved"]) or "none"
    out.append(f"**Calendar check:** {cal['expiries']} standard monthly expiries (January 2005 to December 2025); expiries moved off the third Friday: {moved}. "
               f"The moved set {'equals' if cal['moved_equals_good_friday_set'] else 'DOES NOT EQUAL'} the Good-Friday set "
               f"({', '.join(cal['expected_good_friday_third_fridays'])}). Expiries not on a Friday and not moved: {cal['not_third_friday'] or 'none'}.")
    if "alpaca_day_check" in cal:
        a = cal["alpaca_day_check"]
        out.append(f"Trading days 2016-2025, yfinance vs Alpaca-derived: {a['yfinance_days']} vs {a['alpaca_days']}; only yfinance {a['only_yfinance'][:10] or 'none'}, only Alpaca {a['only_alpaca'][:10] or 'none'}.")
    out.append("")
    for k, v in data_notes.items():
        out.append(f"* {k}: {v}")
    return "\n".join(out)


def render(header: dict, res: dict, price: dict | None, price_reason: str | None, cal_reason: str | None,
           data_notes: dict, spy_year_returns: dict[int, float] | None) -> str:
    metrics, gate = res["metrics"], res["gate"]
    order = [v.name for v in sw.ALL_VARIANTS if v.name in metrics]
    verdict = gate["verdict"] if header["valid_for_verdict"] else "NOT A PRE-REGISTERED RUN"
    prim_run = res["runs"]["primary"]
    pre = header["preregistration"]
    lines = ["# Safe wheel (SPY put-credit spread), modeled screening backtest: results", "",
             f"**Screening verdict: {verdict}.** (A screening test only: it can never approve trading.)", ""]
    if not header["valid_for_verdict"]:
        lines += ["**This run is NOT the pre-registered run, so it carries no verdict:** " + "; ".join(header["invalid_reasons"]), ""]
    lines += [gate_table(gate), "",
              f"Run `{header['run_id']}`; code commit `{(header['code_commit'].get('sha') or 'n/a')[:12]}` "
              f"({'dirty' if header['code_commit'].get('dirty') else 'clean'} tree); pre-registration `{pre['path']}` introduced at "
              f"`{(pre['first_commit'] or 'n/a')[:12]}`, rule text {'unchanged' if pre['unchanged'] else 'CHANGED or unverifiable'} since; "
              f"window {header['window']['start']} to {header['window']['end']} ({header['window']['trading_days']} trading days); "
              f"{header['wall_time_s']} s. Nothing dated on or after 2026-01-01 was requested, stored or used.", "",
              "## Headline table, every variant", "", headline_tables(metrics, order), "",
              "Only the primary decides the gate; every other row changes exactly one thing and is reported for context.", "",
              "## Skipped cycles", "", skip_section(metrics, order), "",
              "## Kill switch", "", kill_switch_section(metrics, order), "",
              "## Year by year, primary", "", yearly_table(prim_run, metrics["primary"], spy_year_returns), "",
              "## Crisis close-up, primary", "", crisis_section(sw.crisis_closeup(prim_run)),
              "## PUT-index calibration", "", calibration_section(res["calibration"], cal_reason), "",
              "## Data and model checks", "", checks_section(price, price_reason, res["calendar"], data_notes), "",
              "## Limitations", "", LIMITATIONS, ""]
    return "\n".join(lines)
