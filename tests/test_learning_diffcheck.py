"""Pure-Python unified-diff apply checker tests (src/learning/diffcheck.py).
Run: PYTHONPATH=src pytest tests/test_learning_diffcheck.py
"""
import difflib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from learning.diffcheck import DiffApplyError, apply_unified_diff, diff_applies_cleanly  # noqa: E402


def _diff(original: str, modified: str) -> str:
    return "".join(difflib.unified_diff(
        original.splitlines(keepends=True), modified.splitlines(keepends=True), lineterm="\n",
    ))


def test_clean_apply_single_hunk():
    original = "line1\nline2\nline3\nline4\nline5\n"
    modified = "line1\nline2\nCHANGED\nline4\nline5\n"
    diff = _diff(original, modified)
    assert apply_unified_diff(original, diff) == modified


def test_clean_apply_multiple_hunks():
    original = "".join(f"line{i}\n" for i in range(1, 21))
    modified_lines = original.splitlines(keepends=True)
    modified_lines[1] = "CHANGED-EARLY\n"
    modified_lines[17] = "CHANGED-LATE\n"
    modified = "".join(modified_lines)
    diff = _diff(original, modified)
    assert apply_unified_diff(original, diff) == modified


def test_clean_apply_insertion_only():
    original = "a\nb\nc\n"
    modified = "a\nb\nNEW\nc\n"
    diff = _diff(original, modified)
    assert apply_unified_diff(original, diff) == modified


def test_clean_apply_deletion_only():
    original = "a\nb\nc\n"
    modified = "a\nc\n"
    diff = _diff(original, modified)
    assert apply_unified_diff(original, diff) == modified


def test_context_mismatch_fails():
    original = "line1\nline2\nline3\nline4\nline5\n"
    modified = "line1\nline2\nCHANGED\nline4\nline5\n"
    diff = _diff(original, modified)
    bad_original = "line1\nDIFFERENT\nline3\nline4\nline5\n"
    ok, reason = diff_applies_cleanly(bad_original, diff)
    assert ok is False
    assert "context mismatch" in reason


def test_removal_mismatch_fails():
    original = "line1\nline2\nline3\n"
    modified = "line1\nCHANGED\nline3\n"
    diff = _diff(original, modified)
    bad_original = "line1\nNOT-LINE2\nline3\n"
    ok, reason = diff_applies_cleanly(bad_original, diff)
    assert ok is False
    assert "mismatch" in reason


def test_apply_unified_diff_raises_on_mismatch():
    diff = _diff("a\nb\nc\n", "a\nX\nc\n")
    try:
        apply_unified_diff("a\nZZZ\nc\n", diff)
        assert False, "expected DiffApplyError"
    except DiffApplyError as e:
        assert "mismatch" in str(e)


def test_no_hunks_in_diff_fails():
    ok, reason = diff_applies_cleanly("a\nb\n", "not a diff at all")
    assert ok is False
    assert "no hunks" in reason


def test_empty_diff_fails():
    ok, reason = diff_applies_cleanly("a\nb\n", "")
    assert ok is False


def test_diff_with_file_headers_skipped():
    original = "a\nb\nc\n"
    modified = "a\nX\nc\n"
    diff = f"--- a/file\n+++ b/file\n{_diff(original, modified)}"
    assert apply_unified_diff(original, diff) == modified


def test_diff_applies_cleanly_never_raises():
    # malformed hunk header should be caught, not propagate
    ok, reason = diff_applies_cleanly("a\nb\n", "@@ garbage @@\n a\n")
    assert ok is False
    assert reason is not None
