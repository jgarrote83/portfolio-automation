"""ALFRED backtest harness (2026-08-21, `feat/20260821-alfred-backtest-harness`),
Task B — `scripts/point_in_time.py`'s as-of reconstruction.

This is the harness's single most important correctness surface: get the
publication-lag / latest-vintage-wins / ordering semantics wrong and every
downstream lag number is fiction (per the harness's own doctrine — see
`scripts/replay_parity.py`). Empirical verification: probe the reconstruction
functions directly, assert on returned values; do not trust a diff read alone.

Run: PYTHONPATH=src python -m pytest tests/test_point_in_time.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from point_in_time import (  # noqa: E402
    _gdpnow_key_as_of,
    _standard_series_as_of,
    macro_data_as_of,
)


def _row(date_, rt, value):
    return {"date": date_, "realtime_start": rt, "realtime_end": "9999-12-31", "value": value}


# --- _standard_series_as_of ---------------------------------------------------

def test_row_published_after_as_of_is_excluded():
    """A row with realtime_start AFTER as_of was not knowable that day and
    must never appear in the reconstruction."""
    rows = [_row("2026-06-01", "2026-08-13", "3.50")]   # published 08-13
    out = _standard_series_as_of(rows, "2026-08-01")    # as_of BEFORE publication
    assert out == []


def test_row_published_exactly_on_as_of_is_included():
    rows = [_row("2026-06-01", "2026-08-01", "3.50")]
    out = _standard_series_as_of(rows, "2026-08-01")
    assert out == [{"date": "2026-06-01", "value": "3.50"}]


def test_two_vintages_same_observation_latest_wins():
    """Two revisions of the SAME observation date, both known by as_of -- the
    one with the greater realtime_start (the later revision) must win, never
    the first print."""
    rows = [
        _row("2026-06-01", "2026-07-01", "3.10"),   # initial print
        _row("2026-06-01", "2026-08-01", "3.50"),   # first revision
    ]
    out = _standard_series_as_of(rows, "2026-09-01")
    assert out == [{"date": "2026-06-01", "value": "3.50"}]


def test_two_vintages_only_the_earlier_is_knowable_yet():
    """Same two revisions as above, but as_of sits BETWEEN them -- only the
    initial print was knowable; the later revision must not leak backward in
    time."""
    rows = [
        _row("2026-06-01", "2026-07-01", "3.10"),
        _row("2026-06-01", "2026-08-01", "3.50"),
    ]
    out = _standard_series_as_of(rows, "2026-07-15")
    assert out == [{"date": "2026-06-01", "value": "3.10"}]


def test_payrolls_revision_reconstructs_original_value_before_revision():
    """THE headline correctness test (per the PR spec, named explicitly): a
    revised payrolls print must reconstruct to its ORIGINAL value at an as_of
    BEFORE the revision published, and to the REVISED value at an as_of AFTER
    -- this is the entire reason naive (revised-data) backtests lie."""
    payems = [
        _row("2026-05-01", "2026-06-06", "158200"),   # initial May print (BLS)
        _row("2026-05-01", "2026-07-03", "158050"),   # June revision (down)
        _row("2026-05-01", "2026-08-01", "158100"),   # July revision (up a bit)
    ]
    before = _standard_series_as_of(payems, "2026-06-20")
    assert before == [{"date": "2026-05-01", "value": "158200"}]

    between = _standard_series_as_of(payems, "2026-07-15")
    assert between == [{"date": "2026-05-01", "value": "158050"}]

    after = _standard_series_as_of(payems, "2026-09-01")
    assert after == [{"date": "2026-05-01", "value": "158100"}]

    # And the value TODAY (per get_series_latest, i.e. every row visible) is
    # the fully-revised print -- provably different from what "before" saw.
    assert before[0]["value"] != after[0]["value"]


def test_standard_series_ordered_newest_first():
    """Matches _macro_vals's documented 'Latest-first float list' contract."""
    rows = [
        _row("2026-04-01", "2026-05-01", "1.0"),
        _row("2026-05-01", "2026-06-01", "2.0"),
        _row("2026-06-01", "2026-07-01", "3.0"),
    ]
    out = _standard_series_as_of(rows, "2026-12-01")
    assert [r["date"] for r in out] == ["2026-06-01", "2026-05-01", "2026-04-01"]


def test_missing_or_dot_value_dropped_never_fabricated():
    rows = [
        _row("2026-06-01", "2026-07-01", "."),
        _row("2026-07-01", "2026-08-01", None),
        _row("2026-08-01", "2026-09-01", "4.2"),
    ]
    out = _standard_series_as_of(rows, "2026-12-01")
    assert out == [{"date": "2026-08-01", "value": "4.2"}]


def test_empty_series_degrades_to_empty_list():
    assert _standard_series_as_of([], "2026-08-01") == []
    assert _standard_series_as_of(None, "2026-08-01") == []


def test_row_missing_realtime_start_excluded_not_fabricated():
    rows = [{"date": "2026-06-01", "value": "3.5"}]   # no realtime_start at all
    assert _standard_series_as_of(rows, "2026-08-01") == []


# --- _gdpnow_key_as_of ---------------------------------------------------------

def test_gdpnow_vintage_key_ordered_oldest_first():
    """Matches _gdpnow_vintage_rows's documented oldest-first contract --
    opposite of the standard-series direction, and this MUST NOT be conflated
    (the #2.1 'two input-shape traps' the PR spec warns about)."""
    rows = [
        _row("2026-04-01", "2026-04-05", "3.70"),
        _row("2026-04-01", "2026-04-20", "3.99"),
        _row("2026-04-01", "2026-05-10", "4.26"),
    ]
    out = _gdpnow_key_as_of(rows, "2026-12-01", obs_date="2026-04-01")
    assert [r["value"] for r in out] == ["3.70", "3.99", "4.26"]
    # asof is the realtime_start, renamed exactly like production's transform
    assert out[0]["asof"] == "2026-04-05"


def test_gdpnow_vintage_key_excludes_not_yet_published_vintages():
    """The trajectory as of a date in the MIDDLE of the revision sequence
    must stop exactly there -- this is what makes the growth-axis replay
    genuinely point-in-time rather than a fully-revised trajectory read
    backward."""
    rows = [
        _row("2026-04-01", "2026-04-05", "3.70"),
        _row("2026-04-01", "2026-04-20", "3.99"),
        _row("2026-04-01", "2026-05-10", "4.26"),
        _row("2026-04-01", "2026-06-01", "3.82"),
    ]
    out = _gdpnow_key_as_of(rows, "2026-04-25", obs_date="2026-04-01")
    assert [r["value"] for r in out] == ["3.70", "3.99"]


def test_gdpnow_vintage_key_keeps_every_vintage_not_just_latest():
    """Unlike a standard series, every row for the SAME observation date is a
    genuinely distinct sequential nowcast -- the trajectory IS the sequence,
    never collapsed to 'latest wins' the way a revised CPI print is."""
    rows = [
        _row("2026-04-01", "2026-04-05", "3.70"),
        _row("2026-04-01", "2026-04-20", "3.99"),
    ]
    out = _gdpnow_key_as_of(rows, "2026-12-01", obs_date="2026-04-01")
    assert len(out) == 2   # NOT collapsed to 1


def test_gdpnow_vintage_key_filters_to_the_requested_observation_date():
    rows = [
        _row("2026-01-01", "2026-01-10", "2.50"),   # a different quarter
        _row("2026-04-01", "2026-04-05", "3.70"),
    ]
    out = _gdpnow_key_as_of(rows, "2026-12-01", obs_date="2026-04-01")
    assert [r["value"] for r in out] == ["3.70"]


def test_gdpnow_vintage_key_empty_when_no_data_yet():
    assert _gdpnow_key_as_of([], "2026-08-01", obs_date="2026-07-01") == []
    assert _gdpnow_key_as_of(None, "2026-08-01", obs_date="2026-07-01") == []


# --- macro_data_as_of (integration of the above) -------------------------------

def test_macro_data_as_of_routes_gdpnow_through_both_keys():
    cache = {
        "GDPNOW": [
            _row("2026-01-01", "2026-01-10", "2.10"),
            _row("2026-04-01", "2026-04-05", "3.70"),
            _row("2026-04-01", "2026-04-20", "3.99"),
        ],
        "CPILFESL": [_row("2026-03-01", "2026-04-01", "310.5")],
    }
    out = macro_data_as_of("2026-04-25", cache, q_start="2026-04-01", prior_q_start="2026-01-01")
    assert [r["value"] for r in out["GDPNOW_VINTAGES"]] == ["3.70", "3.99"]
    assert [r["value"] for r in out["GDPNOW_VINTAGES_PRIOR"]] == ["2.10"]
    assert out["CPILFESL"] == [{"date": "2026-03-01", "value": "310.5"}]
    # GDPNOW itself is ALSO reconstructed as a standard (newest-first) series,
    # matching production's parallel fetch of the plain quarterly series.
    assert out["GDPNOW"][0]["date"] == "2026-04-01"


def test_macro_data_as_of_missing_series_key_omitted_not_fabricated():
    out = macro_data_as_of("2026-04-25", {}, q_start="2026-04-01", prior_q_start="2026-01-01")
    assert out == {}
