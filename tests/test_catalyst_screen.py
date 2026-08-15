"""Task D (2026-08-10, catalyst-sleeve-funnel) — src/collector/catalyst_screen.py.

Run: PYTHONPATH=src pytest tests/test_catalyst_screen.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.catalyst_screen import (  # noqa: E402
    COMPONENTS,
    applicable_components,
    build_ranking_ledger,
    composite_score,
    days_since_latest_news,
    discovery_symbols,
    earnings_proximity_score,
    group_news_by_symbol,
    keyword_hits,
    momentum_from_bars,
    momentum_score,
    news_recency_score,
    news_tone_score,
    political_flow_score,
    relative_strength_from_closes,
    relative_strength_score,
    screen_candidate,
)

TONE_KEYWORDS = {
    "positive": ["beats", "raises guidance", "wins bid"],
    "negative": ["misses", "cuts guidance", "lawsuit"],
}


# --- screen_candidate (hard filters) -----------------------------------------

def _screen_kwargs(**overrides):
    base = dict(held=False, separated=False, non_reenterable_legacy=False,
                has_price_data=True, adv_usd=100_000_000.0, min_adv_usd=50_000_000.0)
    base.update(overrides)
    return base


def test_screen_passes_clean_candidate():
    passed, reason = screen_candidate(**_screen_kwargs())
    assert passed is True
    assert reason is None


def test_screen_rejects_held():
    passed, reason = screen_candidate(**_screen_kwargs(held=True))
    assert passed is False
    assert reason == "currently_held"


def test_screen_rejects_separation_set():
    passed, reason = screen_candidate(**_screen_kwargs(separated=True))
    assert passed is False
    assert reason == "flex_separation_set"


def test_screen_rejects_non_reenterable_legacy():
    passed, reason = screen_candidate(**_screen_kwargs(non_reenterable_legacy=True))
    assert passed is False
    assert reason == "non_reenterable_legacy_exit"


def test_screen_rejects_no_price_data():
    passed, reason = screen_candidate(**_screen_kwargs(has_price_data=False))
    assert passed is False
    assert reason == "insufficient_price_history"


def test_screen_rejects_below_min_adv():
    passed, reason = screen_candidate(**_screen_kwargs(adv_usd=10_000_000.0))
    assert passed is False
    assert reason == "liquidity_below_min"


def test_screen_rejects_missing_adv():
    passed, reason = screen_candidate(**_screen_kwargs(adv_usd=None))
    assert passed is False
    assert reason == "liquidity_below_min"


# --- earnings_proximity_score: absent-vs-zero is the load-bearing case ------

def test_earnings_proximity_absent_when_no_date():
    assert earnings_proximity_score(None, "2026-08-10", 14) is None
    assert earnings_proximity_score("", "2026-08-10", 14) is None


def test_earnings_proximity_absent_when_past_or_beyond_horizon():
    assert earnings_proximity_score("2026-08-01", "2026-08-10", 14) is None  # past
    assert earnings_proximity_score("2026-09-15", "2026-08-10", 14) is None  # too far out


def test_earnings_proximity_scores_forward_window():
    assert earnings_proximity_score("2026-08-10", "2026-08-10", 14) == 1.0
    assert earnings_proximity_score("2026-08-24", "2026-08-10", 14) == 0.0
    mid = earnings_proximity_score("2026-08-17", "2026-08-10", 14)
    assert 0.4 < mid < 0.6


# --- momentum ------------------------------------------------------------

def _asc_bars(closes):
    return [{"c": c} for c in closes]


def test_momentum_from_bars_needs_enough_history():
    assert momentum_from_bars(_asc_bars([100, 101, 102]), window=10) is None


def test_momentum_from_bars_computes_pct_change():
    closes = [100.0] * 5 + [110.0]  # 6 bars, window=5 -> compares closes[-6] vs closes[-1]
    assert momentum_from_bars(_asc_bars(closes), window=5) == 10.0


def test_momentum_score_absent_when_raw_none():
    assert momentum_score(None) is None


def test_momentum_score_clamps_and_centers():
    assert momentum_score(0.0) == 0.5
    assert momentum_score(15.0, cap_pct=15.0) == 1.0
    assert momentum_score(-15.0, cap_pct=15.0) == 0.0
    assert momentum_score(999.0, cap_pct=15.0) == 1.0  # clamped, never > 1.0


# --- relative_strength (Task D, 2026-08-14 flex-conviction-path cycle) ------

def _by_date(dates: list[str], vals: list[float]) -> dict[str, float]:
    return dict(zip(dates, vals))


_DATES_61 = [f"2026-{(1 + i // 28):02d}-{(1 + i % 28):02d}" for i in range(61)]


def test_relative_strength_needs_enough_history_both_series():
    cand = _by_date(_DATES_61[:30], [100.0] * 30)   # too short
    spy = _by_date(_DATES_61, [500.0] * 61)
    assert relative_strength_from_closes(cand, spy, window=60) is None


def test_relative_strength_computes_excess_vs_spy():
    # Candidate up 20% over the window, SPY up 5% -> excess = +15pp.
    cand_vals = [100.0] * 60 + [120.0]
    spy_vals = [500.0] * 60 + [525.0]
    cand = _by_date(_DATES_61, cand_vals)
    spy = _by_date(_DATES_61, spy_vals)
    excess = relative_strength_from_closes(cand, spy, window=60)
    assert abs(excess - 15.0) < 1e-6


def test_relative_strength_missing_either_series_is_none():
    spy = _by_date(_DATES_61, [500.0] * 61)
    assert relative_strength_from_closes({}, spy, window=60) is None
    assert relative_strength_from_closes(None, spy, window=60) is None
    cand = _by_date(_DATES_61, [100.0] * 61)
    assert relative_strength_from_closes(cand, {}, window=60) is None


def test_relative_strength_score_absent_when_raw_none():
    assert relative_strength_score(None) is None


def test_relative_strength_score_clamps_and_centers():
    assert relative_strength_score(0.0) == 0.5
    assert relative_strength_score(20.0, cap_pct=20.0) == 1.0
    assert relative_strength_score(-20.0, cap_pct=20.0) == 0.0
    assert relative_strength_score(999.0, cap_pct=20.0) == 1.0  # clamped


# --- news recency ---------------------------------------------------------

def test_days_since_latest_news_absent_when_no_items():
    assert days_since_latest_news([], "2026-08-10") is None
    assert days_since_latest_news([{"headline": "x"}], "2026-08-10") is None  # no date


def test_days_since_latest_news_picks_most_recent():
    items = [{"publishedDate": "2026-08-05"}, {"date": "2026-08-08"}]
    assert days_since_latest_news(items, "2026-08-10") == 2


def test_news_recency_score_absent_beyond_lookback():
    assert news_recency_score(None, 7) is None
    assert news_recency_score(10, 7) is None


def test_news_recency_score_decays():
    assert news_recency_score(0, 7) == 1.0
    assert news_recency_score(7, 7) == 0.0


# --- news grouping ---------------------------------------------------------

def test_group_news_by_symbol():
    items = [
        {"symbol": "aapl", "headline": "a"},
        {"symbol": "AAPL", "headline": "b"},
        {"ticker": "MSFT", "headline": "c"},
        {"headline": "no symbol"},
    ]
    grouped = group_news_by_symbol(items)
    assert len(grouped["AAPL"]) == 2
    assert len(grouped["MSFT"]) == 1
    assert sum(len(v) for v in grouped.values()) == 3


# --- news tone: absent only when there is literally no news -----------------

def test_keyword_hits_counts_categories_once_per_item():
    items = [
        {"headline": "Company beats estimates and raises guidance"},  # 2 kw, 1 category -> 1 hit
        {"headline": "Unrelated filing news"},
    ]
    hits = keyword_hits(items, TONE_KEYWORDS)
    assert hits == {"positive": 1, "negative": 0}


def test_news_tone_absent_when_no_news_at_all():
    assert news_tone_score(has_news=False, positive_hits=0, negative_hits=0) is None


def test_news_tone_neutral_when_news_exists_but_no_keyword_hits():
    # Real news, no tone signal in either direction -> a genuine neutral
    # reading (0.5), NOT absent. This is distinct from "no news at all".
    assert news_tone_score(has_news=True, positive_hits=0, negative_hits=0) == 0.5


def test_news_tone_scores_diffusion():
    assert news_tone_score(True, positive_hits=3, negative_hits=0) == 1.0
    assert news_tone_score(True, positive_hits=0, negative_hits=3) == 0.0
    assert news_tone_score(True, positive_hits=1, negative_hits=1) == 0.5


# --- political flow ---------------------------------------------------------

def test_political_flow_absent_when_zero():
    assert political_flow_score(0) is None
    assert political_flow_score(None) is None


def test_political_flow_caps_at_full_score():
    assert political_flow_score(1, cap=5) == 0.2
    assert political_flow_score(5, cap=5) == 1.0
    assert political_flow_score(50, cap=5) == 1.0


# --- composite_score: the absent-vs-zero handicap test ----------------------

def test_composite_drops_absent_components_from_mean():
    # Only 4 of 7 available; the 3 absent must NOT drag the mean toward 0.
    comps = {
        "earnings_proximity": None,
        "news_recency": 1.0,
        "news_tone": 1.0,
        "momentum": 1.0,
        "regime_fit_score": 1.0,
        "political_flow": None,
        "relative_strength": None,
    }
    cs = composite_score(comps)
    assert cs["score"] == 1.0  # mean of the 4 available 1.0's, not diluted by absence
    assert cs["components_available"] == 4
    assert set(cs["components_missing"]) == {"earnings_proximity", "political_flow", "relative_strength"}
    assert cs["rankable"] is True


def test_composite_below_min_coverage_is_not_rankable():
    comps = {
        "earnings_proximity": None,
        "news_recency": None,
        "news_tone": None,
        "momentum": 1.0,
        "regime_fit_score": 1.0,
        "political_flow": 1.0,
    }
    cs = composite_score(comps)
    assert cs["components_available"] == 3
    assert cs["rankable"] is False


def test_composite_all_absent_scores_none():
    comps = {k: None for k in (
        "earnings_proximity", "news_recency", "news_tone",
        "momentum", "regime_fit_score", "political_flow",
    )}
    cs = composite_score(comps)
    assert cs["score"] is None
    assert cs["rankable"] is False


# --- applicable_components / double-clause rankability guard (Task D-priority-3/4) --

def test_applicable_components_single_name_gets_all_seven():
    assert applicable_components(is_fund=False) == COMPONENTS


def test_applicable_components_fund_excludes_earnings_and_political():
    out = applicable_components(is_fund=True)
    assert "earnings_proximity" not in out
    assert "political_flow" not in out
    assert len(out) == 5


def test_omitting_applicable_preserves_original_unconditional_behavior():
    comps = {
        "earnings_proximity": None, "news_recency": 1.0, "news_tone": 1.0,
        "momentum": 1.0, "regime_fit_score": 1.0, "political_flow": None,
        "relative_strength": None,
    }
    with_none = composite_score(comps, None)
    without_arg = composite_score(comps)
    assert with_none == without_arg
    assert with_none["components_not_applicable"] == []
    assert with_none["components_applicable"] == 7


def test_fund_candidate_classifies_earnings_and_political_as_not_applicable():
    comps = {
        "earnings_proximity": None, "news_recency": 1.0, "news_tone": 1.0,
        "momentum": 1.0, "regime_fit_score": 1.0, "political_flow": None,
        "relative_strength": 1.0,
    }
    cs = composite_score(comps, applicable_components(is_fund=True))
    assert set(cs["components_not_applicable"]) == {"earnings_proximity", "political_flow"}
    assert cs["components_missing"] == []   # nothing MISSING -- absence here is structural
    assert cs["components_applicable"] == 5
    assert cs["components_available"] == 5
    assert cs["rankable"] is True


def test_double_clause_guard_vetoes_low_applicable_count_even_when_fully_populated():
    # The exact "2-of-2 applicable passes" failure mode: only 2 components are
    # even conceptually possible, BOTH populated -- an available-only bar
    # (2 >= 2) would trivially pass. The applicable-count clause must
    # independently veto it regardless.
    comps = {"momentum": 1.0, "regime_fit_score": 1.0}
    cs = composite_score(comps, applicable=("momentum", "regime_fit_score"))
    assert cs["components_available"] == 2
    assert cs["components_applicable"] == 2
    assert cs["rankable"] is False   # vetoed despite 100% coverage of its applicable set


def test_narrow_applicable_set_with_enough_populated_and_enough_applicable_passes():
    comps = {
        "news_recency": 1.0, "news_tone": 1.0, "momentum": 1.0,
        "regime_fit_score": 1.0, "relative_strength": 1.0,
    }
    applicable = applicable_components(is_fund=True)  # 5 applicable
    cs = composite_score(comps, applicable)
    assert cs["rankable"] is True


# --- discovery_symbols --------------------------------------------------

def test_discovery_symbols_prioritizes_earnings_then_congressional():
    out = discovery_symbols(
        earnings_market_symbols=["AAA", "BBB"],
        congressional_symbols=["CCC", "BBB"],  # BBB deduped
        exclude=set(),
        cap=10,
    )
    assert out == ["AAA", "BBB", "CCC"]


def test_discovery_symbols_excludes_known_names():
    out = discovery_symbols(
        earnings_market_symbols=["AAA", "HELD"],
        congressional_symbols=["SEPARATED"],
        exclude={"HELD", "SEPARATED"},
        cap=10,
    )
    assert out == ["AAA"]


def test_discovery_symbols_respects_cap():
    out = discovery_symbols(
        earnings_market_symbols=["A", "B", "C"],
        congressional_symbols=["D", "E"],
        exclude=set(),
        cap=2,
    )
    assert out == ["A", "B"]


# --- build_ranking_ledger: end-to-end, the two Task-D-mandated proofs -------

def _candidate(symbol, components, screen_overrides=None, basis=None):
    return {
        "symbol": symbol,
        "screen": _screen_kwargs(**(screen_overrides or {})),
        "components": components,
        "basis": basis or {},
    }


def test_no_earnings_date_but_strong_signal_outranks_weak_earnings_name():
    # The account-holder-mandated proof: absence of an earnings date must never
    # disqualify a candidate, nor even handicap it relative to a weak-everything-
    # else name that merely happens to have a date.
    no_earnings_strong = _candidate("STRONG", {
        "earnings_proximity": None,      # no date at all
        "news_recency": 1.0,
        "news_tone": 1.0,
        "momentum": 1.0,
        "regime_fit_score": 1.0,
        "political_flow": None,
    })
    has_earnings_weak = _candidate("WEAK", {
        "earnings_proximity": 1.0,       # reports today
        "news_recency": 0.1,
        "news_tone": None,
        "momentum": 0.1,
        "regime_fit_score": 0.0,
        "political_flow": None,
    })
    result = build_ranking_ledger([no_earnings_strong, has_earnings_weak], top_n=5)
    strong_row = next(r for r in result["ledger"] if r["symbol"] == "STRONG")
    weak_row = next(r for r in result["ledger"] if r["symbol"] == "WEAK")
    assert strong_row["score"] > weak_row["score"]
    assert result["nominated"][0] == "STRONG"
    assert "STRONG" in result["nominated"] and "WEAK" in result["nominated"]


def test_thin_coverage_never_nominated_regardless_of_score():
    # Only 3 of 6 components available, all perfect (1.0) -> a flatteringly
    # high mean that must still never be nominated (min-coverage guard).
    thin_but_perfect = _candidate("THIN", {
        "earnings_proximity": 1.0,
        "news_recency": 1.0,
        "news_tone": 1.0,
        "momentum": None,
        "regime_fit_score": None,
        "political_flow": None,
    })
    well_covered = _candidate("COVERED", {
        "earnings_proximity": 0.5,
        "news_recency": 0.5,
        "news_tone": 0.5,
        "momentum": 0.5,
        "regime_fit_score": None,
        "political_flow": None,
    })
    result = build_ranking_ledger([thin_but_perfect, well_covered], top_n=5)
    thin_row = next(r for r in result["ledger"] if r["symbol"] == "THIN")
    assert thin_row["score"] == 1.0          # a real, flattering score...
    assert thin_row["rankable"] is False     # ...but never rankable
    assert thin_row["nominated"] is False
    assert "THIN" not in result["nominated"]
    assert "COVERED" in result["nominated"]


def test_hard_screen_failures_never_scored_or_nominated():
    held = _candidate("HELD", {
        "earnings_proximity": 1.0, "news_recency": 1.0, "news_tone": 1.0,
        "momentum": 1.0, "regime_fit_score": 1.0, "political_flow": 1.0,
    }, screen_overrides={"held": True})
    result = build_ranking_ledger([held], top_n=5)
    row = result["ledger"][0]
    assert row["screened_in"] is False
    assert row["screen_reason"] == "currently_held"
    assert row["score"] is None
    assert row["nominated"] is False
    assert result["nominated"] == []


def test_ties_broken_by_components_available():
    fewer = _candidate("FEWER", {
        "earnings_proximity": 1.0, "news_recency": 1.0, "news_tone": 1.0,
        "momentum": 1.0, "regime_fit_score": None, "political_flow": None,
    })
    more = _candidate("MORE", {
        "earnings_proximity": 1.0, "news_recency": 1.0, "news_tone": 1.0,
        "momentum": 1.0, "regime_fit_score": 1.0, "political_flow": 1.0,
    })
    result = build_ranking_ledger([fewer, more], top_n=1)
    assert result["nominated"] == ["MORE"]


def test_top_n_cuts_the_rest():
    cands = [
        _candidate(f"S{i}", {
            "earnings_proximity": None, "news_recency": 1.0, "news_tone": None,
            "momentum": 1.0, "regime_fit_score": 1.0, "political_flow": 1.0,
        })
        for i in range(5)
    ]
    result = build_ranking_ledger(cands, top_n=2)
    assert len(result["nominated"]) == 2
    assert len(result["ledger"]) == 5
