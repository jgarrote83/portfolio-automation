"""One-time (idempotent) seeder: mirrors real holdings into the Alpaca paper account.

Trigger: HTTP POST /api/seeder
Body:    {"source": "config" | "portfolio", "dry_run": false, "force": false}

Behaviour:
- Loads positions from `src/config/portfolio.json` (fallback while E*TRADE OAuth
  is offline). When the snapshot already contains a fresher list of holdings,
  pass `source="snapshot"` to use today's `daily-snapshots/{today}.json`.
- For each holding, checks the Alpaca paper account's current positions.
  If the symbol is already held, it is skipped unless `force=true`.
- Submits a `market` buy with the configured quantity (fractional allowed).
- Writes a per-symbol report to `seeding/{utc_ts}.json` and returns it.

Not destructive: never sells; only adds positions that are missing. Safe to
re-run (idempotent on symbol presence).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from shared.keyvault import load_secrets
from shared.storage import _blob_client, read_snapshot
from shared.clients.alpaca import AlpacaClient

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "portfolio.json"


def seed_positions(
    source: str = "config",
    dry_run: bool = False,
    force: bool = False,
    whole_shares_only: bool = False,
) -> dict:
    """Seed the Alpaca paper account with the current E*TRADE holdings.

    Pass ``whole_shares_only=True`` when seeding outside market hours: Alpaca
    rejects fractional day-orders queued for the next session, so we floor each
    quantity and drop tickers that would round to zero.
    """
    logger.info(
        "=== Seeder starting (source=%s, dry_run=%s, force=%s, whole_shares_only=%s) ===",
        source, dry_run, force, whole_shares_only,
    )

    holdings = _load_holdings(source)
    if not holdings:
        return {"status": "no_holdings", "source": source, "results": []}

    secrets = load_secrets()
    key = secrets.get("AlpacaApiKey")
    secret = secrets.get("AlpacaApiSecret")
    if not key or not secret:
        raise RuntimeError("Alpaca credentials missing from Key Vault")

    client = AlpacaClient(api_key=key, api_secret=secret)

    existing_qty: dict[str, float] = {}
    try:
        for p in client.list_positions():
            sym = str(p.get("symbol", "")).upper()
            try:
                existing_qty[sym] = float(p.get("qty") or 0)
            except (TypeError, ValueError):
                existing_qty[sym] = 0.0
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not list existing Alpaca positions: %s", e)

    # Also block symbols with an open buy order so re-running the seeder
    # before fills doesn't duplicate (issue seen 2026-05-26).
    open_buy_symbols: set[str] = set()
    try:
        for o in client.list_orders(status="open", limit=500):
            if str(o.get("side", "")).lower() == "buy":
                open_buy_symbols.add(str(o.get("symbol", "")).upper())
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not list existing Alpaca open orders: %s", e)

    results: list[dict] = []
    for h in holdings:
        symbol = str(h.get("ticker") or h.get("symbol") or "").upper()
        qty = h.get("quantity") or h.get("qty")
        sec_type = str(h.get("security_type") or "EQ").upper()

        base = {"symbol": symbol, "requested_qty": qty, "security_type": sec_type}

        if not symbol or not qty:
            results.append({**base, "status": "skipped_invalid"})
            continue
        if sec_type != "EQ":
            results.append({**base, "status": "skipped_non_equity"})
            continue

        held = existing_qty.get(symbol, 0.0)
        if held > 0 and not force:
            results.append({
                **base,
                "status": "skipped_already_held",
                "alpaca_qty": held,
            })
            continue

        if symbol in open_buy_symbols and not force:
            results.append({
                **base,
                "status": "skipped_open_order_pending",
            })
            continue

        # When seeding outside market hours, Alpaca only queues whole-share day
        # orders for the next session. Floor fractional qty; drop sub-1 tickers.
        if whole_shares_only:
            try:
                whole = int(float(qty))
            except (TypeError, ValueError):
                whole = 0
            if whole <= 0:
                results.append({
                    **base,
                    "status": "skipped_sub_one_share",
                    "floored_qty": whole,
                })
                continue
            qty = whole

        if dry_run:
            results.append({**base, "status": "dry_run", "would_buy": qty})
            continue

        try:
            order = client.submit_order(
                symbol=symbol,
                qty=qty,
                side="buy",
                order_type="market",
                time_in_force="day",
                client_order_id=f"seed-{uuid.uuid4().hex[:12]}",
            )
            results.append({
                **base,
                "status": "submitted",
                "alpaca_order_id": order.get("id"),
                "alpaca_status": order.get("status"),
            })
        except Exception as e:  # noqa: BLE001
            logger.exception("Seed buy failed for %s qty=%s", symbol, qty)
            results.append({**base, "status": "error", "error": str(e)})

    summary = {
        "status": "ok",
        "source": source,
        "dry_run": dry_run,
        "force": force,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "submitted": sum(1 for r in results if r["status"] == "submitted"),
        "skipped": sum(1 for r in results if r["status"].startswith("skipped")),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "dry_run_count": sum(1 for r in results if r["status"] == "dry_run"),
        "results": results,
    }

    if not dry_run:
        _write_seeding_report(summary)

    logger.info(
        "=== Seeder done — %d submitted, %d skipped, %d errors ===",
        summary["submitted"], summary["skipped"], summary["errors"],
    )
    return summary


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load_holdings(source: str) -> list[dict]:
    src = (source or "config").lower()
    if src in ("snapshot", "today"):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            snap = read_snapshot(today)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not read today's snapshot (%s): %s", today, e)
            snap = None
        if snap:
            positions = (
                snap.get("portfolio", {}).get("positions")
                or snap.get("positions")
                or []
            )
            if positions:
                return positions
        logger.info("Snapshot source empty — falling back to config file")

    if not _CONFIG_PATH.exists():
        logger.warning("Config file not found: %s", _CONFIG_PATH)
        return []
    with _CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg.get("positions", [])


def _write_seeding_report(summary: dict) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    name = f"{ts}.json"
    client = _blob_client()
    container = client.get_container_client("seeding")
    try:
        container.create_container()
    except Exception:
        pass
    blob = client.get_blob_client("seeding", name)
    data = json.dumps(summary, default=str, indent=2)
    blob.upload_blob(data, overwrite=True)
    logger.info("Seeding report written: seeding/%s (%d bytes)", name, len(data))
