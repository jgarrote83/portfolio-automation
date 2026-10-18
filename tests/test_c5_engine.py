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
    return H.Deps(alpaca=alpaca, fred=fred_fn, read=lambda c, n: store.get((c, n)), write=lambda c, n, o: store.__setitem__((c, n), o),
                  list_names=lambda c: [n for (cc, n) in store if cc == c], now=lambda: now, enabled=enabled), store


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


def test_kill_switch_blocks_orders():
    a = FakeAlpaca()
    deps, store = make(a, at(date(2026, 7, 6), 10, 0), store={("c5-state", "kill-switch.json"): {"tripped": True}})
    h = H.tick(deps)
    assert a.submitted == [] and h["kill_switch"] is True and "kill switch tripped: no orders" in h["actions"]


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
