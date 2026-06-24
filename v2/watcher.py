"""v2 watcher — polls artifacts, triggers transitions, dispatches agents.

Can be run as:
    python -m v2.watcher --target /path/to/project [--workflow-id wf_001]

Or used programmatically via the Watcher class.
"""

import os
import time
import signal
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from v2.schema import (
    WorkflowStatus,
    TERMINAL_STATES,
    WAITING_STATES,
    load_manifest,
    save_manifest,
    set_status,
    all_agents,
    APPROVE,
    REQUEST_CHANGES,
    CANCEL,
)
from v2.files import (
    workflow_dir,
    write_atomic,
    read_file,
    read_file_optional,
    append_event,
    exists_nonempty,
    load_active_workflow_id,
    set_active_workflow,
    load_runtime_state,
    save_runtime_state,
    get_dispatch_record,
    persist_dispatch,
    reconcile_runtime_state,
    acquire_executor_lock,
    release_executor_lock,
    check_executor_lock,
    RUNTIME_STATUS_PENDING,
    RUNTIME_STATUS_DISPATCHED,
    RUNTIME_STATUS_COMPLETED,
    RUNTIME_STATUS_FAILED,
    RUNTIME_STATUS_STALE,
)
from v2.workflow import (
    compute_dispatch,
    try_transition,
    user_answer,
    user_decision,
    user_cancel,
    mark_failed,
    generate_instruction,
    DispatchItem,
    instruction_path,
    ARTIFACT_ANSWERS,
    ARTIFACT_USER_DECISION,
)
from v2.dispatcher import dispatch, cancel_item, DispatchResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("maw.watcher")

DEFAULT_POLL_INTERVAL = float(os.getenv("WATCHER_POLL_INTERVAL", "3"))
DEFAULT_AGENT_TIMEOUT = int(os.getenv("AGENT_TIMEOUT_SECONDS", "600"))
DEFAULT_MAX_RETRIES = int(os.getenv("MAX_AGENT_RETRIES", "2"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Watcher:
    def __init__(
        self,
        target_path: str,
        workflow_id: Optional[str] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        agent_timeout: int = DEFAULT_AGENT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        auto_run: bool = True,
    ):
        self.target_path = str(Path(target_path).resolve())
        self.workflow_id = workflow_id or load_active_workflow_id(self.target_path)
        self.poll_interval = poll_interval
        self.agent_timeout = agent_timeout
        self.max_retries = max_retries
        self.auto_run = auto_run

        self._running = False
        self._active_dispatches: dict[str, dict] = {}
        self._agent_locks: dict[str, bool] = {}
        self._failure_counts: dict[str, int] = {}
        self._runtime_state: dict[str, Any] = {"schema_version": 1, "dispatches": {}}

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    # ── public API ──

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._load_state()
        logger.info("Watcher started for %s (workflow: %s)", self.target_path, self.workflow_id)
        self._loop()

    def stop(self) -> None:
        self._running = False
        self._cancel_all_dispatches()
        logger.info("Watcher stopped")

    def run_once(self) -> bool:
        """Tick once; return True if still active."""
        if not self.workflow_id:
            logger.warning("No active workflow")
            return False
        wf_dir = workflow_dir(self.target_path, self.workflow_id)
        manifest = load_manifest(str(wf_dir))
        if WorkflowStatus(manifest["status"]) in TERMINAL_STATES:
            logger.info("Workflow %s is in terminal state %s", self.workflow_id, manifest["status"])
            return False
        self._tick(wf_dir, manifest)
        return True

    def inject_answer(self, answer: str) -> bool:
        if not self.workflow_id:
            return False
        wf_dir = workflow_dir(self.target_path, self.workflow_id)
        manifest = load_manifest(str(wf_dir))
        return user_answer(wf_dir, manifest, answer)

    def inject_decision(self, decision: str) -> bool:
        if not self.workflow_id:
            return False
        wf_dir = workflow_dir(self.target_path, self.workflow_id)
        manifest = load_manifest(str(wf_dir))
        return user_decision(wf_dir, manifest, decision)

    def inject_cancel(self) -> bool:
        if not self.workflow_id:
            return False
        wf_dir = workflow_dir(self.target_path, self.workflow_id)
        manifest = load_manifest(str(wf_dir))
        return user_cancel(wf_dir, manifest)

    # ── internals ──

    def _loop(self) -> None:
        consecutive_idle = 0
        max_idle = 10  # slow down polling after extended idle

        while self._running:
            if not self.workflow_id:
                time.sleep(self.poll_interval)
                continue

            wf_dir = workflow_dir(self.target_path, self.workflow_id)
            manifest = load_manifest(str(wf_dir))
            status = WorkflowStatus(manifest["status"])

            if status in TERMINAL_STATES:
                logger.info("Workflow %s reached %s", self.workflow_id, status.value)
                self._cancel_all_dispatches()
                break

            changed = self._tick(wf_dir, manifest)
            if changed:
                consecutive_idle = 0
            else:
                consecutive_idle += 1

            # Adaptive polling
            if status in WAITING_STATES:
                time.sleep(self.poll_interval * 2)  # slower poll when waiting for user
            elif consecutive_idle > max_idle:
                time.sleep(self.poll_interval * 3)
            else:
                time.sleep(self.poll_interval)

    def _tick(self, wf_dir: Path, manifest: dict) -> bool:
        changed = False

        # 1. Check for active dispatch completions
        if self._check_completions(wf_dir, manifest):
            changed = True

        # 2. Try automatic state transitions
        if try_transition(wf_dir, manifest):
            changed = True
            # Reload manifest after transition
            manifest = load_manifest(str(wf_dir))

        # 3. Compute and dispatch new work
        items = compute_dispatch(wf_dir, manifest)
        for item in items:
            if self._should_dispatch(item, manifest):
                if self._schedule_dispatch(wf_dir, manifest, item):
                    changed = True

        return changed

    def _should_dispatch(self, item: DispatchItem, manifest: dict) -> bool:
        record = get_dispatch_record(self._runtime_state, item.key)
        if record:
            status = record.get("status", "")
            if status == RUNTIME_STATUS_COMPLETED:
                return False
            if status == RUNTIME_STATUS_DISPATCHED:
                return False
        if item.key in self._active_dispatches:
            return False
        if self._agent_locks.get(item.agent):
            return False
        if self._failure_counts.get(item.key, 0) >= self.max_retries:
            logger.warning("Max retries exceeded for %s", item.key)
            return False
        if item.agent == "maw":
            return False
        # Executor lock: only one executor per target
        if item.role == "executor":
            existing = check_executor_lock(self.target_path)
            if existing and existing.get("workflow_id") != self.workflow_id:
                logger.info("Executor locked by workflow %s", existing.get("workflow_id"))
                return False
        return True

    def _schedule_dispatch(self, wf_dir: Path, manifest: dict, item: DispatchItem) -> bool:
        logger.info("Dispatching %s -> agent=%s role=%s phase=%s",
                     item.key, item.agent, item.role, item.phase)

        now = _now()
        attempt = self._failure_counts.get(item.key, 0) + 1

        # Persist pending dispatch to runtime_state
        record = {
            "status": RUNTIME_STATUS_DISPATCHED,
            "agent": item.agent,
            "role": item.role,
            "seat": item.seat,
            "iteration": item.iteration,
            "attempt": attempt,
            "expected_output": self._expected_output_for(item, wf_dir),
            "started_at": now,
            "updated_at": now,
            "invocation_id": None,
            "last_error": None,
        }
        persist_dispatch(wf_dir, self._runtime_state, item.key, record)

        # Generate instruction
        instruction = generate_instruction(wf_dir, manifest, item)
        inst_path = wf_dir / instruction_path(item.seat)
        write_atomic(inst_path, instruction)

        # Lock agent
        self._agent_locks[item.agent] = True

        # Acquire executor lock if applicable
        if item.role == "executor":
            acquire_executor_lock(self.target_path, self.workflow_id, item.key)

        # Record dispatch
        self._active_dispatches[item.key] = {
            "item": item,
            "started_at": now,
            "timeout_at": now,
        }

        append_event(wf_dir, {
            "ts": now,
            "type": "agent.dispatched",
            "role": item.role,
            "seat": item.seat,
            "agent": item.agent,
            "attempt": attempt,
            "key": item.key,
        })

        if self.auto_run:
            result = dispatch(item, wf_dir, instruction, timeout=self.agent_timeout)
            self._handle_result(wf_dir, manifest, item, result)
        return True

    def _handle_result(self, wf_dir: Path, manifest: dict, item: DispatchItem, result: DispatchResult) -> None:
        self._agent_locks[item.agent] = False
        if item.role == "executor":
            release_executor_lock(self.target_path)
        now = _now()

        if result.success:
            record = get_dispatch_record(self._runtime_state, item.key)
            if record:
                record["status"] = RUNTIME_STATUS_COMPLETED
                record["updated_at"] = now
                persist_dispatch(wf_dir, self._runtime_state, item.key, record)
            self._failure_counts.pop(item.key, None)
            append_event(wf_dir, {
                "ts": now,
                "type": "agent.completed",
                "key": item.key,
                "agent": item.agent,
            })
            logger.info("Completed %s", item.key)
        else:
            self._failure_counts[item.key] = self._failure_counts.get(item.key, 0) + 1
            attempt = self._failure_counts[item.key]
            record = get_dispatch_record(self._runtime_state, item.key)
            if record:
                record["status"] = RUNTIME_STATUS_FAILED
                record["attempt"] = attempt
                record["last_error"] = result.error or "unknown"
                record["updated_at"] = now
                persist_dispatch(wf_dir, self._runtime_state, item.key, record)
            append_event(wf_dir, {
                "ts": now,
                "type": "agent.failed",
                "key": item.key,
                "agent": item.agent,
                "reason": result.error or "unknown",
                "attempt": attempt,
            })
            logger.warning("Failed %s: %s (attempt %d)", item.key, result.error, attempt)

            if self._failure_counts[item.key] >= self.max_retries:
                mark_failed(wf_dir, manifest, f"Agent {item.agent} failed {item.key} after {self.max_retries} retries: {result.error}")

        self._active_dispatches.pop(item.key, None)

    def _check_completions(self, wf_dir: Path, manifest: dict) -> bool:
        """Check all active dispatches for artifact completion (async/manual mode)."""
        changed = False
        now = _now()
        to_complete = []

        for key, info in list(self._active_dispatches.items()):
            item = info["item"]
            expected_output = self._expected_output_for(item, wf_dir)
            if expected_output and exists_nonempty(wf_dir / expected_output):
                to_complete.append((key, item))

        for key, item in to_complete:
            self._agent_locks[item.agent] = False
            if item.role == "executor":
                release_executor_lock(self.target_path)
            record = get_dispatch_record(self._runtime_state, key)
            if record:
                record["status"] = RUNTIME_STATUS_COMPLETED
                record["updated_at"] = now
                persist_dispatch(wf_dir, self._runtime_state, key, record)
            self._failure_counts.pop(key, None)
            self._active_dispatches.pop(key, None)
            append_event(wf_dir, {
                "ts": now,
                "type": "agent.completed",
                "key": key,
                "agent": item.agent,
            })
            logger.info("Async-completed %s (artifact detected)", key)
            changed = True

        # Also check stale dispatches for timeout
        to_stale = []
        for key, info in list(self._active_dispatches.items()):
            started = info.get("started_at", "")
            if started:
                try:
                    from datetime import datetime
                    started_dt = datetime.fromisoformat(started)
                    elapsed = (datetime.now(started_dt.tzinfo) - started_dt).total_seconds()
                    if elapsed > self.agent_timeout:
                        to_stale.append((key, info["item"]))
                except (ValueError, TypeError):
                    pass

        for key, item in to_stale:
            self._agent_locks[item.agent] = False
            if item.role == "executor":
                release_executor_lock(self.target_path)
            record = get_dispatch_record(self._runtime_state, key)
            if record:
                record["status"] = RUNTIME_STATUS_STALE
                record["updated_at"] = now
                persist_dispatch(wf_dir, self._runtime_state, key, record)
            self._active_dispatches.pop(key, None)
            append_event(wf_dir, {
                "ts": now,
                "type": "agent.stale",
                "key": key,
                "agent": item.agent,
            })
            logger.warning("Stale dispatch %s (timeout)", key)
            changed = True

        return changed

    def _expected_output_for(self, item, wf_dir: Path) -> str:
        """Return the workflow-relative expected output path for a dispatch item."""
        from v2.schema import (
            proposal_path, comment_path, walkthrough_path, review_path,
            ARTIFACT_CHAIR_BRIEF, ARTIFACT_FINAL_PLAN, ARTIFACT_COMMIT,
            ARTIFACT_COMPLETION, ARTIFACT_QUESTIONS,
        )
        if item.role == "chair" and item.phase in ("clarify",):
            return ARTIFACT_CHAIR_BRIEF
        elif item.role == "planner" and item.phase == "proposal":
            return proposal_path(item.seat)
        elif item.role == "planner" and item.phase == "comment":
            key_parts = item.key.split(":")
            if len(key_parts) >= 3:
                seat_pair = key_parts[2]
                parts = seat_pair.split("_on_")
                if len(parts) == 2:
                    return comment_path(parts[0], parts[1])
            return ""
        elif item.role == "chair" and item.phase == "synthesis":
            return ARTIFACT_FINAL_PLAN
        elif item.role == "executor" and item.phase == "execution":
            return walkthrough_path(item.iteration)
        elif item.role == "reviewer" and item.phase == "review":
            return review_path(item.iteration)
        elif item.role == "executor" and item.phase == "commit":
            return ARTIFACT_COMMIT
        elif item.role == "chair" and item.phase == "final_check":
            return ARTIFACT_COMPLETION
        return ""

    def _cancel_all_dispatches(self) -> None:
        for key, info in list(self._active_dispatches.items()):
            item = info["item"]
            cancel_item(item)
            self._agent_locks[item.agent] = False
        self._active_dispatches.clear()

    def _load_state(self) -> None:
        if not self.workflow_id:
            return
        wf_dir = workflow_dir(self.target_path, self.workflow_id)
        if not wf_dir.is_dir():
            logger.warning("Workflow dir not found: %s", wf_dir)
            self.workflow_id = None
            return

        manifest = load_manifest(str(wf_dir))
        logger.info("Loaded workflow %s (status: %s)", self.workflow_id, manifest["status"])
        logger.info("Roster: chair=%s, planners=%d, executor=%s, reviewer=%s",
                     manifest["roster"]["chair"],
                     len(manifest["roster"]["planners"]),
                     manifest["roster"]["executor"],
                     manifest["roster"]["reviewer"])

        # Load and reconcile runtime state
        self._runtime_state = load_runtime_state(wf_dir)
        reconcile_runtime_state(wf_dir, self._runtime_state)

        # Recover failure counts from runtime_state
        for key, rec in self._runtime_state.get("dispatches", {}).items():
            att = rec.get("attempt", 1)
            status = rec.get("status", "")
            if status in (RUNTIME_STATUS_FAILED, RUNTIME_STATUS_STALE):
                self._failure_counts[key] = att

    def _handle_signal(self, signum, frame):
        logger.info("Received signal %s, stopping...", signum)
        self.stop()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="MAW v2 Watcher")
    parser.add_argument("--target", "-t", required=True, help="Path to target project")
    parser.add_argument("--workflow-id", "-w", help="Workflow ID to watch")
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL_INTERVAL, help="Poll interval in seconds")
    parser.add_argument("--timeout", type=int, default=DEFAULT_AGENT_TIMEOUT, help="Agent timeout in seconds")
    parser.add_argument("--once", action="store_true", help="Run one tick and exit")
    parser.add_argument("--no-auto", action="store_true", help="Do not auto-dispatch agents")
    args = parser.parse_args()

    watcher = Watcher(
        target_path=args.target,
        workflow_id=args.workflow_id,
        poll_interval=args.poll,
        agent_timeout=args.timeout,
        auto_run=not args.no_auto,
    )

    if args.once:
        watcher.run_once()
    else:
        watcher.start()


if __name__ == "__main__":
    main()
