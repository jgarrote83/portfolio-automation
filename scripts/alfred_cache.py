"""ALFRED backtest harness (2026-08-21, `feat/20260821-alfred-backtest-harness`),
Task A — local vintage cache.

FOLLOWUPS #23's harness needs the FULL revision history of every macro series,
not just its current (fully-revised) value — `point_in_time.py`'s
reconstruction is meaningless without it. This script fetches that history
ONCE per series, spanning the whole backtest window, and persists it to a
gitignored local directory so replay is offline and repeatable (decision J-4:
local + gitignored, never a blob container — this is an offline research tool
and putting vintage data in production storage would invite the collector to
start depending on it).

Reuses the EXISTING `FREDClient.get_series_vintages` (`shared/clients/fred.py`)
— no second FRED client. One fetch per series (`limit=100000`, comfortably
above any series' total revision count over any realistic backtest window),
never one per (series, date) — respects FRED's rate limit by construction.

Series set: every non-comment key in `src/config/macro-series.json` (GDPNow
is already one of them — fetching it via `get_series_vintages` instead of the
production `get_series_latest` IS what makes its within-quarter revision
trajectory available; no separate "GDPNow vintage key" fetch is needed here).

Idempotent and resumable: a series already cached (present in the manifest
with a real row count) is skipped on a re-run unless `--force` is passed, and
each series is written to disk (and the manifest updated) immediately after
its own fetch completes — so a partial run (interrupted, rate-limited, etc.)
never corrupts what was already fetched, and a re-run picks up exactly where
it left off.

Run:
    $env:FRED_API_KEY = "<key>"
    python scripts/alfred_cache.py --start 1990-01-01 --end 2026-08-21

Or to refresh specific series only:
    python scripts/alfred_cache.py --start 1990-01-01 --end 2026-08-21 \\
        --series GDPNOW,CPILFESL --force
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from shared.clients.fred import FREDClient  # noqa: E402

CACHE_DIR = Path(__file__).resolve().parent / ".alfred_cache"
MANIFEST_PATH = CACHE_DIR / "manifest.json"
_MACRO_SERIES_FILE = _REPO / "src" / "config" / "macro-series.json"
_FETCH_LIMIT = 100_000   # FRED's documented per-request max; comfortably above
                          # any single series' total revision count.


def series_ids() -> list[str]:
    """Every fetchable series id in macro-series.json (comment keys, which
    all start with '_', excluded)."""
    with open(_MACRO_SERIES_FILE, encoding="utf-8") as f:
        meta = json.load(f)
    return [k for k in meta if not k.startswith("_")]


def _series_cache_path(series_id: str) -> Path:
    return CACHE_DIR / f"{series_id}.json"


def load_cached_series(series_id: str) -> list[dict] | None:
    """Raw ALFRED vintage rows for one series, or None if never cached."""
    p = _series_cache_path(series_id)
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_all_cached(series: list[str] | None = None) -> dict[str, list[dict]]:
    """Every cached series loaded into one dict -- the ``cache`` shape
    `point_in_time.macro_data_as_of` expects. A series with no cache file is
    simply absent (never a fabricated empty list)."""
    ids = series if series is not None else series_ids()
    out: dict[str, list[dict]] = {}
    for sid in ids:
        rows = load_cached_series(sid)
        if rows is not None:
            out[sid] = rows
    return out


def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {"series": {}}
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_manifest(manifest: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)


def fetch_and_cache_series(
    fred: FREDClient, series_id: str, realtime_start: str, realtime_end: str,
) -> dict:
    """Fetch one series' full vintage history and write it to disk. Returns
    the manifest entry (row count + earliest/latest realtime_start actually
    returned -- Task D §6.2's coverage-honesty check depends on knowing this
    per series, GDPNOW especially)."""
    rows = fred.get_series_vintages(
        series_id, realtime_start=realtime_start, realtime_end=realtime_end,
        limit=_FETCH_LIMIT,
    )
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_series_cache_path(series_id), "w", encoding="utf-8") as f:
        json.dump(rows, f)
    realtime_starts = [r["realtime_start"] for r in rows if r.get("realtime_start")]
    return {
        "row_count": len(rows),
        "earliest_realtime_start": min(realtime_starts) if realtime_starts else None,
        "latest_realtime_start": max(realtime_starts) if realtime_starts else None,
        "fetch_window": {"realtime_start": realtime_start, "realtime_end": realtime_end},
        "fetched_at": datetime.utcnow().isoformat() + "Z",
    }


def build_cache(
    fred: FREDClient,
    start: str,
    end: str,
    series: list[str] | None = None,
    force: bool = False,
    sleep_s: float = 0.5,
) -> dict:
    """Idempotent cache build: fetches every requested series not already
    cached (or every requested series, with ``force``), updating the manifest
    after EACH series so an interruption never loses already-fetched work."""
    ids = series if series is not None else series_ids()
    manifest = load_manifest()
    manifest.setdefault("series", {})
    for sid in ids:
        already = manifest["series"].get(sid, {}).get("row_count", 0) > 0
        if already and not force:
            print(f"  {sid}: already cached ({manifest['series'][sid]['row_count']} rows) -- skipping")
            continue
        print(f"  {sid}: fetching {start}..{end} ...", end=" ", flush=True)
        entry = fetch_and_cache_series(fred, sid, start, end)
        manifest["series"][sid] = entry
        _save_manifest(manifest)   # persist after EVERY series -- resumability
        print(f"{entry['row_count']} rows "
              f"(earliest realtime_start={entry['earliest_realtime_start']})")
        if not already or force:
            time.sleep(sleep_s)
    manifest["window"] = {"start": start, "end": end}
    _save_manifest(manifest)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True, help="realtime_start window floor, ISO date")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--series", help="comma-separated series ids (default: all of macro-series.json)")
    ap.add_argument("--force", action="store_true", help="refetch even if already cached")
    ap.add_argument("--sleep", type=float, default=0.5, help="seconds between fetches")
    args = ap.parse_args()

    key = os.environ.get("FRED_API_KEY")
    if not key:
        print("ERROR: set $env:FRED_API_KEY before running")
        return 2

    ids = args.series.split(",") if args.series else None
    fred = FREDClient(key)
    print(f"Building ALFRED cache in {CACHE_DIR} for window {args.start}..{args.end}")
    manifest = build_cache(fred, args.start, args.end, series=ids, force=args.force, sleep_s=args.sleep)

    print(f"\n{len(manifest['series'])} series cached. Manifest: {MANIFEST_PATH}")
    no_data = [sid for sid, m in manifest["series"].items() if m.get("row_count", 0) == 0]
    if no_data:
        print(f"WARNING: zero rows returned for: {no_data} (check series id / API key / window)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
