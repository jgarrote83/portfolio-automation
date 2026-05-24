import logging
import azure.functions as func

from collector.handler import run as collector_run
from analyzer.handler import analyze_snapshot

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

