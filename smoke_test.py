#!/usr/bin/env python3
"""End-to-end smoke test using the template target project (mock mode).

This script copies template_target_project to a temp directory, points
TARGET_PROJECT_PATH at it, and exercises the full MAW workflow via HTTP.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

MAW_ROOT = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(MAW_ROOT, "template_target_project")


def api(method: str, path: str, data: dict | None = None) -> dict:
    url = f"http://localhost:8082{path}"
    body = json.dumps(data).encode() if data else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def poll_workflow(task_num: str, wanted_states: list[str], max_seconds: int = 60) -> dict:
    for i in range(max_seconds):
        wf = api("GET", f"/api/maw/workflow/{task_num}/status")
        print(f"  [{i}] state={wf['state']}")
        if wf["state"] in wanted_states:
            return wf
        if wf["state"] == "FAILED":
            raise RuntimeError(f"Workflow failed: {wf.get('reason')}")
        time.sleep(1)
    raise TimeoutError("Workflow did not reach wanted state")


def main() -> int:
    target_path = tempfile.mkdtemp(prefix="maw_smoke_target_")
    shutil.copytree(TEMPLATE, target_path, dirs_exist_ok=True)

    env = os.environ.copy()
    env["OPENROUTER_API_KEY"] = "dummy"
    env["TARGET_PROJECT_PATH"] = target_path
    env["MAW_MOCK_MODE"] = "1"
    env["ALLOW_AUTO_COMMIT"] = "false"

    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "uvicorn", "main:app", "--port", "8082"],
        cwd=MAW_ROOT,
        env=env,
    )

    try:
        time.sleep(2)
        print("1. Creating council...")
        wf = api("POST", "/api/maw/conversations/new", {
            "prompt": "Add a glassmorphism login button",
            "targetKey": "env",
            "title": "Glass Login Button",
            "councilModels": ["openai/gpt-4o"],
            "chairmanModel": "openai/gpt-4o",
            "reviewPolicy": {
                "mode": "AI",
                "max_iterations": 1,
                "allow_request_changes": True,
                "require_pre_commit_approval": True,
            },
            "mock": True,
        })
        print("  workflow_id:", wf["workflow_id"], "state:", wf["state"])
        workflow_id = wf["workflow_id"]

        print("2. Polling for COUNCIL_PENDING_APPROVAL...")
        conversation_id = None
        for i in range(30):
            time.sleep(1)
            wf = api("GET", f"/api/maw/workflows/{workflow_id}")
            print(f"  [{i}] state={wf['state']}")
            if wf["state"] == "COUNCIL_PENDING_APPROVAL":
                conversation_id = wf["conversation_id"]
                break
            if wf["state"] == "FAILED":
                raise RuntimeError(f"Council failed: {wf.get('reason')}")
        if not conversation_id:
            raise TimeoutError("Council did not complete")

        print("3. Approving council...")
        wf = api("POST", f"/api/maw/conversations/{conversation_id}/approve", {})
        task_num = wf["task_num"]
        print("  task_num:", task_num)

        print("4. Polling for COMMIT_PENDING_APPROVAL...")
        wf = poll_workflow(task_num, ["COMMIT_PENDING_APPROVAL"], max_seconds=30)
        print("  pre_commit_report:", json.dumps(wf.get("pre_commit_report"), indent=2))

        print("5. Approving commit...")
        wf = api("POST", f"/api/maw/workflow/{task_num}/approve-commit", {})
        print("  final state:", wf["state"])
        if wf["state"] not in ("COMPLETED", "FINAL_REPORT_PRESENTED"):
            raise RuntimeError(f"Unexpected final state: {wf['state']}")

        final_report_path = os.path.join(target_path, "PLANNING", f"final_report_{task_num}.md")
        if not os.path.isfile(final_report_path):
            raise RuntimeError("Final report file missing")
        print("  final report:", final_report_path)

        print("\nSMOKE TEST PASSED")
        return 0
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait()
        shutil.rmtree(target_path, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
