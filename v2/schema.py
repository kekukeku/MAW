"""v2 schema — all data models, enums, and constants.

This module is the single source of truth for workflow manifests,
state machines, roster definitions, artifact naming, and dispatch keys.
"""

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class WorkflowStatus(str, Enum):
    CREATED = "CREATED"
    CHAIR_CLARIFYING = "CHAIR_CLARIFYING"
    WAITING_USER_CLARIFICATION = "WAITING_USER_CLARIFICATION"
    PLANNING = "PLANNING"
    PEER_REVIEW = "PEER_REVIEW"
    CHAIR_SYNTHESIS = "CHAIR_SYNTHESIS"
    WAITING_USER_APPROVAL = "WAITING_USER_APPROVAL"
    EXECUTING = "EXECUTING"
    REVIEWING = "REVIEWING"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    COMMITTING = "COMMITTING"
    CHAIR_FINAL_CHECK = "CHAIR_FINAL_CHECK"
    COMPLETED = "COMPLETED"
    WAITING_USER_DECISION = "WAITING_USER_DECISION"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


TERMINAL_STATES = {WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED, WorkflowStatus.FAILED}
WAITING_STATES = {
    WorkflowStatus.WAITING_USER_CLARIFICATION,
    WorkflowStatus.WAITING_USER_APPROVAL,
    WorkflowStatus.WAITING_USER_DECISION,
}

# Ordered phases for sequencing
PHASES = [
    "clarify",
    "proposal",
    "comment",
    "synthesis",
    "execution",
    "review",
    "commit",
    "final_check",
]

# ---------------------------------------------------------------------------
# Decision tokens
# ---------------------------------------------------------------------------

APPROVE = "APPROVE"
REQUEST_CHANGES = "REQUEST_CHANGES"
CANCEL = "CANCEL"


# ---------------------------------------------------------------------------
# Artifact naming
# ---------------------------------------------------------------------------

ARTIFACT_REQUEST = "request.md"
ARTIFACT_QUESTIONS = "questions.md"
ARTIFACT_ANSWERS = "answers.md"
ARTIFACT_CHAIR_BRIEF = "chair_brief.md"
ARTIFACT_FINAL_PLAN = "final_plan.md"
ARTIFACT_USER_DECISION = "user_decision.md"
ARTIFACT_TASK = "task.md"
ARTIFACT_COMMIT = "commit.md"
ARTIFACT_COMPLETION = "completion.md"
ARTIFACT_EVENTS = "events.jsonl"

DIR_PROPOSALS = "proposals"
DIR_COMMENTS = "comments"
DIR_WALKTHROUGHS = "walkthroughs"
DIR_REVIEWS = "reviews"
DIR_INSTRUCTIONS = "instructions"


def proposal_path(seat: str) -> str:
    return f"{DIR_PROPOSALS}/{seat}.md"


def comment_path(reviewer_seat: str, target_seat: str) -> str:
    return f"{DIR_COMMENTS}/{reviewer_seat}_on_{target_seat}.md"


def walkthrough_path(iteration: int) -> str:
    return f"{DIR_WALKTHROUGHS}/walkthrough_{iteration:03d}.md"


def review_path(iteration: int) -> str:
    return f"{DIR_REVIEWS}/review_{iteration:03d}.md"


def instruction_path(role_or_seat: str) -> str:
    return f"{DIR_INSTRUCTIONS}/{role_or_seat}.md"


# ---------------------------------------------------------------------------
# Dispatch keys
# ---------------------------------------------------------------------------

def dispatch_key(workflow_id: str, phase: str, role_or_seat: str, iteration: int = 1) -> str:
    return f"{workflow_id}:{phase}:{role_or_seat}:{iteration}"


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

def build_roster(
    chair: str,
    planners: list[dict[str, str]],
    executor: str,
    reviewer: str,
) -> dict[str, Any]:
    return {
        "chair": chair,
        "planners": planners,  # [{"seat": "planner_a", "agent": "codex"}, ...]
        "executor": executor,
        "reviewer": reviewer,
    }


def planner_seats(roster: dict) -> list[str]:
    return [p["seat"] for p in roster.get("planners", [])]


def planner_agents(roster: dict) -> list[str]:
    return [p["agent"] for p in roster.get("planners", [])]


def planner_count(roster: dict) -> int:
    return len(roster.get("planners", []))


def all_agents(roster: dict) -> list[str]:
    agents = {roster["chair"]}
    agents.update(planner_agents(roster))
    agents.add(roster["executor"])
    agents.add(roster["reviewer"])
    return sorted(agents)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

MANIFEST_SCHEMA_VERSION = 1


def make_manifest(
    workflow_id: str,
    target_path: str,
    roster: dict,
    require_user_plan_approval: bool = True,
    max_review_iterations: int = 3,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "workflow_id": workflow_id,
        "target_path": str(Path(target_path).resolve()),
        "created_at": now,
        "status": WorkflowStatus.CREATED.value,
        "roster": roster,
        "review_iteration": 0,
        "max_review_iterations": max_review_iterations,
        "require_user_plan_approval": require_user_plan_approval,
        "last_transition_at": now,
    }


def load_manifest(workflow_dir: str) -> dict[str, Any]:
    path = Path(workflow_dir) / "manifest.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_manifest(workflow_dir: str, manifest: dict) -> None:
    path = Path(workflow_dir) / "manifest.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def set_status(manifest: dict, status: WorkflowStatus) -> None:
    manifest["status"] = status.value
    manifest["last_transition_at"] = datetime.now(timezone.utc).isoformat()


def increment_review_iteration(manifest: dict) -> int:
    manifest["review_iteration"] = manifest.get("review_iteration", 0) + 1
    manifest["last_transition_at"] = datetime.now(timezone.utc).isoformat()
    return manifest["review_iteration"]


# ---------------------------------------------------------------------------
# Expected artifact sets
# ---------------------------------------------------------------------------

def expected_proposals(roster: dict) -> list[str]:
    return [proposal_path(s) for s in planner_seats(roster)]


def expected_comments(roster: dict) -> list[str]:
    seats = planner_seats(roster)
    result = []
    for reviewer_seat in seats:
        for target_seat in seats:
            if reviewer_seat != target_seat:
                result.append(comment_path(reviewer_seat, target_seat))
    return result


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------

TRANSITIONS: dict[WorkflowStatus, list[WorkflowStatus]] = {
    WorkflowStatus.CREATED: [WorkflowStatus.CHAIR_CLARIFYING],
    WorkflowStatus.CHAIR_CLARIFYING: [
        WorkflowStatus.PLANNING,
        WorkflowStatus.WAITING_USER_CLARIFICATION,
    ],
    WorkflowStatus.WAITING_USER_CLARIFICATION: [
        WorkflowStatus.CHAIR_CLARIFYING,
    ],
    WorkflowStatus.PLANNING: [
        WorkflowStatus.PEER_REVIEW,
    ],
    WorkflowStatus.PEER_REVIEW: [
        WorkflowStatus.CHAIR_SYNTHESIS,
    ],
    WorkflowStatus.CHAIR_SYNTHESIS: [
        WorkflowStatus.WAITING_USER_APPROVAL,
    ],
    WorkflowStatus.WAITING_USER_APPROVAL: [
        WorkflowStatus.EXECUTING,
        WorkflowStatus.CHAIR_CLARIFYING,
        WorkflowStatus.CANCELLED,
    ],
    WorkflowStatus.EXECUTING: [
        WorkflowStatus.REVIEWING,
    ],
    WorkflowStatus.REVIEWING: [
        WorkflowStatus.REVISION_REQUIRED,
        WorkflowStatus.COMMITTING,
    ],
    WorkflowStatus.REVISION_REQUIRED: [
        WorkflowStatus.REVIEWING,
        WorkflowStatus.WAITING_USER_DECISION,
    ],
    WorkflowStatus.COMMITTING: [
        WorkflowStatus.CHAIR_FINAL_CHECK,
    ],
    WorkflowStatus.CHAIR_FINAL_CHECK: [
        WorkflowStatus.COMPLETED,
        WorkflowStatus.WAITING_USER_DECISION,
    ],
    WorkflowStatus.WAITING_USER_DECISION: [
        WorkflowStatus.COMPLETED,
        WorkflowStatus.CHAIR_CLARIFYING,
    ],
}

# Any non-terminal state can go to CANCELLED or FAILED
for s in list(TRANSITIONS.keys()):
    for terminal in [WorkflowStatus.CANCELLED, WorkflowStatus.FAILED]:
        if terminal not in TRANSITIONS[s]:
            TRANSITIONS[s].append(terminal)


def can_transition(current: WorkflowStatus, target: WorkflowStatus) -> bool:
    return target in TRANSITIONS.get(current, [])


def requires_transition(current: WorkflowStatus, target: WorkflowStatus) -> None:
    if not can_transition(current, target):
        raise ValueError(f"Illegal transition: {current.value} -> {target.value}")
