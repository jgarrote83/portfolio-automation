"""DayTrade Lab validation gates (spec §3) — pass/discard per gate, missing-data
fail-closed, basis recording, survivor tie-break.

Run: PYTHONPATH=src pytest tests/test_daytrade_gates.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from daytrade.config import DayTradeConfig  # noqa: E402
from daytrade.gates import run_validation_gates, select_survivors  # noqa: E402

CFG = DayTradeConfig(consolidated_source="fmp")
CFG_UNMEASURED = DayTradeConfig()   # ships consolidated_source="unavailable"


def _good_data(**over):
    """A candidate that passes every gate under CFG (consolidated=fmp)."""
    d = {
        "in_core": False, "in_catalyst_ledger": False, "in_daytrade_ledger": False,
        "is_common": True,
        "prior_close": 20.0,
        "pm_last": 21.5,                    # +7.5% gap
        "pm_iex_volume": 90_000.0,
        "pm_iex_vol_30d_avg": 20_000.0,     # rvol 4.5×
        "pm_dollar_volume": 80_000_000.0,   # rotation vs 50M float ≈ 7.4%
        "float_shares": 50_000_000.0,
        "market_cap": 1_000_000_000.0,      # sub-$2B ⇒ dilution gate runs
        "dilution_flag": False,
        "bid": 21.49, "ask": 21.51,         # ~0.09% spread
        "prior_day_high": 20.4, "prior_day_low": 19.2,
        "pm_high": 21.8, "pm_low": 20.6,
    }
    d.update(over)
    return d


def _cand(sym="ABCD", klass="A"):
    return {"symbol": sym, "catalyst_class": klass}


def test_full_pass_records_levels_and_bases():
    r = run_validation_gates(_cand(), _good_data(), CFG)
    assert r["survivor"] and r["discard_reason"] is None
    assert r["levels"]["prior_day_high"] == 20.4
    assert r["levels"]["orb_high"] is None          # ORB pending pre-open
    assert r["bases"]["rvol"] == "iex_ratio"
    assert r["bases"]["pm_dollar_volume"] == "fmp"
    assert r["bases"]["spread"] == "iex_quote"
    # every recorded gate carries the full quad + pass flag
    for g in r["gates"]:
        assert {"gate", "value", "threshold", "basis", "passed"} <= set(g)


def test_gate1_core_and_catalyst_symbols_discarded():
    for field in ("in_core", "in_catalyst_ledger", "in_daytrade_ledger"):
        r = run_validation_gates(_cand(), _good_data(**{field: True}), CFG)
        assert not r["survivor"] and r["discard_reason"] == "core_or_catalyst_symbol"


def test_gate1_not_common_and_price_band():
    assert run_validation_gates(_cand(), _good_data(is_common=False), CFG)[
        "discard_reason"] == "not_us_common_stock"
    assert run_validation_gates(_cand(), _good_data(is_common=None), CFG)[
        "discard_reason"] == "missing_data"
    assert run_validation_gates(
        _cand(), _good_data(prior_close=150.0, pm_last=160.0), CFG,
    )["discard_reason"] == "price_out_of_band"
    assert run_validation_gates(
        _cand(), _good_data(prior_close=3.0, pm_last=3.2), CFG,
    )["discard_reason"] == "price_out_of_band"


def test_gate2_gap_direction_and_size():
    r = run_validation_gates(_cand(), _good_data(pm_last=19.0), CFG)
    assert r["discard_reason"] == "gap_down"        # → avoid-list
    r = run_validation_gates(_cand(), _good_data(pm_last=20.4), CFG)
    assert r["discard_reason"] == "gap_below_min"   # +2% < 4%
    r = run_validation_gates(_cand(), _good_data(pm_last=None), CFG)
    assert r["discard_reason"] == "missing_data"


def test_gate3_rvol():
    r = run_validation_gates(_cand(), _good_data(pm_iex_volume=40_000.0), CFG)
    assert r["discard_reason"] == "rvol_below_min"  # 2× < 3×
    r = run_validation_gates(_cand(), _good_data(pm_iex_vol_30d_avg=None), CFG)
    assert r["discard_reason"] == "missing_data"


def test_gate3_consolidated_unmeasured_discards_loudly():
    """The DECISION-0 ship state: source unavailable ⇒ basis unmeasured ⇒ discard."""
    r = run_validation_gates(_cand(), _good_data(), CFG_UNMEASURED)
    assert not r["survivor"]
    assert r["discard_reason"] == "consolidated_unmeasured"
    gate = next(g for g in r["gates"] if g["gate"] == "pm_dollar_volume")
    assert gate["basis"] == "unmeasured" and gate["passed"] is False


def test_gate3_float_band_and_rotation():
    assert run_validation_gates(
        _cand(), _good_data(float_shares=10_000_000.0), CFG,
    )["discard_reason"] == "float_out_of_band"
    assert run_validation_gates(
        _cand(), _good_data(float_shares=200_000_000.0), CFG,
    )["discard_reason"] == "float_out_of_band"
    assert run_validation_gates(
        _cand(), _good_data(pm_dollar_volume=10_000_000.0), CFG,
    )["discard_reason"] == "rotation_below_min"     # ~0.9% < 5%


def test_gate3_missing_float_fails_closed_only_sub_2b():
    # sub-$2B ⇒ discard missing_data (opposite of the flex WATCH rule)
    r = run_validation_gates(_cand(), _good_data(float_shares=None), CFG)
    assert r["discard_reason"] == "missing_data"
    # ≥$2B ⇒ rotation unmeasured, survives to later gates + rvol tie-break
    r = run_validation_gates(
        _cand(), _good_data(float_shares=None, market_cap=5e9), CFG)
    assert r["survivor"]
    assert r["bases"]["float_rotation"] == "unmeasured"
    assert r["float_rotation"] is None


def test_gate4_dilution():
    assert run_validation_gates(
        _cand(), _good_data(dilution_flag=True), CFG,
    )["discard_reason"] == "dilution_overhang"
    assert run_validation_gates(
        _cand(), _good_data(dilution_flag=None), CFG,
    )["discard_reason"] == "filings_unavailable"
    # ≥$2B skips the dilution gate entirely
    r = run_validation_gates(
        _cand(), _good_data(dilution_flag=None, market_cap=5e9), CFG)
    assert r["survivor"]


def test_gate5_spread():
    assert run_validation_gates(
        _cand(), _good_data(bid=21.0, ask=21.2), CFG,
    )["discard_reason"] == "spread_too_wide"        # ~0.95%
    assert run_validation_gates(
        _cand(), _good_data(bid=None), CFG,
    )["discard_reason"] == "missing_data"
    gate = next(
        g for g in run_validation_gates(_cand(), _good_data(), CFG)["gates"]
        if g["gate"] == "spread")
    assert gate["basis"] == "iex_quote"


def test_survivor_tiebreak_rotation_then_rvol_backup_second():
    a = run_validation_gates(_cand("AAAA"), _good_data(), CFG)                    # rot ~7.4%
    b = run_validation_gates(
        _cand("BBBB"), _good_data(pm_dollar_volume=150_000_000.0), CFG)           # rot ~14%
    c = run_validation_gates(
        _cand("CCCC"), _good_data(float_shares=None, market_cap=5e9,
                                  pm_iex_volume=200_000.0), CFG)                  # unmeasured rot, rvol 10×
    primary, backup = select_survivors([a, b, c])
    assert primary["symbol"] == "BBBB"      # measured rotation outranks
    assert backup["symbol"] == "AAAA"       # measured rotation beats unmeasured
    # zero survivors ⇒ (None, None) — gate 8's no_setup upstream
    dead = run_validation_gates(_cand("DDDD"), _good_data(pm_last=19.0), CFG)
    assert select_survivors([dead]) == (None, None)
