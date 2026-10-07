"""The independent post-run verifier (`backtest.samebar_verify`) on a constructed run: a consistent run passes, and
a tampered tick outcome, a tampered resulting trade and a tampered classification are each caught. It must also
work offline only (stub layer: any non-offline call fails the test)."""
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd  # noqa: E402
from backtest import samebar as S  # noqa: E402
from backtest import samebar_run as run  # noqa: E402
from backtest import samebar_verify as V  # noqa: E402
from backtest.data.store import normalise_trades  # noqa: E402
from backtest.engine import DayInput, Pick, simulate_day  # noqa: E402
from orb.config import OrbConfig  # noqa: E402
from orb.signals import LONG  # noqa: E402

DAY = date(2024, 3, 5)
FREE = OrbConfig().replace(slippage_cents_per_side=0.0, commission_per_share=0.0)
ET = "US/Eastern"


def bar(o, h, lo, c, v=1000):
    return (o, h, lo, c, v)


def t(h, m):
    return h * 60 + m


def long_pick(sym):
    return Pick(sym, 2.0, LONG, 100.00, 101.00, 99.50, 100.80, 2.0)


AMBIG = bar(100.90, 101.10, 100.70, 100.95)
PATHS = {"AAA": {t(9, 35): AMBIG, t(9, 40): bar(101.0, 101.2, 100.9, 101.1), t(15, 59): bar(101.40, 101.70, 101.40, 101.60)},
         "BBB": {t(9, 35): AMBIG, t(9, 36): bar(100.95, 100.95, 100.80, 100.85)},
         "CCC": {t(9, 35): bar(101.50, 101.60, 100.70, 101.00), t(15, 59): bar(101.4, 101.7, 101.4, 101.6)},
         "DDD": {t(9, 35): AMBIG, t(15, 59): bar(101.40, 101.70, 101.40, 101.60)}}          # no bar-level stop later; ticks say a REAL stop


def tr(sec, price, codes=("@",), size=100, i=1):
    return {"t": f"2024-03-05T14:35:{sec:09.6f}Z", "p": price, "s": size, "c": list(codes), "i": i}


def frame(rows):
    df = normalise_trades(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(ET)
    return df


TICKS = {"AAA": frame([tr(1, 100.90), tr(2, 100.50, ("@", "I"), 10, 2), tr(3, 100.70, i=3), tr(4, 100.95, i=4),
                       tr(5, 101.05, i=5), tr(6, 100.95, i=6)]),                           # dip (the odd lot does not count), then the fill
         "DDD": frame([tr(1, 100.90), tr(2, 101.05, i=2), tr(3, 100.70, i=3)])}              # the fill, then the stop: real


class OfflineStub:
    """get_bars / get_trades that insist on offline=True (the verifier must never touch the network)."""

    def get_bars(self, symbols, start, end, timeframe, feed, window=None, offline=False, **_kw):
        assert offline is True
        rows = []
        for sym in symbols:
            for m, b in PATHS.get(sym, {}).items():
                ts = pd.Timestamp(f"{start.isoformat()} {m // 60:02d}:{m % 60:02d}", tz=ET)
                rows.append({"symbol": sym, "ts": ts, "open": b[0], "high": b[1], "low": b[2], "close": b[3], "volume": b[4]})
        return pd.DataFrame(rows, columns=["symbol", "ts", "open", "high", "low", "close", "volume"])

    def get_trades(self, symbol, start, end, offline=False, **_kw):
        assert offline is True
        return TICKS[symbol]


def _build(tmp_path):
    d = DayInput(DAY, 960, tuple(long_pick(s) for s in ("AAA", "BBB", "CCC", "DDD")), PATHS)
    rec = S.Recorder(S.assume_stop_rule)
    default = [simulate_day(d, FREE, entry_bar_stop=rec)]
    opt = [simulate_day(d, FREE, entry_bar_stop=S.ignore_stop_rule)]
    cands = S.build_candidates(S.trades_by_key(default[0:1] and default), S.trades_by_key(opt), rec.calls)
    changed = [c for c in cands if c.changed]
    res = {}
    for c in changed:
        pr = S.counted_prints(TICKS[c.symbol])
        res[(c.day, c.symbol)] = S.resolve_entry_minute(c.side, c.trigger, c.stop, pr)
    resolved = [simulate_day(d, FREE, entry_bar_stop=S.ResolvedRule(S.tick_decisions(res)))]
    out = tmp_path / "run"
    run.write_trades_csv(tmp_path / "phase2" / "P2" / "primary" / "trades.csv", default)
    run.write_trades_csv(out / "tick_resolved" / "trades.csv", resolved)
    pd.DataFrame(run.candidate_rows(cands, res)).to_csv(out / "candidates.csv", index=False)
    (out / "header.json").write_text(json.dumps({"phase2_run": "P2"}), encoding="utf-8")
    return out, tmp_path / "phase2", cands, res


def _check(out, p2root):
    return V.check(OfflineStub(), out, n=4, seed=1, phase2_root=p2root)


def test_a_consistent_run_passes_every_check_offline(tmp_path):
    out, p2root, cands, res = _build(tmp_path)
    assert {c.symbol: c.changed for c in cands} == {"AAA": True, "BBB": False, "CCC": False, "DDD": True}
    assert {k[1]: r.outcome for k, r in res.items()} == {"AAA": S.DIP_FIRST, "DDD": S.REAL_STOP}
    r = _check(out, p2root)
    assert r["pass"] and r["A"]["sampled"] == 2 and r["B"]["sampled"] == 2 and r["C"]["sampled"] == 4
    text = V.render(r, out)
    assert "Overall: **PASS**" in text and "2 of 2 match" in text


def test_a_wrong_tick_outcome_is_caught(tmp_path):
    out, p2root, _c, _r = _build(tmp_path)
    df = pd.read_csv(out / "candidates.csv", dtype={"day": str})
    df.loc[df["symbol"] == "AAA", "tick_outcome"] = "real_stop"
    df.to_csv(out / "candidates.csv", index=False)
    r = _check(out, p2root)
    assert not r["pass"] and any("AAA" in d and "dip_first" in d for d in r["A"]["differences"])


def test_a_wrong_resulting_trade_is_caught_for_both_a_dip_and_a_real_stop(tmp_path):
    out, p2root, _c, _r = _build(tmp_path)
    p = out / "tick_resolved" / "trades.csv"
    df = pd.read_csv(p, dtype={"day": str})
    df.loc[df["symbol"] == "AAA", "exit_fill"] += 0.5            # the dip-first trade's exit price is off
    df.loc[df["symbol"] == "DDD", "exit_reason"] = "time"        # the real stop is booked as a time exit
    df.to_csv(p, index=False)
    r = _check(out, p2root)
    assert not r["pass"] and len(r["B"]["differences"]) == 2


def test_a_wrong_classification_is_caught(tmp_path):
    out, p2root, _c, _r = _build(tmp_path)
    df = pd.read_csv(out / "candidates.csv", dtype={"day": str})
    df.loc[df["symbol"] == "BBB", "ambiguous"] = False           # an ambiguous entry called unambiguous
    df.loc[df["symbol"] == "CCC", "ambiguous"] = True            # a gap entry called ambiguous
    df.loc[df["symbol"] == "DDD", "r_gross_optimistic"] = 2.0    # a wrong optimistic R (the true one is +3.0)
    df.to_csv(out / "candidates.csv", index=False)
    r = _check(out, p2root)
    bad = " ".join(r["C"]["differences"])
    assert not r["pass"] and "BBB" in bad and "CCC" in bad and "DDD" in bad and "optimistic R" in bad


def test_the_verifier_has_its_own_code_list_and_imports_nothing_from_the_diagnostic():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(V))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.append(f"{'.' * node.level}{node.module or ''}")
            imported += [f"{'.' * node.level}{node.module or ''}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
    assert not [m for m in imported if "samebar" in m or "engine" in m or "reports" in m], imported
    assert V.EXCLUDE == set(S.EXCLUDED_CODES)       # the same codes today; a drift between the two copies would show up here
