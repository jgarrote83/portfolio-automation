"""Phase-1 analyzer: blob-triggered Function that turns a daily snapshot into a
markdown report + trade recommendations via Azure AI Foundry (Claude Sonnet 4.6).

Trigger: new blob in `daily-snapshots/{name}.json`.

Outputs:
- `daily-reports/{date}.md`  — markdown analysis
- `daily-trades/{date}.json` — structured trade recommendations
- `TradeHistory` table rows for each trade
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from shared.keyvault import load_secrets
from shared.storage import (
    list_recent_reports,
    upsert_entity,
    write_report,
    write_trades,
)
from shared.clients.foundry import FoundryClient
from shared.overrides import OVERRIDE_DEFAULTS, validate_overrides
from shared.quadrants import (
    CORE_ROSTER,
    EXEMPT_HOLDS,
    QUADRANT_CONCENTRATE,
    active_quadrant,
    benchmark_etf_for,
)
from shared.reference_execution import REFERENCE_EXECUTION_DEFAULTS, reconcile
from shared.trade_validation import validate_trades

logger = logging.getLogger(__name__)

_SRC = Path(__file__).parent.parent
_SYSTEM_PROMPT_FILE = _SRC / "config" / "project-instructions.md"
_RISK_LIMITS_FILE = _SRC / "config" / "risk-limits.json"
_TRADES_MARKER = "===TRADES_JSON==="


def _load_override_cfg() -> dict:
    """The override_protocol config from risk-limits.json (fallback to module defaults)."""
    try:
        data = json.loads(_RISK_LIMITS_FILE.read_text(encoding="utf-8"))
        block = data.get("override_protocol")
        return {**OVERRIDE_DEFAULTS, **block} if isinstance(block, dict) else dict(OVERRIDE_DEFAULTS)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(OVERRIDE_DEFAULTS)


def _load_reference_execution_cfg() -> dict:
    """override_protocol + reference_execution + exempt_holds (+ the Tier-1 scalars the
    trade validator needs) from risk-limits.json, shaped for
    `shared.reference_execution.reconcile` and `shared.trade_validation.validate_trades`
    (fallback to module defaults)."""
    out = {
        "override_protocol": dict(OVERRIDE_DEFAULTS),
        "reference_execution": dict(REFERENCE_EXECUTION_DEFAULTS),
        "exempt_holds": list(EXEMPT_HOLDS),
        "sleeve_floor_pct_of_core": 0.1,
        "active_quadrant_ceiling_pct_of_core": 90.0,
    }
    try:
        data = json.loads(_RISK_LIMITS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return out
    for key in ("override_protocol", "reference_execution"):
        block = data.get(key)
        if isinstance(block, dict):
            out[key] = {**out[key], **{k: v for k, v in block.items() if not k.startswith("_")}}
    if isinstance(data.get("exempt_holds"), list):
        out["exempt_holds"] = data["exempt_holds"]
    for key in ("sleeve_floor_pct_of_core", "active_quadrant_ceiling_pct_of_core"):
        if isinstance(data.get(key), (int, float)):
            out[key] = float(data[key])
    return out

# Prompt↔code schema gate for the Flex catalyst engine. The engine consumes
# `flex_nominations` from the analyzer's output; if a stale/reverted prompt no
# longer emits them (or the sentinel), the two engines silently desync. Fail
# loud at load rather than ship old-style flex trades.
_FLEX_SCHEMA_SENTINELS = ("FLEX_SCHEMA_V1", "flex_nominations")

# Prompt↔code gate for the Phase-4 override protocol: the live prompt must instruct the
# model to consume `reference_weights` and emit the override-record schema, or the analyzer
# is executing against a stale prompt that ignores the reference (the 2026-06-30 pathology).
# V1_1 (Finding 2): overrides became per-sleeve records with residual-cap semantics; a
# prompt still on V1 would emit sleeve-less records the validator now rejects wholesale.
_OVERRIDE_SCHEMA_SENTINELS = ("OVERRIDE_SCHEMA_V1_1", "reference_weights", "overrides")


def assert_flex_prompt_schema(prompt: str) -> None:
    """Raise if the system prompt is missing the flex nomination schema markers."""
    missing = [s for s in _FLEX_SCHEMA_SENTINELS if s not in prompt]
    if missing:
        raise RuntimeError(
            f"project-instructions.md is missing flex schema markers {missing} — "
            "prompt/code desync (a reverted prompt?). Refusing to run."
        )


def assert_override_prompt_schema(prompt: str) -> None:
    """Raise if the system prompt is missing the Phase-4 override-protocol markers."""
    missing = [s for s in _OVERRIDE_SCHEMA_SENTINELS if s not in prompt]
    if missing:
        raise RuntimeError(
            f"project-instructions.md is missing override-protocol markers {missing} — "
            "prompt/code desync (the prompt would ignore reference_weights). Refusing to run."
        )

# Soft caps to keep the user message inside Claude's context window. The full snapshot
# is ~935 KB / ~318K tokens — WELL over the 200K model limit — so trimming is not
# cosmetic: an untrimmed prompt overflows the context and the analyzer never completes.
# The deterministic analytics blocks (growth_axis/inflation_axis/bond_signals/labor_signals/
# regional_rotation/reference_weights/divergences) are the conclusions the analyzer reads;
# the raw macro series / alt-data are supporting detail and are aggressively clipped here.
_MAX_NEWS_PER_SCOPE = 15
_MAX_COMPANY_NEWS_PER_TICKER = 3
_MAX_CONGRESSIONAL = 25
_MAX_RECENT_REPORTS = 5
_MAX_GOV_CONTRACTS = 20
_MAX_LOBBYING = 12
_MAX_EARNINGS = 25
_MAX_STOCK_NEWS = 15
# Fundamentals: keep only the fields the analyzer reasons over (valuation / quality /
# sector), dropping the verbose FMP profile boilerplate (description, address, ceo, cik,
# cusip, isin, phone, image, website, zip, …) that is ~2/3 of each entry and never used.
_FUNDAMENTALS_FIELDS_KEPT = {
    "symbol", "companyName", "sector", "industry", "price", "beta", "marketCap",
    "pe", "peRatio", "eps", "dcf", "rating", "lastDividend", "range",
    "changePercentage", "averageVolume", "isEtf", "isFund", "earningsDate",
}
_MACRO_OBS_KEPT = 3          # latest N observations per kept raw macro series
_RECENT_REPORT_CHARS = 4000  # head excerpt of each prior report (summary + call), not the whole thing
# Raw macro series the analyzer actually cites (Freshness table + context). The axes are
# pre-computed, so the deep history behind them is dropped — only these, latest few obs.
_MACRO_SERIES_KEPT = {
    "GDPNOW", "GDPNOW_VINTAGES", "CPILFESL", "PCEPILFE", "CPIAUCSL", "PCEPI", "PPIACO",
    "DFF", "DGS10", "DGS2", "DFII10", "T5YIE", "T5YIFR", "T10YIE",
    "DCOILWTICO", "DCOILBRENTEU", "DTWEXBGS", "UNRATE", "ICSA", "PAYEMS",
    "ECBDFR", "DEXJPUS", "DEXUSEU", "DEXCHUS",
}


def analyze_snapshot(snapshot_bytes: bytes, blob_name: str) -> None:
    """Main entry point — called by the blob trigger in function_app.py."""
    snapshot = json.loads(snapshot_bytes)
    date_str = snapshot.get("date") or _date_from_blob_name(blob_name)
    if not date_str:
        raise ValueError(f"Cannot determine date from snapshot/blob name: {blob_name}")

    logger.info("=== Analyzer starting for %s ===", date_str)

    secrets = load_secrets()
    api_key = secrets.get("FoundryApiKey")
    if not api_key:
        raise RuntimeError("FoundryApiKey missing from Key Vault")

    client = FoundryClient(api_key=api_key)
    if not client.ready:
        raise RuntimeError(
            "FoundryClient not ready — check FOUNDRY_ENDPOINT app setting"
        )

    system_prompt = _SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")
    assert_flex_prompt_schema(system_prompt)
    assert_override_prompt_schema(system_prompt)
    recent = list_recent_reports(limit=_MAX_RECENT_REPORTS)
    logger.info("Loaded %d recent reports for continuity", len(recent))

    user_message = _build_user_message(snapshot, recent)
    logger.info("User message size: %d chars", len(user_message))

    raw = client.complete(
        system=system_prompt,
        user_message=user_message,
    )

    report_md, trades_obj = _split_response(raw, date_str)

    # Reference gaps are built FIRST (session 2026-07-15, Task E1 needs them to
    # derive each override's direction before validating overrides). Non-fatal:
    # a gap-build failure must never lose the report/trades — downstream steps
    # degrade gracefully with gaps=[] (Tier-1's absolute rules still apply).
    gaps: list[dict] = []
    ctx: dict = {}
    rex_cfg = _load_reference_execution_cfg()
    try:
        gaps, ctx = _build_reference_gaps(snapshot)
        ctx["date"] = date_str
        ctx["exempt_holds"] = rex_cfg["exempt_holds"]
    except Exception as e:  # noqa: BLE001
        logger.error("Reference-gap build failed (non-fatal): %s", e)

    vctx = dict(ctx) if ctx else {
        "deployment_gate": (snapshot.get("regime_gate") or {}).get("status"),
        "exempt_holds": rex_cfg["exempt_holds"],
        "intl_leader_pick": (snapshot.get("intl_governance") or {}).get("leader_pick"),
    }

    # Phase 4 — validate the override records the model emitted (Tier-2 enforcement):
    # structural gates + the de-risk/re-risk asymmetry. Rejected overrides do not
    # authorize a deviation; downsized ones have their magnitude halved. The result is
    # stamped back into trades_obj so the persisted record shows what was accepted, and
    # into OverrideHistory for the Phase-5 outcome loop. Non-fatal: a validation error
    # must not lose the report/trades. `gaps` (Task E1) lets the validator DERIVE each
    # override's direction from the sleeve's block + gap sign rather than trusting the
    # model's self-declared `direction` — see shared/overrides.py::validate_override.
    try:
        cfg = _load_override_cfg()
        result = validate_overrides(trades_obj.get("overrides", []), cfg, gaps)
        trades_obj["override_validation"] = {
            "accepted": len(result["accepted"]),
            "downsized": len(result["downsized"]),
            "rejected": len(result["rejected"]),
            "decisions": result["decisions"],
        }
        logger.info(
            "Override validation: %d accepted, %d downsized, %d rejected",
            len(result["accepted"]), len(result["downsized"]), len(result["rejected"]),
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Override validation failed (non-fatal): %s", e)
        result = {"decisions": []}

    # --- Session 2026-07-15, Task B1: two-pass validation around reconcile -------
    # Before this, reconcile ran on the model's RAW trades BEFORE Tier-1 validation,
    # so a buy Tier-1 would go on to reject (e.g. a gate-closed amplifier buy) was
    # still counted as already-spent cash while reconcile sized its own enforcement
    # buys — 07-14: a rejected $1,927 VXUS buy starved a KMLM enforcement buy down
    # to 57 of a true ~126 shares. Pass 1 validates the model's raw trades and drops
    # what Tier-1 would reject BEFORE reconcile ever sees the list, so its cash_avail
    # reflects reality. Pass 2 re-validates the FULL merged list (pass-1 survivors +
    # any synthesized enforcement trades) so cumulative checks (V3/V4 oversell, the
    # SGOV pre-cash tracker, the aggregate amplifier assertion, within-role budgets)
    # see the final list, not just the enforced trades in isolation.

    # Pass 1 — Tier-1 over the model's raw trades. FAIL-CLOSED like the old single
    # pass: a crash here means the validator never ran cleanly, so the file is
    # quarantined and reconcile/pass-2 are skipped entirely.
    pass1_rejected: list[dict] = []
    survivors: list[dict] = []
    try:
        tv1 = validate_trades(
            gaps, trades_obj.get("trades", []), result.get("decisions", []),
            rex_cfg, vctx,
        )
        survivors = tv1["trades"]
        pass1_rejected = tv1["rejected"]
    except Exception:  # noqa: BLE001
        logger.exception("Pass-1 trade validation CRASHED — flagging file (fail-closed)")
        trades_obj["validation_error"] = True

    if not trades_obj.get("validation_error"):
        # Finding 2 (D3) — reconcile against the pass-1 SURVIVORS (not the model's
        # raw list), so a trade Tier-1 already rejected can never be mistaken for
        # spent cash. Non-fatal: an enforcement error must never lose the report.
        merged = survivors
        try:
            if gaps:
                recon = reconcile(
                    gaps, survivors, result.get("decisions", []), rex_cfg, ctx,
                )
                trades_obj["reference_execution"] = {
                    "sleeves": recon["sleeves"],
                    "summary": recon["summary"],
                    "enforcement_notional_usd": recon["enforcement_notional_usd"],
                }
                # Task D2 (unconditional, no gate): surface non_compliant_flagged
                # sleeves in the REPORT, not just the JSON.
                report_md += _flagged_sleeves_addendum(recon["sleeves"])
                if recon["enforced_trades"]:
                    # Keep the executor's sells-before-buys contract across the
                    # merge (stable sort preserves model order within each side).
                    merged = sorted(
                        survivors + recon["enforced_trades"],
                        key=lambda t: str(t.get("side", "")).lower() != "sell",
                    )
                    _stamp_enforced_decisions(result.setdefault("decisions", []), recon)
                logger.info(
                    "Reference execution: %s, enforced_notional=$%.0f",
                    recon["summary"], recon["enforcement_notional_usd"],
                )
            else:
                logger.info("Reference execution: no gaps computable (reference/account absent)")
        except Exception as e:  # noqa: BLE001
            logger.error("Band enforcement failed (non-fatal): %s", e)

        # Pass 2 — Tier-1 over the FULL merged list. Synthesized trades must pass
        # by construction; a rejection there is a reconcile bug (bad_enforced
        # tripwire, unchanged). FAIL-CLOSED like pass 1.
        try:
            tv2 = validate_trades(
                gaps, merged, result.get("decisions", []), rex_cfg, vctx,
            )
            trades_obj["trades"] = tv2["trades"]
            # Combine both passes' rejections, deduped by trade id (no double-
            # stamping) — pass 1 and pass 2 operate on disjoint candidate sets in
            # practice, but a shared id is a cheap safety net.
            seen_ids: set = set()
            all_rejected: list[dict] = []
            for r in pass1_rejected + tv2["rejected"]:
                rid = r.get("id")
                if rid is not None and rid in seen_ids:
                    continue
                if rid is not None:
                    seen_ids.add(rid)
                all_rejected.append(r)
            combined_summary = {
                "passed": tv2["summary"]["passed"],
                "clamped": tv2["summary"]["clamped"],
                "rejected": len(all_rejected),
            }
            trades_obj["trade_validation"] = {
                "summary": combined_summary, "rejected": all_rejected,
            }
            bad_enforced = [
                t for t in tv2["rejected"] if t.get("source") == "band_enforcement"
            ]
            if bad_enforced:
                logger.error(
                    "Tier-1 validator rejected %d band_enforcement trade(s) — "
                    "reconcile bug, investigate: %s",
                    len(bad_enforced), [t.get("id") for t in bad_enforced],
                )
            if all_rejected or combined_summary["clamped"]:
                report_md += _validation_addendum(
                    {"summary": combined_summary, "trades": tv2["trades"],
                     "rejected": all_rejected},
                    gaps, vctx, snapshot.get("paper_account", {}).get("positions"),
                )
            logger.info("Trade validation: %s", combined_summary)
        except Exception:  # noqa: BLE001
            logger.exception("Pass-2 trade validation CRASHED — flagging file (fail-closed)")
            trades_obj["validation_error"] = True

    write_report(date_str, report_md)
    write_trades(date_str, trades_obj)
    _write_trade_history(date_str, trades_obj, snapshot)
    _write_override_history(date_str, result.get("decisions", []), snapshot)
    _write_regime_suspect_history(date_str, snapshot, trades_obj)

    logger.info(
        "=== Analyzer completed for %s — %d trades recommended ===",
        date_str, len(trades_obj.get("trades", [])),
    )


# ---------------------------------------------------------------------------
# Reference execution (Finding 2 — input assembly for shared/reference_execution.py)
# ---------------------------------------------------------------------------

def _flagged_sleeves_addendum(recon_sleeves: dict) -> str:
    """Markdown addendum listing `reconcile()`'s `non_compliant_flagged` sleeves
    (session 2026-07-15, Task D2) — sleeves that fell short of their required
    tranche move and that band enforcement could not synthesize (a re-risk
    shortfall is never auto-traded, spec §6 asymmetry: e.g. a legacy exit slow-
    walked below tranche pace with no override filed). Unconditional, no gate —
    empty string when nothing is flagged.
    """
    flagged = {
        sym: e for sym, e in (recon_sleeves or {}).items()
        if e.get("status") == "non_compliant_flagged"
    }
    if not flagged:
        return ""
    lines = [
        "\n\n---\n\n### 🚩 Reference-execution shortfalls (deterministic, post-model)\n",
        "Sleeve(s) that fell short of the required tranche move toward reference and "
        "were NOT auto-corrected (re-risk shortfalls are never synthesized — spec §6 "
        "asymmetry; file an honest override or trade the remainder next session):\n",
    ]
    for sym, e in sorted(flagged.items()):
        reasons = "; ".join(e.get("reasons") or [])
        lines.append(
            f"- **{sym}**: gap {e.get('gap_pp')}pp vs reference, required move today "
            f"{e.get('required_move_today_pp')}pp, model moved {e.get('model_move_pp')}pp"
            f"{f' — {reasons}' if reasons else ''}"
        )
    return "\n".join(lines) + "\n"


def _validation_addendum(tv: dict, gaps: list[dict] | None = None,
                         ctx: dict | None = None,
                         positions: list[dict] | None = None) -> str:
    """Markdown addendum appended to the report when the Tier-1 validator rejected or
    clamped anything — the human-readable record next to the model's own prose.

    Also states the **submittable count** (X of Y proposed trades) and, when a
    rejection/clamp changed the cash arithmetic, a corrected literal-cash figure
    (the body's cash/sleeve prose assumed every proposed trade executed — the
    2026-07-09 report narrated a cash level built on a trade the validator dropped).
    """
    summary = tv["summary"]
    submittable = len(tv.get("trades", []))
    total = submittable + len(tv.get("rejected", []))
    lines = [
        "\n\n---\n\n### ⚠️ Trade-validation addendum (deterministic, post-model)\n",
        f"Tier-1 validator result: {summary['passed']} passed, "
        f"{summary['clamped']} clamped, {summary['rejected']} rejected.\n",
        f"**Submittable: {submittable} of {total} proposed trades.**\n",
    ]
    for t in tv.get("rejected", []):
        reasons = "; ".join((t.get("validation") or {}).get("reasons", []))
        lines.append(
            f"- **REJECTED** {t.get('side', '?').upper()} {t.get('quantity', '?')} "
            f"{t.get('symbol', '?')} ({t.get('id', 'no-id')}): {reasons}"
        )
    for t in tv.get("trades", []):
        v = t.get("validation") or {}
        if v.get("status") == "clamped":
            lines.append(
                f"- **CLAMPED** {t.get('side', '?').upper()} {t.get('symbol', '?')} "
                f"({t.get('id', 'no-id')}): {'; '.join(v.get('reasons', []))}"
            )
    # Cash-invalidation note: the body assumed ALL proposed trades executed; a
    # rejection/clamp changes the arithmetic, so recompute literal cash from the
    # VALIDATED (post-clamp) trades + the pre-trade cash in ctx.
    if (tv.get("rejected") or summary["clamped"]) and ctx is not None:
        post_cash = _post_validation_cash(tv.get("trades", []), gaps or [], ctx, positions)
        if post_cash is not None:
            lines.append(
                "\n_Body cash/sleeve figures assumed all proposed trades executed; "
                f"post-validation literal cash ≈ ${post_cash:,.0f}._"
            )
    return "\n".join(lines) + "\n"


def _post_validation_cash(trades: list[dict], gaps: list[dict], ctx: dict,
                          positions: list[dict] | None = None) -> float | None:
    """Literal cash after only the VALIDATED trades execute: pre-trade cash + sell
    proceeds - buy notional, priced off the gap rows (paper-account position price as
    a fallback for a validated trade in a name with no gap row — e.g. an off-roster
    flex leftover like MU, 2026-07-13 audit finding 3). None if pre-trade cash is
    unknown."""
    try:
        cash = float(ctx.get("cash_usd"))
    except (TypeError, ValueError):
        return None
    price: dict[str, float] = {}
    for p in positions or []:
        sym = str(p.get("ticker") or "").upper()
        try:
            px = float(p.get("current_price"))
        except (TypeError, ValueError):
            continue
        if sym and px > 0:
            price[sym] = px
    for g in gaps or []:
        sym = str(g.get("symbol") or "").upper()
        try:
            px = float(g.get("price"))
        except (TypeError, ValueError):
            continue
        if sym and px > 0:
            price[sym] = px   # gap-row price wins over the position-price fallback
    for t in trades or []:
        sym = str(t.get("symbol") or t.get("ticker") or "").upper()
        side = str(t.get("side") or t.get("action") or "").lower()
        try:
            qty = float(t.get("quantity") or t.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        px = price.get(sym)
        if not px or qty <= 0:
            continue
        if side == "sell":
            cash += qty * px
        elif side == "buy":
            cash -= qty * px
    return cash


def _build_reference_gaps(snapshot: dict) -> tuple[list[dict], dict]:
    """Per-sleeve current-vs-reference rows + account context for `reconcile()`.

    Universe = the reference `target_weights_pct` keys ∪ held core-roster names (a held
    core name missing from the targets counts as target 0). The cash/SGOV sleeve is
    governed by its own band, not the per-sleeve gap protocol — SGOV appears only if
    the reference explicitly targets it. Returns ([], {}) when the reference or the
    paper account is unavailable (enforcement then has nothing to reconcile against).

    A held name that is off-roster (neither a reference target nor a CORE_ROSTER
    member — a flex leftover like MU, 2026-07-13 audit finding 3) also gets a row, at
    `reference_pct: 0.0` and flagged `"off_roster": True`, so the Tier-1 validator's
    V3/V4 sell-side checks see it (sell ≤ held) and `_post_validation_cash` can price
    it. `reconcile` (band enforcement) must never synthesize a trade for one of these
    — flex exits are the flex engine's + human approval's job — so it filters
    `off_roster` rows out of its own working set.
    """
    ref = snapshot.get("reference_weights") or {}
    targets = ref.get("target_weights_pct") or {}
    pa = snapshot.get("paper_account") or {}
    try:
        equity = float(pa.get("equity") or 0)
    except (TypeError, ValueError):
        equity = 0.0
    if not targets or equity <= 0:
        return [], {}

    positions = {
        str(p.get("ticker") or "").upper(): p
        for p in (pa.get("positions") or []) if p.get("ticker")
    }
    prices = snapshot.get("prices") or {}

    def _price(sym: str) -> float | None:
        row = prices.get(sym)
        c = row.get("c") if isinstance(row, dict) else None
        if c is None:
            c = (positions.get(sym) or {}).get("current_price")
        try:
            px = float(c)
            return px if px > 0 else None
        except (TypeError, ValueError):
            return None

    gaps = []
    targets_up = {str(t).upper() for t in targets}
    core_held = set(positions) & set(CORE_ROSTER)
    off_roster_held = set(positions) - targets_up - set(CORE_ROSTER)
    universe = targets_up | core_held | off_roster_held
    for sym in sorted(universe):
        pos = positions.get(sym) or {}
        try:
            mv = float(pos.get("market_value") or 0)
        except (TypeError, ValueError):
            mv = 0.0
        try:
            # paper_account.positions uses "qty" (Alpaca-native); the canonical
            # portfolio.positions uses "quantity". A "quantity"-only read zeroed
            # held_qty here and V4-rejected every sell as "not held" (2026-07-07).
            held_qty = float(pos.get("qty") or pos.get("quantity") or 0)
        except (TypeError, ValueError):
            held_qty = 0.0
        gaps.append({
            "symbol": sym,
            "current_pct": round(mv / equity * 100.0, 4),
            "reference_pct": round(float(targets.get(sym, 0.0) or 0.0), 4),
            "price": _price(sym),
            "held_qty": held_qty,   # V4 sell-clamp input for the Tier-1 validator
            "off_roster": sym in off_roster_held,
        })
    ctx = {
        "deployment_gate": (snapshot.get("regime_gate") or {}).get("status"),
        "equity_usd": equity,
        "cash_usd": float(pa.get("cash") or 0),
        # Task 2: the literal-cash buffer for the Tier-1 SGOV cash-swap carve-out.
        "literal_cash_target_pct": float(ref.get("literal_cash_target_pct") or 1.5),
        # V1.5: the intl_leader role's auto-rotation exception — a buy of the CURRENT
        # leader_pick passes even before sleeve-roles.json's `selected` is committed to
        # match (Task F, intl_governance).
        "intl_leader_pick": (snapshot.get("intl_governance") or {}).get("leader_pick"),
    }
    return gaps, ctx


def _stamp_enforced_decisions(decisions: list[dict], recon: dict) -> None:
    """Mark the override decisions enforcement fired against (Phase-5 hook).

    For each enforced sleeve: a REJECTED record naming that sleeve is stamped
    `enforced: true`; with no record at all a synthetic decision (outcome
    `"enforced"`) is appended so OverrideHistory still carries one row per
    enforcement event. Accepted/downsized records are never stamped — enforcement
    only fires on the gap remainder their residual does not shelter.
    """
    for sym, entry in (recon.get("sleeves") or {}).items():
        if entry.get("status") != "enforced":
            continue
        trade = entry.get("enforced_trade") or {}
        stamped = False
        for dec in decisions:
            ov = dec.get("override") or {}
            if (dec.get("outcome") == "rejected"
                    and str(ov.get("sleeve") or "").upper() == sym):
                dec["enforced"] = True
                dec.setdefault("reasons", []).append(
                    f"band enforcement synthesized trade {trade.get('id')} against "
                    "this rejected record"
                )
                stamped = True
        if not stamped:
            decisions.append({
                "outcome": "enforced",
                "enforced": True,
                "override": {
                    "sleeve": sym,
                    "direction": "de_risk",
                    "magnitude_pp": entry.get("required_move_today_pp"),
                },
                "reasons": [
                    f"band enforcement fired with no override record — synthesized "
                    f"trade {trade.get('id')} "
                    f"({entry.get('required_move_today_pp')}pp tranche)"
                ],
            })


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

def _build_user_message(snapshot: dict, recent: list[tuple[str, str]]) -> str:
    """Compose a compact, structured user message for Claude.

    We pass the snapshot mostly as JSON, but trim oversized news arrays so we don't
    burn tokens on noise. Previous reports are included as plain text.
    """
    trimmed = _trim_snapshot(snapshot)

    parts: list[str] = []
    parts.append(f"# Daily snapshot for {snapshot.get('date', 'unknown')}\n")
    parts.append("## Snapshot data (JSON)\n")
    parts.append("```json")
    # Compact separators (no indent) — indentation adds ~15-20% tokens on a ~900KB JSON
    # for zero analytical value.
    parts.append(json.dumps(trimmed, default=str, separators=(",", ":")))
    parts.append("```\n")

    if recent:
        parts.append("## Recent reports (most recent first — head excerpt for continuity)\n")
        for d, md in recent:
            parts.append(f"### Report — {d}\n")
            excerpt = md.strip()
            if len(excerpt) > _RECENT_REPORT_CHARS:
                excerpt = excerpt[:_RECENT_REPORT_CHARS] + "\n…[report truncated for context]…"
            parts.append(excerpt)
            parts.append("\n---\n")

    parts.append(
        "\nProduce today's report and trade recommendations following the system "
        "instructions exactly. Remember the literal marker line "
        f"`{_TRADES_MARKER}` between the markdown report and the JSON block."
    )
    return "\n".join(parts)


def _trim_snapshot(snapshot: dict) -> dict:
    """Return a shallow copy trimmed to fit the model context window.

    The untrimmed snapshot is ~318K tokens (over the 200K limit). The deterministic
    analytics blocks — the conclusions the analyzer reads — are kept in full; the raw
    supporting detail (macro series history, alt-data, news) is clipped hard:
    - `macro.data`: keep only the cited series (`_MACRO_SERIES_KEPT`), latest few obs each;
      drop everything else and the deep history (the axes already encode it).
    - news / stock_news / congressional / gov_contracts / lobbying / earnings: clip counts.
    """
    s = dict(snapshot)

    # --- macro: keep cited series only, latest few observations ------------------
    macro = dict(s.get("macro") or {})
    data = macro.get("data")
    if isinstance(data, dict):
        clipped: dict = {}
        for k, v in data.items():
            if k not in _MACRO_SERIES_KEPT:
                continue
            clipped[k] = v[:_MACRO_OBS_KEPT] if isinstance(v, list) else v
        macro["data"] = clipped
        s["macro"] = macro

    # --- news: clip each scope --------------------------------------------------
    news = dict(s.get("news") or {})
    if "market" in news:
        news["market"] = news["market"][:_MAX_NEWS_PER_SCOPE]
    if "forex" in news:
        news["forex"] = news["forex"][:_MAX_NEWS_PER_SCOPE]
    if "company" in news and isinstance(news["company"], dict):
        news["company"] = {
            t: (items or [])[:_MAX_COMPANY_NEWS_PER_TICKER]
            for t, items in news["company"].items()
        }
    s["news"] = news

    # --- fundamentals: slim each entry to the fields the analyzer uses -----------
    fund = s.get("fundamentals")
    if isinstance(fund, list):
        s["fundamentals"] = [
            {k: v for k, v in (row or {}).items() if k in _FUNDAMENTALS_FIELDS_KEPT}
            for row in fund
        ]

    # --- alt-data + calendars: clip counts --------------------------------------
    for key, cap in (
        ("congressional_trades", _MAX_CONGRESSIONAL),
        ("stock_news", _MAX_STOCK_NEWS),
        ("gov_contracts", _MAX_GOV_CONTRACTS),
        ("lobbying", _MAX_LOBBYING),
        ("earnings_calendar", _MAX_EARNINGS),
    ):
        if isinstance(s.get(key), list):
            s[key] = s[key][:cap]
    return s


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _split_response(raw: str, date_str: str) -> tuple[str, dict]:
    """Split Claude's response into (markdown_report, trades_dict)."""
    if _TRADES_MARKER not in raw:
        logger.warning(
            "Marker missing in response for %s (len=%d) \u2014 saving raw output to "
            "daily-reports/_debug/%s-raw.txt; treating full response as report",
            date_str, len(raw), date_str,
        )
        try:
            from shared.storage import write_debug_raw
            write_debug_raw(date_str, raw)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not persist debug raw response: %s", e)
        return raw.strip(), {"trades": []}

    md_part, _, trades_part = raw.partition(_TRADES_MARKER)
    md_part = md_part.strip()

    trades_obj = _extract_json(trades_part.strip())
    if not isinstance(trades_obj, dict) or "trades" not in trades_obj:
        logger.warning("Trades block malformed — defaulting to empty list")
        trades_obj = {"trades": []}

    trades_obj.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
    trades_obj.setdefault("date", date_str)
    return md_part, trades_obj


def _extract_json(text: str) -> dict | None:
    """Extract the first JSON object from a string, tolerating ```json fences."""
    if not text:
        return None
    # Strip optional ```json ... ``` fence
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        # Fall back to first {...} balanced span
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        logger.warning("JSON parse failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Table writer
# ---------------------------------------------------------------------------

def _write_trade_history(date_str: str, trades_obj: dict, snapshot: dict | None = None) -> None:
    year_month = date_str[:7]  # YYYY-MM
    quadrant_current = trades_obj.get("quadrant_current") or ""
    quadrant_projected_6m = trades_obj.get("quadrant_projected_6m") or ""
    risk_score = trades_obj.get("risk_score")

    # Entry metadata for the conviction-sleeve flex review (computed here, NOT
    # taken from the LLM): the active quadrant at entry, its representative sleeve
    # ETF, and the snapshot entry price. Persisted write-once on a flex BUY so the
    # collector's `_build_flex_review` can score the name against its benchmarks.
    snap = snapshot or {}
    prices = snap.get("prices") or {}
    entry_quadrant = active_quadrant(
        (snap.get("growth_axis") or {}).get("direction"),
        (snap.get("inflation_axis") or {}).get("direction"),
    )
    entry_bench_etf = benchmark_etf_for(entry_quadrant)

    def _entry_price(symbol: str) -> float | None:
        row = prices.get(symbol) or {}
        c = row.get("c") if isinstance(row, dict) else None
        try:
            return round(float(c), 4) if c is not None else None
        except (TypeError, ValueError):
            return None

    for t in trades_obj.get("trades", []):
        trade_id = t.get("id") or f"T-{date_str.replace('-', '')}-X"
        is_flex_buy = (t.get("layer") == "flex") and (t.get("side") == "buy")
        try:
            entity = {
                "PartitionKey":         year_month,
                "RowKey":               trade_id,
                "recommended_at":       date_str,
                "status":               "recommended",
                "side":                 t.get("side", ""),
                "symbol":               t.get("symbol", ""),
                "layer":                t.get("layer", ""),
                "source":               t.get("source") or "",   # "band_enforcement" for D3 synthesis
                "flex_source":          t.get("flex_source") or "",
                "quantity":             int(t.get("quantity") or 0),
                "order_type":           t.get("order_type", ""),
                "limit_price":          t.get("limit_price"),
                "time_in_force":        t.get("time_in_force", ""),
                "rationale":            (t.get("rationale") or "")[:32000],
                "confidence":           float(t.get("confidence") or 0.0),
                "stop_loss":            t.get("stop_loss"),
                "take_profit":          t.get("take_profit"),
                # Phase C §7 reasoning capture (write-once, flex trades; core
                # trades emit nulls). Feeds the track_record learning aggregates.
                "primary_trigger":      t.get("primary_trigger") or "",
                "thesis_type":          t.get("thesis_type") or "",
                "trigger_evidence":     (t.get("trigger_evidence") or "")[:32000],
                "catalyst_date":        t.get("catalyst_date"),
                "quadrant_current":     quadrant_current,
                "quadrant_projected_6m": quadrant_projected_6m,
                "risk_score":           risk_score,
            }
            if is_flex_buy:
                # Conviction-sleeve entry metadata (write-once on the flex BUY).
                entity["entry_date"] = date_str
                entity["entry_price"] = _entry_price(t.get("symbol", ""))
                entity["entry_quadrant"] = entry_quadrant
                entity["flex_benchmark_etf"] = entry_bench_etf
            upsert_entity("TradeHistory", entity)
        except Exception as e:  # noqa: BLE001
            logger.error("TradeHistory upsert failed for %s: %s", trade_id, e)


def _write_override_history(date_str: str, decisions: list[dict], snapshot: dict | None = None) -> None:
    """Persist each override decision to OverrideHistory (Phase 4d — WRITE ONLY).

    One row per override record the model emitted, tagged with the validator's outcome
    (accepted / downsized / rejected) and the (possibly halved) magnitude actually applied.
    Phase 5 will later stamp the realized outcome at `falsifier_date`; this phase only writes
    the record + the hook fields (left null). Mirrors the TradeHistory key convention
    (PK=year-month, RowKey=stable per-override id). Non-fatal per row.
    """
    year_month = date_str[:7]
    for idx, dec in enumerate(decisions or []):
        ov = dec.get("override") or {}
        row_key = f"OV-{date_str.replace('-', '')}-{idx:03d}"
        try:
            evidence = ov.get("evidence") or []
            entity = {
                "PartitionKey":        year_month,
                "RowKey":              row_key,
                "recommended_at":      date_str,
                "outcome":             dec.get("outcome", ""),          # accepted|downsized|rejected|enforced
                "validator_reasons":   "; ".join(dec.get("reasons", []))[:32000],
                "sleeve":              (ov.get("sleeve") or "").upper(),  # per-sleeve (V1_1)
                "enforced":            bool(dec.get("enforced", False)),  # Finding 2 D3 stamp
                "premise_challenged":  ov.get("premise_challenged", ""),
                # `direction` is the EFFECTIVE (derived) direction the asymmetry bar
                # used (Task E1); `declared_direction` is the model's original claim
                # — both persisted so Phase C can measure the misclassification rate.
                "direction":           ov.get("direction", ""),
                "declared_direction":  ov.get("declared_direction", ""),
                "magnitude_pp":        ov.get("magnitude_pp"),
                "downsized":           bool(ov.get("_downsized", False)),
                "evidence":            (" | ".join(str(e) for e in evidence))[:32000],
                "evidence_count":      len(evidence),
                "falsifier":           (ov.get("falsifier") or "")[:32000],
                "falsifier_date":      ov.get("falsifier_date"),
                "clean_data_only":     bool(ov.get("clean_data_only", False)),
                "layer":               "override",
                # Phase-5 outcome hooks (stamped later; left null here).
                "outcome_status":      "",
                "resolved_correct":    None,
            }
            upsert_entity("OverrideHistory", entity)
        except Exception as e:  # noqa: BLE001
            logger.error("OverrideHistory upsert failed for %s: %s", row_key, e)


def _write_regime_suspect_history(date_str: str, snapshot: dict, trades_obj: dict) -> None:
    """FOLLOWUPS #12 Task C: one OverrideHistory row per SUSPECT favored bucket per
    report day — the dataset #13's monthly review needs ("what did we do when the
    market disagreed, and who was right"). Reuses the OverrideHistory write-once
    shape (mirrors `_write_override_history` / the collector's
    `_build_sleeve_switch_records`), tagged `layer: "regime_suspect"`.

    NOT YET GRADED by any stamper (verified, not rebuilt — per the task): the
    collector's `_stamp_override_outcomes` only selects rows carrying a
    `falsifier_date` and grades via `_grade_override`, which assumes an
    override-shaped `sleeve`/`direction` (de_risk/re_risk) pair — neither concept
    applies here. `_stamp_switch_outcomes` hardcodes an allow-list of `layer`
    values (`sleeve_switch`, `intl_leader_rotation`) that excludes
    `regime_suspect`, so these rows are simply never selected by either existing
    path (harmless — not mis-graded, just ungraded). A future stamper would need a
    THIRD grading function mirroring `_grade_switch`'s shape (forward return of the
    favored bucket vs SPY from `recommended_at`, "the market was right" if the
    excess stays negative through the horizon) rather than `_grade_override`'s.
    """
    qp = snapshot.get("quadrant_performance") or {}
    buckets = qp.get("buckets") or {}
    suspects = [q for q, b in buckets.items() if b.get("suspect")]
    if not suspects:
        return

    prices = snapshot.get("prices") or {}
    trades = trades_obj.get("trades") or []
    year_month = date_str[:7]

    for q in suspects:
        members = set(QUADRANT_CONCENTRATE.get(q, ()))
        delta_usd = 0.0
        for t in trades:
            sym = str(t.get("symbol") or t.get("ticker") or "").upper()
            if sym not in members:
                continue
            side = str(t.get("side") or t.get("action") or "").lower()
            try:
                qty = float(t.get("quantity") or t.get("qty") or 0)
                px = float((prices.get(sym) or {}).get("c"))
            except (TypeError, ValueError):
                continue
            if side == "buy":
                delta_usd += qty * px
            elif side == "sell":
                delta_usd -= qty * px
        action = "increased" if delta_usd > 1e-6 else (
            "reduced" if delta_usd < -1e-6 else "held")

        b = buckets[q]
        row_key = f"RS-{date_str.replace('-', '')}-{q}"
        try:
            entity = {
                "PartitionKey":       year_month,
                "RowKey":             row_key,
                "recommended_at":     date_str,
                "layer":              "regime_suspect",
                "sleeve":             q,
                "favored_streak":     b.get("favored_streak"),
                "streak_excess_pp":   b.get("streak_excess_pp"),
                "action":             action,
                # Grading hooks (left null — no stamper reads this layer yet).
                "outcome_status":     "",
                "resolved_correct":   None,
            }
            upsert_entity("OverrideHistory", entity)
        except Exception as e:  # noqa: BLE001
            logger.error("OverrideHistory (regime_suspect) upsert failed for %s: %s", row_key, e)


def _date_from_blob_name(blob_name: str) -> str | None:
    # Trigger gives full path like "daily-snapshots/2026-05-24.json" or just the file name
    base = blob_name.rsplit("/", 1)[-1]
    if base.endswith(".json"):
        return base[:-5]
    return None
