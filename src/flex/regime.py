"""Regime-fit filter — the *only* input the Flex engine shares with the Core book.

A flex catalyst name may be entered only if its sector wants the active quadrant
(derived deterministically from the precomputed ``growth_axis`` / ``inflation_axis``
directions, exactly as Core uses them). The quadrant→sector map mirrors
``project-instructions.md``. An unknown quadrant (a ``flat``/``indeterminate``
axis) blocks NEW entries — you cannot confirm regime fit without a confirmed
regime — but never forces an exit (handled elsewhere).
"""
from __future__ import annotations

from shared.quadrants import active_quadrant, benchmark_etf_for  # noqa: F401  (re-exported)

# The 24 fixed Core tickers — used by separation checks (a flex order must never
# touch a core name as a flex order, and vice-versa).
CORE_TICKERS = frozenset({
    "SPY", "QQQ", "XSD", "XLI", "PPA", "VDE", "MCK", "INTC", "AMZN", "GOOGL",
    "IDMO", "EUAD", "VSS", "AIA", "EWJ", "IEMG", "EWZ", "GLD", "DBA", "PDBC",
    "SGOV", "TLT", "XLP", "TIP",
})

# FMP `sector` strings → the quadrants whose factor profile they fit.
_SECTOR_QUADRANTS: dict[str, set[str]] = {
    "technology": {"Q1"},
    "communication services": {"Q1"},
    "consumer cyclical": {"Q1", "Q2"},
    "industrials": {"Q2"},
    "financial services": {"Q2"},
    "basic materials": {"Q2", "Q3"},
    "energy": {"Q2", "Q3"},
    "utilities": {"Q3", "Q4"},
    "consumer defensive": {"Q3", "Q4"},
    "healthcare": {"Q3", "Q4"},
    "real estate": {"Q4"},
}


def quadrant_from_axes(growth_direction: str | None, inflation_direction: str | None) -> str:
    """Deterministic active quadrant from the two axis directions ("" if unknown)."""
    return active_quadrant(growth_direction, inflation_direction)


def regime_fit(sector: str | None, quadrant: str | None) -> bool:
    """True iff ``sector`` belongs to the active ``quadrant``.

    Conservative for entry: an unknown quadrant or an unrecognized sector returns
    ``False`` (no entry without a confirmed regime fit).
    """
    q = (quadrant or "").upper()
    if q not in ("Q1", "Q2", "Q3", "Q4"):
        return False
    fits = _SECTOR_QUADRANTS.get((sector or "").strip().lower())
    if not fits:
        return False
    return q in fits


# ---------------------------------------------------------------------------
# Dynamic watch_candidates funnel — FOLLOWUPS #8 v2
# ---------------------------------------------------------------------------

# Legacy exits that may re-enter the flex funnel once flat (core re-entry remains
# closed; flex nomination while the position is fully sold is allowed).
# INTC/MCK/PPA/EUAD are seeded in config/flex-candidates.json for exactly this reason.
# AMZN/GOOGL/DBA/TIP/XSD/VDE are NOT re-enterable as flex — they either have
# a re-entry prohibition or are ETF roles not suitable as single-name flex catalysts.
FLEX_REENTERABLE: frozenset[str] = frozenset({"INTC", "MCK", "PPA", "EUAD"})


def flex_separation_set(held: set[str]) -> frozenset[str]:
    """The set of tickers that must NOT appear in the dynamic watch_candidates list.

    All CORE_ROSTER members (role pool members ∪ LEGACY_EXITS) are separated —
    the Flex Catalyst Engine Separation Contract prohibits a flex order touching a
    core name. FLEX_REENTERABLE names are carved out when they are currently flat:
    a flat INTC/MCK/PPA/EUAD is a valid flex candidate (and IS seeded in the static
    list). A held FLEX_REENTERABLE name is already excluded via the ``exclude``
    set in ``_load_flex_candidates`` — it is also returned here for belt-and-suspenders.

    ``held`` should be the set of currently-held ticker symbols (uppercase).
    """
    from shared.quadrants import CORE_ROSTER
    separation: set[str] = set(CORE_ROSTER)
    # Carve out flat re-enterable names (they are valid flex candidates while flat)
    for sym in FLEX_REENTERABLE:
        if sym not in held:
            separation.discard(sym)
    return frozenset(separation)
