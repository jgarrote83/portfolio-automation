"""Unit tests for the KMLM day-P/L zero-watch diagnostic (session 2026-07-28, Task D).

KMLM's day P/L printed exactly $0.00 in three consecutive reports (07-24, 07-27,
07-28) while its total P/L moved (-$93.60, then -$109.20). Diagnostics only — this
module does not (and should not) adjudicate upstream-Alpaca-bug vs.
our-pipeline-mapping-bug; it flags the symptom and echoes the raw Alpaca fields so
a human/future session can. Run:
    PYTHONPATH=src pytest tests/test_day_pl_zero_watch.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_day_pl_zero_watch  # noqa: E402


def _pos(symbol, unrealized_intraday_pl, unrealized_pl, lastday_price=100.0,
         current_price=100.0, change_today=0.0, qty=None):
    pos = {
        "symbol": symbol,
        "unrealized_intraday_pl": unrealized_intraday_pl,
        "unrealized_pl": unrealized_pl,
        "lastday_price": lastday_price,
        "current_price": current_price,
        "change_today": change_today,
    }
    if qty is not None:
        pos["qty"] = qty
    return pos


def test_flagged_when_day_zero_and_total_moved_past_threshold():
    raw = [_pos("KMLM", 0.0, -109.20, lastday_price=54.32, current_price=54.10,
                change_today=-0.004)]
    prior = {"KMLM": -70.00}   # delta = -39.20, past the $25 default
    result = _build_day_pl_zero_watch(raw, prior)
    assert result["available"] is True
    assert len(result["flagged"]) == 1
    row = result["flagged"][0]
    assert row["symbol"] == "KMLM"
    assert row["day_pl_reported"] == 0.0
    assert row["total_pl_delta"] == -39.2
    assert row["lastday_price"] == 54.32
    assert row["current_price"] == 54.10
    assert row["unrealized_intraday_pl"] == 0.0
    assert row["change_today"] == -0.004


def test_not_flagged_when_total_move_under_threshold():
    raw = [_pos("KMLM", 0.0, -95.00)]
    prior = {"KMLM": -93.60}   # delta = -1.40, well under the $25 default
    result = _build_day_pl_zero_watch(raw, prior)
    assert result["flagged"] == []


def test_not_flagged_when_day_pl_nonzero():
    raw = [_pos("KMLM", 12.34, -109.20)]
    prior = {"KMLM": -93.60}
    result = _build_day_pl_zero_watch(raw, prior)
    assert result["flagged"] == []


def test_not_flagged_when_no_prior_value_for_ticker():
    """A newly-opened position has no prior snapshot to diff against — never a
    false positive from missing history."""
    raw = [_pos("NEWPOS", 0.0, 0.0)]
    result = _build_day_pl_zero_watch(raw, {"OTHER": -10.0})
    assert result["flagged"] == []


def test_non_fatal_when_prior_snapshot_unavailable():
    raw = [_pos("KMLM", 0.0, -109.20)]
    result = _build_day_pl_zero_watch(raw, None)
    assert result["available"] is False
    assert result["flagged"] == []


def test_threshold_is_configurable():
    raw = [_pos("KMLM", 0.0, -100.0)]
    prior = {"KMLM": -90.0}   # delta = -10
    assert _build_day_pl_zero_watch(raw, prior, threshold_usd=25.0)["flagged"] == []
    assert len(_build_day_pl_zero_watch(raw, prior, threshold_usd=5.0)["flagged"]) == 1


def test_multiple_positions_only_qualifying_ones_flagged():
    raw = [
        _pos("KMLM", 0.0, -109.20),      # flagged: zero day, moved $39.20
        _pos("USMV", 3.20, 45.00),       # not flagged: nonzero day P/L
        _pos("AIA", 0.0, 10.10),         # not flagged: moved only $0.10
    ]
    prior = {"KMLM": -70.00, "USMV": 40.00, "AIA": 10.00}
    result = _build_day_pl_zero_watch(raw, prior)
    assert [r["symbol"] for r in result["flagged"]] == ["KMLM"]


# --- Task C (session 2026-08-01): resize awareness -----------------------------
# A share-count change (sale/buy) moves total P/L on its own (realized gain,
# new lot basis) and must not be misread as a P/L-mapping anomaly.

def test_resize_with_delta_but_no_price_freeze_is_not_flagged():
    """The KMLM-shaped 07-31 case: 183 -> 4 shares (a 179-share sale), delta
    -$93.87 — fully explained by realizing the sale's gain. lastday != current
    (no frozen-quote signal), so this must NOT be flagged at all."""
    raw = [_pos("KMLM", 0.0, -203.07, lastday_price=54.32, current_price=54.10,
                qty=4)]
    prior_pl = {"KMLM": -109.20}     # delta = -93.87
    prior_qty = {"KMLM": 183.0}
    result = _build_day_pl_zero_watch(raw, prior_pl, prior_qty)
    assert result["flagged"] == []


def test_resize_with_frozen_quote_still_flagged_and_annotated():
    """Same resize, but the independent price-identity signal (lastday ==
    current — a literally frozen quote) also fires: this is the genuine,
    separate anomaly and must still surface, annotated `position_resized`."""
    raw = [_pos("KMLM", 0.0, -203.07, lastday_price=54.10, current_price=54.10,
                qty=4)]
    prior_pl = {"KMLM": -109.20}
    prior_qty = {"KMLM": 183.0}
    result = _build_day_pl_zero_watch(raw, prior_pl, prior_qty)
    assert len(result["flagged"]) == 1
    row = result["flagged"][0]
    assert row["symbol"] == "KMLM"
    assert row["position_resized"] is True
    assert row["prior_qty"] == 183.0
    assert row["current_qty"] == 4.0


def test_unchanged_qty_position_with_large_delta_still_flags_as_before():
    """A same-size position (qty unchanged) with a large total-P/L delta must
    keep flagging exactly as before resize-awareness was added, regardless of
    the lastday/current price relationship."""
    raw = [_pos("COWZ", 0.0, -50.00, lastday_price=30.0, current_price=29.50,
                qty=100.0)]
    prior_pl = {"COWZ": -10.02}       # delta = -39.98
    prior_qty = {"COWZ": 100.0}       # unchanged
    result = _build_day_pl_zero_watch(raw, prior_pl, prior_qty)
    assert len(result["flagged"]) == 1
    row = result["flagged"][0]
    assert row["symbol"] == "COWZ"
    assert row["position_resized"] is False
    assert row["prior_qty"] == 100.0
    assert row["current_qty"] == 100.0


def test_resize_awareness_is_opt_in_omitting_prior_qty_keeps_old_behavior():
    """Omitting `prior_qty` entirely (the default) must preserve the original
    unconditional delta-only behavior — no share-count data means no resize
    determination is possible, so nothing is suppressed."""
    raw = [_pos("KMLM", 0.0, -203.07, lastday_price=54.32, current_price=54.10,
                qty=4)]
    prior_pl = {"KMLM": -109.20}
    result = _build_day_pl_zero_watch(raw, prior_pl)   # no prior_qty passed
    assert len(result["flagged"]) == 1
    assert result["flagged"][0]["position_resized"] is False
