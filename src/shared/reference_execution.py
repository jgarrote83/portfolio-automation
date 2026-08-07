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

D-B1 (session 2026-08-01, decided with the account holder — Option 1 of two):
`move_pp`'s SGOV entry now excludes the qualifying literal-cash-carve-out portion of
a SGOV buy (the same sanctioned sweep `shared/trade_validation.py` exempts from the
per-name window) from counting as the model having "traded AWAY" from reference.
Before this, every carve-out sweep scored strongly negative on `model_move_pp`
(observed 07-31: gap +17.74pp, required 2.74pp, model_move_pp -7.01pp for a 69-share
SGOV sweep that was, by construction, a pure cash-sleeve composition swap) and
tripped the "model traded AWAY from reference" reason — a false alarm on the STEADY
STATE the carve-out exists to enable, firing on every future sweep while SGOV sits
above reference+band. Option 2 (measuring the SGOV row at cash-sleeve level, SGOV +
literal cash vs a sleeve-level target) was considered and rejected as more invasive —
it would change the gap semantics the prompt/gap-table already documents per-name.
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


def effective_execution_config(cfg: dict) -> dict:
    """The execution-config numbers exactly as `reconcile()` resolves them, in one
    place (session 2026-07-17, Task B — #33(i) graduation). Four consecutive report
    sessions guessed these values from the prose (e.g. "tranche_pp_max = assumed
    5pp" against a true 10.0, "gap_band_pp = 1.0pp assumed" against a true 5.0) —
    the 07-17 band guess alone filed three unnecessary in-band overrides (GLD/XLP/
    TLT, all inside the real 5pp band). The collector echoes this dict verbatim
    into the snapshot's `execution_config` block so the prompt never has to guess;
    `reconcile` and `validate_trades` keep resolving their own copies from `cfg`
    (unchanged) — this is a read-only mirror, never a new input to enforcement.

    Accepts the same ``cfg`` shape ``reconcile``/``validate_trades`` do (top-level
    ``override_protocol`` / ``reference_execution`` / ``sleeve_floor_pct_of_core``
    blocks — i.e. `risk-limits.json` itself, or the analyzer's
    `_load_reference_execution_cfg()` output).
    """
    ov_cfg = (cfg or {}).get("override_protocol") or {}
    rex_cfg = {**REFERENCE_EXECUTION_DEFAULTS, **((cfg or {}).get("reference_execution") or {})}
    return {
        "gap_band_pp": float(ov_cfg.get("gap_band_pp", 5.0)),
        # O4 (2026-08-06 audit): the HYBRID band multiplier — a sleeve's
        # effective shelter is min(gap_band_pp, relative_band_frac *
        # reference_pct) whenever reference_pct > 0 (see reconcile /
        # _effective_band). Surfaced here so the prompt can quote it verbatim
        # rather than assuming every sleeve is governed by the flat gap_band_pp.
        "relative_band_frac": float(ov_cfg.get("relative_band_frac", 0.5)),
        "max_magnitude_pp": float(ov_cfg.get("max_magnitude_pp", 15.0)),
        # Structural gate (shared/overrides.py): BOTH directions require >=1 clean
        # evidence item or the record is rejected outright; re-risk additionally
        # needs >= re_risk_min_evidence or it is downsized. de_risk_min_evidence is
        # not itself a config key — it is the fixed structural floor, surfaced here
        # so the prompt can quote both bars without guessing either.
        "de_risk_min_evidence": 1,
        "re_risk_min_evidence": int(ov_cfg.get("re_risk_min_evidence", 2)),
        "tranche_pp_max": float(rex_cfg["tranche_pp_max"]),
        "enforce": bool(rex_cfg["enforce"]),
        "enforcement_turnover_max_pct": float(rex_cfg["enforcement_turnover_max_pct"]),
        "min_notional_usd": float(rex_cfg["min_notional_usd"]),
        "sleeve_floor_pct_of_core": float((cfg or {}).get("sleeve_floor_pct_of_core", 0.1)),
    }


def _effective_band(band: float, relative_band_frac: float, reference_pct: float) -> float:
    """O4 (2026-08-06 audit) — the HYBRID band: a sleeve's shelter is
    ``min(gap_band_pp, relative_band_frac * reference_pct)`` whenever
    ``reference_pct > 0``, so a small strategic target (e.g. intl_broad/VXUS
    at a 2.0% reference — observed sitting at 0.14% every session, always
    inside the fixed 5pp band built for the much-larger core amplifiers,
    never funded) becomes enforceable once it drifts more than
    ``relative_band_frac`` of its OWN reference away. A zero-or-negative
    reference (LEGACY_EXITS, a zeroed non-selected pool member) is
    UNAFFECTED — the relative term never narrows the shelter for a sleeve
    with no positive target to measure against; it keeps the plain
    ``gap_band_pp``."""
    if reference_pct <= 0:
        return band
    return min(band, relative_band_frac * reference_pct)


def _flag(entry: dict, reason: str) -> None:
    entry["status"] = "non_compliant_flagged"
    entry["reasons"].append(reason)


def _ration(entry: dict, reason: str) -> None:
    entry["status"] = "rationed_by_envelope"
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
    enforced_trade?, pro_rata_share_pp?}}, "enforced_trades": [...],
    "summary": {...}, "enforcement_notional_usd": float}`` where status is one
    of ``confirming | override_covered | enforced | rationed_by_envelope |
    non_compliant_flagged``. ``rationed_by_envelope`` (2026-08-06 audit B6) is a
    re-risk shortfall that already covers its pro-rata share of the session's
    aggregate re-risk envelope — paced by cash/pacing, not a discretionary hold;
    only a shortfall BELOW that share is ``non_compliant_flagged``.
    Sleeves within ``gap_band_pp`` of reference are not reported.
    """
    ov_cfg = (cfg or {}).get("override_protocol") or {}
    rex_cfg = {**REFERENCE_EXECUTION_DEFAULTS, **((cfg or {}).get("reference_execution") or {})}
    band = float(ov_cfg.get("gap_band_pp", 5.0))
    relative_band_frac = float(ov_cfg.get("relative_band_frac", 0.5))
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
               "non_compliant_flagged": 0, "rationed_by_envelope": 0}
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

    # Task B (D-B1, session 2026-08-01): the sanctioned literal-cash -> SGOV
    # carve-out sweep (`shared/trade_validation.py`'s SGOV exemption) is a pure
    # cash-sleeve composition swap, not a deviation from reference — it must not
    # score as the model having "traded AWAY" on SGOV. Mirror the validator's
    # qualifying budget EXACTLY: pre-trade literal cash above the buffer, same-day
    # sell proceeds never counted (else a sell-and-sweep combo could backdoor-grow
    # the exemption past what pre-trade cash actually supports).
    literal_cash_buffer_pct = float(ctx.get("literal_cash_target_pct") or 1.5)
    sgov_carveout_remaining = max(
        0.0, float(ctx.get("cash_usd") or 0) - literal_cash_buffer_pct / 100.0 * equity,
    )

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
        move_notional = notional
        # M1 (review round, session 2026-08-01): the carve-out exclusion must be
        # DIRECTION-AWARE — only a SGOV buy that moves AWAY from reference
        # (SGOV overweight, toward == "sell") is the sanctioned cash-composition
        # swap this exemption exists for. A SGOV buy that is already the TOWARD
        # side (SGOV underweight, toward == "buy") is genuine progress and must
        # be credited in full — applying the exclusion there zeroed out a real
        # corrective sweep's move_pp and caused reconcile to synthesize a
        # redundant band_enforcement buy on top of a trade the model already
        # placed (reproduced: gap -13.5pp, model sweeps the full 13.5pp,
        # model_move_pp read 0.0 instead of 13.5, entry status "enforced").
        if sym == "SGOV" and side == "buy" and toward == "sell" and sgov_carveout_remaining > 0:
            qualifying = min(notional, sgov_carveout_remaining)
            move_notional -= qualifying
            sgov_carveout_remaining -= qualifying
        pp = move_notional / equity * 100.0
        move_pp[sym] = move_pp.get(sym, 0.0) + (pp if side == toward else -pp)

    cash_avail = max(0.0, float(ctx.get("cash_usd") or 0) + sell_notional - buy_notional)
    turnover_budget = float(rex_cfg["enforcement_turnover_max_pct"]) / 100.0 * equity

    # Out-of-band sleeves only; overweights (sells) first so proceeds fund the buys,
    # largest gap first within each side — the resulting enforced list is
    # sells-before-buys by construction.
    out_of_band = []
    for sym, row in rows.items():
        ref_pct = float(row.get("reference_pct") or 0)
        gap_signed = float(row.get("current_pct") or 0) - ref_pct
        eff_band = _effective_band(band, relative_band_frac, ref_pct)
        if abs(gap_signed) > eff_band + _EPS_PP:
            out_of_band.append((sym, gap_signed, row.get("price")))
    out_of_band.sort(key=lambda r: (r[1] < 0, -abs(r[1])))

    # B6 (2026-08-06 audit) — deployable envelope for RE-RISK shortfalls only.
    # required_move_today is computed PER SLEEVE up to tranche each, so during a
    # multi-sleeve de-cash program the sum across sleeves (e.g. 3 underweight
    # amplifiers each "requiring" ~10pp) routinely exceeds what the model can
    # sanely deploy in one session — while re-risk is never synthesized (D3
    # asymmetry), it was unconditionally flagged "file an override or trade next
    # session" even when the model deployed its full aggregate tranche pro-rata
    # across those sleeves. The aggregate re-risk envelope is capped at the SAME
    # tranche_pp_max as a PORTFOLIO-level pace limit (not the sum of each
    # sleeve's own allowance); a sleeve that moved at least its pro-rata share of
    # that envelope is `rationed_by_envelope` (deployed as fast as pacing sanely
    # allows), not `non_compliant_flagged` (a genuine discretionary hold) — the
    # detector must still catch true silent-holds (net_move ~0 with cash/tranche
    # available), which it does: their pro-rata share is > 0.
    re_risk_required: dict[str, float] = {}
    for sym, gap_signed, _px in out_of_band:
        allowed_r = residual.get(sym, 0.0)
        eff_band_r = _effective_band(band, relative_band_frac, float(rows[sym].get("reference_pct") or 0))
        required_total_r = max(0.0, abs(gap_signed) - max(allowed_r, eff_band_r))
        if required_total_r <= _EPS_PP:
            continue
        side_r = "sell" if gap_signed > 0 else "buy"
        if side_r == "sell" and sym in exempt:
            continue
        if not is_de_risk_move(side_r, sym):
            re_risk_required[sym] = min(required_total_r, tranche)
    total_required_re_risk = sum(re_risk_required.values())
    re_risk_envelope_pp = min(total_required_re_risk, tranche) if total_required_re_risk > 0 else 0.0

    seq = 0
    total_enf_notional = 0.0
    for sym, gap_signed, px in out_of_band:
        abs_gap = abs(gap_signed)
        allowed = residual.get(sym, 0.0)
        eff_band = _effective_band(band, relative_band_frac, float(rows[sym].get("reference_pct") or 0))
        required_total = max(0.0, abs_gap - max(allowed, eff_band))
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
            # M2 (2026-08-06 audit) — a sub-min-notional required move on an
            # out-of-band DAMPER sell (re-risk: trimming an overweight damper
            # toward its floor) must not be a per-session coin flip. Before
            # this, the identical 0.05pp/$50-ish required move produced "hold"
            # one day and "sell to the 2-share floor" the next on TLT — the
            # same inputs, opposite actions, no override sheltering either
            # time. Fixed choice (matches the existing "size-floored != total
            # impossibility" doctrine, spec §6 discretionary-move language):
            # ALWAYS trim to the sleeve floor, never hold, when the dollar
            # value of required_move_today alone would be rejected by Tier-1's
            # min-notional floor. A live override sheltering this sleeve
            # (allowed > 0) licenses a hold instead — that IS the override
            # doing its job, not per-session discretion.
            required_today_usd = required_today / 100.0 * equity
            if sym in _DEFENSIVE and side == "sell" and allowed <= _EPS_PP and (
                0 < required_today_usd < min_notional
            ):
                entry["sub_min_notional_action"] = "trim_to_floor"
                entry["reasons"].append(
                    f"required move (${required_today_usd:.0f}) is below the "
                    f"${min_notional:.0f} min-notional floor and no override shelters "
                    f"{sym} — per the fixed M2 rule this is ALWAYS trim-to-floor, "
                    "never a hold: sell the full overweight down to the sleeve floor "
                    "(not just the required pp) rather than treating the size floor "
                    "as impossibility"
                )
            pro_rata_share = (
                (required_today / total_required_re_risk) * re_risk_envelope_pp
                if total_required_re_risk > _EPS_PP else required_today
            )
            entry["pro_rata_share_pp"] = round(pro_rata_share, 2)
            if net_move + _EPS_PP >= pro_rata_share:
                _ration(entry, (
                    f"{shortfall_pp:.1f}pp shortfall vs the full {required_today:.1f}pp "
                    f"required move, but {max(net_move, 0.0):.1f}pp moved is at/above this "
                    f"sleeve's pro-rata share ({pro_rata_share:.1f}pp) of the session's "
                    f"{re_risk_envelope_pp:.1f}pp aggregate re-risk envelope — rationed by "
                    "pacing/cash, not a discretionary hold; never synthesized (spec §6 asymmetry)"
                ))
            else:
                _flag(entry, (
                    f"{shortfall_pp:.1f}pp re-risk shortfall — {max(net_move, 0.0):.1f}pp moved "
                    f"is BELOW this sleeve's pro-rata share ({pro_rata_share:.1f}pp) of the "
                    f"session's {re_risk_envelope_pp:.1f}pp aggregate re-risk envelope; never "
                    "synthesized (spec §6 asymmetry) — requires an honest override or "
                    "next-session action"
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
