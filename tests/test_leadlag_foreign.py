"""The foreign-data side of the lead-lag study (`backtest.foreign`): normalising Yahoo history, the parquet cache, split
events, the 2026 refusals and the Stooq files placed by hand. Synthetic; no network (the fetch is injected)."""
import json
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from backtest import foreign as F  # noqa: E402
from backtest.data.bars import HoldoutError  # noqa: E402


def history(rows, tz="Asia/Tokyo", splits=None, divs=None):
    """A yfinance-shaped frame: tz-aware local-midnight index; columns Open, High, Low, Close, Volume, Dividends, Stock Splits."""
    idx = pd.DatetimeIndex([pd.Timestamp(r[0], tz=tz) for r in rows])
    return pd.DataFrame({"Open": [r[1] for r in rows], "High": [r[2] for r in rows], "Low": [r[2] for r in rows],
                         "Close": [r[2] for r in rows], "Adj Close": [r[2] * 0.5 for r in rows], "Volume": [1] * len(rows),
                         "Dividends": divs or [0.0] * len(rows), "Stock Splits": splits or [0.0] * len(rows)}, index=idx)


ROWS = [("2023-11-30", 90.0, 91.0), ("2023-12-01", 100.0, 101.0), ("2024-01-02", 101.0, 103.0), ("2025-12-30", 150.0, 151.0)]
NOW = lambda: datetime(2026, 10, 8, tzinfo=timezone.utc)                                      # noqa: E731


def test_the_session_date_is_the_local_calendar_date_not_the_utc_date():
    # Tokyo midnight is 15:00Z the day BEFORE: a UTC conversion would shift every row a day
    h = history([("2024-01-04", 1, 2)], tz="Asia/Tokyo")
    assert h.index[0].tz_convert("UTC").date() == date(2024, 1, 3)
    df, tz = F.normalise_history(h)
    assert tz == "Asia/Tokyo" and df["date"].iloc[0] == pd.Timestamp("2024-01-04")
    hk, tz2 = F.normalise_history(history([("2024-01-04", 1, 2)], tz="America/New_York"))
    assert hk["date"].iloc[0] == pd.Timestamp("2024-01-04") and tz2 == "America/New_York"


def test_normalise_keeps_open_and_close_only_unadjusted_sorts_and_dedupes():
    h = history([("2024-01-03", 5.0, 6.0), ("2024-01-02", 3.0, 4.0), ("2024-01-03", 5.5, 6.5)], splits=[0.0, 0.0, 2.0], divs=[0.0, 0.1, 0.0])
    df, _tz = F.normalise_history(h)
    assert list(df.columns) == F.CACHE_COLUMNS
    assert df["date"].dt.date.tolist() == [date(2024, 1, 2), date(2024, 1, 3)]
    assert df["open"].tolist() == [3.0, 5.5] and df["close"].tolist() == [4.0, 6.5]                  # the later duplicate wins; Adj Close unused
    assert df["split"].tolist() == [0.0, 2.0] and F.split_events(df) == [{"date": "2024-01-03", "ratio": 2.0}]
    empty, tz = F.normalise_history(pd.DataFrame())
    assert empty.empty and tz == "" and list(empty.columns) == F.CACHE_COLUMNS


def test_download_writes_the_cache_with_metadata_and_drops_rows_before_december_2023(tmp_path):
    seen = []

    def fetch(sym, start, end):
        seen.append((sym, start, end))
        return history(ROWS, splits=[0.0, 0.0, 0.0, 3.0])
    meta = F.download_member("^N225", root=tmp_path, fetch=fetch, now=NOW)
    assert seen == [("^N225", "2023-11-25", "2026-01-01")]                                          # end is exclusive: no 2026 bar requested
    assert meta["rows"] == 3 and meta["rows_downloaded"] == 4 and meta["first_date"] == "2023-12-01" and meta["last_date"] == "2025-12-30"
    assert meta["splits"] == [{"date": "2025-12-30", "ratio": 3.0}] and meta["tz"] == "Asia/Tokyo"
    assert meta["downloaded_utc"].startswith("2026-10-08") and meta["request"] == {"start": "2023-11-25", "end_exclusive": "2026-01-01"}
    df, m2 = F.load_member("^N225", tmp_path)
    assert len(df) == 3 and df["date"].iloc[0] == pd.Timestamp("2023-12-01") and m2 == json.loads((tmp_path / "yahoo" / "^N225.json").read_text())
    assert not list((tmp_path / "yahoo").glob("*.tmp-*"))                                             # no temp file left behind


def test_a_request_that_reaches_2026_or_a_row_dated_2026_is_refused(tmp_path):
    with pytest.raises(HoldoutError):
        F.download_member("^N225", root=tmp_path, fetch=lambda *a: history(ROWS), end="2026-01-02")
    with pytest.raises(HoldoutError):
        F.download_member("^N225", root=tmp_path, fetch=lambda *a: history(ROWS), start="2026-01-01", end="2026-01-01")
    leaky = history(ROWS + [("2026-01-02", 1.0, 2.0)])
    with pytest.raises(HoldoutError, match="refusing to keep 2026"):
        F.download_member("^N225", root=tmp_path, fetch=lambda *a: leaky)
    assert not (tmp_path / "yahoo" / "^N225.parquet").exists()                                         # nothing was written
    F.download_member("^N225", root=tmp_path, fetch=lambda *a: history(ROWS))
    p = tmp_path / "yahoo" / "^N225.parquet"
    bad = pd.read_parquet(p)
    bad.loc[len(bad)] = [pd.Timestamp("2026-02-02"), 1.0, 2.0, 0.0, 0.0]
    bad.to_parquet(p, index=False)
    with pytest.raises(HoldoutError, match="refusing to read it"):
        F.load_member("^N225", tmp_path)                                                              # and a cache with 2026 in it is never read


def test_an_empty_yahoo_response_fails_loudly_by_name_and_caches_nothing(tmp_path):
    import re
    for empty in (pd.DataFrame(), pd.DataFrame(columns=["Open", "Close"])):
        with pytest.raises(ValueError, match=re.escape("no rows for ^HSI")):
            F.download_member("^HSI", root=tmp_path, fetch=lambda *a, e=empty: e)
    assert not (tmp_path / "yahoo").exists() or not list((tmp_path / "yahoo").iterdir())


def test_ensure_cache_downloads_only_what_is_missing_unless_refreshed(tmp_path):
    calls = []

    def fetch(sym, start, end):
        calls.append(sym)
        return history(ROWS)
    F.ensure_cache(["A", "B"], root=tmp_path, fetch=fetch)
    assert calls == ["A", "B"]
    F.ensure_cache(["A", "B", "C"], root=tmp_path, fetch=fetch)
    assert calls == ["A", "B", "C"]
    F.ensure_cache(["A"], root=tmp_path, fetch=fetch, refresh=True)
    assert calls == ["A", "B", "C", "A"]
    with pytest.raises(FileNotFoundError, match="not cached"):
        F.load_member("ZZZ", tmp_path)


def test_member_rows_turns_the_cache_into_plain_indexed_tuples():
    df, tz = F.normalise_history(history(ROWS))
    m = F.MemberRows.from_frame("^N225", df, {"tz": tz, "splits": [{"date": "2024-01-02", "ratio": 2.0}]})
    assert m.dates[0] == date(2023, 11, 30) and m.open[1] == 100.0 and m.close[2] == 103.0 and m.tz == "Asia/Tokyo"
    assert m.splits == ((date(2024, 1, 2), 2.0),) and m.index()[date(2024, 1, 2)] == 2


def test_stooq_files_are_read_only_when_placed_by_hand_and_2026_rows_are_dropped(tmp_path):
    assert F.load_stooq("^N225", tmp_path) is None                                                    # nothing placed: unchecked, never fetched
    p = F.stooq_path("^N225", tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text("Date,Open,High,Low,Close,Volume\n2024-01-03,100,101,99,100.5,10\n2025-12-31,1,1,1,1,1\n2026-01-05,9,9,9,9,9\n", encoding="utf-8")
    df = F.load_stooq("^N225", tmp_path)
    assert df["date"].dt.date.tolist() == [date(2024, 1, 3), date(2025, 12, 31)] and df["open"].tolist() == [100.0, 1.0]
    p.write_text("Datum,Otwarcie\n2024-01-03,1\n", encoding="utf-8")
    assert F.load_stooq("^N225", tmp_path) is None                                                    # unrecognised columns: unchecked, not guessed
    p.write_text("Date,Open,Close\n2026-01-05,9,9\n", encoding="utf-8")
    assert F.load_stooq("^N225", tmp_path) is None                                                    # only 2026 rows: nothing usable


def test_this_module_has_no_http_client_so_it_can_never_contact_stooq():
    import ast
    import inspect
    imported = set()
    for node in ast.walk(ast.parse(inspect.getsource(F))):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0] if not node.level else "." + node.module)
    assert not imported & {"requests", "urllib", "http", "socket", "httpx", "aiohttp"}, imported
    assert F.assert_not_holdout(date(2025, 12, 31)) is None
    with pytest.raises(HoldoutError):
        F.assert_not_holdout(date(2026, 1, 1))
