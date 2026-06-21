# MAW: Autonomous Council-Executor-Reviewer Loop

## Implementation Recommendation (for Antigravity)

---

## 1. Goal

Transform MAW from a passive Karpathy-export adapter into a **standalone autonomous AI workflow engine**:

- User describes a task.
- MAW runs a local multi-model AI council (Karpathy logic, embedded in MAW).
- MAW exports the council decision as a task into a target project.
- MAW triggers the target project's Executor (Antigravity) to implement the task.
- MAW triggers the target project's Reviewer (Grok Build) to review the result.
- Based on review outcome, MAW either loops back for fixes or proceeds toward commit.
- Two explicit human approval gates are required before any irreversible action:
  1. After the council produces its plan.
  2. Before the final git commit / merge.

MAW must be **portable**: downloading the MAW folder alone should be enough to run the system, assuming the user also provides a valid target project with the required scripts.

---

## 2. Core Design Principles

- **Self-contained council** - Karpathy's 3-Stage logic lives inside `MAW/council/`. No external Karpathy project needed.
- **Target project owns executor/reviewer scripts** - `scripts/trigger_antigravity.py`, `agent-runner/trigger-review.js`, and `agent-runner/route-review-decision.js` remain in the target project. MAW only invokes them.
- **MAW files are local records, not git history** - Generated `TASKS/`, `PLANNING/`, `REVIEWS/`, and `AGENT_STATE.md` should be git-ignored in the target project. They live in the workspace for human inspection only.
- **User controls the council** - Model selection for council members and chairman is exposed in the UI.
- **Human-in-the-loop at gates** - Council plan and final commit each require explicit user approval.
- **Safety by default** - Loop limits, timeouts, cost awareness, and graceful failure states are mandatory.
- **Real-time visibility** - WebSocket streaming for executor/reviewer logs from day one.

---

## 3. Target Project Contract

A valid target project must provide the following structure:

```
<target-project>/
├── AGENT_STATE.md              # Central task registry
├── TASKS/                      # Task markdown files
├── PLANNING/                   # Council provenance files
├── REVIEWS/                    # Review reports
├── scripts/
│   └── trigger_antigravity.py  # Start executor for a task
├── agent-runner/
│   ├── trigger-review.js       # Start reviewer for a task
│   └── route-review-decision.js   # Parse review and emit decision
└── .gitignore                  # Must ignore MAW-generated files
```

### Required .gitignore additions for target projects

```gitignore
# MAW-generated local records (do not commit)
AGENT_STATE.md
TASKS/
PLANNING/
REVIEWS/
*.tmp
.maw_export.lock
```

MAW must validate this contract on startup via `validate_target()` and report exactly which files are missing.

---

## 4. New MAW Directory Structure

```
MAW/
├── .env                          # OPENROUTER_API_KEY, TARGET_PROJECT_PATH
├── .gitignore
├── pyproject.toml
├── main.py                       # FastAPI + WebSocket endpoints
├── export.py                     # Task export to target project (modified)
├── loop_orchestrator.py          # NEW: core workflow state machine
├── council/                      # NEW: embedded Karpathy logic
│   ├── __init__.py
│   ├── config.py                 # Default model lists + chairman
│   ├── openrouter.py             # Async OpenRouter client
│   ├── council.py                # 3-Stage meeting logic
│   └── storage.py                # Conversation JSON read/write
├── data/
│   ├── conversations/            # Local council records
│   └── workflows.json            # Persisted workflow states
├── template_target_project/      # NEW: minimal skeleton for quick setup
│   ├── AGENT_STATE.md
│   ├── TASKS/
│   ├── PLANNING/
│   ├── REVIEWS/
│   ├── scripts/
│   │   └── trigger_antigravity.py
│   ├── agent-runner/
│   │   ├── trigger-review.js
│   │   └── route-review-decision.js
│   └── .gitignore
├── static/
│   └── index.html                # NEW UI: council + pipeline + logs
└── start.sh
```

---

## 5. Component Specifications

### 5.1 `council/openrouter.py`

- Async functions:
  - `query_model(model_id: str, messages: list, temperature=0.7) -> str`
  - `query_models_parallel(model_ids: list, messages: list) -> list[dict]`
- Read `OPENROUTER_API_KEY` from environment (`.env`).
- Handle rate limits and retries with exponential backoff.
- Return structured errors that the orchestrator can log.

### 5.2 `council/config.py`

- Default council members: e.g. `openai/gpt-4o`, `anthropic/claude-3-5-sonnet`, `google/gemini-2.5-pro`.
- Default chairman: e.g. `google/gemini-2.5-pro`.
- UI must override these per task; `config.py` only supplies initial defaults.

### 5.3 `council/council.py`

Re-implement Karpathy 3-Stage logic:

1. **Stage 1** - Each selected model answers the user's request independently.
2. **Stage 2** - Each model anonymously ranks all Stage 1 answers.
3. **Stage 3** - Chairman model synthesizes the best plan from Stage 1 answers and Stage 2 rankings.

Output must match the existing JSON schema expected by `export.py` (`stage1`, `stage2`, `stage3`, `metadata`).

Support a **mock mode** for testing that returns fixed stage data without API calls.

### 5.4 `council/storage.py`

- Save/load conversation JSON to `data/conversations/{conversation_id}.json`.
- Format compatible with the old Karpathy schema so `export.py` continues to work.

### 5.5 `loop_orchestrator.py`

This is the heart of the autonomous loop.

#### Responsibilities

- Maintain a persistent workflow state store (`data/workflows.json`).
- Manage a state machine per task.
- Spawn subprocess calls to target project scripts.
- Stream stdout/stderr from subprocesses to WebSocket clients.
- Enforce loop limits and timeouts.
- Pause at human approval gates.

#### State Machine

```
IDLE
  ↓ (user creates task)
COUNCIL_RUNNING
  ↓ (council done)
COUNCIL_PENDING_APPROVAL        ← human gate #1
  ↓ (user approves)
EXPORTED
  ↓ (task files written to target project)
EXECUTOR_RUNNING
  ↓ (AGENT_STATE.md transitions to UNDER_REVIEW)
REVIEW_PENDING
  ↓
REVIEW_RUNNING
  ↓ (REVIEWS/review_NNN.md generated)
REVIEW_DECISION_PENDING
  ├─ REQUEST_CHANGES ──→ EXECUTOR_RUNNING  (loop, max N times)
  ├─ APPROVE ───────────→ COMMIT_PENDING_APPROVAL  ← human gate #2
  └─ REJECT ───────────→ FAILED (operator alert)
  ↓ (user approves final report)
COMMITTING
  ↓ (git add, commit, merge)
COMPLETED
  ↓
FINAL_REPORT_PRESENTED
```

#### Safety Limits

- `MAX_REVIEW_ITERATIONS` - default 3, configurable per task.
- `EXECUTOR_TIMEOUT` - default 10 minutes.
- `REVIEWER_TIMEOUT` - default 5 minutes.
- If any limit is exceeded, transition to `FAILED` with a descriptive reason.

### 5.6 `main.py` Additions

#### New REST Endpoints

- `POST /api/maw/conversations/new` - Start a council from a prompt with model selection and review policy.
- `GET  /api/maw/conversations/{id}` - Fetch council result details.
- `POST /api/maw/conversations/{id}/approve` - User approves council plan; triggers export and executor launch.
- `GET  /api/maw/workflow/{task_num}/status` - Current workflow state including latest logs.
- `POST /api/maw/workflow/{task_num}/approve-commit` - User approves final report; triggers git commit and merge.
- `POST /api/maw/workflow/{task_num}/cancel` - Cancel an active workflow.
- `GET  /api/maw/targets` - List configured target projects.

#### WebSocket

- `WS   /ws/workflow/{task_num}` - Real-time log stream for executor/reviewer subprocess output.

### 5.7 `static/index.html` Requirements

#### Panel 1: Council Creation

- Prompt textarea for the user's task request.
- Model selector:
  - Checklist of available council member models.
  - Dropdown for chairman model.
- Review policy checkboxes:
  - Review mode: None / AI Review / Human Review
  - Max AI review iterations: 0 to 5
  - Allow REQUEST_CHANGES loop: yes / no
  - Require pre-commit approval: yes / no (default yes)

#### Panel 2: Council Result Review

- Render Stage 1 individual answers.
- Render Stage 2 anonymous rankings.
- Render Stage 3 chairman synthesis.
- Approve and Reject buttons.

#### Panel 3: Workflow Pipeline Tracker

- Horizontal node graph showing the current flow:
  `Council → Executor → Review → (Fix Loop) → Commit Approval → Done`
- Current active node glows or pulses with animation.

#### Panel 4: Real-Time Log Terminal

- WebSocket-connected terminal panel.
- Displays stdout and stderr from executor/reviewer subprocesses.
- Auto-scrolls to latest output.

#### Panel 5: Pre-Commit Report Modal

Short report containing:
- Task ID and title
- List of files changed
- Review decision (APPROVE or after how many REQUEST_CHANGES loops)
- One-sentence chairman summary of the completed work

Action buttons: Approve Commit, Request Changes, Cancel.

---

## 6. Workflow Detail

### 6.1 Starting a Task (Council Phase)

1. User submits prompt, model selection, and review policy via the UI.
2. `loop_orchestrator` creates a new workflow entry with state `COUNCIL_RUNNING`.
3. `council/council.py` runs Stage 1, Stage 2, Stage 3 against the selected models.
4. `council/storage.py` saves the result to `data/conversations/{id}.json`.
5. State transitions to `COUNCIL_PENDING_APPROVAL`.
6. UI notifies the user: council result is ready for review.

### 6.2 Council Approval Gate (Human Gate #1)

1. User reviews the Stage 3 chairman synthesis.
2. **If user clicks Approve:**
   - `export.py:export_to_target()` writes:
     - `TASKS/task_NNN.md` (executor task file)
     - `PLANNING/council_NNN.md` (human-readable meeting record)
     - `PLANNING/council_NNN.json` (full provenance data)
     - Updates `AGENT_STATE.md` registry row to `IN_PROGRESS`.
   - Orchestrator spawns the target project executor script:
     ```bash
     python3 scripts/trigger_antigravity.py --task-num NNN
     ```
   - State transitions to `EXECUTOR_RUNNING`.
3. **If user clicks Reject:**
   - Council result is discarded. State transitions to `FAILED`.

### 6.3 Executor Phase

1. Orchestrator monitors `AGENT_STATE.md` for task status changes.
2. Subprocess stdout/stderr is streamed to the UI via WebSocket.
3. When `AGENT_STATE.md` shows the task status changed to `UNDER_REVIEW`:
   - Based on the task's review policy, MAW proceeds accordingly:
     - **Review mode: None** → skip review, go directly to `COMMIT_PENDING_APPROVAL`.
     - **Review mode: Human** → notify user to perform manual review.
     - **Review mode: AI** → state becomes `REVIEW_PENDING`.

### 6.4 Review Phase

1. Orchestrator spawns the reviewer:
   ```bash
   node agent-runner/trigger-review.js NNN
   ```
2. When `REVIEWS/review_NNN.md` appears, state briefly becomes `REVIEW_RUNNING`.
3. Orchestrator spawns the routing script:
   ```bash
   node agent-runner/route-review-decision.js NNN
   ```
4. The decision is parsed: `APPROVE`, `REQUEST_CHANGES`, or `REJECT`.

### 6.5 Review Decision Routing

| Decision | Action |
|----------|--------|
| `APPROVE` | State → `COMMIT_PENDING_APPROVAL`. Orchestrator generates a short pre-commit report. User must approve to proceed. |
| `REQUEST_CHANGES` | If under `MAX_REVIEW_ITERATIONS`, reset task state to `IN_PROGRESS` and re-trigger the executor. Otherwise, state → `FAILED` with loop-exhausted reason. |
| `REJECT` | State → `FAILED`. Alert user for manual intervention. |

### 6.6 Commit Approval Gate (Human Gate #2)

1. UI shows the short pre-commit report.
2. **If user clicks Approve Commit:**
   - Orchestrator performs atomic Git operations:
     ```bash
     git add -A
     git commit -m "TASK-NNN: {title}"
     git merge task/task_NNN_{slug}
     ```
   - Updates `AGENT_STATE.md` registry row to `MERGED`.
   - State → `COMPLETED`.
   - Chairman model generates a final summary report.
   - State → `FINAL_REPORT_PRESENTED`.
3. **If user clicks Request Changes:**
   - Task returns to `IN_PROGRESS` and executor is re-triggered.
4. **If user clicks Cancel:**
   - State → `FAILED`. Workflow stops.

---

## 7. Configuration

### `.env` file

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENROUTER_API_KEY` | YES | — | OpenRouter API key |
| `TARGET_PROJECT_PATH` | NO | — | Default target project path |
| `DEFAULT_COUNCIL_MODELS` | NO | `openai/gpt-4o,anthropic/claude-3-5-sonnet` | Comma-separated model IDs |
| `DEFAULT_CHAIRMAN_MODEL` | NO | `openai/gpt-4o` | Chairman model ID |
| `MAX_REVIEW_ITERATIONS` | NO | `3` | Default loop limit |
| `EXECUTOR_TIMEOUT_SECONDS` | NO | `600` | Executor timeout |
| `REVIEWER_TIMEOUT_SECONDS` | NO | `300` | Reviewer timeout |

### `~/.agent-cowork/targets.json` (persisted target definitions)

```json
{
  "default": "pad",
  "projects": {
    "pad": {
      "name": "Pixel Agent Desk",
      "path": "/Users/user/projects/pixel-agent-desk",
      "description": "UI-heavy multi-agent workspace"
    }
  }
}
```

---

## 8. Safety and Error Handling

### Failure Modes

- **OpenRouter API failure** - Retry 3 times with exponential backoff. If retries exhausted, mark `FAILED` and report error.
- **Rate limit** - Wait for `Retry-After` header or fall back to default delay.
- **Subprocess crash** - Capture return code and stderr. Mark `FAILED` with exit code and error output.
- **Subprocess timeout** - Hard kill the process group. Mark `FAILED` with timeout message.
- **Loop exhaustion** - When `MAX_REVIEW_ITERATIONS` is reached, stop looping, mark `FAILED`, and ask user to intervene.
- **Concurrent exports** - Preserve the existing `.maw_export.lock` file-lock mechanism to prevent write collisions.
- **State recovery** - On MAW startup, reload unfinished workflows from `data/workflows.json` and resume monitoring their target project state.

### Design Safeguards

- All `FAILED` states include a descriptive `reason` field and the full subprocess logs for debugging.
- Human approval is always required before any destructive or irreversible action.
- Git operations are wrapped in confirmation checks (e.g., no force-push, no rebase, no branch deletion).

---

## 9. Testing Strategy

### Unit Tests

- `council/` parsing and storage logic.
- `export.py` task generation with mock council data.
- `loop_orchestrator.py` state transitions for all decision branches.

### Mock Mode

- `council/council.py` supports a mock mode that returns deterministic stage data without making API calls.
- `template_target_project/scripts/` includes mock executor/reviewer scripts that simulate state file changes without real work.
- All mock components are usable in both unit tests and E2E tests.

### E2E Test

Full happy path with mock components: Council → Approval → Export → Executor → Review → Approval → Commit.

### WebSocket Tests

Verify that log streaming works with both real and mock subprocess output.

---

## 10. Implementation Phases

### Phase 1: Embedded Council Module

- Build `council/openrouter.py`, `council/config.py`, `council/council.py`, `council/storage.py`.
- Store conversations to `data/conversations/`.
- Verify: can run a 3-Stage council from CLI and save valid JSON.

### Phase 2: Export Integration + Orchestrator Skeleton

- Integrate `export.py` with the new council output.
- Build basic `loop_orchestrator.py` with the state machine and first two states (`COUNCIL_RUNNING`, `COUNCIL_PENDING_APPROVAL`).
- Add REST endpoints: `POST /api/maw/conversations/new`, `POST /api/maw/conversations/{id}/approve`.

### Phase 3: Review Loop and Routing

- Implement executor and reviewer subprocess spawning.
- Implement `AGENT_STATE.md` polling and `REVIEWS/` file detection.
- Implement review decision routing (`REQUEST_CHANGES` loop + `APPROVE`).
- Add state persistence to `data/workflows.json`.

### Phase 4: WebSocket and New UI

- Implement WebSocket endpoint for log streaming.
- Build the new `static/index.html` with all five panels.
- Connect UI to all REST and WebSocket endpoints.

### Phase 5: Safety, Tests, Polish

- Add all timeout and loop-limit enforcement.
- Implement mock council mode and mock target scripts.
- Write full test suite.
- Add `template_target_project/` with working executor/reviewer scripts.
- Final documentation (README.md).

---

## 11. Open Decisions

Before coding starts, please confirm:

1. Should `template_target_project/` include **working** executor/reviewer scripts, or placeholder stubs?
2. Should the chairman's final summary report also be written to `PLANNING/final_report_NNN.md`?
3. Should MAW display a cost estimate in the UI before running a council?
4. Should MAW automatically create `data/` and `template_target_project/` folders on first run if they are missing?

---

## 12. Summary Checklist

- [ ] Embed Karpathy 3-Stage council into `MAW/council/`
- [ ] Store conversations locally in `MAW/data/conversations/`
- [ ] Add `loop_orchestrator.py` with persistent state machine
- [ ] Add two human approval gates (post-council, pre-commit)
- [ ] Add per-task review policy UI (mode, max iterations, REQUEST_CHANGES toggle)
- [ ] Add user-selectable council member models and chairman model
- [ ] Implement WebSocket log streaming for executor/reviewer output
- [ ] Keep executor/reviewer scripts in target project; MAW only triggers them
- [ ] Ensure MAW-generated files are local records, not committed to target git
- [ ] Add safety limits: loop count, subprocess timeouts, failure recovery
- [ ] Provide `template_target_project/` skeleton for quick target setup
- [ ] Add mock mode for testing without API costs
- [ ] Persist workflow states in `data/workflows.json` for crash recovery
- [ ] Write short pre-commit report with chairman summary
- [ ] Default pre-commit approval to on (require human confirmation)
