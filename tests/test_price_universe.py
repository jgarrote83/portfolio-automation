"""Unit tests for the EOD price-fetch universe (2026-07-13 audit finding 1).

The reference can target any role's `selected` incumbent (sleeve-roles.json), but
the collector's price fetch previously only covered held tickers + the ETF
watchlist + flex candidates — an unheld reference target (KMLM, IEF, VXUS, XLV,
USMV, COWZ, VTIP, ...) had no price, no gap row, and no way for band enforcement
to synthesize the buy that would close its underweight. Run:
    PYTHONPATH=src pytest tests/test_price_universe.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_price_universe  # noqa: E402
from shared.quadrants import roles_config, selected_core_members  # noqa: E402


def test_selected_core_members_covers_every_role():
    sel = selected_core_members()
    expected = {(r.get("selected") or "").upper() for r in roles_config()}
    expected.discard("")
    assert set(sel) == expected


def test_selected_core_members_deduped_and_upper():
    sel = selected_core_members()
    assert len(sel) == len(set(sel))
    assert all(t == t.upper() for t in sel)


def test_price_universe_includes_unheld_reference_targets():
    """None of these are held, in the ETF watchlist, or a flex candidate — only
    Task A's selected-core-members addition puts them in the fetch list."""
    universe = _build_price_universe(tickers=["SPY", "QQQ"], flex_candidate_tickers=[])
    for sym in ("KMLM", "IEF", "VXUS", "XLV", "USMV", "COWZ", "VTIP"):
        assert sym in universe


def test_price_universe_dedupes_preserving_first_occurrence_order():
    universe = _build_price_universe(tickers=["SMH", "SPY"], flex_candidate_tickers=["SMH"])
    assert universe.count("SMH") == 1
    assert universe.index("SMH") == 0   # held ticker order wins over selected-core/flex


def test_price_universe_includes_watchlist_and_flex_candidates():
    universe = _build_price_universe(tickers=[], flex_candidate_tickers=["MU"])
    assert "AIA" in universe   # _ETF_WATCHLIST member
    assert "MU" in universe
