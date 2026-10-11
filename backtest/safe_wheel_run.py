"""Command line for the safe-wheel model study (pre-registered: docs/specs/Safe_Wheel_Model_Preregistration.md).

    PYTHONPATH=src python -m backtest.safe_wheel_run download [--refresh]     # FRED x3, Yahoo x2, Cboe PUT -> data/reference/wheel/
    PYTHONPATH=src python -m backtest.safe_wheel_run run [--review-file .review/safe-wheel-model-results.md]
    PYTHONPATH=src python -m backtest.safe_wheel_verify                         # 30 random positions, separate code

`run` reads only the reference cache and, for the two-source price check, SPY daily bars through
`backtest.data.get_bars` (SIP, raw, read-only GETs). Every date on/after 2026-01-01 is refused for every source with no
override flag. Only the exact pre-registered run (window, default configuration, all ten variants, unmodified
pre-registration) yields a verdict; anything else is labelled NOT A PRE-REGISTERED RUN. Outputs go to
`reports/safe_wheel_model/<run-id>/` (gitignored). The CLI reports; it never tunes.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from . import provenance, safe_wheel as sw, safe_wheel_report, wheel_data
from .data import DataLayer
from .data.bars import CacheMissError, HoldoutError
from .data.client import AlpacaDataError
from .data.config import REPO_ROOT
from .data.credentials import MissingCredentialsError

DEFAULT_OUT = REPO_ROOT / "reports" / "safe_wheel_model"
PRICE_CHECK_START, PRICE_CHECK_END = date(2016, 1, 1), date(2025, 12, 31)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def validity(start: str, end: str, cfg: sw.WheelConfig, variants, prereg: dict) -> list[str]:
    """Reasons this run is NOT the pre-registered one (empty list = valid for a verdict)."""
    why = []
    if (start, end) != (sw.PREREG_START, sw.PREREG_END):
        why.append(f"window {start}..{end} is not the pre-registered {sw.PREREG_START}..{sw.PREREG_END}")
    if cfg != sw.WheelConfig():
        why.append(f"config differs from the pre-registered defaults: {cfg.diff_from_default()}")
    if tuple(v.name for v in variants) != tuple(v.name for v in sw.ALL_VARIANTS):
        why.append("not the full pre-registered variant set")
    if prereg.get("unchanged") is False:
        why.append("the pre-registration's rule text no longer matches its first-commit hash")
    elif prereg.get("unchanged") is None:
        why.append("the pre-registration's first-commit hash could not be verified (no git history)")
    return why


# ======================================================================================== outputs
def _csv(path: Path, header: list[str], rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        for r in rows:
            w.writerow([f"{x:.10f}" if isinstance(x, float) else ("" if x is None else x) for x in r])


POSITION_COLUMNS = ["entry_day", "expiry", "third_friday", "moved", "sched_exit_day", "exit_day", "exit_reason", "spot", "vol_index", "rate",
                    "q", "t_entry", "k_short", "k_long", "width", "p_short", "p_long", "mid_credit", "credit", "contracts", "mark_exit",
                    "debit_exit", "fees", "net_pnl", "worst_unreal"]


def write_run(out: Path, header: dict, res: dict, price: dict | None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "header.json").write_text(json.dumps(header, indent=1, default=str), encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps({"metrics": res["metrics"], "gate": res["gate"]}, indent=1, default=str), encoding="utf-8")
    (out / "checks.json").write_text(json.dumps({"price_check": price, "calendar": res["calendar"]}, indent=1, default=str), encoding="utf-8")
    for name, run in res["runs"].items():
        d = out / name
        _csv(d / "positions.csv", POSITION_COLUMNS,
             [tuple(getattr(p, c) if not isinstance(getattr(p, c), date) else getattr(p, c).isoformat() for c in POSITION_COLUMNS) for p in run.positions])
        _csv(d / "daily.csv", ["day", "equity", "pnl"], [(day.isoformat(), e, p) for day, e, p in run.daily])
        _csv(d / "skips.csv", ["cycle_expiry", "entry_day", "reason"],
             [(s["cycle_expiry"].isoformat(), s["entry_day"].isoformat(), s["reason"]) for s in run.skips])
        _csv(d / "cycles.csv", ["third_friday", "expiry", "moved", "entry_day", "exit_day", "status"],
             [(c.third_friday.isoformat(), c.expiry.isoformat(), c.moved, c.entry_day.isoformat(), c.exit_day.isoformat(), c.status) for c in run.cycles])
    cal = res["calibration"]
    if cal:
        _csv(out / "calibration_periods.csv", ["start", "end", "strike", "spot", "vol", "premium", "payoff", "model_return", "put_return", "put_start", "put_end"],
             [(p["start"].isoformat(), p["end"].isoformat(), p["strike"], p["spot"], p["vol"], p["premium"], p["payoff"], p["model_return"],
               p["put_return"], p["put_start"], p["put_end"]) for p in cal["periods"]])


def underlying_year_returns(inputs: wheel_data.WheelInputs, first: int, last: int) -> dict[int, float]:
    """Calendar-year price return of the underlying (last close of the year over the last close of the year before)."""
    lastc: dict[int, float] = {}
    for d in inputs.days:
        lastc[d.year] = inputs.close[d]
    return {y: lastc[y] / lastc[y - 1] - 1.0 for y in range(first, last + 1) if y in lastc and (y - 1) in lastc}


def run_price_check(layer: DataLayer, spy_close: dict[date, float], *, offline: bool) -> tuple[dict | None, str | None, list[date] | None]:
    """(check, None, the Alpaca trading days) or (None, why it could not run, None)."""
    try:
        df = layer.get_bars(["SPY"], PRICE_CHECK_START, PRICE_CHECK_END, "1Day", "sip", offline=offline)
    except (MissingCredentialsError, AlpacaDataError, CacheMissError) as exc:
        return None, f"Alpaca SIP daily bars unavailable: {exc}", None
    alp = {ts.date(): float(c) for ts, c in zip(df["ts"], df["close"])}
    return wheel_data.two_source_check(spy_close, alp, PRICE_CHECK_START, PRICE_CHECK_END), None, sorted(alp)


# ============================================================================================== run
def cmd_download(args) -> int:
    root = Path(args.wheel_dir) if args.wheel_dir else wheel_data.WHEEL_DIR
    metas = wheel_data.ensure_cache(root=root, refresh=args.refresh, log=_log)
    print(f"{len(metas)} series cached under {root}")
    return 0


def cmd_run(args) -> int:
    cfg = sw.WheelConfig()
    sw.assert_not_holdout(date.fromisoformat(args.start), date.fromisoformat(args.end))
    t0 = time.time()
    root = Path(args.wheel_dir) if args.wheel_dir else wheel_data.WHEEL_DIR
    spy = wheel_data.load_inputs("SPY", "VIXCLS", root)
    qqq = wheel_data.load_inputs("QQQ", "VXNCLS", root)
    try:
        put, put_meta = wheel_data.load_series("cboe", "PUT", root)
        cal_reason = None
    except FileNotFoundError as exc:
        put, put_meta, cal_reason = None, {}, f"the Cboe PUT index file is not cached ({exc})"
    run_cfg = cfg.replace(window_start=args.start, window_end=args.end)
    res = sw.analyse({("SPY", "VIXCLS"): spy, ("QQQ", "VXNCLS"): qqq}, put, sw.ALL_VARIANTS, run_cfg)
    price, price_reason, alp_days = run_price_check(DataLayer(args.cache_dir), spy.close, offline=args.offline)
    if alp_days is not None:
        res["calendar"] = sw.calendar_check(list(spy.days), run_cfg, alp_days)
    prereg = provenance.prereg_info(path=sw.PREREG_PATH)
    why = validity(args.start, args.end, run_cfg, sw.ALL_VARIANTS, prereg)
    commit = provenance.code_commit()
    now = datetime.now(timezone.utc)
    run_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{(commit['sha'] or 'nogit')[:7]}"
    if why:                                        # a non-pre-registered run carries no verdict
        res["gate"] = {**res["gate"], "verdict": "NOT A PRE-REGISTERED RUN"}
    metas = {n: json.loads((root / k / f"{n}.json").read_text(encoding="utf-8"))
             for k, n in (("fred", "VIXCLS"), ("fred", "VXNCLS"), ("fred", "DTB3"), ("yahoo", "SPY"), ("yahoo", "QQQ"), ("cboe", "PUT"))
             if (root / k / f"{n}.json").is_file()}
    header = {"run_id": run_id, "created_utc": now.isoformat(), "spec_version": cfg.spec_version,
              "window": {"start": args.start, "end": args.end, "trading_days": res["metrics"]["primary"]["days"]},
              "config": dataclasses.asdict(run_cfg), "config_diff_from_default": run_cfg.diff_from_default(),
              "variants": [v.name for v in sw.ALL_VARIANTS], "code_commit": commit, "preregistration": prereg,
              "valid_for_verdict": not why, "invalid_reasons": why, "gate": res["gate"],
              "data": {k: {kk: m.get(kk) for kk in ("rows", "first_date", "last_date", "downloaded_utc", "content_sha256", "yfinance", "splits",
                                                    "rows_dated_2026_dropped_at_parse")} for k, m in metas.items()},
              "wall_time_s": round(time.time() - t0, 1),
              "holdout": "2026 was never read (this code refuses any date on/after 2026-01-01 for every source)"}
    notes = {"Volatility and rate carry-forwards (trading days without an exact observation)":
             f"SPY/VIXCLS {len(spy.stale['vol'])} days, SPY/DTB3 {len(spy.stale['rate'])} days, QQQ/VXNCLS {len(qqq.stale['vol'])} days, "
             f"QQQ/DTB3 {len(qqq.stale['rate'])} days; the largest staleness is "
             f"{max([x['stale_days'] for k in ('vol', 'rate') for x in spy.stale[k] + qqq.stale[k]] or [0])} calendar days (limit {wheel_data.MAX_STALE_DAYS})",
             "Splits in the cached data": f"SPY {list(spy.splits) or 'none'}, QQQ {list(qqq.splits) or 'none'}",
             "Cboe PUT file": ("not cached" if not put_meta else
                               f"{put_meta.get('rows')} rows dated before 2026 kept ({put_meta.get('first_date')} to {put_meta.get('last_date')}); "
                               "rows dated 2026 were dropped at parse and never stored or inspected")}
    out = Path(args.out) / run_id
    write_run(out, header, res, price)
    text = safe_wheel_report.render(header, res, price, price_reason, cal_reason, notes,
                                    underlying_year_returns(spy, 2005, 2025))
    (out / "summary.md").write_text(text, encoding="utf-8")
    if args.review_file:
        review = Path(args.review_file)
        review.parent.mkdir(parents=True, exist_ok=True)
        review.write_text(text, encoding="utf-8")
    g = res["gate"]["verdict"]
    print(f"run {run_id}: {g if not why else 'NOT a pre-registered run'}; outputs in {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backtest.safe_wheel_run", description=__doc__.split("\n")[0])
    ap.add_argument("--cache-dir", help="override data/cache")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download", help="download the reference series into data/reference/wheel/")
    d.add_argument("--refresh", action="store_true", help="re-download series that are already cached")
    d.add_argument("--wheel-dir", help="override data/reference/wheel")
    r = sub.add_parser("run", help="run the pre-registered safe-wheel model study")
    r.add_argument("--start", default=sw.PREREG_START)
    r.add_argument("--end", default=sw.PREREG_END)
    r.add_argument("--out", default=str(DEFAULT_OUT))
    r.add_argument("--review-file", help="also write the results markdown here (.review/safe-wheel-model-results.md)")
    r.add_argument("--offline", action="store_true", help="fail on a US-bar cache miss instead of calling the API")
    r.add_argument("--wheel-dir", help="override data/reference/wheel")
    args = ap.parse_args(argv)
    try:
        return cmd_download(args) if args.cmd == "download" else cmd_run(args)
    except (CacheMissError, HoldoutError, MissingCredentialsError, AlpacaDataError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
