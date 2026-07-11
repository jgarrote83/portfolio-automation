"""Three-way Separation Contract regression (DayTrade_Lab spec §1) — the lab,
the catalyst engine, and Core never bleed into each other; the flex sleeve cap
holds jointly across both flex engines.

Run: PYTHONPATH=src pytest tests/test_daytrade_separation.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from daytrade.config import DayTradeConfig  # noqa: E402
from daytrade.gates import run_validation_gates  # noqa: E402
from daytrade.handler import _coid as daytrade_coid  # noqa: E402
from daytrade.ledger import COID_PREFIX  # noqa: E402
from daytrade.sizing import size_daytrade_entry  # noqa: E402
from flex.config import FlexConfig  # noqa: E402
from flex.entry import size_flex_position  # noqa: E402
from flex.handler import _coid as flex_coid  # noqa: E402
from flex.handler import _flex_nominations, _symbols_notional  # noqa: E402
from flex.reconcile import reconcile_ledger  # noqa: E402
from flex.regime import CORE_TICKERS  # noqa: E402

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")


# ── order namespacing ────────────────────────────────────────────────────────

def test_order_prefixes_are_disjoint():
    d = daytrade_coid("2026-07-07", "ABCD", "e1")
    c = flex_coid("2026-07-07", "NVDA", "entry")
    assert d.startswith("FLEXD-") and COID_PREFIX == "FLEXD"
    assert c.startswith("FLEXC-")
    assert not c.startswith("FLEXD") and not d.startswith("FLEXC")


# ── symbol exclusivity ───────────────────────────────────────────────────────

def test_lab_discards_core_and_catalyst_symbols():
    cfg = DayTradeConfig(consolidated_source="fmp")
    for flag in ("in_core", "in_catalyst_ledger"):
        r = run_validation_gates(
            {"symbol": "SPY", "catalyst_class": "A"},
            {flag: True, "is_common": True, "prior_close": 20.0}, cfg)
        assert not r["survivor"]
        assert r["discard_reason"] == "core_or_catalyst_symbol"


def test_catalyst_nominations_exclude_daytrade_symbols():
    doc = {"flex_nominations": [
        {"symbol": "SPY"},     # core — dropped (pre-existing rule)
        {"symbol": "ABCD"},    # in the daytrade ledger — dropped (new rule)
        {"symbol": "NVDA"},    # clean — kept
    ]}
    syms = {n["symbol"] for n in _flex_nominations(doc, exclude=frozenset({"ABCD"}))}
    assert syms == {"NVDA"}
    assert all(s not in CORE_TICKERS for s in syms)


def test_catalyst_reconcile_ignores_lab_positions():
    """A broker position the catalyst ledger doesn't know (e.g. a FLEXD- lab
    position) is never touched by the catalyst reconcile."""
    lab_positions = [{"symbol": "ABCD", "qty": "300"}, {"symbol": "MU", "qty": "2"}]
    new_ledger, exits, repairs = reconcile_ledger({}, lab_positions, [])
    assert new_ledger == {} and exits == [] and repairs == []


# ── joint sleeve cap ─────────────────────────────────────────────────────────

def test_engines_cannot_jointly_exceed_sleeve_cap():
    """The arbitration seam from both sides: however the sleeve is split, the
    two engines' combined notional can never exceed FLEX_SLEEVE_CAP_PCT."""
    equity = 100_000.0
    fcfg = FlexConfig()                     # sleeve_cap_pct 25 ⇒ $25K budget
    dcfg = DayTradeConfig()
    sleeve_usd = fcfg.sleeve_cap_pct / 100.0 * equity

    for catalyst_open in (0.0, 10_000.0, 20_000.0, 24_500.0, 25_000.0):
        # Catalyst side: its own headroom already subtracts the lab (handler
        # passes sleeve_room = cap − flex − daytrade); mirror the seam here.
        for daytrade_open in (0.0, 3_000.0, 6_000.0):
            if catalyst_open + daytrade_open > sleeve_usd:
                continue   # starting state already at/over cap — sizers can only add
            room = max(0.0, sleeve_usd - catalyst_open - daytrade_open)
            f = size_flex_position(equity, 50.0, 1.5, fcfg, sleeve_room_usd=room)
            d = size_daytrade_entry(equity, 20.0, 19.7, dcfg,
                                    catalyst_open_notional=catalyst_open + f["notional_usd"],
                                    daytrade_open_notional=daytrade_open)
            total = (catalyst_open + daytrade_open
                     + f["notional_usd"] + d["notional_usd"])
            assert total <= sleeve_usd + 1e-6, (
                f"joint sleeve breached: {total} > {sleeve_usd} "
                f"(cat={catalyst_open}, lab={daytrade_open})")


def test_symbols_notional_sums_only_named_symbols():
    positions = [
        {"symbol": "ABCD", "market_value": "3000"},
        {"symbol": "NVDA", "market_value": "5000"},
        {"symbol": "SPY", "market_value": "17000"},
    ]
    assert _symbols_notional(positions, {"ABCD"}) == 3000.0
    assert _symbols_notional(positions, {"ABCD", "NVDA"}) == 8000.0


# ── the daily macro batch never sees the lab ─────────────────────────────────

def test_collector_and_analyzer_never_import_daytrade():
    for rel in (("collector", "handler.py"), ("analyzer", "handler.py")):
        path = os.path.join(_SRC, *rel)
        with open(path, encoding="utf-8") as f:
            src = f.read()
        assert "daytrade" not in src.lower(), f"{rel} references the lab"


def test_daytrade_package_never_imports_flex():
    pkg = os.path.join(_SRC, "daytrade")
    for name in os.listdir(pkg):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(pkg, name), encoding="utf-8") as f:
            src = f.read()
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import flex", "from flex")):
                raise AssertionError(f"daytrade/{name} imports flex.*: {stripped}")
