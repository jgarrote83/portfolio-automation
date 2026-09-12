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
for it). A real negative reading (e.g. news that exists and is plainly
negative in tone) IS a 0.0 — that is data, not absence, and must stay
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

# The seven catalyst_score components, in a fixed order (also the ledger's
# `components_missing` ordering — deterministic output, easier to diff/test).
# `relative_strength` (2026-08-14, flex-conviction-path cycle, Task D) is the
# seventh: 60-day total-return excess vs SPY. Applicable to every instrument
# type (unlike earnings_proximity/political_flow, which are single-name-only
# concepts) — the system already computes and displays this exact figure
# every session (`regional_rotation`'s rotation-score dashboard row) and it
# fed nothing into the funnel; this wires it in. Motivating incident: EUAD
# carried +16-22pp 60d excess vs SPY for four straight sessions, displayed
# daily, discarded by the ranking pipeline.
COMPONENTS: tuple[str, ...] = (
    "earnings_proximity",
    "news_recency",
    "news_tone",
    "momentum",
    "volume_surge",
    "political_flow",
    "relative_strength",
)

# --- rankability (R2, session 2026-09-12) -----------------------------------
# REPLACES a bare count bar (`>= 4 of N available`). A count treats every
# component as interchangeable, which is how a strategy meant to trade on NEWS
# ended up rankable on momentum + regime fit alone. Rankability is now a
# REQUIRED-COMPONENT SET:
#
#   1. `news_recency` must be PRESENT — this is a news strategy; a candidate
#      with no recent news has no thesis, whatever else scores well.
#   2. at least one PRICE CONFIRMATION (`momentum` or `volume_surge`) must be
#      present — news without any tape response is a story, not a trade.
#   3. at least MIN_COMPONENTS_RANKABLE available overall — the original
#      "mostly unmeasured" guard, kept, but lowered to 3 because (1)+(2)
#      already carry the qualitative bar a count can only approximate.
#
# `earnings_proximity` is deliberately NOT required (R2): the old bar was
# unsatisfiable by construction for the discovery universe, which was drawn
# from YESTERDAY's reporters while this component scores a 14-day FORWARD
# window — always null for exactly the names being screened. A catalyst is now
# purely opportunistic: it contributes to the composite when present and gates
# nothing.
REQUIRED_COMPONENT = "news_recency"
PRICE_CONFIRMATION_COMPONENTS = ("momentum", "volume_surge")
MIN_COMPONENTS_RANKABLE = 3

# Single-name-only components: a fund/ETF has no earnings date to report and
# is not the kind of individual-insider-conviction target Quiver's
# congressional-purchase signal measures. NOT_APPLICABLE for a fund, never
# merely "missing_data" -- see applicable_components()'s docstring for the
# distinction and why it matters.
_SINGLE_NAME_ONLY_COMPONENTS = ("earnings_proximity", "political_flow")


def applicable_components(is_fund: bool) -> tuple[str, ...]:
    """Task D-priority-3/4 (2026-08-14 flex-conviction-path cycle) — which of
    the 7 `COMPONENTS` are even CONCEPTUALLY possible for this instrument
    type, independent of whether data happens to be available this session.

    This is the ABSENT-VS-ZERO rule's sibling distinction: `earnings_proximity`
    /`political_flow` being `None` for a real operating company with no
    scheduled print / no congressional flow this week is `missing_data` (could
    resolve tomorrow); the SAME fields being `None` for an ETF are
    `not_applicable` (can never resolve — an ETF will never report
    "earnings"). Conflating the two let a candidate's rankability be judged
    against a bar it can never structurally clear. Determined from FMP's own
    `isEtf`/`isFund` profile booleans (D-priority-4) — more robust than
    inferring instrument type from a sector string, which is unreliable/absent
    for many funds.
    """
    if is_fund:
        return tuple(c for c in COMPONENTS if c not in _SINGLE_NAME_ONLY_COMPONENTS)
    return COMPONENTS


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


def relative_strength_from_closes(
    candidate_closes: dict[str, float], spy_closes: dict[str, float], window: int = 60,
) -> float | None:
    """Total-return excess vs SPY over the trailing `window` TRADING days, from
    `{date: close}` maps (the `_close_by_date` shape — date-keyed, not the
    ascending-list shape `momentum_from_bars` takes). Mirrors the
    `_r5_from_closes` trading-day-offset idiom used elsewhere in this system:
    sort the date keys, compare the latest close to the close `window`
    trading days earlier. `None` when either series lacks enough history —
    never a fabricated 0.0 (an absent relative-strength reading must drop out
    of the composite mean like every other component, not silently score as
    "flat vs SPY")."""
    if not candidate_closes or not spy_closes:
        return None
    c_dates = sorted(candidate_closes)
    s_dates = sorted(spy_closes)
    if len(c_dates) < window + 1 or len(s_dates) < window + 1:
        return None
    c_now, c_then = candidate_closes[c_dates[-1]], candidate_closes[c_dates[-1 - window]]
    s_now, s_then = spy_closes[s_dates[-1]], spy_closes[s_dates[-1 - window]]
    if not c_then or not s_then:
        return None
    c_ret = (c_now / c_then - 1.0) * 100.0
    s_ret = (s_now / s_then - 1.0) * 100.0
    return round(c_ret - s_ret, 4)


def relative_strength_score(raw_excess_pct: float | None, cap_pct: float = 20.0) -> float | None:
    """Normalizes a raw excess-vs-SPY % to [0,1] via the SAME symmetric
    +/-cap_pct clamp `momentum_score` uses (0.5 = matching SPY; 1.0 = +cap_pct%
    or more excess; 0.0 = -cap_pct% or worse) — same treatment as momentum so
    this component does not dominate the composite by scale alone."""
    if raw_excess_pct is None:
        return None
    clamped = max(-cap_pct, min(cap_pct, raw_excess_pct))
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


def hours_since_latest_news(items: list[dict], now_iso: str) -> float | None:
    """Hours since the most recent item's publish TIMESTAMP (N1, session
    2026-09-12). None if no parseable dated items at all.

    The day-resolution sibling (`days_since_latest_news`) is far too coarse for
    a sleeve holding ~2 days: every item published at any point today scored
    identically. FMP's `publishedDate` carries a time component
    ("2026-09-11 14:32:00"); a date-only value parses as midnight, which is the
    conservative (older) reading — never fabricates freshness."""
    stamps: list[datetime] = []
    for it in items or ():
        raw = it.get("publishedDate") or it.get("date") or it.get("datetime")
        if not raw:
            continue
        txt = str(raw).strip().replace("Z", "").replace("T", " ")[:19]
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                stamps.append(datetime.strptime(txt, fmt))
                break
            except ValueError:
                continue
    if not stamps:
        return None
    txt_now = str(now_iso).strip().replace("Z", "").replace("T", " ")[:19]
    now = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            now = datetime.strptime(txt_now, fmt)
            break
        except ValueError:
            continue
    if now is None:
        return None
    return max(0.0, (now - max(stamps)).total_seconds() / 3600.0)


def news_recency_score(hours_since: float | None, lookback_hours: int) -> float | None:
    """1.0 = news just now, decaying linearly to 0.0 at `lookback_hours` out.
    Absent when there is no dated news, or the freshest item is older than the
    lookback.

    N1 (2026-09-12): the unit is HOURS, not days. The old day-resolution version
    paired a 7-DAY lookback with a sleeve that holds ~2 days — a 6-day-old
    headline still scored 0.14 and counted as "news". See
    `_FLEX_NEWS_WINDOW_H`."""
    if hours_since is None or lookback_hours <= 0:
        return None
    if hours_since < 0 or hours_since > lookback_hours:
        return None
    return round(1.0 - (hours_since / lookback_hours), 4)


def volume_surge_from_bars(daily_bars: list[dict], window: int = 20) -> float | None:
    """Latest session's volume as a MULTIPLE of its own trailing `window`-day
    average (N1, session 2026-09-12) — the "something is happening right now"
    signal, and the natural partner to `news_recency`.

    `daily_bars` must be ascending (oldest first), same contract as
    `momentum_from_bars`. Returns None when there is not enough history or the
    trailing average is zero — never a fabricated 1.0 (an unmeasurable surge
    must drop out of the composite like any other absent component, not score
    as "perfectly average")."""
    vols: list[float] = []
    for b in daily_bars or ():
        v = b.get("v")
        if v is None:
            continue
        try:
            vols.append(float(v))
        except (TypeError, ValueError):
            continue
    if len(vols) <= window:
        return None
    latest, trailing = vols[-1], vols[-1 - window:-1]
    if not trailing:
        return None
    avg = sum(trailing) / len(trailing)
    if avg <= 0:
        return None
    return round(latest / avg, 4)


def volume_surge_score(ratio: float | None, cap_x: float = 3.0) -> float | None:
    """Normalizes a volume MULTIPLE to [0,1]. 1.0x (perfectly average volume)
    maps to 0.0 and `cap_x` or more maps to 1.0 — deliberately one-sided, unlike
    `momentum_score`'s symmetric clamp: below-average volume is simply the
    absence of a surge, not a negative signal about the name."""
    if ratio is None:
        return None
    if ratio <= 1.0:
        return 0.0
    if cap_x <= 1.0:
        return None
    return round(min(ratio - 1.0, cap_x - 1.0) / (cap_x - 1.0), 4)


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

def composite_score(
    components: dict[str, float | None], applicable: tuple[str, ...] | None = None,
) -> dict:
    """`catalyst_score = mean(available components)`. Absent components drop
    out of the mean entirely rather than scoring 0.0 (see module docstring).

    `applicable` (Task D-priority-3, default `None` = all 7, i.e. the
    ORIGINAL unconditional behavior — fully backward compatible) narrows the
    denominator to what's even conceptually possible for this instrument type
    (see `applicable_components`).

    `rankable` is delegated to `rankability()` (R2, session 2026-09-12) — a
    REQUIRED-COMPONENT SET, no longer a bare count. `rankability_reason` names
    the first unmet condition so a zero-nomination session is diagnosable from
    the ledger alone.
    """
    applicable = tuple(applicable) if applicable is not None else COMPONENTS
    available = {k: v for k, v in components.items() if k in applicable and v is not None}
    missing = [k for k in applicable if components.get(k) is None]
    not_applicable = [k for k in COMPONENTS if k not in applicable]
    n_applicable = len(applicable)
    n = len(available)
    score = round(sum(available.values()) / n, 4) if n > 0 else None
    rankable, reason = rankability(components, applicable)
    return {
        "score": score,
        "components_available": n,
        "components_missing": missing,
        "components_not_applicable": not_applicable,
        "components_applicable": n_applicable,
        "rankable": rankable,
        "rankability_reason": reason,
    }


def rankability(
    components: dict[str, float | None], applicable: tuple[str, ...],
) -> tuple[bool, str | None]:
    """R2 (session 2026-09-12) — the REQUIRED-COMPONENT-SET rankability rule.

    Returns ``(rankable, reason)``; ``reason`` is ``None`` iff rankable, else a
    short machine-readable string naming the FIRST unmet condition (ledger
    output, so a zero-nomination session is diagnosable at a glance rather than
    requiring a re-run).

    Replaces a bare ``available >= 4`` count. See the REQUIRED_COMPONENT block
    at the top of this module for why a count was the wrong shape, and why
    `earnings_proximity` is no longer able to gate anything.

    A component that is NOT APPLICABLE to this instrument type cannot be
    required of it: if `news_recency` were ever made non-applicable for some
    instrument, requiring it would be the "judged against a bar it can never
    structurally clear" failure `applicable_components` exists to prevent. So
    each clause below is skipped when its component is not applicable — the
    overall-count clause still binds.
    """
    avail = {k for k in applicable if components.get(k) is not None}

    if REQUIRED_COMPONENT in applicable and REQUIRED_COMPONENT not in avail:
        return False, f"missing_required:{REQUIRED_COMPONENT}"

    price_conf = [c for c in PRICE_CONFIRMATION_COMPONENTS if c in applicable]
    if price_conf and not any(c in avail for c in price_conf):
        return False, "missing_price_confirmation"

    if len(avail) < MIN_COMPONENTS_RANKABLE:
        return False, f"insufficient_components:{len(avail)}<{MIN_COMPONENTS_RANKABLE}"

    return True, None


# --- discovery universe (pure symbol-list assembly) --------------------------

def movers_discovery_symbols(
    mover_rows: list[dict],
    news_by_symbol: dict[str, list[dict]],
    now_iso: str,
    exclude: set[str],
    cap: int,
    news_window_hours: int,
    min_price_usd: float,
) -> tuple[list[str], dict[str, str]]:
    """N1 (session 2026-09-12) — the MOVERS discovery universe.

    ``(symbols, dropped)`` where ``dropped`` maps each rejected symbol to the
    first reason it failed, so a thin session is diagnosable from the ledger.

    ``discovery = (most_active ∪ gainers) ∩ has_news_within(news_window_hours)``,
    minus ``exclude``, minus sub-``min_price_usd`` names, capped at ``cap``.

    REPLACES the earnings-calendar ∪ congressional universe, which could not
    nominate by construction: it was drawn from YESTERDAY's reporters while
    `earnings_proximity` scores a 14-day FORWARD window, so that component was
    always null for exactly the names being screened. Live evidence
    (2026-09-09/10/11): 66 of 75 candidates (88%) died on the liquidity floor —
    the universe was OTC/foreign micro-caps (IDWM, REBN, FANDF, HGRAF, SRTSF,
    ODMUF, GYYMF, MHPSY) — and the 9 that reached scoring got exactly 3 of a
    required 4 components. Both failures had the same root: the universe.

    **The news intersection is the point, not a filter.** A mover with no recent
    news is a price move without a reason — momentum, not information. This
    sleeve trades on recent news confirmed by the tape, so a name must have BOTH
    to enter the universe at all.

    **``min_price_usd`` is a hard screen, not a preference.** The live probe
    found the mover union is ~19% sub-$1 and ~42% sub-$5. The ADV floor alone
    does not defend against that: a $1 stock can trade $50M on the day it is
    being promoted. The price floor removes the region where "recent news +
    volume surge" most often means something other than information.

    Order: most-active names first (a volume surge is the stronger prior for
    this sleeve), then gainers. Order-preserving within each source; dedups.
    """
    seen = {str(s).upper() for s in (exclude or ())}
    out: list[str] = []
    dropped: dict[str, str] = {}
    for row in mover_rows or ():
        sym = str((row or {}).get("symbol") or "").upper().strip()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        try:
            price = float(row.get("price"))
        except (TypeError, ValueError):
            price = None
        if price is None or price < float(min_price_usd):
            dropped[sym] = "below_min_price"
            continue
        hrs = hours_since_latest_news(news_by_symbol.get(sym) or [], now_iso)
        if hrs is None or hrs > float(news_window_hours):
            dropped[sym] = "no_recent_news"
            continue
        out.append(sym)
        if len(out) >= cap:
            break
    return out, dropped


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
                "components_not_applicable": [], "components_applicable": len(COMPONENTS),
                "rankable": False, "nominated": False,
            })
            ledger.append(row)
            continue
        comp = c["components"]
        cs = composite_score(comp, c.get("applicable"))
        row.update({
            "components": comp,
            "score": cs["score"],
            "components_available": cs["components_available"],
            "components_missing": cs["components_missing"],
            "components_not_applicable": cs["components_not_applicable"],
            "components_applicable": cs["components_applicable"],
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
    "REQUIRED_COMPONENT",
    "PRICE_CONFIRMATION_COMPONENTS",
    "rankability",
    "movers_discovery_symbols",
    "volume_surge_from_bars",
    "volume_surge_score",
    "hours_since_latest_news",
    "applicable_components",
    "screen_candidate",
    "earnings_proximity_score",
    "momentum_from_bars",
    "momentum_score",
    "relative_strength_from_closes",
    "relative_strength_score",
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
