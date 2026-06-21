#!/usr/bin/env python3
"""Mock executor: simulates agent work and moves task to UNDER_REVIEW."""

import argparse
import os
import re
import subprocess
import sys
import time

WORKFLOW_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(WORKFLOW_ROOT)


def run_git(args, check=True):
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=check,
    )
    return result


def ensure_git_repo() -> None:
    """Initialize a git repo if the target project does not have one."""
    if os.path.isdir(os.path.join(PROJECT_ROOT, ".git")):
        return
    run_git(["init", "-b", "main"])
    run_git(["config", "user.email", "maw@localhost"])
    run_git(["config", "user.name", "MAW"])
    run_git(["add", "-A"])
    run_git(["commit", "-m", "Initial commit"], check=False)


def update_status(task_num: str, new_status: str) -> None:
    agent_state = os.path.join(WORKFLOW_ROOT, "AGENT_STATE.md")
    with open(agent_state, "r", encoding="utf-8") as f:
        content = f.read()
    pattern = rf"(\|\s*\*\*TASK-{task_num}\*\*\s*\|\s*)`[^`]+`"
    new_content, n = re.subn(pattern, rf"\g<1>`{new_status}`", content, count=1)
    if n == 0:
        print(f"WARNING: TASK-{task_num} not found in AGENT_STATE.md", file=sys.stderr)
    else:
        with open(agent_state, "w", encoding="utf-8") as f:
            f.write(new_content)


def slugify(title: str) -> str:
    import re as _re
    slug = _re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:48] or "task"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-num", required=True)
    parser.add_argument("--title", default="Mock task")
    args = parser.parse_args()
    task_num = args.task_num.zfill(3)
    branch = f"task/task_{task_num}_{slugify(args.title)}"

    ensure_git_repo()

    print(f"[mock-executor] Starting TASK-{task_num}")
    time.sleep(0.3)
    print(f"[mock-executor] Creating branch {branch}")

    base_branch = "main"
    result = run_git(["branch", "--show-current"], check=False)
    if result.returncode == 0 and result.stdout.strip():
        base_branch = result.stdout.strip()

    run_git(["checkout", base_branch], check=False)
    run_git(["branch", "-D", branch], check=False)
    run_git(["checkout", "-b", branch])

    print(f"[mock-executor] Implementing changes on {branch}...")
    time.sleep(0.3)

    task_file = os.path.join(WORKFLOW_ROOT, "TASKS", f"task_{task_num}.md")
    if os.path.isfile(task_file):
        with open(task_file, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace("`IN_PROGRESS`", "`UNDER_REVIEW`")
        with open(task_file, "w", encoding="utf-8") as f:
            f.write(content)

    impl_file = os.path.join(PROJECT_ROOT, "src", "mock_impl.txt")
    os.makedirs(os.path.dirname(impl_file), exist_ok=True)
    with open(impl_file, "w", encoding="utf-8") as f:
        f.write(f"# TASK-{task_num}\nMock implementation for: {args.title}\n")

    update_status(task_num, "UNDER_REVIEW")

    run_git(["add", "-A"])
    run_git(["commit", "-m", f"TASK-{task_num}: {args.title}"])

    run_git(["checkout", base_branch])

    print(f"[mock-executor] TASK-{task_num} moved to UNDER_REVIEW on branch {branch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())