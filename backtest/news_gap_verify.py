"""Post-run verification of a news-gap run, with code deliberately separate from `backtest.news_gap`.

    PYTHONPATH=src python -m backtest.news_gap_verify [--run reports/news_gap/<run-id>] [--n 50]

Re-derives a random sample of primary trades AND control (no-news) trades from the raw cached bars and news with
naive pandas/loop code that imports nothing from `backtest.news_gap`: for each sampled (day, symbol) it rebuilds
that day's WHOLE candidate list (open >= $10, prior-20-day dollar volume >= $50M, not an ETF, |gap| >= 2%), splits it
into events and no-news gaps by the overnight window (16:00 ET D-1 to 09:30 ET D, at most 2 symbols, `created_at`
only), ranks by |g| (ties by symbol), and checks that the trade's symbol is in that day's top 5 of its kind with the
same gap, the same open, the same close, and that the 10:00 bar open, share count and net P&L recompute to the
recorded values. Offline cache only (`DataLayer.get_bars/get_news(offline=True)`, repo rule 6): zero API calls; it
cannot change a result.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .data import DataLayer
from .data.config import REPO_ROOT

NY = ZoneInfo("America/New_York")
DATA_FROM, NEWS_FROM = "2023-12-01", "2023-12-29"
EXCHANGES = ("NYSE", "NASDAQ", "AMEX", "ARCA", "BATS")
SLOT, SLIP_C, COMM = 5000.0, 2.0, 0.0035


def latest_run(root: Path | None = None) -> Path:
    root = root or REPO_ROOT / "reports" / "news_gap"
    runs = sorted(p for p in root.iterdir() if (p / "header.json").is_file())
    if not runs:
        raise FileNotFoundError(f"no news-gap run under {root}")
    return runs[-1]


def _utc(day: date, hh: int, mm: int) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=NY).astimezone(timezone.utc)


class World:
    """The raw data, loaded once from the offline cache."""

    def __init__(self, layer: DataLayer, etfs: frozenset[str], end: str, batch: int = 1000) -> None:
        self.cal = layer.trading_days(DATA_FROM, date.fromisoformat(end), offline=True)
        assets = layer.get_assets(offline=True)
        symbols = sorted({s for s in assets.loc[assets["exchange"].isin(EXCHANGES), "symbol"].astype(str)} - set(etfs))
        frames = [layer.get_bars(symbols[i:i + batch], DATA_FROM, date.fromisoformat(end), "1Day", "sip", offline=True)
                  for i in range(0, len(symbols), batch)]
        daily = pd.concat([f for f in frames if not f.empty], ignore_index=True)
        daily["day"] = daily["ts"].dt.date
        self.by_day = {d: g.set_index("symbol") for d, g in daily.groupby("day")}
        news = layer.get_news(date.fromisoformat(NEWS_FROM), date.fromisoformat(end), offline=True)
        news = news[news["symbols"].map(len).between(1, 2)]
        self.news = news.assign(t=news["created_at"].dt.as_unit("ns").astype("int64"))
        self.layer = layer
        self._cands: dict[date, tuple[list[tuple[str, float, float, float]], set[str]]] = {}

    def candidates(self, day: date) -> tuple[list[tuple[str, float, float, float]], set[str]]:
        """-> (gap candidates as (symbol, g, open, close) in the universe, symbols with matching overnight news)."""
        if day in self._cands:
            return self._cands[day]
        i = self.cal.index(day)
        prev, prior = self.cal[i - 1], self.cal[i - 20:i]
        today, prev_df = self.by_day.get(day), self.by_day.get(prev)
        if today is None or prev_df is None:
            self._cands[day] = ([], set())
            return self._cands[day]
        j = today[["open", "close"]].join(prev_df[["close"]].rename(columns={"close": "pc"}), how="inner")
        j = j[(j["open"] >= 10.0) & (j["pc"] > 0)]
        j["g"] = j["open"] / j["pc"] - 1.0
        j = j[j["g"].round(12).abs() >= 0.02]
        out = []
        if len(j):
            rows = [self.by_day[d].reindex(j.index)[["close", "volume"]] for d in prior if d in self.by_day]
            if len(rows) == 20:
                dv = pd.concat([r["close"] * r["volume"] for r in rows], axis=1)
                ok = dv.notna().all(axis=1) & (dv.mean(axis=1) >= 50_000_000.0)
                for sym in ok[ok].index:
                    out.append((sym, float(j.at[sym, "g"]), float(j.at[sym, "open"]), float(j.at[sym, "close"])))
        lo, hi = pd.Timestamp(_utc(prev, 16, 0)).as_unit("ns").value, pd.Timestamp(_utc(day, 9, 30)).as_unit("ns").value
        win = self.news[(self.news["t"] >= lo) & (self.news["t"] < hi)]
        matched: set[str] = set()
        for syms in win["symbols"]:
            matched.update(str(s).upper() for s in syms)
        self._cands[day] = (out, matched)
        return self._cands[day]

    def top5(self, day: date, kind: str) -> list[tuple[str, float, float, float]]:
        cands, matched = self.candidates(day)
        pick = [c for c in cands if (c[0].upper() in matched) == (kind == "news")]
        return sorted(pick, key=lambda c: (-abs(c[1]), c[0]))[:5]

    def open_10(self, day: date, sym: str) -> float | None:
        df = self.layer.get_bars([sym], day, day, "1Min", "sip", window=("10:00", "10:01"), offline=True)
        for r in df.itertuples():
            if r.ts.hour == 10 and r.ts.minute == 0:
                return float(r.open)
        return None


def _check_set(world: World, trades: pd.DataFrame, kind: str, n: int, rng: random.Random) -> dict:
    picks = sorted(rng.sample(range(len(trades)), min(n, len(trades))))
    diffs = []
    for k in picks:
        t = trades.iloc[k]
        day, sym = date.fromisoformat(t["day"]), t["symbol"]
        bad = []
        top = world.top5(day, kind)
        match = [c for c in top if c[0] == sym]
        if not match:
            bad.append("not_in_top5")
        else:
            _s, g, o, c = match[0]
            side = "long" if g > 0 else "short"
            if abs(g - float(t["g"])) > 1e-9:
                bad.append("g")
            if side != t["side"]:
                bad.append("side")
            if abs(o - float(t["daily_open"])) > 1e-6:
                bad.append("daily_open")
            if abs(c - float(t["exit_price"])) > 1e-6:
                bad.append("exit_price")
            o10 = world.open_10(day, sym)
            if o10 is None or abs(o10 - float(t["entry_open"])) > 1e-6:
                bad.append("entry_open")
            else:
                shares = int(math.floor(SLOT / o10 + 1e-9))
                sign = 1.0 if side == "long" else -1.0
                net = sign * shares * (c - o10) - shares * SLIP_C / 100 - 2 * shares * COMM
                if shares != int(t["shares"]):
                    bad.append("shares")
                if abs(net - float(t["net_pnl"])) > 1e-4:
                    bad.append("net_pnl")
        if bad:
            diffs.append({"day": t["day"], "symbol": sym, "fields": bad})
    return {"sampled": len(picks), "matched": len(picks) - len(diffs), "differences": diffs}


def check(layer: DataLayer, run: Path, *, n_primary: int = 50, n_control: int = 50, seed: int = 11,
          etfs: frozenset[str] | None = None, start: str = "2024-01-02", end: str = "2025-12-31") -> dict:
    if etfs is None:
        from .etf import load_etf_list
        etfs = load_etf_list().symbols
    world = World(layer, etfs, end)
    rng = random.Random(seed)
    prim = pd.read_csv(run / "primary" / "trades.csv", dtype={"day": str})
    ctrl = pd.read_csv(run / "control_no_news" / "trades.csv", dtype={"day": str})
    res = {"primary": _check_set(world, prim, "news", n_primary, rng),
           "control": _check_set(world, ctrl, "control", n_control, rng)}
    res["pass"] = all(v["sampled"] > 0 and not v["differences"] for v in res.values() if isinstance(v, dict))
    return res


def render(res: dict, run: Path) -> str:
    mark = "PASS" if res["pass"] else "FAIL"
    lines = [f"# Post-run verification — `{run.name}`", "",
             "Run after the study with code separate from `backtest.news_gap`; offline cache only, no API call, cannot change a result.", "",
             "| Check | Result |", "| --- | --- |"]
    for key, label in (("primary", "primary (news) trades"), ("control", "control (no-news) trades")):
        r = res[key]
        lines.append(f"| Independent re-derivation of {r['sampled']} random {label} from the raw bars and news (universe, gap, "
                     f"overnight news, top-5 membership, gap, open, close, 10:00 open, shares, net P&L) | "
                     f"**{'PASS' if not r['differences'] and r['sampled'] else 'FAIL'}** — {r['matched']} of {r['sampled']} match |")
    lines.append("")
    diffs = res["primary"]["differences"] + res["control"]["differences"]
    if diffs:
        lines.append("Differences: " + "; ".join(f"{d['day']} {d['symbol']} {d['fields']}" for d in diffs[:12]) + "\n")
    lines.append(f"Overall: **{mark}**\n")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backtest.news_gap_verify", description=__doc__.split("\n")[0])
    ap.add_argument("--run")
    ap.add_argument("--n", type=int, default=50, help="trades to re-derive from EACH of the primary and the control")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--cache-dir")
    args = ap.parse_args(argv)
    run = Path(args.run) if args.run else latest_run()
    res = check(DataLayer(args.cache_dir), run, n_primary=args.n, n_control=args.n, seed=args.seed)
    text = render(res, run)
    (run / "verification.md").write_text(text, encoding="utf-8", newline="\n")
    print(text.encode("ascii", "replace").decode("ascii"))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
