"""Intraday catalyst Flex engine.

A separate strategy from the Core quadrant book — see
`docs/specs/Flex_Catalyst_Engine_v1.0.md` and the Separation Contract in
`project-instructions.md`. **The only input shared with Core is
`separation.flex_separation_set`** — book-collision prevention, not a regime
opinion. Regime was removed from the sleeve entirely on 2026-09-12 (B1/R1);
cadence, data, sizing, exits, and the order path are all distinct.

Pure, unit-tested modules (`indicators`, `separation`, `entry`, `exit_state`,
`reconcile`) hold all logic; `handler.run_flex_intraday` is thin orchestration.
"""
