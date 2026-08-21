"""2026-08-21 SWA lean-visibility cycle, Task A —
web/api/function_app.py's `_attach_quadrant_accountability`.

Mirrors `_attach_sleeve_series`'s contract exactly (see
test_sleeve_performance_api.py): an absent/malformed `quadrant_performance`
block must leave the response UNCHANGED except for `quadrant_accountability`
itself degrading to `{"available": False}` — never an exception, never a
fabricated value.

Premise correction (verified empirically, not assumed): `web/api/*.py` has
ZERO references to `risk-limits.json` or `src/config` — it is a separate
deployment with no filesystem access to that config, so it cannot itself
determine the configured `trailing_window_sessions`. This is exactly why the
collector-side `suspect_path` field (see
tests/test_quadrant_regime_accountability.py) exists — the API only ever
echoes what the collector already determined.

Run: PYTHONPATH=src;web/api pytest tests/test_quadrant_accountability_api.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "web", "api"))

import function_app  # noqa: E402


def _bucket(trailing_n=20, **overrides) -> dict:
    row = {
        "ret_30d_pct": 1.2, "excess_30d_pp": 0.5,
        "favored_streak": 3, "streak_excess_pp": -0.4, "lagging_sessions": 1,
        f"trailing_excess_pp_{trailing_n}": -2.0,
        f"favored_sessions_{trailing_n}": 8,
        "cumulative_favored_excess_pp": 3.97,
        "suspect": True, "suspect_path": "rolling",
    }
    row.update(overrides)
    return row


def _snap(buckets: dict, available: bool = True, as_of: str = "2026-08-21") -> dict:
    return {"quadrant_performance": {"available": available, "as_of": as_of, "buckets": buckets}}


# --- graceful degradation -----------------------------------------------------

def test_absent_quadrant_performance_degrades():
    payload = {"series": [{"date": "2026-08-21"}]}
    original_series = list(payload["series"])
    function_app._attach_quadrant_accountability(payload, {})
    assert payload["series"] == original_series
    assert payload["quadrant_accountability"] == {"available": False}


def test_snapshot_none_degrades():
    payload = {}
    function_app._attach_quadrant_accountability(payload, None)
    assert payload["quadrant_accountability"]["available"] is False


def test_available_false_block_degrades():
    payload = {}
    snap = _snap({"Q3": _bucket()}, available=False)
    function_app._attach_quadrant_accountability(payload, snap)
    assert payload["quadrant_accountability"]["available"] is False


def test_buckets_not_a_dict_degrades():
    payload = {}
    snap = {"quadrant_performance": {"available": True, "buckets": "not-a-dict"}}
    function_app._attach_quadrant_accountability(payload, snap)
    assert payload["quadrant_accountability"]["available"] is False


def test_malformed_single_bucket_skipped_others_kept():
    payload = {}
    snap = _snap({"Q1": _bucket(), "Q2": "not-a-dict"})
    function_app._attach_quadrant_accountability(payload, snap)
    qa = payload["quadrant_accountability"]
    assert qa["available"] is True
    assert "Q1" in qa["buckets"]
    assert "Q2" not in qa["buckets"]


def test_download_exception_style_error_degrades(monkeypatch):
    """A snap that raises when accessed (e.g. a weird mock) must not crash
    the whole payload build."""
    class _Boom(dict):
        def get(self, *a, **k):
            raise RuntimeError("boom")
    payload = {}
    function_app._attach_quadrant_accountability(payload, _Boom())
    assert payload["quadrant_accountability"]["available"] is False


# --- population ---------------------------------------------------------------

def test_populates_all_five_per_bucket_fields():
    payload = {}
    snap = _snap({"Q1": _bucket(), "Q2": _bucket(), "Q3": _bucket(), "Q4": _bucket()})
    function_app._attach_quadrant_accountability(payload, snap)
    qa = payload["quadrant_accountability"]
    assert qa["available"] is True
    assert qa["as_of"] == "2026-08-21"
    for q in ("Q1", "Q2", "Q3", "Q4"):
        b = qa["buckets"][q]
        assert set(b) >= {
            "cumulative_favored_excess_pp", "trailing_excess_pp",
            "favored_sessions", "favored_streak", "suspect",
        }


def test_dynamic_trailing_window_key_discovery():
    """trailing_window_sessions=15 in the snapshot -> the API must discover
    trailing_excess_pp_15/favored_sessions_15 by prefix match, never a
    hardcoded 20."""
    payload = {}
    snap = _snap({"Q3": _bucket(trailing_n=15, **{
        "trailing_excess_pp_15": -1.5, "favored_sessions_15": 6,
    })})
    function_app._attach_quadrant_accountability(payload, snap)
    qa = payload["quadrant_accountability"]
    assert qa["available"] is True
    assert qa["trailing_window_sessions"] == 15
    assert qa["buckets"]["Q3"]["trailing_excess_pp"] == -1.5
    assert qa["buckets"]["Q3"]["favored_sessions"] == 6


def test_suspect_path_echoed_for_each_case():
    for path in ("streak", "rolling", "both", None):
        payload = {}
        snap = _snap({"Q3": _bucket(suspect_path=path, suspect=(path is not None))})
        function_app._attach_quadrant_accountability(payload, snap)
        assert payload["quadrant_accountability"]["buckets"]["Q3"]["suspect_path"] == path


# ---------------------------------------------------------------------------
# 2026-08-21 SWA lean-visibility cycle, Task B2 — `lean` passthrough in the
# `performance()` route's series comprehension. End-to-end (empirical
# verification doctrine: probe the API handler directly, not the diff).
# ---------------------------------------------------------------------------

class _FakeParams:
    def __init__(self, d):
        self._d = d or {}

    def get(self, key, default=None):
        return self._d.get(key, default)


class _FakeRequest:
    def __init__(self, params=None):
        self.params = _FakeParams(params)


def _call_performance(**params):
    import json as _json_mod
    resp = function_app.performance(_FakeRequest(params))
    return _json_mod.loads(resp.get_body())


def test_lean_passed_through_series(monkeypatch):
    cache = [
        {"date": "2026-08-19", "equity": 100_000.0, "spy_close": 620.0,
         "favored_bucket": ["Q4"],
         "lean": {"projected_quadrant": "Q2", "direction": "re_risk",
                   "staged_fraction": 0.1, "inert": False}},
        {"date": "2026-08-20", "equity": 100_100.0, "spy_close": 621.0,
         "favored_bucket": ["Q4"], "lean": None},
    ]
    monkeypatch.setattr(function_app, "_download_json", lambda c, n: (
        cache if n == "equity-series.json" else {}
    ))
    monkeypatch.setattr(function_app, "_latest_snapshot", lambda: (None, None))
    body = _call_performance(window="1Y")
    series = body["series"]
    assert series[0]["lean"]["projected_quadrant"] == "Q2"
    assert series[1]["lean"] is None


def test_lean_absent_from_point_degrades_to_none(monkeypatch):
    """A cache point predating this feature (no `lean` key at all) must
    still produce a response — never an exception, `lean: None` via `.get`,
    not a KeyError."""
    cache = [{"date": "2026-08-19", "equity": 100_000.0, "spy_close": 620.0}]
    monkeypatch.setattr(function_app, "_download_json", lambda c, n: (
        cache if n == "equity-series.json" else {}
    ))
    monkeypatch.setattr(function_app, "_latest_snapshot", lambda: (None, None))
    body = _call_performance(window="1Y")
    assert body["series"][0]["lean"] is None
