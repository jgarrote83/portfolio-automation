"""Learning Loop v1.0 — reviewer function (spec §2-4, §9).

Runs one cycle: build the input bundle (`learning.bundle`), call the reviewer
model via Azure AI Foundry (same client the daily analyzer uses), validate the
output deterministically (`learning.schema`) before anything is treated as
approvable, and persist. FAIL LOUD: a schema violation writes a
`failed_validation` cycle record with the raw model output preserved for
inspection — nothing surfaces as pending.

Kept out of `src/analyzer/` deliberately — the daily analyzer path is
untouched by this feature (spec §5).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path

from learning.bundle import build_bundle
from learning.schema import validate_cycle_output
from shared.clients.foundry import FoundryClient
from shared.keyvault import load_secrets
from shared.storage import list_blob_names, query_entities, upsert_entity, write_json_blob

logger = logging.getLogger(__name__)

_SRC = Path(__file__).parent.parent
_REVIEW_PROMPT_FILE = _SRC / "config" / "learning-review-instructions.md"

_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_MAX_TOKENS = 16000
_DEFAULT_BUNDLE_MAX_TOKENS = 150_000
_TEMPERATURE = 0.2
_MIN_SESSIONS_FOR_FULL_MODE = 15

_LEARNING_CONTAINER = "learning"
_CYCLES_TABLE = "LearningCycles"
_PROPOSALS_TABLE = "LearningProposals"
_ENTITY_TEXT_CAP = 32000  # Azure Table Storage per-property string limit is 32K chars


def _model_name() -> str:
    return os.environ.get("LEARNING_MODEL", _DEFAULT_MODEL)


def _max_tokens() -> int:
    return int(os.environ.get("LEARNING_MAX_TOKENS", _DEFAULT_MAX_TOKENS))


def _bundle_max_tokens() -> int:
    return int(os.environ.get("LEARNING_BUNDLE_MAX_TOKENS", _DEFAULT_BUNDLE_MAX_TOKENS))


# ---------------------------------------------------------------------------
# Pure: cadence gates (unit-testable with no I/O)
# ---------------------------------------------------------------------------

def is_first_saturday(d: date) -> bool:
    """The NCRONTAB fires every Saturday; this in-code gate narrows to the
    FIRST Saturday of the month (spec §4)."""
    return d.weekday() == 5 and d.day <= 7


def count_sessions_since(last_cycle_date: str | None, today: date, report_dates: list[str]) -> int:
    """Trading sessions elapsed since the last COMPLETED cycle, approximated
    by counting `daily-reports` dates in `(last_cycle_date, today]` — one
    report per trading day the collector/analyzer ran. `None` (no prior
    cycle) never triggers observation-only mode: return a count that is
    always >= the threshold."""
    if last_cycle_date is None:
        return len(report_dates) + _MIN_SESSIONS_FOR_FULL_MODE
    today_str = today.isoformat()
    return sum(1 for d in report_dates if last_cycle_date < d <= today_str)


def is_observation_only(last_cycle_date: str | None, today: date, report_dates: list[str]) -> bool:
    return count_sessions_since(last_cycle_date, today, report_dates) < _MIN_SESSIONS_FOR_FULL_MODE


def due_amendment_ids(override_history: list[dict], learning_cycles: list[dict], today: date) -> list[str]:
    """Amendment OverrideHistory rows whose `review_by` is today-or-earlier
    and that no prior cycle's proposal has already re-reviewed (spec §9's
    forced re-review rule)."""
    amendments = [r for r in override_history if (r.get("layer") or "").lower() == "amendment"]
    already_reviewed: set[str] = set()
    for cyc in learning_cycles:
        for p in cyc.get("proposals", []) or []:
            rr = p.get("re_review_of")
            if rr:
                already_reviewed.add(rr)
    today_str = today.isoformat()
    due: list[str] = []
    for r in amendments:
        review_by = r.get("review_by")
        proposal_id = r.get("proposal_id") or r.get("RowKey")
        if not review_by or not proposal_id or proposal_id in already_reviewed:
            continue
        if review_by <= today_str:
            due.append(proposal_id)
    return due


# ---------------------------------------------------------------------------
# I/O wrappers
# ---------------------------------------------------------------------------

def _list_report_dates() -> list[str]:
    return sorted(
        n[:-3] for n in list_blob_names("daily-reports")
        if n.endswith(".md") and not n.startswith("_debug/")
    )


def _last_completed_cycle_date() -> str | None:
    rows = [r for r in query_entities(_CYCLES_TABLE) if r.get("status") == "completed"]
    if not rows:
        return None
    return max(str(r.get("RowKey") or "") for r in rows)


def already_ran_today(cycle_id: str) -> bool:
    """Rate limit for the manual `/learning_run` trigger (spec §4: 1/day)."""
    rows = query_entities(_CYCLES_TABLE, f"RowKey eq '{cycle_id}'")
    return len(rows) > 0


def _build_user_message(bundle: dict, observation_only: bool) -> str:
    parts: list[str] = [f"# Learning Loop cycle — {bundle['as_of']}\n"]
    if observation_only:
        parts.append(
            "**MODE: observation_only** — fewer than 15 trading sessions have "
            "elapsed since the last completed cycle. Emit class-0 proposals only "
            "(plus any forced re-reviews, which are also class-0 `keep`/`revert`/`amend`).\n"
        )
    parts.append(f"diff_base_sha: `{bundle['diff_base_sha']}`\n")

    parts.append("## Live config (verbatim, at diff_base_sha)\n")
    for path, content in bundle["live_config"].items():
        parts.append(f"### {path}\n```\n{content}\n```\n")

    parts.append("## FOLLOWUPS (open items only)\n" + bundle["followups_open"] + "\n")

    for title, key in (
        ("TradeHistory (capture-fine, all rows)", "trade_history"),
        ("OverrideHistory (all layers)", "override_history"),
        ("Learning history (prior cycles + proposal decisions)", "learning_history"),
        ("performance", "performance"),
        ("quadrant_performance", "quadrant_performance"),
    ):
        parts.append(f"## {title}\n```json\n{json.dumps(bundle[key], default=str)}\n```\n")

    parts.append("## Daily reports (verbatim, oldest first; see bundle_stats for any drops)\n")
    for d, md in bundle["daily_reports"]:
        parts.append(f"### Report — {d}\n{md}\n---\n")

    parts.append(f"\n## bundle_stats\n```json\n{json.dumps(bundle['bundle_stats'])}\n```\n")
    parts.append(
        "\nProduce this cycle's output as a single JSON document per your system "
        "instructions. Output ONLY the JSON — no prose outside it."
    )
    return "\n".join(parts)


def _persist_cycle(cycle_id: str, trigger: str, bundle: dict, parsed: dict, raw_output: str) -> None:
    write_json_blob(_LEARNING_CONTAINER, f"proposals/{cycle_id}.json", {
        "cycle": cycle_id,
        "trigger": trigger,
        "status": "completed",
        "diff_base_sha": bundle["diff_base_sha"],
        "bundle_stats": bundle["bundle_stats"],
        "narrative": parsed.get("narrative"),
        "mode": parsed.get("mode"),
        "model": _model_name(),
        "proposals": parsed.get("proposals", []),
    })
    upsert_entity(_CYCLES_TABLE, {
        "PartitionKey": cycle_id[:7],
        "RowKey": cycle_id,
        "trigger": trigger,
        "status": "completed",
        "model": _model_name(),
        "mode": parsed.get("mode") or "",
        "proposal_count": len(parsed.get("proposals", [])),
        "narrative": (parsed.get("narrative") or "")[:_ENTITY_TEXT_CAP],
    })
    for p in parsed.get("proposals", []):
        upsert_entity(_PROPOSALS_TABLE, {
            "PartitionKey": cycle_id[:7],
            "RowKey": p["id"],
            "cycle": cycle_id,
            "class": p.get("class"),
            "title": p.get("title", ""),
            "change_summary": p.get("change_summary", ""),
            "data_summary": p.get("data_summary", ""),
            "target_file": p.get("target_file", ""),
            "diff": (p.get("diff") or "")[:_ENTITY_TEXT_CAP],
            "evidence": json.dumps(p.get("evidence") or [])[:_ENTITY_TEXT_CAP],
            "evidence_n": p.get("evidence_n"),
            "expected_effect": (p.get("expected_effect") or "")[:_ENTITY_TEXT_CAP],
            "falsifier": (p.get("falsifier") or "")[:_ENTITY_TEXT_CAP],
            "review_by": p.get("review_by", ""),
            "spec_draft": (p.get("spec_draft") or "")[:_ENTITY_TEXT_CAP],
            "implementation_brief": (p.get("implementation_brief") or "")[:_ENTITY_TEXT_CAP],
            "re_review_of": p.get("re_review_of", ""),
            "is_revert": bool(p.get("is_revert", False)),
            "diff_base_sha": bundle["diff_base_sha"],
            "status": "pending",
            "decision": "",
            "decision_reason": "",
            "decided_at": "",
            "pr_url": "",
            "applied_at": "",
        })


def _persist_failed_cycle(cycle_id: str, trigger: str, bundle: dict | None, error_summary: str,
                          raw_output: str | None) -> None:
    write_json_blob(_LEARNING_CONTAINER, f"proposals/{cycle_id}.json", {
        "cycle": cycle_id,
        "trigger": trigger,
        "status": "failed_validation",
        "diff_base_sha": (bundle or {}).get("diff_base_sha"),
        "bundle_stats": (bundle or {}).get("bundle_stats"),
        "error": error_summary,
        "raw_output": raw_output,
    })
    upsert_entity(_CYCLES_TABLE, {
        "PartitionKey": cycle_id[:7],
        "RowKey": cycle_id,
        "trigger": trigger,
        "status": "failed_validation",
        "error": error_summary[:_ENTITY_TEXT_CAP],
    })


def run_cycle(trigger: str = "timer", date_str: str | None = None) -> dict:
    """Execute one Learning Loop reviewer cycle. `trigger` is `"timer"` or
    `"manual"` (recorded on the cycle row, no behavior difference otherwise).
    Returns `{"cycle", "status", ...}` — never raises on a validation failure
    (that is a normal, logged outcome); may raise on a genuine infra failure
    (Foundry unreachable, bundle build failed) so the caller's HTTP/timer
    wrapper can report it.
    """
    today = date.fromisoformat(date_str) if date_str else date.today()
    cycle_id = today.isoformat()

    report_dates = _list_report_dates()
    last_completed = _last_completed_cycle_date()
    observation_only = is_observation_only(last_completed, today, report_dates)

    bundle = build_bundle(today=today, max_tokens=_bundle_max_tokens())
    due_ids = due_amendment_ids(
        bundle["override_history"], bundle["learning_history"].get("cycles") or [], today,
    )

    system_prompt = _REVIEW_PROMPT_FILE.read_text(encoding="utf-8")
    user_message = _build_user_message(bundle, observation_only)

    secrets = load_secrets()
    api_key = secrets.get("FoundryApiKey")
    client = FoundryClient(api_key=api_key, model=_model_name())
    if not client.ready:
        raise RuntimeError("FoundryClient not ready for the Learning Loop reviewer")

    raw = client.complete(system=system_prompt, user_message=user_message,
                          max_tokens=_max_tokens(), temperature=_TEMPERATURE)

    result = validate_cycle_output(raw, bundle["live_config"], observation_only, due_ids)
    if not result["valid"]:
        logger.error("Learning cycle %s failed validation: %s", cycle_id, result["errors"])
        _persist_failed_cycle(cycle_id, trigger, bundle, "; ".join(result["errors"]), raw_output=raw)
        return {"cycle": cycle_id, "status": "failed_validation", "errors": result["errors"]}

    _persist_cycle(cycle_id, trigger, bundle, result["parsed"], raw_output=raw)
    logger.info(
        "Learning cycle %s completed: mode=%s proposals=%d",
        cycle_id, result["parsed"].get("mode"), len(result["parsed"].get("proposals", [])),
    )
    return {
        "cycle": cycle_id,
        "status": "completed",
        "mode": result["parsed"].get("mode"),
        "proposals": len(result["parsed"].get("proposals", [])),
    }
