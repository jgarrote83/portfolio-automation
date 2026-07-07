# Portfolio Automation System

Azure-native automated portfolio analysis and **paper**-trade execution pipeline. Single-user personal system. **NOT for live trading** — live execution always requires human approval; only paper auto-execute is enabled.

Each weekday a timer collects market/macro/alt data, a deterministic layer pre-computes the regime call and a reference allocation, Claude (via Azure AI Foundry) writes the daily analysis and trade recommendations, a deterministic validator enforces the risk limits on those trades, and the paper executor submits the survivors to Alpaca.

## Where to read next

- **[`CLAUDE.md`](CLAUDE.md)** — the living architecture doc: resource names, data flow (Phase 1 collect/analyze + Phase 2 execute), Table Storage schemas, the snapshot analytics blocks (`reference_weights`, `divergences`, `transition_watch`, override protocol + enforcement), the Rules, and the hard-won **Deployment lessons**. Start here for how the system actually works.
- **[`FOLLOWUPS.md`](FOLLOWUPS.md)** — open work, backlog, and dated incident records (where we left off).
- **[`docs/specs/growth_strategy_spec_v1.md`](docs/specs/growth_strategy_spec_v1.md)** — the north-star strategy spec (the regime-concentration machine all automation is downstream of). Read this to understand *why*, not just *how*.
- Other companion specs (data sources, storage, analyzer pipeline, Phase C, flex engine) are listed at the bottom of `CLAUDE.md`.

## Layout (top level)

| Path | What |
|---|---|
| `src/` | `func-pfauto` — all Azure Functions (`collector`, `analyzer`, `executor`, `flex`, `seeder`) + `shared/` libs + `config/`. Entry point: `src/function_app.py`. |
| `src/config/` | Prompt (`project-instructions.md`) + all tunable config (`risk-limits.json`, `macro-series.json`, `divergence-config.json`, `fomc-stance.json`, …). **Config lives under `src/`, not at repo root.** |
| `web/` | `swa-pfauto` Static Web App — report viewer + per-trade approval (`/today`, `/performance`, `/history`, `/portfolio`) + managed API in `web/api/`. |
| `infra/` | Bicep IaC (`main.bicep` + `modules/`). Prod has no manual portal config. |
| `tests/` | pytest suite. |
| `scripts/` | dev helpers (`docx_to_md.py`, `run_fmp_smoke.py`). |
| `docs/specs/` | strategy + companion specs (`.md` authoritative; paired `.docx`). |

## Tech stack

Python 3.11 · Azure Functions (Flex Consumption, Linux) · Bicep · GitHub Actions (OIDC, no secrets in GitHub) · ruff + pytest · Blob + Table Storage · Key Vault (RBAC, Managed Identity only) · Application Insights.

## Development

```bash
# From the repo root. Tests and lint mirror CI (.github/workflows/ci.yml).
pip install -r src/requirements.txt ruff pytest
PYTHONPATH=src pytest -q
ruff check .
```

Deployment is via GitHub Actions:
- `deploy-code.yml` → `func-pfauto` (triggers on `src/**`; workflow-only edits need a manual `gh workflow run`).
- `deploy-web.yml` → `swa-pfauto`.
- `deploy-infra.yml` → Bicep.
- `ci.yml` → lint + tests on every push/PR (no deploy).

> ⚠️ Azure resources live in the **EasyGridsProduction** subscription under the **jgarrote@easygrids.com** identity (a different Entra tenant from a Quirch-default `az` session). See `CLAUDE.md` → Deployment lessons for the login sequence — the wrong identity fails even read-only blob access with an issuer-mismatch error.

## Status

Phase 1 (collect → analyze → report) and Phase 2 (paper execution, auto + manual approval) are live. The reference-execution enforcement stack (override protocol, band enforcement, Tier-1 trade validator) and the Phase C / Phase 5 performance-and-outcome feedback loops are merged. Remaining work is tracked in `FOLLOWUPS.md`.
