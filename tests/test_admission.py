"""ALFRED backtest harness (2026-08-21), Task E — `scripts/admission.py`.

Run: PYTHONPATH=src python -m pytest tests/test_admission.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import admission  # noqa: E402

_CFG = {"min_lag_reduction_days": 3.0, "max_relative_false_flip_increase": 0.10}


def test_admits_strictly_better_candidate():
    baseline = {"median_lag_days": 20.0, "false_flips_per_year": 2.0}
    candidate = {"median_lag_days": 15.0, "false_flips_per_year": 2.0}   # -5d lag, no ff change
    result = admission.admit(baseline, candidate, cfg=_CFG)
    assert result["admit"] is True
    assert result["lag_delta_days"] == 5.0
    assert result["false_flip_delta"] == 0.0


def test_rejects_lag_neutral_candidate():
    baseline = {"median_lag_days": 20.0, "false_flips_per_year": 2.0}
    candidate = {"median_lag_days": 19.0, "false_flips_per_year": 2.0}   # only -1d, below the 3d bar
    result = admission.admit(baseline, candidate, cfg=_CFG)
    assert result["admit"] is False
    assert "demonstrable" in result["reason"]


def test_rejects_lag_reducing_candidate_that_blows_past_false_flip_tolerance():
    baseline = {"median_lag_days": 20.0, "false_flips_per_year": 2.0}
    # Big lag win (-10d) but false flips nearly double (+90%), well past 10% tolerance.
    candidate = {"median_lag_days": 10.0, "false_flips_per_year": 3.8}
    result = admission.admit(baseline, candidate, cfg=_CFG)
    assert result["admit"] is False
    assert result["lag_delta_days"] == 10.0
    assert "material" in result["reason"] or "tolerance" in result["reason"]


def test_is_a_pure_function_of_its_two_metric_inputs():
    baseline = {"median_lag_days": 20.0, "false_flips_per_year": 2.0}
    candidate = {"median_lag_days": 15.0, "false_flips_per_year": 2.1}
    r1 = admission.admit(baseline, candidate, cfg=_CFG)
    r2 = admission.admit(dict(baseline), dict(candidate), cfg=dict(_CFG))
    assert r1 == r2


def test_worse_lag_never_admitted_even_with_fewer_false_flips():
    baseline = {"median_lag_days": 20.0, "false_flips_per_year": 5.0}
    candidate = {"median_lag_days": 25.0, "false_flips_per_year": 0.0}
    result = admission.admit(baseline, candidate, cfg=_CFG)
    assert result["admit"] is False
    assert result["lag_delta_days"] == -5.0


def test_zero_baseline_false_flips_any_increase_is_material():
    baseline = {"median_lag_days": 20.0, "false_flips_per_year": 0.0}
    candidate = {"median_lag_days": 10.0, "false_flips_per_year": 0.5}   # tiny absolute increase
    result = admission.admit(baseline, candidate, cfg=_CFG)
    assert result["admit"] is False   # relative increase off a zero baseline is infinite -> material


def test_zero_baseline_false_flips_no_new_flips_still_admits():
    baseline = {"median_lag_days": 20.0, "false_flips_per_year": 0.0}
    candidate = {"median_lag_days": 10.0, "false_flips_per_year": 0.0}
    result = admission.admit(baseline, candidate, cfg=_CFG)
    assert result["admit"] is True


def test_missing_lag_metric_degrades_gracefully_never_admits():
    result = admission.admit({"false_flips_per_year": 1.0}, {"median_lag_days": 5.0, "false_flips_per_year": 1.0}, cfg=_CFG)
    assert result["admit"] is False
    assert result["lag_delta_days"] is None


def test_missing_false_flip_metric_degrades_gracefully_never_admits():
    baseline = {"median_lag_days": 20.0}
    candidate = {"median_lag_days": 10.0, "false_flips_per_year": 1.0}
    result = admission.admit(baseline, candidate, cfg=_CFG)
    assert result["admit"] is False
    assert result["lag_delta_days"] == 10.0
    assert result["false_flip_delta"] is None


def test_load_admission_rule_reads_the_committed_config():
    cfg = admission.load_admission_rule()
    assert cfg["min_lag_reduction_days"] == 3.0
    assert cfg["max_relative_false_flip_increase"] == 0.10


def test_admit_defaults_to_the_committed_config_when_cfg_omitted():
    baseline = {"median_lag_days": 20.0, "false_flips_per_year": 2.0}
    candidate = {"median_lag_days": 16.5, "false_flips_per_year": 2.0}   # -3.5d, clears default 3.0d bar
    result = admission.admit(baseline, candidate)
    assert result["admit"] is True
