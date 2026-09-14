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

**The baseline is LEAVE-ONE-OUT, and that is not a refinement — a mean-inclusive
baseline makes the score's scale depend on coverage.** A sector included in the
mean it is measured against is shrunk by exactly `(n-1)/n`: 33% at n=3, 20% at
n=5, 12.5% at n=8. So the SAME real divergence scores ~31% differently on a thin
morning than a full one, driven purely by how many ADRs happened to have a fresh
quote. Concretely, one sector at +3.0% with the rest flat reads +2.00 at n=3 and
+2.63 at n=8 mean-inclusive; leave-one-out reads +3.00 in both. Each sector is
therefore measured against `mean(pct_j for j != i)` — its own baseline, recorded
per sector as `baseline_pct`. The session-level `sector_mean_pct` is retained for
narration only and is NOT what anything is scored against.

**What leave-one-out does NOT fix: composition dependence.** The baseline is still
the mean of whichever sectors resolved. On a morning when semis are ripping and
only Technology, Energy and Materials have fresh quotes, the baseline is itself
elevated and Technology's excess is understated. That is a property of the
benchmark's membership, not of self-inclusion, and no arithmetic removes it —
`coverage.sector_gaps` makes it visible instead. The consequence for
`MIN_SECTORS_FOR_CROSS_SECTION` is the important part: it governs **benchmark
stability**, not merely "enough data to read", which is why it sits at 5 rather
than at the 2 the arithmetic alone would allow. Recorded as decision gate
**G-12**.

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

# Minimum sectors for a cross-section. This is a BENCHMARK-STABILITY floor, not a
# data-adequacy one (see the composition-dependence note in the module docstring):
# the leave-one-out baseline is only as meaningful as the set it averages, and a
# 3-sector benchmark is one absent sector away from being a different benchmark.
# Decision gate G-12 — set to 5 of the 8 configured sectors.
MIN_SECTORS_FOR_CROSS_SECTION = 5

# Symmetric clamp mapping a signed leave-one-out excess-% to [0,1], mirroring
# `catalyst_screen.momentum_score`/`relative_strength_score`.
#
# **This cap is the de facto WEIGHT of the whole global signal, not a guard rail.**
# `tone` contributes `tone/len(COMPONENTS)` to the composite, so a saturated
# reading moves a candidate by up to +/-0.0625 — plausibly over half the spread of
# a nominated pool. Too tight and every reading saturates and the component
# dominates; too wide and every reading sits inert near 0.5. It cannot be settled
# on one session's data: it needs the measured cross-sector dispersion across ~10
# sessions, targeting saturation below ~10% of sector-days — the same discipline
# every other capped scorer in this system is held to.
#
# `saturated` is stamped per sector and counted per session precisely so that
# settlement is mechanical rather than a judgement call.
# Decision gate G-13 — proposed, not confirmed; DO NOT tune on a single morning.
EXCESS_CAP_PCT = 1.5

# Band edge for the describe-only `global_risk_tone` label, in mean regional %.
# Banded rather than a bare sign test, for the same reason `rate_decomposition`'s
# `dominant_driver` is banded: a bare comparison flips the label on a fraction of
# a basis point. Decision gate G-14 — proposed, not confirmed.
RISK_TONE_BAND_PCT = 0.75

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
    risk_tone_band_pct: float = RISK_TONE_BAND_PCT,
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
    # BREADTH and MAGNITUDE are complementary and neither substitutes for the
    # other: every region at -0.1% and every region at -3.0% share a breadth of
    # 0.0, and a broad risk-off morning is real information for a long-only
    # multi-day sleeve. One region at -5% with the rest flat is the mirror case
    # (high breadth, meaningful magnitude). Both are emitted; both describe-only.
    region_pcts = [v["pct"] for v in regions.values()]
    region_breadth = (
        round(sum(1 for p in region_pcts if p > 0) / len(region_pcts), 4)
        if region_pcts else None
    )
    region_mean = (
        round(sum(region_pcts) / len(region_pcts), 4) if region_pcts else None
    )
    risk_tone = None
    if region_mean is not None:
        band = abs(float(risk_tone_band_pct))
        risk_tone = ("risk_off" if region_mean <= -band
                     else "risk_on" if region_mean >= band
                     else "neutral")

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
    sector_mean_pct: float | None = None
    # `max(2, ...)` is a structural floor, not a tunable: leave-one-out needs at
    # least one OTHER sector to average, so n-1 must be >= 1. G-12 sits well
    # above it for benchmark-stability reasons, but the guard stands on its own
    # so a caller passing a lower `min_sectors` cannot divide by zero.
    if len(raw_sector) >= max(2, int(min_sectors)):
        n = len(raw_sector)
        total = sum(v["pct"] for v in raw_sector.values())
        sector_mean_pct = round(total / n, 4)          # narration only
        cap = abs(float(excess_cap_pct))
        for sector, v in raw_sector.items():
            # LEAVE-ONE-OUT: the sector is never part of the benchmark it is
            # measured against. A mean-inclusive baseline shrinks every reading
            # by (n-1)/n, so the same divergence would score ~31% differently at
            # n=3 than at n=8 — coverage, not signal. See the module docstring.
            loo_baseline = round((total - v["pct"]) / (n - 1), 4)
            excess = round(v["pct"] - loo_baseline, 4)
            sectors[sector] = {
                **v,
                "baseline_pct": loo_baseline,
                "excess_pct": excess,
                "tone": excess_score(excess, cap),
                # Stamped so G-13 can be settled by COUNTING saturated
                # sector-days across ~10 sessions rather than by judgement.
                "saturated": abs(excess) >= cap,
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
        "region_mean_pct": region_mean,
        "global_risk_tone": risk_tone,
        "sector_tone": sectors,
        # Narration only — NOT the baseline anything is scored against. Each
        # sector carries its own `baseline_pct` (leave-one-out).
        "sector_mean_pct": sector_mean_pct,
        "structurally_absent_sectors": absent_sectors,
        "unavailable_reason": thin_reason,
        "basis": {
            "scored_on": "leave_one_out_cross_sector_excess",
            "excess_cap_pct": excess_cap_pct,
            "min_sectors_for_cross_section": int(min_sectors),
            "risk_tone_band_pct": risk_tone_band_pct,
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
            "sectors_saturated": sum(1 for v in sectors.values() if v["saturated"]),
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
    "RISK_TONE_BAND_PCT",
    "SOURCE_BASES",
]
