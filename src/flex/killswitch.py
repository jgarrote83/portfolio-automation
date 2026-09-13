"""Flex sleeve kill switch — S1 (session 2026-09-13, amendment §2.3).

**Why this exists.** The sleeve runs at the full 25% (`sleeve_cap_pct`) with no
trial period — a deliberate sizing decision — on a strategy whose own measured
calibration is `n=2, hit_rate 0.0`. That is an absence of evidence, not evidence
it works. The kill switch is the only AUTOMATIC brake, and it is therefore MORE
important under that sizing decision, not less.

**Two independent trips; either one disables.** They catch different failures:

- **Slow trip** (`min_closed_trades` + `hit_rate_floor`) — a signal that simply
  does not work. Needs a sample before it can say anything, so it cannot fire
  early.
- **Fast trip** (`max_drawdown_pct`) — a signal that BLOWS UP. **No trade-count
  minimum**, deliberately: the slow trip needs ~20 closed trades (~10 trading
  days at ~2 closes/day post-N4), and a catastrophic path does not wait that
  long. At ~$99.5k equity, 2.0% ≈ $2,000 — roughly 5-6 full stop-outs, or one
  bad overnight gap.

**On trip:** stop opening NEW positions; **manage existing positions to their
exits normally — never a forced liquidation** (a forced exit at a bad moment is
its own risk, and the bracket legs are already resting at the broker). Emit a
blocking Data Integrity Warning naming the hit rate and trade count.

**Re-enabling is a HUMAN action, never automatic.** Once tripped the state
persists (`flex-ledger/kill-switch.json`) and a later improvement in the numbers
does NOT clear it — otherwise the switch would flap around the threshold and a
sleeve could re-enable itself into the same failure. `cleared_at`/`cleared_by`
are written only by a human action.

PURE module — no I/O. The handler loads/persists the state and applies the
decision; the collector echoes it into the snapshot.
"""
from __future__ import annotations

# Drawdown semantics: PEAK-TO-TROUGH of cumulative realized P&L, not simply
# "cumulative P&L below -X". The two coincide when the sleeve never goes
# profitable (peak = 0), which is the case the amendment's own arithmetic
# describes ("5-6 full stop-outs"). They differ when the sleeve earns first and
# gives it back: peak-to-trough trips, cumulative-from-zero does not.
#
# Peak-to-trough was chosen because it is what `max_drawdown_pct` NAMES, and
# because a signal that works and then stops working is exactly the failure a
# fast trip should catch. The cost is that it can trip while the sleeve is still
# net-profitable overall (up $3k after a $5k peak). That is a real trade-off and
# a human can clear the trip in one action -- recorded as decision gate G-11.
_DRAWDOWN_BASIS = "peak_to_trough_cumulative_realized_pnl"


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def hit_rate(closed_trades: list[dict]) -> tuple[float | None, int]:
    """``(hit_rate, gradeable_count)`` over closed trades with a KNOWN P&L.

    A row whose `pnl_usd` is null is EXCLUDED from both numerator and
    denominator, never counted as a loss. `flex/trades.py` deliberately writes
    `pnl_usd: None` (with a `pnl_unavailable_reason`) rather than fabricating a
    number when a fill price is unknown — scoring that as a loss would let a
    data gap trip a risk control. Same absent-vs-zero doctrine as the catalyst
    composite.

    Returns ``(None, 0)`` when nothing is gradeable — never a fabricated 0.0,
    which would read as "every trade lost" and trip the slow arm instantly.
    """
    graded = [t for t in (closed_trades or []) if _num(t.get("pnl_usd")) is not None]
    if not graded:
        return None, 0
    wins = sum(1 for t in graded if _num(t.get("pnl_usd")) > 0)
    return round(wins / len(graded), 4), len(graded)


def cumulative_drawdown_usd(closed_trades: list[dict]) -> float:
    """Peak-to-trough drawdown of cumulative realized P&L, in dollars (>= 0).

    Ordered by `closed_date` then `trade_id` so the walk is deterministic and
    independent of append order. Rows with unknown P&L contribute 0 — they move
    neither the peak nor the trough, which is the conservative treatment (an
    unmeasurable trade cannot manufacture a drawdown).
    """
    rows = sorted(
        closed_trades or [],
        key=lambda t: (str(t.get("closed_date") or ""), str(t.get("trade_id") or "")),
    )
    cum = peak = 0.0
    worst = 0.0
    for t in rows:
        cum += _num(t.get("pnl_usd")) or 0.0
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return round(worst, 2)


def evaluate_kill_switch(
    closed_trades: list[dict],
    equity_usd: float | None,
    cfg,
    prior_state: dict | None = None,
) -> dict:
    """Evaluate both trips and return the full state to persist AND surface.

    ``prior_state`` is the persisted ``flex-ledger/kill-switch.json`` (or None on
    the first run). **A prior trip is STICKY** — it is carried forward regardless
    of what the current numbers say, because re-enabling is a human action. Only
    an explicit `cleared_at` in the prior state releases it.

    Returns ``{tripped, trip_reason, hit_rate, gradeable_trades, closed_trades,
    drawdown_usd, drawdown_pct, drawdown_basis, thresholds, tripped_at,
    note}``.
    """
    prior = prior_state or {}
    enabled = bool(getattr(cfg, "kill_switch_enabled", True))
    min_closed = int(getattr(cfg, "kill_switch_min_closed_trades", 20))
    floor = float(getattr(cfg, "kill_switch_hit_rate_floor", 0.45))
    max_dd_pct = float(getattr(cfg, "kill_switch_max_drawdown_pct", 2.0))

    hr, gradeable = hit_rate(closed_trades)
    dd_usd = cumulative_drawdown_usd(closed_trades)
    eq = _num(equity_usd) or 0.0
    dd_pct = round(dd_usd / eq * 100.0, 4) if eq > 0 else None

    state = {
        "enabled": enabled,
        "tripped": False,
        "trip_reason": None,
        "hit_rate": hr,
        "gradeable_trades": gradeable,
        "closed_trades": len(closed_trades or []),
        "drawdown_usd": dd_usd,
        "drawdown_pct": dd_pct,
        "drawdown_basis": _DRAWDOWN_BASIS,
        "thresholds": {
            "min_closed_trades": min_closed,
            "hit_rate_floor": floor,
            "max_drawdown_pct": max_dd_pct,
        },
        "tripped_at": prior.get("tripped_at"),
        "note": "",
    }

    if not enabled:
        state["note"] = "kill switch disabled by config — no automatic brake is active"
        return state

    # A prior trip is STICKY. Never auto-clears, whatever the numbers now say.
    if prior.get("tripped") and not prior.get("cleared_at"):
        state["tripped"] = True
        state["trip_reason"] = prior.get("trip_reason") or "prior_trip"
        state["note"] = (
            f"TRIPPED (carried forward, {state['trip_reason']}). Re-enabling is a "
            "human action — this never clears itself, even if the numbers recover."
        )
        return state

    # Fast trip first — no trade-count minimum, by design.
    if dd_pct is not None and dd_pct >= max_dd_pct:
        state.update({
            "tripped": True,
            "trip_reason": "max_drawdown",
            "tripped_at": prior.get("tripped_at") or "PENDING",
            "note": (
                f"TRIPPED (fast): sleeve drawdown ${dd_usd:,.0f} = {dd_pct:.2f}% of "
                f"equity, at or beyond the {max_dd_pct:.2f}% limit "
                f"({_DRAWDOWN_BASIS}). New entries suppressed; existing positions "
                "are managed to their exits normally, never force-liquidated."
            ),
        })
        return state

    # Slow trip — needs a sample.
    if gradeable >= min_closed and hr is not None and hr < floor:
        state.update({
            "tripped": True,
            "trip_reason": "hit_rate_floor",
            "tripped_at": prior.get("tripped_at") or "PENDING",
            "note": (
                f"TRIPPED (slow): realized hit rate {hr:.2%} over {gradeable} graded "
                f"closed trades is below the {floor:.0%} floor. New entries "
                "suppressed; existing positions are managed to their exits normally."
            ),
        })
        return state

    if gradeable < min_closed:
        state["note"] = (
            f"armed — {gradeable} of {min_closed} graded closed trades toward the "
            f"hit-rate arm; the drawdown arm is live now ({dd_pct if dd_pct is not None else 0:.2f}% "
            f"of the {max_dd_pct:.2f}% limit used)."
        )
    else:
        state["note"] = (
            f"armed — hit rate {hr:.2%} over {gradeable} graded trades (floor "
            f"{floor:.0%}); drawdown {dd_pct if dd_pct is not None else 0:.2f}% of "
            f"the {max_dd_pct:.2f}% limit."
        )
    return state


__all__ = ["evaluate_kill_switch", "hit_rate", "cumulative_drawdown_usd"]
