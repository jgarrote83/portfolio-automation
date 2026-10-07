"""Run the same-minute diagnostic for ORB v1 (a DIAGNOSTIC, never a verdict).

    PYTHONPATH=src python -m backtest.samebar_run run [--out reports/orb/samebar] \\
        [--review-file .review/phase2-samebar-resolution.md] [--rebuild-selection]

Step 1 (offline, from the cache): the Phase 2 PRIMARY variant three ways over the same bars -- the
pre-registered rule (must reproduce the Phase 2 trades exactly), the same rule recorded, and the OPTIMISTIC
bound (the entry bar's stop is not checked). Step 2 (read-only SIP trade prints for the changed trades only,
through `backtest.data.get_trades`): each changed trade's entry minute is replayed print by print and the
engine is run once more with those per-trade answers. Nothing here changes `src/orb/`, the Phase 2
pre-registration or the Phase 2 results; the engine refuses 2026 and there is no override flag.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import pickle
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from orb import universe
from orb.config import OrbConfig

from . import provenance, reports, samebar, samebar_report
from .data import DataLayer
from .data.bars import CacheMissError, HoldoutError
from .data.client import AlpacaDataError
from .data.config import ET_NAME, REPO_ROOT
from .engine import Trade, assert_not_holdout
from .etf import EtfListMissingError, load_etf_list
from .iex_vs_sip import equity_symbols
from .selection import FETCH_FROM, build_selection

START, END = "2024-01-02", "2025-12-31"                        # the Phase 2 window (2026 is never read)
DEFAULT_OUT = REPO_ROOT / "reports" / "orb" / "samebar"
PHASE2_ROOT = REPO_ROOT / "reports" / "orb" / "phase2"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def latest_phase2_run(root: Path = PHASE2_ROOT) -> Path:
    runs = sorted(p for p in root.iterdir() if (p / "primary" / "trades.csv").is_file())
    if not runs:
        raise FileNotFoundError(f"no Phase 2 run under {root}")
    return runs[-1]


# ================================================================================ selection (offline)
def load_picks(layer: DataLayer, cfg: OrbConfig, cache: Path, rebuild: bool):
    """Window days and the PRIMARY (ETFs excluded) picks per day, built exactly as `backtest.cli` builds them."""
    if cache.is_file() and not rebuild:
        blob = pickle.loads(cache.read_bytes())
        if blob["cfg"] == dataclasses.asdict(cfg) and blob["window"] == (START, END):
            _log(f"selection loaded from {cache.name}")
            return blob["days"], blob["picks"], blob["stats"]
    etf = load_etf_list(None)
    assets = layer.get_assets(offline=True)
    core = universe.core_roster()
    symbols = [s for s in equity_symbols(assets) if s not in core]
    sel = build_selection(layer, symbols, etf.symbols, cfg, START, END, fetch_from=FETCH_FROM, offline=True, log=_log)
    picks = {d: tuple(per.get(False, ())) for d, per in sel.picks_by_day.items()}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pickle.dumps({"cfg": dataclasses.asdict(cfg), "window": (START, END),
                                    "days": list(sel.window_days), "picks": picks, "stats": dict(sel.stats)}))
    return list(sel.window_days), picks, dict(sel.stats)


# ====================================================================== reproduction of Phase 2
def check_reproduces_phase2(results, csv_path: Path) -> dict:
    """Every Phase 2 primary trade in the run's trades.csv must be reproduced by the default rule."""
    ref = pd.read_csv(csv_path, dtype={"day": str})
    mine = {(t.day.isoformat(), t.symbol): t for r in results for t in r.trades}
    bad = []
    for row in ref.itertuples():
        t = mine.get((row.day, row.symbol))
        if t is None:
            bad.append((row.day, row.symbol, "missing"))
            continue
        same = (t.side == row.side and t.shares == row.shares and t.entry_minute == row.entry_minute
                and t.exit_minute == row.exit_minute and t.exit_reason == row.exit_reason
                and abs(t.entry_fill - row.entry_fill) < 1e-5 and abs(t.exit_fill - row.exit_fill) < 1e-5
                and abs(t.net_pnl - row.net_pnl) < 1e-4 and abs(t.r_net - row.r_net) < 1e-5)
        if not same:
            bad.append((row.day, row.symbol, "differs"))
    return {"phase2_trades": len(ref), "reproduced_trades": len(mine), "mismatches": len(bad),
            "extra_in_rerun": len(set(mine) - set(zip(ref.day, ref.symbol))), "first_mismatches": bad[:5],
            "identical": not bad and len(mine) == len(ref)}


# ============================================================================= ticks for the changed set
def _minute_window(day: date, minute: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    hh, mm = divmod(minute, 60)
    start = pd.Timestamp(f"{day.isoformat()} {hh:02d}:{mm:02d}:00", tz=ET_NAME)
    return start, start + pd.Timedelta(seconds=60)


def fetch_and_resolve(layer: DataLayer, changed: list[samebar.Candidate], sets: dict[str, frozenset], *, log=_log):
    """For each changed trade: the entry minute's prints, resolved under every print filter, plus the check of
    the tick-derived bar against Alpaca's own bar. -> (resolutions[set][key], bar_checks[set][key], raw[key])."""
    res = {name: {} for name in sets}
    checks = {name: {} for name in sets}
    raw: dict = {}
    t0 = time.time()
    for i, c in enumerate(changed):
        start, end = _minute_window(c.day, c.entry_minute)
        df = layer.get_trades(c.symbol, start, end)
        key = (c.day, c.symbol)
        raw[key] = df
        for name, ex in sets.items():
            pr = samebar.counted_prints(df, ex)
            res[name][key] = samebar.resolve_entry_minute(c.side, c.trigger, c.stop, pr)
            checks[name][key] = samebar.bar_matches(samebar.bar_from_prints(pr), c.bar)
        if (i + 1) % 100 == 0:
            log(f"ticks: {i + 1}/{len(changed)} changed trades ({time.time() - t0:.0f}s, "
                f"{layer.client.requests_made} requests)")
    return res, checks, raw


# ====================================================================================== outputs
def _asdict_trade(t: Trade) -> dict:
    d = dataclasses.asdict(t)
    d["day"] = t.day.isoformat()
    return d


def write_trades_csv(path: Path, results) -> None:
    rows = [_asdict_trade(t) for r in results for t in r.trades]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=reports.TRADE_COLUMNS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.6f}" if isinstance(v, float) else v) for k, v in r.items()})


def candidate_rows(cands, resolutions) -> list[dict]:
    """One row per Phase 2 `stop_same_bar` trade, with the tick outcome where it was replayed."""
    rows = []
    for c in cands:
        r = resolutions.get((c.day, c.symbol))
        rows.append({"day": c.day.isoformat(), "symbol": c.symbol, "side": c.side, "trigger": c.trigger,
                     "stop": c.stop, "entry_minute": c.entry_minute, "bar_open": c.bar[0], "bar_high": c.bar[1],
                     "bar_low": c.bar[2], "bar_close": c.bar[3], "ambiguous": c.ambiguous,
                     "r_gross_optimistic": c.r_gross_optimistic, "changed": c.changed,
                     "tick_outcome": r.outcome if r else "", "tick_counted_prints": r.n_prints if r else ""})
    return rows


# ========================================================================================== the run
def cmd_run(args) -> int:
    cfg = OrbConfig()
    assert_not_holdout(date.fromisoformat(START), date.fromisoformat(END))
    out_root = Path(args.out)
    layer = DataLayer(args.cache_dir)
    p2 = Path(args.phase2_run) if args.phase2_run else latest_phase2_run()
    t0 = time.time()
    days, picks, sel_stats = load_picks(layer, cfg, out_root / "selection-primary.pkl", args.rebuild_selection)
    assert_not_holdout(*days)

    # ---- Step 1: three rules over the same bars --------------------------------------------------------
    rec_assume, rec_ignore = samebar.Recorder(samebar.assume_stop_rule), samebar.Recorder(samebar.ignore_stop_rule)
    sims = samebar.simulate_window(layer, days, picks, cfg, {"default": None, "assume_recorded": rec_assume,
                                                              "optimistic": rec_ignore}, progress=_log)
    default, assume_rec, optimistic = sims["default"], sims["assume_recorded"], sims["optimistic"]
    repro = check_reproduces_phase2(default, p2 / "primary" / "trades.csv")
    rule_equal = samebar.trades_by_key(default) == samebar.trades_by_key(assume_rec)
    _log(f"reproduces Phase 2 primary: {repro['identical']} ({repro['reproduced_trades']} trades); "
         f"explicit pre-registered rule == default: {rule_equal}")
    prim, opt = samebar.trades_by_key(default), samebar.trades_by_key(optimistic)
    cands = samebar.build_candidates(prim, opt, rec_assume.calls)
    changed = [c for c in cands if c.changed]
    _log(f"stop_same_bar {len(cands)}; ambiguous {sum(c.ambiguous for c in cands)}; changed {len(changed)}")

    # ---- Step 2: ticks for the changed set only -------------------------------------------------------------
    sets = {"primary": samebar.PRIMARY_EXCLUDED, "minimal": samebar.MINIMAL_EXCLUDED, "none": frozenset()}
    layer.trade_conditions("A"), layer.trade_conditions("B"), layer.trade_conditions("C")
    res, checks, raw = fetch_and_resolve(layer, changed, sets)
    # a check on the definition of "ambiguous": the entries that opened AT or THROUGH the trigger fill on the
    # opening print, so ticks must show a real stop for every one of them (they never feed the engine)
    unamb = [c for c in cands if not c.ambiguous]
    val_res, _val_checks, _val_raw = fetch_and_resolve(layer, unamb, {"primary": samebar.PRIMARY_EXCLUDED})
    validation = {"n": len(unamb), "outcomes": samebar.outcome_counts(val_res["primary"]),
                  "at_trigger": sum(1 for c in unamb if c.bar[0] == c.trigger),
                  "through_trigger": sum(1 for c in unamb if c.bar[0] != c.trigger)}
    n_requests = layer.client.requests_made if layer._client else 0
    decisions = {name: samebar.tick_decisions(res[name]) for name in sets}

    # ---- the engine once more with the tick answers (and its own consistency checks) ---------------------
    keys = [(c.day, c.symbol) for c in changed]
    final = samebar.simulate_window(layer, days, picks, cfg, {
        "resolved": samebar.ResolvedRule(decisions["primary"]),
        "resolved_minimal": samebar.ResolvedRule(decisions["minimal"]),
        "resolved_nofilter": samebar.ResolvedRule(decisions["none"]),
        "changed_all_real": samebar.ResolvedRule({k: True for k in keys}),
        "changed_all_dip": samebar.ResolvedRule({k: False for k in keys}),
    }, progress=_log)
    all_real_equal_default = samebar.trades_by_key(final["changed_all_real"]) == prim

    scen = {"reported": default, "optimistic": optimistic, "tick_resolved": final["resolved"],
            "tick_resolved_minimal_filter": final["resolved_minimal"],
            "tick_resolved_no_filter": final["resolved_nofilter"], "changed_set_all_dips": final["changed_all_dip"]}
    metrics = {k: reports.compute_metrics(v, cfg) for k, v in scen.items()}

    # ---- outputs -----------------------------------------------------------------------------------------------
    commit = provenance.code_commit()
    now = datetime.now(timezone.utc)
    run_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{(commit['sha'] or 'nogit')[:7]}"
    out = out_root / run_id
    for name, results in scen.items():
        write_trades_csv(out / name / "trades.csv", results)
        (out / name / "metrics.json").write_text(json.dumps(metrics[name], indent=1, default=str), encoding="utf-8")
    header = {
        "run_id": run_id, "created_utc": now.isoformat(), "code_commit": commit, "window": [START, END],
        "phase2_run": p2.name, "config_diff_from_default": cfg.diff_from_default(),
        "reproduces_phase2": repro, "explicit_pre_registered_rule_equals_default": rule_equal,
        "changed_set_all_real_equals_default": all_real_equal_default,
        "excluded_codes": samebar.EXCLUDED_CODES, "minimal_excluded": sorted(samebar.MINIMAL_EXCLUDED),
        "api_requests_this_run": n_requests, "wall_time_s": round(time.time() - t0, 1), "unambiguous_check": validation,
        "selection": sel_stats, "holdout": "2026 was never read (the engine refuses any date on/after 2026-01-01)"}
    (out / "header.json").write_text(json.dumps(header, indent=1, default=str), encoding="utf-8")
    text = samebar_report.render(header, cands, changed, res, checks, raw, metrics, layer, args.seed, validation)
    (out / "report.md").write_text(text, encoding="utf-8")
    if args.review_file:
        review = Path(args.review_file)
        review.parent.mkdir(parents=True, exist_ok=True)
        review.write_text(text, encoding="utf-8")
    cand_rows = candidate_rows(cands, res["primary"])
    with (out / "candidates.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(cand_rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(cand_rows)
    print(f"run {run_id}: outputs in {out}; {n_requests} API requests; report {args.review_file or out / 'report.md'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backtest.samebar_run", description=__doc__.split("\n")[0])
    ap.add_argument("--cache-dir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the same-minute diagnostic")
    r.add_argument("--out", default=str(DEFAULT_OUT))
    r.add_argument("--phase2-run", help="Phase 2 run directory (default: the latest under reports/orb/phase2)")
    r.add_argument("--review-file", help="also write the report here (.review/phase2-samebar-resolution.md)")
    r.add_argument("--rebuild-selection", action="store_true", help="ignore the pickled selection")
    r.add_argument("--seed", type=int, default=7, help="seed for the worked examples")
    args = ap.parse_args(argv)
    try:
        return cmd_run(args)
    except (CacheMissError, HoldoutError, AlpacaDataError, EtfListMissingError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

