"""Empirical probe: dynamic watch_candidates filtering (Task A verification).

Run: PYTHONPATH=src python scripts/probe_dynamic_candidates.py
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

import collector.handler as ch  # noqa: E402

# --- Setup: temp static seed -------------------------------------------
tmp = tempfile.mkdtemp()
seed_path = pathlib.Path(tmp) / "flex-candidates.json"
static_tickers = ["ETN", "NEE", "XLU", "MU"]
seed_path.write_text(json.dumps({"candidates": static_tickers}))
ch._FLEX_CANDIDATES_FILE = seed_path  # type: ignore[attr-defined]

# --- Synthetic trades doc with a bad mix of watch_candidates -----------
trades_doc = {
    "date": "2026-07-21",
    "trades": [],
    "watch_candidates": [
        {"symbol": "SOXX",       "reason": "separation-set member (semis pool) — DROP"},
        {"symbol": "HELD_NAME",  "reason": "currently held — DROP"},
        {"reason": "no symbol key at all — DROP"},
        {"symbol": "GOOGL",      "reason": "non-reenterable legacy exit — DROP"},
        {"symbol": "INTC",       "reason": "FLEX_REENTERABLE + flat — KEEP"},
        {"symbol": "LNG",        "reason": "clean, new name — KEEP"},
    ],
}

# --- Patch read_trades to serve the synthetic doc -----------------------
ch.read_trades = lambda d: trades_doc if d == "2026-07-21" else None  # type: ignore[attr-defined]

# --- Run the loader ----------------------------------------------------
held = {"HELD_NAME"}
tickers, prov = ch._load_flex_candidates(exclude=held, today="2026-07-22")

static_out = [t for t in tickers if prov[t] == "static"]
dynamic_out = [t for t in tickers if prov[t] == "dynamic"]

print("=== Empirical probe — dynamic watch_candidates ===")
print(f"Total tickers : {len(tickers)}")
print(f"Static        : {static_out}")
print(f"Dynamic       : {dynamic_out}")
print()

# --- Assertions --------------------------------------------------------
assert "SOXX" not in tickers,       "SOXX must be dropped (core roster separation)"
assert "HELD_NAME" not in tickers,  "HELD_NAME must be dropped (currently held)"
assert "GOOGL" not in tickers,      "GOOGL must be dropped (non-reenterable legacy)"
assert "INTC" in tickers,           "INTC must survive (FLEX_REENTERABLE + flat)"
assert prov["INTC"] == "dynamic",   "INTC source must be 'dynamic'"
assert "LNG" in tickers,            "LNG must survive (clean new name)"
assert prov["LNG"] == "dynamic",    "LNG source must be 'dynamic'"
for s in static_tickers:
    assert s in tickers,            f"{s} (static) must be present"
    assert prov[s] == "static",     f"{s} source must be 'static'"

print("ALL ASSERTIONS PASSED")
print()
print("INTC and LNG survive; SOXX/HELD_NAME/GOOGL correctly dropped.")
print("Static tickers ETN/NEE/XLU/MU all present with source=static.")
