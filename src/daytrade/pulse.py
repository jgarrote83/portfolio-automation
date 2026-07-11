"""Global Sector Pulse (pure) — spec §10 (addendum). Cross-region sector/volume
read from US-listed proxies only (ETFs + lead ADRs), all off the existing Alpaca
IEX extended-hours bars — zero new data vendors, no FMP tier risk.

Doctrine: the ENGINE computes the pulse, the LLM (future scan amendment) reads
it. It is regime CONTEXT that shapes the shortlist and raises/lowers the
confirmation bar — it is NEVER a catalyst and never bypasses a gate. ADR
pre-market change embeds the completed overseas session (that is the point);
the block self-describes this so a reader can't misread it.
"""
from __future__ import annotations

import re

_BASIS_IEX_RATIO = "iex_ratio"

# v1 proxy map (spec §10.1) — US-listed only, ≤ ~40 symbols. Overridable via the
# DAYTRADE_SECTOR_PULSE_MAP env (JSON of the same shape).
SECTOR_PULSE_MAP: dict = {
    "regions": {
        "japan": ["EWJ"], "korea": ["EWY"], "taiwan": ["EWT"],
        "china_hk": ["FXI"], "europe": ["FEZ"], "germany": ["EWG"],
    },
    "sectors": {
        # semis: ETFs + lead ADRs (TSM carries the Taiwan close, ASML the live EU session)
        "semis": ["SMH", "SOXX", "TSM", "ASML"],
        "software_tech": ["XLK", "SAP"],
        "energy": ["XLE", "XOP", "BNO", "USO"],
        "banks": ["KBE", "EUFN"],
        "autos_ev": ["TM", "HMC"],
        "consumer_luxury": ["XLY"],   # LVMH has no clean US listing — known gap
        "defense": ["ITA", "EUAD"],
        "gold_miners": ["GDX", "GLD"],
    },
    # Index tone anchors — reused by the #34 tone block (same fetch, two blocks).
    "index_anchors": ["SPY", "QQQ"],
}

# FMP profile sector/industry → pulse sector key (engine-side alignment join).
_INDUSTRY_TO_PULSE = (
    (re.compile(r"semiconductor", re.I), "semis"),
    (re.compile(r"software|information technology|it services", re.I), "software_tech"),
    (re.compile(r"oil|gas|energy|coal|drilling", re.I), "energy"),
    (re.compile(r"bank|capital markets|credit", re.I), "banks"),
    (re.compile(r"auto|vehicle", re.I), "autos_ev"),
    (re.compile(r"luxury|apparel|retail|restaurant|leisure", re.I), "consumer_luxury"),
    (re.compile(r"aerospace|defense", re.I), "defense"),
    (re.compile(r"gold|silver|precious", re.I), "gold_miners"),
)
_SECTOR_TO_PULSE = (
    (re.compile(r"^energy$", re.I), "energy"),
    (re.compile(r"^financial", re.I), "banks"),
    (re.compile(r"^technology$", re.I), "software_tech"),
    (re.compile(r"^consumer cyclical$", re.I), "consumer_luxury"),
)

# uncited_catalyst guardrail vocabulary (spec §10.3): a note built ONLY from
# these words cites no catalyst — the pulse is context, never a catalyst.
_PULSE_VOCAB = frozenset({
    "global", "sector", "pulse", "strength", "strong", "momentum", "tailwind",
    "tailwinds", "sympathy", "market", "up", "green", "risk", "on", "risk_on",
    "hot", "group", "theme", "move", "running", "overseas", "asia", "europe",
    "the", "a", "an", "is", "are", "with", "and", "of", "in", "today",
})


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_sector_pulse_map() -> dict:
    """The proxy map, overridable via the DAYTRADE_SECTOR_PULSE_MAP env (JSON of
    the same shape) without a code change. Bad JSON ⇒ default map, logged."""
    import json
    import logging
    import os
    raw = os.getenv("DAYTRADE_SECTOR_PULSE_MAP")
    if not raw:
        return SECTOR_PULSE_MAP
    try:
        m = json.loads(raw)
        if isinstance(m, dict) and (m.get("sectors") or m.get("regions")):
            return m
    except json.JSONDecodeError:
        pass
    logging.getLogger(__name__).warning(
        "Bad DAYTRADE_SECTOR_PULSE_MAP — using the default map")
    return SECTOR_PULSE_MAP


def all_pulse_symbols(pulse_map: dict | None = None) -> list[str]:
    m = pulse_map or SECTOR_PULSE_MAP
    syms: set[str] = set(m.get("index_anchors") or [])
    for group in ("regions", "sectors"):
        for members in (m.get(group) or {}).values():
            syms.update(members)
    return sorted(syms)


def _symbol_row(bars: list[dict], prior_close: float | None,
                pm_vol_30d_avg: float | None, now_epoch_s: float,
                stale_after_s: float) -> dict:
    """Per-symbol pre-market read. ``stale`` = no print in the last hour."""
    last_px = None
    last_ts = None
    vol = 0.0
    dollar = 0.0
    for b in bars or []:
        c, v = _f(b.get("c")), _f(b.get("v"))
        if c is not None:
            last_px = c
            last_ts = str(b.get("t", ""))
        if v is not None:
            vol += v
            if c is not None:
                dollar += v * c
    stale = True
    if last_ts:
        from datetime import datetime
        try:
            t = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            stale = (now_epoch_s - t.timestamp()) > stale_after_s
        except ValueError:
            stale = True
    change = None
    if last_px is not None and prior_close and prior_close > 0:
        change = round((last_px - prior_close) / prior_close * 100.0, 3)
    ratio = None
    if vol and pm_vol_30d_avg and pm_vol_30d_avg > 0:
        ratio = round(vol / pm_vol_30d_avg, 2)
    return {
        "pm_change_pct": change,
        "pm_dollar_volume": round(dollar, 0) or None,
        "pm_vol_vs_30d_ratio": ratio,
        "basis": _BASIS_IEX_RATIO,
        "stale": stale or change is None,
    }


def _classify(avg: float | None, breadth: float | None, cfg) -> str:
    """Discrete pulse from config thresholds; unmeasurable ⇒ mixed, never a guess."""
    if avg is None or breadth is None:
        return "mixed"
    if avg >= cfg.pulse_strong_pct and breadth >= cfg.pulse_strong_breadth:
        return "strong_up"
    if avg <= -cfg.pulse_strong_pct and breadth <= (1.0 - cfg.pulse_strong_breadth):
        return "strong_down"
    if avg >= cfg.pulse_up_pct:
        return "up"
    if avg <= -cfg.pulse_up_pct:
        return "down"
    return "mixed"


def _group_row(members: list[str], symbols: dict, cfg) -> dict:
    live = [s for s in members if s in symbols and not symbols[s]["stale"]]
    stale_members = [s for s in members if s not in live]
    changes = [symbols[s]["pm_change_pct"] for s in live
               if symbols[s]["pm_change_pct"] is not None]
    avg = round(sum(changes) / len(changes), 3) if changes else None
    breadth = round(sum(1 for c in changes if c > 0) / len(changes), 2) if changes else None
    volume_flag = any(
        (symbols[s].get("pm_vol_vs_30d_ratio") or 0) >= cfg.pulse_volume_ratio
        for s in live)
    return {
        "members": members,
        "avg_change_pct": avg,
        "breadth": breadth,
        "volume_flag": volume_flag,
        "pulse": _classify(avg, breadth, cfg),
        "stale_members": stale_members,
    }


def build_sector_pulse(
    bars_by_symbol: dict[str, list[dict]],
    prior_closes: dict[str, float],
    pm_vol_30d_avgs: dict[str, float],
    cfg,
    now_epoch_s: float,
    pulse_map: dict | None = None,
    as_of: str = "",
) -> dict:
    """The compact `sector_pulse` block (spec §10.2). Pure; ~1–2K tokens.

    Stale/missing inputs drop out of the aggregates and are LISTED, degrading
    confidence — never a fabricated pulse (house rule).
    """
    m = pulse_map or SECTOR_PULSE_MAP
    symbols = {
        sym: _symbol_row(bars_by_symbol.get(sym) or [],
                         prior_closes.get(sym), pm_vol_30d_avgs.get(sym),
                         now_epoch_s, cfg.pulse_stale_after_s)
        for sym in all_pulse_symbols(m)
    }
    return {
        "as_of": as_of,
        "self_description": (
            "Deterministic pre-open global read from US-listed proxies (IEX "
            "extended-hours). ADR pm change EMBEDS the overseas session. Asia "
            "values = completed sessions (final); Europe = live mid-session (can "
            "reverse); US pre-market = thin IEX tape (directional only). Context "
            "only — never a catalyst; volume fields are IEX/IEX ratios."),
        "symbols": symbols,
        "sectors": {k: _group_row(v, symbols, cfg)
                    for k, v in (m.get("sectors") or {}).items()},
        "regions": {k: _group_row(v, symbols, cfg)
                    for k, v in (m.get("regions") or {}).items()},
    }


def pulse_sector_for(sector: str | None, industry: str | None) -> str | None:
    """FMP profile sector/industry → pulse sector key (None ⇒ alignment 'na').

    Industry match wins (finer-grained: 'Semiconductors' beats 'Technology')."""
    for rx, key in _INDUSTRY_TO_PULSE:
        if industry and rx.search(industry):
            return key
    for rx, key in _SECTOR_TO_PULSE:
        if sector and rx.search(sector):
            return key
    return None


def pulse_alignment(pulse_doc: dict | None, sector: str | None,
                    industry: str | None) -> tuple[bool | None, str | None]:
    """Engine-side alignment stamp at entry (spec §10.4): join the candidate's
    sector to the persisted pulse. Returns ``(aligned, pulse_value)`` where
    aligned is True (pulse up/strong_up), False (down/strong_down), or None
    ('na' — mixed pulse, unknown sector, or no pulse persisted)."""
    key = pulse_sector_for(sector, industry)
    if not key or not isinstance(pulse_doc, dict):
        return None, None
    row = (pulse_doc.get("sectors") or {}).get(key)
    if not isinstance(row, dict):
        return None, None
    pulse = row.get("pulse")
    if pulse in ("up", "strong_up"):
        return True, pulse
    if pulse in ("down", "strong_down"):
        return False, pulse
    return None, pulse


def is_pulse_only_catalyst(note: str | None) -> bool:
    """uncited_catalyst guardrail (spec §10.3): True when a NON-EMPTY note is
    built entirely from pulse/strength vocabulary — i.e. it cites nothing.

    (An empty note is allowed in v0.1's manual flow — the account holder is the
    citation; the scan amendment will tighten this to a dated, sourced <24h
    requirement when it lands.)"""
    if not note or not note.strip():
        return False
    words = re.findall(r"[a-z0-9_']+", note.lower())
    if not words:
        return True
    return all(w in _PULSE_VOCAB for w in words)
