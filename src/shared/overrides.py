"""Override-record validation (responsiveness brief Phase 4, Tier-2 enforcement).

An *override* is the LLM's structured, falsifiable justification for deviating from the
deterministic `reference_weights` target. Judgment operates only through these records, and
they are governed by the **de-risk / re-risk asymmetry** (strategy-spec §6):

- a **de-risk** override (toward more caution/defense) is cheap — a single clean evidence
  item passes at full size.
- a **re-risk** override (toward more risk / less defense) is dear — it needs a higher
  evidence bar; below it the override is **downsized** (magnitude halved), and with no
  evidence at all it is **rejected**. Never silently accepted at full size.

Structural gates apply to both directions: a falsifier + falsifier_date are mandatory,
evidence must be clean (`clean_data_only` true and non-empty), the magnitude must sit within
the Tier-2 band, the direction must be valid, and (OVERRIDE_SCHEMA_V1_1 — Finding 2 D1) the
record must name the single core `sleeve` it shelters. This module is PURE (no I/O) so it is
fully unit-testable; the analyzer calls it after parsing the model output and before writing.

Finding 2 (D1) semantics: `magnitude_pp` caps the RESIDUAL an override may shelter, not the
move — for an out-of-band sleeve, `required_move_total = max(0, gap − max(allowed_residual,
gap_band_pp))` MUST still trade (see `shared/reference_execution.py::reconcile`, which the
analyzer runs after this validator). A hold-override can therefore shelter at most
`max_magnitude_pp` of any gap; a rejected override shelters nothing.

Note: an override can never *loosen* the deployment gate — `premise_challenged` is a reason to
deviate from the reference *weights*, not a licence to buy Q1/Q2 beta while the gate is closed.
That gate rule is enforced on the trades themselves (the prompt's deployment_gate rule); this
validator additionally refuses any override whose premise is "policy"/"growth_axis" in the
re-risk direction from *asserting* the gate should open (flagged, not silently passed).
"""
from __future__ import annotations

from shared.reference_execution import derive_override_direction

# Fallback config if risk-limits.json lacks an override_protocol block (mirror that file).
OVERRIDE_DEFAULTS = {
    "max_magnitude_pp": 15.0,
    "re_risk_min_evidence": 2,
    "gap_band_pp": 5.0,
}

_VALID_DIRECTIONS = ("de_risk", "re_risk")
_VALID_PREMISES = (
    "growth_axis", "inflation_axis", "policy", "dollar_tilt", "conviction",
    "transition_watch",
)  # plus any "divergence:<id>"


def _premise_ok(premise: str) -> bool:
    if not premise:
        return False
    return premise in _VALID_PREMISES or premise.startswith("divergence:")


def validate_override(ov: dict, cfg: dict, gap_signed: float | None = None) -> dict:
    """Validate one override record. Returns a decision dict:

        {"outcome": "accepted" | "downsized" | "rejected",
         "override": <the (possibly magnitude-adjusted) record>,
         "reasons": [ ... ]}

    Structural failures → rejected. A re-risk override below the evidence bar → downsized
    (magnitude halved) if it has *some* evidence, else rejected. de-risk always passes the
    asymmetry (structural gates still apply).

    ``gap_signed`` (session 2026-07-15, Task E1) = current_pct − reference_pct for
    this override's sleeve, when available — lets `derive_override_direction`
    compute the direction deterministically from the sleeve's block rather than
    trusting the model's self-declared claim. The DERIVED direction (not the
    declared one) governs the asymmetry bar below; a disagreement is
    corrected-and-flagged (a reason is appended), never rejected outright — a
    mislabeled direction is a data-quality signal against the model, not grounds
    to lose an otherwise-valid override. The returned record carries both
    `direction` (now the effective/derived value) and `declared_direction` (the
    model's original claim) so OverrideHistory can measure the misclassification
    rate. When no gap is available (off-roster sleeve, or gaps not supplied),
    derivation is skipped and the declared direction is used as-is — unchanged,
    fully backward-compatible behavior.
    """
    reasons: list[str] = []
    max_mag = float(cfg.get("max_magnitude_pp", OVERRIDE_DEFAULTS["max_magnitude_pp"]))
    re_risk_min = int(cfg.get("re_risk_min_evidence", OVERRIDE_DEFAULTS["re_risk_min_evidence"]))

    sleeve = ov.get("sleeve")
    direction = ov.get("direction")
    premise = ov.get("premise_challenged")
    evidence = ov.get("evidence") or []
    falsifier = ov.get("falsifier")
    falsifier_date = ov.get("falsifier_date")
    clean = ov.get("clean_data_only")
    try:
        magnitude = abs(float(ov.get("magnitude_pp")))
    except (TypeError, ValueError):
        magnitude = None

    # --- structural gates (both directions) ---------------------------------
    if not sleeve or not str(sleeve).strip():
        reasons.append(
            "missing sleeve — overrides are per-sleeve records (OVERRIDE_SCHEMA_V1_1)"
        )
    if direction not in _VALID_DIRECTIONS:
        reasons.append(f"invalid direction {direction!r}")
    if not _premise_ok(premise or ""):
        reasons.append(f"invalid premise_challenged {premise!r}")
    if magnitude is None:
        reasons.append("magnitude_pp missing/non-numeric")
    elif magnitude > max_mag:
        reasons.append(f"magnitude {magnitude}pp exceeds Tier-2 band {max_mag}pp")
    if not falsifier:
        reasons.append("missing falsifier")
    if not falsifier_date:
        reasons.append("missing falsifier_date")
    if clean is not True:
        reasons.append("clean_data_only not asserted true (evidence integrity unverified)")
    if not evidence:
        reasons.append("no evidence")

    if reasons:
        return {"outcome": "rejected", "override": ov, "reasons": reasons}

    # --- Task E1: derive the direction deterministically; correct-and-flag, ----
    # never reject solely for a mislabeled direction (the structural direction
    # gate above already rejected anything that isn't a valid enum value at all).
    derived = derive_override_direction(sleeve, gap_signed)
    effective_direction = derived or direction
    disagreement = None
    if derived is not None and derived != direction:
        disagreement = (
            f"declared direction {direction!r} disagrees with the derived "
            f"direction {derived!r} for sleeve {sleeve!r} (gap {gap_signed:+.2f}pp) "
            "— using the derived direction for the asymmetry bar"
        )
    ov = {**ov, "declared_direction": direction, "direction": effective_direction}

    # --- asymmetry (spec §6): re-risk needs the higher bar ------------------
    if effective_direction == "re_risk" and len(evidence) < re_risk_min:
        # Some evidence but below the bar → downsize (halve), never silent full-size accept.
        adjusted = dict(ov)
        adjusted["magnitude_pp"] = round(magnitude / 2.0, 2)
        adjusted["_downsized"] = True
        out_reasons = [
            f"re-risk override below evidence bar ({len(evidence)} < {re_risk_min}) "
            f"— magnitude halved to {adjusted['magnitude_pp']}pp"
        ]
        if disagreement:
            out_reasons.append(disagreement)
        return {"outcome": "downsized", "override": adjusted, "reasons": out_reasons}

    return {"outcome": "accepted", "override": ov, "reasons": [disagreement] if disagreement else []}


def validate_overrides(
    overrides: list[dict], cfg: dict | None = None, gaps: list[dict] | None = None,
) -> dict:
    """Validate a list of override records. Returns:

        {"accepted": [...], "downsized": [...], "rejected": [...],
         "decisions": [ per-record decision dicts ]}

    The analyzer persists all decisions (Phase 4d) and applies accepted + downsized
    magnitudes; rejected overrides do not authorize any deviation.

    ``gaps`` (session 2026-07-15, Task E1) — the same per-sleeve gap rows
    `reconcile` consumes (``{"symbol", "current_pct", "reference_pct", ...}``) —
    lets each override's direction be derived deterministically rather than
    trusted from the model's claim. Optional and backward-compatible: omitted or
    a sleeve missing from ``gaps`` simply skips derivation for that record.
    """
    cfg = cfg or OVERRIDE_DEFAULTS
    gap_by_sleeve: dict[str, float] = {}
    for g in gaps or []:
        sym = str(g.get("symbol") or "").upper()
        if not sym:
            continue
        try:
            gap_by_sleeve[sym] = float(g.get("current_pct") or 0) - float(g.get("reference_pct") or 0)
        except (TypeError, ValueError):
            continue

    decisions = []
    for ov in overrides or []:
        ov = ov or {}
        sleeve = str(ov.get("sleeve") or "").upper()
        decisions.append(validate_override(ov, cfg, gap_by_sleeve.get(sleeve)))
    return {
        "accepted": [d["override"] for d in decisions if d["outcome"] == "accepted"],
        "downsized": [d["override"] for d in decisions if d["outcome"] == "downsized"],
        "rejected": [d["override"] for d in decisions if d["outcome"] == "rejected"],
        "decisions": decisions,
    }
