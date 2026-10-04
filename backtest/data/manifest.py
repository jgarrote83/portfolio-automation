"""SQLite manifest: which (feed, timeframe, adjustment, window, symbol, day) were already fetched.

Days are stored compactly as a per-(symbol, month) BITMASK (bit d-1 = day-of-month d), not one row
per day: ~15k symbols x 500 days would otherwise be millions of rows. `fetched_mask` records
every day that was REQUESTED -- including days that returned no bars (halted / untraded) -- so
such days are never requested twice; `bars_mask` records the subset that actually had bars.

`window` is part of the key because a day fetched only for its 09:30-09:35 opening range must not
satisfy a full-day request. A full-day fetch (`window == "full"`) covers every window.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from pathlib import Path

FULL = "full"
_CHUNK = 400                       # symbols per IN (...) query (SQLite variable limit is far higher)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS coverage (
    feed TEXT NOT NULL, timeframe TEXT NOT NULL, adjustment TEXT NOT NULL, win TEXT NOT NULL,
    symbol TEXT NOT NULL, month TEXT NOT NULL,
    fetched_mask INTEGER NOT NULL, bars_mask INTEGER NOT NULL,
    PRIMARY KEY (feed, timeframe, adjustment, win, symbol, month)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS files (
    feed TEXT NOT NULL, timeframe TEXT NOT NULL, symbol TEXT NOT NULL, month TEXT NOT NULL,
    nrows INTEGER NOT NULL, nbytes INTEGER NOT NULL,
    PRIMARY KEY (feed, timeframe, symbol, month)
) WITHOUT ROWID;
"""


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def window_key(window: tuple[str, str] | None) -> str:
    """('09:30', '09:35') -> '0930-0935'; None -> 'full'."""
    if window is None:
        return FULL
    return f"{window[0].replace(':', '')}-{window[1].replace(':', '')}"


class Manifest:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # ----------------------------------------------------------------- reads
    def missing(self, feed: str, timeframe: str, adjustment: str, win: str,
                symbols: Iterable[str], days: Iterable[date]) -> dict[str, list[date]]:
        """{symbol: [days NOT yet fetched]} for the requested days (symbols fully covered omitted).
        A day counts as covered if fetched under this window OR under a full-day fetch."""
        by_month: dict[str, list[date]] = defaultdict(list)
        for d in sorted(set(days)):
            by_month[month_key(d)].append(d)
        syms = list(dict.fromkeys(symbols))
        wins = (FULL,) if win == FULL else (FULL, win)
        out: dict[str, list[date]] = defaultdict(list)
        for month, mdays in by_month.items():
            need = 0
            for d in mdays:
                need |= 1 << (d.day - 1)
            covered: dict[str, int] = defaultdict(int)
            for i in range(0, len(syms), _CHUNK):
                chunk = syms[i:i + _CHUNK]
                q = ("SELECT symbol, fetched_mask FROM coverage WHERE feed=? AND timeframe=? "
                     "AND adjustment=? AND month=? AND win IN (%s) AND symbol IN (%s)"
                     % (",".join("?" * len(wins)), ",".join("?" * len(chunk))))
                for sym, mask in self._db.execute(q, (feed, timeframe, adjustment, month, *wins, *chunk)):
                    covered[sym] |= mask
            for sym in syms:
                gap = need & ~covered.get(sym, 0)
                if gap:
                    out[sym].extend(d for d in mdays if gap >> (d.day - 1) & 1)
        return {s: sorted(v) for s, v in out.items()}

    def coverage_row(self, feed: str, timeframe: str, adjustment: str, win: str,
                     symbol: str, month: str) -> tuple[int, int] | None:
        row = self._db.execute(
            "SELECT fetched_mask, bars_mask FROM coverage WHERE feed=? AND timeframe=? AND "
            "adjustment=? AND win=? AND symbol=? AND month=?",
            (feed, timeframe, adjustment, win, symbol, month)).fetchone()
        return (row[0], row[1]) if row else None

    # ---------------------------------------------------------------- writes
    def record(self, feed: str, timeframe: str, adjustment: str, win: str,
               fetched: Iterable[tuple[str, date, bool]]) -> None:
        """Record (symbol, day, had_bars) triples. Idempotent (masks are OR-merged)."""
        agg: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
        for sym, d, had in fetched:
            slot = agg[(sym, month_key(d))]
            bit = 1 << (d.day - 1)
            slot[0] |= bit
            if had:
                slot[1] |= bit
        rows = [(feed, timeframe, adjustment, win, sym, month, fm, bm)
                for (sym, month), (fm, bm) in agg.items()]
        with self._db:
            self._db.executemany(
                "INSERT INTO coverage(feed,timeframe,adjustment,win,symbol,month,fetched_mask,bars_mask) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(feed,timeframe,adjustment,win,symbol,month) "
                "DO UPDATE SET fetched_mask = fetched_mask | excluded.fetched_mask, "
                "bars_mask = bars_mask | excluded.bars_mask", rows)

    def record_files(self, rows: Iterable[tuple[str, str, str, str, int, int]]) -> None:
        """Record (feed, timeframe, symbol, month, nrows, nbytes) for parquet files just written
        (one transaction for the whole batch)."""
        with self._db:
            self._db.executemany(
                "INSERT INTO files(feed,timeframe,symbol,month,nrows,nbytes) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(feed,timeframe,symbol,month) DO UPDATE SET nrows=excluded.nrows, "
                "nbytes=excluded.nbytes", list(rows))

    # ----------------------------------------------------------------- stats
    def stats(self) -> dict:
        files, rows, nbytes = self._db.execute(
            "SELECT COUNT(*), COALESCE(SUM(nrows),0), COALESCE(SUM(nbytes),0) FROM files").fetchone()
        cov = self._db.execute("SELECT COUNT(*) FROM coverage").fetchone()[0]
        return {"parquet_files": files, "rows": rows, "bytes": nbytes, "coverage_rows": cov}
