"""Deterministic validation gates (pure) — spec §3, discard-by-default.

The handler assembles a ``data`` dict per candidate from broker/FMP fetches;
this module only evaluates. Every gate result is recorded as
``{gate, value, threshold, basis, passed}`` so a future feed upgrade can never
silently re-grade history (spec §2). First failure discards with a
``discard_reason``; missing data DISCARDS (``missing_data`` /
``consolidated_unmeasured`` / ``filings_unavailable``) — deliberately the
opposite of the flex WATCH rule: a scanner with surplus candidates fails closed,
and the logged reason keeps a data gap from masquerading as a merits finding.
"""
from __future__ import annotations

_BASIS_IEX_RATIO = "iex_ratio"
_BASIS_IEX_QUOTE = "iex_quote"
_BASIS_UNMEASURED = "unmeasured"


def _num(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _gate(name: str, value, threshold, basis: str, passed: bool) -> dict:
    return {"gate": name, "value": value, "threshold": threshold,
            "basis": basis, "passed": bool(passed)}


def run_validation_gates(candidate: dict, data: dict, cfg) -> dict:
    """Evaluate one candidate. ``data`` keys (handler-assembled):

    ``in_core, in_catalyst_ledger, in_daytrade_ledger, is_common, prior_close,
    pm_last, pm_iex_volume, pm_iex_vol_30d_avg, pm_dollar_volume (consolidated,
    None=unmeasured), float_shares, market_cap, dilution_flag (True/False/None=
    endpoint unavailable), bid, ask, prior_day_high, prior_day_low, pm_high,
    pm_low``.
    """
    symbol = str(candidate.get("symbol") or "").upper()
    out: dict = {
        "symbol": symbol,
        "catalyst_class": candidate.get("catalyst_class"),
        "survivor": False,
        "discard_reason": None,
        "gates": [],
        "levels": None,
        "rvol": None,
        "float_rotation": None,
        "gap_pct": None,
        "spread_pct": None,
        "bases": {},
    }
    gates: list[dict] = out["gates"]

    def _discard(reason: str) -> dict:
        out["discard_reason"] = reason
        return out

    # ── Gate 1 — exclusivity, instrument type, price band ───────────────────
    excl = not (data.get("in_core") or data.get("in_catalyst_ledger")
                or data.get("in_daytrade_ledger"))
    gates.append(_gate("exclusivity", excl, True, "ledger", excl))
    if not excl:
        return _discard("core_or_catalyst_symbol")
    is_common = data.get("is_common")
    gates.append(_gate("us_common_stock", is_common, True, "fmp_profile",
                       is_common is True))
    if is_common is not True:
        return _discard("not_us_common_stock" if is_common is False else "missing_data")
    prior_close = _num(data.get("prior_close"))
    in_band = prior_close is not None and cfg.price_min <= prior_close <= cfg.price_max
    gates.append(_gate("price_band", prior_close,
                       [cfg.price_min, cfg.price_max], "prior_close", in_band))
    if prior_close is None:
        return _discard("missing_data")
    if not in_band:
        return _discard("price_out_of_band")

    # ── Gate 2 — gap up ≥ min ────────────────────────────────────────────────
    pm_last = _num(data.get("pm_last"))
    gap = None if pm_last is None else (pm_last - prior_close) / prior_close * 100.0
    out["gap_pct"] = gap
    passed = gap is not None and gap >= cfg.gap_min_pct
    gates.append(_gate("gap_up", gap, cfg.gap_min_pct, _BASIS_IEX_RATIO, passed))
    if gap is None:
        return _discard("missing_data")
    if gap < 0:
        return _discard("gap_down")   # → avoid-list log
    if not passed:
        return _discard("gap_below_min")

    # ── Gate 3 — RVOL (iex_ratio) + consolidated $-vol + float band/rotation ─
    pm_vol = _num(data.get("pm_iex_volume"))
    pm_avg = _num(data.get("pm_iex_vol_30d_avg"))
    rvol = pm_vol / pm_avg if pm_vol is not None and pm_avg and pm_avg > 0 else None
    out["rvol"] = rvol
    out["bases"]["rvol"] = _BASIS_IEX_RATIO
    passed = rvol is not None and rvol >= cfg.rvol_min
    gates.append(_gate("rvol", rvol, cfg.rvol_min, _BASIS_IEX_RATIO, passed))
    if rvol is None:
        return _discard("missing_data")
    if not passed:
        return _discard("rvol_below_min")

    # Consolidated-basis gates — measured only per cfg.consolidated_source (§2).
    consolidated = cfg.consolidated_source in ("fmp", "sip")
    basis_cons = cfg.consolidated_source if consolidated else _BASIS_UNMEASURED
    out["bases"]["pm_dollar_volume"] = basis_cons
    out["bases"]["float_rotation"] = basis_cons
    pm_usd = _num(data.get("pm_dollar_volume")) if consolidated else None
    if not consolidated or pm_usd is None:
        gates.append(_gate("pm_dollar_volume", pm_usd, cfg.pm_dollar_vol_min,
                           _BASIS_UNMEASURED, False))
        return _discard("consolidated_unmeasured")
    passed = pm_usd >= cfg.pm_dollar_vol_min
    gates.append(_gate("pm_dollar_volume", pm_usd, cfg.pm_dollar_vol_min,
                       basis_cons, passed))
    if not passed:
        return _discard("pm_dollar_volume_below_min")

    float_shares = _num(data.get("float_shares"))
    market_cap = _num(data.get("market_cap"))
    small_cap = market_cap is None or market_cap < cfg.small_cap_usd
    if float_shares is None:
        gates.append(_gate("float_band", None,
                           [cfg.float_min_shares, cfg.float_max_shares],
                           _BASIS_UNMEASURED, not small_cap))
        out["bases"]["float_rotation"] = _BASIS_UNMEASURED
        if small_cap:
            return _discard("missing_data")   # sub-$2B fails closed (spec §3.3)
        # ≥$2B with missing float: rotation unmeasured, tie-break falls to RVOL.
    else:
        in_band = cfg.float_min_shares <= float_shares <= cfg.float_max_shares
        gates.append(_gate("float_band", float_shares,
                           [cfg.float_min_shares, cfg.float_max_shares],
                           "fmp_float", in_band))
        if not in_band:
            return _discard("float_out_of_band")
        rotation = (pm_usd / pm_last) / float_shares if pm_last else None
        out["float_rotation"] = rotation
        passed = rotation is not None and rotation >= cfg.rotation_min
        gates.append(_gate("float_rotation", rotation, cfg.rotation_min,
                           basis_cons, passed))
        if not passed:
            return _discard("rotation_below_min")

    # ── Gate 4 — dilution overhang (sub-$2B only) ────────────────────────────
    if small_cap:
        flag = data.get("dilution_flag")
        gates.append(_gate("dilution_overhang", flag, False, "fmp_filings",
                           flag is False))
        if flag is None:
            return _discard("filings_unavailable")
        if flag:
            return _discard("dilution_overhang")

    # ── Gate 5 — spread (iex_quote ≈ NBBO) ───────────────────────────────────
    bid, ask = _num(data.get("bid")), _num(data.get("ask"))
    spread = None
    if bid and ask and ask > bid > 0:
        spread = (ask - bid) / ((ask + bid) / 2.0)
    out["spread_pct"] = spread
    out["bases"]["spread"] = _BASIS_IEX_QUOTE
    passed = spread is not None and spread <= cfg.spread_max
    gates.append(_gate("spread", spread, cfg.spread_max, _BASIS_IEX_QUOTE, passed))
    if spread is None:
        return _discard("missing_data")
    if not passed:
        return _discard("spread_too_wide")

    # ── Gate 6 — levels stored pre-open (ORB pending) ────────────────────────
    out["levels"] = {
        "prior_day_high": _num(data.get("prior_day_high")),
        "prior_day_low": _num(data.get("prior_day_low")),
        "premarket_high": _num(data.get("pm_high")),
        "premarket_low": _num(data.get("pm_low")),
        "orb_high": None,
        "orb_low": None,
    }

    out["survivor"] = True
    return out


def select_survivors(results: list[dict]) -> tuple[dict | None, dict | None]:
    """Gate 7 — tie-break by float rotation (RVOL when rotation unmeasured).

    Returns ``(primary, backup)``; either may be None (gate 8: zero survivors ⇒
    ``no_setup`` upstream).
    """
    survivors = [r for r in results if r.get("survivor")]

    def _key(r: dict) -> tuple:
        rot = r.get("float_rotation")
        # Measured rotation always outranks unmeasured; then by value; RVOL breaks.
        return (rot is not None, rot if rot is not None else 0.0, r.get("rvol") or 0.0)

    ranked = sorted(survivors, key=_key, reverse=True)
    primary = ranked[0] if ranked else None
    backup = ranked[1] if len(ranked) > 1 else None
    return primary, backup
