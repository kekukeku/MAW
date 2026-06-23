#!/usr/bin/env python3
"""Phase 7B: Context-aware E2E smoke test (HTTP, mock council, audit export).

This script validates the entire context governance contract survives a real
HTTP path: preview -> explorer preview -> council with full context -> Gate #1
approval -> PLANNING export audit artifacts -> double-GET reload stability.

Port 8083 to avoid collision with smoke_test.py (8082).
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
TARGETS_CONFIG = os.path.expanduser("~/.agent-cowork/targets.json")
PORT = 8083
BASE_URL = f"http://localhost:{PORT}"

# Live reason codes accepted by _can_auto_approve_council (loop_orchestrator.py:353).
LIVE_REASON_CODES = {
    "allowed_policy_ok",
    "blocked_policy_disabled",
    "blocked_no_context",
    "blocked_l0_only",
    "blocked_scout_auto_selected",
    "blocked_context_failed",
    "blocked_context_partial",
    "blocked_fatal_access",
    "blocked_prompt_file_missing",
}
# Export fallback only; not in live reason codes.
EXPORT_REASON_CODES = LIVE_REASON_CODES | {"audit_unavailable"}

PROMPT = "Add a glassmorphism login button in src/main.py"


def api(method: str, path: str, data: dict | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    body = json.dumps(data).encode() if data else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    # ---- Build temp target project -------------------------------------------
    target_path = tempfile.mkdtemp(prefix="maw_ctx_smoke_")
    shutil.copytree(TEMPLATE, target_path, dirs_exist_ok=True)

    # Augment with minimal source files (template is MAW_workflow-only).
    readme = os.path.join(target_path, "README.md")
    with open(readme, "w", encoding="utf-8") as f:
        f.write("# Mock app for MAW context smoke\n")
    pkg = os.path.join(target_path, "package.json")
    with open(pkg, "w", encoding="utf-8") as f:
        json.dump({"name": "ctx-smoke", "private": True}, f)
    src_dir = os.path.join(target_path, "src")
    os.makedirs(src_dir, exist_ok=True)
    main_py = os.path.join(src_dir, "main.py")
    with open(main_py, "w", encoding="utf-8") as f:
        f.write("def login():\n    pass\n\ndef main():\n    login()\n")

    # git init with user config (mirrors test_e2e_workflow.py:33-43).
    for cmd in (
        ["git", "init"],
        ["git", "config", "user.email", "test@maw.local"],
        ["git", "config", "user.name", "MAW Smoke Test"],
        ["git", "add", "-A"],
        ["git", "commit", "-m", "initial smoke fixture"],
    ):
        subprocess.run(cmd, cwd=target_path, capture_output=True, check=False)

    # ---- Override targets.json so uvicorn sees the temp target ---------------
    original_targets = None
    os.makedirs(os.path.dirname(TARGETS_CONFIG), exist_ok=True)
    if os.path.exists(TARGETS_CONFIG):
        with open(TARGETS_CONFIG, "r", encoding="utf-8") as f:
            original_targets = f.read()
    with open(TARGETS_CONFIG, "w", encoding="utf-8") as f:
        json.dump({
            "default": "env",
            "projects": {
                "env": {"name": "Smoke Target", "path": target_path},
            },
        }, f)

    # ---- Start uvicorn -------------------------------------------------------
    env = os.environ.copy()
    env["MAW_MOCK_MODE"] = "1"
    env["OPENROUTER_API_KEY"] = "dummy"
    env["ALLOW_AUTO_COMMIT"] = "false"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(PORT)],
        cwd=MAW_ROOT,
        env=env,
    )

    try:
        time.sleep(2)

        # =====================================================================
        # Step 1 — Context preview
        # =====================================================================
        print("1. POST /api/maw/context/preview ...")
        prev = api("POST", "/api/maw/context/preview", {
            "targetKey": "env",
            "prompt": PROMPT,
            "contextFiles": ["src/main.py"],
            "autoScoutContext": True,
            "maxAutoScoutFiles": 3,
            "minScoutScore": 40,
        })
        assert prev.get("level") in ("L0", "L1"), f"unexpected level: {prev.get('level')}"
        assert "unavailable" not in str(prev.get("warnings", "")), "preview warnings contain unavailable"
        print(f"   level={prev.get('level')} files={len(prev.get('files', []))} suggested={len(prev.get('suggestedFiles', []))}")

        # =====================================================================
        # Step 2 — Explorer preview
        # =====================================================================
        print("2. POST /api/maw/context/explorer/preview ...")
        exp_prev = api("POST", "/api/maw/context/explorer/preview", {
            "targetKey": "env",
            "prompt": PROMPT,
            "contextFiles": ["src/main.py"],
            "timeoutSeconds": 15,
        })
        exp_status = exp_prev.get("status")
        assert exp_status in ("ready", "partial", "timeout", "failed", "skipped"), \
            f"unexpected explorer status: {exp_status}"
        if exp_status in ("ready", "partial"):
            assert "summary" in exp_prev or "candidateFiles" in exp_prev, \
                "ready/partial explorer missing summary or candidateFiles"
        print(f"   status={exp_status} files={exp_prev.get('limits', {}).get('filesRead', 0)}")

        # =====================================================================
        # Step 3 — Start council (full context path, manual Gate #1)
        # =====================================================================
        print("3. POST /api/maw/conversations/new ...")
        council = api("POST", "/api/maw/conversations/new", {
            "prompt": PROMPT,
            "targetKey": "env",
            "title": "Context Smoke Test",
            "councilModels": ["openai/gpt-4o"],
            "chairmanModel": "openai/gpt-4o",
            "reviewPolicy": {
                "mode": "AI",
                "max_iterations": 1,
                "allow_request_changes": False,
                "require_pre_commit_approval": True,
                "auto_approve_council": False,
            },
            "mock": True,
            "contextFiles": ["src/main.py"],
            "autoIncludeScoutFiles": True,
            "maxAutoScoutFiles": 3,
            "minScoutScore": 40,
            "scoutPreviewKey": {"targetKey": "env", "prompt": PROMPT},
            "generateExplorerBrief": True,
            "explorerPreviewKey": {"targetKey": "env", "prompt": PROMPT},
        })
        workflow_id = council["workflow_id"]
        print(f"   workflow_id={workflow_id} state={council['state']}")

        # =====================================================================
        # Step 4 — Poll for COUNCIL_PENDING_APPROVAL
        # =====================================================================
        print("4. Polling for COUNCIL_PENDING_APPROVAL...")
        conversation_id = None
        for i in range(60):
            time.sleep(1)
            wf = api("GET", f"/api/maw/workflows/{workflow_id}")
            st = wf["state"]
            print(f"   [{i}] state={st}")
            if st == "COUNCIL_PENDING_APPROVAL":
                conversation_id = wf["conversation_id"]
                break
            if st == "FAILED":
                raise RuntimeError(f"Workflow failed: {wf.get('reason')}")
        if not conversation_id:
            raise TimeoutError("Council did not reach PENDING_APPROVAL")
        print(f"   conversation_id={conversation_id}")

        # =====================================================================
        # Step 5 — First GET conversation (persistence check)
        # =====================================================================
        print("5. GET /api/maw/conversations/{conversation_id} (first read) ...")
        conv5 = api("GET", f"/api/maw/conversations/{conversation_id}")
        _assert_conversation_audit(conv5, "step5")

        # =====================================================================
        # Step 6 — Second GET conversation (reload stability)
        # =====================================================================
        print("6. GET /api/maw/conversations/{conversation_id} (reload) ...")
        conv6 = api("GET", f"/api/maw/conversations/{conversation_id}")
        _assert_conversation_audit(conv6, "step6")

        # =====================================================================
        # Step 7 — Approve council
        # =====================================================================
        print("7. POST /api/maw/conversations/{conversation_id}/approve ...")
        approved = api("POST", f"/api/maw/conversations/{conversation_id}/approve", {})
        task_num = approved["task_num"]
        print(f"   task_num={task_num}")

        # =====================================================================
        # Step 8 — Verify exported JSON audit contract
        # =====================================================================
        planning_dir = os.path.join(target_path, "MAW_workflow", "PLANNING")
        council_json_path = os.path.join(planning_dir, f"council_{task_num}.json")
        print(f"8. Reading {council_json_path} ...")
        with open(council_json_path, "r", encoding="utf-8") as f:
            council_json = json.load(f)

        assert "contextPack" in council_json, "missing contextPack"
        assert "contextAuditSummary" in council_json, "missing contextAuditSummary"
        assert "autoApprovePolicy" in council_json, "missing autoApprovePolicy"

        audit = council_json["contextAuditSummary"]
        assert audit.get("contextPackVersion") >= 1
        assert audit.get("targetKey") == "env"
        assert audit.get("highestLevel") in ("L0", "L1", "L2", "L3")
        assert isinstance(audit.get("riskFlags"), list)

        policy_json = council_json["autoApprovePolicy"]
        assert policy_json.get("reasonCode") in EXPORT_REASON_CODES, \
            f"unexpected export reasonCode: {policy_json.get('reasonCode')}"
        print(f"   JSON audit OK  level={audit['highestLevel']}  reason={policy_json['reasonCode']}")

        # =====================================================================
        # Step 9 — Verify exported Markdown audit contract
        # =====================================================================
        council_md_path = os.path.join(planning_dir, f"council_{task_num}.md")
        print(f"9. Reading {council_md_path} ...")
        with open(council_md_path, "r", encoding="utf-8") as f:
            council_md = f.read()
        assert "## Target Project Context Audit" in council_md, \
            "missing Target Project Context Audit section"
        assert "Auto-Approve Decision" in council_md, \
            "missing Auto-Approve Decision line"
        print("   Markdown audit OK")

        # =====================================================================
        print("\nCONTEXT SMOKE TEST PASSED")
        return 0

    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        # Restore original targets.json
        if original_targets is not None:
            with open(TARGETS_CONFIG, "w", encoding="utf-8") as f:
                f.write(original_targets)
        else:
            if os.path.exists(TARGETS_CONFIG):
                os.remove(TARGETS_CONFIG)
        shutil.rmtree(target_path, ignore_errors=True)


def _assert_conversation_audit(conv_resp: dict, label: str) -> None:
    """Validate context_audit and explorerBrief persistence on GET conversation."""
    conv = conv_resp.get("conversation", conv_resp)
    # context pack survived council
    cp = conv.get("context_pack")
    assert cp is not None, f"{label}: context_pack missing"
    assert cp.get("targetKey") == "env", f"{label}: wrong targetKey: {cp.get('targetKey')}"

    # 6g audit record persisted
    assert "context_audit" in conv, f"{label}: context_audit missing"
    audit = conv["context_audit"]
    assert "auditSummary" in audit, f"{label}: auditSummary missing"
    assert "autoApprovePolicy" in audit, f"{label}: autoApprovePolicy missing"

    summary = audit["auditSummary"]
    policy = audit["autoApprovePolicy"]
    assert summary.get("highestLevel") in ("L0", "L1", "L2", "L3"), \
        f"{label}: bad highestLevel: {summary.get('highestLevel')}"
    assert isinstance(summary.get("riskFlags"), list), f"{label}: riskFlags not list"
    assert policy.get("reasonCode") in LIVE_REASON_CODES, \
        f"{label}: unknown reasonCode: {policy.get('reasonCode')}"
    assert isinstance(policy.get("allowed"), bool), f"{label}: allowed not bool"

    # Explorer brief on context_pack (6g.1 reload fix)
    eb = cp.get("explorerBrief")
    if eb:
        assert eb.get("status") is not None, f"{label}: explorerBrief missing status"
        # 6g.1: highestLevel must NOT be L3 if explorer failed/timeout/skipped
        if eb.get("status") not in ("ready", "partial"):
            assert summary["highestLevel"] != "L3", \
                f"{label}: explorer {eb['status']} but highestLevel is L3 (6g.1 violation)"

    print(f"   {label} audit OK  level={summary['highestLevel']}  reason={policy['reasonCode']}  allowed={policy['allowed']}")


if __name__ == "__main__":
    sys.exit(main())
