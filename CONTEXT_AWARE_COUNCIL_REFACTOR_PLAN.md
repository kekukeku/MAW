# MAW Context-Aware Council 大改造計劃

> **版本**：0.1  
> **狀態**：架構改造計劃  
> **目標階段**：Phase 6 - Context-Aware Council  
> **核心原則**：盡量不改變使用者感受到的 UI/UX 流程，但徹底修正 Council 盲眼決策問題。

---

## 1. 問題定義

目前 MAW 的工作流表面上已經形成完整閉環：

```text
使用者輸入任務
  -> AI Council 三階段討論
  -> 人工審批 Gate #1
  -> 匯出 TASK / PLANNING
  -> Executor 實作
  -> Reviewer 審查
  -> Gate #2
  -> commit / merge / final report
```

但 Council 階段存在一個架構層級缺陷：

**Council 只收到使用者 prompt，沒有收到目標專案的真實上下文。**

目前 `start_council()` 會驗證 target project 是否符合 MAW contract，但驗證結果沒有轉換成 Council 可用的 context。`run_council()` 實際送給各模型的內容仍然只是使用者的自然語言需求。

這造成三個後果：

1. **Stage 1 是通用猜測**：模型不知道專案結構、技術棧、實際檔案與既有約束。
2. **Stage 2 是對猜測排序**：匿名評分看似嚴謹，但評的是沒有 repo 依據的方案。
3. **Stage 3 會把猜測正式化**：Chairman synthesis 進入 `TASKS/task_NNN.md`，Executor 後面只能重新摸索。

所以核心修正不是「把 README 塞進 prompt」而已，而是建立一個正式的：

```text
Target Project Context Contract
```

讓 Council 的每一份決策都能回答：

- 它看過什麼？
- 它沒看過什麼？
- 它依據哪些檔案或摘要推論？
- 如果資訊不足，它是否有明確承認？
- 匯出給 Executor 和 Reviewer 時，這份依據是否可審計？

---

## 2. 改造目標

### 2.1 主要目標

將 MAW 從：

```text
Prompt-only Council
```

升級為：

```text
Context-Aware Council
```

也就是在不明顯改變使用者啟動任務流程的前提下，讓 Council 在開會前自動取得目標專案的分層上下文，並把 context provenance 保存到 conversation、PLANNING 與 TASK 輸出中。

### 2.2 使用者體感目標

現有 UI/UX 的主要感覺要保留：

- 使用者仍然在同一個 Panel 1 輸入任務。
- 使用者仍然按同一個「Start Council / 發起圓桌會議」動作。
- 仍然保留現有模型選擇、Chairman 選擇、review policy 與 gate 流程。
- Gate #1 仍然是審批 Council plan，而不是多一個強制前置表單。

新增功能應該表現為「輕量輔助」：

- Start Council 前顯示 context 預覽。
- 可以展開查看 Council 將看到哪些檔案。
- 可以手動附加檔案，但不是每次都強迫使用者操作。
- 自動掃描失敗時以清楚錯誤提示阻止盲眼 Council，而不是悄悄退回舊模式。

### 2.3 非目標

本次改造不做以下事情：

- 不重做整個 UI layout。
- 不把 MAW 變成完整 IDE。
- 不在第一階段引入重型 embedding database。
- 不要求 Explorer Agent 在 MVP 就能理解整個 monorepo。
- 不改變 Executor / Reviewer 仍由目標專案 adapter scripts 負責的原則。
- 不讓 Council 直接修改目標專案檔案；Council 階段必須唯讀。

---

## 3. 設計原則

### 3.1 Context 是 Council 啟動前置條件

未來 Council 不應存在「沒有任何 target context」的正常模式。

最低要求：

- target project contract 驗證通過。
- L0 Project Blueprint 成功產生。
- conversation metadata 記錄 context pack。

若 L0 context 產生失敗，Council 應進入 `FAILED` 或 `CONTEXT_FAILED`，而不是退回 prompt-only 模式。

### 3.2 Context 分層，不一次塞整個 repo

Context pack 採分層設計：

```text
L0 Blueprint       自動、永遠有
L1 User Files      使用者手動指定
L2 Scout Files     系統自動推薦
L3 Explorer Brief  未來深度調研
```

每層都有明確 token/字元預算、排序與截斷規則。

### 3.3 可審計比「看起來聰明」更重要

每一次 Council 都要留下 context provenance：

- 檔案清單
- 每個檔案的來源層級
- 是否完整讀取或被截斷
- 字元數 / token 估算
- 排除原因
- 內容 hash
- context pack 版本

這樣 Gate #1 的審查者、Executor、Reviewer 以及未來回溯 debug 都能知道 Council 的依據。

### 3.4 UI 流程不變，內部狀態變精準

使用者看到的流程盡量保持：

```text
IDLE -> COUNCIL_RUNNING -> COUNCIL_PENDING_APPROVAL
```

內部可以新增細分狀態：

```text
IDLE
  -> CONTEXT_GATHERING
  -> CONTEXT_READY
  -> COUNCIL_RUNNING
  -> COUNCIL_PENDING_APPROVAL
```

但 UI 可以把 `CONTEXT_GATHERING` 顯示為：

```text
圓桌會議準備中：正在讀取目標專案上下文...
```

而不是讓使用者感覺多了一個工作流步驟。

---

## 4. 目標架構

### 4.1 新增模組

```text
MAW/
├── project_context.py              # Context pack 組裝主模組
├── context_scanner.py              # 目錄樹、README、依賴檔、gitignore 排除
├── context_selector.py             # L1 手選檔案驗證與讀取
├── context_scout.py                # L2 自動候選檔案推薦
├── context_budget.py               # 字元/token 預算、截斷、排序
└── tests/
```

若想保持檔案數量較少，MVP 可以先只新增：

```text
project_context.py
```

並在內部拆 helper functions。等功能成熟後再分檔。

### 4.2 Context Pack 資料結構

建議 conversation JSON 中新增：

```json
{
  "context_pack": {
    "version": 1,
    "targetKey": "pixel-agent-desk",
    "targetPath": "/abs/path/to/project",
    "generatedAt": "2026-06-22T00:00:00Z",
    "policy": {
      "respectGitignore": true,
      "excludeSecrets": true,
      "excludeWorkflowDir": true,
      "maxTotalChars": 80000,
      "maxFileChars": 12000
    },
    "summary": {
      "status": "ready",
      "totalChars": 53240,
      "truncated": false,
      "includedFiles": 8,
      "excludedFiles": 241
    },
    "blueprint": {
      "tree": "...",
      "readme": "...",
      "dependencies": [
        {
          "path": "package.json",
          "content": "..."
        }
      ]
    },
    "files": [
      {
        "path": "src/auth/authentication.py",
        "source": "user_selected",
        "reason": "User attached file",
        "chars": 8400,
        "truncated": false,
        "sha256": "..."
      }
    ],
    "accessIssues": [
      {
        "path": "large.log",
        "reason": "excluded_by_size"
      }
    ]
  }
}
```

### 4.3 Council Prompt 結構

`run_council()` 不應直接把 raw user prompt 傳給模型，而是先建立正式 prompt envelope：

```markdown
# Target Project Context

## Context Status
- Context pack version: 1
- Target project: pixel-agent-desk
- Included files: 8
- Truncated: false

## Project Blueprint
[directory tree + README/dependency summary]

## Selected / Scout Files
### File: src/auth/authentication.py
```python
...
```

## Context Boundaries
- You may only make concrete claims based on the provided context.
- If the context is insufficient, explicitly list the missing files or information.
- Do not assume unseen implementation details.
- Produce a plan that names files/functions only when supported by context.

# User Request

[original user prompt]
```

這個 envelope 應該由 `project_context.py` 或 `council/prompt_builder.py` 統一產生，不要散落在 orchestrator 裡。

---

## 5. 分層 Context 設計

### 5.1 L0 Project Blueprint

**狀態**：P0 必做，Council 啟動必備。

內容：

- 目錄樹，排除 `.git`、`node_modules`、`venv`、`.venv`、`dist`、`build`、`coverage`、`__pycache__`、`MAW_workflow`。
- README 前 N 字元。
- 主要依賴檔：
  - `package.json`
  - `pyproject.toml`
  - `requirements.txt`
  - `Cargo.toml`
  - `go.mod`
  - `Gemfile`
  - `pom.xml`
  - `build.gradle`
- 測試與 lint script 摘要。

價值：

- Council 至少知道專案形狀、語言、框架、測試方式。
- 可以避免最粗糙的通用猜測。

限制：

- L0 不足以做精確程式碼修改計劃。
- L0 只能支撐「專案層級判斷」，不能支撐「第幾個函式怎麼改」。

### 5.2 L1 User-Selected Files

**狀態**：P1 黃金方案。

UI 以輕量方式加入：

- 任務輸入框下方新增一行 compact context status。
- 例如：

```text
Context: Blueprint ready · 3 suggested files · Add files
```

互動：

- 使用者點「Add files」才展開檔案選擇器。
- 預設不打斷 Start Council 流程。
- 手選檔案優先級高於 scout 檔案。

後端：

- `NewConversationRequest` 新增 `contextFiles: list[str] = []`。
- 僅允許 target project root 內的相對路徑。
- 禁止 `..`、絕對路徑、symlink 跳出 target root。
- 單檔讀取前做大小、binary、secret 檢查。

### 5.3 L2 Scout Files

**狀態**：P2 自動化層。

先使用便宜、可解釋的方式：

- 從 user prompt 抽取 filename-like token。
- 用 `rg --files` 找檔名匹配。
- 用 `rg` 搜關鍵字命中。
- README/package scripts 命中的入口檔加權。
- 最近修改檔案可作為弱訊號，但不要過度依賴。

排序分數範例：

```text
+100 exact filename match
+70 path component match
+50 symbol/token text match
+30 test file near matched source
+20 dependency/config relevance
-80 generated/vendor/build path
-100 secret or binary candidate
```

輸出：

- top-N 檔案，預設 3 到 8 個。
- UI 顯示為 suggested files，可取消勾選。
- 若使用者不展開 UI，系統可以自動納入 top-N，但 Gate #1 要顯示 Council 看過哪些。

### 5.4 L3 Explorer Brief

**狀態**：P3 長期目標。

Explorer 是唯讀調研 agent，不是 executor。

可做：

- symbol search
- callsite search
- test discovery
- framework route discovery
- 產出 `Context Brief`

不可做：

- 修改檔案
- 安裝依賴
- 跑高風險命令
- 自動把不透明結論當成事實

MVP 不需要 L3。先把 L0/L1/L2 做好。

---

## 6. UX 保持策略

### 6.1 使用者主流程不變

現有主流程：

```text
填任務 -> 選模型/策略 -> Start Council -> 等待結果 -> Gate #1 審批
```

改造後仍維持：

```text
填任務 -> 選模型/策略 -> Start Council -> 等待結果 -> Gate #1 審批
```

差別只在 Start Council 後，系統內部先做 context gathering。

### 6.2 UI 最小新增元素

在 Panel 1 任務輸入框下方新增一個 compact context bar：

```text
Context: Blueprint ready · 3 suggested files · 42K chars
[Preview] [Add files] [Auto-detect]
```

狀態：

- `Not scanned yet`
- `Scanning...`
- `Blueprint ready`
- `Files attached`
- `Context too large`
- `Context failed`

不要新增大型 wizard，不要拆成多頁。

### 6.3 Gate #1 增加 Context 摘要

Council result review panel 中新增一個可折疊區塊：

```text
Council Context
- Blueprint: included
- Files read: 5
- Truncated: 1
- Excluded secrets/build/vendor: 219
[Show details]
```

這讓使用者審批時能知道 Council 是否真的看過專案。

### 6.4 Auto-approve 安全限制

若 `auto_approve_council = true`，必須加上 context guard：

- context status 必須是 `ready`
- no fatal access issues
- context pack 至少有 L0
- 若 user request 明確提到某檔名但該檔未找到，禁止 auto approve

否則自動降級為需要 Gate #1 人工審批，並顯示原因。

---

## 7. 狀態機改造

### 7.1 目前狀態

```text
IDLE
  -> COUNCIL_RUNNING
  -> COUNCIL_PENDING_APPROVAL
```

### 7.2 建議內部狀態

```text
IDLE
  -> CONTEXT_GATHERING
  -> COUNCIL_RUNNING
  -> COUNCIL_PENDING_APPROVAL
```

失敗分支：

```text
CONTEXT_GATHERING
  -> FAILED(reason="Context gathering failed: ...")
```

可選新增 enum：

```python
CONTEXT_GATHERING = "CONTEXT_GATHERING"
```

不用新增 `CONTEXT_READY` 狀態也可以，因為 context ready 後立即進 Council。

### 7.3 WebSocket / Logs

新增 log lines：

```text
[context] Scanning target project blueprint...
[context] Included README.md (2000 chars)
[context] Suggested 4 files from prompt keywords
[context] Context pack ready: 52K chars, 6 files
[council] Starting Stage 1 with context pack v1
```

UI 顯示可以仍然是「圓桌會議準備中」。

---

## 8. API 改造

### 8.1 `POST /api/maw/conversations/new`

新增 request fields：

```python
class NewConversationRequest(BaseModel):
    prompt: str
    targetKey: str
    title: Optional[str] = None
    councilModels: Optional[List[str]] = None
    chairmanModel: Optional[str] = None
    reviewPolicy: Optional[ReviewPolicy] = None
    filesAffected: str = "To be determined by council context analysis"
    nonGoals: str = "None specified."
    contextFiles: Optional[List[str]] = None
    autoScoutContext: bool = True
    mock: Optional[bool] = None
```

### 8.2 新增 Context Preview API

```text
POST /api/maw/context/preview
```

用途：

- UI 可在使用者選 target 或輸入 prompt 後預覽 context。
- 不啟動 Council。
- 僅回傳 summary，不一定回傳完整檔案內容。

Request：

```json
{
  "targetKey": "pixel-agent-desk",
  "prompt": "修正 token 過期 bug",
  "contextFiles": ["src/auth/authentication.py"],
  "autoScoutContext": true
}
```

Response：

```json
{
  "status": "ready",
  "summary": {
    "includedFiles": 5,
    "totalChars": 42000,
    "truncated": false
  },
  "files": [
    {
      "path": "src/auth/authentication.py",
      "source": "user_selected",
      "chars": 8400,
      "truncated": false
    }
  ],
  "accessIssues": []
}
```

### 8.3 新增檔案列表 API

```text
GET /api/maw/targets/{targetKey}/files
```

用途：

- 支援手動 context file selector。
- 僅列安全、非排除檔案。
- 回傳相對路徑、大小、類型推測。

---

## 9. Conversation / Export 改造

### 9.1 Conversation JSON

`run_council()` 新增參數：

```python
async def run_council(
    prompt: str,
    context_pack: dict[str, Any] | None = None,
    council_models: list[str] | None = None,
    chairman_model: str | None = None,
    title: str | None = None,
    mock: bool | None = None,
) -> dict[str, Any]:
```

conversation root 新增：

```json
{
  "context_pack": { "...": "..." }
}
```

assistant message metadata 新增：

```json
{
  "metadata": {
    "aggregate_rankings": [],
    "contextPackVersion": 1,
    "contextStatus": "ready",
    "contextSummary": {
      "includedFiles": 5,
      "totalChars": 42000
    }
  }
}
```

### 9.2 Council Markdown

`PLANNING/council_NNN.md` 新增：

```markdown
## 3. Target Project Context

- Context pack version: 1
- Target Project Key: ...
- Included files: ...
- Total chars: ...
- Truncated files: ...

### Files Provided to Council

| Path | Source | Chars | Truncated |
|------|--------|-------|-----------|
| README.md | blueprint | 2000 | yes |
| src/auth/authentication.py | user_selected | 8400 | no |
```

原本 Stage 1/2/3 往後順延。

### 9.3 Task Markdown

`TASKS/task_NNN.md` 的 `Files Affected` 不應再預設：

```text
To be determined by executor after repository inspection
```

改成以下策略：

1. 若 user 明確傳 `filesAffected`，保留。
2. 否則從 context pack 中的 L1/L2 檔案產生候選清單。
3. 若只有 L0，寫：

```markdown
Council had only project blueprint context. Executor must inspect the target files before changing code.
```

但這應被視為較低信心計劃，Gate #1 顯示警示。

---

## 10. 安全規則

### 10.1 排除規則

永遠排除：

```text
.git/
MAW_workflow/
node_modules/
venv/
.venv/
__pycache__/
dist/
build/
coverage/
.next/
.turbo/
*.pyc
*.log
*.sqlite
*.db
*.png
*.jpg
*.jpeg
*.gif
*.pdf
```

疑似 secret 排除：

```text
.env
.env.*
*.pem
*.key
*.p12
*.crt
id_rsa
id_ed25519
credentials.json
service-account*.json
```

### 10.2 Path Safety

所有檔案讀取必須：

- 從 target project root 解析。
- 使用 realpath 驗證仍在 target root 內。
- 拒絕絕對路徑。
- 拒絕 `..` 跳出。
- 對 symlink 做 root containment 檢查。

### 10.3 Read-only Guarantee

Context gathering 階段不得：

- 寫入 target project。
- 執行 package install。
- 執行 formatter。
- 執行測試，除非未來 L3 Explorer 明確取得使用者授權。

---

## 11. Token / 字元預算

MVP 建議先用 char budget，不必先做 tokenizer。

預設：

```text
maxTotalChars: 80000
maxFileChars: 12000
maxTreeChars: 12000
maxReadmeChars: 6000
maxDependencyFileChars: 8000
maxScoutFiles: 6
```

優先級：

```text
1. User-selected files
2. Direct filename matches from prompt
3. Scout source files
4. Nearby tests
5. README / dependency summary
6. Tree
```

截斷策略：

- 手選檔案可截斷，但要在 UI 和 markdown 明確標記。
- 檔案過大時保留 head + tail，中間標記 omitted。
- 不要靜默截斷。

---

## 12. 實作路線圖

### Phase 6a - L0 Blueprint + Council Prompt Injection

目標：

從完全盲眼變成至少知道專案形狀。

工作項：

- 新增 `project_context.py`
- 實作 `build_context_pack(target_key, prompt, context_files=[], auto_scout=False)`
- 產生 L0 blueprint：
  - tree
  - README
  - dependency files
- 在 `LoopOrchestrator._run_council_task()` 先產生 context pack。
- `run_council()` 接收 context pack。
- Stage 1/2/3 prompt 使用 context envelope。
- conversation JSON 保存 context pack。
- `PLANNING/council_NNN.md` 輸出 context summary。

驗收：

- Mock council conversation JSON 含 `context_pack`。
- Live council Stage 1 prompt 不再只有 user prompt。
- target project 沒有 README 也可正常產生 blueprint。
- `MAW_workflow/` 不會被讀進 context。
- context gathering 失敗不會默默 fallback 到 blind council。

### Phase 6b - Context Preview + UI Minimal Bar

目標：

讓使用者在不改變主流程的前提下看到 Council 會看什麼。

工作項：

- 新增 `/api/maw/context/preview`
- Panel 1 新增 compact context bar。
- target 或 prompt 改變時 debounce preview。
- Start Council 時使用 preview 結果或重新產生 context。
- Gate #1 顯示 context summary。

驗收：

- 不使用新功能時，原本 Start Council 體感仍順。
- 使用者可展開查看檔案清單。
- context 太大時有清楚提示。

### Phase 6c - L1 手動 Context Files

目標：

讓使用者能明確指定 Council 必須看的檔案。

工作項：

- 新增 `/api/maw/targets/{targetKey}/files`
- UI 增加 file selector modal / popover。
- `NewConversationRequest.contextFiles`
- path safety 驗證。
- 手選檔案在 budget 中最高優先。

驗收：

- 選定檔案內容進入 Council prompt。
- 選定檔案出現在 conversation JSON 與 council markdown。
- 非法路徑被拒絕。
- 大檔案會標記截斷。

### Phase 6d - L2 Scout 自動推薦

目標：

減少使用者手動選檔成本。

工作項：

- 實作 prompt keyword extraction。
- 實作 filename/path matching。
- 實作 `rg` text matching。
- top-N scoring。
- UI 顯示 suggested files，可取消。

驗收：

- prompt 提到 `authentication.py` 時能命中該檔。
- prompt 提到 `token expiry` 時能找到 auth/token 相關檔案。
- generated/vendor/build 檔案不會被推薦。

### Phase 6e - Auto-approve Context Guard

目標：

避免全自動模式跳過一份 context 不足的 plan。

工作項：

- 若 context status 非 ready，禁止 auto approve。
- 若 prompt 明確提到的檔案不存在，禁止 auto approve。
- 若 context 只有 L0，auto approve 預設禁止，除非 review policy 明確允許。

驗收：

- auto approve 不再對 context-insufficient council 生效。
- UI / logs 顯示降級原因。

### Phase 6f - Explorer Brief Prototype

目標：

為大型專案提供深度調研，但不阻塞 MVP。

工作項：

- 唯讀 Explorer command set。
- `Context Brief` schema。
- 可選啟用。
- 超時與失敗 fallback。

驗收：

- Explorer 不寫入 target repo。
- Explorer 失敗不破壞 L0/L1/L2 council。

---

## 13. 測試計劃

### 13.1 Unit Tests

新增測試：

- `test_project_context.py`
- `test_context_budget.py`
- `test_context_scout.py`

覆蓋：

- excludes `.git`, `MAW_workflow`, `node_modules`
- respects `.gitignore`
- rejects path traversal
- rejects absolute paths
- truncates large files
- preserves user-selected priority
- produces stable context schema

### 13.2 Council Tests

更新：

- `test_council.py`
- `test_orchestrator.py`
- `test_e2e_workflow.py`

覆蓋：

- mock council conversation includes context pack。
- `_run_council_live()` prompt contains Target Project Context。
- Stage 2 prompt includes original context-aware request, not raw prompt only。
- Stage 3 synthesis includes context-aware deliberation。

### 13.3 Export Tests

更新：

- `test_export.py`

覆蓋：

- council markdown includes context summary。
- council JSON includes context pack。
- task markdown files affected can be derived from context pack。

### 13.4 UI Smoke

手動或 Playwright：

- Start Council 仍可一鍵執行。
- Context bar 不遮擋現有 controls。
- Gate #1 context summary 可展開。
- mobile / narrow viewport 不溢出。

---

## 14. 風險與對策

| 風險 | 影響 | 對策 |
|------|------|------|
| Context 太大 | 成本爆炸、模型輸入過長 | char budget、優先級、明確截斷 |
| 讀到 secret | 安全事故 | secret path denylist、gitignore、UI 顯示檔案清單 |
| Scout 找錯檔 | Council 誤判 | 顯示來源與 reason，允許取消，手選優先 |
| UI 變複雜 | 破壞現有好用感 | compact context bar + optional expand |
| Context 掃描慢 | Start Council 延遲 | preview debounce、cache、progress logs |
| Mock tests 變脆 | 開發速度下降 | context pack 支援 minimal target fixture |
| Auto approve 危險 | 錯 plan 直接執行 | context guard，不足時強制 Gate #1 |

---

## 15. 最小可行改造範圍

如果只做最小但有意義的修正，應做：

1. `project_context.py` L0 blueprint。
2. `run_council(prompt, context_pack=...)`。
3. Context envelope prompt。
4. conversation JSON 保存 context pack。
5. council markdown 輸出 context summary。
6. 測試確保不再 blind council。

這可以不改 UI，使用者體感幾乎完全相同，但 Council 已經不再是純 prompt-only。

---

## 16. 建議最終排序

```text
P0: 6a L0 Blueprint + Prompt Injection + Provenance
P1: 6b Context Preview + Gate #1 Summary
P1: 6c Manual Context Files
P2: 6d Scout Auto Recommendation
P2: 6e Auto-approve Context Guard
P3: 6f Explorer Brief
```

最重要的取捨：

**先修正 Council 的資訊來源，再追求智慧推薦。**

也就是先保證每一次 Council 都有可審計的 context，再逐步讓 context selection 變得更聰明。

---

## 17. 完成定義

這次大改造完成後，MAW 應滿足：

- Council 不會在無 target context 的情況下正常啟動。
- 使用者仍能維持原本的任務啟動流程。
- Gate #1 可以看到 Council 依據了哪些 target files。
- Executor 收到的 TASK 不再只是通用抽象指令。
- Reviewer 可以回看 Council 當時的 context provenance。
- 若 context 不足，Council 會明確說不足，而不是硬猜。

一句話標準：

```text
MAW 的 Council 必須從「多模型通用建議」升級為「基於目標專案上下文的可審計決策」。
```
