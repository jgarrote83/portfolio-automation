"""Unit tests for axis-direction confirmation (session 2026-07-28, Task A, decision D-2).

A growth/inflation direction label change (and the market-implied policy stance)
only reaches the CONSUMED `direction`/`stance` field after persisting for >= 2
consecutive raw runs — restores the invariant that nothing acting on regime sizing
moves on a signal seen exactly once. Motivating case: the 2026-07-28 growth-axis
flip (falling -> rising) fired purely because the oldest GDPNow vintage aged out of
the six-vintage window while the newest print was actually LOWER, re-anchoring the
reference from 55.9% Q3 to 72.5% Q1+Q2 overnight on a single print.

Run: PYTHONPATH=src pytest tests/test_axis_confirmation.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import (  # noqa: E402
    _build_policy_axis,
    _build_reference_weights,
    _confirm_axis_direction,
    _growth_rolloff_diagnostics,
    _load_risk_limits,
)

RW_CFG = _load_risk_limits()


def _obs(values):
    return [{"value": str(v)} for v in values]


def _dgs2(latest, day20, n=30):
    vals = [day20] * n
    vals[0] = latest
    return _obs(vals)


# --- 1/2/3/4/5: _confirm_axis_direction ------------------------------------------

def test_raw_flip_run1_confirmed_unchanged_pending_true_streak_1():
    prev = {"raw_direction": "falling", "confirmed_direction": "falling",
            "raw_streak": 5, "confirmed_as_of": "2026-07-01"}
    result = _confirm_axis_direction("rising", prev, "2026-07-28")
    assert result["direction"] == "falling"        # confirmed unchanged
    assert result["raw_direction"] == "rising"
    assert result["direction_pending"] is True
    assert result["raw_streak"] == 1


def test_same_raw_run2_confirms_flip_pending_false_as_of_updates():
    prev = {"raw_direction": "rising", "confirmed_direction": "falling",
            "raw_streak": 1, "confirmed_as_of": "2026-07-01"}
    result = _confirm_axis_direction("rising", prev, "2026-07-29")
    assert result["direction"] == "rising"         # confirmed flips
    assert result["direction_pending"] is False
    assert result["raw_streak"] == 2
    assert result["confirmed_as_of"] == "2026-07-29"   # run-2 date


def test_raw_reverts_run2_streak_resets_confirmed_never_moved():
    # Run 1 (from test 1's scenario): raw flipped to "rising", confirmed held "falling".
    prev = {"raw_direction": "rising", "confirmed_direction": "falling",
            "raw_streak": 1, "confirmed_as_of": "2026-07-01"}
    # Run 2: raw reverts back to "falling".
    result = _confirm_axis_direction("falling", prev, "2026-07-29")
    assert result["raw_streak"] == 1               # streak resets on the reverted label
    assert result["direction"] == "falling"         # confirmed never moved off "falling"
    assert result["direction_pending"] is False


def test_flat_transition_needs_two_runs_like_any_other_change():
    prev = {"raw_direction": "rising", "confirmed_direction": "rising",
            "raw_streak": 3, "confirmed_as_of": "2026-06-01"}
    run1 = _confirm_axis_direction("flat", prev, "2026-07-28")
    assert run1["direction"] == "rising"            # still confirmed rising
    assert run1["direction_pending"] is True
    assert run1["raw_streak"] == 1
    state_after_run1 = {"raw_direction": "flat", "confirmed_direction": "rising",
                        "raw_streak": 1, "confirmed_as_of": "2026-06-01"}
    run2 = _confirm_axis_direction("flat", state_after_run1, "2026-07-29")
    assert run2["direction"] == "flat"              # now confirmed
    assert run2["direction_pending"] is False


def test_first_run_or_missing_state_confirms_raw_immediately():
    result = _confirm_axis_direction("rising", None, "2026-07-28")
    assert result["direction"] == "rising"
    assert result["raw_direction"] == "rising"
    assert result["direction_pending"] is False
    assert result["raw_streak"] == 2                # D-A2: seeded, not lagged
    assert result["confirmed_as_of"] == "2026-07-28"

    # Empty dict (a table read that returned nothing) behaves identically to None.
    result2 = _confirm_axis_direction("falling", {}, "2026-07-28")
    assert result2["direction"] == "falling"
    assert result2["direction_pending"] is False


# --- 6: rolloff acceptance case ---------------------------------------------------

def test_rolloff_acceptance_case_window_rolloff_attribution():
    """2026-07-27 -> 2026-07-28 reconstruction: the oldest vintage (2.54) ages out
    of the window while a new one is appended; the newest print itself FELL
    (1.5794 vs the prior newest 1.68) even though the raw slope flips to rising
    (first=1.19 vs last=1.5794)."""
    prior_trajectory = [2.54, 1.19, 1.36, 1.26, 1.74, 1.68]
    cur_trajectory = [1.19, 1.36, 1.26, 1.74, 1.68, 1.5794]

    diag = _growth_rolloff_diagnostics(cur_trajectory, prior_trajectory, "rising", "falling")
    assert diag["head_vintage_dropped"] is True
    assert diag["newest_vintage_delta"] < 0        # the newest print itself fell
    assert diag["attribution"] == "window_rolloff"

    # With N=2 confirmation, the confirmed direction stays "falling" (pending).
    prev = {"raw_direction": "falling", "confirmed_direction": "falling",
            "raw_streak": 6, "confirmed_as_of": "2026-07-01"}
    confirm = _confirm_axis_direction("rising", prev, "2026-07-28")
    assert confirm["direction"] == "falling"
    assert confirm["direction_pending"] is True
    assert confirm["raw_streak"] == 1


def test_rolloff_diagnostic_new_print_when_newest_supports_the_flip():
    """Control: the window slid (head dropped) BUT the newest print's own delta
    DOES support the flip direction — a genuine new print, not a rolloff artifact."""
    prior_trajectory = [2.54, 1.19, 1.36, 1.26, 1.74, 1.68]
    cur_trajectory = [1.19, 1.36, 1.26, 1.74, 1.68, 2.10]   # newest print rose
    diag = _growth_rolloff_diagnostics(cur_trajectory, prior_trajectory, "rising", "falling")
    assert diag["head_vintage_dropped"] is True
    assert diag["newest_vintage_delta"] > 0
    assert diag["attribution"] == "new_print"


def test_rolloff_diagnostic_indeterminate_when_prior_unavailable():
    diag = _growth_rolloff_diagnostics([1.0, 2.0], None, "rising", "falling")
    assert diag["attribution"] == "indeterminate"


# --- 7: policy stance confirmation + manual-fomc bypass ---------------------------

def test_policy_market_implied_flip_pending_one_run():
    """A market-implied stance flip (no fresh manual override) is pending for one
    run — `stance` holds at the prior confirmed value."""
    prev_policy_state = {"raw_direction": "neutral", "confirmed_direction": "neutral",
                         "raw_streak": 4, "confirmed_as_of": "2026-07-01"}
    p = _build_policy_axis(
        {"DGS2": _dgs2(4.50, 4.30), "DFF": _obs([4.33])},   # DGS2 delta = +20bp -> raw hawkish
        {}, {}, "2026-07-28", prev_policy_state,
    )
    assert p["raw_stance"] == "hawkish"
    assert p["stance"] == "neutral"            # confirmed stance holds
    assert p["stance_pending"] is True
    assert p["raw_streak"] == 1
    assert p["source"] == "market_implied"


def test_policy_manual_fresh_applies_same_day_bypassing_confirmation():
    prev_policy_state = {"raw_direction": "neutral", "confirmed_direction": "neutral",
                         "raw_streak": 4, "confirmed_as_of": "2026-07-01"}
    p = _build_policy_axis(
        {"DGS2": _dgs2(4.50, 4.30)},   # market-implied would be hawkish, pending
        {"stance": "dovish", "as_of": "2026-07-27"},   # fresh manual (1 day old)
        {}, "2026-07-28", prev_policy_state,
    )
    assert p["stance"] == "dovish"
    assert p["source"] == "manual_fresh"
    assert p["stance_pending"] is False
    # The market-implied confirmation still advances in the background.
    assert p["raw_stance"] == "hawkish"
    assert p["confirmed_market_implied_stance"] == "neutral"   # not yet confirmed (streak 1)


def test_policy_market_implied_confirms_on_second_run():
    prev_policy_state = {"raw_direction": "hawkish", "confirmed_direction": "neutral",
                         "raw_streak": 1, "confirmed_as_of": "2026-07-01"}
    p = _build_policy_axis(
        {"DGS2": _dgs2(4.50, 4.30), "DFF": _obs([4.33])},
        {}, {}, "2026-07-29", prev_policy_state,
    )
    assert p["stance"] == "hawkish"
    assert p["stance_pending"] is False
    assert p["raw_streak"] == 2


# --- 8: reference-weights integration — zero consumer code changes ---------------

def _paper(weights, equity=100_000.0, cash_pct=2.0):
    positions = [{"ticker": t, "qty": 1.0, "market_value": equity * w / 100.0}
                 for t, w in weights.items()]
    return {"available": True, "equity": equity, "cash": equity * cash_pct / 100.0,
            "positions": positions}


def test_reference_weights_consumes_confirmed_direction_not_raw():
    """The 2026-07-28 counterfactual: raw growth flipped to rising, but confirmed
    direction (what `_build_reference_weights` actually reads) is still falling —
    the reference must still concentrate Q3/Q4 (falling+rising), not Q1/Q2
    (rising+falling). Zero code changes in `_build_reference_weights` itself; it
    reads the SAME `direction` field name it always has."""
    confirmed_growth_axis = {
        "direction": "falling", "raw_direction": "rising", "direction_pending": True,
        "confidence": "high",
    }
    inflation_axis = {"direction": "rising"}
    paper = _paper({"SPY": 17.0, "QQQ": 14.0, "GLD": 5.0, "SGOV": 10.0})
    rw = _build_reference_weights(
        paper, confirmed_growth_axis, inflation_axis,
        {"status": "closed", "derived_from": {"policy_stance": "neutral"}},
        {"dxy_tailwind_for_intl": "neutral"}, {}, {}, {}, RW_CFG,
    )
    assert rw["active_quadrant"] == "Q3"
    assert rw["target_weights_pct"]["GLD"] > 5.0     # Q3 concentrate gets real weight
    assert rw["target_weights_pct"]["SPY"] < 1.0     # Q1 growth beta trimmed to floor
    assert rw["target_weights_pct"]["QQQ"] < 1.0
