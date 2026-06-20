import os
import sys
import json
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

# Import export functions
from export import load_targets, validate_target, export_to_target

app = FastAPI(title="MAW Council Export Adapter API")

# Enable CORS for local integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Resolve Karpathy conversations directory path
sibling_conversations = "../Karpathy/data/conversations"
cwd_conversations = "./data/conversations"
default_conversations = "/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/Karpathy/data/conversations"

def get_conversations_dir():
    for path in [cwd_conversations, sibling_conversations, default_conversations]:
        if os.path.isdir(path):
            return os.path.realpath(path)
    # Default fallback
    return default_conversations

class ExportRequest(BaseModel):
    conversationId: str
    messageIndex: int
    targetKey: str
    title: str
    priority: str = "MEDIUM"
    filesAffected: str = "To be determined by executor after repository inspection"
    nonGoals: str = "None specified."

@app.get("/")
async def serve_dashboard():
    """Serve the single-page HTML client dashboard."""
    static_html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")
    if os.path.exists(static_html_path):
        return FileResponse(static_html_path)
    
    # Simple inline fallback if file not found
    return HTMLResponse("<h1>MAW Council Export Adapter</h1><p>Client page static/index.html not found.</p>")

@app.get("/api/maw/targets")
async def get_targets():
    """Retrieve and validate targets configuration."""
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
            "issues": issues
        })
        
    return {
        "default": default_key,
        "projects": validated_projects
    }

@app.get("/api/maw/conversations")
async def get_conversations():
    """List all available Karpathy conversations."""
    conv_dir = get_conversations_dir()
    if not os.path.isdir(conv_dir):
        return []
        
    conversations = []
    try:
        for filename in os.listdir(conv_dir):
            if filename.endswith(".json"):
                fpath = os.path.join(conv_dir, filename)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    
                    # Look for Stage 3 message count
                    has_stage3_count = 0
                    for msg in data.get("messages", []):
                        if msg.get("role") == "assistant" and msg.get("stage3"):
                            has_stage3_count += 1
                            
                    conversations.append({
                        "id": data.get("id"),
                        "created_at": data.get("created_at", ""),
                        "title": data.get("title", "New Conversation"),
                        "message_count": len(data.get("messages", [])),
                        "exportable_count": has_stage3_count
                    })
                except Exception:
                    # Ignore unparseable files
                    pass
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to scan conversations: {e}")
        
    # Sort newest first
    conversations.sort(key=lambda x: x["created_at"], reverse=True)
    return conversations

@app.get("/api/maw/conversations/{conversation_id}")
async def get_conversation_details(conversation_id: str):
    """Get full details of a specific conversation."""
    conv_dir = get_conversations_dir()
    fpath = os.path.join(conv_dir, f"{conversation_id}.json")
    if not os.path.isfile(fpath):
        raise HTTPException(status_code=404, detail=f"Conversation '{conversation_id}' not found.")
        
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read conversation: {e}")

@app.post("/api/maw/export")
async def export_task(req: ExportRequest):
    """Export the council plan to the target project."""
    try:
        result = export_to_target(
            target_key=req.targetKey,
            conversation_id=req.conversationId,
            message_index=req.messageIndex,
            title=req.title,
            priority=req.priority,
            files_affected=req.filesAffected,
            non_goals=req.nonGoals
        )
        return result
    except ValueError as e:
        # Bad request parameters
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        # Target locked / Conflict
        raise HTTPException(status_code=409, detail=str(e))
    except FileNotFoundError as e:
        # Conversation missing
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        # Internal write / registry append failure
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True)
