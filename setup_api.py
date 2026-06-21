"""Panel 0 setup APIs: status, validation, scaffold, and configuration."""

import os
import json
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any

from dotenv import dotenv_values, set_key

from export import load_targets, CONFIG_PATH
from maw_paths import WORKFLOW_DIR_NAME, get_project_root, get_workflow_root
from export import validate_target as _validate_contract
from council.direct_resolver import load_vendors, resolve_all_configured_vendors
from council.llm_provider import model_vendor
from adapters.installer import install_adapters, list_agents

MAW_ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(MAW_ROOT, ".env")
ENV_EXAMPLE_PATH = os.path.join(MAW_ROOT, ".env.example")
TEMPLATE_WORKFLOW = os.path.join(MAW_ROOT, "template_target_project", WORKFLOW_DIR_NAME)
SETUP_STATE_PATH = os.path.expanduser("~/.agent-cowork/setup_state.json")

REQUIRED_GITIGNORE_ENTRY = f"{WORKFLOW_DIR_NAME}/"

DIRECT_VENDOR_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "kimi": "KIMI_API_KEY",
    "qwen": "QWEN_API_KEY",
    "grok": "GROK_API_KEY",
}

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


def _load_setup_state() -> dict[str, Any]:
    if not os.path.isfile(SETUP_STATE_PATH):
        return {}
    try:
        with open(SETUP_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_setup_state(state: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(SETUP_STATE_PATH), exist_ok=True)
    with open(SETUP_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


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


def _get_project_agents(targets: dict[str, Any], key: str) -> dict[str, str]:
    project = targets.get("projects", {}).get(key, {})
    agents = project.get("agents", {})
    return {
        "executor": agents.get("executor", "antigravity"),
        "reviewer": agents.get("reviewer", "grok_build"),
        "customExecutorCmd": agents.get("custom_executor_cmd", ""),
        "customReviewerCmd": agents.get("custom_reviewer_cmd", ""),
    }


def _direct_keys_status(env: dict[str, str]) -> dict[str, str]:
    return {vendor: _mask_key(env.get(env_key, "")) for vendor, env_key in DIRECT_VENDOR_KEYS.items()}


def _llm_configured(env: dict[str, str]) -> tuple[bool, str | None]:
    if env.get("MAW_MOCK_MODE", "0") in ("1", "true", "yes"):
        return True, None
    provider = env.get("LLM_PROVIDER", "litellm")
    if provider == "openrouter":
        if env.get("OPENROUTER_API_KEY", "").strip():
            return True, None
        return False, "OPENROUTER_API_KEY not configured."
    if provider == "litellm":
        if env.get("LITELLM_API_BASE", "").strip():
            return True, None
        return False, "LITELLM_API_BASE not configured."
    if provider == "direct":
        for env_key in DIRECT_VENDOR_KEYS.values():
            if env.get(env_key, "").strip():
                return True, None
        return False, "No Direct API vendor keys configured."
    return False, f"Unknown LLM provider '{provider}'."


def get_setup_status() -> dict[str, Any]:
    env = _read_env()
    targets = load_targets()
    default_key = targets.get("default", "")
    setup_state = _load_setup_state()
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
            "agents": _get_project_agents(targets, key),
        })

    default_agents = _get_project_agents(targets, default_key) if default_key else {
        "executor": "antigravity",
        "reviewer": "grok_build",
    }

    return {
        "mawVersion": "0.6-phase3",
        "llmProvider": env.get("LLM_PROVIDER", "litellm"),
        "llmTested": setup_state.get("llm_test_ok", False),
        "llmTestedAt": setup_state.get("llm_tested_at"),
        "keys": {
            "openrouter": _mask_key(env.get("OPENROUTER_API_KEY", "")),
            "litellmBase": env.get("LITELLM_API_BASE", "http://localhost:4000"),
            "litellmKey": _mask_key(env.get("LITELLM_API_KEY", "")),
            "direct": _direct_keys_status(env),
        },
        "mockMode": env.get("MAW_MOCK_MODE", "0") in ("1", "true", "yes"),
        "defaultTarget": default_key,
        "defaultAgents": default_agents,
        "projects": projects,
        "agents": list_agents(),
        "vendorRoutes": setup_state.get("vendor_routes", {}),
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


def _is_masked(value: str) -> bool:
    if not value:
        return False
    value = value.strip()
    return "..." in value or value == "***"


def save_setup(
    *,
    target_path: str | None = None,
    target_key: str | None = None,
    target_name: str | None = None,
    llm_provider: str | None = None,
    openrouter_key: str | None = None,
    litellm_base: str | None = None,
    litellm_key: str | None = None,
    direct_keys: dict[str, str] | None = None,
    executor_id: str | None = None,
    reviewer_id: str | None = None,
    custom_executor_cmd: str | None = None,
    custom_reviewer_cmd: str | None = None,
) -> dict[str, Any]:
    if not os.path.isfile(ENV_PATH) and os.path.isfile(ENV_EXAMPLE_PATH):
        shutil.copy2(ENV_EXAMPLE_PATH, ENV_PATH)

    if llm_provider:
        set_key(ENV_PATH, "LLM_PROVIDER", llm_provider)
    if openrouter_key and not _is_masked(openrouter_key):
        set_key(ENV_PATH, "OPENROUTER_API_KEY", openrouter_key)
    if litellm_base:
        set_key(ENV_PATH, "LITELLM_API_BASE", litellm_base)
    if litellm_key and not _is_masked(litellm_key):
        set_key(ENV_PATH, "LITELLM_API_KEY", litellm_key)
    if direct_keys:
        for vendor, env_key in DIRECT_VENDOR_KEYS.items():
            value = direct_keys.get(vendor, "").strip()
            if value and not _is_masked(value):
                set_key(ENV_PATH, env_key, value)
    if target_path:
        set_key(ENV_PATH, "TARGET_PROJECT_PATH", get_project_root(target_path))

    targets = load_targets()
    key = target_key or targets.get("default") or "default"
    if target_path or executor_id or reviewer_id:
        targets.setdefault("projects", {})
        entry = targets["projects"].setdefault(key, {
            "name": target_name or key,
            "path": get_project_root(target_path) if target_path else "",
            "description": "Configured via MAW Panel 0",
        })
        if target_path:
            entry["path"] = get_project_root(target_path)
            entry["name"] = target_name or entry.get("name", key)
        if executor_id or reviewer_id or custom_executor_cmd is not None or custom_reviewer_cmd is not None:
            entry.setdefault("agents", {})
            if executor_id:
                entry["agents"]["executor"] = executor_id
            if reviewer_id:
                entry["agents"]["reviewer"] = reviewer_id
            if custom_executor_cmd is not None:
                entry["agents"]["custom_executor_cmd"] = custom_executor_cmd
            if custom_reviewer_cmd is not None:
                entry["agents"]["custom_reviewer_cmd"] = custom_reviewer_cmd
        if not targets.get("default"):
            targets["default"] = key
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(targets, f, indent=2)

    return get_setup_status()


def _catalog_models_for_provider(env: dict[str, str]) -> list[dict[str, Any]]:
    from council.config import AVAILABLE_MODELS

    provider = env.get("LLM_PROVIDER", "litellm")
    mock = env.get("MAW_MOCK_MODE", "0") in ("1", "true", "yes")

    if provider == "direct":
        vendors = load_vendors()
        setup_state = _load_setup_state()
        resolved_vendors = {
            vid: info for vid, info in setup_state.get("vendor_routes", {}).items()
            if info.get("ok")
        }
        models: list[dict[str, Any]] = []
        for vendor_id, vendor in vendors.items():
            env_key = vendor.get("env_key", "")
            has_key = bool(env.get(env_key, "").strip())
            resolved = resolved_vendors.get(vendor_id, {}).get("ok", False)
            for model_name in vendor.get("models", []):
                model_id = f"{vendor_id}/{model_name}"
                enabled = mock or (has_key and resolved)
                reason = None
                if not enabled:
                    if not has_key:
                        reason = f"Set {env_key} in Panel 0"
                    elif not resolved:
                        reason = "Run Test Connection to resolve endpoint"
                    else:
                        reason = "Unavailable"
                models.append({
                    "id": model_id,
                    "label": model_name,
                    "vendor": vendor_id,
                    "enabled": enabled,
                    "reason": reason,
                })
        return models

    configured, config_issue = _llm_configured(env)
    enabled_global = mock or configured
    return [
        {
            "id": m,
            "label": m.split("/")[-1],
            "vendor": model_vendor(m),
            "enabled": enabled_global,
            "reason": None if enabled_global else (config_issue or "Configure LLM in Panel 0"),
        }
        for m in AVAILABLE_MODELS
    ]


def get_llm_models() -> dict[str, Any]:
    from council.config import DEFAULT_COUNCIL_MODELS, DEFAULT_CHAIRMAN_MODEL

    env = _read_env()
    provider = env.get("LLM_PROVIDER", "litellm")
    models = _catalog_models_for_provider(env)
    enabled_models = [m["id"] for m in models if m["enabled"]]

    default_council = [m for m in DEFAULT_COUNCIL_MODELS if m in enabled_models]
    if not default_council and enabled_models:
        default_council = enabled_models[:3]

    default_chairman = DEFAULT_CHAIRMAN_MODEL
    if default_chairman not in enabled_models and enabled_models:
        default_chairman = enabled_models[0]

    return {
        "provider": provider,
        "enabled": len(enabled_models) > 0,
        "defaultCouncilModels": default_council,
        "defaultChairmanModel": default_chairman,
        "models": models,
    }


async def test_llm_connection() -> dict[str, Any]:
    env = _read_env()
    provider = env.get("LLM_PROVIDER", "litellm")

    if env.get("MAW_MOCK_MODE", "0") in ("1", "true", "yes"):
        state = _load_setup_state()
        state.update({
            "llm_test_ok": True,
            "llm_tested_at": datetime.now(timezone.utc).isoformat(),
            "llm_provider": provider,
        })
        _save_setup_state(state)
        return {"ok": True, "provider": provider, "message": "Mock mode enabled (MAW_MOCK_MODE)"}

    vendor_results: dict[str, Any] = {}

    if provider == "openrouter":
        key = env.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            return {"ok": False, "provider": provider, "message": "OPENROUTER_API_KEY not set", "vendors": {}}
        result = {"ok": True, "provider": provider, "message": "OpenRouter key configured", "vendors": {}}

    elif provider == "litellm":
        base = env.get("LITELLM_API_BASE", "http://localhost:4000").rstrip("/")
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{base}/v1/models")
            ok = resp.status_code == 200
            result = {
                "ok": ok,
                "provider": provider,
                "message": f"LiteLLM proxy responded HTTP {resp.status_code}",
                "vendors": {},
            }
        except Exception as exc:
            result = {
                "ok": False,
                "provider": provider,
                "message": f"Cannot reach LiteLLM at {base}: {exc}",
                "vendors": {},
            }

    elif provider == "direct":
        vendor_results = await resolve_all_configured_vendors(env)
        any_ok = any(v.get("ok") for v in vendor_results.values())
        if not vendor_results:
            result = {
                "ok": False,
                "provider": provider,
                "message": "No Direct API vendor keys configured",
                "vendors": vendor_results,
            }
        elif any_ok:
            ok_count = sum(1 for v in vendor_results.values() if v.get("ok"))
            result = {
                "ok": True,
                "provider": provider,
                "message": f"Direct API: {ok_count} vendor(s) connected",
                "vendors": vendor_results,
            }
        else:
            result = {
                "ok": False,
                "provider": provider,
                "message": "All configured Direct API vendors failed to connect",
                "vendors": vendor_results,
            }
    else:
        return {"ok": False, "provider": provider, "message": f"Unknown provider '{provider}'", "vendors": {}}

    state = _load_setup_state()
    state.update({
        "llm_test_ok": result["ok"],
        "llm_tested_at": datetime.now(timezone.utc).isoformat(),
        "llm_provider": provider,
        "vendor_routes": vendor_results,
    })
    _save_setup_state(state)
    return result


def get_preflight(project_path: str | None = None) -> dict[str, Any]:
    targets = load_targets()
    default_key = targets.get("default", "")
    path = project_path
    if not path and default_key:
        path = targets.get("projects", {}).get(default_key, {}).get("path", "")

    issues: list[str] = []
    health: dict[str, Any] = {"lamp": "red", "label": "Unknown", "valid": False, "issues": []}

    if not path:
        issues.append("No target project configured.")
    else:
        health = assess_health(path)
        if not health["valid"]:
            issues.extend(health["issues"])

    env = _read_env()
    setup_state = _load_setup_state()

    if env.get("MAW_MOCK_MODE", "0") not in ("1", "true", "yes"):
        configured, config_issue = _llm_configured(env)
        if not configured:
            issues.append(config_issue or "LLM not configured.")
        elif not setup_state.get("llm_test_ok"):
            issues.append("LLM connection not tested — click Test Connection in Panel 0.")
        elif setup_state.get("llm_provider") != env.get("LLM_PROVIDER", "litellm"):
            issues.append("LLM provider changed since last test — re-run Test Connection.")

    if default_key:
        agents = _get_project_agents(targets, default_key)
        if not agents.get("executor"):
            issues.append("Executor agent not selected.")
        if not agents.get("reviewer"):
            issues.append("Reviewer agent not selected.")
        if path and health.get("valid"):
            workflow = get_workflow_root(path)
            if not os.path.isfile(os.path.join(workflow, "scripts", "trigger_executor.py")):
                issues.append("Executor script not installed — run Scaffold or Install Agent Scripts.")

    return {
        "ready": len(issues) == 0,
        "issues": issues,
        "health": health,
        "llmTested": setup_state.get("llm_test_ok", False),
    }


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


def install_project_adapters(
    project_path: str,
    executor_id: str,
    reviewer_id: str,
    *,
    custom_executor_cmd: str = "",
    custom_reviewer_cmd: str = "",
) -> dict[str, Any]:
    root = get_project_root(project_path)
    if not os.path.isdir(get_workflow_root(root)):
        scaffold_project(root, patch_gitignore=True)

    if executor_id == "custom" and not custom_executor_cmd.strip():
        raise ValueError("Custom executor requires a command in Panel 0.")
    if reviewer_id == "custom" and not custom_reviewer_cmd.strip():
        raise ValueError("Custom reviewer requires a command in Panel 0.")

    result = install_adapters(
        root,
        executor_id,
        reviewer_id,
        custom_executor_cmd=custom_executor_cmd,
        custom_reviewer_cmd=custom_reviewer_cmd,
    )
    save_setup(
        target_path=root,
        executor_id=executor_id,
        reviewer_id=reviewer_id,
        custom_executor_cmd=custom_executor_cmd,
        custom_reviewer_cmd=custom_reviewer_cmd,
    )
    result["health"] = assess_health(root)
    return result