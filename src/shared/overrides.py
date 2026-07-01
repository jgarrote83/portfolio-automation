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
the Tier-2 band, and the direction must be valid. This module is PURE (no I/O) so it is fully
unit-testable; the analyzer calls it after parsing the model output and before writing.

Note: an override can never *loosen* the deployment gate — `premise_challenged` is a reason to
deviate from the reference *weights*, not a licence to buy Q1/Q2 beta while the gate is closed.
That gate rule is enforced on the trades themselves (the prompt's deployment_gate rule); this
validator additionally refuses any override whose premise is "policy"/"growth_axis" in the
re-risk direction from *asserting* the gate should open (flagged, not silently passed).
"""
from __future__ import annotations

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


def validate_override(ov: dict, cfg: dict) -> dict:
    """Validate one override record. Returns a decision dict:

        {"outcome": "accepted" | "downsized" | "rejected",
         "override": <the (possibly magnitude-adjusted) record>,
         "reasons": [ ... ]}

    Structural failures → rejected. A re-risk override below the evidence bar → downsized
    (magnitude halved) if it has *some* evidence, else rejected. de-risk always passes the
    asymmetry (structural gates still apply).
    """
    reasons: list[str] = []
    max_mag = float(cfg.get("max_magnitude_pp", OVERRIDE_DEFAULTS["max_magnitude_pp"]))
    re_risk_min = int(cfg.get("re_risk_min_evidence", OVERRIDE_DEFAULTS["re_risk_min_evidence"]))

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

    # --- asymmetry (spec §6): re-risk needs the higher bar ------------------
    if direction == "re_risk" and len(evidence) < re_risk_min:
        # Some evidence but below the bar → downsize (halve), never silent full-size accept.
        adjusted = dict(ov)
        adjusted["magnitude_pp"] = round(magnitude / 2.0, 2)
        adjusted["_downsized"] = True
        return {
            "outcome": "downsized",
            "override": adjusted,
            "reasons": [
                f"re-risk override below evidence bar ({len(evidence)} < {re_risk_min}) "
                f"— magnitude halved to {adjusted['magnitude_pp']}pp"
            ],
        }

    return {"outcome": "accepted", "override": ov, "reasons": []}


def validate_overrides(overrides: list[dict], cfg: dict | None = None) -> dict:
    """Validate a list of override records. Returns:

        {"accepted": [...], "downsized": [...], "rejected": [...],
         "decisions": [ per-record decision dicts ]}

    The analyzer persists all decisions (Phase 4d) and applies accepted + downsized
    magnitudes; rejected overrides do not authorize any deviation.
    """
    cfg = cfg or OVERRIDE_DEFAULTS
    decisions = [validate_override(ov or {}, cfg) for ov in (overrides or [])]
    return {
        "accepted": [d["override"] for d in decisions if d["outcome"] == "accepted"],
        "downsized": [d["override"] for d in decisions if d["outcome"] == "downsized"],
        "rejected": [d["override"] for d in decisions if d["outcome"] == "rejected"],
        "decisions": decisions,
    }
