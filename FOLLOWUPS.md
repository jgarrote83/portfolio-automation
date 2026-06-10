# Follow-up items

Running backlog of known-open work. Newest context at top. When you pick an
item up, move it to **Done** with the date + commit so the history is visible.

**Last session: 2026-06-09** — diagnosed and restored the auto analyzer/execution
pipeline (dead since the Flex Consumption migration ~2026-06-01). Root causes and
the permanent fixes are in the commit messages (`36bd465`, `6171eeb`) and in
CLAUDE.md → "Deployment lessons". Live Azure changes applied out-of-band that
session: `az provider register Microsoft.EventGrid`; EG webhook `functionName`
→ `Host.Functions.analyzer`; app settings `TZ` / `AUTO_EXECUTE_ENABLED` /
`AzureFunctionsJobHost__functionTimeout` (now also in `functionapp.bicep`); SWA
`STORAGE_CONNECTION_STRING` / `FUNC_MASTER_KEY` restored after an infra deploy
wiped them.

---

## Open

### 1. Verify the first fully-unattended weekday run (HIGH — time-sensitive)
The chain (collector → EventGrid → analyzer → `daily-trades` → `auto_executor`
→ Alpaca) is fixed and verified **through report/trades generation**, but
`auto_executor` (09:35 ET timer) was **never live-tested** — the market was
closed when the fix landed. On the next weekday, confirm without intervening:
- `daily-reports/{date}.md` + `daily-trades/{date}.json` appear ~09:00–09:10 ET
- `daily-executions/{date}.json` appears after 09:35 ET with **submitted** Alpaca
  paper orders (this is the first real auto-execute)

Telemetry now flows to App Insights (`appi-pfauto-prod`, `cloud_RoleName ==
'func-pfauto'`) — watch `traces`/`exceptions` there if anything is missing.

### 2. SWA secret app settings are wiped by every infra deploy (HIGH)
`web/api` reads blob storage via `STORAGE_CONNECTION_STRING` and calls the
func-pfauto executor via `FUNC_MASTER_KEY`. These (plus `AAD_CLIENT_ID` /
`AAD_CLIENT_SECRET`) are **post-deploy-only** secrets — see the note in
`infra/modules/staticwebapp.bicep`. Because `az deployment group create` replaces
the SWA's app-setting set wholesale, any `infra/**` deploy wipes them and the
`/today` page breaks (`/api/dates → 500`, table stuck on "Loading…").
- Observed + restored live on 2026-06-09.
- **Fix:** move these to **Key Vault references** in `staticwebapp.bicep` (mirror
  how `functionapp.bicep` handles secrets), so deploys set rather than wipe them.
  Requires the secret values to live in `kv-pfauto-prod` first.
- **Until then:** after any infra deploy, re-apply:
  `az staticwebapp appsettings set --name swa-pfauto -g rg-portfolio-automation-prod --setting-names STORAGE_CONNECTION_STRING=<conn> FUNC_MASTER_KEY=<masterkey>`

### 3. Entra ID auth is currently OFF on the SWA (MEDIUM — security)
`web/staticwebapp.config.json` has `allowedRoles: ["anonymous"]` on `/api/*` and
`/*`, so the site is **publicly reachable** — this deviates from the documented
"Entra ID Easy Auth, owner role" design. (Pre-existing in the committed config,
not changed this session.) Note: `AAD_CLIENT_ID/SECRET` were wiped by the infra
deploy and the client **secret is not recoverable**.
- **If re-enabling:** mint a new client secret on the app registration, restore
  `AAD_CLIENT_ID` / `AAD_CLIENT_SECRET` (via KV ref per #2), and set
  `allowedRoles` back to the authenticated/owner role in `staticwebapp.config.json`.

### 4. Migrate the SWA API off the storage connection string (LOW)
CLAUDE.md mandates "Managed Identity only — no connection strings." `web/api`
still uses `STORAGE_CONNECTION_STRING` (account key). Switching it to
`DefaultAzureCredential` + the already-present `STORAGE_ACCOUNT_NAME` would align
with the rule and **eliminate the secret entirely** — which also resolves the
storage half of #2.

---

## Done
- _(none yet — move completed items here with date + commit)_
