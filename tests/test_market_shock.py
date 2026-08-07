"""Unit tests for _build_market_shock's news channel (2026-08-06 audit B3).

Before this fix the news channel triggered off ABSOLUTE daily keyword-hit
counts (>=20/15/5) with no baseline, so a persistent multi-week theme (e.g.
Iran/Hormuz, 130-147 hits/day) alone pinned shock_level at 3 even on a benign
tape (SPY up, VIX down) — lifting the cash-sleeve ceiling to shock3_ceiling
(25%) every session and suppressing the de-cash deployment mandate.

Run: PYTHONPATH=src pytest tests/test_market_shock.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_market_shock, _load_risk_limits  # noqa: E402


class _FakeFMP:
    """Stub FMPClient exposing only get_historical_price_light."""

    def __init__(self, rows: list[dict]):
        self._rows = rows

    def get_historical_price_light(self, symbol: str) -> list[dict]:
        return self._rows


def _spy_rows(today_pct: float, background_pct: float = 0.08, n: int = 65) -> list[dict]:
    """Newest-first SPY closes: index 0 is "today" (1d return == today_pct vs
    index 1); indices 1..n-1 alternate +/-background_pct day-over-day so the
    60d realized-vol baseline is well-defined and not dominated by the test
    day's own move (unless today_pct is itself the dominant outlier, which is
    exactly what the "genuine price shock" test wants)."""
    hist = [100.0]
    p = 100.0
    for i in range(n - 1):
        sign = 1 if i % 2 == 0 else -1
        p = p / (1 + sign * background_pct / 100.0)
        hist.append(p)
    p0 = hist[0] * (1 + today_pct / 100.0)
    closes = [p0] + hist
    return [{"date": f"2026-08-{(1 + i):02d}", "price": c} for i, c in enumerate(closes)]


def _vix_rows(latest: float, pct_1d: float) -> list[dict]:
    prev = latest / (1 + pct_1d / 100.0)
    return [{"date": "2026-08-05", "value": str(latest)},
            {"date": "2026-08-04", "value": str(prev)}]


def _news_items(n: int, keyword: str = "missile strike") -> list[dict]:
    return [{"headline": f"Iran {keyword} update {i}", "summary": ""} for i in range(n)]


def _history_day(total_hits: int, dominant: str | None, floor_hits: int) -> dict:
    return {
        "date": "2026-07-01",
        "total_hits": total_hits,
        "hits_by_category": {dominant: floor_hits} if dominant else {},
        "dominant_category": dominant,
    }


def test_heavy_news_benign_tape_capped_at_elevated_not_acute():
    """130 news hits (a fresh geopolitical spike, day 1 — no persistence yet)
    against a LOW historical baseline (~10/day) produces a genuinely elevated
    z-score, but with the price channel benign (SPY 1d z ~2.0, still only
    'watch'-tier — no elevated/acute price signal) shock_level must cap at 2,
    never reach 3 (fails today: returns 3 off total_hits>=25 alone)."""
    fmp = _FakeFMP(_spy_rows(today_pct=2.0, background_pct=1.0))  # -> price_level 1
    macro = {"VIXCLS": _vix_rows(14.0, -6.0)}
    history = [_history_day(10, "geopolitical", 10) for _ in range(15)]
    result = _build_market_shock(
        fmp=fmp, macro_data=macro,
        market_news=_news_items(130), forex_news=[], stock_news=[], company_news={},
        bond_signals=None, news_hits_history=history,
    )
    assert result["news_hits_total"] == 130
    assert result["price_level"] <= 1
    assert result["news_hits_zscore"] is not None and result["news_hits_zscore"] >= 3.5
    assert result["shock_level"] <= 2, result


def test_persistent_theme_benign_tape_capped_at_watch():
    """The SAME dominant news category has held >=15 hits/day for 15 straight
    sessions (an Iran/Hormuz-style standing theme) and today spikes further —
    with the price channel benign, shock_level must cap at 1, not just 2
    (fails today: returns 3)."""
    fmp = _FakeFMP(_spy_rows(today_pct=0.3, background_pct=1.0))  # -> price_level 0
    macro = {"VIXCLS": _vix_rows(14.0, -2.0)}
    history = [_history_day(20, "geopolitical", 20) for _ in range(15)]
    result = _build_market_shock(
        fmp=fmp, macro_data=macro,
        market_news=_news_items(150), forex_news=[], stock_news=[], company_news={},
        bond_signals=None, news_hits_history=history,
    )
    assert result["news_persistent_theme_streak"] >= 10
    assert result["price_level"] <= 1
    assert result["shock_level"] <= 1, result


def test_genuine_price_shock_still_returns_acute_regardless_of_news():
    """A real price shock (SPY 1d return dominates its own 60d vol window,
    |z| far past 3.5) must still return shock_level 3 regardless of the news
    channel — no regression from the B3 fix."""
    fmp = _FakeFMP(_spy_rows(today_pct=-8.0, background_pct=0.2))
    macro = {"VIXCLS": _vix_rows(45.0, 60.0)}
    result = _build_market_shock(
        fmp=fmp, macro_data=macro,
        market_news=[], forex_news=[], stock_news=[], company_news={},
        bond_signals=None, news_hits_history=[],
    )
    assert abs(result["spy"]["return_1d_zscore"]) >= 3.5
    assert result["shock_level"] == 3


def test_reduced_shock_level_yields_operative_ceiling_below_25pct():
    """Downstream consequence: with the fix, a benign-tape/heavy-news session's
    reduced shock_level must resolve the cash-sleeve operative ceiling to the
    normal 15% band, not the shock3_ceiling 25%."""
    fmp = _FakeFMP(_spy_rows(today_pct=2.0, background_pct=1.0))
    macro = {"VIXCLS": _vix_rows(14.0, -6.0)}
    history = [_history_day(10, "geopolitical", 10) for _ in range(15)]
    result = _build_market_shock(
        fmp=fmp, macro_data=macro,
        market_news=_news_items(130), forex_news=[], stock_news=[], company_news={},
        bond_signals=None, news_hits_history=history,
    )
    assert result["shock_level"] < 3
    cash_band = _load_risk_limits()["cash_sleeve_band_pct"]
    operative_ceiling = (
        float(cash_band["shock3_ceiling"]) if result["shock_level"] == 3
        else float(cash_band["ceiling"])
    )
    assert operative_ceiling < 25.0
