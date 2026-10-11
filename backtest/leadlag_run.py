"""Command line for the global lead-lag study (pre-registered: docs/specs/Global_LeadLag_Preregistration.md).

    PYTHONPATH=src python -m backtest.leadlag_run download [--refresh]     # Yahoo -> data/reference/foreign/ (22 requests)
    PYTHONPATH=src python -m backtest.leadlag_run run [--review-file .review/global-leadlag-results.md]
    PYTHONPATH=src python -m backtest.leadlag_verify                        # 30 random trade-days, separate code

`run` reads only the Yahoo cache, the Stooq files placed by hand in `data/reference/foreign/stooq/` (never fetched)
and US daily bars through `backtest.data.get_bars` (SIP, raw). Every date on/after 2026-01-01 is refused for US and
foreign data alike, with no override flag. Only the exact pre-registered run (window, config, all groups and
variants, unmodified pre-registration) yields a verdict; anything else is labelled NOT A PRE-REGISTERED RUN.
Outputs go to `reports/global_leadlag/<run-id>/` (gitignored). The CLI reports; it never tunes.
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

from . import foreign, leadlag, leadlag_report, provenance
from .data import DataLayer
from .data.bars import CacheMissError, HoldoutError
from .data.client import AlpacaDataError
from .data.config import REPO_ROOT
from .data.credentials import MissingCredentialsError

DEFAULT_OUT = REPO_ROOT / "reports" / "global_leadlag"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def validity(start: str, end: str, cfg: leadlag.LeadLagConfig, groups, variants, prereg: dict) -> list[str]:
    """Reasons this run is NOT the pre-registered one (empty list = valid for a verdict)."""
    why = []
    if (start, end) != (leadlag.PREREG_START, leadlag.PREREG_END):
        why.append(f"window {start}..{end} is not the pre-registered {leadlag.PREREG_START}..{leadlag.PREREG_END}")
    if cfg != leadlag.LeadLagConfig():
        why.append(f"config differs from the pre-registered defaults: {cfg.diff_from_default()}")
    if tuple(groups) != leadlag.GROUPS:
        why.append("not the full pre-registered group set")
    if tuple(v.name for v in variants) != tuple(v.name for v in leadlag.ALL_VARIANTS):
        why.append("not the full pre-registered variant set")
    if prereg.get("unchanged") is False:
        why.append("the pre-registration's rule text no longer matches its first-commit hash")
    elif prereg.get("unchanged") is None:
        why.append("the pre-registration's first-commit hash could not be verified (no git history)")
    return why


# ======================================================================================== loading
def load_members(root: Path | None = None) -> tuple[dict[str, foreign.MemberRows], dict[str, dict]]:
    root = root or foreign.FOREIGN_DIR
    members, metas = {}, {}
    for s in leadlag.ALL_MEMBERS:
        df, meta = foreign.load_member(s, root)
        members[s], metas[s] = foreign.MemberRows.from_frame(s, df, meta), meta
    return members, metas


def load_stooq_all(root: Path | None = None) -> dict[str, dict[date, tuple[float, float]] | None]:
    root = root or foreign.FOREIGN_DIR
    out = {}
    for s in leadlag.ALL_MEMBERS:
        df = foreign.load_stooq(s, root)
        out[s] = None if df is None else {d.date(): (float(o), float(c)) for d, o, c in zip(df["date"], df["open"], df["close"])}
    return out


def load_us(layer: DataLayer, cfg: leadlag.LeadLagConfig, *, offline: bool) -> tuple[list[date], dict[str, dict[date, tuple[float, float]]]]:
    start, end = date.fromisoformat(cfg.window_start), date.fromisoformat(cfg.window_end)
    leadlag.assert_not_holdout(start, end)
    days = layer.trading_days(start, end, offline=offline)
    df = layer.get_bars(list(leadlag.US_INSTRUMENTS), cfg.data_from, end, "1Day", "sip", offline=offline)
    us: dict[str, dict[date, tuple[float, float]]] = {s: {} for s in leadlag.US_INSTRUMENTS}
    for sym, ts, o, c in zip(df["symbol"], df["ts"], df["open"], df["close"]):
        us[sym][ts.date()] = (float(o), float(c))
    missing = [(s, d) for s in us for d in days if d not in us[s]]
    if missing:
        raise ValueError(f"US daily bars missing for {len(missing)} instrument-days, e.g. {missing[0]}")
    return days, us


# ======================================================================================== outputs
def _csv(path: Path, header: list[str], rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        for r in rows:
            w.writerow([f"{x:.10f}" if isinstance(x, float) else ("" if x is None else x) for x in r])


def write_run(out: Path, header: dict, res: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "header.json").write_text(json.dumps(header, indent=1, default=str), encoding="utf-8")
    (out / "quality.json").write_text(json.dumps(res["quality"], indent=1, default=str), encoding="utf-8")
    (out / "timing.json").write_text(json.dumps(res["timing"], indent=1, default=str), encoding="utf-8")
    for name, gr in res["groups"].items():
        d = out / name
        (d / "metrics.json").parent.mkdir(parents=True, exist_ok=True)
        (d / "metrics.json").write_text(json.dumps({"metrics": gr.metrics, "verdict": gr.verdict, "correlations": gr.correlations,
                                                    "regression": gr.regression, "members_used": gr.members_used,
                                                    "members_unchecked": gr.members_unchecked, "members_excluded": gr.members_excluded,
                                                    "unavailable": gr.unavailable}, indent=1, default=str), encoding="utf-8")
        _csv(d / "signals.csv", ["day", "s_asia", "s_europe", "s", "n_asia", "n_europe", "us_gap", "us_open_to_close"],
             [(sd.day.isoformat(), sd.s_asia, sd.s_europe, sd.s, sd.n_asia, sd.n_europe, gr.us_g.get(sd.day), gr.us_o2c.get(sd.day))
              for sd in gr.signals])
        for vname, outs in gr.outcomes.items():
            _csv(d / vname / "trades.csv", ["day", "group", "instrument", "side", "signal", "shares", "entry", "exit", "gross_pnl",
                                            "slippage_cost", "commission", "net_pnl", "net_ret_bps"],
                 [(t.day.isoformat(), t.group, t.instrument, t.side, t.signal, t.shares, t.entry, t.exit, t.gross_pnl, t.slippage_cost,
                   t.commission, t.net_pnl, t.net_ret_bps) for t in (o.trade for o in outs) if t])
            _csv(d / vname / "daily.csv", ["day", "signal", "net_pnl", "skip"],
                 [(o.day.isoformat(), o.signal, o.net_pnl, o.skip) for o in outs])


# ============================================================================================ run
def cmd_download(args) -> int:
    root = Path(args.foreign_dir) if args.foreign_dir else foreign.FOREIGN_DIR
    metas = foreign.ensure_cache(leadlag.ALL_MEMBERS, root=root, refresh=args.refresh, log=_log)
    print(f"{len(metas)} members cached under {root}")
    return 0


def cmd_run(args) -> int:
    cfg = leadlag.LeadLagConfig()
    leadlag.assert_not_holdout(date.fromisoformat(args.start), date.fromisoformat(args.end))
    t0 = time.time()
    layer = DataLayer(args.cache_dir)
    froot = Path(args.foreign_dir) if args.foreign_dir else None
    members, metas = load_members(froot)
    stooq = load_stooq_all(froot)
    days, us = load_us(layer, cfg.replace(window_start=args.start, window_end=args.end), offline=args.offline)
    res = leadlag.analyse(members, us, stooq, cfg, days)
    prereg = provenance.prereg_info(path=leadlag.PREREG_PATH)
    why = validity(args.start, args.end, cfg, leadlag.GROUPS, leadlag.ALL_VARIANTS, prereg)
    commit = provenance.code_commit()
    now = datetime.now(timezone.utc)
    run_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{(commit['sha'] or 'nogit')[:7]}"
    if why:                                        # a non-pre-registered run carries no verdict
        for gr in res["groups"].values():
            gr.verdict = {**gr.verdict, "verdict": "NOT A PRE-REGISTERED RUN"}
    header = {
        "run_id": run_id, "created_utc": now.isoformat(), "spec_version": cfg.spec_version,
        "window": {"start": args.start, "end": args.end, "us_trading_days": len(days), "data_from": cfg.data_from},
        "config": dataclasses.asdict(cfg), "config_diff_from_default": cfg.diff_from_default(),
        "groups": {g.name: {"us": g.us, "asia": list(g.asia), "europe": list(g.europe)} for g in leadlag.GROUPS},
        "variants": [v.name for v in leadlag.ALL_VARIANTS], "code_commit": commit, "preregistration": prereg,
        "valid_for_verdict": not why, "invalid_reasons": why,
        "yahoo": {s: {k: m[k] for k in ("tz", "rows", "first_date", "last_date", "downloaded_utc", "yfinance", "splits")} for s, m in metas.items()},
        "excluded_members": res["excluded"], "unchecked_members": res["unchecked"],
        "stooq_files_found": sorted(s for s, v in stooq.items() if v), "us_api_requests_this_run": layer.cache_stats().get("requests_made", 0),
        "wall_time_s": round(time.time() - t0, 1),
        "holdout": "2026 was never read (this code refuses any US or foreign date on/after 2026-01-01)"}
    out = Path(args.out) / run_id
    write_run(out, header, res)
    text = leadlag_report.render(header, res, REPO_ROOT)
    (out / "summary.md").write_text(text, encoding="utf-8")
    if args.review_file:
        review = Path(args.review_file)
        review.parent.mkdir(parents=True, exist_ok=True)
        review.write_text(text, encoding="utf-8")
    verdicts = ", ".join(f"{n}: {g.verdict['verdict']}" for n, g in res["groups"].items())
    print(f"run {run_id}: {verdicts if not why else 'NOT a pre-registered run'}; outputs in {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backtest.leadlag_run", description=__doc__.split("\n")[0])
    ap.add_argument("--cache-dir", help="override data/cache")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download", help="download the 22 foreign members from Yahoo into data/reference/foreign/")
    d.add_argument("--refresh", action="store_true", help="re-download members that are already cached")
    d.add_argument("--foreign-dir", help="override data/reference/foreign")
    r = sub.add_parser("run", help="run the pre-registered lead-lag study")
    r.add_argument("--start", default=leadlag.PREREG_START)
    r.add_argument("--end", default=leadlag.PREREG_END)
    r.add_argument("--out", default=str(DEFAULT_OUT))
    r.add_argument("--review-file", help="also write the results markdown here (.review/global-leadlag-results.md)")
    r.add_argument("--offline", action="store_true", help="fail on a US-bar cache miss instead of calling the API")
    r.add_argument("--foreign-dir", help="override data/reference/foreign")
    args = ap.parse_args(argv)
    try:
        return cmd_download(args) if args.cmd == "download" else cmd_run(args)
    except (CacheMissError, HoldoutError, MissingCredentialsError, AlpacaDataError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
