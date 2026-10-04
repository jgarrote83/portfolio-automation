"""ORB Phase 0B -- `scripts/orb_phase0b_replay.py` (test-only harness; no snapshot, no network).

These tests use a SMALL SYNTHETIC fixture to exercise the harness mechanics. They are not
replay evidence -- the evidence comes from a real downloaded snapshot (see
`.review/phase0b-replay.md`).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from orb_phase0b_replay import (  # noqa: E402
    diff_outputs,
    run_replay,
    stub_spy_rows,
    synthetic_news_history,
)

_SNAP = {
    "date": "2026-10-02",
    "paper_account": {"available": True, "equity": 100000.0, "cash": 5000.0, "positions": []},
    "portfolio": {"positions": [{"ticker": "SPY"}, {"ticker": "GLD"}]},
    "market_shock": {"news_hits_total": 8, "news_hits_by_category": {"geopolitical": 8},
                     "spy": {"return_1d_pct": 0.4, "return_1d_zscore": 0.5}},
    "macro": {"data": {"VIXCLS": [{"date": "2026-10-02", "value": "15.0"},
                                  {"date": "2026-10-01", "value": "15.5"}]}},
    "news": {"market": [], "forex": [], "company": {}},
    "stock_news": [],
    "earnings_calendar": [{"symbol": "SPY", "date": "2026-10-10"}, {"symbol": "ZZZZ", "date": "2026-10-10"}],
    "effective_selected": {},
}


def test_diff_ignores_meta_and_reports_changed_and_absent_leaves():
    a = {"_meta": {"mode": "x"}, "k": {"a": 1, "b": [1, 2]}, "only_a": 5}
    b = {"_meta": {"mode": "y"}, "k": {"a": 1, "b": [1, 3]}, "only_b": 6}
    d = {x["path"]: (x["a"], x["b"]) for x in diff_outputs(a, b)}
    assert d == {"k.b[1]": (2, 3), "only_a": (5, "<absent>"), "only_b": ("<absent>", 6)}
    assert diff_outputs(a, a) == []


def test_stub_spy_reproduces_stored_move_and_is_deterministic():
    rows = stub_spy_rows({"return_1d_pct": 2.0, "return_1d_zscore": 2.0})
    assert len(rows) == 66
    assert rows == stub_spy_rows({"return_1d_pct": 2.0, "return_1d_zscore": 2.0})
    assert round((rows[0]["price"] / rows[1]["price"] - 1) * 100, 6) == 2.0
    assert stub_spy_rows(None)   # degrades to a default series, never raises


def test_synthetic_history_is_built_from_stored_figures_and_tag_is_optional():
    ms = {"news_hits_total": 7, "news_hits_by_category": {"geopolitical": 7}}
    tagged = synthetic_news_history(ms, n=12, tagged=True)
    untagged = synthetic_news_history(ms, n=12, tagged=False)
    assert len(tagged) == 12 and all(r["basis"] == "held_only" for r in tagged)
    assert all("basis" not in r for r in untagged)
    assert tagged[0]["total_hits"] == 7 and tagged[0]["dominant_category"] == "geopolitical"


def test_run_replay_covers_every_builder_and_never_aborts():
    out = run_replay(_SNAP)
    assert out["_meta"]["mode"] == "retired(branch)"          # this checkout has no flex pipeline
    for key in ("regime_gate", "reference_weights", "thematic_conviction", "r4_probe",
                "market_shock", "price_universe_core", "earnings_filter",
                "quadrant_allocation", "functional_coverage", "u3_check"):
        assert key in out, key
    assert [r["symbol"] for r in out["earnings_filter"]] == ["SPY"]    # ZZZZ is outside the book
    assert out["quadrant_allocation"]["available"] is not None or "error" in out["quadrant_allocation"]


def test_r4_probe_uses_the_new_label_on_this_checkout():
    out = run_replay(_SNAP)
    assert [e["reason"] for e in out["r4_probe"]] == ["non_roster_report_only"]


def test_u2_scenarios_untagged_history_enters_the_no_baseline_mode():
    out = run_replay(_SNAP)["market_shock"]
    assert out["no_history"]["news_hits_zscore"] is None
    assert out["untagged_history"]["news_hits_zscore"] is None        # old-basis rows ignored (U2)
    assert out["tagged_history"]["news_hits_zscore"] is not None      # >= min sessions, tagged
    assert out["no_history"]["price_level"] == out["tagged_history"]["price_level"]   # price channel identical


def test_real_history_scenarios_are_added_only_when_supplied():
    hist = [{"date": f"2026-09-{i + 1:02d}", "total_hits": 6, "hits_by_category": {}} for i in range(12)]
    ms = run_replay(_SNAP, hist)["market_shock"]
    assert ms["real_history_as_downloaded"]["news_hits_zscore"] is None   # untagged -> ignored
    assert ms["real_history_all_tagged"]["news_hits_zscore"] is not None
    assert "real_history_as_downloaded" not in run_replay(_SNAP)["market_shock"]
