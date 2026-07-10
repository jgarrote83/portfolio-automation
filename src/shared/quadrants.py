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


# ---------------------------------------------------------------------------
# Strategy membership (growth_strategy_spec_v1.md §2 blocks + §3 rotation table)
#
# These power the deterministic reference-weight precompute (collector
# `_build_reference_weights`). They deliberately model the spec's view — two
# opposite *blocks* (Amplifier vs Damper) + a dollar switch, and a per-quadrant
# CONCENTRATE/TRIM list where a name may appear in several quadrants' lists — NOT
# a single "primary quadrant per ticker" map (that earlier model was too rigid and
# the Table A/B prompt rework already flagged it). The 24-name core roster is fixed
# (see the analyzer prompt's Core table); edits here must stay in sync with it.
# ---------------------------------------------------------------------------

# §2 — the two opposite blocks. The Amplifier is the return engine (can beat SPY);
# the Damper is ballast (wins in drawdowns). The dollar switch tilts the amplifier
# internally (US growth vs international), see `amplifier_split`.
AMPLIFIER_US = ("SPY", "QQQ", "XSD", "AMZN", "GOOGL", "INTC")
AMPLIFIER_INTL = ("IDMO", "AIA", "EWJ", "IEMG", "EWZ", "VSS", "EUAD")
DAMPER = ("GLD", "TLT", "TIP", "DBA", "PDBC", "SGOV", "XLP", "MCK", "VDE", "XLI", "PPA")

# §3 — per-quadrant rotation table: which core names to CONCENTRATE into when that
# quadrant is active. Everything held and NOT in the active quadrant's concentrate
# list is trimmed toward the 0.1% floor. Names recur across quadrants by design
# (GLD concentrates in Q3 and Q4; TIP in Q2 and Q3; EM/IDMO in Q1 and Q2). SGOV is
# part of the cash sleeve, handled separately — it is intentionally absent here.
QUADRANT_CONCENTRATE = {
    # Q1 Goldilocks — amplifier (US growth + intl-if-DXY-falling). Intl members are
    # included; the dollar switch decides the US/intl split within the amplifier.
    # EWZ is intentionally absent — commodity-heavy Brazil is the weakest Goldilocks
    # fit; it lives in Q2/Q3 (reflation + stagflation via commodity exposure).
    "Q1": ("SPY", "QQQ", "XSD", "AMZN", "GOOGL", "INTC",
           "IDMO", "AIA", "EWJ", "IEMG", "VSS"),
    # Q2 Reflation — energy/materials/industrials/EM/commodities/TIPS.
    "Q2": ("VDE", "XLI", "PPA", "EUAD", "DBA", "PDBC", "EWZ", "IEMG", "IDMO", "TIP"),
    # Q3 Stagflation — gold/energy/commodities/TIPS/defensives. EWZ included:
    # commodity-linked EM survives stagflation via its commodity exposure.
    "Q3": ("GLD", "VDE", "PDBC", "DBA", "TIP", "XLP", "MCK", "EWZ"),
    # Q4 Deflation — long Treasuries + defensive equity (SGOV/cash handled in the
    # cash sleeve, not here).
    "Q4": ("TLT", "XLP", "MCK", "GLD"),
}

# Permanent mega-cap conviction holds — never trimmed below their current weight and
# never forced down by the reference-weight math (spec §8 living-hedge nuance; the
# account holder keeps them through the cycle for balance-sheet quality + cash flow).
EXEMPT_HOLDS = ("AMZN", "GOOGL")

# The full fixed core roster (24). A held name not in this set is off-roster (a flex
# leftover like MU, or a stale fallback entry like ADBE) — the reference math assigns
# it to a flex/unclassified bucket, never a core quadrant target.
CORE_ROSTER = tuple(sorted(set(AMPLIFIER_US) | set(AMPLIFIER_INTL) | set(DAMPER)))


# Deterministic PRIMARY-quadrant map (single bucket per ticker) for aggregating the
# reference weights by quadrant. The strategy deliberately has no single "primary
# quadrant per ticker" model — QUADRANT_CONCENTRATE lets a name recur across quadrants
# (GLD in Q3+Q4, TIP in Q2+Q3, …). But a Quadrant Allocation TABLE needs each name in
# exactly one bucket, so we derive one deterministically: a ticker's primary is the
# FIRST quadrant (Q1→Q2→Q3→Q4 order) whose concentrate list contains it. That
# first-match rule reproduces the prompt's documented primaries (GLD→Q3, TLT→Q4,
# AMZN/GOOGL→Q1, EWZ→Q2, TIP→Q2, XLP/MCK→Q3). SGOV is the cash sleeve (intentionally
# absent from the concentrate lists), mapped explicitly to "cash_sleeve".
def _build_primary_quadrant_map() -> dict[str, str]:
    m: dict[str, str] = {}
    for q in ("Q1", "Q2", "Q3", "Q4"):
        for t in QUADRANT_CONCENTRATE.get(q, ()):
            m.setdefault(t.upper(), q)
    m["SGOV"] = "cash_sleeve"
    return m


PRIMARY_QUADRANT = _build_primary_quadrant_map()


def primary_quadrant(ticker: str) -> str:
    """The single deterministic bucket for a core ticker: Q1-Q4, "cash_sleeve" for
    SGOV, or "unclassified" for an off-roster/unknown name (never silently dropped)."""
    return PRIMARY_QUADRANT.get((ticker or "").upper(), "unclassified")


def favored_bucket(growth_direction: str | None, inflation_direction: str | None) -> list[str]:
    """The favored quadrant *bucket* the book should concentrate into.

    Unlike ``active_quadrant`` (which needs both axes pinned to rising/falling and
    returns "" otherwise), this returns a *union* of quadrants so a half-confirmed
    regime still yields a directional read — the reference must produce a specific
    posture on a falling-growth + flat-inflation book (a borderline regime), not a
    freeze. Used for the borderline intersection blend.

    - growth rising  + inflation falling -> [Q1]
    - growth rising  + inflation rising  -> [Q2]
    - growth rising  + inflation flat/unknown -> [Q1, Q2] (risk-on, inflation TBD)
    - growth falling + inflation rising  -> [Q3]
    - growth falling + inflation falling -> [Q4]
    - growth falling + inflation flat/unknown -> [Q3, Q4] (defensive, borderline)
    - growth flat/unknown -> [] (no directional read — caller falls back to ballast)
    """
    g = (growth_direction or "").lower()
    i = (inflation_direction or "").lower()
    if g == "rising":
        if i == "falling":
            return ["Q1"]
        if i == "rising":
            return ["Q2"]
        return ["Q1", "Q2"]
    if g == "falling":
        if i == "rising":
            return ["Q3"]
        if i == "falling":
            return ["Q4"]
        return ["Q3", "Q4"]
    return []


def concentrate_names(quadrant: str | None) -> tuple[str, ...]:
    """Core names to concentrate into for a quadrant label (() if unknown)."""
    return QUADRANT_CONCENTRATE.get((quadrant or "").upper(), ())


def intersection_names(buckets: list[str]) -> list[str]:
    """Core names that concentrate in EVERY quadrant in ``buckets`` (the borderline
    intersection — assets that work across the candidate regimes, e.g. GLD across
    Q3/Q4). Empty list for an empty/unknown bucket."""
    if not buckets:
        return []
    sets = [set(QUADRANT_CONCENTRATE.get(q.upper(), ())) for q in buckets]
    if not sets:
        return []
    common = set.intersection(*sets) if len(sets) > 1 else sets[0]
    # Preserve a stable, deterministic order (roster order).
    return [t for t in CORE_ROSTER if t in common]


def is_amplifier(ticker: str) -> bool:
    t = (ticker or "").upper()
    return t in AMPLIFIER_US or t in AMPLIFIER_INTL
