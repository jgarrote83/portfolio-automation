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


def code_commit(repo: Path | None = None) -> dict:
    repo = repo or REPO_ROOT
    sha = _git(["rev-parse", "HEAD"], repo)
    dirty = _git(["status", "--porcelain", "--untracked-files=no"], repo)
    return {"sha": sha, "dirty": None if dirty is None else bool(dirty)}


def prereg_info(repo: Path | None = None, path: str = PREREG_PATH) -> dict:
    """{path, first_commit, sha256_at_first_commit, sha256_now, unchanged}. `unchanged` is True only
    when both hashes are known and equal; None when it cannot be determined."""
    repo = repo or REPO_ROOT
    out = {"path": path, "first_commit": None, "sha256_at_first_commit": None,
           "sha256_now": None, "unchanged": None}
    f = repo / path
    if f.is_file():
        out["sha256_now"] = _sha256_lf(f.read_bytes())
    added = _git(["log", "--diff-filter=A", "--format=%H", "--", path], repo)
    if added:
        first = added.splitlines()[-1].strip()          # the oldest add commit
        out["first_commit"] = first
        blob = _git(["show", f"{first}:{path}"], repo, text=False)
        if blob is not None:
            out["sha256_at_first_commit"] = _sha256_lf(blob)
    if out["sha256_now"] and out["sha256_at_first_commit"]:
        out["unchanged"] = out["sha256_now"] == out["sha256_at_first_commit"]
    return out
