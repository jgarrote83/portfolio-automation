"""Core/flex book separation — the ONE hard gate the flex sleeve shares with Core.

Extracted from the deleted ``flex/regime.py`` (session 2026-09-12, B1/R1) when
regime was removed from the flex sleeve entirely. **This module contains no
regime concept and must never acquire one.**

The distinction matters and was explicitly called out when ``regime_fit`` was
first demoted (2026-08-10): ``flex_separation_set`` is **book-collision
prevention** — a flex order in a name the Core book already governs blurs two
books into one — and is **not** a regime opinion. Regime fit asked "does this
sector suit the macro quadrant?", a monthly-vintage question with no business
gating a multi-day trade. Separation asks "is Core already responsible for this
ticker?", which is true or false regardless of any macro read. The first is
gone; the second is absolute and unchanged.

It is deliberately **POOL-based, not `selected`-based**: a flex order in SOXX
while SMH is the core semis incumbent is still a collision, so the whole pool is
off-limits, not just the currently-selected name. That also makes it immune to
the auto-switch staleness class that produced the 2026-09-12 de-risk classifier
defect — there is no incumbent to resolve, so there is nothing to go stale.
"""
from __future__ import annotations

from shared.quadrants import LEGACY_EXITS, roles_config

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


__all__ = ["FLEX_REENTERABLE", "flex_separation_set"]
