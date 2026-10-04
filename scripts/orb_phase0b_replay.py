"""ORB Phase 0B -- replay harness: prove Core's outputs are unchanged by the Flex removal.

TEST-ONLY. Never deployed, never imported by src/ or web/. Strictly offline: it reads ONE
stored daily snapshot (a file you downloaded read-only) and runs Core's pure builders over
it. No Azure, no Alpaca, no network, no blob/table access.

Run it twice, once per checkout, then diff:

    # branch
    $env:PYTHONPATH = "src"
    python scripts/orb_phase0b_replay.py run --snapshot data/replay/<date>.json --out data/replay/out-branch.json
    # master (git worktree add ../pf-master master), same command, other --out
    python scripts/orb_phase0b_replay.py diff data/replay/out-master.json data/replay/out-branch.json

What it runs (each in isolation; a builder that raises records ``{"error": ...}`` instead of
aborting, so a difference in failure mode is itself visible in the diff):

  regime_gate, reference_weights, thematic_conviction (+ a labelled non-roster probe),
  market_shock news channel (3 scenarios), price universe (core part), earnings filter,
  quadrant_allocation, functional_coverage.

EXPECTED differences (and the only ones accepted for Part B):
  * U2 -- market_shock news-baseline: the ``untagged_history`` scenario (rows WITHOUT
    ``basis: "held_only"``) must differ (master builds a baseline from them; the branch enters
    the designed no-baseline mode). The ``no_history`` and ``tagged_history`` scenarios must NOT.
  * R4 -- the labelled ``r4_probe`` nomination's ``reason``: ``routed_to_flex`` (master) ->
    ``non_roster_report_only`` (branch). It is a synthetic probe, flagged as such.
Anything else is a bug in Part B.

``_meta`` (checkout mode, builder availability, the quarantine set emulated) is excluded from
the diff on purpose.

The market_shock PRICE channel needs SPY history, which a snapshot does not store. The harness
feeds BOTH checkouts the SAME deterministic stub series reconstructed from the snapshot's own
stored ``market_shock.spy`` figures, so the price channel is identical by construction and any
difference is attributable to the code under test (disclosed in ``_meta``).
"""
from __future__ import annotations

import argparse
import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# --------------------------------------------------------------------------
# pure helpers (unit-tested)
# --------------------------------------------------------------------------

def _flat(obj, prefix=""):
    """Flatten a JSON-able object into {path: leaf}. Lists index by position."""
    out = {}
    if isinstance(obj, dict):
        if not obj:
            out[prefix or "$"] = {}
        for k in sorted(obj):
            out.update(_flat(obj[k], f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        if not obj:
            out[prefix or "$"] = []
        for i, v in enumerate(obj):
            out.update(_flat(v, f"{prefix}[{i}]"))
    else:
        out[prefix or "$"] = obj
    return out


def diff_outputs(a: dict, b: dict) -> list[dict]:
    """Leaf-level differences between two replay outputs, ``_meta`` excluded.
    Each item: {path, a, b} (missing side = "<absent>")."""
    fa = _flat({k: v for k, v in (a or {}).items() if k != "_meta"})
    fb = _flat({k: v for k, v in (b or {}).items() if k != "_meta"})
    diffs = []
    for path in sorted(set(fa) | set(fb)):
        va, vb = fa.get(path, "<absent>"), fb.get(path, "<absent>")
        if va != vb:
            diffs.append({"path": path, "a": va, "b": vb})
    return diffs


def stub_spy_rows(spy: dict | None, n: int = 65) -> list[dict]:
    """Newest-first SPY closes reproducing the snapshot's stored 1d return and z-score:
    today's move = stored return_1d_pct; background alternates +/- sigma where
    sigma = |return| / |z| (0.8 when z is missing/zero)."""
    spy = spy or {}
    r1 = spy.get("return_1d_pct")
    z = spy.get("return_1d_zscore")
    r1 = float(r1) if isinstance(r1, (int, float)) else 0.3
    sigma = abs(r1) / abs(float(z)) if isinstance(z, (int, float)) and abs(float(z)) > 1e-9 else 0.8
    sigma = max(sigma, 0.05)
    hist = [100.0]
    p = 100.0
    for i in range(n - 1):
        sign = 1 if i % 2 == 0 else -1
        p = p / (1 + sign * sigma / 100.0)
        hist.append(p)
    closes = [hist[0] * (1 + r1 / 100.0)] + hist
    return [{"date": f"2026-01-{(1 + i) % 28 + 1:02d}", "price": c} for i, c in enumerate(closes)]


class _StubFMP:
    def __init__(self, rows):
        self._rows = rows

    def get_historical_price_light(self, symbol):
        return self._rows


def synthetic_news_history(market_shock: dict | None, n: int = 15, tagged: bool = True) -> list[dict]:
    """A reproducible baseline built from the snapshot's OWN stored news figures (not invented
    data): ``n`` rows at the stored ``news_hits_total``/dominant category."""
    ms = market_shock or {}
    total = int(ms.get("news_hits_total") or 0)
    by_cat = ms.get("news_hits_by_category") or {}
    dom = max(by_cat, key=by_cat.get) if by_cat else None
    rows = []
    for i in range(n):
        row = {"date": f"2026-09-{i + 1:02d}", "total_hits": total,
               "hits_by_category": dict(by_cat), "dominant_category": dom}
        if tagged:
            row["basis"] = "held_only"
        rows.append(row)
    return rows


def _safe(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 -- a failure is data in a replay
        return {"error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

def run_replay(snap: dict, news_history: list[dict] | None = None) -> dict:
    import collector.handler as ch
    from shared.quadrants import LEGACY_EXITS, selected_core_members

    legacy_mode = hasattr(ch, "_load_flex_candidates")        # master still has the flex pipeline
    flex_profiles = snap.get("flex_candidates") or []
    quarantined = ({str(p.get("symbol") or "").upper() for p in flex_profiles
                    if p.get("price_quarantined")} if legacy_mode else set())
    eff = snap.get("effective_selected") or {}
    paper = snap.get("paper_account") or {}
    positions = paper.get("positions") or []
    equity = float(paper.get("equity") or 0)
    cash = float(paper.get("cash") or 0)
    held = [str(p.get("ticker") or p.get("symbol") or "").upper() for p in
            (snap.get("portfolio") or {}).get("positions", [])]
    held = [t for t in held if t]
    limits = ch._load_risk_limits()
    out: dict = {"_meta": {
        "mode": "legacy(master)" if legacy_mode else "retired(branch)",
        "quarantined_emulated": sorted(quarantined),
        "snapshot_date": snap.get("date"),
        "market_shock_price_channel": "stub series reconstructed from the snapshot's stored spy figures",
    }}

    # regime gate (pure over the three axis blocks)
    out["regime_gate"] = _safe(lambda: ch._build_regime_gate(
        snap.get("growth_axis") or {}, snap.get("inflation_axis") or {}, snap.get("policy_axis") or {}))
    gate = out["regime_gate"] if "error" not in out["regime_gate"] else (snap.get("regime_gate") or {})

    # reference weights, fed the stored sibling blocks
    out["reference_weights"] = _safe(lambda: ch._build_reference_weights(
        paper, snap.get("growth_axis") or {}, snap.get("inflation_axis") or {}, gate,
        snap.get("regional_rotation") or {}, snap.get("bond_signals") or {},
        snap.get("labor_signals") or {}, snap.get("market_shock") or {}, limits,
        snap.get("transition_watch"), snap.get("intl_governance"), eff,
        snap.get("thematic_conviction"), quarantined))

    # thematic overlay: the snapshot's own active/pending entries re-fed as nominations ...
    tc = snap.get("thematic_conviction") or {}
    noms = []
    for e in list(tc.get("active") or []) + list(tc.get("pending") or []):
        if isinstance(e, dict) and e.get("symbol"):
            noms.append({"symbol": e["symbol"], "p_up": e.get("p_up"), "theme": e.get("theme"),
                         "horizon_days": e.get("horizon_days") or 60,
                         "evidence": e.get("evidence") or [], "invalidation": "replay",
                         "review_date": e.get("review_date")})
    calib = {"damping_factor": 1.0, "sample_size": 0}
    out["thematic_conviction"] = _safe(lambda: ch._build_thematic_conviction(
        limits, noms, {}, quarantined, eff, calib)[0])
    # ... plus ONE labelled synthetic probe: a clearly non-roster ticker (R4 label check).
    probe = {"symbol": "ORBPROBE", "p_up": 0.7, "theme": "r4_probe", "horizon_days": 60,
             "evidence": ["a", "b"], "invalidation": "n/a", "review_date": "2099-01-01"}
    probe_block = _safe(lambda: ch._build_thematic_conviction(limits, [probe], {}, set(), eff, calib)[0])
    out["r4_probe"] = ([e for e in probe_block.get("excluded", []) if e.get("symbol") == "ORBPROBE"]
                       if isinstance(probe_block, dict) and "error" not in probe_block else probe_block)

    # market_shock news channel: three history scenarios, identical stubbed price channel
    ms_in = snap.get("market_shock") or {}
    macro = (snap.get("macro") or {}).get("data") or {}
    news = snap.get("news") or {}
    fmp = _StubFMP(stub_spy_rows(ms_in.get("spy")))

    def _shock(history):
        res = ch._build_market_shock(
            fmp=fmp, macro_data=macro, market_news=news.get("market") or [],
            forex_news=news.get("forex") or [], stock_news=snap.get("stock_news") or [],
            company_news=news.get("company") or {}, bond_signals=snap.get("bond_signals"),
            news_hits_history=history)
        keep = ("news_hits_total", "news_hits_zscore", "news_level", "price_level", "shock_level",
                "shock_label", "news_persistent_theme_streak")
        return {k: res.get(k) for k in keep}

    out["market_shock"] = {
        "no_history": _safe(lambda: _shock([])),
        "tagged_history": _safe(lambda: _shock(synthetic_news_history(ms_in, tagged=True))),
        "untagged_history": _safe(lambda: _shock(synthetic_news_history(ms_in, tagged=False))),
    }
    if news_history:
        out["market_shock"]["real_history_as_downloaded"] = _safe(lambda: _shock(news_history))
        out["market_shock"]["real_history_all_tagged"] = _safe(lambda: _shock(
            [{**h, "basis": "held_only"} for h in news_history]))

    # price universe (core part): flex candidates removed from the comparison on purpose
    flex_syms = {str(p.get("symbol") or "").upper() for p in flex_profiles}

    def _universe():
        params = inspect.signature(ch._build_price_universe).parameters
        if "flex_candidate_tickers" in params:
            u = ch._build_price_universe(held, sorted(flex_syms), eff)
        else:
            u = ch._build_price_universe(held, eff)
        return [t for t in u if t not in flex_syms]
    out["price_universe_core"] = _safe(_universe)

    universe = set(held) | set(selected_core_members(eff)) | (set(held) & set(LEGACY_EXITS))
    out["earnings_filter"] = _safe(lambda: ch._filter_earnings_to_universe(
        snap.get("earnings_calendar") or [], universe))
    out["quadrant_allocation"] = _safe(lambda: ch._build_quadrant_allocation(positions, equity, cash, eff))
    out["functional_coverage"] = _safe(lambda: ch._build_functional_coverage(positions, equity))
    out["u3_check"] = {
        "quarantined_flex_candidates_in_snapshot": sorted(
            str(p.get("symbol") or "").upper() for p in flex_profiles if p.get("price_quarantined")),
        "thematic_symbols_in_snapshot": sorted({str(n["symbol"]).upper() for n in noms}),
    }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--snapshot", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--news-history", help="optional downloaded market-shock/news-hits-history.json")
    d = sub.add_parser("diff")
    d.add_argument("a")
    d.add_argument("b")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        snap = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
        hist = (json.loads(Path(args.news_history).read_text(encoding="utf-8"))
                if args.news_history else None)
        res = run_replay(snap, hist)
        Path(args.out).write_text(json.dumps(res, indent=1, sort_keys=True, default=str),
                                  encoding="utf-8")
        print(f"wrote {args.out} (mode: {res['_meta']['mode']})")
        return 0
    a = json.loads(Path(args.a).read_text(encoding="utf-8"))
    b = json.loads(Path(args.b).read_text(encoding="utf-8"))
    diffs = diff_outputs(a, b)
    print(f"{len(diffs)} differing leaf path(s)")
    for x in diffs:
        print(f"  {x['path']}: {x['a']!r} -> {x['b']!r}")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
