"""Catalyst-sleeve funnel: deterministic candidate discovery + ranking (Task D,
session 2026-08-10). Pure functions only — no I/O, no FMP/Quiver/Finnhub calls.
The collector fetches everything (profiles, daily bars, news, congressional flow)
and hands it to this module; the model never computes a score here or downstream.

Fixes G3 (FOLLOWUPS): the pre-existing `_load_flex_candidates` merges a static
seed with the PREVIOUS run's own `watch_candidates` emission — self-referential,
nothing generates a genuinely new name. This module scores a DISCOVERY universe
(sourced from the market-wide earnings calendar + market-wide congressional
flow — both already fetched elsewhere at zero incremental API cost) and ranks it
independently of anything the model has ever nominated.

ABSENT-VS-ZERO (the load-bearing rule — see every `*_score` function below): a
component with no underlying data is ABSENT (``None``) and drops out of the
composite mean. It is never scored 0.0 and averaged in — that would silently
impose a permanent handicap on every candidate lacking that one input (most
concretely: a name with no scheduled earnings print must never be penalized
for it). A real negative reading (e.g. a sector that plainly does not fit the
active quadrant) IS a 0.0 — that is data, not absence, and must stay
distinguishable from it.

Equal weights in v1 (FOLLOWUPS #23 doctrine): with no point-in-time backtest
harness, tuned coefficients would be unfalsifiable priors dressed as signal.
Weight-tuning is deferred until graded outcome rows exist to falsify against —
this module deliberately carries no weights config.

DATA-AVAILABILITY NOTE (verified empirically, session 2026-08-10): the momentum
component and the "price history present" hard-filter check are both
close-price-only. `FMPClient.get_eod_prices`/`get_historical_price_light` — the
only historical-price fetch already integrated — hits FMP's `/light` variant,
which returns `date`/`price`(or `close`)/`volume` and NO high/low. A true
Average True Range needs high/low, so `screen_candidate`'s `has_price_data`
flag is caller-defined against a minimum CLOSE-observation count, not a literal
ATR read — see the Task C probe (`scripts/probe_fmp_tier.py`) and the PR body
for the full finding. Switching to a full-OHLC endpoint for a real ATR-based
volatility read is out of scope for this PR.
"""
from __future__ import annotations

from datetime import date, datetime

# The six catalyst_score components, in a fixed order (also the ledger's
# `components_missing` ordering — deterministic output, easier to diff/test).
COMPONENTS: tuple[str, ...] = (
    "earnings_proximity",
    "news_recency",
    "news_tone",
    "momentum",
    "regime_fit_score",
    "political_flow",
)

# A candidate needs at least this many of the 6 components to be nominatable —
# below this, a lucky one- or two-input score would look flatteringly high
# despite being mostly unmeasured. See composite_score()'s docstring.
MIN_COMPONENTS_RANKABLE = 4


# --- hard screen (mechanical, applied before any scoring) -------------------

def screen_candidate(
    *,
    held: bool,
    separated: bool,
    non_reenterable_legacy: bool,
    has_price_data: bool,
    adv_usd: float | None,
    min_adv_usd: float,
) -> tuple[bool, str | None]:
    """Cheap, mechanical hard filters — applied first, before any component is
    computed. Returns ``(passed, reason)``; ``reason`` is ``None`` iff passed.

    Order matters only for which single reason is reported when several would
    apply; every reason is independently sufficient to reject. ``has_price_data``
    is caller-defined (the collector currently gates it on a minimum close-price
    observation count — the integrated FMP historical-price-eod/light endpoint
    returns close + volume only, no high/low, so a literal ATR presence check
    is not available; see the module docstring's Task C/D note).
    """
    if held:
        return False, "currently_held"
    if separated:
        return False, "flex_separation_set"
    if non_reenterable_legacy:
        return False, "non_reenterable_legacy_exit"
    if not has_price_data:
        return False, "insufficient_price_history"
    if adv_usd is None or adv_usd < min_adv_usd:
        return False, "liquidity_below_min"
    return True, None


# --- per-component scorers (each: real data -> [0,1]; no data -> None) ------

def earnings_proximity_score(
    catalyst_date: str | None, today: str, horizon_days: int,
) -> float | None:
    """1.0 = reports today, decaying linearly to 0.0 at `horizon_days` out.
    Absent (None) when there is no scheduled date, OR the date is outside the
    forward window (already passed, or further out than the calendar fetch
    horizon) — never scored as a negative. This is the load-bearing case: a
    name with no upcoming print must never be penalized for lacking one."""
    if not catalyst_date:
        return None
    try:
        d = date.fromisoformat(str(catalyst_date)[:10])
        t = date.fromisoformat(today)
    except ValueError:
        return None
    delta = (d - t).days
    if delta < 0 or delta > horizon_days or horizon_days <= 0:
        return None
    return round(1.0 - (delta / horizon_days), 4)


def momentum_from_bars(daily_bars: list[dict], window: int) -> float | None:
    """Raw % price change over the trailing `window` trading days. `daily_bars`
    MUST be in ascending chronological order (oldest first) — FMP's
    historical-price-light endpoint returns DESCENDING order and must be
    reversed by the caller before this is called; Alpaca-shaped bars (as used
    elsewhere in `flex/`) are already ascending."""
    closes: list[float] = []
    for b in daily_bars or ():
        c = b.get("c")
        if c is None:
            continue
        try:
            closes.append(float(c))
        except (TypeError, ValueError):
            continue
    if len(closes) <= window:
        return None
    past, now = closes[-1 - window], closes[-1]
    if past <= 0:
        return None
    return round((now / past - 1.0) * 100.0, 6)


def momentum_score(raw_pct: float | None, cap_pct: float = 15.0) -> float | None:
    """Normalizes a raw % return to [0,1] via a symmetric +/-cap_pct clamp
    (0.5 = flat; 1.0 = up cap_pct% or more; 0.0 = down cap_pct% or more)."""
    if raw_pct is None:
        return None
    clamped = max(-cap_pct, min(cap_pct, raw_pct))
    return round((clamped + cap_pct) / (2 * cap_pct), 4)


def group_news_by_symbol(items: list[dict]) -> dict[str, list[dict]]:
    """FMP stock-news items -> {symbol: [items...]}. Items with no symbol field
    are dropped (nothing to attribute them to)."""
    out: dict[str, list[dict]] = {}
    for item in items or ():
        sym = str(item.get("symbol") or item.get("ticker") or "").upper().strip()
        if not sym:
            continue
        out.setdefault(sym, []).append(item)
    return out


def days_since_latest_news(items: list[dict], today: str) -> float | None:
    """Calendar days since the most recent item's publish date. None if no
    dated items at all."""
    dates: list[date] = []
    for it in items or ():
        raw = it.get("publishedDate") or it.get("date") or it.get("datetime")
        if not raw:
            continue
        try:
            dates.append(datetime.fromisoformat(str(raw)[:10]).date())
        except ValueError:
            continue
    if not dates:
        return None
    try:
        t = date.fromisoformat(today)
    except ValueError:
        return None
    return max(0, (t - max(dates)).days)


def news_recency_score(days_since: float | None, lookback_days: int) -> float | None:
    """1.0 = news today, decaying to 0.0 at `lookback_days` out. Absent when
    there is no dated news, or the freshest item is older than the lookback."""
    if days_since is None or lookback_days <= 0:
        return None
    if days_since < 0 or days_since > lookback_days:
        return None
    return round(1.0 - (days_since / lookback_days), 4)


def keyword_hits(items: list[dict], keyword_sets: dict[str, list[str]]) -> dict[str, int]:
    """Category -> hit count, mirroring the `_SHOCK_KEYWORDS` scan in
    `collector/handler.py` (same shape: headline/summary text, lowercased,
    first match per item per category to avoid double-counting)."""
    hits = {cat: 0 for cat in keyword_sets}
    for item in items or ():
        parts = [item.get("headline") or item.get("title") or "",
                 item.get("summary") or item.get("text") or ""]
        body = " ".join(p for p in parts if p).lower()
        if not body:
            continue
        for cat, kws in keyword_sets.items():
            if any(kw in body for kw in kws):
                hits[cat] += 1
    return hits


def news_tone_score(has_news: bool, positive_hits: int, negative_hits: int) -> float | None:
    """Signed keyword diffusion mapped to [0,1] (0.5 = neutral/no signal in
    either direction). Absent ONLY when there is no news at all for the name —
    news that exists but matches neither keyword set is a real neutral
    reading (0.5), not an absence; see module docstring's absent-vs-zero rule."""
    if not has_news:
        return None
    total = positive_hits + negative_hits
    if total == 0:
        return 0.5
    diffusion = (positive_hits - negative_hits) / total
    return round((diffusion + 1.0) / 2.0, 4)


def political_flow_score(purchase_count: int, cap: int = 5) -> float | None:
    """Congressional-purchase cluster size normalized to [0,1] (cap = a full
    score). Absent when there is no flow at all for the name — zero purchases
    is "nothing observed," not "observed and bearish" (Quiver has no sell-side
    equivalent signal in this composite)."""
    if purchase_count is None or purchase_count <= 0:
        return None
    return round(min(purchase_count, cap) / cap, 4)


# --- composite ----------------------------------------------------------

def composite_score(components: dict[str, float | None]) -> dict:
    """`catalyst_score = mean(available components)`. Absent components drop
    out of the mean entirely rather than scoring 0.0 (see module docstring).
    `rankable` requires >= MIN_COMPONENTS_RANKABLE of 6 — below that, a score
    built from one or two lucky inputs would look flatteringly confident
    despite being mostly unmeasured; such a candidate is never nominated
    regardless of its (still-reported, for the ledger) score.
    """
    available = {k: v for k, v in components.items() if v is not None}
    missing = [k for k in COMPONENTS if components.get(k) is None]
    n = len(available)
    score = round(sum(available.values()) / n, 4) if n > 0 else None
    return {
        "score": score,
        "components_available": n,
        "components_missing": missing,
        "rankable": n >= MIN_COMPONENTS_RANKABLE,
    }


# --- discovery universe (pure symbol-list assembly) --------------------------

def discovery_symbols(
    earnings_market_symbols: list[str],
    congressional_symbols: list[str],
    exclude: set[str],
    cap: int,
) -> list[str]:
    """Union of market-wide-earnings-calendar names and market-wide
    congressional-flow names, minus anything already known to the funnel
    (held / existing static+dynamic flex candidates / flex_separation_set /
    non-reenterable legacy exits), capped at `cap`. Earnings-sourced names are
    taken first — a dated catalyst is a stronger discovery prior than a single
    congressional filing — then congressional-only names fill the remainder.
    Order-preserving within each source; dedups across both.
    """
    seen = {str(s).upper() for s in (exclude or ())}
    out: list[str] = []
    for pool in (earnings_market_symbols or (), congressional_symbols or ()):
        for raw in pool:
            s = str(raw or "").upper().strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
            if len(out) >= cap:
                return out
    return out


# --- ledger orchestration (still pure — all inputs precomputed by caller) ---

def build_ranking_ledger(candidates: list[dict], top_n: int) -> dict:
    """`candidates[i]` = {"symbol", "screen": {...screen_candidate kwargs...},
    "components": {...composite_score input...}, "basis": {...auditable raw
    values, echoed verbatim...}}. Applies the hard screen, scores survivors,
    ranks by (score desc, components_available desc — a tiebreaker rewarding
    better-covered names), and marks the top `top_n` rankable rows nominated.

    Returns {"ledger": [...every candidate, screened out or not...],
    "nominated": [symbols...], "top_n": top_n} — the full ledger is the raw
    material for future weight-tuning and sector read-through work (FOLLOWUPS);
    nothing is silently dropped from it, only from `nominated`.
    """
    ledger: list[dict] = []
    for c in candidates:
        sym = c["symbol"]
        passed, reason = screen_candidate(**c["screen"])
        row = {
            "symbol": sym,
            "screened_in": passed,
            "screen_reason": reason,
            "basis": c.get("basis", {}),
        }
        if not passed:
            row.update({
                "components": {}, "score": None,
                "components_available": 0, "components_missing": list(COMPONENTS),
                "rankable": False, "nominated": False,
            })
            ledger.append(row)
            continue
        comp = c["components"]
        cs = composite_score(comp)
        row.update({
            "components": comp,
            "score": cs["score"],
            "components_available": cs["components_available"],
            "components_missing": cs["components_missing"],
            "rankable": cs["rankable"],
            "nominated": False,
        })
        ledger.append(row)

    rankable_rows = [r for r in ledger if r["rankable"]]
    rankable_rows.sort(key=lambda r: (r["score"], r["components_available"]), reverse=True)
    nominated: list[str] = []
    for r in rankable_rows[:max(top_n, 0)]:
        r["nominated"] = True
        nominated.append(r["symbol"])

    return {"ledger": ledger, "nominated": nominated, "top_n": top_n}


__all__ = [
    "COMPONENTS",
    "MIN_COMPONENTS_RANKABLE",
    "screen_candidate",
    "earnings_proximity_score",
    "momentum_from_bars",
    "momentum_score",
    "days_since_latest_news",
    "news_recency_score",
    "keyword_hits",
    "news_tone_score",
    "political_flow_score",
    "composite_score",
    "discovery_symbols",
    "build_ranking_ledger",
    "group_news_by_symbol",
]
