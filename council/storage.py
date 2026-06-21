"""Conversation JSON persistence compatible with Karpathy schema."""

import os
import json
import uuid
from datetime import datetime, timezone
from typing import Any

MAW_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONVERSATIONS_DIR = os.path.join(MAW_ROOT, "data", "conversations")


def ensure_conversations_dir() -> str:
    os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
    return CONVERSATIONS_DIR


def conversation_path(conversation_id: str) -> str:
    return os.path.join(ensure_conversations_dir(), f"{conversation_id}.json")


def save_conversation(conversation: dict[str, Any]) -> str:
    """Save conversation JSON and return the file path."""
    conv_id = conversation.get("id")
    if not conv_id:
        raise ValueError("Conversation must have an 'id' field")
    path = conversation_path(conv_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(conversation, f, indent=2)
    return path


def load_conversation(conversation_id: str) -> dict[str, Any]:
    path = conversation_path(conversation_id)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Conversation {conversation_id} not found at {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_conversations() -> list[dict[str, Any]]:
    conv_dir = ensure_conversations_dir()
    results = []
    for filename in os.listdir(conv_dir):
        if not filename.endswith(".json"):
            continue
        try:
            with open(os.path.join(conv_dir, filename), "r", encoding="utf-8") as f:
                data = json.load(f)
            exportable = sum(
                1 for m in data.get("messages", [])
                if m.get("role") == "assistant" and m.get("stage3")
            )
            results.append({
                "id": data.get("id"),
                "created_at": data.get("created_at", ""),
                "title": data.get("title", "New Conversation"),
                "message_count": len(data.get("messages", [])),
                "exportable_count": exportable,
            })
        except Exception:
            pass
    results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return results


def create_conversation_skeleton(title: str, user_prompt: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "created_at": now,
        "title": title or "New Council Task",
        "messages": [
            {"role": "user", "content": user_prompt},
        ],
    }