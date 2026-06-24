# MAW v2 實作審核報告（小O vs 小C 計畫）

> **審核日期**: 2026-06-25  
> **審核者**: Grok（對照程式碼與測試執行）  
> **被審對象**: 小O（OpenWork）Phase 0–3 交付  
> **對照基準**: 小C（Codex）定稿  
> **計畫文件**: `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/LOCAL_AGENT_ARCHITECTURE_PLAN.md`（Version 3.0）  
> **補充審查**: `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/PLAN_REVIEW_APPENDIX.md`  
> **實作目錄**: `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2/`  
> **測試目錄**: `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2_tests/`  
> **Git 分支**: `codex/file-workflow-v2`

---

## 1. 執行摘要

小O **大方向符合** 小C v3.0 的 Watcher-First、檔案契約、本機 adapter 路線；**未引入** v1 Council / Context / LLM 依賴；**94 項測試通過**（審核時重跑確認）。

但：

1. **Phase 編號與小C 定稿不一致** — 小O 所稱「Phase 0–3 完成」實際對應小C 計畫的 **Phase 0 + 1 + 2 + 4（mock E2E）**；小C 的 **Phase 3（真實 Agent spike）尚未開始**。
2. **Phase 0–2 有多項驗收缺口** — 見 §5。
3. **`v2/` 程式尚未 commit** — 審核時為 untracked（見 §6）。

**總評**: 架構方向 **A**；mock 核心 **B+**；真實 adapter **未開始**（合理 blocked）。

---

## 2. 小O 交付聲明（原文摘要）

小O 報告：

- Phase 0–3 完成
- 94 tests pass，CLI 全流程 CREATED → COMPLETED
- v2 核心 7 檔案、約 1400 SLOC
- Phase 4 blocked：需真實 Agent adapter（Phase 3.5 spike）

審核後修正：

- 核心 Python 約 **2355 LOC**（見 §4 檔案清單）
- 實際為 **8 個模組檔**（含 `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2/git_ops.py`）

---

## 3. Phase 對照表（小O vs 小C）

對照文件：`/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/LOCAL_AGENT_ARCHITECTURE_PLAN.md` §14

| 小C 計畫 Phase | 小O 宣稱 | 審核結果 | 說明 |
|----------------|----------|----------|------|
| **Phase 0** 隔離骨架 | ✅ | **大部分通過** | 缺 v1 import 守門測試；程式未 commit |
| **Phase 1** 工作流核心 | ✅ | **通過** | schema / transitions / artifact calculator |
| **Phase 2** Mock Watcher | ✅ | **大部分通過** | 缺 `--inspect`、`runtime_state.json`、強重啟測試 |
| **Phase 3** 真實 Adapter Spike | — | **未做** | 符合 `PLAN_REVIEW_APPENDIX.md` §B 預期 |
| **Phase 4** 完整 Mock E2E | 小O 併入 Phase 3 | **已完成** | `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2_tests/test_e2e.py` |
| **Phase 5** 真實完整 E2E | — | **未做** | — |
| **Phase 6** 極簡 UI | — | **未做** | CLI 代替；`v2/ui/` 為空殼 |

---

## 4. v2 核心檔案清單（完整路徑）

| 檔案 | 行數（約） | 職責 | 對計畫符合度 |
|------|-----------|------|-------------|
| `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2/schema.py` | 287 | 資料模型、狀態機、artifact 路徑、dispatch key | ✅ |
| `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2/files.py` | 178 | 路徑、原子寫入、scaffold、events | ⚠️ scaffold 未複製模板 |
| `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2/workflow.py` | 759 | 轉移、dispatch 計算、instruction 生成 | ✅ |
| `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2/watcher.py` | 348 | polling、lock、dispatch 編排 | ⚠️ 見 §5 |
| `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2/dispatcher.py` | 392 | Adapter 介面、MockAdapter、registry | ⚠️ 僅 mock |
| `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2/app.py` | 326 | CLI 入口 | ✅ |
| `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2/git_ops.py` | 65 | 最小 git 安全檢查 | ✅ |
| `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2/adapters/__init__.py` | 0 | 預留 | — |

**模板**（Phase 0 要求）：

- `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2_templates/TEAM_RULES.md`
- `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2_templates/AGENTS.md`

**測試**：

- `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2_tests/test_schema.py`
- `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2_tests/test_workflow.py`
- `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2_tests/test_dispatcher.py`
- `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2_tests/test_e2e.py`

**執行命令**（審核時驗證）：

```bash
cd "/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW"
uv run python -m unittest discover -s v2_tests -q
# 結果：Ran 94 tests — OK
```

---

## 5. 符合項目（✅）

對照 `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/LOCAL_AGENT_ARCHITECTURE_PLAN.md` 核心原則：

| 項目 | 證據（完整路徑） |
|------|------------------|
| v2 不 import v1 核心 | `v2/` 內無 `council`、`loop_orchestrator`、`project_context` import |
| 檔案即協定 | `MAW_workflow/workflows/<id>/` 結構見 `v2/files.py` |
| 使用 polling（計畫 §9.1 允許） | `v2/watcher.py` `_loop()` + `poll_interval` |
| expected artifact set | `v2/schema.py` `expected_proposals()` / `expected_comments()` |
| 原子寫入 `.tmp` → rename | `v2/files.py` `write_atomic()` |
| per-agent 序列化 lock | `v2/watcher.py` `_agent_locks` |
| dispatch key 格式 | `v2/schema.py` `dispatch_key()` |
| events.jsonl 稽核 | `v2/files.py` `append_event()` |
| Mock 全流程 E2E | `v2_tests/test_e2e.py`（單 Agent、多 Agent、4 Planner、REQUEST_CHANGES、釐清） |
| CLI 入口齊全 | `v2/app.py`：`create` / `watch` / `status` / `answer` / `decide` / `list` / `read` / `adapters` |
| Agent 零網路 IPC | `v2/dispatcher.py` `MockAdapter.invoke()` 僅寫本地檔 |
| 獨立分支開發 | `codex/file-workflow-v2` |
| v1 未刪除 | `main.py`、`loop_orchestrator.py` 等仍在 repo 根目錄 |

---

## 6. 缺口與風險（⚠️ / ❌）

### 6.1 工程狀態

| 項目 | 狀態 |
|------|------|
| `v2/`、`v2_tests/`、`v2_templates/` commit 狀態 | **審核時為 untracked**，尚未進入 `codex/file-workflow-v2` 歷史 |

### 6.2 相對小C 計畫的缺口

| 項目 | 計畫出處 | 現況 | 嚴重度 |
|------|----------|------|--------|
| `runtime_state.json` | `LOCAL_AGENT_ARCHITECTURE_PLAN.md` §5.2、§9.2 | **未實作**；`_dispatch_history` 僅在 `v2/watcher.py` 記憶體 | 高 |
| 重啟不重複 dispatch | §14 Phase 2、`PLAN_REVIEW_APPENDIX.md` §D | `v2_tests/test_e2e.py` `test_watcher_restart_no_duplicate_dispatch` 過弱 | 高 |
| `--inspect` 無副作用模式 | `LOCAL_AGENT_ARCHITECTURE_PLAN.md` §9.2 | **未實作** | 中 |
| per-target Executor lock | `LOCAL_AGENT_ARCHITECTURE_PLAN.md` §9.2 | **未實作** | 中 |
| `comment_mode`（chair/linear/none） | `WATCHER_FIRST_SYNTHESIS.md`、`implementation_plan.md` | **固定** full N×(N-1)（`v2/schema.py` `expected_comments()`） | 中 |
| Scaffold 複製 `AGENTS.md` / `TEAM_RULES.md` | §5.1、`v2_templates/` | `v2/files.py` `scaffold_target()` 只建目錄 | 中 |
| Phase 0：禁止 import v1 測試 | §14 Phase 0 | **無** 對應測試檔 | 中 |
| Phase 1：symlink 路徑安全測試 | §14 Phase 1 | **無** | 中 |
| `v2_smoke_test.py` | Antigravity `implementation_plan.md` | **不存在**（unittest E2E 代替） | 低 |
| Reviewer **REJECT** 路徑 | §14 Phase 4 | **未實作**（僅 APPROVE / REQUEST_CHANGES / CANCEL） | 低 |
| 真實 Agent adapter | §14 Phase 3、`PLAN_REVIEW_APPENDIX.md` §B | registry 僅 `MockAdapter` | **阻擋項** |
| 極簡 Web UI | §14 Phase 6 | `v2/ui/__init__.py` 空殼 | 預期內 |

### 6.3 重啟恢復的具體問題

`v2/watcher.py`：

- `_dispatch_history`、`_agent_locks`、`_active_dispatches` 為 **程序內狀態**
- `_load_state()` 只讀 `manifest.json`，**不** 從磁碟恢復 dispatch 完成紀錄
- 計畫要求的 `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/LOCAL_AGENT_ARCHITECTURE_PLAN.md` §5.2 `runtime_state.json` 尚未落地

**風險**：watcher 程序重啟後，可能對已有 artifact 的階段 **重複 dispatch**（尤其同一 Agent 多席位序列場景）。

---

## 7. 評分

| 維度 | 評分 | 說明 |
|------|------|------|
| 架構方向（Watcher-First、檔案契約） | **A** | 與小C v3.0、`WATCHER_FIRST_SYNTHESIS.md` 一致 |
| Phase 0–2 mock 核心 | **B+** | 功能齊，持久化與守門測試不足 |
| Phase 3 真實 adapter | **未評** | 正確 blocked |
| 與小C Phase 編號對齊 | **需校正** | 實際完成 Phase 0–2 + 4 |

---

## 8. 建議下一步（進 Phase 3 前）

優先順序：

1. **Commit v2 程式** 至 `codex/file-workflow-v2`  
   路徑：`/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2/`、`v2_tests/`、`v2_templates/`

2. **實作 `runtime_state.json`**（或重啟時從 artifact + `events.jsonl` 推導已完成 dispatch）  
   修改：`/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2/watcher.py`、`v2/files.py`

3. **加強重啟測試**  
   修改：`/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2_tests/test_e2e.py` — 新 watcher 不得覆寫已存在 artifact、不得重複 dispatch 已完成 key

4. **`scaffold_target` 複製模板**  
   從 `v2_templates/` 複製至 target 根目錄  
   修改：`/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2/files.py`

5. **Phase 3.5 真實 Agent spike**（需本機運行 Agent）  
   依 `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/PLAN_REVIEW_APPENDIX.md` §B：至少一個真實 Agent 完成 Chair/Planner + 一個完成 Executor/Reviewer

---

## 9. 相關文件索引（完整路徑）

| 文件 | 路徑 |
|------|------|
| 小C 定稿計畫 v3.0 | `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/LOCAL_AGENT_ARCHITECTURE_PLAN.md` |
| 審查附錄 | `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/PLAN_REVIEW_APPENDIX.md` |
| Watcher-First 綜合建議 | `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/WATCHER_FIRST_SYNTHESIS.md` |
| Antigravity 實作分期（參考） | `/Users/kevin/.gemini/antigravity/brain/4c85c8e0-d98f-40e7-b88a-14dcf18ba06c/implementation_plan.md` |
| 本審核報告 | `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/V2_IMPLEMENTATION_AUDIT.md` |
| v1 舊核心（尚未刪除） | `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/loop_orchestrator.py` |

---

## 10. 一句話結論

> 小O 已把小C 的 **Watcher + 檔案契約 + mock adapter** 骨架做對，並用 `/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/MAW/v2_tests/test_e2e.py` 驗證 mock 全流程；但在 **持久化重啟恢復** 與 **Phase 0 守門測試** 上尚未達小C 定稿；**真實 Agent adapter（Phase 3）** 尚未開始——此時 blocked 是正確的。

---

*報告結束*