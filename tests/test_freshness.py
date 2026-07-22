"""B4 (2026-07-21) — deterministic freshness + growth_axis.as_of (vintage recency).

The freshness table's dates flip-flopped because the model chose observation-date vs
vintage-date differently each run (GDPNow "3d" one day, "81d" the next for the SAME
value). growth_axis now emits `as_of` = the newest USED vintage row's realtime `asof`,
and the `freshness` block dates every series deterministically with a cadence-aware
staleness threshold. Run: PYTHONPATH=src pytest tests/test_freshness.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_freshness, _build_growth_axis  # noqa: E402


def test_growth_axis_as_of_is_used_vintage_asof_prior_tail():
    prior = [{"date": "2026-04-01", "asof": f"2026-06-{10 + k:02d}", "value": 2.0 + 0.1 * k}
             for k in range(6)]
    ax = _build_growth_axis({"GDPNOW_VINTAGES": [], "GDPNOW_VINTAGES_PRIOR": prior})
    assert ax["basis"] == "prior_quarter_tail"
    assert ax["as_of"] == "2026-06-15"   # last used vintage row's realtime asof


def test_growth_axis_as_of_within_quarter():
    traj = [{"date": "2026-07-01", "asof": f"2026-07-{10 + k:02d}", "value": 2.0 + 0.1 * k}
            for k in range(4)]
    ax = _build_growth_axis({"GDPNOW_VINTAGES": traj})
    assert ax["basis"] == "within_quarter_vintages"
    assert ax["as_of"] == "2026-07-13"


def test_growth_axis_as_of_none_when_no_gdpnow():
    ax = _build_growth_axis({"GDPNOW_VINTAGES": [], "GDPNOW_VINTAGES_PRIOR": []})
    assert ax["direction"] == "indeterminate"
    assert ax["as_of"] is None


def test_freshness_monthly_vs_daily_thresholds():
    macro = {
        "CPILFESL": [{"date": "2026-06-01", "value": "320.0"}],  # 50d — monthly (45d) → stale
        "DGS2": [{"date": "2026-07-20", "value": "4.1"}],         # 1d — daily → fresh
        "DTWEXBGS": [{"date": "2026-07-10", "value": "120"}],     # 11d — daily (5d) → stale
    }
    growth_axis = {"gdpnow_latest": 2.1, "as_of": "2026-07-18"}
    fr = _build_freshness(macro, growth_axis, "2026-07-21")
    s = fr["series"]

    assert s["CPILFESL"]["convention"] == "observation_date"
    assert s["CPILFESL"]["days_stale"] == 50
    assert s["CPILFESL"]["stale"] is True

    assert s["DGS2"]["days_stale"] == 1 and s["DGS2"]["stale"] is False
    assert s["DTWEXBGS"]["days_stale"] == 11 and s["DTWEXBGS"]["stale"] is True


def test_freshness_gdpnow_uses_vintage_recency():
    fr = _build_freshness({}, {"gdpnow_latest": 2.1, "as_of": "2026-07-18"}, "2026-07-21")
    g = fr["series"]["GDPNOW"]
    assert g["convention"] == "vintage_date"
    assert g["as_of"] == "2026-07-18"
    assert g["days_stale"] == 3 and g["stale"] is False   # 3 < 7-day threshold
    assert g["value"] == 2.1
