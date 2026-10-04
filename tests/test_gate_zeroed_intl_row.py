"""Gate-zeroed intl_broad gap row (session 2026-07-22, Task B-2).

PYTHONPATH=src pytest tests/test_gate_zeroed_intl_row.py -v
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer.handler import _build_reference_gaps


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
