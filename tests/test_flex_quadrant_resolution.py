"""A1 (2026-07-21) — borderline-quadrant 5-day tiebreak for the Flex engine.

An indeterminate ``active_quadrant`` must NOT freeze the flex sleeve (it had since
2026-07-02). ``resolve_quadrant`` resolves a 2-quadrant favored bucket to the member
with the better trailing 5d benchmark return; a no-read regime or missing benchmark
data still fails closed. The collector precomputes the ``flex_quadrant`` block and the
engine consumes it, falling back to the strict axes when the block is absent (old
snapshots). Run: PYTHONPATH=src pytest tests/test_flex_quadrant_resolution.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_flex_quadrant  # noqa: E402
from flex.config import FlexConfig  # noqa: E402
from flex.entry import build_flex_entry  # noqa: E402
from flex.handler import _resolve_quadrant  # noqa: E402
from flex.regime import regime_fit, resolve_quadrant  # noqa: E402


def _closes(start: float, end: float, n: int = 7) -> dict:
    """n dated closes rising/falling linearly from start to end (≥6 → r5 defined)."""
    dates = [f"2026-07-{10 + k:02d}" for k in range(n)]
    prices = [round(start + (end - start) * k / (n - 1), 4) for k in range(n)]
    return dict(zip(dates, prices))


def _daily(rng=0.6):
    return [{"o": 100, "h": 100 + rng / 2, "l": 100 - rng / 2, "c": 100, "v": 1_000_000}
            for _ in range(20)]


def _intraday(closes):
    return [{"o": c, "h": c + 0.1, "l": c - 0.1, "c": c, "v": 1000} for c in closes]


# --- resolve_quadrant (pure) -------------------------------------------------

def test_pinned_axes_pass_through_unchanged():
    assert resolve_quadrant("falling", "rising", None) == ("Q3", "active")
    assert resolve_quadrant("rising", "falling", None) == ("Q1", "active")


def test_borderline_tiebreak_higher_5d_wins():
    # falling growth + flat inflation → Q3/Q4 union; Q3 (GLD) beat Q4 (TLT).
    assert resolve_quadrant("falling", "flat", {"Q3": 1.42, "Q4": -0.61}) == \
        ("Q3", "borderline_5d_tiebreak")
    # reversed returns → Q4 wins.
    assert resolve_quadrant("falling", "flat", {"Q3": -0.61, "Q4": 1.42}) == \
        ("Q4", "borderline_5d_tiebreak")


def test_exact_tie_takes_first_bucket_member():
    assert resolve_quadrant("falling", "flat", {"Q3": 1.0, "Q4": 1.0}) == \
        ("Q3", "borderline_5d_tiebreak")


def test_missing_either_return_is_unresolved():
    assert resolve_quadrant("falling", "flat", {"Q3": 1.0}) == ("", "unresolved")
    assert resolve_quadrant("falling", "flat", {"Q4": 1.0}) == ("", "unresolved")
    assert resolve_quadrant("falling", "flat", None) == ("", "unresolved")


def test_no_directional_read_is_unresolved():
    # growth flat → empty favored bucket → still fail closed.
    assert resolve_quadrant("flat", "flat", {"Q3": 1.0, "Q4": 0.0}) == ("", "unresolved")


# --- _build_flex_quadrant (collector precompute) -----------------------------

def test_build_flex_quadrant_resolves_q3_from_cache():
    cache = {"GLD": _closes(100.0, 103.0), "TLT": _closes(100.0, 99.0),
             "QQQ": _closes(100.0, 100.0), "XLI": _closes(100.0, 100.0)}
    fq = _build_flex_quadrant({"direction": "falling"}, {"direction": "flat"}, cache)
    assert fq["resolved"] == "Q3"
    assert fq["basis"] == "borderline_5d_tiebreak"
    assert fq["favored_bucket"] == ["Q3", "Q4"]
    assert fq["benchmark_returns_5d"]["Q3"]["etf"] == "GLD"
    assert fq["window_trading_days"] == 5


def test_build_flex_quadrant_missing_benchmark_is_unresolved():
    cache = {"GLD": _closes(100.0, 103.0)}  # TLT (Q4) absent
    fq = _build_flex_quadrant({"direction": "falling"}, {"direction": "flat"}, cache)
    assert fq["resolved"] == ""
    assert fq["basis"] == "unresolved"


def test_build_flex_quadrant_pinned_axes():
    cache = {}
    fq = _build_flex_quadrant({"direction": "falling"}, {"direction": "rising"}, cache)
    assert (fq["resolved"], fq["basis"]) == ("Q3", "active")


# --- G1 admission (regime_fit) -----------------------------------------------

def test_regime_fit_admits_defensive_utilities_blocks_tech_in_q3():
    assert regime_fit("Consumer Defensive", "Q3") is True
    assert regime_fit("Utilities", "Q3") is True
    assert regime_fit("Technology", "Q3") is False


def test_build_entry_admits_defensive_past_g1_in_resolved_q3():
    e = build_flex_entry(
        {"symbol": "XLP", "sector": "Consumer Defensive"},
        _intraday([100, 100.5, 101, 101.5, 102, 102.5, 103]), _daily(),
        "Q3", 1_000_000.0, 45, FlexConfig(), quadrant_basis="borderline_5d_tiebreak",
    )
    assert e["regime_fit"] is True
    assert e["quadrant_basis"] == "borderline_5d_tiebreak"


def test_build_entry_skips_tech_in_q3_with_basis_in_reason():
    e = build_flex_entry(
        {"symbol": "NVDA", "sector": "Technology"},
        _intraday([100, 101, 102]), _daily(),
        "Q3", 1_000_000.0, 45, FlexConfig(), quadrant_basis="borderline_5d_tiebreak",
    )
    assert e["regime_fit"] is False
    assert e["skip_reason"].startswith("regime_fit:")
    assert "borderline_5d_tiebreak" in e["skip_reason"]


# --- engine consumption + fallback -------------------------------------------

def test_engine_reads_flex_quadrant_block_when_present():
    snap = {"flex_quadrant": {"resolved": "Q3", "basis": "borderline_5d_tiebreak"},
            "growth_axis": {"direction": "falling"}, "inflation_axis": {"direction": "flat"}}
    assert _resolve_quadrant(snap) == ("Q3", "borderline_5d_tiebreak")


def test_engine_falls_back_to_strict_axes_without_block():
    # No flex_quadrant → identical to master's strict behaviour.
    snap = {"growth_axis": {"direction": "falling"}, "inflation_axis": {"direction": "rising"}}
    assert _resolve_quadrant(snap) == ("Q3", "active")
    # Borderline with no block → strict active_quadrant is "" (the old freeze).
    snap2 = {"growth_axis": {"direction": "falling"}, "inflation_axis": {"direction": "flat"}}
    assert _resolve_quadrant(snap2) == ("", "unresolved")
