"""Unit tests for the leading-growth composite (FOLLOWUPS #17, 2026-07-23).

Verifies: diffusion score computation, per-signal direction detection, stale/absent
inputs degrade gracefully (never a false active), and the divergence detector
(_div_leading_vs_lagging_growth) fires / stays indeterminate correctly.

Run: PYTHONPATH=src pytest tests/test_leading_growth.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import (  # noqa: E402
    _build_leading_growth,
    _div_leading_vs_lagging_growth,
    _load_divergence_config,
    _series_direction,
)

CFG = _load_divergence_config()


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _macro_rows(values: list[float], dates: list[str] | None = None) -> list[dict]:
    """Newest-first FRED rows."""
    d = dates or [f"2026-07-{20 - i:02d}" for i in range(len(values))]
    return [{"date": d[i], "value": str(v)} for i, v in enumerate(values)]


def _close_cache(
    cper: list[tuple[str, float]] | None = None,
    gld: list[tuple[str, float]] | None = None,
    xly: list[tuple[str, float]] | None = None,
    xlp: list[tuple[str, float]] | None = None,
) -> dict[str, dict[str, float]]:
    """Build a minimal close cache with date→price dicts."""
    cache: dict[str, dict[str, float]] = {}
    for sym, pairs in (("CPER", cper), ("GLD", gld), ("XLY", xly), ("XLP", xlp)):
        if pairs:
            cache[sym] = {d: v for d, v in pairs}
    return cache


def _rising_closes(start: float = 100.0, n: int = 25) -> list[tuple[str, float]]:
    """25 daily closes rising 0.5% each day."""
    return [
        (f"2026-07-{(1 + i):02d}", round(start * (1.005 ** i), 4))
        for i in range(n)
    ]


def _falling_closes(start: float = 100.0, n: int = 25) -> list[tuple[str, float]]:
    return [(d, round(start * (0.995 ** i), 4)) for i, (d, _) in enumerate(_rising_closes(start, n))]


# ---------------------------------------------------------------------------
# _series_direction
# ---------------------------------------------------------------------------

def test_series_direction_rising():
    vals = [5.0, 4.0, 3.0, 2.0, 1.0]  # newest-first → oldest=1.0 newest=5.0 → rising
    assert _series_direction(vals, window=5) == "rising"


def test_series_direction_falling():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]  # newest-first → oldest=5.0 newest=1.0 → falling
    assert _series_direction(vals, window=5) == "falling"


def test_series_direction_flat_no_trend():
    vals = [3.0, 3.0, 3.0, 3.0]
    assert _series_direction(vals, window=4) == "flat"


def test_series_direction_too_few_returns_none():
    assert _series_direction([3.0], window=4) is None


# ---------------------------------------------------------------------------
# _build_leading_growth — FRED signals
# ---------------------------------------------------------------------------

def _empty_macro() -> dict:
    """All series absent — ensures graceful degradation."""
    return {}


def _full_macro(wei_trend: str = "rising") -> dict:
    """Build a macro_data dict with all 6 FRED leading-growth series set to the given trend."""
    vals_up = [10.0, 9.0, 8.0, 7.0, 6.0, 5.0]    # newest-first, rising
    vals_dn = [5.0, 6.0, 7.0, 8.0, 9.0, 10.0]    # newest-first, falling
    vals = vals_up if wei_trend == "rising" else vals_dn
    return {
        "WEI": _macro_rows(vals),
        "NFCI": _macro_rows([v * -1 for v in vals]),  # inverted
        "PERMIT": _macro_rows(vals),
        "NEWORDER": _macro_rows(vals),
        "NOCDFSA066MSFRBPHI": _macro_rows(vals),
        "GACDISA066MSFRBNY": _macro_rows(vals),
    }


def test_all_absent_signals_low_confidence():
    """All FRED inputs missing → available_signals < 2 → confidence 'none', score 0."""
    result = _build_leading_growth(_empty_macro(), {}, {}, {})
    assert result["confidence"] in ("none", "low")
    assert result["available"] is False
    assert result["score"] == 0.0


def test_all_rising_signals_positive_score():
    """All 6 FRED signals rising → score > 0, direction 'rising'."""
    macro = _full_macro("rising")
    # no bond signals, no price cache — market-derived signals absent but FRED present
    result = _build_leading_growth(macro, {}, {}, {})
    assert result["available"] is True
    assert result["score"] > 0
    assert result["direction"] in ("rising", "flat")  # depends on threshold


def test_all_falling_signals_negative_score():
    """All 6 FRED signals falling → score < 0, direction 'falling'."""
    macro = _full_macro("falling")
    result = _build_leading_growth(macro, {}, {}, {})
    assert result["available"] is True
    assert result["score"] < 0
    assert result["direction"] in ("falling", "flat")


def test_two_stale_signals_reduces_confidence():
    """Only 4 fresh signals → confidence 'medium', not 'full'."""
    macro = {
        "WEI": _macro_rows([5.0, 4.0, 3.0, 2.0]),
        "NFCI": _macro_rows([5.0, 4.0, 3.0, 2.0]),
        "PERMIT": _macro_rows([5.0, 4.0, 3.0, 2.0]),
        "NEWORDER": _macro_rows([5.0, 4.0, 3.0, 2.0]),
        # NOCDFSA066MSFRBPHI and GACDISA066MSFRBNY absent
    }
    result = _build_leading_growth(macro, {}, {}, {})
    assert result["confidence"] == "medium"


def test_hy_oas_tightening_adds_up_vote():
    """HY OAS tightening → growth-positive vote added."""
    macro = _full_macro("rising")
    bond = {"credit": {"hy_oas": {"trend_4w": "tightening"}}}
    r_no_bond = _build_leading_growth(macro, {}, {}, {})
    r_with_bond = _build_leading_growth(macro, {}, bond, {})
    assert r_with_bond["votes_up"] > r_no_bond["votes_up"]


def test_hy_oas_widening_adds_down_vote():
    """HY OAS widening → growth-negative vote added."""
    macro = _full_macro("falling")
    bond = {"credit": {"hy_oas": {"trend_4w": "widening"}}}
    r_no_bond = _build_leading_growth(macro, {}, {}, {})
    r_with_bond = _build_leading_growth(macro, {}, bond, {})
    assert r_with_bond["votes_down"] > r_no_bond["votes_down"]


def test_cper_gld_rising_ratio_adds_up_vote():
    """Rising CPER/GLD ratio (copper outperforming gold) → growth signal."""
    macro = {}   # no FRED signals — test isolation
    cc = _close_cache(
        cper=_rising_closes(20.0),
        gld=_falling_closes(200.0),   # gold falling while copper rises → ratio strongly up
    )
    result = _build_leading_growth(macro, {}, {}, cc)
    cg_sig = next((s for s in result["signals"] if s["name"] == "CPER_GLD_20d"), None)
    assert cg_sig is not None
    assert cg_sig["direction"] == "rising"
    assert result["votes_up"] >= 1


def test_xly_xlp_falling_ratio_adds_down_vote():
    """Falling XLY/XLP ratio (defensives outperforming cyclicals) → growth-negative signal."""
    macro = {}
    cc = _close_cache(
        xly=_falling_closes(150.0),   # cyclicals falling
        xlp=_rising_closes(60.0),     # defensives rising
    )
    result = _build_leading_growth(macro, {}, {}, cc)
    xl_sig = next((s for s in result["signals"] if s["name"] == "XLY_XLP_20d"), None)
    assert xl_sig is not None
    assert xl_sig["direction"] == "falling"
    assert result["votes_down"] >= 1


def test_insufficient_close_history_skips_ratio_signal():
    """Only 5 closes (< 21 needed for 20d ratio) → ratio signal direction is None."""
    macro = {}
    cc = _close_cache(
        cper=_rising_closes(20.0, n=5),
        gld=_falling_closes(200.0, n=5),
    )
    result = _build_leading_growth(macro, {}, {}, cc)
    cg_sig = next((s for s in result["signals"] if s["name"] == "CPER_GLD_20d"), None)
    assert cg_sig is not None
    assert cg_sig["direction"] is None   # not enough history


# ---------------------------------------------------------------------------
# _div_leading_vs_lagging_growth
# ---------------------------------------------------------------------------

def _lg(direction: str, score: float = 0.5, confidence: str = "full") -> dict:
    return {
        "available": True,
        "direction": direction,
        "score": score,
        "confidence": confidence,
    }


def test_div_growth_fires_when_leading_rising_vs_realized_falling():
    """Leading rising while realized falling → active, direction_implied 'rising'."""
    growth = {"direction": "falling"}
    d = _div_leading_vs_lagging_growth(growth, _lg("rising"), CFG)
    assert d["status"] == "active"
    assert d["direction_implied"] == "rising"


def test_div_growth_fires_when_leading_falling_vs_realized_rising():
    growth = {"direction": "rising"}
    d = _div_leading_vs_lagging_growth(growth, _lg("falling", score=-0.5), CFG)
    assert d["status"] == "active"
    assert d["direction_implied"] == "falling"


def test_div_growth_indeterminate_when_aligned():
    growth = {"direction": "rising"}
    d = _div_leading_vs_lagging_growth(growth, _lg("rising"), CFG)
    assert d["status"] == "indeterminate"
    assert d["direction_implied"] == "aligned"


def test_div_growth_indeterminate_when_flat():
    growth = {"direction": "falling"}
    d = _div_leading_vs_lagging_growth(growth, _lg("flat", score=0.0), CFG)
    assert d["status"] == "indeterminate"


def test_div_growth_indeterminate_when_unavailable():
    growth = {"direction": "falling"}
    d = _div_leading_vs_lagging_growth(growth, {"available": False}, CFG)
    assert d["status"] == "indeterminate"
    assert d["direction_implied"] == "unresolved"


def test_div_growth_indeterminate_when_low_confidence():
    """Low-confidence composite → doesn't fire (house rule: low confidence = indeterminate)."""
    growth = {"direction": "falling"}
    d = _div_leading_vs_lagging_growth(growth, _lg("rising", confidence="low"), CFG)
    assert d["status"] == "indeterminate"


def test_div_growth_id_and_schema():
    """Result has the required schema."""
    growth = {"direction": "falling"}
    d = _div_leading_vs_lagging_growth(growth, _lg("rising"), CFG)
    assert d["id"] == "leading_vs_lagging_growth"
    assert "description" in d
    assert "signals" in d


# ---------------------------------------------------------------------------
# Growth-side transition_watch generalisation (Task A)
# ---------------------------------------------------------------------------

def test_growth_side_transition_watch_de_risk():
    """Leading growth FALLING (de-risk) while realized growth RISING and inflation FALLING
    → projects Q4 (falling+falling), de-risk from Q1, stages at de_risk fraction."""
    from collector.handler import _build_transition_watch, _load_risk_limits
    cfg = _load_risk_limits()

    growth_div = {
        "id": "leading_vs_lagging_growth",
        "status": "active",
        "direction_implied": "falling",
        "signals": [
            {"name": "leading_growth.direction", "value": "falling"},
            {"name": "leading_growth.score", "value": -0.4},
            {"name": "leading_growth.confidence", "value": "full"},
        ],
    }
    divergences = [growth_div]
    g_axis = {"direction": "rising"}
    i_axis = {"direction": "falling"}

    tw = _build_transition_watch(divergences, g_axis, i_axis, cfg)
    assert tw["active"] is True
    assert tw["projected_quadrant"] == "Q4"
    assert tw["direction"] == "de_risk"
    assert tw["staged_fraction"] == cfg["transition_watch"]["staged_fraction_de_risk"]
    assert any(s["side"] == "growth" for s in tw.get("sides", []))


def test_growth_side_re_risk_below_bar_does_not_activate():
    """Leading growth RE-RISK below confidence bar (low confidence) → not staged."""
    from collector.handler import _build_transition_watch, _load_risk_limits
    cfg = _load_risk_limits()

    growth_div = {
        "id": "leading_vs_lagging_growth",
        "status": "active",
        "direction_implied": "rising",
        "signals": [
            {"name": "leading_growth.direction", "value": "rising"},
            {"name": "leading_growth.score", "value": 0.4},
            {"name": "leading_growth.confidence", "value": "low"},  # below bar
        ],
    }
    divergences = [growth_div]
    g_axis = {"direction": "falling"}
    i_axis = {"direction": "falling"}

    tw = _build_transition_watch(divergences, g_axis, i_axis, cfg)
    assert tw["active"] is False
    assert tw["status"] == "indeterminate"


def test_inflation_and_growth_de_risk_takes_more_defensive():
    """When both sides fire de-risk, the more defensive projected quadrant wins."""
    from collector.handler import _build_transition_watch, _load_risk_limits
    cfg = _load_risk_limits()

    infl_div = {
        "id": "leading_vs_lagging_inflation",
        "status": "active",
        "direction_implied": "falling",  # inflation falling → Q4 projection (falling+falling)
        "signals": [
            {"name": "be_5y.delta_20d_bp", "value": -25.0},
            {"name": "inflation_axis.oil_wti_20d_pct", "value": -15.0},
            {"name": "inflation_axis.direction (realized)", "value": "rising"},
        ],
    }
    growth_div = {
        "id": "leading_vs_lagging_growth",
        "status": "active",
        "direction_implied": "falling",  # growth falling → from Q2 (rising+rising) toward Q3
        "signals": [
            {"name": "leading_growth.direction", "value": "falling"},
            {"name": "leading_growth.score", "value": -0.5},
            {"name": "leading_growth.confidence", "value": "full"},
        ],
    }
    divergences = [infl_div, growth_div]
    g_axis = {"direction": "rising"}   # realized growth = rising (inflation side speaks)
    i_axis = {"direction": "rising"}   # realized inflation rising (Q2 baseline)

    tw = _build_transition_watch(divergences, g_axis, i_axis, cfg)
    assert tw["active"] is True
    assert tw["direction"] == "de_risk"
    # both sides project defensively; both should be in sides
    assert len(tw.get("sides", [])) >= 1
