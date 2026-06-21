"""Install GUI/TUI agent adapter scripts into target MAW_workflow/."""

import json
import os
import stat
from typing import Any

from maw_paths import get_project_root, get_workflow_root

ADAPTERS_ROOT = os.path.dirname(os.path.abspath(__file__))
REGISTRY_PATH = os.path.join(ADAPTERS_ROOT, "registry.json")

EXECUTOR_SCRIPT = "trigger_executor.py"
REVIEWER_SCRIPT = "trigger-review.js"
ROUTER_SCRIPT = "route-review-decision.js"

FORBIDDEN_KINDS = {"cli"}


def load_registry() -> dict[str, Any]:
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def list_agents() -> list[dict[str, Any]]:
    """Return GUI/TUI agents sorted by priority then label."""
    data = load_registry()
    agents = [a for a in data.get("agents", []) if a.get("kind") not in FORBIDDEN_KINDS]
    agents.sort(key=lambda a: (a.get("priority", 99), a.get("label", a.get("id", ""))))
    return agents


def get_agent(agent_id: str) -> dict[str, Any] | None:
    for agent in load_registry().get("agents", []):
        if agent.get("id") == agent_id:
            return agent
    return None


def render_template(rel_path: str, variables: dict[str, str]) -> str:
    full_path = os.path.join(ADAPTERS_ROOT, rel_path)
    if not os.path.isfile(full_path):
        raise FileNotFoundError(f"Template not found: {rel_path}")
    with open(full_path, "r", encoding="utf-8") as f:
        content = f.read()
    for key, value in variables.items():
        content = content.replace(f"{{{{{key}}}}}", value)
    return content


def _write_executable(path: str, content: str, *, executable: bool = True) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    if executable:
        mode = os.stat(path).st_mode
        os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_adapters(
    project_path: str,
    executor_id: str,
    reviewer_id: str,
    *,
    custom_executor_cmd: str = "",
    custom_reviewer_cmd: str = "",
) -> dict[str, Any]:
    """Render and install executor/reviewer/router scripts for selected agents."""
    root = get_project_root(project_path)
    workflow = get_workflow_root(root)

    if not os.path.isdir(root):
        raise ValueError(f"Project path does not exist: {root}")

    executor_agent = get_agent(executor_id)
    reviewer_agent = get_agent(reviewer_id)
    if not executor_agent:
        raise ValueError(f"Unknown executor agent: {executor_id}")
    if not reviewer_agent:
        raise ValueError(f"Unknown reviewer agent: {reviewer_id}")
    if executor_agent.get("kind") in FORBIDDEN_KINDS:
        raise ValueError(f"Executor agent '{executor_id}' is not supported in v1")
    if reviewer_agent.get("kind") in FORBIDDEN_KINDS:
        raise ValueError(f"Reviewer agent '{reviewer_id}' is not supported in v1")

    os.makedirs(os.path.join(workflow, "scripts"), exist_ok=True)
    os.makedirs(os.path.join(workflow, "agent-runner"), exist_ok=True)

    exec_vars = {
        "AGENT_ID": executor_id,
        "AGENT_LABEL": executor_agent.get("label", executor_id),
        "CUSTOM_EXECUTOR_CMD": custom_executor_cmd.replace('"', '\\"'),
    }
    rev_vars = {
        "AGENT_ID": reviewer_id,
        "AGENT_LABEL": reviewer_agent.get("label", reviewer_id),
        "CUSTOM_REVIEWER_CMD": custom_reviewer_cmd.replace('"', '\\"'),
    }

    executor_path = os.path.join(workflow, "scripts", EXECUTOR_SCRIPT)
    reviewer_path = os.path.join(workflow, "agent-runner", REVIEWER_SCRIPT)
    router_path = os.path.join(workflow, "agent-runner", ROUTER_SCRIPT)

    executor_content = render_template(executor_agent["executor_template"], exec_vars)
    reviewer_content = render_template(reviewer_agent["reviewer_template"], rev_vars)
    router_content = render_template(reviewer_agent["router_template"], rev_vars)

    _write_executable(executor_path, executor_content, executable=True)
    _write_executable(reviewer_path, reviewer_content, executable=True)
    _write_executable(router_path, router_content, executable=True)

    # Remove legacy script name if present
    legacy = os.path.join(workflow, "scripts", "trigger_antigravity.py")
    if os.path.isfile(legacy):
        os.remove(legacy)

    return {
        "projectPath": root,
        "workflowPath": workflow,
        "executor": executor_id,
        "reviewer": reviewer_id,
        "installed": {
            "executor": executor_path,
            "reviewer": reviewer_path,
            "router": router_path,
        },
        "message": (
            f"Installed {executor_agent['label']} executor and "
            f"{reviewer_agent['label']} reviewer scripts."
        ),
    }