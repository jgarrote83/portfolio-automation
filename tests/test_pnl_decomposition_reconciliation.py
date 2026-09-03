"""Unit tests for the P&L reconciliation break (2026-09-02, Task E1).

The 2026-09-02 report's Section 1 cited "SOXX -$1,073.93, GLD -$783.87" while
Section 4's table showed "SOXX -$1,035.61, GLD -$340.97" (GLD differs by
$442.90, 2.3x) — and Section 4's table summed to -$854.79 against Section 1's
claimed core -$354.96 (a $499.83 gap). Two different numbers for the same
quantity, published in one document.

Root cause: `_build_pnl_decomposition`'s `contributors` list — the ONE
deterministic per-symbol source both sections should cite verbatim — was
capped at the top 15 (by |total_usd|). The core roster carries ~17 sleeves;
once a bucket holds more symbols than the cap, `sum(contributors[].total_usd)`
no longer equals the bucket's own `total_usd`, so a report section built by
summing "the contributors list" (or, worse, a symbol excluded from the top 15
entirely) can never reconcile against a section quoting the bucket total
directly — exactly the two-different-numbers shape observed. Fix: make
`contributors` COMPLETE (every symbol in the bucket), so there is only ever
ONE number for a given symbol/bucket, cited verbatim by both sections.

Run: PYTHONPATH=src pytest tests/test_pnl_decomposition_reconciliation.py
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_pnl_decomposition  # noqa: E402


def _fill(symbol, side, qty, price, day):
    return {"symbol": symbol, "side": side, "qty": qty, "price": price,
            "transaction_time": f"2026-08-{day:02d}T14:30:00Z"}


def _position(symbol, unrealized_pl):
    return {"ticker": symbol, "unrealized_pl": unrealized_pl}


def _many_symbol_fixture(n=17):
    """A core-roster-shaped bucket with MORE than 15 symbols, each with a
    distinct, easily-traceable P&L so a truncation bug is unmissable."""
    symbols = [f"COR{i:02d}" for i in range(n)]
    fills = []
    positions = []
    for i, sym in enumerate(symbols):
        qty = 10 + i
        buy_price = 100.0
        fills.append(_fill(sym, "buy", qty, buy_price, day=1))
        # Distinct unrealized P&L per symbol (i=0 largest magnitude .. n-1 smallest).
        positions.append(_position(sym, unrealized_pl=-(1000.0 - i * 10)))
    return symbols, fills, positions


def _alp_with_fills(fills):
    alp = MagicMock()
    alp.get_activities.return_value = fills
    return alp


def test_every_core_symbol_gets_a_contributor_row(monkeypatch):
    """17 core-roster-shaped symbols (more than the old top-15 cap) — every
    one of them must appear in `contributors`, not just the top 15."""
    symbols, fills, positions = _many_symbol_fixture(17)
    monkeypatch.setattr("collector.handler.CORE_ROSTER", tuple(symbols))
    monkeypatch.setattr("collector.handler.LEGACY_EXITS", ())
    monkeypatch.setattr("collector.handler.role_of", lambda sym: None)
    alp = _alp_with_fills(fills)
    paper_account = {"positions": positions, "equity": 100_000.0}
    out = _build_pnl_decomposition(alp, paper_account, "2026-08-01")
    contributor_symbols = {c["symbol"] for c in out["core_current"]["contributors"]}
    assert contributor_symbols == set(symbols)
    assert len(out["core_current"]["contributors"]) == 17


def test_section_level_total_equals_sum_of_per_position_rows(monkeypatch):
    """The literal E1 acceptance criterion: a bucket's `total_usd` must equal
    the sum of its own `contributors[].total_usd` — the property that makes
    it safe for a report to cite EITHER the bucket total OR a summed table of
    per-position rows and always get the SAME number."""
    symbols, fills, positions = _many_symbol_fixture(17)
    monkeypatch.setattr("collector.handler.CORE_ROSTER", tuple(symbols))
    monkeypatch.setattr("collector.handler.LEGACY_EXITS", ())
    monkeypatch.setattr("collector.handler.role_of", lambda sym: None)
    alp = _alp_with_fills(fills)
    paper_account = {"positions": positions, "equity": 100_000.0}
    out = _build_pnl_decomposition(alp, paper_account, "2026-08-01")
    core = out["core_current"]
    row_sum = round(sum(c["total_usd"] for c in core["contributors"]), 2)
    assert row_sum == core["total_usd"]


def test_per_position_total_is_realized_plus_unrealized(monkeypatch):
    """Each contributor row's own total_usd = realized_usd + unrealized_usd —
    the SAME number a narrative section and a table section must both cite,
    never separately recomputed from a partial view (e.g. unrealized-only)."""
    symbols, fills, positions = _many_symbol_fixture(3)
    monkeypatch.setattr("collector.handler.CORE_ROSTER", tuple(symbols))
    monkeypatch.setattr("collector.handler.LEGACY_EXITS", ())
    monkeypatch.setattr("collector.handler.role_of", lambda sym: None)
    alp = _alp_with_fills(fills)
    paper_account = {"positions": positions, "equity": 100_000.0}
    out = _build_pnl_decomposition(alp, paper_account, "2026-08-01")
    for c in out["core_current"]["contributors"]:
        assert c["total_usd"] == round(c["realized_usd"] + c["unrealized_usd"], 2)
