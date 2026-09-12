"""Pipeline tests for build_flex_entry — the deterministic confirmation gates.

Run: PYTHONPATH=src pytest tests/test_flex_entry.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flex.config import FlexConfig  # noqa: E402
from flex.entry import build_flex_entry  # noqa: E402

CFG = FlexConfig()
EQUITY = 1_000_000.0


def _daily(n=20, base=100.0, rng=0.6, v=1_000_000):
    return [{"o": base, "h": base + rng / 2, "l": base - rng / 2, "c": base, "v": v} for _ in range(n)]


def _intraday(closes, rng=0.2, v=1000):
    return [{"o": c, "h": c + rng / 2, "l": c - rng / 2, "c": c, "v": v} for c in closes]


def _run(intraday, daily, sector="Technology", minutes=45, minutes_remaining=200):
    return build_flex_entry(
        {"symbol": "NVDA", "sector": sector},
        intraday, daily, EQUITY, minutes, CFG,
        session_minutes_remaining=minutes_remaining,
    )


def test_pass_above_rising_vwap_acceptable_stop():
    r = _run(_intraday([100, 100.5, 101, 101.5, 102, 102.5, 103]), _daily())
    assert r["entry_trigger"] == "pass"
    assert r["size_shares"] >= 1
    assert r["above_vwap"] is True
    assert r["binding"] in ("risk_budget", "per_name_cap")


def test_below_vwap_fails():
    r = _run(_intraday([103, 102.5, 102, 101.5, 101, 100.5, 100]), _daily())
    assert r["entry_trigger"] == "fail"
    assert r["skip_reason"] == "below_vwap"


def test_wide_atr_stop_is_CLAMPED_to_the_cap_not_skipped():
    """N2 (2026-09-12): `max_stop_pct` clamps, it no longer skips.

    With `atr_mult` 3.0 and the new 1.5% cap, a typical liquid name's ATR stop is
    ~4x the cap — a literal "skip if wider" would have rejected essentially every
    candidate, turning zero-nominations into zero-entries. The clamp is also what
    makes the +2%/-1.5% expectancy arithmetic true, and the bracket's resting stop
    leg is a fixed price either way."""
    r = _run(_intraday([100, 100.5, 101, 101.5, 102, 102.5, 103]), _daily(rng=12.0))
    assert r["entry_trigger"] == "pass"
    assert r["skip_reason"] is None
    assert round(r["stop_pct"], 4) == 1.5          # clamped exactly to the cap
    assert r["stop_clamped_to_cap"] is True


def test_structure_may_make_the_stop_TIGHTER_than_the_cap_never_wider():
    """The clamp is a ceiling on stop WIDTH, not a fixed distance: a nearby
    structure low still gives a better R when it is closer than the cap."""
    r = _run(_intraday([100, 100.5, 101, 101.5, 102, 102.5, 103]), _daily(rng=0.05))
    assert r["stop_pct"] <= 1.5 + 1e-9


def test_liquidity_below_min_rejected():
    r = _run(_intraday([100, 100.5, 101, 101.5, 102, 102.5, 103]), _daily(v=1))
    assert r["skip_reason"] == "liquidity_below_min"


def test_entry_window_is_all_day_with_a_late_cutoff_only():
    """N3 (2026-09-12): the morning-only 90-minute cutoff is GONE — news arrives
    all day. Two structural bounds remain: the VWAP window at the open, and a
    late cutoff so a position is never opened with no time to work."""
    bars = _intraday([100, 100.5, 101, 101.5, 102, 102.5, 103])
    assert _run(bars, _daily(), minutes=10)["skip_reason"] == "pre_window"
    # Mid-afternoon (formerly "after_cutoff") now ENTERS.
    assert _run(bars, _daily(), minutes=300)["entry_trigger"] == "pass"
    # ...but not inside the last 30 minutes.
    late = _run(bars, _daily(), minutes=380, minutes_remaining=10)
    assert late["skip_reason"] == "too_close_to_close"


def test_missing_clock_does_not_apply_a_late_bound_nor_block_every_entry():
    """A missing minutes-to-close must degrade to the pre-N3 behaviour (no late
    bound), never silently block the sleeve. The handler logs it."""
    bars = _intraday([100, 100.5, 101, 101.5, 102, 102.5, 103])
    r = _run(bars, _daily(), minutes=380, minutes_remaining=None)
    assert r["entry_trigger"] == "pass"


def test_sector_never_gates_entry():
    """R1 (2026-09-12) — regime is GONE from the flex sleeve.

    Ported from the deleted `test_flex_quadrant_resolution.py`, which pinned the
    2026-08-10 demotion of `regime_fit` from a hard veto to an informational
    field. The demotion's own reasoning ("a monthly-vintage macro quadrant has no
    business vetoing a multi-day trade — cadence mismatch") is now applied in
    full: no sector, no quadrant, and no regime field participates in entry
    admission at all.

    Any sector, with otherwise-clean structure, must reach sizing —
    liquidity/window/VWAP/stop govern, and nothing else does. `regime_fit` must
    no longer exist in the output at all, so a future re-introduction of a
    sector/regime veto fails here."""
    for sector in ("Utilities", "Technology", "Consumer Defensive", None, "Nonsense Sector"):
        r = _run(_intraday([100, 100.5, 101, 101.5, 102, 102.5, 103]), _daily(), sector=sector)
        assert "regime_fit" not in r, f"regime_fit resurfaced for {sector!r}"
        assert "quadrant" not in r and "quadrant_basis" not in r
        assert r["entry_trigger"] == "pass", sector
        assert r["skip_reason"] is None
        assert r["size_shares"] >= 1


def test_big_gap_strong_vwap_passes():
    # Open gaps +2% (gap_in_adr ~3.3 > 2) but holds a rising VWAP well above → pass.
    r = _run(_intraday([102, 102.4, 102.8, 103.2, 103.6, 104.0, 104.4]), _daily())
    assert r["gap_in_adr"] > CFG.gap_adr_mult
    assert r["entry_trigger"] == "pass"


def test_big_gap_vwap_fail_skips():
    r = _run(_intraday([102, 101.5, 101, 100.5, 100, 99.5, 99]), _daily())
    assert r["gap_in_adr"] > CFG.gap_adr_mult
    assert r["skip_reason"] == "below_vwap"


def test_big_gap_weak_hold_skips():
    # Big gap, above VWAP but only barely (< 0.1×ATR) → weak hold, not a clean entry.
    r = _run(_intraday([102, 102, 102, 102, 102, 102, 102.02]), _daily(rng=0.5))
    assert r["gap_in_adr"] > CFG.gap_adr_mult
    assert r["above_vwap"] is True
    assert r["skip_reason"] == "big_gap_weak_hold"
