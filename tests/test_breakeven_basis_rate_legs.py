"""Breakeven bridge basis + rate-leg decomposition (session 2026-09-12).

C1 — `_build_inflation_axis`'s bridge preferred the 5y5y FORWARD breakeven
"least contaminated by near-term noise". Sound for a divergence detector,
backwards for a regime bridge: the 5y5y is expected average inflation over five
years BEGINNING FIVE YEARS FROM NOW — a long-run ANCHORING measure, deliberately
the slowest-moving and least relevant to a 1-3 month regime call. Reading it as
"the inflation bridge" measures whether expectations are anchored and reports the
answer as the current impulse. Live consequence 2026-09-09/10/11: the bridge read
flat -> rising -> flat, where the `rising` came only from T5YIFR being NULL that
session (falling through to the 5Y at +18bp) and the revert to `flat` came only
from T5YIFR returning — while the 5Y had moved to +22bp, clearing the 15bp
threshold, with oil at +24.4% 20d. The bridge flipped because a data outage ENDED.

C3/C4 — `rate_decomposition`, describe-only: a breakeven is a DIFFERENCE and
cannot say which leg moved, so `DGS10 = DFII10 + T10YIE` is split explicitly, with
the identity itself used as a free data-integrity cross-check.

Run: PYTHONPATH=src pytest tests/test_breakeven_basis_rate_legs.py
"""
import copy

import pytest

from collector import handler as ch

THR = 15.0
TODAY = "2026-09-11"


def ser(latest, delta_bp, n=25):
    """21+ newest-first observations whose [0]-[20] gap is exactly `delta_bp`."""
    if delta_bp is None:
        return None
    base = latest - delta_bp / 100.0
    return [{"date": f"2026-09-{max(1, 11 - i):02d}",
             "value": f"{(latest if i == 0 else (base if i >= 20 else latest - (delta_bp/100.0)*(i/20))):.4f}"}
            for i in range(n)]


def macro(t5=None, t10=None, t55=None, nominal=None, real=None, be10=None, core=True):
    m = {}
    for sid, lvl, d in (("T5YIE", 2.40, t5), ("T10YIE", 2.40, t10 if be10 is None else be10),
                        ("T5YIFR", 2.35, t55)):
        if d is not None:
            m[sid] = ser(lvl, d)
    if nominal is not None:
        m["DGS10"] = ser(4.83, nominal)
    if real is not None:
        m["DFII10"] = ser(2.46, real)
    if core:
        for sid in ("PCEPILFE", "CPILFESL", "CPIAUCSL"):
            m[sid] = [{"date": f"2026-{max(1, 9 - i):02d}-01", "value": f"{100.0 + i * 0.2:.4f}"}
                      for i in range(26)]
    return m


def axis(**kw):
    return ch._build_inflation_axis(macro(**kw), None, TODAY)


RD_CFG = ch._RISK_LIMITS_DEFAULTS["rate_decomposition"]


# --- C1: shortest liquid tenor first ----------------------------------------

def test_bridge_prefers_the_5y_spot_over_the_5y5y_forward():
    """THE FIX. With all three present the 5Y spot governs, not the 5y5y."""
    a = axis(t5=22.0, t10=17.0, t55=6.0)
    assert a["bridge_basis"] == "breakeven_5y"
    assert a["bridge_delta_20d_bp"] == 22.0
    assert a["bridge_direction"] == "rising"


def test_the_0909_0910_0911_outage_driven_flip_is_gone():
    """The motivating incident, replayed. Under the OLD order the sequence was
    flat -> rising -> flat, where BOTH transitions were artifacts of T5YIFR's
    availability rather than a change in expectations. Under the new order the
    basis is `breakeven_5y` on all three sessions, so T5YIFR's presence or
    absence cannot move the reading at all.

    Note 09-09 reads `flat`, NOT `rising`: the 5Y was +6bp, below the 15bp
    threshold. The fix removes the outage artifact; it does not manufacture a
    rising call."""
    sessions = [(6.0, 4.0, 1.0), (18.0, 14.0, None), (22.0, 17.0, 6.0)]
    out = [axis(t5=a, t10=b, t55=c) for a, b, c in sessions]
    assert [x["bridge_basis"] for x in out] == ["breakeven_5y"] * 3
    assert [x["bridge_delta_20d_bp"] for x in out] == [6.0, 18.0, 22.0]
    assert [x["bridge_direction"] for x in out] == ["flat", "rising", "rising"]
    # The 09-10 -> 09-11 transition is no longer driven by T5YIFR returning:
    # removing T5YIFR from the 09-11 input changes nothing.
    assert axis(t5=22.0, t10=17.0, t55=None)["bridge_direction"] == "rising"


def test_t5yifr_availability_no_longer_moves_the_bridge():
    """The structural property the fix buys: with the 5Y present, T5YIFR's value
    and its presence/absence are both irrelevant."""
    base = axis(t5=22.0, t10=17.0, t55=6.0)
    for t55 in (None, -40.0, 0.0, 6.0, 80.0):
        a = axis(t5=22.0, t10=17.0, t55=t55)
        assert (a["bridge_basis"], a["bridge_delta_20d_bp"], a["bridge_direction"]) == (
            base["bridge_basis"], base["bridge_delta_20d_bp"], base["bridge_direction"])


def test_threshold_is_unchanged_at_15bp():
    """Basis change only — retuning `breakeven_delta_20d_bp` in the same PR would
    make neither attributable."""
    assert axis(t5=22.0)["bridge_delta_20d_bp_threshold"] == 15.0
    assert axis(t5=14.9)["bridge_direction"] == "flat"
    assert axis(t5=15.1)["bridge_direction"] == "rising"
    assert axis(t5=-15.1)["bridge_direction"] == "falling"


# --- C1 degradation matrix (verification #8) ---------------------------------

@pytest.mark.parametrize("t5,t10,t55,want_basis,want_delta", [
    (22.0, 17.0, 6.0, "breakeven_5y", 22.0),      # all present -> 5Y
    (None, 17.0, 6.0, "breakeven_10y", 17.0),     # 5Y missing -> 10Y
    (None, None, 6.0, "breakeven_5y5y", 6.0),     # both missing -> 5y5y last resort
    (None, None, None, None, None),               # all missing -> no bridge
])
def test_degradation_falls_through_shortest_first(t5, t10, t55, want_basis, want_delta):
    a = axis(t5=t5, t10=t10, t55=t55)
    assert a["bridge_basis"] == want_basis
    assert a["bridge_delta_20d_bp"] == want_delta
    if want_basis is None:
        assert a["bridge_direction"] is None


def test_all_three_deltas_still_reported_regardless_of_basis():
    """The basis picks which one GOVERNS; all three stay visible."""
    a = axis(t5=22.0, t10=17.0, t55=6.0)
    assert a["breakeven_5y_delta_20d_bp"] == 22.0
    assert a["breakeven_10y_delta_20d_bp"] == 17.0
    assert a["breakeven_5y5y_delta_20d_bp"] == 6.0


# --- §0 scope guard: the axis classification itself must not move ------------

def test_inflation_axis_direction_is_untouched_by_the_basis_change():
    """Verification #7. `direction`/`realized_governs` are governed by realized
    core; no breakeven configuration may move either. Swept across every basis
    combination, including ones where `bridge_direction` disagrees violently."""
    base = axis(t5=22.0, t10=17.0, t55=6.0)
    for t5 in (None, -40.0, 0.0, 22.0):
        for t10 in (None, -30.0, 17.0):
            for t55 in (None, -20.0, 6.0, 60.0):
                a = axis(t5=t5, t10=t10, t55=t55)
                assert a["direction"] == base["direction"]
                assert a["reason"] == base["reason"]
                assert a["realized_governs"] is True


# --- C2: the divergence already keys on the 5Y spot -------------------------

def test_divergence_keys_on_be_5y_not_the_5y5y():
    """C2's premise corrected. `_div_leading_vs_lagging_inflation` reads
    `bond_signals.breakevens.be_5y` — the 5Y SPOT — and always has; the stale
    code comment in `_build_inflation_axis` claimed it keyed on the 5y5y. So
    inverting the bridge to the short end MAKES the two consistent, and the
    divergence itself needs no change."""
    m = macro(t5=22.0, t10=17.0, t55=6.0)
    bond = ch._build_bond_signals(copy.deepcopy(m))
    div = ch._div_leading_vs_lagging_inflation(
        axis(t5=22.0, t10=17.0, t55=6.0), bond, ch._load_divergence_config(), TODAY, 7)
    names = [s["name"] for s in div["signals"]]
    assert "be_5y.delta_20d_bp" in names
    assert not any("5y5y" in n for n in names)
    be = next(s for s in div["signals"] if s["name"] == "be_5y.delta_20d_bp")
    assert be["value"] == bond["breakevens"]["be_5y"]["delta_20d_bp"] == 22.0


def test_divergence_and_confirmations_unchanged_across_bridge_bases():
    """Verification #5 — D-3/#78 (the 1-of-3 vs 2-of-3 inflation-side re-risk
    bar) stays untouched: this PR changes one source's input series, never the
    denominator. Mirrors the existing
    `test_new_inflation_quality_block_does_not_become_a_fourth_confirmation_source`
    pattern."""
    m = macro(t5=22.0, t10=17.0, t55=6.0)
    bond = ch._build_bond_signals(copy.deepcopy(m))
    dcfg = ch._load_divergence_config()
    new_ax = axis(t5=22.0, t10=17.0, t55=6.0)
    # The pre-C1 axis differs ONLY in the three bridge_* fields.
    old_ax = {**new_ax, "bridge_basis": "breakeven_5y5y",
              "bridge_delta_20d_bp": 6.0, "bridge_direction": "flat"}
    old_div = ch._div_leading_vs_lagging_inflation(old_ax, bond, dcfg, TODAY, 7)
    new_div = ch._div_leading_vs_lagging_inflation(new_ax, bond, dcfg, TODAY, 7)
    assert old_div == new_div

    g = ch._build_growth_axis(copy.deepcopy(m))
    tw_cfg = {"transition_watch": ch._RISK_LIMITS_DEFAULTS["transition_watch"]}
    old_tw = ch._build_transition_watch([old_div], g, old_ax, tw_cfg, None)
    new_tw = ch._build_transition_watch([new_div], g, new_ax, tw_cfg, None)
    assert old_tw == new_tw
    assert old_tw.get("confirmations_of") == new_tw.get("confirmations_of")


# --- C3: rate_decomposition -------------------------------------------------

def test_real_rate_shock_is_identified():
    """Nominal +30bp driven by the real leg (+26bp) with breakevens nearly flat
    (+4bp) — a growth/policy repricing, not an inflation shock. This is the
    reading the book could not previously make while holding TLT + IEF."""
    rd = ch._build_rate_decomposition(
        macro(t5=5.0, be10=4.0, nominal=30.0, real=26.0), TODAY, RD_CFG)
    assert rd["available"] is True
    assert rd["nominal_delta_20d_bp"] == 30.0
    assert rd["real_delta_20d_bp"] == 26.0
    assert rd["breakeven_delta_20d_bp"] == 4.0
    assert rd["real_share_pct"] == pytest.approx(86.7, abs=0.1)
    assert rd["dominant_driver"] == "real_rate"
    assert "REAL leg" in rd["note"]


def test_inflation_shock_is_identified():
    rd = ch._build_rate_decomposition(
        macro(t5=5.0, be10=26.0, nominal=30.0, real=4.0), TODAY, RD_CFG)
    assert rd["dominant_driver"] == "inflation"
    assert rd["real_share_pct"] == pytest.approx(13.3, abs=0.1)


def test_mixed_band_is_banded_not_a_bare_comparison():
    """A bare `real > breakeven` would flip the label on a fraction of a bp. The
    50/50 case must read `mixed`, and both band edges must hold."""
    rd = ch._build_rate_decomposition(
        macro(t5=5.0, be10=15.0, nominal=30.0, real=15.0), TODAY, RD_CFG)
    assert rd["real_share_pct"] == 50.0
    assert rd["dominant_driver"] == "mixed"
    # Just inside each edge.
    assert ch._build_rate_decomposition(
        macro(t5=5.0, be10=35.0, nominal=100.0, real=65.0), TODAY, RD_CFG
    )["dominant_driver"] == "real_rate"      # exactly 65%
    assert ch._build_rate_decomposition(
        macro(t5=5.0, be10=65.0, nominal=100.0, real=35.0), TODAY, RD_CFG
    )["dominant_driver"] == "inflation"      # exactly 35%


@pytest.mark.parametrize("drop", ["DGS10", "DFII10", "T10YIE"])
def test_missing_leg_degrades_to_unavailable_never_a_verdict(drop):
    """Verification #8 — missing input never fabricates a driver."""
    m = macro(t5=5.0, be10=4.0, nominal=30.0, real=26.0)
    m.pop(drop)
    rd = ch._build_rate_decomposition(m, TODAY, RD_CFG)
    assert rd["available"] is False
    assert rd["dominant_driver"] is None
    assert rd["real_share_pct"] is None
    assert drop in rd["note"]


def test_thin_history_degrades_to_unavailable():
    m = macro(t5=5.0, be10=4.0, nominal=30.0, real=26.0)
    m["DFII10"] = m["DFII10"][:10]      # < 21 observations
    assert ch._build_rate_decomposition(m, TODAY, RD_CFG)["available"] is False


def test_flat_legs_produce_no_driver_rather_than_a_guess():
    rd = ch._build_rate_decomposition(
        macro(t5=5.0, be10=0.0, nominal=0.0, real=0.0), TODAY, RD_CFG)
    assert rd["available"] is True
    assert rd["dominant_driver"] is None
    assert rd["real_share_pct"] is None


# --- C4: the identity cross-check -------------------------------------------

def test_identity_residual_is_computed_every_run():
    """2026-09-11 as observed: DGS10 4.83 - DFII10 2.46 = 2.37 vs T10YIE 2.40,
    a -3bp residual — inside the 15bp tolerance, so no warning."""
    rd = ch._build_rate_decomposition(
        macro(t5=5.0, be10=4.0, nominal=30.0, real=26.0), TODAY, RD_CFG)
    assert rd["identity_residual_bp"] == pytest.approx(-3.0, abs=0.1)
    assert rd["identity_warning"] is False


def test_identity_break_warns_when_all_legs_are_fresh():
    m = macro(t5=5.0, be10=4.0, nominal=30.0, real=26.0)
    m["DFII10"] = ser(1.90, 26.0)      # 4.83 - 1.90 - 2.40 = +53bp residual
    rd = ch._build_rate_decomposition(m, TODAY, RD_CFG)
    assert rd["identity_residual_bp"] == pytest.approx(53.0, abs=0.1)
    assert rd["identity_warning"] is True
    assert "DATA INTEGRITY" in rd["note"]


def test_identity_break_does_not_warn_when_a_leg_is_stale():
    """Differing as-of dates are the EXPECTED benign cause — a stale leg must not
    raise a data-integrity alarm, and every leg's as_of/days_stale is carried so
    the reader can dismiss it at a glance."""
    m = macro(t5=5.0, be10=4.0, nominal=30.0, real=26.0)
    m["DFII10"] = [{**r, "date": "2026-07-01"} for r in ser(1.90, 26.0)]
    rd = ch._build_rate_decomposition(m, TODAY, RD_CFG)
    assert abs(rd["identity_residual_bp"]) > RD_CFG["identity_tolerance_bp"]
    assert rd["identity_warning"] is False
    assert rd["legs"]["real"]["as_of"] == "2026-07-01"
    assert rd["legs"]["real"]["days_stale"] > RD_CFG["staleness_days"]


# --- C3 scope guard: bond_signals must be byte-identical --------------------

def test_bond_signals_is_byte_identical_with_rate_decomposition_populated():
    """Verification #6. DFII10 already feeds bond_signals' `systemic` sub-score
    and its 2.5% real-yield hard trigger; `rate_decomposition` must neither
    duplicate, shadow nor alter either. `_build_bond_signals` takes macro_data
    only and never sees this block — pinned here so a future refactor that
    threads it in has to face this test."""
    m = macro(t5=22.0, t10=17.0, t55=6.0, nominal=30.0, real=26.0)
    before = ch._build_bond_signals(copy.deepcopy(m))
    rd = ch._build_rate_decomposition(copy.deepcopy(m), TODAY, RD_CFG)
    after = ch._build_bond_signals(copy.deepcopy(m))
    assert rd["available"] is True
    assert before == after
    assert "rate_decomposition" not in before


def test_rate_decomposition_config_is_loadable_from_risk_limits():
    cfg = ch._load_risk_limits().get("rate_decomposition")
    assert cfg is not None, "rate_decomposition missing from risk-limits.json"
    for k in ("identity_tolerance_bp", "real_share_real_rate_min",
              "real_share_inflation_max", "staleness_days"):
        assert k in cfg
    assert cfg["real_share_real_rate_min"] > cfg["real_share_inflation_max"]
