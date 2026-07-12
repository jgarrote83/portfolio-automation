# Learning Loop v1.0 — Institutionalized Strategy Learning

**Status:** ACTIVE (approved by account holder 2026-07-11; committed 2026-07-12).
Resolves FOLLOWUPS #13 + #32.
**Prerequisite:** met (verified Task 0). The Learning tab is an approval surface;
it MUST NOT exist on an unauthenticated site. Note: Task 0 verified this against
the SWA hardening batch's CORRECTED design (owner-role auth + deploy-time Key
Vault secret resolution, PR #22) rather than the SystemAssigned-identity +
runtime-KV-reference design this spec's prerequisite line originally assumed —
Azure Static Web Apps managed functions cannot support that pattern on any plan
(verified against Microsoft Learn 2026-07-11). The underlying safety property
(no anonymous access to the approval surface) is met either way.

---

## 1. Purpose & principles

The system records and grades every decision (Phase C) but cannot yet convert
lessons into policy. This spec adds the missing half: a monthly, high-reasoning
review that proposes amendments from the graded record, and a human approval
surface that turns proposals into version-controlled changes.

Non-negotiable principles, inherited from the existing architecture:

1. **Proposer ≠ approver.** The reviewer model proposes; the account holder
   disposes. No automated path from proposal to applied change exists anywhere.
2. **Git is the only change mechanism.** Approval opens a GitHub PR. The human
   merge is the final gate; CI runs the full test suite on every amendment; the
   repo history is the audit trail.
3. **Process discipline, not outcome-chasing.** Proposals must cite graded
   process evidence (hit rates, calibration, streak records), not raw recent
   P&L. A losing month may justify caution; it never justifies silently
   rewriting the strategy.
4. **The loop grades itself.** Every applied amendment becomes a graded decision
   (OverrideHistory layer `amendment`), so the system eventually learns whether
   its learning works.

## 2. Architecture overview

```
[monthly timer / manual trigger]
        │
        ▼
 learning_reviewer (new Azure Function, func-pfauto)
   ├─ builds the input bundle (§5)
   ├─ calls the reviewer model via Azure AI Foundry (§3)
   ├─ validates output against the proposal schema (deterministic, §6)
   └─ writes learning/proposals/{cycle}.json  +  Table row per proposal
        │
        ▼
 SWA "Learning" tab (§7)
   ├─ GET /api/learning/proposals      (pending + history + grades)
   └─ POST /api/learning/decision      (approve | reject + required reason)
        │ approve
        ▼
 GitHub PR opened via API (§8) ──► human review ──► merge (or close)
        │ merged
        ▼
 OverrideHistory row, layer `amendment` ──► graded 30/60/90d (§9)
```

## 3. Model & deployment

- **Launch reviewer model: `claude-sonnet-4-6`** — the analyzer's existing
  Foundry deployment (same project, `Portfolio-Analysis`, East US 2). Decided
  2026-07-11: launch on existing capacity; no new deployment, no new quota.
- **Target reviewer model: Claude Fable 5** (Hosted on Anthropic infrastructure,
  Global Standard) once the requested quota lands. Rationale: 1M-token context
  ingests a full month of reports + the complete graded record + all live config
  verbatim; adaptive thinking suits a deliberation task; monthly cadence makes
  cost negligible (single-digit dollars per cycle).
- **The upgrade is a config flip, never a code change:** set app settings
  `LEARNING_MODEL=claude-fable-5` and raise `LEARNING_BUNDLE_MAX_TOKENS` /
  `LEARNING_MAX_TOKENS`. Tracked as a FOLLOWUPS entry.
- **Call parameters (config-driven, launch defaults):** temperature 0.2
  (consistent with the analyzer); `LEARNING_MAX_TOKENS` default 16000 (the
  analyzer deployment's known-good output size); `LEARNING_BUNDLE_MAX_TOKENS`
  default 150000 (conservative for the Sonnet context window — see §5 for the
  truncation consequence). Single call per cycle.
- The daily analyzer remains `claude-sonnet-4-6` — unchanged and unaffected.

## 4. Cadence & triggers

- **Timer:** first Saturday of each month, 12:00 ET (market closed; a full month
  of graded rows available; no collision with daily runs).
- **Manual:** a "Run review now" button on the Learning tab (POST
  /api/learning/run, auth-gated), rate-limited to 1/day.
- **Skip rule:** if <15 trading sessions have elapsed since the last completed
  cycle, the reviewer runs in observation-only mode (class-0 proposals only, §6).

## 5. Reviewer input bundle

Built by the `learning_reviewer` function (not the collector — keep the daily
path untouched). Contents, in priority order:

1. The month's daily reports (`daily-reports/*.md`) — verbatim.
2. `track_record` at capture-fine granularity (all enums, all horizons) — the
   report-coarse promotion rule does not apply to the reviewer.
3. OverrideHistory — ALL layers (`override`, `sleeve_switch`,
   `intl_leader_rotation`, `regime_suspect`, `amendment`), including grades.
4. Live config verbatim, fetched from GitHub raw at master HEAD (see §8's
   `diff_base_sha` requirement): `project-instructions.md`, `risk-limits.json`,
   `sleeve-roles.json`, `flex-candidates.json`.
5. FOLLOWUPS.md (open items only).
6. Learning history: all prior cycles' proposals, decisions, rejection reasons,
   and amendment grades — so rejected ideas are not re-proposed without new
   evidence (§6, re-proposal rule).
7. `quadrant_performance` + `performance` blocks from the latest snapshot.

**Token budget:** `LEARNING_BUNDLE_MAX_TOKENS` (config). When the estimated
bundle exceeds it, the oldest daily reports drop first; graded records (2, 3, 6)
and live config (4) NEVER drop. Consequence at the launch budget (150K on
Sonnet): expect roughly the most recent 8–12 reports to fit — the graded record
is the primary evidence engine at launch; the full-month verbatim prose is what
the Fable upgrade unlocks. Per-section bundle stats are recorded on every cycle.

## 6. Proposal contract

The reviewer's output is a JSON document validated **deterministically** before
anything reaches the tab. Out-of-schema output fails the cycle loudly (logged,
surfaced on the tab as a failed run) — it is never partially accepted.

Each proposal:

```json
{
  "id": "AMD-2026-08-01",
  "class": 1,
  "title": "Raise flex confidence bar for thesis_type=turnaround",
  "change_summary": "Raise flex confidence bar for turnaround entries",
  "data_summary": "Turnaround theses: 29% hit rate at 60d (n=14) vs 61% for quadrant-gap trades",
  "target_file": "src/config/project-instructions.md",
  "diff": "<unified diff, applies cleanly at diff_base_sha>",
  "evidence": [
    "TradeHistory: thesis_type=turnaround hit_rate_60d=0.29 (n=14)",
    "Calibration: stated confidence 0.68 vs realized 0.31 on that bucket"
  ],
  "evidence_n": 14,
  "expected_effect": "Fewer low-quality turnaround entries; est. 2-3 fewer flex trades/month",
  "falsifier": "If flex 60d hit rate does not improve by 2027-01 review, revert",
  "review_by": "2027-01-03",
  "risk_class_notes": "Prompt-text only; no parameter or structural change"
}
```

**Lean-summary fields (required, deterministically validated):**
`change_summary` ≤120 chars (what changes, plain language) and `data_summary`
≤140 chars (the single strongest stat, with n). These render the tab's summary
table; a proposal that cannot state its case in one line each fails validation.
**Type badge mapping (tab display):** class 0 → `Note`, classes 1–2 →
`Small change`, class 3 → `Structural`. The underlying class drives the
approval mechanics; the badge is what the operator sees.

**Proposal classes and their bars:**

| Class | Scope | Bar |
|---|---|---|
| 0 — Observation | FOLLOWUPS entry or spec-gap note; no config change | None (informational; auto-filed on approve) |
| 1 — Prompt text | `project-instructions.md` wording/rules | Diff + evidence citing ≥1 graded aggregate |
| 2 — Parameter | Numeric tunables (`risk-limits.json`, `sleeve-roles.json` thresholds) | Diff + evidence with **n ≥ 10 graded rows** directly bearing on the parameter (this is how #37 eventually resolves) |
| 3 — Structural | Roster, gates, validator, new signals | **No diff permitted.** The proposal's artifact is a spec document draft (like roster_revision_2026-07) PLUS an `implementation_brief`: a structured build-prompt skeleton (task decomposition, files expected to change, constraints, test expectations). Approval files the spec under `docs/specs/` for a separate human-driven implementation cycle |

**Target-file allowlist (classes 1–2):** {`src/config/project-instructions.md`,
`src/config/risk-limits.json`, `src/config/sleeve-roles.json`,
`config/flex-candidates.json`}. A diff targeting ANY other path — code,
workflows, validator, infra — is schema-invalid by construction.

**Hard caps:** ≤3 proposals per cycle (class-0 excluded). ≤1 class-3 per cycle.

**Re-proposal rule:** a previously rejected idea may only reappear if its
evidence cites graded rows dated **after** the rejection, and the proposal must
name the prior rejection and what changed.

## 7. Learning tab (SWA)

- **Pending view — one lean table, one row per proposal:**
  `# | Change (change_summary) | Type badge | What the data shows (data_summary)
  | Decision (✅/❌)`. The decision must be makeable from the table alone.
  Tapping a row expands full detail: complete evidence list, rendered diff
  (verbatim; the tab never reformats a diff) or spec link for Structural,
  expected effect, falsifier, review-by, inline reject-reason input.
- **History view — same table shape plus outcome:**
  `Cycle | Change | Type | Decision | Reason | PR | Grade` — the grade column
  fills in as `amendment` rows mature (§9).
- **Run view:** last cycle's narrative summary, bundle stats, model/tokens used,
  "Run review now".
- **Class-3 handoff:** approved class-3 proposals expose a "Copy implementation
  brief" action (the §6 skeleton, verbatim). The brief is a STARTING POINT for a
  build prompt, not a finished one — it reflects the repo as of the review
  bundle, so it must be re-grounded against live master before a Claude Code run
  (function names, file locations, and config values re-verified).
- **API:** `GET /api/learning/proposals`, `POST /api/learning/decision`,
  `POST /api/learning/run` — all behind the (now mandatory) Entra auth; the
  decision/run endpoints additionally require the authenticated principal to
  hold the `owner` role, optionally pinned tighter to one specific SWA user id
  via `OWNER_USER_ID` (defense in depth on a shared-tenant edge case) — that
  value is SWA's own opaque `userId` from `/.auth/me` *after* signing in, NOT
  an Entra object id (the two are unrelated identifiers; a 2026-07-12 fix
  after the wrong value shipped and would have denied everyone).

## 8. Approval mechanics (PR, never direct write)

On Approve (classes 1–2):
1. The API re-validates the stored diff still applies cleanly to current master
   (`git apply --check` semantics server-side, from the bundle's recorded
   `diff_base_sha` forward). If stale → proposal flagged `stale`, returned to
   the reviewer's next cycle for regeneration. Nothing is force-applied.
2. Branch `amend/{cycle}-{id}` created via GitHub API; diff committed; PR opened
   with a templated body: proposal JSON verbatim + evidence + falsifier +
   review_by date.
3. **The human merges.** CI (full test suite, ruff) must pass. The automation
   credential CANNOT merge (enforced by branch protection, not convention).
4. On merge (lazy reconciliation via the GitHub API; no webhook in v1), the
   proposal row flips to `applied`, and the `amendment` OverrideHistory row is
   written (§9).

Class-3 approval commits the spec draft to `docs/specs/proposals/` via the same
PR path. Class-0 approval appends the FOLLOWUPS entry via the same PR path.

**Credential:** a fine-grained GitHub PAT scoped to this repo only, permissions
`contents:write` + `pull_requests:write` (NO merge/admin), stored in
`kv-pfauto-prod`, surfaced to the SWA API as a KV-referenced app setting (per
the hardening batch pattern). A GitHub App installation is the cleaner
long-term replacement — noted, not required for v1.

## 9. The meta-loop (grading amendments)

Every applied class-1/2 amendment writes an OverrideHistory row: layer
`amendment`, the proposal id, its falsifier and review_by. A third grading
function (sibling of `_grade_switch`, per the shape sketched in the
`regime_suspect` docstring) evaluates at the falsifier's own terms where
mechanically measurable — deferred until ≥5 amendments exist (FOLLOWUPS entry).
Independently, every amendment is force-reviewed by the reviewer at its
`review_by` cycle: **keep / revert / amend**, with revert proposals exempt from
the 3-proposal cap. An amendment whose falsifier fired and was not reverted
must be explicitly re-justified — silence is not an option the schema permits.

## 10. Failure modes & mitigations

- **Schema violation / hallucinated diff** → deterministic validation fails the
  cycle loudly; nothing surfaces as approvable.
- **Prompt injection via the record** (a report or memo containing adversarial
  text) → the reviewer's output is inert JSON proposals; nothing it emits
  executes; the target-file allowlist bounds what a diff can touch; diffs are
  human-reviewed twice (tab + PR) and CI-tested.
- **Proposal churn** → caps, re-proposal rule, falsifier obligation.
- **Stale diffs after unrelated merges** → apply-check on approval; stale
  proposals regenerate rather than force-apply.
- **Credential blast radius** → fine-grained PAT, single repo, no merge rights,
  KV-stored, branch protection as the backstop.

## 11. Rollout phases (single build, `LEARNING_PHASE` app setting)

1. **Phase 1 — dry-run (≥1 cycle):** reviewer function + bundle + schema
   validation only; output lands in blob; account holder reads the JSON
   directly. No tab nav entry, no decision endpoint. Gate: proposals are sane,
   evidence citations resolve to real rows.
2. **Phase 2 — tab read-only (≥1 cycle):** Learning tab renders proposals +
   history + manual run; decision endpoint returns 409. Gate: rendering
   fidelity, auth verified.
3. **Phase 3 — full loop:** decision endpoint + PR mechanics + amendment
   tracking live.

## 12. Decisions (resolved 2026-07-11)

1. Model: launch on `claude-sonnet-4-6` (existing deployment); upgrade to
   Fable 5 by config flip when quota lands (§3).
2. Cycle timing: first Saturday, 12:00 ET.
3. Proposal cap: 3 (≤1 structural).
4. Credential: fine-grained PAT for v1; GitHub App as tracked follow-up.
5. Class-2 may not touch sleeve-selection hysteresis or
   `suspect_after_sessions` until #37's n≥10 sample exists — enforced
   automatically by the class-2 evidence bar; no special-case code.
