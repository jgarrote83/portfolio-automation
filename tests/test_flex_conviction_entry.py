"""Task E (2026-08-14 flex-conviction-path cycle) — build_conviction_entry,
the conviction-path Layer 2 gate sequence: no-chase entry cap (no VWAP-rising/
gap logic at all), invalidation-level stop (bounded by conviction_max_stop_pct
= 10.0, vs the catalyst profile's 4.0), and size_mult-scaled risk-budget
sizing. Mirrors test_flex_entry.py's style for the sibling catalyst-path
pipeline.

Run: PYTHONPATH=src pytest tests/test_flex_conviction_entry.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flex.config import FlexConfig  # noqa: E402
from flex.entry import build_conviction_entry, cash_accommodation_shares  # noqa: E402

CFG = FlexConfig()
EQUITY = 1_000_000.0


def _daily(n=20, base=100.0, rng=0.6, v=1_000_000):
    return [{"o": base, "h": base + rng / 2, "l": base - rng / 2, "c": base, "v": v} for _ in range(n)]


def _intraday(closes, rng=0.2, v=1000):
    return [{"o": c, "h": c + rng / 2, "l": c - rng / 2, "c": c, "v": v} for c in closes]


def _run(intraday, daily, invalidation=95.0, size_mult=1.0, sector="Technology",
         quadrant="Q1", minutes=45, **kw):
    return build_conviction_entry(
        {"symbol": "AVGO", "sector": sector, "invalidation": invalidation},
        intraday, daily, quadrant, EQUITY, minutes, CFG, size_mult, **kw,
    )


def test_pass_flat_price_within_no_chase_limit_and_sane_invalidation():
    r = _run(_intraday([100] * 7), _daily())
    assert r["entry_trigger"] == "pass"
    assert r["path"] == "conviction"
    assert r["size_shares"] >= 1
    assert r["stop_price"] == 95.0
    assert r["binding"] in ("risk_budget", "per_name_cap")


def test_entry_above_no_chase_limit_skips():
    # Flat at 100 for six bars then a sharp pop to 110 -- VWAP stays anchored
    # near 100 while the last close is well above vwap + 1 ATR.
    r = _run(_intraday([100, 100, 100, 100, 100, 100, 110]), _daily())
    assert r["entry_trigger"] == "fail"
    assert r["skip_reason"] == "entry_above_no_chase_limit"


def test_no_invalidation_level_skips():
    r = _run(_intraday([100] * 7), _daily(), invalidation=None)
    assert r["skip_reason"] == "no_invalidation_level"


def test_invalidation_above_entry_price_is_invalid():
    r = _run(_intraday([100] * 7), _daily(), invalidation=101.0)
    assert r["skip_reason"] == "invalid_invalidation_level"


def test_invalidation_zero_or_negative_is_invalid():
    r = _run(_intraday([100] * 7), _daily(), invalidation=0.0)
    assert r["skip_reason"] == "invalid_invalidation_level"


def test_stop_too_wide_skips_independent_of_atr():
    # Entry ~100, invalidation 85 -> 15% stop, over the 10.0% conviction cap --
    # independent of the daily bars' own ATR (unlike the catalyst profile).
    r = _run(_intraday([100] * 7), _daily(), invalidation=85.0)
    assert r["skip_reason"] == "stop_too_wide"


def test_stop_within_ten_percent_cap_passes():
    # Entry ~100, invalidation 91 -> 9% stop, under the 10.0% cap.
    r = _run(_intraday([100] * 7), _daily(), invalidation=91.0)
    assert r["entry_trigger"] == "pass"


def test_liquidity_below_min_rejected():
    r = _run(_intraday([100] * 7), _daily(v=1))
    assert r["skip_reason"] == "liquidity_below_min"


def test_pre_window_and_after_cutoff():
    bars = _intraday([100] * 7)
    assert _run(bars, _daily(), minutes=10)["skip_reason"] == "pre_window"
    assert _run(bars, _daily(), minutes=120)["skip_reason"] == "after_cutoff"


def test_no_gap_or_vwap_rising_fields_required():
    # A DECLINING intraday sequence -- would fail the catalyst profile's
    # above_vwap/rising-slope gates -- must still pass here (no such gates).
    r = _run(_intraday([101, 100.8, 100.6, 100.4, 100.2, 100, 99.8]), _daily())
    assert r["entry_trigger"] == "pass"


def test_size_mult_scales_shares_down():
    full = _run(_intraday([100] * 7), _daily(), size_mult=1.0)
    half = _run(_intraday([100] * 7), _daily(), size_mult=0.5)
    assert half["size_shares"] < full["size_shares"]


def test_size_mult_zero_yields_zero_shares():
    r = _run(_intraday([100] * 7), _daily(), size_mult=0.0)
    assert r["skip_reason"] == "size_zero"
    assert r["size_shares"] == 0


def test_no_bars_skips():
    r = build_conviction_entry(
        {"symbol": "AVGO", "sector": "Technology", "invalidation": 95.0},
        [], [], "Q1", EQUITY, 45, CFG, 1.0,
    )
    assert r["skip_reason"] == "no_bars"


# --- B5 cash accommodation ---------------------------------------------------

def test_cash_accommodation_no_clamp_when_room_is_ample():
    out = cash_accommodation_shares(
        proposed_shares=100, entry_price=100.0,
        literal_cash_usd=50_000.0, sgov_usd=20_000.0, equity=EQUITY, cfg=CFG,
    )
    assert out == {"shares": 100, "funding_clamped": False}


def test_cash_accommodation_clamps_when_literal_cash_thin():
    # literal_cash_floor_pct=0.75% of 1,000,000 = 7,500. Only 8,000 literal
    # cash available -> room = 500 -> 5 shares at $100, well under 100 proposed.
    out = cash_accommodation_shares(
        proposed_shares=100, entry_price=100.0,
        literal_cash_usd=8_000.0, sgov_usd=100_000.0, equity=EQUITY, cfg=CFG,
    )
    assert out["funding_clamped"] is True
    assert out["shares"] == 5


def test_cash_accommodation_clamps_when_cash_sleeve_thin():
    # cash_sleeve_floor_pct=5.0% of 1,000,000 = 50,000. literal+SGOV = 51,000
    # -> sleeve room = 1,000 -> 10 shares, binding even though literal cash
    # alone (30,000) has plenty of room -- the sleeve-level floor governs.
    out = cash_accommodation_shares(
        proposed_shares=100, entry_price=100.0,
        literal_cash_usd=30_000.0, sgov_usd=21_000.0, equity=EQUITY, cfg=CFG,
    )
    assert out["funding_clamped"] is True
    assert out["shares"] == 10


def test_cash_accommodation_zero_room_yields_zero_shares():
    out = cash_accommodation_shares(
        proposed_shares=100, entry_price=100.0,
        literal_cash_usd=1_000.0, sgov_usd=0.0, equity=EQUITY, cfg=CFG,
    )
    assert out == {"shares": 0, "funding_clamped": True}


def test_cash_accommodation_zero_proposed_shares_is_not_clamped():
    out = cash_accommodation_shares(
        proposed_shares=0, entry_price=100.0,
        literal_cash_usd=1_000.0, sgov_usd=0.0, equity=EQUITY, cfg=CFG,
    )
    assert out == {"shares": 0, "funding_clamped": False}


def test_build_conviction_entry_wires_cash_accommodation_end_to_end():
    r = _run(
        _intraday([100] * 7), _daily(), size_mult=1.0,
        literal_cash_usd=8_000.0, sgov_usd=0.0,
    )
    assert r["funding_clamped"] is True
    assert r["binding"] == "cash_floor"
