# ORB Phase 0 — Flex shutdown runbook

**Audience:** Jorge, running these by hand. **Scope:** stop the Flex catalyst engine from opening new positions, let it close what it holds, confirm everything is flat, archive history. This runbook changes **no code**. Part B (the removal PR) is issued only after step 4 reports FLAT.
**Spec:** `docs/specs/ORB_Engine_v1.0.md` (Phase 0). **Inventory:** `docs/specs/ORB_Phase0_Retirement_Inventory.md`.

> **Do not disable Flex while it holds positions.** If `FLEX_ENABLED` is set `false` (or the function is removed) with positions open, the engine stops running entirely: no 2-day time stop, no reconcile, no stop repair, no orphan sweep. Only the bracket legs resting at Alpaca would remain, and nothing would clean up after them. The order is always: **trip → wait until flat → only then remove the setting** (steps 2, 4, 5).

**Azure scope (standing rule):** everything here happens inside the resource group `rg-portfolio-automation-prod` (subscription EasyGridsProduction). Every `az` command names the subscription (`--subscription $sub`); resource-scoped commands also pass `-g $rg`; data-plane commands (`az storage …`, `az keyvault secret show`) use only the three names whose resource group Step 1 verifies. Anything outside that group needs Jorge's explicit approval first.

Commands are PowerShell 7+ (`ConvertFrom-Json -AsHashtable` needs it). Nothing here prints a secret. Every command that writes is marked **WRITES**; everything else is read-only.

## How the pieces behave (verified in code — file:line)

- The kill-switch state lives at blob **`flex-ledger/kill-switch.json`** (`src/flex/trades.py:32,34`). The engine **reads and fully rewrites it on every in-hours tick** (`src/flex/handler.py:179-185`); the timer is `0 */15 * * * 1-5` (`src/function_app.py:95`). Closed-market ticks return before the kill-switch step (`handler.py:95-105`), so they neither read nor write it.
- A state with `tripped: true` and **no truthy `cleared_at`** is carried forward and cannot clear itself: `src/flex/killswitch.py:151-158` runs *before* both the drawdown and hit-rate checks, so no metric (even a perfect one, even zero closed trades) releases it. Your `tripped_at` and `trip_reason` are preserved. Proven for the generic case by `tests/test_flex_news_momentum_b1.py:496-511` (no test uses a custom reason string; the code path is reason-agnostic).
- A trip suppresses **new entries only**. Exits are managed in STEP 4, *before* the kill-switch check (`handler.py:145-165` then `170-194`), so held positions keep their time stop (2 trading days, `exit_state.py`), stop repair and orphan sweep. The two entry loops are skipped at `handler.py:199` and `:226`. The bracket take-profit/stop legs rest at the broker and keep working regardless.
- The time stop counts **weekdays only, entry day = 0, no holiday calendar** (`src/flex/exit_state.py:36-48`): a position opened Monday is cut at market on Wednesday's first in-hours tick that has bars. Over a market holiday it can take one day longer in wall-clock terms.
- The DayTrade Lab has **no kill switch**; it is gated only by `DAYTRADE_ENABLED` (`src/function_app.py:159`), which is `false` in Bicep (`infra/modules/functionapp.bicep:122`). While disabled it does not reconcile or flatten, so step 4 also verifies it left nothing behind.

### Three caveats you must know about a hand-written trip

1. **Do not set `cleared_at`.** Omit it or leave it null/empty. Any truthy value releases the trip.
2. **A silent read failure erases the trip.** `read_json_blob` swallows *all* exceptions (`src/shared/storage.py:233-244`), so a transient storage/auth error while the engine reads the blob yields `{}`, which evaluates as "untripped" and is then **written back over your trip** (`handler.py:185`). Unlikely, but real — hence the re-checks in step 3. (Fixing this is out of scope for Phase 0.)
3. **The engine drops unknown keys on its first rewrite.** Notes or `cleared_by` you add are discarded on the next in-hours tick. Keep notes elsewhere. Also, `FLEX_KILL_SWITCH_ENABLED=false` would bypass the trip and erase it; it is **not** set in Bicep today (code default true) — confirm it is not set live (step 2b).

---

## Step 1 — Sign in

CLAUDE.md "Deployment lessons": the portfolio resources live in the **EasyGridsProduction** subscription under **jgarrote@easygrids.com**, a different Entra tenant from a default Quirch session. With the wrong identity even `--auth-mode login` blob reads fail with `InvalidAuthenticationInfo: Issuer validation failed`.

```powershell
$sub  = "EasyGridsProduction"
$rg   = "rg-portfolio-automation-prod"
$acct = "stpfautoprod"

az login --use-device-code          # sign in as jgarrote@easygrids.com
az account set --subscription $sub
az account show --query "{sub:name, user:user.name}" -o json
```
**Expect:** `{"sub": "EasyGridsProduction", "user": "jgarrote@easygrids.com"}`. Stop if either differs.

**Ownership check — confirm the three resources you are about to touch live in the allowed resource group** (read-only; `az resource list` filters by name, so the same command works for all three):
```powershell
foreach ($n in "stpfautoprod", "kv-pfauto-prod", "func-pfauto") {
  "{0,-16} -> {1}" -f $n, ((az resource list --subscription $sub --name $n --query "[].resourceGroup" -o tsv) -join ",")
}
```
**Expect exactly three lines, each ending in `rg-portfolio-automation-prod`:**
```
stpfautoprod     -> rg-portfolio-automation-prod
kv-pfauto-prod   -> rg-portfolio-automation-prod
func-pfauto      -> rg-portfolio-automation-prod
```
**Stop** if any line shows a different group, is empty (name not found in this subscription), or lists more than one group. Do not run anything below until all three match. (For the storage account alone, `az storage account show -n stpfautoprod --subscription $sub --query resourceGroup -o tsv` should also print `rg-portfolio-automation-prod`.)

## Step 2 — Stop new Flex entries (manual sticky trip)

**Do this after the market has closed (after 16:00 ET) or at a weekend.** The engine does a read-modify-write of this blob on in-hours ticks; writing mid-session can race a tick that read the old (untripped) state and overwrite your change. Outside hours no tick touches the blob, so the write is safe, and the first in-hours tick afterwards carries it forward.

### 2a. Confirm the switches' live values (read-only)
```powershell
az functionapp config appsettings list -n func-pfauto -g $rg --subscription $sub `
  --query "[?name=='FLEX_ENABLED' || name=='DAYTRADE_ENABLED' || name=='FLEX_KILL_SWITCH_ENABLED' || name=='FLEX_SLEEVE_CAP_PCT'].{name:name,value:value}" -o table
```
**Expect:** `FLEX_ENABLED = true`, `DAYTRADE_ENABLED = false`, and **no** `FLEX_KILL_SWITCH_ENABLED` row. If `FLEX_KILL_SWITCH_ENABLED` is `false`, the trip below will be ignored and erased — **stop and tell me**. (These are not secrets; the command lists only those four names.)

### 2b. Back up the current blob (read-only)
```powershell
$backup = "kill-switch.backup.$(Get-Date -Format 'yyyyMMdd-HHmmss').json"
az storage blob download --account-name $acct --subscription $sub --auth-mode login `
  -c flex-ledger -n kill-switch.json -f $backup --no-progress 2>&1 | Out-String
Get-Content $backup -ErrorAction SilentlyContinue
```
**Expect either:** the existing JSON printed (keys such as `enabled`, `tripped:false`, `hit_rate`, `thresholds`, `drawdown_pct`, `note`), **or** a `BlobNotFound` error on first use — fine; step 2c starts from `{}`. Keep the backup file; it is the rollback source.

### 2c. Write the trip, preserving the existing fields — **WRITES**
```powershell
$state = if (Test-Path $backup) { Get-Content $backup -Raw | ConvertFrom-Json -AsHashtable } else { @{} }
$state["tripped"]     = $true
$state["trip_reason"] = "manual_retirement_orb"
$state["tripped_at"]  = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$state.Remove("cleared_at")      # must be absent/empty, or the trip is released
$state.Remove("cleared_by")
$state | ConvertTo-Json -Depth 10 | Set-Content kill-switch.new.json -Encoding utf8
Get-Content kill-switch.new.json
```
**Expect:** the printed JSON shows `"tripped": true`, `"trip_reason": "manual_retirement_orb"`, a `tripped_at` timestamp, **no** `cleared_at`, and every field that was in the backup still present. Read it before uploading. Then:
```powershell
az storage blob upload --account-name $acct --subscription $sub --auth-mode login `
  -c flex-ledger -n kill-switch.json -f kill-switch.new.json --overwrite --no-progress
```
**Expect:** a JSON result with an `etag` and `lastModified`, no error. (Container `flex-ledger` already exists from the engine's own writes.)

## Step 3 — Confirm the trip took effect

The engine only evaluates the switch on **in-hours ticks**, so confirmation happens at the next market open + first tick (the timer fires every 15 minutes; allow up to ~30 minutes after 09:30 ET — the first tick may land before bars exist).

1. **The blob survived the engine's rewrite** (re-run after the first in-hours tick):
   ```powershell
   az storage blob download --account-name $acct --subscription $sub --auth-mode login -c flex-ledger -n kill-switch.json -f kill-switch.check.json --no-progress | Out-Null
   Get-Content kill-switch.check.json
   ```
   **Expect:** `"tripped": true`, `"trip_reason": "manual_retirement_orb"`, `"tripped_at"` preserved, and `"note"` beginning `TRIPPED (carried forward, manual_retirement_orb).` (written by `killswitch.py:151-158`). If it reads `tripped: false`, the trip was erased (caveat 2) — re-do step 2c and check again.
2. **Today's engine state:** blob `flex-state/<YYYY-MM-DD>.json` (ET date) should contain a `kill_switch` key with the same state (`handler.py:944-951`).
   ```powershell
   $d = (Get-Date).ToString("yyyy-MM-dd")   # use the ET trading date
   az storage blob download --account-name $acct --subscription $sub --auth-mode login -c flex-state -n "$d.json" -f flex-state.check.json --no-progress | Out-Null
   Get-Content flex-state.check.json
   ```
3. **Suppression logged:** blob `flex-decisions/<date>.jsonl` — each tick's record has `orders_suppressed` entries `{"reason": "kill_switch:manual_retirement_orb", ...}` (`handler.py:192-194`). Also, `flex-executions/<date>.json` should contain **no** `entry_bracket`/`entry_oto` records after the trip.
4. **Log line (Application Insights → Logs):**
   ```kusto
   traces | where timestamp > ago(1d) | where message has "FLEX KILL SWITCH TRIPPED" | project timestamp, message
   ```
   **Expect** an ERROR-severity line starting `FLEX KILL SWITCH TRIPPED (manual_retirement_orb) -- no new entries.`
5. **Next collector run (09:00 ET):** the snapshot's top-level `flex_kill_switch` echoes `{available: true, tripped: true, ...}` (`src/collector/handler.py:3620-3632`), and the report should raise it under Data Integrity Warning.
6. **Re-check the blob once more the next morning** and after any deployment or host restart (caveat 2).

If steps 2-3 do not show the trip, treat Flex as **still entering** and do not rely on the trip.

## Step 4 — Wait for flat

"Flat" = the kill switch is **tripped** *and* there are no Flex/DayTrade **positions, open orders or ledger rows**. Run the read-only check (it makes GET requests and blob downloads only; it never submits, replaces or cancels anything, and never prints credentials):

```powershell
# Alpaca paper keys: read straight into the environment, never echoed.
$env:ALPACA_API_KEY    = az keyvault secret show --vault-name kv-pfauto-prod --subscription $sub --name AlpacaApiKey    --query value -o tsv
$env:ALPACA_API_SECRET = az keyvault secret show --vault-name kv-pfauto-prod --subscription $sub --name AlpacaApiSecret --query value -o tsv
$env:STORAGE_ACCOUNT_NAME = "stpfautoprod"
$env:PYTHONPATH = "src"
python scripts/orb_phase0_flat_check.py
```
(If your account lacks Key Vault secret read, take the paper key/secret from the Alpaca paper dashboard into the same two variables. Do not paste them into a file or chat.) The script imports the Core universe read-only from `shared.quadrants` (`CORE_ROSTER` = every role's full pool + `LEGACY_EXITS`), which is why `PYTHONPATH=src` is required.

Every run first prints the kill-switch state and the counts, e.g.
```
Flex kill switch: tripped=True cleared_at=None trip_reason=manual_retirement_orb tripped_at=2026-10-05T01:00:00Z
Alpaca positions (all): 17   open orders (incl. legs): 2   Core universe: 46 symbols
flex-ledger rows: 0
daytrade-ledger rows: 0
```
(the counts are illustrative). **Expected verdicts** (exit code 0 / 1 / 2):

| Output | Exit | Meaning / what to do |
| --- | --- | --- |
| `VERDICT: FLAT -- kill switch tripped; no Flex/DayTrade positions, orders or ledger rows remain.` | 0 | Counts as one FLAT reading. Take two (see cadence). |
| `VERDICT: NOT SAFE: Flex kill switch is not tripped (tripped=False, cleared_at=None)` (or `blob absent`, or `cleared_at=<timestamp>`) | 1 | Nothing is open **right now**, but the engine can enter again on its next tick. Go back to Step 2 and trip it. Never accept a FLAT-looking world with an untripped switch. |
| `VERDICT: NOT FLAT` plus lines for each offender | 1 | Wait and re-run. If the switch is also untripped it adds `(also NOT SAFE: the Flex kill switch is not tripped)`. |
| `INCONCLUSIVE: ...` | 2 | A credential was missing or a read failed (for example wrong az identity). **Never treat this as flat.** |

The offender lines you can see, and why each is attributed:
- `ledger [flex|daytrade] SYMBOL qty=… entered=…` — a ledger row still tracks the position.
- `position SYMBOL qty=… (via ledger | attributed order)` — held at Alpaca under a ledger row or under an attributed order.
- `position SYMBOL qty=… (via unattributed non-Core position)` — held at Alpaca, **outside the Core universe**, and no ledger names it. Why this exists: if a Flex tick's broker read fails, `flex/handler.py` STEP 0 falls back to `positions, open_orders = [], []`, reconcile treats every ledger row as closed and **drops it** while the position stays open at Alpaca; its bracket legs carry broker-assigned UUIDs, so nothing else would name it. Treat this as a real Flex orphan until proven otherwise (check the symbol against `flex-ledger/closed-trades.json` and Alpaca's order history).
- `order SYMBOL side type status=… coid=… (reason)` where the reason is one or more of `client_order_id prefix` (`FLEXC-` / legacy `flex-` / `FLEXD-`, case-insensitive), `id in ledger order_ids`, `parent in ledger order_ids` (bracket children carry broker UUIDs, not the prefix — CLAUDE.md, B2 §8.2), `symbol tracked by ledger`, or **`resting stop (Core never rests stops)`** — any open `stop` / `stop_limit` / `trailing_stop` order not otherwise attributed. Core never rests stops: the executor places market/limit orders only (`src/executor/handler.py:_place_one`, with `order_type` limited to `"market" | "limit"` by the prompt schema, `project-instructions.md:2345`; its stop levels are advisory and never sent to Alpaca). Caveat: that limit is by prompt schema and executor design, not enforced in code, so an unattributed stop is *probably* Flex but check its history before cancelling anything by hand.

Cadence: positions held when you tripped close by their bracket legs or by the 2-trading-day time stop (a Monday entry exits Wednesday). Re-run once a day, and run it **twice, at least one in-hours engine tick (15 min) apart, with the trip confirmed in step 3**, before accepting FLAT — a single FLAT taken before the trip was confirmed could miss an entry that raced it. A stale resting order on a symbol with no ledger row is also reported (CLAUDE.md "stale resting order" lesson).

## Step 5 — Before Part B merges or deploys: what Part B does, and the Bicep rule

**Part B *coding* may start before FLAT; *merging and deploying* Part B wait for two FLAT readings with the trip confirmed** (step 3 and step 4), the `LearningProposals` check in step 6 clean, and history archived (step 7). Tell me when you have those and I will cut Part B (a separate prompt, reviewed before anything merges). Part B is purely subtractive: it removes the Flex/DayTrade code, timers, routes and tests, and in its final steps removes the `FLEX_ENABLED` and `DAYTRADE_ENABLED` lines from `infra/modules/functionapp.bicep`.

**`FLEX_ENABLED` is removed through Bicep, never set to `false` by `az` alone.** CLAUDE.md "Deployment lessons": a runtime switch in `functionapp.bicep` must change in **both** places — an `az functionapp config appsettings set` is silently reverted by the next `infra/**` deploy (it replaces the app-setting set wholesale), and editing `infra/**` itself self-triggers an infra deploy (expected here; the deployment is scoped to `rg-portfolio-automation-prod`). Both settings default to `"false"` in code (`src/function_app.py:107,159`), so removing the Bicep lines means off. Do not touch either setting before FLAT.

## Step 6 — Check no Learning Loop proposal targets the files Part B removes (read-only)

Part B deletes `src/config/flex-candidates.json` and the inert Flex keys in `src/config/risk-limits.json`. A pending, approved or applied Learning Loop proposal (or a forced re-review of an applied amendment) that targets them would be orphaned. **Schema, read from the code:** table **`LearningProposals`** (`src/learning/handler.py:40`, rows written at `:188-211`): `PartitionKey` = cycle year-month, `RowKey` = proposal id `AMD-YYYY-MM-DD[-N]`; fields `target_file`, `diff` (capped at 32,000 characters, `:41,198`), `status` (`pending` → `approved`/`rejected`/`stale` → `applied`), `decision`, `pr_url`, `applied_at`, `review_by`. Applied amendments also leave an **`OverrideHistory`** row with `layer = 'amendment'`, `RowKey` `AMD-{proposal_id}`, `sleeve` = the amendment's `target_file`, `review_by` (`web/api/function_app.py:413-428`). A cycle that failed validation stores only a blob (`learning/proposals/{cycle}.json`), never a proposal row.

Needs a data-plane read role (e.g. Storage Table Data Reader) on `stpfautoprod`; all four queries are read-only.

```powershell
# (1) proposals targeting flex-candidates.json — either spelling (the allowlist entry has the wrong path, see inventory finding 8)
az storage entity query --table-name LearningProposals --account-name $acct --subscription $sub --auth-mode login `
  --filter "target_file eq 'src/config/flex-candidates.json' or target_file eq 'config/flex-candidates.json'" `
  --select RowKey target_file status decision applied_at -o json

# (2) every proposal targeting risk-limits.json; list the ones whose diff touches the Flex keys
$r = az storage entity query --table-name LearningProposals --account-name $acct --subscription $sub --auth-mode login `
  --filter "target_file eq 'src/config/risk-limits.json'" `
  --select RowKey status applied_at diff -o json | ConvertFrom-Json
"risk-limits proposals: $($r.items.Count)   nextMarker: $($r.nextMarker)"
$r.items | Where-Object { $_.diff -match 'flex_sleeve_cap_pct|single_name_cap_pct|"conviction"|price_quarantine|flex' } |
  Select-Object RowKey, status, applied_at
$r.items | Where-Object { $_.diff.Length -ge 32000 } | Select-Object RowKey, status   # truncated diffs: cannot be ruled out

# (3) applied amendments and what they target (forced re-review rows)
az storage entity query --table-name OverrideHistory --account-name $acct --subscription $sub --auth-mode login `
  --filter "layer eq 'amendment'" --select RowKey sleeve review_by -o table
```
**Expected, if clear:** query (1) prints `{"items": [], "nextMarker": null}`; query (2) prints a count (possibly 0), a `nextMarker` of nothing, and **no rows** from either `Where-Object` (no Flex-key match, no truncated diff); query (3) shows no row whose `sleeve` is `src/config/flex-candidates.json`, `config/flex-candidates.json`, or `src/config/risk-limits.json` with a Flex-key amendment. A table or row that does not exist yet (`TableNotFound`, or an empty result because the Learning Loop is still at `LEARNING_PHASE` 1) also counts as clear.

**If anything matches, or a `nextMarker` is non-empty (page with `--marker`), stop and tell me** — Part B's `risk-limits.json` key deletion (U6) and the Learning allowlist edit (B5) wait on the outcome. Do not edit or reject the proposals yourself.

## Step 7 — Archive (keep; do not delete)

None of these is defined in Bicep (`infra/modules/storage.bicep:4-10` lists only the daily blobs and `deployment`); they are created at runtime in `stpfautoprod`. No lifecycle/retention policy appears in `infra/modules/*.bicep` (the only soft-delete setting is the Key Vault's) — **I did not verify the live storage account for a portal-set lifecycle rule; check there is none that expires these containers.**

| Keep (never delete) | Contents |
| --- | --- |
| `flex-ledger` | `ledger.json`, `closed-trades.json` (the sleeve's realized-performance record), `equity-series.json`, `kill-switch.json` |
| `flex-state`, `flex-decisions`, `flex-executions` | per-day engine state, decision log, executions |
| `daytrade-ledger`, `daytrade-nominations`, `daytrade-state`, `daytrade-log`, `daytrade-grades` | DayTrade Lab record |
| Tables `TradeHistory` (rows `layer = 'flex'`), `ThematicHistory` (rows `path = 'flex_conviction'`), `FlexConvictionState` | `TradeHistory`/`ThematicHistory` are **shared with Core — never drop the tables** |

Optional local copy (read-only on Azure; **writes only to your disk**):
```powershell
$stamp = Get-Date -Format yyyyMMdd
foreach ($c in "flex-ledger","flex-state","flex-decisions","flex-executions",
               "daytrade-ledger","daytrade-nominations","daytrade-state","daytrade-log","daytrade-grades") {
  az storage blob download-batch --account-name $acct --subscription $sub --auth-mode login -s $c -d "archive-$stamp/$c" --no-progress 2>&1 | Out-Null
}
Get-ChildItem "archive-$stamp" -Recurse -File | Measure-Object | Select-Object Count
```
**Expect:** a non-zero file count (a container that never existed simply yields none). Keep `archive-<date>/` outside the repo (it is not gitignored). Part B reads nothing from it.

## Step 8 — Rollback (if retirement is called off)

To release the trip, re-upload the blob with `cleared_at` **and** `cleared_by` set. `cleared_at` is the only field the engine reads (`killswitch.py:151`); `cleared_by` is informational and will be dropped on the next rewrite (caveat 3), so record who cleared it elsewhere (e.g. FOLLOWUPS). Do this after market close for the same race reason as step 2.

```powershell
az storage blob download --account-name $acct --subscription $sub --auth-mode login -c flex-ledger -n kill-switch.json -f kill-switch.cur.json --no-progress
$s = Get-Content kill-switch.cur.json -Raw | ConvertFrom-Json -AsHashtable
$s["cleared_at"] = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$s["cleared_by"] = "jgarrote@easygrids.com"
$s | ConvertTo-Json -Depth 10 | Set-Content kill-switch.cleared.json -Encoding utf8
Get-Content kill-switch.cleared.json    # confirm cleared_at is present and non-empty
az storage blob upload --account-name $acct --subscription $sub --auth-mode login -c flex-ledger -n kill-switch.json -f kill-switch.cleared.json --overwrite --no-progress
```
**Expect:** after the next in-hours tick the blob reads `tripped: false` and entries resume (the engine re-evaluates the drawdown and hit-rate arms from the closed-trade ledger, so a genuinely tripped condition would re-trip on its own). Alternatively restore the pre-trip file saved in step 2b with `--overwrite`. If `FLEX_ENABLED` was already removed by a later Part B deploy, rollback also needs that change reverted through Bicep — the trip alone is not enough.
