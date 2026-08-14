import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from shared.keyvault import load_secrets
from shared.storage import (
    ensure_tables,
    list_snapshot_dates,
    query_entities,
    read_executions,
    read_json_blob,
    read_perf_series,
    read_snapshot,
    read_trades,
    upsert_entity,
    write_json_blob,
    write_perf_quadrant_config,
    write_perf_series,
    write_snapshot,
)
from shared.clients.fmp import FMPClient
from shared.clients.fred import FREDClient
from shared.clients.finnhub import FinnhubClient
from shared.clients.quiver import QuiverClient
from shared.clients.alpaca import AlpacaClient
from shared.quadrants import (
    AMPLIFIER_INTL,
    CORE_ROSTER,
    DAMPER,
    EXEMPT_HOLDS,
    LEGACY_EXITS,
    QUADRANT_BENCHMARK_ETF,
    QUADRANT_CONCENTRATE,
    active_quadrant,
    benchmark_etf_for,
    concentrate_names,
    favored_bucket,
    intersection_names,
    is_amplifier,
    primary_quadrant,
    intl_config,
    quadrant_allocation_bucket,
    role_of,
    roles_config,
    selected_core_members,
    selection_config,
)
from flex.config import load_flex_config
from flex.indicators import avg_dollar_volume
from flex.regime import FLEX_REENTERABLE, flex_separation_set, regime_fit_score, resolve_quadrant
from shared.reference_execution import effective_execution_config
from shared.overrides import evaluate_falsifier
from collector import catalyst_screen

logger = logging.getLogger(__name__)

_SRC = Path(__file__).parent.parent   # src/
_MACRO_SERIES_FILE = _SRC / "config" / "macro-series.json"
_PORTFOLIO_FALLBACK = _SRC / "config" / "portfolio.json"
_FLEX_CANDIDATES_FILE = _SRC / "config" / "flex-candidates.json"
_FOMC_STANCE_FILE = _SRC / "config" / "fomc-stance.json"
_FLEX_REVIEW_FILE = _SRC / "config" / "flex-review.json"
_RISK_LIMITS_FILE = _SRC / "config" / "risk-limits.json"

# Single stocks in the fixed core roster (idiosyncratic risk) — the single-name soft
# cap applies to these, not to diversified ETF sleeves (which a high-conviction quadrant
# is meant to concentrate past the cap). Everything else in CORE_ROSTER is an ETF.
# No core single stocks remain after the roster revision (all roles are ETFs; the
# former single names AMZN/GOOGL/INTC/MCK are LEGACY_EXITS). Kept as an empty tuple so
# the single-name soft-cap loop is a harmless no-op.
_CORE_SINGLE_STOCKS: tuple[str, ...] = ()
# Literal-cash buffer kept inside the cash sleeve (rest of the sleeve is SGOV).
_CASH_BUFFER_PCT = 1.5
# Trailing window (trading days) for the flex borderline-quadrant tiebreak (D1,
# 2026-07-21). A named constant, deliberately NOT an env knob in v1.
_FLEX_TIEBREAK_WINDOW_D = 5
# Extra tickers fetched for the leading-growth (Task A) and market-implied-quadrant
# (Task B) builders. XLY is the cyclicals/discretionary signal vs XLP (already in
# CORE_ROSTER via the staples role); CPER is the copper ETF used for the copper/gold
# growth proxy. SPY is already in the rotation universe; GLD in CORE_ROSTER.
_LEADING_GROWTH_EXTRAS = ("XLY", "CPER")

_DIVERGENCE_CONFIG_FILE = _SRC / "config" / "divergence-config.json"
_SPY_SMA_WINDOW = 200  # long-trend filter for the price-vs-regime divergence (spec §6)
# Fallback divergence thresholds if config/divergence-config.json is missing/invalid
# (mirror that file — it is the canonical source).
_DIVERGENCE_DEFAULTS = {
    "leading_vs_lagging_inflation": {"breakeven_delta_20d_bp": 15.0, "oil_20d_pct": 10.0},
    "credit_complacency": {"hy_oas_pct_rank_max": 10.0, "hy_oas_complacency_level_pct": 3.5},
    "price_vs_regime": {},
    "dollar_vs_intl_tilt": {"intl_heavy_pct": 20.0, "intl_light_pct": 8.0},
    "leading_vs_lagging_growth": {"diffusion_threshold": 0.3},
    "market_vs_macro_quadrant": {"basket_momentum_min_pct": 2.0, "vote_majority_threshold": 0.5},
    "market_implied_quadrant": {"confidence_min_populated": {"low": 2, "medium": 3, "high": 5}},
    "staleness_days": 7,
}

# Fallback risk limits if config/risk-limits.json is missing/invalid (keep in sync
# with that file — it is the canonical source; this only guards a broken deploy).
_RISK_LIMITS_DEFAULTS = {
    "active_quadrant_ceiling_pct_of_core": 90.0,
    "sleeve_floor_pct_of_core": 0.1,
    "single_name_cap_pct": {"flex": 4.0, "any_name_soft": 15.0},
    "cash_sleeve_band_pct": {"floor": 5.0, "ceiling": 15.0, "shock3_ceiling": 25.0},
    "flex_sleeve_cap_pct": {"soft": 15.0, "hard": 25.0},
    "exempt_holds": list(EXEMPT_HOLDS),
    "conviction_ladder_pct_of_core": [
        {"risk_score_max": 2, "conviction": "very_high", "active_quadrant_target": 90.0},
        {"risk_score_max": 4, "conviction": "high", "active_quadrant_target": 78.0},
        {"risk_score_max": 6, "conviction": "mixed", "active_quadrant_target": 50.0},
        {"risk_score_max": 8, "conviction": "low", "active_quadrant_target": 30.0},
        {"risk_score_max": 10, "conviction": "no_read", "active_quadrant_target": 15.0},
    ],
    "no_read_ballast": {
        "conviction_score_min": 7.0,
        "ballast_names": ["GLD", "TLT"],
        "ballast_target_pct_of_core": 55.0,
    },
    "thematic_conviction": {
        "enabled": True,
        "ladder": [
            {"p_up_min": 0.75, "conviction": "very_high", "target_pct_of_equity": 4.0},
            {"p_up_min": 0.65, "conviction": "high", "target_pct_of_equity": 2.5},
            {"p_up_min": 0.58, "conviction": "moderate", "target_pct_of_equity": 1.25},
            {"p_up_min": 0.52, "conviction": "low", "target_pct_of_equity": 0.50},
            {"p_up_min": 0.0, "conviction": "none", "target_pct_of_equity": 0.0},
        ],
        "per_ticker_cap_pct_of_equity": 4.0,
        "aggregate_cap_pct_of_equity": 8.0,
        "max_session_delta_pp": 1.5,
        "confirm_sessions": 2,
        "release_sessions": 2,
        "min_evidence_items": 2,
        "horizon_days": [30, 60, 90],
        "brier_min_sample": 10,
        "brier_damping": [
            {"brier_max": 0.20, "factor": 1.0},
            {"brier_max": 0.25, "factor": 0.75},
            {"brier_max": 0.30, "factor": 0.50},
            {"brier_max": 1.0, "factor": 0.0},
        ],
    },
    "borderline_blend": {
        "intersection_target_pct_of_core": 60.0,
        "divergent_staged_pct_of_core": 20.0,
    },
    "transition_watch": {
        "staged_fraction_de_risk": 0.30,
        "staged_fraction_re_risk": 0.15,
        "re_risk_min_confirmations": 2,
        "confirm_sessions": 2,
        "release_sessions": 2,
        "max_session_delta_frac": 0.10,
    },
    "policy_axis": {
        "dgs2_delta_20d_bp_hawkish": 20.0,
        "dgs2_delta_20d_bp_dovish": 20.0,
        "manual_fresh_days": 45,
    },
    "quadrant_performance": {
        "suspect_after_sessions": 10,
    },
    "bond_signals": {
        "hy_oas_trend_bp": 10.0,
    },
    "labor_leading": {
        "forward_softening_gap_k": 20.0,
    },
    "market_shock": {
        "news_baseline_min_sessions": 10,
        "news_baseline_window_sessions": 20,
        "news_z_watch": 1.5,
        "news_z_elevated": 2.5,
        "news_z_acute": 3.5,
        "persistent_theme_sessions": 10,
        "persistent_theme_hits_floor": 15.0,
        "dxy_news_corroboration_hits": 8,
        "no_baseline_watch_floor_hits": 5,
    },
}

# Conviction-sleeve flex-review defaults (overridable via config/flex-review.json).
_FLEX_REVIEW_DEFAULTS = {
    "REVIEW_DAYS": 60,
    "LAG_TOL_PP": -2.0,
    "BREAK_PP": -5.0,
    "EXTENSION_DAYS": 30,
    "DEADBAND_PP": 1.0,
}
# Cap on non-held flex candidates fetched per run — protects the FMP 250 req/day
# budget (each candidate costs ~2 calls: profile + EOD price). See FOLLOWUPS #8.
_FLEX_CANDIDATES_MAX = 20
# Dynamic watch_candidates: per-emission cap (prompt-enforced) and walk-back window.
_WATCH_CANDIDATES_EMISSION_CAP = 6
_DYNAMIC_WALKBACK_DAYS = 7
# Regex for sanitizing dynamic candidate symbols (uppercase-first, 1–10 chars).
_WATCH_CANDIDATE_RE = re.compile(r'^[A-Z][A-Z0-9.\-]{0,9}$')
_ETF_WATCHLIST = ["IDVO", "IDMO", "AIA"]

# --- Catalyst-sleeve funnel (Task A/B/D, session 2026-08-10) ----------------
# G1 fix: the market-wide earnings calendar was fetched then discarded outright
# (_filter_earnings_to_universe). Row cap for the market-wide subset now
# emitted alongside the book's own `earnings_calendar` — protects snapshot size
# since the calendar row carries no volume/market-cap field to screen on
# directly (the real ADV floor applies downstream, once a name is promoted to
# the catalyst screen and its price history is fetched). `_TICKER_FORMAT_RE` is
# a cheap proxy that drops obvious non-common-stock symbols (class shares,
# warrants, OTC/foreign suffixes) before the count cap is applied.
_EARNINGS_MARKET_CAP = 40
_TICKER_FORMAT_RE = re.compile(r'^[A-Z]{1,5}$')
# G3 fix: how many genuinely-new (never held, never nominated) candidates get
# their profile + full daily-bar history fetched per run (2 FMP calls each —
# the only recurring cost this funnel adds; see FOLLOWUPS + the PR body for
# the daily delta). Decision gate 3 (PR body): proposed default, not yet
# confirmed by the account holder.
_CATALYST_DISCOVERY_CAP = 25
# How many of the scored, screened survivors get nominated into flex_candidates
# each run (Task D spec default).
_CATALYST_TOP_N = 15
_CATALYST_MIN_COMPONENTS = catalyst_screen.MIN_COMPONENTS_RANKABLE
# Forward window for the earnings_proximity component — mirrors the earnings
# calendar's own 2-week fetch horizon (`to_2w` below), so nothing in
# `earnings_market` can fall outside the window earnings_proximity scores.
_CATALYST_EARNINGS_HORIZON_DAYS = 14
# News lookback (calendar days) for the news_recency component, and the
# get_stock_news article limit — bumped from the held-only default (30) now
# that the symbol list is much larger (decision gate 3, PR body).
_CATALYST_NEWS_LOOKBACK_DAYS = 7
_STOCK_NEWS_LIMIT = 100
# Trailing trading-day window + symmetric clamp for the momentum component.
_CATALYST_MOMENTUM_WINDOW_D = 10
_CATALYST_MOMENTUM_CAP_PCT = 15.0
# Congressional-purchase cluster size that scores a full 1.0 political_flow.
_CATALYST_POLITICAL_CAP = 5
# Minimum close-price observations for the "price history present" hard filter.
# NOT a literal ATR presence check — the integrated FMP historical-price-eod
# /light endpoint returns close+volume only (no high/low); see the Task C
# probe + catalyst_screen.py's module docstring for the full finding. Matches
# avg_dollar_volume's own 20-day window (no point requiring more than its
# consumer needs).
_CATALYST_MIN_PRICE_OBS = 20
# Company-level tone keyword sets for the news_tone component — mirrors the
# `_SHOCK_KEYWORDS` scan shape/style above (Task B): same headline+summary
# text extraction, same first-match-per-item-per-category counting.
_CATALYST_TONE_KEYWORDS: dict[str, list[str]] = {
    "positive": [
        "beats", "beat estimates", "raises guidance", "record revenue",
        "upgrade", "outperform", "strong demand", "contract win", "expands",
        "surpasses", "accelerating growth", "buyback", "new order", "wins bid",
    ],
    "negative": [
        "misses", "miss estimates", "cuts guidance", "downgrade",
        "underperform", "weak demand", "recall", "investigation", "lawsuit",
        "delisting", "restatement", "resigns", "layoffs", "guidance cut",
    ],
}
# Phase C §5: horizons (calendar days) at which a recommendation's outcome vs SPY
# is stamped onto its TradeHistory row.
_OUTCOME_HORIZONS = [30, 60, 90]
# Phase C §6: headline hit-rate horizon (30d/90d shown for context); enum-coarsening
# map for primary_trigger (capture fine, report coarse) and the per-fine-bucket
# sample size at which a fine trigger gets promoted to its own reported line.
_HEADLINE_HORIZON = 60
_COARSE_TRIGGER = {
    "news_catalyst": "catalyst",
    "earnings": "catalyst",
    "congressional_cluster": "catalyst",
    "thematic_tier": "thematic",
    "valuation": "valuation",
    "technical": "technical",
}
_TRIGGER_PROMOTION_MIN = 10

# Regional rotation universe: SPY benchmark + international ETFs in Core.
# Used to compute 60-trading-day relative strength + 50/200d MA cross so the
# analyzer can call US-vs-international rotation independently of the quadrant.
_ROTATION_TICKERS = ["SPY", "IDMO", "AIA", "IEMG", "VSS", "EUAD", "EWZ", "EWJ"]
_ROTATION_WINDOW_DAYS = 60
_MA_LONG_DAYS = 200
_MA_SHORT_DAYS = 50
# Pure-international subset used for MA-cross signals against SPY.
_INTL_RATIO_TICKERS = ["IDMO", "AIA", "IEMG", "EWJ"]
# FRED deep-history fetch depth for the rotation/bond-signals pre-computes —
# tied to _ROTATION_WINDOW_DAYS (the widest consumer) + a calendar-gap buffer
# (weekends/holidays/occasional missing prints) rather than a bare literal
# (2026-08-06 audit B4).
_MACRO_DEEP_FETCH_DAYS = _ROTATION_WINDOW_DAYS + 30
# Days DTWEXBGS's latest observation may lag `today` before the FX-pairs
# dollar_proxy fallback kicks in. Boundary fixed 2026-08-06 (audit B4): was
# `> 5`, which left the fallback dark at exactly 5 days stale; also now fires
# when the DXY cadence can't be evaluated at all (no observation whatsoever,
# not just a stale one).
_DXY_STALE_FALLBACK_DAYS = 5

# Market shock detection: short-horizon move windows and keyword sets.
# The analyzer uses the resulting shock_level to optionally override the 60d
# rotation windows and lift tilt limits when a structural event hits the tape.
_SHOCK_SHORT_WINDOW_DAYS = 5
_SHOCK_VOL_LOOKBACK_DAYS = 60
_SHOCK_KEYWORDS: dict[str, list[str]] = {
    "geopolitical": [
        "tariff", "tariffs", "sanction", "sanctions", "embargo", "export ban",
        "war", "invasion", "missile", "strike", "attack", "airstrike",
        "ceasefire", "escalation", "retaliation", "trade war",
    ],
    "policy_shock": [
        "emergency cut", "emergency hike", "surprise cut", "surprise hike",
        "intervention", "devaluation", "capital controls", "shutdown",
        "debt ceiling", "default", "downgrade", "impeach", "resign",
        "bailout", "liquidity facility",
    ],
    "market_stress": [
        "crash", "plunge", "collapse", "contagion", "recession",
        "bankruptcy", "insolvency", "halt", "circuit breaker",
        "freeze", "run on", "margin call", "liquidation",
    ],
}


def _load_flex_candidates(
    exclude: set[str],
    today: str,
) -> tuple[list[str], dict[str, str]]:
    """Non-held flex candidate tickers: static seed + analyzer-emitted dynamics.

    Returns ``(tickers, provenance_map)`` where ``provenance_map`` maps each
    ticker to ``"static"`` or ``"dynamic"``.

    Static seed: ``config/flex-candidates.json`` (existing behavior); capped at
    ``_FLEX_CANDIDATES_MAX`` total. Dynamic names: sourced from the MOST RECENT
    ``daily-trades/{date}.json`` found walking back up to ``_DYNAMIC_WALKBACK_DAYS``
    calendar days (reuses the ``_build_execution_review`` walk-back pattern).

    Sanitization of dynamic names (each drop logged at INFO):
    - Uppercased; must match ``^[A-Z][A-Z0-9.\\-]{0,9}$``
    - Not currently held (in ``exclude``)
    - Not in ``flex.regime.flex_separation_set(exclude)`` (core roster separation)
    - Not a ``LEGACY_EXITS`` name that is NOT ``FLEX_REENTERABLE``
    - Not already present (dedup)

    Static names always take priority; dynamic names fill the remainder up to
    ``_FLEX_CANDIDATES_MAX`` (cap=20). Persistence is LAST-EMISSION-ONLY — the
    dynamic list is exactly the newest trades file's emission; re-emit a name to
    keep it in the funnel (A-G1 default: simplest, no stale ledger). See FOLLOWUPS #8 v2.
    """
    from flex.regime import FLEX_REENTERABLE, flex_separation_set

    # --- Static seed ---------------------------------------------------------
    static_names: list[str] = []
    try:
        with open(_FLEX_CANDIDATES_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("flex-candidates.json missing or invalid — no static candidates this run")
        data = {}
    for raw in data.get("candidates", []):
        t = (raw or "").upper().strip()
        if t and t not in exclude and t not in static_names:
            static_names.append(t)

    # --- Dynamic names from most recent trades file (walk back up to 7 days) --
    d0 = date.fromisoformat(today)
    dynamic_doc: dict | None = None
    for back in range(1, _DYNAMIC_WALKBACK_DAYS + 1):
        d_str = (d0 - timedelta(days=back)).isoformat()
        doc = read_trades(d_str)
        if isinstance(doc, dict):
            dynamic_doc = doc
            break

    separation = flex_separation_set(exclude)
    dynamic_names: list[str] = []
    if dynamic_doc is not None:
        raw_wc = dynamic_doc.get("watch_candidates")
        if isinstance(raw_wc, list):
            for item in raw_wc[:_WATCH_CANDIDATES_EMISSION_CAP * 2]:  # budget guard
                if not isinstance(item, dict):
                    continue
                raw_sym = item.get("symbol") or ""
                if not isinstance(raw_sym, str):
                    logger.info(
                        "flex_candidates dynamic: dropped non-string symbol %r", raw_sym
                    )
                    continue
                sym = raw_sym.upper().strip()
                if not sym:
                    continue
                if not _WATCH_CANDIDATE_RE.match(sym):
                    logger.info(
                        "flex_candidates dynamic: dropped '%s' — invalid symbol format", sym
                    )
                    continue
                if sym in exclude:
                    logger.info(
                        "flex_candidates dynamic: dropped '%s' — currently held", sym
                    )
                    continue
                if sym in separation:
                    logger.info(
                        "flex_candidates dynamic: dropped '%s' — core roster separation", sym
                    )
                    continue
                if sym in LEGACY_EXITS and sym not in FLEX_REENTERABLE:
                    logger.info(
                        "flex_candidates dynamic: dropped '%s' — non-reenterable legacy exit", sym
                    )
                    continue
                if sym in static_names or sym in dynamic_names:
                    continue  # deduplicate silently
                dynamic_names.append(sym)

    # --- Merge: static priority, cap at _FLEX_CANDIDATES_MAX -----------------
    combined = static_names + dynamic_names
    combined = combined[:_FLEX_CANDIDATES_MAX]
    provenance: dict[str, str] = {
        sym: ("static" if sym in static_names else "dynamic")
        for sym in combined
    }
    return combined, provenance


def _load_fomc_stance() -> dict:
    """Manually-maintained FOMC policy stance from config/fomc-stance.json.

    The dot-plot / SEP and CME-FedWatch odds are NOT FRED series, so the funds-rate
    *level* (DFF) is all the automated feed carries. This file is the policy *stance*
    the analyzer echoes; update it after each SEP. Missing/malformed/blank → an
    ``unconfirmed`` stance (policy cannot confirm Q1; see _build_regime_gate). Goes
    stale by design — the analyzer should flag the ``as_of`` age.
    """
    try:
        with open(_FOMC_STANCE_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"stance": "unconfirmed", "note": "fomc-stance.json missing/invalid"}
    stance = (data.get("stance") or "unconfirmed").lower().strip()
    if stance not in ("hawkish", "neutral", "dovish", "unconfirmed"):
        stance = "unconfirmed"
    data["stance"] = stance
    return data


def _load_flex_review_config() -> dict:
    """Conviction-sleeve flex-review knobs from config/flex-review.json.

    Missing/malformed file or absent keys → the documented defaults. Numeric only.
    """
    cfg = dict(_FLEX_REVIEW_DEFAULTS)
    try:
        with open(_FLEX_REVIEW_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return cfg
    for k in _FLEX_REVIEW_DEFAULTS:
        v = data.get(k)
        if isinstance(v, (int, float)):
            cfg[k] = v
    return cfg


def _close_by_date(fmp: FMPClient, symbol: str) -> dict[str, float]:
    """{'YYYY-MM-DD': close} from FMP's ~5yr EOD light series (one call)."""
    out: dict[str, float] = {}
    for row in fmp.get_historical_price_light(symbol):
        d = row.get("date")
        c = row.get("price") if row.get("price") is not None else row.get("close")
        if d and c is not None:
            try:
                out[str(d)[:10]] = float(c)
            except (TypeError, ValueError):
                continue
    return out


def _close_on_or_before(close_map: dict[str, float], target: str) -> float | None:
    """Close on `target`, else the most recent trading day before it (weekends/holidays)."""
    if target in close_map:
        return close_map[target]
    earlier = [d for d in close_map if d <= target]
    return close_map[max(earlier)] if earlier else None


def _outcome_level(status: str | None) -> int:
    return {"30d": 30, "60d": 60, "90d": 90, "closed": 90}.get(status or "", 0)


def _max_matured_horizon(rec_date: str, today: date) -> int:
    best = 0
    for n in _OUTCOME_HORIZONS:
        if date.fromisoformat(rec_date) + timedelta(days=n) <= today:
            best = n
    return best


def _outcome_metrics(side: str, p0: float, s0: float, pn: float, sn: float) -> dict:
    """Pure: symbol vs SPY return over a window + whether the call was correct.

    A buy is correct if the symbol beat SPY (excess > 0); a sell/trim is correct
    if it lagged SPY (excess < 0). `correct` is omitted for non-buy/sell sides.
    """
    ret = (pn / p0 - 1.0) * 100.0
    spy_ret = (sn / s0 - 1.0) * 100.0
    excess = ret - spy_ret
    out = {"ret": round(ret, 3), "spy_ret": round(spy_ret, 3), "excess": round(excess, 3)}
    s = (side or "").lower()
    if s in ("buy", "sell"):
        out["correct"] = (excess > 0) if s == "buy" else (excess < 0)
    return out


def _stamp_trade_outcomes(fmp: FMPClient) -> None:
    """Phase C §5: stamp matured TradeHistory rows with N-day return vs SPY.

    For each recommendation whose 30/60/90-day mark has passed and isn't yet
    stamped, compute the symbol's excess return vs SPY over the window and whether
    the call was correct (buy beat SPY / sell lagged SPY). Read-only on prices;
    caller wraps in try/except so this can never break the collector. One FMP call
    per unique symbol needing work + one for SPY. FOLLOWUPS #7 / Phase C spec §5.
    """
    today = date.today()
    hi = (today - timedelta(days=min(_OUTCOME_HORIZONS))).isoformat()  # >= 30d old
    rows = query_entities("TradeHistory", f"recommended_at le '{hi}'")

    # Rows with a horizon that has matured beyond what's already stamped.
    pending = []
    for r in rows:
        rec, sym = r.get("recommended_at"), r.get("symbol")
        if not rec or not sym:
            continue
        if _max_matured_horizon(rec, today) > _outcome_level(r.get("outcome_status")):
            pending.append(r)
    if not pending:
        logger.info("Outcome stamping: nothing matured to stamp")
        return

    # One price series per unique symbol + SPY (cached for this run).
    series: dict[str, dict[str, float]] = {}
    for s in {r["symbol"] for r in pending} | {"SPY"}:
        series[s] = _close_by_date(fmp, s)
    spy_map = series.get("SPY") or {}
    if not spy_map:
        logger.warning("Outcome stamping: no SPY series — skipping")
        return

    stamped = 0
    for r in pending:
        rec = r["recommended_at"]
        sym_map = series.get(r["symbol"]) or {}
        p0 = _close_on_or_before(sym_map, rec)
        s0 = _close_on_or_before(spy_map, rec)
        if not p0 or not s0:
            continue
        side = (r.get("side") or "").lower()
        patch = {
            "PartitionKey": r["PartitionKey"], "RowKey": r["RowKey"],
            "price_at_rec": round(p0, 4), "spy_at_rec": round(s0, 4),
        }
        highest = 0
        for n in _OUTCOME_HORIZONS:
            if date.fromisoformat(rec) + timedelta(days=n) > today:
                continue  # not matured yet
            target = (date.fromisoformat(rec) + timedelta(days=n)).isoformat()
            pn = _close_on_or_before(sym_map, target)
            sn = _close_on_or_before(spy_map, target)
            if not pn or not sn:
                continue
            m = _outcome_metrics(side, p0, s0, pn, sn)
            patch[f"ret_{n}d_pct"] = m["ret"]
            patch[f"spy_ret_{n}d_pct"] = m["spy_ret"]
            patch[f"excess_{n}d_pp"] = m["excess"]
            if "correct" in m:
                patch[f"call_correct_{n}d"] = m["correct"]
            highest = n
        if highest == 0:
            continue
        patch["outcome_status"] = "closed" if highest >= max(_OUTCOME_HORIZONS) else f"{highest}d"
        upsert_entity("TradeHistory", patch)
        stamped += 1
    logger.info("Outcome stamping: %d row(s) stamped (of %d pending)", stamped, len(pending))


# ---------------------------------------------------------------------------
# Phase C §4 — performance scoreboard (account equity vs fully-invested SPY)
# ---------------------------------------------------------------------------

def _load_equity_spy_series(
    today: str,
    equity: float | None,
    spy_close: float | None,
    cash: float | None,
    prices: dict | None = None,
    growth_axis: dict | None = None,
    inflation_axis: dict | None = None,
) -> list[dict]:
    """Compact, self-healing (date, equity, spy_close, cash_pct) series.

    Reuses the web `performance` endpoint basis: a day counts only when it has
    BOTH `paper_account.equity` and `prices.SPY.c` (so the series begins on the
    first funded/trading day and normalized %-change is the true time-weighted
    return vs SPY — no external cash flows). Backed by a tiny cached blob so the
    collector downloads each ~1 MB snapshot at most once ever; any missing prior
    day is backfilled from its snapshot, and today's point is taken from the
    in-memory values (today's snapshot blob isn't written yet). Phase C §4.

    Each point also carries `closes` (CORE_ROSTER EOD closes) + `favored_bucket`
    (the day's quadrant read) for the web quadrant-vs-SPY chart. Points written
    before those fields existed are re-hydrated from their snapshot once (same
    at-most-once-more property as the original backfill).
    """
    series = read_perf_series()
    by_date = {p.get("date"): p for p in series}
    changed = False

    for d in list_snapshot_dates():
        if d >= today:
            continue
        existing = by_date.get(d)
        if existing is not None and "closes" in existing:
            continue
        try:
            snap = read_snapshot(d)
        except Exception:  # noqa: BLE001
            continue
        closes = _roster_closes(snap.get("prices"))
        fav = favored_bucket(
            ((snap.get("growth_axis") or {}).get("direction")),
            ((snap.get("inflation_axis") or {}).get("direction")),
        )
        if existing is not None:
            # v1 point predating the quadrant fields — patch in place.
            existing["closes"] = closes
            existing["favored_bucket"] = fav
            changed = True
            continue
        eq = (snap.get("paper_account") or {}).get("equity")
        sp = ((snap.get("prices") or {}).get("SPY") or {}).get("c")
        if eq is None or sp is None:
            continue
        csh = (snap.get("paper_account") or {}).get("cash")
        point = _perf_point(d, eq, sp, csh, closes=closes, favored=fav)
        series.append(point)
        by_date[d] = point
        changed = True

    if equity is not None and spy_close is not None:
        point = _perf_point(
            today, equity, spy_close, cash,
            closes=_roster_closes(prices),
            favored=favored_bucket(
                (growth_axis or {}).get("direction"),
                (inflation_axis or {}).get("direction"),
            ),
        )
        existing = by_date.get(today)
        if existing != point:
            series = [p for p in series if p.get("date") != today]
            series.append(point)
            changed = True

    series.sort(key=lambda p: p.get("date") or "")
    if changed:
        try:
            write_perf_series(series)
        except Exception:  # noqa: BLE001
            logger.exception("Could not persist perf series (non-fatal)")
    return series


def _build_price_universe(
    tickers: list[str], flex_candidate_tickers: list[str],
    effective_selected: dict[str, str] | None = None,
) -> list[str]:
    """The EOD-price fetch list: held tickers, every role's EFFECTIVE `selected`
    incumbent, the ETF watchlist, and flex candidates (order-preserving, deduped).

    Flex candidates are included so the analyzer can size a buy (weight→shares
    needs a price) and so gatekeeper G2 sees a price for the candidate. Every
    role's `selected` incumbent is included (2026-07-13 audit finding 1) because
    those are exactly the names `reference_weights` can target — a name with no
    held position and no other reason to be fetched (e.g. KMLM, IEF, VXUS while
    unheld) previously had no price, no gap row, and no way for band enforcement
    to synthesize the buy that would close its underweight. ``effective_selected``
    (session 2026-07-27, blanket auto-switch) substitutes a freshly auto-switched
    incumbent (e.g. IHE) for its role's static config `selected` — otherwise a
    switch would target an unpriced name until the NEXT day it happened to also be
    held or watchlisted (`selected_core_members` handles the substitution).
    """
    return list(dict.fromkeys(
        tickers + list(selected_core_members(effective_selected)) + _ETF_WATCHLIST
        + list(_LEADING_GROWTH_EXTRAS) + flex_candidate_tickers
    ))


def _correct_10x_ingestion_error(
    price_now: float, high_52: float, low_52: float,
) -> tuple[float, bool, str]:
    """R1 (2026-08-06 audit) — ingestion-level 10x sanity CORRECTION, applied
    BEFORE the quarantine backstop below. A feed off by a clean factor of ~10x
    (a decimal-place / cents-vs-dollars mixup) against its OWN 52-week range
    can be corrected at the source instead of merely re-quarantining the same
    bad print every session forever (observed: MU printing ~10x its real
    price every session, never corrected). Only corrects when the RESULT
    lands back inside a generous band around the 52-week range — never
    guesses a correction that doesn't itself look sane.

    Returns ``(price, corrected, note)``; ``note`` is ``""`` when uncorrected.
    Never raises — any bad input just returns the original price uncorrected.
    """
    if high_52 <= 0 or low_52 <= 0 or price_now <= 0:
        return price_now, False, ""
    mid = (high_52 + low_52) / 2.0
    if mid <= 0:
        return price_now, False, ""
    ratio = price_now / mid
    sane_lo, sane_hi = low_52 * 0.5, high_52 * 1.5
    if 7.0 <= ratio <= 13.0:
        candidate = price_now / 10.0
        if sane_lo <= candidate <= sane_hi:
            return candidate, True, (
                f"price {price_now:.2f} is ~10x the 52-wk range midpoint {mid:.2f} — "
                f"corrected to {candidate:.2f} at ingestion"
            )
    elif 0.07 <= ratio <= 0.13:
        candidate = price_now * 10.0
        if sane_lo <= candidate <= sane_hi:
            return candidate, True, (
                f"price {price_now:.2f} is ~1/10th the 52-wk range midpoint {mid:.2f} — "
                f"corrected to {candidate:.2f} at ingestion"
            )
    return price_now, False, ""


def _quarantine_flex_price(
    profile: dict,
    prices: dict,
    prior_prices: dict,
    company_news: dict,
    cfg: dict,
) -> tuple[bool, str]:
    """Task E (F7): structural price-sanity guard for flex candidates.

    Returns (quarantined: bool, reason: str).
    First applies the R1 (2026-08-06 audit) 10x ingestion CORRECTION — when it
    fires, ``prices[sym]["c"]`` and ``profile`` are updated in place (the fix
    at the SOURCE) and the corrected price feeds the checks below. Quarantine
    (the backstop, unchanged) fires when:
      1. Price (post-correction) is outside the symbol's 52-week high/low
         range by > range_pct, OR
      2. Price moved > single_day_move_pct vs prior snapshot EOD without a
         corroborating news hit in company_news for that symbol.

    Never raises — returns (False, "") on any input error.
    """
    sym = (profile.get("symbol") or "").upper()
    if not sym:
        return False, ""
    q_cfg = cfg.get("price_quarantine") or {}
    range_pct = float(q_cfg.get("range_pct", 20.0))
    move_pct = float(q_cfg.get("single_day_move_pct", 50.0))

    price_now_raw = (prices.get(sym) or {}).get("c")
    if price_now_raw is None:
        return False, ""  # no price — can't quarantine on data we don't have
    try:
        price_now = float(price_now_raw)
    except (TypeError, ValueError):
        return False, ""

    try:
        high_52 = float(profile.get("yearHigh") or 0)
        low_52 = float(profile.get("yearLow") or 0)
    except (TypeError, ValueError):
        high_52 = low_52 = 0.0

    # --- Gate 0: 10x ingestion correction (source fix, before quarantine) ---
    corrected_price, was_corrected, correction_note = _correct_10x_ingestion_error(
        price_now, high_52, low_52)
    if was_corrected:
        prices[sym]["c"] = corrected_price
        profile["price_corrected_10x"] = True
        profile["price_correction_note"] = correction_note
        logger.warning("Flex candidate %s: %s", sym, correction_note)
        price_now = corrected_price

    # --- Gate 1: 52-week range check (backstop) ------------------------------
    if high_52 > 0 and low_52 > 0:
        # Allow a range_pct% overshoot beyond either bound before quarantining.
        if price_now > high_52 * (1 + range_pct / 100.0):
            return True, (f"price {price_now:.2f} is >{range_pct}% above 52-wk high {high_52:.2f} "
                          f"— possible data error")
        if price_now < low_52 * (1 - range_pct / 100.0):
            return True, (f"price {price_now:.2f} is >{range_pct}% below 52-wk low {low_52:.2f} "
                          f"— possible data error")

    # --- Gate 2: large single-day move without news corroboration -----------
    price_prev_raw = (prior_prices.get(sym) or {}).get("c")
    if price_prev_raw is not None:
        try:
            price_prev = float(price_prev_raw)
            if price_prev > 0:
                delta_pct = abs((price_now / price_prev - 1.0) * 100.0)
                if delta_pct > move_pct:
                    # Check for corroborating news (any item in company_news for this symbol).
                    news_items = company_news.get(sym) or []
                    if not news_items:
                        return True, (f"price moved {delta_pct:.1f}% vs prior snapshot "
                                      f"({price_prev:.2f}→{price_now:.2f}) with no news corroboration "
                                      f"— possible bad print")
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    return False, ""


def _filter_earnings_to_universe(rows: list[dict], universe) -> list[dict]:
    """Keep only earnings-calendar rows whose symbol is in the book's universe
    (held ∪ every role's `selected` ∪ flex candidates ∪ currently-held legacy exits).

    FMP's earnings-calendar endpoint returns the MARKET-WIDE calendar; writing it
    unfiltered both buries held names' confirmed dates and pads the snapshot with
    irrelevant names (deferred finding 4, 2026-07-13: the 07-14/15 reports claimed
    'no held positions report within 14 days' while GOOGL reported 07-22; the 07-21
    report listed MCD/MPC/REZI/HALO — none held). Row schema preserved."""
    keep = {str(s).upper() for s in (universe or ())}
    return [r for r in (rows or []) if str(r.get("symbol") or "").upper() in keep]


def _screen_earnings_market_rows(
    rows: list[dict], universe, cap: int,
) -> tuple[list[dict], int]:
    """Task A (2026-08-10 catalyst-sleeve funnel, G1 fix) — the market-wide rows
    NOT already covered by the book's own `earnings_calendar` (this is the
    ADDITIONAL subset; existing consumers of `earnings_calendar` are untouched).

    The calendar row carries no volume/market-cap field, so there is no ADV/size
    figure to screen on directly here — `_TICKER_FORMAT_RE` is a cheap PROXY
    (plain 1-5 letter tickers only, dropping class shares / warrants / OTC-style
    suffixes) rather than a true liquidity floor; the real ADV floor applies
    downstream once a name is promoted to the catalyst screen (Task D) and its
    price history is fetched. Capped at `cap` rows, nearest-dated first, so the
    snapshot never balloons on a heavy earnings week. Returns
    ``(kept_rows, dropped_by_cap_count)``.
    """
    seen = {str(s).upper() for s in (universe or ())}
    candidates = [
        r for r in (rows or [])
        if _TICKER_FORMAT_RE.match(str(r.get("symbol") or "").upper())
        and str(r.get("symbol") or "").upper() not in seen
    ]
    candidates.sort(key=lambda r: r.get("date") or "9999-99-99")
    kept = candidates[:cap]
    dropped = len(candidates) - len(kept)
    return kept, dropped


def _build_catalyst_screen(
    discovery: list[str],
    profiles_by_symbol: dict[str, dict],
    bars_by_symbol: dict[str, list[dict]],
    earnings_market_rows: list[dict],
    stock_news: list[dict],
    congressional: list[dict],
    quadrant: str,
    quadrant_basis: str,
    held: set[str],
    exclude: set[str],
    legacy_blocked: set[str],
    min_adv_usd: float,
    today: str,
    top_n: int,
) -> dict:
    """Task D (2026-08-10 catalyst-sleeve funnel, G3 fix) — scores the catalyst
    discovery universe and returns the `catalyst_screen` snapshot block.

    Everything passed in is ALREADY FETCHED (no I/O here) — this is the
    collector-side glue that shapes raw FMP/Quiver/Finnhub data into
    `catalyst_screen`'s per-candidate input contract, mirroring
    `_build_flex_quadrant`'s role for `flex.regime.resolve_quadrant`. The
    flex_candidates MERGE (appending to collector-local mutable lists, running
    the price-quarantine guard) has side effects and stays in `collect()` —
    this function only returns the block, it never mutates its inputs.
    """
    earnings_dates: dict[str, str] = {}
    for r in earnings_market_rows or ():
        sym = str(r.get("symbol") or "").upper()
        if sym and r.get("date") and sym not in earnings_dates:
            earnings_dates[sym] = r["date"]

    news_by_symbol = catalyst_screen.group_news_by_symbol(stock_news)

    # Political-flow cluster count: congressional PURCHASE rows only (a buy
    # cluster is the standard bullish-attention reading; Quiver's field name
    # for transaction direction varies by response vintage, so several
    # aliases are checked — a missing/unrecognized field degrades to 0
    # (absent, per composite_score's own absent-vs-zero handling) rather than
    # fabricating a direction).
    political_counts: dict[str, int] = {}
    for r in congressional or ():
        sym = (r.get("Ticker") or r.get("ticker") or "").upper()
        if not sym:
            continue
        txn = str(r.get("Transaction") or r.get("transaction")
                   or r.get("TransactionType") or "").lower()
        if "purchase" in txn or "buy" in txn:
            political_counts[sym] = political_counts.get(sym, 0) + 1

    candidates = []
    for sym in discovery:
        profile = profiles_by_symbol.get(sym) or {}
        bars = bars_by_symbol.get(sym) or []
        adv = avg_dollar_volume(bars) if bars else None
        items = news_by_symbol.get(sym) or []
        hits = catalyst_screen.keyword_hits(items, _CATALYST_TONE_KEYWORDS)
        raw_momentum = catalyst_screen.momentum_from_bars(bars, _CATALYST_MOMENTUM_WINDOW_D)
        candidates.append({
            "symbol": sym,
            "screen": {
                "held": sym in held,
                "separated": sym in exclude,
                "non_reenterable_legacy": sym in legacy_blocked,
                "has_price_data": len(bars) >= _CATALYST_MIN_PRICE_OBS,
                "adv_usd": adv,
                "min_adv_usd": min_adv_usd,
            },
            "components": {
                "earnings_proximity": catalyst_screen.earnings_proximity_score(
                    earnings_dates.get(sym), today, _CATALYST_EARNINGS_HORIZON_DAYS),
                "news_recency": catalyst_screen.news_recency_score(
                    catalyst_screen.days_since_latest_news(items, today),
                    _CATALYST_NEWS_LOOKBACK_DAYS),
                "news_tone": catalyst_screen.news_tone_score(
                    bool(items), hits["positive"], hits["negative"]),
                "momentum": catalyst_screen.momentum_score(
                    raw_momentum, _CATALYST_MOMENTUM_CAP_PCT),
                "regime_fit_score": regime_fit_score(
                    profile.get("sector"), quadrant, quadrant_basis),
                "political_flow": catalyst_screen.political_flow_score(
                    political_counts.get(sym, 0), _CATALYST_POLITICAL_CAP),
            },
            "basis": {
                "sector": profile.get("sector"),
                "adv_usd": adv,
                "price_observations": len(bars),
                "earnings_date": earnings_dates.get(sym),
                "news_item_count": len(items),
                "news_positive_hits": hits["positive"],
                "news_negative_hits": hits["negative"],
                "political_purchase_count": political_counts.get(sym, 0),
                "momentum_raw_pct": raw_momentum,
                "quadrant": quadrant,
                "quadrant_basis": quadrant_basis,
            },
        })

    result = catalyst_screen.build_ranking_ledger(candidates, top_n)
    return {
        "available": True,
        "quadrant": quadrant,
        "quadrant_basis": quadrant_basis,
        "discovery_universe": discovery,
        "discovery_cap": _CATALYST_DISCOVERY_CAP,
        "top_n": top_n,
        "min_components_rankable": _CATALYST_MIN_COMPONENTS,
        "ledger": result["ledger"],
        "nominated": result["nominated"],
    }


def _roster_closes(prices: dict | None) -> dict:
    """EOD closes for the fixed core roster (the quadrant-basket members)."""
    out = {}
    for t in CORE_ROSTER:
        c = ((prices or {}).get(t) or {}).get("c")
        if c is not None:
            out[t] = round(float(c), 4)
    return out


def _perf_point(
    d: str, equity, spy_close, cash,
    closes: dict | None = None,
    favored: list | None = None,
) -> dict:
    eq = round(float(equity), 2)
    point = {
        "date": d,
        "equity": eq,
        "spy_close": round(float(spy_close), 4),
        "cash_pct": round(float(cash) / eq * 100, 2) if (cash is not None and eq) else None,
    }
    if closes is not None:
        point["closes"] = closes
    if favored is not None:
        point["favored_bucket"] = favored
    return point


def _excess_attribution(series: list[dict], window) -> dict | None:
    """Two-term decomposition of the vs-SPY excess into a cash-sleeve contribution and
    an invested-book contribution (B5, deferred finding 5).

    Reports habitually blame the SPY lag on "cash drag" with the sign backwards: when
    SPY is NEGATIVE since inception, flat T-bills ADD excess, so the lag lives entirely
    in the invested book (2026-07-21). This lands the blame on the right side:
    ``cash_contribution = avg_cash_weight × (r_cash − r_spy)``; ``invested_contribution
    = excess − cash_contribution`` (exact residual, so the two always sum to the excess).
    Approximate by construction (average weights, not a daily attribution) — the sign
    and rough magnitude are the point. ``window`` is ``"inception"`` or an int of days."""
    if len(series) < 2:
        return None
    last = series[-1]
    latest = last["date"]
    if window == "inception":
        start = series[0]
        window_label = "inception"
    else:
        target = (date.fromisoformat(latest) - timedelta(days=int(window))).isoformat()
        start = None
        for p in series:
            if p["date"] <= target:
                start = p
        if start is None:
            return None
        window_label = f"{int(window)}d"

    eq0, eqN = start["equity"], last["equity"]
    spy0, spyN = start["spy_close"], last["spy_close"]
    if not eq0 or not spy0:
        return None
    r_book = (eqN / eq0 - 1.0) * 100.0
    r_spy = (spyN / spy0 - 1.0) * 100.0
    excess = r_book - r_spy

    sgov0 = (start.get("closes") or {}).get("SGOV")
    sgovN = (last.get("closes") or {}).get("SGOV")
    if sgov0 and sgovN:
        r_cash = (sgovN / sgov0 - 1.0) * 100.0
        cash_src = "SGOV price return"
    else:
        r_cash = 0.0
        cash_src = "cash return unavailable → treated as 0"

    window_points = [p for p in series if p["date"] >= start["date"]]
    cashes = [p.get("cash_pct") for p in window_points if p.get("cash_pct") is not None]
    avg_cash = sum(cashes) / len(cashes) if cashes else 0.0
    cash_contribution = (avg_cash / 100.0) * (r_cash - r_spy)
    invested_contribution = excess - cash_contribution
    return {
        "window": window_label,
        "excess_pp": round(excess, 3),
        "cash_contribution_pp": round(cash_contribution, 3),
        "invested_contribution_pp": round(invested_contribution, 3),
        "avg_cash_pct": round(avg_cash, 2),
        "avg_invested_pct": round(100.0 - avg_cash, 2),
        "method": (
            "two-term decomposition: avg cash weight × (r_cash − r_spy) for the cash "
            "term, invested = excess − cash term (exact residual). "
            f"Cash sleeve return = {cash_src}. Approximate — sign and rough magnitude "
            "are the point, not precision."
        ),
    }


def _build_performance(series: list[dict]) -> dict:
    """Scoreboard block: return-since-inception + rolling 30/60/90d vs SPY.

    Pure function over the compact series (last point is today). Rolling windows
    that predate inception are reported null (not yet available). Phase C §4.
    """
    if not series:
        return {"available": False, "note": "no funded snapshots yet"}

    eq_map = {p["date"]: p["equity"] for p in series}
    spy_map = {p["date"]: p["spy_close"] for p in series}
    first, last = series[0], series[-1]
    inception, latest = first["date"], last["date"]
    days_live = (date.fromisoformat(latest) - date.fromisoformat(inception)).days
    eq0, spy0, eqN, spyN = first["equity"], first["spy_close"], last["equity"], last["spy_close"]

    ret = (eqN / eq0 - 1.0) * 100.0 if eq0 else 0.0
    spy_ret = (spyN / spy0 - 1.0) * 100.0 if spy0 else 0.0

    rolling: dict[str, dict] = {}
    for n in _OUTCOME_HORIZONS:
        target = (date.fromisoformat(latest) - timedelta(days=n)).isoformat()
        eq_then = _close_on_or_before(eq_map, target)
        spy_then = _close_on_or_before(spy_map, target)
        if eq_then and spy_then:
            a = (eqN / eq_then - 1.0) * 100.0
            s = (spyN / spy_then - 1.0) * 100.0
            rolling[f"{n}d"] = {
                "account_pct": round(a, 3),
                "spy_pct": round(s, 3),
                "excess_pp": round(a - s, 3),
            }
        else:
            rolling[f"{n}d"] = {"account_pct": None, "spy_pct": None, "excess_pp": None}

    peak: float | None = None
    max_dd = 0.0
    for p in series:
        e = p["equity"]
        if peak is None or e > peak:
            peak = e
        if peak:
            dd = (e / peak - 1.0) * 100.0
            if dd < max_dd:
                max_dd = dd

    return {
        "available": True,
        "inception_date": inception,
        "days_live": days_live,
        "account": {"equity": eqN, "cash_pct": last.get("cash_pct")},
        "return_since_inception_pct": round(ret, 3),
        "spy_return_since_inception_pct": round(spy_ret, 3),
        "excess_vs_spy_pp": round(ret - spy_ret, 3),
        "excess_attribution": {
            "inception": _excess_attribution(series, "inception"),
            "30d": _excess_attribution(series, 30),
        },
        "rolling": rolling,
        "max_drawdown_pct": round(max_dd, 3),
        "note": (
            f"12-month rolling not yet available (only {days_live} days live)"
            if days_live < 365 else None
        ),
    }


# ---------------------------------------------------------------------------
# FOLLOWUPS #12 — quadrant_performance (regime-call accountability)
# ---------------------------------------------------------------------------

def _quadrant_perf_series(points: list[dict], quadrant_map: dict) -> list[dict]:
    """Equal-weight buy-and-hold index (window start = 100) per quadrant basket.

    This is a DELIBERATE PURE COPY of `web/api/function_app.py::_quadrant_series`
    (the SWA API deploys standalone and cannot import `shared/quadrants.py` or this
    module, so the two are kept in lock-step by hand — cross-reference both sides
    if you change the semantics). A member's base is its first close INSIDE the
    points passed in (so callers control the window by slicing `points` before
    calling); a day's quadrant index is the mean of member normalized closes
    available that day. A member with no base yet contributes nothing (a
    late-appearing ticker — e.g. the 2026-07-10 roster-revision additions — can't
    distort the index retroactively); a quadrant with no members priced that day
    is None.
    """
    bases: dict[str, float] = {}
    out: list[dict] = []
    for p in points:
        closes = p.get("closes") or {}
        for t, c in closes.items():
            if c and t not in bases:
                bases[t] = float(c)
        row: dict = {}
        for q, members in (quadrant_map or {}).items():
            vals = [
                float(closes[t]) / bases[t] * 100.0
                for t in members
                if closes.get(t) and bases.get(t)
            ]
            row[q] = round(sum(vals) / len(vals), 3) if vals else None
        out.append(row)
    return out


def _build_quadrant_performance(
    series: list[dict], quadrant_map: dict[str, tuple[str, ...]], cfg: dict | None = None,
) -> dict:
    """Regime-call accountability (FOLLOWUPS #12): per-bucket basket-vs-SPY
    performance + a hysteresis `suspect` flag for a FAVORED bucket that keeps
    losing to SPY.

    Pure over the SAME compact perf series `_build_performance` already consumed
    (reused, not re-downloaded — see the collector call site) + the CURRENT
    `QUADRANT_CONCENTRATE` membership. Describe-only: it informs the analyzer's
    prose and never touches `reference_weights` or any deterministic gate.

    Window returns (`ret_Nd_pct` / `excess_Nd_pp`) mirror `_quadrant_perf_series`
    semantics: for each window the base is the first close INSIDE that window
    slice, so a late-joining roster member never retroactively distorts earlier
    history (same caveat as the `/performance` web chart — see `roster_note`).

    The streak/lagging scan is a single forward pass per bucket (O(len(series))):
    `favored_streak` counts consecutive sessions (ending today) the bucket has
    appeared in that day's `favored_bucket`; `streak_excess_pp` is the basket's
    cumulative excess vs SPY over that streak, based at the session BEFORE the
    streak began (falls back to day 0 if the streak covers the whole series);
    `lagging_sessions` is the current run (ending today) of sessions where that
    AS-OF-THAT-SESSION streak excess was negative — recomputed at each session,
    not just read off today's number, so a bucket that flips favored on/off
    doesn't inherit a stale run. `suspect` fires when the bucket is favored today
    AND `lagging_sessions >= suspect_after_sessions` (config, default 10).
    """
    if not series:
        return {"available": False, "note": "no perf series yet"}
    cfg = cfg or {}
    suspect_after = int(cfg.get("suspect_after_sessions", 10))
    dates = [p["date"] for p in series]
    today = dates[-1]
    spy_map = {p["date"]: p.get("spy_close") for p in series}

    def _cutoff(days: int) -> str | None:
        target = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
        earlier = [d for d in dates if d <= target]
        return max(earlier) if earlier else None

    buckets: dict[str, dict] = {}
    for q, members in (quadrant_map or {}).items():
        row: dict = {}
        for n in (30, 60, 90):
            cutoff = _cutoff(n)
            ret = excess = None
            if cutoff is not None:
                window_pts = [p for p in series if p["date"] >= cutoff]
                idx_rows = _quadrant_perf_series(window_pts, {q: members})
                last_val = idx_rows[-1].get(q) if idx_rows else None
                if last_val is not None:
                    ret = round(last_val - 100.0, 3)
                    spy0 = spy_map.get(window_pts[0]["date"])
                    spyN = spy_map.get(window_pts[-1]["date"])
                    if spy0 and spyN:
                        excess = round(ret - ((spyN / spy0 - 1.0) * 100.0), 3)
            row[f"ret_{n}d_pct"] = ret
            row[f"excess_{n}d_pp"] = excess

        # --- streak / lagging-run scan (single forward pass) --------------------
        run_start: int | None = None
        bases: dict[str, float] = {}
        streak_len = 0
        streak_excess: float | None = None
        lagging = 0
        for i, p in enumerate(series):
            fav = q in (p.get("favored_bucket") or [])
            if not fav:
                run_start = None
                bases = {}
                streak_len = 0
                streak_excess = None
                lagging = 0
                continue
            if run_start is None:
                run_start = i
                anchor_idx = max(i - 1, 0)
                bases = {}
                anchor_closes = series[anchor_idx].get("closes") or {}
                for t in members:
                    c = anchor_closes.get(t)
                    if c:
                        bases[t] = float(c)
            closes = p.get("closes") or {}
            for t in members:
                c = closes.get(t)
                if c and t not in bases:
                    bases[t] = float(c)
            vals = [
                float(closes[t]) / bases[t] * 100.0
                for t in members if closes.get(t) and bases.get(t)
            ]
            streak_len = i - run_start + 1
            if vals:
                basket_chg = sum(vals) / len(vals) - 100.0
                anchor_date = series[max(run_start - 1, 0)]["date"]
                spy0, spyN = spy_map.get(anchor_date), spy_map.get(p["date"])
                streak_excess = (
                    round(basket_chg - (spyN / spy0 - 1.0) * 100.0, 3)
                    if (spy0 and spyN) else None
                )
            else:
                streak_excess = None
            lagging = lagging + 1 if (streak_excess is not None and streak_excess < 0) else 0

        favored_today_q = q in (series[-1].get("favored_bucket") or [])
        row["favored_streak"] = streak_len
        row["streak_excess_pp"] = streak_excess
        row["lagging_sessions"] = lagging
        row["suspect"] = bool(favored_today_q and lagging >= suspect_after)
        buckets[q] = row

    spy_ret_30 = None
    cutoff30 = _cutoff(30)
    if cutoff30 is not None:
        spy0, spyN = spy_map.get(cutoff30), spy_map.get(today)
        if spy0 and spyN:
            spy_ret_30 = round((spyN / spy0 - 1.0) * 100.0, 3)

    return {
        "available": True,
        "as_of": today,
        "spy_ret_30d_pct": spy_ret_30,
        "buckets": buckets,
        "favored_today": list(series[-1].get("favored_bucket") or []),
        "roster_note": (
            "Basket composition is as-of the CURRENT roster (roster_revision_2026-07); "
            "new members (SMH, XLF, COWZ, XLV, VTIP, KMLM, IEF, USMV) have bases starting "
            "~2026-07-10, so early-window basket history under-represents them — the same "
            "caveat as the /performance web chart."
        ),
    }


# ---------------------------------------------------------------------------
# Phase C §6 — track_record (compact learning aggregates the analyzer reads)
# ---------------------------------------------------------------------------

def _hit_rate(rows: list[dict], field: str) -> float | None:
    """Fraction of `field` (a call_correct_Nd bool) that is truthy; None if empty."""
    vals = [r.get(field) for r in rows if r.get(field) is not None]
    return round(sum(1 for v in vals if v) / len(vals), 2) if vals else None


def _hit_cell(rows: list[dict], field: str) -> dict:
    return {"n": len(rows), "hit_rate": _hit_rate(rows, field)}


def _aggregate_track_record(rows: list[dict], headline: int = _HEADLINE_HORIZON) -> dict:
    """Roll stamped TradeHistory rows into the compact track_record block.

    Pure over `rows` (dicts with `layer`, `confidence`, `primary_trigger`,
    `thesis_type`, `recommended_at`, and stamped `call_correct_Nd`). Reports
    hit-rate at the headline horizon (per-horizon `horizons` for 30/90d context),
    by layer, and — flex only — by coarse trigger/thesis with confidence
    calibration. Patterns + sample sizes only, never per-name logs. Phase C §6.
    """
    field = f"call_correct_{headline}d"

    block: dict = {"headline_horizon": f"{headline}d"}

    # Per-horizon overall hit-rate — gives launch-time signal (30d matures first).
    block["horizons"] = {
        f"{h}d": _hit_cell([r for r in rows if r.get(f"call_correct_{h}d") is not None],
                           f"call_correct_{h}d")
        for h in _OUTCOME_HORIZONS
    }

    # Over-trading uses every recommendation row (not just matured ones).
    rec_dates = {r.get("recommended_at") for r in rows if r.get("recommended_at")}
    block["over_trading"] = {
        "avg_trades_per_day": round(len(rows) / len(rec_dates), 2) if rec_dates else None
    }

    matured = [r for r in rows if r.get(field) is not None]
    block["sample_size"] = len(matured)
    if not matured:
        block["note"] = f"no matured {headline}d outcomes yet — scoreboard only"
        block["caveat"] = "no matured outcomes at the headline horizon; do not infer skill yet"
        return block

    # By layer (core + flex).
    by_layer = {}
    for layer in ("core", "flex"):
        subset = [r for r in matured if (r.get("layer") or "").lower() == layer]
        if subset:
            by_layer[layer] = _hit_cell(subset, field)
    if by_layer:
        block["by_layer"] = by_layer

    # Flex-only reasoning aggregates (the §7 enums live on flex trades).
    flex = [r for r in matured if (r.get("layer") or "").lower() == "flex"]

    # by_trigger: capture fine, report coarse; promote a fine bucket to its own
    # line only once it reaches _TRIGGER_PROMOTION_MIN samples (§8).
    fine_groups: dict[str, list[dict]] = {}
    for r in flex:
        pt = (r.get("primary_trigger") or "").strip()
        if pt:
            fine_groups.setdefault(pt, []).append(r)
    by_trigger: dict[str, dict] = {}
    coarse_acc: dict[str, list[dict]] = {}
    for fine, subset in fine_groups.items():
        if len(subset) >= _TRIGGER_PROMOTION_MIN:
            by_trigger[fine] = _hit_cell(subset, field)
        else:
            coarse_acc.setdefault(_COARSE_TRIGGER.get(fine, "other"), []).extend(subset)
    for parent, subset in coarse_acc.items():
        by_trigger[parent] = _hit_cell(subset, field)
    if by_trigger:
        block["by_trigger"] = by_trigger

    # by_thesis: coarse from the start (3 gatekeeper-gate values).
    thesis_groups: dict[str, list[dict]] = {}
    for r in flex:
        tt = (r.get("thesis_type") or "").strip()
        if tt:
            thesis_groups.setdefault(tt, []).append(r)
    if thesis_groups:
        block["by_thesis"] = {k: _hit_cell(v, field) for k, v in thesis_groups.items()}

    # Confidence calibration: 0.1-wide buckets, predicted (avg confidence) vs
    # actual (hit rate). The centerpiece — "when I said 0.8, was I right ~80%?"
    buckets: dict[float, list[dict]] = {}
    for r in matured:
        try:
            c = float(r.get("confidence"))
        except (TypeError, ValueError):
            continue
        lo = min(int(c * 10) / 10, 0.9)  # clamp 1.0 into the 0.9-1.0 bucket
        buckets.setdefault(round(lo, 1), []).append(r)
    calibration = []
    for lo in sorted(buckets):
        subset = buckets[lo]
        confs = [float(r["confidence"]) for r in subset]
        calibration.append({
            "bucket": f"{lo:.1f}-{lo + 0.1:.1f}",
            "n": len(subset),
            "predicted": round(sum(confs) / len(confs), 2),
            "actual": _hit_rate(subset, field),
        })
    if calibration:
        block["calibration"] = calibration

    block["caveat"] = (
        f"n={len(matured)} is anecdotal; treat as calibration signal, not per-name veto"
    )
    return block


def _build_track_record() -> dict:
    """Query all TradeHistory rows and aggregate them. Phase C §6."""
    return _aggregate_track_record(query_entities("TradeHistory"))


# ---------------------------------------------------------------------------
# Brief Phase 5 — override-outcome stamping (reference-path counterfactual)
# ---------------------------------------------------------------------------
# Overrides are falsifiable bet slips; until Phase 5 nothing ever collected on the
# bets (the outcome_status/resolved_correct hooks sat empty since Phase 4d).
# LOCKED DECISION (account holder, 2026-07-04): an override is graded against the
# REFERENCE PATH — "did disagreeing beat obeying" — NOT vs SPY. The counterfactual
# portfolio is the filed-date reference vector itself (reference_weights.
# target_weights_pct from that day's snapshot: per-ticker % of equity incl. the
# SGOV-denominated cash sleeve; the small literal-cash remainder is absent from the
# vector and thus implicitly earns 0.0, which is exactly right).

def _override_sign(sleeve: str, direction: str) -> float | None:
    """+1 when the override held MORE of the sleeve than reference, −1 when LESS.

    The row stores the deviation's RISK direction, not the weight direction, but
    the two determine each other through the block model: holding more of a
    defensive name (or less of an amplifier) than reference IS the de-risk
    deviation, and vice versa. None for an invalid direction."""
    d = (direction or "").lower()
    if d not in ("de_risk", "re_risk"):
        return None
    defensive = (sleeve or "").upper() in set(DAMPER)
    return 1.0 if defensive == (d == "de_risk") else -1.0


def _grade_override(row: dict, ref_vector: dict | None, px) -> dict:
    """Grade ONE matured override vs the reference-path counterfactual (pure).

    ``px(symbol, date) -> float | None`` returns the last close on/before `date`.
    Over [filed=recommended_at, matured=falsifier_date]:
        ret_sleeve    = price return of the override's sleeve
        ret_reference = Σ target_weights_pct[i]/100 × ret_i (filed-date vector)
        excess_pp     = sign × (ret_sleeve − ret_reference)
    where sign is +1 if the override held MORE of the sleeve than reference
    (hold/overweight) and −1 if LESS (refused buy / underweight). Any missing
    material input → ``indeterminate_data`` — never guess: a reference component
    weighing ≥1% that cannot be priced voids the grade (sub-1% floor sleeves are
    skipped as de minimis; ≥90% of the vector's weight must be priced overall).
    Free-text falsifier interpretation is EXPLICITLY out of scope — mechanical
    price grading only; judging falsifier quality is the #13 monthly review's job.
    """
    indeterminate = {"outcome_status": "indeterminate_data", "resolved_correct": None}
    filed = str(row.get("recommended_at") or "")[:10]
    matured = str(row.get("falsifier_date") or "")[:10]
    sleeve = str(row.get("sleeve") or "").upper()
    sign = _override_sign(sleeve, row.get("direction"))
    if not filed or not matured or not sleeve or sign is None or not ref_vector:
        return indeterminate

    p0, p1 = px(sleeve, filed), px(sleeve, matured)
    if not p0 or not p1:
        return indeterminate
    ret_sleeve = (p1 / p0 - 1) * 100.0

    total_w = priced_w = ret_ref = 0.0
    for sym, w in ref_vector.items():
        try:
            w = float(w)
        except (TypeError, ValueError):
            continue
        if w <= 0:
            continue
        total_w += w
        q0 = px(str(sym).upper(), filed)
        q1 = px(str(sym).upper(), matured)
        if not q0 or not q1:
            if w >= 1.0:
                return indeterminate   # material component unpriced — void, don't guess
            continue                   # de-minimis floor sleeve — skip
        priced_w += w
        ret_ref += w / 100.0 * (q1 / q0 - 1) * 100.0
    if total_w <= 0 or priced_w / total_w < 0.9:
        return indeterminate

    excess = sign * (ret_sleeve - ret_ref)
    return {
        "ret_sleeve_pct": round(ret_sleeve, 4),
        "ret_reference_pct": round(ret_ref, 4),
        "excess_pp": round(excess, 4),
        "resolved_correct": excess > 0,
        "outcome_status": "resolved_correct" if excess > 0 else "resolved_wrong",
    }


def _stamp_override_outcomes(fmp: FMPClient) -> None:
    """Brief Phase 5: stamp matured OverrideHistory rows (mirror of Phase C §5).

    Selects rows whose `falsifier_date` has passed and whose `outcome_status` is
    still empty. Synthetic enforcement rows without a falsifier_date are never
    selected (the property is absent, so the OData filter excludes them) — those
    bets are already graded via their `band_enforcement` trades in TradeHistory.
    Prices come from the `performance/equity-series.json` closes (last close on or
    before each boundary date — falsifier dates land on weekends); FMP fallback
    only for gaps, one call per unique missing symbol. The filed-date reference
    vector is reconstructed from `daily-snapshots/{filed}.json` (no schema change;
    works retroactively). Caller wraps in try/except — never breaks the collector.
    """
    today = date.today().isoformat()
    rows = query_entities("OverrideHistory", f"falsifier_date le '{today}'")
    pending = [r for r in rows if not r.get("outcome_status")]
    if not pending:
        logger.info("Override stamping: nothing matured to stamp")
        return

    # Price lookup: perf-series closes first (already on disk daily), FMP per
    # unique missing symbol as fallback.
    perf_points = sorted(
        ((p.get("date"), p.get("closes") or {}) for p in read_perf_series() if p.get("date")),
    )
    fmp_cache: dict[str, dict[str, float]] = {}

    def _px(sym: str, d: str) -> float | None:
        best = None
        for pd, closes in perf_points:
            if pd > d:
                break
            c = closes.get(sym)
            if c is not None:
                best = float(c)
        if best is not None:
            return best
        if sym not in fmp_cache:
            fmp_cache[sym] = _close_by_date(fmp, sym)
        return _close_on_or_before(fmp_cache[sym], d)

    # Filed-date reference vectors, one snapshot read per unique filed date.
    ref_cache: dict[str, dict | None] = {}

    def _ref_vector(filed: str) -> dict | None:
        if filed not in ref_cache:
            try:
                snap = read_snapshot(filed)
                ref_cache[filed] = (
                    (snap.get("reference_weights") or {}).get("target_weights_pct") or None
                )
            except Exception:  # noqa: BLE001
                ref_cache[filed] = None   # missing filed-date snapshot → indeterminate
        return ref_cache[filed]

    stamped = 0
    for r in pending:
        filed = str(r.get("recommended_at") or "")[:10]
        grade = _grade_override(r, _ref_vector(filed) if filed else None, _px)
        try:
            upsert_entity("OverrideHistory", {
                "PartitionKey": r["PartitionKey"], "RowKey": r["RowKey"],
                "resolved_at": today, **grade,
            })
            stamped += 1
        except Exception:  # noqa: BLE001
            logger.exception("Override stamping upsert failed for %s", r.get("RowKey"))
    logger.info("Override stamping: %d row(s) stamped (of %d pending)", stamped, len(pending))


def _stamp_switch_outcomes(fmp: FMPClient) -> None:
    """Grade matured role switches + intl leader rotations vs the INCUMBENT
    counterfactual (Task G / Phase C): correct if the new member outperformed the one
    it replaced. Stamps `excess_{30,60,90}d_pp` and, at the 60d headline, `resolved_correct`
    + `outcome_status`. Prices from perf-series closes first, FMP fallback per symbol.
    Caller wraps in try/except — never breaks the collector.
    """
    today = date.today()
    rows = query_entities("OverrideHistory")
    pending = [
        r for r in rows
        if r.get("layer") in ("sleeve_switch", "intl_leader_rotation")
        and not r.get("outcome_status")
    ]
    if not pending:
        return

    perf_points = sorted(
        ((p.get("date"), p.get("closes") or {}) for p in read_perf_series() if p.get("date")),
    )
    fmp_cache: dict[str, dict[str, float]] = {}

    def _px(sym: str, d: str) -> float | None:
        best = None
        for pd, closes in perf_points:
            if pd > d:
                break
            c = closes.get(sym)
            if c is not None:
                best = float(c)
        if best is not None:
            return best
        if sym not in fmp_cache:
            fmp_cache[sym] = _close_by_date(fmp, sym)
        return _close_on_or_before(fmp_cache[sym], d)

    stamped = 0
    for r in pending:
        filed = str(r.get("recommended_at") or "")[:10]
        if not filed or _max_matured_horizon(filed, today) < 30:
            continue
        inc, new = r.get("incumbent"), r.get("new_member")
        base_i, base_n = _px(inc, filed), _px(new, filed)
        entity = {"PartitionKey": r["PartitionKey"], "RowKey": r["RowKey"], "resolved_at": today.isoformat()}
        headline = None
        for h in _OUTCOME_HORIZONS:   # 30 / 60 / 90
            if date.fromisoformat(filed) + timedelta(days=h) > today:
                continue
            tgt = (date.fromisoformat(filed) + timedelta(days=h)).isoformat()
            ci, cn = _px(inc, tgt), _px(new, tgt)
            ri = (ci / base_i - 1.0) * 100.0 if (ci and base_i) else None
            rn = (cn / base_n - 1.0) * 100.0 if (cn and base_n) else None
            grade = _grade_switch(ri, rn)
            if grade:
                entity[f"excess_{h}d_pp"] = grade["excess_pp"]
                if h == _HEADLINE_HORIZON:
                    headline = grade
        if headline is not None:
            entity["outcome_status"] = "closed"
            entity["resolved_correct"] = headline["resolved_correct"]
        if len(entity) > 3:   # something to write beyond the keys
            try:
                upsert_entity("OverrideHistory", entity)
                stamped += 1
            except Exception:  # noqa: BLE001
                logger.exception("Switch stamping upsert failed for %s", r.get("RowKey"))
    logger.info("Switch stamping: %d row(s) stamped (of %d pending)", stamped, len(pending))


def _grade_thematic_horizon(
    entry_price: float | None, horizon_price: float | None,
    spy_entry: float | None, spy_horizon: float | None,
) -> dict | None:
    """D6 (2026-08-14 audit): pure grading for ONE matured thematic-conviction
    horizon. ``actual_up`` resolves the SAME contract `p_up` was defined
    against in the prompt (D7): the ticker's total return over the horizon is
    positive. `None` on any unpriced input — never a guessed outcome."""
    if entry_price is None or horizon_price is None or entry_price <= 0:
        return None
    ret = (horizon_price / entry_price - 1.0) * 100.0
    out = {"ret_pct": round(ret, 4), "actual_up": 1.0 if ret > 0 else 0.0}
    if spy_entry and spy_horizon and spy_entry > 0:
        spy_ret = (spy_horizon / spy_entry - 1.0) * 100.0
        out["excess_vs_spy_pp"] = round(ret - spy_ret, 4)
    return out


def _stamp_thematic_outcomes(fmp: FMPClient) -> None:
    """D6: grade matured `ThematicHistory` rows at each horizon (30/60/90d) —
    reuses the SAME perf-series-closes-first / FMP-fallback price resolution
    as `_stamp_override_outcomes`/`_stamp_switch_outcomes` (never a parallel
    pricing path). `actual_up_{h}d` / `excess_{h}d_pp` stamp independently as
    each horizon matures (mirrors `_stamp_switch_outcomes`'s per-horizon
    loop exactly); `outcome_status`/`resolved_correct` (the 60d headline) is
    set once that horizon resolves, after which the row is no longer
    reprocessed. Unpriced maturity -> `indeterminate_data`, never guessed.
    Caller wraps in try/except — never breaks the collector.
    """
    today = date.today()
    rows = query_entities("ThematicHistory")
    pending = [r for r in rows if not r.get("outcome_status")]
    if not pending:
        return

    perf_points = sorted(
        ((p.get("date"), p.get("closes") or {}) for p in read_perf_series() if p.get("date")),
    )
    fmp_cache: dict[str, dict[str, float]] = {}

    def _px(sym: str, d: str) -> float | None:
        best = None
        for pd, closes in perf_points:
            if pd > d:
                break
            c = closes.get(sym)
            if c is not None:
                best = float(c)
        if best is not None:
            return best
        if sym not in fmp_cache:
            fmp_cache[sym] = _close_by_date(fmp, sym)
        return _close_on_or_before(fmp_cache[sym], d)

    stamped = 0
    for r in pending:
        sym = str(r.get("symbol") or "").upper()
        filed = str(r.get("filed_date") or "")[:10]
        if not sym or not filed or _max_matured_horizon(filed, today) < 30:
            continue
        base = _px(sym, filed)
        base_spy = _px("SPY", filed)
        entity = {"PartitionKey": r["PartitionKey"], "RowKey": r["RowKey"],
                  "resolved_at": today.isoformat()}
        headline = None
        any_graded = False
        for h in _OUTCOME_HORIZONS:
            if date.fromisoformat(filed) + timedelta(days=h) > today:
                continue
            tgt = (date.fromisoformat(filed) + timedelta(days=h)).isoformat()
            grade = _grade_thematic_horizon(base, _px(sym, tgt), base_spy, _px("SPY", tgt))
            if grade:
                any_graded = True
                entity[f"actual_up_{h}d"] = grade["actual_up"]
                if "excess_vs_spy_pp" in grade:
                    entity[f"excess_{h}d_pp"] = grade["excess_vs_spy_pp"]
                if h == _HEADLINE_HORIZON:
                    headline = grade
        if headline is not None:
            entity["outcome_status"] = "resolved"
            entity["p_up"] = float(r.get("p_up") or 0.0)
        elif _max_matured_horizon(filed, today) >= _HEADLINE_HORIZON and not any_graded:
            entity["outcome_status"] = "indeterminate_data"
        if len(entity) > 3:
            try:
                upsert_entity("ThematicHistory", entity)
                stamped += 1
            except Exception:  # noqa: BLE001
                logger.exception("Thematic stamping upsert failed for %s", r.get("RowKey"))
    logger.info("Thematic stamping: %d row(s) stamped (of %d pending)", stamped, len(pending))


def _build_thematic_calibration(risk_limits: dict) -> dict:
    """D6: aggregate resolved `ThematicHistory` rows (the 60d-headline
    `p_up`/`actual_up_60d` pairs) into `{sample_size, brier_score, hit_rate,
    damping_factor}` for the `thematic_conviction.calibration` snapshot field.
    Reuses the pure `_thematic_brier`/`_thematic_damping_factor` — this
    function only queries + shapes the pairs."""
    tc_cfg = risk_limits.get("thematic_conviction") or _RISK_LIMITS_DEFAULTS["thematic_conviction"]
    rows = query_entities("ThematicHistory", "outcome_status eq 'resolved'")
    pairs: list[tuple[float, float]] = []
    for r in rows:
        p_up = r.get("p_up")
        actual = r.get(f"actual_up_{_HEADLINE_HORIZON}d")
        if p_up is None or actual is None:
            continue
        try:
            pairs.append((float(p_up), float(actual)))
        except (TypeError, ValueError):
            continue
    brier = _thematic_brier(pairs)
    damping = _thematic_damping_factor(
        brier["brier_score"], brier["sample_size"],
        tc_cfg.get("brier_damping") or [], int(tc_cfg.get("brier_min_sample", 10)),
    )
    return {**brier, "damping_factor": damping}


def _aggregate_override_record(rows: list[dict]) -> dict:
    """Brief Phase 5 §2 — roll stamped OverrideHistory rows into the compact
    `override_record` snapshot block (sibling of track_record: capture-fine /
    report-coarse, same n≥10 promotion rule as 7c). Pure over `rows`.

    Grades are vs the REFERENCE PATH ("did disagreeing beat obeying"), price-return
    only in v1. `enforced: true` rows aggregate SEPARATELY — they grade the
    ENFORCEMENT system, not the model's judgment; blending would poison both
    lessons. Splits: `by_direction` (the §6 asymmetry doctrine predicts de_risk and
    re_risk differ) and `by_status` (accepted/downsized vs rejected); `by_premise`
    reports a premise only once it clears the promotion threshold.
    """
    resolved = [r for r in rows
                if r.get("outcome_status") in ("resolved_correct", "resolved_wrong")]
    model = [r for r in resolved if not r.get("enforced")]
    enforced = [r for r in resolved if r.get("enforced")]

    def _cell(subset: list[dict]) -> dict:
        wins = sum(1 for r in subset if r.get("resolved_correct"))
        exc = [float(r["excess_pp"]) for r in subset
               if isinstance(r.get("excess_pp"), (int, float))]
        return {
            "n": len(subset),
            "win_rate": round(wins / len(subset), 2),
            "avg_excess_pp": round(sum(exc) / len(exc), 2) if exc else None,
        }

    block: dict = {
        "basis": "reference_path_counterfactual",
        "sample_size": len(model),
    }
    if not model and not enforced:
        block["note"] = "no matured override outcomes yet"
        block["caveat"] = "no matured overrides; do not infer judgment skill yet"
        return block

    if model:
        block["overall"] = _cell(model)
        by_direction = {}
        for d in ("de_risk", "re_risk"):
            sub = [r for r in model if (r.get("direction") or "").lower() == d]
            if sub:
                by_direction[d] = _cell(sub)
        if by_direction:
            block["by_direction"] = by_direction
        by_status = {}
        for s in ("accepted", "downsized", "rejected"):
            sub = [r for r in model if (r.get("outcome") or "") == s]
            if sub:
                by_status[s] = _cell(sub)
        if by_status:
            block["by_status"] = by_status
        prem_groups: dict[str, list[dict]] = {}
        for r in model:
            p = (r.get("premise_challenged") or "").strip()
            if p:
                prem_groups.setdefault(p, []).append(r)
        by_premise = {p: _cell(sub) for p, sub in prem_groups.items()
                      if len(sub) >= _TRIGGER_PROMOTION_MIN}
        if by_premise:
            block["by_premise"] = by_premise
    if enforced:
        # Grades the enforcement SYSTEM (Finding 2 D3), not the model's judgment.
        block["enforced_separately"] = _cell(enforced)

    block["caveat"] = (
        f"n={len(model)} price-return-only v1; a calibration signal for how boldly "
        "to deviate — never a per-sleeve veto, never a reason to stop filing"
    )
    return block


def _build_override_record() -> dict:
    """Query all OverrideHistory rows and aggregate them. Brief Phase 5 §2."""
    return _aggregate_override_record(query_entities("OverrideHistory"))


# Filed overrides older than this are never surfaced as "pending" — an
# un-graded row this old more likely reflects a Phase-5 stamping gap than a
# live adjudication the analyst still owes (2026-08-06 audit M1).
_PRIOR_OVERRIDE_LOOKBACK_DAYS = 60


def _build_prior_overrides_pending(
    today: str, growth_axis: dict, inflation_axis: dict, policy_axis: dict,
) -> list[dict]:
    """Still-live filed overrides the analyzer must explicitly adjudicate this
    session (2026-08-06 audit M1) — closes the seam where 08-04 filed a de-risk
    TLT hold with a dated falsifier, and 08-05 sold TLT to the floor without
    ever engaging with whether that falsifier had actually fired.

    A prior override is "pending" while: it was ACCEPTED or DOWNSIZED (a
    rejected record authorizes nothing, so there is nothing to adjudicate),
    Phase 5's stamper has not yet graded it (``outcome_status`` still empty —
    once graded it is a closed matter, not a live one), and it was filed within
    ``_PRIOR_OVERRIDE_LOOKBACK_DAYS``. Each entry echoes the falsifier verbatim,
    a best-effort deterministic ``falsifier_met`` (``shared/overrides.py::
    evaluate_falsifier`` — None when the free-text falsifier doesn't parse,
    never a fabricated verdict), and the current axis raw_direction/raw_streak
    the evaluation used, so the analyzer/human can adjudicate an unparseable
    falsifier from the same numbers this function looked at.

    Non-fatal by construction (caller wraps in try/except); a table-query
    failure surfaces as an empty list, never blocks the snapshot.
    """
    rows = query_entities("OverrideHistory", "layer eq 'override'")
    cutoff = (date.fromisoformat(today) - timedelta(days=_PRIOR_OVERRIDE_LOOKBACK_DAYS)).isoformat()

    out: list[dict] = []
    for r in rows:
        if r.get("outcome") not in ("accepted", "downsized"):
            continue
        if r.get("outcome_status"):
            continue   # already graded by Phase 5 — resolved, not pending
        filed = str(r.get("recommended_at") or "")[:10]
        if not filed or filed < cutoff or filed >= today:
            continue
        falsifier = r.get("falsifier") or ""
        met = evaluate_falsifier(falsifier, growth_axis, inflation_axis, policy_axis)
        out.append({
            "sleeve": r.get("sleeve"),
            "direction": r.get("direction"),
            "filed_date": filed,
            "falsifier": falsifier,
            "falsifier_date": r.get("falsifier_date"),
            "falsifier_met": met,
            "current_axis_state": {
                "growth": {
                    "raw_direction": (growth_axis or {}).get("raw_direction"),
                    "raw_streak": (growth_axis or {}).get("raw_streak"),
                },
                "inflation": {
                    "raw_direction": (inflation_axis or {}).get("raw_direction"),
                    "raw_streak": (inflation_axis or {}).get("raw_streak"),
                },
                "policy": {
                    "raw_direction": (policy_axis or {}).get("raw_stance"),
                    "raw_streak": (policy_axis or {}).get("raw_streak"),
                },
            },
        })
    out.sort(key=lambda x: x["filed_date"], reverse=True)
    return out


def run() -> None:
    today = date.today().isoformat()
    logger.info("=== Collector starting for %s ===", today)

    secrets = load_secrets()
    ensure_tables()

    # --- Portfolio (primary source: Alpaca paper account) -------------------
    # E*TRADE has been retired. Alpaca paper is the source of truth for
    # positions and balances. Falls back to config/portfolio.json only if
    # Alpaca is unreachable — in that case dollar gains will be unavailable.
    positions: list[dict] = []
    balances: dict = {}
    portfolio_source = "fallback"
    paper_account: dict = {"available": False}
    day_pl_zero_watch: dict = {"available": False}

    ak = secrets.get("AlpacaApiKey")
    asec = secrets.get("AlpacaApiSecret")
    if ak and asec:
        try:
            alp = AlpacaClient(api_key=ak, api_secret=asec)
            acct = alp.get_account()
            pos = alp.list_positions()

            # Canonical positions schema (compatible with previous E*TRADE shape).
            positions = [
                {
                    "ticker":        p.get("symbol"),
                    "quantity":      float(p.get("qty") or 0),
                    "market_value":  round(float(p.get("market_value") or 0), 4),
                    "cost_basis":    round(float(p.get("cost_basis") or 0), 4),
                    "day_gain":      round(float(p.get("unrealized_intraday_pl") or 0), 4),
                    "total_gain":    round(float(p.get("unrealized_pl") or 0), 4),
                    "avg_entry":     float(p.get("avg_entry_price") or 0),
                    "current_price": float(p.get("current_price") or 0),
                    "security_type": "EQ",
                }
                for p in pos
            ]
            equity     = float(acct.get("equity") or 0)
            last_eq    = float(acct.get("last_equity") or equity)
            cash       = float(acct.get("cash") or 0)
            net_mv     = sum(p["market_value"] for p in positions)
            total_cost = sum(p["cost_basis"]   for p in positions)
            total_gain = sum(p["total_gain"]   for p in positions)
            day_gain   = sum(p["day_gain"]     for p in positions)
            balances = {
                "totalAccountValue":           round(equity, 2),
                "netMv":                       round(net_mv, 2),
                "cashAvailableForInvestment":  round(cash, 2),
                "cashAvailableForWithdrawal":  round(cash, 2),
                "buyingPower":                 round(float(acct.get("buying_power") or 0), 2),
                "totalGainDollar":             round(total_gain, 2),
                "totalGainPct":                round((total_gain / total_cost * 100), 2) if total_cost else 0.0,
                "dayGainDollar":               round(day_gain, 2),
                "dayGainPct":                  round(((equity - last_eq) / last_eq * 100), 2) if last_eq else 0.0,
            }
            portfolio_source = "alpaca"

            # Keep `paper_account` block too so the analyzer's existing
            # reconciliation logic (which references paper_account.equity etc.)
            # keeps working.
            paper_account = {
                "available":     True,
                "cash":          cash,
                "buying_power":  float(acct.get("buying_power") or 0),
                "equity":        equity,
                "last_equity":   last_eq,
                "portfolio_value": float(acct.get("portfolio_value") or 0),
                "status":        acct.get("status"),
                "position_count": len(positions),
                "positions": [
                    {
                        "ticker":          p.get("symbol"),
                        "qty":             float(p.get("qty") or 0),
                        "avg_entry":       float(p.get("avg_entry_price") or 0),
                        "market_value":    float(p.get("market_value") or 0),
                        "unrealized_pl":   float(p.get("unrealized_pl") or 0),
                        "unrealized_plpc": float(p.get("unrealized_plpc") or 0),
                        "current_price":   float(p.get("current_price") or 0),
                        "side":            p.get("side"),
                    }
                    for p in pos
                ],
            }
            logger.info(
                "Alpaca portfolio: %d positions, equity=$%.2f, cash=$%.2f, total_gain=$%.2f",
                len(positions), equity, cash, total_gain,
            )

            # Task D (2026-07-28): diagnostics-only KMLM day-P/L zero-watch. Non-fatal.
            # Task C1 (2026-08-01): the walkback now also returns prior share counts
            # so resize-driven P/L moves aren't misflagged as anomalies (Task C2).
            try:
                _prior_pl_qty = _load_prior_position_total_pl(today)
                _prior_total_pl, _prior_qty = _prior_pl_qty if _prior_pl_qty else (None, None)
                day_pl_zero_watch = _build_day_pl_zero_watch(pos, _prior_total_pl, _prior_qty)
                if day_pl_zero_watch.get("flagged"):
                    logger.warning(
                        "Day-P/L zero-watch: %d position(s) flagged: %s",
                        len(day_pl_zero_watch["flagged"]),
                        [f["symbol"] for f in day_pl_zero_watch["flagged"]],
                    )
            except Exception:  # noqa: BLE001
                logger.exception("Day-P/L zero-watch build failed (non-fatal)")
        except Exception:  # noqa: BLE001
            logger.exception("Alpaca portfolio fetch failed — falling back to portfolio.json")
            positions = []
            balances = {}
    else:
        logger.warning("Alpaca creds missing — falling back to portfolio.json")

    if not positions:
        logger.warning("Loading config/portfolio.json fallback")
        with open(_PORTFOLIO_FALLBACK) as f:
            fb = json.load(f)
        positions = fb.get("positions", [])
        balances = fb.get("balances", {})

    tickers = [p["ticker"] for p in positions if p.get("ticker")]
    logger.info("Portfolio tickers (%d): %s", len(tickers), tickers)

    # Non-held flex candidates: static seed + analyzer-emitted dynamic watch_candidates
    # (FOLLOWUPS #8 v2). The dynamic list is the PREVIOUS run's watch_candidates array
    # (2-day latency: name → data next run → actionable run after). The analyzer's
    # gatekeeper G2 needs FMP profile + EOD price for each candidate.
    flex_candidate_tickers, _flex_provenance = _load_flex_candidates(
        exclude=set(tickers), today=today,
    )
    logger.info(
        "Flex candidates (%d: %d static, %d dynamic): %s",
        len(flex_candidate_tickers),
        sum(1 for v in _flex_provenance.values() if v == "static"),
        sum(1 for v in _flex_provenance.values() if v == "dynamic"),
        flex_candidate_tickers,
    )

    # --- FMP -----------------------------------------------------------------
    fmp = FMPClient(secrets["FmpApiKey"])
    profiles = fmp.get_profiles(tickers)
    flex_candidate_profiles = fmp.get_profiles(flex_candidate_tickers) if flex_candidate_tickers else []
    # Tag each profile with its source (A3 provenance — "static" or "dynamic")
    for p in flex_candidate_profiles:
        sym = (p.get("symbol") or "").upper()
        p["source"] = _flex_provenance.get(sym, "static")
    from_2w = (date.today() - timedelta(days=1)).isoformat()
    to_2w   = (date.today() + timedelta(days=14)).isoformat()
    from_30d = (date.today() - timedelta(days=30)).isoformat()

    # --- Sleeve selection scorecard (Task E; blanket auto-switch, session ---------
    # 2026-07-27) — describe-only role-member ranking that now ALSO auto-advances an
    # UNPINNED role's effective `selected` under hysteresis (see _build_sleeve_selection
    # for the full doctrine: pin / config-edit adoption / LEGACY_EXITS guard). Moved
    # EARLY — before the earnings/price universe and reference_weights — so a switch
    # that fires THIS run is priced and targeted THIS run ("ships hot", 2026-07-27
    # decision), rather than only taking effect the following day.
    #
    # `effective_selected`/`substitution` are derived from the PERSISTED table state
    # FIRST (a plain table read that can't fail the way an FMP-dependent metrics
    # fetch can), then overwritten with this run's fresh decision on success. A bad
    # FMP day therefore degrades to yesterday's already-committed state (no
    # config/effective whipsaw) instead of losing every role's override for the day —
    # consumers below never see `_new_streak` directly, only these two maps.
    _roles = roles_config()
    _prev_streak = _load_sleeve_streak_state()
    effective_selected: dict[str, str] = _effective_selected_map(_roles, _prev_streak)
    substitution: dict[str, str] = _substitution_map(_roles, effective_selected)
    sleeve_selection: dict = {"available": False}
    _new_streak: dict = {}
    _sleeve_closes_cache: dict[str, dict[str, float]] = {}
    try:
        _metrics = _sleeve_selection_metrics(fmp, _roles, cache=_sleeve_closes_cache)
        sleeve_selection, _new_streak = _build_sleeve_selection(
            _roles, _metrics, _prev_streak, selection_config()
        )
        _save_sleeve_streak_state(_new_streak)
        effective_selected = {
            rid: ns["selected"] for rid, ns in _new_streak.items() if ns.get("selected")
        }
        substitution = _substitution_map(_roles, effective_selected)
        _sig = [r["role_id"] for r in sleeve_selection.get("roles", []) if r.get("switch_signal")]
        _switched = [r["role_id"] for r in sleeve_selection.get("roles", []) if r.get("auto_switched")]
        logger.info(
            "Sleeve selection: %d roles scored, switch_signals=%s, auto_switched=%s, "
            "substitution=%s",
            len(sleeve_selection.get("roles", [])), _sig or "none", _switched or "none",
            substitution or "none",
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Sleeve selection build failed (non-fatal) — effective_selected/"
            "substitution fall back to the persisted-state read above (no whipsaw)"
        )

    # --- Quiver (primary congressional source) ------------------------------
    # Moved ahead of the earnings/news fetch below (Task D, 2026-08-10 catalyst-
    # sleeve funnel): `congressional` is a genuinely MARKET-WIDE feed (unlike
    # `lobbying`/`gov_contracts`, ticker-filtered to `_interest` further down) —
    # a zero-cost discovery source for names never held or nominated before.
    # The discovery-universe symbol list built below needs it ahead of the
    # (now-extended) stock_news fetch.
    quiver = QuiverClient(secrets.get("QuiverApiKey"))
    if quiver.ready:
        congressional = quiver.get_live_congress_trades()
        if from_30d:
            congressional = [
                r for r in congressional
                if (r.get("TransactionDate") or r.get("Date") or r.get("transactionDate") or "") >= from_30d
            ]
        lobbying      = quiver.get_live_lobbying()
        gov_contracts = quiver.get_live_gov_contracts()
        # Quiver returns ~20K rows of all-market activity. Filter to portfolio
        # tickers + watchlist (and last 90 days) so the snapshot stays small
        # enough to fit Claude's context window. Without this, snapshot
        # balloons to ~20MB (12MB lobbying + 4MB gov_contracts).
        _interest = set(tickers) | set(_ETF_WATCHLIST)
        _cutoff_90d = (date.today() - timedelta(days=90)).isoformat()

        def _row_ticker(r: dict) -> str:
            return (r.get("Ticker") or r.get("ticker") or "").upper()

        def _row_date(r: dict) -> str:
            return r.get("Date") or r.get("date") or r.get("action_date") or ""

        lobbying = [
            r for r in lobbying
            if _row_ticker(r) in _interest and _row_date(r) >= _cutoff_90d
        ]
        gov_contracts = [
            r for r in gov_contracts
            if _row_ticker(r) in _interest and _row_date(r) >= _cutoff_90d
        ]
    else:
        logger.warning("Quiver key missing — falling back to FMP senate/house latest")
        congressional = fmp.get_congressional_trading(from_30d)
        lobbying      = []
        gov_contracts = []
    logger.info("Quiver/FMP: %d congressional, %d lobbying, %d gov contracts",
                len(congressional), len(lobbying), len(gov_contracts))

    earnings_raw       = fmp.get_earnings_calendar(from_2w, to_2w)
    # B2 (deferred finding 4): filter the market-wide calendar to the book's universe
    # so held names' dates surface and irrelevant names don't. No extra FMP calls.
    # selected_core_members(effective_selected) includes a freshly auto-switched
    # incumbent (e.g. IHE) so its earnings date is never dropped mid-rotation.
    _earn_universe = (set(tickers) | set(selected_core_members(effective_selected))
                      | set(flex_candidate_tickers) | (set(tickers) & set(LEGACY_EXITS)))
    _earn_pre = len(earnings_raw)
    earnings           = _filter_earnings_to_universe(earnings_raw, _earn_universe)
    logger.info("Earnings calendar filtered to book universe: %d → %d rows",
                _earn_pre, len(earnings))
    # Task A (G1 fix): the market-wide rows are no longer simply discarded — the
    # ADDITIONAL subset (not already in the book's universe) is screened + capped
    # and emitted separately. API cost: zero — `earnings_raw` is already fetched
    # above; these rows were being thrown away, not re-fetched.
    _earn_market_rows, _earn_market_dropped = _screen_earnings_market_rows(
        earnings_raw, _earn_universe, _EARNINGS_MARKET_CAP,
    )
    earnings_calendar_market: dict = {
        "rows": _earn_market_rows,
        "cap": _EARNINGS_MARKET_CAP,
        "dropped_by_cap": _earn_market_dropped,
    }
    logger.info("Earnings calendar market-wide (Task A): %d kept (cap=%d), %d dropped by cap",
                len(_earn_market_rows), _EARNINGS_MARKET_CAP, _earn_market_dropped)

    # --- Catalyst discovery universe (Task D early phase, G3 fix) ------------
    # Genuinely NEW names — never held, never in the static/dynamic flex-
    # candidate lists — sourced from the two market-wide feeds above at zero
    # incremental API cost (earnings_calendar_market + congressional). Computed
    # here, before the (now-extended) stock_news fetch, so news gets fetched for
    # the FULL discovery set rather than a pre-ranked slice: the eventual
    # ranking depends on news_recency/news_tone as inputs, so cutting the
    # symbol list by a preliminary rank before fetching news would starve
    # exactly the component that should help decide the cut.
    _catalyst_exclude = (
        set(tickers) | set(flex_candidate_tickers)
        | flex_separation_set(set(tickers))
        | (set(LEGACY_EXITS) - FLEX_REENTERABLE)
    )
    _catalyst_discovery = catalyst_screen.discovery_symbols(
        [r.get("symbol") for r in _earn_market_rows],
        [(r.get("Ticker") or r.get("ticker") or "") for r in congressional],
        _catalyst_exclude,
        _CATALYST_DISCOVERY_CAP,
    )
    logger.info("Catalyst discovery universe (%d, cap=%d): %s",
                len(_catalyst_discovery), _CATALYST_DISCOVERY_CAP, _catalyst_discovery)

    # Task B (G2 fix): news for candidates, not just holdings. A single call
    # regardless of symbol-list size (verified against the client — see
    # scripts/probe_fmp_tier.py and tests/test_catalyst_news.py), so extending
    # the list to held + flex candidates + the full discovery set costs nothing
    # extra in call count; `limit` is bumped since far more symbols now compete
    # for the same article pool (decision gate 3, PR body).
    _news_symbols = list(dict.fromkeys(tickers + flex_candidate_tickers + _catalyst_discovery))
    stock_news         = fmp.get_stock_news(_news_symbols, limit=_STOCK_NEWS_LIMIT)
    etf_holdings: dict = {etf: fmp.get_etf_holdings(etf) for etf in _ETF_WATCHLIST}
    etf_country: dict  = {etf: fmp.get_etf_country_weights(etf) for etf in _ETF_WATCHLIST}
    etf_sector: dict   = {etf: fmp.get_etf_sector_weights(etf) for etf in _ETF_WATCHLIST}

    logger.info("FMP: %d profiles, %d earnings, %d news (%d symbols)",
                len(profiles), len(earnings), len(stock_news), len(_news_symbols))

    # --- FRED ----------------------------------------------------------------
    with open(_MACRO_SERIES_FILE) as f:
        macro_meta: dict = json.load(f)

    fred = FREDClient(secrets["FredApiKey"])
    macro_data = fred.get_all_series(list(macro_meta.keys()))
    # Series that need deeper history for the rotation + bond-signals pre-compute
    # (get_all_series only fetches the latest 5 observations per series).
    macro_data["DTWEXBGS"] = fred.get_series_latest("DTWEXBGS", limit=_MACRO_DEEP_FETCH_DAYS)
    macro_data["DGS2"]     = fred.get_series_latest("DGS2",     limit=_MACRO_DEEP_FETCH_DAYS)
    macro_data["DFF"]      = fred.get_series_latest("DFF",      limit=_MACRO_DEEP_FETCH_DAYS)
    # Bond-signals pre-compute needs ~90d for percentiles + 4w deltas.
    for _bond_sid in (
        "DGS10", "DGS30", "DGS3MO", "T10Y2Y", "T10Y3M",
        "BAMLH0A0HYM2", "BAMLC0A0CM",
        "DFII10", "T10YIE", "T5YIE", "T5YIFR",
        "MORTGAGE30US",
    ):
        macro_data[_bond_sid] = fred.get_series_latest(_bond_sid, limit=90)
    # Labor-signals pre-compute: weekly series need ~26 obs for 4w avg + YoY-ish
    # trend; monthly series need ~24 obs for 3m / 6m / 12m comparisons.
    for _labor_sid in ("ICSA", "CCSA"):
        macro_data[_labor_sid] = fred.get_series_latest(_labor_sid, limit=60)
    for _labor_sid in ("PAYEMS", "UNRATE", "CES0500000003", "JTSJOL",
                       "CIVPART", "SAHMREALTIME"):
        macro_data[_labor_sid] = fred.get_series_latest(_labor_sid, limit=24)
    # O3 (2026-08-06 audit): ADP private payrolls — a LEADING labor signal
    # (available days before BLS PAYEMS). Never overwrites the PAYEMS
    # scorecard; see _build_labor_leading.
    macro_data["NPPTTL"] = fred.get_series_latest("NPPTTL", limit=24)
    # Inflation pre-compute (quadrant inflation axis): monthly series need >=13 obs
    # so the analyzer can compute YoY and the 3-month annualized direction (the
    # realized-CPI/PCE read that governs the regime label over forward breakevens).
    for _infl_sid in ("CPIAUCSL", "CPILFESL", "PCEPI", "PCEPILFE", "PPIACO", "RSAFS"):
        macro_data[_infl_sid] = fred.get_series_latest(_infl_sid, limit=18)
    # Growth axis: the standard observations endpoint returns ONE latest value per
    # quarter, so limit=N yields N *quarters*, not the within-quarter nowcast
    # revisions. Keep the quarterly series for cross-quarter context, and pull the
    # current-quarter ALFRED vintages so the analyzer reads the real intra-quarter
    # slope (e.g. 3.70 -> 4.26 -> 2.54) — the deceleration the quarterly view hides.
    macro_data["GDPNOW"] = fred.get_series_latest("GDPNOW", limit=8)
    _t = date.today()
    _q_month = 3 * ((_t.month - 1) // 3) + 1
    _q_start = date(_t.year, _q_month, 1).isoformat()
    _prev_q_start = (
        date(_t.year - 1, 10, 1) if _q_month == 1 else date(_t.year, _q_month - 3, 1)
    ).isoformat()
    # Window starts at the PRIOR quarter: at every quarter turn the new quarter has
    # 0-2 vintages for weeks (the Atlanta Fed keeps nowcasting the just-ended quarter
    # until the BEA advance release), which left GDPNOW_VINTAGES empty and degraded
    # the growth axis exactly at the boundary (FOLLOWUPS #15, observed 2026-07-01..03).
    # The prior quarter's trajectory rides along so _build_growth_axis can read its tail.
    _gdpnow_vint = fred.get_series_vintages(
        "GDPNOW", realtime_start=_prev_q_start, realtime_end=_t.isoformat()
    )
    macro_data["GDPNOW_VINTAGES"] = _gdpnow_vintage_rows(_gdpnow_vint, _q_start)
    macro_data["GDPNOW_VINTAGES_PRIOR"] = _gdpnow_vintage_rows(_gdpnow_vint, _prev_q_start)
    # Energy axis: oil spot for the stagflation/Hormuz-shock read (~90d for baseline).
    for _oil_sid in ("DCOILWTICO", "DCOILBRENTEU"):
        macro_data[_oil_sid] = fred.get_series_latest(_oil_sid, limit=90)
    # Leading-growth composite (#17): weekly series need ~26 obs for trend; monthly
    # regional-Fed surveys and building permits need ~12 obs for 3m comparisons.
    # All degrade gracefully (stale/absent → dropped, confidence reduced).
    for _lg_weekly in ("WEI", "NFCI"):
        macro_data[_lg_weekly] = fred.get_series_latest(_lg_weekly, limit=60)
    for _lg_monthly in ("PERMIT", "NEWORDER", "NOCDFSA066MSFRBPHI", "GACDISA066MSFRBNY"):
        macro_data[_lg_monthly] = fred.get_series_latest(_lg_monthly, limit=18)
    logger.info("FRED: %d series collected", sum(1 for v in macro_data.values() if v))

    # --- EOD prices (FMP batch-quote, single call) --------------------------
    all_tickers = _build_price_universe(tickers, flex_candidate_tickers, effective_selected)
    prices = fmp.get_eod_prices(all_tickers)
    logger.info("FMP prices: %d/%d collected (universe: held+selected-core+watchlist+flex)",
                len(prices), len(all_tickers))

    # Task E (F7): load prior snapshot prices for the quarantine delta check.
    _prior_prices: dict = {}
    try:
        d0 = date.fromisoformat(today)
        for _back in range(1, 8):
            _prior_snap = read_json_blob("daily-snapshots",
                                         f"{(d0 - timedelta(days=_back)).isoformat()}.json")
            if isinstance(_prior_snap, dict) and _prior_snap.get("prices"):
                _prior_prices = _prior_snap["prices"]
                break
    except Exception:  # noqa: BLE001
        logger.debug("Price quarantine: could not load prior snapshot prices (non-fatal)")

    # --- Regional rotation pre-compute --------------------------------------
    regional_rotation = _build_regional_rotation(fmp, macro_data)
    logger.info(
        "Regional rotation: %d tickers scored, DXY 60d=%s",
        len(regional_rotation.get("tickers", {})),
        regional_rotation.get("dxy_60d_pct_change"),
    )

    # --- Finnhub -------------------------------------------------------------
    finnhub = FinnhubClient(secrets["FinnhubApiKey"])
    market_news = finnhub.get_market_news("general")
    forex_news  = finnhub.get_market_news("forex")

    week_ago = (date.today() - timedelta(days=7)).isoformat()
    company_news: dict = {}
    # Cap at 10 tickers to stay within 60 calls/min free tier
    for ticker in tickers[:10]:
        company_news[ticker] = finnhub.get_company_news(ticker, week_ago, today)

    logger.info("Finnhub: %d market news, %d company news items",
                len(market_news), sum(len(v) for v in company_news.values()))

    # --- Task E (F7): price-sanity quarantine for flex candidates -----------
    # Apply after company_news is available (needed for news-corroboration gate).
    _quarantine_cfg = _load_risk_limits()
    _quarantined_count = 0
    for _fcp in flex_candidate_profiles:
        _quar, _quar_reason = _quarantine_flex_price(
            _fcp, prices, _prior_prices, company_news, _quarantine_cfg)
        if _quar:
            _fcp["price_quarantined"] = True
            _fcp["quarantine_reason"] = _quar_reason
            _quarantined_count += 1
            logger.warning(
                "Flex candidate %s QUARANTINED: %s",
                _fcp.get("symbol"), _quar_reason,
            )
    if _quarantined_count:
        logger.info("Price quarantine: %d/%d flex candidates quarantined",
                    _quarantined_count, len(flex_candidate_profiles))

    # --- Market shock detector (short-horizon moves + news keyword scan) ----
    bond_signals = _build_bond_signals(macro_data)
    logger.info(
        "Bond signals: composite=%s label=%s hy_oas=%s recession_prob=%s",
        bond_signals.get("scorecard", {}).get("composite"),
        bond_signals.get("scorecard", {}).get("label"),
        bond_signals.get("credit", {}).get("hy_oas", {}).get("latest"),
        bond_signals.get("yield_curve", {}).get("recession_prob_12m"),
    )

    labor_signals = _build_labor_signals(macro_data)
    logger.info(
        "Labor signals: composite=%s label=%s claims_4w=%s sahm=%s payrolls_3m_avg=%s",
        labor_signals.get("scorecard", {}).get("composite"),
        labor_signals.get("scorecard", {}).get("label"),
        labor_signals.get("claims", {}).get("icsa_4w_avg"),
        labor_signals.get("unemployment", {}).get("sahm_latest"),
        labor_signals.get("payrolls", {}).get("delta_3m_avg_k"),
    )

    # News-hits trailing baseline (2026-08-06 audit B3) — persisted so the
    # z-score has a real history to compare against across runs.
    _news_hist_raw = read_json_blob("market-shock", "news-hits-history.json")
    _news_hist: list[dict] = _news_hist_raw if isinstance(_news_hist_raw, list) else []
    market_shock = _build_market_shock(
        fmp=fmp,
        macro_data=macro_data,
        market_news=market_news,
        forex_news=forex_news,
        stock_news=stock_news,
        company_news=company_news,
        bond_signals=bond_signals,
        news_hits_history=_news_hist,
    )
    logger.info(
        "Market shock: level=%s (price=%s news=%s), spy_1d_z=%s, news_hits=%s news_z=%s",
        market_shock.get("shock_level"), market_shock.get("price_level"),
        market_shock.get("news_level"),
        market_shock.get("spy", {}).get("return_1d_zscore"),
        market_shock.get("news_hits_total"), market_shock.get("news_hits_zscore"),
    )
    try:
        _dominant_cat = (
            max(market_shock["news_hits_by_category"], key=lambda c: market_shock["news_hits_by_category"][c])
            if market_shock.get("news_hits_total", 0) > 0 else None
        )
        _news_hist_window = int(
            (_load_risk_limits().get("market_shock") or {}).get("news_baseline_window_sessions", 20))
        _news_hist_updated = [h for h in _news_hist if h.get("date") != today]
        _news_hist_updated.append({
            "date": today,
            "total_hits": market_shock.get("news_hits_total", 0),
            "hits_by_category": market_shock.get("news_hits_by_category", {}),
            "dominant_category": _dominant_cat,
        })
        _news_hist_updated = _news_hist_updated[-_news_hist_window:]
        write_json_blob("market-shock", "news-hits-history.json", _news_hist_updated)
    except Exception:  # noqa: BLE001
        logger.exception("Could not persist news-hits history (non-fatal)")

    # --- Quadrant axes (deterministic; analyzer ECHOES these, see prompt) ----
    # Growth + inflation direction are the two axes that decide the quadrant. They
    # were previously left to the LLM on raw macro.data — the discretion point where
    # it rationalized its prior label. Now pre-computed like bond/labor signals.
    growth_axis_raw = _build_growth_axis(macro_data)
    # O2 (2026-08-06 audit): FRED's DCOILWTICO/DCOILBRENTEU run 8-9d stale every
    # session — on the exact channel that can flip the inflation axis to
    # "rising". USO (a liquid, daily-traded oil ETF) is fetched as a fresher
    # trend proxy; _build_inflation_axis falls back to FRED when it's
    # unavailable/thin. One extra FMP call, well within the 250/day budget.
    try:
        _oil_proxy_cache = _close_by_date(fmp, "USO")
    except Exception:  # noqa: BLE001
        logger.debug("Inflation axis: USO oil-proxy fetch failed (non-fatal)")
        _oil_proxy_cache = {}
    inflation_axis_raw = _build_inflation_axis(macro_data, _oil_proxy_cache)

    # Session 2026-07-28 (Task A, decision D-2): N=2 confirmation on the CONSUMED
    # `direction` field — a label change (any value, including flat) only reaches
    # every downstream consumer (active_quadrant, reference_weights, regime_gate,
    # market_vs_macro, ...) after persisting 2 consecutive runs. Non-fatal: a table
    # read failure degrades to `{}` (D-A2's "first run" path — adopt raw
    # immediately), never blocks the snapshot.
    try:
        _axis_state = _load_axis_direction_state()
    except Exception:  # noqa: BLE001
        logger.exception("Axis direction state load failed (non-fatal, D-A2 applies)")
        _axis_state = {}

    _growth_confirm = _confirm_axis_direction(
        growth_axis_raw["direction"], _axis_state.get("growth"), today
    )
    _inflation_confirm = _confirm_axis_direction(
        inflation_axis_raw["direction"], _axis_state.get("inflation"), today
    )
    growth_axis = {**growth_axis_raw, **_growth_confirm}
    inflation_axis = {**inflation_axis_raw, **_inflation_confirm}

    # Rolloff diagnostic (growth/GDPNow only) — only meaningful when the RAW
    # classification actually flipped vs the last persisted raw value (never on
    # the very first run, where there is no known prior raw to compare against).
    _prior_growth_raw = (_axis_state.get("growth") or {}).get("raw_direction")
    if _axis_state.get("growth") and _growth_confirm["raw_direction"] != _prior_growth_raw:
        try:
            _prior_growth_axis = _load_prior_growth_axis(today)
        except Exception:  # noqa: BLE001
            logger.exception("Prior growth axis walkback failed (non-fatal)")
            _prior_growth_axis = None
        growth_axis["direction_change_diagnostics"] = _growth_rolloff_diagnostics(
            growth_axis_raw.get("gdpnow_trajectory"),
            (_prior_growth_axis or {}).get("gdpnow_trajectory"),
            _growth_confirm["raw_direction"],
            _prior_growth_raw,
        )

    fomc_stance = _load_fomc_stance()
    # Policy axis (#16): resolves manual SEP layer vs market-implied DGS2 momentum;
    # the gate + conviction proxy consume the RESOLVED stance. fomc_stance stays in
    # the snapshot as the raw manual echo (backward compatible). Session 2026-07-28
    # Task A: the market-implied half also gets N=2 confirmation (prev_state below).
    policy_axis = _build_policy_axis(
        macro_data, fomc_stance, _load_risk_limits(), today, _axis_state.get("policy")
    )
    regime_gate = _build_regime_gate(growth_axis, inflation_axis, policy_axis)
    logger.info(
        "Quadrant axes: growth=%s(%s, raw=%s pending=%s) inflation=%s(raw=%s pending=%s) "
        "gate=%s policy=%s(%s, raw=%s pending=%s)",
        growth_axis.get("direction"), growth_axis.get("confidence"),
        growth_axis.get("raw_direction"), growth_axis.get("direction_pending"),
        inflation_axis.get("direction"), inflation_axis.get("raw_direction"),
        inflation_axis.get("direction_pending"), regime_gate.get("status"),
        policy_axis.get("stance"), policy_axis.get("source"),
        policy_axis.get("raw_stance"), policy_axis.get("stance_pending"),
    )

    # Persist confirmation state for next run (non-fatal — a write failure never
    # blocks the snapshot; it just means tomorrow's read falls back to D-A2 too).
    try:
        _save_axis_direction_state({
            "growth": {
                "raw_direction": _growth_confirm["raw_direction"],
                "confirmed_direction": _growth_confirm["direction"],
                "raw_streak": _growth_confirm["raw_streak"],
                "confirmed_as_of": _growth_confirm["confirmed_as_of"],
            },
            "inflation": {
                "raw_direction": _inflation_confirm["raw_direction"],
                "confirmed_direction": _inflation_confirm["direction"],
                "raw_streak": _inflation_confirm["raw_streak"],
                "confirmed_as_of": _inflation_confirm["confirmed_as_of"],
            },
            "policy": {
                # Persist the market-implied confirmation's OWN state — NEVER the
                # blended `stance` (which may be a fresh manual value bypassing
                # confirmation entirely) — so a later stale-manual day resumes
                # from the real hysteresis position.
                "raw_direction": policy_axis.get("raw_stance"),
                "confirmed_direction": policy_axis.get("confirmed_market_implied_stance"),
                "raw_streak": policy_axis.get("raw_streak"),
                "confirmed_as_of": policy_axis.get("confirmed_market_implied_as_of") or today,
            },
        })
    except Exception:  # noqa: BLE001
        logger.exception("Axis direction state save failed (non-fatal)")

    # Build order (dependency chain): divergences → transition_watch → reference_weights.
    # divergences (Phase 2) only needs the BINDING active_quadrant, which is exactly
    # active_quadrant(g, i) and which transition_watch deliberately does NOT move — so we
    # pass that directly and avoid a build cycle (reference_weights consumes transition_watch
    # consumes divergences). All three non-fatal: a build failure must never block the snapshot.
    _binding_quad = {"active_quadrant": active_quadrant(
        growth_axis.get("direction"), inflation_axis.get("direction")) or None}

    # --- Task A (#17): Leading-growth composite (FOLLOWUPS #17) ---------------
    # Diffusion score from FRED weekly/monthly leading indicators + market-derived
    # signals (copper/gold, XLY/XLP, HY OAS direction). Describe-only. Feeds the
    # new leading_vs_lagging_growth divergence and generalises transition_watch.
    # Fetch historical closes for market-derived ratio signals (XLY, CPER, GLD, XLP)
    # — 4 extra FMP calls; within the 250/day budget (see PR body).
    # Hoisted above the try (2026-08-06 audit B1/B2) so it's always defined even
    # if leading_growth's build fails before populating it — market_implied_quadrant
    # below reuses the SAME cache for its copper/gold and XLY/XLP votes.
    _lg_close_cache: dict[str, dict[str, float]] = {}
    leading_growth: dict = {"available": False}
    try:
        for _lg_sym in ("XLY", "CPER", "GLD", "XLP"):
            try:
                _lg_close_cache[_lg_sym] = _close_by_date(fmp, _lg_sym)
            except Exception:  # noqa: BLE001
                logger.debug("Leading growth: could not fetch %s history (non-fatal)", _lg_sym)
        leading_growth = _build_leading_growth(macro_data, prices, bond_signals, _lg_close_cache)
        logger.info(
            "Leading growth: direction=%s score=%s confidence=%s available=%d/%d",
            leading_growth.get("direction"), leading_growth.get("score"),
            leading_growth.get("confidence"),
            leading_growth.get("available_signals", 0), leading_growth.get("total_signals", 0),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Leading growth build failed (non-fatal)")

    # --- Task B (#18): market_implied_quadrant --------------------------------
    # Built BEFORE divergences — loads the perf series from blob directly so it
    # doesn't depend on the in-memory `series` (which is built post-reference_weights).
    # Prices injected for the copper/gold and XLY/XLP votes; close_cache reuses the
    # leading_growth fetch above (2026-08-06 audit B1/B2 — zero extra FMP calls).
    market_implied_quadrant: dict = {"available": False}
    try:
        market_implied_quadrant = _build_market_implied_quadrant(
            [], macro_data, bond_signals, regional_rotation, today, prices=prices,
            close_cache=_lg_close_cache,
        )
        logger.info(
            "Market implied quadrant: implied=%s confidence=%s growth=%s inflation=%s",
            market_implied_quadrant.get("implied_quadrant"),
            market_implied_quadrant.get("confidence"),
            market_implied_quadrant.get("implied_growth"),
            market_implied_quadrant.get("implied_inflation"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Market implied quadrant build failed (non-fatal)")

    # --- Task B (#18) sub-item: daily dollar proxy (DTWEXBGS stale/unavailable) --
    # 2026-08-06 audit B4: fires at >=_DXY_STALE_FALLBACK_DAYS (was `> 5`, dark
    # at exactly 5d stale) OR when dxy_stale is None — DTWEXBGS returning ZERO
    # usable observations (dxy_latest_date never set) previously left the DXY
    # signal blind on BOTH the primary and fallback paths simultaneously.
    dxy_date = (regional_rotation or {}).get("dxy_latest_date")
    dxy_stale = _days_stale(dxy_date, today)
    dollar_proxy: dict = {"available": False}
    try:
        if _should_use_dollar_proxy(dxy_stale):
            dollar_proxy = _daily_dollar_proxy(macro_data, today)
            logger.info(
                "Dollar proxy (DTWEXBGS stale=%s): available=%s direction=%s",
                dxy_stale, dollar_proxy.get("available"), dollar_proxy.get("proxy_direction"),
            )
    except Exception:  # noqa: BLE001
        logger.exception("Dollar proxy build failed (non-fatal)")

    # --- Divergences (Phase 2: DETECT tensions, don't resolve) ---------------
    # Descriptive precompute pointing the analyzer's judgment at high-value zones; the LLM
    # adjudicates them (Phase 4). The SPY 200-day SMA (#3's filter) is fetched here and
    # reduced by the pure _sma_from_rows so _build_divergences stays no-network/testable.
    divergences: list[dict] = []
    try:
        try:
            spy_sma = _sma_from_rows(fmp.get_historical_price_light("SPY"), _SPY_SMA_WINDOW)
        except Exception:  # noqa: BLE001
            logger.warning("Divergences: SPY history fetch failed; price-vs-regime indeterminate")
            spy_sma = {"available": False}
        divergences = _build_divergences(
            paper_account, growth_axis, inflation_axis, bond_signals, regional_rotation,
            _binding_quad, market_shock, spy_sma, today, _load_divergence_config(),
            leading_growth=leading_growth,
            market_implied_quadrant=market_implied_quadrant,
        )
        _active = [d["id"] for d in divergences if d.get("status") == "active"]
        logger.info("Divergences: %d total, active=%s", len(divergences), _active)
    except Exception:  # noqa: BLE001
        logger.exception("Divergences build failed (non-fatal)")

    # --- Transition watch (Phase 3: bounded pre-staging on leading inflation) ---
    # Reuses the Phase-2 leading_vs_lagging_inflation divergence; emits a partial lean for
    # reference_weights toward the projected quadrant WITHOUT moving the binding quad/gate/axis.
    # Task C (2026-08-14 audit): the raw per-session evaluation is now wrapped with
    # confirm/release hysteresis + a fraction ramp (`_confirm_transition_watch`) —
    # response to the 2026-08-12 VDE whipsaw (a single session's stateless activation
    # blew a reference weight ~45x and reversed it the very next day).
    transition_watch: dict = {"active": False, "status": "indeterminate"}
    try:
        _tw_cfg = (_load_risk_limits().get("transition_watch") or _RISK_LIMITS_DEFAULTS["transition_watch"])
        _tw_raw = _build_transition_watch(
            divergences, growth_axis, inflation_axis, _load_risk_limits(),
        )
        _tw_prev = _load_transition_watch_state()
        transition_watch = _confirm_transition_watch(_tw_raw, _tw_prev, _tw_cfg)
        _tw_new_state = transition_watch.pop("_state", None)
        if _tw_new_state is not None:
            _save_transition_watch_state(_tw_new_state)
        logger.info(
            "Transition watch: active=%s projected=%s direction=%s target=%s applied=%s "
            "status=%s confirm_streak=%s release_streak=%s",
            transition_watch.get("active"), transition_watch.get("projected_quadrant"),
            transition_watch.get("direction"), transition_watch.get("target_fraction"),
            transition_watch.get("staged_fraction"), transition_watch.get("status"),
            transition_watch.get("confirm_streak"), transition_watch.get("release_streak"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Transition watch build failed (non-fatal)")

    # --- International governance (Task F) — rotation/DXY-governed intl sleeve. ---
    # Built BEFORE reference_weights (which consumes it for the two intl roles). Non-fatal.
    intl_governance: dict = {"available": False}
    _intl_prev: dict = {}
    try:
        _intl_leader_pool: list = []
        _intl_broad_sel = ""
        for _r in roles_config():
            if _r.get("role_id") == "intl_leader":
                _intl_leader_pool = _r.get("pool", [])
            elif _r.get("role_id") == "intl_broad":
                _intl_broad_sel = _r.get("selected", "")
        _intl_prev = _load_intl_state()
        intl_governance, _intl_new = _build_intl_governance(
            regional_rotation, regime_gate, market_shock,
            _intl_leader_pool, _intl_broad_sel, _intl_prev, intl_config(),
        )
        _save_intl_state(_intl_new)
        logger.info(
            "Intl governance: status=%s composite=%s leader_pick=%s sleeve=%.1fpp mods=%s",
            intl_governance.get("status"), intl_governance.get("rotation_composite"),
            intl_governance.get("leader_pick"), intl_governance.get("sleeve_target_pp") or 0.0,
            intl_governance.get("modifiers"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Intl governance build failed (non-fatal)")

    # --- Thematic conviction overlay (2026-08-14 audit, D-4/D-5) --------------
    # Built BEFORE reference_weights (which applies the confirmed active[] entries
    # as a floor lift). One-session lag: reads the PRIOR day's LLM-emitted
    # nominations, applies eligibility/caps/hysteresis. Non-fatal.
    thematic_conviction: dict = {"available": False}
    try:
        _tc_prev_noms = _load_prior_thematic_nominations(today)
        _tc_prev_states = _load_thematic_state()
        _tc_quarantined = {
            str(p.get("symbol") or "").upper()
            for p in flex_candidate_profiles if p.get("price_quarantined")
        }
        _tc_calibration = _build_thematic_calibration(_load_risk_limits())
        thematic_conviction, _tc_new_states = _build_thematic_conviction(
            _load_risk_limits(), _tc_prev_noms, _tc_prev_states,
            _tc_quarantined, effective_selected, _tc_calibration,
        )
        for _tc_sym, _tc_state in _tc_new_states.items():
            _save_thematic_state(_tc_sym, _tc_state)
        logger.info(
            "Thematic conviction: enabled=%s active=%d pending=%d excluded=%d calibration=%s",
            thematic_conviction.get("enabled"), len(thematic_conviction.get("active", [])),
            len(thematic_conviction.get("pending", [])), len(thematic_conviction.get("excluded", [])),
            _tc_calibration,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Thematic conviction build failed (non-fatal)")

    # --- Reference weights (strategy-spec §10: precomputed target weights the ----
    # analyzer executes toward, NOT a mandate). Consumes transition_watch (Phase 3) as a
    # bounded lean. Deterministic + echoed; non-fatal.
    reference_weights: dict = {"available": False}
    try:
        reference_weights = _build_reference_weights(
            paper_account, growth_axis, inflation_axis, regime_gate,
            regional_rotation, bond_signals, labor_signals, market_shock,
            _load_risk_limits(), transition_watch, intl_governance,
            effective_selected, thematic_conviction,
        )
        logger.info(
            "Reference weights: quad=%s conviction=%s(%s) active_target=%s%%core tilt=%s lean=%s binding=%s",
            reference_weights.get("active_quadrant"),
            reference_weights.get("conviction_proxy"),
            reference_weights.get("conviction_label"),
            reference_weights.get("active_quadrant_target_pct_of_core"),
            reference_weights.get("dollar_tilt"),
            (reference_weights.get("transition_lean") or {}).get("applied"),
            reference_weights.get("binding"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Reference weights build failed (non-fatal)")

    # --- Session 2026-07-17, Task D: quadrant_allocation (Table A "Current" column) --
    # Non-fatal. Deterministic CURRENT-side counterpart to reference_weights.by_quadrant
    # — kills the freehand quadrant-sum arithmetic that produced two contradictory
    # Table A's in the 07-17 report (Q1 0.77% vs a corrected 1.46%; Q2 5.37% vs 3.72%)
    # and a leaked "wait — let me recompute carefully" mid-table.
    quadrant_allocation: dict = {"available": False}
    try:
        quadrant_allocation = _build_quadrant_allocation(
            paper_account.get("positions") or [],
            float(paper_account.get("equity") or 0),
            float(paper_account.get("cash") or 0),
            effective_selected,
        )
        logger.info(
            "Quadrant allocation: buckets=%s total=%s%%",
            quadrant_allocation.get("buckets"), quadrant_allocation.get("total_pct"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Quadrant allocation build failed (non-fatal)")

    # --- B3 (deferred finding 7): functional_coverage (Table B — secondary roles ---
    # counted; NOT additive to 100%). Echoed verbatim so the model stops mis-summing it.
    functional_coverage: dict = {"available": False}
    try:
        functional_coverage = _build_functional_coverage(
            paper_account.get("positions") or [],
            float(paper_account.get("equity") or 0),
        )
        logger.info(
            "Functional coverage: Q totals=%s sgov=%s committed_q4=%s",
            {q: v.get("total_pct") for q, v in (functional_coverage.get("quadrants") or {}).items()},
            (functional_coverage.get("sgov_note_inputs") or {}).get("sgov_pct"),
            (functional_coverage.get("sgov_note_inputs") or {}).get("committed_q4_pct"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Functional coverage build failed (non-fatal)")

    # --- Session 2026-07-17, Task C: role_selection (static vs runtime doctrine) --
    # Non-fatal, independent of the sleeve_selection try above (a fresh roles_config()
    # read so this never depends on that block's success). `sleeve_selection` only
    # covers "scorecard" roles — the intl_leader role (selection: "rotation") never
    # appears there, so the model has nothing to check before conflating a runtime
    # leader_pick=null with an actual deselection (2026-07-17 AIA incident).
    role_selection: dict = {"roles": []}
    try:
        role_selection = _build_role_selection(
            roles_config(), (intl_governance or {}).get("leader_pick"), effective_selected
        )
    except Exception:  # noqa: BLE001
        logger.exception("Role selection build failed (non-fatal)")

    # --- Flex quadrant (D1, 2026-07-21): borderline 5-day benchmark tiebreak so an
    # indeterminate active_quadrant never freezes the flex sleeve. Reuses the closes
    # cache the sleeve scorecard just populated (QQQ/XLI/GLD/TLT are pool members) —
    # zero extra FMP calls. Non-fatal: on failure the engine falls back to strict axes.
    flex_quadrant: dict = {}
    try:
        flex_quadrant = _build_flex_quadrant(growth_axis, inflation_axis, _sleeve_closes_cache)
        logger.info(
            "Flex quadrant: resolved=%s basis=%s bucket=%s",
            flex_quadrant.get("resolved"), flex_quadrant.get("basis"),
            flex_quadrant.get("favored_bucket"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Flex quadrant build failed (non-fatal)")

    # --- Catalyst screen: score the discovery universe, rank, nominate ------
    # Task D (G3 fix). Placed after flex_quadrant (needs its resolved quadrant +
    # basis for the regime_fit_score component). Non-fatal — a failure here
    # must never block the snapshot; on failure the funnel falls back to the
    # pre-existing static+dynamic flex_candidates only (unchanged behavior).
    catalyst_screen_block: dict = {"available": False}
    try:
        _cs_quadrant = flex_quadrant.get("resolved") or ""
        _cs_basis = flex_quadrant.get("basis") or ""
        _flex_cfg = load_flex_config()

        # 2 FMP calls per discovery candidate (profile + historical price) — the
        # only recurring cost this funnel adds; see the PR body for the daily
        # delta. Historical rows come back DESCENDING (newest first) — reversed
        # to ascending and reshaped to the {"c","v"} pair the shared indicator
        # helpers expect (see catalyst_screen.py's data-availability note: no
        # high/low on this endpoint, so no literal ATR here).
        _cs_profiles: dict[str, dict] = {}
        _cs_bars: dict[str, list[dict]] = {}
        for _sym in _catalyst_discovery:
            _prof = fmp.get_profile(_sym)
            if _prof:
                _cs_profiles[_sym] = _prof
            _rows = fmp.get_historical_price_light(_sym)
            if _rows:
                _cs_bars[_sym] = [
                    {"c": r.get("price") if r.get("price") is not None else r.get("close"),
                     "v": r.get("volume")}
                    for r in reversed(_rows)
                ]
        logger.info(
            "Catalyst discovery fetch: %d/%d profiles, %d/%d price histories "
            "(2 FMP calls/candidate)",
            len(_cs_profiles), len(_catalyst_discovery),
            len(_cs_bars), len(_catalyst_discovery),
        )

        catalyst_screen_block = _build_catalyst_screen(
            _catalyst_discovery, _cs_profiles, _cs_bars, _earn_market_rows,
            stock_news, congressional, _cs_quadrant, _cs_basis,
            set(tickers), _catalyst_exclude, set(LEGACY_EXITS) - FLEX_REENTERABLE,
            _flex_cfg.min_adv_usd, today, _CATALYST_TOP_N,
        )
        _cs_nominated = catalyst_screen_block["nominated"]

        # Feed the nominees into flex_candidates (Task D final step) — same
        # mechanism as the pre-existing static/dynamic merge, third provenance
        # value "screened", so each name's origin stays auditable. Nominees are
        # ALSO run through the existing price-quarantine guard (F7) for
        # consistency with every other flex candidate, and their already-fetched
        # latest close is merged into `prices` directly — `_build_price_universe`
        # already ran earlier in this function, so a nominee would otherwise have
        # no price entry at all this run.
        for _sym in _cs_nominated:
            _nom_profile = dict(_cs_profiles.get(_sym) or {"symbol": _sym})
            _nom_profile["source"] = "screened"
            _nom_quar, _nom_quar_reason = _quarantine_flex_price(
                _nom_profile, prices, _prior_prices, company_news, _quarantine_cfg)
            if _nom_quar:
                _nom_profile["price_quarantined"] = True
                _nom_profile["quarantine_reason"] = _nom_quar_reason
            flex_candidate_profiles.append(_nom_profile)
            flex_candidate_tickers.append(_sym)
            _flex_provenance[_sym] = "screened"
            _nom_bars = _cs_bars.get(_sym) or []
            if _sym not in prices and _nom_bars:
                _latest = _nom_bars[-1]  # ascending order — last is most recent
                prices[_sym] = {"c": _latest.get("c"), "v": _latest.get("v")}
        logger.info(
            "Catalyst screen: %d/%d nominated into flex_candidates: %s",
            len(_cs_nominated), len(_catalyst_discovery), _cs_nominated or "none",
        )
    except Exception:  # noqa: BLE001
        logger.exception("Catalyst screen build failed (non-fatal)")

    # --- Phase C: record APPLIED role switches + intl leader rotations to ----------
    # OverrideHistory (Task G) — graded later vs the incumbent counterfactual. Non-fatal.
    try:
        _sw_records = _build_sleeve_switch_records(
            _prev_streak, _new_streak,
            (_intl_prev or {}).get("leader"), (intl_governance or {}).get("leader_pick"),
            today,
        )
        for _rec in _sw_records:
            upsert_entity("OverrideHistory", _rec)
        if _sw_records:
            logger.info(
                "Recorded %d sleeve switch/rotation record(s): %s",
                len(_sw_records), [r["RowKey"] for r in _sw_records],
            )
    except Exception:  # noqa: BLE001
        logger.exception("Sleeve switch recording failed (non-fatal)")

    # --- Flex engine state (intraday catalyst engine; echoed by the analyzer) -
    # The engine writes flex-state/{date}.json during the trading session. At
    # collector time (09:00 ET) today's run hasn't happened yet, so echo the most
    # recent prior state (up to a week back). Non-fatal. The conviction-sleeve
    # flex_review was retired when Flex became a separate intraday engine.
    flex_state: dict = {"available": False}
    try:
        d0 = date.fromisoformat(today)
        for back in range(0, 8):
            blob = read_json_blob("flex-state", f"{(d0 - timedelta(days=back)).isoformat()}.json")
            if isinstance(blob, dict):
                flex_state = {"available": True, **blob}
                break
        # Deterministic guard (MU incident): flag broker-held flex positions the
        # engine has forgotten (paper account is canonical). Runs even when the
        # engine state is unavailable — an orphan is exactly the case to catch.
        flex_state["reconciliation"] = _build_flex_reconciliation(flex_state, paper_account)
        if flex_state["reconciliation"]["status"] == "mismatch":
            logger.error(
                "Flex reconciliation MISMATCH: engine_held=%s broker_held=%s — "
                "paper account is canonical; analyzer must run kill-criteria against "
                "the broker position and block new entries in the affected symbol",
                flex_state["reconciliation"]["engine_held"],
                flex_state["reconciliation"]["broker_held"],
            )
        logger.info(
            "Flex state: available=%s as_of=%s held=%s reconciliation=%s",
            flex_state.get("available"), flex_state.get("as_of"), flex_state.get("held"),
            flex_state["reconciliation"]["status"],
        )
    except Exception:  # noqa: BLE001
        logger.exception("Flex state load failed (non-fatal)")

    # --- Phase C §4: performance scoreboard (account equity vs SPY) ---------
    # Non-fatal: a scoreboard failure must never block the daily snapshot.
    performance: dict = {"available": False}
    try:
        today_equity = paper_account.get("equity") if paper_account.get("available") else None
        today_cash = paper_account.get("cash") if paper_account.get("available") else None
        today_spy = (prices.get("SPY") or {}).get("c")
        series = _load_equity_spy_series(
            today, today_equity, today_spy, today_cash,
            prices=prices, growth_axis=growth_axis, inflation_axis=inflation_axis,
        )
        performance = _build_performance(series)
        # Publish the quadrant basket membership for the web chart (the SWA API
        # can't import shared/quadrants.py — this blob keeps it in lock-step).
        write_perf_quadrant_config({
            "quadrants": {q: list(names) for q, names in QUADRANT_CONCENTRATE.items()},
            "benchmark_etf": dict(QUADRANT_BENCHMARK_ETF),
            "as_of": today,
        })
        logger.info(
            "Performance: days_live=%s ret=%s%% spy=%s%% excess=%spp cash=%s%%",
            performance.get("days_live"),
            performance.get("return_since_inception_pct"),
            performance.get("spy_return_since_inception_pct"),
            performance.get("excess_vs_spy_pp"),
            (performance.get("account") or {}).get("cash_pct"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Performance scoreboard build failed (non-fatal)")

    # --- FOLLOWUPS #12: quadrant_performance (regime-call accountability) -----
    # Non-fatal. Reuses the SAME `series` the performance scoreboard just built —
    # do not re-download the perf-series cache.
    quadrant_performance: dict = {"available": False}
    try:
        qp_cfg = _load_risk_limits().get("quadrant_performance") \
            or _RISK_LIMITS_DEFAULTS["quadrant_performance"]
        quadrant_performance = _build_quadrant_performance(series, QUADRANT_CONCENTRATE, qp_cfg)
        suspects = [q for q, b in (quadrant_performance.get("buckets") or {}).items() if b.get("suspect")]
        if suspects:
            logger.warning(
                "Quadrant performance: SUSPECT favored bucket(s) %s (lagging_sessions >= %s)",
                suspects, qp_cfg.get("suspect_after_sessions"),
            )
        else:
            logger.info(
                "Quadrant performance: favored_today=%s spy_ret_30d=%s%%",
                quadrant_performance.get("favored_today"),
                quadrant_performance.get("spy_ret_30d_pct"),
            )
    except Exception:  # noqa: BLE001
        logger.exception("Quadrant performance build failed (non-fatal)")

    # --- Phase C §6: track_record (learning signal from stamped outcomes) ----
    # Non-fatal. Reads TradeHistory (stamped by _stamp_trade_outcomes on prior
    # runs); compact aggregates only — never raw trade logs in the snapshot.
    track_record: dict = {}
    try:
        track_record = _build_track_record()
        logger.info(
            "Track record: sample_size=%s avg_trades/day=%s",
            track_record.get("sample_size"),
            (track_record.get("over_trading") or {}).get("avg_trades_per_day"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Track record build failed (non-fatal)")

    # --- Task C: pnl_decomposition (inception-shortfall analysis) ------------
    # Non-fatal. FIFO realized + current unrealized P&L split by bucket. Answers
    # the "where does the vs-SPY wedge sit" question without re-deriving it freehand.
    pnl_decomposition: dict = {"available": False}
    _inception_date = "2026-05-26"  # account inception date (CLAUDE.md)
    try:
        if ak and asec and paper_account.get("available"):
            _alp_pnl = AlpacaClient(api_key=ak, api_secret=asec)
            pnl_decomposition = _build_pnl_decomposition(
                _alp_pnl, paper_account, _inception_date)
            logger.info(
                "P&L decomposition: core_current=%s legacy_exits=%s off_roster_flex=%s fills=%s",
                (pnl_decomposition.get("core_current") or {}).get("total_usd"),
                (pnl_decomposition.get("legacy_exits") or {}).get("total_usd"),
                (pnl_decomposition.get("off_roster_flex") or {}).get("total_usd"),
                pnl_decomposition.get("fill_count"),
            )
    except Exception:  # noqa: BLE001
        logger.exception("P&L decomposition build failed (non-fatal)")

    # --- Brief Phase 5: override_record (judgment loop, sibling of track_record) --
    # Non-fatal. Reads OverrideHistory (stamped by _stamp_override_outcomes on
    # prior runs); compact aggregates only.
    override_record: dict = {}
    try:
        override_record = _build_override_record()
        logger.info(
            "Override record: sample_size=%s overall=%s",
            override_record.get("sample_size"), override_record.get("overall"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Override record build failed (non-fatal)")

    # --- 2026-08-06 audit M1: prior_overrides_pending (falsifier adjudication) ---
    prior_overrides_pending: list[dict] = []
    try:
        prior_overrides_pending = _build_prior_overrides_pending(
            today, growth_axis, inflation_axis, policy_axis,
        )
        if prior_overrides_pending:
            logger.info(
                "Prior overrides pending: %d (%s)",
                len(prior_overrides_pending),
                [(p["sleeve"], p["falsifier_met"]) for p in prior_overrides_pending],
            )
    except Exception:  # noqa: BLE001
        logger.exception("Prior overrides pending build failed (non-fatal)")

    # --- Session 2026-07-15, Task A1: execution_review (fill/failure visibility) --
    # Non-fatal. Alpaca-only, FMP budget untouched. See _build_execution_review's
    # docstring — this is the fix for the MU incident's invisibility, not the MU
    # position itself (that is the account holder's call, outside this session).
    execution_review: dict = {"available": False}
    try:
        execution_review = _build_execution_review(secrets, today)
        if execution_review.get("failed") or execution_review.get("unfilled"):
            logger.warning(
                "Execution review for %s: %d failed, %d unfilled (of %d submitted)",
                execution_review.get("date"), len(execution_review.get("failed") or []),
                len(execution_review.get("unfilled") or []),
                execution_review.get("submitted"),
            )
        else:
            logger.info(
                "Execution review: available=%s date=%s filled=%s",
                execution_review.get("available"), execution_review.get("date"),
                execution_review.get("filled"),
            )
    except Exception:  # noqa: BLE001
        logger.exception("Execution review build failed (non-fatal)")

    # --- Session 2026-07-17, Task E: series_deltas (prior-vs-current macro compare) --
    # Non-fatal. Hardens F1 catalyst adjudication — the model's own memory of a
    # prior report's cited value is unreliable (07-17 named the wrong prior report
    # and the wrong prior value for the same CPI print); this makes the comparison
    # data, read back from the prior day's snapshot, not recollection.
    series_deltas: dict = {"available": False}
    try:
        series_deltas = _build_series_deltas(macro_data, today)
        if series_deltas.get("available"):
            new_prints = [sid for sid, s in series_deltas.get("series", {}).items()
                          if s.get("new_print")]
            logger.info(
                "Series deltas: prior_date=%s new_prints=%s",
                series_deltas.get("prior_date"), new_prints or "none",
            )
    except Exception:  # noqa: BLE001
        logger.exception("Series deltas build failed (non-fatal)")

    # --- B4 (2026-07-21): freshness (deterministic Data-Freshness table) ----------
    # Non-fatal. The model echoes this verbatim; it must never re-derive a date or
    # staleness (the GDPNow as-of flip-flop). GDPNow dated by vintage recency.
    freshness: dict = {"available": False}
    try:
        freshness = _build_freshness(macro_data, growth_axis, today)
        _stale = [sid for sid, s in (freshness.get("series") or {}).items() if s.get("stale")]
        logger.info("Freshness: %d series tracked, stale=%s",
                    len(freshness.get("series") or {}), _stale or "none")
    except Exception:  # noqa: BLE001
        logger.exception("Freshness build failed (non-fatal)")

    # --- Session 2026-07-17, Task B: execution_config (config-guessing kill) -----
    # Non-fatal, pure echo of `shared.reference_execution.effective_execution_config`
    # — the SAME resolution `reconcile`/`validate_trades` apply, so the prompt can
    # quote real tranche/band/floor/min-notional/evidence-bar numbers instead of
    # guessing them (four consecutive sessions guessed wrong; the 07-17 band guess
    # alone filed three unnecessary in-band overrides). See #33(i).
    execution_config: dict = {}
    try:
        execution_config = effective_execution_config(_load_risk_limits())
    except Exception:  # noqa: BLE001
        logger.exception("Execution config build failed (non-fatal)")

    # --- Assemble snapshot ---------------------------------------------------
    snapshot = {
        "date": today,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "portfolio": {
            "positions": positions,
            "balances": balances,
            "source": portfolio_source,
        },
        "paper_account": paper_account,
        "fundamentals": profiles,
        "flex_candidates": flex_candidate_profiles,
        "earnings_calendar": earnings,
        "earnings_calendar_market": earnings_calendar_market,
        "catalyst_screen": catalyst_screen_block,
        "stock_news": stock_news,
        "congressional_trades": congressional,
        "lobbying": lobbying,
        "gov_contracts": gov_contracts,
        "etf_holdings": etf_holdings,
        "etf_country_weights": etf_country,
        "etf_sector_weights": etf_sector,
        "macro": {
            "series_meta": macro_meta,
            "data": macro_data,
        },
        "prices": prices,
        "regional_rotation": regional_rotation,
        "bond_signals": bond_signals,
        "labor_signals": labor_signals,
        "market_shock": market_shock,
        "growth_axis": growth_axis,
        "inflation_axis": inflation_axis,
        "fomc_stance": fomc_stance,
        "policy_axis": policy_axis,
        "regime_gate": regime_gate,
        "reference_weights": reference_weights,
        "quadrant_allocation": quadrant_allocation,
        "functional_coverage": functional_coverage,
        "intl_governance": intl_governance,
        "sleeve_selection": sleeve_selection,
        "role_selection": role_selection,
        "transition_watch": transition_watch,
        "thematic_conviction": thematic_conviction,
        "divergences": divergences,
        "flex_quadrant": flex_quadrant,
        "flex_state": flex_state,
        "performance": performance,
        "quadrant_performance": quadrant_performance,
        "track_record": track_record,
        "override_record": override_record,
        "prior_overrides_pending": prior_overrides_pending,
        "execution_review": execution_review,
        "execution_config": execution_config,
        "series_deltas": series_deltas,
        "freshness": freshness,
        "leading_growth": leading_growth,
        "market_implied_quadrant": market_implied_quadrant,
        "dollar_proxy": dollar_proxy,
        "pnl_decomposition": pnl_decomposition,
        "day_pl_zero_watch": day_pl_zero_watch,
        # Session 2026-07-28 (Task E hardening): the FINAL value of `effective_selected`
        # at snapshot-assembly time — persisted-state-derived, overwritten by the fresh
        # scorecard on success, so it is populated even on a scorecard-build-failure day.
        # The analyzer prefers this top-level key over scanning `sleeve_selection.roles[]`
        # (which is exactly the block that's UNAVAILABLE on such a day).
        "effective_selected": effective_selected,
        "news": {
            "market": market_news[:50],
            "forex": forex_news[:20],
            "company": company_news,
        },
    }

    # --- Persist -------------------------------------------------------------
    write_snapshot(today, snapshot)
    _write_portfolio_history(today, positions, prices)
    _write_fundamentals_history(today, profiles)
    _write_macro_history(today, macro_data, macro_meta)
    _write_etf_history(today, etf_holdings, prices)
    _write_sentiment_history(today, snapshot)

    # --- Phase C §5: stamp matured trade outcomes (read-only; non-fatal) ------
    try:
        _stamp_trade_outcomes(fmp)
    except Exception:  # noqa: BLE001
        logger.exception("Outcome stamping failed (non-fatal)")

    # --- Brief Phase 5: stamp matured override outcomes (non-fatal) -----------
    try:
        _stamp_override_outcomes(fmp)
    except Exception:  # noqa: BLE001
        logger.exception("Override stamping failed (non-fatal)")

    # --- Task G: grade matured role switches + intl leader rotations (non-fatal) -
    try:
        _stamp_switch_outcomes(fmp)
    except Exception:  # noqa: BLE001
        logger.exception("Switch stamping failed (non-fatal)")

    # --- D6 (2026-08-14 audit): grade matured thematic-conviction nominations ---
    try:
        _stamp_thematic_outcomes(fmp)
    except Exception:  # noqa: BLE001
        logger.exception("Thematic stamping failed (non-fatal)")

    logger.info("=== Collector completed for %s ===", today)


# ---------------------------------------------------------------------------
# Table writers
# ---------------------------------------------------------------------------

def _write_portfolio_history(today: str, positions: list[dict], prices: dict) -> None:
    total_value = sum(p.get("market_value", 0) for p in positions) or 1.0
    for pos in positions:
        ticker = pos.get("ticker")
        if not ticker:
            continue
        price_data = prices.get(ticker, {})
        upsert_entity("PortfolioHistory", {
            "PartitionKey": ticker,
            "RowKey": today,
            "quantity":     pos.get("quantity", 0),
            "market_value": pos.get("market_value", 0),
            "cost_basis":   pos.get("cost_basis", 0),
            "weight":       round(pos.get("market_value", 0) / total_value, 4),
            "day_gain":     pos.get("day_gain", 0),
            "total_gain":   pos.get("total_gain", 0),
            "close_price":  price_data.get("c", 0),
            "volume":       price_data.get("v", 0),
            "security_type": pos.get("security_type", "EQ"),
        })


def _write_fundamentals_history(today: str, profiles: list[dict]) -> None:
    for p in profiles:
        ticker = p.get("symbol")
        if not ticker:
            continue
        upsert_entity("FundamentalsHistory", {
            "PartitionKey":    ticker,
            "RowKey":          today,
            "price":           p.get("price"),
            "market_cap":      p.get("mktCap"),
            "pe_ratio":        p.get("pe"),
            "beta":            p.get("beta"),
            "dcf_value":       p.get("dcf"),
            "analyst_rating":  p.get("rating"),
            "div_yield":       p.get("lastAnnualDividendYield"),
            "sector":          p.get("sector", ""),
            "industry":        p.get("industry", ""),
        })


def _write_macro_history(today: str, macro_data: dict, meta: dict) -> None:
    for series_id, observations in macro_data.items():
        if not observations:
            continue
        latest = observations[0]
        prev   = observations[1] if len(observations) > 1 else None
        try:
            val = float(latest.get("value", "nan"))
        except (ValueError, TypeError):
            val = None
        try:
            delta = (val - float(prev["value"])) if (prev and val is not None
                                                     and prev.get("value") not in (".", "")) else None
        except (ValueError, TypeError):
            delta = None
        upsert_entity("MacroHistory", {
            "PartitionKey": series_id,
            "RowKey":       today,
            "obs_date":     latest.get("date", ""),
            "value":        val,
            "delta":        delta,
            "series_name":  meta.get(series_id, {}).get("name", series_id),
            "unit":         meta.get(series_id, {}).get("unit", ""),
        })


def _write_etf_history(today: str, etf_holdings: dict, prices: dict) -> None:
    for etf, holdings in etf_holdings.items():
        price_data = prices.get(etf, {})
        upsert_entity("ETFLookthroughHistory", {
            "PartitionKey":    etf,
            "RowKey":          today,
            "holdings_count":  len(holdings),
            "top_10":          json.dumps(holdings[:10], default=str),
            "close_price":     price_data.get("c", 0),
            "volume":          price_data.get("v", 0),
        })


def _rotation_composite_category(weighted: float) -> tuple[float, str]:
    """Round the weighted rotation score to 1dp, then bucket the ROUNDED value.

    The displayed ``composite`` and the ``category`` are derived from the same
    rounded number so they can never disagree — the 2026-07-09 seam where an
    unrounded 3.049 displayed as 3.0 but bucketed "transition_window". Rubric:
    composite <= 3 us_leadership_intact; 4-6 transition_window; 7-10 rotation_underway.
    """
    composite = round(weighted, 1)
    if composite <= 3:
        category = "us_leadership_intact"
    elif composite <= 6:
        category = "transition_window"
    else:
        category = "rotation_underway"
    return composite, category


def _flex_pos_qty(pos: dict) -> float:
    """Share count from a paper_account position row (Alpaca-native `qty`, or the
    canonical `quantity`; see the 2026-07-07 held_qty incident)."""
    raw = pos.get("qty") if pos.get("qty") is not None else pos.get("quantity")
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def _build_flex_reconciliation(flex_state: dict, paper_account: dict) -> dict:
    """Deterministic guard (MU incident): compare the flex engine's ledger-derived
    ``held`` against the broker's OFF-CORE-ROSTER positions.

    The paper account is CANONICAL. A broker-held flex name the engine has forgotten
    (an orphan — the 2026-07-09 MU case: engine ``held=[]``, ``exits=[]``, yet the
    paper account still holds MU) is a ``mismatch`` the analyzer must act on (count
    the broker position, run kill-criteria against it, block new entries in that
    symbol). The reverse (engine holds a name the broker doesn't) is equally a
    mismatch. ``ok`` only when the two off-roster sets agree.

    Root-cause note: the ledger is durably written only at end-of-tick, and
    ``reconcile_ledger`` only REMOVES ledger rows to match the broker — it never
    re-adopts a broker position missing from the ledger, and ``read_ledger`` returns
    ``{}`` on any miss. So a lost/never-persisted ledger row makes an open flex
    position invisible with no exit logged. This guard surfaces exactly that.
    """
    engine_held = sorted({str(s).upper() for s in (flex_state.get("held") or []) if s})
    broker: set[str] = set()
    for p in (paper_account.get("positions") or []):
        sym = str(p.get("ticker") or p.get("symbol") or "").upper()
        if sym and sym not in CORE_ROSTER and _flex_pos_qty(p) > 1e-6:
            broker.add(sym)
    broker_held = sorted(broker)
    status = "ok" if engine_held == broker_held else "mismatch"
    return {"status": status, "engine_held": engine_held, "broker_held": broker_held}


def _build_execution_review(secrets: dict, today: str) -> dict:
    """Read the prior trading day's `daily-executions/{date}.json` and reconcile
    each submitted order's ACTUAL terminal state against Alpaca (session 2026-07-15,
    Task A1 — a response to the 2026-07-14/15 MU incident).

    By the 09:00 ET run every order from the prior session is terminal (filled,
    rejected, canceled, or expired) — but nothing previously read this blob back or
    checked in with Alpaca after submission: `_place_one` records SUBMISSION, not
    fills, and the analyzer/report were both blind to a submitted-but-errored (MU's
    403, two days running) or submitted-but-unfilled order. It silently re-proposed
    the same trade the next day with no visible trace of the failure. This block
    closes that loop: the analyzer's prompt (Task A1 companion edit) must surface
    `failed`/`unfilled` entries in the Data Integrity Warning and must not assume
    yesterday's proposals executed.

    Alpaca-only (the FMP budget is untouched). Non-fatal: any failure here (missing
    creds, no prior file within a week, an Alpaca outage) returns
    `{"available": False, "reason": ...}` — never raises, never loses the snapshot.
    """
    try:
        ak = secrets.get("AlpacaApiKey")
        asec = secrets.get("AlpacaApiSecret")
        if not ak or not asec:
            return {"available": False, "reason": "Alpaca credentials missing"}
        client = AlpacaClient(api_key=ak, api_secret=asec)

        d0 = date.fromisoformat(today)
        prev_doc = None
        prev_date = None
        for back in range(1, 8):
            d = (d0 - timedelta(days=back)).isoformat()
            doc = read_executions(d)
            if doc:
                prev_doc, prev_date = doc, d
                break
        if not prev_doc:
            return {
                "available": False,
                "reason": "no prior daily-executions found in the last 7 days",
            }

        executions = prev_doc.get("executions") or []
        failed: list[dict] = []
        unfilled: list[dict] = []
        filled_count = 0

        for e in executions:
            oid = e.get("alpaca_order_id")
            if not oid:
                # Never even reached Alpaca (e.g. the MU 403) — no order to look
                # up, but still a failure the analyzer must not assume executed.
                if e.get("status") == "error":
                    failed.append({
                        "symbol": e.get("symbol"), "side": e.get("side"),
                        "qty": e.get("qty"), "status": "error",
                        "error": e.get("error") or "submission failed",
                    })
                continue
            try:
                order = client.get_order(oid)
            except Exception as oe:  # noqa: BLE001
                unfilled.append({
                    "symbol": e.get("symbol"), "side": e.get("side"),
                    "qty": e.get("qty"), "status": "unknown",
                    "error": f"order lookup failed: {oe}",
                })
                continue
            status = str(order.get("status") or "")
            if status == "filled":
                filled_count += 1
            elif status in ("rejected", "canceled", "expired"):
                failed.append({
                    "symbol": e.get("symbol"), "side": e.get("side"),
                    "qty": e.get("qty"), "status": status,
                    "error": f"order {status}",
                })
            else:
                # Still resting / partially filled at the next day's collector run
                # — not terminal, worth surfacing (e.g. a limit order never filled).
                unfilled.append({
                    "symbol": e.get("symbol"), "side": e.get("side"),
                    "qty": e.get("qty"), "status": status,
                    "filled_qty": order.get("filled_qty"),
                })

        try:
            plan_vs_submitted = _build_plan_vs_submitted(prev_date, executions)
        except Exception as e:  # noqa: BLE001
            logger.error("plan_vs_submitted build failed (non-fatal): %s", e)
            plan_vs_submitted = {
                "available": False, "reason": str(e), "status": "indeterminate",
            }

        return {
            "date": prev_date,
            "submitted": len(executions),
            "filled": filled_count,
            "failed": failed,
            "unfilled": unfilled,
            "available": True,
            "plan_vs_submitted": plan_vs_submitted,
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("Execution review build failed (non-fatal)")
        return {"available": False, "reason": str(e)}


def _load_prior_thematic_nominations(today: str) -> list[dict] | None:
    """D2's deliberate one-session lag: the PRIOR trading day's `thematic_
    conviction[]` nominations from `daily-trades/{prev}.json` (`read_trades`
    is best-effort — returns `None` on a missing/malformed blob, unlike
    `read_snapshot`'s raising contract, so a plain 7-day scan is safe here,
    no try/except-per-date needed unlike the `read_snapshot`-based walkbacks).
    """
    d0 = date.fromisoformat(today)
    for back in range(1, 8):
        d = (d0 - timedelta(days=back)).isoformat()
        doc = read_trades(d)
        if isinstance(doc, dict) and doc.get("thematic_conviction") is not None:
            return doc.get("thematic_conviction") or []
        if isinstance(doc, dict):
            # A trades file existed for that date but carried no thematic
            # nominations that session — that IS the answer (empty), not a
            # reason to keep walking back further.
            return []
    return None


def _pvs_num(x) -> float | int:
    """Render a quantity as an int when whole, else a float — matches the
    integer-share quantities this system's trades always carry."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return 0
    return int(f) if f.is_integer() else f


def _build_plan_vs_submitted(prev_date: str, executions: list[dict]) -> dict:
    """Task A2 (2026-08-14, decision D-1) — reconcile the analyzer's DETERMINISTIC
    intended order array (`daily-trades/{prev_date}.json`'s final, post-validation
    `trades[]` — never the model's markdown prose, which `_build_execution_review`
    has no visibility into and never will) against what actually reached the
    broker (`daily-executions/{prev_date}.json`'s `executions[]`, already resolved
    by the caller's 7-day walkback).

    Motivating gap (2026-08-14 A0 audit): `_build_execution_review` reconciled
    submitted -> Alpaca terminal state, but had no knowledge of what the analyzer
    INTENDED, so a submitted-and-filled order could still be a tiny fraction of
    what was actually planned with zero visibility. NOTE (A0 finding): this block
    is JSON-to-broker fidelity only — it structurally cannot catch a divergence
    between the report's markdown narrative and the model's own `trades[]` JSON
    (the 2026-08-12 incident: 6 orders narrated in prose, 1 in `trades[]`, that 1
    matched execution exactly) — Task A1's canonical addendum is what makes THAT
    divergence visible. This block guards a different, real failure mode: a
    genuine JSON -> broker drop (a future executor bug) or a quantity mismatch at
    submission time.

    Matches on (symbol, side) per spec. `extra_in_submission` entries carry an
    `origin` tag (from the execution's own `source` field when present, else
    "unknown") — an `origin == "enforced"` entry is legitimate (reconcile's
    synthesized trades are already merged into `trades[]` before it's written, so
    this should not occur in practice, but the check is defensive) and does NOT
    flip `status` to "mismatch" on its own.

    Missing `daily-trades` blob (or a malformed one) -> `{"available": False,
    "reason": ..., "status": "indeterminate"}` — never a fabricated "ok".
    """
    trades_doc = read_trades(prev_date)
    if not isinstance(trades_doc, dict) or "trades" not in trades_doc:
        return {
            "available": False,
            "reason": f"no daily-trades blob for {prev_date}",
            "status": "indeterminate",
        }

    planned = trades_doc.get("trades") or []

    def _key(side, symbol) -> tuple[str, str]:
        return (str(symbol or "").upper(), str(side or "").lower())

    planned_by_key: dict[tuple[str, str], dict] = {}
    for t in planned:
        planned_by_key[_key(t.get("side"), t.get("symbol"))] = t

    submitted_by_key: dict[tuple[str, str], dict] = {}
    for e in executions:
        submitted_by_key[_key(e.get("side"), e.get("symbol"))] = e

    missing_from_submission: list[dict] = []
    qty_mismatch: list[dict] = []
    for key, t in planned_by_key.items():
        symbol, side = key
        planned_qty = _pvs_num(t.get("quantity"))
        e = submitted_by_key.get(key)
        if e is None:
            missing_from_submission.append({"symbol": symbol, "side": side, "qty": planned_qty})
            continue
        submitted_qty = _pvs_num(e.get("qty"))
        if planned_qty != submitted_qty:
            qty_mismatch.append({
                "symbol": symbol, "side": side,
                "planned_qty": planned_qty, "submitted_qty": submitted_qty,
            })

    extra_in_submission: list[dict] = []
    non_enforced_extra = False
    for key, e in submitted_by_key.items():
        if key in planned_by_key:
            continue
        symbol, side = key
        origin = "enforced" if e.get("source") == "band_enforcement" else "unknown"
        extra_in_submission.append({
            "symbol": symbol, "side": side, "qty": _pvs_num(e.get("qty")), "origin": origin,
        })
        if origin != "enforced":
            non_enforced_extra = True

    status = "mismatch" if (missing_from_submission or qty_mismatch or non_enforced_extra) else "ok"

    return {
        "available": True,
        "date": prev_date,
        "planned_count": len(planned),
        "submitted_count": len(executions),
        "missing_from_submission": missing_from_submission,
        "qty_mismatch": qty_mismatch,
        "extra_in_submission": extra_in_submission,
        "status": status,
    }


# D-D1 default (session 2026-07-28, Task D): a held position's total P/L must have
# moved by more than this many dollars vs the prior snapshot for a reported $0.00
# day P/L to be flagged as suspicious (vs. genuinely flat).
_DAY_PL_ZERO_WATCH_THRESHOLD_USD = 25.0


def _load_prior_position_total_pl(today: str) -> tuple[dict[str, float], dict[str, float]] | None:
    """Prior trading day's per-ticker total P/L AND share count (session
    2026-07-28, Task D; qty extended session 2026-08-01, Task C1) — the SAME
    non-fatal 7-day walkback as the Task-B-fixed `_build_series_deltas`
    (``read_snapshot`` raises on a missing blob; each date is tried individually
    and skipped on failure). Reads `portfolio.positions[].total_gain`/`.quantity`
    (the canonical mapped fields — NOTE `quantity`, not the Alpaca-native `qty`
    this run's own raw fetch uses), not the raw Alpaca response — that's stored,
    unlike the raw `unrealized_intraday_pl`/`lastday_price`/`change_today` this
    run's OWN raw Alpaca fetch supplies for the flagged row.

    Returns ``(total_pl_by_ticker, quantity_by_ticker)``, or ``None`` when no
    prior snapshot was found in the window — the qty map lets the zero-watch
    diagnostic (Task C) tell a real position RESIZE (share count changed —
    total-P/L movement is expected, not a P/L-mapping anomaly) apart from a
    genuine same-size anomaly."""
    d0 = date.fromisoformat(today)
    for back in range(1, 8):
        d = (d0 - timedelta(days=back)).isoformat()
        try:
            snap = read_snapshot(d)
        except Exception:  # noqa: BLE001
            continue
        if snap:
            positions = (snap.get("portfolio") or {}).get("positions") or []
            pl_by_ticker = {
                p["ticker"]: float(p.get("total_gain") or 0)
                for p in positions if p.get("ticker")
            }
            qty_by_ticker = {
                p["ticker"]: float(p.get("quantity") or 0)
                for p in positions if p.get("ticker")
            }
            return pl_by_ticker, qty_by_ticker
    return None


def _build_day_pl_zero_watch(
    raw_positions: list[dict],
    prior_total_pl: dict[str, float] | None,
    prior_qty: dict[str, float] | None = None,
    threshold_usd: float = _DAY_PL_ZERO_WATCH_THRESHOLD_USD,
) -> dict:
    """Deterministic diagnostic (session 2026-07-28, Task D — KMLM day-P/L
    zero-watch; resize-awareness added session 2026-08-01, Task C): flags a held
    position whose reported day P/L printed exactly $0.00 while its total P/L
    moved by more than ``threshold_usd`` since the prior snapshot — a symptom,
    not a diagnosis. Diagnostics ONLY; this module does not (and should not)
    adjudicate whether Alpaca itself sends the zero (upstream) or our mapping
    drops it (pipeline) — the flagged row echoes the RAW Alpaca position fields
    relevant to day-P/L derivation so a human/future session can.

    ``raw_positions`` is the UNMAPPED Alpaca ``/v2/positions`` response (the same
    ``pos`` list the collector already fetches) — day P/L is mapped from
    ``unrealized_intraday_pl`` (see `positions[].day_gain` in `run()`); this
    function reads that field directly, plus ``lastday_price``/``current_price``/
    ``change_today``, none of which are otherwise persisted anywhere. Share
    count is read from the SAME raw response's ``qty`` field (Alpaca-native —
    NOTE this is `qty`, not the canonical `portfolio.positions[].quantity` field
    `prior_qty` is built from; see the `held_qty` incident this file already
    documents elsewhere).

    ``prior_total_pl`` (ticker -> total P/L from the prior snapshot, or None when
    unavailable — non-fatal: nothing is flagged without a prior value to diff
    against, never a crash) drives the "moved materially" comparison.

    ``prior_qty`` (ticker -> share count from the prior snapshot, optional —
    Task C1) lets this diagnostic tell a genuine RESIZE (the share count itself
    changed since the prior snapshot — realizing a sale's gain, or a buy's new
    lot basis, both of which move total P/L on their own and are NOT a P/L-
    mapping anomaly) apart from a same-size position whose total P/L moved with
    no trade to explain it. A resized position is suppressed from
    ``total_pl_delta``-only flagging UNLESS the price-identity signal also fires
    (``lastday_price == current_price`` — a literally frozen quote, the
    independent, genuine anomaly signal that must be kept regardless of any
    resize); when kept, the row is annotated ``position_resized: true`` so the
    report attributes the delta correctly instead of calling it an unexplained
    anomaly. Omitting ``prior_qty`` (the default) preserves the original
    unconditional delta-only behavior — no share-count data means no resize
    determination is possible.

    Two INDEPENDENT trigger paths (2026-08-06 audit B7 — before this, the price-
    identity signal only ever fired as a resize-suppression OVERRIDE, still
    gated behind the outer ``abs(delta) > threshold_usd`` check; a stuck feed on
    a SMALL-delta position — 08-05's VDE, a ~4.1% position with $0.00 day P/L
    the same session COWZ was flagged — slipped through entirely):
      1. ``delta_trigger`` — the original mechanism: day_pl==0 and
         ``abs(total_pl_delta) > threshold_usd``.
      2. ``identity_trigger`` — day_pl==0 AND ``lastday_price == current_price``
         AND qty>0, completely INDEPENDENT of total_pl_delta's magnitude (or
         even its availability).
    A position qualifies via EITHER path. Resize-suppression applies ONLY to a
    delta-only qualifier (``delta_trigger and not identity_trigger``) — the
    identity path, being independent of delta, is never suppressed by a resize.
    """
    if not prior_total_pl:
        return {"available": False, "reason": "no prior snapshot total P/L available", "flagged": []}

    flagged: list[dict] = []
    for p in raw_positions or []:
        sym = p.get("symbol")
        if not sym:
            continue
        try:
            day_pl = float(p.get("unrealized_intraday_pl") or 0)
        except (TypeError, ValueError):
            continue
        if day_pl != 0.0:
            continue

        try:
            total_pl = float(p.get("unrealized_pl") or 0)
        except (TypeError, ValueError):
            continue
        prior = prior_total_pl.get(sym)
        delta = round(total_pl - prior, 2) if prior is not None else None
        delta_trigger = delta is not None and abs(delta) > threshold_usd

        try:
            current_qty = float(p.get("qty")) if p.get("qty") is not None else None
        except (TypeError, ValueError):
            current_qty = None
        try:
            price_frozen = float(p.get("lastday_price")) == float(p.get("current_price"))
        except (TypeError, ValueError):
            price_frozen = False
        identity_trigger = price_frozen and current_qty is not None and current_qty > 0

        if not (delta_trigger or identity_trigger):
            continue

        prior_qty_val = (prior_qty or {}).get(sym)
        resized = (
            prior_qty_val is not None and current_qty is not None
            and current_qty != prior_qty_val
        )

        # Task C2 (unchanged): a resize fully (or partly) explains the total-P/L
        # move on its own — do not flag on total_pl_delta ALONE in that case.
        # The frozen-quote identity signal is independent of delta and must
        # still surface even on a resized position (annotated, not suppressed).
        if delta_trigger and not identity_trigger and resized:
            continue

        flagged.append({
            "symbol": sym,
            "day_pl_reported": day_pl,
            "total_pl": total_pl,
            "prior_total_pl": prior,
            "total_pl_delta": delta,
            "lastday_price": p.get("lastday_price"),
            "current_price": p.get("current_price"),
            "unrealized_intraday_pl": p.get("unrealized_intraday_pl"),
            "change_today": p.get("change_today"),
            "prior_qty": prior_qty_val,
            "current_qty": current_qty,
            "position_resized": resized,
            "identity_trigger": identity_trigger,
        })

    out: dict = {"available": True, "flagged": flagged}
    if len(flagged) > 1:
        # 2026-08-06 audit B7: multiple tickers sharing the identical frozen-
        # quote symptom in one session most likely reflects a shared upstream
        # feed issue, not N independent per-ticker anomalies — collapse to one
        # snapshot-wide note (full per-ticker detail stays in `flagged` below).
        symbols = [f["symbol"] for f in flagged]
        out["multi_symbol_note"] = (
            f"{len(flagged)} positions ({', '.join(symbols)}) show a frozen day-P/L "
            "price mark this session (unrealized_intraday_pl == $0.00, "
            "lastday_price == current_price) — likely a shared upstream feed issue, "
            "not independent per-ticker anomalies. See flagged[] for per-ticker detail."
        )
    return out


# The freshness-set macro series the analyzer actually cites in cadence/new-print
# adjudication (mirrors `analyzer.handler._MACRO_SERIES_KEPT` minus the pure rate
# series the analyzer already compares via `policy_axis`/`bond_signals`, and adding
# the HY OAS credit series `divergences.credit_complacency` cites).
_SERIES_DELTAS_TRACKED = (
    "GDPNOW", "CPILFESL", "PCEPILFE", "CPIAUCSL", "PCEPI",
    "DFF", "DGS2", "DFII10",
    "DCOILWTICO", "DCOILBRENTEU", "DTWEXBGS",
    "T5YIE", "T5YIFR", "T10YIE",
    "BAMLH0A0HYM2",
)

# B4 (2026-07-21): per-series staleness thresholds + dating convention for the
# deterministic `freshness` block. Monthly macro (CPI/PCE) is dated by observation
# month and stays "fresh" ~45d (monthly cadence + release lag); daily series use the
# 5d threshold; GDPNow uses VINTAGE recency (the realtime asof), not observation age.
_FRESHNESS_MONTHLY = frozenset({"CPILFESL", "PCEPILFE", "CPIAUCSL", "PCEPI"})
_FRESHNESS_DAILY_THRESHOLD_D = 5
_FRESHNESS_MONTHLY_THRESHOLD_D = 45
_FRESHNESS_GDPNOW_THRESHOLD_D = 7


def _build_freshness(macro_data: dict, growth_axis: dict, today: str) -> dict:
    """Deterministic Data-Freshness table (B4): per tracked series
    ``{value, as_of, days_stale, stale, convention, threshold_days}``. The model
    echoes this verbatim and NEVER re-derives a date or staleness — the flip-flopping
    freshness table (GDPNow "3d" one day, "81d" the next for the SAME value) came from
    the model picking observation-date vs vintage-date differently each run. GDPNow is
    dated by vintage recency (``growth_axis.as_of``); everything else by observation
    date with a cadence-appropriate threshold. Non-fatal in the caller."""
    out: dict[str, dict] = {}
    for sid in _SERIES_DELTAS_TRACKED:
        if sid == "GDPNOW":
            value = (growth_axis or {}).get("gdpnow_latest")
            as_of = (growth_axis or {}).get("as_of")
            convention = "vintage_date"
            threshold = _FRESHNESS_GDPNOW_THRESHOLD_D
        else:
            rows = macro_data.get(sid) or []
            row = rows[0] if rows else None
            value = _obs_value(row)
            as_of = row.get("date") if row else None
            convention = "observation_date"
            threshold = (_FRESHNESS_MONTHLY_THRESHOLD_D if sid in _FRESHNESS_MONTHLY
                         else _FRESHNESS_DAILY_THRESHOLD_D)
        ds = _days_stale(as_of, today)
        out[sid] = {
            "value": value,
            "as_of": as_of,
            "days_stale": ds,
            "stale": bool(ds is not None and ds > threshold),
            "convention": convention,
            "threshold_days": threshold,
        }
    return {"available": True, "series": out}


def _obs_value(row: dict | None) -> float | None:
    """A FRED observation's numeric value, or None for missing/non-numeric (FRED
    marks a missing print with the literal string ".")."""
    if not row:
        return None
    v = row.get("value")
    if v in (None, ".", ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _build_series_deltas(macro_data: dict, today: str) -> dict:
    """Deterministic prior-vs-current comparison for the freshness-set macro series
    (session 2026-07-17, Task E — hardens the F1 catalyst-adjudication mechanism).

    07-17's adjudication section fired (the mechanism works) but attributed the CPI
    flag to the wrong prior report and compared against the wrong prior value
    ("prior report showed 2.96%" — that was 07-14; the actual prior report showed
    2.81%) — the model's memory of prior-report values is unreliable, so this makes
    the comparison DATA instead of recollection. Same non-fatal "read back the prior
    trading day's snapshot" pattern as `_build_execution_review` (looks back up to 7
    days so a weekend/holiday gap doesn't stall it).

    Per tracked series: ``{value, as_of, prior_value, prior_as_of, delta, new_print}``
    — ``new_print`` is true whenever the value OR the as_of date changed vs. the
    prior snapshot (an unchanged value with a bumped as_of is still a new print — the
    prompt must never call that "no new print"). A series present today but absent
    from the prior snapshot gets ``prior_value``/``prior_as_of``/``delta`` all
    ``None`` and ``new_print`` left ``False`` (nothing to compare against yet, not a
    false new-print claim). Non-fatal: no prior snapshot within 7 days, or any
    failure, returns ``{"available": False, "reason": ...}`` — never fatal, never
    loses the snapshot.
    """
    try:
        d0 = date.fromisoformat(today)
        prior_macro: dict | None = None
        prior_date: str | None = None
        for back in range(1, 8):
            d = (d0 - timedelta(days=back)).isoformat()
            try:
                snap = read_snapshot(d)
            except Exception:  # noqa: BLE001
                # read_snapshot RAISES on a missing blob (unlike read_executions'
                # best-effort None) — a per-date miss (e.g. a weekend/holiday gap)
                # must not abort the whole 7-day walkback (2026-07-27 Monday hit
                # this on back=1 landing on Sunday). Mirrors the identical
                # try/except/continue already used around read_snapshot in
                # _load_equity_spy_series (~line 503).
                continue
            if snap:
                prior_macro = (snap.get("macro") or {}).get("data") or {}
                prior_date = d
                break
        if prior_macro is None:
            return {"available": False, "reason": "no prior snapshot found in the last 7 days"}

        series: dict[str, dict] = {}
        for sid in _SERIES_DELTAS_TRACKED:
            cur_rows = macro_data.get(sid) or []
            if not cur_rows:
                continue
            cur_row = cur_rows[0]
            prior_rows = prior_macro.get(sid) or []
            prior_row = prior_rows[0] if prior_rows else None

            cur_val = _obs_value(cur_row)
            cur_asof = cur_row.get("date")
            prior_val = _obs_value(prior_row)
            prior_asof = prior_row.get("date") if prior_row else None

            delta = round(cur_val - prior_val, 4) if (cur_val is not None and prior_val is not None) else None
            new_print = bool(prior_row) and (cur_val != prior_val or cur_asof != prior_asof)
            series[sid] = {
                "value": cur_val, "as_of": cur_asof,
                "prior_value": prior_val, "prior_as_of": prior_asof,
                "delta": delta, "new_print": new_print,
            }
        return {"available": True, "prior_date": prior_date, "series": series}
    except Exception as e:  # noqa: BLE001
        logger.exception("Series deltas build failed (non-fatal)")
        return {"available": False, "reason": str(e)}


def _aggregate_by_quadrant(target_weights_pct: dict, literal_cash_pct: float,
                           effective_selected: dict[str, str] | None = None) -> dict:
    """Deterministic per-quadrant aggregation of the reference `target_weights_pct`
    (Task 5). Each ticker lands in exactly one bucket via `primary_quadrant`; SGOV's
    target plus the literal-cash buffer form the `cash_sleeve` bucket. The analyzer
    echoes this verbatim rather than re-deriving quadrant totals freehand. Sums to
    ~100 within rounding (sub-0.05% floors already dropped from target_weights_pct).
    ``effective_selected`` (session 2026-07-27) buckets a freshly auto-switched
    incumbent (e.g. IHE) into its role's quadrant instead of "unclassified"."""
    buckets = {"Q1": 0.0, "Q2": 0.0, "Q3": 0.0, "Q4": 0.0, "intl": 0.0, "cash_sleeve": 0.0}
    for tkr, w in (target_weights_pct or {}).items():
        q = primary_quadrant(tkr, effective_selected)
        buckets[q] = buckets.get(q, 0.0) + float(w or 0.0)
    buckets["cash_sleeve"] += float(literal_cash_pct or 0.0)
    return {k: round(v, 2) for k, v in buckets.items()}


def _build_functional_coverage(positions: list[dict], equity: float) -> dict:
    """Deterministic Table-B "functional coverage" view (B3, deferred finding 7).

    Table B counts each held name in EVERY quadrant its role's ``quadrants`` list
    covers (a dual-quadrant name like VDE/energy counts in both Q2 and Q3), so it is
    NOT additive to 100%. It was the last quadrant table left to the model to compute
    per-prompt, and the arithmetic was broken on both 2026-07-20/21 (07-21 claimed Q3
    68.72% vs 79.59% summed from its own listed names). Now precomputed and echoed
    verbatim, exactly like Table A (`_build_quadrant_allocation`).

    Rules: bucket via ``role_of`` + the role's ``quadrants`` list; SGOV (the cash role)
    counts in Q4 (primary duration proxy) AND Q3 (secondary), per the prompt doctrine;
    intl-role (``rotation``) holdings go to the ``intl`` row only; a held off-roster /
    legacy name (no covering role) is excluded with a note, never silently dropped.
    ``committed_q4_pct`` = holdings in Q4-EXCLUSIVE roles (duration_long/duration_mid/
    defensive_equity, i.e. quadrants == ['Q4']) so the SGOV intent annotation can quote
    a deterministic 'truly committed to Q4' figure separate from SGOV's optionality."""
    if equity <= 0:
        return {"available": False, "reason": "no equity"}
    roles_by_id = {r["role_id"]: r for r in roles_config()}
    quadrants: dict[str, dict] = {
        q: {"total_pct": 0.0, "names": []} for q in ("Q1", "Q2", "Q3", "Q4", "intl")
    }
    excluded: list[dict] = []
    committed_q4 = 0.0
    sgov_pct = 0.0

    def _add(bucket: str, sym: str, pct: float) -> None:
        quadrants[bucket]["total_pct"] = round(quadrants[bucket]["total_pct"] + pct, 4)
        quadrants[bucket]["names"].append({"ticker": sym, "pct": pct})

    for pos in positions or []:
        sym = str(pos.get("ticker") or "").upper()
        if not sym:
            continue
        try:
            mv = float(pos.get("market_value") or 0)
        except (TypeError, ValueError):
            mv = 0.0
        pct = round(mv / equity * 100.0, 4)
        rid = role_of(sym)
        if rid is None:
            excluded.append({"ticker": sym, "pct": pct,
                             "reason": "off_roster_or_legacy — no role covers this name"})
            continue
        quads = roles_by_id.get(rid, {}).get("quadrants")
        if quads == "rotation":
            _add("intl", sym, pct)
        elif quads == "cash":
            sgov_pct = round(sgov_pct + pct, 4)
            _add("Q4", sym, pct)   # primary — duration proxy
            _add("Q3", sym, pct)   # secondary
        elif isinstance(quads, list):
            for q in quads:
                if q in quadrants:
                    _add(q, sym, pct)
            if quads == ["Q4"]:
                committed_q4 = round(committed_q4 + pct, 4)
        else:
            excluded.append({"ticker": sym, "pct": pct,
                             "reason": f"unrecognized role quadrants: {quads}"})

    return {
        "available": True,
        "quadrants": {q: {"total_pct": round(v["total_pct"], 2), "names": v["names"]}
                      for q, v in quadrants.items()},
        "excluded": excluded,
        "sgov_note_inputs": {"sgov_pct": round(sgov_pct, 2),
                             "committed_q4_pct": round(committed_q4, 2)},
    }


def _build_quadrant_allocation(positions: list[dict], equity: float, cash_usd: float,
                               effective_selected: dict[str, str] | None = None) -> dict:
    """Deterministic Table-A "Current % of equity" view (session 2026-07-17, Task D).

    Companion to `_aggregate_by_quadrant` (the Reference column) — uses the SAME
    static `primary_quadrant()` bucketing, so Current and Reference are always
    apples-to-apples per bucket: a nonzero gap in a dual-quadrant role's bucket
    (e.g. VDE/energy, tagged Q2 by `primary_quadrant` regardless of the current
    regime) reflects a real reference gap, never an artifact of the two columns
    using different tagging rules for the same name.

    Every held name lands in EXACTLY one bucket: Q1-Q4 (per `primary_quadrant`),
    `intl` (the two rotation roles), `legacy_exits` (a held LEGACY_EXITS name —
    a dedicated row so a wind-down position is never silently folded into a
    quadrant it no longer represents), `off_roster` (held but outside CORE_ROSTER
    and not a legacy exit — a flex leftover like MU), or `cash_sleeve` (SGOV +
    literal cash). A name `primary_quadrant` cannot classify despite being in
    CORE_ROSTER (should not happen by construction) lands in `unmapped` rather
    than vanishing silently. Buckets sum to ~100% of equity within rounding.
    """
    buckets = {"Q1": 0.0, "Q2": 0.0, "Q3": 0.0, "Q4": 0.0, "intl": 0.0,
               "legacy_exits": 0.0, "off_roster": 0.0, "cash_sleeve": 0.0,
               "unmapped": 0.0}
    contributions: dict[str, list] = {k: [] for k in buckets}
    if equity <= 0:
        return {
            "available": False, "buckets": buckets, "contributions": contributions,
            "cash_literal_pct": 0.0, "total_pct": 0.0,
        }

    for pos in positions or []:
        sym = str(pos.get("ticker") or "").upper()
        if not sym:
            continue
        try:
            mv = float(pos.get("market_value") or 0)
        except (TypeError, ValueError):
            mv = 0.0
        pct = round(mv / equity * 100.0, 4)
        bucket = quadrant_allocation_bucket(sym, effective_selected)
        buckets[bucket] = round(buckets[bucket] + pct, 4)
        contributions[bucket].append({"symbol": sym, "pct_of_equity": pct})

    cash_pct = round(float(cash_usd or 0) / equity * 100.0, 4)
    buckets["cash_sleeve"] = round(buckets["cash_sleeve"] + cash_pct, 4)
    return {
        "available": True,
        "buckets": buckets,
        "contributions": contributions,
        "cash_literal_pct": cash_pct,
        "total_pct": round(sum(buckets.values()), 4),
    }


def _build_regional_rotation(fmp: FMPClient, macro_data: dict) -> dict:
    """Pre-compute the US-vs-international rotation signal block.

    Produces, for the analyzer to consume directly:
      - per-ticker 60-trading-day return + excess vs SPY
      - leaders / laggards vs SPY (>= +/-5 percentage-point cutoff)
      - 50/200-day moving-average cross for {IDMO,AIA,IEMG,EWJ}/SPY ratios
      - DXY 60d % change (FRED DTWEXBGS) with tailwind/headwind tag at +/-3%
      - policy divergence sub-score from US 2Y yield trend
      - composite Rotation Score 0-10 (dollar 30 / RS 30 / policy 20 / valuation 20)

    Components we cannot compute from current data sources (ETF flows from
    Bloomberg/ICI, regional earnings revision breadth) are marked
    'unavailable' and held at the neutral score of 5.
    """
    out: dict = {
        "window_trading_days": _ROTATION_WINDOW_DAYS,
        "ma_short_days": _MA_SHORT_DAYS,
        "ma_long_days": _MA_LONG_DAYS,
        "benchmark": "SPY",
        "tickers": {},
        "leaders_vs_spy": [],
        "laggards_vs_spy": [],
        "ratio_ma_cross": {},
        "dxy_60d_pct_change": None,
        "dxy_tailwind_for_intl": None,
        "policy": {},
        "rotation_flags": {},
        "rotation_score": {},
    }

    def _close(row: dict) -> float | None:
        v = row.get("price") if row.get("price") is not None else row.get("close")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    # --- 1. Fetch full history once per rotation ticker (newest-first) -------
    histories: dict[str, list[dict]] = {}
    for t in _ROTATION_TICKERS:
        try:
            rows = fmp.get_historical_price_light(t)
        except Exception as e:  # noqa: BLE001
            logger.warning("Rotation: history fetch failed for %s: %s", t, e)
            continue
        if rows:
            histories[t] = rows

    # --- 2. 60d returns + excess vs SPY --------------------------------------
    spy_return: float | None = None
    per_ticker_ret: dict[str, float] = {}
    for t, rows in histories.items():
        if len(rows) < _ROTATION_WINDOW_DAYS + 1:
            continue
        latest = _close(rows[0])
        past = _close(rows[_ROTATION_WINDOW_DAYS])
        if not latest or not past or past == 0:
            continue
        ret_pct = (latest / past - 1.0) * 100.0
        per_ticker_ret[t] = round(ret_pct, 2)
        out["tickers"][t] = {
            "return_60d_pct": round(ret_pct, 2),
            "latest_close": round(latest, 4),
            "latest_date": rows[0].get("date"),
            "window_start_close": round(past, 4),
            "window_start_date": rows[_ROTATION_WINDOW_DAYS].get("date"),
        }
        if t == "SPY":
            spy_return = ret_pct

    if spy_return is not None:
        for t, ret in per_ticker_ret.items():
            if t == "SPY":
                continue
            excess = round(ret - spy_return, 2)
            out["tickers"][t]["excess_vs_spy_pp"] = excess
            if excess >= 5.0:
                out["leaders_vs_spy"].append({"ticker": t, "excess_pp": excess})
            elif excess <= -5.0:
                out["laggards_vs_spy"].append({"ticker": t, "excess_pp": excess})
        out["leaders_vs_spy"].sort(key=lambda x: x["excess_pp"], reverse=True)
        out["laggards_vs_spy"].sort(key=lambda x: x["excess_pp"])

    # --- 3. 50/200-day MA cross on intl/SPY ratios ---------------------------
    spy_hist = histories.get("SPY") or []
    if len(spy_hist) >= _MA_LONG_DAYS:
        # Build a date->close map for SPY so we can align on common trading dates.
        spy_by_date = {
            r.get("date"): _close(r) for r in spy_hist
            if r.get("date") and _close(r)
        }
        for t in _INTL_RATIO_TICKERS:
            int_hist = histories.get(t) or []
            if len(int_hist) < _MA_LONG_DAYS:
                continue
            # Build aligned ratio series, newest-first.
            ratios: list[float] = []
            for r in int_hist:
                d = r.get("date")
                ic = _close(r)
                sc = spy_by_date.get(d)
                if ic and sc:
                    ratios.append(ic / sc)
                if len(ratios) >= _MA_LONG_DAYS:
                    break
            if len(ratios) < _MA_LONG_DAYS:
                continue
            ratio_now = ratios[0]
            ma_short = sum(ratios[:_MA_SHORT_DAYS]) / _MA_SHORT_DAYS
            ma_long = sum(ratios[:_MA_LONG_DAYS]) / _MA_LONG_DAYS
            out["ratio_ma_cross"][f"{t}/SPY"] = {
                "ratio_now": round(ratio_now, 6),
                "ma_50d": round(ma_short, 6),
                "ma_200d": round(ma_long, 6),
                "ma50_above_ma200": ma_short > ma_long,
                "ratio_above_ma200": ratio_now > ma_long,
                "signal": (
                    "bullish_intl" if (ma_short > ma_long and ratio_now > ma_long)
                    else "bearish_intl" if (ma_short < ma_long and ratio_now < ma_long)
                    else "mixed"
                ),
            }

    # --- 4. DXY 60-trading-day % change --------------------------------------
    dxy_rows = macro_data.get("DTWEXBGS") or []
    valid_dxy = [
        (r.get("date"), float(r["value"])) for r in dxy_rows
        if r.get("value") not in (None, ".", "")
    ]
    dxy_pct: float | None = None
    if len(valid_dxy) >= _ROTATION_WINDOW_DAYS + 1:
        latest_dxy = valid_dxy[0][1]
        past_dxy = valid_dxy[_ROTATION_WINDOW_DAYS][1]
        if past_dxy:
            dxy_pct = round((latest_dxy / past_dxy - 1.0) * 100.0, 2)
            out["dxy_60d_pct_change"] = dxy_pct
            out["dxy_latest_date"] = valid_dxy[0][0]
            if dxy_pct <= -3.0:
                out["dxy_tailwind_for_intl"] = "tailwind"
            elif dxy_pct >= 3.0:
                out["dxy_tailwind_for_intl"] = "headwind"
            else:
                out["dxy_tailwind_for_intl"] = "neutral"

    # --- 5. Policy divergence (US 2Y yield trend as a proxy) -----------------
    dgs2_rows = macro_data.get("DGS2") or []
    valid_dgs2 = [
        float(r["value"]) for r in dgs2_rows
        if r.get("value") not in (None, ".", "")
    ]
    us2y_bp_change: float | None = None
    if len(valid_dgs2) >= _ROTATION_WINDOW_DAYS + 1:
        latest_y = valid_dgs2[0]
        past_y = valid_dgs2[_ROTATION_WINDOW_DAYS]
        us2y_bp_change = round((latest_y - past_y) * 100.0, 1)  # bp
    fed_funds = next(
        (float(r["value"]) for r in (macro_data.get("DFF") or [])
         if r.get("value") not in (None, ".", "")),
        None,
    )
    ecb_rate = next(
        (float(r["value"]) for r in (macro_data.get("ECBDFR") or [])
         if r.get("value") not in (None, ".", "")),
        None,
    )
    out["policy"] = {
        "fed_funds_latest": fed_funds,
        "ecb_deposit_latest": ecb_rate,
        "us_2y_60d_bp_change": us2y_bp_change,
        # Falling US 2Y => market pricing Fed easing => USD weakness => intl tailwind.
        "stance_for_intl": (
            "supportive" if us2y_bp_change is not None and us2y_bp_change <= -25
            else "adverse" if us2y_bp_change is not None and us2y_bp_change >= 25
            else "neutral" if us2y_bp_change is not None
            else "unknown"
        ),
    }

    # --- 6. Legacy boolean flags (kept for backward compat) ------------------
    rs_flag = len(out["leaders_vs_spy"]) > 0
    dxy_tail = out["dxy_tailwind_for_intl"] == "tailwind"
    dxy_head = out["dxy_tailwind_for_intl"] == "headwind"
    out["rotation_flags"] = {
        "intl_rs_leader": rs_flag,
        "dxy_tailwind": dxy_tail,
        "dxy_headwind": dxy_head,
        "rotate_to_international": sum([rs_flag, dxy_tail]) >= 2,
        "rotate_back_to_us": sum([(not rs_flag and bool(per_ticker_ret)), dxy_head]) >= 2,
    }

    # --- 7. Composite Rotation Score 0-10 ------------------------------------
    # Weights: dollar 30 / relative strength 30 / policy 20 / valuation 20.
    # Each component is scored 0-10 then weight-averaged. Missing components
    # default to neutral=5 and are flagged in 'components_missing'.
    components: dict[str, dict] = {}
    missing: list[str] = []

    # Dollar momentum (lower DXY = higher score for intl).
    if dxy_pct is not None:
        if dxy_pct <= -8: d_score = 10.0
        elif dxy_pct <= -5: d_score = 8.5
        elif dxy_pct <= -3: d_score = 7.0
        elif dxy_pct <= -1: d_score = 6.0
        elif dxy_pct <  1: d_score = 5.0
        elif dxy_pct <  3: d_score = 4.0
        elif dxy_pct <  5: d_score = 3.0
        elif dxy_pct <  8: d_score = 1.5
        else: d_score = 0.0
    else:
        d_score = 5.0
        missing.append("dollar_momentum")
    components["dollar_momentum"] = {"score": d_score, "weight": 30, "input_dxy_60d_pct": dxy_pct}

    # Relative strength: average excess vs SPY across intl tickers in universe.
    intl_excess = [
        info.get("excess_vs_spy_pp") for tk, info in out["tickers"].items()
        if tk != "SPY" and info.get("excess_vs_spy_pp") is not None
    ]
    if intl_excess:
        avg_excess = sum(intl_excess) / len(intl_excess)
        # +10pp avg -> 10, 0pp -> 5, -10pp -> 0; clamp.
        rs_score = max(0.0, min(10.0, 5.0 + avg_excess * 0.5))
        rs_input = round(avg_excess, 2)
    else:
        rs_score = 5.0
        rs_input = None
        missing.append("relative_strength")
    components["relative_strength"] = {"score": round(rs_score, 2), "weight": 30, "input_avg_excess_pp": rs_input}

    # Policy divergence: based on US 2Y bp change.
    if us2y_bp_change is not None:
        # Falling >=50bp -> 10; +/-25bp band -> 5; rising >=50bp -> 0.
        p_score = max(0.0, min(10.0, 5.0 - us2y_bp_change / 10.0))
    else:
        p_score = 5.0
        missing.append("policy_divergence")
    components["policy_divergence"] = {"score": round(p_score, 2), "weight": 20, "input_us_2y_60d_bp": us2y_bp_change}

    # Valuation gap: not computable from current feeds (ETF P/E aggregation absent).
    v_score = 5.0
    missing.append("valuation_gap")
    components["valuation_gap"] = {"score": v_score, "weight": 20, "input": None, "note": "ETF forward-P/E aggregation not available on current data tier"}

    weighted = sum(c["score"] * c["weight"] for c in components.values()) / 100.0
    # Round FIRST, then bucket on the rounded composite — otherwise the category can
    # be derived from an unrounded score that disagrees with the displayed number
    # (2026-07-09: weighted 3.049 displayed as 3.0 but bucketed "transition_window",
    # handing the analyzer a "don't tilt" number with a "tilt" label).
    composite, category = _rotation_composite_category(weighted)

    out["rotation_score"] = {
        "composite": composite,
        "category": category,
        "components": components,
        "components_missing": missing,
        "scoring_rubric": "0-3 US leadership intact; 4-6 transition window; 7-10 rotation underway",
    }

    return out


def _build_bond_signals(macro_data: dict) -> dict:
    """Pre-compute a four-signal bond market scorecard for the analyzer.

    Inputs come from the FRED ``macro_data`` block (deep-history fetched in
    ``run()``). Output sections:

      yield_curve:   3m10y / 2s10s / 5s30s spreads + 5d deltas, curve regime
                     label, Estrella-Mishkin 12-month recession probability
      credit:        HY OAS, IG OAS — levels, 5d/20d deltas, 90d percentile,
                     ``credit_stress`` flag (HY OAS +50bp 4w OR >=90th pct OR
                     IG OAS +25bp 4w)
      breakevens:    5y, 10y, 5y5y — levels + 20d deltas
      systemic:      MBS-Treasury spread proxy (MORTGAGE30US - DGS10) + 20d
                     delta, real 10Y yield (DFII10) level

    Composite scorecard: each of the four signals scored -2..+2 (negative =
    bearish risk assets); composite -8..+8 with label
    ``risk_on`` / ``neutral`` / ``defensive`` / ``acute_defensive``.

    All deltas use trading-day approximations (1d, ~5d, ~20d index offsets in
    the descending-order FRED responses). Percentile is computed over the
    available 90d window. None propagates through cleanly when data missing.
    """
    out: dict = {
        "yield_curve": {},
        "credit": {},
        "breakevens": {},
        "systemic": {},
        "scorecard": {},
        "caveat": (
            "2025-2026 bond signals may be partially distorted by QT and "
            "Treasury issuance patterns. Require confluence (>=3 of 4 signals "
            "agreeing) before acting on the composite alone."
        ),
    }

    def _vals(sid: str) -> list[float]:
        """Latest-first list of floats, skipping missing observations."""
        rows = macro_data.get(sid) or []
        out_vals: list[float] = []
        for r in rows:
            v = r.get("value")
            if v in (None, ".", ""):
                continue
            try:
                out_vals.append(float(v))
            except (TypeError, ValueError):
                continue
        return out_vals

    def _delta_bp(vals: list[float], n: int) -> float | None:
        """Change in basis points over n trading days."""
        if len(vals) > n:
            return round((vals[0] - vals[n]) * 100.0, 1)
        return None

    def _percentile(vals: list[float], v: float | None) -> int | None:
        if v is None or not vals:
            return None
        below = sum(1 for x in vals if x <= v)
        return int(round(100.0 * below / len(vals)))

    def _latest(vals: list[float]) -> float | None:
        return round(vals[0], 4) if vals else None

    def _latest_date(sid: str) -> str | None:
        """B2 (2026-08-14 audit): the observation date of a series' newest usable
        row — populates a real `as_of` for the divergence detectors' staleness
        gate, which previously hardcoded `as_of: None` on every bond-signal leg."""
        rows = macro_data.get(sid) or []
        for r in rows:
            if r.get("value") not in (None, ".", ""):
                return r.get("date")
        return None

    # --- 1. Yield curve ----------------------------------------------------
    dgs2  = _vals("DGS2")
    dgs10 = _vals("DGS10")
    dgs30 = _vals("DGS30")
    dgs3m = _vals("DGS3MO")
    t10y2y = _vals("T10Y2Y")
    t10y3m = _vals("T10Y3M")

    # 5s30s spread we compute ourselves (FRED doesn't ship it as a series)
    # using DGS5 isn't in our set; approximate "belly-to-long" via 10s30s.
    spread_10s30s = None
    if dgs10 and dgs30:
        spread_10s30s = round(dgs30[0] - dgs10[0], 3)

    curve_2s10s_latest = _latest(t10y2y) or (
        round(dgs10[0] - dgs2[0], 3) if dgs10 and dgs2 else None
    )
    curve_3m10y_latest = _latest(t10y3m) or (
        round(dgs10[0] - dgs3m[0], 3) if dgs10 and dgs3m else None
    )

    # Curve regime (bull/bear * steepen/flatten) from 5d deltas in 2Y and 10Y
    regime = "unknown"
    d10_5d = _delta_bp(dgs10, 5)
    d2_5d  = _delta_bp(dgs2, 5)
    if d10_5d is not None and d2_5d is not None:
        # Steepening = 2s10s widened; Flattening = 2s10s narrowed
        # Bull = yields falling on average; Bear = yields rising
        avg = (d10_5d + d2_5d) / 2.0
        steepening = (d10_5d - d2_5d) > 5.0   # 10Y rose more (or fell less) than 2Y
        flattening = (d10_5d - d2_5d) < -5.0
        if steepening and avg < -5.0:
            regime = "bull_steepening"
        elif steepening and avg > 5.0:
            regime = "bear_steepening"
        elif flattening and avg < -5.0:
            regime = "bull_flattening"
        elif flattening and avg > 5.0:
            regime = "bear_flattening"
        else:
            regime = "stable"

    # Estrella-Mishkin probit: P(recession 12m) = Phi(-0.5333 - 0.6629 * spread3m10y)
    recession_prob = None
    if curve_3m10y_latest is not None:
        import math
        z = -0.5333 - 0.6629 * curve_3m10y_latest
        # Normal CDF via erf
        recession_prob = round(0.5 * (1.0 + math.erf(z / math.sqrt(2.0))) * 100.0, 1)

    out["yield_curve"] = {
        "dgs3m": _latest(dgs3m),
        "dgs2":  _latest(dgs2),
        "dgs10": _latest(dgs10),
        "dgs30": _latest(dgs30),
        "spread_2s10s": curve_2s10s_latest,
        "spread_2s10s_delta_5d_bp": _delta_bp(t10y2y, 5) if t10y2y else None,
        "spread_3m10y": curve_3m10y_latest,
        "spread_3m10y_delta_5d_bp": _delta_bp(t10y3m, 5) if t10y3m else None,
        "spread_10s30s": spread_10s30s,
        "dgs10_delta_5d_bp": d10_5d,
        "dgs2_delta_5d_bp":  d2_5d,
        "regime": regime,
        "recession_prob_12m": recession_prob,
        "regime_notes": (
            "bull_steepening: Fed-cuts-into-weakness; bear_steepening: "
            "inflation/fiscal concern; bull_flattening: growth fading; "
            "bear_flattening: Fed-hike risk"
        ),
    }

    # --- 2. Credit spreads -------------------------------------------------
    hy = _vals("BAMLH0A0HYM2")
    ig = _vals("BAMLC0A0CM")

    hy_latest = _latest(hy)
    ig_latest = _latest(ig)
    hy_d20 = _delta_bp(hy, 20)
    ig_d20 = _delta_bp(ig, 20)
    hy_pct = _percentile(hy, hy_latest)
    ig_pct = _percentile(ig, ig_latest)

    # trend_4w: coarse tightening/widening/flat label off the 20d (~4w) HY OAS
    # delta — consumed by _build_leading_growth and _build_market_implied_quadrant.
    # 2026-08-06 audit B1: this key was referenced by both but never actually set
    # here (mismatch against the raw delta_20d_bp field), so the HY-OAS vote read
    # None in both consumers every session.
    _hy_trend_bp = float((_load_risk_limits().get("bond_signals") or {}).get("hy_oas_trend_bp", 10.0))
    hy_trend_4w: str | None = None
    if hy_d20 is not None:
        if hy_d20 <= -_hy_trend_bp:
            hy_trend_4w = "tightening"
        elif hy_d20 >= _hy_trend_bp:
            hy_trend_4w = "widening"
        else:
            hy_trend_4w = "flat"

    credit_reasons: list[str] = []
    if hy_d20 is not None and hy_d20 >= 50.0:
        credit_reasons.append(f"HY OAS +{hy_d20}bp over 4w (>=+50bp)")
    if hy_pct is not None and hy_pct >= 90:
        credit_reasons.append(f"HY OAS at {hy_pct}th pct of 90d (>=90th)")
    if ig_d20 is not None and ig_d20 >= 25.0:
        credit_reasons.append(f"IG OAS +{ig_d20}bp over 4w (>=+25bp)")

    out["credit"] = {
        "hy_oas": {
            "latest": hy_latest,
            "delta_5d_bp": _delta_bp(hy, 5),
            "delta_20d_bp": hy_d20,
            "pct_rank_90d": hy_pct,
            "trend_4w": hy_trend_4w,
            "as_of": _latest_date("BAMLH0A0HYM2"),
        },
        "ig_oas": {
            "latest": ig_latest,
            "delta_5d_bp": _delta_bp(ig, 5),
            "delta_20d_bp": ig_d20,
            "pct_rank_90d": ig_pct,
        },
        "credit_stress": {
            "flag": bool(credit_reasons),
            "reasons": credit_reasons,
        },
        "hy_threshold_notes": (
            "<3.5 complacency; 3.5-5.0 normal; 5.0-7.0 stress; "
            "7.0-10.0 crisis; >10.0 panic (units: %)"
        ),
    }

    # --- 3. Breakevens -----------------------------------------------------
    t5y   = _vals("T5YIE")
    t10y  = _vals("T10YIE")
    t5y5y = _vals("T5YIFR")

    out["breakevens"] = {
        "be_5y":  {"latest": _latest(t5y),   "delta_20d_bp": _delta_bp(t5y, 20),
                   "as_of": _latest_date("T5YIE")},
        "be_10y": {"latest": _latest(t10y),  "delta_20d_bp": _delta_bp(t10y, 20),
                   "as_of": _latest_date("T10YIE")},
        "be_5y5y": {"latest": _latest(t5y5y), "delta_20d_bp": _delta_bp(t5y5y, 20),
                    "as_of": _latest_date("T5YIFR")},
    }

    # --- 4. Systemic stress proxies ----------------------------------------
    mortg = _vals("MORTGAGE30US")
    real10 = _vals("DFII10")

    mbs_spread_latest = None
    mbs_spread_d20 = None
    if mortg and dgs10:
        mbs_spread_latest = round(mortg[0] - dgs10[0], 3)
        if len(mortg) > 4 and len(dgs10) > 20:
            # mortgage is weekly so ~4 obs ≈ 4 weeks; pair with 20d DGS10
            prior = mortg[4] - dgs10[20]
            mbs_spread_d20 = round((mbs_spread_latest - prior) * 100.0, 1)

    out["systemic"] = {
        "mbs_spread_proxy": mbs_spread_latest,
        "mbs_spread_delta_20d_bp": mbs_spread_d20,
        "real_yield_10y": _latest(real10),
        "real_yield_10y_delta_20d_bp": _delta_bp(real10, 20),
        "mbs_notes": "MORTGAGE30US - DGS10; historical avg 50-80bp, >130bp = stretched",
    }

    # --- 5. Four-signal scorecard -----------------------------------------
    def _score_curve() -> int:
        if curve_3m10y_latest is None:
            return 0
        # Recession warning territory
        if curve_3m10y_latest < 0 and curve_2s10s_latest is not None and curve_2s10s_latest < 0.20:
            return -2
        if curve_3m10y_latest < 0:
            return -1
        if curve_2s10s_latest is not None and curve_2s10s_latest > 1.0 and regime in ("bull_steepening",):
            return 2
        if curve_2s10s_latest is not None and curve_2s10s_latest > 0.5:
            return 1
        return 0

    def _score_credit() -> int:
        if hy_latest is None:
            return 0
        # HY OAS thresholds in %: <3.5 complacency, 3.5-5 normal, >5 stress
        if credit_reasons:
            return -2 if hy_d20 is not None and hy_d20 >= 75.0 else -1
        if hy_latest >= 7.0:
            return -2
        if hy_latest >= 5.0:
            return -1
        if hy_latest < 3.5 and hy_d20 is not None and hy_d20 > 10.0:
            return -1  # complacency + starting to widen
        if hy_latest < 3.5:
            return 0   # tight & stable -- no juice, but no warning yet
        return 1       # normal range, stable

    def _score_breakevens() -> int:
        b = _latest(t5y5y) or _latest(t10y)
        d = _delta_bp(t5y5y, 20) if t5y5y else _delta_bp(t10y, 20)
        if b is None or d is None:
            return 0
        if abs(d) >= 30.0:
            return -2  # fast move in either direction = regime shift risk
        if abs(d) >= 15.0:
            return -1
        if 2.0 <= b <= 2.6:
            return 1
        return 0

    def _score_systemic() -> int:
        if mbs_spread_latest is None:
            return 0
        if mbs_spread_d20 is not None and mbs_spread_d20 >= 30.0:
            return -2
        if mbs_spread_latest >= 1.5:
            return -1
        if mbs_spread_latest <= 0.8:
            return 1
        return 0

    s_curve  = _score_curve()
    s_credit = _score_credit()
    s_be     = _score_breakevens()
    s_sys    = _score_systemic()
    composite = s_curve + s_credit + s_be + s_sys

    if composite >= 4:
        label = "risk_on"
    elif composite >= 0:
        label = "neutral"
    elif composite >= -4:
        label = "defensive"
    else:
        label = "acute_defensive"

    out["scorecard"] = {
        "yield_curve":  s_curve,
        "credit":       s_credit,
        "breakevens":   s_be,
        "systemic":     s_sys,
        "composite":    composite,
        "label":        label,
        "scale":        "-8..+8; <=-5 acute_defensive, -4..-1 defensive, 0..+3 neutral, >=+4 risk_on",
    }

    return out


def _build_labor_leading(macro_data: dict) -> dict:
    """O3 (2026-08-06 audit) — deterministic LEADING labor sub-signal: ADP
    private payrolls (FRED ``NPPTTL``), surfaced explicitly as a forward-risk
    flag. This is a LEADING indicator (available days before BLS PAYEMS) and
    NEVER overwrites the binding PAYEMS scorecard in ``_build_labor_signals``
    — it is additive context only.

    Motivating incident: 2026-08-05's ADP miss (+44K vs +70K consensus) — the
    session's most important forward labor signal — was caught only because
    the analyzer happened to parse it out of the forex-news feed; there was no
    deterministic field for it at all.

    Degrades gracefully (``available: False``) when ``NPPTTL`` has fewer than
    2 usable observations (series absent, renamed, or not yet fetched) —
    never a crash, never a fabricated reading.
    """
    vals = _macro_vals(macro_data, "NPPTTL")   # newest-first, thousands of persons
    if len(vals) < 2:
        return {"available": False, "reason": "NPPTTL (ADP) series absent or insufficient history"}

    delta_1m_k = round(vals[0] - vals[1], 1)
    delta_3m_avg_k = round((vals[0] - vals[3]) / 3.0, 1) if len(vals) > 3 else None

    gap_k = float((_load_risk_limits().get("labor_leading") or {}).get("forward_softening_gap_k", 20.0))
    forward_softening_flag = (
        delta_3m_avg_k is not None and delta_1m_k < delta_3m_avg_k - gap_k
    )

    return {
        "available": True,
        "source": "FRED_NPPTTL",
        "latest_k": round(vals[0], 0),
        "delta_1m_k": delta_1m_k,
        "delta_3m_avg_k": delta_3m_avg_k,
        "forward_softening_flag": forward_softening_flag,
        "note": (
            "ADP private-payrolls LEADING signal (forward risk only) — never overwrites "
            "the binding BLS-PAYEMS scorecard; feeds the labor read as additive context."
        ),
    }


def _build_labor_signals(macro_data: dict) -> dict:
    """Pre-compute a four-signal labor-market scorecard for the analyzer.

    Inputs from FRED ``macro_data`` (deep-history fetched in ``run()``):
      ICSA, CCSA               weekly (~60 obs)
      PAYEMS, UNRATE           monthly (~24 obs)
      CES0500000003            monthly avg hourly earnings ($)
      JTSJOL                   monthly job openings
      CIVPART                  monthly labor force participation
      SAHMREALTIME             monthly Sahm Rule indicator
      DFF                      daily Fed funds (already deep-fetched for bonds)

    Output sections (mirrors bond_signals shape):
      claims:        ICSA latest + 4w avg + 4w vs 26w avg % change; CCSA latest
      payrolls:      PAYEMS 1m / 3m / 6m monthly deltas (in thousands)
      unemployment:  UNRATE latest + 6m delta (pp); Sahm Rule + flag
      wages:         CES YoY%; JTSJOL latest + 3m delta; CIVPART latest + 6m delta

    Composite scorecard: each signal -2..+2, composite -8..+8 with label
      ``labor_strong`` (>=+4) / ``neutral`` (0..+3) / ``labor_softening``
      (-1..-4) / ``labor_breaking`` (<=-5).

    Labor data leads recessions: jobless claims and Sahm Rule turn before
    GDP. Treat as cross-confirmation with bond_signals — when claims and
    HY OAS both deteriorate, defensive posture is warranted regardless of
    yield-curve regime.
    """
    out: dict = {
        "claims":       {},
        "payrolls":     {},
        "unemployment": {},
        "wages":        {},
        "scorecard":    {},
        "leading":      _build_labor_leading(macro_data),
        "notes": (
            "Labor leads the cycle. ICSA 4w rising >10% vs 26w avg, "
            "SAHMREALTIME >=0.5, or PAYEMS 3m avg <100k are early-warning "
            "signals. Combine with bond_signals.credit_stress for confluence."
        ),
    }

    def _vals(sid: str) -> list[float]:
        rows = macro_data.get(sid) or []
        out_vals: list[float] = []
        for r in rows:
            v = r.get("value")
            if v in (None, ".", ""):
                continue
            try:
                out_vals.append(float(v))
            except (TypeError, ValueError):
                continue
        return out_vals

    def _latest(vals: list[float], digits: int = 2) -> float | None:
        return round(vals[0], digits) if vals else None

    def _avg(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 2) if vals else None

    def _monthly_delta_k(vals: list[float], n: int) -> float | None:
        """Average monthly change over n months, in thousands of persons.

        PAYEMS arrives from FRED already in thousands, so no /1000 here.
        """
        if len(vals) <= n:
            return None
        diffs = [(vals[i] - vals[i + 1]) for i in range(n)]
        return round(sum(diffs) / n, 1)

    # --- 1. Jobless claims --------------------------------------------------
    icsa = _vals("ICSA")
    ccsa = _vals("CCSA")

    icsa_4w  = _avg(icsa[:4])  if len(icsa) >= 4  else None
    icsa_26w = _avg(icsa[:26]) if len(icsa) >= 26 else None
    icsa_pct_vs_26w = None
    if icsa_4w is not None and icsa_26w not in (None, 0):
        icsa_pct_vs_26w = round(100.0 * (icsa_4w - icsa_26w) / icsa_26w, 1)

    out["claims"] = {
        "icsa_latest":          _latest(icsa, 0),
        "icsa_4w_avg":          icsa_4w,
        "icsa_26w_avg":         icsa_26w,
        "icsa_4w_vs_26w_pct":   icsa_pct_vs_26w,
        "ccsa_latest":          _latest(ccsa, 0),
        "ccsa_4w_avg":          _avg(ccsa[:4]) if len(ccsa) >= 4 else None,
    }

    # --- 2. Payrolls momentum ----------------------------------------------
    payems = _vals("PAYEMS")
    out["payrolls"] = {
        "payems_latest_k":  round(payems[0], 0) if payems else None,
        "delta_1m_k":       _monthly_delta_k(payems, 1),
        "delta_3m_avg_k":   _monthly_delta_k(payems, 3),
        "delta_6m_avg_k":   _monthly_delta_k(payems, 6),
    }

    # --- 3. Unemployment + Sahm --------------------------------------------
    unrate = _vals("UNRATE")
    sahm   = _vals("SAHMREALTIME")
    civpart = _vals("CIVPART")

    unrate_6m_delta_pp = None
    if len(unrate) > 6:
        unrate_6m_delta_pp = round(unrate[0] - unrate[6], 2)

    civpart_6m_delta_pp = None
    if len(civpart) > 6:
        civpart_6m_delta_pp = round(civpart[0] - civpart[6], 2)

    sahm_latest = _latest(sahm, 2)
    out["unemployment"] = {
        "unrate_latest":            _latest(unrate, 2),
        "unrate_delta_6m_pp":       unrate_6m_delta_pp,
        "sahm_latest":              sahm_latest,
        "sahm_triggered":           bool(sahm_latest is not None and sahm_latest >= 0.5),
        "civpart_latest":           _latest(civpart, 2),
        "civpart_delta_6m_pp":      civpart_6m_delta_pp,
        "sahm_notes":               "Sahm Rule triggers at >=0.5pp; historically coincides with recession start",
    }

    # --- 4. Wages + JOLTS --------------------------------------------------
    ces = _vals("CES0500000003")
    jolts = _vals("JTSJOL")
    dff = _vals("DFF")

    wage_yoy_pct = None
    if len(ces) > 12 and ces[12] not in (None, 0):
        wage_yoy_pct = round(100.0 * (ces[0] - ces[12]) / ces[12], 2)

    jolts_3m_delta_k = None
    if len(jolts) > 3:
        jolts_3m_delta_k = round((jolts[0] - jolts[3]), 0)  # already in thousands

    out["wages"] = {
        "ahe_latest":           _latest(ces, 2),
        "ahe_yoy_pct":          wage_yoy_pct,
        "jolts_openings_k":     _latest(jolts, 0),
        "jolts_delta_3m_k":     jolts_3m_delta_k,
        "fed_funds_latest":     _latest(dff, 2),
    }

    # --- 5. Four-signal scorecard ------------------------------------------
    def _score_claims() -> int:
        if icsa_pct_vs_26w is None:
            return 0
        if icsa_pct_vs_26w >= 10.0:
            return -2
        if icsa_pct_vs_26w >= 5.0:
            return -1
        if icsa_pct_vs_26w <= -5.0:
            return 1
        return 0

    def _score_payrolls() -> int:
        d3 = out["payrolls"]["delta_3m_avg_k"]
        if d3 is None:
            return 0
        if d3 < 0:
            return -2
        if d3 < 100.0:
            return -1
        if d3 >= 200.0:
            return 1
        return 0

    def _score_unemployment() -> int:
        if sahm_latest is not None and sahm_latest >= 0.5:
            return -2
        if sahm_latest is not None and sahm_latest >= 0.3:
            return -1
        if unrate_6m_delta_pp is not None and unrate_6m_delta_pp >= 0.4:
            return -1
        if unrate_6m_delta_pp is not None and unrate_6m_delta_pp <= -0.2:
            return 1
        return 0

    def _score_wages() -> int:
        # Hawkish Fed risk if wages hot AND policy already restrictive
        w = wage_yoy_pct
        f = _latest(dff, 2)
        if w is None:
            return 0
        if w >= 4.5 and (f is not None and f >= 4.0):
            return -1
        if w >= 5.0:
            return -1
        if w <= 3.0 and (f is not None and f >= 4.0):
            return 1   # disinflation in wages + restrictive policy = cuts coming
        if 3.0 < w < 4.0:
            return 1
        return 0

    s_claims = _score_claims()
    s_pay    = _score_payrolls()
    s_unemp  = _score_unemployment()
    s_wages  = _score_wages()
    composite = s_claims + s_pay + s_unemp + s_wages

    if composite >= 4:
        label = "labor_strong"
    elif composite >= 0:
        label = "neutral"
    elif composite >= -4:
        label = "labor_softening"
    else:
        label = "labor_breaking"

    out["scorecard"] = {
        "claims":       s_claims,
        "payrolls":     s_pay,
        "unemployment": s_unemp,
        "wages":        s_wages,
        "composite":    composite,
        "label":        label,
        "scale":        "-8..+8; <=-5 labor_breaking, -4..-1 labor_softening, 0..+3 neutral, >=+4 labor_strong",
    }

    return out


def _macro_vals(macro_data: dict, sid: str) -> list[float]:
    """Latest-first float list for a FRED series (shared idiom; drops '.'/None)."""
    rows = macro_data.get(sid) or []
    vals: list[float] = []
    for r in rows:
        v = r.get("value")
        if v in (None, ".", ""):
            continue
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            continue
    return vals


def _gdpnow_vintage_rows(rows: list, obs_date: str) -> list[dict]:
    """One quarter's nowcast revisions from an ALFRED vintage response: the rows whose
    observation date is ``obs_date``, oldest-first as FRED returns them, '.'/empty
    values dropped. Pure — the fetch stays in the orchestration layer."""
    return [
        {"date": r.get("date"), "asof": r.get("realtime_start"), "value": r.get("value")}
        for r in (rows or [])
        if r.get("date") == obs_date and r.get("value") not in (None, ".", "")
    ]


def _r5_from_closes(close_map: dict[str, float]) -> float | None:
    """Point-to-point % return over the last ``_FLEX_TIEBREAK_WINDOW_D`` trading
    days from a ``{date: close}`` map (latest close vs the close 5 trading days
    earlier). ``None`` when there is not enough history."""
    if not close_map:
        return None
    dates = sorted(close_map)
    if len(dates) < _FLEX_TIEBREAK_WINDOW_D + 1:
        return None
    latest = close_map[dates[-1]]
    prior = close_map[dates[-1 - _FLEX_TIEBREAK_WINDOW_D]]
    if not prior:
        return None
    return round((latest / prior - 1.0) * 100.0, 4)


def _build_flex_quadrant(growth_axis: dict, inflation_axis: dict,
                         closes_cache: dict[str, dict[str, float]] | None) -> dict:
    """Deterministic resolution of the quadrant the FLEX engine treats as in force
    (decision D1, 2026-07-21). An indeterminate ``active_quadrant`` must NOT freeze
    the flex sleeve: when the favored bucket is a 2-quadrant union (e.g. Q3/Q4) it
    resolves to the member with the better trailing 5-trading-day benchmark return.

    Zero extra FMP calls: the four ``QUADRANT_BENCHMARK_ETF`` names (QQQ/XLI/GLD/TLT)
    are all scorecard pool members, so their closes are already in ``closes_cache``
    (the cache ``_sleeve_selection_metrics`` populated). A benchmark missing from the
    cache → ``resolved: ""``, ``basis: "unresolved"`` (fail-closed). Non-fatal in the
    caller. See ``flex.regime.resolve_quadrant`` for the resolution rules."""
    g = (growth_axis or {}).get("direction")
    i = (inflation_axis or {}).get("direction")
    bucket = favored_bucket(g, i)
    cache = closes_cache or {}

    bench_returns_5d: dict[str, dict] = {}
    bench_r5: dict[str, float] = {}
    for q in bucket:
        etf = benchmark_etf_for(q)
        r5 = _r5_from_closes(cache.get(etf) or cache.get(etf.upper()) or {})
        bench_returns_5d[q] = {"etf": etf, "r5": r5}
        if r5 is not None:
            bench_r5[q] = r5

    resolved, basis = resolve_quadrant(g, i, bench_r5 or None)

    if basis == "unresolved" and not bucket:
        note = "No directional read (growth flat/unknown) — flex fails closed."
    elif basis == "unresolved":
        missing = [q for q in bucket if q not in bench_r5]
        note = (f"Borderline {bucket} but the trailing {_FLEX_TIEBREAK_WINDOW_D}d "
                f"benchmark return is unavailable for {missing or bucket} — "
                "fail-closed, never guess.")
    elif basis == "borderline_5d_tiebreak":
        note = (f"Borderline {bucket}: resolved to {resolved} on the better trailing "
                f"{_FLEX_TIEBREAK_WINDOW_D}d benchmark return.")
    elif basis == "favored_single":
        note = f"Single-quadrant favored bucket {bucket} → {resolved}."
    else:
        note = f"Both axes pinned → active quadrant {resolved}."

    return {
        "resolved": resolved,
        "basis": basis,
        "favored_bucket": bucket,
        "benchmark_returns_5d": bench_returns_5d,
        "window_trading_days": _FLEX_TIEBREAK_WINDOW_D,
        "note": note,
    }


_AXIS_STATE_TABLE = "AxisDirectionState"


def _load_axis_direction_state() -> dict:
    """Per-axis confirmation state (`growth`/`inflation`/`policy`) from Table
    Storage (session 2026-07-28, Task A, decision D-2). Mirrors
    `_load_sleeve_streak_state` exactly — same table-per-row shape, same
    non-fatal-on-read-failure caller contract (an empty `{}` is a valid "first
    run" input to `_confirm_axis_direction`, never a crash)."""
    state: dict = {}
    for e in query_entities(_AXIS_STATE_TABLE):
        rid = e.get("RowKey")
        if rid:
            state[rid] = {
                "raw_direction": (e.get("raw_direction") or None) or None,
                "confirmed_direction": (e.get("confirmed_direction") or None) or None,
                "raw_streak": int(e.get("raw_streak") or 0),
                "confirmed_as_of": (e.get("confirmed_as_of") or None) or None,
            }
    return state


def _save_axis_direction_state(new_state: dict) -> None:
    for rid, s in (new_state or {}).items():
        upsert_entity(_AXIS_STATE_TABLE, {
            "PartitionKey": "state",
            "RowKey": rid,
            "raw_direction": s.get("raw_direction") or "",
            "confirmed_direction": s.get("confirmed_direction") or "",
            "raw_streak": int(s.get("raw_streak") or 0),
            "confirmed_as_of": s.get("confirmed_as_of") or "",
        })


def _confirm_axis_direction(raw_direction: str, prev: dict | None, today: str) -> dict:
    """N=2 direction-label confirmation (session 2026-07-28, Task A, decision D-2).

    Invariant being restored: nothing that moves real money (regime sizing via
    `reference_weights`, the deployment gate) acts on a direction label seen
    exactly once. A label CHANGE — to ANY value, including `flat` — only reaches
    the CONSUMED ``direction`` field after the raw classification has persisted
    for >= 2 consecutive runs. Member-selection already has an analogous
    hysteresis gate (`sleeve_selection`, 2.0pp lead / 10 runs); this is the same
    doctrine applied to regime sizing, which previously had none — the 2026-07-28
    growth-axis flip (falling -> rising, purely from the oldest GDPNow vintage
    aging out of the window while the newest print was actually LOWER) re-anchored
    the reference from 55.9% Q3 to 72.5% Q1+Q2 overnight on a single print.

    Returns ``{direction (confirmed), raw_direction, direction_pending, raw_streak,
    confirmed_as_of}``. ``direction`` is a drop-in replacement for the raw value in
    every existing consumer (`active_quadrant`, `reference_weights`,
    `regime_gate`, ...) — they read the SAME field name, now cushioned.

    D-A2 (first run / no persisted state): adopt the raw value as confirmed
    IMMEDIATELY (streak seeded at 2) — no artificial lag on deploy day, and no
    forced whipsaw back toward a stale prior regime the book was never actually
    holding (the rejected alternative: seeding a fake "prior raw" from an old
    snapshot would flip a live book's reference on deploy day for a decision that
    was never actually made with hysteresis in the first place).
    """
    if not prev:
        return {
            "direction": raw_direction,
            "raw_direction": raw_direction,
            "direction_pending": False,
            "raw_streak": 2,
            "confirmed_as_of": today,
        }
    prior_raw = prev.get("raw_direction")
    prior_confirmed = prev.get("confirmed_direction") or prior_raw
    prior_streak = int(prev.get("raw_streak") or 0)
    prior_as_of = prev.get("confirmed_as_of") or today

    streak = prior_streak + 1 if raw_direction == prior_raw else 1
    confirmed = raw_direction if streak >= 2 else prior_confirmed
    changed = confirmed != prior_confirmed
    return {
        "direction": confirmed,
        "raw_direction": raw_direction,
        "direction_pending": confirmed != raw_direction,
        "raw_streak": streak,
        "confirmed_as_of": today if changed else prior_as_of,
    }


def _load_prior_growth_axis(today: str) -> dict | None:
    """Prior trading day's `growth_axis` snapshot block (session 2026-07-28, Task A
    rolloff diagnostic) — the SAME non-fatal 7-day walkback as the Task-B-fixed
    `_build_series_deltas` (``read_snapshot`` raises on a missing blob, so each
    date is tried individually and skipped on failure rather than aborting the
    whole walkback)."""
    d0 = date.fromisoformat(today)
    for back in range(1, 8):
        d = (d0 - timedelta(days=back)).isoformat()
        try:
            snap = read_snapshot(d)
        except Exception:  # noqa: BLE001
            continue
        if snap and snap.get("growth_axis"):
            return snap["growth_axis"]
    return None


def _growth_rolloff_diagnostics(
    cur_trajectory: list[float] | None,
    prior_trajectory: list[float] | None,
    raw_direction: str | None,
    prior_raw_direction: str | None,
) -> dict | None:
    """Deterministic annotation for WHY the growth axis's raw classification
    flipped this run (session 2026-07-28, Task A). Only meaningful — and only
    called by the caller — when ``raw_direction != prior_raw_direction``.

    ``head_vintage_dropped``: true when the two trajectories are the same length
    and every entry but the oldest of ``prior_trajectory`` reappears, in order, as
    every entry but the newest of ``cur_trajectory`` — i.e. the window slid by
    exactly one vintage (one dropped off the head, one new one appended), not a
    genuinely new dataset. ``newest_vintage_delta``: signed change in the newest
    vintage's own value. ``attribution``: ``"window_rolloff"`` when the window
    slid AND the newest print's own delta sign does not support the flip
    direction (2026-07-28: flipped to rising while the newest print itself fell)
    — a pure windowing artifact, not new information; ``"new_print"`` otherwise;
    ``"indeterminate"`` when either trajectory is unavailable (no prior snapshot
    within the walkback window)."""
    if not cur_trajectory or not prior_trajectory:
        return {
            "head_vintage_dropped": False,
            "newest_vintage_delta": None,
            "attribution": "indeterminate",
        }
    head_vintage_dropped = (
        len(prior_trajectory) == len(cur_trajectory) >= 2
        and list(cur_trajectory[:-1]) == list(prior_trajectory[1:])
    )
    newest_delta = round(cur_trajectory[-1] - prior_trajectory[-1], 4)
    if head_vintage_dropped:
        flip_supported = (
            (raw_direction == "rising" and newest_delta > 0)
            or (raw_direction == "falling" and newest_delta < 0)
        )
        attribution = "new_print" if flip_supported else "window_rolloff"
    else:
        attribution = "new_print"
    return {
        "head_vintage_dropped": head_vintage_dropped,
        "newest_vintage_delta": newest_delta,
        "attribution": attribution,
    }


def _build_growth_axis(macro_data: dict) -> dict:
    """Deterministic growth-direction read — the quadrant *growth axis*, computed in
    Python so the analyzer ECHOES it (mirrors bond_signals/labor_signals) rather than
    re-deriving it from raw series (where a temperature-0.2 model rationalizes toward
    its prior label).

    Primary signal: the GDPNow *current-quarter vintage trajectory*
    (``GDPNOW_VINTAGES``, oldest-first) — the within-quarter nowcast revisions. The
    standard /observations endpoint hides this (one latest value per quarter), so a
    naive cross-quarter "slope" can read 'rising' while the live quarter is being
    marked down. Quarter boundary (FOLLOWUPS #15): with <3 current-quarter vintages
    but >=3 in ``GDPNOW_VINTAGES_PRIOR``, read the TAIL of the just-ended quarter's
    trajectory (``prior_quarter_tail``, medium confidence) — never an empty trajectory
    while FRED has vintages in the window. Fallback: cross-quarter GDPNOW slope (low
    confidence) only with <3 vintages in both; 'indeterminate' only with no GDPNow
    at all.
    """
    def _rows(key: str) -> list[dict]:
        return [
            r for r in (macro_data.get(key) or [])
            if r.get("value") not in (None, ".", "")
        ]  # oldest-first, each {date, asof, value}

    def _vals(key: str) -> list[float]:
        return [float(r["value"]) for r in _rows(key)]  # oldest-first

    traj = _vals("GDPNOW_VINTAGES")
    prior = _vals("GDPNOW_VINTAGES_PRIOR")   # the just-ended quarter
    traj_rows = _rows("GDPNOW_VINTAGES")
    prior_rows = _rows("GDPNOW_VINTAGES_PRIOR")

    BAND = 0.1
    PRIOR_TAIL_N = 6   # ~3 weeks of vintages — the recent slope, not the whole quarter
    confidence = "high"
    basis = "within_quarter_vintages"
    note = ""
    used = traj
    used_rows = traj_rows
    if len(traj) >= 3:
        first, last = traj[0], traj[-1]
        latest = last
    elif len(prior) >= 3:
        # Quarter-boundary splice (FOLLOWUPS #15): the new quarter warms up over
        # ~weeks while the Atlanta Fed is still revising the just-ended quarter —
        # read that trajectory's tail instead of degrading to the coarse fallback.
        used = prior[-PRIOR_TAIL_N:]
        used_rows = prior_rows[-PRIOR_TAIL_N:]
        first, last = used[0], used[-1]
        latest = last
        confidence = "medium"
        basis = "prior_quarter_tail"
        note = (
            f"Quarter boundary: only {len(traj)} current-quarter vintage(s) so far — "
            "direction read from the just-ended quarter's nowcast tail at medium "
            "confidence until the new quarter has >=3 vintages of its own."
        )
    else:
        # Fallback: cross-quarter quarterly prints (newest-first from get_series_latest)
        q = _macro_vals(macro_data, "GDPNOW")  # newest-first
        if len(q) >= 2:
            first, last = q[1], q[0]   # prior quarter -> latest quarter
            latest = q[0]
            confidence = "low"
            basis = "cross_quarter_fallback"
        else:
            return {
                "direction": "indeterminate",
                "confidence": "none",
                "basis": "no_gdpnow_data",
                "as_of": None,
                "gdpnow_latest": None,
                "gdpnow_trajectory": traj,
                "gdpnow_vintage_count": len(traj),
                "confirming": {},
                "note": (
                    "INDETERMINATE: no GDPNow data — the deployment gate must NOT "
                    "assert 'rising' and should fail closed on the growth axis."
                ),
            }

    if last > first + BAND:
        direction = "rising"
    elif last < first - BAND:
        direction = "falling"
    else:
        direction = "flat"

    pay = _macro_vals(macro_data, "PAYEMS")            # 000s, level; newest-first
    pay_3m = round((pay[0] - pay[3]) / 3.0, 1) if len(pay) > 3 else None
    claims = _macro_vals(macro_data, "ICSA")
    retail = _macro_vals(macro_data, "RSAFS")
    retail_dir = (
        "up" if len(retail) > 1 and retail[0] > retail[1]
        else "down" if len(retail) > 1 else None
    )

    if direction == "rising" and confidence == "low":
        note = (
            "Cross-quarter fallback only (no within-quarter vintages) — 'rising' is "
            "the coarse Q/Q comparison and may hide an in-quarter markdown; treat as "
            "low confidence."
        )

    # Freshness for GDPNow is *vintage recency* (the realtime `asof` of the newest
    # vintage row actually used), NOT the observation-quarter start date — the vintage
    # rows carry both, and letting the model pick produced the 07-20/21 as-of flip
    # (same value dated "2026-07-17, 3d" then "2026-04-01, 81d"). B4, 2026-07-21.
    as_of = used_rows[-1].get("asof") if used_rows else None

    return {
        "direction": direction,
        "confidence": confidence,
        "basis": basis,
        "as_of": as_of,
        "gdpnow_latest": round(latest, 2),
        "gdpnow_trajectory": [round(v, 2) for v in used],   # oldest -> newest
        "gdpnow_vintage_count": len(used),
        "confirming": {
            "payrolls_3m_avg_k": pay_3m,
            "initial_claims_latest_k": round(claims[0] / 1000.0, 1) if claims else None,
            "retail_sales_dir": retail_dir,
        },
        "note": note,
    }


def _fresh_oil_20d_pct(close_cache: dict[str, float] | None) -> tuple[float | None, str | None]:
    """20-trading-day % change from a ``{date: close}`` cache (e.g. USO) — the
    O2 (2026-08-06 audit) fresher-oil-proxy source for the inflation-axis energy
    overlay. Returns ``(pct_change, as_of_date)``; ``(None, None)`` when the
    cache is empty or thinner than 21 trading days."""
    if not close_cache:
        return None, None
    dates = sorted(close_cache, reverse=True)
    if len(dates) <= 20:
        return None, None
    latest, past = close_cache[dates[0]], close_cache[dates[20]]
    if not past:
        return None, dates[0]
    return round((latest / past - 1.0) * 100.0, 1), dates[0]


def _build_inflation_axis(macro_data: dict, oil_proxy_close_cache: dict[str, float] | None = None) -> dict:
    """Deterministic inflation-direction read — the quadrant *inflation axis*.

    Realized core (PCE-first, then CPI) governs via the 3-month-annualized-vs-YoY
    trend. Headline CPI is the energy channel: when headline is elevated AND rising
    AND oil is *also* rising, that is genuine energy inflation -> 'rising'. But when
    headline is elevated while oil is collapsing (the rear-view artifact of a prior
    oil spike), the headline is about to roll over -> do NOT force 'rising'; classify
    by core and flag the pending disinflation. Breakevens are secondary (expectations).

    NOTE: the energy overlay keys off the *actual oil price trend*, NOT the
    news-keyword ``market_shock`` level — the shock detector is a headline-count
    signal prone to false positives, and binding realized-inflation direction to
    it would hard-wire stagflation off a news flurry.

    O2 (2026-08-06 audit): FRED's DCOILWTICO/DCOILBRENTEU run 8-9d stale every
    session — on the exact channel that can flip this axis to "rising".
    ``oil_proxy_close_cache`` (optional, a liquid daily-traded proxy like USO,
    fetched by the caller via ``_close_by_date``) sources the 20-session trend
    when available (>20 trading days of history); FRED is the fallback when it
    isn't. The overlay RULE is unchanged either way — it keys on the price
    trend, never the news-shock level.
    """
    def _yoy(sid: str, base: int = 0) -> float | None:
        v = _macro_vals(macro_data, sid)
        return round((v[base] / v[base + 12] - 1) * 100, 2) if len(v) > base + 12 else None

    def _ann3(sid: str) -> float | None:
        v = _macro_vals(macro_data, sid)
        return round(((v[0] / v[3]) ** 4 - 1) * 100, 2) if len(v) > 3 else None

    def _oil_20d_pct(sid: str) -> float | None:
        v = _macro_vals(macro_data, sid)   # newest-first
        return round((v[0] / v[20] - 1) * 100, 1) if len(v) > 20 else None

    def _bp_delta_20d(sid: str) -> float | None:
        v = _macro_vals(macro_data, sid)   # newest-first, in percentage points
        return round((v[0] - v[20]) * 100.0, 1) if len(v) > 20 else None

    def _newest_date(sid: str) -> str | None:
        rows = macro_data.get(sid) or []
        for r in rows:
            if r.get("value") not in (None, ".", ""):
                return r.get("date")
        return None

    head_yoy = _yoy("CPIAUCSL")
    head_yoy_prev = _yoy("CPIAUCSL", base=1)
    core_cpi_yoy = _yoy("CPILFESL")
    core_pce_yoy = _yoy("PCEPILFE")
    core_cpi_ann3 = _ann3("CPILFESL")
    core_pce_ann3 = _ann3("PCEPILFE")
    be_5y5y = _macro_vals(macro_data, "T5YIFR")

    head_rising = (
        head_yoy is not None and head_yoy_prev is not None and head_yoy >= head_yoy_prev
    )
    oil_wti_20d = _oil_20d_pct("DCOILWTICO")
    oil_brent_20d = _oil_20d_pct("DCOILBRENTEU")
    oil_proxy_20d, oil_proxy_as_of = _fresh_oil_20d_pct(oil_proxy_close_cache)
    if oil_proxy_20d is not None:
        oil_trend_source = "USO_proxy"
        oil_chgs = [oil_proxy_20d]
    else:
        oil_trend_source = "fred_futures"
        oil_chgs = [c for c in (oil_wti_20d, oil_brent_20d) if c is not None]
    oil_rising = bool(oil_chgs) and max(oil_chgs) >= 10.0      # genuine energy push
    oil_falling = bool(oil_chgs) and min(oil_chgs) <= -10.0    # spike reversing

    # B1 (2026-08-14 audit): the SINGLE governing oil reading — whichever source
    # this axis actually used above — under a source-neutral name so downstream
    # consumers (the leading_vs_lagging_inflation divergence) echo the axis's own
    # resolution instead of re-deriving it and independently reading the stale
    # FRED leg directly (the 2026-08-12 whipsaw: the axis itself correctly
    # preferred the fresh USO proxy at 6.2%, but the divergence detector still
    # read raw `oil_wti_20d_pct` — stale FRED WTI at 17.8% — and fired on it).
    if oil_trend_source == "USO_proxy":
        oil_20d_pct_governing = oil_proxy_20d
        oil_20d_pct_governing_as_of = oil_proxy_as_of
    else:
        oil_20d_pct_governing = oil_wti_20d
        oil_20d_pct_governing_as_of = _newest_date("DCOILWTICO")

    headline_hot = head_yoy is not None and head_yoy >= 3.5 and head_rising

    # classify by realized core trend (3m annualized vs YoY); PCE-first
    ref_ann3 = core_pce_ann3 if core_pce_ann3 is not None else core_cpi_ann3
    ref_yoy = core_pce_yoy if core_pce_yoy is not None else core_cpi_yoy
    # B2: the realized leg's as_of tracks whichever series actually governed
    # (PCEPILFE preferred, CPILFESL fallback) — a monthly-cadence print, so
    # downstream staleness gating must use the freshness block's monthly
    # threshold, never the flat daily `staleness_days`.
    realized_core_as_of = _newest_date("PCEPILFE") if core_pce_ann3 is not None else _newest_date("CPILFESL")

    if headline_hot and oil_rising:
        direction = "rising"
        reason = "headline elevated & rising with oil rising (active energy push)"
    elif ref_ann3 is None or ref_yoy is None:
        direction = "indeterminate"
        reason = "insufficient realized core history"
    elif ref_ann3 > ref_yoy + 0.2:
        direction = "rising"
        reason = "core 3m-annualized accelerating above YoY"
    elif ref_ann3 < ref_yoy - 0.2:
        direction = "falling"
        reason = "core 3m-annualized below YoY"
    else:
        direction = "flat"
        reason = "core 3m-annualized ~ YoY (sticky)"

    note = (
        "Realized core governs; breakevens are secondary. Energy overlay keys on the "
        "oil price trend, not the news-shock level."
    )
    if headline_hot and oil_falling:
        note = (
            f"Headline CPI elevated/rising ({head_yoy}% YoY) but oil reversing "
            f"(WTI {oil_wti_20d}% / Brent {oil_brent_20d}% 20d) — the headline is a "
            f"rear-view artifact of a prior oil spike; disinflation pending. "
            f"Classified by realized core."
        )

    # --- O1 (2026-08-06 audit): secondary, NON-BINDING breakeven bridge -------
    # Core CPI/PCE are monthly (60-65d stale for most of the window between
    # prints) — this promotes the already-fresh breakeven series (T5YIFR/
    # T5YIE) to an explicit bridge read so the analyzer has SOMETHING current
    # between prints. Reuses the SAME bp threshold the leading_vs_lagging_
    # inflation divergence already uses (no new magic number). realized core
    # still governs `direction` above — this block never touches it.
    be_5y_delta = _bp_delta_20d("T5YIE")
    be_10y_delta = _bp_delta_20d("T10YIE")
    be_5y5y_delta = _bp_delta_20d("T5YIFR")
    _be_thr = float(
        (_load_divergence_config().get("leading_vs_lagging_inflation") or {})
        .get("breakeven_delta_20d_bp", 15.0)
    )
    # Prefer the 5y5y forward breakeven (least contaminated by near-term noise,
    # the same series the leading_vs_lagging_inflation divergence keys on);
    # fall back to the 5y spot breakeven when 5y5y lacks history.
    if be_5y5y_delta is not None:
        bridge_basis, bridge_delta = "breakeven_5y5y", be_5y5y_delta
    elif be_5y_delta is not None:
        bridge_basis, bridge_delta = "breakeven_5y", be_5y_delta
    else:
        bridge_basis, bridge_delta = None, None

    if bridge_delta is None:
        bridge_direction = None
    elif bridge_delta > _be_thr:
        bridge_direction = "rising"
    elif bridge_delta < -_be_thr:
        bridge_direction = "falling"
    else:
        bridge_direction = "flat"

    return {
        "direction": direction,
        "reason": reason,
        "headline_cpi_yoy": head_yoy,
        "headline_cpi_rising": head_rising,
        "core_cpi_yoy": core_cpi_yoy,
        "core_pce_yoy": core_pce_yoy,
        "core_cpi_ann3": core_cpi_ann3,
        "core_pce_ann3": core_pce_ann3,
        "oil_wti_20d_pct": oil_wti_20d,
        "oil_brent_20d_pct": oil_brent_20d,
        "oil_trend_source": oil_trend_source,
        "oil_proxy_20d_pct": oil_proxy_20d,
        "oil_proxy_as_of": oil_proxy_as_of,
        "oil_20d_pct_governing": oil_20d_pct_governing,
        "oil_20d_pct_governing_as_of": oil_20d_pct_governing_as_of,
        "realized_core_as_of": realized_core_as_of,
        "breakeven_5y5y": be_5y5y[0] if be_5y5y else None,
        "bridge_direction": bridge_direction,
        "bridge_basis": bridge_basis,
        "bridge_delta_20d_bp": bridge_delta,
        "bridge_delta_20d_bp_threshold": _be_thr,
        "breakeven_5y_delta_20d_bp": be_5y_delta,
        "breakeven_10y_delta_20d_bp": be_10y_delta,
        "breakeven_5y5y_delta_20d_bp": be_5y5y_delta,
        "realized_governs": True,
        "note": note,
        "bridge_note": (
            "bridge_direction is a SECONDARY, NON-BINDING read off the already-fresh "
            "breakeven series, meant to bridge the 60-65d gap between monthly core "
            "CPI/PCE prints — it never overrides `direction`, which realized core "
            "always governs."
        ),
    }


def _build_policy_axis(
    macro_data: dict, manual_stance: dict, cfg: dict, today: str,
    prev_state: dict | None = None,
) -> dict:
    """Deterministic policy stance — the classifier's *policy leg*, resolved from two
    layers (FOLLOWUPS #16). Before this, policy came only from the manually-maintained
    fomc-stance.json, which sat `unconfirmed` with a null `as_of` since inception — the
    gate was STRUCTURALLY unable to confirm Q1 until a human edited a JSON file, and
    "policy unconfirmed" inflated the conviction proxy daily.

    Layer 1 (override): the manual SEP/dot-plot stance GOVERNS while fresh (`as_of`
    within `manual_fresh_days`) — a real dot-plot beats a market proxy, and applies
    SAME-DAY (a real central-bank decision is an actual print, not a windowed-series
    artifact — exempt from Task A's confirmation gate below, same doctrine as D-G2's
    "fresh authoritative input adopted immediately"). Layer 2: the market-implied
    stance from DGS2 20-session momentum (front-end repricing of the policy path;
    DGS2/DFF already fetched at limit=90) governs when the manual file is stale or
    null. `unconfirmed` only when BOTH layers are unavailable — rare by construction.
    Gate semantics unchanged: fail-closed on hawkish, unconfirmed cannot confirm Q1.
    Thresholds in risk-limits.json -> policy_axis (no magic numbers).

    Session 2026-07-28 (Task A, decision D-A1): the MARKET-IMPLIED stance gets the
    same N=2 confirmation as the growth/inflation axes (`_confirm_axis_direction`)
    — a gate open/close moves real money, and a one-print stance flip off DGS2 noise
    is the same artifact class the axis confirmation targets. `prev_state` (the
    persisted `AxisDirectionState` row for `policy`, or None on the first run / a
    read failure — D-A2 applies identically: adopt immediately, no lag) drives the
    confirmation; it is a no-op when the manual-fresh layer governs. Adds
    `raw_stance`/`stance_pending`/`raw_streak` alongside the existing `stance` field.
    Pure — echo-not-re-derive; the fetch stays in orchestration.
    """
    pa_cfg = cfg.get("policy_axis") or _RISK_LIMITS_DEFAULTS["policy_axis"]
    hawk_bp = float(pa_cfg.get("dgs2_delta_20d_bp_hawkish", 20.0))
    dove_bp = float(pa_cfg.get("dgs2_delta_20d_bp_dovish", 20.0))
    fresh_days = int(pa_cfg.get("manual_fresh_days", 45))

    dgs2 = _macro_vals(macro_data, "DGS2")   # newest-first
    dff = _macro_vals(macro_data, "DFF")
    mi_stance = None
    delta_bp = None
    if len(dgs2) > 20:   # observation-index convention, same as the oil 20d pattern
        delta_bp = round((dgs2[0] - dgs2[20]) * 100, 1)
        if delta_bp >= hawk_bp:
            mi_stance = "hawkish"
        elif delta_bp <= -dove_bp:
            mi_stance = "dovish"
        else:
            mi_stance = "neutral"
    spread_bp = round((dgs2[0] - dff[0]) * 100, 1) if dgs2 and dff else None

    m_stance = (manual_stance or {}).get("stance", "unconfirmed")
    as_of = (manual_stance or {}).get("as_of")
    fresh = False
    if m_stance in ("hawkish", "neutral", "dovish") and as_of:
        try:
            age_days = (
                date.fromisoformat(str(today)[:10]) - date.fromisoformat(str(as_of)[:10])
            ).days
            fresh = age_days <= fresh_days
        except ValueError:
            fresh = False

    agreement = None
    if mi_stance and m_stance in ("hawkish", "neutral", "dovish"):
        agreement = mi_stance == m_stance

    # Task A (2026-07-28): confirm the market-implied stance regardless of whether
    # it ends up governing this run — a fresh manual stance may bypass it TODAY,
    # but the confirmation streak must keep advancing in the background so a later
    # stale-manual day picks up wherever the hysteresis organically reached, never
    # a frozen/stale point.
    confirm = _confirm_axis_direction(mi_stance or "unconfirmed", prev_state, today)

    if fresh:
        stance, source = m_stance, "manual_fresh"
        stance_pending = False
        note = (
            f"Manual SEP/dot-plot stance '{m_stance}' (as_of {as_of}, fresh) governs; "
            "the market-implied DGS2 read is secondary context."
        )
    elif mi_stance:
        stance, source = confirm["direction"], "market_implied"
        stance_pending = confirm["direction_pending"]
        note = (
            f"Market-implied stance '{mi_stance}' governs: DGS2 20d delta "
            f"{delta_bp:+.1f}bp (hawkish >= +{hawk_bp:.0f}bp / dovish <= -{dove_bp:.0f}bp); "
            f"manual fomc-stance.json stale or null (as_of {as_of}). A fresh SEP/dot-plot "
            "update still beats this proxy."
        )
        if stance_pending:
            note += (
                f" UNCONFIRMED (raw_streak {confirm['raw_streak']} of 2 required) — "
                f"stance holds at '{stance}' pending a second confirming run."
            )
    else:
        stance, source = "unconfirmed", "unconfirmed"
        stance_pending = False
        note = (
            "Policy UNCONFIRMED: manual stance stale/absent AND <21 DGS2 observations "
            "for the market-implied read."
        )
    if agreement is False:
        note += (
            f" DISAGREEMENT: manual says '{m_stance}', market-implied says '{mi_stance}'."
        )

    return {
        "stance": stance,
        "raw_stance": mi_stance or "unconfirmed",
        "stance_pending": stance_pending,
        "raw_streak": confirm["raw_streak"],
        # The market-implied confirmation's OWN state, independent of whether a
        # fresh manual stance is bypassing it for `stance` today — this (not the
        # blended `stance`) is what the caller must persist back to
        # AxisDirectionState, so a later stale-manual day resumes from the real
        # hysteresis position rather than the manual value.
        "confirmed_market_implied_stance": confirm["direction"],
        "confirmed_market_implied_as_of": confirm["confirmed_as_of"],
        "source": source,
        "market_implied": {
            "stance": mi_stance,
            "dgs2_latest": round(dgs2[0], 2) if dgs2 else None,
            "dff_latest": round(dff[0], 2) if dff else None,
            "dgs2_delta_20d_bp": delta_bp,
            "spread_bp": spread_bp,
        },
        "manual": {"stance": m_stance, "as_of": as_of, "fresh": fresh},
        "agreement": agreement,
        "note": note,
    }


def _build_regime_gate(growth_axis: dict, inflation_axis: dict, policy_axis: dict) -> dict:
    """Deterministic macro deployment gate from the precomputed axes + policy stance.

    CLOSED unless growth is confirmed rising, realized inflation is not rising, and
    the RESOLVED policy stance (``policy_axis``: manual-fresh SEP layer, else the
    market-implied DGS2 read — see _build_policy_axis) is not hawkish. An unconfirmed
    stance cannot *confirm* Q1 but does not by itself hard-close the gate (it is
    flagged); growth/inflation drive it. The analyzer echoes ``status`` into the
    trades JSON ``deployment_gate`` field.
    """
    reasons: list[str] = []
    g = (growth_axis or {}).get("direction")
    i = (inflation_axis or {}).get("direction")
    stance = (policy_axis or {}).get("stance", "unconfirmed")
    source = (policy_axis or {}).get("source")

    if g != "rising":
        reasons.append(f"growth axis {g} (not rising)")
    if i == "rising":
        reasons.append("inflation axis rising")
    if stance == "hawkish":
        reasons.append("policy stance hawkish")

    status = "closed" if reasons else "open"
    policy_note = ""
    if stance == "unconfirmed":
        policy_note = "policy stance UNCONFIRMED — cannot confirm Q1; deploy cautiously."
    return {
        "status": status,
        "reasons": reasons,
        "policy_note": policy_note,
        "derived_from": {
            "growth": g, "inflation": i,
            "policy_stance": stance, "policy_source": source,
        },
        "rule": (
            "OPEN only when growth rising AND inflation not rising AND policy not "
            "hawkish; else CLOSED. Cash-sleeve band is subordinate to this gate."
        ),
    }


def _load_risk_limits() -> dict:
    """Canonical risk limits from config/risk-limits.json (spec §3/§8).

    Single source of truth for the reference-weight math: concentration ceiling
    (90% of core), 0.1% sleeve floor, single-name caps, cash band, flex caps, the
    exempt holds, the conviction ladder, and the borderline-blend params. Missing or
    malformed → the in-module defaults (which mirror the file). Tolerant of the
    ``_comment`` / ``_*_note`` annotation keys.
    """
    try:
        with open(_RISK_LIMITS_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("risk-limits.json missing/invalid — using built-in defaults")
        return dict(_RISK_LIMITS_DEFAULTS)
    # Shallow-merge over defaults so a partial file still yields every key.
    merged = dict(_RISK_LIMITS_DEFAULTS)
    for k, v in data.items():
        if not k.startswith("_"):
            merged[k] = v
    return merged


def _conviction_proxy(
    growth_axis: dict,
    inflation_axis: dict,
    regime_gate: dict,
    bond_signals: dict,
    labor_signals: dict,
    market_shock: dict,
) -> dict:
    """Deterministic stand-in for the analyzer's Risk Score (0–10, higher = LESS
    conviction), computed from signals the collector already has.

    The conviction ladder (risk-limits.json) maps this to an active-quadrant target
    share of core. The analyzer still produces its own Risk Score; if it differs
    materially it may deviate from the reference via a logged override (brief Phase 4).
    This keeps the reference fully deterministic and echoed (spec §5/§10) with no
    chicken-and-egg on the LLM's number.

    Scoring (additive penalties on a clean base of 1, clamped 0–10):
      +2 gate CLOSED (regime not deployable) ; +1 growth indeterminate/flat ;
      +1 growth confidence low ; +1 inflation indeterminate/flat ; +1 policy
      unconfirmed ; +1 bond scorecard defensive (≤ -3) ; +1 labor scorecard
      defensive (≤ -3) ; +2 shock level 3 / +1 shock level 2 ; +1 a primary axis
      missing entirely. A clean risk-on regime (gate open, both axes pinned, policy
      confirmed, no defensive/scorecard/shock flags) lands at 1 (very high conviction);
      a contradicted/stale regime lands 6–9 (low / no read).
    """
    score = 1.0
    drivers: list[str] = []

    g = (growth_axis or {}).get("direction")
    gc = (growth_axis or {}).get("confidence")
    i = (inflation_axis or {}).get("direction")
    stance = (regime_gate or {}).get("derived_from", {}).get("policy_stance")
    gate = (regime_gate or {}).get("status")

    if gate == "closed":
        score += 2
        drivers.append("gate closed (+2)")
    if g not in ("rising", "falling"):
        score += 1
        drivers.append(f"growth {g or 'missing'} (+1)")
    if g is None:
        score += 1
        drivers.append("growth axis missing (+1)")
    if gc == "low":
        score += 1
        drivers.append("growth confidence low (+1)")
    if i not in ("rising", "falling"):
        score += 1
        drivers.append(f"inflation {i or 'missing'} (+1)")
    if stance == "unconfirmed":
        score += 1
        drivers.append("policy unconfirmed (+1)")

    bond_c = ((bond_signals or {}).get("scorecard") or {}).get("composite")
    if isinstance(bond_c, (int, float)) and bond_c <= -3:
        score += 1
        drivers.append(f"bonds defensive ({bond_c}) (+1)")
    labor_c = ((labor_signals or {}).get("scorecard") or {}).get("composite")
    if isinstance(labor_c, (int, float)) and labor_c <= -3:
        score += 1
        drivers.append(f"labor defensive ({labor_c}) (+1)")

    shock = (market_shock or {}).get("shock_level")
    if shock == 3:
        score += 2
        drivers.append("shock level 3 (+2)")
    elif shock == 2:
        score += 1
        drivers.append("shock level 2 (+1)")

    score = max(0.0, min(10.0, score))
    return {"score": round(score, 1), "drivers": drivers}


def _ladder_target_pct(ladder: list[dict], proxy_score: float) -> tuple[float, str]:
    """Map a 0–10 conviction proxy to (active-quadrant target % of core, label) via
    the config ladder. Picks the first rung whose ``risk_score_max`` ≥ the score."""
    for rung in ladder:
        if proxy_score <= rung.get("risk_score_max", 10):
            return float(rung.get("active_quadrant_target", 50.0)), rung.get("conviction", "")
    last = ladder[-1] if ladder else {"active_quadrant_target": 50.0, "conviction": ""}
    return float(last.get("active_quadrant_target", 50.0)), last.get("conviction", "")


# ---------------------------------------------------------------------------
# Thematic conviction overlay (2026-08-14 audit, decisions D-4/D-5). An
# LLM-emitted probability directly sizing a position is an unbounded
# self-grading loop unless constrained (Task D1's five mandatory properties:
# quantized, evidence-bound, bounded, hysteretic, graded). This section
# implements the pure pieces; `_build_thematic_conviction` (collector
# orchestration) and the `_build_reference_weights` step-3c integration wire
# them together. Motivating incident: a sustained Hormuz oil disruption
# (see B1/B3) left VDE pinned at a 1-share 0.097% floor for four consecutive
# sessions with no path from a live named theme to a position size — the
# prompt's prior instruction was to "note the linkage in the rebalancing
# rationale" and nothing more.
# ---------------------------------------------------------------------------

def _thematic_ladder_lookup(ladder: list[dict], p_up: float) -> tuple[float, str]:
    """D1 property 1 (quantized): map a continuous probability to a coarse
    conviction band via the config ladder (sorted descending by `p_up_min` in
    config) — the mirror of `_ladder_target_pct`'s ascending-threshold lookup.
    Picks the FIRST rung whose `p_up_min` <= p_up (i.e. the highest band the
    probability clears). A 0.63 and a 0.66 landing in different bands is
    intentional; two values landing in the SAME band must produce the exact
    same target — that is the point of quantizing."""
    for rung in ladder:
        if p_up >= float(rung.get("p_up_min", 0.0)):
            return float(rung.get("target_pct_of_equity", 0.0)), rung.get("conviction", "")
    last = ladder[-1] if ladder else {"target_pct_of_equity": 0.0, "conviction": "none"}
    return float(last.get("target_pct_of_equity", 0.0)), last.get("conviction", "none")


def _thematic_classify_symbol(
    symbol: str,
    quarantined_symbols: set[str] | None,
    effective_selected: dict[str, str] | None,
) -> dict:
    """D4 eligibility rules (strict guardrails) — one symbol at a time. Returns
    ``{"status": "core_eligible" | "flex_route" | "excluded", "reason": str | None}``.

    1. A `LEGACY_EXITS` name is always excluded — reference stays 0, never
       floored; this PR does not relax that doctrine.
    2. A price-quarantined name is excluded — a thematic lift on a name whose
       price feed is already flagged untrustworthy would size a position off
       a number nobody trusts.
    3. A `CORE_ROSTER` name must be its role's EFFECTIVE selected incumbent
       (`role_of` + `effective_selected`, the SAME live map every other
       roster consumer resolves through — session 2026-07-27 doctrine). A
       non-selected pool member is excluded: `_build_reference_weights` step 4
       zeroes every non-selected pool member by design, and a thematic lift
       would fight that loop, producing a permanently unfillable reference.
       The correct response to a theme pointing at a non-selected pool member
       is a sleeve-selection switch, not a thematic lift (decision gate D-6).
    4. A ticker NOT in `CORE_ROSTER` at all is never excluded here — it is
       simply not core-eligible, and routes to the flex pipeline instead
       (`flex_source: "thematic"`, D4 rule 3). New tickers enter only via
       flex; this PR does not relax that doctrine either.

    Evidence-count sufficiency (D4 rule 4, `min_evidence_items`) is a
    NOMINATION-level check, not a universe-membership check — it depends on
    the LLM's own emitted `evidence[]` for that specific nomination, so it is
    evaluated separately by the caller once a nomination exists.
    """
    sym = (symbol or "").upper()
    if sym in LEGACY_EXITS:
        return {"status": "excluded", "reason": "legacy_exit"}
    if sym in (quarantined_symbols or set()):
        return {"status": "excluded", "reason": "price_quarantined"}
    if sym not in CORE_ROSTER:
        return {"status": "flex_route", "reason": None}
    role_id = role_of(sym)
    if role_id is not None:
        selected = (effective_selected or {}).get(role_id)
        if selected and sym != str(selected).upper():
            return {"status": "excluded", "reason": "non_selected_pool_member"}
    return {"status": "core_eligible", "reason": None}


def _thematic_scale_to_caps(
    targets: dict[str, float], per_ticker_cap: float, aggregate_cap: float,
) -> dict[str, float]:
    """D5 cap application: clamp each entry to `per_ticker_cap`, then — if the
    SUM still exceeds `aggregate_cap` — scale ALL entries down pro-rata (never
    truncate by rank, which would create an arbitrary ordering dependency
    between economically-equivalent nominations)."""
    clamped = {t: min(max(0.0, v), per_ticker_cap) for t, v in targets.items()}
    total = sum(clamped.values())
    if total <= aggregate_cap or total <= 0:
        return clamped
    scale = aggregate_cap / total
    return {t: v * scale for t, v in clamped.items()}


def _thematic_brier(pairs: list[tuple[float, float]]) -> dict:
    """D6: Brier score (mean squared error of probability vs binary outcome)
    + hit rate over a list of ``(p_up, actual_up)`` resolved pairs (`actual_up`
    is 1.0/0.0). Brier alone is opaque to read in a daily report, hence
    `hit_rate` alongside it. Empty input -> `sample_size: 0`, both scores
    `None` (never a fabricated 0.0, which would misleadingly read as
    "perfect")."""
    n = len(pairs)
    if n == 0:
        return {"sample_size": 0, "brier_score": None, "hit_rate": None}
    brier = sum((p - a) ** 2 for p, a in pairs) / n
    hits = sum(1 for p, a in pairs if (p >= 0.5) == (a >= 0.5))
    return {"sample_size": n, "brier_score": round(brier, 4), "hit_rate": round(hits / n, 4)}


def _thematic_damping_factor(
    brier_score: float | None, sample_size: int, damping_ladder: list[dict], brier_min_sample: int,
) -> float:
    """D6: below `brier_min_sample`, no damping (`1.0`) — a thin sample proves
    nothing about calibration either way; report the sample size prominently
    instead of damping on noise. Otherwise the first rung whose `brier_max` >=
    the score governs (ladder sorted ascending by `brier_max` in config)."""
    if sample_size < brier_min_sample or brier_score is None:
        return 1.0
    for rung in damping_ladder:
        if brier_score <= float(rung.get("brier_max", 1.0)):
            return float(rung.get("factor", 1.0))
    return damping_ladder[-1].get("factor", 1.0) if damping_ladder else 1.0


_THEMATIC_STATE_TABLE = "ThematicConvictionState"


def _load_thematic_state() -> dict[str, dict]:
    """Per-symbol hysteresis state (PK='state', RK=symbol) — mirrors
    `SleeveSelectionState`'s per-row-per-entity shape (one row per role there,
    one row per nominated symbol here)."""
    state: dict[str, dict] = {}
    for e in query_entities(_THEMATIC_STATE_TABLE):
        sym = e.get("RowKey")
        if sym:
            state[sym] = {
                "active": bool(e.get("active", False)),
                "active_conviction": (e.get("active_conviction") or None) or None,
                "active_target_pct": float(e.get("active_target_pct") or 0.0),
                "confirm_streak": int(e.get("confirm_streak") or 0),
                "candidate_conviction": (e.get("candidate_conviction") or None) or None,
                "release_streak": int(e.get("release_streak") or 0),
                "applied_pct": float(e.get("applied_pct") or 0.0),
            }
    return state


def _save_thematic_state(symbol: str, state: dict) -> None:
    upsert_entity(_THEMATIC_STATE_TABLE, {
        "PartitionKey": "state",
        "RowKey": symbol,
        "active": bool(state.get("active", False)),
        "active_conviction": state.get("active_conviction") or "",
        "active_target_pct": float(state.get("active_target_pct") or 0.0),
        "confirm_streak": int(state.get("confirm_streak") or 0),
        "candidate_conviction": state.get("candidate_conviction") or "",
        "release_streak": int(state.get("release_streak") or 0),
        "applied_pct": float(state.get("applied_pct") or 0.0),
    })


def _confirm_thematic_entry(candidate: dict | None, prev: dict | None, tw_cfg: dict) -> dict:
    """D5 hysteresis, per symbol — "exactly as Task C": `confirm_sessions`
    consecutive sessions with the same conviction band before a FRESH
    activation applies; once active, `release_sessions` consecutive sessions
    of absence OR a DIFFERENT band (the spec calls out "a lower band"
    specifically; here any band change is symmetric — a symbol whose
    nomination merely fluctuates day to day must not update every session
    either) before the active state steps to the new reading. A band change
    that does apply ramps via `max_session_delta_pp`, never a jump — the SAME
    ramp helper shape as `_confirm_transition_watch`.

    ``candidate`` is ``None`` (no nomination this session) or
    ``{"conviction": str, "target_pct_of_equity": float, ...}`` — the ladder-
    resolved reading from today's (i.e. the PRIOR trading day's, one-session
    lag) nomination. Extra keys on ``candidate`` (symbol/theme/evidence/etc.)
    pass through into the result verbatim for the snapshot block to render.
    """
    confirm_n = int(tw_cfg.get("confirm_sessions", 2))
    release_n = int(tw_cfg.get("release_sessions", 2))
    max_delta = float(tw_cfg.get("max_session_delta_pp", 1.5))
    prev = prev or {}

    was_active = bool(prev.get("active"))
    active_conviction = prev.get("active_conviction")
    active_target = float(prev.get("active_target_pct") or 0.0)
    prior_applied = float(prev.get("applied_pct") or 0.0)
    prior_confirm_streak = int(prev.get("confirm_streak") or 0)
    prior_candidate_conviction = prev.get("candidate_conviction")
    prior_release_streak = int(prev.get("release_streak") or 0)

    def _ramp(target: float, prior: float) -> float:
        return min(target, prior + max_delta) if target >= prior else max(target, prior - max_delta)

    def _result(*, active: bool, status: str, conviction, target: float, applied: float,
                confirm_streak: int, cand_conviction, release_streak: int,
                pending_streak, release_pending: bool, meta: dict) -> dict:
        out = {
            "active": active, "status": status, "conviction": conviction,
            "target_pct_of_equity": round(target, 4), "applied_pct_of_equity": round(applied, 4),
            "confirm_streak": confirm_streak, "candidate_conviction": cand_conviction,
            "release_streak": release_streak, "pending_streak": pending_streak,
            "release_pending": release_pending,
            "_state": {
                "active": active,
                "active_conviction": conviction if active else None,
                "active_target_pct": target if active else 0.0,
                "confirm_streak": confirm_streak,
                "candidate_conviction": cand_conviction,
                "release_streak": release_streak,
                "applied_pct": applied,
            },
        }
        out.update(meta)
        return out

    meta = {k: v for k, v in (candidate or {}).items()
            if k not in ("conviction", "target_pct_of_equity")}

    if candidate is None:
        if not was_active:
            return _result(active=False, status="indeterminate", conviction=None, target=0.0,
                            applied=0.0, confirm_streak=0, cand_conviction=None, release_streak=0,
                            pending_streak=None, release_pending=False, meta={})
        release_streak = prior_release_streak + 1
        if release_streak >= release_n:
            return _result(active=False, status="indeterminate", conviction=None, target=0.0,
                            applied=0.0, confirm_streak=0, cand_conviction=None, release_streak=0,
                            pending_streak=None, release_pending=False, meta={})
        return _result(active=True, status="active", conviction=active_conviction,
                        target=active_target, applied=prior_applied, confirm_streak=0,
                        cand_conviction=None, release_streak=release_streak,
                        pending_streak=None, release_pending=True, meta={})

    cand_conviction = candidate["conviction"]
    cand_target = float(candidate["target_pct_of_equity"])

    if not was_active:
        same = cand_conviction == prior_candidate_conviction
        streak = prior_confirm_streak + 1 if same else 1
        if streak >= confirm_n:
            applied = _ramp(cand_target, 0.0)
            return _result(active=True, status="active", conviction=cand_conviction,
                            target=cand_target, applied=applied, confirm_streak=0,
                            cand_conviction=None, release_streak=0, pending_streak=None,
                            release_pending=False, meta=meta)
        return _result(active=False, status="pending", conviction=None, target=0.0, applied=0.0,
                        confirm_streak=streak, cand_conviction=cand_conviction, release_streak=0,
                        pending_streak=streak, release_pending=False, meta=meta)

    if cand_conviction == active_conviction:
        applied = _ramp(cand_target, prior_applied)
        return _result(active=True, status="active", conviction=active_conviction,
                        target=cand_target, applied=applied, confirm_streak=0,
                        cand_conviction=None, release_streak=0, pending_streak=None,
                        release_pending=False, meta=meta)

    # A different band while active — treat like a release candidate: needs
    # release_sessions consecutive occurrences of this SAME different band
    # before the active state steps to it. Old stays in force meanwhile.
    same_cand = cand_conviction == prior_candidate_conviction
    release_streak = prior_release_streak + 1 if same_cand else 1
    if release_streak >= release_n:
        applied = _ramp(cand_target, prior_applied)
        return _result(active=True, status="active", conviction=cand_conviction,
                        target=cand_target, applied=applied, confirm_streak=0,
                        cand_conviction=None, release_streak=0, pending_streak=None,
                        release_pending=False, meta=meta)
    applied = _ramp(active_target, prior_applied)
    return _result(active=True, status="active", conviction=active_conviction,
                    target=active_target, applied=applied, confirm_streak=0,
                    cand_conviction=cand_conviction, release_streak=release_streak,
                    pending_streak=release_streak, release_pending=True, meta=meta)

def _build_thematic_conviction(
    risk_limits: dict,
    prev_nominations: list[dict] | None,
    prev_states: dict[str, dict],
    quarantined_symbols: set[str],
    effective_selected: dict[str, str],
    calibration: dict,
) -> tuple[dict, dict[str, dict]]:
    """D2 (2026-08-14 audit) — the `thematic_conviction` snapshot block.

    The COLLECTOR emits the scaffold (eligible universe, caps, ladder, current
    state, calibration); the LLM fills in probabilities via `trades_obj
    ["thematic_conviction"]`; the collector applies them on the FOLLOWING run
    (`prev_nominations` — the PRIOR day's emission, one-session lag, resolved
    by the SAME 7-day walkback pattern `_build_execution_review` already
    uses). This lag is deliberate: it keeps `reference_weights` deterministic
    and auditable, and it is itself the hysteresis substrate (D1 property 4)
    — a probability must survive to the next session before it can move
    capital at all.

    `enabled: False` (or a crash) -> a complete no-op: no eligible universe,
    no active/pending entries, nothing partially applied (D5 fail-closed
    doctrine) — returns `{"available": True, "enabled": False}` and an
    EMPTY new-state dict (any existing per-symbol hysteresis state is left
    untouched on disk, not wiped, so re-enabling resumes rather than
    restarting).

    Returns ``(snapshot_block, new_states_by_symbol)`` — the caller persists
    ``new_states_by_symbol`` via `_save_thematic_state` per symbol.
    """
    tc_cfg = risk_limits.get("thematic_conviction") or _RISK_LIMITS_DEFAULTS["thematic_conviction"]
    if not tc_cfg.get("enabled", True):
        return {"available": True, "enabled": False}, {}

    ladder = tc_cfg.get("ladder") or []
    per_ticker_cap = float(tc_cfg.get("per_ticker_cap_pct_of_equity", 4.0))
    aggregate_cap = float(tc_cfg.get("aggregate_cap_pct_of_equity", 8.0))
    min_evidence = int(tc_cfg.get("min_evidence_items", 2))

    # Eligible universe / excluded — every CORE_ROSTER member classified once,
    # independent of whether any nomination targets it (a describe-only map
    # of "what COULD be lifted right now").
    eligible_universe: list[str] = []
    excluded: list[dict] = []
    for sym in sorted(CORE_ROSTER):
        cls = _thematic_classify_symbol(sym, quarantined_symbols, effective_selected)
        if cls["status"] == "core_eligible":
            eligible_universe.append(sym)
        elif cls["status"] == "excluded":
            excluded.append({"symbol": sym, "reason": cls["reason"]})

    # Resolve each nomination from the prior session: eligibility, evidence
    # sufficiency, then the ladder lookup. Rejected nominations are logged in
    # `excluded` too (never silently downsized — D4 rule 4) but do NOT enter
    # the hysteresis machinery at all (a rejection carries no state).
    candidates_by_symbol: dict[str, dict] = {}
    for nom in prev_nominations or []:
        sym = str(nom.get("symbol") or "").upper()
        if not sym:
            continue
        cls = _thematic_classify_symbol(sym, quarantined_symbols, effective_selected)
        if cls["status"] == "excluded":
            excluded.append({"symbol": sym, "reason": cls["reason"]})
            continue
        evidence = nom.get("evidence") or []
        if len(evidence) < min_evidence:
            excluded.append({"symbol": sym, "reason": "insufficient_evidence"})
            continue
        try:
            p_up = float(nom.get("p_up"))
        except (TypeError, ValueError):
            excluded.append({"symbol": sym, "reason": "invalid_p_up"})
            continue
        target, conviction = _thematic_ladder_lookup(ladder, p_up)
        # D5: thematic_target = ladder_lookup(p_up) x calibration.damping_factor —
        # applied ONCE here (echo-not-re-derive) so reference_weights never
        # independently recomputes the same formula.
        damping = float((calibration or {}).get("damping_factor", 1.0))
        target = target * damping
        if cls["status"] == "flex_route":
            # D4 rule 3: a non-roster ticker never receives a core reference —
            # it is routed to flex instead. Recorded for visibility; no core
            # hysteresis/target applies to it.
            excluded.append({
                "symbol": sym, "reason": "routed_to_flex",
                "flex_source": "thematic", "theme": nom.get("theme"),
            })
            continue
        candidates_by_symbol[sym] = {
            "conviction": conviction, "target_pct_of_equity": target,
            "symbol": sym, "p_up": p_up, "theme": nom.get("theme"),
            "evidence": evidence, "review_date": nom.get("review_date"),
        }

    # Aggregate/per-ticker cap BEFORE hysteresis, so the ramp converges to the
    # already-capped target rather than a since-scaled-down one (D5 pro-rata,
    # never truncation by rank).
    raw_targets = {sym: c["target_pct_of_equity"] for sym, c in candidates_by_symbol.items()}
    capped_targets = _thematic_scale_to_caps(raw_targets, per_ticker_cap, aggregate_cap)
    for sym, capped in capped_targets.items():
        candidates_by_symbol[sym]["target_pct_of_equity"] = capped

    # Confirm/release hysteresis, per symbol — union of today's candidates and
    # any symbol with live persisted state (so an absent-today symbol that was
    # previously active still walks its release path instead of vanishing).
    active: list[dict] = []
    pending: list[dict] = []
    new_states: dict[str, dict] = {}
    all_symbols = set(candidates_by_symbol) | set(prev_states)
    for sym in sorted(all_symbols):
        result = _confirm_thematic_entry(
            candidates_by_symbol.get(sym), prev_states.get(sym), tc_cfg,
        )
        new_states[sym] = result.pop("_state")
        result["symbol"] = sym
        if result["active"]:
            active.append(result)
        elif result["status"] == "pending":
            pending.append(result)

    return {
        "available": True,
        "enabled": True,
        "ladder": ladder,
        "aggregate_cap_pct_of_equity": aggregate_cap,
        "per_ticker_cap_pct_of_equity": per_ticker_cap,
        "eligible_universe": eligible_universe,
        "excluded": excluded,
        "active": active,
        "pending": pending,
        "calibration": calibration,
    }, new_states


# Quadrant defensiveness rank (Q1 most offensive → Q4 most defensive). A transition to a
# HIGHER-ranked quadrant is de-risk; to a LOWER-ranked one is re-risk (spec §6 asymmetry).
_QUADRANT_DEFENSIVENESS = {"Q1": 0, "Q2": 1, "Q3": 2, "Q4": 3}


def _project_quadrant(realized_quad: str, leading_inflation_dir: str, growth_dir: str) -> str:
    """The quadrant the LEADING inflation signal projects, holding the growth axis fixed.

    Inflation is the only axis the leading signal (breakevens + oil) speaks to, so we move
    only along the inflation dimension of the grid, never the growth one:
      growth rising:  inflation falling → Q1, inflation rising → Q2
      growth falling: inflation falling → Q4, inflation rising → Q3
    Returns "" if the growth axis is not pinned (can't place on the grid).
    """
    g = (growth_dir or "").lower()
    d = (leading_inflation_dir or "").lower()
    if g == "rising":
        return "Q1" if d == "falling" else ("Q2" if d == "rising" else "")
    if g == "falling":
        return "Q4" if d == "falling" else ("Q3" if d == "rising" else "")
    return ""


def _project_quadrant_growth(leading_growth_dir: str, realized_inflation_dir: str) -> str:
    """The quadrant the LEADING growth signal projects, holding the inflation axis fixed.

    Growth is the only axis the leading-growth composite speaks to, so we move only along
    the growth dimension of the grid, never the inflation one:
      inflation falling: growth rising → Q1, growth falling → Q4
      inflation rising:  growth rising → Q2, growth falling → Q3
    Returns "" if the inflation axis is not pinned.
    """
    g = (leading_growth_dir or "").lower()
    i = (realized_inflation_dir or "").lower()
    if i == "falling":
        return "Q1" if g == "rising" else ("Q4" if g == "falling" else "")
    if i == "rising":
        return "Q2" if g == "rising" else ("Q3" if g == "falling" else "")
    return ""


def _build_transition_watch(
    divergences: list[dict],
    growth_axis: dict,
    inflation_axis: dict,
    cfg: dict,
) -> dict:
    """Deterministic PRE-STAGING signal: when leading inflation OR leading growth disagrees
    with realized, project the quadrant it points to and emit a bounded lean for
    reference_weights — WITHOUT moving the binding active_quadrant / regime_gate / realized
    axes (strategy-spec §6). Both sides use the same asymmetry: de-risk stages readily;
    re-risk needs a higher bar.

    GENERALIZED (FOLLOWUPS #17, 2026-07-23): now consumes BOTH
    `leading_vs_lagging_inflation` (original) AND `leading_vs_lagging_growth` (new)
    divergences symmetrically. The inflation side projects by flipping the inflation axis
    while holding growth fixed; the growth side projects by flipping the growth axis while
    holding inflation fixed. When both sides fire, the more defensive projection wins
    (de-risk bias, spec §6 safety).

    REUSE not re-detect (§5/DRY): triggers are Phase-2 divergences — consumed here,
    never re-derived.
    """
    tw_cfg = cfg.get("transition_watch") or _RISK_LIMITS_DEFAULTS["transition_watch"]
    g = (growth_axis or {}).get("direction")
    realized_i = (inflation_axis or {}).get("direction")

    base = {"active": False, "projected_quadrant": None, "direction": None,
            "staged_fraction": 0.0, "basis": [], "sides": []}

    def _evaluate_inflation_side(div: dict) -> dict | None:
        """Returns a side-result dict or None if not activatable."""
        if div.get("status") != "active":
            return None
        leading_dir = div.get("direction_implied")
        projected = _project_quadrant("", leading_dir, g)
        if not projected:
            return None
        realized_quad = active_quadrant(g, realized_i)
        if realized_quad:
            r_real = float(_QUADRANT_DEFENSIVENESS.get(realized_quad, 0))
        else:
            bucket = favored_bucket(g, realized_i)
            ranks = [_QUADRANT_DEFENSIVENESS[q] for q in bucket if q in _QUADRANT_DEFENSIVENESS]
            if not ranks:
                return None
            r_real = sum(ranks) / len(ranks)
        r_proj = float(_QUADRANT_DEFENSIVENESS.get(projected, 0))
        if r_proj == r_real:
            return None
        direction = "de_risk" if r_proj > r_real else "re_risk"
        basis = [f"{s['name']}={s['value']}" for s in div.get("signals", [])
                 if s.get("name") in ("be_5y.delta_20d_bp", "inflation_axis.oil_20d_pct_governing")
                 and s.get("value") is not None]
        if direction == "re_risk":
            div_cfg_thr = _load_divergence_config().get("leading_vs_lagging_inflation", {})
            thr = float(div_cfg_thr.get("breakeven_delta_20d_bp", 15.0))
            oil_thr = float(div_cfg_thr.get("oil_20d_pct", 10.0))
            be = next((s["value"] for s in div.get("signals", []) if s.get("name") == "be_5y.delta_20d_bp"), None)
            oil = next((s["value"] for s in div.get("signals", [])
                        if s.get("name") == "inflation_axis.oil_20d_pct_governing"), None)
            want_up = leading_dir == "rising"
            confs = sum([
                1 if (be is not None and ((be >= thr) if want_up else (be <= -thr))) else 0,
                1 if (oil is not None and ((oil >= oil_thr) if want_up else (oil <= -oil_thr))) else 0,
            ])
            if confs < int(tw_cfg.get("re_risk_min_confirmations", 2)):
                return None  # below confirmation bar
            frac = float(tw_cfg.get("staged_fraction_re_risk", 0.15))
        else:
            frac = float(tw_cfg.get("staged_fraction_de_risk", 0.30))
        return {"side": "inflation", "projected_quadrant": projected, "direction": direction,
                "staged_fraction": frac, "basis": basis,
                "defensiveness": float(_QUADRANT_DEFENSIVENESS.get(projected, 0))}

    def _evaluate_growth_side(div: dict) -> dict | None:
        """Returns a side-result dict or None if not activatable."""
        if div.get("status") != "active":
            return None
        leading_dir = div.get("direction_implied")  # "rising"/"falling"
        projected = _project_quadrant_growth(leading_dir, realized_i)
        if not projected:
            return None
        realized_quad = active_quadrant(g, realized_i)
        if realized_quad:
            r_real = float(_QUADRANT_DEFENSIVENESS.get(realized_quad, 0))
        else:
            bucket = favored_bucket(g, realized_i)
            ranks = [_QUADRANT_DEFENSIVENESS[q] for q in bucket if q in _QUADRANT_DEFENSIVENESS]
            if not ranks:
                return None
            r_real = sum(ranks) / len(ranks)
        r_proj = float(_QUADRANT_DEFENSIVENESS.get(projected, 0))
        if r_proj == r_real:
            return None
        direction = "de_risk" if r_proj > r_real else "re_risk"
        basis = [f"{s['name']}={s['value']}" for s in div.get("signals", [])
                 if s.get("name") in ("leading_growth.direction", "leading_growth.score")
                 and s.get("value") is not None]
        if direction == "re_risk":
            # Growth re-risk: the composite must have high confidence (medium+).
            lg_conf = next((s["value"] for s in div.get("signals", [])
                            if s.get("name") == "leading_growth.confidence"), None)
            if lg_conf not in ("full", "medium"):
                return None
            frac = float(tw_cfg.get("staged_fraction_re_risk", 0.15))
        else:
            frac = float(tw_cfg.get("staged_fraction_de_risk", 0.30))
        return {"side": "growth", "projected_quadrant": projected, "direction": direction,
                "staged_fraction": frac, "basis": basis,
                "defensiveness": float(_QUADRANT_DEFENSIVENESS.get(projected, 0))}

    # Evaluate both sides.
    infl_div = next((d for d in (divergences or []) if d.get("id") == "leading_vs_lagging_inflation"), None)
    grow_div = next((d for d in (divergences or []) if d.get("id") == "leading_vs_lagging_growth"), None)

    infl_result = _evaluate_inflation_side(infl_div) if infl_div else None
    grow_result = _evaluate_growth_side(grow_div) if grow_div else None

    active_sides = [r for r in (infl_result, grow_result) if r is not None]
    if not active_sides:
        # No side could be staged (below confirmation bar, or unprojectable).
        # Always return indeterminate — the divergence's own "active" status must
        # not propagate here; the transition_watch is inactive when nothing stages.
        return {**base, "status": "indeterminate"}

    # When both sides fire, the more defensive projected quadrant wins (de-risk bias).
    if len(active_sides) == 1:
        best = active_sides[0]
    else:
        # Prefer the de-risk side; within same direction, the more defensive quadrant.
        de_risk_sides = [r for r in active_sides if r["direction"] == "de_risk"]
        if de_risk_sides:
            best = max(de_risk_sides, key=lambda r: r["defensiveness"])
        else:
            best = min(active_sides, key=lambda r: r["defensiveness"])

    realized_quad = active_quadrant(g, realized_i)
    return {
        "active": True,
        "projected_quadrant": best["projected_quadrant"],
        "realized_quadrant": realized_quad,
        "direction": best["direction"],
        "staged_fraction": best["staged_fraction"],
        "basis": best["basis"],
        "sides": active_sides,
        "status": "active",
        "rule": (
            "Bounded partial lean toward projected_quadrant staged into reference_weights "
            "as a convex blend; binding active_quadrant / regime_gate / realized axes "
            "UNCHANGED. Consumes leading_vs_lagging_inflation AND leading_vs_lagging_growth "
            "divergences symmetrically (FOLLOWUPS #17 generalisation)."
        ),
    }


# ---------------------------------------------------------------------------
# Task C (2026-08-14 audit) — transition_watch confirm/release hysteresis +
# staged-fraction ramp. `_build_transition_watch` above stays PURE and
# STATELESS (unchanged, still directly unit-tested) — it is now treated as the
# per-session CANDIDATE evaluation. `_confirm_transition_watch` wraps it with
# persisted state, mirroring the existing axis-direction confirmation pattern
# (`_confirm_axis_direction` / `AxisDirectionState`) rather than inventing a
# new storage mechanism, per instruction.
#
# Motivating incident: a single session's stateless activation blew the Q2
# reference block from 0.091% to 4.118% and back to 0.091% the very next day
# (2026-08-12 VDE whipsaw, itself downstream of the B1 stale-oil-leg bug) —
# transition_watch had no memory at all, so one session's noise could
# generate a ~45x round trip in a reference weight and a same-day reversal of
# ~$10k of proposed trades.
# ---------------------------------------------------------------------------
_TRANSITION_WATCH_STATE_TABLE = "TransitionWatchState"


def _load_transition_watch_state() -> dict | None:
    """Persisted hysteresis state (single row, PK='state' RK='transition_watch'
    — mirrors `_load_axis_direction_state`'s table-per-row shape). None on the
    first run / an empty table, which `_confirm_transition_watch` treats as
    "nothing active, nothing pending" (never a crash)."""
    for e in query_entities(_TRANSITION_WATCH_STATE_TABLE):
        if e.get("RowKey") == "transition_watch":
            return {
                "active": bool(e.get("active", False)),
                "active_projected_quadrant": (e.get("active_projected_quadrant") or None) or None,
                "active_direction": (e.get("active_direction") or None) or None,
                "confirm_streak": int(e.get("confirm_streak") or 0),
                "candidate_projected_quadrant": (e.get("candidate_projected_quadrant") or None) or None,
                "candidate_direction": (e.get("candidate_direction") or None) or None,
                "release_streak": int(e.get("release_streak") or 0),
                "applied_fraction": float(e.get("applied_fraction") or 0.0),
            }
    return None


def _save_transition_watch_state(state: dict) -> None:
    upsert_entity(_TRANSITION_WATCH_STATE_TABLE, {
        "PartitionKey": "state",
        "RowKey": "transition_watch",
        "active": bool(state.get("active", False)),
        "active_projected_quadrant": state.get("active_projected_quadrant") or "",
        "active_direction": state.get("active_direction") or "",
        "confirm_streak": int(state.get("confirm_streak") or 0),
        "candidate_projected_quadrant": state.get("candidate_projected_quadrant") or "",
        "candidate_direction": state.get("candidate_direction") or "",
        "release_streak": int(state.get("release_streak") or 0),
        "applied_fraction": float(state.get("applied_fraction") or 0.0),
    })


def _confirm_transition_watch(raw: dict, prev: dict | None, tw_cfg: dict) -> dict:
    """C1/C2 hysteresis + fraction ramp around the stateless `raw` candidate
    evaluation (this session's `_build_transition_watch` output).

    C1 — confirm/release:
    - Not yet active: the SAME (projected_quadrant, direction) must recur for
      `confirm_sessions` consecutive sessions before `active` flips true.
      Below that: `active: False, status: "pending"`, with `pending_streak`/
      `pending_projected_quadrant` visible.
    - Active and reproduced: streaks reset, ramp continues toward target.
    - Active and a DIFFERENT candidate appears: the OLD projection stays in
      force UNCHANGED (never a no-projection limbo) while the new candidate
      builds its OWN independent confirm streak; once that streak reaches
      `confirm_sessions` the active projection SWAPS to it directly (no
      intermediate deactivation). This is not a release miss.
    - Active and NOTHING reproduces (raw fully inactive): a genuine release
      miss — `release_streak` increments; below `release_sessions`, stay
      active unchanged (`release_pending: True`); at/above it, deactivate.

    C2 — fraction ramp: `staged_fraction` (the field every existing consumer
    already reads) becomes the RAMPED/APPLIED value — capped to move at most
    `max_session_delta_frac` per session toward `target_fraction` (the new,
    additive field carrying the un-ramped target) in EITHER direction. A
    fresh activation ramps in from 0; a target-fraction change while
    continuously active (e.g. a de-risk/re-risk swap) ramps between the two
    rather than stepping. No ramp during the release-grace window itself
    ("stay active" = unchanged) — the grace period is its own gradual
    mechanism; deactivation is a discrete cutover to 0.
    """
    prev = prev or {}
    confirm_n = int(tw_cfg.get("confirm_sessions", 2))
    release_n = int(tw_cfg.get("release_sessions", 2))
    max_delta = float(tw_cfg.get("max_session_delta_frac", 0.10))

    raw_active = bool(raw.get("active"))
    raw_proj = raw.get("projected_quadrant") if raw_active else None
    raw_dir = raw.get("direction") if raw_active else None
    raw_target = float(raw.get("staged_fraction") or 0.0) if raw_active else 0.0

    was_active = bool(prev.get("active"))
    active_proj = prev.get("active_projected_quadrant")
    active_dir = prev.get("active_direction")
    prior_applied = float(prev.get("applied_fraction") or 0.0)
    prior_confirm_streak = int(prev.get("confirm_streak") or 0)
    prior_candidate_proj = prev.get("candidate_projected_quadrant")
    prior_candidate_dir = prev.get("candidate_direction")
    prior_release_streak = int(prev.get("release_streak") or 0)

    def _ramp(target: float, prior: float) -> float:
        return min(target, prior + max_delta) if target >= prior else max(target, prior - max_delta)

    def _result(*, active: bool, status: str, proj, direction, target: float, applied: float,
                confirm_streak: int, cand_proj, cand_dir, release_streak: int,
                pending_streak, pending_proj, release_pending: bool) -> dict:
        return {
            "active": active, "status": status,
            "projected_quadrant": proj, "direction": direction,
            "realized_quadrant": raw.get("realized_quadrant"),
            "target_fraction": round(target, 4), "staged_fraction": round(applied, 4),
            "confirm_streak": confirm_streak,
            "candidate_projected_quadrant": cand_proj, "candidate_direction": cand_dir,
            "release_streak": release_streak,
            "basis": raw.get("basis", []), "sides": raw.get("sides", []),
            "pending_streak": pending_streak, "pending_projected_quadrant": pending_proj,
            "release_pending": release_pending,
            "rule": raw.get("rule"),
            "_state": {
                "active": active,
                "active_projected_quadrant": proj if active else None,
                "active_direction": direction if active else None,
                "confirm_streak": confirm_streak,
                "candidate_projected_quadrant": cand_proj,
                "candidate_direction": cand_dir,
                "release_streak": release_streak,
                "applied_fraction": applied,
            },
        }

    if not was_active:
        if not (raw_active and raw_proj is not None):
            return _result(active=False, status="indeterminate", proj=None, direction=None,
                            target=0.0, applied=0.0, confirm_streak=0, cand_proj=None,
                            cand_dir=None, release_streak=0, pending_streak=None,
                            pending_proj=None, release_pending=False)
        same = raw_proj == prior_candidate_proj and raw_dir == prior_candidate_dir
        streak = prior_confirm_streak + 1 if same else 1
        if streak >= confirm_n:
            applied = _ramp(raw_target, 0.0)
            return _result(active=True, status="active", proj=raw_proj, direction=raw_dir,
                            target=raw_target, applied=applied, confirm_streak=0,
                            cand_proj=None, cand_dir=None, release_streak=0,
                            pending_streak=None, pending_proj=None, release_pending=False)
        return _result(active=False, status="pending", proj=None, direction=None,
                        target=0.0, applied=0.0, confirm_streak=streak,
                        cand_proj=raw_proj, cand_dir=raw_dir, release_streak=0,
                        pending_streak=streak, pending_proj=raw_proj, release_pending=False)

    # was_active == True
    if raw_active and raw_proj == active_proj and raw_dir == active_dir:
        applied = _ramp(raw_target, prior_applied)
        return _result(active=True, status="active", proj=active_proj, direction=active_dir,
                        target=raw_target, applied=applied, confirm_streak=0,
                        cand_proj=None, cand_dir=None, release_streak=0,
                        pending_streak=None, pending_proj=None, release_pending=False)

    if raw_active and raw_proj is not None:
        same = raw_proj == prior_candidate_proj and raw_dir == prior_candidate_dir
        streak = prior_confirm_streak + 1 if same else 1
        old_target = float(tw_cfg.get(f"staged_fraction_{active_dir}", 0.0)) if active_dir else 0.0
        if streak >= confirm_n:
            applied = _ramp(raw_target, prior_applied)
            return _result(active=True, status="active", proj=raw_proj, direction=raw_dir,
                            target=raw_target, applied=applied, confirm_streak=0,
                            cand_proj=None, cand_dir=None, release_streak=0,
                            pending_streak=None, pending_proj=None, release_pending=False)
        applied = _ramp(old_target, prior_applied)
        return _result(active=True, status="active", proj=active_proj, direction=active_dir,
                        target=old_target, applied=applied, confirm_streak=streak,
                        cand_proj=raw_proj, cand_dir=raw_dir, release_streak=0,
                        pending_streak=streak, pending_proj=raw_proj, release_pending=False)

    # raw fully inactive — a genuine release miss.
    release_streak = prior_release_streak + 1
    old_target = float(tw_cfg.get(f"staged_fraction_{active_dir}", 0.0)) if active_dir else 0.0
    if release_streak >= release_n:
        return _result(active=False, status="indeterminate", proj=None, direction=None,
                        target=0.0, applied=0.0, confirm_streak=0, cand_proj=None,
                        cand_dir=None, release_streak=0, pending_streak=None,
                        pending_proj=None, release_pending=False)
    return _result(active=True, status="active", proj=active_proj, direction=active_dir,
                    target=old_target, applied=prior_applied, confirm_streak=0,
                    cand_proj=None, cand_dir=None, release_streak=release_streak,
                    pending_streak=None, pending_proj=None, release_pending=True)


# ---------------------------------------------------------------------------
# Sleeve selection scorecard (Task E — role-based core, roster_revision_2026-07)
#
# Deterministic, DESCRIBE-ONLY ranking of each quadrant-governed role's candidate pool.
# A `switch_signal` NEVER auto-trades and NEVER edits `selected` — a human disposes by
# committing a new `selected` to sleeve-roles.json. Pure functions (no I/O) so the blend,
# eligibility, and hysteresis are unit-testable; the collector does the FMP + Table I/O.
# ---------------------------------------------------------------------------
_SLEEVE_STATE_TABLE = "SleeveSelectionState"
_SLEEVE_CORR_WINDOW = 120
_SLEEVE_MIN_CORR_OBS = 20   # need at least this many overlapping daily returns for a corr


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)


def _returns_from_closes(close_map: dict[str, float],
                         windows: tuple[int, ...] = (60, 120, 252)) -> dict:
    """Point-to-point % returns over N trading days from a {date: close} map. A window
    with insufficient history yields None (skipped by the scorer)."""
    dates = sorted(close_map)
    closes = [close_map[d] for d in dates]
    out: dict = {}
    for w in windows:
        if len(closes) > w and closes[-1 - w]:
            out[f"r{w}"] = (closes[-1] / closes[-1 - w] - 1.0) * 100.0
        else:
            out[f"r{w}"] = None
    return out


def _corr_daily_returns(a_map: dict, b_map: dict, window: int) -> float | None:
    """Pearson correlation of daily returns over the last `window` overlapping days."""
    common = sorted(set(a_map) & set(b_map))
    if len(common) < _SLEEVE_MIN_CORR_OBS + 1:
        return None
    common = common[-(window + 1):]
    ra: list[float] = []
    rb: list[float] = []
    for i in range(1, len(common)):
        pa, ca = a_map[common[i - 1]], a_map[common[i]]
        pb, cb = b_map[common[i - 1]], b_map[common[i]]
        if pa and pb:
            ra.append(ca / pa - 1.0)
            rb.append(cb / pb - 1.0)
    return _pearson(ra, rb)


def _member_momentum_score(metrics: dict, expense_ratio: float,
                           weights: dict, er_mult: float) -> float | None:
    """0.5·r120 + 0.3·r60 + 0.2·r252 (weights renormalized over the windows that have
    history) minus the static expense-ratio penalty. None if no window has history."""
    terms = 0.0
    wsum = 0.0
    for key, default in (("r120", 0.5), ("r60", 0.3), ("r252", 0.2)):
        w = float(weights.get(key, default))
        v = metrics.get(key)
        if v is not None:
            terms += w * float(v)
            wsum += w
    if wsum <= 0:
        return None
    return terms / wsum - er_mult * float(expense_ratio or 0.0)


def _resolve_effective_incumbent(cfg_selected: str, pinned: bool, prev: dict) -> str:
    """The effective incumbent for a scorecard role, given its PERSISTED state
    (SleeveSelectionState) — shared by `_build_sleeve_selection` and
    `_effective_selected_map` so the two can never disagree on what "effective"
    means for the same input state (session 2026-07-27, blanket auto-switch).

    `"pin": true` (config) always wins: config is the live authority. Otherwise, an
    UNPINNED role whose config `selected` changed since the last run
    (`config_selected` mismatch — a human commit) is treated as freshly
    authoritative (D-G2): the config edit is adopted immediately, never silently
    shadowed by a stale auto-switch. Absent either condition, the persisted
    `selected` (which may be an auto-switched challenger) is the incumbent — falling
    back to config on a role never seen before (empty/missing prior state).
    """
    prev_cfg = (prev.get("config_selected") or "").upper()
    if pinned or (prev_cfg and prev_cfg != cfg_selected):
        return cfg_selected
    return (prev.get("selected") or cfg_selected).upper()


def _effective_selected_map(roles: list[dict], streak_state: dict) -> dict[str, str]:
    """role_id -> effective incumbent ticker for every SCORECARD role, resolved
    purely from PERSISTED state (no scores/metrics) — the safe, always-available
    basis every non-report consumer (price/earnings universe, reference weights,
    the D2 zeroing loop, quadrant bucketing, the Tier-1 validator) reads. Rotation
    roles (intl_broad/intl_leader) and the cash role are excluded — untouched by
    this feature (intl_leader keeps its own leader_pick auto-rotation path)."""
    out: dict[str, str] = {}
    for r in roles:
        if r.get("selection") != "scorecard":
            continue
        rid = r["role_id"]
        cfg_selected = (r.get("selected") or "").upper()
        pinned = bool(r.get("pin"))
        prev = streak_state.get(rid) or {}
        out[rid] = _resolve_effective_incumbent(cfg_selected, pinned, prev)
    return out


def _substitution_map(roles: list[dict], effective_selected: dict[str, str]) -> dict[str, str]:
    """config `selected` ticker -> effective ticker, only where an unpinned
    auto-switch has moved them apart. Lets a name statically listed in config
    elsewhere (e.g. `risk-limits.json`'s `no_read_ballast.ballast_names`) be
    remapped to the live incumbent instead of routing weight to a name the D2
    zeroing loop is about to zero (session 2026-07-27, Change 2 item 4)."""
    out: dict[str, str] = {}
    for r in roles:
        if r.get("selection") != "scorecard":
            continue
        rid = r["role_id"]
        cfg_sel = (r.get("selected") or "").upper()
        eff = effective_selected.get(rid)
        if cfg_sel and eff and cfg_sel != eff:
            out[cfg_sel] = eff
    return out


def _build_sleeve_selection(roles: list[dict], metrics_by_ticker: dict,
                            streak_state: dict, cfg: dict) -> tuple[dict, dict]:
    """Rank each SCORECARD role's pool. Returns (block, new_streak_state).

    A member is INELIGIBLE this run if its 120d return correlation to the role
    benchmark_proxy < min_benchmark_corr (no off-role chasing). The `switch_signal`
    fires only under hysteresis: a challenger must lead the incumbent by >=
    hysteresis_lead for >= hysteresis_runs consecutive runs (streak persisted, reset
    on lead loss / challenger change).

    Blanket autonomous switching (session 2026-07-27): a `switch_signal` on an
    UNPINNED role auto-advances the streak state's `selected` field — this IS the
    effective incumbent (see `_resolve_effective_incumbent` / `_effective_selected_map`,
    consumed by reference weights, the D2 zeroing loop, quadrant bucketing, and the
    Tier-1 validator). `sleeve-roles.json`'s `selected` is the baseline/pin, not the
    live authority: `"pin": true` disables auto-switch for that role and its state is
    forced back to config every run; an unpinned role whose config `selected` changed
    since the last run is adopted as freshly authoritative (D-G2), resetting the
    streak. A challenger inside LEGACY_EXITS is never adopted (it would create a
    buy-side deadlock — legacy names can never be bought back into core, so a
    reference that targeted one would be structurally unfillable, the VXUS pattern).
    """
    weights = cfg.get("momentum_weights") or {"r120": 0.5, "r60": 0.3, "r252": 0.2}
    er_mult = float(cfg.get("expense_penalty_mult", 1.0))
    min_corr = float(cfg.get("min_benchmark_corr", 0.6))
    lead_thr = float(cfg.get("hysteresis_lead", 2.0))
    runs_thr = int(cfg.get("hysteresis_runs", 10))

    out_roles: list[dict] = []
    new_state: dict = {}
    for r in roles:
        if r.get("selection") != "scorecard":
            continue
        rid = r["role_id"]
        cfg_selected = (r.get("selected") or "").upper()
        pinned = bool(r.get("pin"))
        pool = [str(m).upper() for m in r.get("pool", [])]
        ers = {str(k).upper(): v for k, v in (r.get("expense_ratio") or {}).items()}

        prev = streak_state.get(rid) or {}
        incumbent = _resolve_effective_incumbent(cfg_selected, pinned, prev)

        scores: dict = {}
        ineligible: list[str] = []
        for m in pool:
            mt = metrics_by_ticker.get(m) or {}
            corr = mt.get("corr_bench_120d")
            if m != incumbent and corr is not None and corr < min_corr:
                ineligible.append(m)
                continue
            s = _member_momentum_score(mt, ers.get(m, 0.0), weights, er_mult)
            scores[m] = round(s, 2) if s is not None else None
        inc_score = scores.get(incumbent)
        cand = [(m, s) for m, s in scores.items() if m != incumbent and s is not None]
        challenger: str | None = None
        lead = 0.0
        if cand:
            challenger, ch_score = max(cand, key=lambda kv: kv[1])
            if inc_score is not None:
                lead = round(ch_score - inc_score, 2)

        if challenger and inc_score is not None and lead >= lead_thr:
            streak = prev.get("streak", 0) + 1 if prev.get("challenger") == challenger else 1
            switch_now = (streak >= runs_thr and not pinned
                          and challenger not in LEGACY_EXITS)
            new_sel = challenger if switch_now else incumbent
            new_state[rid] = {
                "challenger": None if switch_now else challenger,
                "streak": 0 if switch_now else streak,
                "selected": new_sel,
                "config_selected": cfg_selected,
            }
        else:
            streak = 0
            switch_now = False
            new_state[rid] = {
                "challenger": None,
                "streak": 0,
                "selected": incumbent,
                "config_selected": cfg_selected,
            }
        out_roles.append({
            "role_id": rid,
            "incumbent": incumbent,
            "config_selected": cfg_selected,
            "effective_selected": new_state[rid]["selected"],
            "scores": scores,
            "ineligible": ineligible,
            "challenger": challenger,
            "lead": lead,
            "streak": streak,
            "switch_signal": streak >= runs_thr,
            "auto_switched": switch_now,
            "pinned": pinned,
        })
    return (
        {
            "available": True,
            "roles": out_roles,
            "_note": (
                "A switch_signal on an UNPINNED role auto-advances the EFFECTIVE "
                "incumbent via SleeveSelectionState (logged as a sleeve_switch "
                "OverrideHistory row, graded by Phase C vs the incumbent "
                "counterfactual); `sleeve-roles.json`'s `selected` is the "
                "baseline/pin, not the live authority — an unpinned config edit is "
                "adopted as freshly authoritative on the next run, and "
                "`\"pin\": true` on a role disables auto-switch and reverts it to "
                "config."
            ),
        },
        new_state,
    )


def _build_role_selection(
    roles: list[dict], intl_leader_pick: str | None,
    effective_selected: dict[str, str] | None = None,
) -> dict:
    """Static `selected` (config baseline/pin) + EFFECTIVE `selected` incumbent per
    role, EVERY role (session 2026-07-17, Task C; extended 2026-07-27 for the
    blanket scorecard auto-switch) — `sleeve_selection` (Task E, above) only ranks
    "scorecard" roles' candidate pools, so a "rotation" role like `intl_leader`
    never appears there at all. That left the model nothing to check before
    conflating `intl_governance`'s RUNTIME `leader_pick` going null (normal daily
    de-rotation modulation — the lead faded, not a deselection) with an actual
    deselection of the role's `selected` member (2026-07-17: the model proposed
    selling AIA's 1-share floor because `leader_pick` went null, which the Tier-1
    validator correctly rejected).

    `selected` is the config baseline/pin — it changes ONLY via a committed
    `sleeve-roles.json` edit. `effective_selected` (session 2026-07-27) is the
    LIVE incumbent a scorecard role's `switch_signal` may have already advanced to
    via `SleeveSelectionState` (auto-advance on an unpinned role); it equals
    `selected` for every rotation/cash role and for any scorecard role that hasn't
    auto-switched (or is pinned). The floor guarantee follows `effective_selected`,
    not the static config value — a deselected-by-state member (D-G1) loses it.
    Describe-only — never trades, never itself edits `selected`.
    """
    eff = effective_selected or {}
    out = []
    for r in roles:
        rid = r.get("role_id")
        cfg_selected = (r.get("selected") or "").upper()
        entry = {
            "role_id": rid,
            "selected": cfg_selected,
            "effective_selected": eff.get(rid, cfg_selected),
            "selection": r.get("selection"),
        }
        if rid == "intl_leader":
            entry["leader_pick"] = intl_leader_pick
            entry["note"] = (
                "`selected` changes ONLY via a committed sleeve-roles.json edit "
                "(a human disposing of a switch_signal/rotation proposal); "
                "`leader_pick` is runtime rotation modulation (intl_governance) and "
                "can go null/0 without deselecting `selected` — a null leader_pick "
                "alone is never grounds to propose selling the selected member's "
                "floor position."
            )
        out.append(entry)
    return {
        "roles": out,
        "_note": (
            "`selected` is the config baseline/pin (changes only via a committed "
            "edit); `effective_selected` is the LIVE incumbent, which a scorecard "
            "role's switch_signal may have already auto-advanced to via "
            "SleeveSelectionState (logged sleeve_switch, graded by Phase C) — "
            "`\"pin\": true` on a role keeps the two identical. The floor guarantee "
            "follows effective_selected: it keeps its 0.1%/≥1-share floor regardless "
            "of runtime modulation (leader_pp=0, leader_pick=null, switch_signal "
            "true), while a member DESELECTED by an auto-switch loses the floor "
            "(sell-to-zero, same as a committed config change)."
        ),
    }


def _sleeve_selection_metrics(fmp: FMPClient, roles: list[dict],
                              cache: dict[str, dict[str, float]] | None = None) -> dict:
    """Fetch EOD closes for every scorecard-role pool member + benchmark (cached, one
    FMP call each) and reduce to {ticker: {r60, r120, r252, corr_bench_120d}}.

    ``cache`` (a ``{ticker: {date: close}}`` dict) may be passed by the caller so the
    populated closes are reused downstream (e.g. ``_build_flex_quadrant``'s benchmark
    5d returns) at zero extra FMP cost. Populated in place; own dict if omitted."""
    cache = cache if cache is not None else {}

    def _closes(t: str) -> dict[str, float]:
        if t not in cache:
            cache[t] = _close_by_date(fmp, t)
        return cache[t]

    metrics: dict = {}
    for r in roles:
        if r.get("selection") != "scorecard":
            continue
        bench = (r.get("benchmark_proxy") or "").upper()
        bench_map = _closes(bench) if bench else {}
        for m in [str(x).upper() for x in r.get("pool", [])]:
            cm = _closes(m)
            met = _returns_from_closes(cm)
            met["corr_bench_120d"] = (
                1.0 if m == bench else _corr_daily_returns(cm, bench_map, _SLEEVE_CORR_WINDOW)
            )
            metrics[m] = met
    return metrics


def _load_sleeve_streak_state() -> dict:
    """Per-role hysteresis streak + last-seen selected (+ `config_selected`, the
    D-G2 adoption anchor, session 2026-07-27) from Table Storage (→ {}). A row
    written before this feature lacks `config_selected` — read back as ``None``,
    which `_resolve_effective_incumbent` treats as "no adoption trigger yet"."""
    state: dict = {}
    for e in query_entities(_SLEEVE_STATE_TABLE):
        rid = e.get("RowKey")
        if rid and rid != _INTL_STATE_KEY:
            state[rid] = {
                "challenger": (e.get("challenger") or None) or None,
                "streak": int(e.get("streak") or 0),
                "selected": (e.get("selected") or None) or None,
                "config_selected": (e.get("config_selected") or None) or None,
            }
    return state


def _save_sleeve_streak_state(new_state: dict) -> None:
    for rid, s in (new_state or {}).items():
        upsert_entity(_SLEEVE_STATE_TABLE, {
            "PartitionKey": "state",
            "RowKey": rid,
            "challenger": s.get("challenger") or "",
            "streak": int(s.get("streak") or 0),
            "selected": s.get("selected") or "",
            "config_selected": s.get("config_selected") or "",
        })


# ---------------------------------------------------------------------------
# International governance (Task F — FOLLOWUPS #36; roster_revision_2026-07 §4)
#
# The intl sleeve is governed by the ROTATION score + the DXY switch, NOT the US
# quadrant. Leader-selective: a small broad base (intl_broad) carries policy weight and
# a rotation-sized leader slot (intl_leader) concentrates into the actual leader. This
# REPLACES the 2026-07 INTERIM "closed gate → suppress rotation tilt to zero" rule (a
# closed gate now HALVES the leader tilt, never zeroes it). Pure + echoed.
# ---------------------------------------------------------------------------
_INTL_STATE_TABLE = "SleeveSelectionState"
_INTL_STATE_KEY = "intl_governance"
_MA_RANK = {"bullish_intl": 0, "mixed": 1, "bearish_intl": 2}


def _rotation_macross_signal(regional_rotation: dict, ticker: str) -> str | None:
    row = (regional_rotation.get("ratio_ma_cross") or {}).get(f"{ticker.upper()}/SPY")
    return (row or {}).get("signal") if isinstance(row, dict) else None


def _build_intl_governance(regional_rotation: dict, regime_gate: dict, market_shock: dict,
                           intl_leader_pool: list[str], broad_selected: str,
                           prev_state: dict, cfg: dict) -> tuple[dict, dict]:
    """Rotation/DXY-governed international sleeve sizing (roster_revision_2026-07 §4).

    Returns (block, new_state). The leader slot follows `leader_pick` (restricted to the
    intl_leader pool, excess >= leader_min_excess_pp, MA cross not bearish_intl; tie-broken
    bullish_intl > mixed). Sizing ladder off the rotation composite, modified by the DXY
    anti-chase (headwind → 0, neutral → halve) then the gate (closed → halve again, never
    zero). De-rotation unwinds the leader slot to floor when the pick loses leader status
    or its MA cross turns bearish, or the composite fades from >=7 to <=5.
    """
    base_pp = float(cfg.get("intl_base_pp", 2.0))
    tilt_mid = float(cfg.get("leader_tilt_mid_pp", 1.0))
    tilt_high = float(cfg.get("leader_tilt_high_pp", 3.0))
    min_excess = float(cfg.get("leader_min_excess_pp", 5.0))
    max_leaders = int(cfg.get("max_leaders_high", 2))

    rr = regional_rotation or {}
    composite = (rr.get("rotation_score") or {}).get("composite")
    category = (rr.get("rotation_score") or {}).get("category")
    dxy = rr.get("dxy_tailwind_for_intl")
    gate = str((regime_gate or {}).get("status") or "").lower()
    shock = (market_shock or {}).get("shock_level")
    pool = {str(t).upper() for t in intl_leader_pool}
    broad_selected = (broad_selected or "").upper()

    # Eligible leaders: in pool, excess >= min, MA cross not bearish.
    elig: list[tuple[str, float, str]] = []
    for row in rr.get("leaders_vs_spy") or []:
        t = str(row.get("ticker") or "").upper()
        ex = row.get("excess_pp")
        sig = _rotation_macross_signal(rr, t) or "mixed"
        if t in pool and ex is not None and float(ex) >= min_excess and sig != "bearish_intl":
            elig.append((t, float(ex), sig))
    elig.sort(key=lambda x: (_MA_RANK.get(x[2], 1), -x[1]))
    leaders = [t for t, _, _ in elig]
    leader_pick = leaders[0] if leaders else None

    prev_leader = (prev_state or {}).get("leader") or None
    prev_comp = (prev_state or {}).get("composite")

    if composite is None:
        block = {
            "available": True, "status": "indeterminate", "rotation_composite": None,
            "category": category, "dxy": dxy, "gate": gate,
            "leaders_in_pool": [], "leader_pick": None, "leader_picks": [],
            "broad_target": broad_selected, "broad_pp": round(base_pp, 2),
            "leader_pp": 0.0, "sleeve_target_pp": round(base_pp, 2),
            "intl_targets_pct": {broad_selected: round(base_pp, 2)} if broad_selected else {},
            "modifiers": ["rotation_composite_indeterminate"],
            "de_rotation": {"triggered": False, "trigger": None, "prior_leader": prev_leader},
            "shock_level": shock,
            "_note": "Rotation composite unavailable — hold the broad base only, no leader tilt.",
        }
        return block, {"leader": None, "composite": None}

    if composite <= 3:
        leader_tilt = 0.0
    elif composite <= 6:
        leader_tilt = tilt_mid
    else:
        leader_tilt = tilt_high

    modifiers: list[str] = []
    if dxy == "headwind":
        leader_tilt = 0.0
        modifiers.append("dxy_headwind_zeroed")
    elif dxy == "neutral":
        leader_tilt /= 2.0
        modifiers.append("dxy_neutral_halved")
    if gate == "closed":
        leader_tilt /= 2.0
        modifiers.append("gate_closed_halved")   # REPLACES the interim suppress-to-zero
    if shock in (2, 3):
        modifiers.append(f"shock_level_{shock}_tilt_limits_lifted")

    # De-rotation echo.
    de_rot = {"triggered": False, "trigger": None, "prior_leader": prev_leader}
    if prev_leader and prev_leader not in leaders:
        trig = "ma_bearish" if _rotation_macross_signal(rr, prev_leader) == "bearish_intl" \
            else "leader_lost_status"
        de_rot = {"triggered": True, "trigger": trig, "prior_leader": prev_leader}
        leader_tilt = 0.0
    elif prev_comp is not None and float(prev_comp) >= 7 and composite <= 5:
        de_rot = {"triggered": True, "trigger": "composite_fade", "prior_leader": prev_leader}

    if not leader_pick:
        leader_tilt = 0.0

    leader_pp = round(leader_tilt, 2)
    broad_pp = round(base_pp, 2)
    sleeve_target_pp = round(broad_pp + leader_pp, 2)

    # Up to 2 leaders only at high composite with a positive tilt.
    picks = (leaders[:max_leaders] if (composite >= 7 and leader_pp > 0)
             else ([leader_pick] if (leader_pick and leader_pp > 0) else []))
    intl_targets: dict[str, float] = {}
    if broad_selected:
        intl_targets[broad_selected] = broad_pp
    if picks and leader_pp > 0:
        per = round(leader_pp / len(picks), 3)
        for p in picks:
            intl_targets[p] = intl_targets.get(p, 0.0) + per

    block = {
        "available": True, "status": "active", "rotation_composite": composite,
        "category": category, "dxy": dxy, "gate": gate,
        "leaders_in_pool": [{"ticker": t, "excess_pp": ex, "ma_signal": sig} for t, ex, sig in elig],
        "leader_pick": leader_pick, "leader_picks": picks,
        "broad_target": broad_selected, "broad_pp": broad_pp, "leader_pp": leader_pp,
        "sleeve_target_pp": sleeve_target_pp, "intl_targets_pct": intl_targets,
        "modifiers": modifiers, "de_rotation": de_rot, "shock_level": shock,
        "_note": (
            "Rotation/DXY-governed intl sleeve. The leader slot follows leader_pick "
            "(sell-old/buy-new at the sleeve target); logged to OverrideHistory "
            "(intl_leader_rotation) for Phase C grading."
        ),
    }
    return block, {"leader": leader_pick, "composite": composite}


def _load_intl_state() -> dict:
    for e in query_entities(_INTL_STATE_TABLE):
        if e.get("RowKey") == _INTL_STATE_KEY:
            comp = e.get("composite")
            return {
                "leader": (e.get("leader") or None) or None,
                "composite": float(comp) if comp not in (None, "") else None,
            }
    return {}


def _save_intl_state(new_state: dict) -> None:
    comp = (new_state or {}).get("composite")
    upsert_entity(_INTL_STATE_TABLE, {
        "PartitionKey": "state",
        "RowKey": _INTL_STATE_KEY,
        "leader": (new_state or {}).get("leader") or "",
        "composite": "" if comp is None else float(comp),
    })


def _build_sleeve_switch_records(prev_streak: dict, new_streak: dict,
                                 prev_leader: str | None, leader_pick: str | None,
                                 date: str) -> list[dict]:
    """OverrideHistory-shaped records for APPLIED role changes (Task G / Phase C).

    A `sleeve_switch` row per role whose `selected` changed since the last run (a human
    committed a new incumbent), and an `intl_leader_rotation` row when the intl leader
    pick rotated. Each is later graded vs the INCUMBENT counterfactual (did the new
    member beat the one it replaced) at 30/60/90d. Write-once; outcome hooks null.
    """
    ym = date[:7]
    tag = date.replace("-", "")
    records: list[dict] = []
    for rid, ns in (new_streak or {}).items():
        prev_sel = (prev_streak.get(rid) or {}).get("selected")
        cur_sel = ns.get("selected")
        if prev_sel and cur_sel and prev_sel != cur_sel:
            records.append({
                "PartitionKey": ym, "RowKey": f"SW-{tag}-{rid}",
                "recommended_at": date, "layer": "sleeve_switch", "role_id": rid,
                "sleeve": rid.upper(), "incumbent": prev_sel, "new_member": cur_sel,
                "outcome_status": "", "resolved_correct": None,
            })
    if leader_pick and prev_leader and prev_leader != leader_pick:
        records.append({
            "PartitionKey": ym, "RowKey": f"ILR-{tag}",
            "recommended_at": date, "layer": "intl_leader_rotation", "role_id": "intl_leader",
            "sleeve": "INTL_LEADER", "incumbent": prev_leader, "new_member": leader_pick,
            "outcome_status": "", "resolved_correct": None,
        })
    return records


def _grade_switch(incumbent_ret_pct: float | None, new_ret_pct: float | None) -> dict | None:
    """Grade a switch/rotation vs the incumbent counterfactual: correct if the new member
    outperformed the one it replaced. None when either return is unavailable."""
    if incumbent_ret_pct is None or new_ret_pct is None:
        return None
    excess = float(new_ret_pct) - float(incumbent_ret_pct)
    return {"resolved_correct": excess > 0, "excess_pp": round(excess, 3)}


def _build_reference_weights(
    paper_account: dict,
    growth_axis: dict,
    inflation_axis: dict,
    regime_gate: dict,
    regional_rotation: dict,
    bond_signals: dict,
    labor_signals: dict,
    market_shock: dict,
    cfg: dict,
    transition_watch: dict | None = None,
    intl_governance: dict | None = None,
    effective_selected: dict[str, str] | None = None,
    thematic_conviction: dict | None = None,
) -> dict:
    """Deterministic per-ticker REFERENCE allocation the analyzer executes toward.

    This is the missing strategy-spec §10 layer ("precomputed target weights"). It is
    a *reference, not a mandate*: the analyzer reasons against it and may deviate only
    via a falsifiable, magnitude-bounded, logged override (brief Phase 4). Computing it
    deterministically removes the unanchored call→target→trades leap where the book
    rationalized silent inaction.

    Pipeline (spec §2/§3/§4/§8):
      1. Conviction proxy → active-quadrant target % of CORE via the ladder.
      2. Active quadrant from the two axes; borderline (flat/unknown axis) → the
         intersection blend (concentrate the cross-regime names, stage the divergent).
      3. Distribute the active-quadrant target across its §3 concentrate names; split
         the amplifier US-vs-intl by the DXY switch (§4). Non-active core names go to
         the 0.1% floor.
      4. Apply Tier-1 constraints: cash sleeve carved out (5–15%, shock-3 → 25%); core
         scaled into the remaining room under the 90%-of-core ceiling; AMZN/GOOGL never
         below current weight; single-name soft cap. Renormalize to ~100%.

    Targets are % of EQUITY. Echo-not-re-derive. Non-fatal in the caller. Returns
    ``available: False`` if the paper account is unavailable.
    """
    if not (paper_account or {}).get("available"):
        return {"available": False, "reason": "paper_account unavailable"}
    equity = float(paper_account.get("equity") or 0) or 0.0
    if equity <= 0:
        return {"available": False, "reason": "no equity"}

    positions = paper_account.get("positions") or []
    cur_w = {
        (p.get("ticker") or "").upper(): float(p.get("market_value") or 0) / equity * 100.0
        for p in positions if p.get("ticker")
    }

    floor = float(cfg["sleeve_floor_pct_of_core"])
    ceiling_core = float(cfg["active_quadrant_ceiling_pct_of_core"])
    cash_band = cfg["cash_sleeve_band_pct"]
    soft_cap = float(cfg["single_name_cap_pct"]["any_name_soft"])
    exempt = set(cfg.get("exempt_holds", EXEMPT_HOLDS))

    # International sleeve (Task F) — sized in %-of-EQUITY by intl_governance (rotation +
    # DXY), a SEPARATE sleeve carved out of the core room like cash. Its pool members are
    # excluded from the quadrant core math (they carry no US-quadrant label).
    ig = intl_governance or {}
    intl_targets = {str(t).upper(): float(v) for t, v in (ig.get("intl_targets_pct") or {}).items()}
    intl_total_pct = float(ig.get("sleeve_target_pp") or 0.0) if ig.get("available") else 0.0

    # Session 2026-07-15 (Task C, decision C0 = Option 1): intl_broad's base target is
    # gated to 0 while the deployment gate is closed. intl_broad is unconditionally
    # `block: amplifier_intl` (sleeve-roles.json), so the Tier-1 validator rejects its
    # buy on every closed-gate day regardless of the rotation score — confirmed
    # 2026-07-14 AND 2026-07-15, both "amplifier buy VXUS forbidden". The 2.0pp base
    # target was therefore structurally unreachable, not merely deferred: a wasted
    # trade slot every day and (until Finding B's two-pass validation) starved cash
    # available for band enforcement. Zeroing it here is self-healing — this function
    # reruns every day, so the target restores automatically the day the gate opens.
    # The LEADER slot is untouched (intl_governance already halves — never zeroes — it
    # on a closed gate; it stays rotation-governed per roster_revision_2026-07 §4). A
    # held intl_broad position is not force-sold: a held-vs-0 gap sits inside the
    # override band, and this only stops a fresh gate-closed BUY from being proposed.
    gate_status = str((regime_gate or {}).get("status") or "").lower()
    broad_ticker = str(ig.get("broad_target") or "").upper()
    if gate_status == "closed" and broad_ticker and broad_ticker in intl_targets:
        intl_total_pct = max(0.0, intl_total_pct - intl_targets.pop(broad_ticker))

    intl_pool: set[str] = set()
    for _r in roles_config():
        if _r.get("quadrants") == "rotation":
            for _m in _r.get("pool", ()):
                intl_pool.add(str(_m).upper())

    # --- 1. conviction proxy → active-quadrant target (% of core) ---------------
    proxy = _conviction_proxy(
        growth_axis, inflation_axis, regime_gate, bond_signals, labor_signals, market_shock
    )
    active_target_core, conviction_label = _ladder_target_pct(
        cfg["conviction_ladder_pct_of_core"], proxy["score"]
    )
    active_target_core = min(active_target_core, ceiling_core)  # never exceed ceiling

    # --- 2. active quadrant / borderline bucket ---------------------------------
    g = (growth_axis or {}).get("direction")
    i = (inflation_axis or {}).get("direction")
    quad = active_quadrant(g, i)
    bucket = favored_bucket(g, i)
    borderline = quad == ""

    # --- 3. DXY switch (§4): amplifier US vs international -----------------------
    dxy_tag = (regional_rotation or {}).get("dxy_tailwind_for_intl")  # tailwind/neutral/headwind
    intl_lean = dxy_tag == "tailwind"   # falling dollar favors international

    # Names to concentrate into + their raw shares of the active-quadrant target.
    if borderline:
        # Intersection blend: cross-regime names take the lion's share; the divergent
        # (single-bucket) names are staged at partial size. Never a freeze.
        inter = intersection_names(bucket, effective_selected)
        union = []
        for q in bucket:
            for t in concentrate_names(q, effective_selected):
                if t not in union:
                    union.append(t)
        divergent = [t for t in union if t not in inter]
        blend = cfg["borderline_blend"]
        inter_share = float(blend["intersection_target_pct_of_core"])
        div_share = float(blend["divergent_staged_pct_of_core"])
        raw_core: dict[str, float] = {}
        if inter:
            per = inter_share / len(inter)
            for t in inter:
                raw_core[t] = raw_core.get(t, 0.0) + per
        if divergent:
            per = div_share / len(divergent)
            for t in divergent:
                raw_core[t] = raw_core.get(t, 0.0) + per
        concentrate = list(raw_core.keys())
    else:
        concentrate = list(concentrate_names(quad, effective_selected))
        raw_core = {}
        if concentrate:
            # Split the active-quadrant target. If the quadrant has an amplifier
            # (Q1/Q2), bias the US-vs-intl halves by the dollar switch; otherwise
            # equal-weight the concentrate names.
            amp = [t for t in concentrate if is_amplifier(t, effective_selected)]
            non_amp = [t for t in concentrate if not is_amplifier(t, effective_selected)]
            if amp and (quad in ("Q1", "Q2")):
                intl = [t for t in amp if t in set(AMPLIFIER_INTL)]
                us = [t for t in amp if t not in set(AMPLIFIER_INTL)]
                # 65/35 lean toward the favored leg; 50/50 if a leg is empty.
                us_share, intl_share = (0.35, 0.65) if intl_lean else (0.65, 0.35)
                if not intl:
                    us_share, intl_share = 1.0, 0.0
                if not us:
                    us_share, intl_share = 0.0, 1.0
                amp_target = active_target_core * (len(amp) / len(concentrate))
                for t in us:
                    raw_core[t] = raw_core.get(t, 0.0) + amp_target * us_share / max(1, len(us))
                for t in intl:
                    raw_core[t] = raw_core.get(t, 0.0) + amp_target * intl_share / max(1, len(intl))
                rest = active_target_core - amp_target
                for t in non_amp:
                    raw_core[t] = raw_core.get(t, 0.0) + rest / max(1, len(non_amp))
            else:
                per = active_target_core / len(concentrate)
                for t in concentrate:
                    raw_core[t] = raw_core.get(t, 0.0) + per

    # --- 3a. no-read ballast (fix for the degenerate low-conviction reference) ----
    # In a low-conviction / no-read regime the spec (Calculated Risk Score 7–10) says:
    # overweight GLD + long-duration Treasuries, push cash toward its ceiling, minimal
    # quadrant bet. Without this the active-quadrant target is tiny and the AMZN/GOOGL
    # exemption balloons on renormalize (observed 2026-07-01: GOOGL 38% / AMZN 22%).
    # Route the bulk of the core to the ballast names instead, so the book reads as
    # capital-preservation, not mega-cap-tech-heavy.
    nrb = cfg.get("no_read_ballast") or _RISK_LIMITS_DEFAULTS["no_read_ballast"]
    no_read = proxy["score"] >= float(nrb.get("conviction_score_min", 7.0))
    if no_read:
        # Session 2026-07-27: route each static ballast name through `substitution`
        # (config ticker -> live auto-switched ticker) FIRST. Without this, a
        # ballast name whose role has since auto-switched (e.g. a future gold
        # GLD→GLDM switch) would have its ~55%-of-core weight assigned here, only
        # for the D2 zeroing loop below to zero it right back (GLD is no longer
        # `gold`'s effective selected member) — a degenerate no-read reference that
        # silently drops the ballast money instead of routing it to GLDM.
        _ballast_sub = _substitution_map(roles_config(), effective_selected or {})
        ballast = [_ballast_sub.get(t, t) for t in nrb.get("ballast_names", ["GLD", "TLT"])]
        ballast = [t for t in ballast if t in CORE_ROSTER]
        if ballast:
            ballast_share = float(nrb.get("ballast_target_pct_of_core", 55.0))
            per = ballast_share / len(ballast)
            for t in ballast:
                raw_core[t] = raw_core.get(t, 0.0) + per

    # --- 3b. transition_watch lean (Phase 3): bounded partial pre-stage toward the -
    # projected quadrant WITHOUT moving the binding quad/gate/axis. Convex blend of the
    # base allocation with a projected-quadrant allocation of the same budget:
    #   raw_core = (1 - f) * base + f * projected   (f = staged_fraction, <= 0.30)
    # Never a full flip; preserves the total budget. Deterministic; reuses the Phase-2
    # divergence via _build_transition_watch (passed in), no re-derivation here.
    tw = transition_watch or {}
    tw_applied = False
    if tw.get("active") and tw.get("projected_quadrant"):
        f = float(tw.get("staged_fraction") or 0.0)
        proj_names = list(concentrate_names(tw["projected_quadrant"], effective_selected))
        base_total = sum(raw_core.values())
        if f > 0 and proj_names and base_total > 0:
            blended = {t: w * (1.0 - f) for t, w in raw_core.items()}
            per_proj = (base_total * f) / len(proj_names)
            for t in proj_names:
                blended[t] = blended.get(t, 0.0) + per_proj
            raw_core = blended
            tw_applied = True

    # --- 4. assemble core targets: floor everything, then the concentrate names --
    core_target: dict[str, float] = {t: floor for t in CORE_ROSTER}
    for t, w in raw_core.items():
        if t in core_target:
            core_target[t] = max(floor, w)

    # Legacy exits get a reference target of 0 (liquidate, never re-buy into core). They
    # stay in CORE_ROSTER so a HELD legacy name still produces a gap row (reference 0)
    # the validator can size an exit sell against — but they are never floored.
    for t in LEGACY_EXITS:
        if t in core_target:
            core_target[t] = 0.0

    # International pool members carry no US-quadrant label — zero them in the core math;
    # the intl sleeve is added back below from intl_governance (% of equity).
    for t in intl_pool:
        if t in core_target:
            core_target[t] = 0.0

    # B1 (decision D2, 2026-07-21): zero every NON-SELECTED pool member, completing the
    # PR #24 Option-1 doctrine. Step 4 floored every CORE_ROSTER name, but only a role's
    # `selected` incumbent is a reference target — the other pool members (SOXX/PAVE/XLB/
    # GLDM/IAU/IHE/STIP/DBMF/CTA/SPLV while SMH/XLI/COWZ/GLD/VTIP/KMLM/XLV/USMV are
    # selected) each kept the 0.1%-of-core floor ≈ 0.092% of equity ≈ 1.01% of the
    # reference that is permanently UNFILLABLE (V1.5 rejects their buys; the V3 floor-
    # bypass sells them to zero if ever held). That surfaced as a phantom `unclassified`
    # bucket in `by_quadrant` and a structural pad on the apparent cash overweight. The
    # selected member keeps its floor, so a future `selected` commit (e.g. XLV→IHE)
    # transfers the floor+reference to the new incumbent on the next collector run —
    # the mechanism follows the config, not the ticker. Rotation roles already zeroed.
    #
    # Session 2026-07-27: keyed on the EFFECTIVE selected set, not the frozen config
    # one — an auto-switched role's OLD incumbent (e.g. XLV after healthcare_def
    # switches to IHE) must be zeroed here (D-G1: sell-to-zero, committed-switch
    # parity), while the NEW effective incumbent (IHE) must NOT be — it is what
    # `selected_core_members(effective_selected)` now returns for that role instead
    # of the config value. Left config-based, this loop would zero IHE's freshly
    # substituted target right back the moment it appears in `raw_core`.
    _selected_incumbents = set(selected_core_members(effective_selected))
    for _r in roles_config():
        if _r.get("quadrants") == "rotation":
            continue
        for _m in _r.get("pool", ()):
            _mu = str(_m).upper()
            if _mu not in _selected_incumbents and _mu in core_target:
                core_target[_mu] = 0.0

    # Soft single-name cap applies only to SINGLE STOCKS (idiosyncratic risk), NOT to
    # diversified ETF sleeves — a high-conviction quadrant is *meant* to push one ETF
    # past 15% (capping it here would defeat the concentration this feature enables).
    # Single stocks in the core roster: AMZN, GOOGL, INTC, MCK.
    for t in _CORE_SINGLE_STOCKS:
        if t in core_target:
            core_target[t] = min(core_target[t], soft_cap)

    # --- 5. carve the cash sleeve, then scale core into the remaining room -------
    shock = (market_shock or {}).get("shock_level")
    cash_ceiling = float(cash_band["shock3_ceiling"]) if shock == 3 else float(cash_band["ceiling"])
    cash_floor = float(cash_band["floor"])
    cur_cash_pct = float(paper_account.get("cash") or 0) / equity * 100.0
    cur_sgov_pct = cur_w.get("SGOV", 0.0)
    cur_sleeve = cur_cash_pct + cur_sgov_pct
    # Reference cash sleeve: stay in band; if currently above the ceiling hold at the
    # ceiling (deploy the surplus into core), if below the floor lift to the floor.
    cash_sleeve_target = max(cash_floor, min(cash_ceiling, cur_sleeve))
    core_room = max(0.0, 100.0 - cash_sleeve_target - intl_total_pct)

    # AMZN/GOOGL are permanent holds: pin them at their CURRENT weight and carve that out
    # of the core room as a FIXED slice — do NOT let the renormalize scale them up. (Before
    # this fix the exemption was applied pre-scale, so a small no-read core budget made the
    # scale multiplier huge and ballooned the exempt names to ~60% of the book — the
    # 2026-07-01 GOOGL-38%/AMZN-22% degeneracy.) Exempt names never go below current, never
    # above it purely from scaling.
    exempt_held = {t: cur_w[t] for t in exempt if t in core_target and t in cur_w and cur_w[t] > 0}
    exempt_total = sum(exempt_held.values())
    # SGOV is the cash sleeve, not core concentration; exempt names are fixed. Scale only
    # the remaining (non-exempt, non-SGOV) core into what's left after cash + exempt.
    scalable = {t: w for t, w in core_target.items()
                if t != "SGOV" and t not in exempt_held}
    scalable_room = max(0.0, core_room - exempt_total)
    scale = scalable_room / (sum(scalable.values()) or 1.0)
    weights = {t: round(w * scale, 3) for t, w in scalable.items()}
    for t, w in exempt_held.items():
        weights[t] = round(w, 3)  # pinned at current
    # Cash sleeve = SGOV (yield-bearing) holding all but a ~1.5% literal-cash buffer.
    sgov_w = max(0.0, cash_sleeve_target - _CASH_BUFFER_PCT)
    weights["SGOV"] = round(sgov_w, 3)
    weights["__cash__"] = round(cash_sleeve_target - sgov_w, 3)

    # International sleeve (Task F): add the rotation/DXY-governed intl targets (% of
    # equity) that intl_governance sized. These REPLACE any quadrant math for intl names.
    for t, v in intl_targets.items():
        if v > 0:
            weights[t] = round(float(v), 3)

    # --- 5b. thematic conviction floor-lift (2026-08-14 audit, decision D-5) ----
    # The D5 spec calls this "step 3c" (before step 4's floor assembly) — but
    # `raw_core`/`core_target` at that point are in %-of-CORE units, while
    # `thematic_conviction.active[].applied_pct_of_equity` is %-of-EQUITY by
    # design (D2). The %-of-core -> %-of-equity conversion factor (`scale`,
    # derived from `core_room`) is not known until THIS point (step 5), so the
    # lift is applied here instead, against the now-equity-denominated
    # `weights` dict — functionally identical (still strictly a floor, still
    # never touches anything before the quadrant math has settled) but at the
    # only point in the pipeline where the units actually line up.
    ceiling_pressure = False
    ceiling_pressure_pp = 0.0
    thematic_applied: dict[str, float] = {}
    tc = thematic_conviction or {}
    if tc.get("available") and tc.get("enabled"):
        for entry in tc.get("active", []):
            sym = str(entry.get("symbol") or "").upper()
            lift = float(entry.get("applied_pct_of_equity") or 0.0)
            if not sym or lift <= 0:
                continue
            prior = weights.get(sym, 0.0)
            if lift > prior:
                weights[sym] = round(lift, 3)
                thematic_applied[sym] = round(lift - prior, 3)

        thematic_added_pp = sum(thematic_applied.values())
        if thematic_added_pp > 0:
            # Budget interaction: carve the lift from the non-active-quadrant
            # remainder (the "living hedge" `active_quadrant_ceiling_pct_of_core`
            # leaves inside the core block), never let it silently breach the
            # 90%-of-core ceiling unnoticed.
            active_names = set(concentrate_names(quad, effective_selected)) if quad else set()
            active_quadrant_equity_pct = sum(
                w for t, w in weights.items() if t in active_names
            )
            non_active_room_pct = max(
                0.0, core_room * (100.0 - active_target_core) / 100.0,
            )
            if thematic_added_pp > non_active_room_pct and active_quadrant_equity_pct > 0:
                excess = thematic_added_pp - non_active_room_pct
                trim_scale = max(0.0, 1.0 - excess / active_quadrant_equity_pct)
                for t in active_names:
                    if t in weights and t not in thematic_applied:
                        weights[t] = round(weights[t] * trim_scale, 3)
                ceiling_pressure = True
                ceiling_pressure_pp = round(excess, 3)

    # Deterministic per-quadrant aggregation (Task 5) — the analyzer echoes this
    # verbatim in the Quadrant Allocation table's Reference column instead of summing
    # the per-name references freehand (the 2026-07-09 report claimed Q3 ~42.9% while
    # its own footnote summed to ~58% and the column totalled ~89.5%).
    literal_cash_pct = round(weights.pop("__cash__", 0.0), 3)
    target_pct = {t: w for t, w in sorted(weights.items()) if w >= 0.05}
    by_quadrant = _aggregate_by_quadrant(target_pct, literal_cash_pct, effective_selected)

    # --- which constraints bound (surface, like flex `binding`) -----------------
    binding: list[str] = []
    if active_target_core >= ceiling_core:
        binding.append("active_quadrant_ceiling")
    if cur_sleeve > cash_ceiling:
        binding.append("cash_above_band")
    elif cur_sleeve < cash_floor:
        binding.append("cash_below_band")
    if any(t in exempt and core_target[t] <= cur_w.get(t, 0.0) for t in exempt):
        binding.append("exempt_hold_floor")
    if no_read:
        binding.append("no_read_ballast")

    return {
        "available": True,
        "as_of": (paper_account.get("as_of") or growth_axis.get("as_of")),
        "no_read": no_read,
        "active_quadrant": quad or None,
        "favored_bucket": bucket,
        "borderline": borderline,
        "conviction_proxy": proxy["score"],
        "conviction_label": conviction_label,
        "conviction_drivers": proxy["drivers"],
        "active_quadrant_target_pct_of_core": round(active_target_core, 1),
        "ceiling_pct_of_core": ceiling_core,
        "dollar_tilt": "international" if intl_lean else "us_growth",
        "dxy_tag": dxy_tag,
        "transition_lean": (
            {"applied": True, "projected_quadrant": tw.get("projected_quadrant"),
             "direction": tw.get("direction"), "staged_fraction": tw.get("staged_fraction")}
            if tw_applied else {"applied": False}
        ),
        "thematic_lean": {
            "applied": bool(thematic_applied),
            "lifted": thematic_applied,
            "ceiling_pressure": ceiling_pressure,
            "ceiling_pressure_pp": ceiling_pressure_pp,
        },
        "cash_sleeve_target_pct": round(cash_sleeve_target, 2),
        "literal_cash_target_pct": literal_cash_pct,
        "target_weights_pct": target_pct,
        "by_quadrant": by_quadrant,
        "binding": binding,
        "rule": (
            "Reference allocation the analyzer executes toward — NOT a mandate. Deviate "
            "only via a falsifiable, magnitude-bounded, logged override (de-risk cheap / "
            "re-risk dear). Deterministic + echoed; never re-derive. Active quadrant "
            "capped at the ceiling; every core sleeve floored; AMZN/GOOGL never forced "
            "down; cash sleeve held to its band; flex is a separate sleeve."
        ),
    }


def _load_divergence_config() -> dict:
    """Thresholds for the divergence detector (config/divergence-config.json).

    Missing/invalid → in-module defaults (mirror the file). Tolerant of ``_*`` notes.
    """
    try:
        with open(_DIVERGENCE_CONFIG_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("divergence-config.json missing/invalid — using built-in defaults")
        return dict(_DIVERGENCE_DEFAULTS)
    merged = dict(_DIVERGENCE_DEFAULTS)
    for k, v in data.items():
        if not k.startswith("_"):
            merged[k] = v
    return merged


def _sma_from_rows(rows: list[dict], window: int) -> dict:
    """200-day-style simple moving average from FMP `get_historical_price_light` rows
    (newest-first). Returns ``{available, sma, latest, latest_date, above}`` or
    ``{available: False}`` if fewer than ``window`` closes. Pure — the network fetch
    happens in the orchestration layer; this only reduces already-fetched rows so the
    divergence detector stays deterministic and unit-testable.
    """
    if not rows or len(rows) < window:
        return {"available": False}

    def _close(r: dict) -> float | None:
        v = r.get("price") if r.get("price") is not None else r.get("close")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    closes = [_close(r) for r in rows[:window]]
    if any(c is None for c in closes):
        return {"available": False}
    sma = sum(closes) / window
    latest = closes[0]
    return {
        "available": True,
        "sma": round(sma, 4),
        "latest": round(latest, 4),
        "latest_date": rows[0].get("date"),
        "above": latest > sma,
    }


def _days_stale(as_of: str | None, today: str) -> int | None:
    """Calendar-day age of an ``as_of`` date vs ``today`` (both ISO). None if unparseable."""
    if not as_of:
        return None
    try:
        return (date.fromisoformat(today) - date.fromisoformat(as_of[:10])).days
    except (TypeError, ValueError):
        return None


def _should_use_dollar_proxy(dxy_stale: int | None) -> bool:
    """Whether the FX-pairs dollar_proxy fallback should fire (2026-08-06 audit
    B4). True when DTWEXBGS is >= `_DXY_STALE_FALLBACK_DAYS` calendar days stale
    OR when its cadence can't be evaluated at all (`dxy_stale is None` — zero
    usable observations reached `regional_rotation`, so `dxy_latest_date` was
    never set). Before this fix the trigger was a bare `dxy_stale > 5` — dark at
    EXACTLY 5 days stale, and dark whenever DTWEXBGS returned no usable
    observations at all, leaving the DXY signal blind on both paths at once."""
    return dxy_stale is None or dxy_stale >= _DXY_STALE_FALLBACK_DAYS


# ---------------------------------------------------------------------------
# Task A (FOLLOWUPS #17): Leading-growth composite
# ---------------------------------------------------------------------------
# Diffusion score in [-1, +1] from FRED leading-growth series + market-derived
# signals (copper/gold, XLY/XLP, HY OAS direction). Describe-only; feeds the
# new `leading_vs_lagging_growth` divergence and generalises _build_transition_watch
# to consume it symmetrically with the inflation side. The composite NEVER flips
# the growth axis itself — it feeds divergence/transition machinery; LLM adjudicates.
# ---------------------------------------------------------------------------

def _series_direction(vals: list[float], window: int = 4) -> str | None:
    """'rising'/'falling'/'flat' from a short trailing window of newest-first values.

    Uses the simple slope (last-first) over a rolling window; 'flat' when the
    absolute move is < 20% of the standard deviation across the window, or when
    there are fewer than 2 values. Returns None when insufficient data.
    """
    recent = [v for v in vals[:window] if v is not None]
    if len(recent) < 2:
        return None
    first, last = recent[-1], recent[0]   # oldest→newest in the slice
    diff = last - first
    mean = sum(recent) / len(recent)
    std = (sum((x - mean) ** 2 for x in recent) / len(recent)) ** 0.5
    if std == 0:
        return "flat" if diff == 0 else ("rising" if diff > 0 else "falling")
    if abs(diff) < 0.2 * std:
        return "flat"
    return "rising" if diff > 0 else "falling"


def _price_return_pct(prices: dict, symbol: str, window_td: int) -> float | None:
    """Trailing N-trading-day return (%) for `symbol` from the prices dict.

    `prices` is {symbol: {date: close}} or the flat FMP EOD dict
    {symbol: {"c": close, ...}}. Handles both shapes. Returns None when
    insufficient history or symbol absent.
    """
    sym_data = prices.get(symbol.upper()) or prices.get(symbol)
    if not sym_data:
        return None
    # Historical series shape: {date: close_float} (from _close_by_date).
    # Flat EOD shape: {"c": float, ...} — only the current day; not usable for
    # a window return. Detect by checking for date-keyed entries.
    if isinstance(sym_data, dict):
        date_keys = [k for k in sym_data if isinstance(k, str) and len(k) == 10 and k[4] == '-']
        if date_keys:
            sorted_dates = sorted(date_keys, reverse=True)
            if len(sorted_dates) <= window_td:
                return None
            p_now = sym_data[sorted_dates[0]]
            p_then = sym_data[sorted_dates[window_td]]
            try:
                return (float(p_now) / float(p_then) - 1.0) * 100.0
            except (TypeError, ValueError, ZeroDivisionError):
                return None
    return None


def _ratio_20d_signal(
    close_cache: dict[str, dict[str, float]],
    sym_a: str,
    sym_b: str,
    window: int = 20,
    thr_pct: float = 2.0,
) -> dict:
    """20-trading-day relative-momentum trend of ``sym_a``/``sym_b`` from a
    ``{symbol: {date: close}}`` close cache (e.g. CPER/GLD, XLY/XLP).

    Shared by ``_build_leading_growth`` and ``_build_market_implied_quadrant``
    so the two copper/gold and cyclicals/defensives reads can never drift apart
    (2026-08-06 audit B1/B2 — before this, market_implied_quadrant read these
    ratios off the CORE_ROSTER-only perf-series ``closes`` dict, which never
    contains CPER or XLY, so both votes were structurally always null).

    Returns ``{"direction": "rising"|"falling"|"flat"|None, "pct_change": float|None,
    "as_of": str|None}``. None when fewer than ``window + 1`` common dates.
    """
    a_map = close_cache.get(sym_a) or {}
    b_map = close_cache.get(sym_b) or {}
    common = sorted(set(a_map) & set(b_map), reverse=True)
    if len(common) <= window:
        return {"direction": None, "pct_change": None, "as_of": common[0] if common else None}
    try:
        r_now = a_map[common[0]] / b_map[common[0]]
        r_then = a_map[common[window]] / b_map[common[window]]
        pct = (r_now / r_then - 1.0) * 100.0
    except (ZeroDivisionError, TypeError):
        return {"direction": None, "pct_change": None, "as_of": common[0]}
    direction = "rising" if pct > thr_pct else ("falling" if pct < -thr_pct else "flat")
    return {"direction": direction, "pct_change": round(pct, 2), "as_of": common[0]}


def _build_leading_growth(
    macro_data: dict,
    prices: dict,
    bond_signals: dict,
    close_cache: dict[str, dict[str, float]],
) -> dict:
    """Deterministic leading-growth composite (FOLLOWUPS #17).

    Aggregates FRED high-frequency leading indicators + market-derived signals into
    a diffusion score in [-1, +1]. Score > 0 ⟹ majority of signals improving
    (growth accelerating); < 0 ⟹ majority deteriorating; 0 ⟹ mixed.

    Signal set:
      FRED:   WEI (weekly GDP tracker), NFCI (inverted — tightening = negative),
              PERMIT (building permits), NEWORDER (core capex orders),
              NOCDFSA066MSFRBPHI (Philly Fed new orders),
              GACDISA066MSFRBNY (Empire State general activity)
      Market: CPER/GLD ratio 20d trend (copper/gold growth proxy),
              XLY/XLP ratio 20d trend (cyclicals vs defensives),
              HY OAS direction (inverted: tightening = positive for growth)

    A stale or absent input is DROPPED from the count (never fabricates a vote).
    Confidence degrades with available-input count: full (>=7), medium (4-6), low (2-3).
    """
    def _newest_vals(sid: str, n: int = 6) -> list[float]:
        rows = macro_data.get(sid) or []
        vals: list[float] = []
        for r in rows:
            v = r.get("value")
            if v in (None, ".", ""):
                continue
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                continue
            if len(vals) >= n:
                break
        return vals   # newest-first

    def _as_of(sid: str) -> str | None:
        rows = macro_data.get(sid) or []
        for r in rows:
            if r.get("value") not in (None, ".", ""):
                return r.get("date") or r.get("asof")
        return None

    signals: list[dict] = []
    votes_up = votes_down = 0

    # --- WEI (weekly economic index) ----------------------------------------
    wei = _newest_vals("WEI", 6)
    wei_dir = _series_direction(wei, window=4)
    signals.append({"name": "WEI", "direction": wei_dir, "as_of": _as_of("WEI"),
                    "latest": round(wei[0], 3) if wei else None})
    if wei_dir == "rising":
        votes_up += 1
    elif wei_dir == "falling":
        votes_down += 1

    # --- NFCI (financial conditions; INVERTED — tightening hurts growth) ----
    nfci = _newest_vals("NFCI", 6)
    nfci_dir_raw = _series_direction(nfci, window=4)
    # NFCI rising = tightening = bad for growth → invert
    nfci_dir = ("falling" if nfci_dir_raw == "rising"
                else ("rising" if nfci_dir_raw == "falling" else nfci_dir_raw))
    signals.append({"name": "NFCI_inv", "direction": nfci_dir, "as_of": _as_of("NFCI"),
                    "latest": round(nfci[0], 3) if nfci else None})
    if nfci_dir == "rising":
        votes_up += 1
    elif nfci_dir == "falling":
        votes_down += 1

    # --- PERMIT (building permits, monthly) ---------------------------------
    permit = _newest_vals("PERMIT", 6)
    permit_dir = _series_direction(permit, window=4)
    signals.append({"name": "PERMIT", "direction": permit_dir, "as_of": _as_of("PERMIT"),
                    "latest": round(permit[0], 1) if permit else None})
    if permit_dir == "rising":
        votes_up += 1
    elif permit_dir == "falling":
        votes_down += 1

    # --- NEWORDER (core capex orders, monthly) ------------------------------
    neworder = _newest_vals("NEWORDER", 6)
    neworder_dir = _series_direction(neworder, window=4)
    signals.append({"name": "NEWORDER", "direction": neworder_dir, "as_of": _as_of("NEWORDER"),
                    "latest": round(neworder[0], 1) if neworder else None})
    if neworder_dir == "rising":
        votes_up += 1
    elif neworder_dir == "falling":
        votes_down += 1

    # --- Philly Fed new orders ---------------------------------------------
    phi = _newest_vals("NOCDFSA066MSFRBPHI", 6)
    phi_dir = _series_direction(phi, window=3)
    signals.append({"name": "PhillyFed_neworders", "direction": phi_dir, "as_of": _as_of("NOCDFSA066MSFRBPHI"),
                    "latest": round(phi[0], 1) if phi else None})
    if phi_dir == "rising":
        votes_up += 1
    elif phi_dir == "falling":
        votes_down += 1

    # --- Empire State general activity -------------------------------------
    emp = _newest_vals("GACDISA066MSFRBNY", 6)
    emp_dir = _series_direction(emp, window=3)
    signals.append({"name": "EmpireState", "direction": emp_dir, "as_of": _as_of("GACDISA066MSFRBNY"),
                    "latest": round(emp[0], 1) if emp else None})
    if emp_dir == "rising":
        votes_up += 1
    elif emp_dir == "falling":
        votes_down += 1

    # --- CPER/GLD ratio (copper/gold growth proxy) -- market-derived --------
    # Uses the closes_cache so we don't re-fetch (same data as sleeve scorecard).
    # Shared helper (2026-08-06 audit B1/B2) so this can never drift from the
    # identical read in _build_market_implied_quadrant.
    cg = _ratio_20d_signal(close_cache, "CPER", "GLD")
    cg_dir = cg["direction"]
    signals.append({"name": "CPER_GLD_20d", "direction": cg_dir, "as_of": cg["as_of"],
                    "latest": cg["pct_change"]})
    if cg_dir == "rising":
        votes_up += 1
    elif cg_dir == "falling":
        votes_down += 1

    # --- XLY/XLP ratio (cyclicals vs defensives, 20d) ----------------------
    xl = _ratio_20d_signal(close_cache, "XLY", "XLP")
    xl_dir = xl["direction"]
    signals.append({"name": "XLY_XLP_20d", "direction": xl_dir, "as_of": xl["as_of"],
                    "latest": xl["pct_change"]})
    if xl_dir == "rising":
        votes_up += 1
    elif xl_dir == "falling":
        votes_down += 1

    # --- HY OAS direction (inverted — tightening = positive for growth) -----
    hy_dir_raw = None
    credit = (bond_signals or {}).get("credit") or {}
    hy = credit.get("hy_oas") or {}
    hy_trend = hy.get("trend_4w")  # "tightening"/"widening"/"flat" from bond_signals
    if hy_trend == "tightening":
        hy_dir_raw = "rising"   # credit improving = growth positive
    elif hy_trend == "widening":
        hy_dir_raw = "falling"  # credit deteriorating = growth negative
    elif hy_trend == "flat":
        hy_dir_raw = "flat"
    signals.append({"name": "HY_OAS_inv", "direction": hy_dir_raw,
                    "as_of": None, "latest": hy.get("latest")})
    if hy_dir_raw == "rising":
        votes_up += 1
    elif hy_dir_raw == "falling":
        votes_down += 1

    # --- Aggregate into a diffusion score ----------------------------------
    total_signals = len(signals)
    voted = votes_up + votes_down
    if total_signals == 0 or voted == 0:
        score = 0.0
    else:
        score = round((votes_up - votes_down) / total_signals, 3)

    available = sum(1 for s in signals if s["direction"] is not None)
    if available >= 7:
        confidence = "full"
    elif available >= 4:
        confidence = "medium"
    elif available >= 2:
        confidence = "low"
    else:
        confidence = "none"

    # Direction of the composite (for the divergence detector).
    cfg_thr = (_load_divergence_config().get("leading_vs_lagging_growth") or {}).get(
        "diffusion_threshold", 0.3)
    if score > float(cfg_thr):
        composite_dir = "rising"
    elif score < -float(cfg_thr):
        composite_dir = "falling"
    else:
        composite_dir = "flat"

    return {
        "available": available >= 2,
        "score": score,
        "direction": composite_dir,
        "confidence": confidence,
        "votes_up": votes_up,
        "votes_down": votes_down,
        "available_signals": available,
        "total_signals": total_signals,
        "signals": signals,
        "note": (
            "Leading-growth composite (FOLLOWUPS #17). Describe-only — never flips the "
            "growth axis directly; feeds leading_vs_lagging_growth divergence and "
            "_build_transition_watch growth side. LLM adjudicates."
        ),
    }


# ---------------------------------------------------------------------------
# Task B (FOLLOWUPS #18): Market-implied quadrant + daily dollar proxy
# ---------------------------------------------------------------------------

def _daily_dollar_proxy(macro_data: dict, today: str) -> dict:
    """Trade-weighted USD proxy from FX pairs when DTWEXBGS is stale (>5d).

    Blend: EUR 57% + JPY 28% + CNY 15% (approximate trade weights for the major
    G3 currencies in the broad USD index). A rising blend → stronger USD.
    Returns {available, proxy_direction, components, as_of, basis} or
    {available: False} when the pairs themselves are stale.
    """
    stale_days = 5
    rows_eu = macro_data.get("DEXUSEU") or []   # USD/EUR (lower = stronger USD)
    rows_jp = macro_data.get("DEXJPUS") or []   # JPY/USD (higher = stronger USD)
    rows_cn = macro_data.get("DEXCHUS") or []   # CNY/USD (higher = stronger USD)

    def _latest_val_date(rows: list) -> tuple[float | None, str | None]:
        for r in rows:
            v, d = r.get("value"), r.get("date")
            if v not in (None, ".", "") and d:
                try:
                    return float(v), str(d)[:10]
                except (TypeError, ValueError):
                    continue
        return None, None

    eu_val, eu_date = _latest_val_date(rows_eu)
    jp_val, jp_date = _latest_val_date(rows_jp)
    cn_val, cn_date = _latest_val_date(rows_cn)

    ages = [_days_stale(d, today) for d in (eu_date, jp_date, cn_date) if d]
    if not ages or all(a is None or a > stale_days for a in ages):
        return {"available": False, "basis": "all_fx_pairs_stale"}

    # Previous values (20d back for a trend read).
    def _val_20d_ago(rows: list) -> float | None:
        non_null = [r for r in rows if r.get("value") not in (None, ".", "")]
        if len(non_null) > 20:
            try:
                return float(non_null[20]["value"])
            except (TypeError, ValueError):
                return None
        return None

    eu_p20, jp_p20, cn_p20 = _val_20d_ago(rows_eu), _val_20d_ago(rows_jp), _val_20d_ago(rows_cn)

    components: list[dict] = []
    score = 0.0
    weight_sum = 0.0
    weights = {"EUR": 0.57, "JPY": 0.28, "CNY": 0.15}

    if eu_val and eu_p20 and _days_stale(eu_date, today) is not None and _days_stale(eu_date, today) <= stale_days:
        # USD/EUR: lower = stronger USD → USD stronger when ratio falls
        delta_pct = (eu_val / eu_p20 - 1.0) * 100.0
        direction = "stronger" if delta_pct < -0.5 else ("weaker" if delta_pct > 0.5 else "flat")
        score += weights["EUR"] * (1 if direction == "stronger" else (-1 if direction == "weaker" else 0))
        weight_sum += weights["EUR"]
        components.append({"pair": "DEXUSEU", "latest": eu_val, "delta_20d_pct": round(delta_pct, 3),
                            "usd_direction": direction, "as_of": eu_date})

    if jp_val and jp_p20 and _days_stale(jp_date, today) is not None and _days_stale(jp_date, today) <= stale_days:
        # JPY/USD: higher = stronger USD
        delta_pct = (jp_val / jp_p20 - 1.0) * 100.0
        direction = "stronger" if delta_pct > 0.5 else ("weaker" if delta_pct < -0.5 else "flat")
        score += weights["JPY"] * (1 if direction == "stronger" else (-1 if direction == "weaker" else 0))
        weight_sum += weights["JPY"]
        components.append({"pair": "DEXJPUS", "latest": jp_val, "delta_20d_pct": round(delta_pct, 3),
                            "usd_direction": direction, "as_of": jp_date})

    if cn_val and cn_p20 and _days_stale(cn_date, today) is not None and _days_stale(cn_date, today) <= stale_days:
        # CNY/USD: higher = stronger USD
        delta_pct = (cn_val / cn_p20 - 1.0) * 100.0
        direction = "stronger" if delta_pct > 0.5 else ("weaker" if delta_pct < -0.5 else "flat")
        score += weights["CNY"] * (1 if direction == "stronger" else (-1 if direction == "weaker" else 0))
        weight_sum += weights["CNY"]
        components.append({"pair": "DEXCHUS", "latest": cn_val, "delta_20d_pct": round(delta_pct, 3),
                            "usd_direction": direction, "as_of": cn_date})

    if not components:
        return {"available": False, "basis": "no_fresh_fx_pairs"}

    as_of = max(c["as_of"] for c in components)
    net = score / weight_sum if weight_sum > 0 else 0.0
    proxy_dir = "stronger" if net > 0.2 else ("weaker" if net < -0.2 else "flat")

    return {
        "available": True,
        "proxy_direction": proxy_dir,
        "proxy_score": round(net, 3),
        "components": components,
        "as_of": as_of,
        "basis": "fx_pairs_blend",
        "weights": weights,
    }


def _build_market_implied_quadrant(
    perf_series: list[dict],
    macro_data: dict,
    bond_signals: dict,
    regional_rotation: dict,
    today: str,
    prices: dict | None = None,
    close_cache: dict[str, dict[str, float]] | None = None,
) -> dict:
    """Market-implied quadrant from cross-asset tape momentum (FOLLOWUPS #18).

    Computes the quadrant the TAPE is pricing — independent of the macro axes, so
    it works at borderline regimes where `active_quadrant` is empty.

    Basket momentum: relative 20/60d performance of equal-weight Q1-Q4 baskets
    from `performance/equity-series.json` closes (reuses `_quadrant_perf_series`).
    When the stored series is short (<20d), falls back to a partial read.
    Per-signal votes: copper/gold, XLY/XLP, DXY trend, breakevens direction,
    HY OAS direction, 2s10s steepening.

    `prices` (optional): today's EOD prices dict — used to extend the series with
    the current day's closes for signals that aren't in perf_series yet.
    `close_cache` (optional): the SAME `{symbol: {date: close}}` cache
    `_build_leading_growth` uses for CPER/GLD/XLY/XLP (2026-08-06 audit B1/B2) —
    the copper/gold and XLY/XLP votes read this, NOT the perf-series `closes`
    (which only ever contains CORE_ROSTER tickers and never CPER/XLY).

    Output: {implied_quadrant, confidence, vote_count, total_votes, votes, basis}.
    Describe-only — never touches reference_weights or regime_gate.
    """
    # Load perf series if not provided.
    if not perf_series:
        perf_series = read_perf_series()
    if not perf_series:
        return {"available": False, "note": "no perf series yet"}

    # --- Basket momentum (20d and 60d via _quadrant_perf_series) ---------------
    dates = [p["date"] for p in perf_series]
    today_date = date.fromisoformat(today)

    def _cutoff_idx(days: int) -> int | None:
        target = (today_date - timedelta(days=days)).isoformat()
        for i, d in enumerate(dates):
            if d >= target:
                return i
        return None

    votes: list[dict] = []
    growth_up_score = 0.0   # positive = growth-favoring (Q1 or Q2)
    infl_up_score = 0.0     # positive = inflation-favoring (Q2 or Q3)
    # Per-vote sign contributions (2026-08-06 audit B2) — used to detect when
    # populated votes DISAGREE on an axis (some +, some -), which caps
    # confidence at 'medium' regardless of populated-vote count.
    growth_signs: list[float] = []
    infl_signs: list[float] = []

    for window_days, weight in ((20, 0.4), (60, 0.6)):
        idx = _cutoff_idx(window_days)
        if idx is None or idx >= len(perf_series) - 1:
            votes.append({"source": f"basket_momentum_{window_days}d",
                          "vote": None, "weight": weight, "note": "insufficient history"})
            continue
        window_pts = perf_series[idx:]
        idx_rows = _quadrant_perf_series(window_pts, QUADRANT_CONCENTRATE)
        if not idx_rows:
            votes.append({"source": f"basket_momentum_{window_days}d",
                          "vote": None, "weight": weight, "note": "no basket data"})
            continue
        last = idx_rows[-1]
        q1, q2 = last.get("Q1"), last.get("Q2")
        q3, q4 = last.get("Q3"), last.get("Q4")
        # Growth axis vote: compare risk-on (Q1+Q2) avg vs defensive (Q3+Q4) avg.
        ro = [v for v in (q1, q2) if v is not None]
        def_ = [v for v in (q3, q4) if v is not None]
        if ro and def_:
            ro_avg = sum(ro) / len(ro)
            def_avg = sum(def_) / len(def_)
            growth_delta = ro_avg - def_avg   # >0 → growth, <0 → stagflation/deflation
            growth_up_score += weight * growth_delta / 10.0   # normalise (baskets ≈ 100)
            if growth_delta != 0:
                growth_signs.append(growth_delta)
        # Inflation axis vote: compare inflationary (Q2+Q3) vs deflationary (Q1+Q4).
        inf_ = [v for v in (q2, q3) if v is not None]
        def_lat = [v for v in (q1, q4) if v is not None]
        if inf_ and def_lat:
            inf_avg = sum(inf_) / len(inf_)
            def_lat_avg = sum(def_lat) / len(def_lat)
            infl_delta = inf_avg - def_lat_avg
            infl_up_score += weight * infl_delta / 10.0
            if infl_delta != 0:
                infl_signs.append(infl_delta)
        votes.append({
            "source": f"basket_momentum_{window_days}d",
            "vote": {
                "q1": round(q1 - 100.0, 2) if q1 is not None else None,
                "q2": round(q2 - 100.0, 2) if q2 is not None else None,
                "q3": round(q3 - 100.0, 2) if q3 is not None else None,
                "q4": round(q4 - 100.0, 2) if q4 is not None else None,
            }, "weight": weight
        })

    # --- Per-signal cross-asset votes (simple up/down flags) ----------------
    # Copper/gold + XLY/XLP read the SAME close_cache _build_leading_growth uses
    # (2026-08-06 audit B1/B2) — the perf-series `closes` dict only ever carries
    # CORE_ROSTER tickers and never contains CPER or XLY, so these two votes were
    # structurally always null before this fix.
    _cc = close_cache or {}
    cg = _ratio_20d_signal(_cc, "CPER", "GLD")
    cg_vote = "growth" if cg["direction"] == "rising" else (
        "stagflation" if cg["direction"] == "falling" else None)
    if cg_vote == "growth":
        growth_up_score += 0.10
        growth_signs.append(1.0)
    elif cg_vote == "stagflation":
        growth_up_score -= 0.10
        growth_signs.append(-1.0)
    votes.append({"source": "copper_gold_ratio", "vote": cg_vote,
                  "as_of": cg["as_of"], "value": cg["pct_change"]})

    # XLY/XLP (cyclicals vs defensives)
    xl = _ratio_20d_signal(_cc, "XLY", "XLP")
    xl_vote = "growth" if xl["direction"] == "rising" else (
        "defensive" if xl["direction"] == "falling" else None)
    if xl_vote == "growth":
        growth_up_score += 0.10
        growth_signs.append(1.0)
    elif xl_vote == "defensive":
        growth_up_score -= 0.10
        growth_signs.append(-1.0)
    votes.append({"source": "XLY_XLP", "vote": xl_vote,
                  "as_of": xl["as_of"], "value": xl["pct_change"]})

    # DXY trend (from regional_rotation; inverted for growth — weaker USD → growth/intl)
    dxy_trend = (regional_rotation or {}).get("dxy_tailwind_for_intl")
    dxy_vote = None
    if dxy_trend == "tailwind":     # USD weakening → favors intl/risk
        dxy_vote = "growth"
        growth_up_score += 0.05
        growth_signs.append(1.0)
    elif dxy_trend == "headwind":   # USD strengthening → defensive/Q4
        dxy_vote = "defensive"
        growth_up_score -= 0.05
        growth_signs.append(-1.0)
    votes.append({"source": "DXY_trend", "vote": dxy_vote})

    # Breakevens direction (from bond_signals)
    be5y = ((bond_signals or {}).get("breakevens") or {}).get("be_5y") or {}
    be_delta = be5y.get("delta_20d_bp")
    be_vote = None
    if be_delta is not None:
        if float(be_delta) > 15.0:
            be_vote = "reflation"    # rising breakevens → Q2/Q3
            infl_up_score += 0.10
            infl_signs.append(1.0)
        elif float(be_delta) < -15.0:
            be_vote = "disinflation"  # falling → Q1/Q4
            infl_up_score -= 0.10
            infl_signs.append(-1.0)
    votes.append({"source": "breakevens_20d", "vote": be_vote,
                  "value": round(be_delta, 1) if be_delta is not None else None})

    # HY OAS direction (from bond_signals; tightening = risk-on = growth)
    hy = ((bond_signals or {}).get("credit") or {}).get("hy_oas") or {}
    hy_trend_val = hy.get("trend_4w")
    hy_vote = None
    if hy_trend_val == "tightening":
        hy_vote = "growth"
        growth_up_score += 0.08
        growth_signs.append(1.0)
    elif hy_trend_val == "widening":
        hy_vote = "defensive"
        growth_up_score -= 0.08
        growth_signs.append(-1.0)
    votes.append({"source": "HY_OAS_trend", "vote": hy_vote})

    # 2s10s steepening (steepening → growth/reflation expectation)
    t10y2y = ((macro_data.get("T10Y2Y") or [{}])[0]).get("value")
    t10y2y_prev = ((macro_data.get("T10Y2Y") or [{}] * 2)[1]).get("value") if len(macro_data.get("T10Y2Y") or []) > 1 else None
    slope_vote = None
    if t10y2y not in (None, ".", "") and t10y2y_prev not in (None, ".", ""):
        try:
            delta_2s10 = float(t10y2y) - float(t10y2y_prev)
            if delta_2s10 > 0.05:
                slope_vote = "growth"    # steepening → growth
                growth_up_score += 0.08
                growth_signs.append(1.0)
            elif delta_2s10 < -0.05:
                slope_vote = "defensive"  # flattening → defensive
                growth_up_score -= 0.08
                growth_signs.append(-1.0)
        except (TypeError, ValueError):
            pass
    votes.append({"source": "2s10s_steepening", "vote": slope_vote})

    # --- Resolve implied quadrant from the accumulated scores ---------------
    # growth_up_score > 0 → growth rising; < 0 → falling
    # infl_up_score > 0 → inflation rising; < 0 → falling
    GROWTH_THR = 0.10
    INFL_THR = 0.05

    if growth_up_score > GROWTH_THR:
        implied_growth = "rising"
    elif growth_up_score < -GROWTH_THR:
        implied_growth = "falling"
    else:
        implied_growth = "flat"

    if infl_up_score > INFL_THR:
        implied_infl = "rising"
    elif infl_up_score < -INFL_THR:
        implied_infl = "falling"
    else:
        implied_infl = "flat"

    implied_q = active_quadrant(implied_growth, implied_infl)
    vote_count = sum(1 for v in votes if v.get("vote") is not None)
    total_votes = len(votes)

    # --- Confidence: gated on POPULATED VOTE COUNT + axis agreement ---------
    # (2026-08-06 audit B2 — replaces the old score-MAGNITUDE gate, under which
    # a strongly-divergent basket-momentum pair alone — the only 2 of 8 votes
    # wired correctly pre-B1-fix — could swing confidence to "high" even with
    # every one of the 6 per-signal cross-asset votes null.) Thresholds are on
    # vote_count (0-8: both basket windows + the 6 per-signal votes), so 2
    # populated basket votes alone land exactly at "low", never above it.
    _miq_cfg = (_load_divergence_config().get("market_implied_quadrant") or {})
    _thr = _miq_cfg.get("confidence_min_populated") or {"low": 2, "medium": 3, "high": 5}
    growth_disagree = any(s > 0 for s in growth_signs) and any(s < 0 for s in growth_signs)
    infl_disagree = any(s > 0 for s in infl_signs) and any(s < 0 for s in infl_signs)
    disagreement = growth_disagree or infl_disagree

    if vote_count >= float(_thr.get("high", 5)) and not disagreement:
        confidence = "high"
    elif vote_count >= float(_thr.get("medium", 3)):
        confidence = "medium"
    elif vote_count >= float(_thr.get("low", 2)):
        confidence = "low"
    else:
        confidence = "none"

    return {
        "available": True,
        "implied_quadrant": implied_q or "borderline",
        "implied_growth": implied_growth,
        "implied_inflation": implied_infl,
        "confidence": confidence,
        "confidence_axis_disagreement": disagreement,
        "growth_score": round(growth_up_score, 3),
        "inflation_score": round(infl_up_score, 3),
        "vote_count": vote_count,
        "total_votes": total_votes,
        "votes": votes,
        "note": (
            "Tape-implied quadrant from cross-asset momentum + signal votes (FOLLOWUPS #18). "
            "Describe-only — never touches reference_weights or regime_gate. "
            "Works at borderline regimes (no dependence on active_quadrant). "
            "Historical rationale: when tape and realized macro disagree at turns, "
            "the tape is early more often than wrong (2022 canonical case)."
        ),
    }


# ---------------------------------------------------------------------------
# Task C: P&L decomposition (inception-shortfall analysis)
# ---------------------------------------------------------------------------

def _fifo_realized_pnl(fills: list[dict]) -> dict[str, float]:
    """FIFO realized P&L per symbol from a list of fill activity dicts.

    Each fill: {symbol, side ('buy'|'sell'), qty (float), price (float)}.
    Older fills processed first (sorted by transaction_time ascending).
    Returns {symbol: realized_pnl_usd}.
    """
    # Cost queues: {symbol: [(qty, cost_per_share), ...]}
    queues: dict[str, list[tuple[float, float]]] = {}
    realized: dict[str, float] = {}

    sorted_fills = sorted(fills, key=lambda f: f.get("transaction_time") or f.get("date") or "")
    for fill in sorted_fills:
        sym = (fill.get("symbol") or "").upper()
        if not sym:
            continue
        try:
            qty = abs(float(fill.get("qty") or 0))
            price = float(fill.get("price") or 0)
        except (TypeError, ValueError):
            continue
        side = (fill.get("side") or "").lower()
        if side == "buy":
            queues.setdefault(sym, []).append((qty, price))
        elif side == "sell":
            q = queues.get(sym) or []
            remaining = qty
            while remaining > 0 and q:
                lot_qty, lot_cost = q[0]
                taken = min(remaining, lot_qty)
                realized[sym] = realized.get(sym, 0.0) + taken * (price - lot_cost)
                remaining -= taken
                if taken >= lot_qty:
                    q.pop(0)
                else:
                    q[0] = (lot_qty - taken, lot_cost)
            queues[sym] = q
    return realized


def _build_pnl_decomposition(
    alp: "AlpacaClient",
    paper_account: dict,
    inception_date: str,
) -> dict:
    """Task C: FIFO realized + current unrealized P&L per bucket since inception.

    Buckets:
      core_current  — symbol in CORE_ROSTER (incl. role-pool members) AND
                      currently held or a role's selected member
      legacy_exits  — LEGACY_EXITS names
      off_roster_flex — everything else (flex leftovers like MU)

    Non-fatal: returns {available: False, reason: ...} on any Alpaca error.
    """
    try:
        fills_raw = alp.get_activities(activity_type="FILL", after=inception_date)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pnl_decomposition: activities fetch failed: %s", exc)
        return {"available": False, "reason": str(exc)}

    fills = []
    for f in fills_raw:
        sym = (f.get("symbol") or "").upper()
        side = (f.get("side") or "").lower()
        try:
            qty = abs(float(f.get("qty") or 0))
            price = float(f.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if sym and side in ("buy", "sell") and qty > 0 and price > 0:
            fills.append({
                "symbol": sym, "side": side, "qty": qty, "price": price,
                "transaction_time": f.get("transaction_time") or f.get("date") or "",
            })

    realized_by_sym = _fifo_realized_pnl(fills)

    # Unrealized from paper_account positions.
    unrealized_by_sym: dict[str, float] = {}
    for pos in (paper_account.get("positions") or []):
        sym = (pos.get("ticker") or "").upper()
        try:
            unr = float(pos.get("unrealized_pl") or 0)
        except (TypeError, ValueError):
            unr = 0.0
        if sym:
            unrealized_by_sym[sym] = unr

    # All symbols that appear in any fill or position.
    all_syms = set(realized_by_sym) | set(unrealized_by_sym)

    # Bucket assignment.
    core_set = set(CORE_ROSTER)
    legacy_set = set(LEGACY_EXITS)

    def _bucket(sym: str) -> str:
        if sym in legacy_set:
            return "legacy_exits"
        if sym in core_set or role_of(sym) is not None:
            return "core_current"
        return "off_roster_flex"

    buckets: dict[str, dict] = {
        "core_current":   {"realized_usd": 0.0, "unrealized_usd": 0.0, "symbols": {}},
        "legacy_exits":   {"realized_usd": 0.0, "unrealized_usd": 0.0, "symbols": {}},
        "off_roster_flex": {"realized_usd": 0.0, "unrealized_usd": 0.0, "symbols": {}},
    }

    for sym in all_syms:
        b = _bucket(sym)
        r = realized_by_sym.get(sym, 0.0)
        u = unrealized_by_sym.get(sym, 0.0)
        # R1 (2026-08-06 audit) — "open" (currently held, even at $0 unrealized)
        # vs "closed" (realized-only, no live position) per symbol. Without
        # this, a fully-closed off-roster name's historical realized loss reads
        # as an ONGOING structural drag from a position that no longer exists
        # (the -$803 off-roster figure reported alongside 0.00% off-roster
        # weight — the position was already closed, not currently sized at a
        # weight that would explain a drag).
        is_open = sym in unrealized_by_sym
        buckets[b]["realized_usd"] += r
        buckets[b]["unrealized_usd"] += u
        buckets[b]["symbols"][sym] = {
            "realized_usd": round(r, 2), "unrealized_usd": round(u, 2),
            "total_usd": round(r + u, 2),
            "position_status": "open" if is_open else "closed",
        }

    equity = float(paper_account.get("equity") or 0) or 1.0
    result: dict = {"available": True, "inception_date": inception_date,
                    "fill_count": len(fills), "symbol_count": len(all_syms)}

    for b_name, b_data in buckets.items():
        r, u = b_data["realized_usd"], b_data["unrealized_usd"]
        total = r + u
        # Top 15 contributors by absolute total P&L.
        top15 = sorted(b_data["symbols"].items(), key=lambda kv: abs(kv[1]["total_usd"]), reverse=True)[:15]
        has_open_position = any(v["position_status"] == "open" for v in b_data["symbols"].values())
        result[b_name] = {
            "realized_usd": round(r, 2),
            "unrealized_usd": round(u, 2),
            "total_usd": round(total, 2),
            "pct_of_equity": round(total / equity * 100.0, 3),
            "has_open_position": has_open_position,
            "contributors": [{"symbol": sym, **v} for sym, v in top15],
        }

    return result


def _build_divergences(
    paper_account: dict,
    growth_axis: dict,
    inflation_axis: dict,
    bond_signals: dict,
    regional_rotation: dict,
    reference_weights: dict,
    market_shock: dict,
    spy_sma: dict,
    today: str,
    cfg: dict,
    leading_growth: dict | None = None,
    market_implied_quadrant: dict | None = None,
) -> list[dict]:
    """Deterministic detector of TENSIONS between signals that should agree but don't
    (responsiveness brief Phase 2). It points the analyzer's judgment at the high-value
    zones — it does **not** resolve, rank, or act on any tension (that is Tier 3 / the
    LLM's job in Phase 4). Output is descriptive, never prescriptive.

    Echo-not-re-derive: every input is read from values already computed in the snapshot
    (the bond scorecard legs, the inflation/growth axes, the DXY trend, reference_weights,
    holdings) plus a 200-day SMA reduced from already-fetched SPY rows. A divergence whose
    input is stale or absent is marked ``status: "indeterminate"`` — never a false
    ``active``, never silently dropped (missing data = WATCH, never REJECT).

    Returns a list of ``{id, description, signals, direction_implied, status}``.
    """
    out: list[dict] = []
    stale_days = int(cfg.get("staleness_days", 7))

    # --- 1. leading vs lagging inflation -------------------------------------
    out.append(_div_leading_vs_lagging_inflation(inflation_axis, bond_signals, cfg, today, stale_days))

    # --- 2. credit complacency vs calm ---------------------------------------
    out.append(_div_credit_complacency(bond_signals, market_shock, cfg, today, stale_days))

    # --- 3. price action vs regime call --------------------------------------
    out.append(_div_price_vs_regime(spy_sma, reference_weights, regional_rotation, today, stale_days))

    # --- 4. dollar vs international tilt --------------------------------------
    out.append(_div_dollar_vs_intl(paper_account, regional_rotation, today, stale_days, cfg))

    # --- 5. leading vs lagging growth (#17) ----------------------------------
    out.append(_div_leading_vs_lagging_growth(growth_axis, leading_growth, cfg, today, stale_days))

    # --- 6. market-implied vs macro quadrant (#18) ---------------------------
    out.append(_div_market_vs_macro_quadrant(
        reference_weights, market_implied_quadrant, today, stale_days, cfg))

    return out


def _div_leading_vs_lagging_growth(
    growth_axis: dict, leading_growth: dict | None, cfg: dict,
    today: str, stale_days: int,
) -> dict:
    """Leading-growth composite vs realized growth_axis direction (FOLLOWUPS #17).

    Fires when the leading composite score is >= cfg threshold in a direction that
    disagrees with the realized axis. Stale or unavailable composite → indeterminate,
    never a false active. Confidence from `leading_growth.confidence` propagates.

    B2 (2026-08-14 audit): the realized leg is gated on `growth_axis.as_of`
    (vintage-recency-based per the 2026-07-21 B4 fix — GDPNow revises far more
    often than a monthly print, so the flat daily `stale_days` is appropriate
    here, unlike inflation's monthly-cadence realized leg). The composite leg
    is NOT separately staleness-gated here — `_build_leading_growth` already
    drops each stale per-series input from its own vote internally (mixed
    weekly/monthly/daily cadences make a single top-level `as_of` for the
    composite meaningless); its existing `available`/`confidence` gate already
    reflects that.
    """
    base = {
        "id": "leading_vs_lagging_growth",
        "description": "Leading-growth composite vs realized growth_axis direction.",
    }
    realized = (growth_axis or {}).get("direction")
    realized_as_of = (growth_axis or {}).get("as_of")
    realized_age = _days_stale(realized_as_of, today)
    realized_stale = realized_age is not None and realized_age > stale_days
    realized_eff = None if realized_stale else realized
    lg = leading_growth or {}

    if not lg.get("available"):
        return {**base, "signals": [
            {"name": "growth_axis.direction (realized)", "value": realized,
             "as_of": realized_as_of, "stale": realized_stale},
        ], "direction_implied": "unresolved", "status": "indeterminate",
                "note": "leading_growth block unavailable"}

    composite_dir = lg.get("direction")  # "rising"/"falling"/"flat"
    score = lg.get("score", 0.0)
    confidence = lg.get("confidence", "none")

    sig = [
        {"name": "leading_growth.direction", "value": composite_dir},
        {"name": "leading_growth.score", "value": score},
        {"name": "growth_axis.direction (realized)", "value": realized,
         "as_of": realized_as_of, "stale": realized_stale},
        {"name": "leading_growth.confidence", "value": confidence},
    ]
    base["signals"] = sig

    if realized_eff is None or composite_dir is None or confidence in ("none", "low"):
        return {**base, "direction_implied": "unresolved", "status": "indeterminate"}

    if composite_dir == "flat" or composite_dir == realized_eff:
        return {**base, "direction_implied": "aligned", "status": "indeterminate"}

    # Leading disagrees with realized — active tension.
    return {
        **base,
        "description": (f"Leading growth composite points {composite_dir} "
                        f"while realized growth_axis is {realized_eff} "
                        f"(score={score:+.2f}, {confidence} confidence)."),
        "direction_implied": composite_dir,
        "status": "active",
    }


def _div_market_vs_macro_quadrant(
    reference_weights: dict,
    market_implied_quadrant: dict | None,
    today: str,
    stale_days: int,
    cfg: dict,
) -> dict:
    """Market tape-implied quadrant vs the macro active_quadrant / favored_bucket (#18).

    Fires when the tape disagrees with the macro call. Works at borderline regimes
    (market_implied_quadrant has no dependence on active_quadrant). High-confidence
    tape read required to fire active (avoids noise). Keeps the `price_vs_regime`
    detector running in parallel — market_vs_macro_quadrant is the broader instrument;
    it catches borderline-regime mismatches where price_vs_regime goes indeterminate.
    Both describe-only; LLM adjudicates.
    """
    base = {
        "id": "market_vs_macro_quadrant",
        "description": "Market-implied quadrant (tape momentum + cross-asset votes) vs macro regime call.",
    }
    miq = market_implied_quadrant or {}

    if not miq.get("available"):
        return {**base, "signals": [], "direction_implied": "unresolved", "status": "indeterminate",
                "note": "market_implied_quadrant block unavailable"}

    implied_q = miq.get("implied_quadrant")
    confidence = miq.get("confidence", "none")
    macro_q = (reference_weights or {}).get("active_quadrant")
    favored = (reference_weights or {}).get("favored_bucket") or []

    sig = [
        {"name": "market_implied_quadrant", "value": implied_q},
        {"name": "market_implied.confidence", "value": confidence},
        {"name": "macro.active_quadrant", "value": macro_q},
        {"name": "macro.favored_bucket", "value": favored},
    ]
    base["signals"] = sig

    # Only fire when the tape has a confident, resolved read.
    if confidence in ("none", "low") or not implied_q or implied_q == "borderline":
        return {**base, "direction_implied": "unresolved", "status": "indeterminate"}

    # At a decided macro quadrant: check direct mismatch.
    if macro_q and macro_q in ("Q1", "Q2", "Q3", "Q4") and implied_q != macro_q:
        macro_def = _QUADRANT_DEFENSIVENESS.get(macro_q, 0)
        impl_def = _QUADRANT_DEFENSIVENESS.get(implied_q, 0)
        direction = "more_defensive" if impl_def > macro_def else "more_risk_on"
        return {
            **base,
            "description": (f"Tape implies {implied_q} ({confidence} confidence) "
                            f"while macro call is {macro_q}."),
            "direction_implied": direction,
            "status": "active",
        }

    # At a borderline/indeterminate macro: check if implied quadrant is outside
    # the favored bucket (a concrete tape read beats the borderline ambiguity).
    if isinstance(favored, list) and favored and implied_q not in favored and implied_q:
        macro_defs = [_QUADRANT_DEFENSIVENESS.get(q, 0) for q in favored if q in _QUADRANT_DEFENSIVENESS]
        impl_def = _QUADRANT_DEFENSIVENESS.get(implied_q, 0)
        if macro_defs:
            avg_macro = sum(macro_defs) / len(macro_defs)
            direction = "more_defensive" if impl_def > avg_macro else "more_risk_on"
            return {
                **base,
                "description": (f"Tape implies {implied_q} ({confidence}) "
                                f"outside the borderline favored bucket {favored}."),
                "direction_implied": direction,
                "status": "active",
            }

    return {**base, "direction_implied": "aligned", "status": "indeterminate"}


def _div_leading_vs_lagging_inflation(
    inflation_axis: dict, bond_signals: dict, cfg: dict, today: str, stale_days: int,
) -> dict:
    """B1/B2 (2026-08-14 audit). Two fixes over the pre-audit version:

    B1 — consume the SAME governing oil value the inflation axis itself used
    (`oil_20d_pct_governing`), never the raw FRED leg directly. The 2026-08-12
    whipsaw: `_build_inflation_axis` correctly preferred the fresh USO proxy
    (6.2%, below the 10.0 threshold) but this detector still read
    `oil_wti_20d_pct` (stale FRED WTI, 17.8%, above threshold) and fired,
    activating `transition_watch` for one session on a stale print. The
    non-governing reading is still echoed as a secondary signal for
    transparency — it never drives the trigger.

    B2 — a real `as_of` per leg, staleness-gated and dropped (not merely
    reported) when too old. The realized-core leg is monthly-cadence
    (CPILFESL/PCEPILFE) and is gated on `_FRESHNESS_MONTHLY_THRESHOLD_D` (45d
    — the SAME threshold the Data-Freshness table already uses for these exact
    series), never the flat daily `stale_days` — that would make the leg
    permanently "stale" between monthly prints and this divergence
    permanently indeterminate, which is not what staleness-gating is for.
    Breakeven and oil are daily series and use the flat `stale_days` like
    every other divergence detector.
    """
    c = cfg["leading_vs_lagging_inflation"]
    be = ((bond_signals or {}).get("breakevens") or {}).get("be_5y") or {}
    be_delta = be.get("delta_20d_bp")
    be_as_of = be.get("as_of")

    ia = inflation_axis or {}
    oil_20d = ia.get("oil_20d_pct_governing")
    oil_as_of = ia.get("oil_20d_pct_governing_as_of")
    oil_source = ia.get("oil_trend_source")
    # Secondary, non-governing reading — transparency only, never drives the trigger.
    non_governing_oil = (
        ia.get("oil_wti_20d_pct") if oil_source == "USO_proxy" else ia.get("oil_proxy_20d_pct")
    )

    realized = ia.get("direction")
    realized_as_of = ia.get("realized_core_as_of")

    be_age = _days_stale(be_as_of, today)
    oil_age = _days_stale(oil_as_of, today)
    realized_age = _days_stale(realized_as_of, today)

    be_stale = be_age is not None and be_age > stale_days
    oil_stale = oil_age is not None and oil_age > stale_days
    realized_stale = realized_age is not None and realized_age > _FRESHNESS_MONTHLY_THRESHOLD_D

    be_eff = None if be_stale else be_delta
    oil_eff = None if oil_stale else oil_20d
    realized_eff = None if realized_stale else realized

    sig = [
        {"name": "be_5y.delta_20d_bp", "value": be_delta, "as_of": be_as_of, "stale": be_stale},
        {"name": "inflation_axis.oil_20d_pct_governing", "value": oil_20d, "as_of": oil_as_of,
         "source": oil_source, "stale": oil_stale},
        {"name": "inflation_axis.oil_20d_pct_non_governing", "value": non_governing_oil},
        {"name": "inflation_axis.direction (realized)", "value": realized,
         "as_of": realized_as_of, "stale": realized_stale},
    ]
    base = {"id": "leading_vs_lagging_inflation",
            "description": "Leading inflation (breakevens + oil) vs realized core direction.",
            "signals": sig}

    if realized_eff is None or (be_eff is None and oil_eff is None):
        return {**base, "direction_implied": "unresolved", "status": "indeterminate"}

    # Leading direction: down if breakevens fall enough OR oil falls enough; up if either rises.
    be_thr = float(c["breakeven_delta_20d_bp"])
    oil_thr = float(c["oil_20d_pct"])
    leading_down = (be_eff is not None and be_eff <= -be_thr) or (oil_eff is not None and oil_eff <= -oil_thr)
    leading_up = (be_eff is not None and be_eff >= be_thr) or (oil_eff is not None and oil_eff >= oil_thr)
    leading = "falling" if leading_down and not leading_up else ("rising" if leading_up and not leading_down else "flat")

    # Tension when the leading direction disagrees with realized (and leading is not flat).
    if leading != "flat" and leading != realized_eff:
        return {**base,
                "description": f"Leading inflation points {leading} while realized core is {realized_eff}.",
                "direction_implied": leading, "status": "active"}
    return {**base, "direction_implied": "aligned", "status": "indeterminate"}


def _div_credit_complacency(
    bond_signals: dict, market_shock: dict, cfg: dict, today: str, stale_days: int,
) -> dict:
    """HY OAS at an absolute complacency LEVEL while nothing else flags stress.

    Gates on the LEVEL band (HY OAS < hy_oas_complacency_level_pct), not the 90-day
    percentile rank: complacency is a level-vs-history concept, but a 90d percentile is
    purely relative and sits mid-range by construction in a persistently tight-spread
    regime — i.e. blind in exactly the calm-low-spread state this detector must catch.
    The 90d percentile is retained as a reported *secondary* signal, not the trigger.

    B2 (2026-08-14 audit): HY OAS is a daily series — gated on the flat
    `stale_days` (mechanical, identical in shape to divergences 3/4/6).
    `market_shock.shock_level` is computed fresh every run (same-session, no
    external lag), so its `as_of` is always `today` — never stale by
    construction.
    """
    c = cfg["credit_complacency"]
    credit = (bond_signals or {}).get("credit") or {}
    hy = credit.get("hy_oas") or {}
    level = hy.get("latest")
    hy_as_of = hy.get("as_of")
    pct_rank = hy.get("pct_rank_90d")  # secondary/context only
    stress_flag = (credit.get("credit_stress") or {}).get("flag")
    shock = (market_shock or {}).get("shock_level")

    hy_age = _days_stale(hy_as_of, today)
    hy_stale = hy_age is not None and hy_age > stale_days
    level_eff = None if hy_stale else level

    sig = [
        {"name": "hy_oas.latest", "value": level, "as_of": hy_as_of, "stale": hy_stale},
        {"name": "hy_oas.pct_rank_90d", "value": pct_rank, "as_of": hy_as_of},
        {"name": "credit_stress.flag", "value": stress_flag, "as_of": hy_as_of, "stale": hy_stale},
        {"name": "market_shock.shock_level", "value": shock, "as_of": today},
    ]
    base = {"id": "credit_complacency",
            "description": "HY credit spread at a complacency level with no corroborating stress.",
            "signals": sig}

    if level_eff is None:
        return {**base, "direction_implied": "unresolved", "status": "indeterminate"}

    calm = (not stress_flag) and (shock is None or shock <= 1)
    complacent = level_eff < float(c["hy_oas_complacency_level_pct"])
    if complacent and calm:
        return {**base,
                "description": (f"HY OAS {level_eff}% is in the complacency band "
                                f"(<{c['hy_oas_complacency_level_pct']}%) with no stress flag and "
                                f"shock<=1 — little spread cushion, repricing-fragile."),
                "direction_implied": "fragility", "status": "active"}
    return {**base, "direction_implied": "none", "status": "indeterminate"}


def _div_price_vs_regime(spy_sma: dict, reference_weights: dict, regional_rotation: dict,
                         today: str, stale_days: int) -> dict:
    """SPY trend vs its 200-day SMA disagreeing with the deterministic active_quadrant."""
    quad = (reference_weights or {}).get("active_quadrant")
    spy_date = ((regional_rotation or {}).get("tickers") or {}).get("SPY", {}).get("latest_date")
    age = _days_stale(spy_date, today)

    sig = [
        {"name": "spy_vs_200d.above", "value": spy_sma.get("above"), "as_of": spy_sma.get("latest_date")},
        {"name": "spy_close", "value": spy_sma.get("latest"), "as_of": spy_sma.get("latest_date")},
        {"name": "spy_200d_sma", "value": spy_sma.get("sma"), "as_of": spy_sma.get("latest_date")},
        {"name": "reference_weights.active_quadrant", "value": quad, "as_of": None},
    ]
    base = {"id": "price_vs_regime",
            "description": "SPY price trend (vs 200-day) vs the deterministic regime call.",
            "signals": sig}

    # Indeterminate if the SMA could not be computed, the price is stale, or the quadrant
    # is unknown/borderline (no single quadrant to disagree with).
    if not spy_sma.get("available") or quad not in ("Q1", "Q2", "Q3", "Q4"):
        return {**base, "direction_implied": "unresolved", "status": "indeterminate"}
    if age is not None and age > stale_days:
        return {**base, "direction_implied": "unresolved", "status": "indeterminate"}

    above = spy_sma.get("above")
    defensive = quad in ("Q3", "Q4")
    risk_on = quad in ("Q1", "Q2")
    if above and defensive:
        return {**base,
                "description": f"SPY above its 200-day while the regime call is defensive ({quad}).",
                "direction_implied": "price_risk_on_vs_defensive_call", "status": "active"}
    if (not above) and risk_on:
        return {**base,
                "description": f"SPY below its 200-day while the regime call is risk-on ({quad}).",
                "direction_implied": "price_risk_off_vs_riskon_call", "status": "active"}
    return {**base, "direction_implied": "aligned", "status": "indeterminate"}


def _div_dollar_vs_intl(paper_account: dict, regional_rotation: dict, today: str,
                        stale_days: int, cfg: dict) -> dict:
    """The DXY switch disagreeing with the book's aggregate international weight."""
    c = cfg["dollar_vs_intl_tilt"]
    dxy_tag = (regional_rotation or {}).get("dxy_tailwind_for_intl")
    dxy_chg = (regional_rotation or {}).get("dxy_60d_pct_change")
    dxy_date = (regional_rotation or {}).get("dxy_latest_date")
    age = _days_stale(dxy_date, today)

    # Aggregate intl weight from holdings × the amplifier-intl roster.
    equity = float((paper_account or {}).get("equity") or 0) or 0.0
    intl_pct = None
    if equity > 0 and (paper_account or {}).get("available"):
        intl_set = set(AMPLIFIER_INTL)
        intl_pct = round(sum(
            float(p.get("market_value") or 0) for p in paper_account.get("positions", [])
            if (p.get("ticker") or "").upper() in intl_set
        ) / equity * 100.0, 2)

    sig = [
        {"name": "dxy_tailwind_for_intl", "value": dxy_tag, "as_of": dxy_date},
        {"name": "dxy_60d_pct_change", "value": dxy_chg, "as_of": dxy_date},
        {"name": "aggregate_intl_weight_pct", "value": intl_pct, "as_of": today},
    ]
    base = {"id": "dollar_vs_intl_tilt",
            "description": "The dollar trend (DXY switch) vs the book's international weight.",
            "signals": sig}

    if dxy_tag is None or intl_pct is None:
        return {**base, "direction_implied": "unresolved", "status": "indeterminate"}
    if age is not None and age > stale_days:
        return {**base, "direction_implied": "unresolved", "status": "indeterminate"}

    heavy = float(c["intl_heavy_pct"])
    light = float(c["intl_light_pct"])
    # DXY headwind/neutral favors US growth; tailwind favors intl.
    if dxy_tag in ("headwind", "neutral") and intl_pct >= heavy:
        return {**base,
                "description": (f"Dollar {dxy_tag} (favors US growth) but international weight is "
                                f"heavy ({intl_pct}%)."),
                "direction_implied": "toward_us_growth", "status": "active"}
    if dxy_tag == "tailwind" and intl_pct <= light:
        return {**base,
                "description": (f"Dollar tailwind (favors international) but international weight is "
                                f"light ({intl_pct}%)."),
                "direction_implied": "toward_international", "status": "active"}
    return {**base, "direction_implied": "aligned", "status": "indeterminate"}


def _classify_flex_review(
    *,
    days_held: int,
    excess_vs_etf_pp: float,
    excess_vs_spy_pp: float,
    spy_return_since_entry_pct: float | None,
    regime_fit_lost: bool,
    cfg: dict,
) -> dict:
    """PURE classifier — the conviction-sleeve dual-benchmark review matrix.

    Resolves `spy_direction` (DEADBAND_PP band) → the binding benchmark (SPY when
    rising/flat, the active-quadrant ETF when falling), then the `review_status`.
    AHEAD := excess >= LAG_TOL_PP (keeping pace, absorbs noise); BEHIND otherwise.
    The LLM echoes the status; it computes none of these inputs.
    """
    review_days = cfg["REVIEW_DAYS"]
    lag = cfg["LAG_TOL_PP"]
    brk = cfg["BREAK_PP"]
    dead = cfg["DEADBAND_PP"]

    if spy_return_since_entry_pct is None:
        spy_dir = "flat"
    elif spy_return_since_entry_pct > dead:
        spy_dir = "rising"
    elif spy_return_since_entry_pct < -dead:
        spy_dir = "falling"
    else:
        spy_dir = "flat"
    # SPY binds in a rising/flat tape (the mission is to beat a rising SPY); the
    # quadrant ETF binds in a drawdown (SPY is a low bar a defensive name clears
    # just by falling less — the honest test is value added over the sleeve).
    binding = "etf" if spy_dir == "falling" else "spy"

    def _result(status: str, reason: str) -> dict:
        return {
            "review_status": status,
            "binding_benchmark": binding,
            "spy_direction": spy_dir,
            "reason": reason,
        }

    # Regime fit is the entry gate; if it is void the position has no thesis —
    # cut regardless of performance or holding window.
    if regime_fit_lost:
        return _result("breaking", "regime fit lost — entry quadrant left the active quadrant")
    if days_held < review_days:
        return _result("ok", f"within holding window (<{review_days}d)")

    ahead_etf = excess_vs_etf_pp >= lag
    ahead_spy = excess_vs_spy_pp >= lag
    binding_excess = excess_vs_spy_pp if binding == "spy" else excess_vs_etf_pp
    binding_ahead = ahead_spy if binding == "spy" else ahead_etf

    if ahead_etf and ahead_spy:
        return _result("ok", "ahead of both SPY and the quadrant ETF")
    if binding_ahead:
        # ahead on the binding benchmark, behind on the non-binding one
        if binding == "spy":
            return _result(
                "ok_flagged",
                "mission met (ahead SPY) but lagging the quadrant ETF — selection "
                "weak; a higher-conviction name should bump it",
            )
        return _result(
            "ok",
            "drawdown: beating the quadrant sleeve (SPY is a low bar while falling)",
        )
    # behind on the binding benchmark
    if binding_excess < brk:
        return _result(
            "breaking",
            f"lagging the binding benchmark ({binding}) by more than {brk}pp",
        )
    return _result(
        "review_due",
        f"lagging the binding benchmark ({binding}) within the break threshold",
    )


def _build_flex_review(
    fmp: FMPClient,
    paper_account: dict,
    trade_rows: list[dict],
    growth_axis: dict,
    inflation_axis: dict,
    cfg: dict,
    today: date | None = None,
) -> dict:
    """Conviction-sleeve performance review for every HELD flex name.

    Deterministic dual-benchmark scoring (vs SPY and the active-quadrant ETF the
    name displaced). Reads write-once entry metadata from TradeHistory, computes
    days_held / returns / excesses / spy_direction / binding benchmark / status,
    and forces ``breaking`` if the regime moved away from the entry quadrant. The
    analyzer ECHOES the status and writes only the narrative for ``review_due``.
    Non-fatal: any name lacking entry/benchmark/return data → status ``unknown``.
    """
    today = today or date.today()
    active_q = active_quadrant(
        (growth_axis or {}).get("direction"),
        (inflation_axis or {}).get("direction"),
    )

    # Latest flex-BUY entry row per symbol (carries the write-once entry metadata).
    entry_by_sym: dict[str, dict] = {}
    for r in trade_rows or []:
        if (r.get("layer") or "").lower() != "flex" or (r.get("side") or "").lower() != "buy":
            continue
        sym = r.get("symbol")
        if not sym:
            continue
        rec = r.get("entry_date") or r.get("recommended_at") or ""
        prev = entry_by_sym.get(sym)
        if prev is None or rec >= (prev.get("entry_date") or prev.get("recommended_at") or ""):
            entry_by_sym[sym] = r

    held = {
        p.get("ticker"): p
        for p in (paper_account.get("positions") or [])
        if float(p.get("qty") or 0) > 0
    }

    series_cache: dict[str, dict] = {}

    def _series(sym: str) -> dict:
        if sym not in series_cache:
            series_cache[sym] = _close_by_date(fmp, sym)
        return series_cache[sym]

    names: list[dict] = []
    for sym, pos in held.items():
        entry = entry_by_sym.get(sym)
        if entry is None:
            continue  # core position — not a flex name

        entry_date = entry.get("entry_date") or entry.get("recommended_at")
        entry_price = entry.get("entry_price")
        if entry_price in (None, ""):
            entry_price = entry.get("price_at_rec")  # fallback to the stamped rec price
        entry_q = entry.get("entry_quadrant") or entry.get("quadrant_current") or ""
        bench = entry.get("flex_benchmark_etf") or benchmark_etf_for(entry_q)

        def _unknown(missing: str) -> dict:
            return {
                "symbol": sym,
                "review_status": "unknown",
                "entry_date": entry_date,
                "benchmark_etf": bench or None,
                "missing": missing,
                "note": f"flex review unavailable — missing {missing}; cannot score deterministically",
            }

        try:
            entry_price = float(entry_price) if entry_price not in (None, "") else None
        except (TypeError, ValueError):
            entry_price = None
        if not entry_date or entry_price is None:
            names.append(_unknown("entry_date/entry_price"))
            continue
        if not bench:
            names.append(_unknown("benchmark_etf"))
            continue

        sym_map, spy_map, bench_map = _series(sym), _series("SPY"), _series(bench)
        cur = _close_on_or_before(sym_map, today.isoformat())
        if cur is None:
            cur = float(pos.get("current_price") or 0) or None
        s0 = _close_on_or_before(spy_map, entry_date)
        sn = _close_on_or_before(spy_map, today.isoformat())
        b0 = _close_on_or_before(bench_map, entry_date)
        bn = _close_on_or_before(bench_map, today.isoformat())
        if not all((cur, s0, sn, b0, bn)):
            names.append(_unknown("price series (symbol/SPY/benchmark)"))
            continue

        ret = (cur / entry_price - 1.0) * 100.0
        spy_ret = (sn / s0 - 1.0) * 100.0
        bench_ret = (bn / b0 - 1.0) * 100.0
        excess_spy = ret - spy_ret
        excess_etf = ret - bench_ret
        days_held = (today - date.fromisoformat(str(entry_date)[:10])).days
        regime_fit_lost = bool(active_q) and bool(entry_q) and active_q != entry_q

        verdict = _classify_flex_review(
            days_held=days_held,
            excess_vs_etf_pp=excess_etf,
            excess_vs_spy_pp=excess_spy,
            spy_return_since_entry_pct=spy_ret,
            regime_fit_lost=regime_fit_lost,
            cfg=cfg,
        )
        names.append({
            "symbol": sym,
            "entry_date": entry_date,
            "entry_quadrant": entry_q or None,
            "active_quadrant": active_q or None,
            "benchmark_etf": bench,
            "days_held": days_held,
            "return_since_entry_pct": round(ret, 3),
            "spy_return_since_entry_pct": round(spy_ret, 3),
            "benchmark_return_since_entry_pct": round(bench_ret, 3),
            "excess_vs_spy_pp": round(excess_spy, 3),
            "excess_vs_etf_pp": round(excess_etf, 3),
            "spy_direction": verdict["spy_direction"],
            "binding_benchmark": verdict["binding_benchmark"],
            "regime_fit_lost": regime_fit_lost,
            "review_status": verdict["review_status"],
            "reason": verdict["reason"],
        })

    return {
        "available": bool(names),
        "as_of": today.isoformat(),
        "review_days": cfg["REVIEW_DAYS"],
        "config": cfg,
        "names": names,
        "note": (
            "Conviction-sleeve dual-benchmark review (primary flex exit). Statuses are "
            "computed here; the analyzer echoes them and writes the review_due narrative. "
            "binding = SPY when its tape is rising/flat, the quadrant ETF when falling."
        ),
    }


def _build_market_shock(
    fmp: FMPClient,
    macro_data: dict,
    market_news: list,
    forex_news: list,
    stock_news: list,
    company_news: dict,
    bond_signals: dict | None = None,
    news_hits_history: list[dict] | None = None,
) -> dict:
    """Detect short-horizon market shocks so the analyzer can override the 60d
    rotation windows and lift tilt limits when a structural event hits.

    Combines hard price signals (1d / 5d returns and z-scores for SPY, DXY,
    VIX) with a keyword scan over the day's news. Outputs a composite
    ``shock_level`` 0-3:

      0 = none        — business as usual; use the 60d framework verbatim
      1 = watch       — single elevated indicator; flag in narrative only
      2 = elevated    — multiple indicators fire; allow window shortening
      3 = acute       — broad shock (e.g. tariff weekend); permit aggressive
                        tilts and immediate de-risking

    The analyzer prompt defines exactly what each level unlocks.

    PRICE and NEWS are scored as two INDEPENDENT channels and combined via
    ``max(price_level, news_level)`` (2026-08-06 audit B3). The news channel
    used to trigger off absolute daily keyword-hit counts with no baseline, so
    a persistent multi-week theme (e.g. Iran/Hormuz, 130-147 hits/day) alone
    pinned shock_level at 3 even on a benign tape (SPY up, VIX down) — that
    alone lifts the cash-sleeve ceiling to shock3_ceiling (25%) every session.
    News now scores a Z-SCORE of ``news_hits_total`` against a trailing
    baseline (``news_hits_history``, caller-supplied — persisted to blob so
    it survives across runs; see ``run()``), and — the SYMMETRIC guard —
    whenever the price channel is benign (``price_level <= 1``), news-alone is
    capped at 2, or at 1 once the SAME dominant news category has persisted
    >= ``persistent_theme_sessions`` consecutive sessions. Level 3 requires
    genuine price-channel corroboration. Thresholds in
    ``risk-limits.json -> market_shock``.
    """
    out: dict = {
        "shock_level": 0,
        "shock_label": "none",
        "triggers": [],
        "spy": {},
        "dxy": {},
        "vix": {},
        "news_hits_total": 0,
        "news_hits_by_category": {},
        "news_examples": [],
        "scoring_rubric": (
            "0=none 1=watch 2=elevated (window override permitted) "
            "3=acute (aggressive tilts + de-risking permitted)"
        ),
    }

    def _close(row: dict) -> float | None:
        v = row.get("price") if row.get("price") is not None else row.get("close")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    triggers: list[str] = []

    # --- 1. SPY 1d / 5d returns + 1d z-score vs 60d realized vol -----------
    try:
        spy_rows = fmp.get_historical_price_light("SPY")
    except Exception as e:  # noqa: BLE001
        logger.warning("Market shock: SPY history fetch failed: %s", e)
        spy_rows = []
    spy_closes = [_close(r) for r in spy_rows[: _SHOCK_VOL_LOOKBACK_DAYS + 2]]
    spy_closes = [c for c in spy_closes if c]
    spy_1d_pct: float | None = None
    spy_5d_pct: float | None = None
    spy_1d_z: float | None = None
    if len(spy_closes) >= 2:
        spy_1d_pct = round((spy_closes[0] / spy_closes[1] - 1.0) * 100.0, 2)
    if len(spy_closes) >= _SHOCK_SHORT_WINDOW_DAYS + 1:
        spy_5d_pct = round(
            (spy_closes[0] / spy_closes[_SHOCK_SHORT_WINDOW_DAYS] - 1.0) * 100.0, 2
        )
    if len(spy_closes) >= _SHOCK_VOL_LOOKBACK_DAYS + 1:
        daily_rets = [
            (spy_closes[i] / spy_closes[i + 1] - 1.0) * 100.0
            for i in range(_SHOCK_VOL_LOOKBACK_DAYS)
        ]
        mean = sum(daily_rets) / len(daily_rets)
        var = sum((r - mean) ** 2 for r in daily_rets) / len(daily_rets)
        sd = var ** 0.5
        if sd > 0 and spy_1d_pct is not None:
            spy_1d_z = round((spy_1d_pct - mean) / sd, 2)
    out["spy"] = {
        "return_1d_pct": spy_1d_pct,
        "return_5d_pct": spy_5d_pct,
        "return_1d_zscore": spy_1d_z,
        "vol_lookback_days": _SHOCK_VOL_LOOKBACK_DAYS,
        "latest_date": spy_rows[0].get("date") if spy_rows else None,
    }
    if spy_1d_z is not None and abs(spy_1d_z) >= 3.5:
        triggers.append(f"SPY 1d z-score {spy_1d_z} (|z|>=3.5, acute)")
    elif spy_1d_z is not None and abs(spy_1d_z) >= 2.5:
        triggers.append(f"SPY 1d z-score {spy_1d_z} (|z|>=2.5, elevated)")
    elif spy_1d_z is not None and abs(spy_1d_z) >= 1.5:
        triggers.append(f"SPY 1d z-score {spy_1d_z} (|z|>=1.5, watch)")

    # --- 2. DXY 1d / 5d % change ------------------------------------------
    dxy_rows = macro_data.get("DTWEXBGS") or []
    dxy_vals = [
        float(r["value"]) for r in dxy_rows
        if r.get("value") not in (None, ".", "")
    ]
    dxy_1d_pct: float | None = None
    dxy_5d_pct: float | None = None
    if len(dxy_vals) >= 2:
        dxy_1d_pct = round((dxy_vals[0] / dxy_vals[1] - 1.0) * 100.0, 2)
    if len(dxy_vals) >= _SHOCK_SHORT_WINDOW_DAYS + 1:
        dxy_5d_pct = round(
            (dxy_vals[0] / dxy_vals[_SHOCK_SHORT_WINDOW_DAYS] - 1.0) * 100.0, 2
        )
    out["dxy"] = {
        "return_1d_pct": dxy_1d_pct,
        "return_5d_pct": dxy_5d_pct,
    }
    if dxy_5d_pct is not None and abs(dxy_5d_pct) >= 3.0:
        triggers.append(f"DXY 5d move {dxy_5d_pct}% (|>=3%|, elevated)")

    # --- 3. VIX level + 1d change ------------------------------------------
    vix_rows = macro_data.get("VIXCLS") or []
    vix_vals: list[float] = []
    for r in vix_rows:
        v = r.get("value")
        if v in (None, ".", ""):
            continue
        try:
            vix_vals.append(float(v))
        except (TypeError, ValueError):
            continue
    vix_latest = vix_vals[0] if vix_vals else None
    vix_1d_pct = (
        round((vix_vals[0] / vix_vals[1] - 1.0) * 100.0, 2)
        if len(vix_vals) >= 2 and vix_vals[1] else None
    )
    out["vix"] = {
        "latest": vix_latest,
        "return_1d_pct": vix_1d_pct,
    }
    if vix_latest is not None and vix_latest >= 35.0:
        triggers.append(f"VIX {vix_latest} >=35 (elevated absolute level)")
    if vix_1d_pct is not None and vix_1d_pct >= 30.0:
        triggers.append(f"VIX 1d jump {vix_1d_pct}% (>=30%, elevated)")

    # --- 4. News keyword scan ----------------------------------------------
    def _text(item: dict) -> str:
        parts = [
            item.get("headline") or item.get("title") or "",
            item.get("summary") or item.get("text") or "",
        ]
        return " ".join(p for p in parts if p).lower()

    pool: list[dict] = []
    pool.extend(market_news or [])
    pool.extend(forex_news or [])
    pool.extend(stock_news or [])
    for items in (company_news or {}).values():
        pool.extend(items or [])

    hits_by_cat: dict[str, int] = {cat: 0 for cat in _SHOCK_KEYWORDS}
    examples: list[dict] = []
    seen_titles: set[str] = set()
    for item in pool:
        body = _text(item)
        if not body:
            continue
        for cat, kws in _SHOCK_KEYWORDS.items():
            for kw in kws:
                if kw in body:
                    hits_by_cat[cat] += 1
                    title = item.get("headline") or item.get("title") or ""
                    if title and title not in seen_titles and len(examples) < 8:
                        examples.append({
                            "category": cat,
                            "keyword": kw,
                            "headline": title[:240],
                            "source": item.get("source") or item.get("site") or "",
                            "date": item.get("datetime") or item.get("date") or "",
                        })
                        seen_titles.add(title)
                    break  # avoid double-counting same item under same category
    total_hits = sum(hits_by_cat.values())
    out["news_hits_total"] = total_hits
    out["news_hits_by_category"] = hits_by_cat
    out["news_examples"] = examples
    dominant_cat_today = max(hits_by_cat, key=lambda c: hits_by_cat[c]) if total_hits > 0 else None

    # --- 4b. Credit-stress signal from bond_signals ------------------------
    credit_stress = False
    if bond_signals:
        cs = (bond_signals.get("credit") or {}).get("credit_stress") or {}
        if cs.get("flag"):
            credit_stress = True
            for reason in cs.get("reasons", []):
                triggers.append(f"Credit stress: {reason}")

    # --- 5. PRICE channel level (hard tape signals only — never news-gated) --
    _mscfg = (_load_risk_limits().get("market_shock") or {})
    dxy_corrob_hits = float(_mscfg.get("dxy_news_corroboration_hits", 8))

    price_level = 0
    if spy_1d_z is not None and abs(spy_1d_z) >= 3.5:
        price_level = 3
    elif (
        (spy_1d_z is not None and abs(spy_1d_z) >= 2.5)
        or (vix_latest is not None and vix_latest >= 35.0)
        or (vix_1d_pct is not None and vix_1d_pct >= 30.0)
        or (dxy_5d_pct is not None and abs(dxy_5d_pct) >= 3.0 and total_hits >= dxy_corrob_hits)
    ):
        price_level = 2
    elif (
        (spy_1d_z is not None and abs(spy_1d_z) >= 1.5)
        or (vix_1d_pct is not None and vix_1d_pct >= 15.0)
        or credit_stress
    ):
        price_level = 1
    # Credit stress paired with a genuine price-side signal escalates to L2.
    if credit_stress and price_level == 1 and (
        (spy_1d_z is not None and abs(spy_1d_z) >= 1.5)
        or (vix_1d_pct is not None and vix_1d_pct >= 15.0)
    ):
        price_level = 2

    # --- 6. NEWS channel level: Z-SCORE vs trailing baseline, not raw count --
    # (2026-08-06 audit B3 — see docstring). news_hits_history is caller-supplied,
    # oldest-first, each {date, total_hits, hits_by_category, dominant_category}.
    min_baseline = int(_mscfg.get("news_baseline_min_sessions", 10))
    watch_z = float(_mscfg.get("news_z_watch", 1.5))
    elevated_z = float(_mscfg.get("news_z_elevated", 2.5))
    acute_z = float(_mscfg.get("news_z_acute", 3.5))
    persistent_sessions = int(_mscfg.get("persistent_theme_sessions", 10))
    persistent_floor = float(_mscfg.get("persistent_theme_hits_floor", 15.0))
    no_baseline_floor = float(_mscfg.get("no_baseline_watch_floor_hits", 5))

    hist = news_hits_history or []
    hist_counts = [float(h["total_hits"]) for h in hist if isinstance(h.get("total_hits"), (int, float))]
    news_z: float | None = None
    if len(hist_counts) >= min_baseline:
        mean_h = sum(hist_counts) / len(hist_counts)
        var_h = sum((x - mean_h) ** 2 for x in hist_counts) / len(hist_counts)
        sd_h = var_h ** 0.5
        news_z = round((total_hits - mean_h) / sd_h, 2) if sd_h > 0 else (
            0.0 if total_hits == mean_h else acute_z + 1.0)

    if news_z is None:
        news_level = 1 if total_hits >= no_baseline_floor else 0
        if news_level:
            triggers.append(f"News keyword hits {total_hits} (no baseline yet, watch floor)")
    elif news_z >= acute_z:
        news_level = 3
        triggers.append(f"News hits z-score {news_z} (>={acute_z}, acute)")
    elif news_z >= elevated_z:
        news_level = 2
        triggers.append(f"News hits z-score {news_z} (>={elevated_z}, elevated)")
    elif news_z >= watch_z:
        news_level = 1
        triggers.append(f"News hits z-score {news_z} (>={watch_z}, watch)")
    else:
        news_level = 0

    # Persistent-theme streak: same dominant category above the floor, today
    # counting back through history, unbroken.
    persistent_streak = 0
    if dominant_cat_today and hits_by_cat.get(dominant_cat_today, 0) >= persistent_floor:
        persistent_streak = 1
        for h in reversed(hist):
            h_dom = h.get("dominant_category")
            h_hits = (h.get("hits_by_category") or {}).get(h_dom, 0) if h_dom else 0
            if h_dom == dominant_cat_today and h_hits >= persistent_floor:
                persistent_streak += 1
            else:
                break
    out["news_persistent_theme_streak"] = persistent_streak
    out["news_hits_zscore"] = news_z

    # Symmetric benign-tape guard: a benign price channel (no elevated/acute
    # price signal) caps news-alone at 2, or at 1 once the theme has persisted.
    price_benign = price_level <= 1
    if price_benign:
        cap = 1 if persistent_streak >= persistent_sessions else 2
        if news_level > cap:
            triggers.append(
                f"News-alone level capped {news_level}->{cap} (benign tape"
                + (f", persistent theme {persistent_streak} sessions" if cap == 1 else "") + ")"
            )
            news_level = cap

    level = max(price_level, news_level)
    out["price_level"] = price_level
    out["news_level"] = news_level
    out["shock_level"] = level
    out["shock_label"] = {0: "none", 1: "watch", 2: "elevated", 3: "acute"}[level]
    out["triggers"] = triggers

    return out


def _write_sentiment_history(today: str, snapshot: dict) -> None:
    news = snapshot.get("news", {})
    upsert_entity("SentimentHistory", {
        "PartitionKey":         today,
        "RowKey":               "market_overview",
        "market_news_count":    len(news.get("market", [])),
        "forex_news_count":     len(news.get("forex", [])),
        "company_news_count":   sum(len(v) for v in news.get("company", {}).values()),
        "positions_count":      len(snapshot.get("portfolio", {}).get("positions", [])),
        "portfolio_source":     snapshot.get("portfolio", {}).get("source", "unknown"),
    })
