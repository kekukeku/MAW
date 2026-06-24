"""v2 files — path resolution, atomic writes, artifact discovery."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from v2.schema import (
    ARTIFACT_REQUEST,
    ARTIFACT_QUESTIONS,
    ARTIFACT_ANSWERS,
    ARTIFACT_CHAIR_BRIEF,
    ARTIFACT_FINAL_PLAN,
    ARTIFACT_USER_DECISION,
    ARTIFACT_TASK,
    ARTIFACT_COMMIT,
    ARTIFACT_COMPLETION,
    ARTIFACT_EVENTS,
    DIR_PROPOSALS,
    DIR_COMMENTS,
    DIR_WALKTHROUGHS,
    DIR_REVIEWS,
    DIR_INSTRUCTIONS,
    proposal_path,
    comment_path,
    walkthrough_path,
    review_path,
    instruction_path,
)


MAW_WORKFLOW_DIR = "MAW_workflow"
WORKFLOWS_DIR = "workflows"
ARCHIVE_DIR = "archive"


def target_workflow_root(target_path: str) -> Path:
    return Path(target_path).resolve() / MAW_WORKFLOW_DIR


def workflows_dir(target_path: str) -> Path:
    return target_workflow_root(target_path) / WORKFLOWS_DIR


def workflow_dir(target_path: str, workflow_id: str) -> Path:
    return workflows_dir(target_path) / workflow_id


def ensure_workflow_dirs(wf_dir: Path) -> None:
    for sub in [DIR_PROPOSALS, DIR_COMMENTS, DIR_WALKTHROUGHS, DIR_REVIEWS, DIR_INSTRUCTIONS]:
        (wf_dir / sub).mkdir(parents=True, exist_ok=True)


def write_atomic(path: Path, content: str) -> Path:
    tmp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(path)
    return path


def write_json_atomic(path: Path, data: Any) -> Path:
    return write_atomic(path, json.dumps(data, indent=2))


def exists_nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_file_optional(path: Path) -> Optional[str]:
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return None


def append_event(wf_dir: Path, event: dict) -> None:
    line = json.dumps(event, ensure_ascii=False) + "\n"
    evt_path = wf_dir / ARTIFACT_EVENTS
    with open(evt_path, "a", encoding="utf-8") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# Artifact helpers
# ---------------------------------------------------------------------------

def artifact_path(wf_dir: Path, artifact_name: str) -> Path:
    return wf_dir / artifact_name


def proposal_exists(wf_dir: Path, seat: str) -> bool:
    return exists_nonempty(wf_dir / proposal_path(seat))


def all_proposals_exist(wf_dir: Path, seats: list[str]) -> bool:
    return all(proposal_exists(wf_dir, s) for s in seats)


def comment_exists(wf_dir: Path, reviewer_seat: str, target_seat: str) -> bool:
    return exists_nonempty(wf_dir / comment_path(reviewer_seat, target_seat))


def all_comments_exist(wf_dir: Path, seats: list[str]) -> bool:
    for reviewer_seat in seats:
        for target_seat in seats:
            if reviewer_seat != target_seat:
                if not comment_exists(wf_dir, reviewer_seat, target_seat):
                    return False
    return True


def walkthrough_exists(wf_dir: Path, iteration: int) -> bool:
    return exists_nonempty(wf_dir / walkthrough_path(iteration))


def review_exists(wf_dir: Path, iteration: int) -> bool:
    return exists_nonempty(wf_dir / review_path(iteration))


# ---------------------------------------------------------------------------
# Scaffold
# ---------------------------------------------------------------------------

def scaffold_target(target_path: str) -> Path:
    root = Path(target_path).resolve()
    root.mkdir(parents=True, exist_ok=True)

    wf_root = root / MAW_WORKFLOW_DIR
    wf_root.mkdir(exist_ok=True)
    (wf_root / WORKFLOWS_DIR).mkdir(exist_ok=True)
    (wf_root / ARCHIVE_DIR).mkdir(exist_ok=True)

    # Copy templates if target doesn't already have them
    _copy_template(root, "AGENTS.md")
    _copy_template(root, "TEAM_RULES.md")

    return wf_root


def _copy_template(target_root: Path, filename: str) -> Optional[Path]:
    """Copy a template file from v2_templates/ to target, never overwriting."""
    import os as _os
    template_dir = Path(__file__).resolve().parent.parent / "v2_templates"
    src = template_dir / filename
    dst = target_root / filename
    if not src.is_file():
        return None
    if dst.is_file():
        return None  # Never overwrite
    try:
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        return dst
    except OSError:
        return None


def init_workflow(target_path: str, workflow_id: str, manifest: dict, request: str) -> Path:
    root = Path(target_path).resolve()
    wf_dir = workflow_dir(str(root), workflow_id)
    wf_dir.mkdir(parents=True, exist_ok=True)
    ensure_workflow_dirs(wf_dir)

    write_json_atomic(wf_dir / "manifest.json", manifest)
    write_atomic(wf_dir / ARTIFACT_REQUEST, request)

    append_event(wf_dir, {
        "ts": manifest["created_at"],
        "type": "workflow.created",
        "actor": "maw",
    })

    return wf_dir


def list_workflow_ids(target_path: str) -> list[str]:
    d = workflows_dir(target_path)
    if not d.is_dir():
        return []
    return sorted(
        [p.name for p in d.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]
    )


def load_active_workflow_id(target_path: str) -> Optional[str]:
    active_file = target_workflow_root(target_path) / "ACTIVE_WORKFLOW"
    if active_file.is_file():
        return active_file.read_text(encoding="utf-8").strip()
    return None


def set_active_workflow(target_path: str, workflow_id: str) -> None:
    active_file = target_workflow_root(target_path) / "ACTIVE_WORKFLOW"
    active_file.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(active_file, workflow_id)


# ---------------------------------------------------------------------------
# Runtime state persistence
# ---------------------------------------------------------------------------

RUNTIME_STATE_FILE = "runtime_state.json"
RUNTIME_STATE_SCHEMA = 1

RUNTIME_STATUS_PENDING = "pending"
RUNTIME_STATUS_DISPATCHED = "dispatched"
RUNTIME_STATUS_COMPLETED = "completed"
RUNTIME_STATUS_FAILED = "failed"
RUNTIME_STATUS_STALE = "stale"
RUNTIME_STATUS_CANCELLED = "cancelled"


def runtime_state_path(wf_dir: Path) -> Path:
    return wf_dir / RUNTIME_STATE_FILE


def load_runtime_state(wf_dir: Path) -> dict:
    path = runtime_state_path(wf_dir)
    if not path.is_file():
        return {"schema_version": RUNTIME_STATE_SCHEMA, "dispatches": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"schema_version": RUNTIME_STATE_SCHEMA, "dispatches": {}}
        data.setdefault("schema_version", RUNTIME_STATE_SCHEMA)
        data.setdefault("dispatches", {})
        return data
    except (json.JSONDecodeError, OSError):
        return {"schema_version": RUNTIME_STATE_SCHEMA, "dispatches": {}}


def save_runtime_state(wf_dir: Path, state: dict) -> Path:
    return write_json_atomic(runtime_state_path(wf_dir), state)


def get_dispatch_record(state: dict, key: str) -> Optional[dict]:
    return state.get("dispatches", {}).get(key)


def set_dispatch_record(state: dict, key: str, record: dict) -> None:
    state.setdefault("dispatches", {})[key] = record


def all_dispatch_keys(state: dict) -> list[str]:
    return sorted(state.get("dispatches", {}).keys())


def persist_dispatch(wf_dir: Path, runtime_state: dict, key: str, record: dict) -> None:
    set_dispatch_record(runtime_state, key, record)
    save_runtime_state(wf_dir, runtime_state)


def reconcile_runtime_state(wf_dir: Path, runtime_state: dict, *, agent_timeout: int = 600) -> None:
    """After restart: mark completed dispatches where artifact exists.

    Dispatched items that have not timed out are left as 'dispatched' so
    _check_completions can continue polling them. Only items whose
    started_at + agent_timeout has passed are marked stale.
    """
    changed = False
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    for key, rec in list(runtime_state.get("dispatches", {}).items()):
        status = rec.get("status", "")
        expected = rec.get("expected_output", "")
        if status == RUNTIME_STATUS_DISPATCHED and expected:
            artifact = wf_dir / expected
            if exists_nonempty(artifact):
                rec["status"] = RUNTIME_STATUS_COMPLETED
                rec["updated_at"] = now_iso
                changed = True
            else:
                started = rec.get("started_at", "")
                if started:
                    try:
                        started_dt = datetime.fromisoformat(started)
                        elapsed = (now - started_dt).total_seconds()
                    except (ValueError, TypeError):
                        elapsed = 0
                else:
                    elapsed = 0
                if elapsed > agent_timeout:
                    rec["status"] = RUNTIME_STATUS_STALE
                    rec["updated_at"] = now_iso
                    changed = True
                # Otherwise leave as dispatched — still in-flight
    if changed:
        save_runtime_state(wf_dir, runtime_state)


# ---------------------------------------------------------------------------
# Per-target executor lock
# ---------------------------------------------------------------------------

EXECUTOR_LOCK_FILE = "EXECUTOR_LOCK.json"


def _executor_lock_path(target_path: str) -> Path:
    return target_workflow_root(target_path) / EXECUTOR_LOCK_FILE


def acquire_executor_lock(target_path: str, workflow_id: str, dispatch_key: str, *, lock_timeout: int = 600) -> bool:
    path = _executor_lock_path(target_path)
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()

    existing = check_executor_lock(target_path)
    if existing:
        # Same workflow can re-acquire (update dispatch_key)
        if existing.get("workflow_id") == workflow_id:
            pass  # allow overwrite
        else:
            started = existing.get("started_at", "")
            if started:
                try:
                    started_dt = datetime.fromisoformat(started)
                    elapsed = (now_dt - started_dt).total_seconds()
                    if elapsed > lock_timeout:
                        pass  # stale lock from another workflow — overwrite
                    else:
                        return False  # Active lock held by another workflow
                except (ValueError, TypeError):
                    pass  # unparseable — overwrite stale

    lock_data = {
        "workflow_id": workflow_id,
        "dispatch_key": dispatch_key,
        "started_at": now,
    }
    try:
        write_json_atomic(path, lock_data)
        return True
    except OSError:
        return False


def release_executor_lock(target_path: str) -> bool:
    path = _executor_lock_path(target_path)
    try:
        if path.is_file():
            path.unlink()
        return True
    except OSError:
        return False


def check_executor_lock(target_path: str) -> Optional[dict]:
    path = _executor_lock_path(target_path)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
