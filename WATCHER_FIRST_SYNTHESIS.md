# Watcher-First 綜合建議（TEAM_RULES + watcher 路線）

> **Version**: 1.0  
> **Audience**: 實作 AI / 人類架構師  
> **Inputs reviewed**:
> - `~/.gemini/antigravity/brain/.../implementation_plan.md`（v2 實作分期）
> - `PLAN_REVIEW_APPENDIX.md`（v2 缺口與風險）
> - `LOCAL_AGENT_ARCHITECTURE_PLAN.md` v2.0（正式改造方案）
> - 使用者口述：另一專案僅用 **`TEAM_RULES.md` + `watcher.py`** 即完成 agents 協作

---

## 0. Executive decision

**採用 Watcher-First，拒絕 Orchestrator-First。**

| 路線 | 核心 | 結論 |
|------|------|------|
| v1 MAW | `loop_orchestrator.py` 內嵌 Council API + Context Pack + spawn | 退役 |
| v1.1 草案 | Hub 依序 `_run_subprocess` + `stage*.md` | 方向對，但仍綁 v1 程式樹 |
| **v2 正解** | **`TEAM_RULES.md` 定義行為 + `watcher.py` 看檔喚人 + `adapters/` 啟動 Agent** | **採用** |

MAW 的新本質：

```text
MAW = scaffold + UI + adapter registry + optional event notify
Watcher = 唯一編排大腦（狀態機 + dispatch + lock + debounce）
Agent = 讀規則與 instruction 檔，寫 artifact 檔，自行讀 target repo
```

**Invariant（不可違反）**：

1. Agent 之間 **零網路 IPC**（無 WS client、無 SDK、無 meeting room）。
2. MAW Hub **不讀 target 原始碼**（可提供路徑提示，不打包內容）。
3. 完成判定 = **expected artifact set** 全滿足 + 非空 + 無活躍 `.tmp`（見 §4）。
4. v2 **不得 import v1**（`council/`、`project_context.py`、`loop_orchestrator.py`）。
5. **真實 Agent adapter 未驗證前，禁止刪 v1**（`PLAN_REVIEW_APPENDIX` §B）。

---

## 1. 三份文件對照與修正

### 1.1 文件角色

| 文件 | 用途 | 可信度 |
|------|------|--------|
| `LOCAL_AGENT_ARCHITECTURE_PLAN.md` v2 | 產品與 schema 規格（North Star、目錄、狀態） | 高 — 但 §0「已具備 watcher」**錯誤** |
| `implementation_plan.md`（Antigravity） | 工程分期：`v2/` 隔離、Phase 0–6、測試清單 | 高 — 可直接當 sprint backlog |
| `PLAN_REVIEW_APPENDIX.md` | 風險補強：adapter spike、artifact 聚合、回退條款 | **必讀約束** — 填 v2 盲點 |

### 1.2 必須修正 v2.0 §0 的錯誤前提

`PLAN_REVIEW_APPENDIX` §A 已證實：

| v2.0 聲稱 | 實際 |
|-----------|------|
| MAW 已有 `watcher.py` | **不存在**；v1 是 `loop_orchestrator.py` |
| `adapters/` 可喚醒真實 Agent | 多為 **mock template** |
| registry 已支援 Chair/Planner | **僅 executor/reviewer** |

**修正文案**（寫入 v2.0 下一版 §0）：

> v2 不是瘦身舊 watcher，而是把使用者已在其他專案驗證過的 **TEAM_RULES + watcher 模式**，移植進 MAW repo 的 `v2/watcher.py`。

### 1.3 Antigravity `implementation_plan.md` 與 v2.0 的差異（需合併）

| 主題 | implementation_plan | v2.0 LOCAL_AGENT | 建議採用 |
|------|---------------------|------------------|----------|
| 評論模式 `full/linear/chair/none` | ✅ 明確 | v2.0 預設 N×(N-1) | **採用 implementation_plan**；預設改 `chair` 或 `linear` |
| Event endpoint `:47821` | 提及 | 未強制 | **可選**；MVP 可只用 `events.jsonl` |
| 雙層狀態 Phase+Sub-state | plan_review 建議 | 16 扁平狀態 | **採用雙層**（implementation_plan Phase 1） |
| `v2/` 隔離目錄 | ✅ | ✅ | 一致 |

---

## 2. 極簡核心：復刻「TEAM_RULES + watcher」成功模式

使用者在其他專案的成功要素可抽象為 **兩個檔案 + 一個迴圈**：

### 2.1 `TEAM_RULES.md`（規範層 — 給 Agent 讀）

**職責**：定義角色、讀寫哪些檔、禁止什麼、完成定義。  
**不是**：程式碼、狀態機、API。

最小章節：

```markdown
# TEAM_RULES
## Roles: chair | planner_<seat> | executor | reviewer
## Read order: request → chair_brief → proposals → ...
## Write contract: only paths listed in instructions/<role>_<seat>.md
## Completion: write to *.tmp then rename; file must be non-empty
## Safety: do not read .env; do not commit without COMMITTING phase
```

Scaffold 時從 `v2_templates/TEAM_RULES.md` 複製到 target；**若已存在則 merge 而非覆蓋**（implementation_plan Open Question #2）。

### 2.2 `watcher.py`（編排層 — 唯一狀態機）

**職責**：

```python
while True:
    state = read_json("WORKFLOW_STATE.json")
    expected = compute_expected_artifacts(manifest, state)
    if all_artifacts_ready(expected):
        transition(state)
    else:
        missing = expected - present
        for task in dispatchable(missing, locks):
            adapter.dispatch(task)  # spawn CLI or emit handoff
    sleep_or_watchdog_debounce()
```

**不是**：LLM、讀 repo、打包 context、WebSocket 給 Agent。

建議 LOC 預算：**300–600 行**（含 debounce、lock、dispatch）。超過 800 行即過度設計信號。

### 2.3 `adapters/`（啟動層 — 把「輪到你」變成可執行動作）

每個 registry entry 必須宣告：

```json
{
  "id": "grok_build",
  "trigger_mode": "auto_cli | manual_guide | already_running",
  "spawn": ["python3", "{workflow}/adapters/trigger_grok.py"],
  "requires_running_app": false,
  "parallel_capacity": 1
}
```

| trigger_mode | 行為 |
|--------------|------|
| `auto_cli` | watcher `Popen`，等 exit 0，再驗證 artifact |
| `manual_guide` | 寫 `instructions/planner_a.md` + UI 通知「請在 Grok 執行」；watcher 只等檔案出現 |
| `already_running` | 透過既有 inbox/hook（**Phase 3.5 才實作**） |

**MVP 只做 `auto_cli`（mock script）+ `manual_guide`（檔案+UI）**，不要假設所有 GUI Agent 可全自動。

---

## 3. 與 v1 後半段 adaptor 的關係（回答「為何不用新 WebSocket」）

v1 後半段已驗證的模式：

```text
loop_orchestrator._spawn_executor → trigger_executor.py
  → 讀寫 MAW_workflow/TASKS、AGENT_STATE.md
  → Hub 輪詢 / 等 exit
/ws/maw → 僅 UI 看 stdout
```

v2 **應將前半段 Council 接入同一哲學**：

| 階段 | v1（錯） | v2（對） |
|------|----------|----------|
| Council | API + context pack | `proposals/*.md` + `comments/*.md` + `final_plan.md` |
| 編排 | `loop_orchestrator` 內嵌 | **`watcher.py`** |
| Agent 通訊 | N/A | **檔案** |
| UI 即時性 | WS log | **仍用 WS log**（可選：輪詢 `events.jsonl`） |

**不要**為 Council 新增 Agent-facing WebSocket。  
**不要**把 `loop_orchestrator` 搬進 v2 改名 watcher——邏輯應從檔案契約重寫。

---

## 4. 關鍵實作約束（來自 PLAN_REVIEW_APPENDIX，執行前硬性採納）

### 4.1 Expected artifact set（聚合完成）

Watcher **禁止**用「目錄裡有幾個 .md」判斷完成。

```python
@dataclass
class DispatchTask:
    dispatch_key: str          # e.g. "proposal:planner_a"
    role: str
    seat: str
    instruction_path: Path
    expected_output: Path
    status: Literal["pending","dispatched","completed","failed","timeout"]
```

狀態轉移條件（全部 AND）：

1. `manifest.expected_tasks` 中每個 `completed` 的 `expected_output` 存在。
2. 檔案 size > 0。
3. 同路徑無 `.tmp` 殘留或 tmp mtime 已穩定（debounce 2s）。
4. `resolve(expected_output).is_relative_to(workflow_root)` — 防 symlink escape。
5. dispatch 的 subprocess 已 exit 0（`auto_cli` 模式）。

### 4.2 Phase 3.5：Real adapter spike（Gate — 擋 v1 刪除）

在 Phase 3（mock E2E）與 Phase 4（UI）之間 **強制插入**：

```text
Phase 3.5 PASS 條件：
  - 1 個真實 Agent 完成 Chair 或 Planner（非 mock 寫檔）
  - 1 個真實 Agent 完成 Executor 或 Reviewer
  - watcher 重啟不重複 dispatch
  - timeout / fail 不誤標 completed
```

FAIL → v2 留分支，**v1 繼續維護**，不切 main。

### 4.3 Token 策略：調查起點，非 context pack

`chair_brief.md` 可含 **Suggested starting points**（僅路徑與命令，無原始碼）：

```markdown
## Suggested starting points
- main.py
- loop_orchestrator.py
- adapters/registry.json
- Run: `uv run pytest -q`
```

MAW 不掃描 repo 填入此區塊（Chair Agent 自行決定）；MAW 可提供使用者手動填寫的 `request.md` 附件路徑列表。

### 4.4 評論模式預設

| 模式 | 評論次數（N=3） | 建議預設 |
|------|-----------------|----------|
| `full` | 6 | 否 |
| `linear` | 3 | 可選 |
| `chair` | 0（Chair 直接對比 proposals） | **推薦預設** |
| `none` | 0 | 小型任務 |

`manifest.json` 欄位：`"comment_mode": "chair"`。

---

## 5. 最小可行 v2 檔案集（MVP scope）

首個可跑通 commit 只需：

```text
v2/
  watcher.py          # 狀態機 + watchdog + dispatch
  workflow.py         # transitions + expected artifact calculator
  dispatcher.py       # adapter 呼叫
  files.py            # atomic write helpers
  app.py              # 可選：靜態 UI + WS 轉發 watcher 日誌
v2_templates/
  TEAM_RULES.md
  AGENTS.md
  MAW_workflow/workflows/_template/
v2_tests/
  test_transitions.py
  test_expected_artifacts.py
  test_watcher_recovery.py
v2_smoke_test.py
```

**刻意不做（MVP）**：

- 47821 event server（用 `events.jsonl` 即可）
- 16 狀態 UI 動畫
- 多專案 `targets.json`（單一 `TARGET_PROJECT_PATH` 即可）
- `git_ops.py`（Executor Agent 負責 commit，watcher 只驗證 `commit.md`）

---

## 6. 實施順序（合併 implementation_plan + 本建議）

```text
Phase 0   v2/ 骨架 + import-linter 禁 v1
Phase 1   manifest + WORKFLOW_STATE + workflow.py + expected artifact 單元測
Phase 2   watcher.py（watchdog debounce）+ mock auto_cli adapters
Phase 3   v2_smoke_test：chair → planners → final_plan → approve → exec → review（全 mock）
Phase 3.5 ★ 真實 Agent spike（擋刀）
Phase 4   極簡 UI：roster、狀態、批准鈕、manual_guide 提示
Phase 5   真實 E2E 到 commit
Phase 6   刪 v1 整批 + v2 提升根目錄
```

---

## 7. Anti-patterns（實作 AI 遇到時應拒絕）

| Anti-pattern | 為何錯 | 正確做法 |
|--------------|--------|----------|
| 在 watcher 內 `import loop_orchestrator` | 拖回 v1 複雜度 | 重寫 transition 表 |
| Agent 連 `/ws/maw` | 兩套通訊 | 只寫檔案 |
| Hub 讀取 target 原始碼塞 instruction | 回到 context pack | 路徑提示 only |
| 用 `listdir` 計數當完成 | race / 空檔 | expected artifact set |
| mock E2E 通過就刪 v1 | 產品未驗證 | Phase 3.5 Gate |
| 預設 `comment_mode=full` | 單機 N=4 太慢 | 預設 `chair` |
| v2 與 v1 雙軌長期並存 | 維護爆炸 | Phase 6 一次性切換 |

---

## 8. 給實作 AI 的單頁 prompt（可直接貼到下一個 session）

```text
Build MAW v2 as Watcher-First coordinator.

Sources of truth:
- LOCAL_AGENT_ARCHITECTURE_PLAN.md v2 (schema + directories)
- implementation_plan.md (phased delivery in v2/)
- WATCHER_FIRST_SYNTHESIS.md (this file — invariants win on conflict)
- PLAN_REVIEW_APPENDIX.md (hard gates)

Do NOT:
- import v1 modules
- add Agent WebSocket clients
- read target source into MAW
- delete v1 before Phase 3.5 real adapter spike passes

DO:
- TEAM_RULES.md + watcher.py + file artifacts under MAW_workflow/workflows/workflow_NNN/
- adapters with trigger_mode auto_cli | manual_guide
- expected artifact set completion checks
- default comment_mode chair
- v2_smoke_test.py before any UI work

First deliverable: Phase 1 tests green for workflow transitions + expected artifacts.
```

---

## 9. 結論

使用者直覺正確：**成功的多 Agent 協作不必是「中央 AI 引擎」，而是「團隊規則 + 看檔的看守者」**。

`LOCAL_AGENT_ARCHITECTURE_PLAN` v2.0 的 schema 與目錄設計可保留；  
`implementation_plan.md` 的分期可執行；  
`PLAN_REVIEW_APPENDIX.md` 的風險條款必須變成 **CI/人工 Gate**。

三者合一後的最短路徑：

> **先把 `v2/watcher.py` 做對，再談 UI；先把真實 adapter 做通，再刪 v1。**

---

*文件位置：`MAW/WATCHER_FIRST_SYNTHESIS.md` — 建議與 v2.0 計畫並讀，衝突時以本檔 §0 Invariants 為準。*