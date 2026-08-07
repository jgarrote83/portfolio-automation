"""Unit tests for the dollar_proxy fallback trigger (2026-08-06 audit B4).

Before this fix, `run()` gated the FX-pairs dollar_proxy fallback on a bare
`dxy_stale > 5`, which was dark at EXACTLY 5 days stale and dark whenever
DTWEXBGS returned zero usable observations at all (dxy_stale is None,
since `dxy_latest_date` is never set in that case) — leaving the DXY signal
blind on both the primary (regional_rotation) AND fallback paths at once.

Run: PYTHONPATH=src pytest tests/test_dollar_proxy_fallback.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _should_use_dollar_proxy  # noqa: E402


def test_fallback_fires_at_exactly_five_days_stale():
    """Boundary fix: was `> 5` (dark at exactly 5); now `>= 5`."""
    assert _should_use_dollar_proxy(5) is True


def test_fallback_fires_when_stale_unknown():
    """DTWEXBGS with zero usable observations -> dxy_stale is None -> the
    cadence can't even be evaluated, which must ALSO trigger the fallback."""
    assert _should_use_dollar_proxy(None) is True


def test_fallback_does_not_fire_when_fresh():
    assert _should_use_dollar_proxy(0) is False
    assert _should_use_dollar_proxy(4) is False


def test_fallback_fires_when_very_stale():
    assert _should_use_dollar_proxy(30) is True
