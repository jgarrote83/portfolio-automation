"""Phase 2 command line: run the pre-registered ORB backtest.

    PYTHONPATH=src python -m backtest.cli run [--out reports/orb/phase2] \
        [--review-file .review/phase2-results.md]

Reads data ONLY through the Phase 1 data layer (cached SIP bars; keys from the gitignored `.env`),
plus the Nasdaq Trader ETF files already downloaded into `data/reference/`. The engine refuses any
date on or after 2026-01-01 and there is NO override flag. Only the exact pre-registered run
(window 2024-01-02..2025-12-31, no `--limit-symbols`, default config, unmodified pre-registration)
produces a go/no-go verdict; any other invocation is labelled NOT A PRE-REGISTERED RUN.

Outputs go to `reports/orb/phase2/<run-id>/` (gitignored): header.json (config, commit SHA,
pre-registration SHA, data statistics), summary.md, and per variant metrics.json / trades.csv /
daily.csv. The CLI reports; it never tunes.
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from orb import universe
from orb.config import OrbConfig

from . import provenance, reports
from .data import DataLayer
from .data.bars import CacheMissError, HoldoutError
from .data.client import AlpacaDataError
from .data.config import REPO_ROOT
from .data.credentials import MissingCredentialsError
from .engine import ALL_VARIANTS, assert_not_holdout, run_backtest
from .etf import EtfListMissingError, load_etf_list
from .iex_vs_sip import equity_symbols
from .selection import FETCH_FROM, build_selection
from .sessions import check_early_closes

PREREG_START, PREREG_END = "2024-01-02", "2025-12-31"
DEFAULT_OUT = REPO_ROOT / "reports" / "orb" / "phase2"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def _spy_last_minutes(layer, days: list[date], offline: bool) -> dict[date, int]:
    """SPY's last regular-session one-minute bar per day (for the early-close cross-check)."""
    if not days:
        return {}
    df = layer.get_bars(["SPY"], days[0], days[-1], "1Min", "sip", window=("09:30", "16:00"),
                        offline=offline)
    if df.empty:
        return {}
    mins = df["ts"].dt.hour * 60 + df["ts"].dt.minute
    return mins.groupby(df["ts"].dt.date).max().to_dict()


def validity(start: str, end: str, limit_symbols: int | None, cfg: OrbConfig, variants,
             prereg: dict) -> list[str]:
    """Reasons this run is NOT the pre-registered one (empty list = valid for a verdict)."""
    why = []
    if (start, end) != (PREREG_START, PREREG_END):
        why.append(f"window {start}..{end} is not the pre-registered {PREREG_START}..{PREREG_END}")
    if limit_symbols:
        why.append(f"--limit-symbols {limit_symbols} (a smoke test)")
    if cfg != OrbConfig():
        why.append(f"config differs from the pre-registered defaults: {cfg.diff_from_default()}")
    if tuple(v.name for v in variants) != tuple(v.name for v in ALL_VARIANTS):
        why.append("not the full pre-registered variant set")
    if prereg.get("unchanged") is False:
        why.append("the pre-registration file no longer matches its first-commit hash")
    elif prereg.get("unchanged") is None:
        why.append("the pre-registration's first-commit hash could not be verified (no git history)")
    return why


def cmd_run(args) -> int:
    cfg = OrbConfig()
    assert_not_holdout(date.fromisoformat(args.start), date.fromisoformat(args.end))
    layer = DataLayer(args.cache_dir)
    etf = load_etf_list(args.reference_dir)
    assets = layer.get_assets(offline=args.offline)
    core = universe.core_roster()
    symbols = [s for s in equity_symbols(assets) if s not in core]
    if args.limit_symbols:
        symbols = symbols[:args.limit_symbols]
    _log(f"{len(assets)} assets; {len(symbols)} US-exchange equities after removing the Core roster; "
         f"{len(etf.symbols)} ETF symbols known")

    t0 = time.time()
    selection = build_selection(layer, symbols, etf.symbols, cfg, args.start, args.end,
                                fetch_from=FETCH_FROM, offline=args.offline, log=_log)
    run = run_backtest(layer, selection, cfg, ALL_VARIANTS, progress=_log)
    spy_last = _spy_last_minutes(layer, selection.window_days, args.offline)
    early = check_early_closes(selection.window_days, spy_last.get)
    metrics = reports.compute_all(run)

    prereg = provenance.prereg_info()
    why = validity(args.start, args.end, args.limit_symbols, cfg, ALL_VARIANTS, prereg)
    commit = provenance.code_commit()
    now = datetime.now(timezone.utc)
    run_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{(commit['sha'] or 'nogit')[:7]}"
    verdict = reports.go_no_go(metrics["primary"]) if not why else None
    header = {
        "run_id": run_id, "created_utc": now.isoformat(), "spec_version": cfg.spec_version,
        "window": {"start": args.start, "end": args.end, "trading_days": len(selection.window_days),
                   "fetch_from": FETCH_FROM},
        "config": dataclasses.asdict(cfg), "config_diff_from_default": cfg.diff_from_default(),
        "variants": reports.variants_header(ALL_VARIANTS),
        "code_commit": commit, "preregistration": prereg,
        "valid_for_verdict": not why, "invalid_reasons": why,
        "symbols_considered": len(symbols), "etf_files": list(etf.files),
        "selection": selection.stats, "minute_data": run.minute_data_stats,
        "early_close_check": {"mismatches": early, "n_mismatches": len(early)},
        "data_layer": layer.cache_stats(), "wall_time_s": round(time.time() - t0, 1),
        "holdout": "2026 was never read (the engine refuses any date on/after 2026-01-01)",
    }
    out = Path(args.out) / run_id
    reports.write_run(out, header, run, metrics, verdict)
    summary = (out / "summary.md").read_text(encoding="utf-8")
    if args.review_file:
        review = Path(args.review_file)
        review.parent.mkdir(parents=True, exist_ok=True)
        review.write_text(_review_md(header, summary, out), encoding="utf-8")
    print(f"run {run_id}: {'verdict ' + verdict['verdict'] if verdict else 'NOT a pre-registered run'}; "
          f"outputs in {out}")
    return 0


def _review_md(header: dict, summary_md: str, out: Path) -> str:
    sel, minute = header["selection"], header["minute_data"]
    pre = header["preregistration"]
    lines = [
        f"<!-- generated by backtest.cli from {out.name}; do not edit by hand -->",
        summary_md.rstrip(), "",
        "## Run provenance", "",
        f"* Run `{header['run_id']}` on code commit `{header['code_commit']['sha']}` "
        f"(tree dirty: {header['code_commit']['dirty']}); spec_version `{header['spec_version']}`.",
        f"* Pre-registration `{pre['path']}` first committed in `{pre['first_commit']}`; unchanged since: {pre['unchanged']}.",
        f"* Window {header['window']['start']}..{header['window']['end']}, {header['window']['trading_days']} trading days; "
        "the 2026 holdout was never read.",
        f"* Universe: {header['symbols_considered']:,} symbols considered; mean {sel.get('mean_universe_size_incl_etfs', 0):.0f} "
        f"names/day pass the filters (ETFs included in that count); mean {sel.get('mean_qualified_rvol_excl_etfs', 0):.1f} "
        f"ETF-excluded names/day reach RVOL >= 100%; {sel.get('days_with_fewer_than_top_n_qualified_excl_etfs', 0)} days "
        "have fewer than 20; "
        f"{sel.get('etf_picks_in_top_n_incl_etfs', 0):,} ETF picks entered the top 20 in the ETFs-included universe.",
        f"* Picks without any one-minute bar: {minute.get('picks_without_minute_bars', 0):,}.",
        f"* Early-close table vs SPY's own bars: {header['early_close_check']['n_mismatches']} mismatching day(s)"
        + (f" {header['early_close_check']['mismatches'][:5]}" if header["early_close_check"]["mismatches"] else "") + ".",
        f"* Data layer: {header['data_layer'].get('requests_made', 0):,} API requests, "
        f"{header['data_layer'].get('rows', 0):,} cached rows, "
        f"{header['data_layer'].get('disk_bytes', 0) / 1e9:.2f} GB; wall time {header['wall_time_s'] / 60:.1f} min.",
        f"* Full outputs: `{out}` (gitignored).", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backtest.cli", description=__doc__.split("\n")[0])
    ap.add_argument("--cache-dir", help="override data/cache")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the pre-registered Phase 2 backtest (all variants)")
    r.add_argument("--start", default=PREREG_START)
    r.add_argument("--end", default=PREREG_END)
    r.add_argument("--out", default=str(DEFAULT_OUT))
    r.add_argument("--review-file", help="also write the results markdown here (e.g. .review/phase2-results.md)")
    r.add_argument("--offline", action="store_true", help="fail on a cache miss instead of calling the API")
    r.add_argument("--limit-symbols", type=int, help="smoke test on the first N symbols (NOT the run)")
    r.add_argument("--reference-dir", help="override data/reference (Nasdaq Trader files)")
    args = ap.parse_args(argv)
    try:
        return cmd_run(args)
    except (CacheMissError, HoldoutError, MissingCredentialsError, AlpacaDataError,
            EtfListMissingError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
