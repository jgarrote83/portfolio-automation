"""Run provenance: the code commit, whether the tree is dirty, and whether the pre-registration is
still what it was at its first commit (pre-registration, header requirement; repo rule 7).

Everything degrades to None rather than raising (a shallow CI clone has no history to search).
Line endings are normalised before hashing: Windows checkouts rewrite LF as CRLF, which must not
read as an edit.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from .data.config import REPO_ROOT

PREREG_PATH = "docs/specs/ORB_Phase2_Preregistration.md"
# A dated section with this heading may be APPENDED to a locked pre-registration ("Post-run clarification, no
# rule change"). It is excluded from the rule-text hash, so the check still proves nothing above it was edited.
CLARIFICATION_MARKER = "\n## Post-run clarification"


def _git(args: list[str], repo: Path, *, text: bool = True):
    try:
        res = subprocess.run(["git", *args], cwd=repo, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return res.stdout.decode("utf-8", errors="replace").strip() if text else res.stdout


def _sha256_lf(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _rules_text(data: bytes) -> tuple[bytes, bool]:
    """The pre-registration's rule text: LF-normalised, cut before an appended clarification section, trailing
    newlines normalised. Returns (bytes, whether a clarification section was present)."""
    text = data.replace(b"\r\n", b"\n")
    marker = CLARIFICATION_MARKER.encode("utf-8")
    i = text.find(marker)
    has_note = i >= 0
    if has_note:
        text = text[:i]
    return text.rstrip(b"\n") + b"\n", has_note


def code_commit(repo: Path | None = None) -> dict:
    repo = repo or REPO_ROOT
    sha = _git(["rev-parse", "HEAD"], repo)
    dirty = _git(["status", "--porcelain", "--untracked-files=no"], repo)
    return {"sha": sha, "dirty": None if dirty is None else bool(dirty)}


def prereg_info(repo: Path | None = None, path: str = PREREG_PATH) -> dict:
    """{path, first_commit, sha256_at_first_commit, sha256_now, unchanged, has_post_run_note}.

    The hashes cover the RULE TEXT (everything before an appended "Post-run clarification" section), so a dated
    note that changes no rule does not read as an edit while any edit above it still does. `unchanged` is True
    only when both hashes are known and equal; None when it cannot be determined."""
    repo = repo or REPO_ROOT
    out = {"path": path, "first_commit": None, "sha256_at_first_commit": None, "sha256_now": None,
           "unchanged": None, "has_post_run_note": None}
    f = repo / path
    if f.is_file():
        rules, has_note = _rules_text(f.read_bytes())
        out["sha256_now"], out["has_post_run_note"] = hashlib.sha256(rules).hexdigest(), has_note
    added = _git(["log", "--diff-filter=A", "--format=%H", "--", path], repo)
    if added:
        first = added.splitlines()[-1].strip()          # the oldest add commit
        out["first_commit"] = first
        blob = _git(["show", f"{first}:{path}"], repo, text=False)
        if blob is not None:
            out["sha256_at_first_commit"] = hashlib.sha256(_rules_text(blob)[0]).hexdigest()
    if out["sha256_now"] and out["sha256_at_first_commit"]:
        out["unchanged"] = out["sha256_now"] == out["sha256_at_first_commit"]
    return out
