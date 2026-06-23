# MAW — Autonomous Council-Executor-Reviewer Workflow Engine

MAW is a standalone AI workflow engine that runs a multi-model **AI Council** (Karpathy-style 3-Stage deliberation), exports the decision as a task to a target project, triggers an **Executor** to implement it, runs a **Reviewer** to verify the result, and finally commits the work after human approval.

```
User Request → AI Council → Human Approval → Export → Executor → Reviewer → Human Approval → Git Commit → Final Report
```

## Features

- **Self-contained**: No external Karpathy project needed. The council engine lives inside `MAW/council/`.
- **User-controlled council**: Choose council members and chairman models via the UI.
- **Safety-first defaults**: Two human approval gates (post-council and pre-commit) with optional advanced auto-mode.
- **Real-time visibility**: Single persistent WebSocket (`/ws/maw`) with task subscribe — no reconnect when switching panels or tasks.
- **Flexible LLM routing**: LiteLLM (default), OpenRouter, or Direct API with auto CN/intl endpoint routing for Kimi/Qwen.
- **Panel 0 setup**: One-page UI for LLM keys, project health, scaffold, and agent installation before launching workflows.
- **Portable target projects**: Executor/reviewer scripts stay in the target repo; MAW only invokes them.
- **Mock mode**: Test the full loop without spending API credits.

## Quick Start (macOS)

> **First-time permission fix**: If double-clicking `.command` files fails, run `chmod +x *.command` in Terminal, or use `install.command` which auto-fixes permissions.

### 1. One-click install

```bash
chmod +x install.command MAW.command   # only needed once after git clone
./install.command
```

This runs `uv sync`, creates `.env` from `.env.example`, and opens `http://127.0.0.1:8002`.

### 2. Daily use

Double-click **`MAW.command`** (or run `./MAW.command`) — Panel 0 opens every time for setup/status before launching a workflow.

### 3. Configure environment

`.env` is created on first install. Key variables:

```env
LLM_PROVIDER=litellm          # litellm | openrouter | direct
LITELLM_API_BASE=http://localhost:4000
OPENROUTER_API_KEY=sk-or-...
# Direct mode: set per-vendor keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.)
TARGET_PROJECT_PATH=/path/to/your/target-project
MAW_MOCK_MODE=0
ALLOW_AUTO_COMMIT=false
```

Mock mode is **server-only** (`MAW_MOCK_MODE=1`); it does not appear in the user UI.

### 4. Target project contract (`MAW_workflow/`)

All MAW artifacts live under `<target-project>/MAW_workflow/`. Executor code changes happen in the project root; state files stay in `MAW_workflow/`.

```
target-project/
├── .gitignore                  # must include MAW_workflow/
└── MAW_workflow/
    ├── AGENT_STATE.md
    ├── TASKS/  PLANNING/  REVIEWS/
    ├── scripts/trigger_executor.py
    └── agent-runner/trigger-review.js, route-review-decision.js
```

Use **Panel 0 → Scaffold** to create this structure automatically. A working mock template is in `template_target_project/`.

Add to `~/.agent-cowork/targets.json`:

```json
{
  "default": "my-project",
  "projects": {
    "my-project": {
      "name": "My Project",
      "path": "/absolute/path/to/target-project",
      "description": "Target workspace for MAW tasks"
    }
  }
}
```

### 4. Start MAW

```bash
./start.sh
# or
uv run python -m uvicorn main:app --host 0.0.0.0 --port 8002 --reload
```

Open http://localhost:8002.

### 5. Run your first task

1. Enter a task prompt.
2. Select council members and chairman.
3. Choose review policy (AI / Human / None).
4. Click **Start Council**.
5. Review the Stage 3 synthesis and click **Approve Plan**.
6. Watch logs in real time.
7. When the pre-commit report appears, review and click **Approve Commit**.

## Project Layout

```
MAW/
├── adapters/             # agent registry + install-adapters templates
├── council/              # embedded Karpathy 3-Stage council
│   ├── config.py
│   ├── council.py
│   ├── llm_provider.py   # LiteLLM / OpenRouter / Direct
│   ├── direct_resolver.py
│   └── storage.py
├── data/
│   ├── conversations/    # council JSON records
│   └── workflows.json    # persisted workflow states
├── export.py             # atomic task export to target project
├── loop_orchestrator.py  # workflow state machine
├── main.py               # FastAPI REST + WebSocket API
├── setup_api.py          # Panel 0 setup / preflight / scaffold
├── static/
│   ├── index.html        # Panel 0–5 single-page dashboard
│   └── ws-manager.js     # persistent WebSocket subscribe manager
├── template_target_project/  # runnable mock target project
└── tests/
```

## WebSocket

The UI uses a single connection to `WS /ws/maw` (alias: `/ws/workflow/global`):

```json
{"action":"subscribe","task_num":"002"}
```

Server pushes `{"type":"log","task_num":"002",...}` and `{"type":"status",...}`. Heartbeat: `{"action":"ping"}` → `{"type":"pong"}`. The legacy per-task endpoint `WS /ws/workflow/{task_num}` remains for backward compatibility.

## Agents

Six GUI/TUI agents (executor and reviewer use the same list): `openwork`, `grok_build`, `antigravity`, `codex`, `claude_cowork`, `custom`. Install via **Panel 0 → Install Adapters**.

## Testing

Run the full test suite in mock mode (**154 tests**):

```bash
MAW_MOCK_MODE=1 uv run pytest -q
```

Legacy unittest entry (equivalent):

```bash
MAW_MOCK_MODE=1 uv run python -m unittest discover -q
```

Context-aware E2E smoke (HTTP, mock council, validates audit export contract):

```bash
MAW_MOCK_MODE=1 uv run python context_smoke_test.py
```

Full workflow E2E smoke (happy path through commit):

```bash
MAW_MOCK_MODE=1 uv run python smoke_test.py
```

Run a single module:

```bash
MAW_MOCK_MODE=1 uv run pytest test_safety.py -v
```

See `docs/CONTEXT_GOVERNANCE.md` for reasonCode, riskFlags, and auto-approve policy reference.

## Safety Defaults

- `ALLOW_AUTO_COMMIT=false`: even if the UI unchecks pre-commit approval, gate #2 stays enforced until this env var is `true`.
- `auto_approve_council` (UI checkbox): skips gate #1 only; independent of `ALLOW_AUTO_COMMIT`.
- `MAX_REVIEW_ITERATIONS=3`: REQUEST_CHANGES loops are capped.
- `EXECUTOR_TIMEOUT_SECONDS` / `REVIEWER_TIMEOUT_SECONDS`: subprocess timeouts prevent runaway processes.
- All workflow states persist to `data/workflows.json`; `resume_unfinished()` re-attaches monitors on restart.
- Mock mode is server-only (`MAW_MOCK_MODE=1`); never exposed in the user UI.

## Advanced Auto-Mode

To run fully autonomously (not recommended for production), set **all** of the following:

```env
ALLOW_AUTO_COMMIT=true
```

And in the task UI:

- Uncheck **Require pre-commit approval**.
- Optionally check **Allow REQUEST_CHANGES loop**.

## License

MIT
