"""DayTrade Lab sizing (spec §4) — risk math, C-class half-risk, binding
constraint reporting, joint sleeve headroom, config bounds.

Run: PYTHONPATH=src pytest tests/test_daytrade_sizing.py
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from daytrade.config import DayTradeConfig  # noqa: E402
from daytrade.sizing import size_daytrade_entry  # noqa: E402

CFG = DayTradeConfig()   # risk 0.5% of a 25% sleeve, notional cap 6%


def test_risk_budget_math():
    # equity 100K ⇒ sleeve 25K ⇒ risk $125; stop distance $0.25 ⇒ 500 shares…
    # …but 500 × $20 = $10K > 6% cap ($6K) ⇒ notional cap binds at 300 shares.
    out = size_daytrade_entry(100_000, 20.0, 19.75, CFG)
    assert out["risk_usd"] == 125.0
    assert out["size_shares"] == 300
    assert out["binding"] == "notional_cap"


def test_risk_budget_binds_on_wide_stop():
    # stop distance $0.40 ⇒ risk shares 312 ⇒ notional $6,240 > $6K cap? No:
    # use a $10 price so notional stays low and risk binds.
    out = size_daytrade_entry(100_000, 10.0, 9.6, CFG)
    assert out["size_shares"] == 312 and out["binding"] == "risk_budget"


def test_half_risk_for_c_class():
    full = size_daytrade_entry(100_000, 10.0, 9.6, CFG)
    half = size_daytrade_entry(100_000, 10.0, 9.6, CFG, half_risk=True)
    assert half["risk_usd"] == full["risk_usd"] / 2
    assert half["size_shares"] == full["size_shares"] // 2


def test_joint_sleeve_headroom_binds():
    # Sleeve 25K; catalyst holds 20K, lab holds 3K ⇒ headroom 2K ⇒ 200 shares @$10.
    out = size_daytrade_entry(100_000, 10.0, 9.6, CFG,
                              catalyst_open_notional=20_000,
                              daytrade_open_notional=3_000)
    assert out["size_shares"] == 200 and out["binding"] == "joint_sleeve"


def test_exhausted_sleeve_sizes_zero():
    out = size_daytrade_entry(100_000, 10.0, 9.6, CFG,
                              catalyst_open_notional=25_000)
    assert out["size_shares"] == 0


def test_degenerate_inputs_size_zero():
    assert size_daytrade_entry(0, 10, 9.6, CFG)["size_shares"] == 0
    assert size_daytrade_entry(100_000, 10, 10.5, CFG)["size_shares"] == 0   # stop above entry


def test_config_bounds_enforced():
    with pytest.raises(ValueError):
        DayTradeConfig(risk_pct=5.0)
    with pytest.raises(ValueError):
        DayTradeConfig(notional_cap_pct=30.0)          # > sleeve cap
    with pytest.raises(ValueError):
        DayTradeConfig(scale_mode="thirds")
    with pytest.raises(ValueError):
        DayTradeConfig(consolidated_source="polygon")
    with pytest.raises(ValueError):
        DayTradeConfig(price_min=100.0, price_max=5.0)
    with pytest.raises(ValueError):
        DayTradeConfig(week_halt_r=3.0)                # wrong sign


def test_config_env_overrides(monkeypatch):
    from daytrade.config import load_daytrade_config
    monkeypatch.setenv("DAYTRADE_RISK_PCT", "0.25")
    monkeypatch.setenv("DAYTRADE_CONSOLIDATED_SOURCE", "fmp")
    monkeypatch.setenv("FLEX_SLEEVE_CAP_PCT", "20.0")
    cfg = load_daytrade_config()
    assert cfg.risk_pct == 0.25
    assert cfg.consolidated_source == "fmp"
    assert cfg.flex_sleeve_cap_pct == 20.0   # the ONE shared knob
