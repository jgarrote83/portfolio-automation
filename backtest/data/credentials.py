"""Alpaca key loading for the backtest data layer.

Keys come from a GITIGNORED `.env` at the repo root (or the process environment) -- never from
Key Vault, never printed, never logged. Before the file is read, git must confirm it is ignored
(fail closed: if that cannot be confirmed the file is not read).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .config import DEFAULT_ENV_PATH, REPO_ROOT

KEY_VAR = "ALPACA_API_KEY"
SECRET_VAR = "ALPACA_API_SECRET"


class MissingCredentialsError(RuntimeError):
    """No usable Alpaca keys. The message says how to create `.env`; it never contains a key."""


def _is_git_ignored(path: Path) -> bool:
    try:
        rel = path.resolve().relative_to(REPO_ROOT.resolve())
    except ValueError:
        return False                                  # outside the repo: cannot confirm -> refuse
    try:
        res = subprocess.run(["git", "check-ignore", "-q", str(rel)], cwd=REPO_ROOT,
                             capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0


def parse_env(text: str) -> dict[str, str]:
    """Minimal KEY=VALUE parser (comments, blank lines, optional `export `, optional quotes)."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def load_credentials(env_path: Path | None = None) -> tuple[str, str]:
    """Return (key, secret). Order: process environment, then the gitignored `.env`."""
    key, secret = os.environ.get(KEY_VAR), os.environ.get(SECRET_VAR)
    if key and secret:
        return key, secret
    path = Path(env_path) if env_path else DEFAULT_ENV_PATH
    if path.is_file():
        if not _is_git_ignored(path):
            raise MissingCredentialsError(
                f"{path.name} exists but git does not confirm it is ignored -- refusing to read it. "
                "Add it to .gitignore (it already lists `.env`) and re-run.")
        vals = parse_env(path.read_text(encoding="utf-8"))
        key, secret = key or vals.get(KEY_VAR), secret or vals.get(SECRET_VAR)
        if key and secret:
            return key, secret
    raise MissingCredentialsError(
        f"Alpaca keys not found. Create a gitignored `.env` at the repo root containing two lines "
        f"({KEY_VAR}=<paper key id> and {SECRET_VAR}=<paper secret>) -- type them into the file "
        "yourself; never paste them into chat -- or set the same two environment variables. "
        "Offline mode (offline=True / --offline) needs no keys.")
