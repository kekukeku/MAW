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
        self._dispatch_history: dict[str, str] = {}
        self._failure_counts: dict[str, int] = {}

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
        # Already completed
        if item.key in self._dispatch_history:
            return False
        # Already in-flight
        if item.key in self._active_dispatches:
            return False
        # Agent locked (another dispatch for same agent in-flight)
        if self._agent_locks.get(item.agent):
            return False
        # Too many failures
        if self._failure_counts.get(item.key, 0) >= self.max_retries:
            logger.warning("Max retries exceeded for %s", item.key)
            return False
        # System/internal items (maw agent) are auto-handled
        if item.agent == "maw":
            return False
        return True

    def _schedule_dispatch(self, wf_dir: Path, manifest: dict, item: DispatchItem) -> bool:
        logger.info("Dispatching %s -> agent=%s role=%s phase=%s",
                     item.key, item.agent, item.role, item.phase)

        # Generate instruction
        instruction = generate_instruction(wf_dir, manifest, item)
        inst_path = wf_dir / instruction_path(item.seat)
        write_atomic(inst_path, instruction)

        # Lock agent
        self._agent_locks[item.agent] = True

        # Record dispatch
        self._active_dispatches[item.key] = {
            "item": item,
            "started_at": _now(),
            "timeout_at": _now(),
        }

        append_event(wf_dir, {
            "ts": _now(),
            "type": "agent.dispatched",
            "role": item.role,
            "seat": item.seat,
            "agent": item.agent,
            "attempt": self._failure_counts.get(item.key, 0) + 1,
            "key": item.key,
        })

        if self.auto_run:
            result = dispatch(item, wf_dir, instruction, timeout=self.agent_timeout)
            self._handle_result(wf_dir, manifest, item, result)
        return True

    def _handle_result(self, wf_dir: Path, manifest: dict, item: DispatchItem, result: DispatchResult) -> None:
        # Release agent lock
        self._agent_locks[item.agent] = False

        if result.success:
            self._dispatch_history[item.key] = "completed"
            self._failure_counts.pop(item.key, None)
            append_event(wf_dir, {
                "ts": _now(),
                "type": "agent.completed",
                "key": item.key,
                "agent": item.agent,
            })
            logger.info("Completed %s", item.key)
        else:
            self._failure_counts[item.key] = self._failure_counts.get(item.key, 0) + 1
            append_event(wf_dir, {
                "ts": _now(),
                "type": "agent.failed",
                "key": item.key,
                "agent": item.agent,
                "reason": result.error or "unknown",
                "attempt": self._failure_counts[item.key],
            })
            logger.warning("Failed %s: %s (attempt %d)", item.key, result.error, self._failure_counts[item.key])

            if self._failure_counts[item.key] >= self.max_retries:
                mark_failed(wf_dir, manifest, f"Agent {item.agent} failed {item.key} after {self.max_retries} retries: {result.error}")

        # Remove from active
        self._active_dispatches.pop(item.key, None)

    def _check_completions(self, wf_dir: Path, manifest: dict) -> bool:
        """Check for async dispatch completions (when auto_run=False). Not used in sync mode."""
        return False

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
