"""Phase-1 analyzer: blob-triggered Function that turns a daily snapshot into a
markdown report + trade recommendations via Azure AI Foundry (Claude Sonnet 4.6).

Trigger: new blob in `daily-snapshots/{name}.json`.

Outputs:
- `daily-reports/{date}.md`  — markdown analysis
- `daily-trades/{date}.json` — structured trade recommendations
- `TradeHistory` table rows for each trade
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from shared.keyvault import load_secrets
from shared.storage import (
    list_recent_reports,
    upsert_entity,
    write_report,
    write_trades,
)
from shared.clients.foundry import FoundryClient
from shared.quadrants import active_quadrant, benchmark_etf_for

logger = logging.getLogger(__name__)

_SRC = Path(__file__).parent.parent
_SYSTEM_PROMPT_FILE = _SRC / "config" / "project-instructions.md"
_TRADES_MARKER = "===TRADES_JSON==="

# Soft caps to keep the user message inside Claude's context window comfortably.
_MAX_NEWS_PER_SCOPE = 25
_MAX_COMPANY_NEWS_PER_TICKER = 5
_MAX_CONGRESSIONAL = 50
_MAX_RECENT_REPORTS = 5


def analyze_snapshot(snapshot_bytes: bytes, blob_name: str) -> None:
    """Main entry point — called by the blob trigger in function_app.py."""
    snapshot = json.loads(snapshot_bytes)
    date_str = snapshot.get("date") or _date_from_blob_name(blob_name)
    if not date_str:
        raise ValueError(f"Cannot determine date from snapshot/blob name: {blob_name}")

    logger.info("=== Analyzer starting for %s ===", date_str)

    secrets = load_secrets()
    api_key = secrets.get("FoundryApiKey")
    if not api_key:
        raise RuntimeError("FoundryApiKey missing from Key Vault")

    client = FoundryClient(api_key=api_key)
    if not client.ready:
        raise RuntimeError(
            "FoundryClient not ready — check FOUNDRY_ENDPOINT app setting"
        )

    system_prompt = _SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")
    recent = list_recent_reports(limit=_MAX_RECENT_REPORTS)
    logger.info("Loaded %d recent reports for continuity", len(recent))

    user_message = _build_user_message(snapshot, recent)
    logger.info("User message size: %d chars", len(user_message))

    raw = client.complete(
        system=system_prompt,
        user_message=user_message,
    )

    report_md, trades_obj = _split_response(raw, date_str)
    write_report(date_str, report_md)
    write_trades(date_str, trades_obj)
    _write_trade_history(date_str, trades_obj, snapshot)

    logger.info(
        "=== Analyzer completed for %s — %d trades recommended ===",
        date_str, len(trades_obj.get("trades", [])),
    )


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

def _build_user_message(snapshot: dict, recent: list[tuple[str, str]]) -> str:
    """Compose a compact, structured user message for Claude.

    We pass the snapshot mostly as JSON, but trim oversized news arrays so we don't
    burn tokens on noise. Previous reports are included as plain text.
    """
    trimmed = _trim_snapshot(snapshot)

    parts: list[str] = []
    parts.append(f"# Daily snapshot for {snapshot.get('date', 'unknown')}\n")
    parts.append("## Snapshot data (JSON)\n")
    parts.append("```json")
    parts.append(json.dumps(trimmed, default=str, indent=2))
    parts.append("```\n")

    if recent:
        parts.append("## Recent reports (most recent first)\n")
        for d, md in recent:
            parts.append(f"### Report — {d}\n")
            parts.append(md.strip())
            parts.append("\n---\n")

    parts.append(
        "\nProduce today's report and trade recommendations following the system "
        "instructions exactly. Remember the literal marker line "
        f"`{_TRADES_MARKER}` between the markdown report and the JSON block."
    )
    return "\n".join(parts)


def _trim_snapshot(snapshot: dict) -> dict:
    """Return a shallow copy with oversized news arrays clipped."""
    s = dict(snapshot)
    news = dict(s.get("news") or {})
    if "market" in news:
        news["market"] = news["market"][:_MAX_NEWS_PER_SCOPE]
    if "forex" in news:
        news["forex"] = news["forex"][:_MAX_NEWS_PER_SCOPE]
    if "company" in news and isinstance(news["company"], dict):
        news["company"] = {
            t: (items or [])[:_MAX_COMPANY_NEWS_PER_TICKER]
            for t, items in news["company"].items()
        }
    s["news"] = news
    if "congressional_trades" in s and isinstance(s["congressional_trades"], list):
        s["congressional_trades"] = s["congressional_trades"][:_MAX_CONGRESSIONAL]
    if "stock_news" in s and isinstance(s["stock_news"], list):
        s["stock_news"] = s["stock_news"][:_MAX_NEWS_PER_SCOPE]
    return s


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _split_response(raw: str, date_str: str) -> tuple[str, dict]:
    """Split Claude's response into (markdown_report, trades_dict)."""
    if _TRADES_MARKER not in raw:
        logger.warning(
            "Marker missing in response for %s (len=%d) \u2014 saving raw output to "
            "daily-reports/_debug/%s-raw.txt; treating full response as report",
            date_str, len(raw), date_str,
        )
        try:
            from shared.storage import write_debug_raw
            write_debug_raw(date_str, raw)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not persist debug raw response: %s", e)
        return raw.strip(), {"trades": []}

    md_part, _, trades_part = raw.partition(_TRADES_MARKER)
    md_part = md_part.strip()

    trades_obj = _extract_json(trades_part.strip())
    if not isinstance(trades_obj, dict) or "trades" not in trades_obj:
        logger.warning("Trades block malformed — defaulting to empty list")
        trades_obj = {"trades": []}

    trades_obj.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
    trades_obj.setdefault("date", date_str)
    return md_part, trades_obj


def _extract_json(text: str) -> dict | None:
    """Extract the first JSON object from a string, tolerating ```json fences."""
    if not text:
        return None
    # Strip optional ```json ... ``` fence
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        # Fall back to first {...} balanced span
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        logger.warning("JSON parse failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Table writer
# ---------------------------------------------------------------------------

def _write_trade_history(date_str: str, trades_obj: dict, snapshot: dict | None = None) -> None:
    year_month = date_str[:7]  # YYYY-MM
    quadrant_current = trades_obj.get("quadrant_current") or ""
    quadrant_projected_6m = trades_obj.get("quadrant_projected_6m") or ""
    risk_score = trades_obj.get("risk_score")

    # Entry metadata for the conviction-sleeve flex review (computed here, NOT
    # taken from the LLM): the active quadrant at entry, its representative sleeve
    # ETF, and the snapshot entry price. Persisted write-once on a flex BUY so the
    # collector's `_build_flex_review` can score the name against its benchmarks.
    snap = snapshot or {}
    prices = snap.get("prices") or {}
    entry_quadrant = active_quadrant(
        (snap.get("growth_axis") or {}).get("direction"),
        (snap.get("inflation_axis") or {}).get("direction"),
    )
    entry_bench_etf = benchmark_etf_for(entry_quadrant)

    def _entry_price(symbol: str) -> float | None:
        row = prices.get(symbol) or {}
        c = row.get("c") if isinstance(row, dict) else None
        try:
            return round(float(c), 4) if c is not None else None
        except (TypeError, ValueError):
            return None

    for t in trades_obj.get("trades", []):
        trade_id = t.get("id") or f"T-{date_str.replace('-', '')}-X"
        is_flex_buy = (t.get("layer") == "flex") and (t.get("side") == "buy")
        try:
            entity = {
                "PartitionKey":         year_month,
                "RowKey":               trade_id,
                "recommended_at":       date_str,
                "status":               "recommended",
                "side":                 t.get("side", ""),
                "symbol":               t.get("symbol", ""),
                "layer":                t.get("layer", ""),
                "flex_source":          t.get("flex_source") or "",
                "quantity":             int(t.get("quantity") or 0),
                "order_type":           t.get("order_type", ""),
                "limit_price":          t.get("limit_price"),
                "time_in_force":        t.get("time_in_force", ""),
                "rationale":            (t.get("rationale") or "")[:32000],
                "confidence":           float(t.get("confidence") or 0.0),
                "stop_loss":            t.get("stop_loss"),
                "take_profit":          t.get("take_profit"),
                # Phase C §7 reasoning capture (write-once, flex trades; core
                # trades emit nulls). Feeds the track_record learning aggregates.
                "primary_trigger":      t.get("primary_trigger") or "",
                "thesis_type":          t.get("thesis_type") or "",
                "trigger_evidence":     (t.get("trigger_evidence") or "")[:32000],
                "catalyst_date":        t.get("catalyst_date"),
                "quadrant_current":     quadrant_current,
                "quadrant_projected_6m": quadrant_projected_6m,
                "risk_score":           risk_score,
            }
            if is_flex_buy:
                # Conviction-sleeve entry metadata (write-once on the flex BUY).
                entity["entry_date"] = date_str
                entity["entry_price"] = _entry_price(t.get("symbol", ""))
                entity["entry_quadrant"] = entry_quadrant
                entity["flex_benchmark_etf"] = entry_bench_etf
            upsert_entity("TradeHistory", entity)
        except Exception as e:  # noqa: BLE001
            logger.error("TradeHistory upsert failed for %s: %s", trade_id, e)


def _date_from_blob_name(blob_name: str) -> str | None:
    # Trigger gives full path like "daily-snapshots/2026-05-24.json" or just the file name
    base = blob_name.rsplit("/", 1)[-1]
    if base.endswith(".json"):
        return base[:-5]
    return None
