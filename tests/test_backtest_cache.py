"""Phase 1 -- parquet store (atomic writes) and the SQLite manifest."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from backtest.data.manifest import FULL, Manifest, window_key  # noqa: E402
from backtest.data.store import BarStore, empty_frame, symbol_dir  # noqa: E402


def _bars(*stamps, volume=100):
    return pd.DataFrame({"ts": pd.to_datetime(list(stamps), utc=True), "open": 1.0, "high": 2.0,
                         "low": 0.5, "close": 1.5, "volume": volume})


def test_write_then_read_roundtrips_in_us_eastern(tmp_path):
    store = BarStore(tmp_path)
    store.write("sip", "5Min", "SPY", "2024-03", _bars("2024-03-08T14:30:00Z", "2024-03-11T13:30:00Z"))
    assert store.path("sip", "5Min", "SPY", "2024-03").is_file()
    df = store.read("sip", "5Min", ["SPY"], ["2024-03"])
    assert list(df.columns) == ["symbol", "ts", "open", "high", "low", "close", "volume"]
    assert str(df["ts"].dt.tz) == "US/Eastern"
    assert [(t.hour, t.minute) for t in df["ts"]] == [(9, 30), (9, 30)]      # DST-correct both sides
    assert [t.utcoffset().total_seconds() / 3600 for t in df["ts"]] == [-5.0, -4.0]


def test_rewrite_merges_dedupes_on_ts_and_keeps_the_latest(tmp_path):
    store = BarStore(tmp_path)
    store.write("sip", "1Min", "AAA", "2024-01", _bars("2024-01-02T14:30:00Z", volume=1))
    n, _ = store.write("sip", "1Min", "AAA", "2024-01", _bars("2024-01-02T14:30:00Z",
                                                            "2024-01-02T14:31:00Z", volume=9))
    df = store.read("sip", "1Min", ["AAA"], ["2024-01"])
    assert n == 2 and list(df["volume"]) == [9, 9]


def test_interrupted_write_leaves_no_partial_file_and_keeps_the_old_one(tmp_path, monkeypatch):
    store = BarStore(tmp_path)
    store.write("sip", "1Min", "AAA", "2024-01", _bars("2024-01-02T14:30:00Z", volume=7))
    path = store.path("sip", "1Min", "AAA", "2024-01")
    before = path.read_bytes()

    real = pd.DataFrame.to_parquet

    def dying(self, target, *a, **k):                # writes a truncated file, then the process "dies"
        with open(target, "wb") as fh:
            fh.write(b"PAR1-truncated")
        raise OSError("disk went away")
    monkeypatch.setattr(pd.DataFrame, "to_parquet", dying)
    with pytest.raises(OSError):
        store.write("sip", "1Min", "AAA", "2024-01", _bars("2024-01-03T14:30:00Z"))
    with pytest.raises(OSError):
        store.write("sip", "1Min", "BBB", "2024-01", _bars("2024-01-03T14:30:00Z"))
    monkeypatch.setattr(pd.DataFrame, "to_parquet", real)

    assert path.read_bytes() == before                                   # old file intact
    assert not store.path("sip", "1Min", "BBB", "2024-01").exists()       # no half-written file
    leftovers = [p for p in tmp_path.rglob("*") if p.is_file() and ".tmp-" in p.name]
    assert leftovers == []
    assert len(store.read("sip", "1Min", ["AAA"], ["2024-01"])) == 1


def test_read_of_nothing_is_an_empty_frame_with_the_public_schema(tmp_path):
    df = BarStore(tmp_path).read("sip", "1Day", ["ZZZ"], ["2024-01"])
    assert df.empty and list(df.columns) == list(empty_frame().columns)


def test_symbol_dirs_are_filesystem_safe():
    assert symbol_dir("aapl") == "AAPL" and symbol_dir("BRK.B") == "BRK.B"
    assert symbol_dir("CON") == "CON_" and symbol_dir("nul") == "NUL_" and symbol_dir("COM1") == "COM1_"
    assert "/" not in symbol_dir("A/B")


def test_manifest_records_days_including_empty_ones_and_reports_what_is_missing():
    m = Manifest(":memory:")
    d = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    m.record("sip", "1Day", "raw", FULL, [("AAA", d[0], True), ("AAA", d[1], False)])
    assert m.missing("sip", "1Day", "raw", FULL, ["AAA", "BBB"], d) == {
        "AAA": [d[2]], "BBB": d}                                  # d[1] counts as fetched (it was empty)
    fetched, bars = m.coverage_row("sip", "1Day", "raw", FULL, "AAA", "2024-01")
    assert fetched == 0b011 << 1 and bars == 0b001 << 1
    m.record("sip", "1Day", "raw", FULL, [("AAA", d[2], True)])
    assert "AAA" not in m.missing("sip", "1Day", "raw", FULL, ["AAA"], d)


def test_manifest_key_includes_feed_timeframe_adjustment_and_window():
    m = Manifest(":memory:")
    day = [date(2024, 1, 2)]
    w = window_key(("09:30", "09:35"))
    m.record("sip", "5Min", "raw", w, [("AAA", day[0], True)])
    assert m.missing("sip", "5Min", "raw", w, ["AAA"], day) == {}
    assert m.missing("sip", "5Min", "raw", FULL, ["AAA"], day) == {"AAA": day}     # window != full day
    assert m.missing("iex", "5Min", "raw", w, ["AAA"], day) == {"AAA": day}        # feed in the key
    assert m.missing("sip", "1Min", "raw", w, ["AAA"], day) == {"AAA": day}        # timeframe in the key
    assert m.missing("sip", "5Min", "split", w, ["AAA"], day) == {"AAA": day}      # adjustment in the key
    m.record("sip", "5Min", "raw", FULL, [("BBB", day[0], True)])
    assert m.missing("sip", "5Min", "raw", w, ["BBB"], day) == {}                  # full covers a window


def test_manifest_stats_and_persistence(tmp_path):
    p = tmp_path / "m.sqlite"
    m = Manifest(p)
    m.record("sip", "1Day", "raw", FULL, [("AAA", date(2024, 1, 2), True)])
    m.record_files([("sip", "1Day", "AAA", "2024-01", 5, 1234)])
    m.close()
    m2 = Manifest(p)
    assert m2.stats() == {"parquet_files": 1, "rows": 5, "bytes": 1234, "coverage_rows": 1}
    assert m2.missing("sip", "1Day", "raw", FULL, ["AAA"], [date(2024, 1, 2)]) == {}
