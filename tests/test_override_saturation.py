"""Unit tests for the override-saturation alarm (2026-09-02, Task D1).

All three source sessions carried 17-19 simultaneous sleeve overrides (one on
every sleeve with a non-zero reference) and proposed ZERO trades — nothing
treated that as anomalous. This is the cheapest possible detector: when the
fraction of referenced sleeves under an override this session exceeds
`override_saturation_alarm_frac` (0.40), the REFERENCE is the suspect, not the
model's positioning.

Run: PYTHONPATH=src pytest tests/test_override_saturation.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer.handler import (  # noqa: E402
    _build_override_saturation,
    _override_saturation_addendum,
)


def _gap(sym, ref):
    return {"symbol": sym, "current_pct": ref, "reference_pct": ref}


def _dec(sleeve, outcome="accepted"):
    return {"outcome": outcome, "override": {"sleeve": sleeve, "magnitude_pp": 5.0}}


def test_reconstructed_17_of_17_fires_the_alarm():
    """The literal reproduced scenario: one override on EVERY referenced
    sleeve, 17/17."""
    sleeves = [f"S{i}" for i in range(17)]
    gaps = [_gap(s, 5.782) for s in sleeves]
    decs = [_dec(s) for s in sleeves]
    sat = _build_override_saturation(decs, gaps, 0.40)
    assert sat["overridden_count"] == 17
    assert sat["total_referenced_sleeves"] == 17
    assert sat["saturation_frac"] == 1.0
    assert sat["alarm"] is True


def test_below_threshold_no_alarm():
    sleeves = [f"S{i}" for i in range(17)]
    gaps = [_gap(s, 5.782) for s in sleeves]
    decs = [_dec(s) for s in sleeves[:5]]   # 5/17 ~ 29%
    sat = _build_override_saturation(decs, gaps, 0.40)
    assert sat["alarm"] is False
    assert sat["overridden_count"] == 5


def test_exactly_at_threshold_does_not_fire_strictly_above_only():
    gaps = [_gap(f"S{i}", 5.0) for i in range(10)]
    decs = [_dec(f"S{i}") for i in range(4)]   # exactly 40%
    sat = _build_override_saturation(decs, gaps, 0.40)
    assert sat["saturation_frac"] == 0.4
    assert sat["alarm"] is False   # strictly greater than, not >=


def test_zero_reference_sleeve_excluded_from_denominator():
    """A LEGACY_EXITS / non-selected pool member (reference 0) overridden
    doesn't count toward the saturation denominator or numerator — the
    alarm is about whether the LIVE reference looks broken."""
    gaps = [_gap("SPY", 5.782), _gap("MCK", 0.0)]   # MCK zero-reference
    decs = [_dec("MCK")]   # only the zero-reference sleeve overridden
    sat = _build_override_saturation(decs, gaps, 0.40)
    assert sat["total_referenced_sleeves"] == 1   # only SPY counts
    assert sat["overridden_count"] == 0
    assert sat["alarm"] is False


def test_rejected_override_still_counts_toward_saturation():
    """The FILING behavior itself is the smell, not the validation outcome —
    a rejected override on every sleeve is still 17 filed overrides."""
    gaps = [_gap(f"S{i}", 5.0) for i in range(5)]
    decs = [_dec(f"S{i}", outcome="rejected") for i in range(5)]
    sat = _build_override_saturation(decs, gaps, 0.40)
    assert sat["overridden_count"] == 5
    assert sat["alarm"] is True


def test_no_referenced_sleeves_is_non_fatal():
    sat = _build_override_saturation([], [], 0.40)
    assert sat["total_referenced_sleeves"] == 0
    assert sat["alarm"] is False


# --- markdown addendum: interpretation, not just the number ------------------

def test_addendum_empty_when_no_alarm():
    assert _override_saturation_addendum({"alarm": False}) == ""


def test_addendum_states_interpretation_when_alarm_fires():
    sat = _build_override_saturation(
        [_dec(f"S{i}") for i in range(17)], [_gap(f"S{i}", 5.782) for i in range(17)], 0.40,
    )
    md = _override_saturation_addendum(sat)
    assert "Data Integrity Warning" in md
    assert "17" in md
    # States the INTERPRETATION (the reference itself is the suspect), not
    # just the raw count.
    assert "reference" in md.lower() and "wrong" in md.lower()
