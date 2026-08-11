"""Task C (2026-08-10, catalyst-sleeve-funnel) — throwaway FMP tier probe.

Verification-only, per FOLLOWUPS #34: that item's own doctrine says park it
rather than build on proxies of proxies if the tier doesn't expose the data.
This script answers exactly that question for #34's prerequisite inputs, and
is committed so the check is reproducible (by the account holder, or in a
future session with live FMP credentials) rather than a one-off finding taken
on faith.

Hits, and reports the raw status/payload shape for:
  - ^N225, ^KS11  (Asia-close index quotes)
  - ^GDAXI, ^STOXX50E  (Europe mid-session index quotes)
  - USDJPY  (forex quote — carry-stress input)

Does NOT touch `FMPClient` — these endpoints are not (yet) wired into the
production client, and this PR does not build the `global_overnight` block
regardless of the result (that stays FOLLOWUPS #34, out of scope here). A 402
on any of these is treated as "unavailable on the Starter tier, park it" —
the same verdict already recorded for `get_etf_holdings`.

Run: $env:FMP_API_KEY = "<key>"; python scripts/probe_fmp_tier.py
"""
from __future__ import annotations

import json
import os
import sys

import requests

_BASE = "https://financialmodelingprep.com/stable"
_TIMEOUT = 30

# FMP's quote endpoint takes any symbol (index/forex included) the same way.
_INDEX_SYMBOLS = ["^N225", "^KS11", "^GDAXI", "^STOXX50E"]
_FOREX_SYMBOLS = ["USDJPY"]


def probe_quote(session: requests.Session, api_key: str, symbol: str) -> dict:
    """GET /stable/quote?symbol=<symbol> — reports status + a shape summary,
    never raises (a probe must survive every failure mode it's checking for)."""
    try:
        r = session.get(f"{_BASE}/quote", params={"symbol": symbol, "apikey": api_key},
                         timeout=_TIMEOUT)
    except requests.RequestException as e:
        return {"symbol": symbol, "status": None, "error": str(e), "available": False}

    result = {"symbol": symbol, "status": r.status_code}
    if r.status_code == 402:
        result["available"] = False
        result["verdict"] = "402 Payment Required — not on this tier, PARK"
        return result
    if r.status_code != 200:
        result["available"] = False
        result["verdict"] = f"HTTP {r.status_code} — not confirmed available"
        try:
            result["body"] = r.text[:300]
        except Exception:  # noqa: BLE001
            pass
        return result

    try:
        payload = r.json()
    except ValueError:
        result["available"] = False
        result["verdict"] = "200 but non-JSON body — treat as unavailable"
        return result

    row = payload[0] if isinstance(payload, list) and payload else payload
    result["available"] = bool(row)
    result["verdict"] = "AVAILABLE" if row else "200 but empty payload — treat as unavailable"
    result["sample"] = json.dumps(row, default=str)[:300] if row else None
    return result


def main() -> int:
    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        print("ERROR: set $env:FMP_API_KEY before running — this probe needs a live key.")
        print("No results below are fabricated in its absence; the script simply refuses to guess.")
        return 2

    session = requests.Session()
    results = []

    print("=== Asia-close index quotes ===")
    for sym in ["^N225", "^KS11"]:
        res = probe_quote(session, api_key, sym)
        results.append(res)
        print(f"  {sym}: {res}")

    print("\n=== Europe mid-session index quotes ===")
    for sym in ["^GDAXI", "^STOXX50E"]:
        res = probe_quote(session, api_key, sym)
        results.append(res)
        print(f"  {sym}: {res}")

    print("\n=== Forex quote (carry stress) ===")
    for sym in _FOREX_SYMBOLS:
        res = probe_quote(session, api_key, sym)
        results.append(res)
        print(f"  {sym}: {res}")

    print("\n=== Summary ===")
    available = [r["symbol"] for r in results if r.get("available")]
    unavailable = [r["symbol"] for r in results if not r.get("available")]
    print(f"Available: {available or 'none'}")
    print(f"Unavailable/unconfirmed: {unavailable or 'none'}")
    print(
        "\nPer FOLLOWUPS #34 doctrine: if any of the Europe/Asia/forex inputs are "
        "unavailable, park #34 with this note rather than building on proxies of "
        "proxies. This PR does not build global_overnight regardless of the result."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
