"""Unit tests for the Phase C §6 track_record aggregation (collector handler).

Covers hit-rate rollup, the capture-fine/report-coarse trigger bucketing with the
n>=10 promotion rule, confidence calibration, and over-trading — none observable
live until recommendations mature at the 60d headline horizon. Run:
    PYTHONPATH=src pytest tests/test_track_record.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _aggregate_track_record  # noqa: E402


def _row(layer="flex", conf=0.6, trigger=None, thesis=None,
         c60=None, c30=None, c90=None, rec="2026-01-05"):
    return {
        "layer": layer,
        "confidence": conf,
        "primary_trigger": trigger,
        "thesis_type": thesis,
        "recommended_at": rec,
        "call_correct_30d": c30,
        "call_correct_60d": c60,
        "call_correct_90d": c90,
    }


# --- empty / pre-maturity ----------------------------------------------------

def test_empty_rows():
    out = _aggregate_track_record([])
    assert out["sample_size"] == 0
    assert out["headline_horizon"] == "60d"
    assert out["over_trading"]["avg_trades_per_day"] is None
    assert "note" in out
    assert out["horizons"]["60d"]["n"] == 0


def test_30d_context_shows_before_headline_matures():
    # 30d outcomes exist, 60d not yet -> sample_size 0 but 30d horizon populated.
    rows = [_row(c30=True, rec="2026-01-01"), _row(c30=False, rec="2026-01-02")]
    out = _aggregate_track_record(rows)
    assert out["sample_size"] == 0
    assert out["horizons"]["30d"]["n"] == 2
    assert out["horizons"]["30d"]["hit_rate"] == 0.5
    assert out["horizons"]["60d"]["n"] == 0


# --- by_layer ----------------------------------------------------------------

def test_by_layer_hit_rates():
    rows = [
        _row(layer="core", c60=True), _row(layer="core", c60=False),
        _row(layer="flex", c60=True), _row(layer="flex", c60=True),
        _row(layer="flex", c60=False),
    ]
    out = _aggregate_track_record(rows)
    assert out["sample_size"] == 5
    assert out["by_layer"]["core"] == {"n": 2, "hit_rate": 0.5}
    assert out["by_layer"]["flex"] == {"n": 3, "hit_rate": 0.67}


# --- by_trigger: coarse rollup + promotion -----------------------------------

def test_by_trigger_rolls_up_to_coarse_while_thin():
    # news_catalyst + earnings both map to coarse "catalyst"; thematic_tier ->
    # "thematic". Each fine bucket is below the n>=10 promotion threshold.
    rows = [
        _row(trigger="news_catalyst", c60=True),
        _row(trigger="earnings", c60=False),
        _row(trigger="thematic_tier", c60=True),
    ]
    out = _aggregate_track_record(rows)
    assert out["by_trigger"]["catalyst"] == {"n": 2, "hit_rate": 0.5}
    assert out["by_trigger"]["thematic"] == {"n": 1, "hit_rate": 1.0}
    assert "news_catalyst" not in out["by_trigger"]  # not promoted


def test_by_trigger_promotes_fine_bucket_at_n10():
    rows = [_row(trigger="news_catalyst", c60=(i % 2 == 0)) for i in range(10)]
    out = _aggregate_track_record(rows)
    assert out["by_trigger"]["news_catalyst"]["n"] == 10  # promoted to its own line
    assert "catalyst" not in out["by_trigger"]


def test_unknown_trigger_falls_into_other():
    rows = [_row(trigger="mystery_signal", c60=True)]
    out = _aggregate_track_record(rows)
    assert out["by_trigger"]["other"] == {"n": 1, "hit_rate": 1.0}


# --- by_thesis ---------------------------------------------------------------

def test_by_thesis_groups_coarse():
    rows = [
        _row(thesis="catalyst", c60=True),
        _row(thesis="mispricing", c60=False),
        _row(thesis="mispricing", c60=False),
    ]
    out = _aggregate_track_record(rows)
    assert out["by_thesis"]["catalyst"] == {"n": 1, "hit_rate": 1.0}
    assert out["by_thesis"]["mispricing"] == {"n": 2, "hit_rate": 0.0}


def test_core_rows_excluded_from_trigger_thesis():
    # core trades carry no reasoning enums -> no by_trigger/by_thesis lines.
    rows = [_row(layer="core", trigger=None, thesis=None, c60=True)]
    out = _aggregate_track_record(rows)
    assert "by_trigger" not in out
    assert "by_thesis" not in out


# --- calibration -------------------------------------------------------------

def test_calibration_bucket_predicted_vs_actual():
    # Four 0.75-confidence calls, 2 correct -> bucket 0.7-0.8, predicted .75, actual .5
    rows = [_row(conf=0.75, c60=(i < 2)) for i in range(4)]
    out = _aggregate_track_record(rows)
    bucket = next(b for b in out["calibration"] if b["bucket"] == "0.7-0.8")
    assert bucket["n"] == 4
    assert bucket["predicted"] == 0.75
    assert bucket["actual"] == 0.5


def test_calibration_clamps_full_confidence_into_top_bucket():
    rows = [_row(conf=1.0, c60=True)]
    out = _aggregate_track_record(rows)
    assert any(b["bucket"] == "0.9-1.0" for b in out["calibration"])


# --- over-trading ------------------------------------------------------------

def test_over_trading_avg_per_day():
    # 4 trades across 2 distinct recommendation dates -> 2.0/day.
    rows = [
        _row(c60=True, rec="2026-01-01"), _row(c60=True, rec="2026-01-01"),
        _row(c60=False, rec="2026-01-02"), _row(c60=False, rec="2026-01-02"),
    ]
    out = _aggregate_track_record(rows)
    assert out["over_trading"]["avg_trades_per_day"] == 2.0


def test_caveat_present_when_matured():
    out = _aggregate_track_record([_row(c60=True)])
    assert "anecdotal" in out["caveat"]
