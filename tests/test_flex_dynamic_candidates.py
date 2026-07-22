"""Task A + B-2 tests for the dynamic watch_candidates flex funnel (FOLLOWUPS #8 v2)
and the gate-zeroed intl_broad gap row (session 2026-07-22).

Run against master source — all FAIL.
Run against feat/20260722-flex-dynamic-candidates — all PASS.

PYTHONPATH=src pytest tests/test_flex_dynamic_candidates.py -v
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import json
from pathlib import Path

import collector.handler as ch
from collector.handler import _load_flex_candidates
from analyzer.handler import _build_reference_gaps, _split_response
from executor.handler import _extract_trades, _validation_refusal


# ---------------------------------------------------------------------------
# Task A1 — Executor indifference
# ---------------------------------------------------------------------------

def test_extract_trades_ignores_watch_candidates():
    """_extract_trades must return the same list whether watch_candidates is present or not."""
    trades = [{"id": "T1", "side": "buy", "symbol": "GLD", "quantity": 5}]
    doc_without = {"trades": trades}
    doc_with = {
        "trades": trades,
        "watch_candidates": [{"symbol": "LNG", "reason": "energy catalyst"}],
    }
    assert _extract_trades(doc_without) == _extract_trades(doc_with)


def test_validation_refusal_ignores_watch_candidates():
    """_validation_refusal must be identical regardless of watch_candidates."""
    trades = [
        {"id": "T1", "side": "buy", "symbol": "GLD", "quantity": 5,
         "validation": {"status": "passed", "reasons": []}},
    ]
    doc_without = {"trades": trades}
    doc_with = {
        "trades": trades,
        "watch_candidates": [{"symbol": "LNG", "reason": "energy catalyst"}],
    }
    result_without = _validation_refusal(doc_without, trades, "2026-07-22")
    result_with = _validation_refusal(doc_with, trades, "2026-07-22")
    assert result_without == result_with


# ---------------------------------------------------------------------------
# Task A1 — watch_candidates sanitization in _split_response
# ---------------------------------------------------------------------------

_GOOD_REPORT = "# Report\n\nsome text"
_MARKER = "===TRADES_JSON==="


def _make_raw(trades_json: dict) -> str:
    return _GOOD_REPORT + "\n" + _MARKER + "\n" + json.dumps(trades_json)


def test_split_response_passes_through_valid_watch_candidates():
    raw = _make_raw({
        "trades": [],
        "watch_candidates": [
            {"symbol": "LNG", "reason": "energy catalyst"},
            {"symbol": "PANW", "reason": "cybersecurity"},
        ],
    })
    _, trades_obj = _split_response(raw, "2026-07-22")
    wc = trades_obj.get("watch_candidates", [])
    assert len(wc) == 2
    syms = {e["symbol"] for e in wc}
    assert syms == {"LNG", "PANW"}


def test_split_response_drops_entry_with_missing_symbol():
    raw = _make_raw({
        "trades": [],
        "watch_candidates": [
            {"reason": "no symbol here"},
            {"symbol": "LNG", "reason": "ok"},
        ],
    })
    _, trades_obj = _split_response(raw, "2026-07-22")
    wc = trades_obj.get("watch_candidates", [])
    assert len(wc) == 1
    assert wc[0]["symbol"] == "LNG"


def test_split_response_drops_non_dict_entry():
    raw = _make_raw({
        "trades": [],
        "watch_candidates": ["LNG", {"symbol": "PANW", "reason": "ok"}],
    })
    _, trades_obj = _split_response(raw, "2026-07-22")
    wc = trades_obj.get("watch_candidates", [])
    assert len(wc) == 1
    assert wc[0]["symbol"] == "PANW"


def test_split_response_uppercases_symbol():
    raw = _make_raw({
        "trades": [],
        "watch_candidates": [{"symbol": "lng", "reason": "lower case"}],
    })
    _, trades_obj = _split_response(raw, "2026-07-22")
    wc = trades_obj.get("watch_candidates", [])
    assert len(wc) == 1
    assert wc[0]["symbol"] == "LNG"


def test_split_response_trims_watch_candidates_to_6():
    entries = [{"symbol": f"T{i}", "reason": ""} for i in range(10)]
    raw = _make_raw({"trades": [], "watch_candidates": entries})
    _, trades_obj = _split_response(raw, "2026-07-22")
    assert len(trades_obj.get("watch_candidates", [])) == 6


def test_split_response_removes_non_list_watch_candidates():
    raw = _make_raw({"trades": [], "watch_candidates": "LNG,PANW"})
    _, trades_obj = _split_response(raw, "2026-07-22")
    assert "watch_candidates" not in trades_obj


def test_split_response_no_watch_candidates_key_unchanged():
    raw = _make_raw({"trades": []})
    _, trades_obj = _split_response(raw, "2026-07-22")
    assert "watch_candidates" not in trades_obj


# ---------------------------------------------------------------------------
# Task A2 — flex/regime FLEX_REENTERABLE and flex_separation_set
# ---------------------------------------------------------------------------

def test_flex_reenterable_contains_expected_names():
    from flex.regime import FLEX_REENTERABLE
    assert "INTC" in FLEX_REENTERABLE
    assert "MCK" in FLEX_REENTERABLE
    assert "PPA" in FLEX_REENTERABLE
    assert "EUAD" in FLEX_REENTERABLE


def test_flex_separation_set_includes_core_roster_members(monkeypatch):
    """SOXX (semis pool member) must appear in the separation set when flat."""
    from flex.regime import flex_separation_set
    from shared.quadrants import CORE_ROSTER
    sep = flex_separation_set(set())
    # All CORE_ROSTER members should be separated (FLEX_REENTERABLE ones may be
    # carved out when flat)
    for sym in CORE_ROSTER:
        if sym not in ("INTC", "MCK", "PPA", "EUAD"):
            assert sym in sep, f"{sym} should be in separation set"


def test_flex_separation_set_carves_out_flat_reenterable():
    """Flat FLEX_REENTERABLE names must NOT be in the separation set."""
    from flex.regime import flex_separation_set, FLEX_REENTERABLE
    sep = flex_separation_set(set())   # nothing held
    for sym in FLEX_REENTERABLE:
        assert sym not in sep, f"Flat {sym} should be carved out of separation set"


def test_flex_separation_set_keeps_held_reenterable():
    """A held FLEX_REENTERABLE name IS in the separation set (separate via exclude)."""
    from flex.regime import flex_separation_set
    sep = flex_separation_set({"INTC"})
    assert "INTC" in sep


# ---------------------------------------------------------------------------
# Task A2 — _load_flex_candidates v2 sanitization matrix
# ---------------------------------------------------------------------------

def _make_static_candidates_file(candidates: list[str], tmp_path: Path) -> Path:
    p = tmp_path / "flex-candidates.json"
    p.write_text(json.dumps({"candidates": candidates}))
    return p


def _make_trades_doc(watch_candidates: list[dict]) -> dict:
    return {
        "date": "2026-07-21",
        "trades": [],
        "watch_candidates": watch_candidates,
    }


def test_dynamic_garbage_symbol_dropped(tmp_path, monkeypatch):
    """Symbol that fails the regex is dropped."""
    p = _make_static_candidates_file(["ETN"], tmp_path)
    monkeypatch.setattr(ch, "_FLEX_CANDIDATES_FILE", p)
    monkeypatch.setattr(ch, "read_trades", lambda d: _make_trades_doc(
        [{"symbol": "$BAD!", "reason": "garbage"}]
    ) if d == "2026-07-21" else None)
    tickers, prov = _load_flex_candidates(exclude=set(), today="2026-07-22")
    assert "$BAD!" not in tickers


def test_dynamic_lowercase_symbol_normalized(tmp_path, monkeypatch):
    """Lowercase symbol is uppercased; if valid after uppercasing, it survives."""
    p = _make_static_candidates_file(["ETN"], tmp_path)
    monkeypatch.setattr(ch, "_FLEX_CANDIDATES_FILE", p)
    monkeypatch.setattr(ch, "read_trades", lambda d: _make_trades_doc(
        [{"symbol": "lng", "reason": "energy"}]
    ) if d == "2026-07-21" else None)
    tickers, prov = _load_flex_candidates(exclude=set(), today="2026-07-22")
    assert "LNG" in tickers
    assert prov["LNG"] == "dynamic"


def test_dynamic_separation_set_member_dropped(tmp_path, monkeypatch):
    """A symbol in the core roster (SOXX = semis pool) is dropped."""
    from shared.quadrants import CORE_ROSTER
    # SOXX should be in CORE_ROSTER (semis pool member); if it isn't for some reason,
    # fall back to any CORE_ROSTER member that's not FLEX_REENTERABLE
    from flex.regime import FLEX_REENTERABLE
    test_sym = "SOXX"
    if test_sym not in CORE_ROSTER:
        test_sym = next(s for s in CORE_ROSTER if s not in FLEX_REENTERABLE)
    p = _make_static_candidates_file(["ETN"], tmp_path)
    monkeypatch.setattr(ch, "_FLEX_CANDIDATES_FILE", p)
    monkeypatch.setattr(ch, "read_trades", lambda d: _make_trades_doc(
        [{"symbol": test_sym, "reason": "core member"}]
    ) if d == "2026-07-21" else None)
    tickers, _ = _load_flex_candidates(exclude=set(), today="2026-07-22")
    assert test_sym not in tickers


def test_dynamic_held_name_dropped(tmp_path, monkeypatch):
    """A symbol that is currently held is dropped."""
    p = _make_static_candidates_file(["ETN"], tmp_path)
    monkeypatch.setattr(ch, "_FLEX_CANDIDATES_FILE", p)
    monkeypatch.setattr(ch, "read_trades", lambda d: _make_trades_doc(
        [{"symbol": "LNG", "reason": "held"}]
    ) if d == "2026-07-21" else None)
    tickers, _ = _load_flex_candidates(exclude={"LNG"}, today="2026-07-22")
    assert "LNG" not in tickers


def test_dynamic_non_reenterable_legacy_dropped(tmp_path, monkeypatch):
    """GOOGL is a LEGACY_EXIT but not FLEX_REENTERABLE → dropped."""
    p = _make_static_candidates_file(["ETN"], tmp_path)
    monkeypatch.setattr(ch, "_FLEX_CANDIDATES_FILE", p)
    monkeypatch.setattr(ch, "read_trades", lambda d: _make_trades_doc(
        [{"symbol": "GOOGL", "reason": "big tech"}]
    ) if d == "2026-07-21" else None)
    tickers, _ = _load_flex_candidates(exclude=set(), today="2026-07-22")
    assert "GOOGL" not in tickers


def test_dynamic_reenterable_legacy_flat_survives(tmp_path, monkeypatch):
    """INTC is FLEX_REENTERABLE and currently flat → survives."""
    p = _make_static_candidates_file(["ETN"], tmp_path)
    monkeypatch.setattr(ch, "_FLEX_CANDIDATES_FILE", p)
    monkeypatch.setattr(ch, "read_trades", lambda d: _make_trades_doc(
        [{"symbol": "INTC", "reason": "turnaround"}]
    ) if d == "2026-07-21" else None)
    tickers, prov = _load_flex_candidates(exclude=set(), today="2026-07-22")
    assert "INTC" in tickers
    assert prov["INTC"] == "dynamic"


def test_static_priority_in_cap_overflow(tmp_path, monkeypatch):
    """When static + dynamic > 20, static names are never evicted."""
    static = [f"S{i:02d}" for i in range(18)]  # 18 static
    dynamic_wc = [{"symbol": f"D{i:02d}", "reason": ""} for i in range(10)]  # 10 dynamic
    p = _make_static_candidates_file(static, tmp_path)
    monkeypatch.setattr(ch, "_FLEX_CANDIDATES_FILE", p)
    monkeypatch.setattr(ch, "read_trades", lambda d: _make_trades_doc(dynamic_wc)
                        if d == "2026-07-21" else None)
    tickers, prov = _load_flex_candidates(exclude=set(), today="2026-07-22")
    assert len(tickers) == 20
    # All 18 static names must be present
    for s in static:
        assert s in tickers, f"Static name {s} was evicted"
    # Only 2 dynamic names should fit
    dynamic_present = [t for t in tickers if prov.get(t) == "dynamic"]
    assert len(dynamic_present) == 2


# ---------------------------------------------------------------------------
# Task A2 — Walk-back: newest trades file wins
# ---------------------------------------------------------------------------

def test_walkback_newest_wins(tmp_path, monkeypatch):
    """When multiple trade files exist, the most recent one (within 7 days) wins."""
    p = _make_static_candidates_file(["ETN"], tmp_path)
    monkeypatch.setattr(ch, "_FLEX_CANDIDATES_FILE", p)

    def _read(d):
        if d == "2026-07-21":
            return _make_trades_doc([{"symbol": "LNG", "reason": "most recent"}])
        if d == "2026-07-20":
            return _make_trades_doc([{"symbol": "PANW", "reason": "older"}])
        return None

    monkeypatch.setattr(ch, "read_trades", _read)
    tickers, prov = _load_flex_candidates(exclude=set(), today="2026-07-22")
    assert "LNG" in tickers
    assert "PANW" not in tickers   # older file ignored — only newest wins


def test_walkback_no_trades_within_7_days_static_only(tmp_path, monkeypatch):
    """No trades file within 7 days → only static candidates returned."""
    p = _make_static_candidates_file(["ETN", "NEE"], tmp_path)
    monkeypatch.setattr(ch, "_FLEX_CANDIDATES_FILE", p)
    monkeypatch.setattr(ch, "read_trades", lambda d: None)
    tickers, prov = _load_flex_candidates(exclude=set(), today="2026-07-22")
    assert set(tickers) == {"ETN", "NEE"}
    assert all(prov[t] == "static" for t in tickers)


# ---------------------------------------------------------------------------
# Task A3 — Provenance tagging
# ---------------------------------------------------------------------------

def test_provenance_static_source(tmp_path, monkeypatch):
    """Static candidates get source='static'."""
    p = _make_static_candidates_file(["ETN"], tmp_path)
    monkeypatch.setattr(ch, "_FLEX_CANDIDATES_FILE", p)
    monkeypatch.setattr(ch, "read_trades", lambda d: None)
    tickers, prov = _load_flex_candidates(exclude=set(), today="2026-07-22")
    assert prov["ETN"] == "static"


def test_provenance_dynamic_source(tmp_path, monkeypatch):
    """Dynamic candidates get source='dynamic'."""
    p = _make_static_candidates_file(["ETN"], tmp_path)
    monkeypatch.setattr(ch, "_FLEX_CANDIDATES_FILE", p)
    monkeypatch.setattr(ch, "read_trades", lambda d: _make_trades_doc(
        [{"symbol": "LNG", "reason": ""}]
    ) if d == "2026-07-21" else None)
    tickers, prov = _load_flex_candidates(exclude=set(), today="2026-07-22")
    assert prov.get("LNG") == "dynamic"


def test_static_overrides_dynamic_same_symbol(tmp_path, monkeypatch):
    """If a symbol appears in both static and dynamic, it is 'static' (static priority)."""
    p = _make_static_candidates_file(["ETN", "LNG"], tmp_path)
    monkeypatch.setattr(ch, "_FLEX_CANDIDATES_FILE", p)
    monkeypatch.setattr(ch, "read_trades", lambda d: _make_trades_doc(
        [{"symbol": "LNG", "reason": "also in static"}]
    ) if d == "2026-07-21" else None)
    tickers, prov = _load_flex_candidates(exclude=set(), today="2026-07-22")
    # LNG should appear only once
    assert tickers.count("LNG") == 1
    assert prov["LNG"] == "static"


# ---------------------------------------------------------------------------
# Task B-2 — Gate-zeroed intl_broad gap row
# ---------------------------------------------------------------------------

def _make_snapshot_for_gaps(
    gate_status: str,
    vxus_held: bool,
    vxus_in_targets: bool,
    role_selection_present: bool = True,
) -> dict:
    """Build a minimal snapshot for _build_reference_gaps testing."""
    equity = 100_000.0
    paper_positions = []
    if vxus_held:
        paper_positions.append({
            "ticker": "VXUS", "qty": 50.0, "quantity": 50.0,
            "market_value": 3_000.0, "current_price": 60.0,
        })
    target_weights: dict = {"SPY": 30.0}
    if vxus_in_targets:
        target_weights["VXUS"] = 0.0  # gate-zeroed but still in targets

    roles = []
    if role_selection_present:
        roles = [
            {"role_id": "intl_broad", "selected": "VXUS", "selection": "rotation"},
            {"role_id": "us_anchor",  "selected": "SPY",  "selection": "scorecard"},
        ]

    return {
        "date": "2026-07-22",
        "regime_gate": {"status": gate_status, "reasons": []},
        "reference_weights": {
            "available": True,
            "target_weights_pct": target_weights,
        },
        "paper_account": {
            "available": True,
            "equity": equity,
            "cash": 5_000.0,
            "positions": paper_positions,
        },
        "prices": {
            "SPY": {"c": 560.0},
            "VXUS": {"c": 60.0},
        },
        "role_selection": {"roles": roles},
        "intl_governance": {"leader_pick": None, "broad_pp": 2.0},
    }


def test_gate_zeroed_row_emitted_when_gate_closed_vxus_absent():
    """Gate closed + VXUS not held + VXUS not in targets → gate-zeroed row emitted."""
    snap = _make_snapshot_for_gaps(gate_status="closed", vxus_held=False,
                                   vxus_in_targets=False)
    gaps, _ = _build_reference_gaps(snap)
    syms = [g["symbol"] for g in gaps]
    assert "VXUS" in syms
    vxus_row = next(g for g in gaps if g["symbol"] == "VXUS")
    assert vxus_row["reference_pct"] == 0.0
    assert vxus_row["current_pct"] == 0.0
    assert vxus_row.get("gate_zeroed") is True
    assert vxus_row.get("off_roster") is False


def test_gate_zeroed_row_absent_when_gate_open():
    """Gate open → no gate-zeroed row for VXUS."""
    snap = _make_snapshot_for_gaps(gate_status="open", vxus_held=False,
                                   vxus_in_targets=False)
    gaps, _ = _build_reference_gaps(snap)
    syms = [g["symbol"] for g in gaps]
    assert "VXUS" not in syms


def test_gate_zeroed_row_absent_when_vxus_already_in_targets():
    """VXUS already in target_weights_pct → no duplicate row."""
    snap = _make_snapshot_for_gaps(gate_status="closed", vxus_held=False,
                                   vxus_in_targets=True)
    gaps, _ = _build_reference_gaps(snap)
    vxus_rows = [g for g in gaps if g["symbol"] == "VXUS"]
    assert len(vxus_rows) == 1
    # The existing row should NOT have gate_zeroed=True (it came from targets)
    assert not vxus_rows[0].get("gate_zeroed")


def test_gate_zeroed_row_absent_when_vxus_held():
    """VXUS held → it's already in universe from the held side."""
    snap = _make_snapshot_for_gaps(gate_status="closed", vxus_held=True,
                                   vxus_in_targets=False)
    gaps, _ = _build_reference_gaps(snap)
    vxus_rows = [g for g in gaps if g["symbol"] == "VXUS"]
    assert len(vxus_rows) == 1
    # Held row should not have gate_zeroed flag
    assert not vxus_rows[0].get("gate_zeroed")


def test_gate_zeroed_row_inert_to_reconcile():
    """A gate-zeroed VXUS row (ref=0, current=0, held_qty=0) produces no enforcement trade."""
    from shared.reference_execution import reconcile as _reconcile
    snap = _make_snapshot_for_gaps(gate_status="closed", vxus_held=False,
                                   vxus_in_targets=False)
    gaps, ctx = _build_reference_gaps(snap)

    # Provide a minimal rex_cfg
    rex_cfg = {
        "gap_band_pp": 5.0,
        "tranche_pp_max": 10.0,
        "max_magnitude_pp": 15.0,
        "re_risk_min_evidence": 2,
        "de_risk_min_evidence": 1,
        "enforce": True,
        "enforcement_turnover_max_pct": 20.0,
        "min_notional_usd": 115.0,
        "sleeve_floor_pct_of_core": 0.1,
        "exempt_holds": [],
        "override_protocol": {},
        "reference_execution": {},
    }
    recon = _reconcile(gaps, [], [], rex_cfg, ctx)
    enforced_syms = [t.get("symbol") for t in recon.get("enforced_trades", [])]
    assert "VXUS" not in enforced_syms


def test_gate_zeroed_row_no_duplicate_when_role_selection_absent():
    """When role_selection is absent, no gate-zeroed row is added (safe fallback)."""
    snap = _make_snapshot_for_gaps(gate_status="closed", vxus_held=False,
                                   vxus_in_targets=False, role_selection_present=False)
    gaps, _ = _build_reference_gaps(snap)
    syms = [g["symbol"] for g in gaps]
    # No VXUS in gaps since role_selection is empty
    assert "VXUS" not in syms
