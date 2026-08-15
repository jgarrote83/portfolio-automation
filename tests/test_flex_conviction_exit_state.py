"""Task E (2026-08-14 flex-conviction-path cycle) — build_flex_exit_state's
path-aware time-stop skip: the conviction path has NO calendar clock at all
(replaced entirely by the collector-side release_sessions hysteresis decay,
enforced by flex/handler.py's release-driven exit against the same-day
flex_conviction snapshot block, not tested here).

Run: PYTHONPATH=src pytest tests/test_flex_conviction_exit_state.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flex.config import FlexConfig  # noqa: E402
from flex.exit_state import build_flex_exit_state  # noqa: E402

CFG = FlexConfig()
NOW = datetime(2026, 6, 26, 15, 0)


def _daily(n=20, base=100.0, rng=2.0, v=1_000_000):
    return [{"o": base, "h": base + rng / 2, "l": base - rng / 2, "c": base, "v": v} for _ in range(n)]


def _intraday(closes, rng=0.2, v=1000):
    return [{"o": c, "h": c + rng / 2, "l": c - rng / 2, "c": c, "v": v} for c in closes]


def _entry(**kw):
    base = {
        "symbol": "AVGO", "entry_price": 100.0, "initial_stop": 91.0,
        "risk_per_share": 9.0, "qty_initial": 10, "qty_current": 10,
        "scaled_out": False, "current_stop": 91.0, "entry_date": "2026-06-01",
    }
    base.update(kw)
    return base


def test_catalyst_path_still_time_stops_after_horizon():
    r = build_flex_exit_state(
        _entry(path="catalyst"), _intraday([100] * 7), _daily(), CFG, NOW,
    )
    assert r["next_action"] == "time_stop"


def test_default_path_absent_key_still_time_stops():
    # No "path" key at all -- a pre-existing (pre-conviction-cycle) ledger row
    # must default to catalyst behavior, never accidentally skip the clock.
    r = build_flex_exit_state(_entry(), _intraday([100] * 7), _daily(), CFG, NOW)
    assert r["next_action"] == "time_stop"


def test_conviction_path_never_time_stops_regardless_of_horizon():
    r = build_flex_exit_state(
        _entry(path="conviction", entry_date="2026-01-01"),
        _intraday([100] * 7), _daily(), CFG, NOW,
    )
    assert r["next_action"] != "time_stop"


def test_conviction_path_still_trails_and_scales_out_normally():
    # R=2.5 >= first_target_r=2.0 -> scale_out still fires for a conviction
    # entry -- only the time-stop rule is path-gated, nothing else.
    r = build_flex_exit_state(
        _entry(path="conviction", entry_price=100.0, initial_stop=91.0,
               risk_per_share=4.0, current_stop=96.0),
        _intraday([110] * 7), _daily(), CFG, NOW,
    )
    assert r["next_action"] == "scale_out"
