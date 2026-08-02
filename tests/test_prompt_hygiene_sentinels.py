"""Prompt-content sentinels for the 2026-08-01 session's hygiene batch (Task D
cash-ceiling deployment doctrine + Task E1/E2/E3 load-bearing prompt sentences).

Mirrors the `_FLEX_SCHEMA_SENTINELS` / `_OVERRIDE_SCHEMA_SENTINELS` pattern in
`analyzer/handler.py` (`tests/test_flex_prompt_schema.py`): a simple, direct
substring check against the LIVE prompt file, so a future edit that silently
drops one of these doctrine sentences fails a test instead of shipping quietly.
These are prose additions for the LLM, not something deterministic code parses
at runtime, so — unlike the flex/override schema gates — there is no
`assert_*_prompt_schema` raising function backing them; the test IS the gate.

Run: PYTHONPATH=src pytest tests/test_prompt_hygiene_sentinels.py
"""
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_PROMPT = pathlib.Path(__file__).parent.parent / "src" / "config" / "project-instructions.md"

# Task D (decision D-D1): cash-sleeve-over-ceiling discretionary-deployment doctrine.
_CASH_CEILING_DOCTRINE_SENTINELS = (
    "decision D-D1",
    "in-band underweight sleeves",
    "buy-eligible",
)

# Task E1: floor-in-shares arithmetic required before a "trim to floor" plan.
_FLOOR_ARITHMETIC_SENTINELS = (
    "Compute the floor in SHARES before proposing",
    "ceil(execution_config.sleeve_floor_pct_of_core",
)

# Task E2: post-trade literal-cash formula must include same-day sell proceeds.
_POST_TRADE_CASH_SENTINELS = (
    "Task E2",
    "post_trade_literal_cash",
    "pre_trade_literal_cash",
)

# Task E3: prior-session flex nomination adjudication (C4 extension).
_FLEX_ADJUDICATION_SENTINELS = (
    "Task E3",
    "Prior-session nomination adjudication is mandatory",
    "flex_state.entries",
)

# Task E4: trade de-risk/re-risk class vs override direction vocabulary.
_TRADE_DIRECTION_VOCAB_SENTINELS = (
    "Task E4",
    "SEPARATE axis from an OVERRIDE's",
)

# Task E5: symmetric catalyst-date discipline, no exceptions.
_CATALYST_DATE_SENTINELS = (
    "Task E5",
    "No exceptions, either",
)

# Task E6: general no-visible-deliberation-chatter + symmetric oil-overlay wording.
_NO_CHATTER_SENTINELS = (
    "Task E6",
    "no section of the report, override-related or not, may show visible",
)
_OIL_OVERLAY_SENTINELS = (
    "counter-signal",
    "never a cause of it",
)


def _text() -> str:
    return _PROMPT.read_text(encoding="utf-8")


def test_cash_ceiling_deployment_doctrine_present():
    text = _text()
    for s in _CASH_CEILING_DOCTRINE_SENTINELS:
        assert s in text, f"missing cash-ceiling doctrine sentinel: {s!r}"


def test_floor_arithmetic_sentence_present():
    text = _text()
    for s in _FLOOR_ARITHMETIC_SENTINELS:
        assert s in text, f"missing floor-arithmetic sentinel: {s!r}"


def test_post_trade_cash_formula_present():
    text = _text()
    for s in _POST_TRADE_CASH_SENTINELS:
        assert s in text, f"missing post-trade-cash sentinel: {s!r}"


def test_flex_nomination_adjudication_present():
    text = _text()
    for s in _FLEX_ADJUDICATION_SENTINELS:
        assert s in text, f"missing flex-adjudication sentinel: {s!r}"


def test_trade_direction_vocabulary_present():
    text = _text()
    for s in _TRADE_DIRECTION_VOCAB_SENTINELS:
        assert s in text, f"missing trade-direction-vocabulary sentinel: {s!r}"


def test_catalyst_date_symmetry_present():
    text = _text()
    for s in _CATALYST_DATE_SENTINELS:
        assert s in text, f"missing catalyst-date-symmetry sentinel: {s!r}"


def test_no_visible_deliberation_chatter_present():
    text = _text()
    for s in _NO_CHATTER_SENTINELS:
        assert s in text, f"missing no-chatter sentinel: {s!r}"


def test_oil_overlay_symmetric_wording_present():
    text = _text()
    for s in _OIL_OVERLAY_SENTINELS:
        assert s in text, f"missing oil-overlay sentinel: {s!r}"
