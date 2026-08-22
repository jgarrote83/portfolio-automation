"""Unit tests for the deterministic quadrant axes (collector handler).

Covers _build_growth_axis (GDPNow within-quarter vintage slope + cross-quarter
fallback), _build_inflation_axis (realized-core trend + oil-keyed energy overlay,
NOT the news-shock level), and _build_regime_gate. These remove the LLM discretion
that previously let the quadrant call anchor on its prior label. Run:
    PYTHONPATH=src pytest tests/test_axis_signals.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import (  # noqa: E402
    _build_growth_axis,
    _build_inflation_axis,
    _build_policy_axis,
    _build_regime_gate,
    _confirm_axis_direction,
    _gdpnow_vintage_rows,
)


def _obs(values):
    """FRED-shaped rows (newest-first) from a newest-first list of values."""
    return [{"value": str(v)} for v in values]


def _vintages(values, q="2026-04-01"):
    """GDPNOW_VINTAGES rows (oldest-first) for the quarter starting at ``q``."""
    return [{"date": q, "asof": f"{q[:8]}{i:02d}", "value": str(v)}
            for i, v in enumerate(values, start=1)]


def _monthly_index(latest_yoy_pct, ann3_pct, n=18):
    """Synthesize a newest-first monthly index series with a target YoY and 3m-annualized.

    index[0]=latest=100; index[3] set so (100/index[3])**4-1 == ann3; index[12] set so
    (100/index[12]) - 1 == yoy. Other months filled by linear interpolation (only
    indices 0/3/12 are read by the builder)."""
    idx = [None] * n
    idx[0] = 100.0
    idx[3] = 100.0 / ((1 + ann3_pct / 100.0) ** 0.25)
    idx[12] = 100.0 / (1 + latest_yoy_pct / 100.0)
    # fill remaining so list is all floats (builder only reads 0,3,12 but be safe)
    for i in range(n):
        if idx[i] is None:
            idx[i] = 99.0
    return _obs(idx)


# --- growth axis -------------------------------------------------------------

def test_growth_falling_from_within_quarter_vintages():
    """The Q2 nowcast marked down 3.70 -> 2.54 is FALLING, even though Q/Q rose."""
    md = {
        "GDPNOW_VINTAGES": _vintages([3.70, 3.99, 4.26, 3.82, 3.02, 2.54]),
        "GDPNOW": _obs([2.54, 1.24]),  # cross-quarter would say 'rising' — must NOT win
    }
    g = _build_growth_axis(md)
    assert g["direction"] == "falling"
    assert g["confidence"] == "high"
    assert g["basis"] == "within_quarter_vintages"


def test_growth_rising_within_quarter():
    md = {"GDPNOW_VINTAGES": _vintages([1.0, 1.4, 2.0, 2.6])}
    assert _build_growth_axis(md)["direction"] == "rising"


def test_growth_flat_within_band():
    md = {"GDPNOW_VINTAGES": _vintages([2.50, 2.55, 2.45, 2.52])}
    assert _build_growth_axis(md)["direction"] == "flat"


def test_growth_cross_quarter_fallback_low_confidence():
    """<3 vintages -> fall back to quarterly slope, flagged low confidence."""
    md = {"GDPNOW_VINTAGES": _vintages([2.5]), "GDPNOW": _obs([2.54, 1.24])}
    g = _build_growth_axis(md)
    assert g["direction"] == "rising"
    assert g["confidence"] == "low"
    assert g["basis"] == "cross_quarter_fallback"


def test_growth_indeterminate_no_data():
    g = _build_growth_axis({})
    assert g["direction"] == "indeterminate"
    assert g["confidence"] == "none"


# --- growth axis: quarter-boundary splice (FOLLOWUPS #15) ---------------------

def test_growth_prior_tail_when_new_quarter_empty():
    """Day 1-3 of a new quarter: zero current vintages, but the just-ended quarter's
    trajectory is in the window — read its tail, NOT the cross-quarter fallback
    (which here would falsely say 'rising') and NEVER an empty trajectory."""
    md = {
        "GDPNOW_VINTAGES": [],
        "GDPNOW_VINTAGES_PRIOR": _vintages([3.70, 3.99, 4.26, 3.82, 3.02, 2.54]),
        "GDPNOW": _obs([2.54, 1.24]),  # cross-quarter says 'rising' — must NOT win
    }
    g = _build_growth_axis(md)
    assert g["direction"] == "falling"
    assert g["confidence"] == "medium"
    assert g["basis"] == "prior_quarter_tail"
    assert g["gdpnow_trajectory"]  # non-empty while FRED has vintages
    assert g["gdpnow_latest"] == 2.54


def test_growth_prior_tail_with_one_and_two_current_vintages():
    """1 or 2 current-quarter vintages still splice to the prior tail (need >=3)."""
    prior = _vintages([1.0, 1.2, 1.5, 1.8, 2.2, 2.6])
    for cur in ([2.9], [2.9, 3.0]):
        md = {
            "GDPNOW_VINTAGES": _vintages(cur, q="2026-07-01"),
            "GDPNOW_VINTAGES_PRIOR": prior,
        }
        g = _build_growth_axis(md)
        assert g["basis"] == "prior_quarter_tail"
        assert g["confidence"] == "medium"
        assert g["direction"] == "rising"   # the prior tail slope, not the new prints
        assert str(len(cur)) in g["note"]


def test_growth_prior_tail_reads_recent_slope_not_whole_quarter():
    """The tail (last 6 vintages) governs: a quarter that rose early but is being
    marked down late must read 'falling'."""
    md = {"GDPNOW_VINTAGES_PRIOR": _vintages(
        [1.0, 2.0, 3.0, 4.0, 4.3, 4.2, 4.0, 3.7, 3.4, 3.1])}
    g = _build_growth_axis(md)
    assert g["direction"] == "falling"
    assert g["gdpnow_trajectory"] == [4.3, 4.2, 4.0, 3.7, 3.4, 3.1]


def test_growth_current_quarter_wins_over_prior():
    """>=3 current vintages -> unchanged behavior; the prior trajectory is ignored."""
    md = {
        "GDPNOW_VINTAGES": _vintages([1.0, 1.4, 2.0, 2.6], q="2026-07-01"),
        "GDPNOW_VINTAGES_PRIOR": _vintages([4.0, 3.0, 2.0]),  # falling — must NOT win
    }
    g = _build_growth_axis(md)
    assert g["direction"] == "rising"
    assert g["confidence"] == "high"
    assert g["basis"] == "within_quarter_vintages"


def test_growth_fallback_when_both_quarters_thin():
    """<3 vintages in BOTH quarters -> existing cross-quarter fallback path."""
    md = {
        "GDPNOW_VINTAGES": _vintages([2.9], q="2026-07-01"),
        "GDPNOW_VINTAGES_PRIOR": _vintages([2.5, 2.6]),
        "GDPNOW": _obs([2.54, 1.24]),
    }
    g = _build_growth_axis(md)
    assert g["basis"] == "cross_quarter_fallback"
    assert g["confidence"] == "low"


def test_gdpnow_vintage_rows_split_by_observation_date():
    """The fetch-side helper splits one ALFRED response into per-quarter rows and
    drops FRED's '.' placeholders."""
    rows = [
        {"date": "2026-04-01", "realtime_start": "2026-06-27", "value": "2.5"},
        {"date": "2026-04-01", "realtime_start": "2026-06-30", "value": "2.6"},
        {"date": "2026-07-01", "realtime_start": "2026-07-17", "value": "2.9"},
        {"date": "2026-07-01", "realtime_start": "2026-07-18", "value": "."},
    ]
    cur = _gdpnow_vintage_rows(rows, "2026-07-01")
    pri = _gdpnow_vintage_rows(rows, "2026-04-01")
    assert [r["value"] for r in cur] == ["2.9"]
    assert cur[0]["asof"] == "2026-07-17"
    assert [r["value"] for r in pri] == ["2.5", "2.6"]
    assert _gdpnow_vintage_rows(None, "2026-07-01") == []


# --- growth axis: rollover detection (FOLLOWUPS #54, 2026-08-21) -------------
# Head-to-tail slope alone cannot see an interior peak/trough — a trajectory
# that has already turned over reads as if it were still moving in its
# original direction. Peak-drawdown detection (NOT the terminal-2/3-agreement
# candidate recorded in FOLLOWUPS #54, which is verified inadequate below)
# reclassifies a rolled-over "rising"/"falling" read as "flat".

def test_rollover_case_a_deferred_08_03_04_q2_tail():
    """Live evidence #1 from FOLLOWUPS #54: peaked at 1.74, falling for 3
    straight vintages by the end — head-to-tail alone reads 'rising'."""
    md = {"GDPNOW_VINTAGES": _vintages([1.36, 1.50, 1.62, 1.74, 1.68, 1.58, 1.54])}
    g = _build_growth_axis(md)
    assert g["direction"] == "flat"
    assert g["rollover"]["detected"] is True
    assert g["rollover"]["peak_value"] == 1.74
    assert g["rollover"]["peak_drawdown"] == 0.2
    assert g["rollover"]["head_to_tail_direction"] == "rising"


def test_rollover_case_b_deferred_08_05_q3_tail_terminal_3_candidate_fails():
    """Live evidence #2 from FOLLOWUPS #54: only 3 vintages, so the terminal-3
    candidate recorded in that entry IS the whole trajectory (identical to
    head-to-tail by construction) and would MISS this rollover entirely.
    Peak-drawdown catches it because it doesn't need a minimum segment length
    beyond 2."""
    md = {"GDPNOW_VINTAGES": _vintages([4.95, 6.18, 5.86])}
    g = _build_growth_axis(md)
    assert g["direction"] == "flat"
    assert g["rollover"]["detected"] is True
    assert g["rollover"]["peak_value"] == 6.18
    assert round(g["rollover"]["peak_drawdown"], 2) == 0.32
    # The terminal-3 read (over the whole 3-point trajectory) is identical to
    # head-to-tail -- proving the #54 candidate would have missed this case.
    assert g["rollover"]["terminal_3_direction"] == g["rollover"]["head_to_tail_direction"] == "rising"


def test_rollover_control_genuinely_rising_monotonic():
    md = {"GDPNOW_VINTAGES": _vintages([1.0, 1.2, 1.4, 1.6, 1.8, 2.0])}
    g = _build_growth_axis(md)
    assert g["direction"] == "rising"
    assert g["rollover"]["detected"] is False
    assert g["rollover"]["peak_drawdown"] == 0.0


def test_rollover_control_noisy_but_rising_peak_stays_at_the_end():
    """A dip mid-trajectory that the series recovers ABOVE by the final
    vintage -- the peak is still the LAST point, so no rollover, no false
    positive from ordinary noise."""
    md = {"GDPNOW_VINTAGES": _vintages([1.0, 1.3, 1.2, 1.5, 1.4, 1.7])}
    g = _build_growth_axis(md)
    assert g["direction"] == "rising"
    assert g["rollover"]["detected"] is False


def test_rollover_control_trivial_wobble_at_peak_below_threshold():
    """A peak that IS before the final vintage, but the drawdown from it is
    below the band -- must NOT fire (this is exactly the case a stricter
    terminal-agreement rule would have false-positived on)."""
    md = {"GDPNOW_VINTAGES": _vintages([1.0, 1.3, 1.6, 1.65, 1.60])}
    g = _build_growth_axis(md)
    assert g["direction"] == "rising"
    assert g["rollover"]["detected"] is False
    assert round(g["rollover"]["peak_drawdown"], 2) == 0.05


def test_rollover_symmetric_trough_case_flips_falling_to_flat():
    """H-2: the symmetric case on the falling side -- bottomed and turning up
    must not still assert 'falling'."""
    md = {"GDPNOW_VINTAGES": _vintages([6.0, 5.5, 5.0, 4.6, 4.8, 5.0, 5.3])}
    g = _build_growth_axis(md)
    assert g["direction"] == "flat"
    assert g["rollover"]["detected"] is True
    assert g["rollover"]["head_to_tail_direction"] == "falling"
    assert round(g["rollover"]["peak_drawdown"], 2) == 0.7  # rise from the trough


def test_rollover_applies_to_prior_quarter_tail_used_not_full_traj():
    """prior_quarter_tail already slices `used` to the tail before this
    function ever sees it -- confirm rollover reads THAT slice, not the
    whole `GDPNOW_VINTAGES_PRIOR` trajectory (which may contain an earlier,
    irrelevant peak outside the tail window)."""
    # Full prior trajectory has an early peak far outside the 6-vintage tail;
    # the tail itself is monotonically falling -- no rollover should fire
    # from the earlier, out-of-window peak.
    md = {"GDPNOW_VINTAGES_PRIOR": _vintages(
        [9.0, 1.0, 2.0, 3.0, 4.0, 4.3, 4.2, 4.0, 3.7, 3.4, 3.1])}
    g = _build_growth_axis(md)
    assert g["basis"] == "prior_quarter_tail"
    assert g["direction"] == "falling"
    assert g["rollover"]["detected"] is False


def test_rollover_cross_quarter_fallback_two_element_used_no_crash():
    """cross_quarter_fallback: `used` must be the actual [first, last] pair
    driving the direction call (not stale leftover `traj`), and a bare
    2-element trajectory must not crash the rollover detector."""
    md = {"GDPNOW_VINTAGES": _vintages([2.5]), "GDPNOW": _obs([2.54, 1.24])}
    g = _build_growth_axis(md)
    assert g["basis"] == "cross_quarter_fallback"
    assert g["direction"] == "rising"
    assert g["rollover"]["detected"] is False
    assert g["gdpnow_trajectory"] == [1.24, 2.54]


def test_rollover_flat_head_to_tail_never_computes_a_false_rollover():
    md = {"GDPNOW_VINTAGES": _vintages([2.50, 2.55, 2.45, 2.52])}
    g = _build_growth_axis(md)
    assert g["direction"] == "flat"
    assert g["rollover"]["detected"] is False


def test_rollover_induced_flat_still_gated_by_n2_confirmation_hysteresis():
    """A rollover changes only `_build_growth_axis`'s raw `direction` output --
    it must NOT bypass, alter, or special-case `_confirm_axis_direction`'s
    existing N=2 hysteresis. Simulate the exact `run()` wiring: raw 'rising'
    was confirmed for a while (state persisted), then the trajectory rolls
    over to a raw 'flat' -- the CONFIRMED direction must still read 'rising'
    for one more run before flipping."""
    md = {"GDPNOW_VINTAGES": _vintages([1.36, 1.50, 1.62, 1.74, 1.68, 1.58, 1.54])}
    g = _build_growth_axis(md)
    assert g["direction"] == "flat"  # the raw read, post-rollover-fix

    prev_state = {
        "raw_direction": "rising", "confirmed_direction": "rising",
        "raw_streak": 5, "confirmed_as_of": "2026-08-19",
    }
    first_confirm = _confirm_axis_direction(g["direction"], prev_state, "2026-08-20")
    assert first_confirm["direction"] == "rising"  # still cushioned -- 1st raw flat
    assert first_confirm["direction_pending"] is True
    assert first_confirm["raw_streak"] == 1

    second_confirm = _confirm_axis_direction(
        g["direction"],
        {
            "raw_direction": first_confirm["raw_direction"],
            "confirmed_direction": first_confirm["direction"],
            "raw_streak": first_confirm["raw_streak"],
            "confirmed_as_of": first_confirm["confirmed_as_of"],
        },
        "2026-08-21",
    )
    assert second_confirm["direction"] == "flat"  # 2nd consecutive raw flat -- now confirmed
    assert second_confirm["direction_pending"] is False


def test_rollover_diagnostics_present_regardless_of_detection():
    """The rollover block is always emitted (auditability), not only when it
    fires."""
    md = {"GDPNOW_VINTAGES": _vintages([1.0, 1.2, 1.4, 1.6])}
    g = _build_growth_axis(md)
    r = g["rollover"]
    assert set(r) >= {
        "detected", "peak_value", "peak_index", "peak_asof", "peak_drawdown",
        "band", "head_to_tail_direction", "terminal_2_direction", "terminal_3_direction",
    }


# --- inflation axis ----------------------------------------------------------

def test_inflation_flat_sticky_core():
    """core PCE 3m-ann ~ YoY -> flat, even with no oil/headline pressure."""
    md = {"PCEPILFE": _monthly_index(3.41, 3.52), "CPILFESL": _monthly_index(2.96, 3.17)}
    i = _build_inflation_axis(md)
    assert i["direction"] == "flat"


def test_inflation_falling_core_decelerating():
    md = {"PCEPILFE": _monthly_index(3.40, 2.10)}
    assert _build_inflation_axis(md)["direction"] == "falling"


def test_inflation_headline_hot_but_oil_collapsing_classifies_by_core():
    """The key bug fix: headline 4.27% rising must NOT force 'rising' when oil is
    collapsing — that headline is a rear-view oil artifact. Classify by core (flat)."""
    md = {
        "CPIAUCSL": _obs([100.0] + [99.0, 99.0] + [99.0] * 9 + [100.0 / 1.0427, 100.0 / 1.0395] + [99.0] * 4),
        "PCEPILFE": _monthly_index(3.41, 3.52),
        "DCOILWTICO": _obs([78.94] + [99.0] * 19 + [100.20] + [99.0] * 5),   # -21% over 20d
        "DCOILBRENTEU": _obs([76.49] + [99.0] * 19 + [106.90] + [99.0] * 5),  # -28% over 20d
    }
    i = _build_inflation_axis(md)
    assert i["direction"] != "rising"
    assert i["oil_wti_20d_pct"] is not None and i["oil_wti_20d_pct"] < -10


def test_inflation_rising_when_headline_hot_and_oil_rising():
    """Genuine energy push: headline elevated+rising AND oil rising -> 'rising'."""
    # headline YoY ~4.3% and rising: idx[0]=100, idx[12]=100/1.043; prev higher base
    head = [100.0, 99.5] + [99.0] * 10 + [100.0 / 1.043, 100.0 / 1.041] + [99.0] * 4
    md = {
        "CPIAUCSL": _obs(head),
        "PCEPILFE": _monthly_index(3.41, 3.52),
        "DCOILWTICO": _obs([120.0] + [99.0] * 19 + [100.0] + [99.0] * 5),   # +20% over 20d
    }
    i = _build_inflation_axis(md)
    assert i["direction"] == "rising"
    assert "energy" in i["reason"]


# --- O1 (2026-08-06 audit): non-binding breakeven bridge ----------------------

def _be_rows(latest_pct, delta_20d_bp, n=25):
    """Newest-first breakeven rows (in percentage points): [0]=latest,
    [20]=latest - delta_20d_bp/100 (so the 20d bp delta comes out exactly)."""
    day20 = latest_pct - delta_20d_bp / 100.0
    vals = [day20] * n
    vals[0] = latest_pct
    return _obs(vals)


def test_bridge_direction_falling_while_core_stale_direction_unaffected():
    """Stale/insufficient core (no CPILFESL/PCEPILFE at all) keeps `direction`
    indeterminate — the bridge must NEVER override it, even though breakevens
    themselves are unambiguously falling."""
    md = {"T5YIFR": _be_rows(2.00, -25.0)}   # -25bp over 20d -> falling
    i = _build_inflation_axis(md)
    assert i["direction"] == "indeterminate"
    assert i["bridge_direction"] == "falling"
    assert i["bridge_basis"] == "breakeven_5y5y"
    assert "non-binding" in i["bridge_note"].lower() or "NON-BINDING" in i["bridge_note"]


def test_bridge_direction_populated_and_labeled_even_when_core_governs():
    """Core still governs `direction`; the bridge is populated alongside it,
    clearly labeled, and does not change the core-driven call."""
    md = {"PCEPILFE": _monthly_index(3.41, 2.10), "T5YIFR": _be_rows(2.30, 20.0)}
    i = _build_inflation_axis(md)
    assert i["direction"] == "falling"   # core-driven, unaffected by rising breakevens
    assert i["bridge_direction"] == "rising"
    assert i["bridge_delta_20d_bp"] == 20.0


def test_bridge_direction_flat_inside_threshold():
    md = {"T5YIFR": _be_rows(2.10, 5.0)}   # 5bp < the 15bp default threshold
    i = _build_inflation_axis(md)
    assert i["bridge_direction"] == "flat"


def test_bridge_direction_none_when_no_breakeven_history():
    i = _build_inflation_axis({})
    assert i["bridge_direction"] is None
    assert i["bridge_basis"] is None


def test_bridge_falls_back_to_5y_when_5y5y_unavailable():
    md = {"T5YIE": _be_rows(2.40, -18.0)}
    i = _build_inflation_axis(md)
    assert i["bridge_basis"] == "breakeven_5y"
    assert i["bridge_direction"] == "falling"


# --- O2 (2026-08-06 audit): fresher oil-proxy trend source --------------------

def _oil_proxy_cache(latest, pct_20d, n=25):
    """{date: close} cache (e.g. USO) with an exact 20-trading-day % change."""
    day20 = latest / (1 + pct_20d / 100.0)
    cache = {f"2026-06-{(1 + i):02d}": day20 for i in range(n)}
    cache["2026-07-01"] = latest   # newest date, sorts first
    return cache


def test_fresh_oil_proxy_used_when_available_headline_hot_rising():
    """A fresh USO-proxy trend (rising) drives the headline_hot+oil_rising
    energy-push classification even when FRED's own series would show nothing
    (empty here) — the overlay RULE (price trend, not news) is unchanged."""
    head = [100.0, 99.5] + [99.0] * 10 + [100.0 / 1.043, 100.0 / 1.041] + [99.0] * 4
    md = {"CPIAUCSL": _obs(head), "PCEPILFE": _monthly_index(3.41, 3.52)}
    cache = _oil_proxy_cache(100.0, 20.0)   # +20% over 20d -> rising
    i = _build_inflation_axis(md, cache)
    assert i["oil_trend_source"] == "USO_proxy"
    assert i["oil_proxy_20d_pct"] == 20.0
    assert i["direction"] == "rising"
    assert "energy" in i["reason"]


def test_falls_back_to_fred_when_proxy_cache_thin():
    """Fewer than 21 days of proxy history -> fall back to FRED (unchanged
    behavior), not a crash or a false read."""
    md = {"PCEPILFE": _monthly_index(3.41, 3.52),
          "DCOILWTICO": _obs([120.0] + [99.0] * 19 + [100.0] + [99.0] * 5)}
    thin_cache = {"2026-07-01": 100.0, "2026-06-30": 99.0}   # only 2 days
    i = _build_inflation_axis(md, thin_cache)
    assert i["oil_trend_source"] == "fred_futures"
    assert i["oil_proxy_20d_pct"] is None
    assert i["oil_wti_20d_pct"] is not None


def test_no_proxy_cache_defaults_to_fred():
    md = {"DCOILWTICO": _obs([78.94] + [99.0] * 19 + [100.20] + [99.0] * 5)}
    i = _build_inflation_axis(md)
    assert i["oil_trend_source"] == "fred_futures"
    assert i["oil_proxy_as_of"] is None


# --- inflation-quality diagnostics (FOLLOWUPS #19, 2026-08-21, Task B) --------
# Diagnostics only -- none of these govern `direction`, and (hard constraint)
# none feed transition_watch confirmation (see
# tests/test_transition_watch_confirmation_sources.py for the regression
# guard that confirmations_of stays 3, not 4).

def _dated_obs(pairs):
    """Newest-first FRED-shaped rows with real dates: pairs = [(date, value), ...]."""
    return [{"date": d, "value": str(v)} for d, v in pairs]


_IQ_TODAY = "2026-08-21"


def test_inflation_quality_all_four_series_present_with_as_of_and_direction():
    md = {
        "CORESTICKM159SFRBATL": _dated_obs([("2026-08-01", 4.30), ("2026-07-01", 4.10)]),
        "FLEXCPIM159SFRBATL":   _dated_obs([("2026-08-01", 2.00), ("2026-07-01", 2.30)]),
        "PCETRIM12M159SFRBDAL": _dated_obs([("2026-08-01", 2.90), ("2026-07-01", 2.92)]),
        "MICH":                 _dated_obs([("2026-08-01", 3.20), ("2026-07-01", 3.20)]),
    }
    i = _build_inflation_axis(md, today=_IQ_TODAY)
    q = i["quality"]
    assert set(q) == {
        "sticky_core_cpi_yoy", "flexible_cpi_yoy", "trimmed_mean_pce_yoy", "umich_1y_expectations",
    }
    assert q["sticky_core_cpi_yoy"]["as_of"] == "2026-08-01"
    assert q["sticky_core_cpi_yoy"]["direction"] == "rising"    # 4.30 vs 4.10, +0.20 > band
    assert q["flexible_cpi_yoy"]["direction"] == "falling"      # 2.00 vs 2.30, -0.30 < -band
    assert q["trimmed_mean_pce_yoy"]["direction"] == "flat"     # 2.90 vs 2.92, within band
    assert q["umich_1y_expectations"]["direction"] == "flat"    # unchanged
    for label in q:
        assert q[label]["stale"] is False


def test_inflation_quality_stale_series_degrades_to_indeterminate():
    """An as_of far outside the 45d monthly freshness threshold must degrade
    `direction` to 'indeterminate' -- never a fabricated rising/falling read
    off a print too old to trust."""
    md = {"MICH": _dated_obs([("2026-01-01", 5.0), ("2025-12-01", 3.0)])}
    i = _build_inflation_axis(md, today=_IQ_TODAY)
    q = i["quality"]["umich_1y_expectations"]
    assert q["stale"] is True
    assert q["direction"] == "indeterminate"


def test_inflation_quality_missing_series_degrades_to_indeterminate_never_crashes():
    i = _build_inflation_axis({}, today=_IQ_TODAY)
    for label, row in i["quality"].items():
        assert row["value"] is None
        assert row["direction"] == "indeterminate"


def test_inflation_quality_single_print_insufficient_for_direction():
    """Only one valid print (no prior to compare) -> indeterminate, not a
    fabricated flat/rising/falling from nothing."""
    md = {"CORESTICKM159SFRBATL": _dated_obs([("2026-08-01", 4.0)])}
    i = _build_inflation_axis(md, today=_IQ_TODAY)
    q = i["quality"]["sticky_core_cpi_yoy"]
    assert q["value"] == 4.0
    assert q["direction"] == "indeterminate"


def test_inflation_quality_never_governs_the_axis_direction():
    """Sanity: `quality` readings must have zero effect on the realized-core
    -governed `direction` -- identical core inputs, wildly different quality
    readings, same direction."""
    core = {"PCEPILFE": _monthly_index(3.40, 2.10)}
    baseline = _build_inflation_axis(core, today=_IQ_TODAY)
    with_quality = _build_inflation_axis({
        **core,
        "CORESTICKM159SFRBATL": _dated_obs([("2026-08-01", 9.0), ("2026-07-01", 0.5)]),
    }, today=_IQ_TODAY)
    assert baseline["direction"] == with_quality["direction"] == "falling"


# --- policy axis (FOLLOWUPS #16) ----------------------------------------------

_TODAY = "2026-07-03"


def _dgs2(latest, day20, n=30):
    """Newest-first DGS2 rows: [0]=latest, [20]=day20, filler elsewhere."""
    vals = [day20] * n
    vals[0] = latest
    return _obs(vals)


def _policy(md, manual=None, cfg=None, today=_TODAY):
    return _build_policy_axis(md, manual or {}, cfg or {}, today)


def test_policy_market_implied_hawkish_at_threshold():
    """DGS2 +20bp/20d exactly meets the hawkish bar (inclusive)."""
    p = _policy({"DGS2": _dgs2(4.50, 4.30), "DFF": _obs([4.33])})
    assert p["stance"] == "hawkish"
    assert p["source"] == "market_implied"
    assert p["market_implied"]["dgs2_delta_20d_bp"] == 20.0
    assert p["market_implied"]["spread_bp"] == 17.0


def test_policy_market_implied_dovish_at_threshold():
    p = _policy({"DGS2": _dgs2(4.10, 4.30), "DFF": _obs([4.33])})
    assert p["stance"] == "dovish"
    assert p["market_implied"]["dgs2_delta_20d_bp"] == -20.0


def test_policy_market_implied_neutral_inside_band():
    """+19.9bp / -19.9bp stay neutral — the band is open below the thresholds."""
    for latest in (4.499, 4.101):
        p = _policy({"DGS2": _dgs2(latest, 4.30)})
        assert p["stance"] == "neutral", latest
        assert p["source"] == "market_implied"


def test_policy_market_implied_needs_21_obs():
    """20 DGS2 observations are not enough for the 20d delta -> unconfirmed."""
    p = _policy({"DGS2": _obs([4.5] * 20)})
    assert p["stance"] == "unconfirmed"
    assert p["source"] == "unconfirmed"
    assert p["market_implied"]["stance"] is None
    assert p["market_implied"]["dgs2_delta_20d_bp"] is None


def test_policy_manual_fresh_wins_over_market():
    """A fresh SEP/dot-plot stance governs even when the market proxy disagrees."""
    p = _policy(
        {"DGS2": _dgs2(4.50, 4.30)},   # market-implied: hawkish
        manual={"stance": "neutral", "as_of": "2026-06-18"},   # 15d old, fresh
    )
    assert p["stance"] == "neutral"
    assert p["source"] == "manual_fresh"
    assert p["manual"]["fresh"] is True
    assert p["agreement"] is False
    assert "DISAGREEMENT" in p["note"]


def test_policy_manual_stale_loses_to_market():
    p = _policy(
        {"DGS2": _dgs2(4.50, 4.30)},
        manual={"stance": "dovish", "as_of": "2026-03-01"},   # 124d old
    )
    assert p["stance"] == "hawkish"
    assert p["source"] == "market_implied"
    assert p["manual"]["fresh"] is False


def test_policy_manual_null_as_of_loses_to_market():
    """The live pathology: stance file all-null since inception -> market governs."""
    p = _policy(
        {"DGS2": _dgs2(4.35, 4.30), "DFF": _obs([4.33])},
        manual={"stance": "unconfirmed", "as_of": None},
    )
    assert p["stance"] == "neutral"
    assert p["source"] == "market_implied"


def test_policy_unconfirmed_only_when_both_missing():
    p = _policy({}, manual={"stance": "unconfirmed", "as_of": None})
    assert p["stance"] == "unconfirmed"
    assert p["source"] == "unconfirmed"
    assert p["agreement"] is None


def test_policy_agreement_true_when_layers_align():
    p = _policy(
        {"DGS2": _dgs2(4.35, 4.30)},
        manual={"stance": "neutral", "as_of": "2026-06-18"},
    )
    assert p["source"] == "manual_fresh"
    assert p["agreement"] is True
    assert "DISAGREEMENT" not in p["note"]


def test_policy_manual_fresh_days_from_config():
    """manual_fresh_days is config-driven: a 15d-old stance is stale at 10."""
    p = _policy(
        {"DGS2": _dgs2(4.35, 4.30)},
        manual={"stance": "hawkish", "as_of": "2026-06-18"},
        cfg={"policy_axis": {"manual_fresh_days": 10}},
    )
    assert p["source"] == "market_implied"
    assert p["stance"] == "neutral"


# --- regime gate -------------------------------------------------------------

def test_gate_closed_on_falling_growth():
    g = {"direction": "falling"}
    i = {"direction": "flat"}
    gate = _build_regime_gate(g, i, {"stance": "unconfirmed"})
    assert gate["status"] == "closed"
    assert any("growth" in r for r in gate["reasons"])
    assert "UNCONFIRMED" in gate["policy_note"]


def test_gate_open_only_when_all_clear():
    gate = _build_regime_gate(
        {"direction": "rising"}, {"direction": "falling"}, {"stance": "neutral"}
    )
    assert gate["status"] == "open"
    assert gate["reasons"] == []


def test_gate_closed_on_rising_inflation_and_hawkish():
    gate = _build_regime_gate(
        {"direction": "rising"}, {"direction": "rising"}, {"stance": "hawkish"}
    )
    assert gate["status"] == "closed"
    assert len(gate["reasons"]) == 2


def test_gate_consumes_resolved_policy_axis():
    """End-to-end #16: a market-implied hawkish repricing closes the gate; the
    resolved stance + source land in derived_from (the conviction proxy reads it)."""
    pa = _policy({"DGS2": _dgs2(4.50, 4.30)})
    gate = _build_regime_gate({"direction": "rising"}, {"direction": "falling"}, pa)
    assert gate["status"] == "closed"
    assert any("hawkish" in r for r in gate["reasons"])
    assert gate["derived_from"]["policy_stance"] == "hawkish"
    assert gate["derived_from"]["policy_source"] == "market_implied"


def test_gate_open_on_market_implied_neutral():
    """The #16 payoff: a null manual file no longer strands policy at unconfirmed —
    a readable DGS2 gives neutral, the gate opens, no policy_note."""
    pa = _policy(
        {"DGS2": _dgs2(4.35, 4.30)},
        manual={"stance": "unconfirmed", "as_of": None},
    )
    gate = _build_regime_gate({"direction": "rising"}, {"direction": "falling"}, pa)
    assert gate["status"] == "open"
    assert gate["policy_note"] == ""


def test_gate_unconfirmed_still_flags_policy_note():
    pa = _policy({}, manual={"stance": "unconfirmed", "as_of": None})
    gate = _build_regime_gate({"direction": "rising"}, {"direction": "falling"}, pa)
    assert gate["status"] == "open"   # unconfirmed does not hard-close (unchanged)
    assert "UNCONFIRMED" in gate["policy_note"]
