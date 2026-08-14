"""Task D6 (2026-08-14 audit) — thematic-conviction matured-outcome grading.

Mirrors the existing `_grade_switch`/`_grade_override` testing precedent: the
pure per-horizon grader is unit-tested directly; the table-I/O orchestration
(`_stamp_thematic_outcomes`) is not (same precedent as `_stamp_switch_outcomes`,
untested for the same reason — too much mocking overhead for the value, and
it is a thin wrapper around the already-tested pure pieces). Calibration
aggregation (`_build_thematic_calibration`) IS tested, with `query_entities`
mocked.

Run: PYTHONPATH=src pytest tests/test_thematic_grading.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import collector.handler as ch  # noqa: E402
from collector.handler import (  # noqa: E402
    _build_thematic_calibration,
    _grade_thematic_horizon,
    _load_risk_limits,
)

RL = _load_risk_limits()


def test_grade_thematic_horizon_up_correct():
    g = _grade_thematic_horizon(entry_price=100.0, horizon_price=110.0,
                                 spy_entry=500.0, spy_horizon=510.0)
    assert g["actual_up"] == 1.0
    assert abs(g["ret_pct"] - 10.0) < 1e-9
    assert abs(g["excess_vs_spy_pp"] - (10.0 - 2.0)) < 1e-9


def test_grade_thematic_horizon_down():
    g = _grade_thematic_horizon(entry_price=100.0, horizon_price=95.0,
                                 spy_entry=500.0, spy_horizon=500.0)
    assert g["actual_up"] == 0.0


def test_grade_thematic_horizon_none_on_missing_price():
    assert _grade_thematic_horizon(None, 110.0, 500.0, 510.0) is None
    assert _grade_thematic_horizon(100.0, None, 500.0, 510.0) is None


def test_grade_thematic_horizon_no_spy_excess_when_spy_missing():
    g = _grade_thematic_horizon(entry_price=100.0, horizon_price=110.0,
                                 spy_entry=None, spy_horizon=None)
    assert g["actual_up"] == 1.0
    assert "excess_vs_spy_pp" not in g


def test_calibration_below_min_sample_no_damping(monkeypatch):
    monkeypatch.setattr(ch, "query_entities", lambda table, filt=None: [
        {"p_up": 0.7, "actual_up_60d": 1.0},
        {"p_up": 0.6, "actual_up_60d": 0.0},
    ])
    out = _build_thematic_calibration(RL)
    assert out["sample_size"] == 2
    assert out["damping_factor"] == 1.0   # below brier_min_sample (10)


def test_calibration_computes_brier_and_damping_at_sufficient_sample(monkeypatch):
    # 12 resolved rows, all well-calibrated (p_up matches actual exactly) -> brier 0.
    rows = [{"p_up": 1.0, "actual_up_60d": 1.0} for _ in range(6)] + \
           [{"p_up": 0.0, "actual_up_60d": 0.0} for _ in range(6)]
    monkeypatch.setattr(ch, "query_entities", lambda table, filt=None: rows)
    out = _build_thematic_calibration(RL)
    assert out["sample_size"] == 12
    assert out["brier_score"] == 0.0
    assert out["hit_rate"] == 1.0
    assert out["damping_factor"] == 1.0


def test_calibration_poor_brier_damps(monkeypatch):
    # Systematically wrong: p_up=0.9 but actual is always 0 -> brier 0.81.
    rows = [{"p_up": 0.9, "actual_up_60d": 0.0} for _ in range(12)]
    monkeypatch.setattr(ch, "query_entities", lambda table, filt=None: rows)
    out = _build_thematic_calibration(RL)
    assert out["brier_score"] == 0.81
    assert out["damping_factor"] == 0.0   # worst damping rung


def test_calibration_no_resolved_rows_returns_empty_sample(monkeypatch):
    monkeypatch.setattr(ch, "query_entities", lambda table, filt=None: [])
    out = _build_thematic_calibration(RL)
    assert out["sample_size"] == 0
    assert out["brier_score"] is None
    assert out["damping_factor"] == 1.0
