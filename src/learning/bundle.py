"""Learning Loop v1.0 — reviewer input bundle builder (spec §5).

Assembles the monthly reviewer's input bundle: daily reports, capture-fine
TradeHistory rows, all-layer OverrideHistory, live config fetched from GitHub
raw at master HEAD (NOT the deployed function package — a proposal diff must
apply to master, not to whatever the package was built from), open FOLLOWUPS
items, prior learning-cycle history, and the latest snapshot's performance
blocks.

Kept out of `src/collector/` deliberately (spec §5: "not the collector — keep
the daily path untouched"). Pure where possible: `_fit_reports_to_budget` and
`_split_followups_open` are pure functions over already-fetched text so they
are unit-testable without any Azure/network mocking; the I/O lives in thin
wrapper functions (`_fetch_*`) that `build_bundle` composes.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

import requests

from shared.storage import (
    list_blob_names,
    list_snapshot_dates,
    query_entities,
    read_blob_bytes,
    read_json_blob,
    read_snapshot,
)

logger = logging.getLogger(__name__)

_GITHUB_OWNER = "jgarrote83"
_GITHUB_REPO = "portfolio-automation"
_GITHUB_API_BASE = f"https://api.github.com/repos/{_GITHUB_OWNER}/{_GITHUB_REPO}"
_GITHUB_RAW_BASE = f"https://raw.githubusercontent.com/{_GITHUB_OWNER}/{_GITHUB_REPO}/master"
_GITHUB_TIMEOUT_SECONDS = 15

# Live config the reviewer reasons against — same set as the class 1-2 target-file
# allowlist (schema.py) plus FOLLOWUPS.md, which is reference-only (never a diff target).
LIVE_CONFIG_FILES = (
    "src/config/project-instructions.md",
    "src/config/risk-limits.json",
    "src/config/sleeve-roles.json",
    "config/flex-candidates.json",
)
FOLLOWUPS_PATH = "FOLLOWUPS.md"

_TRAILING_REPORT_DAYS = 35
_DEFAULT_BUNDLE_MAX_TOKENS = 150_000
# Spec §5: "Estimate tokens as chars/4."
_CHARS_PER_TOKEN = 4

_OVERRIDE_HISTORY_LAYERS = (
    "override", "sleeve_switch", "intl_leader_rotation", "regime_suspect", "amendment",
)

_LEARNING_CONTAINER = "learning"
_LEARNING_PROPOSALS_TABLE = "LearningProposals"


def _estimate_tokens(text: str) -> int:
    """Spec §5's estimator: chars / 4. Never used as a real tokenizer — a
    deliberately conservative, dependency-free approximation."""
    return len(text or "") // _CHARS_PER_TOKEN


# ---------------------------------------------------------------------------
# Pure: budget truncation (unit-testable with no I/O)
# ---------------------------------------------------------------------------

def _fit_reports_to_budget(
    reports: list[tuple[str, str]], fixed_tokens: int, max_tokens: int,
) -> tuple[list[tuple[str, str]], dict]:
    """Drop the OLDEST daily reports first until the bundle fits `max_tokens`.

    `reports` is `[(date, markdown), ...]` sorted ASCENDING (oldest first) —
    the caller guarantees this ordering so "drop oldest" is simply popping
    index 0. `fixed_tokens` is the estimated cost of everything that must
    NEVER drop (TradeHistory, OverrideHistory, live config, FOLLOWUPS,
    learning history, performance blocks) — reports are the only truncatable
    section. Returns (kept_reports, stats) — stats always reflects what
    actually happened, even when nothing was dropped.
    """
    kept = list(reports)
    dropped: list[tuple[str, str]] = []

    def _total() -> int:
        return fixed_tokens + sum(_estimate_tokens(md) for _, md in kept)

    while kept and _total() > max_tokens:
        dropped.append(kept.pop(0))

    stats = {
        "fixed_tokens_est": fixed_tokens,
        "reports_total": len(reports),
        "reports_kept": len(kept),
        "reports_dropped": len(dropped),
        "dropped_dates": [d for d, _ in dropped],
        "total_tokens_est": _total(),
        "max_tokens": max_tokens,
        "over_budget": _total() > max_tokens,
    }
    return kept, stats


def _split_followups_open(text: str) -> str:
    """Everything between the `## Open` and `## Done` headings (open items
    only) — the reviewer never sees the Done section. Falls back to the full
    text if either heading is missing (fail open, not silently empty)."""
    open_idx = text.find("\n## Open")
    done_idx = text.find("\n## Done")
    if open_idx == -1 or done_idx == -1 or done_idx <= open_idx:
        return text
    return text[open_idx:done_idx].strip()


# ---------------------------------------------------------------------------
# I/O wrappers (thin — mocked out in tests)
# ---------------------------------------------------------------------------

def fetch_master_sha(session: requests.Session | None = None) -> str:
    """Current master HEAD commit SHA via the GitHub API (public repo, no auth
    needed for reads). Recorded as `diff_base_sha` — proposal diffs must apply
    cleanly against the config fetched at this exact SHA."""
    resp = (session or requests).get(
        f"{_GITHUB_API_BASE}/commits/master",
        headers={"Accept": "application/vnd.github+json"},
        timeout=_GITHUB_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json()["sha"]


def fetch_live_config(session: requests.Session | None = None) -> dict[str, str]:
    """{path: content} for the 4 allowlisted config files, from GitHub raw at
    master HEAD — deliberately NOT the deployed function package (spec §5)."""
    out: dict[str, str] = {}
    for path in LIVE_CONFIG_FILES:
        resp = (session or requests).get(f"{_GITHUB_RAW_BASE}/{path}", timeout=_GITHUB_TIMEOUT_SECONDS)
        resp.raise_for_status()
        out[path] = resp.text
    return out


def fetch_followups_open(session: requests.Session | None = None) -> str:
    """FOLLOWUPS.md's open items only, from GitHub raw at master HEAD."""
    resp = (session or requests).get(f"{_GITHUB_RAW_BASE}/{FOLLOWUPS_PATH}", timeout=_GITHUB_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return _split_followups_open(resp.text)


def fetch_daily_reports(today: date, days: int = _TRAILING_REPORT_DAYS) -> list[tuple[str, str]]:
    """`[(date, markdown), ...]` for `daily-reports/*.md` in the trailing
    `days` calendar days, sorted ASCENDING (oldest first — see
    `_fit_reports_to_budget`)."""
    cutoff = (today - timedelta(days=days)).isoformat()
    names = [
        n for n in list_blob_names("daily-reports")
        if n.endswith(".md") and not n.startswith("_debug/")
    ]
    dated = [(n[:-3], n) for n in names if n[:-3] >= cutoff]
    dated.sort(key=lambda p: p[0])
    out: list[tuple[str, str]] = []
    for d, blob_name in dated:
        try:
            out.append((d, read_blob_bytes("daily-reports", blob_name).decode("utf-8")))
        except Exception:  # noqa: BLE001
            logger.warning("Learning bundle: could not read daily-reports/%s", blob_name)
    return out


def fetch_trade_history() -> list[dict]:
    """All TradeHistory rows, all columns — capture-fine (spec §5 item 2); the
    report-coarse promotion rule (`_aggregate_track_record`) does not apply
    here, so this reads the raw table rather than the collector's aggregate."""
    return query_entities("TradeHistory")


def fetch_override_history() -> list[dict]:
    """All OverrideHistory rows, every layer (override, sleeve_switch,
    intl_leader_rotation, regime_suspect, amendment) — no filtering."""
    return query_entities("OverrideHistory")


def fetch_learning_history() -> dict:
    """Prior cycles' proposals/narratives (blobs) + every proposal row's
    current status/decision/reason (table) — the reviewer correlates the two
    by `id` itself rather than have this module pre-merge them."""
    cycle_names = [n for n in list_blob_names(_LEARNING_CONTAINER) if n.startswith("proposals/")]
    cycles = []
    for name in sorted(cycle_names):
        data = read_json_blob(_LEARNING_CONTAINER, name)
        if isinstance(data, dict):
            cycles.append(data)
    proposals = query_entities(_LEARNING_PROPOSALS_TABLE)
    return {"cycles": cycles, "proposals": proposals}


def fetch_latest_performance_blocks() -> dict:
    """`performance` + `quadrant_performance` from the most recent daily
    snapshot ({} for either if unavailable)."""
    dates = list_snapshot_dates()
    if not dates:
        return {"performance": {}, "quadrant_performance": {}}
    try:
        snap = read_snapshot(dates[-1])
    except Exception:  # noqa: BLE001
        logger.warning("Learning bundle: could not read latest snapshot %s", dates[-1])
        return {"performance": {}, "quadrant_performance": {}}
    return {
        "performance": snap.get("performance") or {},
        "quadrant_performance": snap.get("quadrant_performance") or {},
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_bundle(today: date | None = None, max_tokens: int | None = None) -> dict:
    """Assemble the full reviewer input bundle (spec §5).

    Non-pure (does real I/O: blob/table reads + 2 GitHub HTTP calls per config
    file). Returns a dict with the bundle sections plus `diff_base_sha` and
    `bundle_stats` (per-section token estimates + what, if anything, the
    budget trim dropped).
    """
    today = today or date.today()
    max_tokens = max_tokens if max_tokens is not None else _DEFAULT_BUNDLE_MAX_TOKENS

    diff_base_sha = fetch_master_sha()
    live_config = fetch_live_config()
    followups_open = fetch_followups_open()
    trade_history = fetch_trade_history()
    override_history = fetch_override_history()
    learning_history = fetch_learning_history()
    perf_blocks = fetch_latest_performance_blocks()
    reports = fetch_daily_reports(today)

    fixed_tokens = sum(_estimate_tokens(v) for v in live_config.values())
    fixed_tokens += _estimate_tokens(followups_open)
    fixed_tokens += _estimate_tokens(json.dumps(trade_history, default=str))
    fixed_tokens += _estimate_tokens(json.dumps(override_history, default=str))
    fixed_tokens += _estimate_tokens(json.dumps(learning_history, default=str))
    fixed_tokens += _estimate_tokens(json.dumps(perf_blocks, default=str))

    kept_reports, report_stats = _fit_reports_to_budget(reports, fixed_tokens, max_tokens)

    bundle_stats = {
        **report_stats,
        "trade_history_rows": len(trade_history),
        "override_history_rows": len(override_history),
        "learning_cycles": len(learning_history.get("cycles") or []),
        "learning_proposals": len(learning_history.get("proposals") or []),
    }

    return {
        "as_of": today.isoformat(),
        "diff_base_sha": diff_base_sha,
        "daily_reports": kept_reports,
        "trade_history": trade_history,
        "override_history": override_history,
        "live_config": live_config,
        "followups_open": followups_open,
        "learning_history": learning_history,
        "performance": perf_blocks.get("performance") or {},
        "quadrant_performance": perf_blocks.get("quadrant_performance") or {},
        "bundle_stats": bundle_stats,
    }
