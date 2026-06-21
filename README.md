# MAW — Autonomous Council-Executor-Reviewer Workflow Engine

MAW is a standalone AI workflow engine that runs a multi-model **AI Council** (Karpathy-style 3-Stage deliberation), exports the decision as a task to a target project, triggers an **Executor** to implement it, runs a **Reviewer** to verify the result, and finally commits the work after human approval.

```
User Request → AI Council → Human Approval → Export → Executor → Reviewer → Human Approval → Git Commit → Final Report
```

## Features

- **Self-contained**: No external Karpathy project needed. The council engine lives inside `MAW/council/`.
- **User-controlled council**: Choose council members and chairman models via the UI.
- **Safety-first defaults**: Two human approval gates (post-council and pre-commit) with optional advanced auto-mode.
- **Real-time visibility**: WebSocket streaming of executor/reviewer logs.
- **Portable target projects**: Executor/reviewer scripts stay in the target repo; MAW only invokes them.
- **Mock mode**: Test the full loop without spending API credits.

## Quick Start

### 1. Install dependencies

```bash
uv sync
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in your OpenRouter key:

```bash
cp .env.example .env
```

```env
OPENROUTER_API_KEY=sk-or-...
TARGET_PROJECT_PATH=/path/to/your/target-project
MAW_MOCK_MODE=0
DEFAULT_COUNCIL_MODELS=openai/gpt-4o,anthropic/claude-3-5-sonnet,google/gemini-2.5-pro
DEFAULT_CHAIRMAN_MODEL=openai/gpt-4o
ALLOW_AUTO_COMMIT=false
MAX_REVIEW_ITERATIONS=3
EXECUTOR_TIMEOUT_SECONDS=600
REVIEWER_TIMEOUT_SECONDS=300
```

### 3. Configure a target project

Create or use a target project that follows the MAW contract. A working mock example is provided in `template_target_project/`.

Target project structure:

```
target-project/
├── AGENT_STATE.md              # central task registry
├── TASKS/                      # task markdown files
├── PLANNING/                   # council records & final reports
├── REVIEWS/                    # review reports
├── scripts/
│   └── trigger_antigravity.py  # start executor
├── agent-runner/
│   ├── trigger-review.js       # start reviewer
│   └── route-review-decision.js # parse review decision
└── .gitignore                  # must ignore MAW-generated files
```

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
├── council/              # embedded Karpathy 3-Stage council
│   ├── config.py
│   ├── council.py
│   ├── openrouter.py
│   └── storage.py
├── data/
│   ├── conversations/    # council JSON records
│   └── workflows.json    # persisted workflow states
├── export.py             # atomic task export to target project
├── loop_orchestrator.py  # workflow state machine
├── main.py               # FastAPI REST + WebSocket API
├── static/index.html     # 5-panel workflow dashboard
├── template_target_project/  # runnable mock target project
└── tests/
```

## Testing

Run all tests in mock mode:

```bash
MAW_MOCK_MODE=1 uv run python -m unittest discover -v
```

Run a quick smoke test against the template target project:

```bash
# Add template target to ~/.agent-cowork/targets.json, then:
MAW_MOCK_MODE=1 uv run python -m uvicorn main:app --port 8002
```

## Safety Defaults

- `ALLOW_AUTO_COMMIT=false`: the final commit always requires human approval.
- `MAX_REVIEW_ITERATIONS=3`: REQUEST_CHANGES loops are capped.
- Subprocess timeouts prevent runaway executor/reviewer processes.
- All workflow states are persisted to `data/workflows.json` for crash recovery.

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
