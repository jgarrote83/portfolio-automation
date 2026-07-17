"""Reference-execution reconciliation (Finding 2 — kills the silent-hold gap).

Phase 4 made inaction *accountable* but not *enforceable*: a hold of an out-of-band
sleeve requires an override; an override larger than ``max_magnitude_pp`` is
structurally rejected; a rejected override authorizes nothing — but nothing then forced
a trade. For any gap > the band the protocol was unenforceable: the model could
silent-hold and the system merely flagged it (the 2026-06-30 zero-trades pathology; the
2026-07-02/03 ~30pp GLD/TLT gaps traded only because the model chose to).

Three locked decisions (session 2026-07-03 — this module is the decision record):

D1 — an override caps the RESIDUAL, not the move. Per out-of-band sleeve::

        allowed_residual    = |magnitude_pp| of the accepted/downsized override FOR
                              THAT SLEEVE (0 if none or rejected), never > max_magnitude_pp
        required_move_total = max(0, |gap| - max(allowed_residual, gap_band_pp))

    A hold-override shelters at most ``max_magnitude_pp`` of a gap — the remainder MUST
    trade. Overrides are therefore per-sleeve (mandatory ``sleeve`` field,
    OVERRIDE_SCHEMA_V1_1; see shared/overrides.py).

D2 — tranche formalization: ``required_move_today = min(required_move_total,
    tranche_pp_max)``. A trade moving >= required_move_today toward reference is
    CONFIRMING — progress at tranche pace is first-class, not underdelivery, and the
    residual gap needs no override while tranche pace is kept.

D3 — deterministic enforcement with DE-RISK-ONLY synthesis (spec §6 asymmetry): where
    the model's trades fall short of required_move_today AND the corrective trade is a
    de-risk move — selling an overweight AMPLIFIER name, or buying an underweight
    DAMPER/SGOV name (classification reuses the shared/quadrants.py block model) — the
    shortfall is synthesized as a ``source: "band_enforcement"`` trade appended to the
    model's own trades[] list. Everything else (selling overweight dampers, any risk-on
    buy) is re-risk: NEVER synthesized, only ``non_compliant_flagged`` — quick to reduce
    risk deterministically, deliberate (human / next session) to add it.

Synthesized trades respect: integer shares (floored), a min-notional skip, sells before
buys (overweight sleeves are processed first so sell proceeds fund the buys), buys
capped by cash available after sells, the deployment gate (buy synthesis is
damper/SGOV-only by construction, and checked), EXEMPT_HOLDS (never force-sold), and a
per-session enforcement turnover cap. Tier-1 floors/ceiling hold by construction:
enforcement only ever moves a sleeve TOWARD its reference — which already encodes the
floor, ceiling, and exemptions — and never past it.

PURE module — no I/O. The analyzer builds the inputs (gaps from the snapshot, the
validator's decisions) and applies the outputs (merged trades, OverrideHistory stamps).
"""
from __future__ import annotations

import math

from shared.quadrants import AMPLIFIER_INTL, AMPLIFIER_US, DAMPER, LEGACY_EXITS

# Fallback config if risk-limits.json lacks a reference_execution block (mirror it).
REFERENCE_EXECUTION_DEFAULTS = {
    "tranche_pp_max": 10.0,
    "enforce": True,
    "enforcement_turnover_max_pct": 20.0,
    "min_notional_usd": 115.0,
}

_EPS_PP = 0.05   # sub-0.05pp residue is rounding noise, never a shortfall
_DEFENSIVE = set(DAMPER) | {"SGOV"}


def is_de_risk_move(side: str, symbol: str) -> bool:
    """The D3 classification, deterministic off the quadrants.py block model:
    SELLING an Amplifier or LEGACY_EXITS name, or BUYING a Damper/SGOV name, is
    de-risk; everything else (selling dampers, any risk-on buy) is re-risk and is
    never synthesized.

    LEGACY_EXITS sells were added session 2026-07-15 (Task D1, decision D0): a
    legacy long (AMZN/GOOGL/MCK/...) is being wound down to a 0% reference target
    by design — reducing it is unambiguously de-risk (less concentration, more
    cash/ballast), the same as trimming an overweight amplifier. Before this, a
    legacy sell shortfall was flagged `non_compliant_flagged` ("re-risk shortfall
    — never synthesized") and left the book's largest overweight (MCK, 07-14/15)
    unpoliced — the model traded 1.65pp of a 6.56pp required tranche on 07-14 and
    0.82pp of 4.79pp on 07-15, with no backstop and no override filed either time."""
    s = (symbol or "").upper()
    if (side or "").lower() == "sell":
        return s in AMPLIFIER_US or s in AMPLIFIER_INTL or s in LEGACY_EXITS
    return s in _DEFENSIVE


def derive_override_direction(sleeve: str, gap_signed: float | None) -> str | None:
    """Deterministic override direction (session 2026-07-15, Task E1) — shares the
    block model with `is_de_risk_move` so an override's direction can never
    disagree with what enforcement itself would call de-risk vs re-risk for the
    same sleeve.

    ``gap_signed`` = current_pct − reference_pct for the sleeve (positive =
    overweight, negative = underweight; the sign convention `reconcile` uses).

    - Damper/SGOV sleeve **overweight** its reference (holding MORE defense than
      the reference wants) ⇒ ``de_risk``; **underweight** (LESS defense) ⇒
      ``re_risk``.
    - Amplifier or LEGACY_EXITS sleeve **overweight** (holding MORE risk-on, or
      slow-walking a legacy exit above its 0% target) ⇒ ``re_risk``;
      **underweight** (LESS) ⇒ ``de_risk``.

    Returns ``None`` when the sleeve's block can't be classified (an unknown/
    off-roster ticker) or the gap is exactly zero (no deviation to direction) —
    callers must not silently default a direction in that case, only fall back
    to whatever was declared.

    *(Motivating case: 2026-07-14 correctly filed a GLD-above-reference hold as
    de_risk; 2026-07-15 filed the identical situation — plus XLP and TLT, also
    dampers held above reference — as re_risk, backwards, which would have held
    them to a HARDER evidence bar than the cheap de-risk case actually requires.)*
    """
    if gap_signed is None or gap_signed == 0:
        return None
    s = (sleeve or "").upper()
    if s in _DEFENSIVE:
        return "de_risk" if gap_signed > 0 else "re_risk"
    if s in AMPLIFIER_US or s in AMPLIFIER_INTL or s in LEGACY_EXITS:
        return "re_risk" if gap_signed > 0 else "de_risk"
    return None


def allowed_residuals(override_decisions: list[dict], max_magnitude_pp: float) -> dict[str, float]:
    """D1 — the per-sleeve residual an override may shelter: |magnitude_pp| of the
    ACCEPTED/DOWNSIZED record for that sleeve, capped at ``max_magnitude_pp``; a
    rejected or absent record shelters nothing. Shared by ``reconcile`` (shortfall
    enforcement) and ``trade_validation.validate_trades`` (the V3 window rule) so
    the two layers can never disagree on what an override authorizes."""
    residual: dict[str, float] = {}
    for dec in override_decisions or []:
        if dec.get("outcome") not in ("accepted", "downsized"):
            continue
        ov = dec.get("override") or {}
        sleeve = str(ov.get("sleeve") or "").upper()
        try:
            mag = abs(float(ov.get("magnitude_pp")))
        except (TypeError, ValueError):
            continue
        if sleeve:
            residual[sleeve] = min(max(residual.get(sleeve, 0.0), mag), float(max_magnitude_pp))
    return residual


def _flag(entry: dict, reason: str) -> None:
    entry["status"] = "non_compliant_flagged"
    entry["reasons"].append(reason)


def reconcile(
    gaps: list[dict],
    trades: list[dict],
    override_decisions: list[dict],
    cfg: dict,
    quadrant_ctx: dict,
) -> dict:
    """Reconcile the model's trades against the reference gaps (D1+D2+D3).

    Args:
        gaps: per-sleeve rows ``{"symbol", "current_pct", "reference_pct", "price"}``
            (percent of equity; price = today's close for share sizing).
        trades: the model's ``trades[]`` (core, pre-merge).
        override_decisions: ``validate_overrides()["decisions"]`` (per-sleeve, V1_1).
        cfg: ``{"override_protocol": {...}, "reference_execution": {...}}``.
        quadrant_ctx: ``{"deployment_gate", "equity_usd", "cash_usd", "date",
            "exempt_holds"}``.

    Returns ``{"sleeves": {sym: {status, gap_pp, allowed_residual_pp,
    required_move_total_pp, required_move_today_pp, model_move_pp, reasons,
    enforced_trade?}}, "enforced_trades": [...], "summary": {...},
    "enforcement_notional_usd": float}`` where status is one of
    ``confirming | override_covered | enforced | non_compliant_flagged``.
    Sleeves within ``gap_band_pp`` of reference are not reported.
    """
    ov_cfg = (cfg or {}).get("override_protocol") or {}
    rex_cfg = {**REFERENCE_EXECUTION_DEFAULTS, **((cfg or {}).get("reference_execution") or {})}
    band = float(ov_cfg.get("gap_band_pp", 5.0))
    max_mag = float(ov_cfg.get("max_magnitude_pp", 15.0))
    tranche = float(rex_cfg["tranche_pp_max"])
    enforce = bool(rex_cfg["enforce"])
    min_notional = float(rex_cfg["min_notional_usd"])

    ctx = quadrant_ctx or {}
    equity = float(ctx.get("equity_usd") or 0)
    gate = str(ctx.get("deployment_gate") or "").lower()
    exempt = {str(t).upper() for t in ctx.get("exempt_holds") or ()}
    date_tag = str(ctx.get("date") or "").replace("-", "")

    sleeves: dict[str, dict] = {}
    enforced: list[dict] = []
    summary = {"confirming": 0, "override_covered": 0, "enforced": 0,
               "non_compliant_flagged": 0}
    if equity <= 0 or not gaps:
        return {"sleeves": sleeves, "enforced_trades": enforced, "summary": summary,
                "enforcement_notional_usd": 0.0}

    # Off-roster held names (flex leftovers, e.g. MU) get a gap row so the Tier-1
    # validator can clamp their sells, but band enforcement must NEVER synthesize a
    # trade for one — flex exits are governed by the flex engine and human approval,
    # not the deterministic reference (2026-07-13 audit finding 3). `all_rows`
    # (session 2026-07-15, Task B2) keeps them for PRICING ONLY — an off-roster
    # sell still raises real cash that enforcement's buy sizing must see (07-14: MU's
    # ~$1,967 sell proceeds were excluded from `cash_avail`, understating what was
    # available and contributing to a cash-starved KMLM synthesis). `rows` (excluding
    # off_roster) remains the synthesis working set below — off-roster names must
    # never become an enforcement TARGET, only a cash SOURCE.
    all_rows = {
        str(g.get("symbol") or "").upper(): g for g in gaps if g.get("symbol")
    }
    rows = {sym: g for sym, g in all_rows.items() if not g.get("off_roster")}

    # D1 — per-sleeve allowed residual (shared helper — rejected/absent shelters nothing).
    residual = allowed_residuals(override_decisions, max_mag)

    # Model's net pp move TOWARD reference per sleeve (moves away count negative),
    # plus sell/buy notionals for the cash-after-sells constraint on synthesized buys.
    move_pp: dict[str, float] = {}
    sell_notional = buy_notional = 0.0
    for t in trades or []:
        sym = str(t.get("symbol") or "").upper()
        side = str(t.get("side") or "").lower()
        try:
            qty = abs(float(t.get("quantity") or 0))
        except (TypeError, ValueError):
            qty = 0.0
        row = all_rows.get(sym)
        try:
            px = float((row or {}).get("price") or 0)
        except (TypeError, ValueError):
            px = 0.0
        if side not in ("buy", "sell") or qty <= 0 or px <= 0:
            continue
        notional = qty * px
        if side == "sell":
            sell_notional += notional
        else:
            buy_notional += notional
        gap_signed = float(row.get("current_pct") or 0) - float(row.get("reference_pct") or 0)
        toward = "sell" if gap_signed > 0 else "buy"
        pp = notional / equity * 100.0
        move_pp[sym] = move_pp.get(sym, 0.0) + (pp if side == toward else -pp)

    cash_avail = max(0.0, float(ctx.get("cash_usd") or 0) + sell_notional - buy_notional)
    turnover_budget = float(rex_cfg["enforcement_turnover_max_pct"]) / 100.0 * equity

    # Out-of-band sleeves only; overweights (sells) first so proceeds fund the buys,
    # largest gap first within each side — the resulting enforced list is
    # sells-before-buys by construction.
    out_of_band = []
    for sym, row in rows.items():
        gap_signed = float(row.get("current_pct") or 0) - float(row.get("reference_pct") or 0)
        if abs(gap_signed) > band + _EPS_PP:
            out_of_band.append((sym, gap_signed, row.get("price")))
    out_of_band.sort(key=lambda r: (r[1] < 0, -abs(r[1])))

    seq = 0
    total_enf_notional = 0.0
    for sym, gap_signed, px in out_of_band:
        abs_gap = abs(gap_signed)
        allowed = residual.get(sym, 0.0)
        required_total = max(0.0, abs_gap - max(allowed, band))
        required_today = min(required_total, tranche)
        net_move = move_pp.get(sym, 0.0)
        entry = {
            "status": "",
            "gap_pp": round(gap_signed, 2),
            "allowed_residual_pp": round(allowed, 2),
            "required_move_total_pp": round(required_total, 2),
            "required_move_today_pp": round(required_today, 2),
            "model_move_pp": round(net_move, 2),
            "reasons": [],
        }
        sleeves[sym] = entry
        if net_move < -_EPS_PP:
            entry["reasons"].append("model traded AWAY from reference on this sleeve")

        if required_total <= _EPS_PP:
            entry["status"] = "override_covered"
            continue
        if net_move + _EPS_PP >= required_today:
            entry["status"] = "confirming"
            continue

        # D3 — shortfall. Synthesize only de-risk; flag everything else.
        shortfall_pp = required_today - max(net_move, 0.0)
        side = "sell" if gap_signed > 0 else "buy"
        if not enforce:
            _flag(entry, f"{shortfall_pp:.1f}pp shortfall — enforcement disabled by config")
            continue
        if side == "sell" and sym in exempt:
            _flag(entry, "exempt hold — never force-sold (Tier-1)")
            continue
        if not is_de_risk_move(side, sym):
            _flag(entry, (
                f"{shortfall_pp:.1f}pp re-risk shortfall — never synthesized (spec §6 "
                "asymmetry); requires an honest override or next-session action"
            ))
            continue
        if gate == "closed" and side == "buy" and sym not in _DEFENSIVE:
            _flag(entry, "deployment gate closed — only defensive buys may be synthesized")
            continue
        try:
            price = float(px or 0)
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            _flag(entry, "no usable price — cannot size enforcement trade")
            continue

        shares = math.floor(shortfall_pp / 100.0 * equity / price)
        shares = min(shares, math.floor(turnover_budget / price))
        if side == "buy":
            shares = min(shares, math.floor(cash_avail / price))
        notional = shares * price
        if shares < 1 or notional < min_notional:
            _flag(entry, (
                f"{shortfall_pp:.1f}pp shortfall not enforceable at current size "
                f"(shares={max(shares, 0)}, notional=${max(notional, 0):.0f} — below "
                f"${min_notional:.0f} min notional, or turnover/cash cap exhausted)"
            ))
            continue

        seq += 1
        trade = {
            "id": f"T-{date_tag}-E{seq:02d}",
            "side": side,
            "symbol": sym,
            "layer": "core",
            "flex_source": None,
            "quantity": int(shares),
            "order_type": "market",
            "limit_price": None,
            "time_in_force": "day",
            "rationale": (
                f"Band enforcement (Finding 2 D3): {sym} sits {gap_signed:+.1f}pp vs "
                f"reference; override shelter {allowed:.1f}pp; model trades covered "
                f"{max(net_move, 0.0):.1f}pp of the required {required_today:.1f}pp "
                "tranche — de-risk shortfall synthesized deterministically."
            ),
            "confidence": 1.0,
            "stop_loss": None,
            "take_profit": None,
            "primary_trigger": None,
            "thesis_type": None,
            "trigger_evidence": None,
            "catalyst_date": None,
            "source": "band_enforcement",
        }
        enforced.append(trade)
        entry["status"] = "enforced"
        entry["enforced_trade"] = trade
        turnover_budget -= notional
        total_enf_notional += notional
        if side == "sell":
            cash_avail += notional
        else:
            cash_avail -= notional

    for e in sleeves.values():
        summary[e["status"]] += 1
    return {
        "sleeves": sleeves,
        "enforced_trades": enforced,
        "summary": summary,
        "enforcement_notional_usd": round(total_enf_notional, 2),
    }
