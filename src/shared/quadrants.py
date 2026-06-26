"""Quadrant helpers shared by the collector and analyzer.

The active quadrant is derived deterministically from the precomputed growth and
inflation axis directions (see collector `_build_growth_axis` / `_build_inflation_axis`).
Each quadrant maps to a representative *held core ETF* — the "active-quadrant sleeve
ETF" a flex single name is judged against (opportunity-cost gate G3 + the dual-benchmark
flex review). Keeping this in one place stops the collector and analyzer from drifting.
"""

# Representative active-quadrant sleeve ETF (all are held core names).
QUADRANT_BENCHMARK_ETF = {
    "Q1": "QQQ",   # Goldilocks — growth/tech
    "Q2": "XLI",   # Reflation — cyclicals/industrials
    "Q3": "GLD",   # Stagflation — real assets
    "Q4": "TLT",   # Deflation — long-duration Treasuries
}


def active_quadrant(growth_direction: str | None, inflation_direction: str | None) -> str:
    """Map the two axis directions to a single quadrant label.

    growth rising + inflation falling -> Q1; rising + rising -> Q2;
    falling + rising -> Q3; falling + falling -> Q4. Any ``flat`` /
    ``indeterminate`` / missing axis -> "" (unknown) — callers must treat an
    unknown quadrant conservatively (e.g. do NOT force a regime-fit cut).
    """
    g = (growth_direction or "").lower()
    i = (inflation_direction or "").lower()
    if g not in ("rising", "falling") or i not in ("rising", "falling"):
        return ""
    if g == "rising":
        return "Q1" if i == "falling" else "Q2"
    return "Q3" if i == "rising" else "Q4"


def benchmark_etf_for(quadrant: str | None) -> str:
    """Representative sleeve ETF for a quadrant label ("" if unknown)."""
    return QUADRANT_BENCHMARK_ETF.get((quadrant or "").upper(), "")
