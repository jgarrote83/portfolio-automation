"""Learning Loop v1.0 — deterministic proposal-schema validation (spec §6).

Pure (no I/O, no Azure, no network): every input the validator needs (the raw
model output string, the diff-base file contents, the observation-only flag,
the list of amendment ids due for forced re-review) is passed in by the
caller (`learning/handler.py`, Task C), which is the only place that touches
Azure/Foundry/network.

Out-of-schema output fails the WHOLE cycle loudly — there is no partial
acceptance. `validate_cycle_output` always returns every error it found
(not just the first), because the raw output is preserved for human
inspection on a failed cycle and a full error list is more useful than a
single one.
"""
from __future__ import annotations

import json

from learning.diffcheck import diff_applies_cleanly

TARGET_FILE_ALLOWLIST = (
    "src/config/project-instructions.md",
    "src/config/risk-limits.json",
    "src/config/sleeve-roles.json",
    "config/flex-candidates.json",
)
MAX_NON_CLASS0_PROPOSALS = 3
MAX_CLASS3_PROPOSALS = 1
CHANGE_SUMMARY_MAX_CHARS = 120
DATA_SUMMARY_MAX_CHARS = 140
CLASS2_MIN_EVIDENCE_N = 10
VALID_CLASSES = (0, 1, 2, 3)
VALID_MODES = ("full", "observation_only")


def _err(errors: list[str], proposal_id: str | None, msg: str) -> None:
    tag = f"[{proposal_id}] " if proposal_id else ""
    errors.append(f"{tag}{msg}")


def _validate_proposal(
    p: dict, diff_base_content: dict[str, str], errors: list[str],
) -> None:
    pid = p.get("id") if isinstance(p.get("id"), str) else None

    if not isinstance(p.get("id"), str) or not p["id"].strip():
        _err(errors, pid, "missing or empty 'id'")
    cls = p.get("class")
    if cls not in VALID_CLASSES:
        _err(errors, pid, f"invalid 'class': {cls!r} (must be one of {VALID_CLASSES})")
        return  # class-specific checks below assume a valid class
    if not isinstance(p.get("title"), str) or not p["title"].strip():
        _err(errors, pid, "missing or empty 'title'")

    cs = p.get("change_summary")
    if not isinstance(cs, str) or not cs.strip():
        _err(errors, pid, "missing or empty 'change_summary'")
    elif len(cs) > CHANGE_SUMMARY_MAX_CHARS:
        _err(errors, pid, f"'change_summary' is {len(cs)} chars, max {CHANGE_SUMMARY_MAX_CHARS}")

    ds = p.get("data_summary")
    if not isinstance(ds, str) or not ds.strip():
        _err(errors, pid, "missing or empty 'data_summary'")
    elif len(ds) > DATA_SUMMARY_MAX_CHARS:
        _err(errors, pid, f"'data_summary' is {len(ds)} chars, max {DATA_SUMMARY_MAX_CHARS}")

    if cls in (1, 2):
        target = p.get("target_file")
        if target not in TARGET_FILE_ALLOWLIST:
            _err(errors, pid, f"'target_file' {target!r} is not in the allowlist {TARGET_FILE_ALLOWLIST}")
            target = None
        diff = p.get("diff")
        if not isinstance(diff, str) or not diff.strip():
            _err(errors, pid, "class 1-2 proposal missing a 'diff'")
            diff = None
        if cls == 2:
            n = p.get("evidence_n")
            if not isinstance(n, (int, float)) or isinstance(n, bool) or n < CLASS2_MIN_EVIDENCE_N:
                _err(errors, pid, f"class 2 requires 'evidence_n' >= {CLASS2_MIN_EVIDENCE_N}, got {n!r}")
        if target is not None and diff is not None:
            base = diff_base_content.get(target)
            if base is None:
                _err(errors, pid, f"no base content available for target_file {target!r}")
            else:
                ok, reason = diff_applies_cleanly(base, diff)
                if not ok:
                    _err(errors, pid, f"diff does not apply cleanly to {target!r}: {reason}")

    elif cls == 3:
        if p.get("diff"):
            _err(errors, pid, "class 3 (structural) proposals must not carry a 'diff'")
        if not isinstance(p.get("spec_draft"), str) or not p["spec_draft"].strip():
            _err(errors, pid, "class 3 proposal missing 'spec_draft'")
        if not isinstance(p.get("implementation_brief"), str) or not p["implementation_brief"].strip():
            _err(errors, pid, "class 3 proposal missing 'implementation_brief'")


def validate_cycle_output(
    raw_output: str,
    diff_base_content: dict[str, str],
    observation_only: bool = False,
    due_amendment_ids: list[str] | None = None,
) -> dict:
    """Validate one cycle's raw model output against the full proposal contract.

    Returns `{"valid": bool, "errors": [str, ...], "parsed": dict | None}` —
    `parsed` is the decoded JSON document when parseable (even if invalid),
    so a failed cycle can still preserve/display what the model actually said.
    """
    errors: list[str] = []
    try:
        doc = json.loads(raw_output)
    except json.JSONDecodeError as e:
        return {"valid": False, "errors": [f"JSON parse failed: {e}"], "parsed": None}

    if not isinstance(doc, dict):
        return {"valid": False, "errors": ["top-level output must be a JSON object"], "parsed": None}

    if not isinstance(doc.get("narrative"), str) or not doc["narrative"].strip():
        errors.append("missing or empty top-level 'narrative'")

    mode = doc.get("mode")
    if mode not in VALID_MODES:
        errors.append(f"invalid top-level 'mode': {mode!r} (must be one of {VALID_MODES})")

    proposals = doc.get("proposals")
    if not isinstance(proposals, list):
        errors.append("top-level 'proposals' must be a list")
        proposals = []

    if observation_only and mode != "observation_only":
        errors.append(
            "bundle indicated observation-only mode (<15 sessions since last "
            "completed cycle) but top-level 'mode' was not 'observation_only'"
        )

    seen_ids: set[str] = set()
    for p in proposals:
        if not isinstance(p, dict):
            errors.append(f"proposal entry is not an object: {p!r}")
            continue
        pid = p.get("id") if isinstance(p.get("id"), str) else None
        if pid and pid in seen_ids:
            errors.append(f"[{pid}] duplicate proposal id")
        elif pid:
            seen_ids.add(pid)

        if (observation_only or mode == "observation_only") and p.get("class") != 0:
            errors.append(
                f"[{pid or '?'}] observation-only mode permits class-0 proposals "
                f"only, got class {p.get('class')!r}"
            )

        _validate_proposal(p, diff_base_content, errors)

    non_class0 = [p for p in proposals if isinstance(p, dict) and p.get("class") != 0]
    capped = [p for p in non_class0 if not p.get("is_revert")]
    if len(capped) > MAX_NON_CLASS0_PROPOSALS:
        errors.append(
            f"{len(capped)} non-observation proposals exceed the cap of "
            f"{MAX_NON_CLASS0_PROPOSALS} (reverts are exempt)"
        )
    class3_capped = [p for p in capped if p.get("class") == 3]
    if len(class3_capped) > MAX_CLASS3_PROPOSALS:
        errors.append(
            f"{len(class3_capped)} class-3 (structural) proposals exceed the cap "
            f"of {MAX_CLASS3_PROPOSALS} (reverts are exempt)"
        )

    if due_amendment_ids:
        reviewed = {
            p.get("re_review_of") for p in proposals
            if isinstance(p, dict) and p.get("re_review_of")
        }
        for amd_id in due_amendment_ids:
            if amd_id not in reviewed:
                errors.append(
                    f"amendment {amd_id!r} is past its review_by and was not "
                    "re-reviewed this cycle (expected a proposal with "
                    f"re_review_of={amd_id!r})"
                )

    return {"valid": len(errors) == 0, "errors": errors, "parsed": doc}
