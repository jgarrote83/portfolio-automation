"""Unit tests for role-based membership resolution (Task C/H — roster_revision_2026-07).

quadrants.py resolves AMPLIFIER_*/DAMPER/QUADRANT_CONCENTRATE/CORE_ROSTER from the
`selected` member of each role in sleeve-roles.json. EXEMPT_HOLDS is retired to (); legacy
single names are LEGACY_EXITS (in CORE_ROSTER while held, target 0). Run:
    PYTHONPATH=src pytest tests/test_roles.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer.handler import _build_reference_gaps  # noqa: E402
from shared.quadrants import (  # noqa: E402
    AMPLIFIER_INTL,
    AMPLIFIER_US,
    CORE_ROSTER,
    DAMPER,
    EXEMPT_HOLDS,
    LEGACY_EXITS,
    QUADRANT_CONCENTRATE,
    intl_roles,
    role_of,
    selected_for_role,
)


def test_selected_for_role():
    assert selected_for_role("semis") == "SMH"
    assert selected_for_role("gold") == "GLD"
    assert selected_for_role("intl_leader") == "AIA"
    assert selected_for_role("nope") is None


def test_role_of_matches_pool_members():
    assert role_of("SMH") == "semis"
    assert role_of("XSD") == "semis"     # non-selected pool member still resolves
    assert role_of("SOXX") == "semis"
    assert role_of("VXUS") == "intl_broad"
    assert role_of("MU") is None         # off-roster


def test_intl_roles():
    assert set(intl_roles()) == {"intl_broad", "intl_leader"}


def test_exempt_holds_retired():
    assert EXEMPT_HOLDS == ()


def test_legacy_exits_membership():
    for t in ("AMZN", "GOOGL", "INTC", "MCK", "DBA", "TIP", "XSD", "PPA", "EUAD"):
        assert t in LEGACY_EXITS


def test_amplifier_and_damper_resolve_from_selected():
    assert set(AMPLIFIER_US) == {"SPY", "QQQ", "SMH"}
    assert set(AMPLIFIER_INTL) == {"VXUS", "AIA"}
    # Selected dampers include the new roles' incumbents; legacy names are NOT here.
    assert {"GLD", "TLT", "IEF", "USMV", "XLF", "COWZ", "VTIP", "KMLM"} <= set(DAMPER)
    assert "AMZN" not in DAMPER and "MCK" not in DAMPER


def test_quadrant_concentrate_reflects_selected_no_intl():
    assert QUADRANT_CONCENTRATE["Q1"] == ("SPY", "QQQ", "SMH")
    assert "GLD" in QUADRANT_CONCENTRATE["Q3"]
    assert "TLT" in QUADRANT_CONCENTRATE["Q4"] and "IEF" in QUADRANT_CONCENTRATE["Q4"]
    # International names are rotation-governed — never in a US-quadrant concentrate.
    for q in ("Q1", "Q2", "Q3", "Q4"):
        assert "AIA" not in QUADRANT_CONCENTRATE[q]
        assert "VXUS" not in QUADRANT_CONCENTRATE[q]


def test_core_roster_is_pools_plus_legacy():
    for t in ("SMH", "XSD", "SOXX", "VXUS", "AIA", "GLD", "TLT", "IEF", "USMV", "SGOV"):
        assert t in CORE_ROSTER
    for t in LEGACY_EXITS:
        assert t in CORE_ROSTER      # held legacy names must be visible to the validator


def test_reference_gaps_include_held_legacy_at_target_zero():
    """A held legacy name (AMZN) produces a gap row at reference 0 so its exit validates."""
    snapshot = {
        "reference_weights": {"target_weights_pct": {"SPY": 40.0, "GLD": 30.0}},
        "regime_gate": {"status": "closed"},
        "paper_account": {
            "equity": 100_000.0, "cash": 5_000.0,
            "positions": [
                {"ticker": "AMZN", "qty": 50, "market_value": 8_600.0, "current_price": 172.0},
                {"ticker": "SPY", "qty": 80, "market_value": 40_000.0, "current_price": 500.0},
            ],
        },
        "prices": {"AMZN": {"c": 172.0}, "SPY": {"c": 500.0}},
    }
    gaps, ctx = _build_reference_gaps(snapshot)
    amzn = next((g for g in gaps if g["symbol"] == "AMZN"), None)
    assert amzn is not None
    assert amzn["reference_pct"] == 0.0
    assert amzn["current_pct"] > 0    # held → overweight vs 0 target → sells validate
    assert amzn["held_qty"] == 50
