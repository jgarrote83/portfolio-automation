"""Render the bonds-vs-SPY results dict as `.review/bonds-spy-analysis.md` (pure; no I/O except reading the optional summary text)."""
from __future__ import annotations

from pathlib import Path

from . import verify

SUMMARY_FILE = Path(__file__).with_name("summary_text.md")
MEASURE_NAME = {"gap": "Overnight gap", "morning": "Morning 09:30-10:00", "full": "Full day", "rest": "Rest of day 10:00-close"}


def _f(v, d=3) -> str:
    return "—" if v is None else f"{v:.{d}f}"


def _signed(v, d=3) -> str:
    return "—" if v is None else f"{v:+.{d}f}"


DISCLAIMER = """> **EXPLORATORY — NOT PRE-REGISTERED, NOT A TRADING RECOMMENDATION.** Every number below is descriptive, computed on 2024–2025 data that
> earlier studies in this repo have already looked at. Nothing here was decided in advance, many things were looked at, and nothing is a
> verdict. Any result that looks predictive is at most a **hypothesis**: it would need its own pre-registration and fresh data (never 2026
> until approved) before it could mean anything. TLT and IEF are bond ETFs whose prices **rise when yields fall**: "bonds up" = "yields down"."""


def _q1(res) -> list[str]:
    out = ["## Q1 — Do bonds and SPY move together at the same time?", "",
           "Pearson and Spearman correlation of the bond's return with SPY's return over the same interval. A negative number means bonds "
           "rose (yields fell) when SPY fell.", "",
           "| Bond | Interval | All: n | All: Pearson | All: Spearman | 2024: n | 2024: Pearson | 2024: Spearman | 2025: n | 2025: Pearson | 2025: Spearman |",
           "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    rows = {}
    for r in res["q1"]:
        rows.setdefault((r["bond"], r["measure"]), {})[r["period"]] = r
    for (bond, m), p in rows.items():
        cells = []
        for per in ("all", "2024", "2025"):
            x = p[per]
            cells += [str(x["n"]), _signed(x["pearson"]), _signed(x["spearman"])]
        out.append(f"| {bond} | {MEASURE_NAME[m]} | " + " | ".join(cells) + " |")
    out += ["", "### Full-day correlation by quarter", "", "| Quarter | n | TLT Pearson | TLT Spearman | IEF Pearson | IEF Spearman |",
            "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for r in res["q1_quarterly"]:
        out.append(f"| {r['quarter']} | {r['TLT']['n']} | {_signed(r['TLT']['pearson'])} | {_signed(r['TLT']['spearman'])} | "
                   f"{_signed(r['IEF']['pearson'])} | {_signed(r['IEF']['spearman'])} |")
    return out


def _q2(res) -> list[str]:
    q = res["q2"]
    out = ["## Q2 — Do bonds move first, by minutes?", "",
           f"Pooled correlation of one-minute returns, 09:31–11:00, over {q['days']} days. **Lag k pairs the bond's return at minute t with SPY's "
           "return at minute t + k, so a positive lag means the bond moved FIRST** and a negative lag means SPY moved first. Pairs never cross a day "
           "boundary and a missing minute bar is never filled. Two 95% bands: the textbook ±1.96/√n, which is too narrow because minute returns are "
           "autocorrelated and the same day's minutes are not independent; and a bootstrap that resamples **whole days** (2,000 resamples, seed 11), "
           "which respects that dependence. Read the bootstrap band.", "",
           "| Lag (min) | TLT corr | TLT ±iid | TLT day-bootstrap 95% | IEF corr | IEF ±iid | IEF day-bootstrap 95% | Pairs (TLT) |",
           "| ---: | ---: | ---: | --- | ---: | ---: | --- | ---: |"]
    for a, b in zip(q["TLT"], q["IEF"]):
        out.append(f"| {a['lag']:+d} | {_signed(a['corr'])} | {_f(a['band_iid'])} | [{_signed(a['boot_lo'])}, {_signed(a['boot_hi'])}] | "
                   f"{_signed(b['corr'])} | {_f(b['band_iid'])} | [{_signed(b['boot_lo'])}, {_signed(b['boot_hi'])}] | {a['n']:,} |")
    return out


def _reg_table(fits, label_key) -> list[str]:
    out = ["| Sample | n | HAC lag | const | bond gap | bond morning | SPY gap | SPY morning | R² | R² without bond terms | ΔR² | Wald p (both bond terms = 0) |",
           "| --- | ---: | ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |"]
    for f in fits:
        coef = [f"{b:+.3f} (t {t:+.2f})" for b, t in zip(f["beta"], f["t"])]
        out.append(f"| {f[label_key]} | {f['n']} | {f['lag']} | " + " | ".join(coef) +
                   f" | {f['r2']:.3f} | {f['baseline_r2']:.3f} | {f['delta_r2']:+.3f} | {f['wald']['p']:.3f} |")
    return out


def _q3(res) -> list[str]:
    out = ["## Q3 — Does the morning bond move say anything about SPY's afternoon?", "",
           "OLS of SPY's rest-of-day return (10:00 → close, %) on the bond's overnight gap and 09:30–10:00 move, and SPY's own gap and morning "
           "move (all in %). t-statistics use Newey-West (Bartlett) standard errors with the rule-of-thumb lag floor(4(n/100)^(2/9)); p-values are "
           "normal approximations with no small-sample correction. 'R² without bond terms' is the same regression with only SPY's own moves; ΔR² "
           "is what the bond terms add. Coefficients are 'percent of SPY afternoon per 1% of the input'.", ""]
    for bond in ("TLT", "IEF"):
        out += [f"### {bond}", ""] + _reg_table([f for f in res["q3"] if f["bond"] == bond], "period") + [""]
    return out


def _q4(res) -> list[str]:
    q = res["q4"]
    out = ["## Q4 — Big bond-move days", "",
           f"The top 10% of days by |TLT overnight gap|: **{q['n_big']} days**, |gap| ≥ {q['threshold_abs_gap_pct']:.3f}%; the other "
           f"{q['n_other']} days are the comparison. Regressions on the big days use White standard errors (HAC lag 0): those days are scattered "
           "through time, so neighbouring rows are not neighbouring days. With about 50 days, every number here is noisy.", "",
           "### Correlations: big days vs all other days", "",
           "| Bond | Interval | Big: n | Big: Pearson | Big: Spearman | Other: n | Other: Pearson | Other: Spearman |",
           "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in q["corr"]:
        b, o = r["big"], r["other"]
        out.append(f"| {r['bond']} | {MEASURE_NAME[r['measure']]} | {b['n']} | {_signed(b['pearson'])} | {_signed(b['spearman'])} | {o['n']} | "
                   f"{_signed(o['pearson'])} | {_signed(o['spearman'])} |")
    out += ["", "### Regression (Q3 model): big days vs all other days", ""] + _reg_table(
        [dict(f, label=f"{f['bond']}, {f['sample']}") for f in q["reg"]], "label")
    out += ["", "### How fragile is the big-day result?", "",
            "Added after seeing the table above (so it is itself exploratory). The joint test that both bond terms are zero, on the big days: "
            "its range when any single day is left out, and the fit with April 2025 (the tariff shock) removed.", "",
            "| Bond | n | Wald p | ΔR² | Leave-one-out p: lowest (day dropped) | highest (day dropped) | April 2025 days in the sample | n without them | Wald p without them | ΔR² without them |",
            "| --- | ---: | ---: | ---: | --- | --- | --- | ---: | ---: | ---: |"]
    for r in res.get("q4_robustness", []):
        out.append(f"| {r['bond']} | {r['n']} | {r['p']:.3f} | {r['delta_r2']:+.3f} | {r['loo_min'][0]:.3f} ({r['loo_min'][1]}) | "
                   f"{r['loo_max'][0]:.3f} ({r['loo_max'][1]}) | {', '.join(str(d) for d in r['april_2025_days'])} | {r['n_ex_april']} | "
                   f"{r['p_ex_april']:.3f} | {r['delta_r2_ex_april']:+.3f} |")
    out += ["", "### The 20 largest |TLT gap| days", "",
            "| # | Date | Day | TLT gap % | IEF gap % | SPY gap % | SPY morning % | SPY rest of day % |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for i, t in enumerate(q["top"], 1):
        out.append(f"| {i} | {t['date']} | {t['weekday']} | {t['tlt_gap']:+.2f} | {t['ief_gap']:+.2f} | {t['spy_gap']:+.2f} | "
                   f"{t['spy_morning']:+.2f} | {t['spy_rest']:+.2f} |")
    return out


def _sensitivity(res) -> list[str]:
    m = res["month_start"]
    out = ["## Sensitivity: monthly distributions in raw prices", "",
           "The bars are RAW (unadjusted). TLT and IEF pay monthly, and on each ex-date the price drops by the distribution (roughly 0.3% for "
           "TLT), which shows up as a negative overnight gap that is not a market move. There is no distribution calendar in the data, so this is "
           "a crude check: the first trading day of each month against all other days, and the headline numbers with those days removed.", "",
           "| Bond | First-of-month days | Mean gap % | Other days | Mean gap % | Difference | Welch t |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for g in m["gaps"]:
        out.append(f"| {g['bond']} | {g['n_on']} | {g['mean_on']:+.3f} | {g['n_off']} | {g['mean_off']:+.3f} | {g['diff']:+.3f} | {g['t_welch']:+.2f} |")
    w = m["without"]
    out += ["", f"Without the first trading day of each month ({w['n']} days):", "", "| Bond | Interval | n | Pearson | Spearman |",
            "| --- | --- | ---: | ---: | ---: |"]
    for c in w["corr"]:
        out.append(f"| {c['bond']} | {MEASURE_NAME[c['measure']]} | {c['n']} | {_signed(c['pearson'])} | {_signed(c['spearman'])} |")
    out += [""] + _reg_table([dict(f, label=f"{f['bond']}, without month starts") for f in w["reg"]], "label")
    return out


def _data(res) -> list[str]:
    c, s = res["counts"], res["skips"]
    out = ["## Data and counts", "",
           "All market data came through `backtest.data.get_bars` (SIP feed, raw prices, the repo's research data layer, which refuses any date on "
           "or after 2026-01-01). Daily bars 2023-12-01 → 2025-12-31 (the first window day needs a prior close); one-minute bars 2024-01-02 → "
           "2025-12-31, 09:30–16:00 ET.", "",
           "| Symbol | Daily bars | Minute bars | Days with any 09:30–11:00 minute bar |", "| --- | ---: | ---: | ---: |"]
    for sym in ("TLT", "IEF", "SPY"):
        out.append(f"| {sym} | {c['daily_rows'][sym]:,} | {c['minute_bars'][sym]:,} | {c['minute_days'][sym]} |")
    out += ["", f"**Trading days in the window: {s['days_in_window']}. Used: {s['used']}. Skipped: {s['days_in_window'] - s['used']}.** "
            "A day is used only when all three symbols have a daily bar on that day and the prior trading day, and a minute bar stamped 10:00. "
            "Nothing is filled or interpolated."]
    if s["by_reason"]:
        out += ["", "| Skip reason | Days |", "| --- | ---: |"] + [f"| {k} | {v} |" for k, v in sorted(s["by_reason"].items())]
        out += ["", "Skipped days: " + ", ".join(f"{d} ({'; '.join(r)})" for d, r in sorted(s["skipped_days"].items())) + "."]
    out += ["", "Minute-bar coverage on the used days, 09:30–11:00 (91 bars possible per day):", "",
            "| Symbol | Days | Mean bars per day | Fewest bars on a day | Share present |", "| --- | ---: | ---: | ---: | ---: |"]
    for r in res["coverage"]:
        out.append(f"| {r['symbol']} | {r['days']} | {r['mean_bars']:.1f} | {r['min_bars']} | {r['share_present'] * 100:.2f}% |")
    out += ["", "Return distributions on the used days (percent):", "", "| Symbol | Interval | n | Mean | Std dev | Min | Max |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for r in res["describe"]:
        out.append(f"| {r['symbol']} | {MEASURE_NAME[r['measure']]} | {r['n']} | {r['mean']:+.3f} | {r['std']:.3f} | {r['min']:+.2f} | {r['max']:+.2f} |")
    return out


DEFINITIONS = """## Definitions

For trading day D, prior trading day P, and each of TLT, IEF and SPY:

| Measure | Formula | Source |
| --- | --- | --- |
| Overnight gap | open_D ÷ close_P − 1 | daily bars |
| Morning | open of the minute bar stamped 10:00 ÷ open_D − 1 | daily open, minute bar |
| Rest of day | close_D ÷ open of the 10:00 bar − 1 | daily close, minute bar |
| Full day | close_D ÷ close_P − 1 | daily bars |

(1 + gap)(1 + morning)(1 + rest) = 1 + full day, by construction."""

LIMITS = """## Limitations

- **Two years, one regime.** 2024–2025 had a particular rate path (cuts from September 2024, a long-end selloff into early 2025). Bond/stock
  correlation is known to flip sign across regimes; two years cannot say what is normal.
- **Already-seen data, many looks.** Four questions, two bonds, three periods, several intervals and lags: dozens of numbers. Some will look
  significant by chance.
- **Raw prices.** Monthly distributions put small artificial drops in the bond gaps (see the sensitivity section); the effect on correlations
  is small but not zero.
- **ETFs, not yields.** TLT and IEF trade on stock-exchange hours; the Treasury market trades nearly 24 hours, so part of the "overnight gap"
  is simply the bond market's own trading while the ETF was closed.
- **Minute data is thin in places.** A minute with no trade has no bar and is never filled; the bootstrap band is the honest one for Q2.
- **Correlation is not a trade.** None of this models costs, spreads, or what an order would actually have filled at."""


def render(res: dict) -> str:
    parts = ["# Exploratory analysis: the morning bond market vs SPY", "", DISCLAIMER, ""]
    summary = SUMMARY_FILE.read_text(encoding="utf-8").strip() if SUMMARY_FILE.exists() else "_Summary not yet written._"
    parts += ["## Plain-language summary for Jorge", "", summary, ""]
    parts += _data(res) + ["", DEFINITIONS, ""]
    parts += _q1(res) + [""] + _q2(res) + [""] + _q3(res) + _q4(res) + [""] + _sensitivity(res) + [""]
    parts += [verify.render(res["verification"]), LIMITS, "", "Reproduce: `python -m analysis.bonds_spy.run` (offline, from the cache), then "
              "`python -m analysis.bonds_spy.verify`.", ""]
    return "\n".join(parts)
