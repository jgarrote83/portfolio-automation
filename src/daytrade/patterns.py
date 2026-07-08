"""Pattern detection (pure) — spec §4. ORB and VWAP-pullback over session 1-min bars.

Everything recomputes from the session's bars-so-far each tick (stateless ⇒
deterministic ⇒ testable); the handler enforces one-entry-per-name via day state.
Bars are Alpaca-shaped ``{"t", "o", "h", "l", "c", "v"}``. Missing data never
raises and never emits a signal.

Structural constants (not knobs): the Pattern-2 "opening drive" is ≥1% above the
session open while holding above the running VWAP — the minimal deterministic
reading of the spec's "opening drive up".
"""
from __future__ import annotations

_DRIVE_MIN_GAIN_PCT = 1.0

# The class rulebook (spec §3): D never trades; C ⇒ ORB-15 only, never
# VWAP-pullback, half risk; risk_off tone ⇒ Pattern 2 only, half size.
_ALL_PATTERNS = frozenset({"orb", "vwap_pullback"})


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def allowed_patterns(tone: str | None, catalyst_class: str | None) -> tuple[frozenset[str], bool]:
    """Return ``(patterns, half_size)`` for this tone × class (spec §3/§4).

    ``carry_stress`` refuses the day upstream; here it yields the empty set as a
    belt. Unknown class ⇒ treated as D (never trades) — fail closed.
    """
    klass = (catalyst_class or "").upper()
    t = (tone or "neutral").lower()
    if t == "carry_stress" or klass not in ("A", "B", "C"):
        return frozenset(), False
    half = False
    patterns = _ALL_PATTERNS
    if klass == "C":
        patterns = frozenset({"orb"})   # ORB-15 only, never VWAP-pullback
        half = True
    if t == "risk_off":
        patterns = patterns & frozenset({"vwap_pullback"})   # Pattern 2 only
        half = True
    return patterns, half


def cumulative_vwap(bars: list[dict]) -> list[float | None]:
    """Running session VWAP after each bar (None until a priced bar appears)."""
    out: list[float | None] = []
    num = den = 0.0
    for b in bars:
        h, lo, c, v = _f(b.get("h")), _f(b.get("l")), _f(b.get("c")), _f(b.get("v"))
        if None not in (h, lo, c, v) and v > 0:
            num += ((h + lo + c) / 3.0) * v
            den += v
        out.append(num / den if den > 0 else None)
    return out


def opening_range(bars: list[dict], orb_n: int) -> dict | None:
    """The first ``orb_n`` 1-min bars: ``{high, low, opening_candle_vol, complete}``."""
    if not bars:
        return None
    window = bars[:orb_n]
    highs = [h for b in window if (h := _f(b.get("h"))) is not None]
    lows = [lo for b in window if (lo := _f(b.get("l"))) is not None]
    if not highs or not lows:
        return None
    return {
        "high": max(highs),
        "low": min(lows),
        "opening_candle_vol": _f(window[0].get("v")),
        "complete": len(bars) >= orb_n,
    }


def orb_signal(bars: list[dict], rng: dict | None, cfg) -> dict:
    """Pattern 1 — evaluate the LATEST completed bar for an ORB entry (spec §4).

    Break above range high with (a) price > session VWAP, (b) breakout-bar volume
    > opening-candle volume (iex_ratio), stop = nearer (higher) of range low /
    VWAP; implied stop > max_stop_pct ⇒ skip.
    """
    out = {"pattern": "orb", "signal": False, "entry": None, "stop": None,
           "reason": None}
    if not rng or not rng.get("complete") or len(bars) <= 1:
        out["reason"] = "range_incomplete"
        return out
    last = bars[-1]
    close, vol = _f(last.get("c")), _f(last.get("v"))
    vwap = cumulative_vwap(bars)[-1]
    open_vol = _f(rng.get("opening_candle_vol"))
    if close is None or vwap is None or vol is None or open_vol is None:
        out["reason"] = "missing_data"
        return out
    if close <= rng["high"]:
        out["reason"] = "no_breakout"
        return out
    if close <= vwap:
        out["reason"] = "below_vwap"
        return out
    if vol <= open_vol:
        out["reason"] = "volume_not_confirming"
        return out
    stop = max(_f(rng.get("low")) or 0.0, vwap)   # nearer of range low / VWAP
    if stop >= close:
        out["reason"] = "bad_stop"
        return out
    if (close - stop) / close * 100.0 > cfg.max_stop_pct:
        out["reason"] = "stop_too_wide"
        return out
    out.update({"signal": True, "entry": close, "stop": stop})
    return out


def _touch_episodes(bars: list[dict], vwaps: list[float | None], start: int) -> list[tuple[int, int]]:
    """Contiguous runs of bars whose low touches/pierces the running VWAP."""
    episodes: list[tuple[int, int]] = []
    i = start
    while i < len(bars):
        lo, vw = _f(bars[i].get("l")), vwaps[i]
        if lo is not None and vw is not None and lo <= vw:
            j = i
            while j + 1 < len(bars):
                lo2, vw2 = _f(bars[j + 1].get("l")), vwaps[j + 1]
                if lo2 is not None and vw2 is not None and lo2 <= vw2:
                    j += 1
                else:
                    break
            episodes.append((i, j))
            i = j + 1
        else:
            i += 1
    return episodes


def vwap_pullback_signal(bars: list[dict], cfg) -> dict:
    """Pattern 2 — VWAP pullback (spec §4), recomputed from bars-so-far.

    Requires: an opening drive up (≥1% above session open holding above VWAP),
    then the FIRST touch of VWAP with pullback volume lighter than drive volume;
    entry when the latest bar closes above the prior bar's high (the reclaim);
    stop below the pullback low. Third touch ⇒ dead for the day.
    """
    out = {"pattern": "vwap_pullback", "signal": False, "entry": None,
           "stop": None, "touches": 0, "dead": False, "reason": None}
    if len(bars) < 3:
        out["reason"] = "insufficient_bars"
        return out
    vwaps = cumulative_vwap(bars)
    sess_open = _f(bars[0].get("o"))
    if sess_open is None or sess_open <= 0:
        out["reason"] = "missing_data"
        return out

    # Opening drive: the first bar whose close is ≥1% above the open with every
    # close since the start holding above the running VWAP.
    drive_end = None
    for i, b in enumerate(bars):
        c, vw = _f(b.get("c")), vwaps[i]
        if c is None or vw is None:
            break
        if c < vw:
            break
        if (c - sess_open) / sess_open * 100.0 >= _DRIVE_MIN_GAIN_PCT:
            drive_end = i
            break
    if drive_end is None:
        out["reason"] = "no_opening_drive"
        return out

    episodes = _touch_episodes(bars, vwaps, drive_end + 1)
    out["touches"] = len(episodes)
    if len(episodes) >= 3:
        out["dead"] = True
        out["reason"] = "third_touch_dead"
        return out
    if not episodes:
        out["reason"] = "no_vwap_touch"
        return out
    first_start, first_end = episodes[0]
    if len(episodes) > 1:
        out["reason"] = "not_first_touch"   # only the first touch is tradeable
        return out

    # Pullback volume must be lighter than drive volume (avg per bar).
    drive_vols = [v for b in bars[: drive_end + 1] if (v := _f(b.get("v"))) is not None]
    pull_vols = [v for b in bars[first_start: first_end + 1]
                 if (v := _f(b.get("v"))) is not None]
    if not drive_vols or not pull_vols:
        out["reason"] = "missing_data"
        return out
    if sum(pull_vols) / len(pull_vols) >= sum(drive_vols) / len(drive_vols):
        out["reason"] = "pullback_volume_heavy"
        return out

    # Reclaim: the LATEST bar closes above the prior bar's high, after the touch.
    if len(bars) - 1 <= first_end:
        out["reason"] = "awaiting_reclaim"
        return out
    last, prior = bars[-1], bars[-2]
    close, prior_high = _f(last.get("c")), _f(prior.get("h"))
    if close is None or prior_high is None or close <= prior_high:
        out["reason"] = "awaiting_reclaim"
        return out

    pull_lows = [lo for b in bars[first_start: first_end + 1]
                 if (lo := _f(b.get("l"))) is not None]
    stop = min(pull_lows) if pull_lows else None
    if stop is None or stop >= close:
        out["reason"] = "bad_stop"
        return out
    if (close - stop) / close * 100.0 > cfg.max_stop_pct:
        out["reason"] = "stop_too_wide"
        return out
    out.update({"signal": True, "entry": close, "stop": stop})
    return out


def is_print_stale(bars: list[dict], now_epoch_s: float, max_age_s: int) -> bool:
    """Stale-print halt guard: True when the latest bar is older than ``max_age_s``.

    ``bars[-1]["t"]`` is an ISO timestamp of the bar OPEN; a 1-min bar is 'fresh'
    within ``max_age_s`` of its close (open + 60s). No bars at all ⇒ stale.
    """
    if not bars:
        return True
    from datetime import datetime
    try:
        t = datetime.fromisoformat(str(bars[-1]["t"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return True
    bar_close = t.timestamp() + 60.0
    return (now_epoch_s - bar_close) > max_age_s
