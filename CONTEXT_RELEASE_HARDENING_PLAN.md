# Phase 7: Release Hardening / Real Workflow Validation

> **Version**: 1.0
> **Status**: Complete (`e18e54a`, Gate #1 polish `1fe8d3e`, Phase 7C manual UI checklist passed)
> **Prerequisite**: Phase 6a–6g.1 landed and accepted (`c5c1e00` on `origin/main`, **154 tests pass**).
> **North Star**: Validate that context-aware council is **stable, auditable, and replayable** on real usage paths. **Release hardening, not feature expansion.**
> **Acceptance baseline**: Existing **154 pytest tests** stay green and the independent
> `context_smoke_test.py` HTTP workflow passes. A pytest wrapper remains optional.

---

## 0. Background (why Phase 7, what is already done)

Phases **6a–6g.1** completed the context-aware council architecture:

| Phase | Delivered |
|-------|-----------|
| 6a | L0 blueprint, prompt envelope, conversation provenance, auto-approve guard |
| 6b–6c | Context bar, preview API, file selector |
| 6d | L1 manual context files |
| 6e | L2 Scout auto-include |
| 6f | L3 Explorer brief (read-only) |
| 6g | `build_context_audit_summary()`, `context_audit` persistence, export contract, Gate #1 audit UI |
| 6g.1 | `highestLevel` fallback on explorer failure, `l0_only` flag, export `audit_unavailable`, dead-code cleanup |

**Gap today**: unit tests are strong (154), but **real HTTP workflow smoke is stale**:

- `smoke_test.py` (`:62-74`) starts council with **no** `contextFiles`, **no** `scoutPreviewKey`, **no** `explorerPreviewKey`.
- It does **not** assert `context_audit`, `contextAuditSummary`, `autoApprovePolicy`, or Explorer brief persistence.
- `README.md` Testing section still says **49 tests** and `unittest discover` (`README.md:145-148`).
- `CONTEXT_AWARE_COUNCIL_REFACTOR_PLAN.md` §13 still says 6g **待實作** (`:1345`).

Phase 7 closes the gap between **architecture landed** and **release confidence**.

---

## 1. Final context pipeline (SSOT reference for 7A docs)

All implementers must use this canonical pipeline string in docs:

```text
User prompt + targetKey
  → build_context_pack()           [L0 blueprint always; L1 manual files; L2 scout_auto_selected if enabled]
  → run_explorer_brief() optional  [L3 explorerBrief attached to context_pack; failure isolated]
  → build_prompt_envelope()        [Council prompt injection; Explorer = NOT source of truth]
  → run_council()                  [conversation.context_pack + conversation.context.preview saved]
  → build_context_audit_summary()  [SSOT for highestLevel, riskFlags, status]
  → _can_auto_approve_council()    [autoApprovePolicy decision object]
  → conversation.context_audit     [persisted via save_conversation()]
  → Gate #1 UI                     [resolveContextPreview() reads context_pack + context_audit]
  → export_to_target()             [PLANNING/council_NNN.md + .json with contextAuditSummary + autoApprovePolicy]
```

**SSOT rules (do not break in Phase 7)**:

1. Level/risk decisions come **only** from `build_context_audit_summary()` (`project_context.py:1137`).
2. Auto-approve decisions come **only** from `_can_auto_approve_council()` (`loop_orchestrator.py:353`).
3. Export reads `conversation.context_audit` first; fallback uses `audit_unavailable` (`export.py`), never fabricates `allowed_policy_ok`.

---

## 2. Task tier overview

| Tier | ID | Content | Primary files |
|------|-----|---------|---------------|
| **7A** | Doc sync | Mark 6g/6g.1 done; update pipeline + test counts | `CONTEXT_AWARE_COUNCIL_REFACTOR_PLAN.md`, `README.md` |
| **7B** | Automated E2E | Context-aware HTTP smoke + API reload persistence + export contract | `context_smoke_test.py` (new), optional `smoke_test.py` touch-up |
| **7C** | Manual UI | Gate #1 / context bar / Explorer wording checklist | `static/index.html` (verify only; fix only if checklist fails) |
| **7D** | Governance doc | reasonCode / riskFlags / auto-approve policy reference | `docs/CONTEXT_GOVERNANCE.md` (new) |
| **7E** | Test hygiene | ResourceWarning / subprocess cleanup or documented waiver | `test_orchestrator.py`, `test_explorer.py` |

---

## 3. 7A — Update completion status & pipeline docs

### 3.1 `CONTEXT_AWARE_COUNCIL_REFACTOR_PLAN.md`

**Change §13 header** (`:1343-1345`):

```markdown
## 13. Phase 6g - Context Governance / Audit Hardening

**狀態**：✅ 已完成（6g: `686da94`，6g.1 polish: `c5c1e00`）。前置 6a–6f 均已落地。
```

**Append new section after §17** (before §18 完成定義):

```markdown
## 17.1 Phase 6g.1 — Audit Level Correctness (completed)

**狀態**：✅ 已完成（`c5c1e00`）。

- `highestLevel` promotes to L3 only when `explorerBrief.status in ("ready", "partial")`.
- Failed/timeout/skipped explorer falls back to L2/L1/L0; risk flags preserved.
- `l0_only` emitted backend-side; export fallback uses `audit_unavailable`.

## 17.2 Phase 7 — Release Hardening (this plan)

See `CONTEXT_RELEASE_HARDENING_PLAN.md`.
```

**Update §17 建議最終排序** block — append:

```text
P3: 6g.1 Audit Level Polish ✅
P4: 7  Release Hardening / Real Workflow Validation  ← current
```

### 3.2 `README.md` Testing section (`:143-161`)

Replace stale content with:

```markdown
## Testing

Run the full test suite in mock mode (**154 tests**):

\`\`\`bash
MAW_MOCK_MODE=1 uv run pytest -q
\`\`\`

Legacy unittest entry (equivalent):

\`\`\`bash
MAW_MOCK_MODE=1 uv run python -m unittest discover -q
\`\`\`

Context-aware E2E smoke (HTTP, mock council, validates audit export):

\`\`\`bash
MAW_MOCK_MODE=1 uv run python context_smoke_test.py
\`\`\`

Full workflow E2E smoke (happy path through commit):

\`\`\`bash
MAW_MOCK_MODE=1 uv run python smoke_test.py
\`\`\`
```

### 3.3 7A acceptance

- [ ] §13 and new §17.1/17.2 reflect completed status.
- [ ] README test count = 154, commands use `pytest`.
- [ ] No contradictory "6g 待實作" remains in repo docs.

---

## 4. 7B — Real E2E smoke (automated, API-level)

### 4.1 Problem

`smoke_test.py` validates workflow states only. Phase 7 requires proving the **context governance contract** survives a real HTTP path.

### 4.2 Deliverable: `context_smoke_test.py` (new file, repo root)

Pattern: copy structure from `smoke_test.py` (`api()`, `poll`, uvicorn on port **8083** to avoid collision with `smoke_test.py:8082`).

**Target fixture setup** (do NOT scan developer's real projects):

1. `tempfile.mkdtemp(prefix="maw_ctx_smoke_")`
2. Copy `template_target_project/` as base.
3. **Augment** with minimal source files (template is MAW_workflow-only today):

```text
<temp-target>/
├── README.md              # "Mock app for MAW context smoke"
├── package.json           # {"name":"ctx-smoke"}
├── src/
│   └── main.py            # def login(): pass  (gives Scout/Explorer something to find)
└── MAW_workflow/          # from template
```

4. `git init` + initial commit (mirror `test_e2e_workflow.py:33-43`).
5. Point `TARGET_PROJECT_PATH` at temp target OR patch `~/.agent-cowork/targets.json` with key `"ctx_smoke"` for the run (prefer env `TARGET_PROJECT_PATH` + targets patch in-script).

**Env for uvicorn child**:

```python
env["MAW_MOCK_MODE"] = "1"
env["OPENROUTER_API_KEY"] = "dummy"
env["TARGET_PROJECT_PATH"] = target_path
env["ALLOW_AUTO_COMMIT"] = "false"
```

### 4.3 HTTP sequence (exact order)

```text
Step 1  POST /api/maw/context/preview
Step 2  POST /api/maw/context/explorer/preview
Step 3  POST /api/maw/conversations/new   (full context path)
Step 4  Poll GET /api/maw/workflows/{workflow_id} → COUNCIL_PENDING_APPROVAL
Step 5  GET  /api/maw/conversations/{conversation_id}   (first read)
Step 6  GET  /api/maw/conversations/{conversation_id}   (reload simulation — same assertions)
Step 7  POST /api/maw/conversations/{conversation_id}/approve
Step 8  Read <target>/MAW_workflow/PLANNING/council_{task_num}.json
Step 9  Read <target>/MAW_workflow/PLANNING/council_{task_num}.md
```

### 4.4 Request bodies (copy-paste contract)

**Step 1 — Context preview** (`main.py:309`, `ContextPreviewRequest`):

```json
{
  "targetKey": "ctx_smoke",
  "prompt": "Add a glassmorphism login button in src/main.py",
  "contextFiles": ["src/main.py"],
  "autoScoutContext": true,
  "maxAutoScoutFiles": 3,
  "minScoutScore": 40
}
```

Assert response: `level` in (`L0`,`L1`), `files` non-empty or `suggestedFiles` present, `warnings` does not contain `unavailable`.

**Step 2 — Explorer preview** (`main.py:378`):

```json
{
  "targetKey": "ctx_smoke",
  "prompt": "Add a glassmorphism login button in src/main.py",
  "contextFiles": ["src/main.py"],
  "timeoutSeconds": 15
}
```

Assert response: `status` in (`ready`, `partial`, `timeout`, `failed`) — **any is OK for smoke**; record status for logging. If `ready`/`partial`, assert `summary` or `candidateFiles` keys exist.

**Step 3 — Start council** (`main.py:254`, `NewConversationRequest`):

```json
{
  "prompt": "Add a glassmorphism login button in src/main.py",
  "targetKey": "ctx_smoke",
  "title": "Context Smoke Test",
  "councilModels": ["openai/gpt-4o"],
  "chairmanModel": "openai/gpt-4o",
  "reviewPolicy": {
    "mode": "AI",
    "max_iterations": 1,
    "allow_request_changes": false,
    "require_pre_commit_approval": true,
    "auto_approve_council": false
  },
  "mock": true,
  "contextFiles": ["src/main.py"],
  "autoIncludeScoutFiles": true,
  "maxAutoScoutFiles": 3,
  "minScoutScore": 40,
  "scoutPreviewKey": { "targetKey": "ctx_smoke", "prompt": "Add a glassmorphism login button in src/main.py" },
  "generateExplorerBrief": true,
  "explorerPreviewKey": { "targetKey": "ctx_smoke", "prompt": "Add a glassmorphism login button in src/main.py" }
}
```

> `auto_approve_council: false` forces Gate #1 path so `context_audit` is populated before export.

### 4.5 Assertions — conversation persistence (Steps 5–6)

On **both** GET conversation responses, assert:

```python
conv = body["conversation"]

# context pack survived council
assert conv.get("context_pack") is not None
assert conv["context_pack"].get("targetKey") == "ctx_smoke"

# 6g audit record persisted
assert "context_audit" in conv
audit = conv["context_audit"]
assert "auditSummary" in audit
assert "autoApprovePolicy" in audit

summary = audit["auditSummary"]
policy = audit["autoApprovePolicy"]

assert summary.get("highestLevel") in ("L0", "L1", "L2", "L3")
assert isinstance(summary.get("riskFlags"), list)
assert policy.get("reasonCode") in LIVE_REASON_CODES  # see §7D table
assert isinstance(policy.get("allowed"), bool)

# Explorer brief on context_pack (6g.1 reload fix)
if conv["context_pack"].get("explorerBrief"):
    eb = conv["context_pack"]["explorerBrief"]
    assert eb.get("status") is not None
    # highestLevel must NOT be L3 if explorer failed/timeout/skipped (6g.1)
    if eb.get("status") not in ("ready", "partial"):
        assert summary["highestLevel"] != "L3"
```

`LIVE_REASON_CODES` set (for assertions):

```python
LIVE_REASON_CODES = {
    "allowed_policy_ok",
    "blocked_policy_disabled",
    "blocked_no_context",
    "blocked_l0_only",
    "blocked_scout_auto_selected",
    "blocked_context_failed",
    "blocked_context_partial",
    "blocked_fatal_access",
    "blocked_prompt_file_missing",
}
```

**Deferred from 6g.1** — this smoke **is** the integration test for `context_audit` persistence.

### 4.6 Assertions — export artifacts (Steps 8–9)

After approve, read `council_{task_num}.json`:

```python
assert "contextPack" in council_json
assert "contextAuditSummary" in council_json
assert "autoApprovePolicy" in council_json

audit = council_json["contextAuditSummary"]
assert audit.get("contextPackVersion") >= 1
assert audit.get("targetKey") == "ctx_smoke"

policy = council_json["autoApprovePolicy"]
assert policy.get("reasonCode") in LIVE_REASON_CODES | {"audit_unavailable"}
```

Read `council_{task_num}.md`:

```python
assert "## Target Project Context Audit" in council_md
assert "Auto-Approve Decision" in council_md  # from export.py _render_context_summary
```

### 4.7 Optional: pytest wrapper

Add `test_context_smoke.py` that subprocess-calls `context_smoke_test.py` and asserts exit code 0, OR mark `context_smoke_test.py` functions as importable and test directly. **Minimum**: runnable script + documented in README.

### 4.8 7B acceptance

- [ ] `MAW_MOCK_MODE=1 uv run python context_smoke_test.py` exits 0.
- [ ] Script prints `CONTEXT SMOKE TEST PASSED`.
- [ ] Steps 5–6 prove reload-stable `context_audit` + `explorerBrief`.
- [ ] Export json/md contain audit fields.
- [ ] Test count increases (if pytest wrapper added).

---

## 5. 7C — UI/UX manual regression checklist

**Scope**: verify only; code changes **only** if a checklist item fails. No redesign.

Run against `MAW_MOCK_MODE=1 ./start.sh` or `MAW.command`, target = ctx_smoke fixture or any real small project.

### 5.1 Desktop viewport (≥ 1024px)

| # | Check | Pass criteria |
|---|-------|---------------|
| C1 | Context bar | Shows L0/L1+ status; Scout hint when previewed; Explorer hint when enabled |
| C2 | File selector chips | Manual files show as user-selected; Scout auto files visually distinct |
| C3 | Gate #1 audit card | Shows Highest Level, Decision, Risk Flags chips (not raw codes only) |
| C4 | Provenance details | Manual table + Scout table separated (`static/index.html:1460-1474`) |
| C5 | Explorer block | Shows **「research brief — not source of truth」** (`:1366`) |
| C6 | Auto-approve blocked | With scout auto + default policy, Decision shows Blocked + `blocked_scout_auto_selected` translation |

### 5.2 Narrow viewport (≤ 768px)

| # | Check | Pass criteria |
|---|-------|---------------|
| C7 | Context bar | No horizontal overflow; link `[預覽 Context]` still clickable |
| C8 | Gate #1 audit card | Risk flag chips wrap; readable without horizontal scroll |
| C9 | Provenance `<details>` | Expands without layout break |

### 5.3 Browser reload (Gate #1)

| # | Check | Pass criteria |
|---|-------|---------------|
| C10 | Reload at Gate #1 | Explorer brief block **still visible** (6g reload bug does not regress) |
| C11 | Audit card | `context_audit` decision still shown after reload |

### 5.4 Deliverable

Create `docs/PHASE7_UI_CHECKLIST.md` with checkboxes; fill pass/fail + date when run. If any fail, fix minimal UI issue in `static/index.html` and note in checklist.

---

## 6. 7D — Governance reference doc

### 6.1 Deliverable: `docs/CONTEXT_GOVERNANCE.md`

Single operator-facing reference. **Do not duplicate** long prose in README; link from README Testing/Safety section.

### 6.2 Required content tables

**Live auto-approve reason codes** (`_can_auto_approve_council`, `loop_orchestrator.py:353`):

| reasonCode | allowed | Meaning | Default blocks auto-approve? |
|------------|---------|---------|------------------------------|
| `allowed_policy_ok` | true | All policy + safety checks passed | — |
| `blocked_policy_disabled` | false | `auto_approve_council` not enabled | yes |
| `blocked_no_context` | false | No context pack / status unavailable | yes |
| `blocked_context_failed` | false | Context status failed | yes |
| `blocked_fatal_access` | false | `permission_denied` in accessIssues | yes |
| `blocked_context_partial` | false | status partial AND `allow_partial_auto_approve=False` | only if policy false |
| `blocked_l0_only` | false | highestLevel L0 AND `allow_l0_auto_approve=False` | yes (default) |
| `blocked_scout_auto_selected` | false | scout_auto_selected present AND `allow_scout_auto_approve=False` | yes (default) |
| `blocked_prompt_file_missing` | false | Prompt references file not in context | yes |

**Export-only reason code** (legacy fallback, `export.py`):

| reasonCode | When used |
|------------|-----------|
| `audit_unavailable` | `context_pack` exists but `conversation.context_audit` missing (pre-6g export) |

**Risk flags** (`build_context_audit_summary`, `project_context.py:1244-1257`):

| riskFlag | Emitted when |
|----------|--------------|
| `l0_only` | `highestLevel == "L0"` |
| `scout_auto_selected` | Any file `source == "scout_auto_selected"` |
| `explorer_timeout` | Explorer status timeout or `limits.hitTimeout` |
| `explorer_failed` | Explorer status failed |
| `access_issue` | Any `accessIssues` entry |
| `context_truncated` | `summary.truncated == true` |

**Audit-only by default (formal decision)**:

- `context_truncated` and ordinary `accessIssues` (secret exclusion, scout skip, etc.) **do not block** auto-approve unless a future policy requires it.
- `blocked_context_truncated` is **not implemented**.
- `allow_partial_auto_approve` defaults **`True`** (`loop_orchestrator.py:404`).

**highestLevel rules (6g.1)**:

```text
L3 if explorerBrief.status in ("ready", "partial")
L2 elif any scout_auto_selected file
L1 elif any user_selected file
L0 else
```

### 6.3 7D acceptance

- [ ] `docs/CONTEXT_GOVERNANCE.md` exists with all tables above.
- [ ] README links to it (one line under Safety or Testing).
- [ ] No contradiction with `CONTEXT_AWARE_COUNCIL_REFACTOR_PLAN.md` §6g 正式設計決策.

---

## 7. 7E — Test warnings hygiene

### 7.1 Current state

`uv run pytest -q` → **154 passed, 2 warnings** (asyncio `ResourceWarning` / unclosed subprocess transports from Explorer-related orchestrator tests).

This predates 6g; not a functional failure.

### 7.2 Approach (pick one per warning source)

**Option A — Fix** (preferred if < 30 LOC per site):

- Ensure explorer subprocess / async tasks are awaited or cancelled in `tearDown` / fixture cleanup.
- Likely files: `test_orchestrator.py` (`test_explorer_failure_does_not_block_council`, etc.), `test_explorer.py`.

**Option B — Document waiver**:

If fix requires event-loop refactor, add to `docs/CONTEXT_GOVERNANCE.md` appendix or `docs/PHASE7_TEST_WARNINGS.md`:

```markdown
## Known pytest warnings (accepted)

- ResourceWarning: unclosed transport in Explorer subprocess tests
- Reason: daemon thread + bounded join pattern in explorer.py
- Impact: none on production; tests pass
- Revisit: if pytest --filterwarnings=error is adopted
```

### 7.3 7E acceptance

- [ ] Either warnings reduced to 0, OR waiver doc exists with exact warning text.
- [ ] `154+` tests still pass; no behavior change to production paths.

---

## 8. Implementation order

```text
7A   Doc sync (REFACTOR_PLAN + README) — unblocks accurate handoff
7B   context_smoke_test.py + assertions — highest value
7D   docs/CONTEXT_GOVERNANCE.md — can parallel with 7B
7C   Manual UI checklist — after 7B green (Gate #1 data exists)
7E   Warnings — last; do not block release on this
```

Suggested commits:

1. `docs: Phase 7A sync 6g/6g.1 status + README test commands`
2. `test: add context_smoke_test.py for context-aware E2E validation`
3. `docs: add CONTEXT_GOVERNANCE.md + Phase7 UI checklist`
4. `test: reduce ResourceWarnings or document waiver` (optional)

---

## 9. Automated acceptance (mandatory)

```bash
MAW_MOCK_MODE=1 uv run pytest -q
MAW_MOCK_MODE=1 uv run python context_smoke_test.py
```

Expected:

- pytest: **≥ 154 passed** (more if pytest wrapper added), warnings 0 or documented.
- context smoke: exit 0, prints `CONTEXT SMOKE TEST PASSED`.

---

## 10. Affected file list

| File | Action | Tier |
|------|--------|------|
| `CONTEXT_AWARE_COUNCIL_REFACTOR_PLAN.md` | Mark 6g/6g.1 done; add §17.1/17.2 | 7A |
| `README.md` | Update test count + commands + governance link | 7A, 7D |
| `context_smoke_test.py` | **Create** — context-aware HTTP smoke | 7B |
| `test_context_smoke.py` | Optional pytest wrapper | 7B |
| `smoke_test.py` | Optional cross-link comment only; do not break existing flow | 7B |
| `docs/CONTEXT_GOVERNANCE.md` | **Create** — reasonCode / riskFlags reference | 7D |
| `docs/PHASE7_UI_CHECKLIST.md` | **Create** — manual checklist results | 7C |
| `static/index.html` | Fix only if 7C fails | 7C |
| `test_orchestrator.py` / `test_explorer.py` | Warning cleanup | 7E |

---

## 11. Out of scope (hard prohibitions)

1. **No new L4 agent** or embedding / vector DB.
2. **No Scout / Explorer algorithm changes** (`scout.py`, `explorer.py` logic frozen at 6f.2/6g.1).
3. **No auto-approve default relaxation** — do not change default `False` on `allow_l0_auto_approve`, `allow_scout_auto_approve`, `auto_approve_council`.
4. **No main UX flow changes** — Panel order, Start Council button, workflow state machine unchanged.
5. **No new public workflow states**.
6. **No Playwright/Cypress** in Phase 7 — browser reload is manual (7C).
7. **Do not break SSOT** — no ad-hoc audit summary builders outside `build_context_audit_summary()`.

---

## 12. Phase 7 completion definition

Phase 7 is **done** when:

```text
✓ Docs say 6g + 6g.1 completed; README says 154+ tests
✓ context_smoke_test.py passes on clean temp target
✓ conversation.context_audit survives double GET (API reload)
✓ PLANNING/council_NNN.{json,md} contain audit contract fields
✓ CONTEXT_GOVERNANCE.md published
✓ UI checklist executed (pass or fixes documented)
✓ pytest green; warnings fixed or waived in writing
```

One-line standard:

```text
MAW context-aware council is release-ready when a real HTTP path produces
the same audit artifacts a reviewer sees at Gate #1 and in exported PLANNING files.
```
