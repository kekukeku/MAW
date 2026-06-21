import os
import json
import logging
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from export import load_targets, validate_target, export_to_target
from council.config import (
    DEFAULT_COUNCIL_MODELS,
    DEFAULT_CHAIRMAN_MODEL,
    AVAILABLE_MODELS,
    MOCK_MODE,
)
from council.storage import list_conversations, load_conversation
from loop_orchestrator import orchestrator, ALLOW_AUTO_COMMIT

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


class ApproveCouncilRequest(BaseModel):
    title: Optional[str] = None
    priority: str = "MEDIUM"
    filesAffected: Optional[str] = None
    nonGoals: Optional[str] = None


class HumanReviewRequest(BaseModel):
    decision: str


@app.get("/")
async def serve_dashboard():
    static_html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")
    if os.path.exists(static_html_path):
        return FileResponse(static_html_path)
    return HTMLResponse("<h1>MAW Workflow Engine</h1><p>static/index.html not found.</p>")


@app.get("/api/maw/config")
async def get_config():
    return {
        "defaultCouncilModels": DEFAULT_COUNCIL_MODELS,
        "defaultChairmanModel": DEFAULT_CHAIRMAN_MODEL,
        "availableModels": AVAILABLE_MODELS,
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
        )
        return workflow
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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