"""Render the 1989-2004 out-of-sample results as `.review/core-candidates-oos-results.md` (pure, except reading the optional summary text)."""
from __future__ import annotations

from pathlib import Path

SUMMARY_FILE = Path(__file__).with_name("oos_summary_text.md")
NAMES = {"S&P 500 TR": "S&P 500 TR (benchmark)", "C1": "C1 Trend-filtered 2x (reference)", "C4": "C4 Credit-filtered 2x", "C5": "C5 Trend + credit"}
RULES = ("C1", "C4", "C5")


def pct(v, d=2):
    return "—" if v is None else f"{v * 100:+.{d}f}%"


def upct(v, d=1):
    return "—" if v is None else f"{v * 100:.{d}f}%"


def render(res: dict) -> str:
    p, b, c = res["prereg"], res["benchmark"], res["candidates"]
    out = ["# Core candidates, out-of-sample validation: 1989–2004, rules unchanged", "",
           f"Run `{res['run_id']}`. Pre-registration `{p['path']}`, first commit `{(p.get('first_commit') or '?')[:12]}`, rule text "
           f"{'unchanged' if p.get('unchanged') else 'CHANGED OR UNKNOWN'}. Code commit `{(res['code'].get('sha') or '?')[:12]}`. "
           + ("**This is the pre-registered run; its verdicts are binding.**" if res["preregistered_run"] else "**NOT A PRE-REGISTERED RUN — no verdict.**"),
           "", "Window 1989-01-03 to 2004-12-31 (4,036 index days); first decision 1988-12-30. The equity series is the S&P 500 Total Return "
           "index (`^SP500TR`) in SPY's place everywhere; every rule, threshold and cost is the 2005–2025 study's, unchanged. Nothing dated after "
           "2004-12-31 was read.", "", "## Verdicts", "",
           "| Candidate | CAGR (index " + pct(b["cagr"]) + ") | (1) | Max drawdown (index " + upct(b["mdd"]) + ") | (2) | Sub-periods won (89–94, 95–99, 00–04) | (3) | **Verdict** |",
           "| --- | ---: | :---: | ---: | :---: | :---: | :---: | :---: |"]
    for r in RULES:
        x = c[r]
        v = res["verdicts"].get(r) or {**res["reference_criteria"][r], "verdict": "reference only — no verdict"}
        out.append(f"| {NAMES[r]} | {pct(x['cagr'])} | {'✔' if v['cagr_beats'] else '✘'} | {upct(x['mdd'])} | {'✔' if v['mdd_ok'] else '✘'} | "
                   f"{''.join('W' if w else 'L' for w in v['sub_wins'])} | {'✔' if v['sub_ok'] else '✘'} | **{v['verdict']}** |")
    passed = [r for r in ("C4", "C5") if res["verdicts"][r]["verdict"] == "VALIDATED"]
    out += ["", (f"**Validated: {', '.join(passed)}.** Two candidates were judged on the same 16 years, so a single pass is weaker evidence than it "
                 "looks; these years were never used to choose or tune anything, which is what makes this the stronger of the two tests."
                 if passed else "**Neither judged candidate was validated.**")]
    summary = SUMMARY_FILE.read_text(encoding="utf-8").strip() if SUMMARY_FILE.exists() else "_Summary not yet written._"
    out += ["", "## Plain-language summary for Jorge", "", summary, ""]

    out += ["## Headline table", "", "| | CAGR | Total return | Volatility | Sharpe | Max drawdown | Peak → trough (recovered) | Worst year | Time in T-bills | "
            "Switches / yr |", "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |"]
    rows = [("S&P 500 TR", b)] + [(r, c[r]) for r in RULES]
    for r, x in rows:
        out.append(f"| {NAMES[r]} | {pct(x['cagr'])} | {pct(x['total_return'], 0)} | {upct(x['vol'])} | {x['sharpe']:.2f} | {upct(x['mdd'])} | "
                   f"{x['peak']} → {x['trough']} ({x['recovery'] or 'not recovered'}) | {x['worst_year'][0]} {pct(x['worst_year'][1], 1)} | "
                   f"{upct(x['time_safe'], 0)} | {x['switches_per_year']:.1f} |")
    out += ["", "Ending value of $100,000: " + "; ".join(f"{NAMES[r].split(' (')[0]} ${x['end_equity']:,.0f}" for r, x in rows) + ".", "",
            "| Sub-period | " + " | ".join(r for r, _ in rows) + " |", "| --- |" + " ---: |" * len(rows)]
    for i, lab in enumerate(("1989–1994", "1995–1999", "2000–2004")):
        out.append(f"| {lab} | " + " | ".join(pct(x["sub_periods"][i], 1) for _, x in rows) + " |")

    out += ["", "## Calendar years", "", "1989 runs from the 1989-01-03 close.", "", "| Year | " + " | ".join(r for r, _ in rows) + " |",
            "| --- |" + " ---: |" * len(rows)]
    for y in sorted(b["calendar"], key=int):
        out.append(f"| {y} | " + " | ".join(pct(x["calendar"][y], 1) for _, x in rows) + " |")

    out += ["", "## Crisis close-ups", "", "Return over the window (from the last close before it), the drawdown inside it (peak reset at the start), "
            "the holding at its start, and every risk cut or restore."]
    for lab, by in res["crises"].items():
        out += ["", f"### {lab}", "", "| | Window return | Max drawdown in window | Held at start | Risk cut / restored |", "| --- | ---: | ---: | --- | --- |"]
        for r in ("S&P 500 TR",) + RULES:
            x = by[r]
            ev = "; ".join(f"{e['date']} {e['event']} ({e['holding']})" for e in x["events"]) or "none"
            out.append(f"| {NAMES[r]} | {pct(x['return'], 1)} | {upct(x['max_dd'])} | {x['held_at_start']} | {ev} |")

    t = res["tracking"]
    out += ["", "## `^SP500TR` vs SPY tracking check", "", f"{t['start']} to {t['end']} ({t['days']:,} days): daily correlation **{t['corr']:.4f}**; "
            f"average yearly difference (SPY − index, 1994–2004) **{t['mean_diff_pp']:+.2f} points** — SPY's expense ratio was about 0.18% then, "
            f"so a gap near −0.2 points is the expected size. Compounded: index {pct(t['index_total'], 1)}, SPY {pct(t['spy_total'], 1)}.", "",
            "| Year | Index | SPY | SPY − index (pts) |", "| --- | ---: | ---: | ---: |"]
    for y, v in t["years"].items():
        out.append(f"| {y}{' (from Feb 1)' if str(y) == '1993' else ''} | {pct(v['index'], 1)} | {pct(v['spy'], 1)} | {v['diff_pp']:+.2f} |")

    s = res["stitched"]
    out += ["", "## Stitched 1989–2025 view (descriptive, decides nothing)", "",
            "This window chained to the 2005–2025 run's daily series (" + res["parent_run"] + "). The S&P side is the index to 2004 and SPY's total "
            "return from 2005; the single day 2005-01-03 is taken as zero for every series.", "",
            "| | CAGR 1989–2025 | Max drawdown | Peak → trough | Worst year | $1 became |", "| --- | ---: | ---: | --- | ---: | ---: |"]
    for r in ("S&P 500",) + RULES:
        x = s[r]
        out.append(f"| {NAMES.get(r, r)} | {pct(x['cagr'])} | {upct(x['mdd'])} | {x['peak']} → {x['trough']} | {x['worst_year'][0]} {pct(x['worst_year'][1], 1)} | "
                   f"${x['end_multiple']:,.1f} |")

    ca, v = res["carry"], res["verification"]
    out += ["", "## Data notes", "",
            f"- `DTB3` carried forward on {len(ca['DTB3'])} window days; `BAA10Y` on {len(ca['BAA10Y'])} (the previous trading day had no value).",
            "- Days by holding: " + "; ".join(f"{r}: " + ", ".join(f"{k} {n}" for k, n in sorted(h.items())) for r, h in res["holdings_count"].items()) + ".",
            "- The index has no expense ratio; SPY would have cost about 0.18%/yr then. Both the benchmark and C5's 1x leg get that advantage; the "
            "2x simulation keeps its 0.91%/yr.", "",
            "## Verification", "", f"A separate verifier (no imports from the engine or the adapter) re-derived from the raw cache the decision and the "
            f"month's return for 12 random month-ends (seed {v['seed']}) for C1, C4 and C5: **{v['matched']} of {v['checks']} match** "
            f"({'PASS' if v['pass'] else 'FAIL'}).", "", "Month-ends: " + ", ".join(v["month_ends"]) + "."]
    if v["mismatches"]:
        out += ["", "| Decision | Rule | Holding | Run | Return | Run |", "| --- | --- | --- | --- | ---: | ---: |"]
        out += [f"| {m['decision']} | {m['rule']} | {m['holding']} | {m['run_holding']} | {m['return']:.10f} | {m['run_return']:.10f} |" for m in v["mismatches"]]
    out += ["", "## Limitations", "",
            "- **An index, not a fund.** 1989–2004 uses an index nobody could hold at zero cost; the 2x fund is simulated (no 2x S&P ETF existed "
            "before 2006). The financing formula assumes T-bills + 0.5%, which may understate 1990s borrowing costs.",
            "- **One more path, still one path.** 16 years with one long bull market (1995–99) and one long bear (2000–02). A slow bear is exactly "
            "where trend and credit filters shine; a fast crash (October 1987) is just outside the window.",
            "- **The rules were written after seeing 2005–2025, not 1989–2004**, so this period is genuinely unseen, but the idea of a credit filter "
            "comes from general knowledge of how credit behaves in recessions, which these years are part of.",
            "- **No taxes, next-close execution at 2 bps, no market impact**, as in the parent study.", "",
            "Reproduce: `PYTHONPATH=src python -m research.core_candidates.oos run` (reads only the cache).", ""]
    return "\n".join(out)
