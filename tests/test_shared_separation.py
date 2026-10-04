"""`shared.separation` regression (moved verbatim from `flex/separation.py`, Part B / U1).

The separation set is derived from the LIVE `sleeve-roles.json` pools + legacy-exit
doctrine (`flex_separation_set`), NOT a fixed roster — so every current pool member is
blocked while re-enterable legacy names (INTC/MCK/PPA/EUAD) are allowed once flat. The
module has no consumer in Core after Part B; it is kept (unchanged, per decision U1) as
the base for the ORB engine's Core-exclusion set (Phase 4 requirement).

Run: PYTHONPATH=src pytest tests/test_shared_separation.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shared.separation import FLEX_REENTERABLE, flex_separation_set  # noqa: E402


# --- flex_separation_set: derived from the LIVE roster -----------------------

def test_current_roster_pool_members_are_blocked():
    sep = flex_separation_set(frozenset())
    # Non-selected pool members AND selected incumbents are all off-limits.
    for t in ("SOXX", "KMLM", "XLV", "IEF", "SMH", "XLI", "GLD"):
        assert t in sep, t
    # International pool members (both rotation roles).
    for t in ("VXUS", "ACWX", "IXUS", "AIA", "EWZ"):
        assert t in sep, t


def test_reenterable_legacy_allowed_when_flat_blocked_when_held():
    sep_flat = flex_separation_set(frozenset())
    assert "PPA" not in sep_flat
    assert "EUAD" not in sep_flat
    assert "MCK" not in sep_flat
    # MCK mid-wind-down (still held) must not exist in both books.
    sep_held = flex_separation_set(frozenset({"MCK"}))
    assert "MCK" in sep_held
    assert FLEX_REENTERABLE == frozenset({"INTC", "MCK", "PPA", "EUAD"})


def test_non_reenterable_legacy_always_blocked():
    for held in (frozenset(), frozenset({"AMZN", "DBA", "TIP"})):
        sep = flex_separation_set(held)
        for t in ("AMZN", "GOOGL", "DBA", "TIP"):
            assert t in sep, t


def test_xsd_blocked_by_pool_membership():
    # XSD is both a legacy exit and a semis pool member — pool membership blocks it.
    assert "XSD" in flex_separation_set(frozenset())
