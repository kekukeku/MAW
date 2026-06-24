# MAW v2 本機多 Agent 極簡實作計畫

> **Version**: 3.0
> **Status**: 正式實作方案
> **策略**: 同一 repository、獨立分支、全新 v2 核心
> **核心**: `TEAM_RULES.md` + `watcher.py` + adapters + workflow artifacts

---

## 1. 決策摘要

MAW v2 不在舊架構上逐層改造，而是在同一個 MAW repository 內重新實作。

新架構沿用先前已在 `pixel-agent-desk` 驗證成功的協作原理：

```text
規則檔定義角色與交接
    ↓
Agent 完成工作並寫入產物
    ↓
watcher 看到狀態與產物
    ↓
adapter 喚醒下一位 Agent
```

MAW v2 的責任只有：

1. 讓使用者選擇目標專案與角色。
2. 建立工作流目錄、規則與任務檔。
3. 由 watcher 判斷下一個工作項目。
4. 由 adapter 喚醒指定 Agent。
5. 顯示產物、狀態與需要使用者決定的事項。

MAW 不負責：

- 呼叫模型 API。
- 保存模型 API Key。
- 讀取並打包專案原始碼。
- 替 Agent 組 context。
- 維護 Agent 專用 WebSocket。
- 保存 Agent 對話。
- 代替 Chair、Planner 或 Reviewer 做判斷。

Agent 是否在自身內部使用本機模型、訂閱服務或雲端能力，由使用者與該 Agent 管理。

---

## 2. 開發與切換策略

### 2.1 同 Repo 重寫

從同步的 `main` 建立：

```text
codex/file-workflow-v2
```

重寫期間：

- v1 保持可運作，但停止新增功能。
- v2 放在獨立目錄與測試入口。
- v2 不 import v1 的 Council、Context、Export 或 Orchestrator。
- 不為了相容舊 conversation／Stage schema 改壞新設計。
- 只摘取少數已驗證的通用能力。

正式切換前，v1 不刪除。

正式切換後：

- v2 提升為正式根目錄結構。
- v1 一次性刪除。
- 不保留 `legacy/`。
- 不保留雙入口。
- 不保留長期 feature flag。

### 2.2 可摘取資產

只摘取概念或窄幅程式片段：

- 路徑正規化與 project/workflow root。
- 多專案設定概念。
- Adapter registry 與 custom command 概念。
- subprocess timeout 與 process-group termination。
- Git branch、diff、commit 前安全檢查。
- macOS folder picker。
- FastAPI 本機控制台。
- `/ws/maw` 的 UI 狀態通知用途。
- mock target 與 E2E 測試方法。

下列舊核心不直接改造：

- `loop_orchestrator.py`
- `export.py`
- `setup_api.py`
- `static/index.html`
- `council/`
- `project_context.py`
- `scout.py`
- `explorer.py`

---

## 3. 最終工作流程

```text
使用者提出需求
  ↓
Chair 釐清需求
  ├─ 有重大疑問 → 詢問使用者並等待
  └─ 需求清楚 → 產出 chair_brief.md
  ↓
Planner 1–4 各自獨立提案
  ↓
每位 Planner 評論其他所有 Planner 的提案
  ↓
Chair 讀取所有提案與評論，產出 final_plan.md
  ↓
等待使用者批准
  ├─ REQUEST_CHANGES → Chair 修改
  ├─ CANCEL → 結束
  └─ APPROVE → 開工
  ↓
Executor 實作並產出 walkthrough
  ↓
Reviewer 審核
  ├─ REQUEST_CHANGES → Executor 補強 → 再審
  └─ APPROVE → Executor commit
  ↓
Chair 檢查完整歷程
  ├─ 無重大問題 → 通知使用者完成
  └─ 有待決事項 → 交由使用者決定
```

---

## 4. 角色與配置

每次工作流包含：

| 角色 | 數量 | 職責 |
|---|---:|---|
| Chair | 1 | 釐清需求、主持規劃、綜整計畫、最終檢查 |
| Planner | 1–4 | 獨立提案並評論其他 Planner |
| Executor | 1 | 實作、補強、測試、commit |
| Reviewer | 1 | 審查並回覆 APPROVE 或 REQUEST_CHANGES |

使用者每次都可重新指定 Agent。

身份可以重疊：

- 同一 Agent 可擔任 Chair 與 Planner。
- 同一 Agent 可承擔多個 Planner 席位。
- 同一 Agent 可擔任 Planner 與 Executor。
- 使用者明確選擇時，Executor 與 Reviewer 也可相同。
- 一個 Agent 可以分飾全部角色。

Planner 使用席位 ID，而不是 Agent ID：

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

同一 Agent 若承擔多個待辦，watcher 必須序列派送，不能同時喚醒同一 Agent 實例。

### 4.1 角色寫入邊界

角色名稱與實際 Agent 分離，但每種角色的寫入權限固定：

| 角色 | 可讀 | 可寫 | 禁止 |
|---|---|---|---|
| Chair | 專案、需求、所有規劃與實作歷程 | Chair brief、問題、final plan、completion | 修改程式碼、代替使用者批准 |
| Planner | 專案、需求、Chair brief、評論階段的其他 proposals | 自己席位的 proposal 與 comments | 修改程式碼、覆寫他人產物 |
| Executor | final plan、reviews、專案 | 程式碼、walkthrough、commit record | 寫 Reviewer 決策、跳過 review |
| Reviewer | final plan、walkthrough、diff、測試結果 | 自己 iteration 的 review | 修改程式碼、commit |
| 使用者／MAW UI | 所有工作流產物 | 使用者回答與 Gate decision | — |

即使同一 Agent 分飾多角，也必須依目前被喚醒的角色遵守該次寫入邊界，不能因為底層是同一 Agent 而混用權限。

---

## 5. 檔案契約

### 5.1 目標專案結構

```text
<target-project>/
├── AGENTS.md
├── TEAM_RULES.md
└── MAW_workflow/
    ├── watcher.py
    ├── ACTIVE_WORKFLOW
    ├── runtime_state.json
    ├── adapters/
    └── workflows/
        └── workflow_001/
            ├── manifest.json
            ├── request.md
            ├── chair_brief.md
            ├── questions.md
            ├── answers.md
            ├── instructions/
            ├── proposals/
            ├── comments/
            ├── final_plan.md
            ├── user_decision.md
            ├── walkthroughs/
            ├── reviews/
            ├── commit.md
            ├── completion.md
            └── events.jsonl
```

### 5.2 單一事實來源

- `manifest.json`：工作流設定快照與目前狀態；roster 等設定建立後固定，status 由 watcher 原子更新。
- 角色產物：工作是否完成的主要證據。
- `runtime_state.json`：watcher 的 dispatch、lock、attempt 與 process 狀態。
- `events.jsonl`：append-only 稽核紀錄。

不再保存：

- Council conversation database。
- Stage 1／2／3 JSON。
- Context Pack。
- Context Audit。
- 模型排名。

### 5.3 Manifest 範例

```json
{
  "schema_version": 1,
  "workflow_id": "workflow_001",
  "target_path": "/absolute/path/to/project",
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
  "created_at": "2026-06-24T12:00:00+08:00",
  "updated_at": "2026-06-24T12:00:00+08:00"
}
```

工作流開始後，roster 不因專案預設設定改變。

### 5.4 原子完成

Agent 寫入產物時：

1. 寫入 `<artifact>.tmp`。
2. 完成後 rename 成正式檔名。

正式檔案必須：

- 存在。
- 非空。
- 位於目前 workflow root。
- 不可透過 symlink 逃逸。
- 對應 dispatch 已成功完成。

failed、timeout 或仍有活躍 `.tmp` 的工作不得視為完成。

---

## 6. 工作流狀態

使用單層狀態，不建立 Phase/Sub-state 雙層模型。

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

主要轉移：

| 狀態 | 完成條件 | 下一狀態 |
|---|---|---|
| CREATED | 基本檔案完成 | CHAIR_CLARIFYING |
| CHAIR_CLARIFYING | `chair_brief.md` 完成 | PLANNING |
| CHAIR_CLARIFYING | `questions.md` 完成 | WAITING_USER_CLARIFICATION |
| WAITING_USER_CLARIFICATION | 使用者寫入 `answers.md` | CHAIR_CLARIFYING |
| PLANNING | 所有 proposal 完成 | PEER_REVIEW |
| PEER_REVIEW | 所有 comment 完成 | CHAIR_SYNTHESIS |
| CHAIR_SYNTHESIS | `final_plan.md` 完成 | WAITING_USER_APPROVAL |
| WAITING_USER_APPROVAL | APPROVE | EXECUTING |
| WAITING_USER_APPROVAL | REQUEST_CHANGES | CHAIR_CLARIFYING |
| EXECUTING | 新 walkthrough 完成 | REVIEWING |
| REVIEWING | REQUEST_CHANGES | REVISION_REQUIRED |
| REVISION_REQUIRED | 新 walkthrough 完成 | REVIEWING |
| REVIEWING | APPROVE | COMMITTING |
| COMMITTING | `commit.md` 完成 | CHAIR_FINAL_CHECK |
| CHAIR_FINAL_CHECK | 完成報告無阻塞 | COMPLETED |
| CHAIR_FINAL_CHECK | 尚需使用者決定 | WAITING_USER_DECISION |

任何非終止狀態都可以進入 `CANCELLED` 或 `FAILED`。

---

## 7. Expected Artifact Set

Watcher 不以目錄檔案數量推測完成，而是根據 manifest 計算本階段必須完成的完整產物集合。

三位 Planner：

```text
Expected proposals:
- proposals/planner_a.md
- proposals/planner_b.md
- proposals/planner_c.md

Expected comments:
- comments/planner_a_on_b.md
- comments/planner_a_on_c.md
- comments/planner_b_on_a.md
- comments/planner_b_on_c.md
- comments/planner_c_on_a.md
- comments/planner_c_on_b.md
```

Planner 數量為 `N`：

```text
proposal 數 = N
comment 數 = N × (N - 1)
```

| Planner 數 | Proposal | Comment |
|---:|---:|---:|
| 1 | 1 | 0 |
| 2 | 2 | 2 |
| 3 | 3 | 6 |
| 4 | 4 | 12 |

完整交叉評論是正式流程，不提供 `linear`、`chair`、`none` 等模式，以免增加分支與改變產品定義。

---

## 8. 各角色產物

### 8.1 Chair 釐清

Chair 讀取：

- `request.md`
- 目標專案
- `AGENTS.md`
- `TEAM_RULES.md`

Chair 二選一：

- 需求清楚：產出 `chair_brief.md`。
- 存在會實質改變方案的歧義：產出 `questions.md`。

`chair_brief.md` 至少包含：

- 使用者目標。
- 已知限制。
- 非目標。
- Planner 應調查的問題。
- 建議調查起點。

建議調查起點只放路徑、檔名或命令，不放原始碼內容。這能減少各 Planner 無差別掃描，又不讓 MAW 重新成為 Context Pack。

### 8.2 Planner 提案

每位 Planner 在自己的 proposal 完成前，不讀其他 Planner 的草稿。

每份 proposal 至少包含：

- 現況理解。
- 建議方案。
- 修改與刪除範圍。
- 實施順序。
- 風險。
- 驗證方式。
- 需要 Chair 決定的事項。

### 8.3 Planner 評論

所有 proposal 到齊後，每位 Planner 評論其餘每一份 proposal。

每份 comment 至少包含：

- 同意之處。
- 遺漏之處。
- 不合理或風險過高之處。
- 建議 Chair 吸收的具體內容。

不匿名、不排名、不計算平均分數。

### 8.4 Chair 綜整

Chair 讀取：

- 使用者需求與回答。
- `chair_brief.md`。
- 所有 proposals。
- 所有 comments。
- 必要的目標專案內容。

Chair 產出可直接交給 Executor 的 `final_plan.md`：

1. 目標與完成定義。
2. 保留內容。
3. 刪除內容。
4. 新增與修改內容。
5. 檔案級清單。
6. 實施順序。
7. 安全限制。
8. 測試與驗收。
9. 回滾界線。
10. 非目標。

若綜整時發現新的重大歧義，回到使用者釐清，不自行猜測。

### 8.5 使用者 Gate

`final_plan.md` 完成後必須等待：

```text
DECISION: APPROVE
DECISION: REQUEST_CHANGES
DECISION: CANCEL
```

只有使用者或 MAW UI/CLI 可以寫入此 Gate。Agent 不得自行批准計畫。

### 8.6 Executor

Executor 依 `final_plan.md` 實作，完成後產出版本化 walkthrough：

```text
walkthroughs/walkthrough_001.md
walkthroughs/walkthrough_002.md
```

Walkthrough 至少包含：

- 實際修改。
- 與 final plan 的差異及原因。
- 刪除內容。
- 測試命令與結果。
- 未解問題。
- branch 與 commit 前狀態。

Executor 在交給 Reviewer 前必須完成自我檢查，並寫入 walkthrough：

- 每項 acceptance criterion 是否完成。
- 未完成或偏離事項及理由。
- 必要測試是否全部執行。
- 使用者可見變更是否更新文件。
- 是否引入未授權 breaking change。
- 是否出現不必要的 dependency 或 lockfile churn。

### 8.7 Reviewer

Reviewer 讀取：

- `final_plan.md`
- 最新 walkthrough
- 實際 git diff
- 測試結果
- 前輪 review

Reviewer 產出：

```text
DECISION: APPROVE
```

或：

```text
DECISION: REQUEST_CHANGES
```

REQUEST_CHANGES 必須是可執行、可驗證的修正事項。

Review 內容必須分級：

- **Blocking Issues**：功能錯誤、測試失敗、安全問題、未達 acceptance criteria 或違反明確架構邊界。
- **Non-Blocking Notes**：不影響本次正確性的設計建議、風格意見或取捨提醒。
- **Optional Follow-ups**：適合留到未來任務的改善。

只有 Blocking Issues 可以導致 `REQUEST_CHANGES`。Reviewer 不得因個人偏好或非必要重構阻擋完成。

允許的 Reviewer decision 僅有：

```text
DECISION: APPROVE
DECISION: REQUEST_CHANGES
DECISION: REJECT
```

- `APPROVE`：進入 commit。
- `REQUEST_CHANGES`：回到 Executor 補強。
- `REJECT`：停止自動循環，交回 Chair 與使用者。
- 缺少、重複或未知 decision：保持等待並回報格式錯誤，不得猜測。

### 8.8 Commit 與 Chair 最終檢查

Reviewer APPROVE 後才可 commit。

Executor 產出 `commit.md`：

- branch
- commit SHA
- 變更摘要
- 最終測試

Chair 讀取完整歷程與最終 diff，產出 `completion.md`。

若有小問題但不阻礙完成，交由使用者決定；若有重大疏失，重新進入修正，不得假裝完成。

Chair 最終 reconciliation 必須確認：

- `commit.md` 記載的 commit SHA 真實存在。
- final diff 與最新 walkthrough 一致。
- Reviewer 的 APPROVE 對應最新 review iteration。
- 最終測試結果存在且與 commit 對應。
- completion 連結 final plan、最新 walkthrough、最新 review 與 commit。
- 沒有殘留 `.tmp`、stale lock 或未完成 dispatch。

---

## 9. Watcher

### 9.1 實作方式

第一版使用簡單 polling，不引入 `watchdog`：

```python
while running:
    load_manifest()
    load_runtime_state()
    validate_expected_artifacts()
    transition_if_ready()
    dispatch_missing_work()
    sleep(poll_interval)
```

Polling 的優點：

- 容易理解與測試。
- macOS、Google Drive 與不同檔案系統行為較一致。
- 重啟恢復自然。
- 不需要 debounce 與事件遺失補救。

只有實測證明 polling 無法滿足需求時，才考慮事件監聽。

### 9.2 Watcher 職責

- 計算 expected artifact set。
- 驗證狀態轉移前置條件。
- 找出尚未完成的工作項目。
- 呼叫 dispatcher。
- 管理 per-agent lock。
- 管理 per-target Executor lock。
- 記錄 attempt、timeout、retry、cancel。
- 寫入 events。
- 重啟後恢復。
- 防止重複 dispatch。
- 提供無副作用 inspect 模式。

### 9.3 Watcher 不做

- 不讀原始碼做判斷。
- 不生成提案或計畫。
- 不解析自由文字猜測完成。
- 不呼叫模型 API。
- 不管理 Agent 對話。
- 不替 Reviewer 判斷。

### 9.4 Dispatch Key

```text
<workflow_id>:<kind>:<role-or-seat>:<iteration>
```

例如：

```text
workflow_001:proposal:planner_a:1
workflow_001:comment:planner_b_on_a:1
workflow_001:review:reviewer:2
```

每個 dispatch 記錄：

- key
- agent ID
- instruction path
- expected output
- attempt
- started_at
- timeout_at
- process/invocation ID
- status

已 completed 的 key 不得再次執行。

Watcher 只根據「尚未完成的 dispatch key」與合法狀態轉移派工，不因某個狀態或檔案持續存在而重複喚醒 Agent。

### 9.5 Lock

- 不同 Agent 的 Planner 工作可並行。
- 同一 Agent 的所有角色工作序列化。
- 所有 proposal 完成後才能開始評論。
- 同一 target 同時只允許一個 Executor 修改程式碼。
- watcher 重啟時，必須辨認 stale in-flight lock 並安全恢復。

不建立獨立 Resource Token Protocol。

### 9.6 Inspect 模式

Watcher 提供完全無副作用的診斷命令：

```bash
python watcher.py --inspect
```

輸出：

- workflow 與目前狀態。
- expected artifact set。
- 已完成與缺少的產物。
- active／stale locks。
- 已派送與待派送工作。
- 下一個合法狀態。
- 目前阻塞原因。

Inspect 不得：

- 寫檔。
- 改狀態。
- 取得 lock。
- 喚醒 Agent。
- 送出通知。

---

## 10. Adapter

Adapter 只負責把標準工作描述轉成特定 Agent 的喚醒方式。

標準工作描述：

```json
{
  "workflow_id": "workflow_001",
  "role": "planner",
  "seat": "planner_a",
  "target_path": "/absolute/path/to/project",
  "instruction_file": ".../instructions/planner_a.md",
  "expected_output": ".../proposals/planner_a.md"
}
```

Adapter 可以在內部使用：

- Agent CLI。
- `agentapi`。
- 本機 hook 或 inbox。
- 自訂 shell command。
- 手動引導。

檔案是 Agent 間的正式協定；不禁止 adapter 使用本機 HTTP 或其他已驗證方式喚醒 Agent。

### 10.1 MVP Trigger Mode

先支援：

```text
mock
manual
```

- `mock`：自動產生測試產物。
- `manual`：建立 instruction 並通知使用者到已開啟的 Agent 執行；watcher 等待產物。

之後逐一新增真實 Agent adapter，不先假設所有 GUI Agent 都能自動觸發。

每個真實 adapter 可依能力使用自動 CLI、`agentapi`、本機 hook 或 inbox。若自動喚醒不可用，必須能安全降級為 manual handoff：

1. 保留同一 dispatch key。
2. 建立完整 instruction file。
3. 在 UI／CLI 顯示應由哪個 Agent 執行。
4. Watcher 等待同一 expected output。
5. 不因降級而重建工作或改變角色。

### 10.2 Registry

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

這是 v2 新 schema，不視為現有 registry 的小幅擴充。

### 10.3 Preflight

開始前檢查：

- Agent 是否已註冊。
- Adapter 是否可用。
- 需要預先開啟的 App 是否正在執行。
- Agent 是否支援指定角色。
- 目標專案與 workflow root 是否可用。
- Executor 是否具必要權限。
- watcher 是否可啟動。

若任何指定 Agent 不可用，不得靜默換人或減少 Planner。

---

## 11. AGENTS.md 與 TEAM_RULES.md

### AGENTS.md

只放所有角色共通的簡短規則：

- 先讀 instruction 與專案規則。
- 只做被指派的角色工作。
- 不覆寫其他角色產物。
- 使用 tmp + rename。
- 不自行跳過使用者 Gate。
- 未授權時不 commit。
- 阻塞時明確回報。

### TEAM_RULES.md

放完整協作治理：

- 角色責任。
- 提案與評論格式。
- 交接順序。
- Executor／Reviewer 循環。
- 使用者批准規則。
- Git 規則。
- Retry 與失敗處理。
- 同一 Agent 多角色規則。

若目標專案已有 `AGENTS.md` 或 `TEAM_RULES.md`，Scaffold 不可直接覆寫。應：

1. 保留原檔。
2. 建立建議新增區塊或 sidecar 規則檔。
3. 由使用者確認後合併。

---

## 12. UI

UI 排在 CLI、mock E2E 與真實 adapter spike 之後。

只保留：

- 專案選擇。
- Chair、Planner 1–4、Executor、Reviewer 選擇。
- Adapter preflight。
- 工作流狀態。
- 提案與評論完成矩陣。
- Artifact 閱讀。
- 回答 Chair 問題。
- 批准或退回 final plan。
- 取消工作流。
- Review loop 超限決策。
- 最終完成／待決事項。

刪除：

- LLM Provider。
- API Key。
- 模型選擇。
- Context preview。
- Scout。
- Explorer。
- Stage 1／2／3。
- 模型排名。

`/ws/maw` 只用於通知 UI 狀態與 artifact 更新。Agent 不連線。

47821 event endpoint 不屬於 v2 核心；未來若要整合 pixel-agent-desk 視覺狀態，可作為選配 adapter。

---

## 13. 安全要求

- MAW 預設只綁 `127.0.0.1`。
- Chair／Planner／Reviewer 預設不得修改應用程式碼。
- 只有 Executor 可寫程式碼。
- Reviewer APPROVE 前不得 commit。
- 使用者 Gate 不能由 Agent 寫入。
- Adapter 不繼承完整 `os.environ`。
- 使用明確 env allowlist。
- Instruction 不包含 MAW secrets。
- Expected output resolve 後必須位於 workflow root。
- 不跟隨逃逸 target/workflow root 的 symlink。
- Timeout 必須終止完整 process group 或取消 invocation。
- failed／timeout 工作不得被視為完成。
- 同一 target 同時只有一個可寫 Executor。
- 一般工作流不得修改 watcher、AGENTS 或 TEAM_RULES。
- 不使用 `shell=True` 組合未驗證的使用者輸入。
- Adapter stdout／stderr 必須保存供診斷，不可全部丟棄。
- Markdown 供人閱讀；正式狀態與 dispatch 資料使用 JSON，不以寬鬆 regex 作主要狀態來源。

---

## 14. 實作階段

### Phase 0：隔離骨架

- 建立 `codex/file-workflow-v2`。
- 建立 `v2/`、`v2_tests/`、`v2_templates/`。
- 建立獨立 CLI 與測試入口。
- 加入 v2 禁止 import v1 核心的測試。

驗收：

- v1 仍可運作。
- v2 可獨立執行最小測試。

### Phase 1：純工作流核心

實作：

- manifest schema。
- 單層狀態轉移。
- expected artifact calculator。
- atomic file helpers。
- dispatch key 與 runtime state。

測試：

- Planner 1–4。
- 合法／非法狀態轉移。
- 路徑與 symlink 安全。
- 空檔、tmp、錯誤 iteration 不得完成。

### Phase 2：Mock Watcher

實作：

- polling watcher。
- mock dispatcher。
- per-agent lock。
- per-target Executor lock。
- retry、timeout、cancel、restart recovery。
- `--inspect` 無副作用診斷。

驗收：

- Chair → Planner → comments → final plan 可全自動完成。
- watcher 重啟不重複 dispatch。

### Phase 3：第一個真實 Adapter Spike

這是提前的硬 Gate，不等 UI。

至少驗證：

- 一個真實 Agent 完成 Chair 或 Planner。
- 一個真實 Agent 完成 Executor 或 Reviewer。
- instruction 可正確傳遞。
- Agent 可自行讀目標專案。
- expected artifact 由真實 Agent 產生。
- timeout、失敗、取消可辨認。
- watcher 重啟不重複工作。

若失敗：

- 不刪 v1。
- 不切換正式入口。
- v2 保留在分支。
- 重新評估該 Agent 的喚醒方式。

### Phase 4：完整 Mock E2E

跑通：

- Chair clarification。
- Planner 1–4。
- 完整交叉評論。
- final plan 使用者 Gate。
- Executor walkthrough。
- Reviewer REQUEST_CHANGES。
- Reviewer REJECT 交回 Chair／使用者。
- Executor 補強。
- Reviewer APPROVE。
- Executor commit。
- Chair final check。

### Phase 5：真實完整 E2E

至少驗證：

- 一個 Agent 分飾全部角色。
- 三個不同 Agent 混合協作。
- 同 Agent 承擔兩個 Planner 席位。
- 四 Planner、四 proposal、十二 comment。
- 一次 REQUEST_CHANGES 後 APPROVE。
- watcher 中途重啟。
- 真實 commit 完成。

### Phase 6：極簡 UI

- Roster。
- Preflight。
- Workflow/artifact view。
- 使用者問答與批准。
- Cancel。
- Waiting user decision。

UI 必須只呼叫 v2 API。

### Phase 7：切換 Gate

全部成立才可切換：

- v2 單元測試通過。
- 完整 mock E2E 通過。
- 真實 Agent E2E 完成到 commit。
- Restart、timeout、cancel、retry 通過。
- 安裝與 scaffold 通過。
- UI 不依賴 v1。
- 使用者確認可取代 v1。

### Phase 8：一次性取代 v1

- 將 v2 提升為正式入口。
- 刪除 v1 Council、Context、Scout、Explorer。
- 刪除舊 Orchestrator、Export、Setup 與 UI。
- 刪除舊專屬測試。
- 清除舊路由、env、setup state 與 conversation schema。
- 更新依賴與 `uv.lock`。
- 重寫 README、規格與安裝說明。
- 不保留 legacy 或雙入口。
- 合併回 `main`。

---

## 15. v2 遷移期結構

```text
MAW/
├── v2/
│   ├── cli.py
│   ├── app.py
│   ├── workflow.py
│   ├── watcher.py
│   ├── files.py
│   ├── dispatcher.py
│   ├── git_checks.py
│   └── adapters/
├── v2_templates/
│   ├── AGENTS.md
│   ├── TEAM_RULES.md
│   └── MAW_workflow/
├── v2_tests/
└── v2_smoke_test.py
```

保持模組數量最少。若合併後更清楚，可以合併；不以行數或檔案數作為架構品質標準。

---

## 16. 測試清單

### 狀態與排程

- 每個合法轉移。
- 每個非法跳階。
- dispatch idempotency。
- stale lock recovery。
- timeout、retry、cancel。
- 同 Agent 序列化。
- 不同 Agent 並行。
- per-target Executor lock。
- 只有狀態轉變／未完成 dispatch 才會派工。
- inspect 不產生任何副作用。

### Artifact

- 1／2／3／4 Planner expected set。
- 空檔不完成。
- tmp 不完成。
- symlink escape 阻擋。
- 錯誤 seat／iteration 不完成。
- 缺少任一 comment 不進入 synthesis。

### Gate

- Chair 問題會停止流程。
- answers 使流程恢復。
- 未批准 final plan 不開工。
- REQUEST_CHANGES 回到 Chair。
- Agent 不能自行批准。

### Executor／Reviewer

- walkthrough 才觸發 review。
- Decision token 嚴格解析。
- Blocking／Non-Blocking／Optional 分級。
- 非 Blocking 意見不得造成 REQUEST_CHANGES。
- REJECT 停止自動循環並交回使用者。
- Review iteration 不覆寫。
- 超過上限交回使用者。
- 未 APPROVE 不 commit。
- commit 後才喚醒 Chair。

### E2E

1. 全 mock、單 Agent、全部角色。
2. 全 mock、三 Agent。
3. 四 Planner 完整評論矩陣。
4. clarification。
5. REQUEST_CHANGES 循環。
6. watcher crash/restart。
7. 真實 Agent 到 commit。

---

## 17. 切換後刪除清單

核心：

```text
council/
project_context.py
scout.py
explorer.py
loop_orchestrator.py
export.py
setup_api.py
static/index.html
context_smoke_test.py
```

舊專屬測試：

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
```

有價值的安全情境必須改寫成 v2 測試，不直接沿用綁定 v1 schema 的測試。

舊文件移至 archive 或刪除：

```text
CONTEXT_AWARE_COUNCIL_REFACTOR_PLAN.md
CONTEXT_RELEASE_HARDENING_PLAN.md
CONTEXT_RELIABILITY_PLAN.md
docs/CONTEXT_GOVERNANCE.md
docs/PHASE7_UI_CHECKLIST.md
docs/PHASE8_UI_CHECKLIST.md
```

---

## 18. 設定遷移

移除 MAW 專屬模型設定：

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

不得自動刪除使用者現有 secrets；升級工具只提示並由使用者確認。

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

舊 `data/conversations/` 不匯入新工作流，可移入 archive。

---

## 19. Definition of Done

```text
✓ MAW 不直接呼叫模型 API
✓ MAW 不保存模型 API Key
✓ MAW 不打包目標專案原始碼
✓ v2 不 import v1 核心
✓ watcher 使用簡單、可恢復的檔案輪詢
✓ Chair、Planner、Executor、Reviewer 都由 adapters 喚醒
✓ Planner 可設定 1–4 位
✓ 同一 Agent 可分飾多角且不重複並行
✓ 完整交叉評論數量正確
✓ expected artifact set 是唯一階段完成判準
✓ Chair 可詢問使用者並等待回答
✓ final_plan.md 未經批准不開工
✓ Executor／Reviewer 可多輪修正
✓ Reviewer APPROVE 前不 commit
✓ commit 後由 Chair 最終檢查
✓ 角色寫入邊界不因同一 Agent 分飾多角而失效
✓ Adapter 自動喚醒失敗時可降級為同一 dispatch 的 manual handoff
✓ Watcher inspect 模式完全無副作用
✓ Executor walkthrough 包含固定自我檢查
✓ Reviewer 意見分級且只有 Blocking Issues 可阻擋
✓ APPROVE、REQUEST_CHANGES、REJECT token 行為明確
✓ Chair 完成 commit 後 reconciliation
✓ timeout、cancel、retry、restart recovery 可用
✓ 至少一條真實 Agent 流程完成到 commit
✓ UI 不依賴 v1
✓ API Council、Context、Scout、Explorer 完全移除
✓ 正式版本沒有 legacy、雙入口或長期 feature flag
✓ README 與安裝文件只描述 v2
```

---

## 20. 第一批實作任務

核准後第一批只做 Phase 0–1：

```text
1. 建立 codex/file-workflow-v2
2. 建立 v2/、v2_tests/、v2_templates/
3. 定義 manifest schema
4. 定義單層狀態轉移
5. 實作 expected artifact calculator
6. 實作 atomic file helpers
7. 建立 Planner 1–4 測試
8. 建立禁止 import v1 核心的測試
```

第一批不做：

- UI。
- 真實 Agent 自動觸發。
- Watchdog。
- 47821。
- Git commit。
- 刪除 v1。

完成第一批並通過審查後，再進入 Mock Watcher。
