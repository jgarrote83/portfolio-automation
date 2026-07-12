import json
import logging
import os
from datetime import date

import azure.functions as func

from collector.handler import run as collector_run
from analyzer.handler import analyze_snapshot
from daytrade.handler import run_daytrade_manage, save_daytrade_nominations
from executor.handler import execute_approvals, run_auto_execute
from seeder.handler import seed_positions
from flex.handler import run_flex_intraday
from learning.handler import already_ran_today, is_first_saturday, run_cycle
from shared.storage import read_blob_bytes

logger = logging.getLogger(__name__)

app = func.FunctionApp()


@app.timer_trigger(
    # 09:00 ET weekdays — ET-local via the TZ=America/New_York app setting.
    # (NOT WEBSITE_TIME_ZONE: that setting is Windows-only and silently ignored on
    # Linux — restoring it re-introduces the pre-6f42f1a 4.5h-early cron bug.)
    schedule="0 0 9 * * 1-5",
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
    # 09:35 ET weekdays — 5 min after open (ET-local via TZ=America/New_York, not
    # the Windows-only WEBSITE_TIME_ZONE).
    schedule="0 35 9 * * 1-5",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def auto_executor(timer: func.TimerRequest) -> None:
    """Paper-only auto-execute (thin wrapper — logic in executor.run_auto_execute).

    Reads today's `daily-trades/{date}.json` (ET trading date, #29) and submits
    every recommendation to Alpaca paper. Gated by AUTO_EXECUTE_ENABLED inside
    the helper; market-closed gate inside execute_approvals defers if open is
    missed (e.g. holiday).
    """
    if timer.past_due:
        logger.warning("auto_executor timer was past due — running now")
    run_auto_execute("auto_executor")


@app.timer_trigger(
    # 10:05 + 11:05 ET weekday retries (#29): the 09:35 shot reads a file the
    # analyzer produces with VARIABLE LLM latency — analyzer >35 min or failed
    # previously meant the day silently never executed, and deferred_market_closed
    # deferred to nothing. Idempotent by the executor's cache asymmetry (terminal
    # outcomes cached, failures not).
    schedule="0 5 10,11 * * 1-5",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def auto_executor_retry(timer: func.TimerRequest) -> None:
    """Retry net for auto_executor (thin wrapper — logic + escalation in
    executor.run_auto_execute: no_trades at ≥11:00 ET logs ERROR)."""
    if timer.past_due:
        logger.warning("auto_executor_retry timer was past due — running now")
    run_auto_execute("auto_executor_retry")


@app.timer_trigger(
    # Every 15 min, weekdays. The cron is TIMEZONE-INDEPENDENT by design: market
    # hours are NOT encoded — the engine gates on Alpaca's clock (is_open) inside
    # run_flex_intraday, which is the DST-safe choice. Do NOT add a TZ-local note.
    schedule="0 */15 * * * 1-5",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def flex_intraday(timer: func.TimerRequest) -> None:
    """Intraday catalyst Flex engine. Gated by FLEX_ENABLED app setting.

    Reconciles the flex ledger to Alpaca, manages held flex positions, and
    enters confirmed nominations inside the morning window. Closed-market ticks
    self-skip after the clock check (effectively free).
    """
    if os.getenv("FLEX_ENABLED", "false").lower() != "true":
        logger.info("FLEX_ENABLED is not 'true' — skipping flex_intraday")
        return
    try:
        result = run_flex_intraday()
        logger.info("flex_intraday result: %s", result.get("status"))
    except Exception:  # noqa: BLE001
        logger.exception("flex_intraday failed")


@app.route(
    route="flex",
    methods=["POST"],
    auth_level=func.AuthLevel.FUNCTION,
)
def flex(req: func.HttpRequest) -> func.HttpResponse:
    """Manual/dry-run invocation of the flex engine. Body: {"date"?, "dry_run"?}.

    `dry_run=true` computes and persists the would-do state but places no orders —
    use it to validate the engine before flipping FLEX_ENABLED on.
    """
    try:
        body = req.get_json()
    except ValueError:
        body = {}
    body = body or {}
    date_str = body.get("date")
    dry_run = bool(body.get("dry_run", False))
    try:
        result = run_flex_intraday(date_str=date_str, dry_run=dry_run)
    except Exception as e:  # noqa: BLE001
        logger.exception("flex run failed")
        return func.HttpResponse(
            json.dumps({"error": str(e)}), status_code=500, mimetype="application/json",
        )
    return func.HttpResponse(
        json.dumps(result, default=str), status_code=200, mimetype="application/json",
    )


@app.timer_trigger(
    # Every minute, weekdays. TIMEZONE-INDEPENDENT by design (repo doctrine):
    # market hours are NOT encoded — the handler gates on the Alpaca clock and
    # the calendar-derived session window [open−5m, open+110m]; outside it the
    # tick is a fast no-op (~115 live ticks/day).
    schedule="0 * * * * 1-5",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=False,   # 1-min cadence — monitor bookkeeping is pure overhead
)
def daytrade_manage(timer: func.TimerRequest) -> None:
    """DayTrade Lab 1-min loop. Gated by the DAYTRADE_ENABLED app setting."""
    if os.getenv("DAYTRADE_ENABLED", "false").lower() != "true":
        return
    try:
        result = run_daytrade_manage()
        status = result.get("status")
        if status not in ("outside_window",):
            logger.info("daytrade_manage result: %s", status)
    except Exception:  # noqa: BLE001
        logger.exception("daytrade_manage failed")


@app.route(
    route="daytrade_nominate",
    methods=["POST"],
    auth_level=func.AuthLevel.FUNCTION,
)
def daytrade_nominate(req: func.HttpRequest) -> func.HttpResponse:
    """Manual pre-open nominations for the DayTrade Lab (spec §3).

    Body: {date?, tone: risk_on|neutral|risk_off|carry_stress,
    candidates: [{symbol, catalyst_note, catalyst_class|null}]}.
    """
    try:
        body = req.get_json()
    except ValueError:
        body = {}
    result = save_daytrade_nominations(body or {})
    code = 400 if "error" in result else 200
    return func.HttpResponse(
        json.dumps(result, default=str), status_code=code, mimetype="application/json",
    )


@app.route(
    route="daytrade",
    methods=["POST"],
    auth_level=func.AuthLevel.FUNCTION,
)
def daytrade(req: func.HttpRequest) -> func.HttpResponse:
    """Manual/dry-run invocation of the DayTrade Lab tick. Body: {"date"?, "dry_run"?}.

    `dry_run=true` computes (reconcile detection, validation, signals) but places
    no orders — use it to validate before flipping DAYTRADE_ENABLED on.
    """
    try:
        body = req.get_json()
    except ValueError:
        body = {}
    body = body or {}
    try:
        result = run_daytrade_manage(date_str=body.get("date"),
                                     dry_run=bool(body.get("dry_run", False)))
    except Exception as e:  # noqa: BLE001
        logger.exception("daytrade run failed")
        return func.HttpResponse(
            json.dumps({"error": str(e)}), status_code=500, mimetype="application/json",
        )
    return func.HttpResponse(
        json.dumps(result, default=str), status_code=200, mimetype="application/json",
    )


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


@app.timer_trigger(
    # Every Saturday 12:00 ET (market closed; a full month of graded rows
    # available) — ET-local via TZ=America/New_York, same as every other
    # timer in this app (NOT the Windows-only WEBSITE_TIME_ZONE). NCRONTAB
    # day-of-week 6 = Saturday. The in-code `is_first_saturday` gate narrows
    # the monthly cron (there is no first-Saturday-of-month NCRONTAB syntax)
    # to only the first Saturday; the other 3-4 Saturdays a month no-op.
    schedule="0 0 12 * * 6",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def learning_reviewer(timer: func.TimerRequest) -> None:
    """Learning Loop v1.0 monthly strategy review (FOLLOWUPS #13/#32, spec
    docs/specs/Learning_Loop_v1.0.md). Always runs the cycle (bundle + Foundry
    call + deterministic validation + persistence) regardless of
    LEARNING_PHASE — phase only governs what the SWA tab/API surface."""
    today = date.today()
    if not is_first_saturday(today):
        return
    if timer.past_due:
        logger.warning("learning_reviewer timer was past due — running now")
    try:
        result = run_cycle(trigger="timer")
        logger.info("learning_reviewer result: %s", result.get("status"))
    except Exception:  # noqa: BLE001
        logger.exception("learning_reviewer failed")


@app.route(
    route="learning_run",
    methods=["POST"],
    auth_level=func.AuthLevel.FUNCTION,
)
def learning_run(req: func.HttpRequest) -> func.HttpResponse:
    """Manual trigger for the Learning Loop reviewer (the tab's "Run review
    now" button, proxied through the SWA API's FUNC_MASTER_KEY). Rate-limited
    to 1/day via the LearningCycles table. Body: {"date"?} (YYYY-MM-DD, for
    testing a specific cycle date; defaults to today)."""
    try:
        body = req.get_json()
    except ValueError:
        body = {}
    date_str = (body or {}).get("date")
    cycle_id = date_str or date.today().isoformat()
    if already_ran_today(cycle_id):
        return func.HttpResponse(
            json.dumps({"error": f"a cycle already ran for {cycle_id} (rate-limited to 1/day)"}),
            status_code=429,
            mimetype="application/json",
        )
    try:
        result = run_cycle(trigger="manual", date_str=date_str)
    except Exception as e:  # noqa: BLE001
        logger.exception("learning_run failed")
        return func.HttpResponse(
            json.dumps({"error": str(e)}), status_code=500, mimetype="application/json",
        )
    return func.HttpResponse(
        json.dumps(result, default=str), status_code=200, mimetype="application/json",
    )


@app.route(
    route="backfill",
    methods=["POST"],
    auth_level=func.AuthLevel.FUNCTION,
)
def backfill(req: func.HttpRequest) -> func.HttpResponse:
    """Directly invoke analyze_snapshot for a given date from storage.

    Useful when the EventGrid trigger can't fire (e.g. provider registering).
    Body: {"date": "YYYY-MM-DD"}
    """
    try:
        body = req.get_json()
    except ValueError:
        body = {}
    date_str = (body or {}).get("date")
    if not date_str:
        return func.HttpResponse(
            json.dumps({"error": "date is required (YYYY-MM-DD)"}),
            status_code=400,
            mimetype="application/json",
        )
    blob_name = f"{date_str}.json"
    try:
        snapshot_bytes = read_blob_bytes("daily-snapshots", blob_name)
        analyze_snapshot(snapshot_bytes, blob_name)
    except Exception as e:  # noqa: BLE001
        logger.exception("Backfill failed for %s", date_str)
        return func.HttpResponse(
            json.dumps({"error": str(e), "date": date_str}),
            status_code=500,
            mimetype="application/json",
        )
    return func.HttpResponse(
        json.dumps({"status": "ok", "date": date_str}),
        status_code=200,
        mimetype="application/json",
    )
