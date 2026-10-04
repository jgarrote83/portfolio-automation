"""ORB Phase 0A -- `scripts/orb_phase0_flat_check.py` pure classification (no network)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from orb_phase0_flat_check import classify_flatness, kill_switch_status  # noqa: E402

TRIPPED = {"tripped": True, "trip_reason": "manual_retirement_orb",
           "tripped_at": "2026-10-05T01:00:00Z"}
CORE = frozenset({"SPY", "QQQ", "SGOV", "AMZN", "INTC"})   # AMZN/INTC stand in for LEGACY_EXITS


def _row(sym, order_ids=()):
    return {"symbol": sym, "qty_current": 10, "entry_date": "2026-10-01",
            "order_ids": list(order_ids)}


def _classify(positions=(), orders=(), ledgers=None, ks=TRIPPED, universe=None):
    return classify_flatness(list(positions), list(orders),
                             ledgers if ledgers is not None else {"flex": {}, "daytrade": {}},
                             ks, universe)


# ---- attribution (kill switch tripped, universe net off) -------------------------

def test_everything_empty_and_tripped_is_flat():
    r = _classify()
    assert r["verdict"] == "FLAT" and r["flat"] is True


def test_core_position_and_core_order_do_not_count():
    r = _classify(
        positions=[{"symbol": "SPY", "qty": "50"}],
        orders=[{"id": "a1", "client_order_id": "core-123", "symbol": "SPY", "side": "buy",
                 "type": "limit"}],
        universe=CORE,
    )
    assert r["flat"] is True and not r["positions"] and not r["orders"]


def test_ledger_row_alone_is_not_flat():
    r = _classify(ledgers={"flex": {"MU": _row("MU")}, "daytrade": {}})
    assert r["verdict"] == "NOT FLAT" and r["ledger_rows"][0]["symbol"] == "MU"


def test_position_in_ledger_is_attributed():
    r = _classify(
        positions=[{"symbol": "MU", "qty": "10"}, {"symbol": "SPY", "qty": "5"}],
        ledgers={"flex": {"MU": _row("MU")}},
    )
    assert [p["symbol"] for p in r["positions"]] == ["MU"]


def test_prefix_attribution_all_three_prefixes_case_insensitive():
    for cid in ("FLEXC-2026-10-01-X-entry", "flex-X-1", "FLEXD-2026-10-01-X", "flexc-lower"):
        r = _classify(orders=[{"id": "z", "client_order_id": cid, "symbol": "X"}])
        assert r["flat"] is False, cid
        assert "client_order_id prefix" in r["orders"][0]["reasons"]


def test_bracket_child_with_uuid_attributed_via_ledger_order_ids():
    # child leg carries a broker UUID, not a FLEX prefix (B2 s8.2)
    uuid_leg = "75e34b5c-1111-2222-3333-444444444444"
    r = _classify(
        orders=[{"id": uuid_leg, "client_order_id": "9e3c3d77-aaaa", "symbol": "KO",
                 "side": "sell", "type": "limit"}],
        ledgers={"flex": {"KO": _row("KO", order_ids=[uuid_leg])}},
    )
    assert r["flat"] is False
    assert any("ledger order_ids" in x for x in r["orders"][0]["reasons"])


def test_nested_legs_and_parent_id_attribution():
    parent = {"id": "P1", "client_order_id": "other", "symbol": "ZZ", "type": "market",
              "legs": [{"id": "L1", "client_order_id": "uuid-x", "symbol": "ZZ",
                        "side": "sell", "type": "limit"}]}
    r = _classify(orders=[parent], ledgers={"flex": {"QQQQ": _row("QQQQ", order_ids=["P1"])}})
    assert {o["id"] for o in r["orders"]} == {"P1", "L1"}   # parent by id, child by parent_order_id


def test_stale_resting_order_on_ledger_symbol_is_attributed():
    r = _classify(
        orders=[{"id": "s1", "client_order_id": "uuid", "symbol": "MU", "side": "sell",
                 "type": "limit"}],
        ledgers={"flex": {"MU": _row("MU")}},
    )
    assert "symbol tracked by ledger" in r["orders"][0]["reasons"]


def test_daytrade_ledger_counts():
    r = _classify(ledgers={"flex": {}, "daytrade": {"NVDA": _row("NVDA")}})
    assert r["flat"] is False and r["ledger_rows"][0]["engine"] == "daytrade"


def test_non_dict_ledger_rows_ignored():
    assert _classify(ledgers={"flex": {"_meta": "x"}})["flat"] is True


# ---- the false-FLAT hole: wiped ledger -------------------------------------------

def test_wiped_ledger_live_non_core_position_and_uuid_stop_is_not_flat():
    # STEP 0 broker-read failure dropped the ledger row; the position and its
    # broker-UUID stop leg remain at Alpaca and nothing names them.
    r = _classify(
        positions=[{"symbol": "MU", "qty": "10"}],
        orders=[{"id": "75e34b5c-aaaa", "client_order_id": "9e3c3d77-bbbb", "symbol": "MU",
                 "side": "sell", "type": "stop", "status": "new"}],
        ledgers={"flex": {}, "daytrade": {}},
        universe=CORE,
    )
    assert r["verdict"] == "NOT FLAT" and r["flat"] is False
    assert r["positions"][0]["via"] == "attributed order"   # via the unattributed stop's symbol
    assert any("resting stop (Core never rests stops)" in o["reasons"] for o in r["orders"])


def test_unattributed_non_core_position_alone_is_not_flat():
    r = _classify(positions=[{"symbol": "MU", "qty": "10"}], universe=CORE)
    assert r["verdict"] == "NOT FLAT"
    assert r["positions"] == [{"symbol": "MU", "qty": "10",
                               "via": "unattributed non-Core position"}]


def test_unattributed_resting_stop_alone_is_not_flat():
    for t in ("stop", "stop_limit", "trailing_stop"):
        r = _classify(orders=[{"id": "u1", "client_order_id": "uuid", "symbol": "SPY",
                               "side": "sell", "type": t}], universe=CORE)
        assert r["verdict"] == "NOT FLAT", t
        assert r["orders"][0]["reasons"] == ["resting stop (Core never rests stops)"]


def test_core_resting_limit_order_is_not_a_stop():
    r = _classify(orders=[{"id": "u1", "client_order_id": "2026-10-05-t1", "symbol": "SPY",
                           "side": "buy", "type": "limit"}], universe=CORE)
    assert r["flat"] is True


def test_core_held_legacy_exit_name_is_flat():
    r = _classify(positions=[{"symbol": "AMZN", "qty": "3"}, {"symbol": "INTC", "qty": "9"}],
                  universe=CORE)
    assert r["verdict"] == "FLAT"


def test_real_core_roster_includes_legacy_exits_and_excludes_flex_names():
    from shared.quadrants import CORE_ROSTER, LEGACY_EXITS
    assert set(LEGACY_EXITS) <= set(CORE_ROSTER)
    assert "MU" not in CORE_ROSTER


# ---- kill-switch gate -------------------------------------------------------------

def test_untripped_switch_is_not_safe_even_when_nothing_is_open():
    for ks in (None, {}, {"tripped": False}):
        r = _classify(ks=ks)
        assert r["verdict"] == "NOT SAFE" and r["flat"] is False and r["safe"] is False


def test_truthy_cleared_at_is_not_safe():
    r = _classify(ks={**TRIPPED, "cleared_at": "2026-10-06T00:00:00Z"})
    assert r["verdict"] == "NOT SAFE"
    assert r["kill_switch"]["cleared_at"] == "2026-10-06T00:00:00Z"


def test_empty_or_null_cleared_at_still_tripped():
    for empty in (None, ""):
        r = _classify(ks={**TRIPPED, "cleared_at": empty})
        assert r["verdict"] == "FLAT", empty


def test_not_flat_takes_precedence_over_not_safe_but_safe_is_false():
    r = _classify(ledgers={"flex": {"MU": _row("MU")}}, ks=None)
    assert r["verdict"] == "NOT FLAT" and r["safe"] is False


def test_kill_switch_status_mirrors_engine_rule():
    assert kill_switch_status(TRIPPED)["safe"] is True
    assert kill_switch_status({"tripped": True, "cleared_at": "x"})["safe"] is False
    assert kill_switch_status(None)["safe"] is False


def test_main_refuses_without_credentials(monkeypatch, capsys):
    import orb_phase0_flat_check as m
    for k in ("ALPACA_API_KEY", "ALPACA_API_SECRET", "STORAGE_ACCOUNT_NAME"):
        monkeypatch.delenv(k, raising=False)
    assert m.main() == 2
    assert "INCONCLUSIVE" in capsys.readouterr().out
