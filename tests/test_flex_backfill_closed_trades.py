"""scripts/flex_backfill_closed_trades.py: repair of fill-less closed-trade records from broker truth (shapes from the 2026-09/10 incident)."""
import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
_spec = importlib.util.spec_from_file_location("backfill", os.path.join(os.path.dirname(__file__), "..", "scripts", "flex_backfill_closed_trades.py"))
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)


def rec(sym, entry, closed, price, qty, reason="stop_fill", risk=1.0):
    return {"trade_id": f"FLEX-{entry}-{sym}", "symbol": sym, "entry_date": entry, "closed_date": closed, "entry_price": price, "qty_initial": qty,
            "risk_per_share": risk, "fills": [], "exit_reason": reason, "pnl_usd": None, "pnl_unavailable_reason": "no_fills_recorded",
            "r_multiple": None, "holding_days": 0}


def act(t, sym, side, qty, price):
    return {"transaction_time": t, "symbol": sym, "side": side, "qty": str(qty), "price": str(price)}


ACTS = [act("2026-09-14T17:15:02Z", "NEE", "buy", 6, 82.29), act("2026-09-14T17:15:03Z", "NEE", "buy", 65, 82.29),
        act("2026-09-18T13:35:03Z", "NEE", "sell", 37, 80.73), act("2026-09-18T13:35:04Z", "NEE", "sell", 34, 80.74),
        act("2026-10-07T16:30:02Z", "NEE", "buy", 75, 77.11), act("2026-10-08T13:35:02Z", "NEE", "sell", 38, 77.09),
        act("2026-10-08T13:35:03Z", "NEE", "sell", 37, 77.09), act("2026-10-09T14:00:02Z", "NEE", "buy", 75, 77.25)]
LEDGER = {"NEE": {"symbol": "NEE", "entry_date": "2026-10-09"}}


def test_repairs_from_broker_fills_and_corrects_the_close_date():
    closed = [rec("NEE", "2026-09-14", "2026-09-14", 82.29, 71), rec("NEE", "2026-10-07", "2026-10-08", 77.11, 75)]
    out, report = B.repair(closed, LEDGER, ACTS, now="T")
    a, b = out
    assert a["pnl_usd"] == pytest.approx(37 * 80.73 + 34 * 80.74 - 71 * 82.29, abs=0.01) and a["closed_date"] == "2026-09-18"
    assert a["holding_days"] == 4 and a["repaired"]["previous"]["closed_date"] == "2026-09-14"
    assert sum(f["qty"] for f in a["fills"]) == 71 and a["fills"][-1]["reason"] == "stop_fill"
    assert b["pnl_usd"] == pytest.approx(75 * 77.09 - 75 * 77.11, abs=0.01) and b["closed_date"] == "2026-10-08"
    assert [r["action"] for r in report] == ["repaired", "repaired"]


def test_next_entry_bounds_the_window_so_the_open_positions_buy_is_never_used():
    out, _ = B.repair([rec("NEE", "2026-10-07", "2026-10-08", 77.11, 75)], LEDGER, ACTS)
    assert out[0]["entry_price"] == 77.11                     # not averaged with the 10-09 buy at 77.25


def test_qty_mismatch_is_left_untouched_and_reported():
    acts = [a for a in ACTS if not (a["side"] == "sell" and a["transaction_time"].startswith("2026-09-18T13:35:04"))]
    closed = [rec("NEE", "2026-09-14", "2026-09-14", 82.29, 71), rec("NEE", "2026-10-07", "2026-10-08", 77.11, 75)]
    out, report = B.repair(closed, LEDGER, acts)                     # the 10-07 entry bounds the first trade's window
    assert out[1]["pnl_usd"] is not None
    assert out[0]["pnl_usd"] is None and out[0]["fills"] == [] and report[0]["action"].startswith("untouched (bought 71.0, sold 37.0")


def test_priced_or_already_repaired_records_are_never_touched():
    good = dict(rec("NEE", "2026-09-14", "2026-09-14", 82.29, 71), pnl_usd=5.0, fills=[{"qty": 71, "price": 82.36}])
    done = dict(rec("NEE", "2026-10-07", "2026-10-08", 77.11, 75), repaired={"at": "earlier"})
    out, report = B.repair([good, done], LEDGER, ACTS)
    assert out == [good, done] and all(r["action"].startswith("untouched") for r in report)


def test_repair_is_idempotent():
    closed = [rec("NEE", "2026-09-14", "2026-09-14", 82.29, 71)]
    once, _ = B.repair(closed, LEDGER, ACTS, now="T")
    twice, _ = B.repair(once, LEDGER, ACTS, now="T2")
    assert once == twice
