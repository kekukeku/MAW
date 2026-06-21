# MAW — Autonomous Council-Executor-Reviewer Workflow Engine

MAW is a portable, self-contained autonomous AI workflow engine. It runs a local multi-model AI council, exports approved plans to a target project, triggers executor/reviewer scripts, and manages the full loop with human approval gates.

## Quick Start

```bash
# Install dependencies
uv sync

# Configure environment
cp .env.example .env   # set OPENROUTER_API_KEY

# Start server
./start.sh
# Open http://localhost:8002
```

## Architecture

```
User Prompt → Council (3-Stage) → [Human Gate #1] → Export → Executor → Review → [Human Gate #2] → Git Commit
```

### Components

| Module | Purpose |
|--------|---------|
| `council/` | Embedded Karpathy 3-Stage LLM council |
| `export.py` | Atomic task export to target project |
| `loop_orchestrator.py` | Workflow state machine + subprocess streaming |
| `main.py` | FastAPI REST + WebSocket API |
| `static/index.html` | 5-panel workflow UI |
| `template_target_project/` | Minimal target project skeleton |

## Target Project Contract

A valid target project must provide:

```
<target>/
├── AGENT_STATE.md
├── TASKS/  PLANNING/  REVIEWS/
├── scripts/trigger_antigravity.py
├── agent-runner/trigger-review.js
├── agent-runner/route-review-decision.js
└── .gitignore  (ignoring MAW-generated files)
```

Copy `template_target_project/` as a starting point.

## Configuration

### `.env`

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | — | Required for live council |
| `TARGET_PROJECT_PATH` | — | Optional default target |
| `MAW_MOCK_MODE` | `false` | Skip API calls in council |
| `MAX_REVIEW_ITERATIONS` | `3` | Review loop limit |
| `EXECUTOR_TIMEOUT_SECONDS` | `600` | Executor timeout |
| `REVIEWER_TIMEOUT_SECONDS` | `300` | Reviewer timeout |
| `ALLOW_AUTO_COMMIT` | `false` | Required to bypass human gate #2 when `require_pre_commit_approval` is false |

### `~/.agent-cowork/targets.json`

Register target projects with name and absolute path.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/maw/conversations/new` | Start council |
| GET | `/api/maw/conversations/{id}` | Council details + workflow |
| POST | `/api/maw/conversations/{id}/approve` | Human gate #1 |
| GET | `/api/maw/workflow/{task_num}/status` | Workflow status |
| POST | `/api/maw/workflow/{task_num}/approve-commit` | Human gate #2 |
| POST | `/api/maw/workflow/{task_num}/cancel` | Cancel workflow |
| WS | `/ws/workflow/{task_num}` | Real-time log stream |

## Testing

```bash
# Run all tests (mock mode, no API costs)
MAW_MOCK_MODE=1 uv run python -m pytest test_export.py test_council.py test_orchestrator.py test_e2e_workflow.py -v
```

## Safety

- Two human approval gates (post-council, pre-commit)
- Loop limits, subprocess timeouts, graceful `FAILED` states
- `.maw_export.lock` prevents concurrent exports
- Workflow state persisted in `data/workflows.json` for crash recovery