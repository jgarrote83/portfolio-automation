"""2026-08-14 fix — `flex-state/{date}.json` stale-read incident.

`_persist` overwrote `flex-state/{date}.json` on EVERY ~15-min tick, all 24
hours. Every trading day's LAST tick is unavoidably a post-close
"market_closed" tick (the clock-gate early return, before entry/exit
evaluation ever runs that tick) — so the blob's final state for ANY day was
always the empty closed-tick stub, regardless of which day's file a reader
looked at. Confirmed empirically: two real nominations (AVGO/ENTG) were
evaluated 26 times each on 2026-08-11 per the `flex-decisions` JSONL log, yet
every downstream snapshot showed `flex_state.entries: []` as if nothing had
ever been evaluated — the collector's walkback found today's own empty
pre-market stub before it ever reached back far enough to see real activity.

Run: PYTHONPATH=src pytest tests/test_flex_state_persistence.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import flex.handler as fh  # noqa: E402


def _ledger():
    return {"AVGO": {"qty_current": 10}}


def test_closed_tick_with_no_existing_file_skips_the_write(monkeypatch):
    """A pre-market closed tick before this trading day has had ANY real
    activity must not create an empty stub -- the file should stay absent so
    a reader's walkback falls through to the prior day's real data."""
    written = []
    monkeypatch.setattr(fh, "read_json_blob", lambda container, name: None)
    monkeypatch.setattr(fh, "write_json_blob", lambda container, name, obj: written.append((container, name, obj)))
    monkeypatch.setattr(fh, "append_jsonl_blob", lambda *a, **k: None)

    decisions = {"entries": [], "exits": [], "reconcile": {}}
    fh._persist("2026-08-12", decisions, quadrant="", ledger=_ledger(),
                executions=[], quadrant_basis="market_closed")

    flex_state_writes = [w for w in written if w[0] == "flex-state"]
    assert flex_state_writes == []


def test_closed_tick_with_existing_file_carries_entries_forward(monkeypatch):
    """A closed tick LATER in the day (or overnight) must preserve the last
    real in-hours tick's entries/exits/quadrant/quadrant_basis, not overwrite
    them with the current (empty) closed-tick values."""
    real_entries = [{"symbol": "AVGO", "entry_trigger": "fail", "skip_reason": "vwap_not_rising"}]
    existing_blob = {
        "as_of": "2026-08-11", "quadrant": "Q1", "quadrant_basis": "active",
        "reconcile": {"repairs": []}, "exits": [], "entries": real_entries,
        "held": [],
    }
    written = []
    monkeypatch.setattr(fh, "read_json_blob", lambda container, name: existing_blob if container == "flex-state" else None)
    monkeypatch.setattr(fh, "write_json_blob", lambda container, name, obj: written.append((container, name, obj)))
    monkeypatch.setattr(fh, "append_jsonl_blob", lambda *a, **k: None)

    decisions = {"entries": [], "exits": [], "reconcile": {"repairs": ["x"]}}
    fh._persist("2026-08-11", decisions, quadrant="", ledger=_ledger(),
                executions=[], quadrant_basis="market_closed")

    flex_state_writes = [w for w in written if w[0] == "flex-state"]
    assert len(flex_state_writes) == 1
    _, _, obj = flex_state_writes[0]
    assert obj["entries"] == real_entries          # carried forward, not erased
    assert obj["quadrant"] == "Q1"                 # carried forward
    assert obj["quadrant_basis"] == "active"        # carried forward
    assert obj["reconcile"] == {"repairs": ["x"]}   # administrative field DOES refresh
    assert obj["held"] == sorted(_ledger().keys())  # administrative field DOES refresh


def test_in_hours_tick_always_writes_its_own_real_evaluation(monkeypatch):
    """An in-hours tick (quadrant_basis != 'market_closed') is unchanged --
    it always writes its own real evaluation, never reads back or merges."""
    read_calls = []
    written = []
    monkeypatch.setattr(fh, "read_json_blob", lambda container, name: read_calls.append((container, name)) or None)
    monkeypatch.setattr(fh, "write_json_blob", lambda container, name, obj: written.append((container, name, obj)))
    monkeypatch.setattr(fh, "append_jsonl_blob", lambda *a, **k: None)

    new_entries = [{"symbol": "AVGO", "entry_trigger": "pass"}]
    decisions = {"entries": new_entries, "exits": [], "reconcile": {}}
    fh._persist("2026-08-11", decisions, quadrant="Q1", ledger=_ledger(),
                executions=[], quadrant_basis="active")

    assert read_calls == []   # never reads back on an in-hours tick
    flex_state_writes = [w for w in written if w[0] == "flex-state"]
    assert len(flex_state_writes) == 1
    _, _, obj = flex_state_writes[0]
    assert obj["entries"] == new_entries
    assert obj["quadrant"] == "Q1"
    assert obj["quadrant_basis"] == "active"


def test_full_day_tick_sequence_ends_with_real_activity_intact(monkeypatch):
    """Integration-shaped: simulate a realistic tick sequence for one trading
    day (pre-market closed -> in-hours real evaluation -> post-close closed
    ticks) using a fake in-memory blob store, and confirm the file's FINAL
    state at end of day still shows the real entries -- not the empty
    post-close stub that motivated this fix."""
    store: dict[tuple[str, str], dict] = {}

    def _read(container, name):
        return store.get((container, name))

    def _write(container, name, obj):
        store[(container, name)] = obj

    monkeypatch.setattr(fh, "read_json_blob", _read)
    monkeypatch.setattr(fh, "write_json_blob", _write)
    monkeypatch.setattr(fh, "append_jsonl_blob", lambda *a, **k: None)

    today = "2026-08-11"
    ledger = _ledger()

    # Pre-market closed ticks (00:00 - 09:15 ET) -- file starts absent.
    for _ in range(3):
        fh._persist(today, {"entries": [], "exits": [], "reconcile": {}},
                    quadrant="", ledger=ledger, executions=[], quadrant_basis="market_closed")
    assert (("flex-state", f"{today}.json")) not in store  # still absent

    # In-hours real evaluation tick (e.g. 10:00 ET) -- AVGO declines on VWAP.
    real_entries = [{"symbol": "AVGO", "entry_trigger": "fail", "skip_reason": "vwap_not_rising"}]
    fh._persist(today, {"entries": real_entries, "exits": [], "reconcile": {}},
                quadrant="Q1", ledger=ledger, executions=[], quadrant_basis="active")

    # Post-close closed ticks (16:00 ET onward, including the final tick of the day).
    for _ in range(5):
        fh._persist(today, {"entries": [], "exits": [], "reconcile": {}},
                    quadrant="", ledger=ledger, executions=[], quadrant_basis="market_closed")

    final = store[("flex-state", f"{today}.json")]
    assert final["entries"] == real_entries
    assert final["quadrant"] == "Q1"
    assert final["quadrant_basis"] == "active"
