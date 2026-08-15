"""Task B2/B3 (2026-08-14 flex-conviction-path cycle) — pure-function layer
for the flex conviction nomination path: empirical base rate, edge-over-base-
rate, the base-rate-relative ladder lookup, and the catalyst amplifier.

Run: PYTHONPATH=src pytest tests/test_flex_conviction_pure.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import (  # noqa: E402
    _base_rate_up,
    _conviction_catalyst_amplifier,
    _conviction_edge,
    _conviction_ladder_lookup,
    _RISK_LIMITS_DEFAULTS,
)

LADDER = _RISK_LIMITS_DEFAULTS["conviction"]["ladder"]


# --- _base_rate_up: fail-closed, never a fabricated 0.50 --------------------

def _closes(values: list[float]) -> dict[str, float]:
    dates = [f"2020-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(len(values))]
    return dict(zip(dates, values))


def test_base_rate_up_empty_input_is_none():
    out = _base_rate_up({}, horizon_days=20, lookback_days=504, min_windows=60)
    assert out == {"base_rate_up": None, "windows": 0}


def test_base_rate_up_insufficient_windows_is_none_never_fifty_percent():
    # Only 70 closes, horizon 20 -> 50 windows, below min_windows=60.
    closes = _closes([100.0 + i for i in range(70)])
    out = _base_rate_up(closes, horizon_days=20, lookback_days=504, min_windows=60)
    assert out["base_rate_up"] is None
    assert out["windows"] == 50


def test_base_rate_up_known_vector_all_up():
    # Strictly increasing series -> every window is "up" -> base_rate_up = 1.0.
    closes = _closes([100.0 + i for i in range(100)])
    out = _base_rate_up(closes, horizon_days=20, lookback_days=504, min_windows=60)
    assert out["windows"] == 80
    assert out["base_rate_up"] == 1.0


def test_base_rate_up_known_vector_all_down():
    closes = _closes([200.0 - i for i in range(100)])
    out = _base_rate_up(closes, horizon_days=20, lookback_days=504, min_windows=60)
    assert out["base_rate_up"] == 0.0


def test_base_rate_up_known_vector_mixed_fraction():
    # 30 sessions, horizon 10 -> 20 windows: block1 (idx 0-19) flat 100,
    # block2 (idx 20-24) drops to 90, block3 (idx 25-29) jumps to 130.
    # i=0..9:  start=100 (idx i),      end=100  (idx i+10, still block1) -> not up
    # i=10..14: start=100 (idx i),     end=90   (idx i+10, block2)       -> not up
    # i=15..19: start=100 (idx i),     end=130  (idx i+10, block3)       -> up
    # Exactly 5 of 20 windows up -> base_rate_up = 0.25, precisely known.
    values = [100.0] * 20 + [90.0] * 5 + [130.0] * 5
    closes = _closes(values)
    out = _base_rate_up(closes, horizon_days=10, lookback_days=504, min_windows=5)
    assert out["windows"] == 20
    assert abs(out["base_rate_up"] - 0.25) < 1e-9


def test_base_rate_up_respects_lookback_window_trailing_only():
    # A big crash-then-recovery far in the past, outside lookback_days, must
    # not count -- trailing=99 drops only the leading 500.0 spike, leaving a
    # monotonically non-decreasing trailing series where every window is up.
    closes = _closes([500.0] + [50.0] * 5 + [100.0 + i for i in range(94)])
    out = _base_rate_up(closes, horizon_days=20, lookback_days=99, min_windows=60)
    assert out["windows"] == 79
    assert out["base_rate_up"] == 1.0


def test_base_rate_up_zero_or_negative_horizon_is_none():
    closes = _closes([100.0 + i for i in range(100)])
    assert _base_rate_up(closes, horizon_days=0, lookback_days=504, min_windows=60) == {
        "base_rate_up": None, "windows": 0,
    }
    assert _base_rate_up(closes, horizon_days=-5, lookback_days=504, min_windows=60) == {
        "base_rate_up": None, "windows": 0,
    }


# --- _conviction_edge: clamped at 0, never negative -------------------------

def test_edge_positive_when_p_up_beats_base_rate():
    assert abs(_conviction_edge(0.70, 0.55) - 0.15) < 1e-9


def test_edge_clamped_at_zero_when_below_base_rate():
    assert _conviction_edge(0.50, 0.58) == 0.0


def test_edge_zero_at_exact_base_rate():
    assert _conviction_edge(0.55, 0.55) == 0.0


# --- _conviction_ladder_lookup: every band boundary -------------------------

def test_ladder_exact_edge_018_lands_very_high():
    mult, conviction = _conviction_ladder_lookup(LADDER, 0.18)
    assert conviction == "very_high"
    assert mult == 1.00


def test_ladder_just_below_018_lands_high():
    mult, conviction = _conviction_ladder_lookup(LADDER, 0.1799)
    assert conviction == "high"
    assert mult == 0.70


def test_ladder_exact_edge_012_lands_high():
    mult, conviction = _conviction_ladder_lookup(LADDER, 0.12)
    assert conviction == "high"


def test_ladder_exact_edge_007_lands_moderate():
    mult, conviction = _conviction_ladder_lookup(LADDER, 0.07)
    assert conviction == "moderate"
    assert mult == 0.45


def test_ladder_exact_edge_004_lands_low():
    mult, conviction = _conviction_ladder_lookup(LADDER, 0.04)
    assert conviction == "low"
    assert mult == 0.25


def test_ladder_zero_lands_none():
    mult, conviction = _conviction_ladder_lookup(LADDER, 0.0)
    assert conviction == "none"
    assert mult == 0.0


def test_ladder_negative_edge_never_reached_but_degrades_to_none():
    # _conviction_edge already clamps at 0 before this is called, but the
    # lookup itself must not crash/misbehave if ever called with a negative.
    mult, conviction = _conviction_ladder_lookup(LADDER, -0.05)
    assert conviction == "none"
    assert mult == 0.0


def test_ladder_high_edge_still_very_high_no_overshoot():
    mult, conviction = _conviction_ladder_lookup(LADDER, 0.99)
    assert conviction == "very_high"
    assert mult == 1.00


# --- _conviction_catalyst_amplifier ------------------------------------------

def test_no_catalyst_date_is_no_amplification():
    out = _conviction_catalyst_amplifier(
        size_mult=0.70, conviction="high", catalyst_date=None, horizon_days=20,
        today="2026-08-14", ladder=LADDER, catalyst_size_mult=1.5, promotes_band=True,
    )
    assert out == {"size_mult": 0.70, "conviction": "high", "amplified": False}


def test_catalyst_within_horizon_multiplies_size_mult():
    out = _conviction_catalyst_amplifier(
        size_mult=0.45, conviction="moderate", catalyst_date="2026-08-20", horizon_days=20,
        today="2026-08-14", ladder=LADDER, catalyst_size_mult=1.5, promotes_band=False,
    )
    assert out["amplified"] is True
    assert out["conviction"] == "moderate"
    assert abs(out["size_mult"] - 0.45 * 1.5) < 1e-9


def test_catalyst_beyond_horizon_no_amplification():
    out = _conviction_catalyst_amplifier(
        size_mult=0.45, conviction="moderate", catalyst_date="2026-09-30", horizon_days=20,
        today="2026-08-14", ladder=LADDER, catalyst_size_mult=1.5, promotes_band=True,
    )
    assert out == {"size_mult": 0.45, "conviction": "moderate", "amplified": False}


def test_catalyst_in_the_past_no_amplification():
    out = _conviction_catalyst_amplifier(
        size_mult=0.45, conviction="moderate", catalyst_date="2026-08-01", horizon_days=20,
        today="2026-08-14", ladder=LADDER, catalyst_size_mult=1.5, promotes_band=True,
    )
    assert out["amplified"] is False


def test_catalyst_promotes_band_uses_promoted_rung_as_base():
    # "moderate" (edge_min 0.07, size_mult 0.45) promotes to "high"
    # (size_mult 0.70), THEN multiplies by catalyst_size_mult -- not the
    # original moderate size_mult.
    out = _conviction_catalyst_amplifier(
        size_mult=0.45, conviction="moderate", catalyst_date="2026-08-15", horizon_days=20,
        today="2026-08-14", ladder=LADDER, catalyst_size_mult=1.5, promotes_band=True,
    )
    assert out["conviction"] == "high"
    assert abs(out["size_mult"] - 0.70 * 1.5) < 1e-9


def test_catalyst_promotes_band_top_rung_has_no_higher_band_to_promote_to():
    out = _conviction_catalyst_amplifier(
        size_mult=1.00, conviction="very_high", catalyst_date="2026-08-15", horizon_days=20,
        today="2026-08-14", ladder=LADDER, catalyst_size_mult=1.5, promotes_band=True,
    )
    assert out["conviction"] == "very_high"
    assert abs(out["size_mult"] - 1.00 * 1.5) < 1e-9


def test_catalyst_promotes_band_false_keeps_original_band():
    out = _conviction_catalyst_amplifier(
        size_mult=0.45, conviction="moderate", catalyst_date="2026-08-15", horizon_days=20,
        today="2026-08-14", ladder=LADDER, catalyst_size_mult=1.5, promotes_band=False,
    )
    assert out["conviction"] == "moderate"
    assert abs(out["size_mult"] - 0.45 * 1.5) < 1e-9


def test_malformed_catalyst_date_degrades_to_no_amplification():
    out = _conviction_catalyst_amplifier(
        size_mult=0.45, conviction="moderate", catalyst_date="not-a-date", horizon_days=20,
        today="2026-08-14", ladder=LADDER, catalyst_size_mult=1.5, promotes_band=True,
    )
    assert out["amplified"] is False
    assert out["size_mult"] == 0.45


def test_catalyst_exactly_on_horizon_boundary_amplifies():
    out = _conviction_catalyst_amplifier(
        size_mult=0.45, conviction="moderate", catalyst_date="2026-09-03", horizon_days=20,
        today="2026-08-14", ladder=LADDER, catalyst_size_mult=1.5, promotes_band=False,
    )
    assert out["amplified"] is True


def test_catalyst_today_amplifies():
    out = _conviction_catalyst_amplifier(
        size_mult=0.45, conviction="moderate", catalyst_date="2026-08-14", horizon_days=20,
        today="2026-08-14", ladder=LADDER, catalyst_size_mult=1.5, promotes_band=False,
    )
    assert out["amplified"] is True
