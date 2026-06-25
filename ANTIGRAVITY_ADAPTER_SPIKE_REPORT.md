# ANTIGRAVITY_ADAPTER_SPIKE_REPORT

本報告記錄了 MAW v2 架構下第一個真實 Agent Adapter (Antigravity) 的 Spike 實作與驗證結果。

---

## 1. Discovery 方法

我們發現本機運行的 Antigravity 實例可以透過 `~/.gemini/antigravity/bin/agentapi` 命令行工具與現有的 Antigravity Language Server 通訊。

- **自動 Discovery**：我們在 `v2/dispatcher.py` 中實作了 `discover_antigravity_credentials()`。該函式會掃描系統中正在運行的 `language_server` 進程，從中安全地解析出 `ANTIGRAVITY_LS_ADDRESS` 和 `ANTIGRAVITY_CSRF_TOKEN`，並從 `app_storage.json` 中讀取 `ANTIGRAVITY_PROJECT_ID`。
- **無副作用 Probe**：為了解決先前 probe 探測會呼叫 `new-conversation` 產生垃圾對話的副作用，我們改用唯讀、無副作用的 gRPC 查詢 `agentapi get-conversation-metadata` 對候選 Port 進行健康檢查。若回應為 "trajectory not found"（代表 RPC 成功連線，僅對話 ID 不存在），即判定該 Port 正常。此探測不建立任何真實 conversation，不執行任何模型，亦不寫入狀態。
- **防止硬編碼**：此機制完全動態化，保證不將一次性 Port、Token 等敏感性憑證硬編碼進代碼庫中。

---

## 2. Adapter 接口與設計 (Asynchronous Mode)

我們在 `v2/dispatcher.py` 中實作了 `AntigravityAdapter(Adapter)`，採用非阻塞（Fire-and-Forget）的非同步模型：

- **輸入參數**：
  - `role`: Agent 擔當的角色（如 `planner`, `reviewer`）
  - `seat`: 席位名稱（如 `planner_a`）
  - `target_path`: 目標專案的絕對路徑
  - `instruction`: 任務的自然語言引導內容
  - `expected_output`: 預期產出 Artifact 的絕對路徑
- **非同步立即回傳**：
  - 呼叫 `invoke()` 時，若目標產物已存在且大小大於 0，則直接返回 `is_async=False` 成功。
  - 若產物不存在，則呼叫 `agentapi new-conversation` 建立背景 Agent 對話，並在 `expected_output` 同目錄下建立一個 `.invocation` 臨時狀態檔，記錄 `conversation_id` 與分派狀態。
  - 建立對話後，`invoke()` 會**立即返回** `is_async=True`，不再進行任何阻塞式輪詢（Polling），完全將輪詢解耦給 Watcher 處理。
- **持久化狀態與重啟恢復**：
  - `v2/watcher.py` 的 `_handle_result()` 收到 `is_async=True` 的成功回傳後，會立即將該 dispatch status 設為 `RUNTIME_STATUS_DISPATCHED`，並將 `invocation_id` 保存至 `runtime_state.json`。
  - 當 Watcher 重啟時，會從 `runtime_state.json` 讀取 `invocation_id` 重建 `_active_dispatches` 快照，避免重複發送 prompt。
  - Watcher 內部的 `_check_completions()` 每次 tick 會輪詢 `expected_output`。當偵測到預期產物已被 Agent 寫入後，會自動清除當前的 `.invocation` 檔案並將任務狀態設為完成。

---

## 3. 自動與 Manual 模式

- **自動模式**：已成功實作。調用 `agentapi new-conversation` 傳送指令，啟動背景 Agent 非同步工作流，並由 Watcher polling 直到預期產物寫入完成。
- **Manual 降級**：若本機 `agentapi` 損壞、無權限 or 找不到 Language Server 憑證，Adapter 會捕獲異常並將 `is_manual` 設為 `True`，寫入標記有 `manual_handoff: true` 的 `.invocation` 檔案。此時 CLI 介面將會醒目提示用戶：
  ```text
  ================================================================================
  [ANTIGRAVITY MANUAL HANDOFF REQUIRED]
  Please open Antigravity and execute the instruction:
  Instruction file: <instruction_file_path>
  Expected output:   <expected_output>
  ================================================================================
  ```
  Watcher 將持續檢測產物，完成後順利推進工作流。

---

## 4. 真實 Invocation ID 與驗收證據

本機 E2E 驗證使用官方提供的 `v2/run_spike.py` 測試腳本：

### 4.1 Planner Spike 驗證
- **測試命令**：`uv run python -m v2.run_spike --role planner`
- **真實 Invocation ID (Conversation ID)**：`e076cc6d-f2f1-4b16-b476-abc143d16bc6`
- **產出路徑**：`MAW_workflow/workflows/wf_spike_planner/proposals/planner_a.md`
- **執行耗時**：26.5 秒
- **結果**：成功 (True)
- **證據**：Antigravity 背景子程序順利讀取了 `chair_brief.md` 並在 `proposals/planner_a.md` 中產出真實的 Markdown 提案。Watcher 異步檢測到產物後自動完成流程。

### 4.2 Reviewer Spike 驗證
- **測試命令**：`uv run python -m v2.run_spike --role reviewer`
- **真實 Invocation ID (Conversation ID)**：`4cca339e-1519-4c72-99c9-0d444f22fe54`
- **結果**：受限 (Quota Limited)
- **詳細狀況**：對話順靈建立並被分派，對話 ID 也立即被寫入 `runtime_state.json` 中。但由於本地 Antigravity 帳戶在當前週度達到了個別 API 限額限制，Language Server 回傳了 `RESOURCE_EXHAUSTED` (429) 錯誤。
- **結論**：程式碼完全符合非同步設計合約，當前執行受阻於外部 Quota 限制而非 MAW 系統缺陷。

---

## 5. Timeout／Cancel／Restart 行為

- **Timeout**：由 Watcher 內部的逾時機制（預設 300/600 秒）負責。當一個狀態處於 `dispatched` 超過時限卻仍無產物，Watcher 會將該任務標記為 stale 釋放 lock，而非由 Adapter 阻塞控制。
- **Cancel**：由於 `agentapi` 目前尚未整合 Agent API 的終止端點，當前的取消僅在 Watcher 內標記並釋放 Lock，Agent 子程序會默默執行完畢。
- **Restart**：已完成驗證。若 Watcher 中途重啟，因存在 `.invocation` 檔案，Adapter 不會發送重複的 prompt，而是直接對 existing output 進行 polling，維持狀態冪等性。

---

## 6. 已知限制與改進建議

1. **二進制路徑依賴**：目前寫死為本機預設的 `~/.gemini/antigravity/bin/agentapi`。未來建議將此路徑放進配置檔中或支援從 `PATH` 中動態檢索。
2. **無強制的進程取消**：由於 `agentapi` 目前不具備 `cancel-conversation`，因此取消工作流時背景 Agent 仍會完成其當前對話。
3. **API 限額處理**：在真實多代理流程中，可能會高頻觸發外部 API 限額，建議後續可增加退避重試（Backoff retry）或自動切換成 Manual / Mock 備援機制的機制。

---

## 7. 下一個最小工作 (Next Milestones)

- [ ] 將 `agentapi` 的執行路徑整合進配置。
- [ ] 進入 Phase 4：實作極簡 UI，展示當前 Roster 的 dispatch 狀態，並把手動批准按鈕與 v2 API 綁定。
