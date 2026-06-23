# MAW Context-Aware Council 大改造計劃

> **版本**：0.3
> **狀態**：架構改造計劃（review 修訂版）
> **目標階段**：Phase 6 - Context-Aware Council
> **核心原則**：盡量不改變使用者感受到的 UI/UX 流程，但徹底修正 Council 盲眼決策問題。

---

## 0. Review 後定案

經過小 A / 小 B / 小 O 對 v0.1 計劃的評估，以及第二組 Openwork 模型對 v0.2 的實作微調後，整體方向維持不變，但第一批實作範圍需要收斂並補上幾個 Phase 6a 細節。

本修訂版採用以下決策：

1. **保留原計劃作為 north star**：L0/L1/L2/L3 分層、provenance、安全邊界、Gate #1 context summary 都仍是最終方向。
2. **第一刀只做 Phase 6a**：先完成 L0 Blueprint、prompt injection、conversation / PLANNING provenance、auto-approve guard 與測試。
3. **Phase 6a 不改 UI 主體**：不做 context bar、preview API、file selector、Scout 或 Explorer；使用者仍只感覺按下 Start Council 後系統多做了一段準備。
4. **不新增公開 workflow 狀態**：Phase 6a 暫時不加入公開 `CONTEXT_GATHERING` enum，context gathering 包在 `COUNCIL_RUNNING` 裡，以 logs 呈現。
5. **Stage 1/2/3 都必須使用 context envelope**：不能只讓 Stage 1 看 context，Stage 2 ranking 與 Stage 3 synthesis 也要基於同一份 target context。
6. **MVP 預算下修**：Phase 6a 先用 40K-50K chars 的總預算，避免三階段 Council 成本暴增。
7. **避免新增強依賴**：Phase 6a 不依賴 `rg`、embedding database 或新 UI 套件；`.gitignore` 可用 `git check-ignore` 輔助，失敗時 fallback 到內建 denylist。
8. **Context 要 task-scoped**：若要給 Executor / Reviewer 使用，不寫全域 `MAW_workflow/CONTEXT.json`，而是保存在 `PLANNING/council_NNN.json`，必要時再輸出 `PLANNING/context_NNN.json`。
9. **Phase 6a 實作細節固定**：`git check-ignore` 必須批次化；mock council 仍產生真實 fixture context pack；`context_pack=None` 要標記為 unavailable；export 層必須同步寫入 council markdown/json；測試 fixture 必須動態建立，不掃真實 repo。

一句話：

```text
第一版先讓 Council 不再盲眼，且結果可審計；之後再做更聰明的檔案選擇與 UI 輔助。
```

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

- Phase 6a 不新增 UI 控制，只在 existing running/log area 顯示 context gathering 進度。
- Phase 6b 之後才在 Start Council 前顯示 context 預覽。
- Phase 6b/6c 之後才允許展開查看 Council 將看到哪些檔案與手動附加檔案。
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

長期可以新增細分狀態：

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

但 **Phase 6a 暫不新增公開 workflow enum**。第一版先把 context gathering 包在既有 `COUNCIL_RUNNING` 裡，降低 `resume_unfinished()`、WebSocket、前端狀態 badge 的同步風險。

---

## 4. 目標架構

### 4.1 新增模組

Phase 6a 只新增：

```text
MAW/
└── project_context.py              # L0 blueprint、budget、prompt envelope、summary helpers
```

後續 Phase 6b+ 若功能變多，再拆成：

```text
MAW/
├── project_context.py              # Context pack 組裝主模組
├── context_scanner.py              # 目錄樹、README、依賴檔、gitignore 排除
├── context_selector.py             # L1 手選檔案驗證與讀取
├── context_scout.py                # L2 自動候選檔案推薦
├── context_budget.py               # 字元/token 預算、截斷、排序
└── tests/
```

這樣第一個 PR 的 blast radius 最小，也方便測試 reviewer 聚焦在「Council 是否真的收到 context」。

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
      "maxTotalChars": 50000,
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

Phase 6a 只要求 `blueprint`、`summary`、`policy`、`accessIssues` 穩定存在；`files` 可先為空陣列，等 L1/L2 實作後再填入 user-selected / scout files。

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

Phase 6a 必須確保三個階段都以 context-aware request 為基礎：

- **Stage 1**：每個 council member 收到 context envelope + original user request。
- **Stage 2**：ranking prompt 也要包含 context-aware request。可以使用完整 envelope，也可以使用由同一份 `context_pack` 產生的 compact context digest；但不得退回 raw prompt only。
- **Stage 3**：Chairman synthesis 要看到 context、Stage 1 回答與 Stage 2 排名，且明確要求「若只有 L0，不得編造未提供的具體檔案內容」。

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

**狀態**：P1 黃金方案，Phase 6a 不做。

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

**狀態**：P2 自動化層，Phase 6a 不做。

先使用便宜、可解釋的方式：

- 從 user prompt 抽取 filename-like token。
- 用純 Python `os.walk` / `fnmatch` 找檔名匹配。
- 用純 Python 輕量文字搜尋做關鍵字命中。
- README/package scripts 命中的入口檔加權。
- 最近修改檔案可作為弱訊號，但不要過度依賴。

`rg` / ripgrep 可作為未來 optional acceleration，但不作為必要依賴；CI 或使用者環境沒有 `rg` 時，功能不能失效。

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

**狀態**：P3 — Phase 6f 已實作（6a–6e 已完成，6f MVP 已落地）。

Explorer 是唯讀調研 agent，不是 executor。詳細規格見 **§12 Phase 6f**。

與 L2 Scout 的分工：

```text
Scout (L2)     → 推薦檔案清單（preview / optional auto-include）
Explorer (L3)  → 調研 brief（摘要 + 證據鏈，不注入 contextFiles）
L1 files       → source of truth（完整檔案內容）
```

可做（MVP）：

- list / read safe files
- 純 Python text search
- test file name discovery
- dependency/config inspect
- symbol-ish search（文字 regex，非 AST）
- 產出可審計 `Context Brief`

不可做：

- 修改檔案或寫入 target project
- 安裝依賴、build、test、formatter
- git 寫操作或高風險 shell
- 自動把不透明結論當成事實（失敗時 summary 必須為空）

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

Phase 6a 不新增 Panel 1 控制項，不新增 file selector，不新增 preview API。使用者體感維持：

```text
填任務 -> Start Council -> 系統顯示圓桌會議準備/進行中 -> Gate #1
```

Phase 6a 僅透過既有狀態文字或 workflow logs 顯示：

```text
圓桌會議準備中：正在讀取目標專案上下文...
```

Phase 6b 之後才在 Panel 1 任務輸入框下方新增 compact context bar：

```text
Context: Blueprint ready · 3 suggested files · 42K chars
[Preview] [Add files] [Auto-detect]
```

Phase 6b+ 狀態：

- `Not scanned yet`
- `Scanning...`
- `Blueprint ready`
- `Files attached`
- `Context too large`
- `Context failed`

不要新增大型 wizard，不要拆成多頁。

### 6.3 Gate #1 增加 Context 摘要

Phase 6a 先在 `PLANNING/council_NNN.md` 和 conversation JSON 中保存 context summary；前端 Gate #1 可先不新增可視區塊。

Phase 6b 之後，Council result review panel 中新增一個可折疊區塊：

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
- 若 context pack 只有 L0，Phase 6a 預設仍允許人工 Gate #1，但不鼓勵無人監督 auto approve；是否允許需由 review policy 明確開啟。

否則自動降級為需要 Gate #1 人工審批，並顯示原因。

---

## 7. 狀態機改造

### 7.1 目前狀態

```text
IDLE
  -> COUNCIL_RUNNING
  -> COUNCIL_PENDING_APPROVAL
```

### 7.2 Phase 6a 狀態策略

Phase 6a 不新增公開狀態 enum。流程維持：

```text
IDLE
  -> COUNCIL_RUNNING
  -> COUNCIL_PENDING_APPROVAL
```

但 `COUNCIL_RUNNING` 內部先做：

```text
build_context_pack()
  -> run_council(context_pack=...)
```

好處：

- 不需要第一版同步修改 `resume_unfinished()`。
- 不需要第一版修改前端狀態 badge。
- 不需要擴大 WebSocket 狀態相容面。
- context gathering 失敗仍可把 workflow 設為 `FAILED`，不會 fallback 到 blind council。

### 7.3 後續可選內部狀態

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

### 7.4 WebSocket / Logs

新增 log lines：

```text
[context] Scanning target project blueprint...
[context] Included README.md (2000 chars)
[context] Context pack ready: 42K chars, L0 blueprint only
[council] Starting Stage 1 with context pack v1
```

UI 顯示可以仍然是「圓桌會議準備中」。

---

## 8. API 改造

### 8.1 `POST /api/maw/conversations/new`

Phase 6a 盡量不改 request shape；後端可直接根據 `targetKey` 與 `prompt` 自動產生 L0 context pack。

Phase 6c 才新增 request fields：

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

**狀態**：Phase 6b，不屬於 Phase 6a。

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

**狀態**：Phase 6c，不屬於 Phase 6a。

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

`context_pack=None` 的行為必須明確：

- 為了向後相容，`run_council()` 可以允許 `None`，並走舊的 prompt-only 模式。
- 但 conversation / assistant metadata 必須標記：

```json
{
  "contextPackVersion": null,
  "contextStatus": "unavailable"
}
```

- 正常 orchestrator 流程不得傳 `None`；`LoopOrchestrator._run_council_task()` 必須先 `build_context_pack()`，成功後才呼叫 `run_council()`。
- 這個相容模式只用於舊資料、直接單元測試或手動低階 API，不是 MAW UI 的正常路徑。

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

`PLANNING/council_NNN.json` 也必須保存完整或可重建的 `context_pack`。若後續需要讓 Executor / Reviewer 更方便讀取，可額外輸出：

```text
PLANNING/context_NNN.json
```

不要輸出全域：

```text
MAW_workflow/CONTEXT.json
```

原因：

- 多任務並行時會覆蓋。
- Executor / Reviewer 都以 task number 工作，context 也應該 task-scoped。
- `PLANNING/council_NNN.json` 已是該 task 的 provenance 主檔，context 應與它保持一一對應。

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

### 10.2 `.gitignore` Handling

Phase 6a 若目標專案是 git repo，應優先用 git 本身判斷 ignore 規則，避免手寫 `.gitignore` parser。

要求：

- 使用 `git check-ignore --stdin` 批次處理候選檔案，不能逐檔啟動 subprocess。
- subprocess `cwd` 設為 target project root。
- 傳入與回傳都使用 target-root-relative path。
- 若 `git` 不存在、target 不是 git repo、或 `git check-ignore` 失敗，必須 fallback 到內建 denylist。
- fallback 不應中斷 context gathering；但要在 `accessIssues` 或 logs 中留下 warning。

範例：

```bash
printf "file1.py\nfile2.js\n" | git check-ignore --stdin
```

### 10.3 Path Safety

所有檔案讀取必須：

- 從 target project root 解析。
- 使用 realpath 驗證仍在 target root 內。
- 拒絕絕對路徑。
- 拒絕 `..` 跳出。
- 對 symlink 做 root containment 檢查。

### 10.4 Read-only Guarantee

Context gathering 階段不得：

- 寫入 target project。
- 執行 package install。
- 執行 formatter。
- 執行測試，除非未來 L3 Explorer 明確取得使用者授權。

---

## 11. Token / 字元預算

MVP 建議先用 char budget，不必先做 tokenizer。

Phase 6a 預設：

```text
maxTotalChars: 50000
maxFileChars: 12000
maxTreeChars: 10000
maxReadmeChars: 4000
maxDependencyFileChars: 6000
maxTreeEntries: 200
maxScoutFiles: 0
```

`maxScoutFiles` 在 Phase 6a 固定為 0，等 Phase 6d 才打開。若實測三階段成本仍偏高，可先把 `maxTotalChars` 下修到 40000。

優先級：

```text
Phase 6a:
1. dependency / test script summary
2. README summary
3. directory tree

Phase 6c+:
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
- 若未來讀取 source file，優先避免切斷在語意不清的位置；可以加入 class/function outline 或明確 omitted marker，但不要求 Phase 6a 完成 AST-aware truncation。

---

## 12. 實作路線圖

### Phase 6a - L0 Blueprint + Council Prompt Injection

目標：

從完全盲眼變成至少知道專案形狀；不改 UI 主流程。

工作項：

- 新增 `project_context.py`
- 實作 `build_context_pack(target_key, prompt, context_files=[], auto_scout=False)`
- 產生 L0 blueprint：
  - tree
  - README
  - dependency files
- 在 `LoopOrchestrator._run_council_task()` 先產生 context pack。
- `run_council()` 接收 context pack。
- `run_council(context_pack=None)` 僅作向後相容，metadata 要標記 `contextStatus: unavailable`。
- Stage 1/2/3 prompt 使用 context envelope。
- conversation JSON 保存 context pack。
- `export.py` 修改 `render_council_markdown()`，新增 Target Project Context 摘要區塊。
- `export.py` 修改 `export_to_target()` 的 `council_json`，寫入完整或可重建的 `context_pack`。
- `PLANNING/council_NNN.md` 與 `PLANNING/council_NNN.json` 都必須輸出 context summary / context pack。
- auto-approve context guard 第一版同步加入。
- 測試確保 mock/live prompt path 不再是 prompt-only。

驗收：

- Mock council conversation JSON 含 `context_pack`。
- Mock council 測試仍應對 temporary target fixture 產生真實 context pack；mock 回答本身不需要引用具體檔案。
- Live council Stage 1 prompt 不再只有 user prompt。
- Stage 2 ranking prompt 不再只有 raw prompt。
- Stage 3 synthesis prompt 明確知道 context boundary。
- target project 沒有 README 也可正常產生 blueprint。
- `MAW_workflow/` 不會被讀進 context。
- context gathering 失敗不會默默 fallback 到 blind council。
- Phase 6a 不新增 visible UI 控制，不新增 preview API。
- `auto_approve_council` 在 context 不 ready 時會降級為人工 Gate #1。

### Phase 6b - Context Summary Visibility

目標：

讓使用者在不改變主流程的前提下，於 Gate #1 看見 Council 依據。

工作項：

- Gate #1 顯示 context summary。
- workflow details / conversation details 可展開 context provenance。
- logs 顯示 context warnings。

驗收：

- 不使用新功能時，原本 Start Council 體感仍順。
- 使用者可展開查看檔案清單。
- context 太大或只有 L0 時有清楚提示。

### Phase 6c - Context Preview API + UI Minimal Bar

目標：

讓使用者可在 Start Council 前預覽，但不強迫新增操作。

工作項：

- 新增 `/api/maw/context/preview`
- Panel 1 新增 compact context bar。
- target 或 prompt 改變時 debounce preview。
- Start Council 時重新產生 context pack，preview 只作為提示，不作為唯一真相。

驗收：

- preview 失敗不會直接啟動 blind council。
- context bar 不破壞現有 layout。

### Phase 6d - L1 手動 Context Files

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

### Phase 6e - L2 Scout 自動推薦

目標：

減少使用者手動選檔成本。

工作項：

- 實作 prompt keyword extraction。
- 實作 filename/path matching。
- 實作純 Python text matching。
- top-N scoring。
- UI 顯示 suggested files，可取消。

驗收：

- prompt 提到 `authentication.py` 時能命中該檔。
- prompt 提到 `token expiry` 時能找到 auth/token 相關檔案。
- generated/vendor/build 檔案不會被推薦。

### Phase 6f - Explorer Brief Prototype

**狀態**：已落地 / 已驗收。前置：6a L0、6d L1、6e Scout + auto-include（含 preview key、provenance、G10 auto-approve guard）均已落地。

#### 6f.0 建議是否做

**建議做，但 MVP 極窄。**

- Scout 解決「推薦哪些檔」；Explorer 補「區域地圖 + 證據鏈」敘事 brief。
- 唯讀、無 target 寫入，與 Executor 邊界清晰。
- **不做全功能 agent**：MVP = deterministic read/search + template summary，**不呼叫 LLM**。`rg`（ripgrep）是 6f MVP **optional read-only acceleration**（`_detect_rg()` 偵測，有則加速、無則純 Python `os.walk` + `re.search` fallback，非硬依賴）。

#### 6f.1 Explorer 定位

- Explorer 是 **read-only research layer**，只產出 brief，**不修改 `contextFiles`**。
- Runtime 主路徑：`context_pack.explorerBrief`。
- Gate #1 export 時可選鏡像至 `MAW_workflow/PLANNING/context_brief_NNN.json`（非 MVP 必要）。
- Explorer 執行當下**不寫入** target project 或 `PLANNING/`。
- **失敗隔離**：`explorerBrief = null` 或 `{status: "failed"|"timeout", summary: ""}`；`build_context_pack()` 照常產 L0/L1/L2；`accessIssues` / `warnings` 記錄 explorer 問題。**禁止**用 L0 tree 拼湊假 brief 或 LLM 補寫 summary。

> **Phase 6f.2 對齊**：Orchestrator 層已實作完整 failure isolation——`_run_council_task()` 中 Explorer 執行包裹在獨立的 `try/except` 區塊內（`loop_orchestrator.py:482`）。任何 Explorer 內部錯誤（timeout、exception、missing key）都會被攔截為 `status: "failed"|"timeout"|"skipped"`、寫入 `explorerBrief.accessIssues`、並以 `logger.warning` 記錄，**絕不拋出阻斷 Council 啟動**。後續 `run_council()` 始終正常執行。

#### 6f.2 啟用方式

| 選項 | MVP 決策 |
|------|----------|
| 預設關閉 | ✅ |
| Panel 1 toggle：`Generate Explorer Brief` | ✅ 唯一入口 |
| Gate #1 前提示「可加深調研」 | ❌ 延後 6f+ |
| 自動啟用 | ❌ 不允許 |

對齊 6e-C 的 **preview key** 模式：

```text
Preview Explorer  →  POST /api/maw/context/explorer/preview
Start Council     →  generateExplorerBrief: true 時必帶 explorerPreviewKey {targetKey, prompt}
Stale             →  target/prompt 變更 → brief 作廢，需重新 preview
```

Start 阻擋 guard（無 preview key / stale）已於 6f-D 實作完畢，對齊 6e-C G3。

#### 6f.3 唯讀命令白名單

後端以 **internal ops enum** 實作，不暴露 shell：

```python
ExplorerOp = Literal[
    "list_files",      # 重用 list_safe_files / _collect_candidate_files
    "read_file",       # 重用 _read_l1_file + path validation
    "search_text",     # rg（偵測加速）或純 Python walk + regex fallback
    "inspect_deps",    # package.json / pyproject.toml 等
    "find_tests",      # fnmatch *test* 鄰近 matched source
    "symbol_search",   # MVP = 文字 regex 找 def/class/export（非 AST）
]
```

**必須重用** `project_context` 的 `_validate_context_file_path`、secret/binary/gitignore 排除；Explorer 不得繞過 Scout 安全層自建讀檔路徑。

**不可用**：install、build、test、formatter、git 寫操作、target project 任何寫入。

> **Phase 6f.2 對齊**：MVP 實作允許 **`rg` (ripgrep) 作為 optional read-only acceleration**。
> `_detect_rg()` 偵測系統是否安裝 `rg`；若可用則透過 subprocess 加速文字搜尋，否則自動降級為純 Python `os.walk` + `re.search` fallback。
> 所有安全層（timeout、path filtering、secret masking、safe file gate）在 `rg` 路徑與 fallback 路徑均完整保留，`rg` **不作為硬依賴**。

#### 6f.4 Safety gates（G1–G12）

| Gate | 規則 |
|------|------|
| G1 | `generateExplorerBrief=false` → 不執行 |
| G2 | `prompt` 為空 → skip |
| G3 | Start 時需 `explorerPreviewKey` 且 match（對稱 6e-C G3） |
| G4 | 路徑限 target root 內 |
| G5 | secret / binary / gitignored / denylist → 不讀、不搜 |
| G6 | wall-clock timeout（MVP 建議 15s） |
| G7 | `maxFilesRead`（建議 8） |
| G8 | `maxCharsRead`（建議 24_000，獨立於 context pack budget） |
| G9 | `maxSearchResults`（建議 50） |
| G10 | `maxCandidateFiles` in brief（建議 12） |
| G11 | 每個 read/search 記錄 provenance → `commands[]` |
| G12 | timeout / exception → `status=timeout\|failed`，**summary 必須為空字串** |

> **Phase 6f.2 對齊 — Timeout 實作**：目前採用 `threading.Thread(daemon=True)` + `join(timeout=…)` 的 bounded-join 模式（`explorer.py:330–343`）。
> worker thread 為 daemon（主線結束時不阻擋退出），join 超時後主線立即回傳 `status=timeout`，且 `summary` 清空為 `""`。
> daemon worker 後續仍可能繼續執行，但其結果被丟棄。
> 後續可升級為 cooperative cancellation（定期檢查 stop flag），但現有 bounded-join 已滿足 MVP 的安全需求。

Explorer brief **不阻擋** auto-approve（與 scout_auto 不同）；brief 是摘要層。

#### 6f.5 Context Brief schema

```json
{
  "version": 1,
  "status": "ready|partial|failed|timeout|skipped",
  "generatedAt": "ISO8601",
  "targetKey": "string",
  "query": "string",
  "previewKey": { "targetKey": "string", "prompt": "string" },
  "summary": "string",
  "relevantAreas": [
    {
      "path": "src/auth",
      "reason": "string",
      "confidence": "low|medium|high",
      "evidence": ["search_hit:token expiry:3"]
    }
  ],
  "candidateFiles": [
    {
      "path": "src/auth/session.ts",
      "reason": "string",
      "evidence": ["keyword_match:token", "nearby_test:session.test.ts"],
      "contentIncluded": false,
      "charsRead": 3000,
      "truncated": false,
      "excerpt": "optional, max 500 chars"
    }
  ],
  "missingContext": ["Need route/controller entrypoint"],
  "commands": [
    {
      "kind": "search_text",
      "query": "token expiry",
      "pathsSearched": 120,
      "resultCount": 12,
      "durationMs": 45
    }
  ],
  "limits": {
    "maxFilesRead": 8,
    "maxCharsRead": 24000,
    "timeoutSeconds": 15,
    "filesRead": 5,
    "charsRead": 8200,
    "hitTimeout": false
  },
  "accessIssues": []
}
```

重點欄位：

- `contentIncluded: false` — brief 提到檔但未讀全文時必須標明。
- `previewKey` — 支援 stale 驗證。
- `limits` — Gate #1 provenance 可一眼看出是否 truncated。
- `summary` — MVP 用 template 拼接，不用 LLM。

#### 6f.6 Prompt integration

對照 `build_prompt_envelope()` 預算優先級，新增 **P2.5**：

```text
P1   Context Status
P2   L1 User-Selected + Scout Auto-Selected（完整內容，永不截斷）
P2.5 Explorer Brief（摘要層，可截斷）   ← 在 L1/L2 之後、L0 之前
P3   L0 Blueprint（tree/README/deps，可截斷）
P4   Context Boundaries + User Request
```

Prompt 必帶 instruction block：

```markdown
## Explorer Research Brief (L3 — NOT source of truth)
- This section is an automated read-only research summary.
- Prefer User-Selected and Scout Auto-Selected file contents over this brief.
- Files listed with contentIncluded=false were NOT fully read; do not infer implementation details.
- Treat missingContext items as explicit gaps.
```

`candidateFiles` 預設只放 **excerpt**（≤500 chars），避免與 L1 重複。

#### 6f.7 UI/UX（MVP 輕量）

| 元素 | 規格 |
|------|------|
| Panel 1 toggle | `[ ] Generate Explorer Brief`，預設 off，`localStorage: maw_explorer_brief` |
| Context bar | `· Explorer brief ready` / `· Explorer 已過期，請重新產生` |
| 觸發 | **獨立按鈕** `[產生 Explorer Brief]`（不拖慢 `/context/preview`） |
| Preview modal | status、summary、relevantAreas、candidateFiles（標 contentIncluded）、commands 摘要 |
| Gate #1 | provenance 顯示 brief status + limits + top commands |

不新增 wizard。Explorer 與 Context Preview **分開觸發**。

#### 6f.8 API 方案

**獨立 API**（不擴充 `/api/maw/context/preview`）：

```http
POST /api/maw/context/explorer/preview
```

Request：

```json
{
  "targetKey": "string",
  "prompt": "string",
  "contextFiles": ["optional L1 seed paths"],
  "maxFilesRead": 8,
  "maxCharsRead": 24000,
  "timeoutSeconds": 15
}
```

Response：`ExplorerBrief` schema（§6f.5）。

Start Council 擴充 `NewConversationRequest`：

```json
{
  "generateExplorerBrief": false,
  "explorerPreviewKey": { "targetKey": "...", "prompt": "..." }
}
```

- `generateExplorerBrief=true` 且無 key → **400**（對稱 6e-C）。
- Orchestrator 在 `build_context_pack()` 後呼叫 `run_explorer_brief()`，結果掛 `context_pack.explorerBrief`。

#### 6f.9 MVP 邊界

| 在 MVP 內 | 在 MVP 外 |
|-----------|-----------|
| 純 Python text search | LLM 生成 summary |
| Template summary | AST / LSP symbol resolution |
| 獨立 explorer preview API | 自動啟用 |
| Panel 1 toggle + modal | Gate #1「建議加深調研」 |
| `context_pack.explorerBrief` | Auto-inject 至 contextFiles |
| `rg` optional read-only acceleration（偵測->加速，無則 fallback） | `rg` 硬依賴 |
| Start optional attach（含 preview key） | Explorer 修改 contextFiles |
| Export provenance 摘要 | 完整 wizard |

#### 6f.10 不做事項

1. 不呼叫 LLM 寫 brief。
2. 不寫入 target project。
3. 不把 brief 內容當 L1 注入 `contextFiles`。
4. 不跑 npm/pip/pytest/build/git。
5. 不阻塞 Context Preview API。
6. 不做 auto-enable。
7. 失敗時不 fallback 合成 summary。
8. 不讓 Explorer brief 優先於 user-selected 檔案內容。
9. MVP 不做 Gate #1 二次調研提示。
10. 不新增 `rg` 硬依賴。

#### 6f.11 實作切分

```text
6f-A  explorer.py + schema + run_explorer_brief()     [pure Python, no HTTP]
6f-B  POST /api/maw/context/explorer/preview          [可獨立驗收]
6f-C  context_pack.explorerBrief + prompt + export    [Council 可見]
6f-D  UI toggle + preview modal + context bar         [最後]
```

建議順序：6f-A → 6f-B → 6f-C → 6f-D。6f-C 可在 6f-D 前 merge，方便後端先驗收。

#### 6f.12 測試計劃

新增 `test_explorer.py`：

| # | 測試 |
|---|------|
| 1 | 預設不執行 → `explorerBrief` null 或 `skipped` |
| 2 | 只讀 safe files；`../etc/passwd` rejected |
| 3 | secret / binary / gitignored 不被 read/search |
| 4 | timeout → `status=timeout`，`summary=""`，L0/L1 仍可用 |
| 5 | hit `maxFilesRead` → `status=partial` |
| 6 | brief schema 欄位穩定 |
| 7 | `build_prompt_envelope` 含 brief + NOT source of truth 文案 |
| 8 | `contentIncluded=false` 在 envelope 標明未讀 |
| 9 | Explorer exception 不阻斷 Council workflow |
| 10 | target project 無新寫入 |
| 11 | missing `explorerPreviewKey` on start → skip + accessIssue |
| 12 | stale key → skip |

`test_context_api.py` 補充：

- `POST /explorer/preview` 200 + schema
- unknown target → 400

#### 6f.13 Rollout sequence

```text
6f-A  backend + unit tests (explorer.py)
6f-B  explorer preview API + API tests
6f-C  prompt envelope + export provenance + orchestrator wire
6f-D  UI toggle + modal + stale/key guard
Post  6f+  Gate #1 re-explore hint, PLANNING/ json export
```

#### 6f.14 驗收標準

- Explorer 不寫入 target repo。
- Explorer 失敗 / 超時不破壞 L0/L1/L2 Council。
- brief 可審計（commands + evidence + limits）。
- Council prompt 明確標示 brief 為 research summary，非 source of truth。
- User-selected 檔案內容優先於 brief。

---

## 13. Phase 6g - Context Governance / Audit Hardening

**狀態**：待實作。前置：6a L0、6d L1、6e Scout、6f Explorer 均已落地。

### 13.0 建議是否做

**建議做，而且應該先做，不急著新增更強的 context agent。**

6a-6f 已經把 Council 從 blind prompt-only 升級成 L0/L1/L2/L3 context-aware workflow；6g 的目標不是再增加資訊來源，而是把已經存在的 context provenance、policy gate、export schema、UI wording 與測試標準收斂成長期可維護的治理層。

一句話：

```text
6g 讓「Council 看了什麼、為什麼能自動批准、哪些 context 不可信」變成可檢查、可回放、可測試的固定契約。
```

### 13.1 6g 範圍

6g 只做 governance / audit hardening：

- 統一 context provenance schema。
- 統一 auto-approve policy reason code。
- 統一 Gate #1 / export / JSON 的 context audit summary。
- 補齊 L0/L1/L2/L3 組合測試。
- 補一份 manual smoke checklist，確保 UI 沒有讓使用者誤會自動選檔或 Explorer brief 的權重。

6g 不做：

- 不新增 L4 agent。
- 不新增 embedding / vector database。
- 不讓 Explorer 或 Scout 直接寫 target project。
- 不改 Council 主流程。
- 不改使用者啟動任務的主要 UX。
- 不新增 auto-approve 寬鬆模式。
- 不把 Explorer brief 當 source of truth。

### 13.2 核心問題

現在 context 能力已經很多，風險從「Council 盲眼」轉成「Council 看了很多東西，但審計者不容易快速判斷哪些內容真的進了 prompt、哪些只是建議、哪些曾被拒絕」。

6g 要解決四個問題：

| 問題 | 6g 目標 |
|------|---------|
| provenance 分散 | conversation / export / Gate #1 使用同一套 audit summary |
| auto-approve 原因不夠機械化 | 每次 allow / block 都有穩定 reason code |
| L1 / Scout / Explorer 權重容易混淆 | UI 與 prompt 都明確標註 source of truth 層級 |
| 測試偏單點 | 補 cross-layer regression，避免 6d/6e/6f 互相踩線 |

### 13.3 Context Audit Summary Schema

新增或集中一個 helper，例如：

```python
build_context_audit_summary(context_pack: dict) -> dict
```

輸出建議：

```json
{
  "contextPackVersion": 1,
  "targetKey": "string",
  "status": "ready|partial|failed|unavailable",
  "highestLevel": "L0|L1|L2|L3",
  "sources": {
    "blueprint": {
      "present": true,
      "files": 3,
      "truncated": false
    },
    "userSelected": {
      "files": 2,
      "chars": 12000,
      "paths": ["src/app.py"]
    },
    "scoutAutoSelected": {
      "files": 3,
      "minScore": 0.62,
      "paths": ["src/auth.py"]
    },
    "explorerBrief": {
      "present": true,
      "status": "ready",
      "candidateFiles": 5,
      "commands": 2,
      "hitTimeout": false
    }
  },
  "riskFlags": [
    "l0_only",
    "scout_auto_selected",
    "explorer_timeout",
    "context_truncated",
    "access_issues_present"
  ],
  "accessIssueCount": 0,
  "promptIncluded": {
    "blueprint": true,
    "userSelectedFiles": true,
    "scoutAutoSelectedFiles": true,
    "explorerBrief": true
  }
}
```

原則：

- `riskFlags` 必須穩定，適合測試與未來 UI 顯示。
- `paths` 只使用 target-root-relative path。
- 不重複塞完整 file content。
- audit summary 是 metadata，不取代完整 `context_pack`。
- 若 `context_pack=None`，summary 必須明確標 `status="unavailable"`。

### 13.4 Auto-Approve Policy Reason Codes

整理 `_can_auto_approve_council()`，讓它回傳穩定、可測的結果，例如：

```python
{
  "allowed": false,
  "reasonCode": "blocked_l0_only",
  "reason": "Context pack has only L0 blueprint; allow_l0_auto_approve is false.",
  "riskFlags": ["l0_only"]
}
```

建議 reason code：

| Code | 意義 |
|------|------|
| `allowed_policy_ok` | policy 允許自動批准 |
| `blocked_no_context` | 沒有 context pack 或 context unavailable |
| `blocked_l0_only` | 只有 L0 blueprint，未允許 L0 auto-approve |
| `blocked_scout_auto_selected` | 有 Scout auto-selected 檔案，未允許 scout auto-approve |
| `blocked_context_failed` | context gathering failed |
| `blocked_context_truncated` | context 超預算或重要內容被截斷（若 policy 不允許） |
| `blocked_access_issues` | accessIssues 存在且 policy 不允許 |

6g 不需要改變現有 default policy；只把判斷結果變穩定、可審計。

### 13.5 Gate #1 顯示規則

Gate #1 context 區塊應呈現一個 compact audit view：

- Target key。
- Highest context level。
- Manual files count。
- Scout auto-selected count。
- Explorer status。
- Access issues count。
- Risk flags。
- Auto-approve decision / blocked reason。

顯示文字要避免誤導：

| 來源 | UI 文案 |
|------|---------|
| L1 manual | User-selected source files |
| L2 scout auto | Scout auto-selected files |
| L3 explorer | Explorer research brief — not source of truth |
| access issue | Skipped / blocked context items |

6g 不做大型 redesign；只調整現有 context summary / modal / export 用語，保持目前 UX 感覺。

### 13.6 Export / JSON Contract

`PLANNING/council_NNN.json` 應包含：

```json
{
  "contextPack": {},
  "contextAuditSummary": {},
  "autoApprovePolicy": {
    "allowed": false,
    "reasonCode": "blocked_scout_auto_selected",
    "riskFlags": ["scout_auto_selected"]
  }
}
```

Markdown export 應新增或整理：

```text
## Target Project Context Audit

- Context status: ready
- Highest level: L3
- Manual files: 2
- Scout auto-selected: 3
- Explorer: ready
- Risk flags: scout_auto_selected
- Auto-approve: blocked_scout_auto_selected
```

原則：

- JSON 是 machine-readable source。
- Markdown 是 reviewer-friendly summary。
- 不把完整 context content 複製多份。
- 保持 backward compatibility：舊 conversation 沒有 audit summary 時可 fallback 現有 rendering。

### 13.7 實作切分

```text
6g-A  project_context.py audit helper + unit tests
6g-B  auto-approve decision object + reason code tests
6g-C  export.py markdown/json audit summary
6g-D  Gate #1 / context bar wording polish
6g-E  cross-layer regression tests + manual smoke checklist
```

建議順序：6g-A → 6g-B → 6g-C → 6g-D → 6g-E。

6g-A/6g-B 可以先 merge，因為它們主要是後端契約；6g-D 只做文字與小型 UI polish。

### 13.8 測試計劃

新增或更新：

- `test_project_context.py`
- `test_orchestrator.py`
- `test_export.py`
- `test_context_api.py`
- `test_explorer.py`

必測：

| # | 測試 |
|---|------|
| 1 | L0 only → `riskFlags=["l0_only"]` |
| 2 | L1 manual files → highestLevel 至少 L1，manual count 正確 |
| 3 | Scout auto-selected → riskFlags 含 `scout_auto_selected` |
| 4 | Explorer ready → audit summary 含 status / command count / candidate count |
| 5 | Explorer timeout → riskFlags 含 `explorer_timeout`，不阻斷 context pack |
| 6 | accessIssues 存在 → accessIssueCount 正確 |
| 7 | context_pack=None → status unavailable，auto-approve blocked |
| 8 | `_can_auto_approve_council()` reason code 穩定 |
| 9 | council markdown export 含 Target Project Context Audit |
| 10 | council JSON 含 contextAuditSummary |

手動 smoke：

1. L0 only project：Gate #1 顯示 L0 only，auto-approve blocked。
2. 手選檔案：chip / Gate #1 / export 都標 user-selected。
3. Scout auto include：chip / Gate #1 / export 都標 scout auto-selected，auto-approve blocked unless policy explicitly allows。
4. Explorer enabled：Gate #1 顯示 research brief，但文案明確不是 source of truth。
5. Explorer timeout / failed：Council 仍可進 Gate #1，brief status 顯示 timeout / failed。

### 13.9 驗收標準

- 所有 context-aware Council 都能產出 `contextAuditSummary`。
- Auto-approve allow/block 都有 stable `reasonCode`。
- Gate #1、Markdown、JSON 三者對同一個 context pack 的摘要一致。
- L1 user-selected、L2 scout auto-selected、L3 explorer brief 的權重與角色不混淆。
- Explorer timeout / failed 不會被渲染成可用 context。
- 測試覆蓋 L0/L1/L2/L3 與 auto-approve 的交互。
- 不新增新 agent 能力，不改主流程，不破壞現有 UI 體感。

### 13.10 給小O的執行提示

請把 6g 當成「合約收斂」，不要當成「功能擴張」。

優先做：

1. 先建立 audit summary helper，不要在多個檔案各自拼 summary。
2. 再把 auto-approve result 改成 decision object。
3. 最後接 export / UI wording。

避免做：

- 不要改 context selection algorithm。
- 不要改 Scout score。
- 不要改 Explorer 搜尋策略。
- 不要新增 background refresh。
- 不要做新的 preview modal。

---

## 14. 測試計劃

### 14.1 Unit Tests

Phase 6a 新增測試：

- `test_project_context.py`

覆蓋：

- excludes `.git`, `MAW_workflow`, `node_modules`
- respects `.gitignore` when `git check-ignore` is available, otherwise falls back safely
- rejects path traversal
- rejects absolute paths
- truncates large files
- produces stable context schema
- uses a temporary target fixture, not the developer's real projects

`test_project_context.py` 建議動態建立 fixture：

```text
/tmp/test_target_XXXX/
├── .git/                 # 可用 git init 建立，或只測 fallback 時省略
├── .gitignore            # contains MAW_workflow/ and node_modules/
├── README.md             # meaningful project description
├── package.json
├── pyproject.toml
├── src/
│   └── main.py
├── node_modules/         # must be excluded
├── MAW_workflow/         # must be excluded
└── .env                  # must be excluded as secret
```

測試不應掃描 MAW 本身或任何真實使用者專案。`template_target_project` 可作參考，但 Phase 6a 測試最好在 test case 內動態建立最小 fixture，避免 fixture drift。

Phase 6d+ 才新增：

- `test_context_budget.py`
- `test_context_scout.py`

Phase 6f 新增：

- `test_explorer.py`（見 §12 Phase 6f.12）

### 14.2 Council Tests

更新：

- `test_council.py`
- `test_orchestrator.py`
- `test_e2e_workflow.py`

覆蓋：

- mock council conversation includes context pack。
- mock council uses a real context pack generated from a temporary target fixture, while mock response text remains deterministic and generic。
- `_run_council_live()` prompt contains Target Project Context。
- Stage 2 prompt includes original context-aware request, not raw prompt only。
- Stage 3 synthesis includes context-aware deliberation。
- `LoopOrchestrator._run_council_task()` gathers context before calling `run_council()`。
- interrupted `COUNCIL_RUNNING` workflows can safely rebuild context on resume。

### 14.3 Export Tests

更新：

- `test_export.py`

覆蓋：

- council markdown includes context summary。
- council JSON includes context pack。
- task markdown files affected can be derived from context pack。
- no global `MAW_workflow/CONTEXT.json` is required for Phase 6a。

### 14.4 UI Smoke

Phase 6a 不要求 UI smoke，因為不新增 UI 控制。

Phase 6b+ 手動或 Playwright：

- Start Council 仍可一鍵執行。
- Context bar 不遮擋現有 controls。
- Gate #1 context summary 可展開。
- mobile / narrow viewport 不溢出。

---

## 15. 風險與對策

| 風險 | 影響 | 對策 |
|------|------|------|
| Context 太大 | 成本爆炸、模型輸入過長 | char budget、優先級、明確截斷 |
| 讀到 secret | 安全事故 | secret path denylist、gitignore、UI 顯示檔案清單 |
| Scout 找錯檔 | Council 誤判 | 顯示來源與 reason，允許取消，手選優先 |
| UI 變複雜 | 破壞現有好用感 | compact context bar + optional expand |
| Context 掃描慢 | Start Council 延遲 | preview debounce、cache、progress logs |
| Mock tests 變脆 | 開發速度下降 | context pack 支援 minimal target fixture |
| Auto approve 危險 | 錯 plan 直接執行 | context guard，不足時強制 Gate #1 |
| 全域 CONTEXT.json 覆蓋 | 多任務 context 混淆 | 使用 task-scoped `PLANNING/council_NNN.json` / `context_NNN.json` |
| Stage 2 半盲 | 排名仍基於 raw prompt | Stage 2 prompt 必須包含 context-aware request |
| 新公開狀態造成 resume bug | 重啟恢復變複雜 | Phase 6a 不新增公開 `CONTEXT_GATHERING` |
| `.gitignore` 判斷太慢 | 大 repo 掃描延遲 | 使用 `git check-ignore --stdin` 批次化，失敗再 fallback |
| 直接呼叫 `run_council()` 仍盲眼 | 舊 API 或測試路徑不可審計 | 允許 `context_pack=None`，但 metadata 標記 `contextStatus: unavailable` |

---

## 16. 最小可行改造範圍

如果只做最小但有意義的修正，應做：

1. `project_context.py` L0 blueprint。
2. `run_council(prompt, context_pack=...)`。
3. Context envelope prompt，且 Stage 1/2/3 都使用。
4. conversation JSON 保存 context pack。
5. `PLANNING/council_NNN.md/json` 輸出 context summary / context pack。
6. auto-approve context guard。
7. `git check-ignore --stdin` 批次 ignore handling + denylist fallback。
8. `context_pack=None` 向後相容標記。
9. temporary target fixture 測試，確保不掃真實 repo。
10. 測試確保不再 blind council。

這一批不改 UI，使用者體感幾乎完全相同，但 Council 已經不再是純 prompt-only。

---

## 17. 建議最終排序

```text
P0: 6a L0 Blueprint + Prompt Injection + Provenance + Auto-approve Guard
P1: 6b Gate #1 Context Summary / Visibility
P1: 6c Context Preview API + Minimal Context Bar
P2: 6d Manual Context Files
P2: 6e Scout Auto Recommendation
P3: 6f Explorer Brief
P3: 6g Context Governance / Audit Hardening
```

### 6g 正式設計決策（6g.1 收斂後落檔）

**1. `context_truncated` 與一般 `accessIssues` 預設只做 audit，不阻擋 auto-approve。**
`blocked_context_truncated` reasonCode 不實作。字元預算截斷（含 secrets 排除）僅以 `riskFlags: context_truncated` / `access_issue` 記錄於 audit trail。未來若 policy 顯式要求，可再評估是否新增阻擋規則。

**2. `allow_partial_auto_approve` 預設值為 `True`。**
當 context `status == "partial"`（有 accessIssues）時，auto-approve 預設仍允許通過，除非 review policy 顯式設為 `False`（見 `loop_orchestrator.py:404`）。此為保守設計——partial context 可能會造成弱基礎決策，但不應硬性阻擋工作流推進。Gate #1 UI 仍會展示所有 riskFlags 供人工審計。

最重要的取捨：

**先修正 Council 的資訊來源，再追求智慧推薦。**

也就是先保證每一次 Council 都有可審計的 context，再逐步讓 context selection 變得更聰明。

---

## 18. 完成定義

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
