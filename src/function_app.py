import json
import logging
import os
from datetime import datetime, timezone

import azure.functions as func

from collector.handler import run as collector_run
from analyzer.handler import analyze_snapshot
from executor.handler import execute_approvals
from seeder.handler import seed_positions

logger = logging.getLogger(__name__)

app = func.FunctionApp()


@app.timer_trigger(
    schedule="0 0 9 * * 1-5",    # 09:00 ET weekdays (WEBSITE_TIME_ZONE=Eastern Standard Time)
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def collector(timer: func.TimerRequest) -> None:
    if timer.past_due:
        logger.warning("Collector timer was past due — running now")
    collector_run()


@app.blob_trigger(
    arg_name="snapshot",
    path="daily-snapshots/{name}.json",
    connection="AzureWebJobsStorage",
    source="EventGrid",  # Flex Consumption requires Event Grid-sourced blob triggers
)
def analyzer(snapshot: func.InputStream) -> None:
    """Fires when a new daily snapshot lands; produces report + trades."""
    blob_name = snapshot.name or "unknown"
    logger.info("Analyzer triggered for blob: %s (%d bytes)", blob_name, snapshot.length or 0)
    data = snapshot.read()
    analyze_snapshot(data, blob_name)


@app.timer_trigger(
    schedule="0 35 9 * * 1-5",   # 09:35 ET weekdays — 5 min after open (WEBSITE_TIME_ZONE)
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def auto_executor(timer: func.TimerRequest) -> None:
    """Paper-only auto-execute. Gated by AUTO_EXECUTE_ENABLED app setting.

    Reads today's `daily-trades/{date}.json` and submits every recommendation
    to Alpaca paper. Market-closed gate inside execute_approvals will defer
    if open is missed (e.g. holiday).
    """
    if timer.past_due:
        logger.warning("auto_executor timer was past due — running now")
    if os.getenv("AUTO_EXECUTE_ENABLED", "false").lower() != "true":
        logger.info("AUTO_EXECUTE_ENABLED is not 'true' — skipping auto execution")
        return
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        result = execute_approvals(date_str, force=False, auto=True)
        logger.info("auto_executor result for %s: %s", date_str, result.get("status"))
    except Exception:  # noqa: BLE001
        logger.exception("auto_executor failed for %s", date_str)


@app.route(
    route="executor",
    methods=["POST"],
    auth_level=func.AuthLevel.FUNCTION,
)
def executor(req: func.HttpRequest) -> func.HttpResponse:
    """Phase-2 paper-trade executor. Called by SWA managed API after approval,
    or with `auto=true` for paper-only auto-execute."""
    try:
        body = req.get_json()
    except ValueError:
        body = {}
    date_str = (body or {}).get("date")
    force = bool((body or {}).get("force", False))
    auto = bool((body or {}).get("auto", False))
    if not date_str:
        return func.HttpResponse(
            json.dumps({"error": "date is required (YYYY-MM-DD)"}),
            status_code=400,
            mimetype="application/json",
        )
    try:
        result = execute_approvals(date_str, force=force, auto=auto)
    except Exception as e:  # noqa: BLE001
        logger.exception("Executor failed for %s", date_str)
        return func.HttpResponse(
            json.dumps({"error": str(e), "date": date_str}),
            status_code=500,
            mimetype="application/json",
        )
    return func.HttpResponse(
        json.dumps(result, default=str),
        status_code=200,
        mimetype="application/json",
    )


@app.route(
    route="seeder",
    methods=["POST"],
    auth_level=func.AuthLevel.FUNCTION,
)
def seeder(req: func.HttpRequest) -> func.HttpResponse:
    """One-time idempotent seeding of the Alpaca paper account from current holdings."""
    try:
        body = req.get_json()
    except ValueError:
        body = {}
    body = body or {}
    source = str(body.get("source") or "config")
    dry_run = bool(body.get("dry_run", False))
    force = bool(body.get("force", False))
    whole_shares_only = bool(body.get("whole_shares_only", False))
    try:
        result = seed_positions(
            source=source,
            dry_run=dry_run,
            force=force,
            whole_shares_only=whole_shares_only,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Seeder failed (source=%s)", source)
        return func.HttpResponse(
            json.dumps({"error": str(e), "source": source}),
            status_code=500,
            mimetype="application/json",
        )
    return func.HttpResponse(
        json.dumps(result, default=str),
        status_code=200,
        mimetype="application/json",
    )

