import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from shared.keyvault import load_secrets
from shared.storage import (
    ensure_tables,
    list_snapshot_dates,
    query_entities,
    read_json_blob,
    read_perf_series,
    read_snapshot,
    upsert_entity,
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
    EXEMPT_HOLDS,
    active_quadrant,
    benchmark_etf_for,
    concentrate_names,
    favored_bucket,
    intersection_names,
    is_amplifier,
)

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
_CORE_SINGLE_STOCKS = ("AMZN", "GOOGL", "INTC", "MCK")
# Literal-cash buffer kept inside the cash sleeve (rest of the sleeve is SGOV).
_CASH_BUFFER_PCT = 1.5

_DIVERGENCE_CONFIG_FILE = _SRC / "config" / "divergence-config.json"
_SPY_SMA_WINDOW = 200  # long-trend filter for the price-vs-regime divergence (spec §6)
# Fallback divergence thresholds if config/divergence-config.json is missing/invalid
# (mirror that file — it is the canonical source).
_DIVERGENCE_DEFAULTS = {
    "leading_vs_lagging_inflation": {"breakeven_delta_20d_bp": 15.0, "oil_20d_pct": 10.0},
    "credit_complacency": {"hy_oas_pct_rank_max": 10.0, "hy_oas_complacency_level_pct": 3.5},
    "price_vs_regime": {},
    "dollar_vs_intl_tilt": {"intl_heavy_pct": 20.0, "intl_light_pct": 8.0},
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
    "borderline_blend": {
        "intersection_target_pct_of_core": 60.0,
        "divergent_staged_pct_of_core": 20.0,
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
_ETF_WATCHLIST = ["IDVO", "IDMO", "AIA"]
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


def _load_flex_candidates(exclude: set[str]) -> list[str]:
    """Non-held flex candidate tickers from config/flex-candidates.json.

    Deduped against current holdings (``exclude``) and capped so a single config
    edit can't blow the FMP budget. Missing/malformed file → empty list (the
    collector must never die over an optional enrichment). See FOLLOWUPS #8.
    """
    try:
        with open(_FLEX_CANDIDATES_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("flex-candidates.json missing or invalid — no candidates this run")
        return []
    out: list[str] = []
    for raw in data.get("candidates", []):
        t = (raw or "").upper().strip()
        if t and t not in exclude and t not in out:
            out.append(t)
    return out[:_FLEX_CANDIDATES_MAX]


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
) -> list[dict]:
    """Compact, self-healing (date, equity, spy_close, cash_pct) series.

    Reuses the web `performance` endpoint basis: a day counts only when it has
    BOTH `paper_account.equity` and `prices.SPY.c` (so the series begins on the
    first funded/trading day and normalized %-change is the true time-weighted
    return vs SPY — no external cash flows). Backed by a tiny cached blob so the
    collector downloads each ~1 MB snapshot at most once ever; any missing prior
    day is backfilled from its snapshot, and today's point is taken from the
    in-memory values (today's snapshot blob isn't written yet). Phase C §4.
    """
    series = read_perf_series()
    have = {p.get("date") for p in series}
    changed = False

    for d in list_snapshot_dates():
        if d >= today or d in have:
            continue
        try:
            snap = read_snapshot(d)
        except Exception:  # noqa: BLE001
            continue
        eq = (snap.get("paper_account") or {}).get("equity")
        sp = ((snap.get("prices") or {}).get("SPY") or {}).get("c")
        if eq is None or sp is None:
            continue
        csh = (snap.get("paper_account") or {}).get("cash")
        series.append(_perf_point(d, eq, sp, csh))
        have.add(d)
        changed = True

    if equity is not None and spy_close is not None:
        point = _perf_point(today, equity, spy_close, cash)
        existing = next((p for p in series if p.get("date") == today), None)
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


def _perf_point(d: str, equity, spy_close, cash) -> dict:
    eq = round(float(equity), 2)
    return {
        "date": d,
        "equity": eq,
        "spy_close": round(float(spy_close), 4),
        "cash_pct": round(float(cash) / eq * 100, 2) if (cash is not None and eq) else None,
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
        "rolling": rolling,
        "max_drawdown_pct": round(max_dd, 3),
        "note": (
            f"12-month rolling not yet available (only {days_live} days live)"
            if days_live < 365 else None
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

    # Non-held flex candidates (static seed) — the analyzer's gatekeeper G2 needs
    # their fundamentals + price in the snapshot to evaluate a new flex name
    # beyond WATCH. Deduped against holdings. FOLLOWUPS #8.
    flex_candidate_tickers = _load_flex_candidates(exclude=set(tickers))
    logger.info("Flex candidates (%d): %s", len(flex_candidate_tickers), flex_candidate_tickers)

    # --- FMP -----------------------------------------------------------------
    fmp = FMPClient(secrets["FmpApiKey"])
    profiles = fmp.get_profiles(tickers)
    flex_candidate_profiles = fmp.get_profiles(flex_candidate_tickers) if flex_candidate_tickers else []

    from_2w = (date.today() - timedelta(days=1)).isoformat()
    to_2w   = (date.today() + timedelta(days=14)).isoformat()
    from_30d = (date.today() - timedelta(days=30)).isoformat()

    earnings           = fmp.get_earnings_calendar(from_2w, to_2w)
    stock_news         = fmp.get_stock_news(tickers, limit=30)
    etf_holdings: dict = {etf: fmp.get_etf_holdings(etf) for etf in _ETF_WATCHLIST}
    etf_country: dict  = {etf: fmp.get_etf_country_weights(etf) for etf in _ETF_WATCHLIST}
    etf_sector: dict   = {etf: fmp.get_etf_sector_weights(etf) for etf in _ETF_WATCHLIST}

    logger.info("FMP: %d profiles, %d earnings, %d news",
                len(profiles), len(earnings), len(stock_news))

    # --- Quiver (primary congressional source) ------------------------------
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

    # --- FRED ----------------------------------------------------------------
    with open(_MACRO_SERIES_FILE) as f:
        macro_meta: dict = json.load(f)

    fred = FREDClient(secrets["FredApiKey"])
    macro_data = fred.get_all_series(list(macro_meta.keys()))
    # Series that need deeper history for the rotation + bond-signals pre-compute
    # (get_all_series only fetches the latest 5 observations per series).
    macro_data["DTWEXBGS"] = fred.get_series_latest("DTWEXBGS", limit=90)
    macro_data["DGS2"]     = fred.get_series_latest("DGS2",     limit=90)
    macro_data["DFF"]      = fred.get_series_latest("DFF",      limit=90)
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
    _q_start = date(_t.year, 3 * ((_t.month - 1) // 3) + 1, 1).isoformat()
    _gdpnow_vint = fred.get_series_vintages(
        "GDPNOW", realtime_start=_q_start, realtime_end=_t.isoformat()
    )
    macro_data["GDPNOW_VINTAGES"] = [
        {"date": r.get("date"), "asof": r.get("realtime_start"), "value": r.get("value")}
        for r in _gdpnow_vint
        if r.get("date") == _q_start and r.get("value") not in (None, ".", "")
    ]
    # Energy axis: oil spot for the stagflation/Hormuz-shock read (~90d for baseline).
    for _oil_sid in ("DCOILWTICO", "DCOILBRENTEU"):
        macro_data[_oil_sid] = fred.get_series_latest(_oil_sid, limit=90)
    logger.info("FRED: %d series collected", sum(1 for v in macro_data.values() if v))

    # --- EOD prices (FMP batch-quote, single call) --------------------------
    # Include flex candidates so the analyzer can size a buy (weight→shares needs
    # a price) and so gatekeeper G2 sees a price for the candidate.
    all_tickers = list(dict.fromkeys(tickers + _ETF_WATCHLIST + flex_candidate_tickers))  # preserve order, dedupe
    prices = fmp.get_eod_prices(all_tickers)
    logger.info("FMP prices: %d/%d collected", len(prices), len(all_tickers))

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

    market_shock = _build_market_shock(
        fmp=fmp,
        macro_data=macro_data,
        market_news=market_news,
        forex_news=forex_news,
        stock_news=stock_news,
        company_news=company_news,
        bond_signals=bond_signals,
    )
    logger.info(
        "Market shock: level=%s, spy_1d_z=%s, news_hits=%s",
        market_shock.get("shock_level"),
        market_shock.get("spy", {}).get("return_1d_zscore"),
        market_shock.get("news_hits_total"),
    )

    # --- Quadrant axes (deterministic; analyzer ECHOES these, see prompt) ----
    # Growth + inflation direction are the two axes that decide the quadrant. They
    # were previously left to the LLM on raw macro.data — the discretion point where
    # it rationalized its prior label. Now pre-computed like bond/labor signals.
    growth_axis = _build_growth_axis(macro_data)
    inflation_axis = _build_inflation_axis(macro_data)
    fomc_stance = _load_fomc_stance()
    regime_gate = _build_regime_gate(growth_axis, inflation_axis, fomc_stance)
    logger.info(
        "Quadrant axes: growth=%s(%s) inflation=%s gate=%s policy=%s",
        growth_axis.get("direction"), growth_axis.get("confidence"),
        inflation_axis.get("direction"), regime_gate.get("status"),
        fomc_stance.get("stance"),
    )

    # --- Reference weights (strategy-spec §10: precomputed target weights the ----
    # analyzer executes toward, NOT a mandate). The missing layer that anchored the
    # call→target→trades leap where the book rationalized inaction. Deterministic +
    # echoed; non-fatal (a build failure must never block the snapshot).
    reference_weights: dict = {"available": False}
    try:
        reference_weights = _build_reference_weights(
            paper_account, growth_axis, inflation_axis, regime_gate,
            regional_rotation, bond_signals, labor_signals, market_shock,
            _load_risk_limits(),
        )
        logger.info(
            "Reference weights: quad=%s conviction=%s(%s) active_target=%s%%core tilt=%s binding=%s",
            reference_weights.get("active_quadrant"),
            reference_weights.get("conviction_proxy"),
            reference_weights.get("conviction_label"),
            reference_weights.get("active_quadrant_target_pct_of_core"),
            reference_weights.get("dollar_tilt"),
            reference_weights.get("binding"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Reference weights build failed (non-fatal)")

    # --- Divergences (responsiveness brief Phase 2: DETECT tensions, don't resolve) ---
    # Descriptive precompute pointing the analyzer's judgment at high-value zones; the
    # LLM adjudicates them in Phase 4 (the prompt does not consume this yet). The SPY
    # 200-day SMA (#3's long-trend filter) is the one new input — fetched here and reduced
    # by the pure _sma_from_rows so _build_divergences itself stays no-network/testable.
    # Non-fatal: a divergence-build failure must never block the snapshot.
    divergences: list[dict] = []
    try:
        try:
            spy_sma = _sma_from_rows(fmp.get_historical_price_light("SPY"), _SPY_SMA_WINDOW)
        except Exception:  # noqa: BLE001
            logger.warning("Divergences: SPY history fetch failed; price-vs-regime indeterminate")
            spy_sma = {"available": False}
        divergences = _build_divergences(
            paper_account, growth_axis, inflation_axis, bond_signals, regional_rotation,
            reference_weights, market_shock, spy_sma, today, _load_divergence_config(),
        )
        _active = [d["id"] for d in divergences if d.get("status") == "active"]
        logger.info("Divergences: %d total, active=%s", len(divergences), _active)
    except Exception:  # noqa: BLE001
        logger.exception("Divergences build failed (non-fatal)")

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
        logger.info(
            "Flex state: available=%s as_of=%s held=%s",
            flex_state.get("available"), flex_state.get("as_of"), flex_state.get("held"),
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
        series = _load_equity_spy_series(today, today_equity, today_spy, today_cash)
        performance = _build_performance(series)
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
        "regime_gate": regime_gate,
        "reference_weights": reference_weights,
        "divergences": divergences,
        "flex_state": flex_state,
        "performance": performance,
        "track_record": track_record,
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
    if weighted <= 3:
        category = "us_leadership_intact"
    elif weighted <= 6:
        category = "transition_window"
    else:
        category = "rotation_underway"

    out["rotation_score"] = {
        "composite": round(weighted, 1),
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
        "be_5y":  {"latest": _latest(t5y),   "delta_20d_bp": _delta_bp(t5y, 20)},
        "be_10y": {"latest": _latest(t10y),  "delta_20d_bp": _delta_bp(t10y, 20)},
        "be_5y5y": {"latest": _latest(t5y5y), "delta_20d_bp": _delta_bp(t5y5y, 20)},
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


def _build_growth_axis(macro_data: dict) -> dict:
    """Deterministic growth-direction read — the quadrant *growth axis*, computed in
    Python so the analyzer ECHOES it (mirrors bond_signals/labor_signals) rather than
    re-deriving it from raw series (where a temperature-0.2 model rationalizes toward
    its prior label).

    Primary signal: the GDPNow *current-quarter vintage trajectory*
    (``GDPNOW_VINTAGES``, oldest-first) — the within-quarter nowcast revisions. The
    standard /observations endpoint hides this (one latest value per quarter), so a
    naive cross-quarter "slope" can read 'rising' while the live quarter is being
    marked down. Fallback: cross-quarter GDPNOW slope (low confidence) only when
    fewer than 3 vintages are available; 'indeterminate' only with no GDPNow at all.
    """
    traj = [
        float(r["value"]) for r in (macro_data.get("GDPNOW_VINTAGES") or [])
        if r.get("value") not in (None, ".", "")
    ]  # oldest-first

    BAND = 0.1
    confidence = "high"
    basis = "within_quarter_vintages"
    if len(traj) >= 3:
        first, last = traj[0], traj[-1]
        latest = last
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

    note = ""
    if direction == "rising" and confidence == "low":
        note = (
            "Cross-quarter fallback only (no within-quarter vintages) — 'rising' is "
            "the coarse Q/Q comparison and may hide an in-quarter markdown; treat as "
            "low confidence."
        )

    return {
        "direction": direction,
        "confidence": confidence,
        "basis": basis,
        "gdpnow_latest": round(latest, 2),
        "gdpnow_trajectory": [round(v, 2) for v in traj],   # oldest -> newest
        "gdpnow_vintage_count": len(traj),
        "confirming": {
            "payrolls_3m_avg_k": pay_3m,
            "initial_claims_latest_k": round(claims[0] / 1000.0, 1) if claims else None,
            "retail_sales_dir": retail_dir,
        },
        "note": note,
    }


def _build_inflation_axis(macro_data: dict) -> dict:
    """Deterministic inflation-direction read — the quadrant *inflation axis*.

    Realized core (PCE-first, then CPI) governs via the 3-month-annualized-vs-YoY
    trend. Headline CPI is the energy channel: when headline is elevated AND rising
    AND oil is *also* rising, that is genuine energy inflation -> 'rising'. But when
    headline is elevated while oil is collapsing (the rear-view artifact of a prior
    oil spike), the headline is about to roll over -> do NOT force 'rising'; classify
    by core and flag the pending disinflation. Breakevens are secondary (expectations).

    NOTE: the energy overlay keys off the *actual oil price trend* (DCOILWTICO /
    DCOILBRENTEU 20-session change), NOT the news-keyword ``market_shock`` level — the
    shock detector is a headline-count signal prone to false positives, and binding
    realized-inflation direction to it would hard-wire stagflation off a news flurry.
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
    oil_chgs = [c for c in (oil_wti_20d, oil_brent_20d) if c is not None]
    oil_rising = bool(oil_chgs) and max(oil_chgs) >= 10.0      # genuine energy push
    oil_falling = bool(oil_chgs) and min(oil_chgs) <= -10.0    # spike reversing

    headline_hot = head_yoy is not None and head_yoy >= 3.5 and head_rising

    # classify by realized core trend (3m annualized vs YoY); PCE-first
    ref_ann3 = core_pce_ann3 if core_pce_ann3 is not None else core_cpi_ann3
    ref_yoy = core_pce_yoy if core_pce_yoy is not None else core_cpi_yoy

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
        "breakeven_5y5y": be_5y5y[0] if be_5y5y else None,
        "realized_governs": True,
        "note": note,
    }


def _build_regime_gate(growth_axis: dict, inflation_axis: dict, fomc_stance: dict) -> dict:
    """Deterministic macro deployment gate from the precomputed axes + policy stance.

    CLOSED unless growth is confirmed rising, realized inflation is not rising, and
    policy is not hawkish. An unconfirmed policy stance cannot *confirm* Q1 but does
    not by itself hard-close the gate (it is flagged); growth/inflation drive it. The
    analyzer echoes ``status`` into the trades JSON ``deployment_gate`` field.
    """
    reasons: list[str] = []
    g = (growth_axis or {}).get("direction")
    i = (inflation_axis or {}).get("direction")
    stance = (fomc_stance or {}).get("stance", "unconfirmed")

    if g != "rising":
        reasons.append(f"growth axis {g} (not rising)")
    if i == "rising":
        reasons.append("inflation axis rising")
    if stance == "hawkish":
        reasons.append("FOMC stance hawkish")

    status = "closed" if reasons else "open"
    policy_note = ""
    if stance == "unconfirmed":
        policy_note = "policy stance UNCONFIRMED — cannot confirm Q1; deploy cautiously."
    return {
        "status": status,
        "reasons": reasons,
        "policy_note": policy_note,
        "derived_from": {"growth": g, "inflation": i, "policy_stance": stance},
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
        inter = intersection_names(bucket)
        union = []
        for q in bucket:
            for t in concentrate_names(q):
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
        concentrate = list(concentrate_names(quad))
        raw_core = {}
        if concentrate:
            # Split the active-quadrant target. If the quadrant has an amplifier
            # (Q1/Q2), bias the US-vs-intl halves by the dollar switch; otherwise
            # equal-weight the concentrate names.
            amp = [t for t in concentrate if is_amplifier(t)]
            non_amp = [t for t in concentrate if not is_amplifier(t)]
            if amp and (quad in ("Q1", "Q2")):
                us = [t for t in amp if t in ("SPY", "QQQ", "XSD", "AMZN", "GOOGL", "INTC")]
                intl = [t for t in amp if t not in us]
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

    # --- 4. assemble core targets: floor everything, then the concentrate names --
    core_target: dict[str, float] = {t: floor for t in CORE_ROSTER}
    for t, w in raw_core.items():
        if t in core_target:
            core_target[t] = max(floor, w)

    # Soft single-name cap applies only to SINGLE STOCKS (idiosyncratic risk), NOT to
    # diversified ETF sleeves — a high-conviction quadrant is *meant* to push one ETF
    # past 15% (capping it here would defeat the concentration this feature enables).
    # Single stocks in the core roster: AMZN, GOOGL, INTC, MCK.
    for t in _CORE_SINGLE_STOCKS:
        if t in core_target:
            core_target[t] = min(core_target[t], soft_cap)

    # AMZN/GOOGL never forced below current weight (% of equity ≈ % here pre-carve).
    for t in exempt:
        if t in core_target and t in cur_w:
            core_target[t] = max(core_target[t], cur_w[t])

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

    # Scale the core into the room left after the cash sleeve. SGOV is part of the cash
    # sleeve, not the core concentration — exclude its floor slot from the core scale
    # and set it from the sleeve target so the book sums to ~100%.
    core_ex_sgov = {t: w for t, w in core_target.items() if t != "SGOV"}
    core_room = max(0.0, 100.0 - cash_sleeve_target)
    scale = core_room / (sum(core_ex_sgov.values()) or 1.0)
    weights = {t: round(w * scale, 3) for t, w in core_ex_sgov.items()}
    # Cash sleeve = SGOV (yield-bearing) holding all but a ~1.5% literal-cash buffer.
    sgov_w = max(0.0, cash_sleeve_target - _CASH_BUFFER_PCT)
    weights["SGOV"] = round(sgov_w, 3)
    weights["__cash__"] = round(cash_sleeve_target - sgov_w, 3)

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

    return {
        "available": True,
        "as_of": (paper_account.get("as_of") or growth_axis.get("as_of")),
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
        "cash_sleeve_target_pct": round(cash_sleeve_target, 2),
        "literal_cash_target_pct": weights.pop("__cash__", 0.0),
        "target_weights_pct": {t: w for t, w in sorted(weights.items()) if w >= 0.05},
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
    out.append(_div_leading_vs_lagging_inflation(inflation_axis, bond_signals, cfg))

    # --- 2. credit complacency vs calm ---------------------------------------
    out.append(_div_credit_complacency(bond_signals, market_shock, cfg))

    # --- 3. price action vs regime call --------------------------------------
    out.append(_div_price_vs_regime(spy_sma, reference_weights, regional_rotation, today, stale_days))

    # --- 4. dollar vs international tilt --------------------------------------
    out.append(_div_dollar_vs_intl(paper_account, regional_rotation, today, stale_days, cfg))

    return out


def _div_leading_vs_lagging_inflation(inflation_axis: dict, bond_signals: dict, cfg: dict) -> dict:
    """Leading inflation (5y breakeven 20d delta + WTI 20d move) vs realized direction."""
    c = cfg["leading_vs_lagging_inflation"]
    be = ((bond_signals or {}).get("breakevens") or {}).get("be_5y") or {}
    be_delta = be.get("delta_20d_bp")
    oil_20d = (inflation_axis or {}).get("oil_wti_20d_pct")
    realized = (inflation_axis or {}).get("direction")

    sig = [
        {"name": "be_5y.delta_20d_bp", "value": be_delta, "as_of": None},
        {"name": "inflation_axis.oil_wti_20d_pct", "value": oil_20d, "as_of": None},
        {"name": "inflation_axis.direction (realized)", "value": realized, "as_of": None},
    ]
    base = {"id": "leading_vs_lagging_inflation",
            "description": "Leading inflation (breakevens + oil) vs realized core direction.",
            "signals": sig}

    if be_delta is None and oil_20d is None or realized is None:
        return {**base, "direction_implied": "unresolved", "status": "indeterminate"}

    # Leading direction: down if breakevens fall enough OR oil falls enough; up if either rises.
    be_thr = float(c["breakeven_delta_20d_bp"])
    oil_thr = float(c["oil_20d_pct"])
    leading_down = (be_delta is not None and be_delta <= -be_thr) or (oil_20d is not None and oil_20d <= -oil_thr)
    leading_up = (be_delta is not None and be_delta >= be_thr) or (oil_20d is not None and oil_20d >= oil_thr)
    leading = "falling" if leading_down and not leading_up else ("rising" if leading_up and not leading_down else "flat")

    # Tension when the leading direction disagrees with realized (and leading is not flat).
    if leading != "flat" and leading != realized:
        return {**base,
                "description": f"Leading inflation points {leading} while realized core is {realized}.",
                "direction_implied": leading, "status": "active"}
    return {**base, "direction_implied": "aligned", "status": "indeterminate"}


def _div_credit_complacency(bond_signals: dict, market_shock: dict, cfg: dict) -> dict:
    """HY OAS at an absolute complacency LEVEL while nothing else flags stress.

    Gates on the LEVEL band (HY OAS < hy_oas_complacency_level_pct), not the 90-day
    percentile rank: complacency is a level-vs-history concept, but a 90d percentile is
    purely relative and sits mid-range by construction in a persistently tight-spread
    regime — i.e. blind in exactly the calm-low-spread state this detector must catch.
    The 90d percentile is retained as a reported *secondary* signal, not the trigger.
    """
    c = cfg["credit_complacency"]
    credit = (bond_signals or {}).get("credit") or {}
    hy = credit.get("hy_oas") or {}
    level = hy.get("latest")
    pct_rank = hy.get("pct_rank_90d")  # secondary/context only
    stress_flag = (credit.get("credit_stress") or {}).get("flag")
    shock = (market_shock or {}).get("shock_level")

    sig = [
        {"name": "hy_oas.latest", "value": level, "as_of": None},
        {"name": "hy_oas.pct_rank_90d", "value": pct_rank, "as_of": None},
        {"name": "credit_stress.flag", "value": stress_flag, "as_of": None},
        {"name": "market_shock.shock_level", "value": shock, "as_of": None},
    ]
    base = {"id": "credit_complacency",
            "description": "HY credit spread at a complacency level with no corroborating stress.",
            "signals": sig}

    if level is None:
        return {**base, "direction_implied": "unresolved", "status": "indeterminate"}

    calm = (not stress_flag) and (shock is None or shock <= 1)
    complacent = level < float(c["hy_oas_complacency_level_pct"])
    if complacent and calm:
        return {**base,
                "description": (f"HY OAS {level}% is in the complacency band "
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
    if total_hits >= 20:
        triggers.append(f"News keyword hits {total_hits} (>=20, elevated)")
    elif total_hits >= 10:
        triggers.append(f"News keyword hits {total_hits} (>=10, watch+)")
    elif total_hits >= 5:
        triggers.append(f"News keyword hits {total_hits} (>=5, watch)")

    # --- 4b. Credit-stress signal from bond_signals ------------------------
    credit_stress = False
    if bond_signals:
        cs = (bond_signals.get("credit") or {}).get("credit_stress") or {}
        if cs.get("flag"):
            credit_stress = True
            for reason in cs.get("reasons", []):
                triggers.append(f"Credit stress: {reason}")

    # --- 5. Composite shock level -----------------------------------------
    level = 0
    # Acute: extreme tape OR (elevated tape + heavy news cluster)
    if (spy_1d_z is not None and abs(spy_1d_z) >= 3.5) or total_hits >= 25:
        level = 3
    elif (
        (spy_1d_z is not None and abs(spy_1d_z) >= 2.5)
        or (vix_latest is not None and vix_latest >= 35.0)
        or (vix_1d_pct is not None and vix_1d_pct >= 30.0)
        or total_hits >= 15
        or (dxy_5d_pct is not None and abs(dxy_5d_pct) >= 3.0 and total_hits >= 8)
    ):
        level = 2
    elif (
        (spy_1d_z is not None and abs(spy_1d_z) >= 1.5)
        or total_hits >= 5
        or (vix_1d_pct is not None and vix_1d_pct >= 15.0)
        or credit_stress
    ):
        level = 1

    # Credit stress paired with any equity-side signal escalates to L2.
    if credit_stress and level == 1 and (
        (spy_1d_z is not None and abs(spy_1d_z) >= 1.5)
        or total_hits >= 5
        or (vix_1d_pct is not None and vix_1d_pct >= 15.0)
    ):
        level = 2

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
