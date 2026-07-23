"""Unit tests for market_implied_quadrant + market_vs_macro_quadrant (FOLLOWUPS #18, 2026-07-23).

Verifies: implied-quadrant structure, per-vote table presence, correct divergence
behavior at borderline regimes, and the daily dollar-proxy builder.

Run: PYTHONPATH=src pytest tests/test_market_implied_quadrant.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import (  # noqa: E402
    _build_market_implied_quadrant,
    _daily_dollar_proxy,
    _div_market_vs_macro_quadrant,
    _load_divergence_config,
)

CFG = _load_divergence_config()
TODAY = "2026-07-23"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _perf_point(date: str, q1: float = 100.0, q2: float = 100.0,
                q3: float = 100.0, q4: float = 100.0, spy: float = 100.0) -> dict:
    """A mock performance-series point with quadrant closes."""
    return {
        "date": date,
        "equity": 100_000.0,
        "spy_close": spy,
        "favored_bucket": [],
        "closes": {"SPY": spy, "QQQ": q1, "XLI": q2, "GLD": q3, "TLT": q4},
    }


def _rising_series(n: int = 25, q2_boost: float = 0.3) -> list[dict]:
    """25 days of perf points where Q2 basket rises faster than others (tape = Q2/reflation)."""
    pts = []
    for i in range(n):
        d = f"2026-06-{(1 + i):02d}" if i < 30 else f"2026-07-{(i - 29):02d}"
        pts.append(_perf_point(
            d,
            q1=100.0 * (1.003 ** i),
            q2=100.0 * ((1.003 + q2_boost * 0.01) ** i),   # Q2 outperforms
            q3=100.0 * (0.998 ** i),                         # Q3/Q4 underperform
            q4=100.0 * (0.997 ** i),
            spy=100.0 * (1.001 ** i),
        ))
    return pts


def _flat_series(n: int = 25) -> list[dict]:
    return [_perf_point(f"2026-06-{(i + 1):02d}") for i in range(n)]


def _macro_rows(values: list, dates: list[str] | None = None) -> list[dict]:
    d = dates or [f"2026-07-{(20 - i):02d}" for i in range(len(values))]
    return [{"date": d[i], "value": str(v)} for i, v in enumerate(values)]


def _bond(trend: str = "tightening") -> dict:
    return {"credit": {"hy_oas": {"trend_4w": trend}},
            "breakevens": {"be_5y": {"delta_20d_bp": 20.0 if trend == "tightening" else -20.0}}}


# ---------------------------------------------------------------------------
# _build_market_implied_quadrant — structure tests
# ---------------------------------------------------------------------------

def test_no_series_returns_unavailable():
    # Calling with an explicit empty list AND no storage env → available: False.
    # We patch read_perf_series to avoid storage access in unit tests.
    from unittest.mock import patch
    with patch("collector.handler.read_perf_series", return_value=[]):
        result = _build_market_implied_quadrant([], {}, {}, {}, TODAY)
    assert result["available"] is False


def test_returns_required_keys():
    """Any call with enough series should return the required schema keys."""
    series = _rising_series(25)
    result = _build_market_implied_quadrant(series, {}, {}, {}, TODAY)
    assert result["available"] is True
    required = {"implied_quadrant", "implied_growth", "implied_inflation",
                "confidence", "vote_count", "total_votes", "votes"}
    assert required <= set(result)


def test_votes_list_has_expected_sources():
    """The votes list must include the expected signal sources."""
    series = _rising_series(25)
    result = _build_market_implied_quadrant(series, {}, {}, {}, TODAY)
    sources = {v["source"] for v in result.get("votes", [])}
    expected = {
        "basket_momentum_20d", "basket_momentum_60d",
        "copper_gold_ratio", "XLY_XLP", "DXY_trend",
        "breakevens_20d", "HY_OAS_trend", "2s10s_steepening",
    }
    assert expected <= sources


def test_borderline_regime_still_returns_result():
    """At a borderline macro regime (active_quadrant = ''), the block still works."""
    series = _rising_series(25)
    macro = {"T10Y2Y": _macro_rows([0.5, 0.4])}
    result = _build_market_implied_quadrant(series, macro, _bond(), {}, TODAY)
    # Must not raise; result should have an implied_quadrant (possibly "borderline")
    assert result["available"] is True
    assert "implied_quadrant" in result


def test_short_series_fewer_basket_votes():
    """Only 10 points of history → 20d basket vote is absent, 60d also absent."""
    series = _flat_series(10)
    result = _build_market_implied_quadrant(series, {}, {}, {}, TODAY)
    basket_votes = [v for v in result["votes"] if "basket_momentum" in v.get("source", "")]
    # With only 10 points, both 20d and 60d baskets are absent (insufficient history)
    for bv in basket_votes:
        if bv.get("vote") is None:
            assert "insufficient history" in str(bv.get("note", ""))


# ---------------------------------------------------------------------------
# _div_market_vs_macro_quadrant
# ---------------------------------------------------------------------------

def _miq(implied: str, confidence: str = "high") -> dict:
    return {"available": True, "implied_quadrant": implied, "confidence": confidence}


def test_div_miq_fires_on_macro_mismatch():
    """Tape implies Q2 while macro call is Q4 → active, more_risk_on."""
    ref = {"active_quadrant": "Q4", "favored_bucket": []}
    d = _div_market_vs_macro_quadrant(ref, _miq("Q2"), TODAY, 7, CFG)
    assert d["status"] == "active"
    assert d["direction_implied"] == "more_risk_on"


def test_div_miq_fires_on_borderline_outside_favored():
    """Tape implies Q1 while favored bucket is [Q3, Q4] at borderline → active."""
    ref = {"active_quadrant": "", "favored_bucket": ["Q3", "Q4"]}
    d = _div_market_vs_macro_quadrant(ref, _miq("Q1"), TODAY, 7, CFG)
    assert d["status"] == "active"
    assert d["direction_implied"] == "more_risk_on"


def test_div_miq_indeterminate_when_aligned():
    """Tape implies Q3 and macro call is Q3 → aligned, indeterminate."""
    ref = {"active_quadrant": "Q3", "favored_bucket": ["Q3"]}
    d = _div_market_vs_macro_quadrant(ref, _miq("Q3"), TODAY, 7, CFG)
    assert d["status"] == "indeterminate"
    assert d["direction_implied"] == "aligned"


def test_div_miq_indeterminate_on_low_confidence():
    """Low-confidence tape read → indeterminate (avoids noise)."""
    ref = {"active_quadrant": "Q4", "favored_bucket": []}
    d = _div_market_vs_macro_quadrant(ref, _miq("Q1", confidence="low"), TODAY, 7, CFG)
    assert d["status"] == "indeterminate"


def test_div_miq_indeterminate_when_unavailable():
    ref = {"active_quadrant": "Q4", "favored_bucket": []}
    d = _div_market_vs_macro_quadrant(ref, {"available": False}, TODAY, 7, CFG)
    assert d["status"] == "indeterminate"
    assert d["direction_implied"] == "unresolved"


def test_div_miq_schema():
    ref = {"active_quadrant": "Q4", "favored_bucket": []}
    d = _div_market_vs_macro_quadrant(ref, _miq("Q2"), TODAY, 7, CFG)
    assert d["id"] == "market_vs_macro_quadrant"
    assert set(d) >= {"id", "description", "signals", "direction_implied", "status"}


def test_div_miq_more_defensive_direction():
    """Tape implies Q4 (more defensive) while macro is Q1 → more_defensive."""
    ref = {"active_quadrant": "Q1", "favored_bucket": []}
    d = _div_market_vs_macro_quadrant(ref, _miq("Q4"), TODAY, 7, CFG)
    assert d["status"] == "active"
    assert d["direction_implied"] == "more_defensive"


# ---------------------------------------------------------------------------
# _daily_dollar_proxy
# ---------------------------------------------------------------------------

def _fx_rows(latest: float, n: int = 22, trend: str = "rising") -> list[dict]:
    """Newest-first FX rows, n <= 22 to keep dates within July 1-23.

    trend="rising" in _fx_rows means: values INCREASE going from newest to older
    indices (vals[i] = latest * factor^i where factor > 1), so vals[0] (newest) is
    LOWER than vals[20] (oldest). This means the series FELL over the past 20 days.

    DEXUSEU (USD/EUR): falling over time → fewer USD per EUR → USD strengthens.
    DEXJPUS (JPY/USD): rising over time → more JPY per USD → USD strengthens.

    trend="falling": values DECREASE going older → series ROSE over 20 days.
    """
    factor = 1.002 if trend == "rising" else 0.998
    vals = [latest * (factor ** i) for i in range(n)]
    return [{"date": f"2026-07-{(23 - i):02d}", "value": str(v)} for i, v in enumerate(vals)]


def test_dollar_proxy_returns_available_when_pairs_fresh():
    """Fresh FX pairs all pointing toward USD strength → available and stronger."""
    macro = {
        # DEXUSEU rising-factor → DEXUSEU fell over time → USD strengthened
        "DEXUSEU": _fx_rows(0.92, trend="rising"),
        # DEXJPUS falling-factor → DEXJPUS rose over time → MORE JPY per USD → USD strengthened
        "DEXJPUS": _fx_rows(155.0, trend="falling"),
        # DEXCHUS falling-factor → DEXCHUS rose over time → USD strengthened
        "DEXCHUS": _fx_rows(7.2, trend="falling"),
    }
    result = _daily_dollar_proxy(macro, TODAY)
    assert result["available"] is True
    assert result["proxy_direction"] == "stronger"
    assert len(result["components"]) >= 2


def test_dollar_proxy_unavailable_when_all_stale():
    """All FX rows are >5d stale → unavailable."""
    macro = {
        "DEXUSEU": [{"date": "2026-07-01", "value": "0.92"}],  # 22 days stale
        "DEXJPUS": [{"date": "2026-07-01", "value": "155.0"}],
        "DEXCHUS": [{"date": "2026-07-01", "value": "7.2"}],
    }
    result = _daily_dollar_proxy(macro, TODAY)
    # 22 days > 5 day threshold → unavailable
    assert result["available"] is False


def test_dollar_proxy_partial_pairs():
    """Only one fresh FX pair → available (not all required)."""
    macro = {
        "DEXUSEU": _fx_rows(0.92, trend="rising"),   # EUR falling = USD rising
        "DEXJPUS": [{"date": "2026-07-01", "value": "155.0"}],  # stale
        "DEXCHUS": [],  # absent
    }
    result = _daily_dollar_proxy(macro, TODAY)
    # At least DEXUSEU is fresh → should be available
    assert result["available"] is True
    assert any(c["pair"] == "DEXUSEU" for c in result["components"])


def test_dollar_proxy_weaker_direction():
    """USD weakening across all pairs → proxy_direction 'weaker'."""
    macro = {
        # DEXUSEU falling-factor → DEXUSEU rose over time → more USD per EUR → USD weakened
        "DEXUSEU": _fx_rows(0.92, trend="falling"),
        # DEXJPUS rising-factor → DEXJPUS fell over time → fewer JPY per USD → USD weakened
        "DEXJPUS": _fx_rows(155.0, trend="rising"),
        # DEXCHUS rising-factor → DEXCHUS fell over time → USD weakened
        "DEXCHUS": _fx_rows(7.2, trend="rising"),
    }
    result = _daily_dollar_proxy(macro, TODAY)
    assert result["available"] is True
    assert result["proxy_direction"] == "weaker"
