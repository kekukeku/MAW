# V2 Cutover Plan — Agent Review Supplement

## Document Role

| Field | Value |
|-------|-------|
| Base plan | `V2_CUTOVER_PLAN_FOR_GROK_BUILD.md` |
| This file | Delta corrections + missing items for the base plan |
| Verdict | **EXECUTE base plan WITH this supplement** |
| Review date | 2026-06-30 |
| Reviewer | Grok Build |
| Baseline verified | `uv run python -m unittest discover -s v2_tests -q` → 122 passed, 1 skipped |

**Rule for agents:** Treat base plan phases as authoritative order. Apply every `DELTA` block in this file before or during the matching phase. Do not skip deltas.

---

## Verified Facts (do not re-investigate unless baseline fails)

```yaml
v2_runtime_deps: []  # v2/ and v2_tests/ use stdlib only
v2_entrypoint: "uv run python -m v2.app"
v2_adapters_registered: [mock, antigravity]
v2_default_cli_agents: codex  # BROKEN: codex is not a registered adapter
v1_entrypoints_still_active:
  - MAW.command  # uvicorn main:app + open http://127.0.0.1:8002
  - start.sh     # uvicorn main:app
  - install.command  # cp .env.example, mkdir ~/.agent-cowork, exec MAW.command
v2_import_isolation_test: v2_tests/test_gate.py::TestV2ImportIsolation
v2_scaffold_source: v2_templates/  # NOT template_target_project/
v2_adapter_system: v2/dispatcher.py + v2/adapters/  # NOT root adapters/
```

---

## Critical Naming Trap

```text
DELETE:  adapters/           # root — v1 Panel 0 installer (tracked in git)
KEEP:    v2/adapters/        # v2 placeholder package (currently empty __init__.py)
KEEP:    v2/dispatcher.py    # v2 adapter registry (mock, antigravity)
```

Deleting `v2/adapters/` is a **cutover failure**.

---

## Phase 1 DELTA — Entrypoints & Config

### ADD to Phase 1 file list

```text
install.command
```

### MODIFY install.command — required behavior after cutover

```yaml
remove:
  - "cp .env.example .env on first install"        # optional: keep only if .env.example is v2-clean first
  - "mkdir -p ~/.agent-cowork"                   # v1-only concept
  - "exec ./MAW.command" if MAW.command still launches v1
replace_with:
  - uv sync
  - chmod +x MAW.command install.command
  - icon setup (keep existing clang block + static/*.png)
  - launch v2 flow (see MAW.command below)
```

### MODIFY MAW.command — required behavior

```yaml
forbidden:
  - 'uvicorn main:app'
  - 'open "http://127.0.0.1:8002"'
acceptable_interim:
  - print v2 quick-start commands
  - optionally read TARGET_PROJECT_PATH from env and pass as --target
preferred:
  - interactive or scripted v2 launcher using TARGET_PROJECT_PATH if set
```

### REPLACE .env.example — v2-only variables

Remove ALL v1 council / LLM / provider keys listed in base plan Phase 1.

**Do NOT keep these in .env.example** (v2 code does not read them):

```text
MAW_MOCK_MODE        # v1 only (council/config.py); v2 uses --chair mock etc.
MAW_HOST             # v2 has no HTTP server
MAW_PORT             # v2 has no HTTP server
TARGET_PROJECT_PATH  # v2 CLI requires --target per command UNLESS MAW.command wrapper reads it
```

**Keep only env vars v2 actually reads** (confirmed in v2/watcher.py):

```text
WATCHER_POLL_INTERVAL=3
AGENT_TIMEOUT_SECONDS=600
MAX_AGENT_RETRIES=2
```

Note: base plan says `MAX_AGENT_RETRIES=3` but code default is `2`. Use `2` unless you change v2/watcher.py.

**Optional wrapper-only variable** (add only if MAW.command implements it):

```text
TARGET_PROJECT_PATH=
```

Document in README: if unset, user must pass `--target` on every `v2.app` subcommand.

### Phase 1 validation — ADD

```bash
grep -n "uvicorn\|main:app\|agent-cowork" install.command MAW.command start.sh
# expected: no matches
```

---

## Phase 2 DELTA — V1 Runtime Deletion

### ADD to Phase 2 delete list (base plan missed these; all git-tracked)

```text
maw_paths.py
adapters/
template_target_project/
```

### Dependency graph (why these are v1-only)

```yaml
maw_paths.py:
  used_by: [main.py, setup_api.py, export.py, loop_orchestrator.py, scout.py, explorer.py, adapters/installer.py]
  used_by_v2: false

adapters/:
  purpose: v1 Panel 0 agent installer + registry.json + executor/reviewer templates
  v2_replacement: v2/dispatcher.py

template_target_project/:
  purpose: v1 scaffold for Panel 0 / smoke_test / v1 tests
  v2_replacement: v2_templates/ + v2/files.py::scaffold_target()
```

### KEEP explicitly (do not delete with adapters/)

```text
v2/adapters/__init__.py
v2/run_spike.py          # real adapter spike tool; referenced in ANTIGRAVITY_ADAPTER_SPIKE_REPORT.md
v2/git_ops.py
v2/ui/__init__.py        # empty shell; future UI
static/main_app_icon.png
static/installer_icon.png
```

### Local cleanup (not git-tracked; safe to delete)

```text
data/                    # v1 runtime; already in .gitignore
```

### Phase 2 validation — EXTEND rg command

Base plan rg pattern is insufficient. Use:

```bash
rg -n "main:app|loop_orchestrator|from council|import council|maw_paths|adapters\.installer|template_target_project|LLM_PROVIDER|OPENROUTER|LITELLM" \
  --glob '!docs/archive-v1/**' \
  --glob '!V2_CUTOVER*.md' \
  .
```

Expected after Phase 2: matches only in archived docs (if any) or this review file.

---

## Phase 3 DELTA — V1 Tests

Base plan delete list is **complete**. No additions needed.

### Port rule (before delete)

```yaml
test_safety.py: DELETE — v1 orchestrator/council only; v2 executor lock coverage exists in v2_tests/test_gate.py
test_adapters.py: DELETE — tests root adapters/installer.py (v1)
```

Do NOT port v1 FastAPI, council, context_pack, scout, explorer tests to v2_tests.

---

## Phase 4 DELTA — Dependencies

### Confirmed: remove ALL runtime dependencies

```yaml
pyproject.toml dependencies to_remove:
  - fastapi
  - httpx
  - pydantic
  - python-dotenv
  - uvicorn[standard]
  - websockets

pyproject.toml optional dev:
  - pytest  # v2 uses unittest; safe to remove or keep for future
```

After removal:

```bash
uv sync
uv run python -m unittest discover -s v2_tests -q
uv run python -m v2.app --help
```

Expected: tests still 122 passed, 1 skipped.

---

## Phase 5 DELTA — Documents

### ADD to archive-or-delete list (base plan missed)

```text
CONTEXT_RELIABILITY_PLAN.md
```

### ADD to keep list (base plan missed)

```text
V2_PHASE_2_5_AND_SPIKE_AUDIT.md
V2_CUTOVER_PLAN_FOR_GROK_BUILD.md
V2_CUTOVER_PLAN_REVIEW_FOR_AGENTS.md
```

### Spike / instruction docs — agent decision tree

```yaml
keep_as_v2_history:
  - ANTIGRAVITY_ADAPTER_SPIKE_REPORT.md
  - ANTIGRAVITY_*_INSTRUCTIONS.md
  - OPENWORK_*_INSTRUCTIONS.md
  - OPENWORK_*_REPORT.md
archive_or_delete_if_clean_repo_preferred:
  - any doc referencing Panel 0, uvicorn main:app, LiteLLM, council, context_pack
```

User preference from base plan: **prefer deletion** for clean v2-only repo. When in doubt, move to `docs/archive-v1/` instead of keeping at root.

---

## Phase 6 DELTA — Guard Tests

Base plan proposes `v2_tests/test_cutover.py`. **Extend** rather than duplicate.

### Test A — file existence (ADD paths base plan missed)

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
    "adapters",              # root only — check Path("adapters").is_dir()
    "template_target_project",
]
```

### Test B — forbidden tokens in active surfaces

Scope (same as base plan plus install.command):

```text
README.md
.env.example
MAW.command
install.command
start.sh
v2/
v2_templates/
pyproject.toml
```

Forbidden tokens (same as base plan). Allow matches only under `docs/archive-v1/`.

### Test C — v2 adapter path must exist

```python
REQUIRED_PATHS = [
    "v2/dispatcher.py",
    "v2/adapters/__init__.py",
    "v2_templates/AGENTS.md",
    "v2_templates/TEAM_RULES.md",
]
```

### Existing coverage — do not remove

`v2_tests/test_gate.py::TestV2ImportIsolation` already guards v2→v1 imports. Keep it.

---

## Phase 7 DELTA — Smoke Test (FIX REQUIRED)

Base plan smoke **will fail** because `v2.app create` defaults to agent `codex`, but registry only has `mock` and `antigravity`.

### REPLACE Phase 7 commands with

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

### Expected artifacts

```text
$tmp/AGENTS.md
$tmp/TEAM_RULES.md
$tmp/MAW_workflow/workflows/workflow_001/manifest.json
$tmp/MAW_workflow/workflows/workflow_001/request.md
```

### Smoke pass criteria

```yaml
create: exit 0
watch --once: exit 0; no "No adapter for agent"
status --verbose: shows workflow_001
v2_tests: 122 passed, 1 skipped
```

---

## Execution Checklist (merged)

Use this as the single agent todo list. Check each box only after its validation passes.

```text
[ ] 0. Baseline: git status, uv sync, v2_tests 122 OK, v2.app --help, v2.app adapters
[ ] 1. Phase 1: MAW.command, install.command, start.sh, README.md, .env.example → v2 only
[ ] 1v. grep entrypoints: no uvicorn, no main:app, no agent-cowork in launchers
[ ] 2. Phase 2: delete base plan list PLUS maw_paths.py, adapters/, template_target_project/
[ ] 2v. rg extended pattern: no active v1 references outside archive
[ ] 2v. v2_tests still pass
[ ] 3. Phase 3: delete all v1 test_*.py at repo root
[ ] 3v. v2_tests still pass
[ ] 4. Phase 4: strip pyproject runtime deps, uv sync
[ ] 4v. v2_tests still pass
[ ] 5. Phase 5: archive/delete v1 docs per lists above
[ ] 6. Phase 6: add/extend test_cutover.py guard tests
[ ] 6v. v2_tests still pass (count may increase)
[ ] 7. Phase 7: smoke with --chair mock --planners mock --executor mock --reviewer mock
[ ] 8. git diff review: no accidental deletion of v2/, v2_templates/, v2_tests/, v2/adapters/
[ ] 9. commit: chore: cut over MAW to v2 and remove v1 council engine
```

---

## Stop Conditions (ADD to base plan)

Stop and report to user if:

```yaml
- v2_tests fail after any phase
- rg finds main:app or uvicorn in MAW.command, install.command, or start.sh after Phase 1
- root adapters/ deleted but v2/adapters/ also missing
- pyproject still lists fastapi or uvicorn after Phase 4
- Phase 7 smoke shows "No adapter for agent 'codex'"
```

---

## Known Non-Blockers (inform user, do not block cutover)

```yaml
ux_regression:
  description: v2/ui/ is empty; MAW.command goes from Panel 0 web UI to CLI
  action: acceptable for cutover; track as follow-up

adapter_gap:
  description: only mock + antigravity registered; codex/openwork/grok_build not yet v2 adapters
  action: document in README; use --chair mock for local smoke

multi_project:
  description: v1 used ~/.agent-cowork/targets.json; v2 uses per-command --target
  action: README must explain --target; optional TARGET_PROJECT_PATH wrapper in MAW.command
```

---

## Success Definition (strict superset of base plan)

Cutover is complete when ALL true:

```text
✓ MAW.command does not launch v1
✓ install.command does not launch v1 or seed v1-only config paths
✓ start.sh does not launch v1
✓ README describes v2 only
✓ .env.example has no external model council keys
✓ .env.example has no MAW_HOST/MAW_PORT/MAW_MOCK_MODE unless wrapper implements them
✓ maw_paths.py deleted
✓ root adapters/ deleted
✓ template_target_project/ deleted
✓ v2/adapters/ still exists
✓ v1 runtime files deleted (base plan list + deltas)
✓ v1 tests deleted
✓ pyproject has zero v1 runtime dependencies
✓ v2_tests pass
✓ Phase 7 smoke passes with mock agents
✓ guard tests pass
✓ no dual entrypoint remains
```

---

## Final Report Template (for executing agent)

```text
Summary:
- Entrypoints switched: [list files]
- V1 deleted: [count + key paths including maw_paths, adapters/, template_target_project]
- Docs updated/archived: [list]
- Dependencies removed: [list]

Validation:
- Commands: [exact commands run]
- v2_tests: [N passed, M skipped]
- Phase 7 smoke: [pass/fail + workflow_id]
- rg residual: [none | list]

Residual Risk:
- UX: CLI-only until v2/ui built
- Adapters: mock/antigravity only unless new adapters added
- [any other]
```