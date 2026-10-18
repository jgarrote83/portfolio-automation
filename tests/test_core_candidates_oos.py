"""Out-of-sample adapter (research/core_candidates/oos.py): date alignment, warm-up, the post-2004 refusal, parity with the parent SPY path,
and the scoped engine-constant override. Synthetic inputs only; no network."""
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from research.core_candidates import engine as E  # noqa: E402
from research.core_candidates import oos as O  # noqa: E402
from research.core_candidates import oos_data as OD  # noqa: E402

WIN = (date(1989, 1, 3), date(1989, 12, 29))


def frames(start="1988-01-04", end="1989-12-29", seed=1, credit_start=None):
    days = pd.bdate_range(start, end).drop(pd.Timestamp("1989-01-02"), errors="ignore")   # a market holiday, absent from the real index
    rng = np.random.default_rng(seed)
    px = 100 * np.cumprod(1 + rng.normal(0.0004, 0.01, len(days)))
    idx = pd.DataFrame({"date": days, "close": px, "adj_close": px})
    cdays = days if credit_start is None else pd.bdate_range(credit_start, end).drop(pd.Timestamp("1989-01-02"), errors="ignore")
    baa = pd.DataFrame({"date": cdays, "value": 2.0 + 0.3 * np.sin(np.arange(len(cdays)) / 15.0)})
    dtb3 = pd.DataFrame({"date": days, "value": 7.5})
    return idx, dtb3, baa


def test_adapter_puts_the_index_in_spys_slot_on_the_index_calendar():
    idx, dtb3, baa = frames()
    m = O.build_oos_market(idx, dtb3, baa, WIN)
    assert m.days == [d.date() for d in idx["date"]]
    assert np.array_equal(m.adj["SPY"], idx["adj_close"].to_numpy())
    assert np.isnan(m.adj["EFA"]).all() and np.isnan(m.adj["AGG"]).all()


def test_first_decision_is_1988_12_30_and_none_earlier():
    idx, dtb3, baa = frames()
    m = O.build_oos_market(idx, dtb3, baa, WIN)
    for r in ("C1", "C4", "C5"):
        dec = O.decisions(m, r, WIN)
        assert m.days[dec[0][0]] == date(1988, 12, 30) and all(m.days[d] >= date(1988, 12, 30) for d, _, _ in dec)
        assert m.days[dec[-1][0]] == date(1989, 11, 30)              # the December month-end would execute after the window


def test_incomplete_credit_lookback_refuses_rather_than_deciding():
    idx, dtb3, baa = frames(credit_start="1988-10-03")               # fewer than 126 credit days before 1988-12-30
    m = O.build_oos_market(idx, dtb3, baa, WIN)
    with pytest.raises(ValueError, match="credit history"):
        O.decisions(m, "C4", WIN)


def test_incomplete_trend_lookback_refuses():
    idx, dtb3, baa = frames(start="1988-06-01")                       # only 7 month-ends by 1988-12-30
    m = O.build_oos_market(idx, dtb3, baa, WIN)
    with pytest.raises(ValueError, match="month-ends"):
        O.decisions(m, "C1", WIN)


def test_refuses_anything_after_2004():
    with pytest.raises(OD.OutOfWindow):
        OD.refuse_after_2004(date(2005, 1, 3))
    idx, dtb3, baa = frames()
    with pytest.raises(OD.OutOfWindow):
        O.build_oos_market(idx, dtb3, baa, (date(1989, 1, 3), date(2005, 1, 3)))
    late = pd.concat([idx, pd.DataFrame({"date": [pd.Timestamp("2005-01-03")], "close": [1.0], "adj_close": [1.0]})])
    with pytest.raises(OD.OutOfWindow):
        O.build_oos_market(late, dtb3, baa, WIN)


def test_download_refuses_a_2005_row_and_writes_nothing(tmp_path):
    raw = pd.DataFrame({"Close": [1.0, 2.0], "Adj Close": [1.0, 2.0]},
                       index=pd.DatetimeIndex(["2004-12-31", "2005-01-03"]).tz_localize("America/New_York"))
    with pytest.raises(OD.OutOfWindow):
        OD.download_yahoo("^SP500TR", root=tmp_path, fetch=lambda *a: raw)
    assert not list(tmp_path.rglob("*.parquet"))


def test_parity_with_the_parent_spy_path():
    """On the same constructed series, the adapter path and the parent engine fed the series as SPY give identical decisions and equity."""
    idx, dtb3, baa = frames(seed=7)
    m_adapter = O.build_oos_market(idx, dtb3, baa, WIN)
    m_parent = E.build_market({"SPY": idx, "EFA": idx, "AGG": idx}, dtb3, baa, WIN)
    m_parent.adj["ACWX"] = np.full(len(m_parent.days), np.nan)
    for r in ("C1", "C4", "C5"):
        with O.oos_engine():
            parent_dec = E.decisions(m_parent, E.DEFAULTS[r], WIN)
            parent_sim = E.simulate(m_parent, E.DEFAULTS[r], WIN)
        adapter_dec = O.decisions(m_adapter, r, WIN)
        assert [(d, w) for d, w, _ in parent_dec] == [(d, w) for d, w, _ in adapter_dec]
        assert np.array_equal(parent_sim.equity, O.simulate(m_adapter, r, WIN).equity)


def test_override_is_scoped_and_restored_even_on_error():
    before = (E.FIRST_DECISION, E.SUB_PERIODS)
    with pytest.raises(RuntimeError):
        with O.oos_engine():
            assert E.FIRST_DECISION == date(1988, 12, 30) and E.SUB_PERIODS == O.OOS_SUB_PERIODS
            raise RuntimeError("boom")
    assert (E.FIRST_DECISION, E.SUB_PERIODS) == before == (date(2004, 12, 31), E.SUB_PERIODS)


def test_engine_rules_are_the_parent_defaults():
    for r in ("C1", "C4", "C5"):
        c = E.DEFAULTS[r]
        assert (c.cost_bps, c.leverage, c.sma_months, c.credit_window, c.daily_200) == (2.0, 2.0, 10, 126, False)


def test_tracking_reports_yearly_differences_and_correlation():
    days = pd.bdate_range("1993-01-29", "1995-12-29")
    rng = np.random.default_rng(3)
    a = 100 * np.cumprod(1 + rng.normal(0, 0.01, len(days)))
    b = a * np.exp(-0.0018 / 252 * np.arange(len(days)))                # SPY = index less an expense drag
    t = O.tracking(pd.DataFrame({"date": days, "adj_close": a}), pd.DataFrame({"date": days, "adj_close": b}))
    assert t["start"] == date(1993, 2, 1) and t["corr"] > 0.999 and -0.25 < t["mean_diff_pp"] < -0.1
