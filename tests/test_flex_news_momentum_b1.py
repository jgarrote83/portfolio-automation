"""B1 (session 2026-09-12) — regime removal (R1), rankability rewrite (R2), and
the movers discovery universe (N1).

THE DROUGHT'S CAUSE, measured before any code was written. Live ledger,
`daily-snapshots/2026-09-{09,10,11}.json`:

    date    universe  liquidity_below_min  reached scoring
    09-09      25         23 (92%)              2
    09-10      25         21 (84%)              4
    09-11      25         22 (88%)              3
    TOTAL      75         66 (88.0%)            9 (12.0%)

88% died on the LIQUIDITY floor before a single component was computed
(`components_available: 0`), because the discovery universe was OTC/foreign
micro-caps (IDWM, REBN, FANDF, HGRAF, SRTSF, ODMUF, GYYMF, MHPSY). The 12% that
reached scoring got exactly 3 components against a bar of 4. So R2 (the
component bar) is real but secondary; **N1 (the universe) is the binding fix** —
the causal weighting in the original brief was backwards, corrected by probe.

`avg_dollar_volume` and `MIN_ADV_USD = $50M` were BOTH verified correct by live
probe (ETN $723M, AAPL $13.5B, RH $116M, ANAB $25M correctly failing) — the 88%
rejection rate was the floor doing its job. See FOLLOWUPS #98.

Run: PYTHONPATH=src pytest tests/test_flex_news_momentum_b1.py
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector import catalyst_screen  # noqa: E402
from flex.config import FlexConfig  # noqa: E402
from flex.entry import build_conviction_entry, build_flex_entry  # noqa: E402
from flex.separation import FLEX_REENTERABLE, flex_separation_set  # noqa: E402

NOW = "2026-09-12 14:00:00"


# --- R1: regime is gone from the flex sleeve ---------------------------------

def test_flex_regime_module_no_longer_exists():
    """R1: `flex/regime.py` is deleted outright. `flex_separation_set` and
    `FLEX_REENTERABLE` survive in `flex/separation.py` — book-collision
    prevention was never a regime concept and must not be deleted with one."""
    with pytest.raises(ModuleNotFoundError):
        __import__("flex.regime")
    assert FLEX_REENTERABLE == frozenset({"INTC", "MCK", "PPA", "EUAD"})
    assert callable(flex_separation_set)


def test_entry_builders_take_no_quadrant_argument():
    """Pinned at the signature level so a future change cannot quietly
    reintroduce a regime input to either entry profile."""
    import inspect
    for fn in (build_flex_entry, build_conviction_entry):
        params = set(inspect.signature(fn).parameters)
        assert "quadrant" not in params, fn.__name__
        assert "quadrant_basis" not in params, fn.__name__


def test_separation_set_is_pool_based_and_survives_intact():
    """R1 explicitly preserves this: it blocks EVERY pool member, not just the
    selected incumbent, which is also why it is immune to the auto-switch
    staleness class that produced the 2026-09-12 de-risk classifier defect."""
    from shared.quadrants import roles_config
    sep = flex_separation_set(frozenset())
    pool_members = {str(m).upper() for r in roles_config() for m in r.get("pool", ())}
    assert pool_members <= sep, sorted(pool_members - sep)
    # a flat re-enterable legacy name is NOT blocked; held, it is
    assert "INTC" not in flex_separation_set(frozenset())
    assert "INTC" in flex_separation_set(frozenset({"INTC"}))


def test_regime_fit_score_is_gone_from_the_components():
    assert "regime_fit_score" not in catalyst_screen.COMPONENTS
    assert "volume_surge" in catalyst_screen.COMPONENTS


# --- R2: required-component rankability, not a bare count --------------------

def test_news_recency_is_mandatory():
    """This is a NEWS strategy. A candidate with no recent news has no thesis,
    however well everything else scores — the exact failure the old count bar
    allowed (rankable on momentum + regime fit alone)."""
    ok, reason = catalyst_screen.rankability(
        {"momentum": 1.0, "relative_strength": 1.0, "news_tone": 0.5, "volume_surge": 1.0},
        catalyst_screen.COMPONENTS)
    assert ok is False
    assert reason == "missing_required:news_recency"


def test_price_confirmation_is_mandatory():
    """News with no tape response is a story, not a trade. Either momentum or
    volume_surge satisfies it — they are alternatives, not both required."""
    base = {"news_recency": 0.9, "news_tone": 0.5, "political_flow": 0.4}
    ok, reason = catalyst_screen.rankability(base, catalyst_screen.COMPONENTS)
    assert (ok, reason) == (False, "missing_price_confirmation")
    assert catalyst_screen.rankability({**base, "momentum": 0.6}, catalyst_screen.COMPONENTS)[0]
    assert catalyst_screen.rankability({**base, "volume_surge": 0.6}, catalyst_screen.COMPONENTS)[0]


def test_earnings_proximity_never_gates_rankability():
    """R2's core: the old universe (yesterday's reporters) could never satisfy a
    14-day FORWARD earnings window, so requiring it was unsatisfiable by
    construction. A catalyst is now purely opportunistic."""
    comps = {"news_recency": 0.9, "momentum": 0.6, "news_tone": 0.5}
    ok_without, _ = catalyst_screen.rankability(comps, catalyst_screen.COMPONENTS)
    ok_with, _ = catalyst_screen.rankability(
        {**comps, "earnings_proximity": 1.0}, catalyst_screen.COMPONENTS)
    assert ok_without is True and ok_with is True
    # ...and it still CONTRIBUTES to the score when present.
    assert (catalyst_screen.composite_score({**comps, "earnings_proximity": 1.0})["score"]
            > catalyst_screen.composite_score(comps)["score"])


def test_insufficient_components_still_vetoes():
    """The "mostly unmeasured" guard survives the rewrite, at 3."""
    ok, reason = catalyst_screen.rankability(
        {"news_recency": 0.9, "momentum": 0.6}, catalyst_screen.COMPONENTS)
    assert ok is False and reason.startswith("insufficient_components:2<3")


def test_a_required_component_that_is_not_applicable_cannot_be_required():
    """If `news_recency` were ever made non-applicable for some instrument,
    requiring it would be the 'judged against a bar it can never structurally
    clear' failure `applicable_components` exists to prevent."""
    applicable = tuple(c for c in catalyst_screen.COMPONENTS if c != "news_recency")
    ok, reason = catalyst_screen.rankability(
        {"momentum": 1.0, "news_tone": 0.5, "relative_strength": 1.0}, applicable)
    assert ok is True, reason


def test_absent_vs_zero_is_unchanged():
    """The load-bearing rule R2 must not disturb: an absent component drops OUT
    of the mean, it is never averaged in as 0.0."""
    two = catalyst_screen.composite_score(
        {"news_recency": 1.0, "momentum": 1.0, "news_tone": None})
    assert two["score"] == 1.0            # not 0.667
    assert two["components_available"] == 2


# --- N1: volume_surge -------------------------------------------------------

def test_volume_surge_measures_todays_volume_against_its_own_trailing_average():
    bars = [{"c": 10.0, "v": 1_000_000} for _ in range(20)] + [{"c": 10.0, "v": 3_000_000}]
    assert catalyst_screen.volume_surge_from_bars(bars, 20) == 3.0
    assert catalyst_screen.volume_surge_score(3.0, 3.0) == 1.0


def test_volume_surge_is_one_sided():
    """Below-average volume is the ABSENCE of a surge, not a negative signal —
    deliberately unlike momentum's symmetric clamp."""
    assert catalyst_screen.volume_surge_score(1.0, 3.0) == 0.0
    assert catalyst_screen.volume_surge_score(0.2, 3.0) == 0.0
    assert catalyst_screen.volume_surge_score(2.0, 3.0) == 0.5


def test_volume_surge_absent_on_thin_history_never_fabricates_average():
    assert catalyst_screen.volume_surge_from_bars([{"c": 1.0, "v": 5}] * 5, 20) is None
    assert catalyst_screen.volume_surge_from_bars([{"c": 1.0, "v": 0}] * 25, 20) is None
    assert catalyst_screen.volume_surge_score(None) is None


# --- N1: hours-based news recency -------------------------------------------

def test_news_recency_is_measured_in_hours():
    """A 7-DAY lookback on a sleeve that holds ~2 days scored a 6-day-old
    headline at 0.14 and counted it as "news"."""
    items = [{"publishedDate": "2026-09-12 08:00:00"}]
    hrs = catalyst_screen.hours_since_latest_news(items, NOW)
    assert hrs == 6.0
    assert catalyst_screen.news_recency_score(hrs, 24) == 0.75
    assert catalyst_screen.news_recency_score(30.0, 24) is None   # past the window


def test_date_only_timestamp_reads_as_midnight_never_fabricates_freshness():
    items = [{"publishedDate": "2026-09-12"}]
    assert catalyst_screen.hours_since_latest_news(items, NOW) == 14.0


# --- N1: the movers discovery universe --------------------------------------

def _rows(*specs):
    return [{"symbol": s, "price": p} for s, p in specs]


def test_movers_universe_requires_recent_news():
    """The news intersection is the POINT, not a filter: a mover with no recent
    news is a price move without a reason."""
    rows = _rows(("AAA", 50.0), ("BBB", 50.0))
    news = {"AAA": [{"publishedDate": "2026-09-12 12:00:00"}]}
    syms, dropped = catalyst_screen.movers_discovery_symbols(
        rows, news, NOW, set(), 25, 24, 5.0)
    assert syms == ["AAA"]
    assert dropped["BBB"] == "no_recent_news"


def test_movers_universe_applies_the_price_floor():
    """G-7: the live mover union is ~19% sub-$1 and ~42% sub-$5 — a
    pump-and-dump surface the ADV floor alone does not defend, since a $1 stock
    can trade $50M on the day it is being promoted."""
    rows = _rows(("PENNY", 0.65), ("REAL", 50.0))
    news = {"PENNY": [{"publishedDate": NOW}], "REAL": [{"publishedDate": NOW}]}
    syms, dropped = catalyst_screen.movers_discovery_symbols(
        rows, news, NOW, set(), 25, 24, 5.0)
    assert syms == ["REAL"]
    assert dropped["PENNY"] == "below_min_price"


def test_movers_universe_respects_exclusions_and_cap():
    rows = _rows(("AAA", 50.0), ("BBB", 50.0), ("CCC", 50.0))
    news = {s: [{"publishedDate": NOW}] for s in ("AAA", "BBB", "CCC")}
    syms, _ = catalyst_screen.movers_discovery_symbols(
        rows, news, NOW, {"AAA"}, 25, 24, 5.0)
    assert syms == ["BBB", "CCC"]
    capped, _ = catalyst_screen.movers_discovery_symbols(
        rows, news, NOW, set(), 2, 24, 5.0)
    assert len(capped) == 2


def test_movers_universe_never_admits_a_separation_set_member():
    """SPY/TLT/XLF are routinely in the live mover list and are core pool
    members. The separation set is an absolute gate and survives N1 intact."""
    sep = flex_separation_set(frozenset())
    blocked = sorted(sep)[:3]
    rows = _rows(*[(s, 100.0) for s in blocked], ("NEWNAME", 100.0))
    news = {s: [{"publishedDate": NOW}] for s in blocked + ["NEWNAME"]}
    syms, _ = catalyst_screen.movers_discovery_symbols(
        rows, news, NOW, sep, 25, 24, 5.0)
    assert syms == ["NEWNAME"]


def test_end_to_end_a_real_shaped_mover_is_nominatable():
    """The whole point of B1: a liquid, newsy mover must now reach `rankable`.
    Mirrors the live probe (2026-09-12), which took a 96-name mover union to 7
    discovered, 7/7 screened in, 7/7 rankable — ORCL/AAPL/HPE/SMR/SPCX/F/NVDA —
    against 0 nominations in every session of the prior universe."""
    comps = {
        "news_recency": catalyst_screen.news_recency_score(6.0, 24),
        "news_tone": catalyst_screen.news_tone_score(True, 2, 0),
        "momentum": catalyst_screen.momentum_score(5.0),
        "volume_surge": catalyst_screen.volume_surge_score(2.5, 3.0),
        "earnings_proximity": None,      # no catalyst — must not matter
        "political_flow": None,
        "relative_strength": None,
    }
    cs = catalyst_screen.composite_score(comps)
    assert cs["rankable"] is True
    assert cs["rankability_reason"] is None
    assert cs["components_available"] == 4


# --- B2 (N2/N3/N4): exit profile, bracket order, window, sizing -------------

def test_b2_config_profile():
    """N2/N4 — the locked profile numbers, pinned so a drift is visible."""
    c = FlexConfig()
    assert (c.take_profit_pct, c.max_stop_pct, c.time_stop_days) == (2.0, 1.5, 2)
    assert c.per_name_cap_pct == 6.0          # N4: 12.0 -> 6.0 (gap risk)
    assert c.sleeve_cap_pct == 25.0           # Jorge's full sleeve, unchanged
    assert c.entry_late_cutoff_min == 30      # N3
    assert not hasattr(c, "first_target_r")   # scale-out retired
    assert not hasattr(c, "scale_out_fraction")
    assert not hasattr(c, "entry_cutoff_min")  # morning-only cutoff retired


def test_b2_breakeven_arithmetic_is_a_floor_not_the_bar():
    """The +2%/-1.5% pair implies a 42.9% breakeven win rate IF outcomes were
    binary. The 2-day time stop adds a third outcome at market, so the true
    breakeven is HIGHER by an unknown amount. Recorded here because the brief
    quoted 43% as 'the bar'; S2 measures realized expectancy instead."""
    c = FlexConfig()
    binary_breakeven = c.max_stop_pct / (c.take_profit_pct + c.max_stop_pct)
    assert round(binary_breakeven, 3) == 0.429


def test_b2_open_position_places_a_native_bracket_with_both_legs():
    """N2 — the core mechanism. Verified live against Alpaca paper 2026-09-12
    (accepted; both legs returned `held`; cancelling the parent cancelled both);
    this pins the payload the engine actually sends."""
    import flex.handler as fh
    sent = {}

    class _Client:
        def submit_order(self, sym, qty, side, **kw):
            sent.update({"sym": sym, "qty": qty, "side": side, **kw})
            return {"id": "o1", "legs": [{"id": "tp", "type": "limit"},
                                         {"id": "sl", "type": "stop"}]}

    e = {"size_shares": 10, "stop_price": 98.5, "entry_price": 100.0,
         "take_profit_price": 102.0}
    ledger = {}
    decisions = {"orders_issued": [], "orders_suppressed": []}
    import unittest.mock as _m
    with _m.patch.object(fh, "write_ledger", lambda *_a, **_k: None), \
         _m.patch.object(fh, "_record_trade_history", lambda *_a, **_k: None):
        ok = fh._open_position(_Client(), ledger, "XYZ", e, {"rationale": "r"},
                               "2026-09-12", decisions, [])
    assert ok is True
    assert sent["order_class"] == "bracket"
    assert sent["take_profit"] == {"limit_price": 102.0}
    assert sent["stop_loss"] == {"stop_price": 98.5}
    # BOTH child legs tracked, so a later cancel/replace can never orphan the
    # take-profit leg.
    assert set(ledger["XYZ"]["order_ids"]) == {"o1", "tp", "sl"}


def test_b2_open_position_falls_back_to_oto_rather_than_entering_naked():
    """A builder that produced no take-profit level must still get its
    protective stop — never enter naked because the profit leg is missing."""
    import flex.handler as fh
    sent = {}

    class _Client:
        def submit_order(self, sym, qty, side, **kw):
            sent.update(kw)
            return {"id": "o1", "legs": [{"id": "sl", "type": "stop"}]}

    e = {"size_shares": 5, "stop_price": 98.5, "entry_price": 100.0,
         "take_profit_price": None}
    import unittest.mock as _m
    with _m.patch.object(fh, "write_ledger", lambda *_a, **_k: None), \
         _m.patch.object(fh, "_record_trade_history", lambda *_a, **_k: None):
        fh._open_position(_Client(), {}, "XYZ", e, {"rationale": "r"},
                          "2026-09-12", {"orders_issued": [], "orders_suppressed": []}, [])
    assert sent["order_class"] == "oto"
    assert sent["stop_loss"] == {"stop_price": 98.5}
    assert "take_profit" not in sent


# --- §8.2: a stale bracket CHILD LEG on a flat position must be sweepable ----

def test_stale_bracket_child_leg_is_swept_after_the_position_closes():
    """§8.2 (2026-09-12) — the OCO-fill risk, closed at the backstop.

    If a take-profit fills and the OCO does not auto-cancel its sibling, a resting
    SELL STOP is left against a position that is now FLAT. In a long-only book
    that is a rejected order at best and a short at worst.

    Two things had to line up and only one did:
      1. `managed` is computed AFTER `del new_ledger[symbol]`, so a symbol closed
         this tick IS an orphan candidate. (Already correct.)
      2. `_sweep_orphan_orders` then filters on `client_order_id` — and a
         bracket/OTO CHILD LEG carries a broker-assigned UUID, NOT `FLEXC-`
         (verified live against Alpaca paper 2026-09-12: parent
         `FLEXC-2026-09-12-KO-entry-...` -> True; legs
         `75e34b5c-...`/`9e3c3d77-...` -> False). So the stale leg was SKIPPED.

    Neither documented fallback covers it: no-naked-long only fires for HELD
    positions, and `_cancel_conflicting_orders` only fires when the daily executor
    submits for that symbol. Note this gap PRE-DATES the bracket — the OTO stop
    child had the same property; B2 only makes the shape reachable.
    """
    from flex.reconcile import reconcile_ledger
    ledger = {"XYZ": {"symbol": "XYZ", "qty_current": 10, "entry_price": 100.0,
                      "initial_stop": 98.5, "order_ids": ["parent1", "tp-leg", "sl-leg"]}}
    stale_stop = {"id": "sl-leg", "symbol": "XYZ", "side": "sell", "type": "stop",
                  "stop_price": 98.5, "client_order_id": "9e3c3d77-uuid-not-flexc"}
    new_ledger, exits, _repairs, orphans = reconcile_ledger(ledger, [], [stale_stop])

    assert "XYZ" not in new_ledger                      # row dropped on close
    assert exits[0]["reason"] == "closed_at_broker"
    assert len(orphans) == 1 and orphans[0]["id"] == "sl-leg"
    assert orphans[0]["engine_owned"] is True           # provably ours

    # ...and the sweep now actually cancels it.
    import flex.handler as fh
    cancelled = []

    class _C:
        def cancel_order(self, oid):
            cancelled.append(oid)

    fh._sweep_orphan_orders(_C(), orphans, {})
    assert cancelled == ["sl-leg"]


def test_sweep_still_never_touches_another_engines_order():
    """The strict-scoping doctrine is unweakened: a DayTrade (`FLEXD-`) or
    daily-executor order is never `engine_owned` and never matches the prefix."""
    import flex.handler as fh
    cancelled = []

    class _C:
        def cancel_order(self, oid):
            cancelled.append(oid)

    foreign = [
        {"id": "d1", "client_order_id": "FLEXD-2026-09-12-AAA", "engine_owned": False},
        {"id": "x1", "client_order_id": "T-20260912-E01", "engine_owned": False},
        {"id": "u1", "client_order_id": "some-uuid", "engine_owned": False},
    ]
    fh._sweep_orphan_orders(_C(), foreign, {})
    assert cancelled == []


# --- G-8 / G-10 (amendment §7.3, §8.1) --------------------------------------

def test_g8_conviction_path_is_dormant_by_default_not_deleted():
    """G-8 — scoped OUT of the news-momentum profile, left intact.

    The conviction path is built around `p_up` vs a ~2-year `base_rate_up` for a
    15-30 DAY horizon, gated by a 2-session confirm/release hysteresis inherited
    from a multi-week overlay. On a ~2-day hold, that confirm delay plus the
    collector's own one-session lag consumes the whole holding period
    (FOLLOWUPS #107). The fix is SCOPING, not retuning `confirm_sessions`."""
    assert FlexConfig().conviction_path_enabled is False
    # the machinery survives for a future slower sub-strategy
    from flex.entry import build_conviction_entry
    assert callable(build_conviction_entry)


def test_g10_noise_band_is_measured_and_is_not_called_an_ATR():
    """G-10 — the fixed-vs-ATR-scaled barrier question (§8.1) gets settled on
    measured data. A true ATR14 is NOT computable here: the light endpoint
    returns close+volume only, and with the engine off the entry pipeline (which
    does compute atr14) never runs. So this is an honest close-only substitute,
    named for what it is."""
    bars = [{"c": 100.0 * (1.04 if i % 2 else 1.0)} for i in range(25)]
    band = catalyst_screen.mean_abs_daily_move_pct(bars, 20)
    assert band is not None and 3.5 < band < 4.5
    # the reading that matters: a 1.5% stop is a FRACTION of one session's move
    assert round(1.5 / band, 2) < 0.5
    assert catalyst_screen.mean_abs_daily_move_pct([{"c": 1.0}] * 5, 20) is None


def test_g10_fixed_barriers_have_no_structural_edge_by_construction():
    """The identity that settles §8.1: for ANY fixed +a/-b pair the driftless
    first-passage probability is exactly b/(a+b) — identical to the breakeven win
    rate. So a fixed-barrier profile has ZERO structural edge at any ratio; it is
    a pure bet on drift arriving before the barriers resolve. Changing 2.0/1.5 to
    other numbers does not create an edge — only moving the barriers OUTSIDE the
    noise band does."""
    for a, b in ((2.0, 1.5), (3.0, 1.0), (1.0, 1.0), (4.0, 2.0)):
        breakeven = b / (a + b)
        driftless_win_prob = b / (a + b)
        assert abs(breakeven - driftless_win_prob) < 1e-12


# --- S1: the kill switch (amendment §2.3) -----------------------------------

def _ct(pnl, day="2026-09-14", tid=None):
    return {"trade_id": tid or f"t{day}{pnl}", "pnl_usd": pnl, "closed_date": day}


def test_s1_fast_trip_needs_no_trade_count_minimum():
    """The whole point of the second arm: the slow arm needs ~20 closed trades
    (~10 trading days at ~2 closes/day post-N4) and a catastrophic path does not
    wait that long. Three trades, -$2,100 on ~$99.5k = 2.11% -> trips."""
    from flex.killswitch import evaluate_kill_switch
    cfg = FlexConfig()
    trades = [_ct(-700, "2026-09-14", "a"), _ct(-700, "2026-09-15", "b"),
              _ct(-700, "2026-09-16", "c")]
    st = evaluate_kill_switch(trades, 99_500.0, cfg, {})
    assert st["tripped"] is True
    assert st["trip_reason"] == "max_drawdown"
    assert st["closed_trades"] == 3 < cfg.kill_switch_min_closed_trades


def test_s1_slow_trip_requires_a_sample_and_fires_below_the_floor():
    from flex.killswitch import evaluate_kill_switch
    cfg = FlexConfig()
    # 20 trades, 8 wins = 40% < 45% floor, but tiny P&L so the fast arm is quiet.
    trades = ([_ct(5, f"2026-09-{i:02d}", f"w{i}") for i in range(1, 9)]
              + [_ct(-4, f"2026-10-{i:02d}", f"l{i}") for i in range(1, 13)])
    st = evaluate_kill_switch(trades, 99_500.0, cfg, {})
    assert st["tripped"] is True and st["trip_reason"] == "hit_rate_floor"
    assert st["hit_rate"] == 0.4
    # one fewer graded trade -> below the sample bar -> armed, not tripped
    st2 = evaluate_kill_switch(trades[:-1], 99_500.0, cfg, {})
    assert st2["tripped"] is False and "armed" in st2["note"]


def test_s1_unknown_pnl_is_excluded_never_counted_as_a_loss():
    """`flex/trades.py` writes `pnl_usd: None` rather than fabricating a number
    when a fill price is unknown. Scoring that as a loss would let a DATA GAP
    trip a risk control -- same absent-vs-zero doctrine as the composite."""
    from flex.killswitch import evaluate_kill_switch, hit_rate
    trades = [_ct(10, "2026-09-14", "a"), {"trade_id": "b", "pnl_usd": None,
                                           "pnl_unavailable_reason": "no fill price",
                                           "closed_date": "2026-09-15"}]
    hr, n = hit_rate(trades)
    assert (hr, n) == (1.0, 1)          # not 0.5
    st = evaluate_kill_switch(trades, 99_500.0, FlexConfig(), {})
    assert st["gradeable_trades"] == 1 and st["closed_trades"] == 2
    # nothing gradeable at all -> None, never a fabricated 0.0 that trips instantly
    assert hit_rate([{"trade_id": "x", "pnl_usd": None}]) == (None, 0)


def test_s1_a_trip_is_STICKY_and_never_clears_itself():
    """Re-enabling is a HUMAN action. Without stickiness the switch would flap
    around the threshold and a sleeve could re-enable itself into the same
    failure."""
    from flex.killswitch import evaluate_kill_switch
    prior = {"tripped": True, "trip_reason": "max_drawdown", "tripped_at": "2026-09-14"}
    # numbers now perfect -- still tripped
    st = evaluate_kill_switch([_ct(500, "2026-09-20", "z")], 99_500.0, FlexConfig(), prior)
    assert st["tripped"] is True
    assert st["tripped_at"] == "2026-09-14"      # original date preserved
    assert "human action" in st["note"]
    # only an explicit human clear releases it
    cleared = {**prior, "cleared_at": "2026-09-21"}
    assert evaluate_kill_switch([_ct(500, "2026-09-20", "z")], 99_500.0,
                                FlexConfig(), cleared)["tripped"] is False


def test_s1_drawdown_is_peak_to_trough_not_cumulative_from_zero():
    """G-11: `max_drawdown_pct` is NAMED drawdown, so it is measured
    peak-to-trough. A sleeve that earns $5k then gives back $2.1k has a 2.11%
    drawdown and trips, even though it is still net +$2.9k. That is a real
    trade-off, recorded rather than silently chosen."""
    from flex.killswitch import cumulative_drawdown_usd, evaluate_kill_switch
    trades = [_ct(5000, "2026-09-14", "a"), _ct(-2100, "2026-09-15", "b")]
    assert cumulative_drawdown_usd(trades) == 2100.0
    st = evaluate_kill_switch(trades, 99_500.0, FlexConfig(), {})
    assert st["tripped"] is True and st["trip_reason"] == "max_drawdown"
    assert st["drawdown_basis"] == "peak_to_trough_cumulative_realized_pnl"


def test_s1_disabled_config_never_trips():
    from flex.killswitch import evaluate_kill_switch
    import dataclasses
    cfg = dataclasses.replace(FlexConfig(), kill_switch_enabled=False)
    st = evaluate_kill_switch([_ct(-50_000, "2026-09-14", "a")], 99_500.0, cfg, {})
    assert st["tripped"] is False
    assert "no automatic brake" in st["note"]
