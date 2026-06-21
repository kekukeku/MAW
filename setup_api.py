"""Panel 0 setup APIs: status, validation, scaffold, and configuration."""

import os
import json
import platform
import shutil
import subprocess
from typing import Any

from dotenv import dotenv_values, set_key

from export import load_targets, CONFIG_PATH
from maw_paths import WORKFLOW_DIR_NAME, get_project_root, get_workflow_root
from export import validate_target as _validate_contract

MAW_ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(MAW_ROOT, ".env")
ENV_EXAMPLE_PATH = os.path.join(MAW_ROOT, ".env.example")
TEMPLATE_WORKFLOW = os.path.join(MAW_ROOT, "template_target_project", WORKFLOW_DIR_NAME)

REQUIRED_GITIGNORE_ENTRY = f"{WORKFLOW_DIR_NAME}/"

AGENT_REGISTRY = [
    {"id": "openwork", "label": "Openwork", "kind": "gui", "priority": 1},
    {"id": "grok_build", "label": "Grok Build", "kind": "gui", "priority": 1},
    {"id": "antigravity", "label": "Antigravity", "kind": "gui", "priority": 2},
    {"id": "codex", "label": "Codex", "kind": "gui", "priority": 2},
    {"id": "claude_cowork", "label": "Claude Cowork", "kind": "gui", "priority": 2},
    {"id": "custom", "label": "Custom", "kind": "custom", "priority": 3},
]


def _mask_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return value[:4] + "..." + value[-4:]


def _read_env() -> dict[str, str]:
    if os.path.isfile(ENV_PATH):
        return {k: v for k, v in dotenv_values(ENV_PATH).items() if v is not None}
    return {}


def _workflow_exists(project_path: str) -> bool:
    return os.path.isdir(get_workflow_root(project_path))


def _project_gitignore_ok(project_path: str) -> bool:
    gitignore = os.path.join(get_project_root(project_path), ".gitignore")
    if not os.path.isfile(gitignore):
        return False
    try:
        with open(gitignore, "r", encoding="utf-8") as f:
            return REQUIRED_GITIGNORE_ENTRY in f.read()
    except OSError:
        return False


def assess_health(project_path: str) -> dict[str, Any]:
    """Return health lamp state for Panel 0."""
    root = get_project_root(project_path)
    workflow = get_workflow_root(project_path)

    if not os.path.isdir(root):
        return {
            "lamp": "red",
            "label": "Invalid project path",
            "valid": False,
            "issues": ["Target directory does not exist or is not a directory."],
        }

    if not os.path.isdir(workflow):
        return {
            "lamp": "red",
            "label": "MAW_workflow not created",
            "valid": False,
            "issues": [f"Missing {WORKFLOW_DIR_NAME}/ directory."],
        }

    valid, issues = _validate_contract(root)
    if valid:
        return {"lamp": "green", "label": "Ready", "valid": True, "issues": []}

    script_issues = [i for i in issues if "scripts/" in i or "agent-runner/" in i]
    gitignore_issues = [i for i in issues if "gitignore" in i.lower()]

    if script_issues or gitignore_issues or not _project_gitignore_ok(root):
        return {
            "lamp": "yellow",
            "label": "Incomplete setup",
            "valid": False,
            "issues": issues,
        }

    return {"lamp": "red", "label": "Missing contract files", "valid": False, "issues": issues}


def get_setup_status() -> dict[str, Any]:
    env = _read_env()
    targets = load_targets()
    default_key = targets.get("default", "")
    projects = []

    for key, info in targets.get("projects", {}).items():
        path = info.get("path", "")
        health = assess_health(path) if path else {
            "lamp": "red",
            "label": "No path",
            "valid": False,
            "issues": ["Project path not configured."],
        }
        projects.append({
            "key": key,
            "name": info.get("name", key),
            "path": path,
            "workflowPath": get_workflow_root(path) if path else "",
            "description": info.get("description", ""),
            "health": health,
            "valid": health["valid"],
            "issues": health["issues"],
        })

    return {
        "mawVersion": "0.6-phase1",
        "llmProvider": env.get("LLM_PROVIDER", "litellm"),
        "keys": {
            "openrouter": _mask_key(env.get("OPENROUTER_API_KEY", "")),
            "litellmBase": env.get("LITELLM_API_BASE", "http://localhost:4000"),
        },
        "mockMode": env.get("MAW_MOCK_MODE", "0") in ("1", "true", "yes"),
        "defaultTarget": default_key,
        "projects": projects,
        "agents": AGENT_REGISTRY,
    }


def validate_project(project_path: str) -> dict[str, Any]:
    root = get_project_root(project_path)
    health = assess_health(root)
    return {
        "projectPath": root,
        "workflowPath": get_workflow_root(root),
        "health": health,
        "valid": health["valid"],
        "issues": health["issues"],
    }


def scaffold_project(project_path: str, patch_gitignore: bool = True) -> dict[str, Any]:
    root = get_project_root(project_path)
    workflow = get_workflow_root(root)

    if not os.path.isdir(root):
        raise ValueError(f"Project path does not exist: {root}")

    if not os.path.isdir(TEMPLATE_WORKFLOW):
        raise RuntimeError(f"Template workflow missing at {TEMPLATE_WORKFLOW}")

    os.makedirs(workflow, exist_ok=True)
    for entry in os.listdir(TEMPLATE_WORKFLOW):
        src = os.path.join(TEMPLATE_WORKFLOW, entry)
        dst = os.path.join(workflow, entry)
        if os.path.isdir(src):
            if os.path.exists(dst):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)

    gitignore_result = None
    if patch_gitignore:
        gitignore_result = patch_project_gitignore(root)

    health = assess_health(root)
    return {
        "projectPath": root,
        "workflowPath": workflow,
        "gitignore": gitignore_result,
        "health": health,
        "valid": health["valid"],
        "issues": health["issues"],
    }


def patch_project_gitignore(project_path: str) -> dict[str, Any]:
    root = get_project_root(project_path)
    gitignore_path = os.path.join(root, ".gitignore")
    entry = REQUIRED_GITIGNORE_ENTRY

    if os.path.isfile(gitignore_path):
        with open(gitignore_path, "r", encoding="utf-8") as f:
            content = f.read()
        if entry in content:
            return {"patched": False, "path": gitignore_path, "message": "Already present"}
        if content and not content.endswith("\n"):
            content += "\n"
        content += f"\n# MAW workflow artifacts\n{entry}\n"
        with open(gitignore_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"patched": True, "path": gitignore_path, "message": "Appended MAW_workflow/"}

    with open(gitignore_path, "w", encoding="utf-8") as f:
        f.write(f"# MAW workflow artifacts\n{entry}\n")
    return {"patched": True, "path": gitignore_path, "message": "Created .gitignore"}


def save_setup(
    *,
    target_path: str | None = None,
    target_key: str | None = None,
    target_name: str | None = None,
    llm_provider: str | None = None,
    openrouter_key: str | None = None,
    litellm_base: str | None = None,
) -> dict[str, Any]:
    if not os.path.isfile(ENV_PATH) and os.path.isfile(ENV_EXAMPLE_PATH):
        shutil.copy2(ENV_EXAMPLE_PATH, ENV_PATH)

    if llm_provider:
        set_key(ENV_PATH, "LLM_PROVIDER", llm_provider)
    if openrouter_key:
        set_key(ENV_PATH, "OPENROUTER_API_KEY", openrouter_key)
    if litellm_base:
        set_key(ENV_PATH, "LITELLM_API_BASE", litellm_base)
    if target_path:
        set_key(ENV_PATH, "TARGET_PROJECT_PATH", get_project_root(target_path))

    if target_path:
        targets = load_targets()
        key = target_key or "default"
        name = target_name or key
        targets.setdefault("projects", {})[key] = {
            "name": name,
            "path": get_project_root(target_path),
            "description": "Configured via MAW Panel 0",
        }
        if not targets.get("default"):
            targets["default"] = key
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(targets, f, indent=2)

    return get_setup_status()


def get_llm_models() -> dict[str, Any]:
    from council.config import AVAILABLE_MODELS, DEFAULT_COUNCIL_MODELS, DEFAULT_CHAIRMAN_MODEL

    env = _read_env()
    provider = env.get("LLM_PROVIDER", "litellm")
    has_openrouter = bool(env.get("OPENROUTER_API_KEY", "").strip())
    has_litellm = bool(env.get("LITELLM_API_BASE", "").strip())

    enabled = provider == "openrouter" and has_openrouter
    if provider == "litellm" and has_litellm:
        enabled = True
    if env.get("MAW_MOCK_MODE", "0") in ("1", "true", "yes"):
        enabled = True

    models = [
        {
            "id": m,
            "label": m.split("/")[-1],
            "enabled": enabled,
            "reason": None if enabled else "Configure LLM provider and API key in Panel 0",
        }
        for m in AVAILABLE_MODELS
    ]

    return {
        "provider": provider,
        "enabled": enabled,
        "defaultCouncilModels": DEFAULT_COUNCIL_MODELS,
        "defaultChairmanModel": DEFAULT_CHAIRMAN_MODEL,
        "models": models,
    }


async def test_llm_connection() -> dict[str, Any]:
    env = _read_env()
    provider = env.get("LLM_PROVIDER", "litellm")

    if env.get("MAW_MOCK_MODE", "0") in ("1", "true", "yes"):
        return {"ok": True, "provider": provider, "message": "Mock mode enabled (MAW_MOCK_MODE)"}

    if provider == "openrouter":
        key = env.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            return {"ok": False, "provider": provider, "message": "OPENROUTER_API_KEY not set"}
        return {"ok": True, "provider": provider, "message": "OpenRouter key configured"}

    if provider == "litellm":
        base = env.get("LITELLM_API_BASE", "http://localhost:4000").rstrip("/")
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{base}/v1/models")
            if resp.status_code < 500:
                return {
                    "ok": resp.status_code == 200,
                    "provider": provider,
                    "message": f"LiteLLM proxy responded HTTP {resp.status_code}",
                }
            return {"ok": False, "provider": provider, "message": f"LiteLLM proxy HTTP {resp.status_code}"}
        except Exception as exc:
            return {
                "ok": False,
                "provider": provider,
                "message": f"Cannot reach LiteLLM at {base}: {exc}",
            }

    return {"ok": False, "provider": provider, "message": f"Provider '{provider}' not configured yet"}


def get_preflight(project_path: str | None = None) -> dict[str, Any]:
    targets = load_targets()
    default_key = targets.get("default", "")
    path = project_path
    if not path and default_key:
        path = targets.get("projects", {}).get(default_key, {}).get("path", "")
    if not path:
        return {"ready": False, "issues": ["No target project configured."]}

    issues: list[str] = []
    health = assess_health(path)
    if not health["valid"]:
        issues.extend(health["issues"])

    env = _read_env()
    if env.get("MAW_MOCK_MODE", "0") not in ("1", "true", "yes"):
        provider = env.get("LLM_PROVIDER", "litellm")
        if provider == "openrouter" and not env.get("OPENROUTER_API_KEY", "").strip():
            issues.append("OPENROUTER_API_KEY not configured.")
        elif provider == "litellm" and not env.get("LITELLM_API_BASE", "").strip():
            issues.append("LITELLM_API_BASE not configured.")

    return {"ready": len(issues) == 0, "issues": issues, "health": health}


def pick_folder_macos() -> dict[str, Any]:
    if platform.system() != "Darwin":
        raise RuntimeError("Folder picker is only available on macOS.")
    script = 'POSIX path of (choose folder with prompt "Select target project root")'
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Folder picker cancelled or failed.")
    path = result.stdout.strip()
    if not path:
        raise RuntimeError("No folder selected.")
    return {"path": path}


def install_adapters_stub(project_path: str, executor_id: str, reviewer_id: str) -> dict[str, Any]:
    """Phase 1 placeholder — copies default mock scripts if missing."""
    root = get_project_root(project_path)
    workflow = get_workflow_root(root)
    scaffold_project(root, patch_gitignore=False)
    return {
        "projectPath": root,
        "workflowPath": workflow,
        "executor": executor_id,
        "reviewer": reviewer_id,
        "message": "Default workflow scripts installed from template.",
    }