"""Markdown report for the global lead-lag study (`backtest.leadlag_run`). Pure rendering: every number comes from
the run's own results; nothing here decides anything about a strategy."""
from __future__ import annotations

from pathlib import Path

from . import leadlag as L

CORR_LABELS = [("s__g", "s vs US gap g"), ("s__o2c", "s vs US open-to-close"), ("s_asia__g", "Asia-only signal vs g"),
               ("s_asia__o2c", "Asia-only signal vs open-to-close"), ("s_europe__g", "Europe/UK-only signal vs g"),
               ("s_europe__o2c", "Europe/UK-only signal vs open-to-close"), ("g_o2c", "g vs open-to-close (does the US gap continue or reverse?)")]
MULTIPLE_TESTING = ("Four groups are tested at the same thresholds on the same two years, so any GO is one of four draws: it is "
                    "provisional and still needs the 2026 holdout, which is closed to this study.")


def _usd(x) -> str:
    return "n/a" if x is None else (f"${x:,.0f}" if x >= 0 else f"-${-x:,.0f}")


def _n(x, nd=2) -> str:
    return "n/a" if x is None else f"{x:,.{nd}f}"


def _pct(x, nd=1) -> str:
    return "n/a" if x is None else f"{x * 100:.{nd}f}%"


def _r(x) -> str:
    return "n/a" if x is None else f"{x:+.3f}"


def strength(r: float | None) -> str:
    """Conventional descriptive labels for |correlation| (Cohen): a wording aid, not a threshold of the study."""
    if r is None:
        return "undefined"
    a = abs(r)
    return "essentially none" if a < 0.1 else "weak" if a < 0.3 else "moderate" if a < 0.5 else "strong"


def verdict_table(res: dict) -> str:
    rows = ["| Group | US instrument | Verdict | Net Sharpe | Net P&L 2024 | Net P&L 2025 | Members used | Checked vs 2nd source | Excluded |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, gr in res["groups"].items():
        v = gr.verdict
        checked = len(gr.members_used) - len(gr.members_unchecked)
        ex = ", ".join(f"{s} ({r})" for s, r in gr.members_excluded.items()) or "none"
        rows.append(f"| {name} | {gr.group.us} | **{v['verdict']}** | {_n(v['net_sharpe'])} | {_usd(v['net_pnl_by_year'].get(2024))} | "
                    f"{_usd(v['net_pnl_by_year'].get(2025))} | {len(gr.members_used)} of {len(gr.group.members)} | "
                    f"{checked} of {len(gr.members_used)} | {ex} |")
    return "\n".join(rows)


def strategy_table(res: dict, variant: str) -> str:
    rows = ["| Group | Net Sharpe | t-stat | Net P&L | 2024 net | 2025 net | Gross P&L | Costs | Max DD | Trades | Hit ratio | Avg net bps | Long / Short | Days with no signal |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, gr in res["groups"].items():
        m = gr.metrics[variant]
        rows.append(f"| {name} | {_n(m['sharpe_net'])} | {_n(m['t_stat_mean_daily_return'])} | {_usd(m['net_pnl'])} | "
                    f"{_usd(m['by_year'].get('2024', {}).get('net_pnl'))} | {_usd(m['by_year'].get('2025', {}).get('net_pnl'))} | "
                    f"{_usd(m['gross_pnl'])} | {_usd(m['slippage_cost'] + m['commission'])} | {_usd(m['max_drawdown_usd'])} | "
                    f"{m['trades']:,} | {_pct(m['hit_ratio'])} | {_n(m['avg_net_bps'])} | "
                    f"{m['long']['trades']:,} ({_n(m['long']['avg_net_bps'])} bps) / {m['short']['trades']:,} ({_n(m['short']['avg_net_bps'])} bps) | {m['days_no_signal']:,} |")
    return "\n".join(rows)


def sensitivity_table(res: dict) -> str:
    rows = ["| Group | Variant | Net Sharpe | Net P&L | 2024 net | 2025 net | Trades | Hit ratio | Avg net bps |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, gr in res["groups"].items():
        for v in L.ALL_VARIANTS:
            m = gr.metrics[v.name]
            rows.append(f"| {name} | {v.name}{' (primary)' if v is L.PRIMARY else ''} | {_n(m['sharpe_net'])} | {_usd(m['net_pnl'])} | "
                        f"{_usd(m['by_year'].get('2024', {}).get('net_pnl'))} | {_usd(m['by_year'].get('2025', {}).get('net_pnl'))} | "
                        f"{m['trades']:,} | {_pct(m['hit_ratio'])} | {_n(m['avg_net_bps'])} |")
    return "\n".join(rows)


def correlation_tables(res: dict) -> str:
    out = []
    for name, gr in res["groups"].items():
        out += [f"### {name} ({gr.group.us})", "", "| Pair | Overall n | Pearson | Spearman | 2024 n | Pearson | Spearman | 2025 n | Pearson | Spearman |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for key, label in CORR_LABELS:
            c = gr.correlations[key]
            if key.startswith("s_asia") and not gr.group.asia:
                out.append(f"| {label} | no Asian member | | | | | | | | |")
                continue
            cells = []
            for part in ("overall", "2024", "2025"):
                x = c.get(part) or {"n": 0, "pearson": None, "spearman": None}       # a partial window may lack a year
                cells += [f"{x['n']:,}", _r(x["pearson"]), _r(x["spearman"])]
            out.append(f"| {label} | " + " | ".join(cells) + " |")
        out.append("")
    return "\n".join(out)


def regression_table(res: dict) -> str:
    rows = ["| Group | n | HAC lags | b1 (foreign signal s) | HAC t(b1) | b2 (US gap g) | HAC t(b2) | constant | R-squared |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, gr in res["groups"].items():
        r = gr.regression
        if r is None:
            rows.append(f"| {name} | too few days | | | | | | | |")
        else:
            rows.append(f"| {name} | {r['n']:,} | {r['lags']} | {r['b1']:+.4f} | {r['t_b1']:+.2f} | {r['b2']:+.4f} | {r['t_b2']:+.2f} | "
                        f"{r['a']:+.5f} | {_n(r['r2'], 4)} |")
    return "\n".join(rows)


def timing_table(res: dict) -> str:
    rows = ["| Market | Time zone | Event used | Nominal local time | Members | Closest margin before the US open | Worst day | (US, local) UTC-offset combinations covered | US dates | Pass |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for t in res["timing"]:
        ev = "close" if t["kind"] == "close" else "open"
        rows.append(f"| {t['market']} | {t['tz']} | {ev} | {t['nominal_local']} | {', '.join(t['members'])} | {t['min_margin_minutes']:.0f} min | "
                    f"{t['worst_day']} | {t['offset_combinations']} | {t['days']:,} | {'PASS' if t['pass'] else 'FAIL'} |")
    rows += ["", "Why a European close is never used (the close falls AFTER the US open on every date):", "",
             "| Market | Nominal close (local) | Earliest margin AFTER the US open | Never usable |", "| --- | --- | --- | --- |"]
    for t in res["timing"]:
        if t["nominal_close_local"]:
            rows.append(f"| {t['market']} | {t['nominal_close_local']} | {t['close_after_us_open_min_minutes']:.0f} min | {'yes' if t['close_never_usable'] else 'NO'} |")
    return "\n".join(rows)


def quality_table(res: dict) -> str:
    days = res["days"]
    rows = ["| Member | Region | Rows in window | Longest gap (days) | Stale-open share | 2nd-source status | Close agrees | Open agrees | Sampled | Agreement | Splits | "
            "Excluded by rule | Available days | No row (holiday) | Missing price | Split days | Excluded days |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    unavailable = {s: c for gr in res["groups"].values() for s, c in gr.unavailable.items()}
    for s in L.ALL_MEMBERS:
        q = res["quality"][s]
        st, two, un = q["stale_open"], q["two_source"], unavailable[s]
        if st is None:
            stale = "n/a (open not used)"
        else:
            stale = _pct(st["share"], 2) + " (" + str(st["stale"]) + " of " + str(st["rows"]) + ")"
        splits = ", ".join(str(x["date"]) + " x" + format(x["ratio"], "g") for x in q["splits"]) or "none"
        cells = [s, q["region"], f"{q['rows_in_window']:,}", str(q["longest_gap_days"]), stale, two["status"],
                 str(two.get("close_agree", "")), str(two.get("open_agree", "")), str(two["sampled"] or ""),
                 _pct(two["share"]) if two["share"] is not None else "", splits, q["excluded"] or "no",
                 f"{res['members_with_returns'][s]:,} of {days:,}", f"{un.get('no_row', 0) + un.get('no_previous_row', 0):,}",
                 f"{un.get('missing_price', 0):,}", f"{un.get('split', 0):,}", f"{un.get('excluded', 0):,}"]
        rows.append("| " + " | ".join(cells) + " |")
    return chr(10).join(rows)


def plain_language(res: dict) -> str:
    lines = []
    for name, gr in res["groups"].items():
        c, reg = gr.correlations, gr.regression
        sg, so = c["s__g"]["overall"], c["s__o2c"]["overall"]
        part = (f"**{name}** ({gr.group.us}): the foreign signal lines up with the US opening gap with Pearson {_r(sg['pearson'])} "
                f"(n = {sg['n']:,}, {strength(sg['pearson'])}); its correlation with what the US does AFTER the open is {_r(so['pearson'])} "
                f"({strength(so['pearson'])})")
        if reg:
            part += (f"; in the regression the signal's coefficient on open-to-close is {reg['b1']:+.4f} (HAC t {reg['t_b1']:+.2f}), "
                     f"against {reg['b2']:+.4f} (HAC t {reg['t_b2']:+.2f}) for the US gap itself. Verdict: **{gr.verdict['verdict']}** "
                     f"(net Sharpe {_n(gr.verdict['net_sharpe'])}).")
        lines.append(part)
    return "\n\n".join(lines)


def render(header: dict, res: dict, repo_root: Path) -> str:
    pre = header["preregistration"]
    valid = header["valid_for_verdict"]
    any_go = any(gr.verdict["verdict"].startswith("GO") for gr in res["groups"].values())
    out = [f"<!-- generated by backtest.leadlag_run from {header['run_id']}; do not edit by hand -->",
           f"# Global lead-lag study results: do Asia, Europe and the UK predict the US day? Run `{header['run_id']}`", ""]
    if not valid:
        out += ["**NOT A PRE-REGISTERED RUN. No verdict.** " + "; ".join(header["invalid_reasons"]), ""]
    out += ["## Verdict per group (mechanical, primary rule only)", "",
            "GO needs net Sharpe >= 1.0 AND net P&L > $0 in both 2024 and 2025; otherwise NO-GO. A GO while any member used has not been "
            "checked against a second source is reported as `GO, UNVERIFIED DATA` and is not to be acted on.", "", verdict_table(res), ""]
    out += [f"**Multiple-testing note.** {MULTIPLE_TESTING}" + (" At least one group met the thresholds on this sample." if any_go else
            " No group met the thresholds, so the note does not bite on this run."), ""]
    unchecked = res["unchecked"]
    out += [f"**Second-source data check.** Stooq's CSV endpoint is behind a JavaScript anti-bot page (pre-registration item 15) and was not bypassed; "
            f"{len(header['stooq_files_found'])} of 22 members had a hand-placed Stooq file. "
            + ("**22 of 22 members are UNCHECKED**, so the pre-registered two-source check did not run; every verdict above was reached on Yahoo data alone."
               if len(unchecked) == 22 else f"{len(unchecked)} members are unchecked: {', '.join(unchecked) or 'none'}."), ""]
    out += ["## Primary rule: strategy table", "", strategy_table(res, "primary"), "",
            "## Sensitivities (4c; never decide the verdict)", "", sensitivity_table(res), "",
            "## (a) Correlations: Pearson and Spearman, with n, overall and per year", "",
            "s is the equal-weight mean of the Asian and the Europe/UK regional means; g the US gap (US open D / US close D-1 - 1); open-to-close = US close D / US open D - 1. "
            "The s-vs-g rows are CONTEXT, not a trading signal: US index futures trade overnight, so a high value there is expected.", "",
            correlation_tables(res),
            "## (b) Regression: open-to-close = a + b1 s + b2 g, Newey-West (HAC) t-statistics", "",
            "Bartlett kernel, L = floor(4 (n/100)^(2/9)) lags, no small-sample correction. b1 is the foreign signal's coefficient BEYOND the US gap.", "",
            regression_table(res), "",
            "## Session-timing verification (every US date D in the window, daylight saving on both sides)", "",
            "Nominal exchange hours converted to UTC with `zoneinfo` for each of the 502 dates and compared with 09:30 ET on D; this verifies the exchange schedule, "
            "not Yahoo's timestamps (a daily row carries only a date).", "", timing_table(res), "",
            "## Data quality", "",
            "Stale-open rule (Europe/UK only): a share above 5% excludes the member. Two-source rule: agreement below 90% excludes it. "
            "'No row' counts US trading days on which the member's own market has no session dated that day (its holidays); "
            "an Asian or European member also has no row on a US date that is a weekday in the US but a holiday there.", "", quality_table(res), "",
            "## Plain-language summary", "",
            "Descriptive labels for the size of a correlation (essentially none below 0.1, weak below 0.3, moderate below 0.5, strong above) are a wording aid, "
            "not a threshold of the study.", "", plain_language(res), "",
            "## Run provenance", "",
            f"* Run `{header['run_id']}` on code commit `{header['code_commit']['sha']}` (tree dirty: {header['code_commit']['dirty']}); spec_version `{header['spec_version']}`.",
            f"* Pre-registration `{pre['path']}` first committed in `{pre['first_commit']}`; rule text unchanged since: {pre['unchanged']}.",
            f"* Window {header['window']['start']}..{header['window']['end']}, {header['window']['us_trading_days']} US trading days; foreign data from {header['window']['data_from']}; "
            "the 2026 holdout was never read, from any source.",
            f"* Yahoo: 22 members cached (yfinance {next(iter(header['yahoo'].values()))['yfinance']}); US bars through `get_bars` (SIP, raw), "
            f"{header['us_api_requests_this_run']} API requests in this run; wall time {header['wall_time_s'] / 60:.1f} min.",
            f"* Full outputs: `reports/global_leadlag/{header['run_id']}/` (gitignored).", ""]
    return "\n".join(out)
