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


def test_risk_budget_is_INERT_at_the_new_stop_width():
    """N4 (2026-09-12) — the documented consequence, pinned so a future stop-width
    change that silently hands control back to the risk budget is caught.

    At `per_name_cap_pct` 6.0 the budget only governs once the stop exceeds
    0.40/6.0 = 6.67% of entry. The catalyst profile's stop is capped at 1.5%, so
    `per_name_cap` binds on EVERY catalyst entry and `risk_budget_pct` is inert.
    Realized risk falls to 6% x 1.5% = 0.09%, well under the 0.40% budget — which
    is intended: with a fixed tight stop, single-name CONCENTRATION is the real
    exposure, not per-trade risk."""
    cfg = FlexConfig()
    # The profile's actual stop width -> the cap binds.
    tight = size_flex_position(EQUITY, ENTRY, ENTRY * 0.015, cfg)
    assert tight["binding"] == "per_name_cap"
    assert round(tight["realized_risk_pct"], 3) == 0.09
    assert round(tight["notional_pct"], 2) == 6.00
    # The budget only re-binds past a stop the catalyst profile can never produce.
    wide = size_flex_position(EQUITY, ENTRY, ENTRY * 0.08, cfg)
    assert wide["binding"] == "risk_budget"
    assert round(wide["realized_risk_pct"], 2) == 0.40


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
