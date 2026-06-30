# Phase 8: Real-Usage Observability & Reliability

> **Version**: 1.0
> **Status**: Planned — entry gate not yet cleared
> **Prerequisite**: Phase 7 complete (`c865aeb`, **154 tests pass**, `context_smoke_test.py` green, `docs/PHASE7_UI_CHECKLIST.md` all pass).
> **North Star**: Observe how MAW behaves on **real projects**, measure context quality/cost, recover from failures, and make Gate #1 audit data **findable** — **not** ship new council/context features.
> **Positioning**: Phase 7 proved the contract on mock + fixture paths. Phase 8 proves **operational confidence** on live usage.

---

## 0. Why Phase 8 (and what it is not)

Phase 6–7 delivered and hardened the context-aware council architecture. The remaining risk is **production-shaped usage**:

- Does a real target project behave the same as `ctx_smoke`?
- How often does context truncate, Scout miss, or Explorer timeout?
- Can an operator find a past council by `reasonCode` or `riskFlags`?
- After a crash or browser reload mid-workflow, is state recoverable and replayable?

**Phase 8 is not**:

- New L4 agent, embeddings, or vector DB.
- Scout / Explorer algorithm changes (`scout.py`, `explorer.py` frozen at 6f.2 / 6g.1).
- Auto-approve default relaxation.
- Panel reorder, new workflow states, or council UX redesign.
- Playwright/Cypress automation (manual checklists remain the UI gate).

---

## 1. Entry gate — full UI manual regression (8A)

Phase 7 closed **context / Gate #1** UI only (`docs/PHASE7_UI_CHECKLIST.md`, C1–C11).

Phase 8 **must not start implementation** until a broader checklist passes on a **real** target project (not mock-only).

### 8A deliverable: `docs/PHASE8_UI_CHECKLIST.md`

| # | Area | Pass criteria |
|---|------|---------------|
| U1 | Panel 0 setup | LLM keys, project health, scaffold, adapter install — all sections load without error |
| U2 | Panel 1 council start | Prompt, models, review policy, context bar, file selector, Scout/Explorer toggles — start council succeeds |
| U3 | Panel 2 Gate #1 | Stage 3 synthesis readable; approve / reject work; context audit card present (Phase 7 regression) |
| U4 | Panel 3 pipeline | State badges advance correctly through council → executor → reviewer |
| U5 | Panel 4 logs | WebSocket logs stream; task subscribe works when switching tasks |
| U6 | Panel 5 pre-commit | Report modal shows; approve-commit gate enforced when `ALLOW_AUTO_COMMIT=false` |
| U7 | Workflow resume | Restart MAW mid-workflow; unfinished workflow re-attaches and UI shows correct state |
| U8 | Target switch | Changing `targetKey` updates context preview and export path |
| U9 | Narrow viewport | Panels 0–2 usable at ≤768px without horizontal scroll on primary actions |
| U10 | Browser reload | Mid-workflow reload restores active panel and pending gate (council or pre-commit) |

**Run env**: `MAW_MOCK_MODE=0` on at least one real small project; optional `MAW_MOCK_MODE=1` pass for U4–U6 log/stream checks.

**8A acceptance**:

- [ ] `docs/PHASE8_UI_CHECKLIST.md` exists with date, target project path, and all U1–U10 marked pass (or documented minimal fix + rerun).
- [ ] Phase 7 checklist remains all-pass (no regression).

---

## 2. Real-project canary validation & quality metrics (8B)

### 2.1 Problem

`context_smoke_test.py` uses an isolated temp fixture. Real projects introduce git history, larger trees, secrets, and slower Explorer runs.

### 2.2 Deliverable: `canary_run.py` (repo root)

Script (or documented manual procedure + JSON log template) that runs **one full happy-path workflow** on a configured real target:

```bash
CANARY_TARGET_KEY=my-project MAW_MOCK_MODE=0 uv run python canary_run.py
```

**Minimum HTTP path** (extends `context_smoke_test.py`):

1. Validate target via existing preflight (`setup_api` / `validate_target`).
2. Context preview + optional Explorer preview.
3. Start council with manual file + Scout auto (same as production defaults).
4. Gate #1 approve → executor → reviewer → pre-commit gate (or stop at configured checkpoint).
5. Emit `data/canary_runs/{timestamp}.json` with outcome + timings.

### 2.3 Quality metrics (per run)

| Metric | Source | Notes |
|--------|--------|-------|
| `workflow_outcome` | final workflow state | `COMPLETED` / `FAILED` / `stopped_at_gate` |
| `gate1_wait_seconds` | poll timing | Time to `COUNCIL_PENDING_APPROVAL` |
| `executor_duration_seconds` | workflow timestamps | Subprocess wall time |
| `review_verdict` | review artifact | `APPROVE` / `REQUEST_CHANGES` / `REJECT` |
| `context_highest_level` | `context_audit.auditSummary` | L0–L3 |
| `context_status` | audit summary | `ready` / `partial` / `failed` |
| `auto_approve_reason` | `autoApprovePolicy.reasonCode` | SSOT from orchestrator |
| `risk_flag_count` | `auditSummary.riskFlags` | Count + list |
| `export_task_num` | PLANNING artifact | Confirms export succeeded |

### 8B acceptance

- [ ] At least one successful canary on a real project documented in checklist notes.
- [ ] Canary JSON schema documented in this plan or `docs/CANARY_METRICS.md`.
- [ ] `154+` pytest still green; canary is additive, not a pytest replacement.

---

## 3. Context cost, truncation & Scout observability (8C)

### 3.1 Problem

`build_context_audit_summary()` already captures truncation, Scout paths, and Explorer status — but nothing **aggregates** these across runs for operator visibility.

### 3.2 SSOT fields to observe (read-only)

From `context_pack` / `context_audit` (no new audit builders):

| Signal | Field path | Operator question |
|--------|------------|-------------------|
| Context size | `summary.totalChars`, `total_tokens` (UI estimate) | How large was the prompt envelope? |
| Truncation | `summary.truncated`, `riskFlags: context_truncated` | Was content cut? |
| Scout hit rate | `suggestedFiles` vs `scout_auto_selected` in files | Did Scout picks get included? |
| Scout score floor | `sources.scoutAutoSelected.minScoutScore` | What threshold was used? |
| Explorer outcome | `sources.explorerBrief.status`, `hitTimeout` | Ready vs timeout/failed? |
| Access issues | `accessIssueCount`, `riskFlags: access_issue` | Secrets/binary exclusions? |

### 3.3 Deliverable options (pick minimal)

**Option A — Metrics snapshot API** (preferred if ≤80 LOC):

```
GET /api/maw/metrics/context?limit=50
```

Returns rolling summaries from recent `data/conversations/*.json` + `data/workflows.json` — no new DB.

**Option B — Offline aggregator**:

```bash
uv run python scripts/aggregate_context_metrics.py --days 7
```

Prints truncation rate, Scout inclusion rate, Explorer timeout rate, median `totalChars`.

### 8C acceptance

- [ ] Operator can answer "what % of recent councils truncated context?" without opening raw JSON.
- [ ] Scout inclusion rate = `scout_auto_selected files / suggestedFiles` (or documented N/A when Scout off).
- [ ] Metrics read from persisted conversations only; SSOT rules unchanged.

---

## 4. Failure recovery & historical council replay (8D)

### 4.1 Problem

- `resume_unfinished()` handles orchestrator restart for in-flight workflows.
- `GET /api/maw/conversations/{id}` returns full conversation + workflow.
- **Gap**: no first-class UI/API to **browse and replay** a past council's Gate #1 view (context pack + audit + Stage 3).

### 4.2 Deliverables

| Item | Scope |
|------|-------|
| Conversation list enrichment | Extend `list_conversations()` metadata: `targetKey`, `highestLevel`, `reasonCode`, `created_at` |
| Replay endpoint or UI panel | Read-only view of saved `context_pack`, `context_audit`, Stage 3 — **no re-run council** |
| Resume contract doc | Document which states rebuild context on resume vs reuse saved pack (`loop_orchestrator.py`) |
| Interrupted run test | Automated test: kill mid-`COUNCIL_RUNNING`, restart, assert safe resume or explicit failure |

### 4.3 Replay rules

- Replay = **display** persisted JSON; does not mutate target project.
- If `context_pack` missing on old conversations, show `audit_unavailable` fallback (export contract).
- Re-approve / re-export remains explicit user action, not automatic on replay.

### 8D acceptance

- [ ] Operator can open any past conversation and see Gate #1-equivalent audit + context provenance.
- [ ] `resume_unfinished()` behavior documented; one new test covers interrupt + restart.
- [ ] No new workflow states.

---

## 5. Gate #1 audit data searchability (8E)

### 5.1 Problem

`GET /api/maw/conversations` returns only `id`, `title`, `created_at`, `message_count` — not audit facets.

### 5.2 Deliverable: conversation search/filter API

```
GET /api/maw/conversations/search?reasonCode=blocked_scout_auto_selected&highestLevel=L2&targetKey=my-project&limit=20
```

Filter dimensions (all optional, AND semantics):

| Param | Matches |
|-------|---------|
| `reasonCode` | `context_audit.autoApprovePolicy.reasonCode` |
| `highestLevel` | `context_audit.auditSummary.highestLevel` |
| `riskFlag` | any entry in `auditSummary.riskFlags` |
| `targetKey` | `context_pack.targetKey` |
| `q` | title or user prompt substring |

Response: same list shape as current endpoint + audit summary chips for UI.

### 5.3 Optional UI (minimal)

- Panel 1 or sidebar: "Recent councils" with filter chips for `blocked_*` / `L3` / `scout_auto_selected`.
- **No redesign** — table or dropdown on existing layout only.

### 8E acceptance

- [ ] Search returns correct subset on fixture conversations with known audit records.
- [ ] Filters use `context_audit` SSOT only (not re-computed ad hoc).
- [ ] `docs/CONTEXT_GOVERNANCE.md` linked from search UI or API doc comment.

---

## 6. Implementation order

```text
8A   Full UI checklist (ENTRY GATE — blocks 8B–8E)
8B   canary_run.py + real-project validation
8C   Context metrics aggregator or API
8E   Conversation search API (+ minimal UI if needed)
8D   Replay view + resume documentation + interrupt test
```

Suggested commits:

1. `docs: add Phase 8 reliability plan + UI entry checklist`
2. `docs: Phase 8A full UI regression results`
3. `test: add canary_run.py for real-project validation`
4. `feat: context metrics snapshot API` or `scripts/aggregate_context_metrics.py`
5. `feat: conversation audit search API`
6. `feat: historical council replay view`

---

## 7. Automated acceptance (mandatory throughout)

```bash
MAW_MOCK_MODE=1 uv run pytest -q
MAW_MOCK_MODE=1 uv run python context_smoke_test.py
```

Expected: **≥ 154 passed**; context smoke exit 0. New Phase 8 tests are additive.

---

## 8. Affected file list

| File | Action | Tier |
|------|--------|------|
| `docs/PHASE8_UI_CHECKLIST.md` | **Create** — entry gate | 8A |
| `CONTEXT_RELIABILITY_PLAN.md` | This plan | — |
| `CONTEXT_AWARE_COUNCIL_REFACTOR_PLAN.md` | Add §17.3 Phase 8 pointer | docs |
| `canary_run.py` | **Create** — real-project validation | 8B |
| `docs/CANARY_METRICS.md` | Optional metrics schema | 8B |
| `main.py` | Metrics + search endpoints | 8C, 8E |
| `council/storage.py` | Enrich `list_conversations()` | 8D, 8E |
| `static/index.html` | Replay/search minimal UI | 8D, 8E |
| `loop_orchestrator.py` | Resume docs only; test hooks | 8D |
| `test_orchestrator.py` | Interrupt + resume test | 8D |

---

## 9. Out of scope (hard prohibitions)

1. No new context layers (L4+) or embedding search.
2. No Scout / Explorer scoring or timeout algorithm changes.
3. No auto-approve default changes.
4. No workflow state machine expansion.
5. No replacement of manual UI gates with Playwright in Phase 8.
6. No SSOT bypass — search and metrics read `context_audit` / `build_context_audit_summary()` output only.

---

## 10. Phase 8 completion definition

Phase 8 is **done** when:

```text
✓ PHASE8_UI_CHECKLIST U1–U10 pass on a real target project
✓ Phase 7 UI checklist still all-pass
✓ At least one documented successful canary run on a real project
✓ Context truncation / Scout / Explorer rates observable (API or script)
✓ Past conversations searchable by reasonCode / riskFlag / highestLevel
✓ Historical Gate #1 view replayable from persisted JSON
✓ resume_unfinished interrupt behavior documented + tested
✓ pytest green; context_smoke_test.py green
```

One-line standard:

```text
MAW is operationally reliable when real-project usage is measured,
recoverable, and auditable — without expanding the council feature surface.
```