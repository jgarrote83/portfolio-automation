"""Render the quadrant-tilt backtest results as `.review/quadrant-backtest-results.md` (pure, except reading the optional summary text)."""
from __future__ import annotations

from pathlib import Path

SUMMARY_FILE = Path(__file__).with_name("summary_text.md")
ORDER = ("Q-TILT", "Q-ALL", "SPY", "C5 (trend + credit)", "Soft tilt (50/50)", "Q-TILT monthly")


def pct(v, d=2):
    return "—" if v is None else f"{v * 100:+.{d}f}%"


def upct(v, d=1):
    return "—" if v is None else f"{v * 100:.{d}f}%"


def render(r: dict) -> str:
    m, v = r["metrics"], r["verdicts"]
    sub_lab = [f"{a[:7]} to {b[:7]}" if a <= b else f"window start to {b[:7]} (empty)" for a, b in r["sub_periods"]]
    if r["sub_periods"][0][0] > r["sub_periods"][0][1]:
        sub_lab[1] = f"{r['first_trading_day'][:7]} to {r['sub_periods'][1][1][:7]}"
    p = r["prereg"]
    out = ["# Quadrant-tilt backtest: does Core's quadrant framework work?", "",
           f"Run `{r['run_id']}`. Pre-registration `docs/specs/Quadrant_Backtest_Preregistration.md`, first commit `{(p.get('first_commit') or '?')[:12]}`; "
           f"rule text {'unchanged' if p['unchanged'] else 'CHANGED'}, Appendix A {'present' if p['has_appendix_a'] else 'MISSING'}, Appendix B "
           f"{'present' if p['has_appendix_b'] else 'MISSING'}. Code commit `{(r['code'].get('sha') or '?')[:12]}`. "
           + ("**This is the pre-registered run; its verdicts are binding.**" if r["preregistered_run"] else "**NOT A PRE-REGISTERED RUN — no verdict.**"), "",
           f"Window: **{r['first_trading_day']} to {r['end']}** ({r['trading_days']:,} trading days). The window rule gave {r['window_start_rule']} (the first business day "
           "from which the production classifier's confirmed quadrant was determinate for 20 straight business days, not before 2011-07-01). Nothing dated on or after "
           "2026-01-01 was read. Total returns, 2 bps per dollar traded, no taxes.", "",
           "## Verdicts", "",
           f"1. **{v['calls']}.** Q-TILT CAGR {pct(m['Q-TILT']['cagr'])} vs Q-ALL {pct(m['Q-ALL']['cagr'])} ({'higher' if v['calls_detail']['cagr'] else 'not higher'}); "
           f"sub-periods won vs Q-ALL: {', '.join(f'{lab} ' + ('W' if w else 'L') for lab, w in zip(sub_lab, v['calls_detail']['sub_wins']))}.",
           f"2. **{v['spy']}.** Q-TILT CAGR {pct(m['Q-TILT']['cagr'])} vs SPY {pct(m['SPY']['cagr'])} ({'higher' if v['spy_detail']['cagr'] else 'not higher'}); "
           f"max drawdown {upct(m['Q-TILT']['mdd'])} vs {upct(m['SPY']['mdd'])} ({'no worse' if v['spy_detail']['mdd'] else 'worse'}); sub-periods won vs SPY: "
           f"{', '.join(f'{lab} ' + ('W' if w else 'L') for lab, w in zip(sub_lab, v['spy_detail']['sub_wins']))}.", ""]
    if any(x is None for x in m["Q-TILT"]["sub_periods"]):
        out += ["**The first sub-period is empty**: the window starts after it ends, because the classifier could not be reconstructed earlier. Under Appendix B an "
                "empty sub-period is never won, so each verdict needed both remaining sub-periods.", ""]
    summary = SUMMARY_FILE.read_text(encoding="utf-8").strip() if SUMMARY_FILE.exists() else "_Summary not yet written._"
    out += ["## Plain-language summary for Jorge", "", summary, ""]

    out += ["## Headline table", "", "| | CAGR | Total return | Volatility | Sharpe | Max drawdown | Peak → trough (recovered) | Worst year | Switches / yr | Costs |",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |"]
    for k in ORDER:
        x = m[k]
        out.append(f"| {k}{' (report only)' if k in ('C5 (trend + credit)', 'Soft tilt (50/50)', 'Q-TILT monthly') else ''} | {pct(x['cagr'])} | {pct(x['total_return'], 0)} | "
                   f"{upct(x['vol'])} | {x['sharpe']:.2f} | {upct(x['mdd'])} | {x['peak']} → {x['trough']} ({x['recovery'] or 'not recovered'}) | "
                   f"{x['worst_year'][0]} {pct(x['worst_year'][1], 1)} | {x['switches_per_year']:.1f} | ${x['costs']:,.0f} |")
    out += ["", f"C5 runs from {r['c5_window'][0]} (its first month-end decision executes then), slightly later than the others; it is shown side by side, never judged.",
            "", "| Sub-period | " + " | ".join(ORDER) + " |", "| --- |" + " ---: |" * len(ORDER)]
    for i, lab in enumerate(sub_lab):
        out.append(f"| {lab} | " + " | ".join(pct(m[k]["sub_periods"][i], 1) for k in ORDER) + " |")

    years = sorted(m["SPY"]["calendar"], key=int)
    out += ["", "## Calendar years", "", "The first year runs from the window's first close.", "", "| Year | " + " | ".join(ORDER) + " |", "| --- |" + " ---: |" * len(ORDER)]
    for y in years:
        out.append(f"| {y} | " + " | ".join(pct(m[k]["calendar"].get(y), 1) for k in ORDER) + " |")

    out += ["", "## Quadrant history", "", "Share of the window's daily returns earned in each quadrant (Q-TILT's holding, after the one-day execution lag): "
            + ", ".join(f"{q} {upct(s)}" for q, s in r["time_in"].items() if s) + ".", "",
            f"**{len(r['switches']) - 1} switches** (the first row is the opening purchase).", "", "| Change visible at the close of | Executed at the close of | Into |", "| --- | --- | --- |"]
    out += [f"| {s['visible']} | {s['execution']} | {s['to']} |" for s in r["switches"]]

    out += ["", "## Hit table: was each basket better while it was the call?", "",
            "Each quadrant's standalone equal-weight basket (month-end rebalanced, no cost), on the days Q-TILT held it against the days it did not. "
            "Annualised mean daily return; cumulative return over each set of days.", "",
            "| Quadrant | Days active | Annualised while active | Cumulative while active | Days not active | Annualised while not | Cumulative while not |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for h in r["hits"]:
        a, n = h["active"], h["inactive"]
        out.append(f"| {h['quadrant']} | {a['days']} | {pct(a['ann_mean'], 1)} | {pct(a['cum'], 1)} | {n['days']} | {pct(n['ann_mean'], 1)} | {pct(n['cum'], 1)} |")

    out += ["", "## Proxies (Appendix A) and how much of the window used them", "", "| Member | Proxy | Member's first date | Window days on the proxy (or dropped) | Share of window |",
            "| --- | --- | --- | ---: | ---: |"]
    out += [f"| {x['member']} | {x['proxy']} | {x['first_date']} | {x['window_days_before_first']} | {upct(x['share_of_window'])} |" for x in r["proxy_use"]]

    ver = r["verification"]
    out += ["", "## Verification", "", f"A separate verifier (no imports from the backtest modules) re-derived from the cached replay and the raw price files every Q-TILT "
            f"switch ({ver['switches_total']}; all match: {'yes' if ver['switches_all_match'] else 'NO'}), 12 sampled switches in detail, and Q-TILT's and Q-ALL's "
            f"returns for 12 random months by re-simulating both books: **{ver['matched']} of {ver['checks']} checks match** ({'PASS' if ver['pass'] else 'FAIL'}).", "",
            "## Data notes and limitations", "",
            f"- **Replay coverage.** {r['replay_rows']:,} business days replayed from 2010-01-04; the confirmed quadrant was determinate on "
            f"{upct(r['replay_determinate_share'], 0)} of them, first on {r['replay_first_determinate']}. GDPNow vintages in ALFRED start on 2016-05-17, so the growth "
            "axis cannot be reconstructed before then, which is why the window starts where it does.",
            f"- **ALFRED series that returned nothing** (HTTP 400 on the vintage request): {', '.join(r['missing_series'])}. None of them sets an axis direction: the "
            "breakevens feed only the describe-only inflation bridge; the rest feed bond, credit and labour signals outside the quadrant.",
            "- **Baskets are today's config mapping applied to the past** (Appendix A, item 5): the roster was designed in 2026 knowing how these ETFs behaved, a hindsight "
            "no backtest can remove. Production's live map differs slightly (SOXX for SMH, IHE for XLV as of 2026-10-02).",
            "- **Q-TILT is a pure version of the framework**: 100% in one basket. Production blends the reference with floors, caps, a cash sleeve, overrides and the "
            "transition lean, so this tests the calls, not the live book.",
            "- **One path, about ten years, no taxes**, next-close execution at 2 bps, no market impact.", "",
            "Reproduce: `PYTHONPATH=src python -m research.quadrant_backtest.run run` (reads the caches only).", ""]
    return "\n".join(out)
