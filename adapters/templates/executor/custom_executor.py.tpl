#!/usr/bin/env python3
"""Custom executor wrapper for MAW (user-defined command)."""

import argparse
import os
import shlex
import subprocess
import sys

WORKFLOW_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(WORKFLOW_ROOT)
CUSTOM_CMD = """{{CUSTOM_EXECUTOR_CMD}}"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-num", required=True)
    parser.add_argument("--title", default="Task")
    args = parser.parse_args()

    cmd = os.getenv("MAW_CUSTOM_EXECUTOR_CMD", "").strip() or CUSTOM_CMD.strip()
    if not cmd:
        print("ERROR: No custom executor command configured.", file=sys.stderr)
        return 1

    env = os.environ.copy()
    env["MAW_TASK_NUM"] = args.task_num.zfill(3)
    env["MAW_TASK_TITLE"] = args.title
    env["MAW_PROJECT_ROOT"] = PROJECT_ROOT
    env["MAW_WORKFLOW_ROOT"] = WORKFLOW_ROOT

    print(f"[custom-executor] Running: {cmd}")
    result = subprocess.run(
        shlex.split(cmd),
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())