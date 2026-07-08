"""DayTrade Lab — orchestration (thin glue over the pure modules). Spec §3–§6.

Runs every minute (gated by ``DAYTRADE_ENABLED`` in function_app + the market
clock/session window here). Order of operations: reconcile FIRST → clock/window
gate → halt/breaker gates → validation (first live tick) → pattern/entry/manage/
flat → persist state/log → session-end grading. All decision logic lives in the
pure modules (`gates`, `patterns`, `sizing`, `state`, `reconcile`, `grading`);
this module only fetches broker/market state, calls them in order, issues
idempotent ``FLEXD-`` orders, and persists.

Separation (spec §1): reads the catalyst engine's ledger BLOB for exclusivity/
sleeve arbitration — never imports ``flex.*``.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta

from daytrade import gates as g
from daytrade import grading, patterns, reconcile, sizing, state as st
from daytrade.config import load_daytrade_config
from daytrade.ledger import COID_PREFIX, new_entry, read_ledger, write_ledger
from shared.clients.alpaca import AlpacaClient
from shared.clients.fmp import FMPClient
from shared.keyvault import load_secrets
from shared.quadrants import CORE_ROSTER
from shared.storage import (
    append_jsonl_blob,
    list_blob_names,
    read_json_blob,
    read_jsonl_blob,
    write_json_blob,
)

logger = logging.getLogger(__name__)

_ET = "America/New_York"
_NOMS_CONTAINER = "daytrade-nominations"
_STATE_CONTAINER = "daytrade-state"
_LOG_CONTAINER = "daytrade-log"
_GRADES_CONTAINER = "daytrade-grades"
_HALT_BLOB = "halt.json"
_CATALYST_LEDGER = ("flex-ledger", "ledger.json")   # blob read only — no flex import


# ---------------------------------------------------------------------------
# nominations route
# ---------------------------------------------------------------------------

def save_daytrade_nominations(body: dict) -> dict:
    """Persist the manual pre-open nominations (spec §3). Returns a summary."""
    cfg = load_daytrade_config()
    body = body or {}
    date_str = str(body.get("date") or _now_et().strftime("%Y-%m-%d"))
    tone = str(body.get("tone") or "neutral").lower()
    if tone not in ("risk_on", "neutral", "risk_off", "carry_stress"):
        return {"error": f"invalid tone: {tone}"}
    raw = body.get("candidates") or []
    candidates = []
    for c in raw[: cfg.max_candidates]:
        sym = str((c or {}).get("symbol") or "").upper().strip()
        if not sym:
            continue
        klass = (c.get("catalyst_class") or None)
        if klass is not None:
            klass = str(klass).upper()
            if klass not in ("A", "B", "C", "D"):
                klass = None
        candidates.append({
            "symbol": sym,
            "catalyst_note": str(c.get("catalyst_note") or "")[:500],
            "catalyst_class": klass,
        })
    doc = {"date": date_str, "tone": tone, "candidates": candidates,
           "submitted_at": _now_utc_iso()}
    write_json_blob(_NOMS_CONTAINER, f"{date_str}.json", doc)
    return {"status": "ok", "date": date_str, "tone": tone,
            "candidates": len(candidates),
            "dropped": max(0, len(raw) - len(candidates))}


# ---------------------------------------------------------------------------
# the 1-min loop
# ---------------------------------------------------------------------------

def run_daytrade_manage(date_str: str | None = None, dry_run: bool = False) -> dict:
    cfg = load_daytrade_config()
    now_et = _now_et()
    today = date_str or now_et.strftime("%Y-%m-%d")

    secrets = load_secrets()
    key, secret = secrets.get("AlpacaApiKey"), secrets.get("AlpacaApiSecret")
    if not key or not secret:
        raise RuntimeError("Alpaca credentials missing from Key Vault")
    client = AlpacaClient(api_key=key, api_secret=secret)

    # ── STEP 0 — reconcile FIRST (this ledger only) ──────────────────────────
    ledger = read_ledger()
    try:
        positions = client.list_positions()
        open_orders = client.list_orders("open", 500)
    except Exception:  # noqa: BLE001
        logger.exception("daytrade: could not read positions/orders — skipping tick")
        return {"status": "broker_unreachable", "date": today}

    day = _read_day_state(today)
    ledger, exits, repairs = reconcile.reconcile_daytrade(ledger, positions, open_orders)
    for ex in exits:
        _record_closure(client, day, ex["entry"], today, cfg,
                        reason=ex["reason"])
    if not dry_run:
        for rep in repairs:
            _apply_repair(client, rep, today)
    write_ledger(ledger)

    # ── clock + session window (derived, never hardcoded ET) ─────────────────
    minutes = _session_minutes(client, today, now_et)
    if minutes is None or not (-cfg.window_pre_open_min <= minutes <= cfg.window_end_min):
        if exits and day is not None:
            _write_day_state(today, day)
        return {"status": "outside_window", "date": today, "minutes": minutes}

    # ── day state / halt / tone gates ─────────────────────────────────────────
    noms = read_json_blob(_NOMS_CONTAINER, f"{today}.json") or {}
    if day is None:
        day = st.new_day_state(today, noms.get("tone"))
    halt = read_json_blob(_STATE_CONTAINER, _HALT_BLOB)
    if st.is_halted(halt if isinstance(halt, dict) else None, today):
        _log_once(day, today, cfg, outcome="halted",
                  note=(halt or {}).get("reason"), flag="no_setup_logged")
        _write_day_state(today, day)
        return {"status": "halted", "date": today, "halt": halt}
    if day["tone"] == "carry_stress" and not day["day_done"]:
        day["day_done"] = True
        day["day_done_reason"] = "carry_stress_day_refused"
        _log_once(day, today, cfg, outcome="no_setup",
                  note="carry_stress_day_refused", flag="no_setup_logged")

    # ── validation (first live tick; spec §3) ─────────────────────────────────
    if not day["validated"] and not day["day_done"]:
        _run_validation(client, day, noms, today, cfg, secrets)

    # ── manage open position / flat / entries ────────────────────────────────
    equity = _equity(client)
    if ledger and minutes >= cfg.flat_min:
        if not dry_run:
            _flatten_all(client, ledger, today)
    elif ledger and cfg.scale_mode == "half_at_1r" and not dry_run:
        _manage_half_at_1r(client, ledger, today)
    elif not ledger and not dry_run:
        _try_entries(client, day, ledger, positions, today, minutes, equity, cfg)
    write_ledger(ledger)

    # ── session end — grades + weekly breaker (spec §5/§6) ────────────────────
    if minutes >= cfg.flat_min and not ledger and not day["graded"]:
        _grade_and_halt(day, today, cfg)
        day["graded"] = True
    _write_day_state(today, day)
    return {
        "status": "ok", "date": today, "minutes": round(minutes, 1),
        "validated": day["validated"], "day_done": day["day_done"],
        "held": sorted(ledger.keys()), "day_r": day["day_r"],
    }


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def _run_validation(client, day, noms, today, cfg, secrets) -> None:
    candidates = (noms.get("candidates") or [])[: cfg.max_candidates]
    fmp = FMPClient(secrets.get("FmpApiKey") or "")
    catalyst_symbols = _catalyst_ledger_symbols()
    foundry = None
    if cfg.llm_classify:
        try:
            from shared.clients.foundry import FoundryClient
            foundry = FoundryClient(secrets.get("FoundryApiKey"))
        except Exception:  # noqa: BLE001
            logger.exception("daytrade: foundry init failed — classes stay null")

    results = []
    for cand in candidates:
        data = _assemble_candidate_data(client, fmp, cand, today, cfg,
                                        catalyst_symbols, foundry)
        res = g.run_validation_gates(cand, data, cfg)
        results.append(res)
        if not res["survivor"]:
            _log_row(today, {
                "slot": 0, "symbol": res["symbol"], "outcome": "discarded",
                "discard_reasons": [res["discard_reason"]],
                "catalyst_class": res.get("catalyst_class"),
                "tone": day["tone"], "gap_pct": res.get("gap_pct"),
                "rvol": res.get("rvol"), "rvol_basis": res["bases"].get("rvol"),
                "float_rotation": res.get("float_rotation"),
                "spread_pct": res.get("spread_pct"), "bases": res.get("bases"),
                "gates": res.get("gates"),
            }, cfg)
            if res["discard_reason"] in ("consolidated_unmeasured", "filings_unavailable"):
                logger.error(   # loud by design — a data gap, not a merits finding
                    "daytrade: %s discarded on UNMEASURABLE gate (%s) — see spec §2",
                    res["symbol"], res["discard_reason"])
    primary, backup = g.select_survivors(results)
    day.update({"validated": True, "candidates": results,
                "primary": primary, "backup": backup})
    if primary is None:
        day["day_done"] = True
        day["day_done_reason"] = "no_setup"
        _log_once(day, today, cfg, outcome="no_setup", note="zero_survivors",
                  flag="no_setup_logged")
    if backup is not None:
        logger.info("daytrade: BACKUP candidate %s", backup["symbol"])


def _assemble_candidate_data(client, fmp, cand, today, cfg,
                             catalyst_symbols, foundry) -> dict:
    sym = str(cand.get("symbol") or "").upper()
    profile = fmp.get_profile(sym) or {}
    is_common = None
    if profile:
        is_common = (not profile.get("isEtf") and not profile.get("isFund")
                     and str(profile.get("country") or "").upper() == "US")

    daily = client.get_bars(sym, "1Day",
                            start=(date.fromisoformat(today) - timedelta(days=10)).isoformat(),
                            end=today).get(sym, [])
    prior = [b for b in daily if str(b.get("t", ""))[:10] < today]
    prior_bar = prior[-1] if prior else None

    pm_bars = _premarket_bars(client, sym, today)
    pm_last = patterns._f(pm_bars[-1].get("c")) if pm_bars else None
    pm_vol = sum(v for b in pm_bars if (v := patterns._f(b.get("v"))) is not None)
    pm_avg = _premarket_vol_30d_avg(client, sym, today)

    pm_dollar = None
    if cfg.consolidated_source == "fmp":
        q = fmp.get_aftermarket_quote(sym)
        vol = patterns._f((q or {}).get("volume"))
        px = pm_last or patterns._f((q or {}).get("bidPrice"))
        pm_dollar = vol * px if vol is not None and px else None
    elif cfg.consolidated_source == "sip":
        sip = client.get_bars(sym, "1Min", start=f"{today}T08:00:00Z",
                              end=f"{today}T13:30:00Z", feed="sip").get(sym, [])
        pm_dollar = sum(
            (patterns._f(b.get("v")) or 0.0) * (patterns._f(b.get("c")) or 0.0)
            for b in sip) or None

    flt = fmp.get_shares_float(sym) or {}
    market_cap = patterns._f(profile.get("mktCap"))
    dilution_flag = None
    if market_cap is None or market_cap < cfg.small_cap_usd:
        since = (date.fromisoformat(today)
                 - timedelta(days=cfg.dilution_lookback_days)).isoformat()
        filings = fmp.get_sec_filings(sym, since, today)
        if filings is not None:
            dilution_flag = any(
                str(f.get("formType", "")).upper().startswith(("S-3", "424B5"))
                for f in filings)

    if cfg.llm_classify and cand.get("catalyst_class") is None and foundry is not None:
        from daytrade.classify import classify_catalyst_llm
        heads = [n.get("title", "") for n in fmp.get_stock_news([sym], limit=10)]
        verdict = classify_catalyst_llm(foundry, sym, heads, dilution_flag)
        if verdict:
            cand["catalyst_class"] = verdict["catalyst_class"]

    quote = client.get_latest_quote(sym) or {}
    return {
        "in_core": sym in CORE_ROSTER,
        "in_catalyst_ledger": sym in catalyst_symbols,
        "in_daytrade_ledger": sym in read_ledger(),
        "is_common": is_common,
        "prior_close": patterns._f((prior_bar or {}).get("c")),
        "prior_day_high": patterns._f((prior_bar or {}).get("h")),
        "prior_day_low": patterns._f((prior_bar or {}).get("l")),
        "pm_last": pm_last,
        "pm_high": max((h for b in pm_bars if (h := patterns._f(b.get("h"))) is not None),
                       default=None),
        "pm_low": min((lo for b in pm_bars if (lo := patterns._f(b.get("l"))) is not None),
                      default=None),
        "pm_iex_volume": pm_vol or None,
        "pm_iex_vol_30d_avg": pm_avg,
        "pm_dollar_volume": pm_dollar,
        "float_shares": patterns._f(flt.get("floatShares")),
        "market_cap": market_cap,
        "dilution_flag": dilution_flag,
        "bid": patterns._f(quote.get("bp")),
        "ask": patterns._f(quote.get("ap")),
    }


# ---------------------------------------------------------------------------
# entries / management / flat
# ---------------------------------------------------------------------------

def _try_entries(client, day, ledger, positions, today, minutes, equity, cfg) -> None:
    ok1, _ = st.can_enter_slot1(day, minutes, cfg)
    ok2, _ = st.can_enter_slot2(day, minutes, cfg)
    if ok1 and day.get("primary"):
        cand, slot = day["primary"], 1
    elif ok2 and day.get("backup"):
        cand, slot = day["backup"], 2
    else:
        return
    sym = cand["symbol"]
    if sym in day["entered_symbols"]:
        return   # one entry per name per day

    grades = read_json_blob(_GRADES_CONTAINER, "latest.json")

    bars = client.get_bars(sym, "1Min", start=_utc(today, "09:30")).get(sym, [])
    if patterns.is_print_stale(bars, datetime.now().timestamp(), cfg.stale_print_max_s):
        return   # stale-print halt guard — no entry this tick

    if slot == 2 and not _revalidate_live(client, cand, bars, cfg):
        return

    allowed, half = patterns.allowed_patterns(day["tone"], cand.get("catalyst_class"))
    orb_n = cfg.orb_minutes_c if (cand.get("catalyst_class") == "C") else cfg.orb_minutes
    rng = patterns.opening_range(bars, orb_n)
    signal = None
    if "orb" in allowed:
        sig = patterns.orb_signal(bars, rng, cfg)
        if sig["signal"]:
            signal = sig
    if signal is None and "vwap_pullback" in allowed:
        sig = patterns.vwap_pullback_signal(bars, cfg)
        if sig["signal"]:
            signal = sig
    if signal is None:
        return

    refusal = grading.entry_refusal(
        grades if isinstance(grades, dict) else None, cfg.spec_version,
        cand.get("catalyst_class"), signal["pattern"])
    if refusal:
        logger.warning("daytrade: entry refused by pre-registered rule: %s", refusal)
        return

    half = half or (cand.get("catalyst_class") == "C")
    sz = sizing.size_daytrade_entry(
        equity, signal["entry"], signal["stop"], cfg,
        catalyst_open_notional=_notional_for(positions, _catalyst_ledger_symbols()),
        daytrade_open_notional=_notional_for(positions, set(ledger)),
        half_risk=half,
    )
    if sz["size_shares"] < 1:
        return
    qty = sz["size_shares"]
    risk = signal["entry"] - signal["stop"]
    target = round(signal["entry"] + 2.0 * risk, 2)
    stop = round(signal["stop"], 2)
    try:
        if cfg.scale_mode == "none":
            order = client.submit_order(
                sym, qty, "buy", order_type="market", time_in_force="day",
                order_class="bracket",
                take_profit={"limit_price": target},
                stop_loss={"stop_price": stop},
                client_order_id=_coid(today, sym, f"e{slot}"))
        else:
            order = client.submit_order(
                sym, qty, "buy", order_type="market", time_in_force="day",
                order_class="oto", stop_loss={"stop_price": stop},
                client_order_id=_coid(today, sym, f"e{slot}"))
    except Exception:  # noqa: BLE001
        logger.exception("daytrade: entry order for %s failed", sym)
        return
    legs = order.get("legs") or []
    ids = [str(order.get("id"))] + [str(leg.get("id")) for leg in legs]
    ledger[sym] = new_entry(
        sym, slot, signal["entry"], today, stop, target, qty,
        signal["pattern"], cand.get("catalyst_class"), day["tone"], minutes,
        order_ids=ids)
    day["entered_symbols"].append(sym)
    logger.info("daytrade: slot %s entry %s ×%d @~%.2f stop %.2f (%s, binding=%s)",
                slot, sym, qty, signal["entry"], stop, signal["pattern"],
                sz["binding"])


def _revalidate_live(client, cand, bars, cfg) -> bool:
    """§3a — slot-2 live re-check of gates 2/3/5 (gap, rvol, spread)."""
    last = patterns._f(bars[-1].get("c")) if bars else None
    prior_close = None
    for g_ in cand.get("gates") or []:
        if g_.get("gate") == "price_band":
            prior_close = patterns._f(g_.get("value"))
    if last is None or prior_close is None or prior_close <= 0:
        return False
    if (last - prior_close) / prior_close * 100.0 < cfg.gap_min_pct:
        return False
    if (cand.get("rvol") or 0) < cfg.rvol_min:   # pre-market ratio is final
        return False
    q = client.get_latest_quote(cand["symbol"]) or {}
    bid, ask = patterns._f(q.get("bp")), patterns._f(q.get("ap"))
    if not bid or not ask or ask <= bid:
        return False
    return (ask - bid) / ((ask + bid) / 2.0) <= cfg.spread_max


def _manage_half_at_1r(client, ledger, today) -> None:
    """`half_at_1r` mode: at +1R sell half, stop→breakeven, runner OCO 2R."""
    for sym, entry in list(ledger.items()):
        if entry.get("scaled_out"):
            continue
        bars = client.get_bars(sym, "1Min", start=_utc(today, "09:30")).get(sym, [])
        px = patterns._f(bars[-1].get("c")) if bars else None
        risk = patterns._f(entry.get("risk_per_share"))
        if px is None or not risk or risk <= 0:
            continue
        if (px - entry["entry_price"]) / risk < 1.0:
            continue
        qty = int(entry.get("qty_current") or 0)
        half = max(1, qty // 2)
        runner = qty - half
        try:
            for oid in entry.get("order_ids") or []:
                client.cancel_order(oid)
            client.submit_order(sym, half, "sell", order_type="market",
                                time_in_force="day",
                                client_order_id=_coid(today, sym, "s1r"))
            if runner > 0:
                oco = client.submit_order(
                    sym, runner, "sell", order_type="limit", time_in_force="day",
                    order_class="oco",
                    take_profit={"limit_price": entry["target_price"]},
                    stop_loss={"stop_price": round(entry["entry_price"], 2)},
                    client_order_id=_coid(today, sym, "oco"))
                entry["order_ids"] = [str(oco.get("id"))] \
                    + [str(leg.get("id")) for leg in (oco.get("legs") or [])]
            entry["qty_current"] = runner
            entry["scaled_out"] = True
            entry["stop_price"] = entry["entry_price"]   # breakeven
        except Exception:  # noqa: BLE001
            logger.exception("daytrade: half_at_1r for %s failed", sym)


def _flatten_all(client, ledger, today) -> None:
    """11:15 flat — cancel legs, market-sell; reconcile records the fill next tick."""
    for sym, entry in ledger.items():
        if entry.get("flat_sent"):
            continue
        try:
            for oid in entry.get("order_ids") or []:
                client.cancel_order(oid)
            qty = int(entry.get("qty_current") or 0)
            if qty > 0:
                client.submit_order(sym, qty, "sell", order_type="market",
                                    time_in_force="day",
                                    client_order_id=_coid(today, sym, "flat"))
            entry["flat_sent"] = True
            entry["exit_reason"] = "time_flat_1115"
        except Exception:  # noqa: BLE001
            logger.exception("daytrade: flatten for %s failed", sym)


def _apply_repair(client, rep, today) -> None:
    if rep.get("action") == "flatten_orphan":
        sym, qty = rep["symbol"], int(rep.get("qty") or 0)
        try:
            if qty > 0:
                client.submit_order(sym, qty, "sell", order_type="market",
                                    time_in_force="day",
                                    client_order_id=_coid(today, sym, "orph"))
                logger.error("daytrade: ORPHAN position %s flattened (no protective order)", sym)
        except Exception:  # noqa: BLE001
            logger.exception("daytrade: orphan flatten for %s failed", sym)


# ---------------------------------------------------------------------------
# closure recording + grading
# ---------------------------------------------------------------------------

def _record_closure(client, day, entry, today, cfg, reason: str) -> None:
    sym = entry.get("symbol", "?")
    exit_px = _fill_price(client, sym) or entry.get("stop_price")
    raw, net = grading.net_r(entry["entry_price"], entry["stop_price"],
                             float(exit_px), cfg.haircut_pp_per_side)
    outcome = grading.outcome_of(net) if net is not None else "scratch"
    minutes = _minutes_now_or(entry.get("entered_min", 0.0), client, today)
    mfe, mae = _mfe_mae(client, entry, today)
    if day is not None:
        st.record_outcome(day, int(entry.get("slot") or 1), sym, outcome,
                          net or 0.0, minutes, cfg)
    _log_row(today, {
        "slot": entry.get("slot"), "symbol": sym, "outcome": outcome,
        "catalyst_class": entry.get("catalyst_class"),
        "pattern": entry.get("pattern"), "tone": entry.get("tone"),
        "entry": entry.get("entry_price"), "stop": entry.get("stop_price"),
        "exit": exit_px, "qty": entry.get("qty_initial"),
        "r_multiple_raw": raw, "r_multiple_net": net,
        "slippage_haircut_pp": round(2 * cfg.haircut_pp_per_side, 2),
        "mfe": mfe, "mae": mae,
        "hold_min": round(max(0.0, minutes - float(entry.get("entered_min") or 0)), 1),
        "exit_reason": entry.get("exit_reason") or reason,
    }, cfg)


def _grade_and_halt(day, today, cfg) -> None:
    rows: list[dict] = []
    for name in list_blob_names(_LOG_CONTAINER):
        rows.extend(read_jsonl_blob(_LOG_CONTAINER, name))
    grades = grading.build_daytrade_grades(rows, cfg.spec_version)
    write_json_blob(_GRADES_CONTAINER, "latest.json", grades)
    monday = st.week_monday(today)
    week_r = sum(
        float(r.get("r_multiple_net") or 0.0) for r in rows
        if r.get("outcome") in ("win", "loss", "scratch")
        and monday <= str(r.get("date", "")) <= today)
    halt = read_json_blob(_STATE_CONTAINER, _HALT_BLOB)
    new_halt = st.apply_weekly_breaker(
        halt if isinstance(halt, dict) else None, today, week_r, cfg)
    if new_halt and new_halt is not halt:
        write_json_blob(_STATE_CONTAINER, _HALT_BLOB, new_halt)
        logger.error("daytrade: WEEKLY BREAKER — %s", new_halt)


def _mfe_mae(client, entry, today) -> tuple[float | None, float | None]:
    try:
        sym = entry["symbol"]
        bars = client.get_bars(sym, "1Min", start=_utc(today, "09:30")).get(sym, [])
        risk = float(entry.get("risk_per_share") or 0)
        if not bars or risk <= 0:
            return None, None
        highs = [h for b in bars if (h := patterns._f(b.get("h"))) is not None]
        lows = [lo for b in bars if (lo := patterns._f(b.get("l"))) is not None]
        e = float(entry["entry_price"])
        return (round((max(highs) - e) / risk, 3) if highs else None,
                round((e - min(lows)) / risk, 3) if lows else None)
    except Exception:  # noqa: BLE001
        return None, None


def _fill_price(client, symbol) -> float | None:
    """Most recent FLEXD sell fill for the symbol (the leg that closed it)."""
    try:
        for o in client.list_orders("closed", 100):
            if (str(o.get("symbol", "")).upper() == symbol
                    and str(o.get("side")) == "sell"
                    and str(o.get("client_order_id", "")).startswith(COID_PREFIX)
                    and o.get("filled_avg_price")):
                return float(o["filled_avg_price"])
        for o in client.list_orders("closed", 100):   # bracket legs have no COID
            if (str(o.get("symbol", "")).upper() == symbol
                    and str(o.get("side")) == "sell" and o.get("filled_avg_price")):
                return float(o["filled_avg_price"])
    except Exception:  # noqa: BLE001
        logger.exception("daytrade: fill lookup for %s failed", symbol)
    return None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _log_row(today, row: dict, cfg) -> None:
    append_jsonl_blob(_LOG_CONTAINER, f"{today}.jsonl", {
        "date": today, "spec_version": cfg.spec_version,
        "ts": _now_utc_iso(), **row,
    })


def _log_once(day, today, cfg, outcome: str, note, flag: str) -> None:
    if not day.get(flag):
        _log_row(today, {"slot": None, "symbol": None, "outcome": outcome,
                         "tone": day.get("tone"), "note": note}, cfg)
        day[flag] = True


def _read_day_state(today) -> dict | None:
    data = read_json_blob(_STATE_CONTAINER, f"{today}.json")
    return data if isinstance(data, dict) else None


def _write_day_state(today, day) -> None:
    write_json_blob(_STATE_CONTAINER, f"{today}.json", day)


def _catalyst_ledger_symbols() -> set[str]:
    data = read_json_blob(*_CATALYST_LEDGER)
    return set(data.keys()) if isinstance(data, dict) else set()


def _notional_for(positions, symbols: set[str]) -> float:
    total = 0.0
    for p in positions or []:
        if str(p.get("symbol", "")).upper() in symbols:
            try:
                total += abs(float(p.get("market_value") or 0))
            except (TypeError, ValueError):
                pass
    return total


def _premarket_bars(client, sym, today) -> list[dict]:
    """IEX 1-min bars from 04:00 ET to the 09:30 open (sparse — spec §2)."""
    bars = client.get_bars(sym, "1Min", start=_utc(today, "04:00"),
                           end=_utc(today, "09:30")).get(sym, [])
    return bars


def _premarket_vol_30d_avg(client, sym, today) -> float | None:
    """The symbol's own 30-day average pre-market IEX volume (iex_ratio basis)."""
    try:
        start = (date.fromisoformat(today) - timedelta(days=45)).isoformat()
        bars = client.get_bars(sym, "1Min", start=f"{start}T00:00:00Z",
                               end=_utc(today, "04:00")).get(sym, [])
    except Exception:  # noqa: BLE001
        logger.exception("daytrade: 30d premarket history for %s failed", sym)
        return None
    by_day: dict[str, float] = {}
    for b in bars:
        t = str(b.get("t", ""))
        if len(t) < 16:
            continue
        # Pre-market = 08:00–13:30 UTC (04:00–09:30 ET in DST; the hour drift in
        # winter slightly widens the window — acceptable for a 30-day average).
        hhmm = t[11:16]
        if "08:00" <= hhmm < "13:30":
            v = patterns._f(b.get("v"))
            if v is not None:
                by_day[t[:10]] = by_day.get(t[:10], 0.0) + v
    items = sorted((d, v) for d, v in by_day.items() if d < today)[-30:]
    if not items:
        return None
    return sum(v for _, v in items) / len(items)


def _session_minutes(client, today, now_et) -> float | None:
    try:
        cal = client.get_calendar(today, today)
        if not cal:
            return None
        oh, om = (int(x) for x in str(cal[0]["open"]).split(":"))
        open_et = now_et.replace(hour=oh, minute=om, second=0, microsecond=0)
        return (now_et - open_et).total_seconds() / 60.0
    except Exception:  # noqa: BLE001
        logger.exception("daytrade: session-minutes calc failed")
        return None


def _minutes_now_or(default, client, today) -> float:
    m = _session_minutes(client, today, _now_et())
    return m if m is not None else float(default)


def _equity(client) -> float:
    try:
        return float(client.get_account().get("equity") or 0)
    except Exception:  # noqa: BLE001
        logger.exception("daytrade: equity read failed")
        return 0.0


def _utc(today: str, hhmm_et: str) -> str:
    """An ET wall-clock time on ``today`` as a UTC ISO string (DST-safe)."""
    from zoneinfo import ZoneInfo
    h, m = (int(x) for x in hhmm_et.split(":"))
    dt = datetime.fromisoformat(today).replace(
        hour=h, minute=m, tzinfo=ZoneInfo(_ET))
    return dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coid(today, sym, kind) -> str:
    return f"{COID_PREFIX}-{today}-{sym}-{kind}-{uuid.uuid4().hex[:6]}"[:48]


def _now_utc_iso() -> str:
    from datetime import timezone
    return datetime.now(timezone.utc).isoformat()


def _now_et() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(_ET))
    except Exception:  # noqa: BLE001
        from datetime import timezone
        return datetime.now(timezone.utc).astimezone()
