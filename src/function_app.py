import logging
import azure.functions as func

from collector.handler import run as collector_run

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
