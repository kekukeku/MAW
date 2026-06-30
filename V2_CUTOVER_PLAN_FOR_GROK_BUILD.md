# MAW V2 Cutover Plan for Grok Build

## Recipient

小 B / Grok Build

## Mission

Make **MAW v2 fully replace MAW v1**.

The final repository must no longer expose, launch, document, test, or depend on the old v1 external-model council engine. v2 is the product.

v1 must be removed carefully. Do not delete anything required by v2.

## Current Architectural Decision

MAW v2 is a **file-driven local agent workflow coordinator**.

The correct v2 model is:

```text
User request
  -> MAW creates workflow files
  -> TEAM_RULES.md defines role behavior
  -> watcher.py reads workflow artifacts
  -> adapters wake local agents
  -> agents write expected artifacts
  -> watcher advances state
  -> user approves required gates
```

The incorrect legacy v1 model is:

```text
MAW calls external LLM APIs
  -> multi-model council
  -> context packing / scout / explorer
  -> FastAPI dashboard orchestrates council / executor / reviewer
```

The v1 model must be removed.

## Non-Negotiable Invariants

Preserve these v2 properties:

1. MAW must not directly call model APIs.
2. MAW must not store model API keys.
3. MAW must not require LiteLLM, OpenRouter, Direct API, or vendor API keys.
4. MAW must not import v1 modules from `v2/`.
5. MAW must coordinate through files under `MAW_workflow/workflows/<workflow_id>/`.
6. Completion must be based on expected artifact files, not chat state.
7. `TEAM_RULES.md`, `AGENTS.md`, `v2/`, `v2_templates/`, and `v2_tests/` are v2 assets.
8. Do not delete v2 real-adapter spike code unless it is proven unused and replaced.
9. Keep command files executable.
10. Every deletion batch must be followed by v2 tests.

## Baseline Before Editing

Run this first and record the result:

```bash
git status --short --branch
uv sync
uv run python -m unittest discover -s v2_tests -q
uv run python -m v2.app --help
uv run python -m v2.app adapters
```

Expected current v2 test baseline:

```text
Ran 122 tests ... OK (skipped=1)
```

If baseline fails, stop and report before deleting anything.

## Keep List

These files/directories are v2 or repo infrastructure and should be preserved unless there is a specific proven reason:

```text
.gitignore
.python-version
AGENTS.md
README.md
install.command
MAW.command
start.sh
pyproject.toml
uv.lock
v2/
v2_templates/
v2_tests/
LOCAL_AGENT_ARCHITECTURE_PLAN.md
WATCHER_FIRST_SYNTHESIS.md
PLAN_REVIEW_APPENDIX.md
V2_IMPLEMENTATION_AUDIT.md
ANTIGRAVITY_ADAPTER_SPIKE_REPORT.md
```

Keep image assets only if still used by `install.command` or the replacement v2 launcher:

```text
static/main_app_icon.png
static/installer_icon.png
```

If keeping icons, consider moving them later to:

```text
assets/
```

Do not move icons in the same batch as deleting v1 unless necessary.

## Phase 1: Switch User-Facing Entrypoints to V2

Goal: after this phase, normal users no longer launch v1.

Change:

```text
MAW.command
start.sh
README.md
.env.example
```

Required behavior:

- `MAW.command` must no longer run `uvicorn main:app`.
- `start.sh` must no longer run `uvicorn main:app`.
- Entrypoints should launch or explain v2 CLI workflow commands.
- README must describe v2 only.
- `.env.example` must remove external model council settings.

Remove from `.env.example`:

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
```

Keep or introduce v2 settings:

```text
TARGET_PROJECT_PATH=
MAW_MOCK_MODE=0
WATCHER_POLL_INTERVAL=3
AGENT_TIMEOUT_SECONDS=600
MAX_AGENT_RETRIES=3
MAX_REVIEW_ITERATIONS=3
MAW_HOST=127.0.0.1
MAW_PORT=8002
```

Validation:

```bash
./MAW.command
./start.sh
uv run python -m v2.app --help
uv run python -m v2.app adapters
uv run python -m unittest discover -s v2_tests -q
```

If `MAW.command` is not yet a full UI launcher, it may temporarily print v2 help and exact next commands. That is acceptable. Launching v1 is not acceptable.

## Phase 2: Remove V1 Runtime Code

Goal: remove the external-model council engine and old FastAPI dashboard runtime.

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
context_smoke_test.py
smoke_test.py
verify_e2e.py
```

Delete old static UI files:

```text
static/index.html
static/ws-manager.js
```

Do not delete `static/main_app_icon.png` or `static/installer_icon.png` in this phase if `install.command` still references them.

Validation:

```bash
rg -n "main:app|loop_orchestrator|from council|import council|LLM_PROVIDER|OPENROUTER|LITELLM" .
uv run python -m unittest discover -s v2_tests -q
uv run python -m v2.app --help
uv run python -m v2.app adapters
```

Expected:

- No active v2 entrypoint references `main:app`.
- No v2 module imports v1 code.
- External model settings do not appear in user-facing docs or env examples.

## Phase 3: Remove V1 Tests

Goal: v2 tests become the only active test suite.

Delete:

```text
test_council.py
test_llm_provider.py
test_openrouter.py
test_direct_resolver.py
test_project_context.py
test_scout.py
test_explorer.py
test_context_api.py
test_export.py
test_orchestrator.py
test_setup_api.py
test_websocket.py
test_e2e_workflow.py
test_safety.py
test_adapters.py
```

Before deleting, check whether any test contains a safety rule still relevant to v2. If yes, port the rule to `v2_tests/` in the smallest possible way.

Do not port v1 schema, v1 route, v1 council, context pack, scout, explorer, or FastAPI assumptions.

Validation:

```bash
uv run python -m unittest discover -s v2_tests -q
```

## Phase 4: Remove V1 Dependencies

Goal: `pyproject.toml` should contain only what v2 actually needs.

Inspect v2 imports first:

```bash
rg -n "^import |^from " v2 v2_tests
```

Likely v1-only dependencies:

```text
fastapi
httpx
pydantic
python-dotenv
uvicorn[standard]
websockets
```

Remove them only if v2 does not use them.

Then run:

```bash
uv sync
uv run python -m unittest discover -s v2_tests -q
uv run python -m v2.app --help
```

If v2 real adapters need only stdlib plus external local CLIs, prefer stdlib. Do not add new dependencies unless functionally necessary.

## Phase 5: Archive or Delete V1 Documents

Goal: docs should not teach agents to rebuild v1.

Either delete or move to `docs/archive-v1/`:

```text
CONTEXT_AWARE_COUNCIL_REFACTOR_PLAN.md
CONTEXT_RELEASE_HARDENING_PLAN.md
CONTEXT_RELIABILITY_PLAN.md
FINAL_SPEC.md
OPTIMIZATION_PLAN.md
docs/CONTEXT_GOVERNANCE.md
docs/PHASE7_UI_CHECKLIST.md
docs/PHASE8_UI_CHECKLIST.md
implementation_plan.md
```

Prefer deletion if the user wants a clean v2-only repo.

Keep v2 decision docs until cutover is fully validated:

```text
LOCAL_AGENT_ARCHITECTURE_PLAN.md
WATCHER_FIRST_SYNTHESIS.md
PLAN_REVIEW_APPENDIX.md
V2_IMPLEMENTATION_AUDIT.md
ANTIGRAVITY_ADAPTER_SPIKE_REPORT.md
```

## Phase 6: Add Cutover Guard Tests

Add small tests under `v2_tests/` or a new `v2_tests/test_cutover.py`.

Test 1: no v1 code paths remain.

Check that these paths do not exist:

```text
council/
main.py
loop_orchestrator.py
setup_api.py
export.py
project_context.py
scout.py
explorer.py
```

Test 2: no external model council settings remain in user-facing config.

Forbidden tokens:

```text
LLM_PROVIDER
LITELLM_API_KEY
LITELLM_API_BASE
OPENROUTER_API_KEY
DEFAULT_COUNCIL_MODELS
DEFAULT_CHAIRMAN_MODEL
```

Scope:

```text
README.md
.env.example
MAW.command
start.sh
v2/
v2_templates/
```

Allow old tokens only inside archived v1 docs if the user explicitly decides to keep archives.

Validation:

```bash
uv run python -m unittest discover -s v2_tests -q
```

## Phase 7: Final V2 Smoke

Run a minimal local workflow smoke:

```bash
tmp=$(mktemp -d)
uv run python -m v2.app create --target "$tmp" --request "test v2 cutover"
uv run python -m v2.app watch --target "$tmp" --once
uv run python -m v2.app status --target "$tmp" --workflow-id workflow_001 --verbose
uv run python -m unittest discover -s v2_tests -q
```

Also inspect generated target files:

```bash
find "$tmp" -maxdepth 4 -type f | sort
```

Expected:

- `AGENTS.md` exists in target.
- `TEAM_RULES.md` exists in target.
- `MAW_workflow/workflows/workflow_001/manifest.json` exists.
- `request.md` exists.
- v2 watcher can inspect or advance without v1.

## Git Hygiene

Before staging:

```bash
git status --short
git diff --stat
git diff --name-status
```

Do not accidentally stage unrelated local files or generated runtime files.

Recommended commit message:

```text
chore: cut over MAW to v2 and remove v1 council engine
```

## Final Report Format

Report back with:

```text
Summary:
- What was switched to v2
- What v1 files were deleted
- What docs/config were updated
- What dependencies were removed

Validation:
- Exact commands run
- Exact test result
- Any skipped tests and why

Residual Risk:
- Any v1 references intentionally kept
- Any v2 UI/launcher limitations
- Any follow-up needed
```

## Stop Conditions

Stop and ask before continuing if:

1. A v2 test fails after deleting v1.
2. A file appears shared by v1 and v2 and its ownership is unclear.
3. A dependency appears unused by v2 but required by a real adapter.
4. `MAW.command` cannot provide a useful v2 user flow.
5. Removing a document would erase the only clear v2 specification.

## Success Definition

Cutover is complete when all are true:

```text
✓ MAW.command no longer launches v1
✓ start.sh no longer launches v1
✓ README describes v2 only
✓ .env.example has no external model council keys
✓ v1 runtime files are removed
✓ v1 tests are removed or ported to v2
✓ pyproject dependencies match v2 needs
✓ v2_tests pass
✓ v2 CLI smoke passes
✓ repo no longer contains active LiteLLM/OpenRouter/Direct council surface
✓ no legacy dual-entrypoint remains
```
