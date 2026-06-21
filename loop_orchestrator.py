"""MAW autonomous Council-Executor-Reviewer workflow state machine."""

import os
import re
import json
import asyncio
import logging
import subprocess
import signal
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from dotenv import load_dotenv

from export import export_to_target, load_targets, validate_target, slugify_title
from maw_paths import get_workflow_root
from council.council import run_council
from council.storage import load_conversation, CONVERSATIONS_DIR

load_dotenv()

logger = logging.getLogger(__name__)

MAW_ROOT = os.path.dirname(os.path.abspath(__file__))
WORKFLOWS_PATH = os.path.join(MAW_ROOT, "data", "workflows.json")

DEFAULT_MAX_REVIEW_ITERATIONS = int(os.getenv("MAX_REVIEW_ITERATIONS", "3"))
DEFAULT_EXECUTOR_TIMEOUT = int(os.getenv("EXECUTOR_TIMEOUT_SECONDS", "600"))
DEFAULT_REVIEWER_TIMEOUT = int(os.getenv("REVIEWER_TIMEOUT_SECONDS", "300"))
ALLOW_AUTO_COMMIT = os.getenv("ALLOW_AUTO_COMMIT", "false").lower() in ("1", "true", "yes")


class WorkflowState(str, Enum):
    IDLE = "IDLE"
    COUNCIL_RUNNING = "COUNCIL_RUNNING"
    COUNCIL_PENDING_APPROVAL = "COUNCIL_PENDING_APPROVAL"
    EXPORTED = "EXPORTED"
    EXECUTOR_RUNNING = "EXECUTOR_RUNNING"
    REVIEW_PENDING = "REVIEW_PENDING"
    REVIEW_RUNNING = "REVIEW_RUNNING"
    REVIEW_DECISION_PENDING = "REVIEW_DECISION_PENDING"
    COMMIT_PENDING_APPROVAL = "COMMIT_PENDING_APPROVAL"
    COMMITTING = "COMMITTING"
    COMPLETED = "COMPLETED"
    FINAL_REPORT_PRESENTED = "FINAL_REPORT_PRESENTED"
    FAILED = "FAILED"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_data_dirs() -> None:
    os.makedirs(os.path.join(MAW_ROOT, "data"), exist_ok=True)
    os.makedirs(CONVERSATIONS_DIR, exist_ok=True)


def load_workflows() -> dict[str, Any]:
    ensure_data_dirs()
    if not os.path.isfile(WORKFLOWS_PATH):
        return {"workflows": {}}
    try:
        with open(WORKFLOWS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        backup_path = WORKFLOWS_PATH + ".corrupt"
        try:
            os.replace(WORKFLOWS_PATH, backup_path)
            logger.error(
                "Corrupt workflows.json moved to %s: %s",
                backup_path,
                exc,
            )
        except OSError:
            logger.error("Corrupt workflows.json could not be backed up: %s", exc)
        return {"workflows": {}}


def save_workflows(data: dict[str, Any]) -> None:
    ensure_data_dirs()
    with open(WORKFLOWS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def parse_review_decision(output: str) -> str:
    """Parse APPROVE / REQUEST_CHANGES / REJECT from structured router stdout."""
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            data = json.loads(stripped)
            if isinstance(data, dict) and "decision" in data:
                token = str(data["decision"]).upper()
                if token in ("APPROVE", "REQUEST_CHANGES", "REJECT"):
                    return token
        except json.JSONDecodeError:
            pass

    match = re.search(
        r"\bDECISION:\s*(APPROVE|REQUEST_CHANGES|REJECT)\b",
        output,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).upper()

    for line in output.splitlines():
        token = line.strip().upper()
        if token in ("APPROVE", "REQUEST_CHANGES", "REJECT"):
            return token

    return "UNKNOWN"


def _default_review_policy() -> dict[str, Any]:
    return {
        "mode": "AI",
        "max_iterations": DEFAULT_MAX_REVIEW_ITERATIONS,
        "allow_request_changes": True,
        "require_pre_commit_approval": True,
        "auto_approve_council": False,
    }


class LoopOrchestrator:
    """Core workflow engine with subprocess streaming and human approval gates."""

    def __init__(self) -> None:
        self._workflows: dict[str, dict[str, Any]] = load_workflows().get("workflows", {})
        self._ws_clients: dict[str, set[Any]] = {}
        self._global_ws_subscriptions: dict[Any, str | None] = {}
        self._monitor_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    def get_workflow_by_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        for wf in self._workflows.values():
            if wf.get("conversation_id") == conversation_id:
                return wf
        return None

    def get_workflow_by_task(self, task_num: str) -> dict[str, Any] | None:
        return self._workflows.get(task_num)

    def get_workflow_by_id(self, workflow_id: str) -> dict[str, Any] | None:
        for wf in self._workflows.values():
            if wf.get("workflow_id") == workflow_id:
                return wf
        if workflow_id in self._workflows:
            return self._workflows[workflow_id]
        return None

    def list_workflows(self) -> list[dict[str, Any]]:
        return list(self._workflows.values())

    def _persist(self) -> None:
        save_workflows({"workflows": self._workflows})

    def _set_state(self, wf: dict[str, Any], state: WorkflowState, reason: str | None = None) -> None:
        wf["state"] = state.value
        wf["updated_at"] = _now_iso()
        if reason is not None:
            wf["reason"] = reason
        self._persist()
        self._schedule_broadcast(wf)

    def _append_log(self, wf: dict[str, Any], stream: str, line: str) -> None:
        entry = {"ts": _now_iso(), "stream": stream, "line": line}
        wf.setdefault("logs", []).append(entry)
        if len(wf["logs"]) > 2000:
            wf["logs"] = wf["logs"][-2000:]
        self._persist()
        self._schedule_broadcast(wf, log_entry=entry)

    def _schedule_broadcast(self, wf: dict[str, Any], log_entry: dict | None = None) -> None:
        target_id = wf.get("task_num") or wf.get("workflow_id")
        if not target_id:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._broadcast(target_id, wf, log_entry))
        except RuntimeError:
            pass

    async def register_ws(self, task_num: str, websocket: Any) -> None:
        self._ws_clients.setdefault(task_num, set()).add(websocket)

    async def unregister_ws(self, task_num: str, websocket: Any) -> None:
        clients = self._ws_clients.get(task_num, set())
        clients.discard(websocket)

    async def register_global_ws(self, websocket: Any) -> None:
        self._global_ws_subscriptions[websocket] = None

    async def unregister_global_ws(self, websocket: Any) -> None:
        self._global_ws_subscriptions.pop(websocket, None)

    async def subscribe_global_ws(self, websocket: Any, subscription_id: str) -> None:
        self._global_ws_subscriptions[websocket] = subscription_id
        wf = self.get_workflow_by_task(subscription_id)
        if not wf:
            wf = self.get_workflow_by_id(subscription_id)
        if wf:
            await websocket.send_json({
                "type": "status",
                "task_num": wf.get("task_num"),
                "workflow_id": wf.get("workflow_id"),
                "workflow": self._public_workflow(wf),
            })

    async def _broadcast(self, target_id: str, wf: dict[str, Any], log_entry: dict | None = None) -> None:
        public_wf = self._public_workflow(wf)
        task_payload: dict[str, Any] = {"type": "status", "workflow": public_wf}
        if log_entry:
            task_payload = {"type": "log", "entry": log_entry, "workflow": public_wf}

        dead_task: list[Any] = []
        for ws in list(self._ws_clients.get(target_id, set())):
            try:
                await ws.send_json(task_payload)
            except Exception:
                dead_task.append(ws)
        for ws in dead_task:
            self._ws_clients.get(target_id, set()).discard(ws)

        global_payload: dict[str, Any] = {
            "type": "status",
            "task_num": wf.get("task_num"),
            "workflow_id": wf.get("workflow_id"),
            "workflow": public_wf,
        }
        if log_entry:
            global_payload = {
                "type": "log",
                "task_num": wf.get("task_num"),
                "workflow_id": wf.get("workflow_id"),
                "entry": log_entry,
                "workflow": public_wf,
            }

        dead_global: list[Any] = []
        for ws, subscribed in list(self._global_ws_subscriptions.items()):
            if subscribed != target_id:
                continue
            try:
                await ws.send_json(global_payload)
            except Exception:
                dead_global.append(ws)
        for ws in dead_global:
            self._global_ws_subscriptions.pop(ws, None)

    def _project_path(self, wf: dict[str, Any]) -> str | None:
        export = wf.get("export_result") or {}
        return export.get("targetPath")

    def _workflow_path(self, wf: dict[str, Any]) -> str | None:
        export = wf.get("export_result") or {}
        path = export.get("workflowPath")
        if path:
            return path
        project = export.get("targetPath")
        return get_workflow_root(project) if project else None

    def _public_workflow(self, wf: dict[str, Any]) -> dict[str, Any]:
        return {
            "workflow_id": wf.get("workflow_id"),
            "task_num": wf.get("task_num"),
            "conversation_id": wf.get("conversation_id"),
            "state": wf.get("state"),
            "title": wf.get("title"),
            "target_key": wf.get("target_key"),
            "review_policy": wf.get("review_policy"),
            "review_iterations": wf.get("review_iterations", 0),
            "reason": wf.get("reason"),
            "pre_commit_report": wf.get("pre_commit_report"),
            "final_report": wf.get("final_report"),
            "logs": wf.get("logs", [])[-100:],
            "updated_at": wf.get("updated_at"),
        }

    async def start_council(
        self,
        prompt: str,
        target_key: str,
        title: str | None = None,
        council_models: list[str] | None = None,
        chairman_model: str | None = None,
        review_policy: dict[str, Any] | None = None,
        files_affected: str = "To be determined by executor after repository inspection",
        non_goals: str = "None specified.",
        mock: bool | None = None,
    ) -> dict[str, Any]:
        """Create workflow and run council asynchronously."""
        targets = load_targets()
        projects = targets.get("projects", {})
        if target_key not in projects:
            raise ValueError(f"Unknown target key: '{target_key}'.")
        target_path = projects[target_key].get("path", "")
        valid, issues = validate_target(target_path)
        if not valid:
            raise ValueError(
                f"Invalid target repo at '{target_path}': {', '.join(issues)}"
            )

        async with self._lock:
            wf_id = f"pending_{datetime.now().timestamp()}"
            wf = {
                "workflow_id": wf_id,
                "conversation_id": None,
                "message_index": 1,
                "task_num": None,
                "state": WorkflowState.IDLE.value,
                "title": title or prompt[:80],
                "target_key": target_key,
                "prompt": prompt,
                "council_models": council_models,
                "chairman_model": chairman_model,
                "review_policy": review_policy or _default_review_policy(),
                "files_affected": files_affected,
                "non_goals": non_goals,
                "mock": mock,
                "review_iterations": 0,
                "logs": [],
                "reason": None,
                "export_result": None,
                "pre_commit_report": None,
                "final_report": None,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
            self._workflows[wf_id] = wf
            self._persist()

        asyncio.create_task(self._run_council_task(wf_id))
        return self._public_workflow(wf)

    async def _run_council_task(self, wf_id: str) -> None:
        wf = self._workflows.get(wf_id)
        if not wf:
            return
        try:
            self._set_state(wf, WorkflowState.COUNCIL_RUNNING)
            conversation = await run_council(
                prompt=wf["prompt"],
                council_models=wf.get("council_models"),
                chairman_model=wf.get("chairman_model"),
                title=wf["title"],
                mock=wf.get("mock"),
            )
            wf["conversation_id"] = conversation["id"]
            wf["message_index"] = len(conversation["messages"]) - 1
            self._set_state(wf, WorkflowState.COUNCIL_PENDING_APPROVAL)
            # Re-key workflow by conversation_id for lookup
            del self._workflows[wf_id]
            self._workflows[conversation["id"]] = wf
            self._persist()

            # Auto-approve council if enabled in review policy (gate #1)
            policy = wf.get("review_policy", {})
            if policy.get("auto_approve_council"):
                logger.info("Auto-approving council for %s", conversation["id"])
                self._append_log(wf, "system", "Auto-approving council (skips gate #1)")
                try:
                    await self.approve_council(
                        conversation_id=conversation["id"],
                        title=wf.get("title"),
                        files_affected=wf.get("files_affected"),
                        non_goals=wf.get("non_goals"),
                    )
                except Exception as exc:
                    logger.exception("Auto-approve council failed: %s", exc)
                    self._set_state(wf, WorkflowState.FAILED, reason=f"Auto-approve failed: {exc}")
        except Exception as exc:
            logger.exception("Council failed for %s", wf_id)
            self._set_state(wf, WorkflowState.FAILED, reason=f"Council failed: {exc}")

    async def approve_council(
        self,
        conversation_id: str,
        title: str | None = None,
        priority: str = "MEDIUM",
        files_affected: str | None = None,
        non_goals: str | None = None,
    ) -> dict[str, Any]:
        """Human gate #1: export task and launch executor."""
        wf = self.get_workflow_by_conversation(conversation_id)
        if not wf:
            raise ValueError(f"No workflow found for conversation {conversation_id}")
        if wf["state"] != WorkflowState.COUNCIL_PENDING_APPROVAL.value:
            raise ValueError(f"Workflow not awaiting council approval (state={wf['state']})")

        target_key = wf["target_key"]
        export_result = export_to_target(
            target_key=target_key,
            conversation_id=conversation_id,
            message_index=wf["message_index"],
            title=title or wf["title"],
            priority=priority,
            files_affected=files_affected or wf.get("files_affected", ""),
            non_goals=non_goals or wf.get("non_goals", ""),
        )

        task_num = export_result["taskNum"]
        wf["task_num"] = task_num
        wf["export_result"] = export_result
        wf["title"] = title or wf["title"]

        # Re-key by task_num
        if conversation_id in self._workflows:
            del self._workflows[conversation_id]
        self._workflows[task_num] = wf
        self._set_state(wf, WorkflowState.EXPORTED)
        await asyncio.sleep(0.05)

        await self._spawn_executor(wf)
        return self._public_workflow(wf)

    async def reject_council(self, conversation_id: str) -> dict[str, Any]:
        wf = self.get_workflow_by_conversation(conversation_id)
        if not wf:
            raise ValueError(f"No workflow found for conversation {conversation_id}")
        self._set_state(wf, WorkflowState.FAILED, reason="Council plan rejected by user")
        return self._public_workflow(wf)

    async def cancel_workflow(self, task_num: str) -> dict[str, Any]:
        wf = self.get_workflow_by_task(task_num)
        if not wf:
            raise ValueError(f"No workflow found for task {task_num}")
        self._cancel_monitor(task_num)
        self._set_state(wf, WorkflowState.FAILED, reason="Workflow cancelled by user")
        return self._public_workflow(wf)

    async def approve_commit(self, task_num: str) -> dict[str, Any]:
        """Human gate #2: git commit and merge."""
        wf = self.get_workflow_by_task(task_num)
        if not wf:
            raise ValueError(f"No workflow found for task {task_num}")
        if wf["state"] != WorkflowState.COMMIT_PENDING_APPROVAL.value:
            raise ValueError(f"Workflow not awaiting commit approval (state={wf['state']})")

        self._set_state(wf, WorkflowState.COMMITTING)
        try:
            await self._perform_git_commit(wf)
            self._update_agent_state_status(wf, "MERGED")
            self._set_state(wf, WorkflowState.COMPLETED)
            wf["final_report"] = await self._generate_final_report(wf)
            self._set_state(wf, WorkflowState.FINAL_REPORT_PRESENTED)
        except Exception as exc:
            self._set_state(wf, WorkflowState.FAILED, reason=f"Commit failed: {exc}")
        return self._public_workflow(wf)

    async def request_changes_at_commit(self, task_num: str) -> dict[str, Any]:
        wf = self.get_workflow_by_task(task_num)
        if not wf:
            raise ValueError(f"No workflow found for task {task_num}")
        if wf["state"] != WorkflowState.COMMIT_PENDING_APPROVAL.value:
            raise ValueError(
                f"Workflow not awaiting commit approval (state={wf['state']})"
            )
        self._update_agent_state_status(wf, "IN_PROGRESS")
        self._set_state(wf, WorkflowState.EXECUTOR_RUNNING)
        await self._spawn_executor(wf)
        return self._public_workflow(wf)

    def get_status(self, task_num: str) -> dict[str, Any]:
        wf = self.get_workflow_by_task(task_num)
        if not wf:
            raise ValueError(f"No workflow found for task {task_num}")
        return self._public_workflow(wf)

    async def complete_human_review(self, task_num: str, decision: str) -> dict[str, Any]:
        """Advance workflow after operator completes manual review."""
        wf = self.get_workflow_by_task(task_num)
        if not wf:
            raise ValueError(f"No workflow found for task {task_num}")
        if wf["state"] != WorkflowState.REVIEW_PENDING.value:
            raise ValueError(f"Workflow not awaiting human review (state={wf['state']})")
        policy = wf.get("review_policy", {})
        if policy.get("mode") != "Human":
            raise ValueError("Workflow is not in Human review mode")
        token = decision.upper().strip()
        if token not in ("APPROVE", "REQUEST_CHANGES", "REJECT"):
            raise ValueError(f"Invalid review decision: {decision}")
        self._set_state(wf, WorkflowState.REVIEW_DECISION_PENDING)
        await self._apply_review_decision(wf, token)
        return self._public_workflow(wf)

    async def resume_unfinished(self) -> None:
        """On startup, resume monitoring for in-progress workflows."""
        for key, wf in list(self._workflows.items()):
            state = wf.get("state")
            task_num = wf.get("task_num")

            if state == WorkflowState.COUNCIL_RUNNING.value:
                wf_id = wf.get("workflow_id") or key
                logger.info("Re-queuing interrupted council for workflow %s", wf_id)
                asyncio.create_task(self._run_council_task(wf_id))
                continue

            if state == WorkflowState.COUNCIL_PENDING_APPROVAL.value:
                logger.info(
                    "Workflow %s awaiting council approval (conversation=%s)",
                    wf.get("workflow_id"),
                    wf.get("conversation_id"),
                )
                continue

            if task_num and state in (
                WorkflowState.EXECUTOR_RUNNING.value,
                WorkflowState.REVIEW_PENDING.value,
                WorkflowState.REVIEW_RUNNING.value,
                WorkflowState.REVIEW_DECISION_PENDING.value,
            ):
                logger.info("Resuming workflow %s in state %s", task_num, state)
                self._start_monitor(task_num)

    async def _spawn_executor(self, wf: dict[str, Any]) -> None:
        project_path = self._project_path(wf)
        workflow_path = self._workflow_path(wf)
        task_num = wf["task_num"]
        if not project_path or not workflow_path:
            self._set_state(wf, WorkflowState.FAILED, reason="Missing target or workflow path")
            return
        script = os.path.join(workflow_path, "scripts", "trigger_executor.py")
        if not os.path.isfile(script):
            self._set_state(wf, WorkflowState.FAILED, reason=f"Missing executor script: {script}")
            return

        self._set_state(wf, WorkflowState.EXECUTOR_RUNNING)
        cmd = ["python3", script, "--task-num", task_num, "--title", wf.get("title", f"Task {task_num}")]
        await self._run_subprocess(wf, cmd, cwd=project_path, timeout=DEFAULT_EXECUTOR_TIMEOUT, label="executor")
        if wf.get("state") == WorkflowState.FAILED.value:
            return
        await self._handle_executor_complete(wf)

    def _start_monitor(self, task_num: str) -> None:
        if task_num in self._monitor_tasks and not self._monitor_tasks[task_num].done():
            return
        self._monitor_tasks[task_num] = asyncio.create_task(self._monitor_workflow(task_num))

    def _cancel_monitor(self, task_num: str) -> None:
        task = self._monitor_tasks.pop(task_num, None)
        if task and not task.done():
            task.cancel()

    async def _monitor_workflow(self, task_num: str) -> None:
        """Poll AGENT_STATE.md and REVIEWS/ for state transitions."""
        wf = self.get_workflow_by_task(task_num)
        if not wf:
            return

        workflow_path = self._workflow_path(wf)
        if not workflow_path:
            return

        agent_state_path = os.path.join(workflow_path, "AGENT_STATE.md")
        reviews_dir = os.path.join(workflow_path, "REVIEWS")
        poll_interval = 2.0
        review_triggered = False

        try:
            while True:
                wf = self.get_workflow_by_task(task_num)
                if not wf:
                    break
                state = wf.get("state")

                if state in (WorkflowState.FAILED.value, WorkflowState.COMPLETED.value,
                             WorkflowState.FINAL_REPORT_PRESENTED.value, WorkflowState.COMMITTING.value,
                             WorkflowState.COMMIT_PENDING_APPROVAL.value, WorkflowState.COUNCIL_PENDING_APPROVAL.value):
                    break

                if state == WorkflowState.EXECUTOR_RUNNING.value:
                    status = self._read_task_status(agent_state_path, task_num)
                    if status == "UNDER_REVIEW":
                        policy = wf.get("review_policy", {})
                        mode = policy.get("mode", "AI")
                        if mode == "None":
                            wf["pre_commit_report"] = self._build_pre_commit_report(wf, "SKIPPED")
                            self._set_state(wf, WorkflowState.COMMIT_PENDING_APPROVAL)
                            break
                        elif mode == "Human":
                            self._append_log(wf, "system", "Awaiting human review in target project")
                            self._set_state(wf, WorkflowState.REVIEW_PENDING)
                        else:
                            self._set_state(wf, WorkflowState.REVIEW_PENDING)
                            await self._spawn_reviewer(wf)
                            review_triggered = True
                            break

                elif state == WorkflowState.REVIEW_PENDING.value and not review_triggered:
                    policy = wf.get("review_policy", {})
                    mode = policy.get("mode", "AI")
                    if mode == "Human":
                        review_file = os.path.join(reviews_dir, f"review_{task_num}.md")
                        if os.path.isfile(review_file):
                            self._set_state(wf, WorkflowState.REVIEW_DECISION_PENDING)
                            await self._route_human_review(wf)
                            break
                    elif mode == "AI":
                        await self._spawn_reviewer(wf)
                        review_triggered = True
                        break

                elif state == WorkflowState.REVIEW_RUNNING.value:
                    review_file = os.path.join(reviews_dir, f"review_{task_num}.md")
                    if os.path.isfile(review_file):
                        self._set_state(wf, WorkflowState.REVIEW_DECISION_PENDING)
                        await self._route_review_decision(wf)
                        break

                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            pass

    async def _handle_executor_complete(self, wf: dict[str, Any]) -> None:
        """Transition after executor finishes based on review policy."""
        workflow_path = self._workflow_path(wf)
        task_num = wf["task_num"]
        agent_state_path = os.path.join(workflow_path, "AGENT_STATE.md")
        status = self._read_task_status(agent_state_path, task_num)

        if status != "UNDER_REVIEW":
            self._start_monitor(task_num)
            return

        policy = wf.get("review_policy", {})
        mode = policy.get("mode", "AI")
        if mode == "None":
            wf["pre_commit_report"] = self._build_pre_commit_report(wf, "SKIPPED")
            self._set_state(wf, WorkflowState.COMMIT_PENDING_APPROVAL)
        elif mode == "Human":
            self._append_log(wf, "system", "Awaiting human review in target project")
            self._set_state(wf, WorkflowState.REVIEW_PENDING)
            self._start_monitor(task_num)
        else:
            self._set_state(wf, WorkflowState.REVIEW_PENDING)
            await self._spawn_reviewer(wf)

    async def _spawn_reviewer(self, wf: dict[str, Any]) -> None:
        project_path = self._project_path(wf)
        workflow_path = self._workflow_path(wf)
        task_num = wf["task_num"]
        script = os.path.join(workflow_path, "agent-runner", "trigger-review.js")
        if not os.path.isfile(script):
            self._set_state(wf, WorkflowState.FAILED, reason=f"Missing reviewer script: {script}")
            return

        self._set_state(wf, WorkflowState.REVIEW_RUNNING)
        cmd = ["node", script, task_num]
        await self._run_subprocess(wf, cmd, cwd=project_path, timeout=DEFAULT_REVIEWER_TIMEOUT, label="reviewer")
        if wf.get("state") == WorkflowState.FAILED.value:
            return
        review_file = os.path.join(workflow_path, "REVIEWS", f"review_{task_num}.md")
        if os.path.isfile(review_file):
            self._set_state(wf, WorkflowState.REVIEW_DECISION_PENDING)
            await self._route_review_decision(wf)
        else:
            self._start_monitor(task_num)

    async def _route_human_review(self, wf: dict[str, Any]) -> None:
        """Read decision from human-written review file metadata."""
        workflow_path = self._workflow_path(wf)
        task_num = wf["task_num"]
        review_file = os.path.join(workflow_path, "REVIEWS", f"review_{task_num}.md")
        try:
            with open(review_file, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as exc:
            self._set_state(wf, WorkflowState.FAILED, reason=f"Cannot read review file: {exc}")
            return
        decision = parse_review_decision(content)
        if decision == "UNKNOWN":
            self._append_log(
                wf,
                "system",
                "Human review file found; awaiting operator decision via API",
            )
            return
        await self._apply_review_decision(wf, decision)

    async def _route_review_decision(self, wf: dict[str, Any]) -> None:
        project_path = self._project_path(wf)
        workflow_path = self._workflow_path(wf)
        task_num = wf["task_num"]
        script = os.path.join(workflow_path, "agent-runner", "route-review-decision.js")
        if not os.path.isfile(script):
            self._set_state(wf, WorkflowState.FAILED, reason=f"Missing route script: {script}")
            return

        cmd = ["node", script, task_num]
        output = await self._run_subprocess(
            wf, cmd, cwd=project_path, timeout=60, label="router", capture_only=True
        )
        decision = parse_review_decision(output)
        await self._apply_review_decision(wf, decision)

    async def _apply_review_decision(self, wf: dict[str, Any], decision: str) -> None:
        task_num = wf["task_num"]
        policy = wf.get("review_policy", {})
        allow_loop = policy.get("allow_request_changes", True)
        max_iter = policy.get("max_iterations", DEFAULT_MAX_REVIEW_ITERATIONS)

        if decision == "APPROVE":
            wf["pre_commit_report"] = self._build_pre_commit_report(wf, decision)
            require_gate = policy.get("require_pre_commit_approval", True)
            if require_gate or not ALLOW_AUTO_COMMIT:
                if not require_gate and not ALLOW_AUTO_COMMIT:
                    self._append_log(
                        wf,
                        "system",
                        "Auto-commit blocked: set ALLOW_AUTO_COMMIT=true to bypass gate #2",
                    )
                self._set_state(wf, WorkflowState.COMMIT_PENDING_APPROVAL)
            else:
                await self.approve_commit(task_num)
        elif decision == "REQUEST_CHANGES":
            if allow_loop and wf.get("review_iterations", 0) < max_iter:
                wf["review_iterations"] = wf.get("review_iterations", 0) + 1
                self._update_agent_state_status(wf, "IN_PROGRESS")
                self._set_state(wf, WorkflowState.EXECUTOR_RUNNING)
                await self._spawn_executor(wf)
            else:
                self._set_state(
                    wf, WorkflowState.FAILED,
                    reason=f"Review loop exhausted after {wf.get('review_iterations', 0)} iterations",
                )
        elif decision == "REJECT":
            self._set_state(wf, WorkflowState.FAILED, reason="Review rejected by reviewer")
        else:
            self._set_state(wf, WorkflowState.FAILED, reason=f"Unknown review decision: {decision}")

    async def _run_subprocess(
        self,
        wf: dict[str, Any],
        cmd: list[str],
        cwd: str,
        timeout: int,
        label: str,
        capture_only: bool = False,
    ) -> str:
        """Run subprocess with log streaming to WebSocket clients."""
        self._append_log(wf, "system", f"Starting {label}: {' '.join(cmd)}")
        output_lines: list[str] = []

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )

            async def _read_stream(stream, name: str) -> None:
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").rstrip()
                    output_lines.append(text)
                    self._append_log(wf, name, text)

            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        _read_stream(proc.stdout, "stdout"),
                        _read_stream(proc.stderr, "stderr"),
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    proc.kill()
                self._set_state(wf, WorkflowState.FAILED, reason=f"{label} timed out after {timeout}s")
                return "\n".join(output_lines)

            await proc.wait()
            if proc.returncode != 0:
                self._append_log(wf, "system", f"{label} exited with code {proc.returncode}")
                if not capture_only:
                    self._set_state(
                        wf, WorkflowState.FAILED,
                        reason=f"{label} failed with exit code {proc.returncode}",
                    )
            else:
                self._append_log(wf, "system", f"{label} completed successfully")

        except Exception as exc:
            self._set_state(wf, WorkflowState.FAILED, reason=f"{label} subprocess error: {exc}")

        return "\n".join(output_lines)

    def _read_task_status(self, agent_state_path: str, task_num: str) -> str | None:
        if not os.path.isfile(agent_state_path):
            return None
        try:
            with open(agent_state_path, "r", encoding="utf-8") as f:
                content = f.read()
            pattern = rf"\|\s*\*\*TASK-{task_num}\*\*\s*\|\s*`([^`]+)`"
            match = re.search(pattern, content)
            return match.group(1) if match else None
        except Exception:
            return None

    def _update_agent_state_status(self, wf: dict[str, Any], new_status: str) -> None:
        workflow_path = self._workflow_path(wf)
        task_num = wf.get("task_num")
        if not workflow_path or not task_num:
            return
        agent_state_path = os.path.join(workflow_path, "AGENT_STATE.md")
        if not os.path.isfile(agent_state_path):
            return
        try:
            with open(agent_state_path, "r", encoding="utf-8") as f:
                content = f.read()
            pattern = rf"(\|\s*\*\*TASK-{task_num}\*\*\s*\|\s*)`[^`]+`"
            replacement = rf"\g<1>`{new_status}`"
            new_content = re.sub(pattern, replacement, content, count=1)
            with open(agent_state_path, "w", encoding="utf-8") as f:
                f.write(new_content)
        except Exception as exc:
            logger.warning("Failed to update AGENT_STATE for TASK-%s: %s", task_num, exc)

    def _git_changed_files(self, wf: dict[str, Any]) -> str:
        target_path = wf.get("export_result", {}).get("targetPath")
        task_num = wf.get("task_num")
        if not target_path or not task_num:
            return wf.get("files_affected", "Unknown")
        try:
            # Prefer files changed on the executor branch vs current base branch
            branch = self._discover_task_branch(target_path, task_num)
            if branch:
                result = subprocess.run(
                    ["git", "diff", f"HEAD..{branch}", "--name-only"],
                    cwd=target_path,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip()

            # Fallback: working-tree changes relative to HEAD
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=target_path,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()

            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=target_path,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                lines = [
                    ln[3:].strip() for ln in result.stdout.splitlines() if ln.strip()
                ]
                return "\n".join(lines) if lines else wf.get("files_affected", "None detected")
        except Exception:
            pass
        return wf.get("files_affected", "See executor output")

    def _build_pre_commit_report(self, wf: dict[str, Any], decision: str) -> dict[str, Any]:
        conversation_id = wf.get("conversation_id")
        summary = "Task completed per council plan."
        if conversation_id:
            try:
                conv = load_conversation(conversation_id)
                for msg in reversed(conv.get("messages", [])):
                    if msg.get("stage3"):
                        summary = msg["stage3"].get("response", summary)[:300]
                        break
            except Exception:
                pass

        return {
            "task_id": f"TASK-{wf.get('task_num')}",
            "title": wf.get("title"),
            "files_changed": self._git_changed_files(wf),
            "review_decision": decision,
            "review_iterations": wf.get("review_iterations", 0),
            "chairman_summary": summary,
        }

    async def _chairman_final_summary(self, wf: dict[str, Any]) -> str:
        conversation_id = wf.get("conversation_id")
        base = "Task committed successfully."
        chairman_model = wf.get("chairman_model")
        if conversation_id:
            try:
                conv = load_conversation(conversation_id)
                for msg in reversed(conv.get("messages", [])):
                    if msg.get("stage3"):
                        base = msg["stage3"].get("response", base)
                        chairman_model = chairman_model or msg["stage3"].get("model")
                        break
            except Exception:
                pass
        if wf.get("mock") or not chairman_model:
            return f"Final summary: {base[:500]}"
        try:
            from council.llm_provider import query_model
            response = await query_model(
                chairman_model,
                [{
                    "role": "user",
                    "content": (
                        "Write a one-paragraph final completion summary for this committed task:\n\n"
                        + base[:2000]
                    ),
                }],
                temperature=0.3,
            )
            return response[:500]
        except Exception as exc:
            logger.warning("Chairman final summary failed, using fallback: %s", exc)
            return f"Final summary: {base[:500]}"

    def _write_final_report_file(self, wf: dict[str, Any], report: dict[str, Any]) -> None:
        workflow_path = self._workflow_path(wf)
        task_num = wf.get("task_num")
        if not workflow_path or not task_num:
            return
        planning_dir = os.path.join(workflow_path, "PLANNING")
        os.makedirs(planning_dir, exist_ok=True)
        report_path = os.path.join(planning_dir, f"final_report_{task_num}.md")
        content = (
            f"# Final Report: TASK-{task_num}\n\n"
            f"- **Title**: {report.get('title')}\n"
            f"- **Committed**: {report.get('committed_at')}\n"
            f"- **Review Iterations**: {report.get('review_iterations', 0)}\n\n"
            f"## Chairman Summary\n\n{report.get('chairman_summary', '')}\n\n"
            f"## Files Changed\n\n{report.get('files_changed', '')}\n"
        )
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(content)
            self._append_log(wf, "system", f"Wrote final report to {report_path}")
        except OSError as exc:
            logger.warning("Failed to write final report: %s", exc)

    async def _generate_final_report(self, wf: dict[str, Any]) -> dict[str, Any]:
        report = self._build_pre_commit_report(wf, "COMMITTED")
        report["chairman_summary"] = await self._chairman_final_summary(wf)
        report["committed_at"] = _now_iso()
        self._write_final_report_file(wf, report)
        return report

    async def _ensure_git_repo(self, wf: dict[str, Any], target_path: str) -> None:
        """Initialize git repo in target project if missing."""
        git_dir = os.path.join(target_path, ".git")
        if os.path.isdir(git_dir):
            return
        self._append_log(wf, "git", "Initializing git repository in target project")
        await self._run_git_command(wf, target_path, ["git", "init", "-b", "main"])
        await self._run_git_command(wf, target_path, ["git", "config", "user.email", "maw@localhost"])
        await self._run_git_command(wf, target_path, ["git", "config", "user.name", "MAW"])
        await self._run_git_command(wf, target_path, ["git", "add", "-A"])
        await self._run_git_command(wf, target_path, ["git", "commit", "-m", "Initial commit"], allow_failure=True)

    async def _run_git_command(
        self,
        wf: dict[str, Any],
        cwd: str,
        cmd: list[str],
        allow_failure: bool = False,
    ) -> str:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        out = stdout.decode() + stderr.decode()
        self._append_log(wf, "git", f"$ {' '.join(cmd)}\n{out}")
        if proc.returncode != 0 and not allow_failure:
            raise RuntimeError(f"Git command failed: {' '.join(cmd)}: {out}")
        return out

    def _discover_task_branch(self, target_path: str, task_num: str) -> str | None:
        """Find the executor-created branch for this task."""
        try:
            result = subprocess.run(
                ["git", "branch", "--format=%(refname:short)"],
                cwd=target_path,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return None
            prefix = f"task/task_{task_num}_"
            branches = [b.strip() for b in result.stdout.splitlines() if b.strip().startswith(prefix)]
            if not branches:
                return None
            if len(branches) == 1:
                return branches[0]
            # Multiple branches: prefer the one with latest commit
            best_branch = None
            best_time = ""
            for branch in branches:
                time_result = subprocess.run(
                    ["git", "log", "-1", "--format=%ci", branch],
                    cwd=target_path,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if time_result.returncode == 0 and time_result.stdout.strip() > best_time:
                    best_time = time_result.stdout.strip()
                    best_branch = branch
            return best_branch
        except Exception:
            return None

    async def _perform_git_commit(self, wf: dict[str, Any]) -> None:
        target_path = wf["export_result"]["targetPath"]
        task_num = wf["task_num"]
        title = wf.get("title", f"Task {task_num}")
        message = f"TASK-{task_num}: {title}"

        await self._ensure_git_repo(wf, target_path)

        branch = self._discover_task_branch(target_path, task_num)
        if not branch:
            raise RuntimeError(f"No executor branch found matching task/task_{task_num}_*")

        await self._run_git_command(wf, target_path, ["git", "add", "-A"])
        await self._run_git_command(wf, target_path, ["git", "commit", "-m", message, "--allow-empty"])
        await self._run_git_command(wf, target_path, ["git", "merge", branch, "--no-edit"])
        wf["merge_output"] = wf.get("logs", [])[-1].get("line", "") if wf.get("logs") else ""


# Module-level singleton
orchestrator = LoopOrchestrator()