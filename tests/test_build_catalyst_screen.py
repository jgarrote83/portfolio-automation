"""Task D (2026-08-10, catalyst-sleeve-funnel) —
``collector.handler._build_catalyst_screen``: the collector-side glue that
shapes already-fetched FMP/Quiver/Finnhub data into catalyst_screen's
per-candidate contract. Everything is pre-fetched/mocked here — no network.

Run: PYTHONPATH=src pytest tests/test_build_catalyst_screen.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_catalyst_screen  # noqa: E402

TODAY = "2026-08-10"


def _bars(n=25, close=100.0, vol=5_000_000):
    # ascending order, flat-ish closes with a slight uptrend at the end
    out = [{"c": close, "v": vol} for _ in range(n - 1)]
    out.append({"c": close * 1.05, "v": vol})
    return out


def test_strong_no_earnings_candidate_beats_weak_earnings_candidate():
    profiles = {
        "STRONG": {"symbol": "STRONG", "sector": "Technology"},
        "WEAK": {"symbol": "WEAK", "sector": "Utilities"},
    }
    bars = {"STRONG": _bars(), "WEAK": _bars(close=50.0)}
    earnings_rows = [{"symbol": "WEAK", "date": "2026-08-10"}]  # WEAK reports today
    stock_news = [
        {"symbol": "STRONG", "headline": "Company beats estimates and raises guidance",
         "publishedDate": "2026-08-10"},
    ]
    congressional = [{"Ticker": "STRONG", "Transaction": "Purchase"}]

    result = _build_catalyst_screen(
        discovery=["STRONG", "WEAK"],
        profiles_by_symbol=profiles,
        bars_by_symbol=bars,
        earnings_market_rows=earnings_rows,
        stock_news=stock_news,
        congressional=congressional,
        quadrant="Q1",
        quadrant_basis="active",
        held=set(),
        exclude=set(),
        legacy_blocked=set(),
        min_adv_usd=50_000_000.0,
        today=TODAY,
        top_n=5,
    )
    assert result["available"] is True
    strong_row = next(r for r in result["ledger"] if r["symbol"] == "STRONG")
    weak_row = next(r for r in result["ledger"] if r["symbol"] == "WEAK")
    assert strong_row["screened_in"] is True
    assert strong_row["components"]["earnings_proximity"] is None  # no date at all
    assert strong_row["components"]["regime_fit_score"] == 1.0    # Technology fits Q1
    assert weak_row["components"]["earnings_proximity"] == 1.0     # reports today
    assert weak_row["components"]["regime_fit_score"] == 0.0       # Utilities doesn't fit Q1
    # STRONG must not be handicapped for lacking a date it never had.
    assert strong_row["score"] >= weak_row["score"]
    assert "STRONG" in result["nominated"]


def test_held_and_excluded_symbols_are_hard_screened_out():
    profiles = {"HELDSYM": {"sector": "Technology"}, "SEPSYM": {"sector": "Technology"}}
    bars = {"HELDSYM": _bars(), "SEPSYM": _bars()}
    result = _build_catalyst_screen(
        discovery=["HELDSYM", "SEPSYM"],
        profiles_by_symbol=profiles,
        bars_by_symbol=bars,
        earnings_market_rows=[],
        stock_news=[],
        congressional=[],
        quadrant="Q1",
        quadrant_basis="active",
        held={"HELDSYM"},
        exclude={"SEPSYM"},
        legacy_blocked=set(),
        min_adv_usd=50_000_000.0,
        today=TODAY,
        top_n=5,
    )
    held_row = next(r for r in result["ledger"] if r["symbol"] == "HELDSYM")
    sep_row = next(r for r in result["ledger"] if r["symbol"] == "SEPSYM")
    assert held_row["screen_reason"] == "currently_held"
    assert sep_row["screen_reason"] == "flex_separation_set"
    assert result["nominated"] == []


def test_below_liquidity_floor_screened_out():
    profiles = {"THIN": {"sector": "Technology"}}
    bars = {"THIN": _bars(vol=100)}  # tiny volume -> ADV well below floor
    result = _build_catalyst_screen(
        discovery=["THIN"],
        profiles_by_symbol=profiles,
        bars_by_symbol=bars,
        earnings_market_rows=[],
        stock_news=[],
        congressional=[],
        quadrant="Q1",
        quadrant_basis="active",
        held=set(),
        exclude=set(),
        legacy_blocked=set(),
        min_adv_usd=50_000_000.0,
        today=TODAY,
        top_n=5,
    )
    row = result["ledger"][0]
    assert row["screen_reason"] == "liquidity_below_min"


def test_no_bars_at_all_screened_out_for_insufficient_history():
    profiles = {"NODATA": {"sector": "Technology"}}
    result = _build_catalyst_screen(
        discovery=["NODATA"],
        profiles_by_symbol=profiles,
        bars_by_symbol={},  # no history fetched for this symbol
        earnings_market_rows=[],
        stock_news=[],
        congressional=[],
        quadrant="Q1",
        quadrant_basis="active",
        held=set(),
        exclude=set(),
        legacy_blocked=set(),
        min_adv_usd=50_000_000.0,
        today=TODAY,
        top_n=5,
    )
    row = result["ledger"][0]
    assert row["screen_reason"] == "insufficient_price_history"


def test_political_flow_only_counts_purchase_rows():
    profiles = {"POL": {"sector": "Technology"}}
    bars = {"POL": _bars()}
    congressional = [
        {"Ticker": "POL", "Transaction": "Purchase"},
        {"Ticker": "POL", "Transaction": "Purchase"},
        {"Ticker": "POL", "Transaction": "Sale (Full)"},
        {"Ticker": "OTHER", "Transaction": "Purchase"},
    ]
    result = _build_catalyst_screen(
        discovery=["POL"],
        profiles_by_symbol=profiles,
        bars_by_symbol=bars,
        earnings_market_rows=[],
        stock_news=[],
        congressional=congressional,
        quadrant="Q1",
        quadrant_basis="active",
        held=set(),
        exclude=set(),
        legacy_blocked=set(),
        min_adv_usd=50_000_000.0,
        today=TODAY,
        top_n=5,
    )
    row = result["ledger"][0]
    assert row["basis"]["political_purchase_count"] == 2  # sale not counted


def test_quadrant_and_basis_and_universe_echoed_in_block():
    result = _build_catalyst_screen(
        discovery=[],
        profiles_by_symbol={},
        bars_by_symbol={},
        earnings_market_rows=[],
        stock_news=[],
        congressional=[],
        quadrant="Q3",
        quadrant_basis="borderline_5d_tiebreak",
        held=set(),
        exclude=set(),
        legacy_blocked=set(),
        min_adv_usd=50_000_000.0,
        today=TODAY,
        top_n=15,
    )
    assert result["quadrant"] == "Q3"
    assert result["quadrant_basis"] == "borderline_5d_tiebreak"
    assert result["discovery_universe"] == []
    assert result["top_n"] == 15
    assert result["ledger"] == []
    assert result["nominated"] == []
