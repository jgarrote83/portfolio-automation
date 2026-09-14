"""global_overnight — the overseas session read, available before the US open
(session 2026-09-13, resolves FOLLOWUPS #34, open since 2026-07-04).

Jorge's direction: *"add the other markets that open before US as an indicator —
we may have a global indicator that a sector is being bought before the US market
opens."* By the time the collector fires at 09:00 ET, Asia has CLOSED and Europe
is mid-session. That is real, settled information about what is being bought,
and it exists before any US price action does.

PURE module — no I/O. The collector fetches every quote and hands rows in; this
module does the arithmetic. Same split as `catalyst_screen.py`.

---

**The load-bearing design decision: the sector score is the EXCESS over the
cross-sector mean, never the absolute move.**

On a risk-on morning every overseas basket is up and every sector would score
high together — which says nothing about WHICH sector is being bought. That is
the common overnight factor (beta), and the composite already reads direction
through `momentum` and `news_tone`. A cross-sector TILT is the only thing this
block can contribute that nothing else already measures, so the score is built
on `sector_pct - mean(all available sector_pct)`. The absolute move rides along
in the payload for description, and is never what is scored.

The direct consequence, stated so it is not mistaken for a bug: the excesses sum
to ~zero by construction, so roughly half of any session's sectors score below
0.5. A 0.5 here means "moved with the overseas tape", not "no data" — absence is
`None`, as everywhere else in this system.

**Why a cross-section needs a minimum width.** With one sector the excess is 0.0
by construction; with two, each is the exact negative of the other. Neither is a
read. `MIN_SECTORS_FOR_CROSS_SECTION` (3) is the floor below which `sector_tone`
is withheld entirely rather than published as false precision — recorded as
decision gate **G-12**, proposed not confirmed.

**Freshness is self-measured, and is the whole safety property.** A row counts
only if its own timestamp lands on the CURRENT ET session date. An ADR quote
still showing the prior US close at 09:00 ET is stale by that test, drops out,
and if enough drop out `sector_tone` comes back empty — at which point the
`global_sector_tone` component is ABSENT for every candidate and the block is a
no-op that changes no nomination. `coverage` records exactly what was read,
what was stale, and why, so the failure is diagnosable from the snapshot alone
rather than by re-running. This is deliberate: whether the ADR basket actually
refreshes pre-market could not be measured on the Sunday this was built, so the
block measures it at runtime instead of assuming it.

**Structurally absent is NOT missing data.** Real Estate, Utilities and Consumer
Defensive have no honest overseas proxy — they are structurally domestic. They
read `not_applicable`, never `missing_data` and never 0.0, the same distinction
`catalyst_screen.applicable_components` already draws and for the same reason: a
candidate must never be judged against a bar it cannot structurally clear.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Below this many sectors the cross-section is not a read (see module docstring).
# Decision gate G-12 — proposed, not confirmed.
MIN_SECTORS_FOR_CROSS_SECTION = 3

# Symmetric clamp mapping a signed excess-% to [0,1], mirroring
# `catalyst_screen.momentum_score`/`relative_strength_score` exactly so this
# component cannot dominate the composite by scale alone. 1.5pp of cross-sector
# excess in a single overnight session is already a large dispersion.
# Decision gate G-13 — proposed, not confirmed.
EXCESS_CAP_PCT = 1.5

# How each row was sourced. Recorded per row because the three are NOT equally
# strong evidence and a later reader must be able to tell them apart:
#   direct_index — the actual overseas index (the real session, no proxy layer)
#   region_proxy — a US-listed country ETF, used ONLY where the index itself is
#                  plan-restricted (^KS11/^GDAXI returned HTTP 402 on Starter)
#   adr_proxy    — a dual-listed ADR, arbitrage-linked to its home session
SOURCE_BASES = ("direct_index", "region_proxy", "adr_proxy")


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # reject NaN


def parse_as_of_et(raw) -> datetime | None:
    """A quote row's timestamp -> an ET-aware datetime, or None if unreadable.

    Accepts what the two probed FMP endpoints actually return: a unix epoch (in
    SECONDS from `/quote`, and tolerated in MILLISECONDS, which some FMP rows
    use) or an ISO-8601 string. An unparseable timestamp is `None` and the row
    is dropped as unverifiable — never silently assumed fresh, which would let
    a stale print govern.
    """
    if raw is None or isinstance(raw, bool):
        return None
    n = _num(raw)
    if n is not None and not isinstance(raw, str):
        if n > 1e11:  # milliseconds
            n /= 1000.0
        if n <= 0:
            return None
        try:
            return datetime.fromtimestamp(n, tz=timezone.utc).astimezone(ET)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        # FMP's string timestamps are ET-local for US-listed instruments.
        dt = dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def freshness(as_of_et: datetime | None, now_et: datetime) -> tuple[bool, float | None]:
    """``(is_fresh, age_hours)`` — fresh iff the row lands on the CURRENT ET
    session date.

    Date equality, not an age bound, is the correct test here and the choice
    matters. At 09:00 ET the overseas prints we want span ~13 hours (Tokyo's
    close at 02:00 ET through a live London quote), while the stale print we
    must reject — yesterday's US close at 16:00 ET — is only 17 hours old. No
    single age threshold separates those cleanly; the ET session date does,
    exactly and without tuning.

    A future-dated row (clock skew, a bad epoch) is NOT fresh — it is unverified
    data, and the conservative reading of unverified data is to drop it.
    """
    if as_of_et is None:
        return False, None
    age = (now_et - as_of_et).total_seconds() / 3600.0
    if age < 0:
        return False, round(age, 3)
    return as_of_et.date() == now_et.date(), round(age, 3)


def change_pct(row: dict, prior_closes: dict[str, float] | None = None) -> float | None:
    """Session change % for a quote row, preferring what the row itself states.

    Order: an explicit `changePercentage`/`changesPercentage` field, then
    `price` vs the row's own `previousClose`, then `price` vs a caller-supplied
    prior close. Returns None when no pair resolves — never 0.0, which would
    read as a genuine flat session.
    """
    for key in ("changePercentage", "changesPercentage"):
        v = _num(row.get(key))
        if v is not None:
            return round(v, 4)
    price = _num(row.get("price")) or _num(row.get("close")) or _num(row.get("c"))
    prev = _num(row.get("previousClose")) or _num(row.get("prevClose"))
    if prev is None and prior_closes:
        sym = str(row.get("symbol") or "").upper().strip()
        prev = _num((prior_closes or {}).get(sym))
    if price is None or prev is None or prev <= 0:
        return None
    return round((price / prev - 1.0) * 100.0, 4)


def excess_score(excess_pct: float | None, cap_pct: float = EXCESS_CAP_PCT) -> float | None:
    """Signed cross-sector excess -> [0,1], symmetric clamp (0.5 = moved with
    the overseas tape; 1.0 = +cap or better; 0.0 = -cap or worse). `None` in,
    `None` out — absence never becomes 0.5."""
    if excess_pct is None:
        return None
    clamped = max(-cap_pct, min(cap_pct, excess_pct))
    return round((clamped + cap_pct) / (2 * cap_pct), 4)


def _read_rows(
    rows: list[dict] | None,
    prior_closes: dict[str, float] | None,
    now_et: datetime,
) -> tuple[dict[str, dict], list[dict]]:
    """``({SYMBOL: reading}, [rejected...])`` — shared by both baskets.

    A reading is ``{symbol, pct, as_of, age_hours, source_basis}``. Every
    rejection carries its own reason so `coverage` can explain an empty basket.
    """
    kept: dict[str, dict] = {}
    rejected: list[dict] = []
    for row in rows or ():
        sym = str(row.get("symbol") or "").upper().strip()
        if not sym:
            continue
        as_of = parse_as_of_et(row.get("timestamp") or row.get("as_of") or row.get("date"))
        fresh, age = freshness(as_of, now_et)
        pct = change_pct(row, prior_closes)
        basis = str(row.get("source_basis") or "")
        if as_of is None:
            rejected.append({"symbol": sym, "reason": "unreadable_timestamp",
                             "source_basis": basis})
            continue
        if not fresh:
            rejected.append({"symbol": sym, "reason": "stale_not_current_session",
                             "as_of": as_of.isoformat(), "age_hours": age,
                             "source_basis": basis})
            continue
        if pct is None:
            rejected.append({"symbol": sym, "reason": "no_change_basis",
                             "as_of": as_of.isoformat(), "source_basis": basis})
            continue
        kept[sym] = {
            "symbol": sym,
            "pct": pct,
            "as_of": as_of.isoformat(),
            "age_hours": age,
            "source_basis": basis,
        }
    return kept, rejected


def build_global_overnight(
    region_rows: list[dict] | None,
    sector_rows: list[dict] | None,
    prior_closes: dict[str, float] | None,
    sector_map: dict[str, list[str]] | None,
    now_et: datetime,
    *,
    structurally_absent_sectors: tuple[str, ...] | list[str] = (),
    min_sectors: int = MIN_SECTORS_FOR_CROSS_SECTION,
    excess_cap_pct: float = EXCESS_CAP_PCT,
) -> dict:
    """The `global_overnight` snapshot block.

    `region_rows` / `sector_rows` are raw quote rows the collector fetched, each
    additionally carrying the `source_basis` (and, for regions, the `region`)
    the caller resolved from config. `prior_closes` is an optional
    ``{symbol: close}`` fallback for a row that states neither a change
    percentage nor its own previous close.

    Returns a block that is ALWAYS well-formed. `available` is False — with
    `sector_tone` empty and a populated `coverage` — whenever the cross-section
    is too thin to be a read, rather than publishing a number nobody can trust.
    """
    absent_sectors = [str(s) for s in (structurally_absent_sectors or ())]

    region_read, region_rejected = _read_rows(region_rows, prior_closes, now_et)
    sector_read, sector_rejected = _read_rows(sector_rows, prior_closes, now_et)

    # --- regions: describe-only, one entry per configured region -------------
    regions: dict[str, dict] = {}
    for row in region_rows or ():
        sym = str(row.get("symbol") or "").upper().strip()
        name = str(row.get("region") or "").strip() or sym.lower()
        r = region_read.get(sym)
        if r:
            regions[name] = {**r, "region": name}
    region_pcts = [v["pct"] for v in regions.values()]
    region_breadth = (
        round(sum(1 for p in region_pcts if p > 0) / len(region_pcts), 4)
        if region_pcts else None
    )

    # --- sectors: the cross-sectional tilt -----------------------------------
    raw_sector: dict[str, dict] = {}
    sector_gaps: dict[str, str] = {}
    for sector, members in (sector_map or {}).items():
        if sector in absent_sectors:
            continue  # structurally absent — never even a missing-data row
        readings = [sector_read[s] for s in
                    (str(m).upper().strip() for m in (members or ()))
                    if s in sector_read]
        if not readings:
            sector_gaps[sector] = "no_fresh_members"
            continue
        pct = round(sum(r["pct"] for r in readings) / len(readings), 4)
        raw_sector[sector] = {
            "pct": pct,
            "members_read": [r["symbol"] for r in readings],
            "members_configured": len(members or ()),
            "source_basis": readings[0]["source_basis"] or "adr_proxy",
            "as_of": max(r["as_of"] for r in readings),
        }

    sectors: dict[str, dict] = {}
    baseline_pct: float | None = None
    if len(raw_sector) >= max(1, int(min_sectors)):
        baseline_pct = round(sum(v["pct"] for v in raw_sector.values()) / len(raw_sector), 4)
        for sector, v in raw_sector.items():
            excess = round(v["pct"] - baseline_pct, 4)
            sectors[sector] = {
                **v,
                "excess_pct": excess,
                "tone": excess_score(excess, excess_cap_pct),
            }

    thin_reason = None
    if not sectors:
        thin_reason = (
            f"cross_section_too_thin:{len(raw_sector)}<{min_sectors}"
            if raw_sector else "no_fresh_sector_data"
        )

    return {
        "available": bool(sectors),
        "as_of": now_et.isoformat(),
        "session_date_et": now_et.strftime("%Y-%m-%d"),
        "region_tone": regions,
        "region_breadth_up": region_breadth,
        "sector_tone": sectors,
        "sector_baseline_pct": baseline_pct,
        "structurally_absent_sectors": absent_sectors,
        "unavailable_reason": thin_reason,
        "basis": {
            "scored_on": "cross_sector_excess",
            "excess_cap_pct": excess_cap_pct,
            "min_sectors_for_cross_section": int(min_sectors),
            "freshness_rule": "current_et_session_date",
        },
        "coverage": {
            "regions_configured": len(region_rows or ()),
            "regions_read": len(regions),
            "regions_rejected": region_rejected,
            "sector_symbols_requested": len(sector_rows or ()),
            "sector_symbols_read": len(sector_read),
            "sector_symbols_rejected": sector_rejected,
            "sectors_with_data": len(raw_sector),
            "sectors_scored": len(sectors),
            "sector_gaps": sector_gaps,
        },
    }


def sector_tone_for(
    block: dict | None, sector: str | None, structurally_absent: tuple[str, ...] | list[str] = (),
) -> tuple[float | None, str | None]:
    """``(tone, reason)`` for one candidate's sector — the single entry point
    `catalyst_screen`'s `global_sector_tone` component is built from.

    ``reason`` is ``None`` when a tone resolved, else names why it did not:
    `not_applicable` (a structurally-domestic sector — this is the
    absent-vs-zero distinction, not a data gap), `block_unavailable`,
    `no_sector` or `sector_not_covered`. Never returns 0.0 for absence.
    """
    if sector and str(sector) in (structurally_absent or ()):
        return None, "not_applicable"
    if not block or not block.get("available"):
        return None, "block_unavailable"
    if sector and str(sector) in (block.get("structurally_absent_sectors") or ()):
        return None, "not_applicable"
    if not sector:
        return None, "no_sector"
    entry = (block.get("sector_tone") or {}).get(str(sector))
    if not entry or entry.get("tone") is None:
        return None, "sector_not_covered"
    return entry["tone"], None


__all__ = [
    "build_global_overnight",
    "sector_tone_for",
    "change_pct",
    "excess_score",
    "freshness",
    "parse_as_of_et",
    "MIN_SECTORS_FOR_CROSS_SECTION",
    "EXCESS_CAP_PCT",
    "SOURCE_BASES",
]
