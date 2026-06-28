"""Sizing-helper tests — the constant-dollar-risk invariant, proved two ways,
plus the per-name and sleeve concentration backstops and which constraint binds.

Run: PYTHONPATH=src pytest tests/test_flex_sizing.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dataclasses import replace  # noqa: E402

from flex.config import FlexConfig  # noqa: E402
from flex.entry import size_flex_position  # noqa: E402

EQUITY = 1_000_000.0
ENTRY = 100.0


def test_constant_dollar_risk_where_budget_governs():
    # Stops in the budget-governing band (~3.3%–4%): risk budget binds, equal risk.
    cfg = FlexConfig()
    a = size_flex_position(EQUITY, ENTRY, 3.5, cfg)   # 3.5% stop
    b = size_flex_position(EQUITY, ENTRY, 4.0, cfg)   # 4.0% stop
    assert a["binding"] == "risk_budget"
    assert b["binding"] == "risk_budget"
    assert round(a["realized_risk_pct"], 2) == 0.40
    assert round(b["realized_risk_pct"], 2) == 0.40


def test_per_name_cap_binds_and_suppresses_risk_for_tight_stops():
    cfg = FlexConfig()
    r = size_flex_position(EQUITY, ENTRY, 2.0, cfg)   # 2% stop
    assert r["binding"] == "per_name_cap"
    assert round(r["notional_pct"], 2) == cfg.per_name_cap_pct
    assert r["realized_risk_pct"] < cfg.risk_budget_pct


def test_sleeve_cap_binds_when_sleeve_near_full():
    cfg = FlexConfig()
    # Only $50k of sleeve room → 500 shares at $100, below both other constraints.
    r = size_flex_position(EQUITY, ENTRY, 4.0, cfg, sleeve_room_usd=50_000.0)
    assert r["binding"] == "sleeve_cap"
    assert r["size_shares"] == 500


def test_mechanic_invariant_on_loosened_cap_regression_guard():
    # With the cap widened to the sleeve cap, the risk-budget mechanic governs
    # again and 2%- vs 4%-stop names carry EQUAL dollar risk (proves the mechanic
    # is correct independent of the production cap).
    cfg = replace(FlexConfig(), per_name_cap_pct=25.0)
    a = size_flex_position(EQUITY, ENTRY, 2.0, cfg)
    b = size_flex_position(EQUITY, ENTRY, 4.0, cfg)
    assert a["binding"] == "risk_budget"
    assert b["binding"] == "risk_budget"
    assert round(a["realized_risk_pct"], 2) == round(b["realized_risk_pct"], 2) == 0.40
