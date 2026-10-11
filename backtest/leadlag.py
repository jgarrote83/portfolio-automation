"""Global lead-lag study: do Asia, Europe and the UK predict the US day? Pure logic (no network, no files).

Rules, thresholds and the go/no-go are pre-registered in `docs/specs/Global_LeadLag_Preregistration.md` (locked at
its first commit; `tests/test_leadlag.py` pins `LeadLagConfig()` and `GROUPS` to it). Per group, a foreign overnight
signal s trades the US instrument at the daily-bar open and closes it at the daily-bar close, $25,000 each:

  Asia member return   = close(local session dated D) / close(previous session) - 1     (closes before 09:30 ET on D)
  Europe/UK member     = open(D) / close(previous session) - 1                          (a European close on D is NEVER used)
  s                    = mean of the available regional means (Asia, Europe/UK), equal weight per region

`backtest.leadlag_run` does the I/O (cache, US bars, reports); this module is what the tests exercise.
"""
from __future__ import annotations

import dataclasses
import math
import random
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import numpy as np

from .foreign import MemberRows

SPEC_VERSION = "leadlag-1.0"
PREREG_PATH = "docs/specs/Global_LeadLag_Preregistration.md"
PREREG_START, PREREG_END = "2024-01-02", "2025-12-31"
LONG, SHORT = "long", "short"
ASIA, EUROPE = "asia", "europe"
ET = ZoneInfo("America/New_York")


# ================================================================================== config and groups
@dataclass(frozen=True)
class LeadLagConfig:
    spec_version: str = SPEC_VERSION
    window_start: str = PREREG_START
    window_end: str = PREREG_END
    data_from: str = "2023-12-01"
    sleeve_capital_usd: float = 25_000.0
    commission_per_share: float = 0.0035
    slippage_cents: float = 0.0
    sensitivity_slippage_cents: float = 2.0
    min_net_sharpe: float = 1.0
    stale_open_max_share: float = 0.05
    agreement_tolerance: float = 0.001
    agreement_min_share: float = 0.9
    agreement_days_per_member: int = 30
    agreement_seed: int = 7
    verifier_trade_days: int = 30
    verifier_seed: int = 11

    def replace(self, **changes) -> "LeadLagConfig":
        return dataclasses.replace(self, **changes)

    def diff_from_default(self) -> dict:
        base = LeadLagConfig()
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self) if getattr(self, f.name) != getattr(base, f.name)}


@dataclass(frozen=True)
class GroupDef:
    name: str
    us: str
    asia: tuple[str, ...]
    europe: tuple[str, ...]

    @property
    def members(self) -> tuple[str, ...]:
        return self.asia + self.europe

    def region_of(self, symbol: str) -> str:
        return ASIA if symbol in self.asia else EUROPE


GROUPS: tuple[GroupDef, ...] = (
    GroupDef("Broad", "SPY", ("^N225", "^HSI", "^KS11", "^TWII", "^AXJO"), ("^GDAXI", "^FCHI", "^STOXX50E", "^FTSE")),
    GroupDef("Semis", "SMH", ("2330.TW", "005930.KS", "8035.T"), ("ASML.AS", "IFX.DE")),
    GroupDef("Banks", "XLF", ("8306.T", "0005.HK"), ("HSBA.L", "BNP.PA", "DBK.DE")),
    GroupDef("Energy", "XLE", (), ("SHEL.L", "BP.L", "TTE.PA")),
)
ALL_MEMBERS: tuple[str, ...] = tuple(m for g in GROUPS for m in g.members)
US_INSTRUMENTS: tuple[str, ...] = tuple(g.us for g in GROUPS)


@dataclass(frozen=True)
class Variant:
    """The primary rule or ONE pre-registered change to it (sections 2 and 4c)."""
    name: str
    signal: str = "s"                 # "s" (both regions), "asia" or "europe"
    long_only: bool = False
    slippage: str = "none"            # "none": cfg.slippage_cents; "sensitivity": cfg.sensitivity_slippage_cents


PRIMARY = Variant("primary")
SENSITIVITIES = (Variant("asia_only", signal="asia"), Variant("europe_only", signal="europe"),
                 Variant("long_only", long_only=True), Variant("slippage_2c", slippage="sensitivity"))
ALL_VARIANTS = (PRIMARY,) + SENSITIVITIES


def assert_not_holdout(*days: date) -> None:
    from .foreign import assert_not_holdout as _a
    _a(*days)


# ===================================================================================== session timing
@dataclass(frozen=True)
class Market:
    name: str
    tz: str
    kind: str                         # "close" for the Asian markets, "open" for Europe/UK
    hhmm: str                         # the conservative nominal local time of that event
    members: tuple[str, ...]
    close_hhmm: str | None = None     # Europe/UK only: the nominal close, shown to document why it is never used


MARKETS: tuple[Market, ...] = (
    Market("Tokyo", "Asia/Tokyo", "close", "15:30", ("^N225", "8035.T", "8306.T")),
    Market("Hong Kong", "Asia/Hong_Kong", "close", "16:10", ("^HSI", "0005.HK")),
    Market("Seoul", "Asia/Seoul", "close", "15:30", ("^KS11", "005930.KS")),
    Market("Taipei", "Asia/Taipei", "close", "13:30", ("^TWII", "2330.TW")),
    Market("Sydney", "Australia/Sydney", "close", "16:20", ("^AXJO",)),
    Market("London", "Europe/London", "open", "08:00", ("^FTSE", "HSBA.L", "SHEL.L", "BP.L"), "16:30"),
    Market("Frankfurt", "Europe/Berlin", "open", "09:00", ("^GDAXI", "IFX.DE", "DBK.DE"), "17:30"),
    Market("Paris", "Europe/Paris", "open", "09:00", ("^FCHI", "BNP.PA", "TTE.PA"), "17:30"),
    Market("Amsterdam", "Europe/Amsterdam", "open", "09:00", ("ASML.AS",), "17:30"),
    Market("Zurich (STOXX 50)", "Europe/Zurich", "open", "09:00", ("^STOXX50E",), "17:30"),
)


def _hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def us_open_utc(d: date) -> datetime:
    """09:30 ET on `d`, in UTC (DST-aware)."""
    return datetime.combine(d, time(9, 30), tzinfo=ET).astimezone(timezone.utc)


def local_event_utc(d: date, tz: str, hhmm: str) -> datetime:
    """`hhmm` local time on the calendar date `d` in `tz`, in UTC (DST-aware)."""
    return datetime.combine(d, _hhmm(hhmm), tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


def timing_rows(days: Sequence[date]) -> list[dict]:
    """Per market, over EVERY US date D: how many minutes before 09:30 ET on D the Asian close / the European open
    falls (the minimum, and the day it happens), the (US, local) UTC-offset combinations covered, and for Europe/UK how
    far AFTER the US open the close falls (why a European close is never used)."""
    rows = []
    for mk in MARKETS:
        margins, combos, worst = [], set(), None
        close_margins = []
        for d in days:
            ev, uso = local_event_utc(d, mk.tz, mk.hhmm), us_open_utc(d)
            m = (uso - ev).total_seconds() / 60.0
            margins.append(m)
            combos.add((datetime.combine(d, time(9, 30), tzinfo=ET).utcoffset(),
                        datetime.combine(d, _hhmm(mk.hhmm), tzinfo=ZoneInfo(mk.tz)).utcoffset()))
            if worst is None or m < worst[0]:
                worst = (m, d)
            if mk.close_hhmm:
                close_margins.append((local_event_utc(d, mk.tz, mk.close_hhmm) - uso).total_seconds() / 60.0)
        rows.append({"market": mk.name, "tz": mk.tz, "kind": mk.kind, "nominal_local": mk.hhmm, "members": list(mk.members),
                     "min_margin_minutes": min(margins) if margins else None, "worst_day": worst[1].isoformat() if worst else None,
                     "offset_combinations": len(combos), "days": len(margins),
                     "pass": bool(margins) and min(margins) > 0,
                     "nominal_close_local": mk.close_hhmm,
                     "close_after_us_open_min_minutes": min(close_margins) if close_margins else None,
                     "close_never_usable": (min(close_margins) > 0) if close_margins else None})
    return rows


# ============================================================================== member returns
def member_return(m: MemberRows, idx: Mapping[date, int], d: date, region: str) -> tuple[float | None, str]:
    """(return, "ok") or (None, reason) for member `m` on US date `d`.

    Asia: close(d) / close(previous row) - 1. Europe/UK: open(d) / close(previous row) - 1 (never the close on d).
    Unavailable when there is no row dated d, a needed price is missing or not positive, or a split has an ex-date in
    (previous row's date, d]."""
    i = idx.get(d)
    if i is None:
        return None, "no_row"
    if i == 0:
        return None, "no_previous_row"
    now = m.close[i] if region == ASIA else m.open[i]
    prev = m.close[i - 1]
    if not (_pos(now) and _pos(prev)):
        return None, "missing_price"
    for sd, _ratio in m.splits:
        if m.dates[i - 1] < sd <= d:
            return None, "split"
    return now / prev - 1.0, "ok"


def _pos(x: float) -> bool:
    return x is not None and not math.isnan(x) and x > 0


def member_returns(m: MemberRows, region: str, days: Sequence[date], excluded: str | None = None
                   ) -> tuple[dict[date, float], Counter]:
    """Every US date's return for one member, plus the count of unavailable days by reason. An excluded member
    is unavailable on every day (reason `excluded`)."""
    if excluded:
        return {}, Counter({"excluded": len(days)})
    idx = m.index()
    out: dict[date, float] = {}
    why: Counter = Counter()
    for d in days:
        r, reason = member_return(m, idx, d, region)
        if r is None:
            why[reason] += 1
        else:
            out[d] = r
    return out, why


# ===================================================================================== the signal
@dataclass(frozen=True)
class SignalDay:
    day: date
    s_asia: float | None
    s_europe: float | None
    s: float | None
    n_asia: int
    n_europe: int


def regional_mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def combine_regions(s_asia: float | None, s_europe: float | None) -> float | None:
    """Each region weighted equally; the one available region alone if the other is empty; None if neither."""
    parts = [x for x in (s_asia, s_europe) if x is not None]
    return statistics.fmean(parts) if parts else None


def group_signals(group: GroupDef, rets: Mapping[str, Mapping[date, float]], days: Sequence[date]) -> list[SignalDay]:
    out = []
    for d in days:
        a = [rets[s][d] for s in group.asia if s in rets and d in rets[s]]
        e = [rets[s][d] for s in group.europe if s in rets and d in rets[s]]
        sa, se = regional_mean(a), regional_mean(e)
        out.append(SignalDay(d, sa, se, combine_regions(sa, se), len(a), len(e)))
    return out


def pick_signal(sd: SignalDay, variant: Variant) -> float | None:
    return {"s": sd.s, "asia": sd.s_asia, "europe": sd.s_europe}[variant.signal]


def direction(x: float | None) -> str | None:
    """Long above zero, short below, nothing at (rounded) zero or without a signal."""
    if x is None:
        return None
    r = round(x, 12)
    return LONG if r > 0 else SHORT if r < 0 else None


# ======================================================================================= US trades
@dataclass(frozen=True)
class Trade:
    day: date
    group: str
    instrument: str
    side: str
    signal: float
    shares: int
    entry: float
    exit: float
    gross_pnl: float
    slippage_cost: float
    commission: float
    net_pnl: float
    net_ret_bps: float


def share_count(open_price: float, capital: float) -> int:
    return int(math.floor(capital / open_price + 1e-9))


def make_trade(group: GroupDef, day: date, side: str, signal: float, open_: float, close: float,
               cfg: LeadLagConfig, slip_cents: float) -> Trade | None:
    """Enter at the daily-bar open, exit at the daily-bar close; `slip_cents` per share adverse on BOTH fills."""
    shares = share_count(open_, cfg.sleeve_capital_usd)
    if shares < 1:
        return None
    sign = 1.0 if side == LONG else -1.0
    gross = sign * shares * (close - open_)
    slip = shares * (slip_cents / 100.0) * 2
    comm = shares * cfg.commission_per_share * 2
    net = gross - slip - comm
    return Trade(day, group.name, group.us, side, signal, shares, open_, close, gross, slip, comm, net,
                 net / (shares * open_) * 1e4)


@dataclass(frozen=True)
class DayOutcome:
    day: date
    signal: float | None
    trade: Trade | None
    skip: str | None                  # None | "no_signal" | "zero_signal" | "short_excluded" | "zero_shares"

    @property
    def net_pnl(self) -> float:
        return self.trade.net_pnl if self.trade else 0.0


def simulate(group: GroupDef, signals: Sequence[SignalDay], us: Mapping[date, tuple[float, float]], variant: Variant,
             cfg: LeadLagConfig) -> list[DayOutcome]:
    slip = cfg.sensitivity_slippage_cents if variant.slippage == "sensitivity" else cfg.slippage_cents
    out = []
    for sd in signals:
        x = pick_signal(sd, variant)
        side = direction(x)
        if x is None:
            out.append(DayOutcome(sd.day, None, None, "no_signal"))
        elif side is None:
            out.append(DayOutcome(sd.day, x, None, "zero_signal"))
        elif side == SHORT and variant.long_only:
            out.append(DayOutcome(sd.day, x, None, "short_excluded"))
        else:
            o, c = us[sd.day]
            tr = make_trade(group, sd.day, side, x, o, c, cfg, slip)
            out.append(DayOutcome(sd.day, x, tr, None if tr else "zero_shares"))
    return out


# ================================================================================== metrics, verdict
def compute_metrics(outcomes: Sequence[DayOutcome], cfg: LeadLagConfig) -> dict:
    cap = cfg.sleeve_capital_usd
    outs = sorted(outcomes, key=lambda o: o.day)
    daily = [o.net_pnl for o in outs]
    rets = [x / cap for x in daily]
    n = len(rets)
    mean = statistics.fmean(rets) if rets else None
    std = statistics.stdev(rets) if n > 1 else None
    sharpe = (mean / std * math.sqrt(252)) if (mean is not None and std and std > 0) else None
    tstat = (mean / (std / math.sqrt(n))) if (mean is not None and std and std > 0) else None
    equity, peak, max_dd = cap, cap, 0.0
    for x in daily:
        equity += x
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    trades = [o.trade for o in outs if o.trade]
    by_year = {}
    for yr in sorted({o.day.year for o in outs}):
        yt = [o.trade for o in outs if o.day.year == yr and o.trade]
        by_year[str(yr)] = {"days": sum(1 for o in outs if o.day.year == yr), "trades": len(yt),
                            "gross_pnl": sum(t.gross_pnl for t in yt), "net_pnl": sum(t.net_pnl for t in yt)}

    def block(ts):
        return {"trades": len(ts), "net_pnl": sum(t.net_pnl for t in ts),
                "hit_ratio": (sum(1 for t in ts if t.net_pnl > 0) / len(ts)) if ts else None,
                "avg_net_bps": (sum(t.net_ret_bps for t in ts) / len(ts)) if ts else None}
    skips = Counter(o.skip for o in outs if o.skip)
    return {"days": n, "net_pnl": sum(daily), "gross_pnl": sum(t.gross_pnl for t in trades),
            "slippage_cost": sum(t.slippage_cost for t in trades), "commission": sum(t.commission for t in trades),
            "sharpe_net": sharpe, "t_stat_mean_daily_return": tstat, "max_drawdown_usd": max_dd, "by_year": by_year,
            "trades": len(trades), "hit_ratio": (sum(1 for t in trades if t.net_pnl > 0) / len(trades)) if trades else None,
            "avg_net_bps": (sum(t.net_ret_bps for t in trades) / len(trades)) if trades else None,
            "long": block([t for t in trades if t.side == LONG]), "short": block([t for t in trades if t.side == SHORT]),
            "days_no_signal": skips.get("no_signal", 0), "days_no_trade": sum(1 for o in outs if not o.trade),
            "skips": dict(sorted(skips.items()))}


def go_no_go(m: dict, cfg: LeadLagConfig = LeadLagConfig(), *, all_members_checked: bool = False) -> dict:
    """The pre-registered mechanical verdict for ONE group's primary variant. `GO` needs net Sharpe >= 1.0 and net
    P&L > 0 in BOTH 2024 and 2025; a GO while any member used is unchecked against a second source is `GO, UNVERIFIED DATA`."""
    sharpe = m.get("sharpe_net")
    pnl = {y: m.get("by_year", {}).get(str(y), {}).get("net_pnl") for y in (2024, 2025)}
    checks = {f"net_sharpe >= {cfg.min_net_sharpe}": sharpe is not None and sharpe >= cfg.min_net_sharpe,
              **{f"net_pnl_{y} > 0": (pnl[y] is not None and pnl[y] > 0) for y in (2024, 2025)}}
    ok = all(checks.values())
    verdict = ("GO" if all_members_checked else "GO, UNVERIFIED DATA") if ok else "NO-GO"
    return {"verdict": verdict, "checks": checks, "net_sharpe": sharpe, "net_pnl_by_year": pnl}


# ========================================================================== correlations, regression
def pearson(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) < 3 or len(x) != len(y):
        return None
    a, b = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _average_ranks(v: Sequence[float]) -> np.ndarray:
    a = np.asarray(v, dtype=float)
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and a[order[j + 1]] == a[order[i]]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) < 3 or len(x) != len(y):
        return None
    return pearson(_average_ranks(x), _average_ranks(y))


def correlation(pairs: Iterable[tuple[float | None, float | None]]) -> dict:
    """Pearson and Spearman over the pairs where both are defined, with n."""
    xs, ys = [], []
    for a, b in pairs:
        if a is not None and b is not None and not (math.isnan(a) or math.isnan(b)):
            xs.append(a)
            ys.append(b)
    return {"n": len(xs), "pearson": pearson(xs, ys), "spearman": spearman(xs, ys)}


def hac_lags(n: int) -> int:
    """Newey-West rule of thumb L = floor(4 (n/100)^(2/9))."""
    return int(math.floor(4.0 * (n / 100.0) ** (2.0 / 9.0)))


def ols_hac(y: Sequence[float], X: np.ndarray, lags: int | None = None) -> dict:
    """OLS with Newey-West (Bartlett kernel) covariance, no small-sample correction. `X` includes the constant.
    -> {coef, se, t, n, lags, r2}."""
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    n, k = X.shape
    L = hac_lags(n) if lags is None else lags
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    e = y - X @ beta
    u = X * e[:, None]
    S = u.T @ u
    for j in range(1, L + 1):
        w = 1.0 - j / (L + 1.0)
        G = u[j:].T @ u[:-j]
        S = S + w * (G + G.T)
    V = xtx_inv @ S @ xtx_inv
    se = np.sqrt(np.diag(V))
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return {"coef": beta.tolist(), "se": se.tolist(), "t": (beta / se).tolist(), "n": n, "lags": L,
            "r2": (1.0 - float((e ** 2).sum()) / ss_tot) if ss_tot > 0 else None}


def regression(rows: Sequence[tuple[float | None, float, float]]) -> dict | None:
    """o2c = a + b1 s + b2 g over the days where s exists. `rows` = (s, g, o2c). None when too few days."""
    use = [(s, g, o) for s, g, o in rows if s is not None]
    if len(use) < 10:
        return None
    y = [o for _s, _g, o in use]
    X = np.column_stack([np.ones(len(use)), [s for s, _g, _o in use], [g for _s, g, _o in use]])
    out = ols_hac(y, X)
    return {"n": out["n"], "lags": out["lags"], "r2": out["r2"], "a": out["coef"][0], "b1": out["coef"][1], "b2": out["coef"][2],
            "t_a": out["t"][0], "t_b1": out["t"][1], "t_b2": out["t"][2], "se_b1": out["se"][1], "se_b2": out["se"][2]}


# ==================================================================================== data quality
def stale_open_share(m: MemberRows, window_start: date, window_end: date) -> dict:
    """Share of the member's rows in the window (that have a previous row) where open equals the previous close
    after rounding both to 6 decimals."""
    n = stale = 0
    for i in range(1, len(m.dates)):
        if not (window_start <= m.dates[i] <= window_end):
            continue
        o, pc = m.open[i], m.close[i - 1]
        if not (_pos(o) and _pos(pc)):
            continue
        n += 1
        stale += int(round(o, 6) == round(pc, 6))
    return {"rows": n, "stale": stale, "share": (stale / n) if n else None}


def agreement_sample(symbol: str, dates: Sequence[date], n: int, seed: int) -> list[date]:
    """Up to `n` of `dates`, drawn with a generator seeded by (seed, symbol): reproducible and independent per member."""
    rng = random.Random(f"{seed}:{symbol}")
    pool = sorted(dates)
    return sorted(rng.sample(pool, min(n, len(pool))))


def fields_agree(a: float, b: float, tol: float) -> bool:
    return _pos(b) and _pos(a) and abs(a - b) / b <= tol


def two_source_check(m: MemberRows, region: str, stooq: Mapping[date, tuple[float, float]] | None,
                     days: Sequence[date], cfg: LeadLagConfig) -> dict:
    """Compare Yahoo with Stooq on a seeded sample of the member's own rows in the window. `stooq` maps a local date
    to (open, close). A sampled day agrees when every field the member's return uses agrees (Asia: the close;
    Europe/UK: the open and the close); a day Stooq lacks does NOT agree. With no Stooq data the member is `unchecked`."""
    if not stooq:
        return {"status": "unchecked", "sampled": 0, "agree": 0, "share": None}
    idx = m.index()
    dates = [d for d in days if d in idx]
    sample = agreement_sample(m.symbol, dates, cfg.agreement_days_per_member, cfg.agreement_seed)
    agree = 0
    open_ok = close_ok = 0
    for d in sample:
        i = idx[d]
        s = stooq.get(d)
        if s is None:
            continue
        o_ok, c_ok = fields_agree(m.open[i], s[0], cfg.agreement_tolerance), fields_agree(m.close[i], s[1], cfg.agreement_tolerance)
        open_ok += int(o_ok)
        close_ok += int(c_ok)
        agree += int(c_ok if region == ASIA else (o_ok and c_ok))
    share = agree / len(sample) if sample else None
    return {"status": "checked", "sampled": len(sample), "agree": agree, "share": share,
            "open_agree": open_ok, "close_agree": close_ok,
            "fails": share is not None and share < cfg.agreement_min_share}


# ======================================================================================= the study
@dataclass
class GroupResult:
    group: GroupDef
    signals: list[SignalDay]
    us_g: dict[date, float]
    us_o2c: dict[date, float]
    outcomes: dict[str, list[DayOutcome]]
    metrics: dict[str, dict]
    verdict: dict
    correlations: dict
    regression: dict | None
    members_used: list[str]
    members_unchecked: list[str]
    members_excluded: dict[str, str]
    unavailable: dict[str, dict]


def us_day_series(bars: Mapping[date, tuple[float, float]], days: Sequence[date]) -> tuple[dict, dict]:
    """g = open(D)/close(previous row) - 1 and o2c = close(D)/open(D) - 1 from the instrument's own daily bars."""
    order = sorted(bars)
    pos = {d: i for i, d in enumerate(order)}
    g, o2c = {}, {}
    for d in days:
        i = pos.get(d)
        if i is None:
            continue
        o, c = bars[d]
        o2c[d] = c / o - 1.0
        if i > 0:
            g[d] = o / bars[order[i - 1]][1] - 1.0
    return g, o2c


def corr_table(sigs: Sequence[SignalDay], g: Mapping[date, float], o2c: Mapping[date, float]) -> dict:
    """Section 4a for one group: every pair, overall and per year."""
    def pairs(name):
        if name == "g_o2c":
            return [(g.get(sd.day), o2c.get(sd.day)) for sd in sigs]
        sel = {"s": lambda sd: sd.s, "s_asia": lambda sd: sd.s_asia, "s_europe": lambda sd: sd.s_europe}[name.split("__")[0]]
        tgt = g if name.endswith("__g") else o2c
        return [(sel(sd), tgt.get(sd.day)) for sd in sigs]
    names = ["s__g", "s__o2c", "s_asia__g", "s_asia__o2c", "s_europe__g", "s_europe__o2c", "g_o2c"]
    out = {}
    for nm in names:
        pr = list(zip([sd.day for sd in sigs], pairs(nm)))
        out[nm] = {"overall": correlation(p for _d, p in pr)}
        for yr in sorted({d.year for d, _p in pr}):
            out[nm][str(yr)] = correlation(p for d, p in pr if d.year == yr)
    return out


def study_group(group: GroupDef, rets: Mapping[str, Mapping[date, float]], us_bars: Mapping[date, tuple[float, float]],
                days: Sequence[date], cfg: LeadLagConfig, *, unavailable: Mapping[str, Counter],
                excluded: Mapping[str, str], unchecked: Iterable[str]) -> GroupResult:
    sigs = group_signals(group, rets, days)
    g, o2c = us_day_series(us_bars, days)
    outcomes = {v.name: simulate(group, sigs, us_bars, v, cfg) for v in ALL_VARIANTS}
    metrics = {k: compute_metrics(v, cfg) for k, v in outcomes.items()}
    used = [s for s in group.members if s not in excluded]
    unchecked_used = [s for s in used if s in set(unchecked)]
    verdict = go_no_go(metrics["primary"], cfg, all_members_checked=not unchecked_used and bool(used))
    reg = regression([(sd.s, g[sd.day], o2c[sd.day]) for sd in sigs if sd.day in g and sd.day in o2c])
    return GroupResult(group, list(sigs), dict(g), dict(o2c), outcomes, metrics, verdict, corr_table(sigs, g, o2c), reg,
                       used, unchecked_used, dict(excluded) if excluded else {}, {s: dict(c) for s, c in unavailable.items()})


def member_quality(m: MemberRows, region: str, stooq: Mapping[date, tuple[float, float]] | None,
                   window_days: Sequence[date], cfg: LeadLagConfig) -> dict:
    """The pre-registered data-quality results for one member, and the exclusion reason (or None)."""
    ws, we = date.fromisoformat(cfg.window_start), date.fromisoformat(cfg.window_end)
    stale = stale_open_share(m, ws, we) if region == EUROPE else None
    two = two_source_check(m, region, stooq, window_days, cfg)
    reason = None
    if stale is not None and stale["share"] is not None and stale["share"] > cfg.stale_open_max_share:
        reason = "stale_open"
    elif two.get("fails"):
        reason = "two_source"
    in_window = [d for d in m.dates if ws <= d <= we]
    gaps = [(b - a).days for a, b in zip(in_window, in_window[1:])]
    return {"region": region, "stale_open": stale, "two_source": two, "excluded": reason,
            "rows_in_window": len(in_window), "longest_gap_days": max(gaps) if gaps else None,
            "splits": [{"date": d.isoformat(), "ratio": r} for d, r in m.splits], "tz": m.tz}


def analyse(members: Mapping[str, MemberRows], us: Mapping[str, Mapping[date, tuple[float, float]]],
            stooq: Mapping[str, Mapping[date, tuple[float, float]] | None], cfg: LeadLagConfig, days: Sequence[date]) -> dict:
    """Every number of a run, from already-loaded data. `days` are the US trading days D of the window."""
    assert_not_holdout(*days)
    region_of = {s: g.region_of(s) for g in GROUPS for s in g.members}
    quality = {s: member_quality(members[s], region_of[s], stooq.get(s), days, cfg) for s in ALL_MEMBERS}
    excluded = {s: q["excluded"] for s, q in quality.items() if q["excluded"]}
    unchecked = [s for s, q in quality.items() if q["two_source"]["status"] == "unchecked"]
    rets: dict[str, dict[date, float]] = {}
    unavailable: dict[str, Counter] = {}
    for s in ALL_MEMBERS:
        rets[s], unavailable[s] = member_returns(members[s], region_of[s], days, excluded.get(s))
    groups = {}
    for g in GROUPS:
        groups[g.name] = study_group(g, {s: rets[s] for s in g.members}, us[g.us], days, cfg,
                                     unavailable={s: unavailable[s] for s in g.members},
                                     excluded={s: excluded[s] for s in g.members if s in excluded},
                                     unchecked=[s for s in g.members if s in unchecked])
    return {"groups": groups, "quality": quality, "excluded": excluded, "unchecked": unchecked, "timing": timing_rows(days),
            "members_with_returns": {s: len(r) for s, r in rets.items()}, "days": len(days)}
