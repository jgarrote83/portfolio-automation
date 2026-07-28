"""Quadrant helpers shared by the collector and analyzer.

The active quadrant is derived deterministically from the precomputed growth and
inflation axis directions (see collector `_build_growth_axis` / `_build_inflation_axis`).
Each quadrant maps to a representative *held core ETF* — the "active-quadrant sleeve
ETF" a flex single name is judged against (opportunity-cost gate G3 + the dual-benchmark
flex review). Keeping this in one place stops the collector and analyzer from drifting.
"""

import json
from pathlib import Path

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
# Strategy membership — ROLE-BASED (roster_revision_2026-07.md)
#
# The core is no longer a fixed ticker list. It is a set of ROLES (a job the book
# needs done), each with a candidate `pool` and one `selected` incumbent, defined in
# `config/sleeve-roles.json`. The Amplifier/Damper blocks, the per-quadrant CONCENTRATE
# lists, and CORE_ROSTER all RESOLVE from that config (the `selected` member of each
# role) — so a human config commit to `selected` is the only way a core member changes.
# AMZN/GOOGL are no longer exempt holds; single names moved to LEGACY_EXITS (liquidated,
# never re-bought into core). International is governed by rotation, not the US quadrant
# (the two `rotation` roles are excluded from QUADRANT_CONCENTRATE — see collector
# `intl_governance`).
# ---------------------------------------------------------------------------

_ROLES_FILE = Path(__file__).parent.parent / "config" / "sleeve-roles.json"


def _load_roles_config() -> dict:
    return json.loads(_ROLES_FILE.read_text(encoding="utf-8"))


_ROLES_CONFIG = _load_roles_config()
_ROLES: dict[str, dict] = {r["role_id"]: r for r in _ROLES_CONFIG.get("roles", [])}


def _resolved_selected(role: dict, overrides: dict[str, str] | None) -> str | None:
    """A role's effective `selected` incumbent: `overrides[role_id]` (the live
    SleeveSelectionState effective map, session 2026-07-27) when present, else the
    static config `selected`. ``overrides`` is None/empty for every existing caller
    that predates auto-switch — this must resolve identically to the plain config
    read in that case."""
    rid = role.get("role_id")
    if overrides:
        eff = overrides.get(rid)
        if eff:
            return str(eff).upper()
    sel = role.get("selected")
    return sel.upper() if isinstance(sel, str) else None


def selected_for_role(role_id: str, overrides: dict[str, str] | None = None) -> str | None:
    """The effective `selected` incumbent ticker for a role (None if unknown).

    ``overrides`` (role_id -> ticker, the SleeveSelectionState effective-selected
    map) resolves to the auto-switched incumbent when present; omitted/empty falls
    back to the static `sleeve-roles.json` `selected` (unchanged zero-arg behavior).
    """
    r = _ROLES.get(role_id)
    if r is None:
        return None
    return _resolved_selected(r, overrides)


def role_of(ticker: str) -> str | None:
    """The role_id whose POOL contains `ticker` (None if off-roster)."""
    t = (ticker or "").upper()
    for rid, r in _ROLES.items():
        if t in {str(m).upper() for m in r.get("pool", ())}:
            return rid
    return None


def intl_roles() -> tuple[str, ...]:
    """The rotation-governed international roles (quadrants == 'rotation')."""
    return tuple(rid for rid, r in _ROLES.items() if r.get("quadrants") == "rotation")


def roles_config() -> list[dict]:
    """All role definitions (raw dicts) from sleeve-roles.json."""
    return list(_ROLES_CONFIG.get("roles", []))


def selection_config() -> dict:
    """The sleeve_selection scorecard tunables (momentum blend, hysteresis, corr floor)."""
    return dict(_ROLES_CONFIG.get("selection_config", {}))


def intl_config() -> dict:
    """The international sizing-ladder tunables (intl_governance)."""
    return dict(_ROLES_CONFIG.get("intl_config", {}))


# Retired single names — liquidated in tranches, never re-bought into CORE (Task D).
# INTC/MCK/PPA/EUAD are re-enterable as FLEX theses (see flex-candidates.json).
LEGACY_EXITS = ("AMZN", "GOOGL", "INTC", "MCK", "DBA", "TIP", "XSD", "PPA", "EUAD")

# Exempt-hold doctrine RETIRED (roster_revision_2026-07). Kept as an empty tuple so the
# validator V2 rule and the reference-weight pinning become NO-OPS rather than being
# deleted — the machinery survives for any future designated hold.
EXEMPT_HOLDS: tuple[str, ...] = ()


def _selected_by_block(block: str, overrides: dict[str, str] | None = None) -> tuple[str, ...]:
    out: list[str] = []
    for r in _ROLES.values():
        if r.get("block") == block:
            sel = _resolved_selected(r, overrides) or ""
            if sel and sel not in out:
                out.append(sel)
    return tuple(out)


# The two opposite blocks, resolved from the SELECTED member of each role. Amplifier =
# return engine; Damper = ballast. Intl = the two rotation roles' selected members.
AMPLIFIER_US = _selected_by_block("amplifier_us")
AMPLIFIER_INTL = _selected_by_block("amplifier_intl")
DAMPER = _selected_by_block("damper")


def amplifier_set(overrides: dict[str, str] | None = None) -> set[str]:
    """The effective Amplifier ticker set (US ∪ intl), recomputed per-call when
    ``overrides`` (the SleeveSelectionState effective-selected map) is given —
    e.g. after semis auto-switches SMH→SOXX, SOXX (not SMH) is the amplifier for
    gate/roster checks. Omitted/empty returns the same frozen ``AMPLIFIER_US ∪
    AMPLIFIER_INTL`` set every existing caller already relies on."""
    if not overrides:
        return set(AMPLIFIER_US) | set(AMPLIFIER_INTL)
    return (set(_selected_by_block("amplifier_us", overrides))
            | set(_selected_by_block("amplifier_intl", overrides)))


def _build_quadrant_concentrate(overrides: dict[str, str] | None = None) -> dict[str, tuple[str, ...]]:
    """Per-quadrant CONCENTRATE list = the EFFECTIVE selected member of every
    quadrant-governed role tagged with that quadrant. Rotation (intl) and cash
    roles are excluded. ``overrides`` (role_id -> ticker) substitutes an
    auto-switched incumbent for its role's static config `selected`; omitted/empty
    reproduces the frozen module-level ``QUADRANT_CONCENTRATE`` exactly."""
    out: dict[str, list[str]] = {"Q1": [], "Q2": [], "Q3": [], "Q4": []}
    for r in _ROLES.values():
        quads = r.get("quadrants")
        if not isinstance(quads, list):   # "rotation" / "cash"
            continue
        sel = _resolved_selected(r, overrides) or ""
        if not sel:
            continue
        for q in quads:
            if q in out and sel not in out[q]:
                out[q].append(sel)
    return {q: tuple(v) for q, v in out.items()}


QUADRANT_CONCENTRATE = _build_quadrant_concentrate()


# CORE_ROSTER = every POOL member of every role (both types) ∪ LEGACY_EXITS while held
# (the validator must see a legacy name to validate its exit sells). A held name not in
# this set is off-roster (a flex leftover like MU). A sold-out legacy name simply never
# trades again (buys are rejected — Task D).
def _build_core_roster() -> tuple[str, ...]:
    names: set[str] = set(LEGACY_EXITS)
    for r in _ROLES.values():
        for m in r.get("pool", ()):
            names.add(str(m).upper())
    return tuple(sorted(names))


CORE_ROSTER = _build_core_roster()


# Deterministic PRIMARY-quadrant map (single bucket per ticker) for aggregating the
# reference weights by quadrant. A ticker's primary is the FIRST quadrant (Q1→Q4 order)
# whose concentrate list contains it. International (rotation) role members map to the
# "intl" bucket (they no longer carry a US-quadrant label); SGOV is the cash sleeve.
def _build_primary_quadrant_map(overrides: dict[str, str] | None = None) -> dict[str, str]:
    m: dict[str, str] = {}
    concentrate = QUADRANT_CONCENTRATE if not overrides else _build_quadrant_concentrate(overrides)
    for q in ("Q1", "Q2", "Q3", "Q4"):
        for t in concentrate.get(q, ()):
            m.setdefault(t.upper(), q)
    for rid in intl_roles():
        for t in _ROLES[rid].get("pool", ()):
            m.setdefault(str(t).upper(), "intl")
    m["SGOV"] = "cash_sleeve"
    return m


PRIMARY_QUADRANT = _build_primary_quadrant_map()


def primary_quadrant(ticker: str, overrides: dict[str, str] | None = None) -> str:
    """The single deterministic bucket for a core ticker: Q1-Q4, "intl" for a
    rotation-governed international name, "cash_sleeve" for SGOV, or "unclassified"
    for an off-roster/unknown name (never silently dropped).

    ``overrides`` (role_id -> effective ticker) recomputes the map so an
    auto-switched incumbent (e.g. IHE replacing XLV) lands in its role's quadrant
    and the deselected old incumbent demotes to "unclassified" — the same
    treatment any other non-selected pool member already gets. Omitted/empty
    reproduces the frozen module-level lookup exactly."""
    m = PRIMARY_QUADRANT if not overrides else _build_primary_quadrant_map(overrides)
    return m.get((ticker or "").upper(), "unclassified")


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


def concentrate_names(quadrant: str | None, overrides: dict[str, str] | None = None) -> tuple[str, ...]:
    """Core names to concentrate into for a quadrant label (() if unknown).

    ``overrides`` substitutes an auto-switched role's effective incumbent for its
    config `selected`; omitted/empty reproduces the frozen zero-arg lookup."""
    concentrate = QUADRANT_CONCENTRATE if not overrides else _build_quadrant_concentrate(overrides)
    return concentrate.get((quadrant or "").upper(), ())


def intersection_names(buckets: list[str], overrides: dict[str, str] | None = None) -> list[str]:
    """Core names that concentrate in EVERY quadrant in ``buckets`` (the borderline
    intersection — assets that work across the candidate regimes, e.g. GLD across
    Q3/Q4). Empty list for an empty/unknown bucket. ``overrides`` threads an
    auto-switched incumbent through the same way `concentrate_names` does."""
    if not buckets:
        return []
    concentrate = QUADRANT_CONCENTRATE if not overrides else _build_quadrant_concentrate(overrides)
    sets = [set(concentrate.get(q.upper(), ())) for q in buckets]
    if not sets:
        return []
    common = set.intersection(*sets) if len(sets) > 1 else sets[0]
    # Preserve a stable, deterministic order (roster order). Pool membership
    # (CORE_ROSTER) is unaffected by a selection override.
    return [t for t in CORE_ROSTER if t in common]


def is_amplifier(ticker: str, overrides: dict[str, str] | None = None) -> bool:
    """``overrides`` (role_id -> ticker) recognizes an auto-switched incumbent of an
    amplifier-block role (e.g. SOXX after semis SMH→SOXX) as an amplifier; the
    deselected old incumbent is no longer counted. Omitted/empty is the frozen
    zero-arg ``AMPLIFIER_US ∪ AMPLIFIER_INTL`` check."""
    t = (ticker or "").upper()
    return t in amplifier_set(overrides)


def quadrant_allocation_bucket(ticker: str, overrides: dict[str, str] | None = None) -> str:
    """The Table-A bucket a HELD name's dollars land in (session 2026-07-17, Task D)
    — shared by the collector's pre-trade `quadrant_allocation` block and the
    analyzer's post-trade addendum, so the two can never disagree about which
    bucket a symbol belongs to (a disagreement would show up as a phantom gap that
    is really just inconsistent tagging, not a real reference deviation).

    Precedence: a LEGACY_EXITS name -> ``"legacy_exits"`` (a dedicated row so a
    wind-down position is never folded into a quadrant it no longer represents);
    off the CORE_ROSTER entirely -> ``"off_roster"`` (a flex leftover like MU);
    else the static `primary_quadrant()` bucket (Q1-Q4 / ``"intl"`` /
    ``"cash_sleeve"``), or ``"unmapped"`` when that returns ``"unclassified"`` (a
    non-selected pool member of a scorecard role, e.g. SOXX while SMH is
    selected — visible, never silently dropped).
    """
    t = (ticker or "").upper()
    if t in LEGACY_EXITS:
        return "legacy_exits"
    if t not in CORE_ROSTER:
        return "off_roster"
    pq = primary_quadrant(t, overrides)
    return pq if pq in ("Q1", "Q2", "Q3", "Q4", "intl", "cash_sleeve") else "unmapped"


def selected_core_members(overrides: dict[str, str] | None = None) -> tuple[str, ...]:
    """The effective `selected` incumbent of every role — the only names the
    reference can target. Used by the collector to guarantee they are always
    priced (including a freshly auto-switched incumbent, e.g. IHE, when
    ``overrides`` — the SleeveSelectionState effective-selected map — is given)."""
    out: list[str] = []
    for r in _ROLES.values():
        sel = _resolved_selected(r, overrides) or ""
        if sel and sel not in out:
            out.append(sel)
    return tuple(out)
