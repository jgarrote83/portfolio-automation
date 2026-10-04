"""Offline backtest package for the ORB program (docs/specs/ORB_Engine_v1.0.md, Phases 1-2).

Runs locally and is NEVER deployed (`deploy-code.yml` packages `src/` only). It may import
read-only from `src/` (e.g. `shared.quadrants.CORE_ROSTER`) when `PYTHONPATH=src` is set, and
`src/` must never import from here.

Repo rule 6: backtests get market data ONLY through `backtest.data.get_bars`; tune only on
2024-2025; the 2026 holdout is never touched without Jorge's approval.
"""
