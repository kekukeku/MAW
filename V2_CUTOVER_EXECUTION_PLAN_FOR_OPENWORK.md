# MAW V2 Cutover Execution Plan for OpenWork

## Recipient

小 O / OpenWork

## Mission

Execute the final cutover so **MAW v2 fully replaces MAW v1**.

After this task, the repository must no longer launch, expose, document, test, or depend on the v1 external-model council engine.

This is a deletion-heavy task. Be conservative around v2 assets, but decisive about v1 removal.

## Source of Truth

This document is the authoritative execution plan.

Use these only as background:

```text
V2_CUTOVER_PLAN_FOR_GROK_BUILD.md
V2_CUTOVER_PLAN_REVIEW_FOR_AGENTS.md
LOCAL_AGENT_ARCHITECTURE_PLAN.md
WATCHER_FIRST_SYNTHESIS.md
PLAN_REVIEW_APPENDIX.md
V2_IMPLEMENTATION_AUDIT.md
```

If any older plan conflicts with this file, follow this file.

## Desired Final State

MAW v2 is a file-driven local agent workflow coordinator:

```text
User request
  -> v2.app creates workflow files
  -> TEAM_RULES.md defines role behavior
  -> watcher.py watches expected artifacts
  -> dispatcher/adapters wake local agents
  -> agents write files
  -> watcher advances workflow state
  -> user approves required gates
```

MAW v1 must be gone:

```text
external model council
LiteLLM / OpenRouter / Direct API routing
context pack / scout / explorer
FastAPI Panel 0 dashboard
loop_orchestrator
conversation export
v1 target template
v1 tests
```

## Non-Negotiable Invariants

Preserve all of these:

1. `v2/` must not import v1 modules.
2. `v2/dispatcher.py` must remain.
3. `v2/adapters/__init__.py` must remain.
4. `v2_templates/AGENTS.md` must remain.
5. `v2_templates/TEAM_RULES.md` must remain.
6. `v2_tests/` must remain and pass.
7. Command files must stay executable.
8. v2 must not require LiteLLM, OpenRouter, Direct API, or vendor API keys.
9. v2 workflow state must remain file-based under `MAW_workflow/workflows/<workflow_id>/`.
10. Every phase must end with v2 validation before moving on.

## Critical Naming Trap

Do not confuse these:

```text
DELETE: adapters/            # root v1 Panel 0 installer
KEEP:   v2/adapters/         # v2 package placeholder
KEEP:   v2/dispatcher.py     # v2 adapter registry and dispatch implementation
```

Deleting `v2/adapters/` is a cutover failure.

## Baseline

Run before editing:

```bash
git status --short --branch
uv sync
uv run python -m unittest discover -s v2_tests -q
uv run python -m v2.app --help
uv run python -m v2.app adapters
```

Expected baseline:

```text
v2_tests: 122 passed, 1 skipped
```

If baseline fails, stop and report. Do not delete v1 until v2 baseline is healthy.

## Phase 1: Switch Entrypoints to V2

Modify:

```text
MAW.command
install.command
start.sh
README.md
.env.example
```

### MAW.command

Forbidden:

```text
uvicorn main:app
open "http://127.0.0.1:8002"
```

Acceptable interim behavior:

```text
print v2 quick-start commands
run uv run python -m v2.app --help
optionally use TARGET_PROJECT_PATH if set
```

Preferred behavior:

```text
If TARGET_PROJECT_PATH is set:
  show workflows/status or print next v2 commands for that target.
If TARGET_PROJECT_PATH is not set:
  print concise v2 usage with examples.
```

### install.command

Keep:

```text
cd "$(dirname "$0")"
uv sync
chmod +x MAW.command install.command
existing icon setup if it still works
exec ./MAW.command
```

Remove:

```text
mkdir -p ~/.agent-cowork
any v1-only setup
anything that assumes Panel 0
```

Only keep `.env.example -> .env` copy after `.env.example` is v2-clean.

### start.sh

Make it v2-only.

Forbidden:

```text
uvicorn main:app
FastAPI server startup
```

### .env.example

Remove all v1 external-model settings:

```text
LLM_PROVIDER
LITELLM_API_BASE
LITELLM_API_KEY
OPENROUTER_API_KEY
OPENAI_API_KEY
ANTHROPIC_API_KEY
GOOGLE_API_KEY
DEEPSEEK_API_KEY
KIMI_API_KEY
QWEN_API_KEY
GROK_API_KEY
DEFAULT_COUNCIL_MODELS
DEFAULT_CHAIRMAN_MODEL
ALLOW_AUTO_COMMIT
EXECUTOR_TIMEOUT_SECONDS
REVIEWER_TIMEOUT_SECONDS
MAW_MOCK_MODE
MAW_HOST
MAW_PORT
```

Keep only v2-relevant variables:

```text
WATCHER_POLL_INTERVAL=3
AGENT_TIMEOUT_SECONDS=600
MAX_AGENT_RETRIES=2
MAX_REVIEW_ITERATIONS=3
TARGET_PROJECT_PATH=
```

`TARGET_PROJECT_PATH` is wrapper-only. v2 CLI still accepts explicit `--target`.

### README.md

Rewrite as v2-only usage:

```text
uv sync
uv run python -m v2.app create --target /path/to/project --request "..."
uv run python -m v2.app watch --target /path/to/project
uv run python -m v2.app status --target /path/to/project --workflow-id workflow_001 --verbose
uv run python -m v2.app decide --target /path/to/project --workflow-id workflow_001 APPROVE
```

Document that current registered adapters are limited. For smoke/local tests, use mock agents explicitly:

```bash
uv run python -m v2.app create \
  --target "$tmp" \
  --request "test v2 cutover" \
  --chair mock \
  --planners mock \
  --executor mock \
  --reviewer mock
```

### Phase 1 Validation

```bash
grep -n "uvicorn\|main:app\|agent-cowork" install.command MAW.command start.sh
uv run python -m v2.app --help
uv run python -m v2.app adapters
uv run python -m unittest discover -s v2_tests -q
```

Expected:

```text
grep has no matches
v2 app commands work
v2 tests pass
```

## Phase 2: Delete V1 Runtime Code

Delete:

```text
council/
main.py
loop_orchestrator.py
setup_api.py
export.py
project_context.py
scout.py
explorer.py
maw_paths.py
adapters/
template_target_project/
context_smoke_test.py
smoke_test.py
verify_e2e.py
static/index.html
static/ws-manager.js
```

Keep:

```text
static/main_app_icon.png
static/installer_icon.png
```

Keep the icons only because `install.command` currently uses them. If you move them, update `install.command` in the same commit and validate it.

Optional local cleanup, not necessarily tracked:

```text
data/
```

### Phase 2 Validation

```bash
rg -n "main:app|loop_orchestrator|from council|import council|maw_paths|adapters\\.installer|template_target_project|LLM_PROVIDER|OPENROUTER|LITELLM" \
  --glob '!docs/archive-v1/**' \
  --glob '!V2_CUTOVER*.md' \
  .

uv run python -m unittest discover -s v2_tests -q
uv run python -m v2.app --help
uv run python -m v2.app adapters
```

Expected:

```text
No active v1 references outside archive/cutover docs
v2 tests pass
```

## Phase 3: Delete V1 Tests

Delete root v1 tests:

```text
test_adapters.py
test_context_api.py
test_council.py
test_direct_resolver.py
test_e2e_workflow.py
test_explorer.py
test_export.py
test_llm_provider.py
test_openrouter.py
test_orchestrator.py
test_project_context.py
test_safety.py
test_scout.py
test_setup_api.py
test_websocket.py
```

Do not port v1 FastAPI, council, context pack, scout, explorer, or route tests.

Only port a test if it expresses a still-relevant v2 invariant and is not already covered by `v2_tests/`.

Known coverage already in v2:

```text
v2 import isolation
runtime_state.json
executor lock
watcher restart recovery
path safety
inspect no side effects
mock E2E
real adapter spike tests
```

### Phase 3 Validation

```bash
uv run python -m unittest discover -s v2_tests -q
```

## Phase 4: Remove V1 Dependencies

Inspect imports:

```bash
rg -n "^import |^from " v2 v2_tests
```

Remove all v1 runtime dependencies from `pyproject.toml` if still present:

```text
fastapi
httpx
pydantic
python-dotenv
uvicorn[standard]
websockets
```

`v2/` and `v2_tests/` currently use stdlib only.

`pytest` is optional. v2 tests use `unittest`; remove `pytest` and `pytest-asyncio` unless there is a clear reason to keep them.

Run:

```bash
uv sync
uv run python -m unittest discover -s v2_tests -q
uv run python -m v2.app --help
```

## Phase 5: Archive or Delete V1 Documents

User goal is v2-only. Prefer deletion for v1 docs unless they are still useful as v2 history.

Delete or move to `docs/archive-v1/`:

```text
CONTEXT_AWARE_COUNCIL_REFACTOR_PLAN.md
CONTEXT_RELEASE_HARDENING_PLAN.md
CONTEXT_RELIABILITY_PLAN.md
FINAL_SPEC.md
OPTIMIZATION_PLAN.md
implementation_plan.md
docs/CONTEXT_GOVERNANCE.md
docs/PHASE7_UI_CHECKLIST.md
docs/PHASE8_UI_CHECKLIST.md
```

Keep v2 decision/history docs:

```text
LOCAL_AGENT_ARCHITECTURE_PLAN.md
WATCHER_FIRST_SYNTHESIS.md
PLAN_REVIEW_APPENDIX.md
V2_IMPLEMENTATION_AUDIT.md
V2_PHASE_2_5_AND_SPIKE_AUDIT.md
ANTIGRAVITY_ADAPTER_SPIKE_REPORT.md
V2_CUTOVER_PLAN_FOR_GROK_BUILD.md
V2_CUTOVER_PLAN_REVIEW_FOR_AGENTS.md
V2_CUTOVER_EXECUTION_PLAN_FOR_OPENWORK.md
```

For instruction/spike docs:

```text
ANTIGRAVITY_*_INSTRUCTIONS.md
OPENWORK_*_INSTRUCTIONS.md
OPENWORK_*_REPORT.md
```

Use this rule:

```text
If the doc describes v2 watcher/runtime/adapter hardening, keep or archive as v2 history.
If the doc describes Panel 0, external model council, context pack, Scout, Explorer, or uvicorn main:app, delete/archive as v1.
```

Do not leave v1 docs at repo root.

## Phase 6: Add Cutover Guard Tests

Add `v2_tests/test_cutover.py`.

Test A: forbidden v1 paths do not exist:

```python
FORBIDDEN_PATHS = [
    "council",
    "main.py",
    "loop_orchestrator.py",
    "setup_api.py",
    "export.py",
    "project_context.py",
    "scout.py",
    "explorer.py",
    "maw_paths.py",
    "adapters",
    "template_target_project",
]
```

Test B: required v2 paths still exist:

```python
REQUIRED_PATHS = [
    "v2/dispatcher.py",
    "v2/adapters/__init__.py",
    "v2_templates/AGENTS.md",
    "v2_templates/TEAM_RULES.md",
]
```

Test C: forbidden v1 tokens are absent from active surfaces:

```python
FORBIDDEN_TOKENS = [
    "LLM_PROVIDER",
    "LITELLM_API_KEY",
    "LITELLM_API_BASE",
    "OPENROUTER_API_KEY",
    "DEFAULT_COUNCIL_MODELS",
    "DEFAULT_CHAIRMAN_MODEL",
    "uvicorn main:app",
    "loop_orchestrator",
]
```

Scan only active files:

```text
README.md
.env.example
MAW.command
install.command
start.sh
pyproject.toml
v2/
v2_templates/
```

Allow old tokens only under `docs/archive-v1/` or cutover plan docs.

Validation:

```bash
uv run python -m unittest discover -s v2_tests -q
```

## Phase 7: Final V2 Smoke

Use mock agents explicitly. Do not rely on default `codex`, because `codex` is not currently a registered v2 adapter.

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
uv run python -m v2.app status --target "$tmp" --workflow-id workflow_001 --verbose
uv run python -m unittest discover -s v2_tests -q

find "$tmp" -maxdepth 6 -type f | sort
```

Smoke pass criteria:

```text
create exits 0
watch --once exits 0
no "No adapter for agent"
status shows workflow_001
AGENTS.md exists in target
TEAM_RULES.md exists in target
MAW_workflow/workflows/workflow_001/manifest.json exists
MAW_workflow/workflows/workflow_001/request.md exists
v2_tests pass
```

## Phase 8: Git Review

Before staging:

```bash
git status --short
git diff --stat
git diff --name-status
```

Confirm no accidental deletion of:

```text
v2/
v2_tests/
v2_templates/
v2/adapters/
v2/dispatcher.py
```

Confirm deletion of:

```text
council/
main.py
loop_orchestrator.py
setup_api.py
export.py
project_context.py
scout.py
explorer.py
maw_paths.py
adapters/
template_target_project/
root v1 tests
```

Recommended commit message:

```text
chore: cut over MAW to v2 and remove v1 council engine
```

## Stop Conditions

Stop and report if any of these happen:

1. Baseline v2 tests fail before editing.
2. v2 tests fail after any phase.
3. `MAW.command`, `install.command`, or `start.sh` still references `uvicorn` or `main:app` after Phase 1.
4. Root `adapters/` is deleted but `v2/adapters/` is missing.
5. `pyproject.toml` still lists `fastapi` or `uvicorn` after Phase 4.
6. Phase 7 smoke reports `No adapter for agent 'codex'`.
7. A file appears shared by v1 and v2 and ownership is unclear.
8. A doc is the only clear source of a v2 invariant and you are about to delete it.

## Known Non-Blockers

These are acceptable after cutover but must be reported:

```text
v2 UI is not built yet; launcher may be CLI-first.
Only mock and antigravity are registered adapters unless new adapters are added.
v2 uses explicit --target instead of v1 ~/.agent-cowork/targets.json.
```

Do not reintroduce v1 to fix these. Track them as v2 follow-up work.

## Final Report Format

Report back exactly:

```text
Summary:
- Entrypoints switched:
- V1 runtime deleted:
- V1 tests deleted:
- Docs deleted/archived:
- Dependencies removed:
- Guard tests added:

Validation:
- Baseline:
- Phase validations:
- Final v2_tests:
- Final smoke:
- Residual rg matches:

Residual Risk:
- UX:
- Adapters:
- Target selection:
- Other:
```

## Success Definition

Cutover is complete only when all are true:

```text
✓ MAW.command does not launch v1
✓ install.command does not launch v1 or seed v1-only config paths
✓ start.sh does not launch v1
✓ README describes v2 only
✓ .env.example has no external model council keys
✓ root adapters/ is deleted
✓ v2/adapters/ still exists
✓ maw_paths.py is deleted
✓ template_target_project/ is deleted
✓ v1 runtime files are deleted
✓ v1 root tests are deleted
✓ pyproject has no v1 runtime dependencies
✓ cutover guard tests pass
✓ v2_tests pass
✓ mock-agent smoke passes
✓ no dual entrypoint remains
```
