"""ORB (Opening Range Breakout) strategy modules -- PURE: no I/O, no timers, no third-party imports.

Spec: docs/specs/ORB_Engine_v1.0.md. The offline backtest (`backtest/`) and, from Phase 4, the live
engine both import these modules, so the logic that is tested is the logic that trades.

Nothing in this package is registered in `function_app.py`; until Phase 4 it is inert code that
ships in the function package but is never imported at startup.
"""
