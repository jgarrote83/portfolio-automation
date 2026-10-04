"""ORB Phase 0A -- `scripts/orb_phase0_flat_check.py` pure classification (no network)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from orb_phase0_flat_check import classify_flatness  # noqa: E402


def _row(sym, order_ids=()):
    return {"symbol": sym, "qty_current": 10, "entry_date": "2026-10-01",
            "order_ids": list(order_ids)}


def test_everything_empty_is_flat():
    assert classify_flatness([], [], {"flex": {}, "daytrade": {}})["flat"] is True


def test_core_position_and_core_order_do_not_count():
    r = classify_flatness(
        [{"symbol": "SPY", "qty": "50"}],
        [{"id": "a1", "client_order_id": "core-123", "symbol": "SPY", "side": "buy"}],
        {"flex": {}, "daytrade": {}},
    )
    assert r["flat"] is True and not r["positions"] and not r["orders"]


def test_ledger_row_alone_is_not_flat():
    r = classify_flatness([], [], {"flex": {"MU": _row("MU")}, "daytrade": {}})
    assert r["flat"] is False and r["ledger_rows"][0]["symbol"] == "MU"


def test_position_in_ledger_is_attributed():
    r = classify_flatness(
        [{"symbol": "MU", "qty": "10"}, {"symbol": "SPY", "qty": "5"}],
        [], {"flex": {"MU": _row("MU")}},
    )
    assert [p["symbol"] for p in r["positions"]] == ["MU"]


def test_prefix_attribution_all_three_prefixes_case_insensitive():
    for cid in ("FLEXC-2026-10-01-X-entry", "flex-X-1", "FLEXD-2026-10-01-X", "flexc-lower"):
        r = classify_flatness([], [{"id": "z", "client_order_id": cid, "symbol": "X"}], {})
        assert r["flat"] is False, cid
        assert "client_order_id prefix" in r["orders"][0]["reasons"]


def test_bracket_child_with_uuid_attributed_via_ledger_order_ids():
    # child leg carries a broker UUID, not a FLEX prefix (B2 s8.2)
    uuid_leg = "75e34b5c-1111-2222-3333-444444444444"
    r = classify_flatness(
        [], [{"id": uuid_leg, "client_order_id": "9e3c3d77-aaaa", "symbol": "KO",
              "side": "sell", "type": "stop"}],
        {"flex": {"KO": _row("KO", order_ids=[uuid_leg])}},
    )
    assert r["flat"] is False
    assert any("ledger order_ids" in x for x in r["orders"][0]["reasons"])


def test_nested_legs_and_parent_id_attribution():
    parent = {"id": "P1", "client_order_id": "other", "symbol": "ZZ",
              "legs": [{"id": "L1", "client_order_id": "uuid-x", "symbol": "ZZ",
                        "side": "sell", "type": "stop"}]}
    r = classify_flatness([], [parent], {"flex": {"QQ": _row("QQ", order_ids=["P1"])}})
    ids = {o["id"] for o in r["orders"]}
    assert ids == {"P1", "L1"}   # parent by id, child by parent_order_id


def test_stale_resting_order_on_ledger_symbol_is_attributed():
    r = classify_flatness(
        [], [{"id": "s1", "client_order_id": "uuid", "symbol": "MU", "side": "sell"}],
        {"flex": {"MU": _row("MU")}},
    )
    assert "symbol tracked by ledger" in r["orders"][0]["reasons"]


def test_daytrade_ledger_counts():
    r = classify_flatness([], [], {"flex": {}, "daytrade": {"NVDA": _row("NVDA")}})
    assert r["flat"] is False and r["ledger_rows"][0]["engine"] == "daytrade"


def test_non_dict_ledger_rows_ignored():
    assert classify_flatness([], [], {"flex": {"_meta": "x"}})["flat"] is True


def test_main_refuses_without_credentials(monkeypatch, capsys):
    import orb_phase0_flat_check as m
    for k in ("ALPACA_API_KEY", "ALPACA_API_SECRET", "STORAGE_ACCOUNT_NAME"):
        monkeypatch.delenv(k, raising=False)
    assert m.main() == 2
    assert "INCONCLUSIVE" in capsys.readouterr().out
