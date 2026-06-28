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
