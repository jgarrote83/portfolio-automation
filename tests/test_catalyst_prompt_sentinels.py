"""Prompt-content sentinels for the 2026-08-10 catalyst-sleeve-funnel session
(Task F — regime_fit demotion + catalyst_screen ranking contract).

Mirrors the pattern in `tests/test_prompt_hygiene_sentinels.py`: a direct
substring check against the LIVE prompt, so a future edit that silently drops
one of these doctrine sentences fails a test instead of shipping quietly.

Run: PYTHONPATH=src pytest tests/test_catalyst_prompt_sentinels.py
"""
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_PROMPT = pathlib.Path(__file__).parent.parent / "src" / "config" / "project-instructions.md"

# regime_fit is demoted from a hard veto to a scored/contextual input.
_REGIME_DEMOTION_SENTINELS = (
    "regime_fit is no longer a hard veto",
    "a mismatch is a WEAKER thesis, not a disqualified one",
)

# The catalyst_screen ranking contract the model reads but never computes.
_CATALYST_SCREEN_SENTINELS = (
    "catalyst_score",
    "you never compute this score, only read it",
    "catalyst_screen.ledger",
    "catalyst_screen.nominated",
    "source: \"screened\"",
)

# Absent-vs-zero doctrine must be explicit in the prompt, not just the code.
_ABSENT_VS_ZERO_SENTINELS = (
    "ABSENT and drops out of the mean",
    "a no-earnings-date name is never handicapped",
)


def _text() -> str:
    return _PROMPT.read_text(encoding="utf-8")


def test_regime_fit_demotion_documented():
    text = _text()
    for s in _REGIME_DEMOTION_SENTINELS:
        assert s in text, f"missing regime-fit-demotion sentinel: {s!r}"


def test_catalyst_screen_contract_documented():
    text = _text()
    for s in _CATALYST_SCREEN_SENTINELS:
        assert s in text, f"missing catalyst_screen sentinel: {s!r}"


def test_absent_vs_zero_doctrine_documented():
    text = _text()
    for s in _ABSENT_VS_ZERO_SENTINELS:
        assert s in text, f"missing absent-vs-zero sentinel: {s!r}"


def test_flex_ticker_and_sleeve_caps_still_present():
    # The hard rules Task F explicitly says must survive this rewrite.
    text = _text()
    assert "10" in text and "flex tickers" in text
    assert "25%" in text
