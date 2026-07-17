"""Session 2026-07-17, Task B — `execution_config` snapshot block (#33(i) graduation).

Four consecutive report sessions guessed operative config numbers (tranche_pp_max,
gap_band_pp, etc.) because nothing surfaced them to the prompt — the 07-17 band
guess alone filed three unnecessary in-band overrides. `effective_execution_config`
is the single place both the collector's snapshot echo and (indirectly, by
construction) `reconcile`/`validate_trades` resolve these values from, so the two
can never disagree.

Run: PYTHONPATH=src pytest tests/test_execution_config.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shared.reference_execution import (  # noqa: E402
    REFERENCE_EXECUTION_DEFAULTS,
    effective_execution_config,
    reconcile,
)

_RISK_LIMITS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "src", "config", "risk-limits.json"
)


def _real_risk_limits() -> dict:
    with open(_RISK_LIMITS_FILE, encoding="utf-8") as f:
        return json.load(f)


def test_matches_real_risk_limits_json():
    cfg = _real_risk_limits()
    out = effective_execution_config(cfg)
    assert out["gap_band_pp"] == cfg["override_protocol"]["gap_band_pp"]
    assert out["max_magnitude_pp"] == cfg["override_protocol"]["max_magnitude_pp"]
    assert out["re_risk_min_evidence"] == cfg["override_protocol"]["re_risk_min_evidence"]
    assert out["tranche_pp_max"] == cfg["reference_execution"]["tranche_pp_max"]
    assert out["enforce"] == cfg["reference_execution"]["enforce"]
    assert out["enforcement_turnover_max_pct"] == cfg["reference_execution"]["enforcement_turnover_max_pct"]
    assert out["min_notional_usd"] == cfg["reference_execution"]["min_notional_usd"]
    assert out["sleeve_floor_pct_of_core"] == cfg["sleeve_floor_pct_of_core"]
    assert out["de_risk_min_evidence"] == 1


def test_defaults_path_when_keys_absent():
    out = effective_execution_config({})
    assert out["gap_band_pp"] == 5.0
    assert out["max_magnitude_pp"] == 15.0
    assert out["re_risk_min_evidence"] == 2
    assert out["tranche_pp_max"] == REFERENCE_EXECUTION_DEFAULTS["tranche_pp_max"]
    assert out["enforce"] == REFERENCE_EXECUTION_DEFAULTS["enforce"]
    assert out["enforcement_turnover_max_pct"] == REFERENCE_EXECUTION_DEFAULTS["enforcement_turnover_max_pct"]
    assert out["min_notional_usd"] == REFERENCE_EXECUTION_DEFAULTS["min_notional_usd"]
    assert out["sleeve_floor_pct_of_core"] == 0.1


def test_partial_cfg_falls_back_per_key():
    cfg = {"reference_execution": {"tranche_pp_max": 3.0}}
    out = effective_execution_config(cfg)
    assert out["tranche_pp_max"] == 3.0
    assert out["enforce"] == REFERENCE_EXECUTION_DEFAULTS["enforce"]
    assert out["gap_band_pp"] == 5.0


def test_parity_with_reconcile_internal_resolution():
    """`reconcile()` resolves band/tranche/enforce/min_notional from `cfg`
    internally; `effective_execution_config` must report the SAME numbers it
    actually used. Drive reconcile with a real gap so its resolved band/tranche
    show up in the output, and compare against the helper's echo of the same cfg."""
    cfg = {
        "override_protocol": {"max_magnitude_pp": 12.0, "re_risk_min_evidence": 3, "gap_band_pp": 4.0},
        "reference_execution": {"tranche_pp_max": 6.0, "enforce": True,
                                "enforcement_turnover_max_pct": 25.0, "min_notional_usd": 200.0},
        "sleeve_floor_pct_of_core": 0.2,
    }
    gaps = [{"symbol": "GLD", "current_pct": 30.0, "reference_pct": 10.0, "price": 50.0}]
    ctx = {"deployment_gate": "open", "equity_usd": 100_000.0, "cash_usd": 0.0,
           "date": "2026-07-17", "exempt_holds": []}
    recon = reconcile(gaps, [], [], cfg, ctx)
    gld = recon["sleeves"]["GLD"]
    echoed = effective_execution_config(cfg)
    # gap 20pp, band 4pp, no override residual -> required_move_total = 20 - 4 = 16,
    # tranche-capped at required_move_today = min(16, 6) = 6 (matches echoed tranche_pp_max).
    assert gld["required_move_today_pp"] == echoed["tranche_pp_max"]
    assert echoed["gap_band_pp"] == cfg["override_protocol"]["gap_band_pp"]
    assert echoed["min_notional_usd"] == cfg["reference_execution"]["min_notional_usd"]
