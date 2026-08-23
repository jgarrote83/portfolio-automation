"""ALFRED backtest harness (2026-08-21), Task A — `scripts/alfred_cache.py`.

Never hits the real FRED API (a `_FakeFred` stub records calls instead) --
these tests are about the cache's idempotency/resumability contract, not
network behavior. Real fetches are the account holder's `--start`/`--end` run
per the script's own docstring.

Run: PYTHONPATH=src python -m pytest tests/test_alfred_cache.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import alfred_cache  # noqa: E402


class _FakeFred:
    def __init__(self, rows_by_series: dict[str, list[dict]]):
        self._rows = rows_by_series
        self.calls: list[tuple[str, str, str]] = []

    def get_series_vintages(self, series_id, realtime_start, realtime_end, limit=100000):
        self.calls.append((series_id, realtime_start, realtime_end))
        return self._rows.get(series_id, [])


def _row(d, rt, v):
    return {"date": d, "realtime_start": rt, "realtime_end": "9999-12-31", "value": v}


def _isolate_cache_dir(monkeypatch, tmp_path):
    cache_dir = tmp_path / "alfred_cache"
    monkeypatch.setattr(alfred_cache, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(alfred_cache, "MANIFEST_PATH", cache_dir / "manifest.json")
    return cache_dir


def test_series_ids_excludes_comment_keys():
    ids = alfred_cache.series_ids()
    assert "GDPNOW" in ids
    assert "CPILFESL" in ids
    assert not any(sid.startswith("_") for sid in ids)


def test_build_cache_writes_one_file_per_series_and_manifest(monkeypatch, tmp_path):
    cache_dir = _isolate_cache_dir(monkeypatch, tmp_path)
    fred = _FakeFred({
        "GDPNOW": [_row("2026-04-01", "2026-04-05", "3.70")],
        "CPILFESL": [_row("2026-06-01", "2026-07-01", "310.5")],
    })
    manifest = alfred_cache.build_cache(fred, "1990-01-01", "2026-08-21", series=["GDPNOW", "CPILFESL"], sleep_s=0)

    assert (cache_dir / "GDPNOW.json").exists()
    assert (cache_dir / "CPILFESL.json").exists()
    with open(cache_dir / "GDPNOW.json") as f:
        assert json.load(f) == [_row("2026-04-01", "2026-04-05", "3.70")]

    assert manifest["series"]["GDPNOW"]["row_count"] == 1
    assert manifest["series"]["GDPNOW"]["earliest_realtime_start"] == "2026-04-05"
    assert manifest["series"]["GDPNOW"]["latest_realtime_start"] == "2026-04-05"
    assert manifest["series"]["CPILFESL"]["row_count"] == 1
    assert manifest["window"] == {"start": "1990-01-01", "end": "2026-08-21"}
    assert len(fred.calls) == 2


def test_build_cache_is_idempotent_skips_already_cached(monkeypatch, tmp_path):
    _isolate_cache_dir(monkeypatch, tmp_path)
    fred = _FakeFred({"GDPNOW": [_row("2026-04-01", "2026-04-05", "3.70")]})
    alfred_cache.build_cache(fred, "1990-01-01", "2026-08-21", series=["GDPNOW"], sleep_s=0)
    assert len(fred.calls) == 1

    # Re-run: same series, no --force -> must NOT call FRED again.
    alfred_cache.build_cache(fred, "1990-01-01", "2026-08-21", series=["GDPNOW"], sleep_s=0)
    assert len(fred.calls) == 1


def test_build_cache_force_refetches(monkeypatch, tmp_path):
    _isolate_cache_dir(monkeypatch, tmp_path)
    fred = _FakeFred({"GDPNOW": [_row("2026-04-01", "2026-04-05", "3.70")]})
    alfred_cache.build_cache(fred, "1990-01-01", "2026-08-21", series=["GDPNOW"], sleep_s=0)
    alfred_cache.build_cache(fred, "1990-01-01", "2026-08-21", series=["GDPNOW"], force=True, sleep_s=0)
    assert len(fred.calls) == 2


def test_build_cache_resumes_partial_run(monkeypatch, tmp_path):
    """A prior run cached GDPNOW but was interrupted before CPILFESL -- a
    fresh call with both series must fetch ONLY the missing one."""
    _isolate_cache_dir(monkeypatch, tmp_path)
    fred = _FakeFred({
        "GDPNOW": [_row("2026-04-01", "2026-04-05", "3.70")],
        "CPILFESL": [_row("2026-06-01", "2026-07-01", "310.5")],
    })
    alfred_cache.build_cache(fred, "1990-01-01", "2026-08-21", series=["GDPNOW"], sleep_s=0)
    assert len(fred.calls) == 1

    alfred_cache.build_cache(fred, "1990-01-01", "2026-08-21", series=["GDPNOW", "CPILFESL"], sleep_s=0)
    assert [c[0] for c in fred.calls] == ["GDPNOW", "CPILFESL"]


def test_build_cache_zero_rows_still_recorded_and_refetchable(monkeypatch, tmp_path):
    """A series that returns zero rows (e.g. wrong id, or genuinely no data
    for that window) must not be silently treated as 'cached' forever -- the
    next run should retry it, since row_count=0 never counts as 'already'."""
    _isolate_cache_dir(monkeypatch, tmp_path)
    fred = _FakeFred({})   # everything returns []
    alfred_cache.build_cache(fred, "1990-01-01", "2026-08-21", series=["NOSUCHSERIES"], sleep_s=0)
    assert len(fred.calls) == 1
    alfred_cache.build_cache(fred, "1990-01-01", "2026-08-21", series=["NOSUCHSERIES"], sleep_s=0)
    assert len(fred.calls) == 2   # retried, not skipped


def test_load_all_cached_omits_uncached_series(monkeypatch, tmp_path):
    _isolate_cache_dir(monkeypatch, tmp_path)
    fred = _FakeFred({"GDPNOW": [_row("2026-04-01", "2026-04-05", "3.70")]})
    alfred_cache.build_cache(fred, "1990-01-01", "2026-08-21", series=["GDPNOW"], sleep_s=0)

    loaded = alfred_cache.load_all_cached(series=["GDPNOW", "NEVER_CACHED"])
    assert list(loaded.keys()) == ["GDPNOW"]
    assert loaded["GDPNOW"] == [_row("2026-04-01", "2026-04-05", "3.70")]


def test_load_cached_series_none_when_absent(monkeypatch, tmp_path):
    _isolate_cache_dir(monkeypatch, tmp_path)
    assert alfred_cache.load_cached_series("GDPNOW") is None


def test_manifest_persists_after_each_series_not_only_at_the_end(monkeypatch, tmp_path):
    """Resumability requires the manifest to reflect a series the moment it's
    fetched -- not batched to the end, where an interruption mid-run would
    lose everything already done."""
    cache_dir = _isolate_cache_dir(monkeypatch, tmp_path)

    calls = []

    class _CrashingFred(_FakeFred):
        def get_series_vintages(self, series_id, realtime_start, realtime_end, limit=100000):
            calls.append(series_id)
            if series_id == "SECOND":
                raise RuntimeError("simulated interruption")
            return super().get_series_vintages(series_id, realtime_start, realtime_end, limit)

    fred = _CrashingFred({"FIRST": [_row("2026-04-01", "2026-04-05", "1.0")]})
    try:
        alfred_cache.build_cache(fred, "1990-01-01", "2026-08-21", series=["FIRST", "SECOND"], sleep_s=0)
    except RuntimeError:
        pass

    assert (cache_dir / "FIRST.json").exists()
    with open(cache_dir / "manifest.json") as f:
        manifest = json.load(f)
    assert "FIRST" in manifest["series"]
    assert "SECOND" not in manifest["series"]
