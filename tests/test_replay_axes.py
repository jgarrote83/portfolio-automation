"""ALFRED backtest harness (2026-08-21), Task C — `scripts/replay_axes.py`'s
stateful day-by-day replay.

The property under test that matters most: the N=2 confirmation hysteresis
MUST be chained forward day to day, never re-derived independently per date
(independent evaluation would silently erase the confirmation delay this
harness exists to measure). `_build_growth_axis`/`_build_inflation_axis` and
`macro_data_as_of` are monkeypatched to isolate this chaining property from
axis-math correctness (already covered by test_axis_signals.py /
test_point_in_time.py) and reconstruction correctness respectively.

Run: PYTHONPATH=src python -m pytest tests/test_replay_axes.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import replay_axes as ra  # noqa: E402


def test_business_dates_excludes_weekends():
    # 2026-04-04 is a Saturday, 2026-04-05 a Sunday, 2026-04-06 a Monday.
    dates = ra.business_dates("2026-04-03", "2026-04-06")
    assert dates == ["2026-04-03", "2026-04-06"]


def test_stateful_confirmation_lags_raw_by_n2_not_independent(monkeypatch):
    """THE headline Task C test: raw growth flips once; the confirmed
    direction must lag by exactly 2 sessions, never flip on the same day raw
    does (which is what independent per-date evaluation would produce)."""
    dates = ra.business_dates("2026-04-06", "2026-04-10")
    assert len(dates) == 5
    raw_by_date = {
        dates[0]: "rising", dates[1]: "rising", dates[2]: "falling",
        dates[3]: "falling", dates[4]: "falling",
    }

    monkeypatch.setattr(ra, "macro_data_as_of", lambda as_of, cache, q, pq: {"_as_of": as_of})
    monkeypatch.setattr(
        ra, "_build_growth_axis",
        lambda md: {"direction": raw_by_date[md["_as_of"]], "basis": "test", "confidence": "high"},
    )
    monkeypatch.setattr(ra, "_build_inflation_axis", lambda md, proxy, today: {"direction": "flat"})

    records = ra.replay("2026-04-06", "2026-04-10", cache={})
    raw = [r["growth"]["raw_direction"] for r in records]
    confirmed = [r["growth"]["direction"] for r in records]

    assert raw == ["rising", "rising", "falling", "falling", "falling"]
    # day0: cold start (D-A2) -> confirmed = raw immediately.
    # day1: raw unchanged -> confirmed unchanged.
    # day2: raw flips -> streak resets to 1 -> confirmed STAYS 'rising' (pending).
    # day3: raw falling again -> streak=2 -> confirmed FLIPS to 'falling'.
    # day4: raw falling again -> streak=3 -> stays 'falling'.
    assert confirmed == ["rising", "rising", "rising", "falling", "falling"]

    # The naive (WRONG) independent-evaluation result would be identical to
    # `raw` at every index -- confirming this test actually distinguishes the
    # two behaviors, not merely restating the input.
    assert confirmed != raw


def test_active_quadrant_uses_confirmed_direction_not_raw(monkeypatch):
    """A raw growth flip that hasn't confirmed yet must not move
    active_quadrant -- consumers read the confirmed field, and so must this
    harness's own active_quadrant computation."""
    dates = ra.business_dates("2026-04-06", "2026-04-08")
    growth_raw = {dates[0]: "rising", dates[1]: "falling", dates[2]: "falling"}

    monkeypatch.setattr(ra, "macro_data_as_of", lambda as_of, cache, q, pq: {"_as_of": as_of})
    monkeypatch.setattr(
        ra, "_build_growth_axis",
        lambda md: {"direction": growth_raw[md["_as_of"]], "basis": "test", "confidence": "high"},
    )
    monkeypatch.setattr(ra, "_build_inflation_axis", lambda md, proxy, today: {"direction": "falling"})

    records = ra.replay("2026-04-06", "2026-04-08", cache={})
    # day0: growth confirmed 'rising' (cold start) + inflation 'falling' -> Q1
    assert records[0]["active_quadrant"] == "Q1"
    # day1: growth raw flips to 'falling' but NOT yet confirmed (streak=1) ->
    # confirmed growth is STILL 'rising' -> active_quadrant STAYS Q1.
    assert records[1]["growth"]["raw_direction"] == "falling"
    assert records[1]["growth"]["direction"] == "rising"
    assert records[1]["active_quadrant"] == "Q1"
    # day2: streak=2 -> growth confirms 'falling' + inflation 'falling' -> Q4
    assert records[2]["growth"]["direction"] == "falling"
    assert records[2]["active_quadrant"] == "Q4"


def test_cold_start_first_date_adopts_raw_immediately(monkeypatch):
    monkeypatch.setattr(ra, "macro_data_as_of", lambda as_of, cache, q, pq: {})
    monkeypatch.setattr(ra, "_build_growth_axis", lambda md: {"direction": "falling"})
    monkeypatch.setattr(ra, "_build_inflation_axis", lambda md, proxy, today: {"direction": "rising"})

    records = ra.replay("2026-04-06", "2026-04-06", cache={})
    assert len(records) == 1
    assert records[0]["growth"]["direction"] == "falling"
    assert records[0]["growth"]["direction_pending"] is False
    assert records[0]["growth"]["raw_streak"] == 2


def test_rollover_block_passed_through_from_growth_axis(monkeypatch):
    rollover = {"detected": True, "peak_value": 1.74}
    monkeypatch.setattr(ra, "macro_data_as_of", lambda as_of, cache, q, pq: {})
    monkeypatch.setattr(
        ra, "_build_growth_axis",
        lambda md: {"direction": "flat", "basis": "b", "confidence": "high", "rollover": rollover},
    )
    monkeypatch.setattr(ra, "_build_inflation_axis", lambda md, proxy, today: {"direction": "flat"})

    records = ra.replay("2026-04-06", "2026-04-06", cache={})
    assert records[0]["growth"]["rollover"] == rollover


# --- reconstructability -------------------------------------------------------

def test_reconstructable_growth_true_when_any_gdpnow_key_present():
    assert ra._reconstructable_growth({"GDPNOW_VINTAGES": [{"value": "3.5"}]}) == (True, None)
    assert ra._reconstructable_growth({"GDPNOW": [{"value": "3.5"}]}) == (True, None)


def test_reconstructable_growth_false_with_reason_when_nothing_present():
    ok, reason = ra._reconstructable_growth({})
    assert ok is False
    assert reason == "no_gdpnow_data_in_cache_window"


def test_reconstructable_inflation_true_when_core_series_present():
    assert ra._reconstructable_inflation({"PCEPILFE": [{"value": "1.0"}]}) == (True, None)
    assert ra._reconstructable_inflation({"CPILFESL": [{"value": "1.0"}]}) == (True, None)


def test_reconstructable_inflation_false_with_reason_when_nothing_present():
    ok, reason = ra._reconstructable_inflation({})
    assert ok is False
    assert reason == "no_realized_core_data_in_cache_window"


def test_replay_records_carry_reconstructable_flags(monkeypatch):
    monkeypatch.setattr(ra, "macro_data_as_of", lambda as_of, cache, q, pq: {})
    monkeypatch.setattr(ra, "_build_growth_axis", lambda md: {"direction": "indeterminate"})
    monkeypatch.setattr(ra, "_build_inflation_axis", lambda md, proxy, today: {"direction": "indeterminate"})

    records = ra.replay("2026-04-06", "2026-04-06", cache={})
    assert records[0]["reconstructable"] == {"growth": False, "inflation": False}
    assert records[0]["reconstructable_reason"]["growth"] == "no_gdpnow_data_in_cache_window"
