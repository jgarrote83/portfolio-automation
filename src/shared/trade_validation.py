"""Tier-1 trade validation (#28 — make "enforced downstream" true).

The analyzer prompt promises *"Bounds you cannot cross with an override (Tier-1,
enforced downstream)"* — but until this module, nothing downstream checked the TRADES:
reference construction enforces the bounds on the reference only, `validate_overrides`
checks override records, Finding 2's `reconcile` polices what the model FAILED to do
(silent-hold shortfalls), and the executor submits `daily-trades/{date}.json`
unfiltered. A hallucinated "BUY 500 QQQ" while the gate is closed, or a SELL through
the AMZN exemption or the 0.1% floor, flowed from LLM JSON to Alpaca untouched. #28
polices what the model DID.

Rules, per trade, in order (fields normalized exactly as the executor normalizes them):

V1 — Gate rule (absolute; overrides cannot cross): `deployment_gate` not "open" ⇒
    REJECT any BUY of an Amplifier name (Damper/SGOV buys pass) — EXCEPT an equal-weight
    within-role substitution (a buy of the role's new selected member funded by selling
    the role's old member at >= the buy notional) which is regime-neutral and passes.
    Additionally a BUY of any name off the CORE_ROSTER is rejected regardless of gate,
    and a BUY of any LEGACY_EXITS name is rejected regardless of gate ("legacy exit —
    core re-entry closed (flex only)") — `trades[]` is core-only and legacy names are
    wind-down-only; flex goes through `flex_nominations[]`.

V1.5 — Selected-member-only buys (role-based roster seam): a BUY of a name that
    belongs to a role's `pool` but is not that role's `selected` incumbent ⇒ REJECT
    ("non-selected pool member — selection changes go through sleeve-roles.json").
    Every other pool member reads as a normal CORE_ROSTER name to V1/V3, so without
    this check a BUY of e.g. SOXX (pool member, semis.selected = SMH) would pass
    straight through — a gap row at/near 0 lands inside the ± band window, and with
    NO gap row V3 used to skip the window entirely. The one exception is the
    `intl_leader` role, whose `selected` follows `intl_governance.leader_pick`
    automatically (Task F) — a buy of the CURRENT `leader_pick` (passed in via
    `quadrant_ctx["intl_leader_pick"]`) passes even before `sleeve-roles.json` is
    committed to match. A name off every role's pool (e.g. a LEGACY_EXITS name,
    already rejected above) is unaffected.

V2 — Exemption rule (absolute): SELL of an EXEMPT_HOLDS name ⇒ REJECT. Retired in the
    roster revision (EXEMPT_HOLDS is now empty, so this is a no-op) but kept so the
    machinery survives for any future designated hold.

V3 — Window rule (the core; D1's mirror image): a BUY with no matching gap row is
    REJECTED ("no reference row — cannot window the buy") rather than silently
    skipping the window below — a name the reference doesn't cover must not be
    buyable just because there is nothing to check it against (a SELL of a held name
    without a row keeps its prior behavior: V3/V4 skip, the trade still passes).
    Otherwise the post-trade weight must land inside
    `[max(reference − W, sleeve_floor), reference + W]` where
    `W = max(allowed_residual_for_sleeve, gap_band_pp)` and the residual comes from the
    SAME `validate_overrides()["decisions"]` that `reconcile` consumes (shared
    `allowed_residuals` helper). Trades that reduce the deviation always pass — a
    tranche-paced partial trim landing outside the window is legitimate (Finding 2 D2).
    Trades that would land beyond the window (overshoot past reference, or push an
    in-window sleeve out) are CLAMPED to the window edge; a trade starting outside the
    window and moving further out is REJECTED. Because the reference already encodes
    the floor, the ceiling, and the exemptions, this one check enforces them all; the
    explicit `sleeve_floor` lower bound covers the case where `reference − W` would
    dip below the floor (integer shares + a positive floor also leave ≥1 share on any
    clamped core sell).

    SGOV literal-cash carve-out: a SGOV BUY funded purely from PRE-TRADE literal cash
    is a pure cash-SLEEVE composition swap (cash sleeve = SGOV + literal cash) — it
    does not change the sleeve total, so it is EXEMPT from the per-name window. The
    exemption applies only while the buy is funded from pre-trade literal cash above
    the `literal_cash_target_pct` buffer (same-day sell proceeds are excluded, so it
    can never grow the sleeve); it is CLAMPED to that budget, not rejected. SGOV SELLs
    are windowed normally; all V4 mechanical checks still apply.

V4 — Mechanical sanity: sell qty ≤ held (clamped, mirroring the executor's held
    filter); buy notional ≤ cash available after the file's sells (clamped; the list
    is sells-first sorted before processing so proceeds are counted); integer shares
    (floored); zero-qty results are rejected. A CLAMPED trade below
    `min_notional_usd` is rejected (a dust remainder is not worth an order).

Aggregate assertion (cheap belt): post-ALL-trades Amplifier weight as a share of core
must not exceed `active_quadrant_ceiling_pct_of_core` — unreachable if V3 holds; if
ever hit it is logged at ERROR and the marginal amplifier buys are rejected from the
end of the list until the book is back under.

Every surviving trade carries ``"validation": {"status": "passed"|"clamped",
"reasons": [...]}``; rejected trades move to ``rejected[]`` (never submitted, still
visible in the daily-trades JSON). Synthesized `band_enforcement` trades must pass by
construction — a rejection there is a `reconcile` bug and is logged upstream.

PURE module (logging only, no I/O) — sibling of `reference_execution.reconcile`: same
gap rows, same config, same override decisions.
"""
from __future__ import annotations

import logging
import math

from shared.quadrants import (
    AMPLIFIER_INTL,
    AMPLIFIER_US,
    CORE_ROSTER,
    DAMPER,
    EXEMPT_HOLDS,
    LEGACY_EXITS,
    role_of,
    selected_for_role,
)
from shared.reference_execution import REFERENCE_EXECUTION_DEFAULTS, allowed_residuals

logger = logging.getLogger(__name__)

_AMPLIFIER = set(AMPLIFIER_US) | set(AMPLIFIER_INTL)
_DEFENSIVE = set(DAMPER) | {"SGOV"}
_EPS_PP = 0.05


def _norm(trade: dict) -> tuple[str, str, float | None]:
    """Normalize exactly as the executor does: symbol|ticker upper, side|action
    lower, quantity|qty numeric (None when non-numeric)."""
    sym = str(trade.get("symbol") or trade.get("ticker") or "").upper()
    side = str(trade.get("side") or trade.get("action") or "").lower()
    try:
        qty = float(trade.get("quantity") or trade.get("qty") or 0)
    except (TypeError, ValueError):
        qty = None
    return sym, side, qty


def validate_trades(
    gaps: list[dict],
    trades: list[dict],
    override_decisions: list[dict],
    cfg: dict,
    quadrant_ctx: dict,
) -> dict:
    """Validate the final trades list against the Tier-1 bounds (V1–V4 above).

    Args mirror ``reconcile``; ``gaps`` rows additionally carry ``held_qty``.
    ``quadrant_ctx`` may carry ``intl_leader_pick`` (the current
    ``intl_governance.leader_pick``) for the V1.5 intl_leader auto-rotation exception.
    Returns ``{"trades": [stamped, possibly clamped, sells-first], "rejected":
    [records with reasons], "summary": {"passed", "clamped", "rejected"}}``.
    With no ``gaps``/equity (reference or account unavailable) the weight-based rules
    (V3/V4, including the no-gap-row buy rejection) are skipped but the absolute rules
    (V1 gate/roster, V1.5 selected-member, V2 exemption, integer shares) still apply —
    trades are always stamped.
    """
    ov_cfg = (cfg or {}).get("override_protocol") or {}
    rex_cfg = {**REFERENCE_EXECUTION_DEFAULTS, **((cfg or {}).get("reference_execution") or {})}
    band = float(ov_cfg.get("gap_band_pp", 5.0))
    max_mag = float(ov_cfg.get("max_magnitude_pp", 15.0))
    min_notional = float(rex_cfg["min_notional_usd"])
    floor_pct = float((cfg or {}).get("sleeve_floor_pct_of_core", 0.1))
    ceiling = float((cfg or {}).get("active_quadrant_ceiling_pct_of_core", 90.0))

    ctx = quadrant_ctx or {}
    equity = float(ctx.get("equity_usd") or 0)
    gate = str(ctx.get("deployment_gate") or "").lower()
    exempt = {str(t).upper() for t in (ctx.get("exempt_holds") or EXEMPT_HOLDS)}
    intl_leader_pick = str(ctx.get("intl_leader_pick") or "").upper() or None
    # Task 2: the literal-cash buffer (SGOV carve-out) — reference_weights.
    # literal_cash_target_pct, defaulting to the 1.5% cash buffer if absent.
    literal_cash_buffer_pct = float(ctx.get("literal_cash_target_pct") or 1.5)

    rows = {str(g.get("symbol") or "").upper(): g for g in (gaps or []) if g.get("symbol")}
    residual = allowed_residuals(override_decisions, max_mag)

    # Running state: sleeve weights, held shares, cash after processed trades.
    cur_pct: dict[str, float] = {}
    held: dict[str, float] = {}
    for sym, row in rows.items():
        cur_pct[sym] = float(row.get("current_pct") or 0)
        try:
            held[sym] = float(row.get("held_qty") or 0)
        except (TypeError, ValueError):
            held[sym] = 0.0
    cash_avail = max(0.0, float(ctx.get("cash_usd") or 0))
    have_account = equity > 0 and bool(rows)
    # Task 2: a SEPARATE pre-trade literal-cash tracker for the SGOV carve-out. It
    # starts at pre-trade literal cash and is decremented ONLY by exempted SGOV buys
    # — deliberately never fed by same-day sell proceeds, so a literal-cash → SGOV
    # swap can never become a backdoor to grow the cash sleeve past what pre-trade
    # cash (above the buffer) supports.
    pre_cash = cash_avail
    literal_cash_buffer_usd = literal_cash_buffer_pct / 100.0 * equity

    # Sells first (stable) so sell proceeds fund the buys we validate after them.
    ordered = sorted(trades or [], key=lambda t: _norm(t)[1] != "sell")

    # Task D — equal-weight within-role substitution budget: sell notional per role
    # (from sells of that role's pool members) available to fund a gate-closed buy of
    # the role's new selected member. A within-role sell-old/buy-new at <= the old
    # dollar weight is regime-neutral (no net-new Q1/Q2 beta), so it is exempt from the
    # closed-gate amplifier-buy block. Legacy-exit sells are NOT in any role, so they
    # never fund a substitution.
    role_sub_budget: dict[str, float] = {}
    for t in trades or []:
        s, sd, q = _norm(t)
        if sd == "sell" and q and q > 0:
            r = role_of(s)
            spx = float((rows.get(s) or {}).get("price") or 0)
            if r and spx > 0:
                role_sub_budget[r] = role_sub_budget.get(r, 0.0) + q * spx

    passed: list[dict] = []
    rejected: list[dict] = []

    def _reject(trade: dict, reasons: list[str]) -> None:
        rejected.append({
            **trade,
            "validation": {"status": "rejected", "reasons": reasons},
        })

    for t in ordered:
        sym, side, qty = _norm(t)
        reasons: list[str] = []

        # --- structural -------------------------------------------------------
        if not sym or side not in ("buy", "sell"):
            _reject(t, [f"invalid trade payload (symbol={sym!r}, side={side!r})"])
            continue
        if qty is None or qty <= 0:
            _reject(t, [f"invalid quantity {t.get('quantity')!r}"])
            continue
        if qty != int(qty):
            qty = float(math.floor(qty))
            reasons.append("fractional quantity floored to integer (Phase 1 is integer-shares)")
            if qty < 1:
                _reject(t, reasons + ["quantity floored to zero"])
                continue

        # --- V1: gate + roster (absolute) --------------------------------------
        if side == "buy":
            if sym in LEGACY_EXITS:
                _reject(t, reasons + [
                    "legacy exit — core re-entry closed (flex only)"
                ])
                continue
            if sym not in CORE_ROSTER:
                _reject(t, reasons + [
                    f"off-roster buy {sym} forbidden in trades[] (core-only; flex goes "
                    "through flex_nominations)"
                ])
                continue
            if gate != "open" and sym in _AMPLIFIER:
                # Equal-weight within-role substitution carve-out (Task D): a buy of the
                # role's new selected member funded by selling the old member of the SAME
                # role at >= this buy's notional is regime-neutral, not net-new beta.
                rrole = role_of(sym)
                bpx = float((rows.get(sym) or {}).get("price") or 0)
                bnotional = (qty or 0) * bpx
                if (rrole and bpx > 0 and bnotional > 0
                        and role_sub_budget.get(rrole, 0.0) + 1e-6 >= bnotional):
                    role_sub_budget[rrole] -= bnotional
                    reasons.append(
                        f"gate {gate or 'unknown'} — allowed as an equal-weight within-role "
                        f"substitution (funded by a same-role sell; no net-new beta)"
                    )
                else:
                    _reject(t, reasons + [
                        f"deployment gate {gate or 'unknown'} — amplifier buy {sym} forbidden "
                        "(Tier-1; an override cannot loosen the gate)"
                    ])
                    continue

        # --- V1.5: selected-member-only buys (role-based roster seam) ----------
        if side == "buy":
            brole = role_of(sym)
            if brole is not None:
                sel = selected_for_role(brole)
                if sym != sel and not (brole == "intl_leader"
                                        and intl_leader_pick and sym == intl_leader_pick):
                    _reject(t, reasons + [
                        f"non-selected pool member of role {brole!r} (selected={sel!r}) — "
                        "selection changes go through sleeve-roles.json"
                    ])
                    continue

        # --- V2: exemption (absolute) ------------------------------------------
        if side == "sell" and sym in exempt:
            _reject(t, reasons + [
                f"exempt hold {sym} — never sold below current weight (Tier-1; core "
                "stop_loss/take_profit are null per Phase B, so no exit path exists here)"
            ])
            continue

        row = rows.get(sym)
        if side == "buy" and have_account and row is None:
            _reject(t, reasons + [
                "no reference row — cannot window the buy"
            ])
            continue
        try:
            px = float((row or {}).get("price") or 0)
        except (TypeError, ValueError):
            px = 0.0

        # --- V3: window rule (needs account + a reference row + a price) --------
        if have_account and row is not None and px > 0:
            ref = float(row.get("reference_pct") or 0)
            w = max(residual.get(sym, 0.0), band)
            # Legacy exits bypass the floor lower bound so an exit sell can reach 0
            # (Task D — "sell-to-zero allowed ONLY for LEGACY_EXITS"). Every other core
            # name keeps the sleeve floor.
            floor_lb = 0.0 if sym in LEGACY_EXITS else (
                floor_pct if sym in CORE_ROSTER else 0.0)
            lo = max(ref - w, floor_lb, 0.0)
            hi = ref + w
            cur = cur_pct.get(sym, 0.0)
            delta_pp = qty * px / equity * 100.0
            post = cur - delta_pp if side == "sell" else cur + delta_pp

            if side == "sell":
                if cur < lo - _EPS_PP:
                    _reject(t, reasons + [
                        f"sell of already-underweight sleeve ({cur:.2f}% < window floor "
                        f"{lo:.2f}% = max(ref {ref:.2f} − {w:.1f}, floor)) — moves further "
                        "from reference beyond the sheltered window"
                    ])
                    continue
                if post < lo - _EPS_PP:
                    # +1e-6: float noise must never cost a whole share on the floor()
                    new_qty = math.floor((cur - lo) / 100.0 * equity / px + 1e-6)
                    reasons.append(
                        f"sell clamped {int(qty)}→{new_qty}: landing {post:.2f}% would "
                        f"breach the window floor {lo:.2f}% (ref {ref:.2f} ± {w:.1f}, "
                        "floor-protected)"
                    )
                    qty = float(new_qty)
            else:
                # Task 2 carve-out: a literal-cash → SGOV conversion is a pure cash-
                # SLEEVE composition swap (cash sleeve = SGOV + literal cash), so it
                # does not change the sleeve TOTAL and must NOT be windowed against
                # SGOV's per-name reference. Exempt the SGOV buy from the window when
                # it is funded purely from PRE-TRADE literal cash (never same-day sell
                # proceeds) and only down to the literal-cash buffer; clamp to that
                # budget rather than reject. Below-budget (e.g. only sell proceeds
                # available) → no exemption, falls through to the normal window.
                # (2026-07-09: SGOV 28.44% vs window ceiling 28.50% rejected a
                # $4k cash→SGOV swap, leaving ~5% of equity idle in literal cash.)
                sgov_exempt = False
                if sym == "SGOV":
                    budget = max(0.0, pre_cash - literal_cash_buffer_usd)
                    max_shares = math.floor(budget / px)
                    if max_shares >= 1:
                        sgov_exempt = True
                        if qty > max_shares:
                            reasons.append(
                                f"SGOV buy clamped {int(qty)}→{max_shares}: funded only "
                                f"from pre-trade literal cash above the "
                                f"{literal_cash_buffer_pct:.1f}% buffer (${budget:,.0f} "
                                "available; same-day sell proceeds excluded)"
                            )
                            qty = float(max_shares)
                        pre_cash -= qty * px
                if not sgov_exempt:
                    if cur > hi + _EPS_PP:
                        _reject(t, reasons + [
                            f"buy of already-overweight sleeve ({cur:.2f}% > window ceiling "
                            f"{hi:.2f}% = ref {ref:.2f} + {w:.1f}) — moves further from "
                            "reference beyond the sheltered window"
                        ])
                        continue
                    if post > hi + _EPS_PP:
                        new_qty = math.floor((hi - cur) / 100.0 * equity / px + 1e-6)
                        reasons.append(
                            f"buy clamped {int(qty)}→{new_qty}: landing {post:.2f}% would "
                            f"exceed the window ceiling {hi:.2f}% (ref {ref:.2f} + {w:.1f})"
                        )
                        qty = float(new_qty)

        # --- V4: mechanical sanity ----------------------------------------------
        if have_account and side == "sell" and row is not None:
            h = held.get(sym, 0.0)
            if h <= 0:
                _reject(t, reasons + ["not held — nothing to sell"])
                continue
            if qty > h:
                reasons.append(f"sell clamped {int(qty)}→{int(h)}: exceeds held quantity")
                qty = float(math.floor(h))
        if have_account and side == "buy" and px > 0:
            notional = qty * px
            if notional > cash_avail + 1e-6:
                new_qty = math.floor(cash_avail / px)
                reasons.append(
                    f"buy clamped {int(qty)}→{new_qty}: notional ${notional:,.0f} exceeds "
                    f"cash available after sells ${cash_avail:,.0f}"
                )
                qty = float(new_qty)

        was_clamped = bool(reasons)
        if qty < 1:
            _reject(t, reasons + ["clamped to zero — nothing submittable"])
            continue
        if was_clamped and px > 0 and qty * px < min_notional:
            _reject(t, reasons + [
                f"clamped remainder ${qty * px:,.0f} below ${min_notional:.0f} min notional"
            ])
            continue

        # --- commit running state + stamp ---------------------------------------
        if px > 0 and equity > 0:
            delta_pp = qty * px / equity * 100.0
            if side == "sell":
                cur_pct[sym] = cur_pct.get(sym, 0.0) - delta_pp
                held[sym] = held.get(sym, 0.0) - qty
                cash_avail += qty * px
            else:
                cur_pct[sym] = cur_pct.get(sym, 0.0) + delta_pp
                cash_avail = max(0.0, cash_avail - qty * px)

        out = {**t, "quantity": int(qty)}
        out["validation"] = {
            "status": "clamped" if was_clamped else "passed",
            "reasons": reasons,
        }
        passed.append(out)

    # --- aggregate assertion (unreachable if V3 holds) ---------------------------
    # Rejects only buys that made things WORSE: the threshold is max(ceiling,
    # pre-trade amplifier share), so a book already concentrated (or a partial gaps
    # universe in a fixture) is a state to log, not a trade violation to punish.
    if have_account:
        pre_core = sum(float(r.get("current_pct") or 0) for s, r in rows.items()
                       if s in CORE_ROSTER)
        pre_amp = sum(float(r.get("current_pct") or 0) for s, r in rows.items()
                      if s in _AMPLIFIER)
        pre_share = pre_amp / pre_core * 100.0 if pre_core > 0 else 0.0
        threshold = max(ceiling, pre_share)
        core_total = sum(v for s, v in cur_pct.items() if s in CORE_ROSTER)
        amp_total = sum(v for s, v in cur_pct.items() if s in _AMPLIFIER)
        if core_total > 0 and amp_total / core_total * 100.0 > threshold + _EPS_PP:
            logger.error(
                "Tier-1 aggregate assertion HIT (should be unreachable if V3 holds): "
                "post-trade amplifier %.1f%% of core > %.1f%% (max of ceiling %.1f%% / "
                "pre-trade %.1f%%) — rejecting marginal amplifier buys",
                amp_total / core_total * 100.0, threshold, ceiling, pre_share,
            )
            for i in range(len(passed) - 1, -1, -1):
                sym, side, _ = _norm(passed[i])
                if side == "buy" and sym in _AMPLIFIER:
                    victim = passed.pop(i)
                    delta_pp = victim["quantity"] * float(rows.get(sym, {}).get("price") or 0) \
                        / equity * 100.0
                    cur_pct[sym] = cur_pct.get(sym, 0.0) - delta_pp
                    amp_total -= delta_pp
                    core_total -= delta_pp
                    _reject(victim, ["aggregate active-quadrant ceiling assertion — "
                                     "marginal amplifier buy rejected"])
                    if core_total <= 0 or amp_total / core_total * 100.0 <= threshold + _EPS_PP:
                        break

    clamped_count = sum(1 for p in passed if p["validation"]["status"] == "clamped")
    return {
        "trades": passed,
        "rejected": rejected,
        "summary": {
            "passed": len(passed) - clamped_count,
            "clamped": clamped_count,
            "rejected": len(rejected),
        },
    }
