"""Regime-fit filter — the *only* input the Flex engine shares with the Core book.

A flex catalyst name may be entered only if its sector wants the active quadrant.
The quadrant is resolved by ``resolve_quadrant`` from the precomputed
``growth_axis`` / ``inflation_axis`` directions: when both axes are pinned it is
``active_quadrant``, exactly as Core uses it; when the regime is *borderline* (a
2-quadrant ``favored_bucket`` such as Q3/Q4 on a falling-growth + flat-inflation
book) it is the bucket member that has performed better over the last 5 trading
days (measured by its ``QUADRANT_BENCHMARK_ETF``). Only a truly no-read regime
(growth flat/unknown → empty bucket) or missing benchmark data still fails
closed. This kills the old G1 freeze that blocked EVERY entry whenever
``active_quadrant`` was "" — which it has been continuously since 2026-07-02
(decision D1, 2026-07-21). An unknown quadrant blocks NEW entries but never
forces an exit (handled elsewhere). The quadrant→sector map mirrors
``project-instructions.md``.
"""
from __future__ import annotations

from shared.quadrants import (  # noqa: F401  (active_quadrant/benchmark_etf_for re-exported)
    LEGACY_EXITS,
    active_quadrant,
    benchmark_etf_for,
    favored_bucket,
    roles_config,
)

# Legacy single-name exits that ARE re-enterable as FLEX theses (quadrants.py
# doctrine: "INTC/MCK/PPA/EUAD are re-enterable as FLEX theses (see
# flex-candidates.json)"). Everything else in LEGACY_EXITS (AMZN/GOOGL/DBA/TIP/XSD)
# is liquidated for good and must never re-enter via the flex path either.
FLEX_REENTERABLE = frozenset({"INTC", "MCK", "PPA", "EUAD"})


def flex_separation_set(held_symbols: frozenset[str] = frozenset()) -> frozenset[str]:
    """Symbols a flex nomination must never touch.

    Derived from the role config (not a stale hard-coded roster) so the current
    book — whatever ``sleeve-roles.json`` says today — is always enforced:

    - **Every pool member of every role** (selected or not): a flex order in SOXX
      while SMH is the core semis incumbent blurs the two books, so the whole pool
      is off-limits, not just the selected name.
    - **Legacy exits that are NOT flex-re-enterable** (AMZN/GOOGL/DBA/TIP/XSD):
      liquidated for good. (XSD needs no special case — it is also a semis pool
      member, so pool membership already blocks it.)
    - **Any legacy exit still HELD** — even a re-enterable one (INTC/MCK/PPA/EUAD):
      a name mid-wind-down must not exist in both books at once. Once it is flat it
      becomes flex-nominatable again.
    """
    held = {str(s).upper() for s in (held_symbols or ())}
    sep: set[str] = set()
    for r in roles_config():
        for m in r.get("pool", ()):
            sep.add(str(m).upper())
    for t in LEGACY_EXITS:
        tu = t.upper()
        if tu not in FLEX_REENTERABLE or tu in held:
            sep.add(tu)
    return frozenset(sep)


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


# Basis values for resolve_quadrant, in order of strength.
_RESOLVE_BASES = ("active", "favored_single", "borderline_5d_tiebreak", "unresolved")


def resolve_quadrant(
    growth_direction: str | None,
    inflation_direction: str | None,
    bench_returns_5d: dict[str, float] | None,
) -> tuple[str, str]:
    """Resolve the quadrant the flex engine treats as *in force* for entries.

    Returns ``(quadrant, basis)`` with ``basis`` one of ``_RESOLVE_BASES``:

    - Both axes pinned to rising/falling → ``(active_quadrant, "active")`` — the
      unchanged strict behaviour.
    - Otherwise take ``favored_bucket``:
      - empty bucket (growth flat/unknown) → ``("", "unresolved")`` — still fail
        closed; there is no directional read at all.
      - single-element bucket → that quadrant, basis ``"favored_single"`` (defensive;
        in practice a single-element bucket coincides with ``active`` resolving).
      - 2-quadrant union → the member with the higher trailing 5d benchmark return
        wins (``"borderline_5d_tiebreak"``). A missing/None return for **any** member
        → ``("", "unresolved")`` — never guess a quadrant on partial data. An exact
        tie → the first quadrant in bucket order.

    ``bench_returns_5d`` maps a quadrant label ("Q3") to its benchmark ETF's trailing
    5-trading-day % return; only consulted for the 2-quadrant tiebreak.
    """
    active = active_quadrant(growth_direction, inflation_direction)
    if active:
        return (active, "active")

    bucket = favored_bucket(growth_direction, inflation_direction)
    if not bucket:
        return ("", "unresolved")
    if len(bucket) == 1:
        return (bucket[0], "favored_single")

    returns = bench_returns_5d or {}
    resolved_r: dict[str, float] = {}
    for q in bucket:
        r = returns.get(q)
        if r is None:
            return ("", "unresolved")  # fail-closed — never guess on partial data
        resolved_r[q] = r

    best_q = bucket[0]
    best_r = resolved_r[bucket[0]]
    for q in bucket[1:]:
        if resolved_r[q] > best_r:  # strict — an exact tie keeps the first member
            best_q, best_r = q, resolved_r[q]
    return (best_q, "borderline_5d_tiebreak")


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
