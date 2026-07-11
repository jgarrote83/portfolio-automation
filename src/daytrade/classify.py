"""Optional LLM catalyst classification hook (spec §3) — ships OFF.

Runs only when ``DAYTRADE_LLM_CLASSIFY=true`` AND a nomination arrives with
``catalyst_class: null``. The LLM classifies A–D and flags dilution risk from a
tightly-scoped context (symbol + <24h headlines + the S-3/424B5 filings flag) —
strict JSON out. It NEVER picks, sizes, or times; the engine enforces the class
rulebook regardless of what the model says (D never trades; C ⇒ half size,
ORB-15 only, never VWAP-pullback).
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You classify a day-trade catalyst for a mechanical lab. Respond with ONLY a "
    "JSON object {\"catalyst_class\": \"A\"|\"B\"|\"C\"|\"D\", \"dilution_risk\": "
    "true|false, \"why\": \"<one sentence>\"}. Classes: A = hard fresh catalyst "
    "(earnings beat+raise, FDA approval, major contract) <24h old; B = softer but "
    "real catalyst (analyst action, sector sympathy with volume); C = momentum/"
    "technical only, no discrete catalyst; D = dilution-driven, promoted, or stale "
    "news. When in doubt, choose the more conservative (later) class."
)


def classify_catalyst_llm(foundry, symbol: str, headlines: list[str],
                          dilution_flag: bool | None) -> dict | None:
    """Return ``{catalyst_class, dilution_risk, why}`` or None on any failure.

    A None return leaves the nomination's class null, which the pattern rulebook
    treats as D (never trades) — the hook can only ever ADD tradability, never
    remove enforcement.
    """
    user = json.dumps({
        "symbol": symbol,
        "headlines_24h": (headlines or [])[:10],
        "recent_s3_or_424b5_filing": dilution_flag,
    })
    try:
        raw = foundry.complete(_SYSTEM, user, max_tokens=200, temperature=0.0)
        data = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
        klass = str(data.get("catalyst_class", "")).upper()
        if klass not in ("A", "B", "C", "D"):
            return None
        return {
            "catalyst_class": klass,
            "dilution_risk": bool(data.get("dilution_risk")),
            "why": str(data.get("why", ""))[:300],
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("daytrade LLM classify failed for %s: %s", symbol, e)
        return None
