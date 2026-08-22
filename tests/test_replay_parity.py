"""ALFRED backtest harness (2026-08-21), Task D — `scripts/replay_parity.py`.

This is the harness's gating check: if parity logic itself has a bug, every
downstream lag/false-flip number is meaningless. Synthetic `records`
fixtures (shaped exactly like `replay_axes.replay()`'s real output) drive
every test -- no real snapshot or ALFRED data needed to verify the SCORING
logic is correct.

Run: PYTHONPATH=src python -m pytest tests/test_replay_parity.py
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import replay_parity as rp  # noqa: E402


def _dates(start: str, n: int) -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def _record(d, growth_raw, growth_confirmed, infl_raw, infl_confirmed,
            active_quadrant="", g_reconstructable=True, i_reconstructable=True):
    return {
        "date": d,
        "growth": {"raw_direction": growth_raw, "direction": growth_confirmed},
        "inflation": {"raw_direction": infl_raw, "direction": infl_confirmed},
        "active_quadrant": active_quadrant,
        "reconstructable": {"growth": g_reconstructable, "inflation": i_reconstructable},
    }


# --- check_parity ---------------------------------------------------------------

def test_check_parity_all_match():
    records = [_record("2026-01-05", "rising", "rising", "flat", "flat", "Q1")]
    snapshots = {"2026-01-05": {"growth_axis": {"direction": "rising"},
                                 "inflation_axis": {"direction": "flat"},
                                 "active_quadrant": "Q1"}}
    result = rp.check_parity(records, snapshots)
    assert result == {"checked": 1, "matched": 1, "parity_rate": 1.0, "mismatches": []}


def test_check_parity_itemizes_every_mismatched_field():
    records = [_record("2026-01-05", "rising", "rising", "flat", "flat", "Q1")]
    snapshots = {"2026-01-05": {"growth_axis": {"direction": "falling"},
                                 "inflation_axis": {"direction": "flat"},
                                 "active_quadrant": "Q4"}}
    result = rp.check_parity(records, snapshots)
    assert result["checked"] == 1
    assert result["matched"] == 0
    assert result["parity_rate"] == 0.0
    assert len(result["mismatches"]) == 1
    fields = {m["field"] for m in result["mismatches"][0]["mismatches"]}
    assert fields == {"growth_axis.direction", "active_quadrant"}


def test_check_parity_skips_snapshot_date_not_in_records():
    records = [_record("2026-01-05", "rising", "rising", "flat", "flat", "Q1")]
    snapshots = {"2099-01-01": {"growth_axis": {"direction": "rising"}}}
    result = rp.check_parity(records, snapshots)
    assert result == {"checked": 0, "matched": 0, "parity_rate": None, "mismatches": []}


def test_check_parity_rate_over_multiple_dates():
    records = [
        _record("2026-01-05", "rising", "rising", "flat", "flat", "Q1"),
        _record("2026-01-06", "rising", "rising", "flat", "flat", "Q1"),
    ]
    snapshots = {
        "2026-01-05": {"growth_axis": {"direction": "rising"}, "inflation_axis": {"direction": "flat"}, "active_quadrant": "Q1"},
        "2026-01-06": {"growth_axis": {"direction": "falling"}, "inflation_axis": {"direction": "flat"}, "active_quadrant": "Q1"},
    }
    result = rp.check_parity(records, snapshots)
    assert result["checked"] == 2
    assert result["matched"] == 1
    assert result["parity_rate"] == 0.5


# --- _flip_date -------------------------------------------------------------

def test_flip_date_finds_genuine_transition():
    dated = [("2026-01-01", "rising"), ("2026-01-02", "rising"), ("2026-01-03", "falling")]
    assert rp._flip_date(dated, "falling", "2026-01-03", 30, 30) == "2026-01-03"


def test_flip_date_none_when_already_at_target_before_window():
    """Already 'falling' at the very first record in the window -- no NEW
    transition happened inside the searched range."""
    dated = [("2026-01-01", "falling"), ("2026-01-02", "falling")]
    assert rp._flip_date(dated, "falling", "2026-01-02", 30, 30) is None


def test_flip_date_none_when_target_never_reached():
    dated = [("2026-01-01", "rising"), ("2026-01-02", "rising")]
    assert rp._flip_date(dated, "falling", "2026-01-01", 30, 30) is None


def test_flip_date_respects_window_boundaries():
    dated = [("2026-01-01", "rising"), ("2026-06-01", "falling")]   # far outside a small window
    assert rp._flip_date(dated, "falling", "2026-01-01", window_before_days=5, window_after_days=5) is None


def test_flip_date_recognizes_transition_using_full_series_not_just_window():
    """The record right before the window's first date already read the
    target direction -- the record AT the window's edge must not be
    misread as a fresh transition."""
    dated = [("2025-12-30", "falling"), ("2026-01-01", "falling"), ("2026-01-02", "falling")]
    assert rp._flip_date(dated, "falling", "2026-01-02", window_before_days=2, window_after_days=2) is None


# --- per_turn_lag / turn_lag_table -----------------------------------------------

def _growth_records(directions_by_date: dict[str, str], reconstructable=True) -> list[dict]:
    return [
        _record(d, dirn, dirn, "flat", "flat", g_reconstructable=reconstructable)
        for d, dirn in sorted(directions_by_date.items())
    ]


def test_per_turn_lag_positive_when_classifier_lags():
    dates = _dates("2026-01-01", 10)
    directions = {d: "rising" for d in dates[:5]}
    directions.update({d: "falling" for d in dates[5:]})   # flips on dates[5]
    records = _growth_records(directions)
    turn = {"id": "t1", "axis": "growth", "direction": "falling", "true_turn_date": dates[3]}
    result = rp.per_turn_lag(records, turn, window_before_days=30, window_after_days=30)
    assert result["reconstructable"] is True
    assert result["confirmed_flip_date"] == dates[5]
    expected_lag = (date.fromisoformat(dates[5]) - date.fromisoformat(dates[3])).days
    assert result["confirmed_lag_days"] == expected_lag > 0


def test_per_turn_lag_negative_when_classifier_leads():
    dates = _dates("2026-01-01", 10)
    directions = {d: "rising" for d in dates[:3]}
    directions.update({d: "falling" for d in dates[3:]})   # flips EARLY, on dates[3]
    records = _growth_records(directions)
    turn = {"id": "t1", "axis": "growth", "direction": "falling", "true_turn_date": dates[7]}
    result = rp.per_turn_lag(records, turn, window_before_days=30, window_after_days=30)
    assert result["confirmed_flip_date"] == dates[3]
    assert result["confirmed_lag_days"] < 0


def test_per_turn_lag_unreconstructable_when_coverage_below_threshold():
    dates = _dates("2026-01-01", 10)
    directions = {d: "falling" for d in dates}
    records = _growth_records(directions, reconstructable=False)   # zero coverage
    turn = {"id": "t1", "axis": "growth", "direction": "falling", "true_turn_date": dates[5]}
    result = rp.per_turn_lag(records, turn, window_before_days=30, window_after_days=30)
    assert result["reconstructable"] is False
    assert result["confirmed_flip_date"] is None
    assert result["confirmed_lag_days"] is None
    assert result["reconstructable_reason"] is not None


def test_turn_lag_table_drops_unreconstructable_from_median():
    dates = _dates("2026-01-01", 10)
    good_directions = {d: "rising" for d in dates[:5]}
    good_directions.update({d: "falling" for d in dates[5:]})
    records = _growth_records(good_directions, reconstructable=True)
    # Second turn deliberately far outside any real data -- forces
    # reconstructable=False via zero coverage in ITS OWN window.
    turns = [
        {"id": "reconstructable-turn", "axis": "growth", "direction": "falling", "true_turn_date": dates[3]},
        {"id": "unreconstructable-turn", "axis": "growth", "direction": "falling", "true_turn_date": "1999-01-01"},
    ]
    table = rp.turn_lag_table(records, turns)
    assert table["n_turns_total"] == 2
    assert table["n_reconstructable"] == 1
    assert table["dropped_turns"] == [{"turn_id": "unreconstructable-turn",
                                        "reason": "primary_input_coverage_below_threshold_in_search_window"}]
    assert table["n_scored_for_median"] == 1
    assert table["median_confirmed_lag_days"] is not None


def test_turn_lag_table_median_of_multiple_reconstructable_turns():
    dates = _dates("2026-01-01", 20)
    directions = {d: "rising" for d in dates[:10]}
    directions.update({d: "falling" for d in dates[10:]})
    records = _growth_records(directions)
    turns = [
        {"id": "a", "axis": "growth", "direction": "falling", "true_turn_date": dates[8]},   # lag +2
        {"id": "b", "axis": "growth", "direction": "falling", "true_turn_date": dates[10]},  # lag  0
    ]
    table = rp.turn_lag_table(records, turns)
    lags = sorted(t["confirmed_lag_days"] for t in table["per_turn"])
    assert lags == [0, 2]
    assert table["median_confirmed_lag_days"] == 1.0  # median of [0, 2]


# --- false_flip_rate -------------------------------------------------------------

def test_false_flip_rate_classifies_associated_vs_false():
    dates = _dates("2026-01-01", 30)
    # One flip near a registered turn (associated), one flip far from any turn (false).
    directions = {d: "rising" for d in dates[:10]}
    directions.update({d: "falling" for d in dates[10:20]})   # flip at dates[10] -- ASSOCIATED
    directions.update({d: "rising" for d in dates[20:]})      # flip at dates[20] -- FALSE
    records = _growth_records(directions)
    turns = [{"id": "t1", "axis": "growth", "direction": "falling", "true_turn_date": dates[10]}]

    result = rp.false_flip_rate(records, "growth", turns, window_before_days=2, window_after_days=2)
    assert result["total_flips"] == 2
    assert result["associated_flips"] == 1
    assert result["false_flips"] == 1
    assert dates[20] in result["false_flip_dates"]
    assert result["false_flips_per_year"] is not None


def test_false_flip_rate_zero_flips_when_direction_constant():
    dates = _dates("2026-01-01", 10)
    records = _growth_records({d: "rising" for d in dates})
    result = rp.false_flip_rate(records, "growth", [])
    assert result["total_flips"] == 0
    assert result["false_flips"] == 0


def test_false_flip_rate_only_scores_turns_for_the_requested_axis():
    """An inflation turn near a growth flip must NOT count as 'associated'
    for the growth axis's false-flip scoring -- axes are scored
    independently."""
    dates = _dates("2026-01-01", 10)
    directions = {d: "rising" for d in dates[:5]}
    directions.update({d: "falling" for d in dates[5:]})
    records = _growth_records(directions)
    turns = [{"id": "infl-turn", "axis": "inflation", "direction": "falling", "true_turn_date": dates[5]}]
    result = rp.false_flip_rate(records, "growth", turns, window_before_days=2, window_after_days=2)
    assert result["false_flips"] == 1   # the growth flip is NOT covered by an inflation-tagged turn


# --- the committed regime-turns.json is well-formed ------------------------------

def test_committed_regime_turns_file_loads_and_has_expected_shape():
    turns = rp.load_turns()
    assert len(turns) >= 3
    for t in turns:
        assert t["axis"] in ("growth", "inflation")
        assert t["direction"] in ("rising", "falling")
        date.fromisoformat(t["true_turn_date"])   # must parse
        assert t.get("source")
    ids = {t["id"] for t in turns}
    assert "2007-08-financial-crisis" in ids
    assert "2020-covid-crash" in ids
    assert "2020-21-reflation" in ids
