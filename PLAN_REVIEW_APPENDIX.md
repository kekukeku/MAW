# LOCAL_AGENT_ARCHITECTURE_PLAN 缺口與風險附錄

> 對 `LOCAL_AGENT_ARCHITECTURE_PLAN.md` v2.0 的獨立審查補充，不取代原文。  
> 審查基準：實際 v1 程式碼比對（2026-06-24）。

---

## A. 現況前提修正

原計畫 §0 將部分元件描述為「MAW 已具備足夠的基本元件」，但實際程式碼比對後，需修正如下：

| 原文前提 | 實際情況 | 建議修正 |
|---|---|---|
| MAW 已具備 `watcher.py` | 專案內沒有 `watcher.py`。v1 的核心編排是 `loop_orchestrator.py`，屬於 Council/Context/Executor/Reviewer 混合式 state machine | 將 watcher 明確列為 v2 全新待造核心，而非既有資產 |
| `adapters/` 可喚醒本機 Agent | 現有 `adapters/registry.json` 仍以 executor/reviewer 為中心，且 template 多為 mock | 真實 adapter 層需視為最高風險的新實作 |
| registry 已足以描述多角色能力 | 現有 registry 沒有 Chair/Planner、`supports`、`parallel_capacity`、`requires_running_app` 等欄位 | §9.2 registry schema 應標示為 v2 新 schema |
| 保留 `targets.json` | repo 內未見固定版控的 `targets.json`，實際多半為 runtime/setup state | 改稱「保留多專案設定概念」，不要假設該檔存在 |

結論：v2 不是「瘦身既有 watcher」，而是「保留少數概念後，從零建立 file-driven watcher + dispatcher + real adapter」。原架構方向正確，但 Phase 2–4 工作量不應低估。

---

## B. 最高風險：真實 Agent adapter 尚未驗證

原計畫 §9.1 將以下方式列為 adapter 可用選項：

- 現有 GUI/TUI Agent 的本機觸發方式
- `agentapi`
- Agent 自己的 CLI
- 自訂 shell command
- 已開啟 Agent 的 inbox/hook

但目前程式碼中尚未看到任何可穩定喚醒真實 Agent、傳入 instruction、等待完成並驗證產物的 adapter。

這是全案最大的技術風險。若真實 Agent 不能被穩定喚醒，Phase 1–3 的 mock E2E 即使全部通過，也無法證明 v2 可用。

建議在 Phase 4 前插入：

## Phase 3.5：單一真實 Agent adapter spike

驗證範圍：

1. 選定一個真實 Agent。
2. 由 watcher/dispatcher 傳入 instruction file 與 expected output path。
3. Agent 能讀取目標專案與 workflow 檔案。
4. Agent 能產出指定 artifact。
5. adapter 能偵測完成、timeout、失敗與取消。
6. watcher 重啟後不重複 dispatch 已完成工作。

通過條件：

- 至少一個真實 Agent 能完成 Chair 或 Planner 任務。
- 至少一個真實 Agent 能完成 Executor 或 Reviewer 任務。
- 產物不是 mock 產生，而是由真實 Agent 依 instruction 完成。

若 Phase 3.5 不通過，禁止進入 v1 刪除階段。

---

## C. Token 效益主張需補強

原計畫的核心動機之一是降低 token 與資源消耗，移除 Context Pack、Scout、Explorer，改由各 Agent 自行讀取專案。

此方向可降低 MAW 中央打包 context 的複雜度，但在大型 repo 中有一個反向風險：

> 每個 Planner 都可能各自掃描大量相同檔案，總 token 消耗可能高於集中式 context pack。

建議補充一條極簡但實用的折衷規則：

- MAW 不打包原始碼、不替 Agent 建 context pack。
- 但 Chair brief 或 instruction file 可以提供「建議調查起點」。
- 起點只包含路徑、關鍵檔名、測試命令、使用者指定線索，不包含大段原始碼。

例如：

```text
Suggested starting points:
- main.py
- loop_orchestrator.py
- adapters/registry.json
- static/index.html
- tests related to setup/context/orchestrator
```

這可維持「Agent 自行讀專案」原則，同時避免多 Agent 無差別全量掃描。

---

## D. 聚合完成偵測需明確化

原計畫 §5.3 的 tmp + rename 可以避免單一 artifact 被半寫入時誤判完成，但多檔聚合仍需更嚴格規則。

例如：

- 所有 Planner proposal 到齊後才可進入 PEER_REVIEW。
- 所有 `N × (N - 1)` comment 到齊後才可進入 CHAIR_SYNTHESIS。
- 每輪 walkthrough/review 必須使用正確 iteration。

建議 watcher 不以「目錄中有多少檔案」作為唯一判定，而是根據 manifest 與 dispatch key 建立完整 expected artifact set：

```text
expected_proposals = [
  proposals/planner_a.md,
  proposals/planner_b.md,
  proposals/planner_c.md
]

expected_comments = [
  comments/planner_a_on_b.md,
  comments/planner_a_on_c.md,
  comments/planner_b_on_a.md,
  comments/planner_b_on_c.md,
  comments/planner_c_on_a.md,
  comments/planner_c_on_b.md
]
```

轉移條件：

1. 每個 expected artifact 都存在。
2. 每個 artifact 非空。
3. 不存在對應 `.tmp` 殘留仍在寫入。
4. 對應 dispatch key 已標記 completed。
5. artifact path 必須位於目前 workflow 目錄內，且不可透過 symlink 逃逸。

---

## E. 回退判準需補上

原計畫已要求 v2 真實 Agent E2E 完成到 commit 後才可刪除 v1，這是正確的。

但仍建議明確加入失敗回退條款：

```text
若真實 Agent adapter 無法穩定完成至少一條完整流程，則：
- 不刪除 v1；
- 不切換 main 入口；
- 不移除現有測試與文件；
- 將 v2 保留在分支或實驗入口；
- 重新評估 adapter 觸發策略。
```

原因：mock E2E 驗證的是狀態機與檔案契約；真實 E2E 才驗證產品可用性。

---

## F. 角色重疊與 lock 測試需加強

原計畫支援同一 Agent 分飾多角，這符合極簡目標，但也讓 watcher 的 per-agent lock 成為關鍵正確性保證。

現有 §17 測試策略列出「同 Agent 多席位」，但還應增加跨階段測試：

1. 單一 Agent 分飾 Chair、所有 Planner、Executor、Reviewer。
2. 同一 Agent 同時分配 planner_a 與 planner_b，proposal/comment 必須序列化。
3. Executor 與 Reviewer 是同一 Agent 時，不得在 executor 尚未完成時派 reviewer。
4. Reviewer REQUEST_CHANGES 後，同一 Agent 回到 Executor，不得被舊 lock 卡死。
5. watcher crash/restart 後，in-flight lock 可恢復或安全釋放。

建議新增 E2E：

```text
全 mock 單 Agent 全角色完整流程：
CREATED
→ CHAIR_CLARIFYING
→ PLANNING
→ PEER_REVIEW
→ CHAIR_SYNTHESIS
→ WAITING_USER_APPROVAL
→ EXECUTING
→ REVIEWING
→ COMMITTING
→ CHAIR_FINAL_CHECK
→ COMPLETED
```

驗證項目：

- 無重複 dispatch。
- 無同 agent 並行。
- 無死結。
- 每個 artifact 均由正確 role/seat/iteration 產出。

---

## G. 安全規則補強

原 §14 已涵蓋多數安全規則。建議補充以下實作級要求：

1. adapter subprocess 不繼承完整 `os.environ`。
2. adapter 傳入的 env allowlist 必須明確列出。
3. instruction file 不得包含 MAW `.env`、API key、token。
4. `expected_output` 必須經 path resolve 後確認在 workflow root 內。
5. watcher 處理 cancel 時必須終止整個 process group。
6. failed/timeout artifact 不得被視為完成。
7. 使用者批准 gate 必須由 UI/CLI 寫入明確 decision token，不由 Agent 自行寫入。

---

## H. 原計畫中已驗證正確的部分

以下部分與實際 v1 程式碼比對後，判斷可直接保留：

- API Council、LLM Provider、Context Pack、Scout、Explorer 確實深度存在於 v1。
- `/api/setup/llm-models`、`/api/setup/test-llm`、`/api/maw/context/*`、`/ws/maw` 等路由存在。
- `loop_orchestrator.py` 確實綁定 Council、Context、Executor、Reviewer 與恢復假設，不適合瘦身沿用。
- `project_context.py`、`scout.py`、`explorer.py` 與新架構方向相反，適合在切換後移除。
- `export.py` 綁定 conversation、Stage 1/2/3、context audit，適合由 v2 artifact 模式取代。
- 同 repo 分支重寫、v2 不 import v1、切換 Gate 後一次性刪除，是乾淨且可控的策略。

---

## I. 建議新增至原計畫的最小修正清單

不必重寫原計畫，只需在執行前採納以下補充：

1. 將 watcher 標示為 v2 新建核心。
2. 將 real adapter spike 提前為 Phase 3.5。
3. 將 adapter registry schema 標示為 v2 新 schema。
4. 將 `targets.json` 改稱多專案設定概念。
5. 為大型 repo 加入「建議調查起點」策略。
6. 為多檔 artifact 完成加入 expected artifact set 判定。
7. 明訂 real Agent E2E 不穩定時不得刪 v1。
8. 補上單 Agent 全角色、跨階段 lock、restart lock recovery 測試。

---

## J. 附錄結論

`LOCAL_AGENT_ARCHITECTURE_PLAN.md` 的產品方向與最終架構是合理的：

> MAW 應從「中央 AI Council Engine」轉為「本機 Agent file-driven workflow coordinator」。

但原文對現況資產的描述偏樂觀。v2 成敗不在 schema 或 mock watcher，而在：

1. 真實 Agent 是否能被穩定喚醒。
2. watcher 是否能可靠管理 dispatch、lock、timeout、retry、restart。
3. 大型 repo 下是否真的降低總 token，而非把掃描成本轉嫁給每個 Agent。

因此本附錄建議：

> 保留原重寫策略，但在刪除 v1 前，必須先完成 real adapter spike 與至少一條穩定真實 Agent E2E。
