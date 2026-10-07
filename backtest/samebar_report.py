"""Markdown report for the same-minute diagnostic (`backtest.samebar_run`). Pure rendering: every number
comes from the run's own results; nothing here computes a strategy decision."""
from __future__ import annotations

import random
from collections import Counter

import pandas as pd

from . import reports, samebar
from .data.config import ET_NAME

SENTENCE_MISMEASURED = ("ORB v1 was mis-measured; a v2 with tick-resolved entries may be worth pre-registering "
                        "for the 2026 holdout.")
SENTENCE_UNCHANGED = "The ambiguity doesn't change the conclusion."


def _fmt_ts(ns: int | None) -> str:
    if ns is None:
        return "-"
    ts = pd.Timestamp(ns, tz="UTC").tz_convert(ET_NAME)
    return f"{ts.strftime('%H:%M:%S')}.{ts.microsecond:06d}{ts.nanosecond:03d}"


def example_block(c: samebar.Candidate, res: samebar.Resolution, df: pd.DataFrame, excluded) -> str:
    """The print sequence behind one resolution: the decisive prints, then the minute as runs by price zone."""
    pr = sorted(samebar.counted_prints(df, excluded), key=lambda p: (p[0], p[2]))
    info = {int(s): (cs, int(z)) for s, cs, z in zip(df["seq"], df["conditions"], df["size"])}
    long_ = c.side == "long"
    if long_:
        def zone(p):
            return ">= trigger" if p >= c.trigger else ("<= stop" if p <= c.stop else "between")
    else:
        def zone(p):
            return "<= trigger" if p <= c.trigger else (">= stop" if p >= c.stop else "between")
    segs: list[list] = []
    for ns, px, _seq in pr:
        z = zone(px)
        if segs and segs[-1][0] == z:
            segs[-1][2], segs[-1][3] = ns, segs[-1][3] + 1
            segs[-1][4], segs[-1][5] = min(segs[-1][4], px), max(segs[-1][5], px)
        else:
            segs.append([z, ns, ns, 1, px, px])
    tie_note = (" (a same-timestamp stop print sorts before the trigger print in Alpaca's order; scored as a real stop anyway)"
                if res.tie_would_be_dip else "")
    lines = [f"**{c.day} {c.symbol} ({c.side})**: trigger {c.trigger:.4f}, stop {c.stop:.4f}; Alpaca bar "
             f"O {c.bar[0]} H {c.bar[1]} L {c.bar[2]} C {c.bar[3]}; counted prints {res.n_prints}; "
             f"**outcome: {res.outcome}**{tie_note}", "",
             f"* first print at/through the trigger (the fill): {_fmt_ts(res.trigger_ts)} @ {res.trigger_price}",
             f"* first print at/through the stop BEFORE the fill: {_fmt_ts(res.stop_before_ts)}"
             + (f" @ {res.stop_before_price}" if res.stop_before_ts else ""),
             f"* first print at/through the stop AFTER the fill: {_fmt_ts(res.stop_after_ts)}"
             + (f" @ {res.stop_after_price}" if res.stop_after_ts else ""), "",
             "| from | to | prints | price zone | low .. high |", "| --- | --- | --- | --- | --- |"]
    for z, a, b, n, lo, hi in segs[:14]:
        lines.append(f"| {_fmt_ts(a)} | {_fmt_ts(b)} | {n} | {z} | {lo} .. {hi} |")
    if len(segs) > 14:
        lines.append(f"| ... | | | {len(segs) - 14} more runs | |")
    trig = next((p for p in pr if p[0] == res.trigger_ts), None)
    if trig is not None and trig[2] in info:
        cs, sz = info[trig[2]]
        lines += ["", f"The trigger print: {_fmt_ts(trig[0])} @ {trig[1]}, {sz} shares, conditions {cs}."]
    return chr(10).join(lines)


def _usd(x) -> str:
    return "n/a" if x is None else f"${x:,.0f}" if x >= 0 else f"-${-x:,.0f}"


def _n(x, nd=3) -> str:
    return "n/a" if x is None else f"{x:,.{nd}f}"


def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def results_table(metrics: dict, order: list[tuple[str, str]]) -> str:
    head = "| | " + " | ".join(label for _k, label in order) + " |"
    rule = "| --- | " + " | ".join("---" for _ in order) + " |"

    def row(name, fn):
        return f"| {name} | " + " | ".join(fn(metrics[k]) for k, _l in order) + " |"

    def yr(y):
        return lambda m: _usd(m["by_year"].get(y, {}).get("net_pnl"))

    def ex(r):
        return lambda m: f"{m['exit_reasons'].get(r, 0):,}"

    rows = [
        row("Trades", lambda m: f"{m['trades']:,}"),
        row("Gross P&L", lambda m: _usd(m["gross_pnl"])),
        row("Slippage + commission", lambda m: _usd(m["slippage_cost"] + m["commission"])),
        row("**Net P&L**", lambda m: "**" + _usd(m["net_pnl"]) + "**"),
        row("Net P&L 2024", yr("2024")), row("Net P&L 2025", yr("2025")),
        row("Hit ratio (net > 0)", lambda m: _pct(m["hit_ratio"])),
        row("Average R (net)", lambda m: _n(m["avg_r_net"])),
        row("Average R (gross)", lambda m: _n(m["avg_r_gross"])),
        row("Net Sharpe (pre-registered definition)", lambda m: _n(m["sharpe_net"], 2)),
        row("Max drawdown", lambda m: _usd(m["max_drawdown_usd"])),
        row("Exits: stop (a later bar)", ex("stop")), row("Exits: stop_same_bar (entry minute)", ex("stop_same_bar")),
        row("Exits: time (15:59 close)", ex("time")), row("Exits: loss_limit", ex("loss_limit")),
        row("Days the daily loss limit fired", lambda m: f"{m['days_loss_limit_hit']:,}"),
    ]
    return "\n".join([head, rule, *rows])


def render(header, cands, changed, res, checks, raw, metrics, layer, seed, validation) -> str:
    n_ss = len(cands)
    amb = [c for c in cands if c.ambiguous]
    unamb = [c for c in cands if not c.ambiguous]
    unchanged_amb = [c for c in amb if not c.changed]
    prim_res = res["primary"]
    counts = Counter(r.outcome for r in prim_res.values())
    ties_dip = sum(1 for r in prim_res.values() if r.outcome == samebar.TIE_REAL_STOP and r.tie_would_be_dip)
    by_side = {s: Counter(r.outcome for (d, sym), r in prim_res.items()
                          if next(c for c in changed if (c.day, c.symbol) == (d, sym)).side == s)
               for s in ("long", "short")}
    rep, opt, tick = metrics["reported"], metrics["optimistic"], metrics["tick_resolved"]
    verdict = reports.go_no_go(tick)
    passed = all(verdict["checks"].values())
    repro = header["reproduces_phase2"]

    L: list[str] = []
    A = L.append
    A(f"<!-- generated by backtest.samebar_run from {header['run_id']}; do not edit by hand -->")
    A("# ORB v1 same-minute resolution (a diagnostic, not a verdict)")
    A("")
    A("**ORB v1 stays NO-GO by its pre-registration.** Nothing here changes `docs/specs/ORB_Phase2_Preregistration.md`, "
      "`src/orb/` or the Phase 2 results (`.review/phase2-results.md`). The window is 2024-01-02..2025-12-31 and the "
      "2026 holdout was never read.")
    A("")
    A("## Answer in one paragraph")
    A("")
    A(f"Under the pre-registered rule, **{n_ss:,}** Phase 2 primary trades were stopped in their entry minute, of which "
      f"**{len(amb):,}** are ambiguous (the entry bar opened short of the trigger, so 1-minute data cannot say whether the "
      f"dip to the stop came before or after the entry). The optimistic bound (the entry bar's stop is not checked) moves "
      f"net P&L from **{_usd(rep['net_pnl'])}** to **{_usd(opt['net_pnl'])}**. It changes the result of **{len(changed):,}** "
      f"trades; replaying their SIP trade prints in timestamp order, **{counts.get(samebar.DIP_FIRST, 0):,}** had the dip "
      f"*before* the entry (no stop-out) and **{counts.get(samebar.REAL_STOP, 0) + counts.get(samebar.TIE_REAL_STOP, 0):,}** "
      f"were real stops. The tick-resolved result is **{_usd(tick['net_pnl'])}** net "
      f"(2024 {_usd(tick['by_year'].get('2024', {}).get('net_pnl'))}, 2025 {_usd(tick['by_year'].get('2025', {}).get('net_pnl'))}; "
      f"net Sharpe {_n(tick['sharpe_net'], 2)}).")
    A("")
    A("## Reported, optimistic and tick-resolved, side by side")
    A("")
    A("*Reported* = the Phase 2 primary variant, reproduced exactly (below). *Optimistic* = the same trades with the "
      "entry bar's stop not checked (from the next bar onward it is). *Tick-resolved* = the optimistic rule only for the "
      "changed trades whose prints show the dip came before the entry, and the pre-registered stop everywhere else.")
    A("")
    A(results_table(metrics, [("reported", "Reported (Phase 2)"), ("optimistic", "Optimistic bound"),
                              ("tick_resolved", "Tick-resolved")]))
    A("")
    A("Costs are the pre-registered ones (2 cents per side, $0.0035 per share, $25,000 sleeve) in all three columns.")
    A("")
    A("## What was and was not ambiguous")
    A("")
    A("| | Trades |")
    A("| --- | --- |")
    A(f"| `stop_same_bar` exits in the Phase 2 primary | {n_ss:,} |")
    A(f"| ambiguous (the entry bar opened short of the trigger) | {len(amb):,} |")
    A(f"| not ambiguous (the bar opened at/through the trigger, so the fill is the first print and a later low is real) | {len(unamb):,} |")
    A(f"| ambiguous but unchanged by the optimistic rule (stopped later at the same level: gross R within {samebar.R_TOLERANCE} of -1.00) | {len(unchanged_amb):,} |")
    A(f"| **changed set** (ambiguous, and the optimistic R differs from -1.00 by more than {samebar.R_TOLERANCE}) | **{len(changed):,}** |")
    A("")
    A(f"The brief's figure is 4,207 ambiguous trades; this run counts **{len(amb):,}**. The 4,207 is every `stop_same_bar` "
      f"entry that was not a gap fill; **{validation['at_trigger']:,}** of those opened EXACTLY at the trigger, which fills on the "
      "bar's opening print, so a later low is unambiguously after the entry (the stop is real) and they are not counted as "
      "ambiguous here. The changed set is judged on gross R (cost-free), where the primary's is exactly -1.00.")
    A("")
    vo = validation["outcomes"]
    A(f"**Check on that definition:** all {validation['n']:,} unambiguous entries ({validation['at_trigger']:,} opened at the "
      f"trigger, {validation['through_trigger']:,} opened through it) were also replayed from their prints "
      f"(they never feed the engine): " + ", ".join(f"{vo.get(k, 0):,} {k}" for k in
                                                    (samebar.REAL_STOP, samebar.TIE_REAL_STOP, samebar.DIP_FIRST,
                                                     samebar.STOP_NEVER_TOUCHED, samebar.NO_TRIGGER_PRINT, samebar.UNRESOLVED))
      + ". Every one should be a real stop (or a same-timestamp tie); anything else would mean the definition is wrong.")
    A("")
    A("## Tick resolution of the changed set")
    A("")
    A(f"Every changed trade's entry minute was replayed from the SIP trade prints of that symbol and that minute "
      f"(`{header['api_requests_this_run']:,}` read-only API requests this run, including the three condition-code tables; "
      "a repeat is served from the cache).")
    A("")
    A("| Outcome (counted prints, exchange-timestamp order) | All | Long | Short |")
    A("| --- | --- | --- | --- |")
    labels = [(samebar.DIP_FIRST, "stop level traded only BEFORE the trigger print: not a stop-out (the dip came first)"),
              (samebar.REAL_STOP, "a stop print AFTER the trigger print: a real -1R stop"),
              (samebar.TIE_REAL_STOP, "a stop print shares the trigger print's exact timestamp: order unknown, scored as a real stop"),
              (samebar.NO_TRIGGER_PRINT, "no counted print reaches the trigger (the ticks do not confirm the entry): scored as the primary scored it"),
              (samebar.STOP_NEVER_TOUCHED, "no counted print reaches the stop (only an excluded print did): not a stop-out"),
              (samebar.UNRESOLVED, "no prints came back for the minute: scored as the primary scored it")]
    for key, label in labels:
        A(f"| {label} | {counts.get(key, 0):,} | {by_side['long'].get(key, 0):,} | {by_side['short'].get(key, 0):,} |")
    A(f"| **Total** | **{sum(counts.values()):,}** | {sum(by_side['long'].values()):,} | {sum(by_side['short'].values()):,} |")
    A("")
    A(f"* **Same-timestamp cases:** {counts.get(samebar.TIE_REAL_STOP, 0):,} trades had a stop print with the trigger "
      f"print's exact timestamp (scored as real stops, the conservative reading); in {ties_dip:,} of them Alpaca's own "
      "response order put the stop print before the trigger print, i.e. the reading would have gone the other way.")
    A(f"* **Not resolvable at all:** {counts.get(samebar.UNRESOLVED, 0):,} (no prints returned).")
    A("* Consistency checks in this run: the pre-registered rule passed explicitly equals the engine default "
      f"({'yes' if header['explicit_pre_registered_rule_equals_default'] else 'NO'}); forcing every changed trade to a real "
      f"stop reproduces the reported trades exactly ({'yes' if header['changed_set_all_real_equals_default'] else 'NO'}).")
    A("")
    A("## Reproducing the Phase 2 primary")
    A("")
    A(f"The default engine (no rule passed) was re-run over the rebuilt selection and compared trade by trade with "
      f"`{header['phase2_run']}/primary/trades.csv` (side, shares, entry and exit minute, exit reason, entry and exit fill, "
      f"net P&L, net R): **{repro['phase2_trades']:,}** Phase 2 trades, **{repro['reproduced_trades']:,}** reproduced, "
      f"**{repro['mismatches']}** mismatches, {repro['extra_in_rerun']} extra. Identical: **{'yes' if repro['identical'] else 'NO'}**.")
    A("")
    A("## Which prints count, and why")
    A("")
    A("A print counts only if none of its sale-condition codes is in the table below; the table was fixed from Alpaca's own "
      "code descriptions before any result and then checked against Alpaca's own one-minute bars. Every print carries a "
      "list of codes (e.g. `['@', 'F', 'I']`); `@` and a blank are regular sales, `F` an intermarket sweep.")
    A("")
    table = {}
    for tape in ("A", "B", "C"):
        for k, v in layer.trade_conditions(tape).items():
            table.setdefault(k, v)
    carried = Counter()
    for df in raw.values():
        for cs in df["conditions"]:
            for c in set(cs):
                carried[c] += 1
    A("| Code | Alpaca's description | Why the print does not count | Prints carrying it (changed minutes) |")
    A("| --- | --- | --- | --- |")
    for code, why in samebar.EXCLUDED_CODES.items():
        A(f"| `{code}` | {table.get(code, '(not in the table)')} | {why} | {carried.get(code, 0):,} |")
    A("")
    kept = sorted(c for c in carried if c not in samebar.EXCLUDED_CODES)
    A("Codes seen in these minutes that DO count: " + ", ".join(f"`{c}` ({table.get(c, '?')})" for c in kept) + ".")
    A("")
    n_all = n_256 = n_dup_minutes = 0
    for df in raw.values():
        vals = [t.value for t in df["ts"]]
        n_all += len(vals)
        n_256 += sum(1 for v in vals if v % 256 == 0)
        counted_ts = [p[0] for p in samebar.counted_prints(df, samebar.PRIMARY_EXCLUDED)]
        n_dup_minutes += int(len(set(counted_ts)) < len(counted_ts))
    A("**Timestamp resolution.** Alpaca delivers many trade timestamps with limited resolution: "
      f"{n_256:,} of {n_all:,} prints ({n_256 / max(n_all, 1) * 100:.1f}%) in these minutes carry a timestamp that is an exact "
      "multiple of 256 ns (0.4% if every nanosecond digit were real; the API response itself reads e.g. `.23596544Z`, and a fresh "
      "raw response and the cache agree). Two prints can therefore share a timestamp without having traded at the same "
      f"instant. {n_dup_minutes:,} of the {len(raw):,} replayed minutes contain at least one pair of COUNTED prints with an "
      "identical timestamp; a pair matters only when it is a trigger print and a stop print, which is the same-timestamp "
      "count in the outcome table above (scored as a real stop, the conservative reading).")
    A("")
    A("**Check against Alpaca's own bars** (the tick-derived open/high/low/close vs the cached one-minute bar the engine used, "
      "over every changed trade's entry minute):")
    A("")
    A("| Print filter | Minutes | open | high | low | close | all four |")
    A("| --- | --- | --- | --- | --- | --- | --- |")
    names = {"primary": "the table above (headline)", "minimal": "only `I` and `4`", "none": "no filter (every print)"}
    for name in ("primary", "minimal", "none"):
        ch = checks[name]
        n = len(ch)
        cells = [sum(1 for v in ch.values() if v[f]) for f in ("open", "high", "low", "close")]
        al = sum(1 for v in ch.values() if all(v.values()))
        A(f"| {names[name]} | {n:,} | " + " | ".join(f"{x:,} ({x / n * 100:.1f}%)" for x in cells)
          + f" | {al:,} ({al / n * 100:.1f}%) |")
    A("")
    A("**Sensitivity to the print filter** (the same replay under the other two filters, then the engine again):")
    A("")
    A("| Print filter | dip first | real stop | tie (real) | no trigger print | stop never touched | unresolved | Tick-resolved net P&L | Trades whose answer differs from the headline |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    dec = {name: samebar.tick_decisions(res[name]) for name in res}
    scen_of = {"primary": "tick_resolved", "minimal": "tick_resolved_minimal_filter", "none": "tick_resolved_no_filter"}
    for name in ("primary", "minimal", "none"):
        c2 = Counter(r.outcome for r in res[name].values())
        diff = sum(1 for k in dec[name] if dec[name][k] != dec["primary"][k])
        A(f"| {names[name]} | {c2.get(samebar.DIP_FIRST, 0):,} | {c2.get(samebar.REAL_STOP, 0):,} | {c2.get(samebar.TIE_REAL_STOP, 0):,} | "
          f"{c2.get(samebar.NO_TRIGGER_PRINT, 0):,} | {c2.get(samebar.STOP_NEVER_TOUCHED, 0):,} | {c2.get(samebar.UNRESOLVED, 0):,} | "
          f"{_usd(metrics[scen_of[name]]['net_pnl'])} | {diff:,} |")
    A("")
    A("**Bounds:** forcing every changed trade to a dip-before-entry (no entry-minute stop) gives "
      f"{_usd(metrics['changed_set_all_dips']['net_pnl'])} net; the full optimistic rule (which also stops ignoring the "
      f"entry-minute low for the {len(unamb):,} unambiguous entries and the {len(unchanged_amb):,} unchanged ambiguous ones) gives "
      f"{_usd(opt['net_pnl'])}; the reported {_usd(rep['net_pnl'])} is the other end.")
    A("")
    A("## Ten worked examples (the actual print sequences)")
    A("")
    rng = random.Random(seed)
    by_out = {k: [c for c in changed if prim_res[(c.day, c.symbol)].outcome == k] for k in
              (samebar.DIP_FIRST, samebar.REAL_STOP, samebar.TIE_REAL_STOP)}
    pick_dip = rng.sample(by_out[samebar.DIP_FIRST], min(5, len(by_out[samebar.DIP_FIRST])))
    pool_stop = by_out[samebar.REAL_STOP] + by_out[samebar.TIE_REAL_STOP]
    pick_stop = rng.sample(pool_stop, min(10 - len(pick_dip), len(pool_stop)))
    if len(pick_dip) + len(pick_stop) < 10:
        rest = [c for c in by_out[samebar.DIP_FIRST] if c not in pick_dip]
        pick_dip += rng.sample(rest, min(10 - len(pick_dip) - len(pick_stop), len(rest)))
    A(f"A seeded random draw (seed {seed}): up to five where the dip came first and the rest real stops. "
      "Each shows the decisive prints and then the minute as consecutive runs of prints by price zone.")
    A("")
    for i, c in enumerate(pick_dip + pick_stop, 1):
        A(f"### Example {i}")
        A("")
        A(example_block(c, prim_res[(c.day, c.symbol)], raw[(c.day, c.symbol)], samebar.PRIMARY_EXCLUDED))
        A("")
    A("## What this implies")
    A("")
    A("The yardstick fixed before the run: apply the three pre-registered go/no-go checks to the tick-resolved result "
      "(net Sharpe >= 1.0; net P&L > 0 in 2024; net P&L > 0 in 2025). **For reference only; it is not a verdict**, "
      "the Phase 2 verdict stands and 2026 was not touched.")
    A("")
    A("| Check on the tick-resolved result | Result |")
    A("| --- | --- |")
    for name, ok in verdict["checks"].items():
        A(f"| {name} | {'PASS' if ok else 'FAIL'} |")
    A("")
    A(f"Tick-resolved: net Sharpe {_n(tick['sharpe_net'], 2)}, net P&L {_usd(tick['by_year'].get('2024', {}).get('net_pnl'))} in 2024 and "
      f"{_usd(tick['by_year'].get('2025', {}).get('net_pnl'))} in 2025, against {_usd(rep['by_year'].get('2024', {}).get('net_pnl'))} and "
      f"{_usd(rep['by_year'].get('2025', {}).get('net_pnl'))} reported. ")
    A("")
    costs = tick["slippage_cost"] + tick["commission"]
    moved = tick["net_pnl"] - rep["net_pnl"]
    A("**In plain language.** " + (
        f"Of the reported net loss of {_usd(-rep['net_pnl'])}, {_usd(moved)} ({moved / -rep['net_pnl'] * 100:.0f}%) came from the "
        "entry-minute assumption: resolving it from the prints moves net P&L to "
        f"{_usd(tick['net_pnl'])} and gross P&L from {_usd(rep['gross_pnl'])} to {_usd(tick['gross_pnl'])}. "
        if rep["net_pnl"] < 0 and moved > 0 else
        f"Resolving the entry-minute assumption from the prints moves net P&L from {_usd(rep['net_pnl'])} to {_usd(tick['net_pnl'])}. ")
      + (f"What is left is a cost gap: at the pre-registered 2 cents a side and $0.0035 a share the costs are {_usd(costs)}, "
         f"{costs / tick['gross_pnl']:.1f} times the tick-resolved gross, so " if 0 < tick["gross_pnl"] < costs else "")
      + f"the tick-resolved net P&L is {_usd(tick['by_year'].get('2024', {}).get('net_pnl'))} in 2024 and "
        f"{_usd(tick['by_year'].get('2025', {}).get('net_pnl'))} in 2025 (gross {_usd(tick['by_year'].get('2024', {}).get('gross_pnl'))} "
        f"and {_usd(tick['by_year'].get('2025', {}).get('gross_pnl'))}) and the net Sharpe is {_n(tick['sharpe_net'], 2)}, "
        "against the pre-registered bar of 1.0. The Phase 2 number was mis-measured in size; "
      + ("it was not mis-measured in verdict." if not passed else "and by the same yardstick the verdict would differ."))
    A("")
    A("What this diagnostic does not touch (all inherited from the Phase 2 engine, bar-level and unchanged): an entry fills at the "
      "trigger price, not at the price of the print that triggered it; a stop fills at the stop level in the entry minute and, "
      "in a later minute, at the stop level or at that bar's open if it opened beyond the stop; and costs are the flat "
      "pre-registered 2 cents a side. Only the order of events inside the entry minute was resolved.")
    A("")
    A("**" + (SENTENCE_MISMEASURED if passed else SENTENCE_UNCHANGED) + "**")
    A("")
    return "\n".join(L)
