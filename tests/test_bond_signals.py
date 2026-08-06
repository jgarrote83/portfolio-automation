"""Unit tests for _build_bond_signals' HY OAS trend_4w field (2026-08-06 audit B1).

`trend_4w` is read by both `_build_leading_growth` and `_build_market_implied_quadrant`
(as `credit.hy_oas.trend_4w`) but `_build_bond_signals` never actually set it — a
key-name mismatch that left the HY-OAS vote null in both consumers every session.

Run: PYTHONPATH=src pytest tests/test_bond_signals.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_bond_signals  # noqa: E402


def _hy_rows(latest: float, delta_20d_bp: float) -> list[dict]:
    """Newest-first HY OAS (BAMLH0A0HYM2) rows: today's value at index 0 + a
    value at index 20 such that (latest - past) * 100 == delta_20d_bp (21 rows
    total — `_delta_bp` needs `len(vals) > n` i.e. an index-20 element)."""
    past = latest - delta_20d_bp / 100.0
    rows = [{"date": "2026-08-05", "value": str(latest)}]
    rows += [{"date": f"2026-07-{20 - i:02d}", "value": "4.00"} for i in range(19)]
    rows.append({"date": "2026-07-01", "value": str(past)})
    return rows


def test_hy_oas_trend_4w_tightening_when_spread_falls():
    macro = {"BAMLH0A0HYM2": _hy_rows(3.50, -20.0)}
    out = _build_bond_signals(macro)
    assert out["credit"]["hy_oas"]["trend_4w"] == "tightening"


def test_hy_oas_trend_4w_widening_when_spread_rises():
    macro = {"BAMLH0A0HYM2": _hy_rows(4.50, 20.0)}
    out = _build_bond_signals(macro)
    assert out["credit"]["hy_oas"]["trend_4w"] == "widening"


def test_hy_oas_trend_4w_flat_when_spread_stable():
    macro = {"BAMLH0A0HYM2": _hy_rows(4.00, 1.0)}
    out = _build_bond_signals(macro)
    assert out["credit"]["hy_oas"]["trend_4w"] == "flat"


def test_hy_oas_trend_4w_none_when_no_data():
    out = _build_bond_signals({})
    assert out["credit"]["hy_oas"]["trend_4w"] is None
