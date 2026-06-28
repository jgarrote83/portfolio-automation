"""Prompt↔code schema gate — the live system prompt must carry the flex
nomination contract the engine depends on, or the analyzer refuses to run.

Run: PYTHONPATH=src pytest tests/test_flex_prompt_schema.py
"""
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from analyzer.handler import assert_flex_prompt_schema  # noqa: E402

_PROMPT = pathlib.Path(__file__).parent.parent / "src" / "config" / "project-instructions.md"


def test_live_prompt_has_flex_schema_markers():
    text = _PROMPT.read_text(encoding="utf-8")
    assert "FLEX_SCHEMA_V1" in text
    assert "flex_nominations" in text


def test_assertion_passes_on_live_prompt():
    assert_flex_prompt_schema(_PROMPT.read_text(encoding="utf-8"))  # must not raise


def test_assertion_raises_when_markers_missing():
    with pytest.raises(RuntimeError):
        assert_flex_prompt_schema("a prompt that forgot the flex contract entirely")
