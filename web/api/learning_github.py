"""GitHub API client for the Learning Loop's PR-based approval mechanics
(spec §8). Git is the only change mechanism — this module never writes
directly to a live file; it always opens a branch + commit + pull request for
a human to merge (or close). Nothing here can merge a PR: the fine-grained
PAT (`GITHUB_LEARNING_PAT`, KV-referenced app setting per the SWA hardening
pattern) is scoped `contents:write` + `pull_requests:write` only.

Reads of CURRENT master content use the public unauthenticated raw endpoint
(this repo is public) — the PAT is reserved for writes (branch/commit/PR),
matching spec §5/§8's scoping (the reviewer's bundle-building reads are also
unauthenticated; only §8's approval mechanics carry a credential).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request

from learning_diffcheck import apply_unified_diff, diff_applies_cleanly

logger = logging.getLogger("pfauto.web.api.learning_github")

_GITHUB_OWNER = "jgarrote83"
_GITHUB_REPO = "portfolio-automation"
_GITHUB_API_BASE = f"https://api.github.com/repos/{_GITHUB_OWNER}/{_GITHUB_REPO}"
_GITHUB_RAW_BASE = f"https://raw.githubusercontent.com/{_GITHUB_OWNER}/{_GITHUB_REPO}/master"
_TIMEOUT_SECONDS = 20


def _pat() -> str:
    token = os.environ.get("GITHUB_LEARNING_PAT")
    if not token:
        raise RuntimeError("GITHUB_LEARNING_PAT app setting is not set")
    return token


def _api(method: str, path: str, body: dict | None = None) -> dict:
    url = path if path.startswith("http") else f"{_GITHUB_API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {_pat()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {method} {path} -> {e.code}: {body_text[:500]}") from e


def fetch_raw_file(path: str, ref: str = "master") -> str:
    """Unauthenticated read of a file's content at `ref` (public repo)."""
    base = _GITHUB_RAW_BASE if ref == "master" else f"https://raw.githubusercontent.com/{_GITHUB_OWNER}/{_GITHUB_REPO}/{ref}"
    with urllib.request.urlopen(f"{base}/{path}", timeout=_TIMEOUT_SECONDS) as resp:
        return resp.read().decode("utf-8")


def get_master_sha() -> str:
    return _api("GET", "/git/refs/heads/master")["object"]["sha"]


def get_file_sha(path: str, ref: str = "master") -> str | None:
    """The blob sha of an EXISTING file on `ref`, or None if it doesn't exist
    yet (a brand-new file, e.g. a class-3 spec draft, has no prior sha)."""
    try:
        return _api("GET", f"/contents/{path}?ref={ref}")["sha"]
    except RuntimeError as e:
        if "-> 404" in str(e):
            return None
        raise


def create_branch(branch_name: str, from_sha: str) -> None:
    _api("POST", "/git/refs", {"ref": f"refs/heads/{branch_name}", "sha": from_sha})


def put_file(branch_name: str, path: str, content: str, message: str, file_sha: str | None) -> None:
    body: dict = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch_name,
    }
    if file_sha:
        body["sha"] = file_sha
    _api("PUT", f"/contents/{path}", body)


def open_pull_request(branch_name: str, title: str, body_md: str) -> dict:
    return _api("POST", "/pulls", {"title": title, "head": branch_name, "base": "master", "body": body_md})


def get_pull_request(pr_number: int) -> dict:
    return _api("GET", f"/pulls/{pr_number}")


def is_pr_merged(pr_number: int) -> bool:
    return bool(get_pull_request(pr_number).get("merged"))


def _pr_body(proposal: dict) -> str:
    return "\n".join([
        "**Learning Loop proposal — auto-generated, human review required.**",
        "",
        "```json",
        json.dumps(proposal, indent=2, default=str),
        "```",
        "",
        f"**Evidence:** {'; '.join(proposal.get('evidence') or [])}",
        f"**Falsifier:** {proposal.get('falsifier', '')}",
        f"**Review by:** {proposal.get('review_by', '')}",
        "",
        "_The automation credential cannot merge this PR (branch protection). "
        "Full CI (pytest + ruff) must pass before merging._",
    ])


def _followups_entry(proposal: dict) -> str:
    return (
        f"\n### Learning Loop observation — {proposal.get('id')} "
        f"(review by {proposal.get('review_by', 'n/a')})\n"
        f"{proposal.get('change_summary', '')}\n\n"
        f"**Data:** {proposal.get('data_summary', '')}\n\n"
        f"**Evidence:** {'; '.join(proposal.get('evidence') or [])}\n\n"
        f"**Falsifier:** {proposal.get('falsifier', '')}\n"
    )


def _append_followups_open(current_text: str, proposal: dict) -> str:
    """Insert a new entry right after the `## Open` heading — the entry is
    correctly filed and complete; a human may reposition/renumber it in
    review, but its content never depends on that."""
    marker = "\n## Open\n"
    idx = current_text.find(marker)
    entry = _followups_entry(proposal)
    if idx == -1:
        return current_text.rstrip() + "\n" + entry
    insert_at = idx + len(marker)
    return current_text[:insert_at] + entry + current_text[insert_at:]


def approve_proposal(proposal: dict) -> dict:
    """Re-validate (classes 1-2 only) against CURRENT master, then branch +
    commit + PR. Returns `{"status": "approved", "pr_url", "pr_number", "branch"}`
    or `{"status": "stale", "reason": ...}` — NEVER force-applies a diff that
    no longer matches current master (spec §8)."""
    cls = proposal.get("class")
    branch = f"amend/{proposal.get('cycle') or proposal['id']}-{proposal['id']}"
    base_sha = get_master_sha()

    if cls == 3:
        path = f"docs/specs/proposals/{proposal['id']}.md"
        content = proposal.get("spec_draft", "")
        file_sha = None
    elif cls == 0:
        path = "FOLLOWUPS.md"
        current = fetch_raw_file(path)
        content = _append_followups_open(current, proposal)
        file_sha = get_file_sha(path)
    else:
        path = proposal.get("target_file") or ""
        current = fetch_raw_file(path)
        ok, reason = diff_applies_cleanly(current, proposal.get("diff") or "")
        if not ok:
            logger.warning("Learning proposal %s is stale: %s", proposal.get("id"), reason)
            return {"status": "stale", "reason": reason}
        content = apply_unified_diff(current, proposal["diff"])
        file_sha = get_file_sha(path)

    create_branch(branch, base_sha)
    put_file(branch, path, content, f"Learning Loop: {proposal.get('title') or proposal['id']}", file_sha)
    pr = open_pull_request(
        branch, f"[Learning Loop] {proposal.get('title') or proposal['id']}", _pr_body(proposal),
    )
    return {
        "status": "approved",
        "pr_url": pr.get("html_url"),
        "pr_number": pr.get("number"),
        "branch": branch,
    }
