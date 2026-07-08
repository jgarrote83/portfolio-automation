"""DayTrade Lab pattern detection (spec §4) — ORB, VWAP pullback, class/tone
rulebook, stale-print guard. Synthetic 1-min bars throughout.

Run: PYTHONPATH=src pytest tests/test_daytrade_patterns.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from daytrade.config import DayTradeConfig  # noqa: E402
from daytrade.patterns import (  # noqa: E402
    allowed_patterns,
    is_print_stale,
    opening_range,
    orb_signal,
    vwap_pullback_signal,
)

CFG = DayTradeConfig()


def _bar(o, h, lo, c, v, t="2026-07-07T13:30:00Z"):
    return {"t": t, "o": o, "h": h, "l": lo, "c": c, "v": v}


def _flat_bars(n, price=20.0, v=1000):
    return [_bar(price, price + 0.02, price - 0.02, price, v) for _ in range(n)]


# ── class / tone rulebook ────────────────────────────────────────────────────

def test_rulebook_class_d_and_unknown_never_trade():
    assert allowed_patterns("neutral", "D") == (frozenset(), False)
    assert allowed_patterns("neutral", None) == (frozenset(), False)
    assert allowed_patterns("neutral", "Z") == (frozenset(), False)


def test_rulebook_class_c_orb_only_half():
    patterns, half = allowed_patterns("neutral", "C")
    assert patterns == frozenset({"orb"}) and half


def test_rulebook_risk_off_pattern2_only_half():
    patterns, half = allowed_patterns("risk_off", "A")
    assert patterns == frozenset({"vwap_pullback"}) and half
    # risk_off × C: C forbids pullback, risk_off forbids ORB ⇒ nothing trades
    patterns, _ = allowed_patterns("risk_off", "C")
    assert patterns == frozenset()


def test_rulebook_carry_stress_empty():
    assert allowed_patterns("carry_stress", "A")[0] == frozenset()


# ── opening range + ORB ──────────────────────────────────────────────────────

def _orb_bars():
    """5 range bars (high 20.30, low 19.90, opening vol 5000) + a breakout."""
    rng = [
        _bar(20.0, 20.3, 19.9, 20.1, 5000),
        _bar(20.1, 20.25, 20.0, 20.2, 3000),
        _bar(20.2, 20.28, 20.1, 20.15, 2500),
        _bar(20.15, 20.22, 20.05, 20.1, 2000),
        _bar(20.1, 20.2, 20.05, 20.18, 2200),
    ]
    return rng


def test_opening_range_and_completeness():
    rng = opening_range(_orb_bars()[:3], 5)
    assert rng["complete"] is False
    rng = opening_range(_orb_bars(), 5)
    assert rng["complete"] and rng["high"] == 20.3 and rng["low"] == 19.9
    assert rng["opening_candle_vol"] == 5000


def test_orb_break_with_volume_and_vwap_fires():
    bars = _orb_bars() + [_bar(20.25, 20.5, 20.24, 20.45, 8000)]
    sig = orb_signal(bars, opening_range(bars, 5), CFG)
    assert sig["signal"] and sig["entry"] == 20.45
    assert sig["stop"] > 19.9          # VWAP is nearer than range low
    assert (sig["entry"] - sig["stop"]) / sig["entry"] * 100 <= CFG.max_stop_pct


def test_orb_requires_volume_confirmation():
    bars = _orb_bars() + [_bar(20.25, 20.5, 20.24, 20.45, 4000)]   # ≤ opening 5000
    sig = orb_signal(bars, opening_range(bars, 5), CFG)
    assert not sig["signal"] and sig["reason"] == "volume_not_confirming"


def test_orb_requires_breakout_and_complete_range():
    bars = _orb_bars() + [_bar(20.1, 20.28, 20.05, 20.2, 9000)]     # no break
    assert orb_signal(bars, opening_range(bars, 5), CFG)["reason"] == "no_breakout"
    part = _orb_bars()[:3]
    assert orb_signal(part, opening_range(part, 5), CFG)["reason"] == "range_incomplete"


def test_orb_stop_too_wide_skips():
    # Deep range low + VWAP far below ⇒ implied stop > 2% ⇒ skip.
    rng = [
        _bar(20.0, 20.3, 19.0, 19.2, 5000),
        _bar(19.2, 19.5, 19.0, 19.4, 3000),
        _bar(19.4, 19.8, 19.3, 19.7, 2000),
        _bar(19.7, 20.0, 19.6, 19.9, 2000),
        _bar(19.9, 20.2, 19.8, 20.1, 2000),
    ]
    bars = rng + [_bar(20.2, 20.6, 20.15, 20.5, 9000)]
    sig = orb_signal(bars, opening_range(bars, 5), CFG)
    assert not sig["signal"] and sig["reason"] == "stop_too_wide"


# ── VWAP pullback ────────────────────────────────────────────────────────────

def _drive_bars():
    """An opening drive: +1.5% above open, holding above VWAP, heavy volume."""
    return [
        _bar(20.0, 20.12, 19.99, 20.1, 9000),
        _bar(20.1, 20.22, 20.08, 20.2, 8000),
        _bar(20.2, 20.33, 20.18, 20.3, 8500),   # +1.5% ⇒ drive confirmed
    ]


def _touch(lo=20.10):
    """A light-volume pullback bar whose low pierces the running VWAP (~20.17)."""
    return _bar(20.25, 20.26, lo, 20.18, 1500)


def test_pullback_first_touch_reclaim_fires():
    bars = _drive_bars() + [_touch()] + [_bar(20.18, 20.35, 20.20, 20.32, 4000)]
    sig = vwap_pullback_signal(bars, CFG)
    assert sig["signal"] and sig["touches"] == 1
    assert sig["entry"] == 20.32
    assert sig["stop"] == 20.10        # pullback low


def test_pullback_requires_drive_and_touch():
    flat = _flat_bars(6)
    assert vwap_pullback_signal(flat, CFG)["reason"] == "no_opening_drive"
    bars = _drive_bars() + [_bar(20.3, 20.4, 20.28, 20.35, 4000)]
    assert vwap_pullback_signal(bars, CFG)["reason"] == "no_vwap_touch"


def test_pullback_heavy_volume_rejected():
    heavy = _bar(20.25, 20.26, 20.10, 20.18, 20000)   # heavier than the drive
    bars = _drive_bars() + [heavy] + [_bar(20.18, 20.35, 20.17, 20.32, 4000)]
    sig = vwap_pullback_signal(bars, CFG)
    assert not sig["signal"] and sig["reason"] == "pullback_volume_heavy"


def test_pullback_awaits_reclaim():
    bars = _drive_bars() + [_touch()]
    assert vwap_pullback_signal(bars, CFG)["reason"] == "awaiting_reclaim"
    # A next bar that does NOT clear the prior high still waits.
    bars2 = bars + [_bar(20.18, 20.2, 20.12, 20.16, 1200)]
    r = vwap_pullback_signal(bars2, CFG)
    assert r["reason"] in ("awaiting_reclaim", "not_first_touch")
    assert not r["signal"]


def test_pullback_third_touch_dead():
    bars = _drive_bars()
    for _ in range(3):   # three separate touch episodes (recovery lows hold above VWAP)
        bars = bars + [_touch()] + [_bar(20.18, 20.3, 20.22, 20.28, 3000)]
    sig = vwap_pullback_signal(bars, CFG)
    assert sig["dead"] and sig["reason"] == "third_touch_dead" and not sig["signal"]


def test_pullback_second_touch_not_tradeable():
    bars = _drive_bars() + [_touch()] + [_bar(20.18, 20.3, 20.22, 20.28, 3000)] \
        + [_touch()] + [_bar(20.18, 20.35, 20.22, 20.33, 3000)]
    sig = vwap_pullback_signal(bars, CFG)
    assert not sig["signal"] and sig["reason"] == "not_first_touch"
    assert sig["touches"] == 2


# ── stale-print guard ────────────────────────────────────────────────────────

def test_stale_print_guard():
    from datetime import datetime, timezone
    bar_open = datetime(2026, 7, 7, 13, 30, tzinfo=timezone.utc).timestamp()
    fresh_now = bar_open + 60 + 30       # 30s after bar close
    stale_now = bar_open + 60 + 90       # 90s after bar close
    bars = [_bar(20, 20.1, 19.9, 20, 100, t="2026-07-07T13:30:00Z")]
    assert is_print_stale(bars, fresh_now, 60) is False
    assert is_print_stale(bars, stale_now, 60) is True
    assert is_print_stale([], fresh_now, 60) is True
