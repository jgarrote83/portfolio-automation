"""C5 / EasyGrid engine (src/c5/): decision parity with c5.signals, month-ends (half-days, holidays), the strict prior-day credit rule,
order sequencing and idempotency, reconcile, fail-closed on every missing input, the kill switch, the disabled switch, the full
first-enable -> execute -> verify flow on a fake C5 account, and the independence source scan."""
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from c5 import decision as dec  # noqa: E402
from c5 import handler as H  # noqa: E402
from c5 import orders as ords  # noqa: E402
from c5 import schedule as sch  # noqa: E402
from c5 import signals  # noqa: E402

ET = sch.ET
HOLIDAYS = {date(2025, 12, 25), date(2026, 1, 1), date(2026, 4, 3), date(2026, 5, 25), date(2026, 8, 31)}   # 2026-08-31 (Mon) made a holiday on purpose
HALF_DAYS = {date(2025, 11, 28): "13:00", date(2026, 7, 31): "13:00"}


def calendar_rows(start=date(2025, 1, 1), end=date(2026, 12, 31)):
    rows, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in HOLIDAYS:
            rows.append({"date": d.isoformat(), "open": "09:30", "close": HALF_DAYS.get(d, "16:00")})
        d += timedelta(days=1)
    return rows


SS = sch.sessions(calendar_rows())


# --- schedule -------------------------------------------------------------------------------------------------------------

def test_month_end_handles_half_days_and_holidays():
    assert sch.is_month_end(SS, date(2025, 11, 28))                      # a half-day month-end
    assert sch.is_month_end(SS, date(2026, 8, 28)) and not sch.session_on(SS, date(2026, 8, 31))   # Friday, because Monday 08-31 is a holiday
    assert not sch.is_month_end(SS, date(2026, 8, 27)) and not sch.is_month_end(SS, date(2026, 8, 29))
    assert sch.moc_cutoff(sch.session_on(SS, date(2025, 11, 28))) == datetime(2025, 11, 28, 12, 50, tzinfo=ET)
    assert sch.moc_cutoff(sch.session_on(SS, date(2026, 6, 1))) == datetime(2026, 6, 1, 15, 50, tzinfo=ET)


def test_last_completed_month_end_waits_for_the_settled_close():
    s = sch.session_on(SS, date(2026, 6, 30))
    assert sch.last_completed_month_end(SS, s.close + timedelta(minutes=5)).day == date(2026, 5, 29)
    assert sch.last_completed_month_end(SS, s.close + timedelta(minutes=25)).day == date(2026, 6, 30)


def test_a_calendar_that_ends_too_soon_refuses_to_guess():
    with pytest.raises(ValueError):
        sch.is_month_end(sch.sessions(calendar_rows(end=date(2026, 6, 30))), date(2026, 6, 30))


# --- decision -------------------------------------------------------------------------------------------------------------

def closes(start=100.0, step=0.1):
    return {s.day: start + i * step for i, s in enumerate(SS)}


def baa(level=2.0, slope=0.0):
    out, d, i = [], date(2025, 1, 1), 0
    while d <= date(2026, 12, 31):
        if d.weekday() < 5:
            out.append((d, level + slope * i))
            i += 1
        d += timedelta(days=1)
    return out


def test_decision_matches_signals_on_fixed_inputs():
    c, b = closes(), baa(slope=-0.001)
    got = dec.decide(date(2026, 6, 30), c, SS, b)
    me = [c[s.day] for s in sch.month_ends_until(SS, date(2026, 6, 30))][-10:]
    cv = [v for _, v, _ in dec.credit_series(b, SS, date(2026, 6, 30))]
    assert got["trend"]["ok"] == signals.trend_healthy(me) is True
    assert got["credit"]["ok"] == signals.credit_calm(cv) is True
    assert got["holding"] == signals.holding(True, True) == "2X" and got["target"] == "SSO"
    worse = dec.decide(date(2026, 6, 30), closes(start=300, step=-0.1), SS, baa(slope=0.001))
    assert (worse["trend"]["ok"], worse["credit"]["ok"], worse["target"]) == (False, False, "BIL")


def test_credit_uses_only_values_dated_strictly_before_each_day():
    b = [(d, 9.0 if d == date(2026, 6, 30) else 2.0) for d, _ in baa()]
    cs = dec.credit_series(b, SS, date(2026, 6, 30))
    assert cs[-1][1] == 2.0 and cs[-1][2] == date(2026, 6, 29)        # the 9.0 printed ON the decision day is never used
    assert signals.credit_value_before([date(2026, 6, 29), date(2026, 6, 30)], [1.0, 9.0], date(2026, 6, 30)) == (1.0, date(2026, 6, 29))


@pytest.mark.parametrize("kind", ["no_month_end_close", "stale_credit", "no_credit", "short_history"])
def test_decide_fails_closed(kind):
    c, b = closes(), baa()
    if kind == "no_month_end_close":
        del c[date(2026, 5, 29)]
    elif kind == "stale_credit":
        b = [(d, v) for d, v in b if d < date(2026, 6, 15)]
    elif kind == "no_credit":
        b = []
    elif kind == "short_history":
        c = {d: v for d, v in c.items() if d >= date(2026, 1, 1)}
    with pytest.raises(dec.MissingInput):
        dec.decide(date(2026, 6, 30), c, SS, b)


def test_preview_is_labelled_as_a_preview():
    p = dec.preview(date(2026, 7, 15), closes(), SS, baa())
    assert p["kind"] == "preview" and p["note"] == "If the month ended today (preview, not a decision)."


# --- orders ---------------------------------------------------------------------------------------------------------------

def test_plan_sells_first_whole_shares_and_idempotent_ids():
    o = ords.plan({"SPY": 150.0, "BIL": 3.0}, "SSO", 100_000.0, 99.0, "202606")
    assert [x["side"] for x in o] == ["sell", "sell", "buy"]
    assert o[-1] == {"symbol": "SSO", "side": "buy", "qty": int(100_000 * 0.995 // 99), "type": "market", "time_in_force": "cls",
                     "client_order_id": "C5-202606-buy-SSO"}
    assert {x["client_order_id"] for x in o} == {"C5-202606-sell-BIL", "C5-202606-sell-SPY", "C5-202606-buy-SSO"}
    assert ords.plan({"SSO": 1005.0}, "SSO", 100_000.0, 99.0, "202606") == []      # already at target: no order


def test_reconcile_and_verify():
    assert ords.reconcile({"SSO": 10.0}, {"holdings": {"SSO": 10}}) == []
    assert ords.reconcile({"SSO": 10.0, "SPY": 1.0}, {"holdings": {"SSO": 9}}) == [
        {"symbol": "SPY", "account": 1.0, "ledger": 0.0}, {"symbol": "SSO", "account": 10.0, "ledger": 9.0}]
    assert ords.holds_target({"SSO": 10.0}, "SSO") and not ords.holds_target({"SSO": 10.0, "SPY": 1.0}, "SSO")


# --- the engine on a fake C5 account --------------------------------------------------------------------------------------

class FakeAlpaca:
    def __init__(self, positions=None, equity=100_000.0, fail=None):
        self.positions, self.equity, self.fail = dict(positions or {}), equity, fail or set()
        self.submitted, self.orders, self.clock_open = [], [], True

    def get_clock(self):
        if "clock" in self.fail:
            raise RuntimeError("clock down")
        return {"is_open": self.clock_open}

    def get_calendar(self, start, end):
        return [] if "calendar" in self.fail else calendar_rows()

    def get_bars(self, symbols, timeframe, start, end, feed="iex", adjustment="raw"):
        assert feed == "sip" and timeframe == "1Day"
        if "bars" in self.fail:
            return {}
        syms = [symbols] if isinstance(symbols, str) else symbols
        cut = datetime.fromisoformat(end).date()
        base = {"SPY": 100.0, "SSO": 50.0, "BIL": 91.0}
        def stamp(d):                                                   # Alpaca stamps a daily bar at New York midnight, in UTC
            return datetime(d.year, d.month, d.day, tzinfo=ET).astimezone(sch.ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {s: [{"t": stamp(d), "c": base.get(s, 10.0) + i * 0.1} for i, d in enumerate(x.day for x in SS) if d <= cut] for s in syms}

    def get_account(self):
        return {"equity": str(self.equity), "cash": "1000.0"}

    def list_positions(self):
        return [{"symbol": s, "qty": str(q)} for s, q in self.positions.items()]

    def list_orders(self, status="open"):
        return list(self.orders)

    def submit_order(self, symbol, qty, side, order_type, time_in_force, client_order_id):
        assert order_type == "market" and time_in_force == "cls"
        self.submitted.append((side, symbol, qty, client_order_id))
        o = {"id": f"id-{len(self.orders)}", "client_order_id": client_order_id, "status": "accepted", "symbol": symbol}
        self.orders.append(o)
        return o


def make(alpaca, now, enabled=True, store=None, fred=None, fred_fail=False):
    store = store if store is not None else {}

    def fred_fn(a, b):
        if fred_fail:
            raise dec.MissingInput("FRED down")
        return fred if fred is not None else baa()
    def append(c, n, rec):
        store.setdefault((c, n), []).append(rec)
    return H.Deps(alpaca=alpaca, fred=fred_fn, read=lambda c, n: store.get((c, n)), write=lambda c, n, o: store.__setitem__((c, n), o),
                  list_names=lambda c: [n for (cc, n) in store if cc == c], now=lambda: now, enabled=enabled,
                  append=append, read_lines=lambda c, n: list(store.get((c, n)) or [])), store


def at(d, hh, mm):
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)


def test_full_flow_first_enable_execute_verify():
    a = FakeAlpaca(positions={})
    deps, store = make(a, at(date(2026, 7, 6), 10, 0))                # Monday 2026-07-06, market open
    h = H.tick(deps)
    st = store[("c5-state", "202606.json")]
    assert st["first_enable"] and st["decision_date"] == "2026-06-30" and st["status"] == "submitted"
    assert [x[0] for x in a.submitted] == ["buy"] and a.submitted[0][1] == st["decision"]["target"]
    assert h["error"] is None
    # a second tick the same morning submits nothing new (idempotent)
    H.tick(deps)
    assert len(a.submitted) == 1
    # after the settled close: the position is there -> executed, ledger written, series appended, preview written
    a.positions = {st["decision"]["target"]: float(a.submitted[0][2])}
    a.orders[0].update({"status": "filled", "filled_avg_price": "125.0", "filled_qty": str(a.submitted[0][2])})
    deps.now = lambda: at(date(2026, 7, 6), 16, 30)
    H.tick(deps)
    st = store[("c5-state", "202606.json")]
    assert st["status"] == "executed" and store[("c5-ledger", "ledger.json")]["holdings"] == a.positions
    assert store[("c5-equity-series", "series.json")][-1]["date"] == "2026-07-06"
    assert store[("c5-state", "preview.json")]["kind"] == "preview"


def test_month_end_decision_then_next_session_execution_sells_first():
    store = {}
    a = FakeAlpaca(positions={"SPY": 900.0})
    store[("c5-ledger", "ledger.json")] = {"holdings": {"SPY": 900.0}}
    store[("c5-state", "202606.json")] = {"month": "202606", "status": "executed", "decision": {"target": "SPY"}}
    deps, store = make(a, at(date(2026, 7, 31), 13, 25), store=store)     # half-day month-end, settled after 13:20
    H.tick(deps)
    st = store[("c5-state", "202607.json")]
    assert st["status"] == "decided" and st["execution_date"] == "2026-08-03" and a.submitted == []
    deps.now = lambda: at(date(2026, 8, 3), 9, 45)
    H.tick(deps)
    assert st["decision"]["target"] == "SSO"                           # rising SPY, flat credit (<= its average) -> both healthy
    assert [(x[0], x[1]) for x in a.submitted] == [("sell", "SPY"), ("buy", "SSO")]       # sells first
    assert [x[3] for x in a.submitted] == ["C5-202607-sell-SPY", "C5-202607-buy-SSO"]


@pytest.mark.parametrize("fail", ["clock", "calendar", "bars", "fred"])
def test_every_missing_input_fails_closed(fail):
    a = FakeAlpaca(fail={fail} - {"fred"})
    deps, store = make(a, at(date(2026, 7, 6), 10, 0), fred_fail=(fail == "fred"))
    h = H.tick(deps)
    assert a.submitted == [] and h["error"] and ("c5-state", "health.json") in store


def test_legacy_tripped_kill_switch_reads_as_paused_and_blocks_orders():
    a = FakeAlpaca()
    deps, store = make(a, at(date(2026, 7, 6), 10, 0), store={("c5-state", "kill-switch.json"): {"tripped": True}})
    h = H.tick(deps)
    assert a.submitted == [] and h["kill_switch"] is True and h["control"] == "paused"


def test_disabled_never_trades_and_refreshes_the_dry_run():
    a = FakeAlpaca()
    deps, store = make(a, at(date(2026, 7, 6), 10, 0), enabled=False)
    H.tick(deps)
    assert a.submitted == [] and store[("c5-state", "dry-run.json")]["note"].startswith("dry run")
    assert not any(n.startswith("2026") for (c, n) in store if c == "c5-state")
    H.tick(deps)                                                        # within the hour: no refetch
    assert a.submitted == []


def test_no_orders_after_the_moc_cutoff():
    a = FakeAlpaca()
    deps, store = make(a, at(date(2026, 7, 6), 15, 55))
    H.tick(deps)
    assert a.submitted == [] and store[("c5-state", "202606.json")]["execution_date"] == "2026-07-07"


def test_reconcile_mismatch_is_reported():
    a = FakeAlpaca(positions={"SSO": 5.0})
    deps, store = make(a, at(date(2026, 7, 8), 11, 0), store={("c5-ledger", "ledger.json"): {"holdings": {"SSO": 6.0}},
                                                             ("c5-state", "202606.json"): {"month": "202606", "status": "executed"}})
    h = H.tick(deps)
    assert h["reconcile"] == {"mismatches": [{"symbol": "SSO", "account": 5.0, "ledger": 6.0}]}


def test_enabled_defaults_to_false(monkeypatch):
    monkeypatch.delenv("C5_ENABLED", raising=False)
    assert H.enabled() is False
    monkeypatch.setenv("C5_ENABLED", "true")
    assert H.enabled() is True
    bicep = (ROOT / "infra" / "modules" / "functionapp.bicep").read_text(encoding="utf-8")
    assert re.search(r"name: 'C5_ENABLED',\s+value: 'false'", bicep)
    assert "secrets/Alpaca-Easygrid-API-Key/" in bicep and "secrets/Alpaca-Easygrid-API-Secret/" in bicep


# --- independence (source scan) -------------------------------------------------------------------------------------------

def test_c5_never_touches_core_and_core_never_imports_c5():
    c5_src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src" / "c5").glob("*.py"))
    for forbidden in ("AlpacaApiKey", "AlpacaApiSecret", "load_secrets", "daily-trades", "daily-executions", "daily-snapshots", "flex-ledger",
                      "performance/", "TradeHistory", "PortfolioHistory", "from executor", "from collector", "from analyzer", "from flex", "import executor"):
        assert forbidden not in c5_src, forbidden
    mods = set(re.findall(r"^\s*(?:from|import) ([a-z_][\w.]*)", c5_src, re.M))
    mods = {m for m in mods if not m.startswith("c5")}
    assert mods <= {"__future__", "logging", "os", "dataclasses", "datetime", "typing", "math", "bisect", "collections.abc", "zoneinfo", "requests",
                    "azure.identity", "azure.keyvault.secrets", "shared", "shared.clients.alpaca"}, mods
    core = [p for p in (ROOT / "src").rglob("*.py") if "c5" not in p.parts and p.name != "function_app.py"]
    for p in core:
        assert not re.search(r"^\s*(from|import) c5\b", p.read_text(encoding="utf-8"), re.M), p
    fa = (ROOT / "src" / "function_app.py").read_text(encoding="utf-8")
    assert "C5_ALPACA_KEY_ID" not in fa and "AlpacaApiKey" not in fa.split("def c5_tick")[1].split("@app")[0]


def test_health_carries_the_next_decision_date():
    a = FakeAlpaca()
    deps, store = make(a, at(date(2026, 7, 6), 10, 0), enabled=False)
    h = H.tick(deps)
    assert h["next_decision_date"] == "2026-07-31"
    deps2, _ = make(FakeAlpaca(), at(date(2026, 7, 31), 16, 30), enabled=False)
    assert H.tick(deps2)["next_decision_date"] == "2026-08-28"        # 07-31 has settled; August ends on Friday 08-28


# --- portal controls: kill-switch states and re-align (engine side) -------------------------------------------------------

def control(state, at_="2026-07-07T10:00:00-04:00", reason="test"):
    return {"state": state, "changed_at": at_, "changed_by": "jorge", "reason": reason}


def running_store(target="SSO", qty=1990.0):
    """An account already running: last month executed into `target`."""
    return {("c5-ledger", "ledger.json"): {"holdings": {target: qty}},
            ("c5-state", "202606.json"): {"month": "202606", "kind": "monthly", "status": "executed", "decision": {"target": target},
                                          "created_at": "2026-07-01T10:00:00-04:00", "submitted_on": "2026-07-01", "equity_after_close": 100000.0}}


def test_paused_places_no_orders_but_still_records_the_month_end_decision():
    store = running_store()
    store[("c5-state", "kill-switch.json")] = control("paused")
    a = FakeAlpaca(positions={"SSO": 1990.0})
    deps, store = make(a, at(date(2026, 7, 31), 13, 25), store=store)          # the half-day month-end, settled
    h = H.tick(deps)
    assert store[("c5-state", "202607.json")]["status"] == "decided" and any("not executed" in x for x in h["actions"])
    deps.now = lambda: at(date(2026, 8, 3), 9, 45)
    H.tick(deps)
    assert a.submitted == [] and store[("c5-state", "202607.json")]["status"] == "decided"


def test_paused_to_tbills_sells_to_bil_at_the_next_close_then_stops():
    store = running_store()
    store[("c5-state", "kill-switch.json")] = control("paused_to_tbills")
    a = FakeAlpaca(positions={"SSO": 1990.0})
    deps, store = make(a, at(date(2026, 7, 7), 10, 5), store=store)            # within one tick of the change
    H.tick(deps)
    assert [(x[0], x[1]) for x in a.submitted] == [("sell", "SSO"), ("buy", "BIL")]          # sells first
    assert all(x[3].startswith("C5-PT202607071000-") for x in a.submitted)
    a.positions = {"BIL": float(a.submitted[1][2])}
    deps.now = lambda: at(date(2026, 7, 7), 16, 30)
    H.tick(deps)
    pt = [v for (c, n), v in store.items() if c == "c5-state" and n.startswith("PT")][0]
    assert pt["status"] == "executed"
    n = len(a.submitted)
    deps.now = lambda: at(date(2026, 7, 31), 16, 30)                           # a month-end passes: recorded, never executed
    H.tick(deps)
    deps.now = lambda: at(date(2026, 8, 3), 10, 0)
    H.tick(deps)
    assert len(a.submitted) == n and store[("c5-state", "202607.json")]["status"] == "decided"


def test_resume_realigns_to_the_latest_month_end_decision():
    store = running_store(target="BIL", qty=1080.0)
    store[("c5-state", "kill-switch.json")] = control("active", at_="2026-07-08T09:40:00-04:00")
    store[("c5-state", "realign-request.json")] = {"requested_at": "2026-07-08T09:40:00-04:00", "requested_by": "jorge", "reason": "resume"}
    a = FakeAlpaca(positions={"BIL": 1080.0})
    deps, store = make(a, at(date(2026, 7, 8), 9, 45), store=store)
    h = H.tick(deps)
    ra = [v for (c, n), v in store.items() if c == "c5-state" and n.startswith("RA")][0]
    assert ra["decision_date"] == "2026-06-30" and ra["decision"]["target"] == "SSO" and ra["status"] == "submitted"
    assert [(x[0], x[1]) for x in a.submitted] == [("sell", "BIL"), ("buy", "SSO")]
    assert store[("c5-state", "realign-request.json")]["cleared_at"] and h["realign_pending"] is False
    H.tick(deps)                                                                # cleared: not honoured twice
    assert len(a.submitted) == 2


def test_realign_is_ignored_while_paused():
    store = running_store()
    store[("c5-state", "kill-switch.json")] = control("paused")
    store[("c5-state", "realign-request.json")] = {"requested_at": "2026-07-08T09:40:00-04:00", "requested_by": "jorge", "reason": "x"}
    a = FakeAlpaca(positions={"SSO": 1990.0})
    deps, store = make(a, at(date(2026, 7, 8), 9, 45), store=store)
    h = H.tick(deps)
    assert a.submitted == [] and "re-align request ignored while paused" in h["actions"]
    assert not any(n.startswith("RA") for (c, n) in store if c == "c5-state") and not store[("c5-state", "realign-request.json")].get("cleared_at")


def test_every_control_change_is_logged_once():
    store = running_store()
    store[("c5-state", "kill-switch.json")] = control("paused")
    deps, store = make(FakeAlpaca(positions={"SSO": 1990.0}), at(date(2026, 7, 8), 9, 45), store=store)
    H.tick(deps)
    H.tick(deps)
    log = store[("c5-state", "kill-switch-log.jsonl")]
    assert [e["kind"] for e in log] == ["state"] and log[0]["state"] == "paused" and log[0]["changed_by"] == "jorge" and log[0]["reason"] == "test"
    store[("c5-state", "kill-switch.json")] = control("active", at_="2026-07-08T11:00:00-04:00")
    H.tick(deps)
    assert [e.get("state") for e in store[("c5-state", "kill-switch-log.jsonl")] if e["kind"] == "state"] == ["paused", "active"]


def test_an_unknown_control_state_fails_closed_to_paused():
    store = running_store()
    store[("c5-state", "kill-switch.json")] = {"state": "yolo", "changed_at": "t"}
    a = FakeAlpaca(positions={"SSO": 1990.0})
    deps, _ = make(a, at(date(2026, 7, 8), 9, 45), store=store)
    h = H.tick(deps)
    assert h["control"] == "paused" and "treated as paused" in h["error"] and a.submitted == []


def test_wrong_holding_raises_the_mismatch_alert_once_a_day(caplog):
    store = running_store(target="SSO")
    a = FakeAlpaca(positions={"SPY": 900.0})                                     # something changed the account
    deps, store = make(a, at(date(2026, 7, 8), 11, 0), store=store)
    with caplog.at_level("ERROR", logger="c5"):
        h = H.tick(deps)
        H.tick(deps)
    assert h["holding_mismatch"] == {"expected": "SSO", "actual": {"SPY": 900.0}}
    assert sum("C5 HOLDING MISMATCH" in r.getMessage() for r in caplog.records) == 1


def test_disabled_dry_run_reflects_a_pause_within_one_tick():
    a = FakeAlpaca(positions={"SSO": 1990.0})
    deps, store = make(a, at(date(2026, 7, 8), 9, 45), enabled=False)
    H.tick(deps)
    assert store[("c5-state", "dry-run.json")]["control"] == "active" and store[("c5-state", "dry-run.json")]["intended_orders"]
    store[("c5-state", "kill-switch.json")] = control("paused")
    deps.now = lambda: at(date(2026, 7, 8), 10, 0)                              # next tick, inside the hour
    H.tick(deps)
    dr = store[("c5-state", "dry-run.json")]
    assert dr["control"] == "paused" and dr["intended_orders"] == [] and a.submitted == []
