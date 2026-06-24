# MAW 本機多 Agent 極簡架構改造計畫

> **Version**: 2.0
> **Status**: 正式改造方案 — 待核准後執行
> **核心原則**: 檔案即協定、Watcher 負責喚醒、Adapter 負責啟動、Agent 自行讀取專案
> **取代範圍**: 現有 API Council、LLM Provider、Context Pack、Scout、Explorer，以及前後段不一致的編排方式

---

## 0. 改造結論

MAW 不需要建立一套新的本機 Agent 通訊平台，也不需要要求 Agent 實作 MAW 專用 WebSocket Client、SDK 或常駐連線。

MAW 已經具備足夠的基本元件：

- `AGENTS.md`：告訴 Agent 如何工作。
- `TEAM_RULES.md`：定義角色、交接、審查與安全規則。
- `watcher.py`：觀察狀態與產物，喚醒下一位 Agent。
- `adapters/`：把「喚醒角色」轉換成各 Agent 的實際啟動方式。
- `MAW_workflow/`：保存狀態、計畫、評論、實作紀錄與審查結果。

新的 MAW 只做：

1. 讓使用者選擇專案、角色與 Agent。
2. 建立工作流檔案。
3. 啟動或確認 `watcher.py` 正在運作。
4. 由 watcher 根據檔案狀態呼叫指定 Agent。
5. 顯示進度、產物與等待使用者決定的事項。

所有分析、讀檔、提案、實作與審查，都由使用者本機已開啟或可被 adapter 喚醒的 Agent 完成。

### 0.1 正式開發策略：同 Repo 重寫 v2

本次不在現有約兩萬行的 v1 架構內逐層改造。舊系統的核心假設、資料格式、UI 與測試大多服務於即將退役的 API Council；直接修改會讓大量時間花在維持暫時相容與清理交錯依賴。

正式策略是：

> 保留 MAW 的 GitHub repository、名稱與 Git 歷史，在新的 `codex/file-workflow-v2` 分支建立獨立 v2 核心；待真實 Agent 全流程驗證完成後，以 v2 取代 v1，最後整批刪除舊實作。

採用同 Repo 而非另開新專案，因為：

- MAW 的產品目的與名稱沒有改變。
- 現有提交歷史與設計文件仍有追溯價值。
- Git 已足以保存舊版，不需要在正式程式樹長期保留 `legacy/`。
- 可以在獨立分支自由重寫，不讓半成品污染 `main`。
- 完成後仍是一個乾淨、唯一的 MAW，而不是 MAW 與 MAW2 並存。

### 0.2 重寫原則

- 新核心使用全新目錄、資料 schema、測試與入口。
- 不讓 v2 import v1 的 Council、Context、Export 或 Orchestrator 模組。
- 不為了維持舊 conversation／Stage schema 而扭曲新設計。
- 舊版在 v2 達到切換條件前保持可運作，但不再新增功能。
- 只摘取已驗證且與新架構相符的少數能力。
- 摘取時以重新實作或窄幅搬移為原則，不複製整個舊模組。
- v2 未通過真實 Agent E2E 前，不刪除 v1。
- v2 通過切換 Gate 後，舊程式一次性整批移除，不留下雙軌架構。

---

## 1. North Star

```text
使用者提出需求
  ↓
Chair 釐清需求
  ↓
Planner 1–4 各自提案
  ↓
Planner 交叉評論其他提案
  ↓
Chair 綜整 final_plan.md
  ↓
使用者批准開工
  ↓
Executor 實作並產出 walkthrough
  ↓
Reviewer 審查
  ├─ REQUEST_CHANGES → Executor 補強 → 再送 Reviewer
  └─ APPROVE → Executor commit
  ↓
Chair 檢查完整歷程
  ↓
通知使用者完成或等待使用者處理小問題
```

一句話標準：

> MAW 是由檔案驅動的本機多 Agent 工作流；Agent 透過產物交接，watcher 透過 adapter 喚醒下一位角色。

---

## 2. 非目標

本次改造明確不做：

- 不建立 Agent WebSocket Meeting Room。
- 不建立 `maw_agent_sdk`。
- 不要求 Agent 支援 MAW 專用 CLI 參數。
- 不讓 MAW 遞迴讀取或打包目標專案原始碼。
- 不由 MAW 呼叫任何模型 API。
- 不由 MAW 保存模型 API Key。
- 不維持 Karpathy Stage 1／2／3 資料格式。
- 不做匿名提案或匿名排名。
- 不建立複雜的 Resource Token Protocol。
- 不把 Agent 對話內容同步進中央訊息匯流排。
- 不為每種 Agent 重寫一套工作流邏輯。

各 Agent 是否在其自身內部使用本機模型、訂閱服務或雲端能力，由使用者與該 Agent 自行管理；MAW 不介入。

---

## 3. 角色模型

### 3.1 支援角色

每次工作流包含：

| 角色 | 數量 | 責任 |
|---|---:|---|
| Chair | 1 | 釐清需求、主持規劃、產出最終計畫、最後驗收與回報 |
| Planner | 1–4 | 獨立提案並評論其他 Planner 的提案 |
| Executor | 1 | 依核准計畫實作、補強、commit |
| Reviewer | 1 | 審查實作並回覆 APPROVE 或 REQUEST_CHANGES |

### 3.2 角色與 Agent 分離

角色是本輪工作的身份；Agent 是實際執行工作的本機程式。

同一 Agent 可以：

- 同時被指定為 Chair 與 Planner。
- 承擔多個 Planner 席位。
- 同時擔任 Planner 與 Executor。
- 在使用者明確選擇下同時擔任 Executor 與 Reviewer。
- 一人分飾全部角色。

若同一 Agent 被分配多個可同時執行的角色，watcher 必須序列化呼叫，避免同一 Agent 實例同時接收多個任務。

### 3.3 每次工作流可覆寫

專案可以保存預設角色配置，但使用者每次開始工作流時均可覆寫：

```json
{
  "chair": "codex",
  "planners": [
    {"seat": "planner_a", "agent": "antigravity"},
    {"seat": "planner_b", "agent": "grok_build"},
    {"seat": "planner_c", "agent": "codex"}
  ],
  "executor": "antigravity",
  "reviewer": "grok_build"
}
```

Planner 使用穩定席位 ID，而非 Agent ID 作為檔名，確保同一 Agent 分飾多席時仍可區分產物。

---

## 4. 檔案即協定

### 4.1 目標專案結構

```text
<target-project>/
├── AGENTS.md
├── TEAM_RULES.md
└── MAW_workflow/
    ├── watcher.py
    ├── WORKFLOW_STATE.json
    ├── ACTIVE_WORKFLOW
    ├── workflows/
    │   └── workflow_001/
    │       ├── manifest.json
    │       ├── request.md
    │       ├── chair_brief.md
    │       ├── questions.md
    │       ├── answers.md
    │       ├── proposals/
    │       │   ├── planner_a.md
    │       │   ├── planner_b.md
    │       │   └── planner_c.md
    │       ├── comments/
    │       │   ├── planner_a_on_b.md
    │       │   ├── planner_a_on_c.md
    │       │   ├── planner_b_on_a.md
    │       │   ├── planner_b_on_c.md
    │       │   ├── planner_c_on_a.md
    │       │   └── planner_c_on_b.md
    │       ├── final_plan.md
    │       ├── user_decision.md
    │       ├── task.md
    │       ├── walkthroughs/
    │       │   ├── walkthrough_001.md
    │       │   └── walkthrough_002.md
    │       ├── reviews/
    │       │   ├── review_001.md
    │       │   └── review_002.md
    │       ├── commit.md
    │       ├── completion.md
    │       └── events.jsonl
    ├── adapters/
    └── archive/
```

### 4.2 為何每個工作流使用獨立目錄

- 所有歷程天然集中。
- 不需 conversation database。
- UI 可直接讀取同一組產物。
- watcher 重啟後可從檔案恢復。
- 不同工作流不會覆寫彼此檔案。
- Planner 數量與評論數量可以動態計算。
- 使用者可完整檢查 Agent 做過什麼。

### 4.3 Manifest

`manifest.json` 是工作流設定快照：

```json
{
  "schema_version": 1,
  "workflow_id": "workflow_001",
  "target_path": "/absolute/path/to/project",
  "created_at": "2026-06-24T12:00:00+08:00",
  "status": "CHAIR_CLARIFYING",
  "roster": {
    "chair": "codex",
    "planners": [
      {"seat": "planner_a", "agent": "antigravity"},
      {"seat": "planner_b", "agent": "grok_build"},
      {"seat": "planner_c", "agent": "codex"}
    ],
    "executor": "antigravity",
    "reviewer": "grok_build"
  },
  "review_iteration": 0,
  "max_review_iterations": 3,
  "require_user_plan_approval": true,
  "last_transition_at": "2026-06-24T12:00:00+08:00"
}
```

已開始的工作流不得因使用者修改專案預設角色而改變 roster；只有新工作流使用新設定。

### 4.4 Events

`events.jsonl` 是 append-only 稽核紀錄，不是 Agent 間的必要通訊管道：

```json
{"ts":"...","type":"workflow.created","actor":"maw"}
{"ts":"...","type":"agent.dispatched","role":"planner_a","agent":"antigravity","attempt":1}
{"ts":"...","type":"artifact.ready","role":"planner_a","path":"proposals/planner_a.md"}
{"ts":"...","type":"state.changed","from":"PLANNING","to":"PEER_REVIEW"}
```

事件只由 watcher／MAW 寫入；Agent 只需產出指定檔案。

---

## 5. 工作流狀態

### 5.1 正式狀態

```text
CREATED
CHAIR_CLARIFYING
WAITING_USER_CLARIFICATION
PLANNING
PEER_REVIEW
CHAIR_SYNTHESIS
WAITING_USER_APPROVAL
EXECUTING
REVIEWING
REVISION_REQUIRED
COMMITTING
CHAIR_FINAL_CHECK
COMPLETED
WAITING_USER_DECISION
CANCELLED
FAILED
```

### 5.2 轉移規則

| 當前狀態 | 完成條件 | 下一狀態 |
|---|---|---|
| CREATED | manifest、request 建立完成 | CHAIR_CLARIFYING |
| CHAIR_CLARIFYING | Chair 判斷需求完整 | PLANNING |
| CHAIR_CLARIFYING | Chair 產出待確認問題 | WAITING_USER_CLARIFICATION |
| WAITING_USER_CLARIFICATION | 使用者寫入回答 | CHAIR_CLARIFYING |
| PLANNING | 所有 Planner 提案完成 | PEER_REVIEW |
| PEER_REVIEW | 所有必要評論完成 | CHAIR_SYNTHESIS |
| CHAIR_SYNTHESIS | `final_plan.md` 完成 | WAITING_USER_APPROVAL |
| WAITING_USER_APPROVAL | 使用者批准 | EXECUTING |
| WAITING_USER_APPROVAL | 使用者要求調整 | CHAIR_CLARIFYING |
| EXECUTING | walkthrough 完成 | REVIEWING |
| REVIEWING | Reviewer 要求修改 | REVISION_REQUIRED |
| REVISION_REQUIRED | Executor 新 walkthrough 完成 | REVIEWING |
| REVIEWING | Reviewer APPROVE | COMMITTING |
| COMMITTING | Executor commit 完成 | CHAIR_FINAL_CHECK |
| CHAIR_FINAL_CHECK | 無重大問題 | COMPLETED |
| CHAIR_FINAL_CHECK | 有小問題待決定 | WAITING_USER_DECISION |
| 任意非終止狀態 | 使用者取消 | CANCELLED |
| 任意非終止狀態 | 不可恢復錯誤 | FAILED |

### 5.3 不以檔案存在作為唯一完成判定

為避免 Agent 先建立空檔後再寫入造成誤觸發，產物必須使用原子完成方式：

1. Agent 寫入 `*.tmp`。
2. 完成後 rename 成正式檔名。

或由 adapter 在 Agent 結束成功後驗證正式檔案存在且非空，再通知 watcher。

---

## 6. 規劃會議流程

### 6.1 Chair 釐清需求

Chair 讀取：

- `request.md`
- 目標專案
- `AGENTS.md`
- `TEAM_RULES.md`

Chair 必須二選一：

1. 需求足夠清楚：產出 `chair_brief.md`。
2. 需求仍有會改變方案的重要歧義：產出 `questions.md`。

`chair_brief.md` 至少包含：

- 使用者真正目標。
- 已知限制。
- 非目標。
- Planner 必須調查的問題。
- 最終計畫應回答的事項。

不得要求使用者回答不影響方案的瑣碎問題。

### 6.2 Planner 獨立提案

每位 Planner：

- 讀取 `request.md`、`chair_brief.md` 與目標專案。
- 不先讀其他 Planner 尚未完成的草稿。
- 產出自己的 `proposals/<seat>.md`。

每份提案至少包含：

- 對現況的理解。
- 建議方案。
- 預計修改／刪除範圍。
- 實施順序。
- 風險。
- 驗證方法。
- 仍需 Chair 決定的事項。

### 6.3 Planner 交叉評論

所有提案完成後，每位 Planner 評論其餘每一份提案。

若 Planner 數量為 `N`，必要評論數為：

```text
N × (N - 1)
```

| Planner 數 | 提案數 | 評論數 |
|---:|---:|---:|
| 1 | 1 | 0 |
| 2 | 2 | 2 |
| 3 | 3 | 6 |
| 4 | 4 | 12 |

評論檔命名：

```text
comments/<reviewer-seat>_on_<proposal-seat>.md
```

評論必須指出：

- 同意之處。
- 遺漏之處。
- 不合理或風險過高之處。
- 可吸收進最終計畫的具體建議。

不需要匿名，不做排名，也不計算平均分數。

### 6.4 Chair 綜整

Chair 讀取：

- 使用者需求與回答。
- Chair brief。
- 所有提案。
- 所有評論。
- 必要的目標專案內容。

Chair 直接產出 `final_plan.md`，不需要保留 Karpathy 三階段格式。

`final_plan.md` 必須能直接交給 Executor，至少包含：

1. 目標與完成定義。
2. 保留項目。
3. 刪除項目。
4. 新增或修改項目。
5. 檔案級改造清單。
6. 執行順序。
7. 資料或設定遷移。
8. 安全限制。
9. 測試與驗收命令。
10. 回滾界線。
11. 明確非目標。

若材料揭露新的重大歧義，Chair 可以再次進入 `WAITING_USER_CLARIFICATION`，而不是自行猜測。

### 6.5 使用者批准

產出 `final_plan.md` 後，工作流必須停在 `WAITING_USER_APPROVAL`。

使用者可以：

- `APPROVE`：開始實作。
- `REQUEST_CHANGES`：附上修改意見，重新交給 Chair。
- `CANCEL`：終止工作流。

未獲批准前，不得呼叫 Executor。

---

## 7. 實作與審查流程

### 7.1 Executor

Executor 讀取：

- `final_plan.md`
- `AGENTS.md`
- `TEAM_RULES.md`
- Reviewer 前一輪意見（若有）
- 目標專案

Executor 必須：

1. 在安全工作分支上實作。
2. 執行計畫要求的測試。
3. 產出 `walkthroughs/walkthrough_NNN.md`。
4. 將工作交給 Reviewer。

Walkthrough 至少包含：

- 實際修改內容。
- 與 `final_plan.md` 的差異及原因。
- 刪除內容。
- 測試命令與結果。
- 未解問題。
- 目前 branch 與 commit 狀態。

### 7.2 Reviewer

Reviewer 必須讀取：

- `final_plan.md`
- 最新 walkthrough。
- 實際 git diff。
- 相關測試結果。
- 前一輪 review 與修正紀錄。

Reviewer 產出 `reviews/review_NNN.md`：

```text
DECISION: APPROVE
```

或：

```text
DECISION: REQUEST_CHANGES
```

`REQUEST_CHANGES` 必須列出可執行、可驗證的修正事項。

### 7.3 修改循環

Reviewer 要求修改時：

1. watcher 將狀態設為 `REVISION_REQUIRED`。
2. 喚醒 Executor。
3. Executor 依 review 補強。
4. 產出下一版 walkthrough。
5. watcher 再喚醒 Reviewer。

循環不得覆寫舊檔；每輪使用遞增編號。

達到 `max_review_iterations` 仍未通過時，停止於 `WAITING_USER_DECISION`，不得無限循環。

### 7.4 Commit

Reviewer APPROVE 後：

1. watcher 喚醒 Executor 進入 commit 階段。
2. Executor 確認工作樹與測試狀態。
3. Executor 建立 commit。
4. 產出 `commit.md`，記錄 branch、commit SHA、測試與變更摘要。
5. watcher 喚醒 Chair。

是否 push／建立 PR 不應由工作流自行推定，必須遵循專案規則或使用者明確設定。

### 7.5 Chair 最終檢查

Chair 讀取完整歷程：

- final plan
- walkthroughs
- reviews
- commit record
- 最終 diff／測試

若無重大問題：

- 產出 `completion.md`。
- 將狀態設為 `COMPLETED`。
- 通知使用者。

若只有不阻礙完成的小問題：

- 寫入 `completion.md` 的注意事項。
- 將狀態設為 `WAITING_USER_DECISION`。
- 交由使用者決定是否追加工作。

若發現重大疏失，不得假裝完成；必須重新進入修正或等待使用者決定。

---

## 8. Watcher 職責

### 8.1 Watcher 要做的事

- 讀取 active workflow 與 manifest。
- 驗證目前狀態所需產物。
- 計算下一個尚未完成的角色工作。
- 呼叫 adapter。
- 記錄 dispatch、完成、錯誤與狀態轉移。
- 防止同一角色任務重複啟動。
- 對同一 Agent 的多角色工作進行序列化。
- 管理 timeout、retry、cancel。
- 在重啟後從檔案恢復。

### 8.2 Watcher 不做的事

- 不閱讀原始碼內容來做決策。
- 不生成提案、評論或計畫。
- 不解析自然語言來猜測 Agent 是否完成。
- 不直接呼叫模型 API。
- 不保存 Agent 對話。
- 不修改應用程式碼。
- 不替 Reviewer 做審查判斷。

### 8.3 Dispatch Key

每個工作項目使用穩定 dispatch key：

```text
<workflow_id>:<phase>:<role-or-seat>:<iteration>
```

例如：

```text
workflow_001:proposal:planner_a:1
workflow_001:comment:planner_b_on_a:1
workflow_001:review:reviewer:2
```

Watcher 在 `WORKFLOW_STATE.json` 記錄：

- dispatch key
- agent ID
- PID 或 adapter invocation ID
- attempt
- started_at
- timeout_at
- completion artifact
- final status

相同 dispatch key 若已完成，不得再次呼叫。

### 8.4 並行規則

- 不同 Agent 的 Planner 提案可以並行。
- 同一 Agent 承擔多個 Planner 席位時必須序列執行。
- 交叉評論必須等待所有提案完成。
- Chair、Executor、Reviewer 預設一次只允許一個工作項目。
- 同一目標專案同一時間只允許一個 Executor 修改程式碼。

不需要獨立 `resource_lock.py`；簡單的 per-agent 與 per-target lock 即可放在 watcher 內。

---

## 9. Adapter 契約

### 9.1 Adapter 的唯一責任

Adapter 將標準工作描述轉成特定 Agent 的啟動方式。

標準輸入：

```json
{
  "workflow_id": "workflow_001",
  "role": "planner",
  "seat": "planner_a",
  "target_path": "/absolute/path/to/project",
  "instruction_file": "MAW_workflow/workflows/workflow_001/instructions/planner_a.md",
  "expected_output": "MAW_workflow/workflows/workflow_001/proposals/planner_a.md"
}
```

Adapter 可以使用：

- 現有 GUI／TUI Agent 的本機觸發方式。
- `agentapi`。
- Agent 自己的 CLI。
- 自訂 shell command。
- 已開啟 Agent 的 inbox／hook。

工作流本身不關心其底層方式。

### 9.2 Registry

Registry 只描述能力與 adapter，不保存臆測的 binary 路徑：

```json
{
  "id": "antigravity",
  "label": "Antigravity",
  "adapter": "antigravity",
  "supports": ["chair", "planner", "executor", "reviewer"],
  "parallel_capacity": 1,
  "requires_running_app": true
}
```

### 9.3 Preflight

開始工作流前，MAW 必須檢查：

- 所選 Agent 是否存在。
- Adapter 是否可用。
- 需要預先開啟的 Agent 是否正在運作。
- Agent 是否支援被指派的角色。
- 目標專案是否可讀。
- Executor 是否具有必要寫入能力。
- watcher 是否可啟動。

若其中一位 Planner 不可用，不能靜默減少 Planner 人數；必須讓使用者重新選擇或明確繼續。

---

## 10. AGENTS.md 與 TEAM_RULES.md

### 10.1 AGENTS.md

保持精簡，只放所有角色共通的執行原則：

- 先讀指定 instruction 與專案規則。
- 只處理被指派的角色工作。
- 不覆寫其他角色產物。
- 以 tmp + rename 完成產物。
- 不自行跳過使用者 Gate。
- 不在未授權階段 commit。
- 遇到阻塞時寫明原因，不假裝完成。

### 10.2 TEAM_RULES.md

保存完整治理規則：

- 各角色責任。
- 提案與評論要求。
- 交接檔案格式。
- Executor／Reviewer 循環。
- 使用者批准規則。
- Git 與 commit 規則。
- 最大重試與失敗處理。
- 同一 Agent 分飾多角時的隔離規則。

### 10.3 角色 Instruction

Watcher 在每次 dispatch 前生成短小、具體的 instruction file；不要把全部歷史塞進 prompt。

Instruction 只提供：

- 目前角色。
- 必讀檔案。
- 預期產物。
- 完成條件。
- 禁止事項。

Agent 自行讀取目標專案與所列產物。

---

## 11. MAW UI

### 11.1 Panel 0：本機 Agent 與專案

保留：

- 專案選擇。
- 目標路徑驗證。
- Scaffold。
- Adapter 安裝／檢查。
- Agent 是否已開啟的狀態。

刪除：

- LLM Provider。
- LiteLLM。
- OpenRouter。
- Direct API Keys。
- 模型清單。
- Test LLM Connection。

新增：

- Chair 選擇。
- Planner 數量 1–4。
- 每個 Planner 席位的 Agent 選擇。
- Executor 選擇。
- Reviewer 選擇。
- 允許角色重疊。
- Agent preflight 結果。

### 11.2 工作流畫面

UI 顯示：

- 目前狀態。
- Roster。
- 哪些角色正在工作。
- 已完成與待完成的產物。
- 提案與評論矩陣。
- `final_plan.md`。
- Walkthrough／Review 歷程。
- Commit record。
- Chair completion。

UI 不需要顯示 Agent 的逐 token 輸出。

### 11.3 使用者互動

UI 需要提供：

- 回答 Chair 問題。
- 批准 final plan。
- 要求 Chair 修改 final plan。
- 取消工作流。
- 在 review loop 超限時決定下一步。
- 對 Chair 最終提出的小問題作出指示。

---

## 12. 重寫邊界與可摘取資產

本次採取「看懂後重新實作」，而不是把舊模組搬進 v2 再慢慢刪除。

### 12.1 保留並精簡

| 現有部分 | v2 處理 |
|---|---|
| `adapters/` | 摘取 registry、template 與 custom command 概念，重新實作為角色通用能力 |
| `maw_paths.py` | 摘取路徑正規化與 project／workflow root 概念 |
| `targets.json` | 保留多專案設定概念，升級 schema |
| 目標專案 scaffold | 依 v2 契約重建 |
| FastAPI | 可繼續作為本機 UI server |
| `/ws/maw` | 只保留 UI 狀態通知 |
| subprocess 管理 | 摘取 timeout、process group termination 與 stdout 診斷 |
| target validation | 依 v2 契約重新實作 |
| Git 安全檢查 | 摘取 branch、diff、commit 前檢查 |
| macOS folder picker | 可窄幅搬移 |
| mock target／E2E 方法 | 依 v2 schema 重建 fixture |

### 12.2 不直接改造的舊核心

下列檔案不作「瘦身後沿用」，而是在 v2 完成後由新模組完全取代：

| 舊檔案 | 原因 |
|---|---|
| `loop_orchestrator.py` | 內含大量 v1 Council、Context、Executor、Reviewer 與恢復假設 |
| `export.py` | 綁定 conversation、Stage 1／2／3 與 context audit |
| `setup_api.py` | 綁定 LLM provider、API keys 與舊 adapter 安裝流程 |
| `static/index.html` | 大量 UI 綁定模型、context、Scout、Explorer 與舊 Stage |
| `council/` | 整個目錄屬於 API Council 時代 |
| `project_context.py` | 與新架構方向相反 |
| `scout.py`、`explorer.py` | Agent 自行讀取專案後不再需要 |

### 12.3 遷移期 v2 結構

新實作先放在獨立目錄，避免與 v1 import graph 混合：

```text
MAW/
├── v2/
│   ├── app.py
│   ├── workflow.py
│   ├── watcher.py
│   ├── files.py
│   ├── dispatcher.py
│   ├── git_ops.py
│   ├── adapters/
│   └── ui/
├── v2_templates/
│   ├── AGENTS.md
│   ├── TEAM_RULES.md
│   └── MAW_workflow/
├── v2_tests/
└── v2_smoke_test.py
```

切換完成後刪除 v1，將 v2 提升為正式根目錄結構並移除 `v2` 前綴。正式 release 不留下兩套入口或 `legacy/` 目錄。

若實作初期更適合 CLI，先以 CLI 驗證完整流程；UI 排在工作流與真實 Agent E2E 之後。

### 12.4 徹底刪除

```text
council/llm_provider.py
council/openrouter.py
council/direct_resolver.py
council/vendors.json
council/council.py
council/config.py
project_context.py
scout.py
explorer.py
context_smoke_test.py
```

以及其專屬測試：

```text
test_llm_provider.py
test_openrouter.py
test_direct_resolver.py
test_council.py
test_project_context.py
test_scout.py
test_explorer.py
test_context_api.py
```

### 12.5 舊文件

下列 context／API Council 時代文件不留在 active docs：

```text
CONTEXT_AWARE_COUNCIL_REFACTOR_PLAN.md
CONTEXT_RELEASE_HARDENING_PLAN.md
CONTEXT_RELIABILITY_PLAN.md
docs/CONTEXT_GOVERNANCE.md
docs/PHASE7_UI_CHECKLIST.md
docs/PHASE8_UI_CHECKLIST.md
```

若需要保留歷史，統一移入：

```text
docs/archive/api-council-era/
```

README、`FINAL_SPEC.md`、`implementation_plan.md`、`OPTIMIZATION_PLAN.md` 必須同步重寫或退役，不能留下現行架構仍支援模型 API 的錯誤描述。

---

## 13. 舊資料與設定清理

### 13.1 `.env`

刪除 MAW 專屬的：

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
```

保留或新增：

```text
TARGET_PROJECT_PATH
MAW_MOCK_MODE
WATCHER_POLL_INTERVAL
AGENT_TIMEOUT_SECONDS
MAX_AGENT_RETRIES
MAX_REVIEW_ITERATIONS
MAW_HOST=127.0.0.1
MAW_PORT=8002
```

不得自動刪除使用者現有 `.env` 中的秘密；升級時提供一次性檢查與明確清除提示。

### 13.2 Setup state

移除 `~/.agent-cowork/setup_state.json` 中：

- `llm_test_ok`
- `llm_tested_at`
- `llm_provider`
- `vendor_routes`

新增 adapter health cache 時，必須使用新 schema version，避免誤讀舊資料。

### 13.3 舊 Conversations

舊 `data/conversations/`：

- 不匯入新工作流。
- 升級時可整體移到 `data/archive/conversations/`。
- 新程式不得依賴 Stage 1／2／3 schema。

### 13.4 Dependencies

在刪除 API provider 後重新檢查：

- 若 `httpx` 無其他用途則移除。
- 若 Agent 不使用 WebSocket，`websockets` 只保留給 FastAPI／UI 所需部分。
- 更新 `uv.lock`。

---

## 14. 安全規則

- MAW 預設只綁定 `127.0.0.1`。
- Council／Planner／Chair 階段不得修改應用程式碼。
- Reviewer 預設唯讀，不得 commit。
- 只有 Executor 可修改程式碼。
- 只有在 Reviewer APPROVE 後，Executor 才可 commit。
- Agent 只接收其角色必要的 instruction 與路徑。
- Adapter 啟動子程序時使用最小必要環境變數。
- 不把 MAW `.env` 全量傳遞給 Agent。
- 所有輸出路徑必須限制在目前 workflow 目錄。
- watcher 不跟隨可逃離 workflow／target root 的 symlink。
- 每次 dispatch 均有 timeout。
- Cancel 必須終止對應 process group 或取消 adapter invocation。
- 同一 target 同時只有一個可寫 Executor。
- `AGENTS.md`、`TEAM_RULES.md` 與 watcher 規則不得被一般工作流自行修改。

---

## 15. 實施階段

### Phase 0：建立重寫分支與隔離邊界

- 從同步的 `main` 建立 `codex/file-workflow-v2`。
- v1 進入功能凍結，只接受必要修復。
- 建立 `v2/`、`v2_tests/`、`v2_templates/`。
- 建立 v2 獨立 CLI／測試入口。
- v2 測試不得 import v1 Council、Context、Export 或 Orchestrator。

驗收：

- v1 在 `main` 仍可運作。
- v2 可獨立啟動最小入口。
- v2 import graph 與 v1 核心隔離。

### Phase 1：固定新契約

先建立 schema、純狀態邏輯與 mock，不接 UI。

- 定義 manifest、狀態、產物命名與 decision token。
- 定義 Planner 1–4 與評論矩陣。
- 定義角色重疊與序列化規則。
- 建立新 `AGENTS.md`、`TEAM_RULES.md` 模板。
- 建立 mock adapter。

驗收：

- 單元測試可從任意 Planner 數量算出正確工作項目。
- 所有狀態轉移都有合法前置條件。

### Phase 2：CLI 跑通規劃會議

- 建立新 watcher。
- 跑通 Chair clarify。
- 跑通 1–4 Planner 提案。
- 跑通交叉評論。
- 跑通 Chair synthesis。
- 跑通使用者問答與 plan approval。

驗收：

- 不呼叫任何模型 API。
- 不由 MAW 讀取專案內容。
- mock Agent 可只靠檔案完成 Gate #1。
- watcher 重啟後不重複已完成工作。

### Phase 3：CLI 跑通完整後半段

- 以新 dispatcher 觸發 Executor／Reviewer。
- 產出版本化 walkthrough 與 review。
- 支援 REQUEST_CHANGES 循環。
- Reviewer APPROVE 後由 Executor commit。
- Chair 完成最終檢查。

驗收：

- 前後段使用同一套 roster、dispatch、state 與 artifact 原則。
- v2 不依賴 `loop_orchestrator.py`。
- 全 mock CLI 可完成到 commit 與 Chair final check。

### Phase 4：接入真實 Agent

至少驗證：

- 一個 Agent 分飾全部角色。
- 三個不同 Agent 擔任 Chair／Planner／Executor Reviewer。
- 同一 Agent 承擔兩個 Planner 席位。
- 四位 Planner 的 4 份提案與 12 份評論。
- Agent 未開啟時 preflight 阻擋。
- Agent 中途失敗後可 retry。
- MAW／watcher 重啟恢復。

至少一條真實完整流程必須完成到 commit，才可刪除舊架構。

### Phase 5：重新建立 UI

- UI 直接建立與操作 v2 workflow。
- 新 roster UI 成為唯一入口。
- 新 workflow artifact UI 取代 Council Stage UI。
- `/ws/maw` 只推送 watcher 狀態與產物更新。
- 不移植舊 UI 的模型、context、Scout、Explorer 區塊。

驗收：

- 不開 CLI 也能完成完整 v2 工作流。
- UI 不依賴任何 v1 API。

### Phase 6：切換 Gate

必須全部成立：

- v2 全 mock E2E 通過。
- v2 真實 Agent E2E 完成到 commit。
- clarification、plan approval、REQUEST_CHANGES、restart recovery 全部通過。
- 安裝、preflight 與新專案 scaffold 通過。
- 使用者確認 v2 可取代 v1。

### Phase 7：一次性取代 v1

- 將 v2 提升為正式根目錄入口。
- 刪除 §12.4 所列檔案。
- 移除所有 import、route、env、setup state、UI、測試與文件殘留。
- 移除 conversation export。
- 移除 context audit 與 auto-approve context policy。
- 移除 chairman API final summary。
- 更新依賴與 lock file。
- 重寫 README、Final Spec 與安裝說明。
- 提供升級／清理指南。
- 完成全測與真實 E2E。
- 驗證 loopback-only。
- 驗證 repo 無 API Council 活躍程式碼。
- 不保留 legacy 目錄、舊入口或長期 feature flag。
- 建立乾淨 release commit 並合併回 `main`。

---

## 16. 建議 Commit／PR 切分

```text
01 create isolated v2 skeleton and test boundary
02 workflow schema + templates + state tests
03 watcher planning phases + mock adapters
04 planner matrix + peer comments + chair synthesis
05 user clarification + plan approval gates
06 executor-reviewer-revision-commit loop
07 real agent adapters + preflight
08 new roster/artifact UI
09 v2 replacement readiness audit
10 replace v1 and remove API council/context stack
11 migration cleanup, docs, dependency cleanup
```

每一批都必須：

- 可獨立測試。
- 不混入無關重構。
- 明確列出新增與刪除。
- 合併後主流程仍可運作。

---

## 17. 測試策略

### 17.1 狀態與排程

- 每個合法狀態轉移。
- 非法跳階阻擋。
- dispatch idempotency。
- watcher restart recovery。
- timeout、retry、cancel。
- per-agent 序列化。
- per-target Executor lock。

### 17.2 Planner 組合

- 1 Planner：1 proposal、0 comments。
- 2 Planners：2 proposals、2 comments。
- 3 Planners：3 proposals、6 comments。
- 4 Planners：4 proposals、12 comments。
- 同 Agent 多席位。
- 不同 Agent 並行。
- 缺少任一必要評論時不得進入 Chair synthesis。

### 17.3 使用者 Gate

- Chair 提問後停止。
- 使用者回答後恢復。
- final plan 未批准不得執行。
- request changes 返回 Chair。
- cancel 為終止狀態。

### 17.4 Executor／Reviewer

- walkthrough 才能觸發 review。
- review token 嚴格解析。
- REQUEST_CHANGES 產生新 iteration。
- 舊 walkthrough／review 不被覆寫。
- 超過最大循環交還使用者。
- 未 APPROVE 不得 commit。
- commit 後才喚醒 Chair。

### 17.5 E2E

至少保留：

1. 全 mock 單 Agent 完整流程。
2. 全 mock 三 Agent 完整流程。
3. 四 Planner 評論矩陣。
4. 一次 REQUEST_CHANGES 後 APPROVE。
5. Chair clarification。
6. watcher crash/restart。
7. 真實 Agent 完整流程。

測試數量不是目標；狀態、交接與恢復行為才是目標。

---

## 18. 剝離驗證

Active code 不得再出現：

```bash
rg -n -i \
  "litellm|openrouter|query_model|DEFAULT_COUNCIL_MODELS|DEFAULT_CHAIRMAN_MODEL|build_context_pack|context_audit|scout_suggestions|run_explorer_brief|Karpathy Stage" \
  --glob '!docs/archive/**'
```

檢查舊路由：

```bash
rg -n \
  "/api/setup/llm-models|/api/setup/test-llm|/api/maw/context" \
  --glob '!docs/archive/**'
```

完整驗證：

```bash
uv run python -m unittest discover -q
uv run python smoke_test.py
```

若專案恢復使用 pytest，亦應執行：

```bash
uv run pytest -q
```

---

## 19. Definition of Done

改造只有在下列條件全部成立時才算完成：

```text
✓ MAW 不直接呼叫任何 LLM API
✓ MAW 不保存模型 API Key
✓ MAW 不讀取或打包目標專案原始碼給 Council
✓ Chair、Planner、Executor、Reviewer 全由本機 Agent adapter 驅動
✓ 使用者可為每次工作流自由指定所有角色
✓ Planner 數量可設定為 1、2、3、4
✓ 同一 Agent 可分飾多角且不發生重複並行呼叫
✓ Planner 提案與交叉評論數量動態正確
✓ Chair 可向使用者提問並等待回答
✓ final_plan.md 未經批准不會啟動 Executor
✓ Executor／Reviewer 可完成多輪修改與再審
✓ Reviewer APPROVE 前不會 commit
✓ Executor commit 後由 Chair 做最終檢查
✓ watcher 重啟後可從檔案恢復且不重複已完成工作
✓ 所有重要歷程皆保留為人類可讀檔案
✓ 前半段會議與後半段實作使用同一套工作流原則
✓ API Council、Provider、Context、Scout、Explorer 程式與 UI 已徹底移除
✓ v2 核心未依賴 v1 Council、Context、Export 或 Orchestrator
✓ 正式版本沒有 legacy 目錄、雙入口或長期 feature flag
✓ README、規格、安裝流程只描述新架構
✓ 至少一條真實 Agent 流程完成到 commit
```

---

## 20. 最終架構

```text
MAW UI
  ├─ 選擇專案與角色
  ├─ 建立 workflow
  ├─ 顯示檔案與狀態
  └─ 接收使用者決定
          ↓
      watcher.py
  ├─ 看狀態
  ├─ 看產物
  ├─ 選下一個角色
  └─ 呼叫 adapter
          ↓
     本機 Agent
  ├─ 自行讀專案
  ├─ 完成角色任務
  └─ 寫入指定產物
```

MAW 的核心不再是「AI Council Engine」，而是：

> 一個讓使用者自由安排本機 Agent，並以透明檔案完成規劃、實作、審查與交接的極簡工作流工具。
