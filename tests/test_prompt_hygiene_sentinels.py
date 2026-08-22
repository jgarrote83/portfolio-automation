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
    "cash_above_band",
)
# M2 (review round, session 2026-08-01): the ORIGINAL trigger wording conditioned
# on `cash_sleeve_target_pct`'s "actual value" sitting above the ceiling — but that
# field is ceiling-clamped BY CONSTRUCTION (`max(cash_floor, min(cash_ceiling,
# cur_sleeve))`), so the condition could never fire. This distinctive phrase must
# never reappear.
_CASH_CEILING_UNSATISFIABLE_TRIGGER_PHRASE = "target_pct`'s actual value"

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

# Task A3 (2026-08-14 audit, decision D-1): plan_vs_submitted doctrine + the
# anti-fabrication rule found by the A0 probe (the 08-12->08-13 false
# "executed as planned" reconciliation).
_PLAN_VS_SUBMITTED_SENTINELS = (
    "plan_vs_submitted",
    "missing_from_submission",
    "qty_mismatch",
)
_ANTI_FABRICATION_SENTINELS = (
    'may NOT write that a prior proposal "executed as planned"',
    "never infer execution from position deltas alone",
    "Do not invent a mapping between filled orders and proposals",
)

# Task B3 (2026-08-14 audit): one governing oil reading, cited with source/as-of,
# closing the 08-12 seam where the same 20-day oil trend printed two different
# numbers (17.8% vs 6.2%) in two sections of the same report.
_OIL_GOVERNING_SENTINELS = (
    "oil_20d_pct_governing",
    "One number, cited once",
    "Never cite a bare percentage with no source/as-of",
)

# Task C (2026-08-14 audit): transition_watch confirm/release hysteresis
# doctrine, closing the 2026-08-12 VDE whipsaw seam.
_TRANSITION_WATCH_HYSTERESIS_SENTINELS = (
    "Confirm/release hysteresis",
    "status: \"pending\"",
    "release_pending: true",
    "Always cite `staged_fraction`, never `target_fraction`",
)

# Task D7 (2026-08-14 audit, decisions D-4/D-5): thematic conviction overlay —
# replaces the old "note the linkage in the rebalancing rationale" instruction
# that produced four sessions of decorative theme narration with zero size.
_THEMATIC_CONVICTION_SENTINELS = (
    "thematic_conviction[]",
    "min_evidence_items",
    "Prose from a prior report does not count as evidence",
    "Anti-inflation guard",
    "Two independent conviction mechanisms",
)
_THEMATIC_OLD_INSTRUCTION_RETIRED_PHRASE = "note the linkage in the rebalancing rationale"

# Task B/E (2026-08-14 flex-conviction-path cycle): the SECOND, parallel flex-
# nomination path — no dated catalyst required, edge-over-base-rate sizing,
# invalidation-as-stop, no time stop. Closes the EUAD 4-of-5-sessions drought
# (a real thesis with no dated catalyst had no nomination path at all).
_FLEX_CONVICTION_PATH_SENTINELS = (
    'path: "conviction"',
    "edge = p_up",
    "base_rate_up",
    "No time stop on this path",
)
# Task C1/C2 (2026-08-14 flex-conviction-path cycle): flex_eligibility citation
# doctrine, closing the EUAD false-prohibition failure mode — a report must
# distinguish "the system could not nominate this" (cited, deterministic) from
# "I am choosing not to nominate this" (a live judgment call), and must
# adjudicate regional_rotation's top-named candidate every session.
_FLEX_ELIGIBILITY_SENTINELS = (
    "flex_eligibility.candidates",
    "the system could not",
    "I am choosing not to nominate this",
    "top-named rotation candidate",
)

# Task D-priority-3/4 (2026-08-14 flex-conviction-path cycle): applicable-set
# split (missing_data vs not_applicable) + the double-clause rankability
# guard, so "components_missing is empty" alone is never read as full
# coverage for a fund with a structurally narrow applicable set.
_APPLICABLE_SET_SENTINELS = (
    "components_not_applicable",
    "components_applicable >= 4",
    "components_available >= 4",
    "is_fund",
)

# PR #41 review round — M-A "also fix" (surface the clamped-but-nonzero cash-
# floor case explicitly, not just via a silent binding echo).
_CASH_FLOOR_VISIBILITY_SENTINELS = (
    'binding == "cash_floor"',
    "cash_floor_breach",
    "FOLLOWUPS #75",
)

# PR #41 review round — S-2 (carried from PR #40): flex_state.as_of staleness
# doctrine, since the collector now correctly runs pre-market and as_of is
# normally the PRIOR trading day.
_FLEX_STATE_AS_OF_STALENESS_SENTINELS = (
    "flex_state.as_of` staleness",
    "must be narrated in the past",
    "say the engine state is stale",
)

# 2026-08-21 quadrant-reachability audit, Task A: diagonal (joint) projection
# doctrine.
_JOINT_PROJECTION_SENTINELS = (
    "Diagonal (joint) projection",
    "composes the DIAGONAL quadrant",
    "both a growth and",
)

# 2026-08-21 quadrant-reachability audit, Task F (decision D-6): inert-lean
# diagnostic doctrine — the projected lean may be 100% unbuyable under the
# current deployment gate; the report must say so and never present the
# resulting gap as a closable shortfall.
_INERT_LEAN_SENTINELS = (
    "Inert-lean diagnostic",
    "transition_watch.inert == true",
    "MUST NOT",
    "not a trade you failed to make",
)

# 2026-08-21 measurement-integrity cycle: equity reconciliation + external
# cash-flow disclosure doctrine.
_MEASUREMENT_INTEGRITY_SENTINELS = (
    "Measurement integrity",
    "paper_account.reconciliation",
    "treat every",
    "position-derived figure",
    "external_flows",
    "not comparable",
    "Do not present a spanning-window alpha figure",
)

# Quadrant baskets are never an achievable alternative to the book; the
# quadrant_performance/quadrant_series measurement-integrity caveat.
_QUADRANT_BASKET_NOT_ACHIEVABLE_SENTINELS = (
    "Never an achievable alternative",
    "not a tradable strategy",
    "potentially biased upward by the same three defects",
)


def _text() -> str:
    return _PROMPT.read_text(encoding="utf-8")


def test_cash_ceiling_deployment_doctrine_present():
    text = _text()
    for s in _CASH_CEILING_DOCTRINE_SENTINELS:
        assert s in text, f"missing cash-ceiling doctrine sentinel: {s!r}"


def test_cash_ceiling_trigger_is_not_the_unsatisfiable_target_pct_condition():
    """M2 (review round, session 2026-08-01): the doctrine must key its trigger
    on the deterministic `cash_above_band` binding flag (or the current cash
    sleeve read directly), never on `cash_sleeve_target_pct` exceeding the
    ceiling — that field is ceiling-clamped by construction and can never
    express the breach, which would make the D-D1 permission unreachable."""
    text = _text()
    assert _CASH_CEILING_UNSATISFIABLE_TRIGGER_PHRASE not in text


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


def test_plan_vs_submitted_doctrine_present():
    text = _text()
    for s in _PLAN_VS_SUBMITTED_SENTINELS:
        assert s in text, f"missing plan_vs_submitted sentinel: {s!r}"


def test_anti_fabrication_reconciliation_rule_present():
    """Pins the doctrine that closes the 08-12->08-13 false 'executed as
    planned' reconciliation so a future prompt edit can't silently drop it."""
    text = _text()
    for s in _ANTI_FABRICATION_SENTINELS:
        assert s in text, f"missing anti-fabrication sentinel: {s!r}"


def test_oil_governing_value_doctrine_present():
    text = _text()
    for s in _OIL_GOVERNING_SENTINELS:
        assert s in text, f"missing oil-governing sentinel: {s!r}"


def test_transition_watch_hysteresis_doctrine_present():
    text = _text()
    for s in _TRANSITION_WATCH_HYSTERESIS_SENTINELS:
        assert s in text, f"missing transition_watch hysteresis sentinel: {s!r}"


def test_thematic_conviction_doctrine_present():
    text = _text()
    for s in _THEMATIC_CONVICTION_SENTINELS:
        assert s in text, f"missing thematic_conviction sentinel: {s!r}"


def test_old_decorative_theme_narration_instruction_retired():
    """Pins the retirement of the exact instruction that produced four
    sessions of theme narration with zero sized expression (the VDE
    four-session floor-pin incident)."""
    text = _text()
    assert _THEMATIC_OLD_INSTRUCTION_RETIRED_PHRASE not in text


def test_flex_conviction_path_doctrine_present():
    text = _text()
    for s in _FLEX_CONVICTION_PATH_SENTINELS:
        assert s in text, f"missing flex-conviction-path sentinel: {s!r}"


def test_flex_eligibility_doctrine_present():
    text = _text()
    for s in _FLEX_ELIGIBILITY_SENTINELS:
        assert s in text, f"missing flex_eligibility sentinel: {s!r}"


def test_applicable_set_doctrine_present():
    text = _text()
    for s in _APPLICABLE_SET_SENTINELS:
        assert s in text, f"missing applicable-set sentinel: {s!r}"


def test_cash_floor_visibility_doctrine_present():
    text = _text()
    for s in _CASH_FLOOR_VISIBILITY_SENTINELS:
        assert s in text, f"missing cash-floor-visibility sentinel: {s!r}"


def test_flex_state_as_of_staleness_doctrine_present():
    text = _text()
    for s in _FLEX_STATE_AS_OF_STALENESS_SENTINELS:
        assert s in text, f"missing flex_state.as_of staleness sentinel: {s!r}"


def test_joint_projection_doctrine_present():
    text = _text()
    for s in _JOINT_PROJECTION_SENTINELS:
        assert s in text, f"missing joint-projection sentinel: {s!r}"


def test_inert_lean_doctrine_present():
    text = _text()
    for s in _INERT_LEAN_SENTINELS:
        assert s in text, f"missing inert-lean sentinel: {s!r}"


def test_measurement_integrity_doctrine_present():
    text = _text()
    for s in _MEASUREMENT_INTEGRITY_SENTINELS:
        assert s in text, f"missing measurement-integrity sentinel: {s!r}"


def test_quadrant_basket_not_achievable_doctrine_present():
    text = _text()
    for s in _QUADRANT_BASKET_NOT_ACHIEVABLE_SENTINELS:
        assert s in text, f"missing quadrant-basket-not-achievable sentinel: {s!r}"
