"""C5 holdout smoke test: the one 2026 allowance (inside, after 2026-05-19, outside the context), the bounded parser, timing check."""
import os
import sys
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from research.core_candidates import data as D  # noqa: E402
from research.core_candidates import engine as E  # noqa: E402
from research.core_candidates import holdout as H  # noqa: E402
from research.core_candidates import holdout_run as R  # noqa: E402


def test_2026_is_refused_outside_the_allowance():
    with pytest.raises(E.HoldoutRefused):
        E.refuse_holdout(date(2026, 1, 2))
    with pytest.raises(Exception, match="2026"):
        D.assert_not_holdout(date(2026, 3, 1))


def test_the_allowance_covers_exactly_2026_01_01_to_05_19():
    with H.allow(date(2026, 5, 19)):
        E.refuse_holdout(date(2026, 1, 1), date(2026, 5, 19))       # allowed
        D.assert_not_holdout(date(2026, 2, 2))
        with pytest.raises(E.HoldoutRefused):
            E.refuse_holdout(date(2026, 5, 20))
        with pytest.raises(Exception):
            D.assert_not_holdout(date(2026, 6, 1))
    with pytest.raises(E.HoldoutRefused):                            # restored on exit
        E.refuse_holdout(date(2026, 1, 2))


def test_no_other_allowance_end_is_accepted():
    with pytest.raises(ValueError):
        with H.allow(date(2026, 6, 30)):
            pass
    assert not H.permits(date(2026, 1, 2))


def test_cache_writer_and_reader_honour_the_allowance(tmp_path):
    df = pd.DataFrame({"date": pd.to_datetime(["2026-05-19"]), "close": [1.0], "adj_close": [1.0], "dividends": [0.0], "split": [0.0]})
    with pytest.raises(Exception, match="2026"):
        D._write(df, {}, "yahoo", "X", tmp_path)
    with H.allow(date(2026, 5, 19)):
        D._write(df, {}, "yahoo", "X", tmp_path)
        assert len(D.read("yahoo", "X", tmp_path)[0]) == 1
    with pytest.raises(Exception, match="2026"):
        D.read("yahoo", "X", tmp_path)


def test_bounded_parser_refuses_anything_after_05_19():
    ok = R.parse_fred("observation_date,BAA10Y\n2026-05-18,1.9\n2026-05-19,\n", "BAA10Y")
    assert len(ok) == 2 and pd.isna(ok["value"].iloc[1])
    with pytest.raises(R.AfterHoldout):
        R.parse_fred("observation_date,BAA10Y\n2026-05-20,1.9\n", "BAA10Y")


def test_timing_check_flags_a_change_off_the_month_end_schedule():
    days = [d.date() for d in pd.bdate_range("2026-01-26", "2026-02-06")]
    m = E.Market(days, {}, None, None)
    ok = E.Result(E.DEFAULTS["C5"], days, None, ["TB"] * 5 + ["2X"] * 5, None, [], 0.0)       # changes on 2026-02-02 (after Jan 30)
    assert days[5] == date(2026, 2, 2) and R.smoke_timing(m, ok)["pass"]
    bad = E.Result(E.DEFAULTS["C5"], days, None, ["TB"] * 7 + ["2X"] * 3, None, [], 0.0)      # changes on 2026-02-04
    assert R.smoke_timing(m, bad)["off_schedule"] == [date(2026, 2, 4)]
