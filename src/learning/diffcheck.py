"""Pure-Python unified-diff apply checker.

No third-party dependency and no shell-out to `git`/`patch` — the reviewer's
diffs must be checkable in a plain Python process (the schema validator,
`shared/keyvault`-only Azure Functions, and later the SWA managed API on
Approve). Deliberately strict: a context or removal-line mismatch is a hard
failure, never a fuzzy/best-effort apply — a diff that doesn't apply EXACTLY
against the recorded `diff_base_sha` content must never silently patch
something else instead (spec §8: "nothing is force-applied").

Supports the standard unified-diff hunk format (`@@ -l,s +l,s @@` with
` `/`-`/`+` line prefixes), the same shape produced by `diff -u`, `git diff`,
and virtually every LLM asked for a "unified diff". Optional `---`/`+++` file
headers are skipped if present.
"""
from __future__ import annotations

import re

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class DiffApplyError(Exception):
    """A unified diff does not apply cleanly to the given original text."""


def _strip_nl(line: str) -> str:
    return line[:-1] if line.endswith("\n") else line


def apply_unified_diff(original: str, diff_text: str) -> str:
    """Apply `diff_text` to `original`; returns the patched text.

    Raises `DiffApplyError` on any hunk-header, context, or removal mismatch —
    callers that only need a yes/no should use `diff_applies_cleanly` instead.
    """
    orig_lines = original.splitlines(keepends=True)
    result: list[str] = []
    orig_idx = 0

    lines = diff_text.splitlines()
    i, n = 0, len(lines)
    while i < n and (lines[i].startswith("---") or lines[i].startswith("+++")):
        i += 1
    if i >= n:
        raise DiffApplyError("no hunks found in diff")

    saw_hunk = False
    while i < n:
        line = lines[i]
        if not line.startswith("@@"):
            i += 1
            continue
        saw_hunk = True
        m = _HUNK_RE.match(line)
        if not m:
            raise DiffApplyError(f"malformed hunk header: {line!r}")
        old_start = int(m.group(1)) - 1  # 1-based -> 0-based
        i += 1
        if old_start < orig_idx:
            raise DiffApplyError(
                f"hunk out of order or overlapping (starts at original line "
                f"{old_start + 1}, already consumed through {orig_idx})"
            )
        result.extend(orig_lines[orig_idx:old_start])
        orig_idx = old_start

        while i < n and not lines[i].startswith("@@"):
            hline = lines[i]
            if hline == "":
                tag, content = " ", ""
            else:
                tag, content = hline[0], hline[1:]

            if tag == " ":
                if orig_idx >= len(orig_lines) or _strip_nl(orig_lines[orig_idx]) != content:
                    got = _strip_nl(orig_lines[orig_idx]) if orig_idx < len(orig_lines) else None
                    raise DiffApplyError(
                        f"context mismatch at original line {orig_idx + 1}: "
                        f"expected {content!r}, got {got!r}"
                    )
                result.append(orig_lines[orig_idx])
                orig_idx += 1
            elif tag == "-":
                if orig_idx >= len(orig_lines) or _strip_nl(orig_lines[orig_idx]) != content:
                    got = _strip_nl(orig_lines[orig_idx]) if orig_idx < len(orig_lines) else None
                    raise DiffApplyError(
                        f"removal mismatch at original line {orig_idx + 1}: "
                        f"expected {content!r}, got {got!r}"
                    )
                orig_idx += 1
            elif tag == "+":
                result.append(content + "\n")
            elif tag == "\\":
                pass  # "\ No newline at end of file" — not modeled, harmless to skip
            else:
                raise DiffApplyError(f"unrecognized diff line: {hline!r}")
            i += 1

    if not saw_hunk:
        raise DiffApplyError("no hunks found in diff")
    result.extend(orig_lines[orig_idx:])
    return "".join(result)


def diff_applies_cleanly(original: str, diff_text: str) -> tuple[bool, str | None]:
    """`(True, None)` if `diff_text` applies cleanly to `original`, else
    `(False, <reason>)`. Never raises."""
    try:
        apply_unified_diff(original, diff_text)
        return True, None
    except DiffApplyError as e:
        return False, str(e)
