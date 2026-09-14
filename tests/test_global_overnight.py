"""global_overnight (session 2026-09-13, FOLLOWUPS #34) — the overseas session
read and its `global_sector_tone` catalyst component.

Run: PYTHONPATH=src pytest tests/test_global_overnight.py

Every test below was confirmed FAILING on pre-fix source via `git stash push -u
-- src/` isolation, except the three noted inline as invariant guards (they pin
behaviour this cycle must NOT disturb, so passing on master is the correct
result and is recorded rather than counted as evidence).
"""
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector import catalyst_screen  # noqa: E402
from collector.catalyst_screen import (  # noqa: E402
    applicable_components,
    composite_score,
    rankability,
)
from collector.global_overnight import (  # noqa: E402
    MIN_SECTORS_FOR_CROSS_SECTION,
    build_global_overnight,
    change_pct,
    excess_score,
    freshness,
    parse_as_of_et,
    sector_tone_for,
)
from collector.handler import (  # noqa: E402
    _build_catalyst_screen,
    _build_global_overnight,
    _fetch_global_overnight_rows,
    _load_global_overnight_config,
)

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 14, 9, 0, tzinfo=ET)          # a Monday, 09:00 ET
FRESH_TS = int(datetime(2026, 9, 14, 4, 0, tzinfo=ET).timestamp())   # 04:00 ET today
STALE_TS = int(datetime(2026, 9, 11, 16, 0, tzinfo=ET).timestamp())  # Friday's US close


def _srow(symbol, pct, ts=FRESH_TS):
    return {"symbol": symbol, "changePercentage": pct, "timestamp": ts,
            "source_basis": "adr_proxy"}


def _rrow(symbol, region, pct, basis="direct_index", ts=FRESH_TS):
    return {"symbol": symbol, "region": region, "changePercentage": pct,
            "timestamp": ts, "source_basis": basis}


_SECTORS = {"Technology": ["TSM", "ASML"], "Energy": ["SHEL"],
            "Healthcare": ["AZN"], "Basic Materials": ["RIO"]}


def _block(sector_rows=None, region_rows=None, now=NOW, **kw):
    rows = sector_rows if sector_rows is not None else [
        _srow("TSM", 2.4), _srow("ASML", 2.0), _srow("SHEL", 0.1),
        _srow("AZN", -0.4), _srow("RIO", 1.0),
    ]
    return build_global_overnight(
        region_rows or [], rows, None, _SECTORS, now, **kw)


# --- G1.3, THE required test -------------------------------------------------
# "Add a test asserting a candidate that would be unrankable without
# `global_sector_tone` is still unrankable with it."

def test_global_sector_tone_can_never_confer_rankability():
    """A sector-wide reading must change RANK, never ADMISSION.

    The candidate has news_recency + momentum and nothing else — 2 real
    components against MIN_COMPONENTS_RANKABLE (3), so it is unrankable. Adding
    a populated `global_sector_tone` must NOT tip it over the bar: every name in
    a covered sector receives the identical value, so counting it would silently
    drop the effective bar to 2 candidate-specific components for a whole sector
    on any morning the overseas block happens to resolve, and raise it back on a
    morning it does not.
    """
    applicable = ("news_recency", "momentum", "global_sector_tone")
    without = {"news_recency": 0.8, "momentum": 0.6, "global_sector_tone": None}
    with_tone = {"news_recency": 0.8, "momentum": 0.6, "global_sector_tone": 0.99}

    assert rankability(without, applicable) == (
        False, "insufficient_components:2<3")
    # The load-bearing assertion: identical verdict, identical reason.
    assert rankability(with_tone, applicable) == rankability(without, applicable)


def test_global_sector_tone_still_counts_toward_the_composite_denominator():
    """The other half of the asymmetry — it is excluded from rankability ONLY.

    A candidate that IS rankable on its own merits must see the tone move its
    score, otherwise the component would be inert and there would be no point
    collecting it.
    """
    base = {"news_recency": 0.6, "momentum": 0.6, "volume_surge": 0.6,
            "global_sector_tone": None}
    lifted = {**base, "global_sector_tone": 1.0}
    applicable = ("news_recency", "momentum", "volume_surge", "global_sector_tone")

    cs_base = composite_score(base, applicable)
    cs_lift = composite_score(lifted, applicable)

    assert cs_base["rankable"] is True and cs_lift["rankable"] is True
    assert cs_base["components_available"] == 3
    assert cs_lift["components_available"] == 4          # denominator DID grow
    assert cs_lift["score"] > cs_base["score"]
    assert cs_lift["score"] == round((0.6 * 3 + 1.0) / 4, 4)


def test_non_rankability_set_names_exactly_the_global_tone_component():
    assert catalyst_screen._NON_RANKABILITY_COMPONENTS == ("global_sector_tone",)
    assert "global_sector_tone" in catalyst_screen.COMPONENTS


def test_required_and_price_confirmation_clauses_are_unaffected():
    """INVARIANT GUARD (passes on master by construction) — R2's own two clauses
    must keep binding exactly as before; the exclusion touches the count only."""
    applicable = catalyst_screen.COMPONENTS
    no_news = {"momentum": 0.9, "volume_surge": 0.9, "news_tone": 0.9,
               "global_sector_tone": 1.0}
    assert rankability(no_news, applicable) == (False, "missing_required:news_recency")

    no_price = {"news_recency": 0.9, "news_tone": 0.9, "political_flow": 0.9,
                "global_sector_tone": 1.0}
    assert rankability(no_price, applicable) == (False, "missing_price_confirmation")


# --- absent-vs-zero / structurally-absent ------------------------------------

def test_structurally_absent_sector_is_not_applicable_never_missing_data():
    block = _block(structurally_absent_sectors=("Utilities",))
    tone, reason = sector_tone_for(block, "Utilities", ("Utilities",))
    assert tone is None                      # NOT 0.0, and not 0.5 either
    assert reason == "not_applicable"
    # It must not even appear as a gap — a structurally-domestic sector is not a
    # data gap that could resolve tomorrow.
    assert "Utilities" not in (block["coverage"]["sector_gaps"] or {})
    assert "Utilities" not in block["sector_tone"]


def test_not_applicable_drops_the_component_from_the_applicable_set():
    out = applicable_components(is_fund=False, global_tone_not_applicable=True)
    assert "global_sector_tone" not in out
    assert "earnings_proximity" in out        # the fund split is independent
    both = applicable_components(is_fund=True, global_tone_not_applicable=True)
    assert "global_sector_tone" not in both and "political_flow" not in both


def test_a_covered_sector_with_no_fresh_member_is_missing_data_not_absent():
    """The opposite case, and the distinction that matters: Energy is configured
    and could resolve tomorrow, so it is a GAP, not `not_applicable`."""
    rows = [_srow("TSM", 2.4), _srow("ASML", 2.0), _srow("AZN", -0.4),
            _srow("RIO", 1.0), _srow("SHEL", 0.1, ts=STALE_TS)]
    block = _block(sector_rows=rows)
    assert block["coverage"]["sector_gaps"] == {"Energy": "no_fresh_members"}
    tone, reason = sector_tone_for(block, "Energy")
    assert tone is None and reason == "sector_not_covered"


def test_absence_never_becomes_a_neutral_score_anywhere():
    assert excess_score(None) is None
    assert sector_tone_for(None, "Technology") == (None, "block_unavailable")
    assert sector_tone_for({"available": False}, "Technology")[0] is None
    assert sector_tone_for(_block(), None) == (None, "no_sector")


# --- the cross-sector excess (the central design decision) -------------------

def test_score_is_the_cross_sector_excess_not_the_absolute_move():
    """A uniform risk-on morning carries NO sector information and must not
    manufacture any: every sector up the same 2% scores exactly 0.5."""
    rows = [_srow("TSM", 2.0), _srow("ASML", 2.0), _srow("SHEL", 2.0),
            _srow("AZN", 2.0), _srow("RIO", 2.0)]
    block = _block(sector_rows=rows)
    assert block["available"] is True
    assert block["sector_baseline_pct"] == 2.0
    assert {v["tone"] for v in block["sector_tone"].values()} == {0.5}
    assert all(v["excess_pct"] == 0.0 for v in block["sector_tone"].values())
    # The absolute move is still carried — described, never scored.
    assert all(v["pct"] == 2.0 for v in block["sector_tone"].values())


def test_a_real_tilt_separates_the_bought_sector_from_the_sold_one():
    block = _block()   # Tech +2.2, Energy +0.1, Health -0.4, Materials +1.0
    tone = block["sector_tone"]
    assert tone["Technology"]["tone"] > tone["Basic Materials"]["tone"]
    assert tone["Basic Materials"]["tone"] > tone["Energy"]["tone"]
    assert tone["Energy"]["tone"] > tone["Healthcare"]["tone"]
    assert tone["Technology"]["excess_pct"] > 0 > tone["Healthcare"]["excess_pct"]


def test_a_sector_is_the_equal_weighted_mean_of_its_fresh_members_only():
    rows = [_srow("TSM", 3.0), _srow("ASML", 1.0, ts=STALE_TS),
            _srow("SHEL", 0.0), _srow("AZN", 0.0), _srow("RIO", 0.0)]
    block = _block(sector_rows=rows)
    tech = block["sector_tone"]["Technology"]
    assert tech["pct"] == 3.0                    # ASML dropped, not averaged in
    assert tech["members_read"] == ["TSM"]
    assert tech["members_configured"] == 2


def test_cross_section_below_the_minimum_is_withheld_not_published():
    rows = [_srow("TSM", 2.0), _srow("ASML", 2.0), _srow("SHEL", 0.5)]
    block = _block(sector_rows=rows)          # only 2 sectors resolve
    assert block["available"] is False
    assert block["sector_tone"] == {}
    assert block["unavailable_reason"] == (
        f"cross_section_too_thin:2<{MIN_SECTORS_FOR_CROSS_SECTION}")
    assert block["coverage"]["sectors_with_data"] == 2
    assert sector_tone_for(block, "Technology") == (None, "block_unavailable")


def test_excess_score_is_the_same_symmetric_clamp_momentum_uses():
    assert excess_score(0.0) == 0.5
    assert excess_score(99.0) == 1.0
    assert excess_score(-99.0) == 0.0
    assert excess_score(0.75, cap_pct=1.5) == 0.75


# --- freshness: the self-measuring safety property ---------------------------

def test_prior_session_rows_are_stale_and_collapse_the_block_to_a_no_op():
    """Monday's live probe, simulated: if the ADR basket never refreshes
    pre-market, every row carries Friday's close, everything drops out, and the
    component is ABSENT for every candidate — a safe no-op, diagnosable from
    `coverage` alone rather than by re-running."""
    rows = [_srow(s, 1.0, ts=STALE_TS) for s in ("TSM", "ASML", "SHEL", "AZN", "RIO")]
    block = _block(sector_rows=rows)
    assert block["available"] is False
    assert block["unavailable_reason"] == "no_fresh_sector_data"
    assert block["coverage"]["sector_symbols_read"] == 0
    reasons = {r["reason"] for r in block["coverage"]["sector_symbols_rejected"]}
    assert reasons == {"stale_not_current_session"}
    assert all("age_hours" in r for r in block["coverage"]["sector_symbols_rejected"])


def test_freshness_is_session_date_equality_not_an_age_bound():
    """Date equality is load-bearing: Tokyo's 02:00 ET close (7h old) must pass
    while yesterday's 16:00 ET US close (17h old) must fail — no single age
    threshold separates those two."""
    tokyo_close = datetime(2026, 9, 14, 2, 0, tzinfo=ET)
    prior_us_close = datetime(2026, 9, 11, 16, 0, tzinfo=ET)
    fresh_t, age_t = freshness(tokyo_close, NOW)
    fresh_p, age_p = freshness(prior_us_close, NOW)
    assert fresh_t is True and age_t == 7.0
    assert fresh_p is False and age_p > age_t     # older in hours AND rejected


def test_a_future_dated_or_unreadable_timestamp_is_dropped_never_assumed_fresh():
    future = NOW + timedelta(hours=3)
    assert freshness(future, NOW)[0] is False
    assert freshness(None, NOW) == (False, None)
    assert parse_as_of_et("not-a-date") is None
    assert parse_as_of_et(None) is None
    assert parse_as_of_et(0) is None

    rows = [{"symbol": "TSM", "changePercentage": 9.0, "timestamp": "garbage"}]
    block = _block(sector_rows=rows)
    assert block["coverage"]["sector_symbols_rejected"][0]["reason"] == (
        "unreadable_timestamp")


def test_parse_accepts_seconds_milliseconds_and_iso():
    want = datetime(2026, 9, 14, 4, 0, tzinfo=ET)
    assert parse_as_of_et(FRESH_TS) == want
    assert parse_as_of_et(FRESH_TS * 1000) == want
    assert parse_as_of_et("2026-09-14T04:00:00").astimezone(ET) == want


# --- change % resolution -----------------------------------------------------

def test_change_pct_prefers_the_stated_field_then_prev_close_then_the_fallback():
    assert change_pct({"symbol": "X", "changePercentage": 1.25}) == 1.25
    assert change_pct({"symbol": "X", "changesPercentage": -0.5}) == -0.5
    assert change_pct({"symbol": "X", "price": 101.0, "previousClose": 100.0}) == 1.0
    assert change_pct({"symbol": "X", "price": 99.0}, {"X": 100.0}) == -1.0
    # No resolvable pair -> None, never a fabricated flat session.
    assert change_pct({"symbol": "X", "price": 99.0}) is None
    assert change_pct({"symbol": "X", "price": 99.0, "previousClose": 0}) is None


def test_a_row_with_no_change_basis_is_rejected_with_its_own_reason():
    rows = [{"symbol": "TSM", "price": 100.0, "timestamp": FRESH_TS}]
    block = _block(sector_rows=rows)
    rej = block["coverage"]["sector_symbols_rejected"][0]
    assert rej["symbol"] == "TSM" and rej["reason"] == "no_change_basis"


# --- regions -----------------------------------------------------------------

def test_regions_are_described_with_their_own_source_basis():
    regions = [_rrow("^N225", "japan", 1.2),
               _rrow("EWG", "germany", -0.3, basis="region_proxy")]
    block = _block(region_rows=regions)
    assert block["region_tone"]["japan"]["source_basis"] == "direct_index"
    assert block["region_tone"]["germany"]["source_basis"] == "region_proxy"
    assert block["region_breadth_up"] == 0.5


def test_region_fetch_falls_back_to_the_proxy_on_a_restricted_index():
    """The 402 fallback is decided by the RESPONSE, not a hardcoded list of
    restricted symbols — so a plan change starts using the real index with no
    code edit, and a newly-restricted one degrades instead of vanishing."""
    class _FakeFMP:
        def __init__(self):
            self.calls = []

        def get_quote(self, sym):
            self.calls.append(sym)
            if sym in ("^GDAXI", "^KS11"):      # HTTP 402 -> client returns None
                return None
            return {"symbol": sym, "changePercentage": 1.0, "timestamp": FRESH_TS}

    fmp = _FakeFMP()
    cfg = {
        "regions": [
            {"region": "japan", "index": "^N225", "proxy": "EWJ"},
            {"region": "germany", "index": "^GDAXI", "proxy": "EWG"},
            {"region": "korea", "index": None, "proxy": "EWY"},
        ],
        "sector_proxies": {"Technology": ["TSM", "TSM"]},   # dedupe check
    }
    regions, sectors = _fetch_global_overnight_rows(fmp, cfg)
    by_region = {r["region"]: r for r in regions}

    assert by_region["japan"]["symbol"] == "^N225"
    assert by_region["japan"]["source_basis"] == "direct_index"
    assert by_region["germany"]["symbol"] == "EWG"
    assert by_region["germany"]["source_basis"] == "region_proxy"
    assert by_region["korea"]["symbol"] == "EWY"
    assert by_region["korea"]["source_basis"] == "region_proxy"
    assert "^KS11" not in fmp.calls              # no index configured, never tried
    assert [s["symbol"] for s in sectors] == ["TSM"]   # deduped, one call each


def test_a_region_with_no_quote_at_all_drops_out_silently():
    class _DeadFMP:
        def get_quote(self, sym):
            return None

    regions, _ = _fetch_global_overnight_rows(
        _DeadFMP(), {"regions": [{"region": "uk", "index": "^FTSE", "proxy": "EWU"}],
                     "sector_proxies": {}})
    assert regions == []


# --- shipped config ----------------------------------------------------------

def test_shipped_config_is_loadable_and_marks_the_domestic_sectors_absent():
    cfg = _load_global_overnight_config()
    assert len(cfg["regions"]) >= 6
    assert set(cfg["structurally_absent_sectors"]) == {
        "Real Estate", "Utilities", "Consumer Defensive"}
    # No sector may be both mapped and declared structurally absent.
    assert not (set(cfg["sector_proxies"]) & set(cfg["structurally_absent_sectors"]))
    # Every configured region resolves to something.
    for r in cfg["regions"]:
        assert r.get("index") or r.get("proxy")


# --- collector glue: non-fatal, and inert when unavailable -------------------

def test_build_is_non_fatal_and_returns_a_well_formed_block_on_failure():
    class _BoomFMP:
        def get_quote(self, sym):
            raise RuntimeError("FMP down")

    block = _build_global_overnight(_BoomFMP(), NOW)
    assert block["available"] is False
    assert block["sector_tone"] == {} and block["region_tone"] == {}
    assert block["structurally_absent_sectors"]      # still populated from config
    # sector_tone_for must cope with the degraded shape.
    assert sector_tone_for(block, "Technology")[0] is None


def _bars(n=25, close=100.0, vol=5_000_000):
    out = [{"c": close, "v": vol} for _ in range(n - 1)]
    out.append({"c": close * 1.05, "v": vol * 2})
    return out


def _screen(go_block, sector="Technology"):
    return _build_catalyst_screen(
        discovery=["NVDA"],
        profiles_by_symbol={"NVDA": {"symbol": "NVDA", "sector": sector}},
        bars_by_symbol={"NVDA": _bars()},
        earnings_market_rows=[], stock_news=[], congressional=[],
        held=set(), exclude=set(), legacy_blocked=set(),
        min_adv_usd=50_000_000.0, today="2026-09-14", top_n=5,
        global_overnight_block=go_block,
    )


def test_an_unavailable_block_leaves_the_component_absent_and_changes_nothing():
    """The dry-run safety property: no block and an unavailable block must
    produce the same ledger row, so a failed overseas read can never alter a
    nomination."""
    none_row = _screen(None)["ledger"][0]
    dead_row = _screen({"available": False, "sector_tone": {},
                        "structurally_absent_sectors": []})["ledger"][0]

    assert none_row["components"]["global_sector_tone"] is None
    assert dead_row["components"]["global_sector_tone"] is None
    assert none_row["score"] == dead_row["score"]
    assert none_row["rankable"] == dead_row["rankable"]


def test_a_live_block_populates_the_component_and_records_its_basis():
    result = _screen(_block())
    row = result["ledger"][0]
    assert row["components"]["global_sector_tone"] == (
        _block()["sector_tone"]["Technology"]["tone"])
    assert row["basis"]["global_sector_tone_reason"] is None
    assert result["global_overnight_available"] is True
    assert result["non_rankability_components"] == ["global_sector_tone"]


def test_a_domestic_sector_candidate_reads_not_applicable_end_to_end():
    block = _block(structurally_absent_sectors=("Utilities",))
    row = _screen(block, sector="Utilities")["ledger"][0]
    assert row["components"]["global_sector_tone"] is None
    assert row["basis"]["global_sector_tone_reason"] == "not_applicable"
    assert "global_sector_tone" in row["components_not_applicable"]
    assert "global_sector_tone" not in row["components_missing"]
