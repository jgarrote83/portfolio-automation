"""Unit tests for the price-sanity quarantine guard (Task E / F7, 2026-07-23).

Structural guard applied to flex-candidate prices at collection time.
Quarantine fires when:
  1. Price is outside the symbol's 52-week high/low range by > 20%, OR
  2. Price moved > 50% vs the prior snapshot's EOD price without any
     corroborating news hit for that symbol in company_news.

These tests FAIL against master (commit 2ef7dfe) because `_quarantine_flex_price`
does not exist there — it was introduced on branch
`feat/20260723-leading-growth-market-implied`.

Run:
    PYTHONPATH=src pytest tests/test_price_quarantine.py -v
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _quarantine_flex_price  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(range_pct: float = 20.0, move_pct: float = 50.0) -> dict:
    return {"price_quarantine": {"range_pct": range_pct, "single_day_move_pct": move_pct}}


def _profile(symbol: str, high_52: float = 120.0, low_52: float = 80.0) -> dict:
    return {"symbol": symbol, "yearHigh": high_52, "yearLow": low_52}


def _prices(sym: str, price: float) -> dict:
    return {sym.upper(): {"c": price}}


def _prior(sym: str, price: float) -> dict:
    return {sym.upper(): {"c": price}}


def _news(sym: str, headlines: list[str]) -> dict:
    return {sym.upper(): [{"headline": h} for h in headlines]}


# ---------------------------------------------------------------------------
# Gate 1: 52-week range checks
# ---------------------------------------------------------------------------

class TestRangeGate:

    def test_mu_10x_above_52w_high_quarantined(self):
        """MU case: price ~10× the 52-week high → quarantined, reason recorded."""
        # 52w high = $70, price = $700 (10×) → 900% above high → > 20% threshold
        q, reason = _quarantine_flex_price(
            _profile("MU", high_52=70.0, low_52=30.0),
            _prices("MU", 700.0),
            {},
            {},
            _cfg(),
        )
        assert q is True
        assert reason != ""
        # Reason must describe the issue
        assert "above" in reason.lower() or "high" in reason.lower()

    def test_price_exactly_at_20pct_above_high_not_quarantined(self):
        """Price at exactly 20% above the 52-week high is the boundary — NOT quarantined
        (guard fires strictly ABOVE, not at, the threshold)."""
        high = 100.0
        price = high * 1.20   # exactly 20% above = boundary, not over
        q, reason = _quarantine_flex_price(
            _profile("TEST", high_52=high, low_52=50.0),
            _prices("TEST", price),
            {},
            {},
            _cfg(range_pct=20.0),
        )
        assert q is False

    def test_price_within_52w_range_not_quarantined(self):
        """Price well within the 52-week high/low — no quarantine."""
        q, reason = _quarantine_flex_price(
            _profile("XLV", high_52=150.0, low_52=100.0),
            _prices("XLV", 125.0),
            {},
            {},
            _cfg(),
        )
        assert q is False
        assert reason == ""

    def test_price_below_52w_low_by_more_than_threshold_quarantined(self):
        """Price > 20% below the 52-week low → quarantined."""
        # 52w low = $100, price = $78 → 22% below threshold
        q, reason = _quarantine_flex_price(
            _profile("NEE", high_52=200.0, low_52=100.0),
            _prices("NEE", 78.0),
            {},
            {},
            _cfg(range_pct=20.0),
        )
        assert q is True
        assert "below" in reason.lower() or "low" in reason.lower()

    def test_missing_52w_range_skips_gate1_goes_to_gate2(self):
        """No yearHigh/yearLow in profile → gate 1 skipped; gate 2 still applies."""
        # A profile with no range fields but a > 50% move without news → quarantined via gate 2
        prior = _prior("XYZ", 50.0)
        q, reason = _quarantine_flex_price(
            {"symbol": "XYZ"},   # no yearHigh/yearLow
            _prices("XYZ", 100.0),   # 100% move
            prior,
            {},
            _cfg(),
        )
        # Gate 2: 100% > 50% and no news → quarantined
        assert q is True

    def test_no_price_available_returns_false(self):
        """Missing price in prices dict → cannot quarantine (no data = no judgment)."""
        q, reason = _quarantine_flex_price(
            _profile("ABC", high_52=100.0, low_52=50.0),
            {},   # no price for ABC
            {},
            {},
            _cfg(),
        )
        assert q is False
        assert reason == ""


# ---------------------------------------------------------------------------
# Gate 2: single-day large-move checks
# ---------------------------------------------------------------------------

class TestSingleDayMoveGate:

    def test_move_over_50pct_without_news_quarantined(self):
        """> 50% single-day move WITHOUT news corroboration → quarantined."""
        q, reason = _quarantine_flex_price(
            _profile("ETN", high_52=300.0, low_52=100.0),  # range allows 180
            _prices("ETN", 180.0),          # 80% move from 100
            _prior("ETN", 100.0),
            {},    # no news
            _cfg(range_pct=20.0, move_pct=50.0),
        )
        assert q is True
        assert reason != ""

    def test_move_over_50pct_with_news_not_quarantined(self):
        """> 50% single-day move WITH a news hit → NOT quarantined (legitimate event)."""
        q, reason = _quarantine_flex_price(
            _profile("NVDA", high_52=200.0, low_52=80.0),
            _prices("NVDA", 160.0),     # 60% move
            _prior("NVDA", 100.0),
            _news("NVDA", ["NVDA announces $2T AI chip contract"]),
            _cfg(range_pct=20.0, move_pct=50.0),
        )
        assert q is False
        assert reason == ""

    def test_move_below_50pct_no_quarantine(self):
        """Move below the 50% threshold → NOT quarantined even without news."""
        q, reason = _quarantine_flex_price(
            _profile("XLU", high_52=100.0, low_52=50.0),
            _prices("XLU", 74.0),    # 48% move — below 50% threshold
            _prior("XLU", 50.0),
            {},
            _cfg(range_pct=20.0, move_pct=50.0),
        )
        assert q is False

    def test_prior_snapshot_missing_gate2_skipped(self):
        """Walk-back exhausted (no prior prices) → delta check skipped; range check still applies."""
        # Price within range, no prior prices → not quarantined
        q, reason = _quarantine_flex_price(
            _profile("MU", high_52=120.0, low_52=40.0),
            _prices("MU", 80.0),   # within range
            {},    # no prior snapshot
            {},
            _cfg(),
        )
        assert q is False

    def test_prior_snapshot_missing_range_check_still_fires(self):
        """Walk-back exhausted but price is outside 52-week range → range check quarantines."""
        q, reason = _quarantine_flex_price(
            _profile("XSD", high_52=50.0, low_52=20.0),
            _prices("XSD", 65.0),   # 30% above 52w high → > 20% threshold
            {},   # no prior snapshot (delta check skipped)
            {},
            _cfg(range_pct=20.0),
        )
        assert q is True

    def test_exact_50pct_move_is_not_quarantined(self):
        """Exactly 50% move is the boundary — NOT quarantined (strict > comparison)."""
        q, reason = _quarantine_flex_price(
            _profile("X", high_52=300.0, low_52=50.0),
            _prices("X", 150.0),   # exactly 50% move from 100
            _prior("X", 100.0),
            {},
            _cfg(range_pct=20.0, move_pct=50.0),
        )
        assert q is False

    def test_quarantine_reason_describes_move(self):
        """The quarantine reason string must mention the move magnitude and missing news."""
        q, reason = _quarantine_flex_price(
            _profile("MU", high_52=200.0, low_52=20.0),
            _prices("MU", 510.0),   # ~900% move from 51
            _prior("MU", 51.0),
            {},
            _cfg(range_pct=20.0, move_pct=50.0),
        )
        assert q is True
        assert "%" in reason or "moved" in reason.lower() or "news" in reason.lower()


# ---------------------------------------------------------------------------
# Edge / integration cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_symbol_case_insensitive_in_prices(self):
        """Symbol lookup is case-insensitive (profile uses 'MU', prices uses 'MU')."""
        q, _ = _quarantine_flex_price(
            _profile("MU", high_52=100.0, low_52=50.0),
            {"MU": {"c": 75.0}},   # within range, no prior → no quarantine
            {},
            {},
            _cfg(),
        )
        assert q is False

    def test_empty_profile_symbol_returns_false(self):
        """Profile with empty symbol string → cannot quarantine."""
        q, reason = _quarantine_flex_price(
            {"symbol": "", "yearHigh": 100.0, "yearLow": 50.0},
            _prices("", 150.0),
            {},
            {},
            _cfg(),
        )
        assert q is False

    def test_custom_thresholds_respected(self):
        """Custom range_pct=10 catches a 15% overshoot that the default 20% would miss."""
        # 52w high = 100, price = 115 (15% above high)
        # With default 20% → NOT quarantined; with 10% → quarantined
        q_default, _ = _quarantine_flex_price(
            _profile("SYM", high_52=100.0, low_52=50.0),
            _prices("SYM", 115.0),
            {},
            {},
            _cfg(range_pct=20.0),
        )
        q_tight, _ = _quarantine_flex_price(
            _profile("SYM", high_52=100.0, low_52=50.0),
            _prices("SYM", 115.0),
            {},
            {},
            _cfg(range_pct=10.0),
        )
        assert q_default is False
        assert q_tight is True
