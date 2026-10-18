"""Render the Core-candidates results as `.review/core-candidates-results.md` (pure, except reading the optional summary text)."""
from __future__ import annotations

from pathlib import Path

SUMMARY_FILE = Path(__file__).with_name("summary_text.md")
NAMES = {"SPY": "SPY (benchmark)", "C1": "C1 Trend-filtered 2x", "C2": "C2 Vol-targeted", "C3": "C3 Dual momentum",
         "C4": "C4 Credit-filtered 2x", "C5": "C5 Trend + credit"}
ORDER = ("C1", "C2", "C3", "C4", "C5")


def pct(v, d=2) -> str:
    return "—" if v is None else f"{v * 100:+.{d}f}%"


def upct(v, d=2) -> str:
    return "—" if v is None else f"{v * 100:.{d}f}%"


def _header(res) -> list[str]:
    p, c = res["prereg"], res["code"]
    ok = res["preregistered_run"]
    return ["# Core replacement research: five rules-based candidates vs SPY, 2005–2025", "",
            f"Run `{res['run_id']}`. Pre-registration `{p['path']}`, first commit `{(p.get('first_commit') or '?')[:12]}`, rule text "
            f"{'unchanged' if p.get('unchanged') else 'CHANGED OR UNKNOWN'}. Code commit `{str(c.get('commit') or c.get('sha') or c)[:60]}`. "
            + ("**This is the pre-registered run; its verdicts are binding.**" if ok else "**NOT A PRE-REGISTERED RUN — no verdict.**"),
            "", "Window 2005-01-03 to 2025-12-31 (5,283 SPY trading days). Nothing dated on or after 2026-01-01 was requested or read; the "
            "2026-01-02 to 2026-05-19 holdout was not run. Total returns throughout; 2 bps per dollar traded; financing at T-bills + 0.5%; "
            "taxes not modeled."]


def _verdicts(res) -> list[str]:
    spy = res["spy"]
    out = ["## Verdicts", "", "All three criteria are required: (1) CAGR above SPY's, (2) maximum drawdown no worse than SPY's, (3) beat SPY in at "
           "least 2 of the 3 sub-periods (2005–11, 2012–18, 2019–25).", "",
           "| Candidate | CAGR (SPY " + pct(spy["cagr"]) + ") | (1) | Max drawdown (SPY " + upct(spy["mdd"], 1) + ") | (2) | Sub-periods won | (3) | **Verdict** |",
           "| --- | ---: | :---: | ---: | :---: | :---: | :---: | :---: |"]
    for r in ORDER:
        c, v = res["candidates"][r], res["verdicts"][r]
        out.append(f"| {NAMES[r]} | {pct(c['cagr'])} | {'✔' if v['cagr_beats'] else '✘'} | {upct(c['mdd'], 1)} | {'✔' if v['mdd_ok'] else '✘'} | "
                   f"{''.join('W' if w else 'L' for w in v['sub_wins'])} | {'✔' if v['sub_ok'] else '✘'} | **{v['verdict']}** |")
    passed = [r for r in ORDER if res["verdicts"][r]["verdict"] == "CANDIDATE"]
    out.append("")
    if passed:
        best = max(passed, key=lambda r: res["candidates"][r]["cagr"])
        out += [f"**Passing: {', '.join(passed)}. The only one eligible for the single holdout run is {best}** (the highest CAGR among the passes). "
                "**Multiple-testing caution:** five candidates were tested against the same 21-year path, so a pass is weaker evidence than it "
                "looks; with five tries, one rule clearing a bar by luck is not unlikely. The holdout and a paper forward test are what would "
                "make it evidence. Nothing is replaced until both are done."]
    else:
        out.append("**No candidate passed.** None is eligible for the holdout.")
    return out


def _sso(res) -> list[str]:
    s = res["sso"]
    out = ["## The 2x simulation against real SSO", "",
           f"{s['start']} to {s['end']}: daily correlation **{s['corr']:.4f}**; average annual tracking difference (simulated − SSO, 2007–2025) "
           f"**{s['mean_diff_pp']:+.2f} points**, average absolute difference {s['mean_abs_diff_pp']:.2f} points; compounded total "
           f"{pct(s['sim_total'], 1)} simulated vs {pct(s['sso_total'], 1)} SSO."]
    if s["flag"]:
        out += ["", f"> **WARNING: the average annual tracking difference ({s['mean_diff_pp']:+.2f} points) is beyond ±1.5 points.** The simulated 2x "
                "fund does not track the real one closely enough to trust the 2x candidates' numbers at face value."]
    out += ["", "| Year | Simulated 2x | SSO | Difference (pts) |", "| --- | ---: | ---: | ---: |"]
    for y, v in s["years"].items():
        out.append(f"| {y}{' (partial)' if str(y) == '2006' else ''} | {pct(v['sim'], 1)} | {pct(v['sso'], 1)} | {v['diff_pp']:+.2f} |")
    return out


def _headline(res) -> list[str]:
    out = ["## Headline table", "",
           "| | CAGR | Total return | Volatility | Sharpe | Max drawdown | Peak → trough (recovered) | Worst year | Time in T-bills/bonds | "
           "Trades / yr | Switches / yr | Costs paid |",
           "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |"]
    rows = [("SPY", res["spy"])] + [(r, res["candidates"][r]) for r in ORDER]
    for r, c in rows:
        safe = upct(c["time_safe"], 0) if r not in ("C2", "SPY") else ("0%" if r == "SPY" else
                                                                         f"exposure < 1 on {upct(c['share_exposure_below_1'], 0)} of days (mean {c['mean_exposure']:.2f})")
        rec = c["recovery"] or "not recovered"
        out.append(f"| {NAMES[r]} | {pct(c['cagr'])} | {pct(c['total_return'], 0)} | {upct(c['vol'], 1)} | {c['sharpe']:.2f} | {upct(c['mdd'], 1)} | "
                   f"{c['peak']} → {c['trough']} ({rec}) | {c['worst_year'][0]} {pct(c['worst_year'][1], 1)} | {safe} | "
                   f"{c['trades_per_year']:.1f} | {c['switches_per_year']:.1f} | ${c['costs']:,.0f} |")
    out += ["", "Ending value of $100,000: " + "; ".join(f"{r} ${c['end_equity']:,.0f}" for r, c in rows) + ".",
            "", "Sub-period returns:", "", "| | 2005–2011 | 2012–2018 | 2019–2025 |", "| --- | ---: | ---: | ---: |"]
    for r, c in rows:
        out.append(f"| {NAMES[r]} | " + " | ".join(pct(x, 1) for x in c["sub_periods"]) + " |")
    return out


def _calendar(res) -> list[str]:
    rows = [("SPY", res["spy"])] + [(r, res["candidates"][r]) for r in ORDER]
    years = sorted(res["spy"]["calendar"], key=int)
    out = ["## Calendar-year returns", "", "2005 runs from the 2005-01-03 close.", "",
           "| Year | " + " | ".join(r for r, _ in rows) + " |", "| --- |" + " ---: |" * len(rows)]
    for y in years:
        out.append(f"| {y} | " + " | ".join(pct(c["calendar"][y], 1) for _, c in rows) + " |")
    return out


def _crises(res) -> list[str]:
    out = ["## Crisis close-ups", "", "Return over the window (from the last close before it), the maximum drawdown inside it (peak reset at the window "
           "start), what was held when it began, and every risk cut / restore inside it (C2: exposure crossing 0.5)."]
    for label, by in res["crises"].items():
        out += ["", f"### {label}", "", "| | Window return | Max drawdown in window | Held at start | Risk cut / restored |", "| --- | ---: | ---: | --- | --- |"]
        for r in ("SPY",) + ORDER:
            c = by[r]
            ev = "; ".join(f"{e['date']} {e['event']} ({e['holding']})" for e in c["events"]) or "none"
            out.append(f"| {NAMES[r]} | {pct(c['return'], 1)} | {upct(c['max_dd'], 1)} | {c['held_at_start']} | {ev} |")
    return out


def _macro(res) -> list[str]:
    c, cr = res["candidates"], res["crises"]

    def line(r):
        return (f"{r}: CAGR {pct(c[r]['cagr'])}, max drawdown {upct(c[r]['mdd'], 1)}, 2008–09 drawdown {upct(cr['2008–09'][r]['max_dd'], 1)}, "
                f"2020 drawdown {upct(cr['Feb–Mar 2020'][r]['max_dd'], 1)}, 2022 drawdown {upct(cr['2022'][r]['max_dd'], 1)}")
    return ["## The macro question", "", "Does credit add anything beyond the price trend (C5 vs C1), and is a credit filter alone better or worse than a "
            "trend filter (C4 vs C1)? Default runs only.", "", f"- {line('C1')}", f"- {line('C4')}", f"- {line('C5')}"]


def _sens(res) -> list[str]:
    spy = res["spy"]
    out = ["## Sensitivities (reported, never decide)", "", "| Run | CAGR | vs SPY | Max drawdown | Sharpe | Sub-periods | Would meet all three? | Costs paid |",
           "| --- | ---: | ---: | ---: | ---: | --- | :---: | ---: |"]
    for s in res["sensitivities"]:
        v = s["would_pass"]
        out.append(f"| {s['name']} | {pct(s['cagr'])} | {(s['cagr'] - spy['cagr']) * 100:+.2f} pts | {upct(s['mdd'], 1)} | {s['sharpe']:.2f} | "
                   f"{''.join('W' if w else 'L' for w in v['sub_wins'])} | {'yes' if v['verdict'] == 'CANDIDATE' else 'no'} | ${s['costs']:,.0f} |")
    return out


def _data(res) -> list[str]:
    ca = res["carry"]
    return ["## Data notes", "",
            "Sources: `yfinance` 'Adj Close' (dividend-and-split adjusted) for SPY, EFA, AGG, SSO and ACWX; FRED `DTB3` and `BAA10Y`. Cached under "
            "`data/reference/core_candidates/`.",
            f"- `DTB3` carried forward on {len(ca['DTB3'])} window days (the previous trading day had no value): "
            + (", ".join(f"{a}" for a, _ in ca["DTB3"]) or "none") + ".",
            f"- `BAA10Y` carried forward on {len(ca['BAA10Y'])} window days: " + (", ".join(f"{a}" for a, _ in ca["BAA10Y"]) or "none") + ".",
            f"- C2's exposure ranged {res['c2_exposure']['min']:.2f}–{res['c2_exposure']['max']:.2f} (mean {res['c2_exposure']['mean']:.2f}).",
            "- Days by holding (monthly candidates): " + "; ".join(f"{r}: " + ", ".join(f"{k} {v}" for k, v in sorted(h.items()))
                                                            for r, h in res["holdings_count"].items()) + "."]


def _verification(res) -> list[str]:
    v = res["verification"]
    out = ["## Verification", "", f"A separate verifier (no imports from the candidate code) re-derived, from the raw cached files, the decision and the "
           f"month's return for 12 random month-ends (seed {v['seed']}) for each of the five candidates: **{v['matched']} of {v['checks']} checks "
           f"match** ({'PASS' if v['pass'] else 'FAIL'}).", "", "Month-ends: " + ", ".join(v["month_ends"]) + "."]
    if v["mismatches"]:
        out += ["", "| Decision | Rule | Holding | Run | Return | Run |", "| --- | --- | --- | --- | ---: | ---: |"]
        out += [f"| {m['decision']} | {m['rule']} | {m['holding']} | {m['run_holding']} | {m['return']:.10f} | {m['run_return']:.10f} |" for m in v["mismatches"]]
    return out


LIMITS = """## Limitations

- **Simulated leverage.** The 2x fund is a formula, not SSO; the SSO comparison above shows how far apart they are. Real leveraged funds also carry
  swap costs that change over time, and C2's financing assumes borrowing at T-bills + 0.5%, cheaper than most brokers charge a retail account.
- **No taxes.** Monthly switching in a taxable account realises gains, often short-term. A tax-free account is the only setting where these numbers
  would be close to what an investor keeps.
- **A single path.** 2005–2025 contains one great financial crisis, one pandemic crash and one rate shock. Trend and credit filters are known to look
  best in long, slow bear markets like 2008; a different ordering of events would give different numbers, and no confidence interval is attached.
- **A 2005 start.** Earlier history (1987's one-day crash, the 2000–02 bear market) is outside the window; a filter's behaviour in a crash that comes
  faster than its monthly check is only seen once here (2020).
- **Survivorship of the instruments.** SPY, EFA, AGG and SSO all survived to 2025, and the choice of these candidates was made knowing how the last
  20 years went. That is a form of hindsight no backtest can remove.
- **Execution.** Trades are assumed at the next close at 2 bps with no market impact; the T-bill leg is assumed costless to hold."""


def render(res: dict) -> str:
    summary = SUMMARY_FILE.read_text(encoding="utf-8").strip() if SUMMARY_FILE.exists() else "_Summary not yet written._"
    parts = _header(res) + [""] + _verdicts(res) + ["", "## Plain-language summary for Jorge", "", summary, ""]
    parts += _headline(res) + [""] + _sso(res) + [""] + _calendar(res) + [""] + _crises(res) + [""] + _macro(res) + [""] + _sens(res)
    parts += [""] + _data(res) + [""] + _verification(res) + ["", LIMITS, "",
              "Reproduce: `PYTHONPATH=src python -m research.core_candidates.run run` (reads only the cache), then "
              "`PYTHONPATH=src python -m research.core_candidates.verify reports/core_candidates/<run-id>`.", ""]
    return "\n".join(parts)
