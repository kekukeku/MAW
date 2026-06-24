# MAW 全面本機化與多進程架構改造方案

> **Version**: 1.0  
> **Status**: 設計草案 — 待審核後分階段執行  
> **取代範圍**: Phase 6–8 的 context-aware API Council 路線（`project_context` / Scout / Explorer / `llm_provider` 整線退役）  
> **North Star**: MAW 只做 **編排器（Orchestrator）** 與 **通訊中樞（WebSocket Hub）**；所有「思考」與「讀檔」發生在使用者本機已安裝的 Agent 進程內，由作業系統權限邊界保護，**零外部 LLM API、零 Token 計費、零預先打包上下文**。

---

## 0. 為什麼要改（問題陳述）

現行架構的核心假設是：**Council 透過 HTTP 呼叫遠端模型，MAW 負責把目標專案「讀進 Prompt」**。

實務上這條路徑有結構性缺陷：

| 問題 | 現況 | 本機化後 |
|------|------|----------|
| Token 成本 | L0–L3 context pack + 三階段多模型呼叫，費用不可控 | Agent 自行讀檔，MAW 不計 Token |
| 讀檔複雜度 | `project_context`（~1300 LOC）+ Scout + Explorer + 截斷/審計 | 刪除；Agent 用本機工具讀 `target/` |
| 權限透明度 | MAW 代讀再轉發，使用者難以感知實際觸及範圍 | Agent 進程 = 使用者 OS 身份，路徑由啟動參數明示 |
| 架構厚重 | Council API 層與 Executor 層兩套啟動/通訊模式 | 統一 `adapters/` 註冊表 + 同一 WebSocket 協定 |
| 可行性 vs 可用性 | 技術上可行，但無人願意維護 API 金鑰與 context 治理 | 與使用者已有 Agent 工具鏈對齊 |

**結論**：保留 MAW 的 **工作流狀態機、雙重人工閘門、匯出契約、WebSocket 即時日誌**；替換 **Council 的資訊來源與執行載體**。

---

## 1. 目標架構（Target Architecture）

### 1.1 一句話

```text
User → MAW Hub (FastAPI + WS) → N 個本機 Agent 子進程（Council ×3 + Executor + Reviewer）
         ↓ 僅傳遞路徑與任務元資料，不讀檔、不呼叫外部 API
     <target-project>/MAW_workflow/  （狀態與產物 SSOT）
```

### 1.2 分層職責

```mermaid
flowchart TB
  subgraph UI["static/index.html"]
    P0[Panel 0: 專案與 Agent 設定]
    P1[Panel 1: 任務與 Council 編排]
    P2[Panel 2: Gate #1 計畫審閱]
    P3[Panel 3–5: 執行 / 日誌 / 提交]
  end

  subgraph Hub["MAW Hub — main.py + loop_orchestrator.py"]
    FSM[Workflow FSM]
    ROOM[Council Room 轉發]
    LOCK[Resource Lock]
    EXP[export.py]
  end

  subgraph Agents["本機 Agent 進程（subprocess）"]
    PROP[Proposer]
    CRIT[Council Reviewer]
    CHAIR[Chairman]
    EXEC[Executor]
    REV[Reviewer]
  end

  WS["/ws/maw — 會議大廳 + 日誌頻道"]

  UI --> Hub
  Hub --> WS
  WS <--> PROP & CRIT & CHAIR & EXEC & REV
  PROP & CRIT & CHAIR -.->|讀寫 target/| TARGET["&lt;target-project&gt;/"]
  EXEC & REV -.-> TARGET
  Hub --> EXP --> TARGET
```

| 層級 | 職責 | 不做 |
|------|------|------|
| **MAW Hub** | 啟動/監控子進程、WebSocket 路由、工作流 FSM、Gate 閘門、寫入 `MAW_workflow/` | LLM 推論、遞迴讀檔、Prompt 工程、Token 計算 |
| **Council Agents** | 讀取 `target_path`、產出方案/評議/主席決議、透過 WS 廣播 | 直接改 workflow 狀態（由 Hub 寫入） |
| **Executor / Reviewer** | 與現行相同，執行 TASK、產出 REVIEW | — |
| **Target Project** | `MAW_workflow/` 為任務 SSOT；程式碼變更在專案根目錄 | — |

### 1.3 Council 三角色對照（概念映射）

| 原 Karpathy 階段 | 本機角色 | 進程數 | 行為 |
|------------------|----------|--------|------|
| Stage 1 獨立提案 | **Proposer** | 1+（可配置多 Proposer 再合併） | 讀 target，產出初步實作計畫 |
| Stage 2 匿名互評 | **Council Reviewer** | 1+ | 讀 Stage 1 產出 + target，評議/排序 |
| Stage 3 主席綜述 | **Chairman** | 1 | 彙整為可匯出的最終計畫 Markdown |

> **設計決策**：預設 **3 進程各 1 實例**（符合「三方會議」）。若使用者僅有單一 Agent 執行檔，Hub 以 **Resource Lock 序列化** 依序啟動同一 binary 三次、以 `role` 環境變數區分（見 §4.3）。

---

## 2. 通訊協定（Agent Protocol）

### 2.1 統一 WebSocket：`/ws/maw`

擴展現有 global WebSocket（`main.py`、`loop_orchestrator._broadcast`），新增 **room** 概念：

| Channel | 訂閱者 | 用途 |
|---------|--------|------|
| `workflow:{task_num}` | UI、Executor、Reviewer | 現行 log/status（保留） |
| `council:{session_id}` | Proposer、Council Reviewer、Chairman、UI | 會議訊息串流 |

Agent 進程以 **WS Client** 連線 `ws://127.0.0.1:8002/ws/maw`（與 UI 相同 endpoint），啟動後第一則訊息：

```json
{
  "action": "join",
  "room": "council:abc123",
  "role": "proposer",
  "agent_id": "grok_build",
  "pid": 12345
}
```

### 2.2 會議訊息類型（Hub ↔ Agent ↔ UI）

```json
{ "type": "council.session_start", "session_id": "...", "target_path": "/abs/path", "prompt": "...", "roles": ["proposer","critic","chairman"] }

{ "type": "council.stage_begin", "stage": "propose" | "review" | "synthesize" }

{ "type": "council.message", "role": "proposer", "stage": "propose", "content": "...", "seq": 1 }

{ "type": "council.stage_complete", "stage": "propose", "artifact_path": "MAW_workflow/PLANNING/council_staging/propose.md" }

{ "type": "council.final_plan", "markdown": "...", "ready_for_gate1": true }

{ "type": "resource.granted", "token": "infer-7", "holder": "proposer" }
{ "type": "resource.release", "token": "infer-7" }

{ "type": "agent.log", "role": "proposer", "line": "..." }
{ "type": "agent.exit", "role": "proposer", "code": 0 }
```

**Hub 轉發規則**：

- Agent 發送的 `council.message` → 廣播至同 room 所有訂閱者 + 寫入 `data/council_sessions/{session_id}.jsonl`
- UI 只讀，不參與推理
- Chairman 發送 `council.final_plan` → Hub 寫入 conversation 記錄 → `COUNCIL_PENDING_APPROVAL`

### 2.3 啟動參數（CLI 契約）

每個 Agent 由 Hub 透過 `asyncio.create_subprocess_exec` 啟動：

```bash
<agent_binary> \
  --maw-role proposer \
  --maw-room council:abc123 \
  --maw-ws ws://127.0.0.1:8002/ws/maw \
  --target-path /abs/path/to/project \
  --prompt-file MAW_workflow/PLANNING/session_abc/prompt.txt \
  --output-file MAW_workflow/PLANNING/session_abc/propose.md
```

Agent 實作可為 Python / Node / 任意語言；MAW 只要求 **支援上述 CLI + WS client**（可提供 `adapters/templates/council/agent_stub.py.tpl` 參考實作）。

---

## 3. 徹底剝離清單（Deletion Manifest）

> **原則**：刪除檔案 + 刪除 import + 刪除測試 + 刪除 UI + 刪除文件，**不留死碼、不留 env 幽靈、不留「暫時 deprecated」**。

### 3.1 整檔刪除（Python / 設定 / 測試）

| 路徑 | 理由 |
|------|------|
| `council/llm_provider.py` | 外部 API 路由 |
| `council/openrouter.py` | OpenRouter 客戶端 |
| `council/direct_resolver.py` | Direct API 探測 |
| `council/vendors.json` | 供應商端點表 |
| `council/council.py` | Karpathy 字串拼接 + `query_model`（由 `local_council.py` 取代） |
| `project_context.py` | Context pack 管線 |
| `scout.py` | Scout 推薦 |
| `explorer.py` | Explorer brief |
| `context_smoke_test.py` | API context E2E |
| `test_llm_provider.py` | — |
| `test_openrouter.py` | — |
| `test_direct_resolver.py` | — |
| `test_project_context.py` | — |
| `test_scout.py` | — |
| `test_explorer.py` | — |
| `test_context_api.py` | — |
| `test_council.py` | 舊 council mock 測試（改寫為 local council 測試） |

### 3.2 文件退役（移至 `docs/archive/` 或刪除）

| 路徑 | 理由 |
|------|------|
| `CONTEXT_AWARE_COUNCIL_REFACTOR_PLAN.md` | 架構已廢棄 |
| `CONTEXT_RELEASE_HARDENING_PLAN.md` | Phase 7 專屬 |
| `CONTEXT_RELIABILITY_PLAN.md` | Phase 8 專屬 |
| `docs/CONTEXT_GOVERNANCE.md` | reasonCode / riskFlags 治理 |
| `docs/PHASE7_UI_CHECKLIST.md` | Context UI 回歸 |
| `docs/PHASE8_UI_CHECKLIST.md` | 未執行即作廢 |

### 3.3 `loop_orchestrator.py` 刪除區塊（精確手術）

| 區塊 | 現行函式/行為 | 動作 |
|------|---------------|------|
| Context 收集 | `_run_council_task` 內 `build_context_pack`、`run_explorer_brief` | **刪除** |
| Auto-approve 審計 | `_can_auto_approve_council` 及 context_audit 分支 | **刪除**（Gate #1 一律人工，或僅保留「使用者勾選」） |
| Chairman API 摘要 | `_chairman_final_summary` → `query_model` | **刪除**（主席在本機進程產出） |
| Council 啟動 | 呼叫 `run_council()` | **替換**為 `LocalCouncilRunner.start()` |

**保留**：`_spawn_executor`、`_spawn_reviewer`、`_monitor_workflow`、Gate #2、`resume_unfinished`、WebSocket 廣播、git commit 流程。

### 3.4 `main.py` 刪除路由

| 路由 | 動作 |
|------|------|
| `POST /api/maw/context/preview` | 刪除 |
| `POST /api/maw/context/explorer/preview` | 刪除 |
| `POST /api/maw/context/scout/dry-run`（若有） | 刪除 |
| `GET /api/setup/llm-models` | 刪除 |
| `POST /api/setup/test-llm` | 刪除 |
| `GET /api/maw/config` 內 `councilModels` / `chairmanModel` | 刪除模型列表 |

**新增**：

| 路由 | 用途 |
|------|------|
| `GET /api/maw/agents` | 列出 registry 內 agent + 支援的 roles |
| `POST /api/maw/council/start` | 啟動本機 council session（或合併進 `conversations/new`） |
| `GET /api/maw/council/sessions/{id}` | 會議 transcript |

### 3.5 `export.py` 簡化

刪除：

- `_render_context_summary`、`contextPack` / `contextAuditSummary` / `autoApprovePolicy` 匯出欄位
- 對 `project_context.build_context_audit_summary` 的依賴

保留：

- `export_to_target`、task slug、atomic lock、`PLANNING/council_NNN.md`（內容改為 **主席最終計畫 + 會議 transcript 連結**）

### 3.6 `static/index.html` 刪除 UI

| 區塊 | 行號參考（約） | 動作 |
|------|----------------|------|
| Panel 0 LLM Provider / LiteLLM / OpenRouter / Direct keys | 156–189 | **刪除** |
| Panel 0 Test Connection | `testLlm()` | **刪除** |
| Panel 1 Council model 多選 + Chairman 下拉 | 303–341, JS 718+ | **刪除** |
| Context bar / file selector / Scout toggle / Explorer toggle | 277–380+ | **刪除** |
| Gate #1 context audit card / provenance tables | 1200–1500 區段 | **替換**為 Council transcript 檢視 |

**新增 Panel 0**：

- Council 三角色 Agent 選擇（Proposer / Critic / Chairman 各選一個 registry agent）
- 「單一 Agent 序列模式」勾選（VRAM 不足時）

**新增 Panel 2 Gate #1**：

- 即時會議訊息時間軸（來自 `council:{session_id}`）
- Chairman 最終計畫 Markdown 預覽

### 3.7 環境變數與依賴

**`.env.example` 刪除**：

```env
LLM_PROVIDER
LITELLM_API_BASE
LITELLM_API_KEY
OPENROUTER_API_KEY
OPENAI_API_KEY
ANTHROPIC_API_KEY
...（所有供應商金鑰）
DEFAULT_COUNCIL_MODELS
DEFAULT_CHAIRMAN_MODEL
```

**保留**：

```env
TARGET_PROJECT_PATH
ALLOW_AUTO_COMMIT
EXECUTOR_TIMEOUT_SECONDS
REVIEWER_TIMEOUT_SECONDS
MAX_REVIEW_ITERATIONS
MAW_MOCK_MODE          # 改為：啟動 mock 本機 agent script，非 API mock
MAW_INFERENCE_SLOTS=1  # Resource Lock 並發上限
```

**`pyproject.toml`**：移除未使用的 HTTP 客戶端依賴（若僅 council 使用）；保留 `fastapi`、`uvicorn`、`websockets`、`httpx`（若 setup 仍需要）。

### 3.8 剝離驗證閘（每階段必跑）

```bash
# 不得再出現外部 LLM 相關符號
rg -i "litellm|openrouter|query_model|build_context_pack|scout_suggestions|run_explorer" \
  --glob '!docs/archive/**' --glob '!*.md'

# 測試
MAW_MOCK_MODE=1 uv run pytest -q
```

---

## 4. 新核心模組設計

### 4.1 目錄結構（改造後）

```text
MAW/
├── main.py                      # 精簡路由
├── loop_orchestrator.py         # FSM + 子進程管理
├── export.py                    # 精簡匯出
├── local_council.py             # NEW: Council session 生命週期
├── agent_protocol.py            # NEW: WS 訊息 schema / 驗證
├── resource_lock.py             # NEW: 推論槽位鎖
├── adapters/
│   ├── registry.json            # 擴充 roles
│   ├── launcher.py              # NEW: 統一 spawn
│   ├── installer.py             # 保留
│   └── templates/
│       ├── council/             # NEW: proposer/critic/chairman 腳本模板
│       ├── executor/
│       └── reviewer/
├── council/
│   ├── config.py                # 精簡：僅 MOCK_MODE、timeouts
│   └── storage.py               # 改存 council session / plan
├── data/
│   ├── council_sessions/        # NEW: *.jsonl transcripts
│   ├── conversations/         # 可併入 session 或保留 Gate #1 記錄
│   └── workflows.json
└── static/
    ├── index.html               # 精簡 UI
    └── ws-manager.js            # 擴充 room 訂閱
```

### 4.2 `adapters/registry.json` 擴充 schema

```json
{
  "id": "grok_build",
  "label": "Grok Build",
  "kind": "gui",
  "binary": {
    "council": "/Applications/Grok.app/.../grok-cli",
    "executor": null,
    "reviewer": null
  },
  "templates": {
    "council_proposer": "templates/council/grok_proposer.sh.tpl",
    "council_critic": "templates/council/grok_critic.sh.tpl",
    "council_chairman": "templates/council/grok_chairman.sh.tpl",
    "executor": "templates/executor/mock_executor.py.tpl",
    "reviewer": "templates/reviewer/mock_review.js.tpl"
  },
  "supports_roles": ["council_proposer", "council_critic", "council_chairman", "executor", "reviewer"],
  "inference_weight": "heavy"
}
```

`adapters/launcher.py` 統一：

```python
async def spawn_agent(role: str, agent_id: str, *, target_path: str, room: str, ...) -> Subprocess
```

Council 與 Executor 皆呼叫此函式。

### 4.3 `resource_lock.py` — VRAM / 推論序列化

**問題**：三個 Agent 若各載入本地 LLM，易 OOM。

**策略**（可配置）：

| 模式 | 行為 | 適用 |
|------|------|------|
| `parallel` | 三進程同時運行（預設 GUI Agent，推理在雲端帳號內） | 使用者本機 Agent 已是 CLI 客戶端 |
| `serialized` | `MAW_INFERENCE_SLOTS=1`，Proposer → Critic → Chairman 排隊 | 本機 GGUF / 單 GPU |
| `hybrid` | 同 `agent_id` 共享槽位 | 同一 binary 三角色 |

API：

```python
class InferenceLock:
    async def acquire(self, agent_id: str, role: str) -> str: ...  # returns token
    async def release(self, token: str) -> None: ...
```

Hub 在 `council.stage_begin` 前 `acquire`，`stage_complete` 後 `release`。子進程透過 WS 等待 `resource.granted` 後才開始讀檔推理。

### 4.4 `local_council.py` — 會議編排

取代 `council/council.py`：

```python
class LocalCouncilRunner:
    async def run_session(
        self,
        *,
        workflow_id: str,
        target_path: str,
        prompt: str,
        roster: CouncilRoster,  # proposer_id, critic_id, chairman_id
        mode: Literal["parallel", "serialized"] = "parallel",
    ) -> CouncilResult:
        """
        1. 建立 session_id + room
        2. stage propose: spawn proposer(s), 等待 stage_complete
        3. stage review: spawn critic(s), 等待 stage_complete
        4. stage synthesize: spawn chairman, 等待 council.final_plan
        5. 寫入 conversation + 返回 Gate #1 資料
        """
```

**不再包含**：`build_prompt_envelope`、`query_models_parallel`、`parse_rankings_from_text`（評議邏輯在 Agent 內或 staging 檔案）。

### 4.5 Workflow Handoff（前後段銜接）

```text
Panel 1 Start
  → LocalCouncilRunner.run_session()
  → state: COUNCIL_RUNNING
  → state: COUNCIL_PENDING_APPROVAL
Gate #1 Approve
  → export_to_target()  # council_NNN.md = chairman plan
  → _spawn_executor()   # 不變
  → ... reviewer → Gate #2 → commit
```

**關鍵**：`export_to_target` 的 `council_NNN.md` 來源改為 `council.final_plan` 的 Markdown，而非 API Stage 3 response。

---

## 5. 實施階段（Phased Migration）

> **禁止 big-bang**：每階段可合併 main、可跑測試、可回滾。

### Phase L0 — 協定與 Mock Agent（1–2 週）

| 項目 | 內容 |
|------|------|
| 新增 | `agent_protocol.py`、`local_council.py`、`adapters/launcher.py`、`templates/council/mock_*.py` |
| 擴充 | `/ws/maw` room 訂閱、`ws-manager.js` |
| Mock | 三個 mock agent 透過 WS 走完 propose → review → synthesize |
| 測試 | `test_local_council.py`（不依賴 API、不依賴 project_context） |
| 不刪 | 舊 council 路徑，以 `MAW_LOCAL_COUNCIL=1` feature flag 切換 |

**驗收**：`MAW_LOCAL_COUNCIL=1 MAW_MOCK_MODE=1` 可跑完 Gate #1。

### Phase L1 — Orchestrator 切換（1 週）

| 項目 | 內容 |
|------|------|
| 修改 | `loop_orchestrator._run_council_task` 預設走 `LocalCouncilRunner` |
| 修改 | `approve_council` 讀取 session 產物 |
| 新增 | Panel 2 transcript 最小 UI |
| 測試 | 改寫 `test_e2e_workflow.py` |

**驗收**：mock local council 全鏈路到 export。

### Phase L2 — 剝離 LLM 層（3–5 天）

| 項目 | 內容 |
|------|------|
| 刪除 | §3.1 LLM 相關檔案 |
| 刪除 | `setup_api` LLM 函式、Panel 0 LLM UI |
| 刪除 | `test_llm_*`、`test_openrouter`、`test_direct_resolver` |
| 驗證 | `rg` 零殘留 |

### Phase L3 — 剝離 Context 層（1 週）

| 項目 | 內容 |
|------|------|
| 刪除 | `project_context.py`、`scout.py`、`explorer.py` 及測試 |
| 刪除 | context API 路由、UI context bar |
| 刪除 | `export.py` context 區塊 |
| 刪除 | `context_smoke_test.py`、Phase 6–8 文件 |
| 修改 | `README.md` 架構說明 |

**驗收**：repo 體積減 ~55%；pytest 全綠。

### Phase L4 — Registry 統一與真實 Agent（持續）

| 項目 | 內容 |
|------|------|
| 實作 | 各 GUI agent 的 council 啟動腳本（與 executor 同安裝流程） |
| 實作 | `resource_lock` 序列模式實測 |
| 文件 | 單一 `docs/LOCAL_AGENT_SETUP.md` |

### Phase L5 — 收尾

| 項目 | 內容 |
|------|------|
| 刪除 | `MAW_LOCAL_COUNCIL` flag（僅留一條路徑） |
| 刪除 | `council/config.py` 內 `AVAILABLE_MODELS` |
| 更新 | `FINAL_SPEC.md`、`README.md`、`implementation_plan.md` |
| 歸檔 | `docs/archive/context-era/` |

---

## 6. 測試策略（改寫後）

| 類型 | 檔案 | 數量估計 |
|------|------|----------|
| Local council 單元 | `test_local_council.py` | ~15 |
| Agent protocol | `test_agent_protocol.py` | ~10 |
| Resource lock | `test_resource_lock.py` | ~5 |
| Launcher | `test_adapters.py`（擴充） | +5 |
| Orchestrator | `test_orchestrator.py`（精簡） | ~10 |
| WebSocket room | `test_websocket.py`（擴充） | +4 |
| E2E mock | `test_e2e_workflow.py` | 1–3 |
| Export | `test_export.py`（精簡） | ~8 |
| Safety / gates | `test_safety.py` | ~6 |

**目標**：~60–70 測試（較現行 154 少，但覆蓋 **新架構關鍵路徑**）。質優於量。

---

## 7. 風險與緩解

| 風險 | 緩解 |
|------|------|
| Agent binary 路徑各機不同 | registry `binary` 可為 null，Panel 0 讓使用者填絕對路徑，寫入 `~/.agent-cowork/agents.json` |
| WS client 實作負擔 | 提供官方輕量 `maw_agent_sdk`（Python + Node 各 <100 LOC） |
| 三進程同時 GUI 搶焦點 | 預設 headless CLI 模式；GUI 僅 executor |
| 剝離遺漏 import 導致 runtime 爆炸 | Phase L2/L3 末尾跑 `rg` + `python -m compileall` + 全測 |
| Gate #1 審計能力消失 | 改為 **transcript 審計**（完整 WS log），可搜尋 role/stage |
| 舊 conversation JSON 不相容 | 一次性 migration script 或版本欄位 `schema_version: 2` |

---

## 8. 完成定義（Definition of Done）

改造完成當且僅當：

```text
✓ rg 全 repo 無 litellm / openrouter / build_context_pack / query_model 殘留
✓ .env.example 無任何 API 金鑰欄位
✓ Council 僅透過本機子進程 + WebSocket 完成三階段
✓ MAW Hub 不讀取 target 原始碼檔案（僅驗證路徑存在與 MAW_workflow 契約）
✓ Executor / Reviewer 與 Council 共用 adapters/launcher
✓ Gate #1 / Gate #2 人工閘門仍有效
✓ export → executor → reviewer → commit 全鏈路在 MAW_MOCK_MODE=1 通過
✓ Phase 6–8 context 文件已歸檔或刪除
✓ README 描述本機多進程架構
```

一句話標準：

```text
MAW 成為輕量編排器：使用者用自己信任的本機 Agent 開會與幹活，
MAW 只負責啟動、排隊、轉發、記錄、閘門與匯出。
```

---

## 9. 建議首批 PR 切分

```text
PR-L0a  agent_protocol + WS room 擴充 + tests
PR-L0b  local_council mock agents + feature flag
PR-L1   orchestrator 切換 + Gate #1 transcript UI
PR-L2   刪除 LLM 層（含 setup + Panel 0）
PR-L3   刪除 context 層（含 export + docs archive）
PR-L4   resource_lock + serialized mode
PR-L5   registry 統一 + 真實 agent 模板 + README
```

---

## 10. 與使用者大綱的對照

| 使用者要點 | 本方案對應 |
|------------|------------|
| 外部 API 完全替換為本機多進程 | §1、§4、`local_council.py` |
| 徹底刪除 llm_provider / direct_resolver / 金鑰 | §3.1、§3.7 |
| 三 Agent WS 會議大廳 | §2、`council:{session_id}` |
| adapters 統一會議與實作 Agent | §4.2、`launcher.py` |
| export → executor 銜接不變 | §4.5 |
| Resource Lock 防 OOM | §4.3、`MAW_INFERENCE_SLOTS` |
| 厚重架構剝離 | §3（~55–65% 程式移除）、§5 分階段 |

---

*文件作者：MAW 架構改造草案 v1.0 — 待 Kevin 審核後進入 Phase L0。*