import os
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from export import load_targets, validate_target, export_to_target
from maw_paths import get_workflow_root
import setup_api
from council.config import (
    DEFAULT_COUNCIL_MODELS,
    DEFAULT_CHAIRMAN_MODEL,
    AVAILABLE_MODELS,
    MOCK_MODE,
)
from council.storage import list_conversations, load_conversation
from loop_orchestrator import orchestrator, ALLOW_AUTO_COMMIT
from project_context import (
    build_context_pack,
    build_context_preview_response,
    ContextTargetError,
    list_safe_files,
)
from scout import scout_suggestions
from explorer import run_explorer_brief

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _validate_all_targets_on_startup() -> None:
    config = load_targets()
    for key, info in config.get("projects", {}).items():
        path = info.get("path", "")
        valid, issues = validate_target(path)
        if valid:
            logger.info("Target '%s' validated OK at %s", key, path)
        else:
            logger.warning(
                "Target '%s' at %s failed validation: %s",
                key,
                path,
                "; ".join(issues),
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_all_targets_on_startup()
    await orchestrator.resume_unfinished()
    yield


app = FastAPI(title="MAW Autonomous Workflow Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


class ExportRequest(BaseModel):
    conversationId: str
    messageIndex: int
    targetKey: str
    title: str
    priority: str = "MEDIUM"
    filesAffected: str = "To be determined by executor after repository inspection"
    nonGoals: str = "None specified."


class ReviewPolicy(BaseModel):
    mode: str = "AI"
    max_iterations: int = 3
    allow_request_changes: bool = True
    require_pre_commit_approval: bool = True
    auto_approve_council: bool = False
    allow_l0_auto_approve: bool = False
    allow_scout_auto_approve: bool = False


class ScoutPreviewKey(BaseModel):
    targetKey: str
    prompt: str


class NewConversationRequest(BaseModel):
    prompt: str
    targetKey: str
    title: Optional[str] = None
    councilModels: Optional[List[str]] = None
    chairmanModel: Optional[str] = None
    reviewPolicy: Optional[ReviewPolicy] = None
    filesAffected: str = "To be determined by executor after repository inspection"
    nonGoals: str = "None specified."
    mock: Optional[bool] = None
    contextFiles: Optional[List[str]] = None
    autoScoutContext: bool = True
    autoIncludeScoutFiles: bool = False
    maxAutoScoutFiles: int = 3
    minScoutScore: int = 40
    scoutPreviewKey: Optional[ScoutPreviewKey] = None
    generateExplorerBrief: bool = False
    explorerPreviewKey: Optional[ScoutPreviewKey] = None


class ExplorerPreviewRequest(BaseModel):
    targetKey: str
    prompt: str
    contextFiles: Optional[List[str]] = None
    maxFilesRead: int = 8
    maxCharsRead: int = 24000
    timeoutSeconds: int = 15
    allowLlmSummary: bool = False


class ContextPreviewRequest(BaseModel):
    targetKey: str
    prompt: str
    contextFiles: Optional[List[str]] = None
    autoScoutContext: bool = True
    maxAutoScoutFiles: int = 3
    minScoutScore: int = 40


class ApproveCouncilRequest(BaseModel):
    title: Optional[str] = None
    priority: str = "MEDIUM"
    filesAffected: Optional[str] = None
    nonGoals: Optional[str] = None


class HumanReviewRequest(BaseModel):
    decision: str


class SetupValidateRequest(BaseModel):
    projectPath: str


class SetupScaffoldRequest(BaseModel):
    projectPath: str
    patchGitignore: bool = True


class DirectKeysRequest(BaseModel):
    openai: Optional[str] = None
    anthropic: Optional[str] = None
    google: Optional[str] = None
    deepseek: Optional[str] = None
    kimi: Optional[str] = None
    qwen: Optional[str] = None
    grok: Optional[str] = None


class SetupSaveRequest(BaseModel):
    targetPath: Optional[str] = None
    targetKey: Optional[str] = None
    targetName: Optional[str] = None
    llmProvider: Optional[str] = None
    openrouterKey: Optional[str] = None
    litellmBase: Optional[str] = None
    litellmKey: Optional[str] = None
    directKeys: Optional[DirectKeysRequest] = None
    executorId: Optional[str] = None
    reviewerId: Optional[str] = None
    customExecutorCmd: Optional[str] = None
    customReviewerCmd: Optional[str] = None


class SetupInstallAdaptersRequest(BaseModel):
    projectPath: str
    executorId: str = "antigravity"
    reviewerId: str = "grok_build"
    customExecutorCmd: Optional[str] = ""
    customReviewerCmd: Optional[str] = ""


@app.get("/")
async def serve_dashboard():
    static_html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")
    if os.path.exists(static_html_path):
        return FileResponse(static_html_path)
    return HTMLResponse("<h1>MAW Workflow Engine</h1><p>static/index.html not found.</p>")


@app.get("/api/maw/config")
async def get_config():
    llm_data = setup_api.get_llm_models()
    return {
        "defaultCouncilModels": llm_data["defaultCouncilModels"],
        "defaultChairmanModel": llm_data["defaultChairmanModel"],
        "availableModels": [m["id"] for m in llm_data["models"] if m["enabled"]],
        "allModels": llm_data["models"],
        "llmProvider": llm_data["provider"],
        "mockMode": MOCK_MODE,
        "allowAutoCommit": ALLOW_AUTO_COMMIT,
    }


@app.get("/api/maw/targets")
async def get_targets():
    config = load_targets()
    projects = config.get("projects", {})
    default_key = config.get("default", "")

    validated_projects = []
    for key, info in projects.items():
        path = info.get("path", "")
        valid, issues = validate_target(path)
        validated_projects.append({
            "key": key,
            "name": info.get("name", key),
            "path": path,
            "workflowPath": get_workflow_root(path) if path else "",
            "description": info.get("description", ""),
            "valid": valid,
            "issues": issues,
        })

    return {"default": default_key, "projects": validated_projects}


@app.get("/api/maw/conversations")
async def get_conversations():
    return list_conversations()


@app.get("/api/maw/conversations/{conversation_id}")
async def get_conversation_details(conversation_id: str):
    try:
        conv = load_conversation(conversation_id)
        wf = orchestrator.get_workflow_by_conversation(conversation_id)
        return {"conversation": conv, "workflow": orchestrator._public_workflow(wf) if wf else None}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Conversation '{conversation_id}' not found.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read conversation: {e}")


@app.post("/api/maw/conversations/new")
async def create_conversation(req: NewConversationRequest):
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt is required.")
    if req.autoIncludeScoutFiles and not req.scoutPreviewKey:
        raise HTTPException(
            status_code=400,
            detail="scoutPreviewKey is required when autoIncludeScoutFiles is enabled.",
        )
    if req.councilModels and not req.mock and not MOCK_MODE:
        llm_data = setup_api.get_llm_models()
        enabled = {m["id"] for m in llm_data["models"] if m["enabled"]}
        disabled = [m for m in req.councilModels if m not in enabled]
        if disabled:
            raise HTTPException(
                status_code=400,
                detail=f"Models not available for current LLM provider: {', '.join(disabled)}",
            )
        if req.chairmanModel and req.chairmanModel not in enabled:
            raise HTTPException(
                status_code=400,
                detail=f"Chairman model not available: {req.chairmanModel}",
            )
    try:
        policy = req.reviewPolicy.model_dump() if req.reviewPolicy else None
        workflow = await orchestrator.start_council(
            prompt=req.prompt,
            target_key=req.targetKey,
            title=req.title,
            council_models=req.councilModels,
            chairman_model=req.chairmanModel,
            review_policy=policy,
            files_affected=req.filesAffected,
            non_goals=req.nonGoals,
            mock=req.mock,
            context_files=req.contextFiles,
            auto_include_scout=req.autoIncludeScoutFiles,
            max_auto_scout=req.maxAutoScoutFiles,
            min_scout_score=req.minScoutScore,
            scout_preview_key=req.scoutPreviewKey.model_dump() if req.scoutPreviewKey else None,
            generate_explorer=req.generateExplorerBrief,
            explorer_preview_key=req.explorerPreviewKey.model_dump() if req.explorerPreviewKey else None,
        )
        return workflow
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/maw/context/preview")
async def preview_context(req: ContextPreviewRequest):
    """Preview target project context.

    Accepts contextFiles and autoScoutContext for L1/L2.
    When autoScoutContext is True, runs the scout recommendation engine
    and returns suggestedFiles alongside the preview.  Scout results are
    never auto-injected into the context pack.

    Also returns wouldAutoInclude when autoIncludeScoutFiles would be active
    (dry-run simulation of what files would be auto-included).
    """
    try:
        context_pack = build_context_pack(
            target_key=req.targetKey,
            prompt=req.prompt,
            context_files=req.contextFiles,
            auto_scout=req.autoScoutContext,
        )
        suggested = None
        would_auto_include = None
        if req.autoScoutContext and req.prompt.strip():
            try:
                suggested = scout_suggestions(req.targetKey, req.prompt)
                # Dry-run: simulate what would be auto-included.
                would_auto_include = _compute_would_auto_include(
                    suggested,
                    user_paths=set(req.contextFiles or []),
                    max_auto=req.maxAutoScoutFiles,
                    min_score=req.minScoutScore,
                )
            except Exception:
                logger.warning("Scout suggestions failed for target %s", req.targetKey, exc_info=True)
        return build_context_preview_response(
            context_pack,
            suggested_files=suggested,
            would_auto_include=would_auto_include,
        )
    except ContextTargetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Context preview failed for target %s", req.targetKey)
        raise HTTPException(status_code=500, detail=f"Context preview failed: {e}")


def _compute_would_auto_include(
    suggestions: list[dict[str, Any]],
    user_paths: set[str],
    max_auto: int,
    min_score: int,
) -> list[dict[str, Any]]:
    """Dry-run computation of which scout files would be auto-included."""
    result: list[dict[str, Any]] = []
    for sug in suggestions:
        if len(result) >= max_auto:
            break
        if sug["score"] < min_score:
            continue
        if sug["path"] in user_paths:
            continue
        result.append({
            "path": sug["path"],
            "score": sug["score"],
            "reasons": sug.get("reasons", []),
            "source": "scout_auto_selected",
        })
    return result


@app.post("/api/maw/context/explorer/preview")
async def preview_explorer(req: ExplorerPreviewRequest):
    """Run the Explorer research layer and return an ExplorerBrief.

    Explorer is a read-only research layer that searches relevant directories
    and produces a brief summary of the project area around the user's task.

    Safety: read-only, target-root-confined, timeout-guarded.
    """
    try:
        brief = run_explorer_brief(
            target_key=req.targetKey,
            prompt=req.prompt,
            max_files_read=req.maxFilesRead,
            max_chars_read=req.maxCharsRead,
            timeout_seconds=req.timeoutSeconds,
            allow_llm_summary=req.allowLlmSummary,
        )
        return brief
    except ContextTargetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Explorer preview failed for target %s", req.targetKey)
        raise HTTPException(status_code=500, detail=f"Explorer failed: {e}")


@app.get("/api/maw/targets/{targetKey}/files")
async def list_target_files(targetKey: str):
    """Return a sanitised list of files available for user selection.

    Excludes .git, MAW_workflow, node_modules, secrets, binaries, gitignored files.
    Returns path/size/kind/mtime only — no file contents.
    """
    try:
        return list_safe_files(targetKey)
    except ContextTargetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to list files for target %s", targetKey)
        raise HTTPException(status_code=500, detail=f"Failed to list files: {e}")


@app.post("/api/maw/conversations/{conversation_id}/approve")
async def approve_council(conversation_id: str, req: ApproveCouncilRequest = ApproveCouncilRequest()):
    try:
        return await orchestrator.approve_council(
            conversation_id=conversation_id,
            title=req.title,
            priority=req.priority,
            files_affected=req.filesAffected,
            non_goals=req.nonGoals,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/maw/conversations/{conversation_id}/reject")
async def reject_council(conversation_id: str):
    try:
        return await orchestrator.reject_council(conversation_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/maw/workflows")
async def list_workflows():
    return orchestrator.list_workflows()


@app.get("/api/maw/workflows/{workflow_id}")
async def get_workflow(workflow_id: str):
    wf = orchestrator.get_workflow_by_id(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")
    return orchestrator._public_workflow(wf)


@app.get("/api/maw/workflow/{task_num}/status")
async def workflow_status(task_num: str):
    try:
        return orchestrator.get_status(task_num)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/maw/workflow/{task_num}/approve-commit")
async def approve_commit(task_num: str):
    try:
        return await orchestrator.approve_commit(task_num)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/maw/workflow/{task_num}/request-changes")
async def request_changes(task_num: str):
    try:
        return await orchestrator.request_changes_at_commit(task_num)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/maw/workflow/{task_num}/human-review-complete")
async def human_review_complete(task_num: str, req: HumanReviewRequest):
    try:
        return await orchestrator.complete_human_review(task_num, req.decision)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/maw/workflow/{task_num}/cancel")
async def cancel_workflow(task_num: str):
    try:
        return await orchestrator.cancel_workflow(task_num)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


async def _handle_global_ws_message(websocket: WebSocket, payload: dict) -> None:
    action = payload.get("action")
    if action == "ping":
        await websocket.send_json({"type": "pong"})
        return
    if action == "subscribe":
        task_num = str(payload.get("task_num", "")).strip()
        if not task_num:
            return
        await orchestrator.subscribe_global_ws(websocket, task_num)


async def _maw_global_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    await orchestrator.register_global_ws(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                await _handle_global_ws_message(websocket, payload)
    except WebSocketDisconnect:
        pass
    finally:
        await orchestrator.unregister_global_ws(websocket)


@app.websocket("/ws/maw")
async def maw_global_websocket(websocket: WebSocket):
    await _maw_global_websocket(websocket)


@app.websocket("/ws/workflow/global")
async def maw_global_websocket_alias(websocket: WebSocket):
    await _maw_global_websocket(websocket)


@app.websocket("/ws/workflow/{task_num}")
async def workflow_websocket(websocket: WebSocket, task_num: str):
    await websocket.accept()
    await orchestrator.register_ws(task_num, websocket)
    try:
        wf = orchestrator.get_workflow_by_task(task_num)
        if wf:
            await websocket.send_json({"type": "status", "workflow": orchestrator._public_workflow(wf)})
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        await orchestrator.unregister_ws(task_num, websocket)


@app.get("/api/setup/status")
async def setup_status():
    return setup_api.get_setup_status()


@app.get("/api/setup/agents")
async def setup_agents():
    from adapters.installer import list_agents
    return {"agents": list_agents()}


@app.get("/api/setup/llm-models")
async def setup_llm_models():
    return setup_api.get_llm_models()


@app.post("/api/setup/test-llm")
async def setup_test_llm():
    return await setup_api.test_llm_connection()


@app.get("/api/setup/preflight")
async def setup_preflight(projectPath: Optional[str] = None):
    return setup_api.get_preflight(projectPath)


@app.post("/api/setup/validate")
async def setup_validate(req: SetupValidateRequest):
    return setup_api.validate_project(req.projectPath)


@app.post("/api/setup/scaffold")
async def setup_scaffold(req: SetupScaffoldRequest):
    try:
        return setup_api.scaffold_project(req.projectPath, patch_gitignore=req.patchGitignore)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/setup/patch-gitignore")
async def setup_patch_gitignore(req: SetupValidateRequest):
    return setup_api.patch_project_gitignore(req.projectPath)


@app.post("/api/setup/save")
async def setup_save(req: SetupSaveRequest):
    direct_keys = None
    if req.directKeys:
        direct_keys = {k: v for k, v in req.directKeys.model_dump().items() if v}
    return setup_api.save_setup(
        target_path=req.targetPath,
        target_key=req.targetKey,
        target_name=req.targetName,
        llm_provider=req.llmProvider,
        openrouter_key=req.openrouterKey,
        litellm_base=req.litellmBase,
        litellm_key=req.litellmKey,
        direct_keys=direct_keys,
        executor_id=req.executorId,
        reviewer_id=req.reviewerId,
        custom_executor_cmd=req.customExecutorCmd,
        custom_reviewer_cmd=req.customReviewerCmd,
    )


@app.post("/api/setup/pick-folder")
async def setup_pick_folder():
    try:
        return setup_api.pick_folder_macos()
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/setup/install-adapters")
async def setup_install_adapters(req: SetupInstallAdaptersRequest):
    try:
        return setup_api.install_project_adapters(
            req.projectPath,
            req.executorId,
            req.reviewerId,
            custom_executor_cmd=req.customExecutorCmd or "",
            custom_reviewer_cmd=req.customReviewerCmd or "",
        )
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/maw/export")
async def export_task(req: ExportRequest):
    try:
        result = export_to_target(
            target_key=req.targetKey,
            conversation_id=req.conversationId,
            message_index=req.messageIndex,
            title=req.title,
            priority=req.priority,
            files_affected=req.filesAffected,
            non_goals=req.nonGoals,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True)