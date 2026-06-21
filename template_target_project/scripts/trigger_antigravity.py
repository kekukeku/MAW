#!/usr/bin/env python3
"""Mock executor: simulates Antigravity work and moves task to UNDER_REVIEW."""

import argparse
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def update_status(task_num: str, new_status: str) -> None:
    agent_state = os.path.join(ROOT, "AGENT_STATE.md")
    with open(agent_state, "r", encoding="utf-8") as f:
        content = f.read()
    pattern = rf"(\|\s*\*\*TASK-{task_num}\*\*\s*\|\s*)`[^`]+`"
    new_content, n = re.subn(pattern, rf"\g<1>`{new_status}`", content, count=1)
    if n == 0:
        print(f"WARNING: TASK-{task_num} not found in AGENT_STATE.md", file=sys.stderr)
    else:
        with open(agent_state, "w", encoding="utf-8") as f:
            f.write(new_content)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-num", required=True)
    args = parser.parse_args()
    task_num = args.task_num.zfill(3)

    print(f"[mock-executor] Starting TASK-{task_num}")
    time.sleep(0.5)
    print(f"[mock-executor] Implementing changes...")
    time.sleep(0.5)

    task_file = os.path.join(ROOT, "TASKS", f"task_{task_num}.md")
    if os.path.isfile(task_file):
        with open(task_file, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace("`IN_PROGRESS`", "`UNDER_REVIEW`")
        with open(task_file, "w", encoding="utf-8") as f:
            f.write(content)

    update_status(task_num, "UNDER_REVIEW")
    print(f"[mock-executor] TASK-{task_num} moved to UNDER_REVIEW")
    return 0


if __name__ == "__main__":
    sys.exit(main())