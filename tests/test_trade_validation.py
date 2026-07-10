"""Unit tests for the Tier-1 trade validator (#28 — make "enforced downstream" true).

Covers the four rule families: V1 gate/roster (absolute), V2 exemption (absolute),
V3 window rule (reference ± max(residual, band), floor-protected — D1's mirror image),
V4 mechanical sanity (held/cash clamps, integer shares, min-notional on clamped
remainders), plus the aggregate ceiling assertion, the band_enforcement pass-through
replay, and the executor's fail-closed auto-path refusal. Run:
    PYTHONPATH=src pytest tests/test_trade_validation.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from executor.handler import _validation_refusal  # noqa: E402
from shared.reference_execution import reconcile  # noqa: E402
from shared.trade_validation import validate_trades  # noqa: E402

CFG = {
    "override_protocol": {
        "max_magnitude_pp": 15.0, "re_risk_min_evidence": 2, "gap_band_pp": 5.0,
    },
    "reference_execution": {
        "tranche_pp_max": 10.0, "enforce": True,
        "enforcement_turnover_max_pct": 20.0, "min_notional_usd": 115.0,
    },
    "sleeve_floor_pct_of_core": 0.1,
    "active_quadrant_ceiling_pct_of_core": 90.0,
    "exempt_holds": ["AMZN", "GOOGL"],
}


def _ctx(**kw):
    base = {
        "deployment_gate": "closed",
        "equity_usd": 100_000.0,
        "cash_usd": 20_000.0,
        "date": "2026-07-06",
        "exempt_holds": ["AMZN", "GOOGL"],
    }
    base.update(kw)
    return base


def _gap(sym, cur, ref, price=100.0, held=None):
    """Gap row; held defaults to current market value / price at $100K equity."""
    if held is None:
        held = float(int(cur / 100 * 100_000 / price))
    return {"symbol": sym, "current_pct": cur, "reference_pct": ref,
            "price": price, "held_qty": held}


def _dec(sleeve, outcome="accepted", magnitude=10.0, direction="de_risk"):
    return {"outcome": outcome, "reasons": [],
            "override": {"sleeve": sleeve, "magnitude_pp": magnitude,
                         "direction": direction}}


def _t(sym, side, qty, **kw):
    return {"id": f"T-20260706-{sym}-{side}", "symbol": sym, "side": side,
            "quantity": qty, **kw}


def _statuses(res):
    return {t["symbol"]: t["validation"]["status"] for t in res["trades"]}


# --- V1: gate + roster ----------------------------------------------------------

def test_gate_closed_amplifier_buy_rejected_damper_buy_passes():
    gaps = [_gap("QQQ", 1.0, 10.0), _gap("GLD", 1.0, 10.0)]
    res = validate_trades(gaps, [_t("QQQ", "buy", 10), _t("GLD", "buy", 10)],
                          [], CFG, _ctx(deployment_gate="closed"))
    assert [r["symbol"] for r in res["rejected"]] == ["QQQ"]
    assert any("gate" in s for s in res["rejected"][0]["validation"]["reasons"])
    assert _statuses(res) == {"GLD": "passed"}


def test_gate_open_amplifier_buy_toward_reference_passes():
    gaps = [_gap("QQQ", 1.0, 10.0)]
    res = validate_trades(gaps, [_t("QQQ", "buy", 10)], [], CFG,
                          _ctx(deployment_gate="open"))
    assert res["rejected"] == []
    assert _statuses(res)["QQQ"] == "passed"


def test_gate_unknown_treated_as_closed():
    """Fail-closed: a missing/unknown gate forbids amplifier buys like `closed`."""
    res = validate_trades([_gap("QQQ", 1.0, 10.0)], [_t("QQQ", "buy", 10)],
                          [], CFG, _ctx(deployment_gate=None))
    assert len(res["rejected"]) == 1


def test_off_roster_buy_rejected_even_gate_open():
    res = validate_trades([], [_t("MEME", "buy", 500)], [], CFG,
                          _ctx(deployment_gate="open"))
    assert len(res["rejected"]) == 1
    assert any("off-roster" in s for s in res["rejected"][0]["validation"]["reasons"])


# --- V2: exemption ----------------------------------------------------------------

def test_exempt_amzn_sell_rejected():
    gaps = [_gap("AMZN", 5.0, 3.0)]
    res = validate_trades(gaps, [_t("AMZN", "sell", 5)], [], CFG, _ctx())
    assert len(res["rejected"]) == 1
    assert any("exempt" in s for s in res["rejected"][0]["validation"]["reasons"])


# --- V3: window rule ---------------------------------------------------------------

def test_overweight_trim_toward_reference_passes_even_landing_outside_window():
    """Tranche-paced partial trim: cur 20 → post 15, window edge 7 — passes (D2)."""
    gaps = [_gap("SPY", 20.0, 2.0, held=200)]
    res = validate_trades(gaps, [_t("SPY", "sell", 50)], [], CFG, _ctx())
    assert res["rejected"] == []
    assert _statuses(res)["SPY"] == "passed"


def test_sell_overshoot_clamped_to_window_edge():
    """cur 20, ref 10, W 5 → lo 5: a sell to 2% is clamped to land at 5%."""
    gaps = [_gap("SPY", 20.0, 10.0, held=200)]
    res = validate_trades(gaps, [_t("SPY", "sell", 180)], [], CFG, _ctx())
    t = res["trades"][0]
    assert t["validation"]["status"] == "clamped"
    assert t["quantity"] == 150   # (20−5)pp of $100K at $100
    assert res["rejected"] == []


def test_floor_protects_when_window_dips_below_it():
    """ref 2, W 5 → ref−W < 0: the explicit floor bound (0.1%) is the sell limit,
    and integer shares leave ≥1 share."""
    gaps = [_gap("SPY", 20.0, 2.0, held=200)]
    res = validate_trades(gaps, [_t("SPY", "sell", 200)], [], CFG, _ctx())
    t = res["trades"][0]
    assert t["validation"]["status"] == "clamped"
    assert t["quantity"] == 199   # leaves 0.1% = $100 → 1 share of $100
    assert res["rejected"] == []


def test_away_buy_inside_accepted_residual_passes_outside_clamped():
    """cur 6, ref 2: with an accepted 10pp override (W=10, hi=12) a buy to 11 passes;
    a buy to 20 is clamped to the 12% edge."""
    gaps = [_gap("GLD", 6.0, 2.0)]
    decs = [_dec("GLD", magnitude=10.0)]
    ok = validate_trades(gaps, [_t("GLD", "buy", 50)], decs, CFG,
                         _ctx(cash_usd=50_000.0))   # → post 11%
    assert ok["trades"][0]["validation"]["status"] == "passed"
    clamp = validate_trades(gaps, [_t("GLD", "buy", 140)], decs, CFG,
                            _ctx(cash_usd=50_000.0))   # → post 20% → clamp at 12%
    t = clamp["trades"][0]
    assert t["validation"]["status"] == "clamped"
    assert t["quantity"] == 60   # (12−6)pp of $100K at $100


def test_away_buy_with_rejected_override_gets_band_only_window():
    """A REJECTED override shelters nothing: W falls back to the 5pp band (hi=7),
    so the same buy is clamped much harder."""
    gaps = [_gap("GLD", 6.0, 2.0)]
    decs = [_dec("GLD", outcome="rejected", magnitude=10.0)]
    res = validate_trades(gaps, [_t("GLD", "buy", 140)], decs, CFG,
                          _ctx(cash_usd=50_000.0))
    assert res["trades"][0]["quantity"] == 10   # (7−6)pp of $100K at $100


def test_buy_of_already_overweight_sleeve_rejected():
    """cur beyond the window ceiling: any buy moves further out — rejected."""
    gaps = [_gap("GLD", 13.0, 2.0)]   # hi = 2+5 = 7 < 13
    res = validate_trades(gaps, [_t("GLD", "buy", 10)], [], CFG, _ctx())
    assert len(res["rejected"]) == 1
    assert any("already-overweight" in s
               for s in res["rejected"][0]["validation"]["reasons"])


def test_sell_of_already_underweight_sleeve_rejected():
    gaps = [_gap("TLT", 1.0, 10.0, held=10)]   # lo = 5 > 1
    res = validate_trades(gaps, [_t("TLT", "sell", 5)], [], CFG, _ctx())
    assert len(res["rejected"]) == 1
    assert any("already-underweight" in s
               for s in res["rejected"][0]["validation"]["reasons"])


# --- V4: mechanical ---------------------------------------------------------------

def test_sell_qty_clamped_to_held():
    gaps = [_gap("SPY", 20.0, 10.0, held=30)]   # window lo=5 permits 150; held only 30
    res = validate_trades(gaps, [_t("SPY", "sell", 50)], [], CFG, _ctx())
    assert res["trades"][0]["quantity"] == 30
    assert res["trades"][0]["validation"]["status"] == "clamped"


def test_sell_of_unheld_sleeve_rejected():
    gaps = [_gap("TLT", 0.0, 2.0, held=0)]   # in targets, not held
    res = validate_trades(gaps, [_t("TLT", "sell", 10)], [], CFG, _ctx())
    assert len(res["rejected"]) == 1


def test_buy_clamped_to_cash_after_sells():
    """Sells-first ordering: the SPY sell proceeds fund most of the GLD buy; the
    remainder above cash is clamped off."""
    gaps = [_gap("SPY", 20.0, 10.0, held=200), _gap("GLD", 1.0, 30.0)]
    trades = [_t("GLD", "buy", 200), _t("SPY", "sell", 100)]   # buy listed first
    res = validate_trades(gaps, trades, [_dec("GLD", magnitude=15.0)], CFG,
                          _ctx(cash_usd=1_000.0))
    by_sym = {t["symbol"]: t for t in res["trades"]}
    assert [t["symbol"] for t in res["trades"]] == ["SPY", "GLD"]   # reordered
    # cash 1,000 + sell 10,000 = 11,000 → 110 shares max (window hi=16 permits 150)
    assert by_sym["GLD"]["quantity"] == 110
    assert by_sym["GLD"]["validation"]["status"] == "clamped"


def test_fractional_qty_floored_and_zero_rejected():
    gaps = [_gap("SPY", 20.0, 10.0, held=200)]
    res = validate_trades(gaps, [_t("SPY", "sell", 10.7), _t("GLD", "buy", 0.4)],
                          [], CFG, _ctx())
    assert res["trades"][0]["quantity"] == 10
    assert len(res["rejected"]) == 1   # the 0.4-share buy floors to zero


def test_clamped_remainder_below_min_notional_rejected():
    """A clamp that leaves <$115 is dust — rejected, not submitted."""
    gaps = [_gap("GLD", 6.9, 2.0)]   # hi=7 → clamp allows 0.1pp = $100 → 1 share = $100
    res = validate_trades(gaps, [_t("GLD", "buy", 50)], [], CFG,
                          _ctx(cash_usd=50_000.0))
    assert len(res["rejected"]) == 1
    assert any("min notional" in s for s in res["rejected"][0]["validation"]["reasons"])


# --- integration: enforcement pass-through + malicious replay ----------------------

def test_band_enforcement_trades_pass_untouched():
    """Reconcile's synthesized trades run through the validator unmodified —
    a rejection there would be a reconcile bug."""
    gaps = [
        _gap("SPY", 17.25, 0.5, price=550.0, held=31),
        _gap("QQQ", 13.91, 0.5, price=480.0, held=28),
        _gap("GLD", 6.0, 20.0, price=205.0, held=29),
    ]
    recon = reconcile(gaps, [], [], CFG, _ctx(cash_usd=5_000.0))
    assert len(recon["enforced_trades"]) == 3
    res = validate_trades(gaps, recon["enforced_trades"], [], CFG,
                          _ctx(cash_usd=5_000.0))
    assert res["rejected"] == []
    assert all(t["validation"]["status"] == "passed" for t in res["trades"])
    assert [t["quantity"] for t in res["trades"]] == \
        [t["quantity"] for t in recon["enforced_trades"]]


def test_malicious_file_yields_zero_submittable_violations():
    """The #28 acceptance replay: gate-closed QQQ buy, exempt AMZN sell, off-roster
    MEME buy all stripped; the SPY floor-breach sell clamped — nothing violating
    survives to the executor."""
    gaps = [
        _gap("QQQ", 1.0, 10.0),
        _gap("AMZN", 5.0, 5.0, held=50),
        _gap("SPY", 20.0, 2.0, held=200),
    ]
    trades = [
        _t("QQQ", "buy", 500),
        _t("AMZN", "sell", 50),
        _t("MEME", "buy", 500),
        _t("SPY", "sell", 200),   # sell-to-zero through the floor
    ]
    res = validate_trades(gaps, trades, [], CFG, _ctx(deployment_gate="closed"))
    assert {r["symbol"] for r in res["rejected"]} == {"QQQ", "AMZN", "MEME"}
    assert len(res["trades"]) == 1
    spy = res["trades"][0]
    assert spy["symbol"] == "SPY" and spy["quantity"] == 199   # floor-protected
    assert all(isinstance(t.get("validation"), dict) for t in res["trades"])


def test_no_account_still_applies_absolute_rules():
    """Reference/account unavailable: V3/V4 skip, but gate/roster/exemption and
    stamping still hold."""
    res = validate_trades([], [
        _t("QQQ", "buy", 10), _t("AMZN", "sell", 5), _t("GLD", "buy", 10),
    ], [], CFG, {"deployment_gate": "closed", "exempt_holds": ["AMZN", "GOOGL"]})
    assert {r["symbol"] for r in res["rejected"]} == {"QQQ", "AMZN"}
    assert _statuses(res) == {"GLD": "passed"}


def test_aggregate_ceiling_assertion_rejects_worsening_buy():
    """The belt behind V3: a wide accepted-override window can admit a buy that
    pushes the amplifier share of core past max(ceiling, pre-trade share) — the
    aggregate assertion strips the marginal amplifier buy."""
    gaps = [_gap("SPY", 88.0, 88.0, held=880), _gap("GLD", 5.0, 5.0, held=50)]
    decs = [_dec("SPY", magnitude=15.0)]   # W=15 → window ceiling 103%
    res = validate_trades(gaps, [_t("SPY", "buy", 70)], decs, CFG,
                          _ctx(deployment_gate="open"))
    assert len(res["rejected"]) == 1
    assert any("aggregate" in s for s in res["rejected"][0]["validation"]["reasons"])
    assert res["trades"] == []


# --- executor fail-closed auto path -------------------------------------------------

def test_executor_refuses_validation_error_file():
    doc = {"validation_error": True, "trades": [_t("GLD", "buy", 1)]}
    assert "validation_error" in _validation_refusal(doc, doc["trades"], "2026-07-06")


def test_executor_refuses_rejected_stamp_in_list_any_date():
    trades = [{**_t("QQQ", "buy", 1),
               "validation": {"status": "rejected", "reasons": ["x"]}}]
    assert "rejected" in _validation_refusal({"trades": trades}, trades, "2026-07-01")


def test_executor_refuses_unstamped_after_cutoff_tolerates_before():
    trades = [_t("GLD", "buy", 1)]   # no validation stamp
    assert "unstamped" in _validation_refusal({"trades": trades}, trades, "2026-07-06")
    assert _validation_refusal({"trades": trades}, trades, "2026-07-02") == ""


def test_executor_accepts_clean_stamped_file():
    trades = [{**_t("GLD", "buy", 1),
               "validation": {"status": "passed", "reasons": []}}]
    assert _validation_refusal({"trades": trades}, trades, "2026-07-06") == ""


# --- Task 2: SGOV literal-cash carve-out ------------------------------------------
# A literal-cash → SGOV conversion is a pure cash-sleeve composition swap (cash sleeve
# = SGOV + literal cash), so it must NOT be windowed against SGOV's per-name reference.
# (2026-07-09: SGOV 28.44% vs window ceiling 28.50% rejected a $4k cash→SGOV swap,
# leaving ~5% of equity idle in literal cash.)

def test_sgov_cash_swap_passes_above_per_name_window():
    """cash 13.9% / SGOV 28.44% (ref 23.5, window ceiling 28.5) → 40-share cash→SGOV
    buy passes despite landing far above the per-name window."""
    gaps = [_gap("SGOV", 28.44, 23.5, price=100.0, held=284)]
    res = validate_trades(gaps, [_t("SGOV", "buy", 40)], [], CFG,
                          _ctx(cash_usd=13_900.0, literal_cash_target_pct=1.5))
    assert res["rejected"] == []
    assert _statuses(res)["SGOV"] == "passed"
    assert res["trades"][0]["quantity"] == 40


def test_sgov_cash_swap_clamped_to_buffer_edge():
    """Pre-trade literal cash 2% with a 1.5% buffer leaves only $500 → a 40-share
    ($4k) buy is clamped to the 5 shares the buffer allows, not rejected."""
    gaps = [_gap("SGOV", 28.44, 23.5, price=100.0, held=284)]
    res = validate_trades(gaps, [_t("SGOV", "buy", 40)], [], CFG,
                          _ctx(cash_usd=2_000.0, literal_cash_target_pct=1.5))
    assert res["rejected"] == []
    t = res["trades"][0]
    assert t["validation"]["status"] == "clamped"
    assert t["quantity"] == 5
    assert any("buffer" in r for r in t["validation"]["reasons"])


def test_sgov_buy_funded_by_same_day_sells_gets_no_exemption():
    """Pre-trade literal cash sits AT the buffer (budget 0), so a SGOV buy could only
    be funded by same-day core sell proceeds — it does NOT get the exemption and is
    rejected by the normal per-name window (clamped to zero)."""
    gaps = [_gap("SPY", 20.0, 2.0, price=100.0, held=200),
            _gap("SGOV", 28.44, 23.5, price=100.0, held=284)]
    res = validate_trades(gaps, [_t("SPY", "sell", 50), _t("SGOV", "buy", 40)],
                          [], CFG, _ctx(cash_usd=1_500.0, literal_cash_target_pct=1.5))
    rejected_syms = [r["symbol"] for r in res["rejected"]]
    assert "SGOV" in rejected_syms
    assert _statuses(res).get("SPY") == "passed"


def test_sgov_sell_still_windowed_normally():
    """SGOV SELLs are unaffected by the buy carve-out — the window still clamps an
    overshoot to the floor edge."""
    gaps = [_gap("SGOV", 28.44, 23.5, price=100.0, held=284)]
    res = validate_trades(gaps, [_t("SGOV", "sell", 200)], [], CFG,
                          _ctx(cash_usd=13_900.0))
    t = res["trades"][0]
    assert t["validation"]["status"] == "clamped"
    assert t["quantity"] == 99   # (28.44−18.5)pp of $100K at $100
    assert res["rejected"] == []
