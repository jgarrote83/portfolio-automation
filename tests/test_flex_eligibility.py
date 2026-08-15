"""Task C1 (2026-08-14 flex-conviction-path cycle) — `_build_flex_eligibility`.

PR #41 review (S-1): the ORIGINAL implementation re-derived pool membership
from `roles_config()` and the re-enterable/held rules inline instead of
calling the flex engine's own `flex_separation_set` — a hand-rolled
reconstruction with real (if latent, per the reviewer's own audit: 0
mismatches across 4 holding scenarios at review time) divergence risk. The
fix grounds the `flex_nominatable` BOOLEAN directly in `flex_separation_set`;
this file both tests the classifier's per-reason behavior AND permanently
cross-checks it against `flex_separation_set` across multiple holding
scenarios, so a future divergence fails a test instead of shipping silently.

Run: PYTHONPATH=src pytest tests/test_flex_eligibility.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_flex_eligibility  # noqa: E402
from flex.regime import FLEX_REENTERABLE, flex_separation_set  # noqa: E402
from shared.quadrants import LEGACY_EXITS, roles_config  # noqa: E402


def _row(result, sym):
    return next(r for r in result["candidates"] if r["symbol"] == sym)


def test_ordinary_candidate_is_nominatable():
    result = _build_flex_eligibility({"AVGO"}, set(), set())
    row = _row(result, "AVGO")
    assert row["flex_nominatable"] is True
    assert row["reason"] is None
    assert row["core_re_entry"] == "not_applicable"


def test_quarantined_candidate_blocked_regardless_of_other_status():
    result = _build_flex_eligibility({"AVGO"}, set(), {"AVGO"})
    row = _row(result, "AVGO")
    assert row["flex_nominatable"] is False
    assert row["reason"] == "price_quarantined"


def test_core_pool_member_blocked():
    # SMH/SOXX are semis-role pool members -- pick one guaranteed to be in
    # some role's pool via roles_config() itself rather than hardcoding.
    pool_member = next(
        str(m).upper() for r in roles_config() for m in r.get("pool", ())
        if str(m).upper() not in LEGACY_EXITS
    )
    result = _build_flex_eligibility({pool_member}, set(), set())
    row = _row(result, pool_member)
    assert row["flex_nominatable"] is False
    assert row["reason"] == "core_pool_member"


def test_legacy_exit_not_reenterable_blocked():
    non_reenterable = next(t for t in LEGACY_EXITS if t not in FLEX_REENTERABLE)
    result = _build_flex_eligibility(set(), set(), set())
    row = _row(result, non_reenterable)
    assert row["flex_nominatable"] is False
    assert row["core_re_entry"] == "closed"


def test_legacy_reenterable_and_flat_is_nominatable():
    reenterable_and_not_pool = next(
        t for t in FLEX_REENTERABLE
        if not any(t in {str(m).upper() for m in r.get("pool", ())} for r in roles_config())
    )
    result = _build_flex_eligibility(set(), set(), set())
    row = _row(result, reenterable_and_not_pool)
    assert row["flex_nominatable"] is True
    assert row["core_re_entry"] == "closed"  # still core doctrine: closed to CORE re-entry
    assert row["reason"] is None


def test_legacy_reenterable_but_held_is_blocked_winddown():
    reenterable = next(iter(FLEX_REENTERABLE))
    result = _build_flex_eligibility(set(), {reenterable}, set())
    row = _row(result, reenterable)
    assert row["flex_nominatable"] is False
    assert row["reason"] == "legacy_exit_held_winddown"


def test_xsd_dual_status_prefers_pool_member_reason():
    """XSD is BOTH a semis-role pool member AND a non-reenterable legacy
    exit -- `flex_separation_set`'s own docstring calls this out explicitly
    ("XSD needs no special case -- it is also a semis pool member, so pool
    membership already blocks it"). The reason-priority must stay
    pool-member-first, matching the pre-existing behavior this fix must not
    silently change."""
    assert "XSD" in LEGACY_EXITS
    assert "XSD" not in FLEX_REENTERABLE
    result = _build_flex_eligibility(set(), set(), set())
    row = _row(result, "XSD")
    assert row["flex_nominatable"] is False
    assert row["reason"] == "core_pool_member"


def test_available_true_and_universe_covers_legacy_exits():
    result = _build_flex_eligibility(set(), set(), set())
    assert result["available"] is True
    symbols = {r["symbol"] for r in result["candidates"]}
    assert set(LEGACY_EXITS) <= symbols


# --- permanent cross-check against flex_separation_set (mirrors the review's ---
# --- own audit methodology, across multiple holding scenarios) ----------------

_HOLDING_SCENARIOS = [
    frozenset(),
    frozenset({"EUAD"}),
    frozenset({"PPA", "INTC"}),
    frozenset({"MCK"}),
]


def test_eligibility_never_disagrees_with_flex_separation_set():
    for held in _HOLDING_SCENARIOS:
        separation = flex_separation_set(held)
        result = _build_flex_eligibility(set(), set(held), set())
        for row in result["candidates"]:
            sym = row["symbol"]
            expected_blocked = sym in separation
            actual_blocked = not row["flex_nominatable"]
            assert actual_blocked == expected_blocked, (
                f"divergence for {sym} under held={held!r}: "
                f"flex_separation_set says blocked={expected_blocked}, "
                f"_build_flex_eligibility says blocked={actual_blocked}"
            )
