"""Unit tests for the override-record validator (responsiveness brief Phase 4, Tier-2).

The validator is the safety layer: it enforces the structural gates on every override
(falsifier + date + clean non-empty evidence + within-band magnitude + valid direction) and
the de-risk/re-risk ASYMMETRY (spec §6) — de-risk passes cheap, re-risk below the evidence
bar is downsized (halved) not silently accepted, and with no evidence is rejected. Run:
    PYTHONPATH=src pytest tests/test_overrides.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shared.overrides import (  # noqa: E402
    OVERRIDE_DEFAULTS,
    validate_override,
    validate_overrides,
)

CFG = dict(OVERRIDE_DEFAULTS)  # max_magnitude_pp=15, re_risk_min_evidence=2, gap_band_pp=5


def _ov(**kw):
    """A well-formed per-sleeve de-risk override (V1_1); override fields via kwargs."""
    base = {
        "sleeve": "SPY",
        "premise_challenged": "divergence:leading_vs_lagging_inflation",
        "direction": "de_risk",
        "magnitude_pp": 5.0,
        "evidence": ["breakevens -28bp 20d (FRED T5YIE 2026-06-29)"],
        "falsifier": "core PCE 3mo reaccelerates above 3.6% in the July print",
        "falsifier_date": "2026-07-25",
        "clean_data_only": True,
    }
    base.update(kw)
    return base


# --- de-risk: cheap (passes with one clean item) ----------------------------

def test_de_risk_single_evidence_accepted_full_size():
    d = validate_override(_ov(), CFG)
    assert d["outcome"] == "accepted"
    assert d["override"]["magnitude_pp"] == 5.0


# --- re-risk: dear (asymmetry) ----------------------------------------------

def test_re_risk_below_bar_is_downsized_not_accepted():
    """Re-risk with 1 evidence item (< 2) -> downsized (magnitude halved), NOT accepted."""
    d = validate_override(_ov(direction="re_risk", magnitude_pp=8.0,
                              evidence=["SPY reclaimed its 200-day (2026-06-29)"]), CFG)
    assert d["outcome"] == "downsized"
    assert d["override"]["magnitude_pp"] == 4.0
    assert d["override"]["_downsized"] is True


def test_re_risk_with_enough_evidence_accepted():
    d = validate_override(_ov(direction="re_risk", magnitude_pp=6.0,
                              evidence=["SPY > 200-day (2026-06-29)",
                                        "GDPNow vintage turned up 2.5->3.1 (2026-06-28)"]), CFG)
    assert d["outcome"] == "accepted"
    assert d["override"]["magnitude_pp"] == 6.0


def test_re_risk_no_evidence_rejected():
    """No evidence at all -> rejected by the structural gate (empty evidence), not downsized."""
    d = validate_override(_ov(direction="re_risk", evidence=[]), CFG)
    assert d["outcome"] == "rejected"
    assert any("no evidence" in r for r in d["reasons"])


def test_asymmetry_same_magnitude_de_risk_passes_re_risk_does_not():
    """The core asymmetry: identical single-evidence override passes as de-risk, is
    downsized as re-risk."""
    de = validate_override(_ov(direction="de_risk"), CFG)
    re = validate_override(_ov(direction="re_risk"), CFG)
    assert de["outcome"] == "accepted"
    assert re["outcome"] == "downsized"


# --- structural gates (reject regardless of direction) ----------------------

def test_missing_falsifier_rejected():
    d = validate_override(_ov(falsifier=None), CFG)
    assert d["outcome"] == "rejected"
    assert any("falsifier" in r for r in d["reasons"])


def test_missing_falsifier_date_rejected():
    d = validate_override(_ov(falsifier_date=None), CFG)
    assert d["outcome"] == "rejected"
    assert any("falsifier_date" in r for r in d["reasons"])


def test_over_band_magnitude_rejected():
    d = validate_override(_ov(magnitude_pp=20.0), CFG)  # > 15
    assert d["outcome"] == "rejected"
    assert any("exceeds Tier-2 band" in r for r in d["reasons"])


def test_dirty_data_rejected():
    d = validate_override(_ov(clean_data_only=False), CFG)
    assert d["outcome"] == "rejected"
    assert any("clean_data_only" in r for r in d["reasons"])


def test_empty_evidence_rejected():
    d = validate_override(_ov(evidence=[]), CFG)
    assert d["outcome"] == "rejected"


def test_invalid_direction_rejected():
    d = validate_override(_ov(direction="sideways"), CFG)
    assert d["outcome"] == "rejected"
    assert any("direction" in r for r in d["reasons"])


def test_invalid_premise_rejected():
    d = validate_override(_ov(premise_challenged="vibes"), CFG)
    assert d["outcome"] == "rejected"


def test_divergence_premise_accepted():
    d = validate_override(_ov(premise_challenged="divergence:credit_complacency"), CFG)
    assert d["outcome"] == "accepted"


def test_bare_premise_accepted():
    for p in ("growth_axis", "inflation_axis", "policy", "dollar_tilt", "conviction",
              "transition_watch"):
        assert validate_override(_ov(premise_challenged=p), CFG)["outcome"] == "accepted"


def test_non_numeric_magnitude_rejected():
    d = validate_override(_ov(magnitude_pp="a lot"), CFG)
    assert d["outcome"] == "rejected"


def test_missing_sleeve_rejected():
    """V1_1 (Finding 2 D1): overrides are per-sleeve — a sleeve-less record shelters
    nothing and is rejected outright."""
    for bad in (None, "", "   "):
        d = validate_override(_ov(sleeve=bad), CFG)
        assert d["outcome"] == "rejected"
        assert any("sleeve" in r for r in d["reasons"])


# --- batch API --------------------------------------------------------------

def test_validate_overrides_partitions_results():
    ovs = [
        _ov(),                                              # accepted
        _ov(direction="re_risk", evidence=["one"]),         # downsized
        _ov(falsifier=None),                                # rejected
    ]
    res = validate_overrides(ovs, CFG)
    assert len(res["accepted"]) == 1
    assert len(res["downsized"]) == 1
    assert len(res["rejected"]) == 1
    assert len(res["decisions"]) == 3


def test_validate_overrides_empty_ok():
    res = validate_overrides([], CFG)
    assert res["accepted"] == [] and res["downsized"] == [] and res["rejected"] == []


def test_validate_overrides_none_cfg_uses_defaults():
    res = validate_overrides([_ov()], None)
    assert len(res["accepted"]) == 1
