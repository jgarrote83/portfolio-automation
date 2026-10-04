"""Prompt↔code schema gate — the live system prompt must carry the Phase-4 override-protocol
markers the analyzer depends on, or the analyzer refuses to run.

Run: PYTHONPATH=src pytest tests/test_override_prompt_schema.py
"""
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from analyzer.handler import assert_override_prompt_schema  # noqa: E402

_PROMPT = pathlib.Path(__file__).parent.parent / "src" / "config" / "project-instructions.md"


def test_live_prompt_has_override_schema_markers():
    text = _PROMPT.read_text(encoding="utf-8")
    assert "OVERRIDE_SCHEMA_V1_1" in text   # Finding 2: per-sleeve residual-cap schema
    assert "reference_weights" in text
    assert "overrides" in text


def test_override_assertion_passes_on_live_prompt():
    assert_override_prompt_schema(_PROMPT.read_text(encoding="utf-8"))  # must not raise


def test_override_assertion_raises_when_markers_missing():
    with pytest.raises(RuntimeError):
        assert_override_prompt_schema("a prompt that ignores the reference entirely")
