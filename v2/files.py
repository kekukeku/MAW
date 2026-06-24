"""v2 files — path resolution, atomic writes, artifact discovery."""

import json
import os
import shutil
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

    return wf_root


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
