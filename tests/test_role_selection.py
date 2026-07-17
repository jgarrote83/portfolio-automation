"""Session 2026-07-17, Task C — `role_selection` snapshot block.

`sleeve_selection` (Task E) only ranks roles with `selection: "scorecard"` — the
`intl_leader` role (`selection: "rotation"`) never appears there at all, so nothing
in the snapshot told the model that `intl_leader`'s static `selected` member keeps
its floor regardless of the RUNTIME `intl_governance.leader_pick` going null. The
2026-07-17 incident: `leader_pick` de-rotated to null and the model proposed
selling AIA's 1-share floor as if that were a deselection (Tier-1 correctly
rejected it via `_non_selected_pool_member`'s existing floor-bypass design — this
task is prompt/snapshot visibility only, no validator change).

Run: PYTHONPATH=src pytest tests/test_role_selection.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_role_selection  # noqa: E402
from shared.quadrants import roles_config  # noqa: E402

_ROLES = roles_config()


def test_covers_every_role_including_rotation_ones():
    out = _build_role_selection(_ROLES, intl_leader_pick=None)
    role_ids = {r["role_id"] for r in out["roles"]}
    assert role_ids == {r["role_id"] for r in _ROLES}
    assert "intl_leader" in role_ids


def test_intl_leader_carries_leader_pick_and_note_even_when_null():
    out = _build_role_selection(_ROLES, intl_leader_pick=None)
    intl_leader = next(r for r in out["roles"] if r["role_id"] == "intl_leader")
    assert intl_leader["leader_pick"] is None
    assert "selected" in intl_leader
    assert intl_leader["selected"]   # static selected member is never null
    assert "note" in intl_leader
    assert "never" in intl_leader["note"].lower() or "only" in intl_leader["note"].lower()


def test_intl_leader_echoes_current_leader_pick_when_present():
    out = _build_role_selection(_ROLES, intl_leader_pick="AIA")
    intl_leader = next(r for r in out["roles"] if r["role_id"] == "intl_leader")
    assert intl_leader["leader_pick"] == "AIA"


def test_non_intl_leader_roles_have_no_leader_pick_field():
    out = _build_role_selection(_ROLES, intl_leader_pick="AIA")
    for r in out["roles"]:
        if r["role_id"] != "intl_leader":
            assert "leader_pick" not in r


def test_selected_matches_config_uppercased():
    out = _build_role_selection(_ROLES, intl_leader_pick=None)
    by_id = {r["role_id"]: r for r in out["roles"]}
    for role in _ROLES:
        assert by_id[role["role_id"]]["selected"] == (role.get("selected") or "").upper()
