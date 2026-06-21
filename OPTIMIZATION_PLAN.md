# MAW 優化計劃 v2：真實 Agent + 一鍵安裝 + 單一整合介面

> **版本**：0.6（Direct API 自動 endpoint 路由）
> **日期**：2026-06-21  
> **前提**：延續 `FINAL_SPEC.md` 的安全模式與狀態機；合約根目錄為 `MAW_workflow/`  
> **體驗目標**：**一個網址、一個介面** — 用戶只感覺在用「MAW」，不是多個分離的 App

---

## 0. 已確認決策（v0.4）

| # | 決策 | 內容 |
|---|------|------|
| 1 | **合約目錄** | 所有 MAW 產物放在 `<目標專案>/MAW_workflow/` |
| 2 | **Agent 清單** | 執行者與審查者**同一清單**；僅 **GUI/TUI 型** Agent（見 §4） |
| 3 | **單一整合 UI** | 一個 `index.html`、一個 port `8002` |
| 4 | **每次必看現況** | `MAW.command` → Panel 0；「啟動工作流」同頁切換至 Panel 1+ |
| 5 | **命令檔** | 僅 `install.command` + `MAW.command`；含 macOS 執行權限防呆（§10.1） |
| 6 | **LLM 供應商** | **LiteLLM（優先推薦）**、OpenRouter、**Direct API** 並存（§6.1、§10.2） |
| 7 | **Mock** | 不出現在使用者 UI；僅 CI/pytest |
| 8 | **CLI 型 Agent** | **不提供** Claude Code、Cursor CLI、Opencode 等純 CLI 選項（§10.3） |

---

## 1. 目標使用者旅程

### 1.1 首次使用

```
下載 MAW
    → README 提示：chmod +x *.command（§10.1）
    → 雙擊 install.command（自動 chmod MAW.command）
    → uv sync + .env → MAW.command
    → 瀏覽器 http://127.0.0.1:8002
    → Panel 0：填 LLM Key、選專案、Scaffold（若需要）、選 Agent
    → 健康燈變綠 →「啟動工作流」解鎖
    → 同頁進入 Panel 1 董事會 → …
```

### 1.2 日常使用

```
雙擊 MAW.command → Panel 0 確認現況 → 啟動工作流 → 同頁 Panel 1+
```

### 1.3 命令檔

```bash
# install.command（節錄，完整見 §6.2）
chmod +x MAW.command 2>/dev/null || true
uv sync
[ -f .env ] || cp .env.example .env
exec ./MAW.command

# MAW.command
cd "$(dirname "$0")"
open "http://127.0.0.1:8002" 2>/dev/null || true
uv run python -m uvicorn main:app --host 127.0.0.1 --port 8002
```

---

## 2. 單一整合 UI 設計

### 2.1 架構

- 一個 FastAPI、`127.0.0.1:8002`
- Setup API 併入 `main.py` → `/api/setup/*`
- 無獨立 `setup.html` / `setup_server.py` / 8001

### 2.2 頁面佈局

```
┌─────────────────────────────────────────────────────────────┐
│  MAW                    [⚙ 設定] [▶ 工作流]   ← 狀態列      │
├─────────────────────────────────────────────────────────────┤
│  Panel 0 — 設定與現況（每次進入預設）                          │
│    · LLM 供應商（LiteLLM ★ / OpenRouter / Direct API）       │
│    · 目標專案 + MAW_workflow 健康燈                           │
│    · 執行者 / 審查者（同一 Agent 清單）                        │
│    · [Scaffold] [儲存] [安裝 Agent 腳本]                     │
│    · [ ▶ 啟動工作流 ]（合約未就緒時 disabled，§10.5）         │
├─────────────────────────────────────────────────────────────┤
│  Panel 1 — 董事會建立（模型多選依已填 Key 防呆，§10.2）       │
│  Panel 2 — 董事會結果 / 最終回報                               │
│  Panel 3 — 管線追蹤                                           │
│  Panel 4 — 即時終端（WebSocket 單連線訂閱，§10.4）             │
│  Panel 5 — 提交前報告 Modal                                    │
└─────────────────────────────────────────────────────────────┘
```

### 2.3 「啟動工作流」（同頁切換）

- Pre-flight 檢查通過後才切換 `mode-workflow`（見 §10.5）
- 不 `window.open()`、不換 port
- Panel 0 可收合；頂部「⚙ 設定」隨時展開

### 2.4 頂部常駐狀態列

```
目標：MyApp → …/MAW_workflow | 執行：Openwork | 審查：Grok Build | LLM：LiteLLM
```

---

## 3. `MAW_workflow/` 合約目錄

```
<目標專案>/
└── MAW_workflow/
    ├── AGENT_STATE.md
    ├── TASKS/  PLANNING/  REVIEWS/
    ├── scripts/trigger_executor.py
    └── agent-runner/trigger-review.js, route-review-decision.js
```

- `maw_paths.py`：`get_project_root()` / `get_workflow_root()`
- 執行者改 code 在 `PROJECT_ROOT`；MAW 狀態檔在 `WORKFLOW_ROOT`
- 專案根 `.gitignore` 加入 `MAW_workflow/`

---

## 4. Agent Registry — 統一清單（僅 GUI/TUI）

執行者與審查者下拉**共用同一份清單**。  
**不含 CLI 型**（Claude Code、Cursor CLI、Opencode 等已移除，見 §10.3）。

| id | 顯示名稱 | 類型 | 備註 |
|----|----------|------|------|
| `openwork` | Openwork | GUI/TUI | 熱門，置頂 |
| `grok_build` | Grok Build | GUI/TUI | 熱門，置頂 |
| `antigravity` | Antigravity | GUI | |
| `codex` | Codex | GUI/TUI | |
| `claude_cowork` | Claude Cowork | GUI/TUI | |
| `custom` | 自訂 | — | 用戶填啟動方式 |

共 **6 項**（含自訂）。角色差異：指派 executor/reviewer + 不同 `.tpl` 範本。

### `adapters/registry.json`（節錄）

```json
{
  "agents": [
    {
      "id": "openwork",
      "label": "Openwork",
      "kind": "gui",
      "priority": 1,
      "executor_template": "executor/openwork.py.tpl",
      "reviewer_template": "reviewer/openwork.js.tpl",
      "router_template": "reviewer/route_openwork.js.tpl"
    }
  ]
}
```

---

## 5. API 整合（`main.py`）

| Method | Path | 說明 |
|--------|------|------|
| GET | `/api/setup/status` | Panel 0 現況（含健康燈、Key 遮罩） |
| GET | `/api/setup/agents` | 統一 Agent 清單 |
| GET | `/api/setup/llm-models` | 依已配置 Key 回傳可選模型（§10.2） |
| POST | `/api/setup/test-llm` | 測試目前 LLM 供應商 |
| POST | `/api/setup/pick-folder` | macOS 選專案根 |
| POST | `/api/setup/validate` | 驗證 `MAW_workflow` |
| POST | `/api/setup/scaffold` | 一鍵補齊合約（§10.5） |
| POST | `/api/setup/patch-gitignore` | 補 gitignore |
| POST | `/api/setup/save` | 寫 `.env` + `targets.json` |
| POST | `/api/setup/install-adapters` | 寫入 adapter 腳本 |

工作流 API / WebSocket：既有不變；WebSocket 改為可訂閱模式（§10.4）。

---

## 6. LLM 供應商規格

### 6.1 三種模式並存

| 模式 | 說明 | `.env` 範例 |
|------|------|-------------|
| **LiteLLM**（★ 優先推薦） | 本機/遠端 proxy，統一管理多廠商模型；隱私與連線可控 | `LLM_PROVIDER=litellm` `LITELLM_API_BASE=http://localhost:4000` `LITELLM_API_KEY=...`（可選） |
| **OpenRouter** | 雲端聚合，一組 Key 跨廠商 | `LLM_PROVIDER=openrouter` `OPENROUTER_API_KEY=...` |
| **Direct API** | 直連各廠官方 API | `LLM_PROVIDER=direct` + 各廠 Key（§10.2，支援 7 廠） |

**預設值**：新安裝 `.env` 預設 `LLM_PROVIDER=litellm`。

`council/llm_provider.py` 統一 `query_model()` / `query_models_parallel()`。

### 6.2 Panel 0 LLM UI（摘要，詳見 §10.2）

- 下拉預設選 **LiteLLM**，標註：**「★ 推薦：LiteLLM Proxy — 本機統一調度多廠商模型，適合跨廠商董事會」**
- OpenRouter 選項標註：「雲端聚合，只需一組 Key」
- 選 Direct API 時並列 **7 組 Key 輸入框**（見 §10.2 廠商表）
- Panel 1 模型多選：依已填 Key / LiteLLM 可用列表 **灰化不可用模型**（防呆）

### 6.3 `install.command`（完整）

```bash
#!/bin/bash
cd "$(dirname "$0")"
command -v uv >/dev/null || { echo "請先安裝 uv: https://docs.astral.sh/uv/"; exit 1; }
chmod +x MAW.command install.command 2>/dev/null || true
uv sync
[ -f .env ] || cp .env.example .env
mkdir -p ~/.agent-cowork
exec ./MAW.command
```

---

## 10. 深入建議與防呆機制（小A 建議，已納入規格）

### 10.1 macOS `.command` 執行權限防呆

**問題**：從 Git 下載的 `.command` 預設常無執行權限，雙擊會出現「沒有適當的權限」。

**規格**：

| 層級 | 作法 |
|------|------|
| **README** | 首段醒目提示：首次請在終端執行 `chmod +x *.command`，或先雙擊 `install.command` |
| **install.command** | 第一步自動：`chmod +x MAW.command install.command 2>/dev/null \|\| true` |
| **Git** | 盡量以 `git update-index --chmod=+x *.command` 追蹤可執行位元（輔助，不能取代 README） |

**驗收**：全新 clone 後執行 `install.command`（即使 MAW.command 原本不可執行），仍能順利進入 MAW。

---

### 10.2 LLM 供應商：LiteLLM 優先 + Direct API 擴充廠商

#### 供應商優先順序（Panel 0 下拉）

```
★ LiteLLM Proxy（推薦：本機統一調度，適合跨廠商董事會）
○ OpenRouter（雲端聚合：一組 Key 跨廠商）
○ Direct API（直連官方：自行管理各廠 Key）
```

**預設選中 LiteLLM**。README 與首次 Scaffold 引導用戶啟動本機 LiteLLM（`litellm --port 4000`）或填入既有 proxy URL。

#### LiteLLM 模式（優先推薦）

| 項目 | 規格 |
|------|------|
| 適用場景 | 用戶想在本機/內網統一管理多廠商 Key，避免請求經第三方聚合 |
| 設定 | `LITELLM_API_BASE` + 可選 `LITELLM_API_KEY` |
| 董事會 | 模型列表由 LiteLLM `/v1/models` 或 MAW 內建 catalog 與 proxy 交集決定 |
| 測試連線 | Setup「測試連線」打 `GET {base}/v1/models` 或輕量 completion |

#### Direct API — 支援廠商（7 廠）

Panel 0 選 Direct API 時，並列以下 Key 輸入框（填寫即啟用該廠模型）：

| vendor id | UI 標籤 | `.env` 變數 | 典型模型前綴 |
|-----------|---------|-------------|--------------|
| `openai` | OpenAI | `OPENAI_API_KEY` | `gpt-*`, `o*` |
| `anthropic` | Anthropic | `ANTHROPIC_API_KEY` | `claude-*` |
| `google` | Google Gemini | `GOOGLE_API_KEY` | `gemini-*` |
| `deepseek` | DeepSeek | `DEEPSEEK_API_KEY` | `deepseek-*` |
| `kimi` | Kimi（月之暗面） | `KIMI_API_KEY` | `moonshot-*`, `kimi-*` |
| `qwen` | Qwen（通義） | `QWEN_API_KEY` | `qwen-*` |
| `grok` | Grok（xAI） | `GROK_API_KEY` | `grok-*` |

> 廠商 endpoint 與 model id 映射維護於 `council/vendors.json`。  
> **用戶只需填一組 Key**；國內/國際、多區域 endpoint 由後台自動解析（§10.2.1）。

#### 10.2.1 Direct API 自動路由（Endpoint Auto-Resolution）

**原則**：用戶**不用選**「Moonshot 國內版 / 國際版」「Qwen 國內 / 國際」等變體。Panel 0 只填 `KIMI_API_KEY` 一欄，後台在「測試連線」與首次請求時自動選通可用 endpoint，並**快取結果**供董事會全程使用。

##### 用戶體驗

```
Panel 0：填 KIMI_API_KEY → 點「測試連線」
    → 後台自動嘗試候選 endpoint
    → 顯示：✅ Kimi 已連線（國際節點 api.moonshot.ai）  或
            ✅ Kimi 已連線（國內節點 api.moonshot.cn）
    → 用戶無需手動選擇
```

董事會執行時沿用已解析的 endpoint，**不會每次重試**（除非測試失敗或 Key 變更）。

##### `council/vendors.json` 結構（候選 endpoint）

每個 vendor 定義**有序候選列表** `endpoints[]`，含區域標籤與探測用 model：

```json
{
  "kimi": {
    "label": "Kimi / Moonshot",
    "env_key": "KIMI_API_KEY",
    "endpoints": [
      {
        "id": "moonshot_intl",
        "region": "international",
        "base_url": "https://api.moonshot.ai/v1",
        "probe_model": "moonshot-v1-8k"
      },
      {
        "id": "moonshot_cn",
        "region": "china",
        "base_url": "https://api.moonshot.cn/v1",
        "probe_model": "moonshot-v1-8k"
      }
    ]
  },
  "qwen": {
    "label": "Qwen / 通義",
    "env_key": "QWEN_API_KEY",
    "endpoints": [
      { "id": "dashscope_intl", "region": "international", "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "probe_model": "qwen-turbo" },
      { "id": "dashscope_cn", "region": "china", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "probe_model": "qwen-turbo" }
    ]
  },
  "deepseek": {
    "endpoints": [
      { "id": "deepseek_main", "region": "default", "base_url": "https://api.deepseek.com/v1", "probe_model": "deepseek-chat" }
    ]
  }
}
```

> 實作時以各廠**最新官方文件**為準更新 URL；上表為規格示意。

##### 自動解析演算法（`council/direct_resolver.py`）

```
resolve_vendor(vendor_id, api_key):
  1. 讀取 ~/.agent-cowork/vendor_routes.json 快取
     若存在且 key_hash 相符且未過期（7 天）→ 直接回傳快取 endpoint
  2. 讀 vendors.json 該廠 endpoints[]（有序）
  3. （可選）依 key 前綴 / 長度調整 probe 順序（heuristic，不當唯一依據）
  4. 對每個候選 endpoint 發送輕量 probe：
        POST {base}/chat/completions
        model=probe_model, max_tokens=1, messages=[{role:user, content:ping}]
        timeout=8s
  5. 第一個 HTTP 2xx 且非 auth 錯誤者 → 選中
  6. 寫入 vendor_routes.json：
        { "kimi": { "endpoint_id": "moonshot_intl", "base_url": "...", "region": "international", "resolved_at": "...", "key_hash": "sha256..." } }
  7. 全部失敗 → 回傳明確錯誤：
        「Kimi API Key 無法連線國內或國際節點，請檢查 Key 是否有效、網路是否可達」
```

| 觸發時機 | 行為 |
|----------|------|
| Panel 0「測試連線」 | 對**已填 Key 的廠商**執行 resolve，UI 顯示各廠選中區域 |
| 董事會首次用到某廠 | 若無快取則 resolve；有快取則直接用 |
| 用戶更換 Key | `key_hash` 變更 → 自動重新 probe |
| 請求收到 401/403 | 清除該廠快取，下次重試 probe 一次 |

##### 各廠多區域／多變體一覽（第一版需覆蓋）

| vendor | 用戶只填 | 後台自動處理 |
|--------|----------|--------------|
| **Kimi** | `KIMI_API_KEY` | Moonshot **國際** vs **國內** endpoint 自動 probe |
| **Qwen** | `QWEN_API_KEY` | 阿里雲 DashScope **國際** vs **國內** compatible-mode |
| **DeepSeek** | `DEEPSEEK_API_KEY` | 單一主 endpoint（若日後有多區再加候選） |
| **OpenAI** | `OPENAI_API_KEY` | 預設 `api.openai.com`；若 Key 為 Azure 型則 Phase 4 再擴 |
| **Anthropic** | `ANTHROPIC_API_KEY` | `api.anthropic.com` |
| **Google** | `GOOGLE_API_KEY` | `generativelanguage.googleapis.com` / Gemini API |
| **Grok** | `GROK_API_KEY` | `api.x.ai/v1` |

**UI 不暴露**「國內/國際」下拉；僅在測試成功後以**唯讀標籤**告知用戶後台選了哪個節點（方便除錯）。

##### Panel 0「測試連線」回傳範例

```json
{
  "kimi": { "ok": true, "region": "international", "endpoint_id": "moonshot_intl", "label": "Kimi 國際節點" },
  "qwen": { "ok": false, "error": "無法連線國內或國際節點，請檢查 QWEN_API_KEY" },
  "openai": { "ok": true, "region": "default", "endpoint_id": "openai_main" }
}
```

##### 與 Panel 1 模型列表的關係

- `GET /api/setup/llm-models` 在 Direct 模式下：
  - 僅對 **resolve 成功** 的廠商回傳 `enabled: true` 模型
  - Kimi resolve 到國際節點 → 只顯示該 endpoint 支援的 `moonshot-*` 型號（避免選了國內才有、國際沒有的 model）

##### 快取位置

`~/.agent-cowork/vendor_routes.json`（與 `targets.json` 同目錄，不進 git）

```json
{
  "routes": {
    "kimi": {
      "endpoint_id": "moonshot_intl",
      "base_url": "https://api.moonshot.ai/v1",
      "region": "international",
      "resolved_at": "2026-06-21T12:00:00Z",
      "key_hash": "abc123..."
    }
  }
}
```

##### 實作任務（併入 Phase 2 / Phase 4）

- [ ] `council/vendors.json` 七廠候選 endpoint 定義
- [ ] `council/direct_resolver.py` probe + 快取
- [ ] `POST /api/setup/test-llm` 整合 per-vendor resolve 結果
- [ ] `llm_provider.py` Direct 模式讀取 resolved `base_url` 發請求
- [ ] UI 測試連線後顯示各廠節點標籤（唯讀）

**驗收**：用戶只填一組 Kimi Key，在中國或海外網路環境下，「測試連線」應自動選通正確節點，無需手動選國內/國際。

#### 情況 A：多廠 Direct Key（真・跨廠商會議）

```env
LLM_PROVIDER=direct
OPENAI_API_KEY=sk-proj-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIzaSy...
DEEPSEEK_API_KEY=sk-...
KIMI_API_KEY=sk-...
QWEN_API_KEY=sk-...
GROK_API_KEY=xai-...
```

- **Stage 1 / 2 / 3**：依模型所屬 vendor 自動選對應 Key 發送請求
- 例如：委員 A 用 DeepSeek、委員 B 用 Qwen、董事長用 Claude → 各用各廠 Key

#### 情況 B：僅一家 Direct Key（單廠商內部研討會）

若只填 `DEEPSEEK_API_KEY`：

- 董事會成員只能選 DeepSeek 旗下模型（如 `deepseek-chat`、`deepseek-reasoner`）
- UI 提示：「目前為 DeepSeek 單廠商董事會模式」
- 同理適用於只填 OpenAI / Kimi / 等其他單一廠商 Key 的情境

#### Panel 1 模型多選防呆（前端 + API）

| 規則 | 行為 |
|------|------|
| **LiteLLM 模式**（預設） | 依 proxy 回傳或 catalog 交集列表；未在 proxy 註冊的模型 disabled |
| OpenRouter 模式 | OpenRouter 支援之模型可選 |
| Direct 模式 | 僅**已填 Key 的 7 廠**旗下模型可選；其餘 disabled + tooltip：「需於 Panel 0 填寫 {廠商} API Key」 |
| 按下 Start Council | 前端攔截非法選擇；後端 `start_council` 二次驗證 |

**API**：`GET /api/setup/llm-models` 回傳範例：

```json
{
  "provider": "direct",
  "available_vendors": ["openai", "deepseek"],
  "models": [
    { "id": "openai/gpt-4o", "vendor": "openai", "enabled": true },
    { "id": "deepseek/deepseek-chat", "vendor": "deepseek", "enabled": true },
    { "id": "qwen/qwen-max", "vendor": "qwen", "enabled": false, "reason": "需填寫 Qwen API Key" }
  ]
}
```

#### 實作備註

- Direct 模式：`httpx` 直打 **已 resolve** 的 `base_url`（§10.2.1）
- `llm_provider.py`：`model_id` → `vendor` → resolved endpoint + Key
- Key 僅存 `.env`；路由快取在 `vendor_routes.json`；API 回傳遮罩 Key
- **LiteLLM 與 Direct 可並存配置**：切換 `LLM_PROVIDER` 即可換模式

---

### 10.3 CLI 型 Agent：不提供選項

**問題**：Claude Code、Cursor CLI、Opencode 等為全域 CLI，非簡單 subprocess 腳本可封裝，適配成本高、失敗率高。

**決策（已確認）**：

- **移除** registry 中的：`claude_code`、`cursor_cli`、`opencode`（CLI）
- **保留** GUI/TUI：`openwork`、`grok_build`、`antigravity`、`codex`、`claude_cowork`、`custom`
- Adapter 範本註明 `kind: "gui"` | `"tui"`，禁止 `kind: "cli"` 進第一版
- 若未來支援 CLI，需單獨開 Phase 並重做 detect/生命周期管理

---

### 10.4 WebSocket 單頁切換時的連接優化

**問題**：Panel 0 ↔ 工作流區來回切換、或換任務時，若每次重建 WebSocket 會頻繁斷線重連。

**規格**：

#### 單一連線管理器（前端 `ws-manager.js` 或內嵌）

```javascript
class MawWebSocket {
  connect() { /* 連 /ws/workflow/global 或 /ws/maw */ }
  subscribe(taskNum) {
    this.send({ action: 'subscribe', task_num: taskNum });
  }
  // 心跳每 25s
}
```

#### 後端 WebSocket 協議擴充

| 訊息 | 方向 | 說明 |
|------|------|------|
| `{"action":"subscribe","task_num":"002"}` | 客戶端→伺服器 | 切換訂閱任務 log，**不斷線** |
| `{"type":"log","task_num":"002",...}` | 伺服器→客戶端 | 僅轉發已訂閱 task |
| `{"type":"status","workflow":{...}}` | 伺服器→客戶端 | 狀態更新 |
| `ping` / `pong` | 雙向 | 心跳 |

#### 行為規則

| 情境 | 作法 |
|------|------|
| 點「啟動工作流」同頁切換 | 若未連線則 `connect()`；已連線則維持 |
| Panel 0 ↔ 工作流切換 | **不關閉** socket |
| 切換監看不同 task | `subscribe(new_task_num)`，不重連 |
| 頁面關閉 | `beforeunload` 優雅 close |
| 斷線 | 指數退避重連 + 恢復上次 `subscribe` |

**向後相容**：保留 `/ws/workflow/{task_num}`；新前端優先用 global socket + subscribe。

---

### 10.5 首次啟動健康預檢（Pre-flight Scaffolding Check）

**問題**：目標專案尚無 `MAW_workflow` 就點「啟動工作流」會失敗。

**規格**：

#### Panel 0 目標專案區塊

```
目標專案：/Users/me/my-app
MAW_workflow：🔴 尚未建立

[ 🔧 Scaffold（一鍵補齊合約結構） ]

[ ▶ 啟動工作流 ]  ← disabled，tooltip：「請先 Scaffold 或選擇有效專案」
```

#### 健康燈狀態

| 燈號 | 條件 |
|------|------|
| 🔴 紅 | 無 `MAW_workflow/` 或缺必要檔案 |
| 🟡 黃 | 目錄在但缺 agent 腳本 / gitignore 未補 |
| 🟢 綠 | `validate_target()` 通過 + executor/reviewer 腳本存在 |

#### 「啟動工作流」解鎖條件（全部滿足）

1. LLM 供應商已設定且測試連線通過（或曾成功測試）
2. 目標專案路徑有效
3. `MAW_workflow` 健康燈為 **綠**
4. `agents.executor` / `agents.reviewer` 已選且腳本已安裝

#### Scaffold 按鈕

- 呼叫 `POST /api/setup/scaffold`
- 建立 `MAW_workflow/` 及 `AGENT_STATE.md`、`TASKS/`、`PLANNING/`、`REVIEWS/`、`.gitignore` 範本
- 可選一併 `patch-gitignore` 專案根
- 完成後自動 re-validate，健康燈更新，解鎖「啟動工作流」

#### `launchWorkflow()` 流程（更新）

```javascript
async function launchWorkflow() {
  const preflight = await fetch('/api/setup/preflight');
  if (!preflight.ok) {
    showPreflightErrors(preflight.issues); // 指向 Scaffold 或 Panel 0 修正
    return;
  }
  // …同頁切換至 Panel 1
}
```

**新增 API**：`GET /api/setup/preflight` → `{ ready: bool, issues: [...] }`

---

## 7. 實作階段（v0.4）

### Phase 1 — 路徑 + 單一服務 + 權限防呆

- [ ] `maw_paths.py` + 核心路徑遷移
- [ ] `install.command` / `MAW.command` + chmod 防呆
- [ ] README 醒目 chmod 說明
- [ ] Panel 0 骨架 + `/api/setup/*` 基礎

### Phase 2 — Panel 0 完整 + Pre-flight + Scaffold

- [ ] LLM 三模式 UI（LiteLLM 預設 / OpenRouter / Direct）
- [ ] Direct 七廠 Key + `/api/setup/llm-models` 防呆
- [ ] `council/vendors.json` + `direct_resolver.py` 自動 endpoint 路由（§10.2.1）
- [ ] 健康燈 + Scaffold + 「啟動工作流」disabled 邏輯
- [ ] `/api/setup/preflight`
- [ ] 同頁啟動工作流

### Phase 3 — Agent Registry（6 GUI/TUI agents）

- [ ] 更新 registry（移除 CLI 項）
- [ ] executor + reviewer 範本
- [ ] `install-adapters`

### Phase 4 — WebSocket 訂閱模式 + Direct API 後端

- [ ] `ws-manager` 前端 + subscribe 協議
- [ ] `llm_provider.py` Direct 直連實作
- [ ] 文件同步 FINAL_SPEC / README

---

## 8. 成功指標

| 指標 | 目標 |
|------|------|
| 用戶 URL | 僅 `http://127.0.0.1:8002` |
| 瀏覽器分頁 | 全程 1 個 |
| Agent 選項 | 執行/審查完全一致（6 項 GUI/TUI） |
| 無 MAW_workflow 誤啟動 | 0（按鈕 disabled + preflight） |
| chmod 後首次可跑 | install.command 自修復 |
| Direct 單 Key 誤選他廠模型 | 0（UI 灰化 + 後端拒絕） |
| WebSocket 切 Panel 斷線次數 | 最小化（subscribe 不重建） |

---

## 9. 與 `FINAL_SPEC.md` 的關係

| 項目 | v0.4 變更 |
|------|-----------|
| 合約路徑 | `<path>/MAW_workflow/` |
| UI | Panel 0–5 單頁 |
| LLM | LiteLLM 優先；Direct 7 廠 + **自動國內/國際 endpoint 路由** |
| Agent | 僅 GUI/TUI；無 CLI 第一版 |
| WebSocket | 建議 subscribe 模式（§10.4） |
| 狀態機、雙關卡 | 不變 |

---

*v0.6：Direct API 一 Key 自動路由（含 Kimi/Qwen 國內國際）。可進入 Phase 1 實作。*