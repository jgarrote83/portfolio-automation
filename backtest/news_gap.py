"""News event study: does a news-driven gap continue through the day?

Hypothesis, rules, diagnostics and the mechanical go/no-go are pre-registered in
`docs/specs/News_Gap_Preregistration.md` (locked at its first commit). No LLM is used anywhere: the news
condition is a timestamp-and-symbol-list match and the event types are a fixed keyword list.

    PYTHONPATH=src python -m backtest.news_gap run [--review-file .review/news-gap-results.md]

Data comes ONLY through `backtest.data` (`get_bars`, `get_news`, repo rule 6) plus the Nasdaq Trader ETF files
already in `data/reference/`. Any date on or after 2026-01-01 is refused (bars and news alike); there is no
override flag. Only the exact pre-registered invocation produces a verdict.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import dataclasses
import json
import math
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import provenance
from .data import DataLayer
from .data.bars import CacheMissError, HoldoutError
from .data.client import AlpacaDataError
from .data.config import ET, HOLDOUT_START, REPO_ROOT
from .data.credentials import MissingCredentialsError
from .etf import EtfListMissingError, load_etf_list
from .iex_vs_sip import equity_symbols, panels_from_daily

SPEC_VERSION = "newsgap-1.0"
PREREG_PATH = "docs/specs/News_Gap_Preregistration.md"
PREREG_START, PREREG_END = "2024-01-02", "2025-12-31"
DATA_FROM = "2023-12-01"                      # 20 prior trading days for 2024-01-02
NEWS_FROM = "2023-12-29"                      # the previous session of 2024-01-02: its overnight window starts here
DEFAULT_OUT = REPO_ROOT / "reports" / "news_gap"
GO_MIN_SHARPE = 1.0
GO_YEARS = (2024, 2025)
_EPS_SHARES = 1e-9
_ROUND = 12                                   # decimals: a float-noise guard for the |g| >= 2% test
SIDE_LONG, SIDE_SHORT = "long", "short"

EVENT_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "earnings": ("earnings", "EPS", "results", "revenue", "quarter", "Q1", "Q2", "Q3", "Q4"),
    "guidance": ("guidance", "outlook", "forecast", "raises", "lowers", "cuts"),
    "analyst": ("upgrade", "downgrade", "price target", "initiates"),
    "deal": ("acquire", "acquisition", "merger", "buyout", "takeover"),
    "regulatory": ("FDA", "approval", "trial", "phase"),
    "offering": ("offering", "priced", "dilution"),
}
EVENT_TYPES = tuple(EVENT_TYPE_KEYWORDS) + ("other",)


# ================================================================================== configuration
@dataclass(frozen=True)
class NewsGapConfig:
    """The primary rule; each diagnostic changes exactly one field. Pinned to the pre-registration's
    machine-readable block by `tests/test_news_gap.py`."""
    spec_version: str = SPEC_VERSION
    kind: str = "news"                             # "news" (events) | "control" (gaps with NO overnight news)
    min_open: float = 10.0
    dollar_volume_days: int = 20
    min_avg_dollar_volume: float = 50_000_000.0
    min_abs_gap: float = 0.02
    max_news_symbols: int = 2
    news_window_start: str = "16:00"
    news_window_end: str = "09:30"
    top_n: int = 5
    sleeve_capital_usd: float = 25_000.0
    slots: int = 5
    entry_time: str = "10:00"
    entry_slippage_cents: float = 2.0
    commission_per_share: float = 0.0035
    allow_shorts: bool = True
    require_confirmation: bool = False

    def replace(self, **changes) -> "NewsGapConfig":
        return dataclasses.replace(self, **changes)

    def diff_from_default(self) -> dict:
        base = NewsGapConfig()
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self)
                if getattr(self, f.name) != getattr(base, f.name)}

    @property
    def slot_capital_usd(self) -> float:
        return self.sleeve_capital_usd / self.slots


@dataclass(frozen=True)
class Variant:
    name: str
    overrides: tuple[tuple[str, object], ...] = ()

    def config(self, base: NewsGapConfig) -> NewsGapConfig:
        return base.replace(**dict(self.overrides)) if self.overrides else base


PRIMARY = Variant("primary")
DIAGNOSTICS = (
    Variant("control_no_news", (("kind", "control"),)),
    Variant("confirmation_filter", (("require_confirmation", True),)),
    Variant("long_only", (("allow_shorts", False),)),
    Variant("slippage_0c", (("entry_slippage_cents", 0.0),)),
    Variant("slippage_1c", (("entry_slippage_cents", 1.0),)),
    Variant("slippage_5c", (("entry_slippage_cents", 5.0),)),
)
ALL_VARIANTS = (PRIMARY,) + DIAGNOSTICS


def assert_not_holdout(*days: date) -> None:
    """Repo rule 6: nothing on or after 2026-01-01 (bars or news) is ever read; no override flag."""
    for d in days:
        if d >= HOLDOUT_START:
            raise HoldoutError(f"{d} is on/after {HOLDOUT_START}: the 2026 holdout is closed and this study has no "
                               "override. It needs Jorge's explicit approval, after a strategy is chosen.")


# ====================================================================================== pure logic
def gap_return(open_d: float, prev_close: float) -> float:
    """g = (day-D open) / (day-(D-1) close) - 1, raw prices."""
    return open_d / prev_close - 1.0


def gap_qualifies(g: float, cfg: NewsGapConfig) -> bool:
    return round(abs(g), _ROUND) >= cfg.min_abs_gap


def avg_dollar_volume(closes: Sequence[float | None], volumes: Sequence[float | None], n: int) -> float | None:
    """Mean of close x volume over EXACTLY the `n` prior trading days; any missing day -> None."""
    if len(closes) != n or len(volumes) != n:
        return None
    total = 0.0
    for c, v in zip(closes, volumes):
        if c is None or v is None or c != c or v != v:
            return None
        total += c * v
    return total / n


def in_universe(open_d: float | None, prev_close: float | None, adv: float | None, cfg: NewsGapConfig) -> bool:
    """Day-D open >= $10, prior-20-day average dollar volume >= $50M, daily bars for D-1 and D (ETF exclusion is
    the caller's: it owns the list)."""
    if open_d is None or prev_close is None or adv is None:
        return False
    if open_d != open_d or prev_close != prev_close or not prev_close > 0:
        return False
    return open_d >= cfg.min_open and adv >= cfg.min_avg_dollar_volume


def _hhmm(s: str) -> tuple[int, int]:
    hh, mm = s.split(":")
    return int(hh), int(mm)


def overnight_window(prev_day: date, day: date, cfg: NewsGapConfig = NewsGapConfig()) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[16:00 ET on D-1, 09:30 ET on D) as UTC timestamps: the start is inclusive, the end exclusive. The ET
    offset is each date's own, so the window is correct across the March and November DST changes."""
    h0, m0 = _hhmm(cfg.news_window_start)
    h1, m1 = _hhmm(cfg.news_window_end)
    start = pd.Timestamp(year=prev_day.year, month=prev_day.month, day=prev_day.day, hour=h0, minute=m0, tz=ET)
    end = pd.Timestamp(year=day.year, month=day.month, day=day.day, hour=h1, minute=m1, tz=ET)
    return start.tz_convert("UTC"), end.tz_convert("UTC")


def article_matches(symbols: Sequence[str], ticker: str, cfg: NewsGapConfig = NewsGapConfig()) -> bool:
    """The article's `symbols` list contains the ticker (exact, upper-cased) and has at most 2 entries."""
    return 0 < len(symbols) <= cfg.max_news_symbols and ticker.upper() in {str(s).upper() for s in symbols}


class NewsIndex:
    """Per-ticker, time-sorted index of the articles that can ever match (at most `max_news_symbols` symbols)."""

    def __init__(self, news: pd.DataFrame, cfg: NewsGapConfig = NewsGapConfig()) -> None:
        self.cfg = cfg
        self._times: dict[str, list[int]] = defaultdict(list)
        self._rows: dict[str, list[tuple[int, str, bool]]] = defaultdict(list)
        if news.empty:
            return
        created = news["created_at"].dt.as_unit("ns").astype("int64").tolist()
        changed = (news["updated_at"] != news["created_at"]).tolist()
        order = sorted(range(len(created)), key=lambda k: (created[k], int(news["id"].iat[k])))
        ids, heads, syms = news["id"].tolist(), news["headline"].tolist(), news["symbols"].tolist()
        for k in order:
            sl = list(syms[k])
            if not 0 < len(sl) <= cfg.max_news_symbols:
                continue
            for t in {str(x).upper() for x in sl}:
                self._times[t].append(created[k])
                self._rows[t].append((int(ids[k]), str(heads[k]), bool(changed[k])))

    def matching(self, ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> list[tuple[int, str, bool]]:
        """(id, headline, updated_at != created_at) of the articles with start <= created_at < end."""
        t = self._times.get(ticker.upper())
        if not t:
            return []
        lo = bisect.bisect_left(t, start.as_unit("ns").value)
        hi = bisect.bisect_left(t, end.as_unit("ns").value)
        return self._rows[ticker.upper()][lo:hi]


def rank_candidates(cands: Iterable["Candidate"], n: int) -> list["Candidate"]:
    """|g| descending, ties by symbol ascending; the first `n`."""
    return sorted(cands, key=lambda c: (-abs(c.g), c.symbol))[:n]


def _keyword_pattern(kw: str) -> re.Pattern:
    body = re.escape(kw).replace(r"\ ", r"\s+")
    # all-capital tokens (EPS, FDA, Q1..Q4) are whole words; every other keyword matches at a word start
    return re.compile(rf"\b{body}\b" if kw.isupper() else rf"\b{body}", re.IGNORECASE)


_PATTERNS = {t: [_keyword_pattern(k) for k in kws] for t, kws in EVENT_TYPE_KEYWORDS.items()}


def classify_headlines(headlines: Iterable[str]) -> tuple[str, ...]:
    """The event types carried by a set of headlines, in the fixed order of EVENT_TYPES; 'other' if none."""
    heads = list(headlines)
    found = [t for t, pats in _PATTERNS.items() if any(p.search(h) for h in heads for p in pats)]
    return tuple(found) if found else ("other",)


def share_count(entry_open: float, slot_capital: float) -> int:
    """floor(slot / entry price) on the un-slipped open, tolerant of float noise."""
    if not (entry_open > 0) or not (slot_capital > 0):
        return 0
    return int(math.floor(slot_capital / entry_open + _EPS_SHARES))


def entry_fill(entry_open: float, side: str, cents: float) -> float:
    """A long buys at open + slippage, a short sells at open - slippage."""
    s = cents / 100.0
    return entry_open + s if side == SIDE_LONG else entry_open - s


@dataclass(frozen=True)
class Candidate:
    day: date
    symbol: str
    g: float
    daily_open: float
    prev_close: float
    daily_close: float | None
    adv: float
    n_articles: int
    article_ids: tuple[int, ...] = ()
    headlines: tuple[str, ...] = ()
    types: tuple[str, ...] = ()
    changed_flags: tuple[bool, ...] = field(default=(), repr=False)

    @property
    def is_event(self) -> bool:
        return self.n_articles > 0


@dataclass(frozen=True)
class Trade:
    day: date
    symbol: str
    kind: str
    side: str
    g: float
    shares: int
    daily_open: float
    entry_open: float
    entry_fill: float
    exit_price: float
    gross_pnl: float
    slippage_cost: float
    commission: float
    net_pnl: float
    net_ret_bps: float
    types: tuple[str, ...] = ()


@dataclass(frozen=True)
class DayOutcome:
    day: date
    trades: tuple[Trade, ...]
    skips: tuple[tuple[str, str], ...]               # (symbol, reason)
    n_events: int
    n_controls: int

    @property
    def net_pnl(self) -> float:
        return sum(t.net_pnl for t in self.trades)


def make_trade(c: Candidate, entry_open: float, cfg: NewsGapConfig) -> Trade | None:
    shares = share_count(entry_open, cfg.slot_capital_usd)
    if shares < 1 or c.daily_close is None:
        return None
    side = SIDE_LONG if c.g > 0 else SIDE_SHORT
    sign = 1.0 if side == SIDE_LONG else -1.0
    gross = sign * shares * (c.daily_close - entry_open)             # at the un-slipped open
    slippage = shares * cfg.entry_slippage_cents / 100.0               # entry only
    commission = 2.0 * shares * cfg.commission_per_share               # entry fill and exit fill
    net = gross - slippage - commission
    return Trade(c.day, c.symbol, cfg.kind, side, c.g, shares, c.daily_open, entry_open,
                 entry_fill(entry_open, side, cfg.entry_slippage_cents), c.daily_close, gross, slippage, commission,
                 net, net / (shares * entry_open) * 1e4, c.types)


def simulate_day(day: date, picks: Sequence[Candidate], bar10: Mapping[str, float | None], cfg: NewsGapConfig,
                 n_events: int = 0, n_controls: int = 0) -> DayOutcome:
    """Trade one day's already-selected top-N picks. A skipped or filtered pick is NEVER replaced by the next
    ranked one. Entry uses only the open of the 10:00 bar (and, for the confirmation filter, the day's open)."""
    assert_not_holdout(day)
    trades: list[Trade] = []
    skips: list[tuple[str, str]] = []
    for c in picks:
        side = SIDE_LONG if c.g > 0 else SIDE_SHORT
        if side == SIDE_SHORT and not cfg.allow_shorts:
            skips.append((c.symbol, "short_excluded"))
            continue
        o10 = bar10.get(c.symbol)
        if o10 is None or o10 != o10:
            skips.append((c.symbol, "no_10_00_bar"))
            continue
        if cfg.require_confirmation:
            move = o10 - c.daily_open
            if not ((move > 0 and c.g > 0) or (move < 0 and c.g < 0)):
                skips.append((c.symbol, "not_confirmed"))
                continue
        t = make_trade(c, o10, cfg)
        if t is None:
            skips.append((c.symbol, "no_daily_close" if c.daily_close is None else "zero_shares"))
            continue
        trades.append(t)
    return DayOutcome(day, tuple(trades), tuple(skips), n_events, n_controls)


# ========================================================================= candidates from the data
@dataclass
class DayPlan:
    day: date
    prev_day: date
    n_gap_candidates: int
    events: list[Candidate]                    # ALL events of the day (pre top-N), ranked
    controls: list[Candidate]                  # ALL no-news gaps of the day (pre top-N), ranked

    def picks(self, cfg: NewsGapConfig) -> list[Candidate]:
        return (self.events if cfg.kind == "news" else self.controls)[:cfg.top_n]


def build_plans(daily_panels: dict[str, pd.DataFrame], cal: list[date], window_days: list[date],
                symbols: Sequence[str], news: NewsIndex, etfs: frozenset[str], cfg: NewsGapConfig = NewsGapConfig(),
                *, log: Callable[[str], None] | None = None) -> list[DayPlan]:
    """Universe -> qualifying gap -> overnight news -> ranked events and controls, for every window day.

    `daily_panels` are day x symbol panels (open/close/volume) indexed by `cal` (the trading calendar). A cheap
    vectorised pre-filter on open and |g| (with a margin, so it can never reject what the pure functions would
    accept) leaves the few hundred names a day to which the pure `in_universe` / `gap_qualifies` / news rules
    are applied."""
    pos = {d: i for i, d in enumerate(cal)}
    opn = daily_panels["open"].to_numpy(dtype=float)
    cls = daily_panels["close"].to_numpy(dtype=float)
    vol = daily_panels["volume"].to_numpy(dtype=float)
    n_adv = cfg.dollar_volume_days
    plans: list[DayPlan] = []
    for k, D in enumerate(window_days):
        i = pos[D]
        if i < n_adv:
            raise ValueError(f"{D} has fewer than {n_adv} prior trading days of data")
        prev_day = cal[i - 1]
        o, pc = opn[i], cls[i - 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            g = o / pc - 1.0
        pre = np.isfinite(o) & np.isfinite(pc) & (o >= cfg.min_open - 1e-9) & (np.abs(g) >= cfg.min_abs_gap - 1e-9)
        w_start, w_end = overnight_window(prev_day, D, cfg)
        events: list[Candidate] = []
        controls: list[Candidate] = []
        n_gap = 0
        for j in np.nonzero(pre)[0]:
            sym = symbols[j]
            if sym in etfs:
                continue
            adv = avg_dollar_volume(cls[i - n_adv:i, j].tolist(), vol[i - n_adv:i, j].tolist(), n_adv)
            if not in_universe(float(o[j]), float(pc[j]), adv, cfg):
                continue
            gj = gap_return(float(o[j]), float(pc[j]))
            if not gap_qualifies(gj, cfg):
                continue
            n_gap += 1
            hits = news.matching(sym, w_start, w_end)
            dc = cls[i, j]
            cand = Candidate(D, sym, gj, float(o[j]), float(pc[j]), None if not np.isfinite(dc) else float(dc), float(adv),
                             len(hits), tuple(h[0] for h in hits), tuple(h[1] for h in hits),
                             classify_headlines(h[1] for h in hits) if hits else (),
                             tuple(h[2] for h in hits))
            (events if hits else controls).append(cand)
        plans.append(DayPlan(D, prev_day, n_gap, rank_candidates(events, 10**9), rank_candidates(controls, 10**9)))
        if log and (k + 1) % 100 == 0:
            log(f"candidates: {k + 1}/{len(window_days)} days")
    return plans


# ======================================================================================= metrics
def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _block(trades: list[Trade]) -> dict:
    n = len(trades)
    if not n:
        return {"trades": 0, "net_pnl": 0.0, "hit_ratio": None, "avg_net_bps": None}
    return {"trades": n, "net_pnl": sum(t.net_pnl for t in trades),
            "hit_ratio": sum(1 for t in trades if t.net_pnl > 0) / n,
            "avg_net_bps": sum(t.net_ret_bps for t in trades) / n}


def compute_metrics(outcomes: list[DayOutcome], cfg: NewsGapConfig) -> dict:
    """Everything the pre-registration says a run reports, for ONE variant (sections 3 and 8; items 26-27)."""
    sleeve = cfg.sleeve_capital_usd
    outs = sorted(outcomes, key=lambda o: o.day)
    daily = [o.net_pnl for o in outs]
    rets = [x / sleeve for x in daily]
    n = len(rets)
    mean = statistics.fmean(rets) if rets else None
    std = statistics.stdev(rets) if n > 1 else None
    sharpe = (mean / std * math.sqrt(252)) if (mean is not None and std and std > 0) else None
    tstat = (mean / (std / math.sqrt(n))) if (mean is not None and std and std > 0) else None
    equity, peak, max_dd, max_dd_pct = sleeve, sleeve, 0.0, 0.0
    for x in daily:
        equity += x
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        max_dd_pct = max(max_dd_pct, (peak - equity) / peak if peak > 0 else 0.0)
    trades = [t for o in outs for t in o.trades]
    by_year = {}
    for yr in sorted({o.day.year for o in outs}):
        yo = [o for o in outs if o.day.year == yr]
        yt = [t for o in yo for t in o.trades]
        by_year[str(yr)] = {"days": len(yo), "trades": len(yt), "gross_pnl": sum(t.gross_pnl for t in yt),
                            "net_pnl": sum(t.net_pnl for t in yt)}
    skips = Counter(r for o in outs for _s, r in o.skips)
    ev = [o.n_events for o in outs]
    return {
        "days": n, "net_pnl": sum(daily), "gross_pnl": sum(t.gross_pnl for t in trades),
        "slippage_cost": sum(t.slippage_cost for t in trades), "commission": sum(t.commission for t in trades),
        "mean_daily_return_pct": None if mean is None else mean * 100, "std_daily_return_pct": None if std is None else std * 100,
        "sharpe_net": sharpe, "t_stat_mean_daily_return": tstat,
        "max_drawdown_usd": max_dd, "max_drawdown_pct_of_peak": max_dd_pct * 100,
        "by_year": by_year, "trades": len(trades),
        "hit_ratio": (sum(1 for t in trades if t.net_pnl > 0) / len(trades)) if trades else None,
        "avg_net_bps": (sum(t.net_ret_bps for t in trades) / len(trades)) if trades else None,
        "long": _block([t for t in trades if t.side == SIDE_LONG]),
        "short": _block([t for t in trades if t.side == SIDE_SHORT]),
        "days_with_no_event": sum(1 for x in ev if x == 0),
        "days_with_no_trade": sum(1 for o in outs if not o.trades),
        "events_per_day_mean": statistics.fmean(ev) if ev else None,
        "events_per_day_median": statistics.median(ev) if ev else None,
        "trades_per_day_mean": (len(trades) / n) if n else None,
        "skips": dict(sorted(skips.items())),
    }


def go_no_go(m: dict) -> dict:
    """The pre-registered mechanical verdict, on the primary variant only."""
    sharpe = m.get("sharpe_net")
    pnl = {y: m.get("by_year", {}).get(str(y), {}).get("net_pnl") for y in GO_YEARS}
    checks = {f"net_sharpe >= {GO_MIN_SHARPE}": sharpe is not None and sharpe >= GO_MIN_SHARPE,
              **{f"net_pnl_{y} > 0": (pnl[y] is not None and pnl[y] > 0) for y in GO_YEARS}}
    return {"verdict": "GO" if all(checks.values()) else "NO-GO", "checks": checks, "net_sharpe": sharpe,
            "net_pnl_by_year": pnl}


def welch_t(a: Sequence[float], b: Sequence[float]) -> dict:
    """Welch's two-sample t on per-trade values: (m1 - m2) / sqrt(s1^2/n1 + s2^2/n2), sample variances."""
    n1, n2 = len(a), len(b)
    out = {"n_news": n1, "n_control": n2, "mean_news": None, "mean_control": None, "difference": None, "t_stat": None}
    if n1:
        out["mean_news"] = statistics.fmean(a)
    if n2:
        out["mean_control"] = statistics.fmean(b)
    if n1 and n2:
        out["difference"] = out["mean_news"] - out["mean_control"]
    if n1 > 1 and n2 > 1:
        se2 = statistics.variance(a) / n1 + statistics.variance(b) / n2
        if se2 > 0:
            out["t_stat"] = out["difference"] / math.sqrt(se2)
    return out


def type_breakdown(trades: Iterable[Trade]) -> dict[str, dict]:
    """Per event type (an event with several types counts once in each): trades, average net bps, hit ratio."""
    groups: dict[str, list[Trade]] = {t: [] for t in EVENT_TYPES}
    for tr in trades:
        for ty in tr.types or ("other",):
            groups[ty].append(tr)
    return {ty: _block(v) for ty, v in groups.items()}


def largest_gaps(plans: list[DayPlan], outcomes: list[DayOutcome], cfg: NewsGapConfig = NewsGapConfig(), n: int = 10) -> list[dict]:
    """The `n` primary picks (the selected top-N events) with the largest |g|, with their net P&L (0 and a skip
    reason if they did not trade). Disclosure only (raw prices make split days look like gaps)."""
    traded = {(t.day, t.symbol): t for o in outcomes for t in o.trades}
    skipped = {(o.day, s): r for o in outcomes for s, r in o.skips}
    rows = []
    for p in plans:
        for c in p.picks(cfg):
            t = traded.get((c.day, c.symbol))
            rows.append({"day": c.day.isoformat(), "symbol": c.symbol, "g_pct": c.g * 100,
                         "side": SIDE_LONG if c.g > 0 else SIDE_SHORT, "traded": t is not None,
                         "net_pnl": t.net_pnl if t else 0.0, "skip_reason": None if t else skipped.get((c.day, c.symbol))})
    rows.sort(key=lambda r: (-abs(r["g_pct"]), r["symbol"], r["day"]))
    return rows[:n]


def news_stats(news: pd.DataFrame, plans: list[DayPlan]) -> dict:
    """Data statistics (pre-registration item 28)."""
    n = len(news)
    sym_counts = news["symbols"].map(len) if n else pd.Series(dtype=int)
    changed = (news["updated_at"] != news["created_at"]) if n else pd.Series(dtype=bool)
    matched = {}
    for p in plans:
        for c in p.events:
            for aid, ch in zip(c.article_ids, c.changed_flags):
                matched[aid] = ch
    return {"articles": n, "by_source": dict(news["source"].value_counts().items()) if n else {},
            "articles_at_most_2_symbols": int(((sym_counts >= 1) & (sym_counts <= 2)).sum()) if n else 0,
            "articles_empty_symbols": int((sym_counts == 0).sum()) if n else 0,
            "share_updated_differs_all": float(changed.mean()) if n else None,
            "matched_articles": len(matched),
            "share_updated_differs_matched": (sum(matched.values()) / len(matched)) if matched else None}


# ========================================================================================== data
def load_bar10(layer, picks_by_day: Mapping[date, Iterable[str]], *, offline: bool = False) -> dict[tuple[date, str], float]:
    """The OPEN of the SIP one-minute bar stamped 10:00, for the symbols and days that need it (one request
    series per day, window 10:00-10:01)."""
    out: dict[tuple[date, str], float] = {}
    for d in sorted(picks_by_day):
        syms = sorted(set(picks_by_day[d]))
        if not syms:
            continue
        df = layer.get_bars(syms, d, d, "1Min", "sip", window=("10:00", "10:01"), offline=offline)
        if df.empty:
            continue
        mins = df["ts"].dt.hour * 60 + df["ts"].dt.minute
        for sym, m, o in zip(df["symbol"].tolist(), mins.tolist(), df["open"].tolist()):
            if m == 10 * 60:
                out[(d, sym)] = float(o)
    return out


def load_daily_panels(layer, symbols: Sequence[str], cal: list[date], end: date, *, batch: int = 1000,
                      offline: bool = False, log: Callable[[str], None] | None = None) -> tuple[dict[str, pd.DataFrame], list[str]]:
    frames = []
    for i in range(0, len(symbols), batch):
        frames.append(layer.get_bars(list(symbols[i:i + batch]), DATA_FROM, end, "1Day", "sip", offline=offline))
        if log:
            log(f"SIP daily bars: {min(i + batch, len(symbols))}/{len(symbols)} symbols")
    frames = [f for f in frames if not f.empty]
    daily = pd.concat(frames, ignore_index=True)
    panels = panels_from_daily(daily)
    out = {k: panels[k].reindex(index=cal) for k in ("open", "close", "volume")}
    return out, list(out["close"].columns)


@dataclass
class StudyData:
    window_days: list[date]
    plans: list[DayPlan]
    bar10: dict[tuple[date, str], float]
    news: pd.DataFrame
    symbols_considered: int
    n_etfs_excluded: int


def prepare(layer, start: str, end: str, cfg: NewsGapConfig = NewsGapConfig(), *, offline: bool = False,
            etfs: frozenset[str] | None = None, log: Callable[[str], None] | None = None) -> StudyData:
    s_day, e_day = date.fromisoformat(start), date.fromisoformat(end)
    assert_not_holdout(s_day, e_day)
    etfs = load_etf_list().symbols if etfs is None else etfs
    log = log or (lambda m: None)
    cal = layer.trading_days(DATA_FROM, e_day, offline=offline)
    window = [d for d in cal if s_day <= d <= e_day]
    news = layer.get_news(date.fromisoformat(NEWS_FROM), e_day, offline=offline)
    log(f"news: {len(news):,} articles")
    assets = layer.get_assets(offline=offline)
    all_equities = equity_symbols(assets)
    symbols = [s for s in all_equities if s not in etfs]
    panels, cols = load_daily_panels(layer, symbols, cal, e_day, offline=offline, log=log)
    plans = build_plans(panels, cal, window, cols, NewsIndex(news, cfg), etfs, cfg, log=log)
    need = {p.day: [c.symbol for c in p.picks(cfg)] + [c.symbol for c in p.picks(cfg.replace(kind="control"))] for p in plans}
    log(f"one-minute 10:00 bars for {sum(len(set(v)) for v in need.values()):,} symbol-days")
    bar10 = load_bar10(layer, need, offline=offline)
    return StudyData(window, plans, bar10, news, len(symbols), len(all_equities) - len(symbols))


def run_variants(data: StudyData, variants: Iterable[Variant], cfg: NewsGapConfig = NewsGapConfig()) -> dict[str, list[DayOutcome]]:
    if data.window_days:
        assert_not_holdout(*data.window_days)
    out: dict[str, list[DayOutcome]] = {}
    for v in variants:
        vcfg = v.config(cfg)
        res = []
        for p in data.plans:
            picks = p.picks(vcfg)
            bar = {c.symbol: data.bar10.get((p.day, c.symbol)) for c in picks}
            res.append(simulate_day(p.day, picks, bar, vcfg, len(p.events), len(p.controls)))
        out[v.name] = res
    return out


# =================================================================================== run and output
def validity(start: str, end: str, cfg: NewsGapConfig, variants, prereg: dict) -> list[str]:
    """Reasons this run is NOT the pre-registered one (empty list = valid for a verdict)."""
    why = []
    if (start, end) != (PREREG_START, PREREG_END):
        why.append(f"window {start}..{end} is not the pre-registered {PREREG_START}..{PREREG_END}")
    if cfg != NewsGapConfig():
        why.append(f"config differs from the pre-registered defaults: {cfg.diff_from_default()}")
    if tuple(v.name for v in variants) != tuple(v.name for v in ALL_VARIANTS):
        why.append("not the full pre-registered variant set")
    if prereg.get("unchanged") is False:
        why.append("the pre-registration's rule text no longer matches its first-commit hash")
    elif prereg.get("unchanged") is None:
        why.append("the pre-registration's first-commit hash could not be verified (no git history)")
    return why


def _f(x, nd=2, pct=False, usd=False) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    s = f"{x:,.{nd}f}"
    return ("$" + s if usd else s) + ("%" if pct else "")


def headline_table(metrics: dict[str, dict], order: list[str]) -> str:
    cols = ["Variant", "Net Sharpe", "t-stat", "Net P&L", "2024 net", "2025 net", "Max DD", "Trades", "Hit ratio",
            "Avg net bps / trade", "Long / Short trades", "Days no event"]
    lines = ["| " + " | ".join(cols) + " |", "|" + " --- |" * len(cols)]
    for name in order:
        m = metrics[name]
        by = m["by_year"]
        hr = None if m["hit_ratio"] is None else m["hit_ratio"] * 100
        lines.append("| " + " | ".join([
            f"**{name}**" if name == "primary" else name, _f(m["sharpe_net"], 2), _f(m["t_stat_mean_daily_return"], 2),
            _f(m["net_pnl"], 0, usd=True), _f(by.get("2024", {}).get("net_pnl"), 0, usd=True),
            _f(by.get("2025", {}).get("net_pnl"), 0, usd=True),
            f"{_f(m['max_drawdown_usd'], 0, usd=True)} ({_f(m['max_drawdown_pct_of_peak'], 1, pct=True)})",
            f"{m['trades']:,}", _f(hr, 1, pct=True), _f(m["avg_net_bps"], 2),
            f"{m['long']['trades']:,} / {m['short']['trades']:,}", f"{m['days_with_no_event']}"]) + " |")
    return "\n".join(lines)


def variant_detail(name: str, m: dict) -> str:
    hr = None if m["hit_ratio"] is None else m["hit_ratio"] * 100
    out = [f"### {name}", "",
           f"* Net P&L {_f(m['net_pnl'], 0, usd=True)} (gross {_f(m['gross_pnl'], 0, usd=True)}, slippage "
           f"{_f(m['slippage_cost'], 0, usd=True)}, commission {_f(m['commission'], 0, usd=True)}); {m['trades']:,} trades over "
           f"{m['days']} trading days ({_f(m['trades_per_day_mean'], 2)} a day; {m['days_with_no_trade']} days without a trade, "
           f"{m['days_with_no_event']} without an event); skips: {m['skips'] or 'none'}.",
           f"* Net Sharpe {_f(m['sharpe_net'], 3)}; t-statistic of the mean daily return {_f(m['t_stat_mean_daily_return'], 2)}; "
           f"max drawdown {_f(m['max_drawdown_usd'], 0, usd=True)} ({_f(m['max_drawdown_pct_of_peak'], 1, pct=True)} of peak).",
           f"* Hit ratio {_f(hr, 1, pct=True)}; average net return {_f(m['avg_net_bps'], 2)} bps per trade."]
    for side in ("long", "short"):
        b = m[side]
        hs = None if b["hit_ratio"] is None else b["hit_ratio"] * 100
        out.append(f"* {side.capitalize()}: {b['trades']:,} trades, net {_f(b['net_pnl'], 0, usd=True)}, hit ratio "
                   f"{_f(hs, 1, pct=True)}, average {_f(b['avg_net_bps'], 2)} bps.")
    out += ["", "| Year | Days | Trades | Gross P&L | Net P&L |", "| --- | --- | --- | --- | --- |"]
    out += [f"| {y} | {b['days']} | {b['trades']:,} | {_f(b['gross_pnl'], 0, usd=True)} | {_f(b['net_pnl'], 0, usd=True)} |"
            for y, b in m["by_year"].items()]
    return "\n".join(out) + "\n"


def render_comparison(cmp: dict, news_m: dict, ctrl_m: dict) -> str:
    def row(label, m):
        hr = None if m["hit_ratio"] is None else m["hit_ratio"] * 100
        return (f"| {label} | {m['trades']:,} | {_f(m['avg_net_bps'], 2)} | {_f(hr, 1, pct=True)} | {_f(m['net_pnl'], 0, usd=True)} | "
                f"{_f(m['sharpe_net'], 2)} | {_f(m['t_stat_mean_daily_return'], 2)} |")
    return ("## News vs. no-news gaps (diagnostic a): does news add information?\n\n"
            "The identical rule on qualifying gaps with **no** overnight news (top 5 by |g|), side by side.\n\n"
            "| Gaps | Trades | Avg net bps / trade | Hit ratio | Net P&L | Net Sharpe | t-stat (mean daily return) |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            + row("**With overnight news** (primary)", news_m) + "\n" + row("No overnight news (control)", ctrl_m) + "\n\n"
            f"**Difference in average net return per trade (news − no news): {_f(cmp['difference'], 2)} bps**, Welch t-statistic "
            f"**{_f(cmp['t_stat'], 2)}** (n = {cmp['n_news']:,} news trades, {cmp['n_control']:,} control trades; means "
            f"{_f(cmp['mean_news'], 2)} and {_f(cmp['mean_control'], 2)} bps). Trades on the same day share the market, so the "
            "t-statistic treats dependent observations as independent and overstates precision (pre-registration item 25).\n")


def render_types(bd: dict[str, dict]) -> str:
    rows = ["## Event types (diagnostic e; reporting only)", "",
            "Primary trades by the type of their overnight headlines (fixed keyword list; an event can carry several types and is counted in each).", "",
            "| Type | Trades | Avg net bps | Hit ratio |", "| --- | --- | --- | --- |"]
    for ty in EVENT_TYPES:
        b = bd[ty]
        hr = None if b["hit_ratio"] is None else b["hit_ratio"] * 100
        rows.append(f"| {ty} | {b['trades']:,} | {_f(b['avg_net_bps'], 2)} | {_f(hr, 1, pct=True)} |")
    return "\n".join(rows) + "\n"


def render_stats(st: dict, ev_stats: dict, big: list[dict], skips: dict) -> str:
    big_rows = ["| Day | Symbol | g | Side | Traded | Net P&L |", "| --- | --- | --- | --- | --- | --- |"]
    big_rows += [f"| {r['day']} | {r['symbol']} | {r['g_pct']:+.1f}% | {r['side']} | "
                 f"{'yes' if r['traded'] else 'no (' + str(r['skip_reason']) + ')'} | {_f(r['net_pnl'], 2, usd=True)} |" for r in big]
    sh_all = None if st["share_updated_differs_all"] is None else st["share_updated_differs_all"] * 100
    sh_m = None if st["share_updated_differs_matched"] is None else st["share_updated_differs_matched"] * 100
    return ("## Data statistics\n\n"
            f"* Articles fetched (2023-12-29 .. 2025-12-31): **{st['articles']:,}**; by source {st['by_source']}; with 1–2 symbols "
            f"{st['articles_at_most_2_symbols']:,}; with an empty symbols list {st['articles_empty_symbols']:,}.\n"
            f"* Share of articles whose `updated_at` differs from `created_at`: **{_f(sh_all, 1, pct=True)}** of all articles; "
            f"**{_f(sh_m, 1, pct=True)}** of the {st['matched_articles']:,} overnight articles that matched an event "
            "(only `created_at` is ever used for matching).\n"
            f"* Events per day (before the top-5 cut, over all trading days): mean {_f(ev_stats['events_per_day_mean'], 2)}, median "
            f"{_f(ev_stats['events_per_day_median'], 1)}; days with no event {ev_stats['days_with_no_event']}; no-news gaps per day: mean "
            f"{_f(ev_stats['controls_per_day_mean'], 2)}, median {_f(ev_stats['controls_per_day_median'], 1)}.\n"
            f"* Skips (primary): {skips or 'none'}.\n\n"
            "### The ten primary events with the largest |g| (raw prices: splits and reverse splits look like gaps)\n\n"
            + "\n".join(big_rows) + "\n")


def render_summary_md(header: dict, metrics: dict, verdict: dict | None, cmp: dict, types: dict, stats: dict,
                      ev_stats: dict, big: list[dict]) -> str:
    order = list(header["variants"])
    top = [f"# News event study results — run `{header['run_id']}`", ""]
    if not header["valid_for_verdict"]:
        top += ["> **NOT A PRE-REGISTERED RUN — no go/no-go verdict.** " + "; ".join(header["invalid_reasons"]) + ".", ""]
    if verdict:
        top += [f"## Mechanical verdict (primary): **{verdict['verdict']}**", "", "| Pre-registered check | Result |", "| --- | --- |"]
        top += [f"| {k} | {'PASS' if ok else 'FAIL'} |" for k, ok in verdict["checks"].items()]
        top += ["", f"Net Sharpe {_f(verdict['net_sharpe'], 3)}; net P&L "
                + ", ".join(f"{y}: {_f(p, 0, usd=True)}" for y, p in verdict["net_pnl_by_year"].items()) + ".", ""]
    top += [render_comparison(cmp, metrics["primary"], metrics["control_no_news"]),
            "## Headline table", "", headline_table(metrics, order), "", "## Variant detail", ""]
    top += [variant_detail(n, metrics[n]) for n in order]
    top += [render_types(types), render_stats(stats, ev_stats, big, metrics["primary"]["skips"])]
    return "\n".join(top)


def _csv(x):
    if isinstance(x, float):
        return f"{x:.8f}"
    if isinstance(x, date):
        return x.isoformat()
    if isinstance(x, tuple):
        return "|".join(str(i) for i in x)
    return x


TRADE_COLUMNS = ["day", "symbol", "kind", "side", "g", "shares", "daily_open", "entry_open", "entry_fill", "exit_price",
                 "gross_pnl", "slippage_cost", "commission", "net_pnl", "net_ret_bps", "types"]


def write_run(out_dir: Path, header: dict, results: dict[str, list[DayOutcome]], metrics: dict, verdict: dict | None,
              cmp: dict, types: dict, stats: dict, ev_stats: dict, big: list[dict], data: StudyData) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def _w(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)

    _w(out_dir / "header.json", json.dumps(dict(header, metrics_verdict=verdict), indent=2, sort_keys=True, default=str) + "\n")
    _w(out_dir / "summary.md", render_summary_md(header, metrics, verdict, cmp, types, stats, ev_stats, big))
    _w(out_dir / "comparison.json", json.dumps(cmp, indent=2, sort_keys=True) + "\n")
    _w(out_dir / "event_types.json", json.dumps(types, indent=2, sort_keys=True) + "\n")
    _w(out_dir / "data_stats.json", json.dumps({"news": stats, "events": ev_stats, "largest_gaps": big}, indent=2, sort_keys=True, default=str) + "\n")
    with (out_dir / "picks.csv").open("w", newline="", encoding="utf-8") as fh:        # every selected pick, traded or not
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["day", "kind", "rank", "symbol", "g", "adv", "n_articles", "types"])
        for p in data.plans:
            for kind, cs in (("news", p.events[:5]), ("control", p.controls[:5])):
                for r, c in enumerate(cs, 1):
                    w.writerow([p.day.isoformat(), kind, r, c.symbol, f"{c.g:.8f}", f"{c.adv:.2f}", c.n_articles, "|".join(c.types)])
    written.append(out_dir / "picks.csv")
    with (out_dir / "daily_counts.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["day", "gap_candidates", "events", "controls"])
        for p in data.plans:
            w.writerow([p.day.isoformat(), p.n_gap_candidates, len(p.events), len(p.controls)])
    written.append(out_dir / "daily_counts.csv")
    for name, outs in results.items():
        vd = out_dir / name
        vd.mkdir(exist_ok=True)
        _w(vd / "metrics.json", json.dumps(metrics[name], indent=2, sort_keys=True, default=str) + "\n")
        with (vd / "trades.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(TRADE_COLUMNS)
            for o in outs:
                for t in o.trades:
                    d = dataclasses.asdict(t)
                    w.writerow([_csv(d[c]) for c in TRADE_COLUMNS])
        written.append(vd / "trades.csv")
        with (vd / "daily.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["day", "events", "controls", "trades", "skips", "net_pnl"])
            for o in outs:
                w.writerow([o.day.isoformat(), o.n_events, o.n_controls, len(o.trades), len(o.skips), f"{o.net_pnl:.8f}"])
        written.append(vd / "daily.csv")
    return written


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def analyse(data: StudyData, variants: tuple[Variant, ...] = ALL_VARIANTS, cfg: NewsGapConfig = NewsGapConfig()) -> dict:
    """Run every variant and gather the reported diagnostics. No files are written here."""
    results = run_variants(data, variants, cfg)
    metrics = {v.name: compute_metrics(results[v.name], v.config(cfg)) for v in variants}
    news_trades = [t.net_ret_bps for o in results["primary"] for t in o.trades]
    ctrl_trades = [t.net_ret_bps for o in results["control_no_news"] for t in o.trades] if "control_no_news" in results else []
    ev = [len(p.events) for p in data.plans]
    co = [len(p.controls) for p in data.plans]
    ev_stats = {"events_per_day_mean": statistics.fmean(ev) if ev else None, "events_per_day_median": statistics.median(ev) if ev else None,
                "days_with_no_event": sum(1 for x in ev if x == 0),
                "controls_per_day_mean": statistics.fmean(co) if co else None, "controls_per_day_median": statistics.median(co) if co else None}
    return {"results": results, "metrics": metrics, "comparison": welch_t(news_trades, ctrl_trades),
            "types": type_breakdown(t for o in results["primary"] for t in o.trades),
            "stats": news_stats(data.news, data.plans), "events": ev_stats,
            "largest_gaps": largest_gaps(data.plans, results["primary"], cfg)}


def cmd_run(args) -> int:
    cfg = NewsGapConfig()
    assert_not_holdout(date.fromisoformat(args.start), date.fromisoformat(args.end))
    layer = DataLayer(args.cache_dir)
    t0 = time.time()
    data = prepare(layer, args.start, args.end, cfg, offline=args.offline, log=_log)
    res = analyse(data, ALL_VARIANTS, cfg)
    prereg = provenance.prereg_info(path=PREREG_PATH)
    why = validity(args.start, args.end, cfg, ALL_VARIANTS, prereg)
    commit = provenance.code_commit()
    now = datetime.now(timezone.utc)
    run_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{(commit['sha'] or 'nogit')[:7]}"
    verdict = go_no_go(res["metrics"]["primary"]) if not why else None
    header = {
        "run_id": run_id, "created_utc": now.isoformat(), "spec_version": cfg.spec_version,
        "window": {"start": args.start, "end": args.end, "trading_days": len(data.window_days), "news_from": NEWS_FROM, "data_from": DATA_FROM},
        "config": dataclasses.asdict(cfg), "config_diff_from_default": cfg.diff_from_default(),
        "variants": {v.name: dict(v.overrides) for v in ALL_VARIANTS}, "code_commit": commit, "preregistration": prereg,
        "valid_for_verdict": not why, "invalid_reasons": why,
        "symbols_considered": data.symbols_considered, "etf_symbols_excluded_from_assets": data.n_etfs_excluded,
        "data_layer": layer.cache_stats(), "wall_time_s": round(time.time() - t0, 1),
        "holdout": "2026 was never read (this code refuses any bar or news date on/after 2026-01-01)",
    }
    out = Path(args.out) / run_id
    write_run(out, header, res["results"], res["metrics"], verdict, res["comparison"], res["types"], res["stats"], res["events"],
              res["largest_gaps"], data)
    summary = (out / "summary.md").read_text(encoding="utf-8")
    if args.review_file:
        review = Path(args.review_file)
        review.parent.mkdir(parents=True, exist_ok=True)
        pre = header["preregistration"]
        review.write_text(
            f"<!-- generated by backtest.news_gap from {run_id}; do not edit by hand -->\n{summary.rstrip()}\n\n## Run provenance\n\n"
            f"* Run `{run_id}` on code commit `{commit['sha']}` (tree dirty: {commit['dirty']}); spec_version `{cfg.spec_version}`.\n"
            f"* Pre-registration `{pre['path']}` first committed in `{pre['first_commit']}`; rule text unchanged since: {pre['unchanged']}.\n"
            f"* Window {args.start}..{args.end}, {len(data.window_days)} trading days; news from {NEWS_FROM}; the 2026 holdout was never read.\n"
            f"* Universe: {data.symbols_considered:,} non-ETF US equities considered ({data.n_etfs_excluded:,} ETF symbols excluded from the assets list).\n"
            f"* Data layer: {header['data_layer'].get('requests_made', 0):,} API requests in this run, {header['data_layer'].get('news_articles', 0):,} cached articles; wall time {header['wall_time_s'] / 60:.1f} min.\n"
            f"* Full outputs: `{out}` (gitignored).\n", encoding="utf-8")
    print(f"run {run_id}: {'verdict ' + verdict['verdict'] if verdict else 'NOT a pre-registered run'}; outputs in {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backtest.news_gap", description=__doc__.split("\n")[0])
    ap.add_argument("--cache-dir", help="override data/cache")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the pre-registered news event study (all variants)")
    r.add_argument("--start", default=PREREG_START)
    r.add_argument("--end", default=PREREG_END)
    r.add_argument("--out", default=str(DEFAULT_OUT))
    r.add_argument("--review-file", help="also write the results markdown here (.review/news-gap-results.md)")
    r.add_argument("--offline", action="store_true", help="fail on a cache miss instead of calling the API")
    args = ap.parse_args(argv)
    try:
        return cmd_run(args)
    except (CacheMissError, HoldoutError, MissingCredentialsError, AlpacaDataError, EtfListMissingError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

