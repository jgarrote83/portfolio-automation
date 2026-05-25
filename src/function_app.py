import json
import logging
import azure.functions as func

from collector.handler import run as collector_run
from analyzer.handler import analyze_snapshot
from executor.handler import execute_approvals

logger = logging.getLogger(__name__)

app = func.FunctionApp()


@app.timer_trigger(
    schedule="0 0 11 * * 1-5",   # 11:00 UTC = 06:00 ET, weekdays only
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
)
def analyzer(snapshot: func.InputStream) -> None:
    """Fires when a new daily snapshot lands; produces report + trades."""
    blob_name = snapshot.name or "unknown"
    logger.info("Analyzer triggered for blob: %s (%d bytes)", blob_name, snapshot.length or 0)
    data = snapshot.read()
    analyze_snapshot(data, blob_name)


@app.route(
    route="executor",
    methods=["POST"],
    auth_level=func.AuthLevel.FUNCTION,
)
def executor(req: func.HttpRequest) -> func.HttpResponse:
    """Phase-2 paper-trade executor. Called by SWA managed API after approval."""
    try:
        body = req.get_json()
    except ValueError:
        body = {}
    date_str = (body or {}).get("date")
    force = bool((body or {}).get("force", False))
    if not date_str:
        return func.HttpResponse(
            json.dumps({"error": "date is required (YYYY-MM-DD)"}),
            status_code=400,
            mimetype="application/json",
        )
    try:
        result = execute_approvals(date_str, force=force)
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

