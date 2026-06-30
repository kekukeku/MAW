# MAW v2 — File-driven Multi-Agent Workflow Coordinator

MAW v2 is a file-driven local agent workflow coordinator. It creates workflow files, watches for expected artifacts, dispatches local agents, and advances workflow state through approval gates.

```
User request
  -> v2.app creates workflow files
  -> TEAM_RULES.md defines role behavior
  -> watcher.py watches expected artifacts
  -> dispatcher/adapters wake local agents
  -> agents write files
  -> watcher advances workflow state
  -> user approves required gates
```

## Quick Start

```bash
uv sync
uv run python -m v2.app create --target /path/to/project --request "your task description"
uv run python -m v2.app watch --target /path/to/project
uv run python -m v2.app status --target /path/to/project --workflow-id workflow_001 --verbose
uv run python -m v2.app decide --target /path/to/project --workflow-id workflow_001 APPROVE
```

## Mock Agent Smoke Test

Registered adapters are currently limited. For smoke/local tests, use mock agents explicitly:

```bash
tmp=$(mktemp -d)
uv run python -m v2.app create \
  --target "$tmp" \
  --request "test v2 cutover" \
  --chair mock \
  --planners mock \
  --executor mock \
  --reviewer mock
uv run python -m v2.app watch --target "$tmp" --once
```

## Commands

| Command | Description |
|---------|-------------|
| `create` | Create a new workflow |
| `watch` | Start the watcher |
| `status` | Show workflow status |
| `answer` | Answer chair questions |
| `decide` | Approve/reject/cancel plan |
| `list` | List workflows |
| `adapters` | List available adapters |
| `inspect` | Inspect workflow (read-only) |
| `read` | Read a workflow artifact |

## Environment Variables

```env
WATCHER_POLL_INTERVAL=3
AGENT_TIMEOUT_SECONDS=600
MAX_AGENT_RETRIES=2
MAX_REVIEW_ITERATIONS=3
TARGET_PROJECT_PATH=
```

`TARGET_PROJECT_PATH` in `.env` is wrapper-only. The v2 CLI accepts explicit `--target`.

## Testing

```bash
uv run python -m unittest discover -s v2_tests -q
```

## License

MIT
