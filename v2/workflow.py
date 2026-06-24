"""v2 workflow — state machine, dispatch computation, artifact validation.

Pure logic: reads manifest + artifacts, determines next dispatch items.
No I/O aside from what files.py provides.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from v2.schema import (
    APPROVE,
    CANCEL,
    REQUEST_CHANGES,
    WorkflowStatus,
    TERMINAL_STATES,
    WAITING_STATES,
    requires_transition,
    set_status,
    increment_review_iteration,
    planner_seats,
    planner_count,
    all_agents,
    expected_proposals,
    expected_comments,
    ARTIFACT_REQUEST,
    ARTIFACT_QUESTIONS,
    ARTIFACT_ANSWERS,
    ARTIFACT_CHAIR_BRIEF,
    ARTIFACT_FINAL_PLAN,
    ARTIFACT_USER_DECISION,
    ARTIFACT_TASK,
    ARTIFACT_COMMIT,
    ARTIFACT_COMPLETION,
    DIR_PROPOSALS,
    DIR_COMMENTS,
    DIR_WALKTHROUGHS,
    DIR_REVIEWS,
    proposal_path,
    comment_path,
    walkthrough_path,
    review_path,
    instruction_path,
)
from v2.files import (
    exists_nonempty,
    read_file,
    read_file_optional,
    write_atomic,
    write_json_atomic,
    append_event,
    proposal_exists,
    all_proposals_exist,
    comment_exists,
    all_comments_exist,
    walkthrough_exists,
    review_exists,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Dispatch items
# ---------------------------------------------------------------------------

class DispatchItem:
    def __init__(
        self,
        key: str,
        role: str,
        seat: str,
        agent: str,
        phase: str,
        iteration: int,
    ):
        self.key = key
        self.role = role
        self.seat = seat
        self.agent = agent
        self.phase = phase
        self.iteration = iteration

    def __repr__(self) -> str:
        return f"DispatchItem({self.key}, agent={self.agent}, role={self.role})"


# ---------------------------------------------------------------------------
# Compute next dispatch items
# ---------------------------------------------------------------------------

def compute_dispatch(wf_dir: Path, manifest: dict) -> list[DispatchItem]:
    status = WorkflowStatus(manifest["status"])
    wf_id = manifest["workflow_id"]
    roster = manifest["roster"]
    seats = planner_seats(roster)
    n_planners = len(seats)
    max_reviews = manifest.get("max_review_iterations", 3)
    review_iter = manifest.get("review_iteration", 0)
    dispatch_list: list[DispatchItem] = []

    if status in TERMINAL_STATES:
        return []

    # ── CREATED → CHAIR_CLARIFYING ──
    if status == WorkflowStatus.CREATED:
        dispatch_list.append(DispatchItem(
            key=f"{wf_id}:clarify:chair:1",
            role="chair",
            seat="chair",
            agent=roster["chair"],
            phase="clarify",
            iteration=1,
        ))

    # ── CHAIR_CLARIFYING ──
    elif status == WorkflowStatus.CHAIR_CLARIFYING:
        if not exists_nonempty(wf_dir / ARTIFACT_CHAIR_BRIEF) and not exists_nonempty(wf_dir / ARTIFACT_QUESTIONS):
            # Chair hasn't produced anything yet — dispatch
            dispatch_list.append(DispatchItem(
                key=f"{wf_id}:clarify:chair:1",
                role="chair",
                seat="chair",
                agent=roster["chair"],
                phase="clarify",
                iteration=1,
            ))
        elif exists_nonempty(wf_dir / ARTIFACT_QUESTIONS) and not exists_nonempty(wf_dir / ARTIFACT_ANSWERS):
            # Waiting for user answers
            pass
        elif exists_nonempty(wf_dir / ARTIFACT_ANSWERS):
            # User answered — dispatch chair again to produce brief
            dispatch_list.append(DispatchItem(
                key=f"{wf_id}:clarify:chair:2",
                role="chair",
                seat="chair",
                agent=roster["chair"],
                phase="clarify",
                iteration=2,
            ))

    # ── PLANNING ──
    elif status == WorkflowStatus.PLANNING:
        for p in roster["planners"]:
            seat = p["seat"]
            if not proposal_exists(wf_dir, seat):
                dispatch_list.append(DispatchItem(
                    key=f"{wf_id}:proposal:{seat}:1",
                    role="planner",
                    seat=seat,
                    agent=p["agent"],
                    phase="proposal",
                    iteration=1,
                ))

    # ── PEER_REVIEW ──
    elif status == WorkflowStatus.PEER_REVIEW:
        for reviewer in roster["planners"]:
            r_seat = reviewer["seat"]
            for target in roster["planners"]:
                t_seat = target["seat"]
                if r_seat == t_seat:
                    continue
                if not comment_exists(wf_dir, r_seat, t_seat):
                    dispatch_list.append(DispatchItem(
                        key=f"{wf_id}:comment:{r_seat}_on_{t_seat}:1",
                        role="planner",
                        seat=r_seat,
                        agent=reviewer["agent"],
                        phase="comment",
                        iteration=1,
                    ))

    # ── CHAIR_SYNTHESIS ──
    elif status == WorkflowStatus.CHAIR_SYNTHESIS:
        if not exists_nonempty(wf_dir / ARTIFACT_FINAL_PLAN):
            dispatch_list.append(DispatchItem(
                key=f"{wf_id}:synthesis:chair:1",
                role="chair",
                seat="chair",
                agent=roster["chair"],
                phase="synthesis",
                iteration=1,
            ))

    # ── EXECUTING ──
    elif status == WorkflowStatus.EXECUTING:
        iteration = review_iter + 1
        if not walkthrough_exists(wf_dir, iteration):
            dispatch_list.append(DispatchItem(
                key=f"{wf_id}:execution:executor:{iteration}",
                role="executor",
                seat="executor",
                agent=roster["executor"],
                phase="execution",
                iteration=iteration,
            ))

    # ── REVIEWING ──
    elif status == WorkflowStatus.REVIEWING:
        iteration = review_iter + 1
        if not review_exists(wf_dir, iteration):
            dispatch_list.append(DispatchItem(
                key=f"{wf_id}:review:reviewer:{iteration}",
                role="reviewer",
                seat="reviewer",
                agent=roster["reviewer"],
                phase="review",
                iteration=iteration,
            ))

    # ── REVISION_REQUIRED ──
    elif status == WorkflowStatus.REVISION_REQUIRED:
        iteration = review_iter + 1
        if review_iter >= max_reviews:
            # No more iterations, transition to WAITING_USER_DECISION
            dispatch_list.append(DispatchItem(
                key=f"{wf_id}:max_reviews:system:1",
                role="system",
                seat="system",
                agent="maw",
                phase="max_reviews",
                iteration=1,
            ))
        elif not walkthrough_exists(wf_dir, iteration):
            dispatch_list.append(DispatchItem(
                key=f"{wf_id}:execution:executor:{iteration}",
                role="executor",
                seat="executor",
                agent=roster["executor"],
                phase="execution",
                iteration=iteration,
            ))

    # ── COMMITTING ──
    elif status == WorkflowStatus.COMMITTING:
        if not exists_nonempty(wf_dir / ARTIFACT_COMMIT):
            dispatch_list.append(DispatchItem(
                key=f"{wf_id}:commit:executor:1",
                role="executor",
                seat="executor",
                agent=roster["executor"],
                phase="commit",
                iteration=1,
            ))

    # ── CHAIR_FINAL_CHECK ──
    elif status == WorkflowStatus.CHAIR_FINAL_CHECK:
        if not exists_nonempty(wf_dir / ARTIFACT_COMPLETION):
            dispatch_list.append(DispatchItem(
                key=f"{wf_id}:final_check:chair:1",
                role="chair",
                seat="chair",
                agent=roster["chair"],
                phase="final_check",
                iteration=1,
            ))

    return dispatch_list


# ---------------------------------------------------------------------------
# Transition logic — called after artifact detection
# ---------------------------------------------------------------------------

def try_transition(wf_dir: Path, manifest: dict) -> bool:
    """Check if current phase is complete and transition to next. Returns True if changed."""
    status = WorkflowStatus(manifest["status"])
    roster = manifest["roster"]
    changed = False

    # CREATED -> CHAIR_CLARIFYING (handled by watcher via dispatch)
    # (no automatic transition; watcher handles it)

    # CREATED -> CHAIR_CLARIFYING (auto transition — workflow has been initialized)
    if status == WorkflowStatus.CREATED:
        requires_transition(status, WorkflowStatus.CHAIR_CLARIFYING)
        set_status(manifest, WorkflowStatus.CHAIR_CLARIFYING)
        _log_transition(wf_dir, manifest, "CREATED", "CHAIR_CLARIFYING", "workflow initialized")
        changed = True

    # CHAIR_CLARIFYING -> PLANNING or WAITING_USER_CLARIFICATION
    elif status == WorkflowStatus.CHAIR_CLARIFYING:
        if exists_nonempty(wf_dir / ARTIFACT_CHAIR_BRIEF):
            requires_transition(status, WorkflowStatus.PLANNING)
            set_status(manifest, WorkflowStatus.PLANNING)
            _log_transition(wf_dir, manifest, "CHAIR_CLARIFYING", "PLANNING", "chair_brief ready")
            changed = True
        elif exists_nonempty(wf_dir / ARTIFACT_QUESTIONS) and not exists_nonempty(wf_dir / ARTIFACT_ANSWERS):
            requires_transition(status, WorkflowStatus.WAITING_USER_CLARIFICATION)
            set_status(manifest, WorkflowStatus.WAITING_USER_CLARIFICATION)
            _log_transition(wf_dir, manifest, "CHAIR_CLARIFYING", "WAITING_USER_CLARIFICATION", "questions pending")
            changed = True

    # WAITING_USER_CLARIFICATION -> CHAIR_CLARIFYING (answers exist)
    elif status == WorkflowStatus.WAITING_USER_CLARIFICATION:
        if exists_nonempty(wf_dir / ARTIFACT_ANSWERS):
            requires_transition(status, WorkflowStatus.CHAIR_CLARIFYING)
            set_status(manifest, WorkflowStatus.CHAIR_CLARIFYING)
            _log_transition(wf_dir, manifest, "WAITING_USER_CLARIFICATION", "CHAIR_CLARIFYING", "user answered")
            changed = True

    # PLANNING -> PEER_REVIEW (all proposals exist)
    elif status == WorkflowStatus.PLANNING:
        seats = planner_seats(roster)
        if all_proposals_exist(wf_dir, seats):
            requires_transition(status, WorkflowStatus.PEER_REVIEW)
            set_status(manifest, WorkflowStatus.PEER_REVIEW)
            _log_transition(wf_dir, manifest, "PLANNING", "PEER_REVIEW", "all proposals ready")
            changed = True

    # PEER_REVIEW -> CHAIR_SYNTHESIS (all comments exist)
    elif status == WorkflowStatus.PEER_REVIEW:
        seats = planner_seats(roster)
        if all_comments_exist(wf_dir, seats):
            requires_transition(status, WorkflowStatus.CHAIR_SYNTHESIS)
            set_status(manifest, WorkflowStatus.CHAIR_SYNTHESIS)
            _log_transition(wf_dir, manifest, "PEER_REVIEW", "CHAIR_SYNTHESIS", "all comments ready")
            changed = True

    # CHAIR_SYNTHESIS -> WAITING_USER_APPROVAL (final_plan exists)
    elif status == WorkflowStatus.CHAIR_SYNTHESIS:
        if exists_nonempty(wf_dir / ARTIFACT_FINAL_PLAN):
            requires_transition(status, WorkflowStatus.WAITING_USER_APPROVAL)
            set_status(manifest, WorkflowStatus.WAITING_USER_APPROVAL)
            _log_transition(wf_dir, manifest, "CHAIR_SYNTHESIS", "WAITING_USER_APPROVAL", "final_plan ready")
            changed = True

    # WAITING_USER_APPROVAL -> EXECUTING (decision = APPROVE)
    elif status == WorkflowStatus.WAITING_USER_APPROVAL:
        decision = _read_decision(wf_dir)
        if decision == APPROVE:
            requires_transition(status, WorkflowStatus.EXECUTING)
            set_status(manifest, WorkflowStatus.EXECUTING)
            _log_transition(wf_dir, manifest, "WAITING_USER_APPROVAL", "EXECUTING", "user approved")
            changed = True
        elif decision == REQUEST_CHANGES:
            requires_transition(status, WorkflowStatus.CHAIR_CLARIFYING)
            set_status(manifest, WorkflowStatus.CHAIR_CLARIFYING)
            _log_transition(wf_dir, manifest, "WAITING_USER_APPROVAL", "CHAIR_CLARIFYING", "user requested changes")
            changed = True
        elif decision == CANCEL:
            requires_transition(status, WorkflowStatus.CANCELLED)
            set_status(manifest, WorkflowStatus.CANCELLED)
            _log_transition(wf_dir, manifest, "WAITING_USER_APPROVAL", "CANCELLED", "user cancelled")
            changed = True

    # EXECUTING -> REVIEWING (walkthrough exists)
    elif status == WorkflowStatus.EXECUTING:
        iteration = manifest.get("review_iteration", 0) + 1
        if walkthrough_exists(wf_dir, iteration):
            requires_transition(status, WorkflowStatus.REVIEWING)
            set_status(manifest, WorkflowStatus.REVIEWING)
            _log_transition(wf_dir, manifest, "EXECUTING", "REVIEWING", f"walkthrough {iteration} ready")
            changed = True

    # REVIEWING -> REVISION_REQUIRED or COMMITTING
    elif status == WorkflowStatus.REVIEWING:
        iteration = manifest.get("review_iteration", 0) + 1
        if review_exists(wf_dir, iteration):
            decision = _parse_review_decision(wf_dir, iteration)
            if decision == REQUEST_CHANGES:
                increment_review_iteration(manifest)
                requires_transition(status, WorkflowStatus.REVISION_REQUIRED)
                set_status(manifest, WorkflowStatus.REVISION_REQUIRED)
                _log_transition(wf_dir, manifest, "REVIEWING", "REVISION_REQUIRED", f"review {iteration}: changes requested")
                changed = True
            elif decision == "APPROVE" or decision == APPROVE:
                if manifest.get("review_iteration", 0) == 0:
                    increment_review_iteration(manifest)
                requires_transition(status, WorkflowStatus.COMMITTING)
                set_status(manifest, WorkflowStatus.COMMITTING)
                _log_transition(wf_dir, manifest, "REVIEWING", "COMMITTING", f"review {iteration}: approved")
                changed = True

    # REVISION_REQUIRED -> REVIEWING (new walkthrough exists)
    elif status == WorkflowStatus.REVISION_REQUIRED:
        iteration = manifest.get("review_iteration", 0)
        max_reviews = manifest.get("max_review_iterations", 3)
        if iteration > max_reviews:
            requires_transition(status, WorkflowStatus.WAITING_USER_DECISION)
            set_status(manifest, WorkflowStatus.WAITING_USER_DECISION)
            _log_transition(wf_dir, manifest, "REVISION_REQUIRED", "WAITING_USER_DECISION", "max review iterations exceeded")
            changed = True
        elif walkthrough_exists(wf_dir, iteration):
            requires_transition(status, WorkflowStatus.REVIEWING)
            set_status(manifest, WorkflowStatus.REVIEWING)
            _log_transition(wf_dir, manifest, "REVISION_REQUIRED", "REVIEWING", f"walkthrough {iteration} ready after revision")
            changed = True

    # COMMITTING -> CHAIR_FINAL_CHECK (commit.md exists)
    elif status == WorkflowStatus.COMMITTING:
        if exists_nonempty(wf_dir / ARTIFACT_COMMIT):
            requires_transition(status, WorkflowStatus.CHAIR_FINAL_CHECK)
            set_status(manifest, WorkflowStatus.CHAIR_FINAL_CHECK)
            _log_transition(wf_dir, manifest, "COMMITTING", "CHAIR_FINAL_CHECK", "commit record ready")
            changed = True

    # CHAIR_FINAL_CHECK -> COMPLETED or WAITING_USER_DECISION
    elif status == WorkflowStatus.CHAIR_FINAL_CHECK:
        if exists_nonempty(wf_dir / ARTIFACT_COMPLETION):
            content = read_file(wf_dir / ARTIFACT_COMPLETION)
            if "⚠" in content or "ISSUE:" in content or "DECISION_NEEDED:" in content:
                requires_transition(status, WorkflowStatus.WAITING_USER_DECISION)
                set_status(manifest, WorkflowStatus.WAITING_USER_DECISION)
                _log_transition(wf_dir, manifest, "CHAIR_FINAL_CHECK", "WAITING_USER_DECISION", "issues found")
            else:
                requires_transition(status, WorkflowStatus.COMPLETED)
                set_status(manifest, WorkflowStatus.COMPLETED)
                _log_transition(wf_dir, manifest, "CHAIR_FINAL_CHECK", "COMPLETED", "all clear")
            changed = True

    if changed:
        write_json_atomic(wf_dir / "manifest.json", manifest)

    return changed


# ---------------------------------------------------------------------------
# User actions (from UI/CLI)
# ---------------------------------------------------------------------------

def user_answer(wf_dir: Path, manifest: dict, answer: str) -> bool:
    if WorkflowStatus(manifest["status"]) != WorkflowStatus.WAITING_USER_CLARIFICATION:
        return False
    write_atomic(wf_dir / ARTIFACT_ANSWERS, answer)
    append_event(wf_dir, {"ts": _now(), "type": "user.answered", "actor": "user"})
    return True


def user_decision(wf_dir: Path, manifest: dict, decision: str) -> bool:
    status = WorkflowStatus(manifest["status"])
    if status not in (WorkflowStatus.WAITING_USER_APPROVAL, WorkflowStatus.WAITING_USER_DECISION):
        return False
    valid = {APPROVE, REQUEST_CHANGES, CANCEL}
    if decision.upper() not in valid:
        return False
    write_atomic(wf_dir / ARTIFACT_USER_DECISION, decision.upper())
    append_event(wf_dir, {"ts": _now(), "type": "user.decision", "actor": "user", "decision": decision.upper()})
    return True


def user_cancel(wf_dir: Path, manifest: dict) -> bool:
    if WorkflowStatus(manifest["status"]) in TERMINAL_STATES:
        return False
    prev_status = manifest["status"]
    requires_transition(WorkflowStatus(prev_status), WorkflowStatus.CANCELLED)
    set_status(manifest, WorkflowStatus.CANCELLED)
    write_json_atomic(wf_dir / "manifest.json", manifest)
    _log_transition(wf_dir, manifest, prev_status, "CANCELLED", "user cancelled")
    return True


def mark_failed(wf_dir: Path, manifest: dict, reason: str) -> bool:
    if WorkflowStatus(manifest["status"]) in TERMINAL_STATES:
        return False
    prev_status = manifest["status"]
    requires_transition(WorkflowStatus(prev_status), WorkflowStatus.FAILED)
    set_status(manifest, WorkflowStatus.FAILED)
    write_json_atomic(wf_dir / "manifest.json", manifest)
    _log_transition(wf_dir, manifest, prev_status, "FAILED", reason)
    return True


# ---------------------------------------------------------------------------
# Instruction generation
# ---------------------------------------------------------------------------

def generate_instruction(wf_dir: Path, manifest: dict, item: DispatchItem) -> str:
    """Generate a concise role-specific instruction file for the agent."""
    wf_id = manifest["workflow_id"]
    target = manifest["target_path"]
    roster = manifest["roster"]

    lines = [
        f"# Instruction for {item.role.upper()}: {item.seat}",
        "",
        f"**Workflow**: {wf_id}",
        f"**Target Project**: {target}",
        f"**Phase**: {item.phase} (iteration {item.iteration})",
        "",
    ]

    if item.role == "chair" and item.phase == "clarify":
        lines += [
            "## Your Task",
            "You are the **Chair**. Read the user request and the target project,",
            "then decide whether the request is clear enough for planners.",
            "",
            "## Required Reading",
            f"- `request.md` (in this workflow directory)",
            f"- `AGENTS.md` in target project",
            f"- `TEAM_RULES.md` in target project (if exists)",
            f"- The target project at `{target}`",
            "",
            "## Output",
            "Write ONE of the following:",
            f"1. `{ARTIFACT_CHAIR_BRIEF}` — if the request is clear. Include:",
            "   - User's real goal",
            "   - Known constraints",
            "   - Non-goals",
            "   - Questions planners must investigate",
            "   - What the final plan should answer",
            f"2. `{ARTIFACT_QUESTIONS}` — if important ambiguities remain.",
            "   Only ask questions that would change the approach.",
            "",
            "## Rules",
            "- Do not edit any workflow files other than your output.",
            "- Write to `.tmp` then rename to the final filename.",
            "- Do not skip the user approval gate.",
            "- Do not proceed to execution.",
        ]

    elif item.role == "planner" and item.phase == "proposal":
        lines += [
            "## Your Task",
            "You are a **Planner**. Read the chair's brief and the target project,",
            "then produce an independent proposal.",
            "",
            "## Required Reading",
            f"- `{ARTIFACT_CHAIR_BRIEF}`",
            f"- `{ARTIFACT_REQUEST}`",
            f"- `AGENTS.md` and `TEAM_RULES.md` in target project",
            f"- The target project at `{target}`",
            "",
            "## Do NOT Read",
            "- Other planners' proposal files (even if they exist)",
            "",
            "## Output",
            f"Write `{proposal_path(item.seat)}` containing:",
            "1. Your understanding of the situation",
            "2. Proposed approach",
            "3. Expected scope (what to modify/add/delete)",
            "4. Implementation order",
            "5. Risks",
            "6. Verification method",
            "7. Items still requiring Chair decision",
            "",
            "## Rules",
            "- Write to `.tmp` then rename.",
            "- Be specific and actionable.",
            "- Do not modify code during planning.",
        ]

    elif item.role == "planner" and item.phase == "comment":
        r_seat = item.seat
        parts = item.seat.split("_on_")
        if len(parts) == 2:
            r_seat, t_seat = parts[0], parts[1]
        else:
            t_seat = "unknown"
        lines += [
            "## Your Task",
            "You are a **Planner** reviewing another planner's proposal.",
            f"The planner ({r_seat}) reviews proposal by ({t_seat}).",
            "",
            "## Required Reading",
            f"- `{proposal_path(t_seat)}`",
            f"- `{ARTIFACT_CHAIR_BRIEF}`",
            "",
            "## Output",
            f"Write `{comment_path(r_seat, t_seat)}` covering:",
            "- What you agree with",
            "- What is missing",
            "- What is unreasonable or too risky",
            "- Specific suggestions to incorporate into the final plan",
            "",
            "## Rules",
            "- Be constructive, not competitive.",
            "- No scoring or ranking needed.",
        ]

    elif item.role == "chair" and item.phase == "synthesis":
        seats = planner_seats(roster)
        proposal_refs = "\n".join(f"- `{proposal_path(s)}`" for s in seats)
        comment_refs = "\n".join(
            f"- `{comment_path(rs, ts)}`"
            for rs in seats for ts in seats if rs != ts
        )
        lines += [
            "## Your Task",
            "You are the **Chair**. Synthesize all proposals and comments into a final plan.",
            "",
            "## Required Reading",
            f"- `{ARTIFACT_REQUEST}`",
            f"- `{ARTIFACT_CHAIR_BRIEF}`",
            f"- All proposals:\n{proposal_refs}",
            f"- All comments:\n{comment_refs}",
            "",
            "## Output",
            f"Write `{ARTIFACT_FINAL_PLAN}` containing:",
            "1. Goal and definition of done",
            "2. What to keep",
            "3. What to delete",
            "4. What to add or modify",
            "5. File-level change list",
            "6. Execution order",
            "7. Data or config migration",
            "8. Safety constraints",
            "9. Test and verification commands",
            "10. Rollback boundary",
            "11. Explicit non-goals",
            "",
            "## Rules",
            "- The executor must be able to follow this plan directly.",
            "- If new ambiguities emerge, produce `questions.md` instead.",
        ]

    elif item.role == "executor" and item.phase == "execution":
        prev_review = ""
        if item.iteration > 1:
            prev_review = f"- Previous review: `{review_path(item.iteration - 1)}`"
        lines += [
            "## Your Task",
            "You are the **Executor**. Implement the approved plan.",
            "",
            "## Required Reading",
            f"- `{ARTIFACT_FINAL_PLAN}`",
            f"- `AGENTS.md` and `TEAM_RULES.md` in target project",
            f"- The target project at `{target}`",
            f"{prev_review}",
            "",
            "## Output",
            f"Write `{walkthrough_path(item.iteration)}` containing:",
            "1. What was actually changed",
            "2. Differences from final_plan and why",
            "3. What was deleted",
            "4. Test commands and results",
            "5. Unresolved issues",
            "6. Current branch and commit status",
            "",
            "## Rules",
            "- Work on a safe branch.",
            "- Run tests specified in final_plan.",
            "- Do NOT commit unless reviewer has APPROVED.",
            "- Do NOT skip the review gate.",
        ]

    elif item.role == "reviewer" and item.phase == "review":
        lines += [
            "## Your Task",
            "You are the **Reviewer**. Inspect the executor's work.",
            "",
            "## Required Reading",
            f"- `{ARTIFACT_FINAL_PLAN}`",
            f"- Latest walkthrough: `{walkthrough_path(item.iteration)}`",
            f"- Actual git diff",
            f"- Test results",
        ]
        if item.iteration > 1:
            lines.append(f"- Previous review: `{review_path(item.iteration - 1)}`")
        lines += [
            "",
            "## Output",
            f"Write `{review_path(item.iteration)}` containing:",
            "```",
            "DECISION: APPROVE",
            "```",
            "or",
            "```",
            "DECISION: REQUEST_CHANGES",
            "```",
            "",
            "If REQUEST_CHANGES, list actionable, verifiable fixes.",
            "",
            "## Rules",
            "- DECISION: must be on its own line.",
            "- Do not commit or push.",
            "- Read-only except for the review file.",
        ]

    elif item.role == "executor" and item.phase == "commit":
        lines += [
            "## Your Task",
            "You are the **Executor**. The reviewer has APPROVED. Create the commit.",
            "",
            "## Required Reading",
            f"- `{ARTIFACT_FINAL_PLAN}`",
            f"- Latest walkthrough: `{walkthrough_path(item.iteration)}`",
            f"- Latest review: `{review_path(item.iteration)}`",
            "",
            "## Output",
            f"Write `{ARTIFACT_COMMIT}` containing:",
            "- Branch name",
            "- Commit SHA",
            "- Test summary",
            "- Change summary",
            "",
            "## Rules",
            "- Verify working tree is clean before committing.",
            "- Follow project git conventions.",
            "- Decide whether to push/PR based on project rules.",
        ]

    elif item.role == "chair" and item.phase == "final_check":
        lines += [
            "## Your Task",
            "You are the **Chair**. Perform the final inspection.",
            "",
            "## Required Reading",
            f"- `{ARTIFACT_FINAL_PLAN}`",
            f"- All walkthroughs in `{DIR_WALKTHROUGHS}/`",
            f"- All reviews in `{DIR_REVIEWS}/`",
            f"- `{ARTIFACT_COMMIT}`",
            f"- Final diff and test results",
            "",
            "## Output",
            f"Write `{ARTIFACT_COMPLETION}`. Start with **COMPLETED** if all good.",
            "If minor issues remain, start with **DECISION_NEEDED:** and list them.",
            "If major issues exist, do NOT produce completion; request intervention.",
            "",
            "## Rules",
            "- Do not pretend everything is fine if it isn't.",
            "- Mark issues that need user attention clearly.",
        ]

    instruction = "\n".join(lines)
    return instruction


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_decision(wf_dir: Path) -> str:
    path = wf_dir / ARTIFACT_USER_DECISION
    if not exists_nonempty(path):
        return ""
    text = read_file(path).strip().upper()
    if text in {APPROVE, REQUEST_CHANGES, CANCEL}:
        return text
    return ""


def _parse_review_decision(wf_dir: Path, iteration: int) -> str:
    path = wf_dir / review_path(iteration)
    if not exists_nonempty(path):
        return ""
    text = read_file(path)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("DECISION:"):
            token = stripped.split(":", 1)[1].strip().upper()
            if token in ("APPROVE", "REQUEST_CHANGES"):
                return token
    return ""


def _log_transition(wf_dir: Path, manifest: dict, from_st: str, to_st: str, reason: str) -> None:
    append_event(wf_dir, {
        "ts": _now(),
        "type": "state.changed",
        "from": from_st,
        "to": to_st,
        "reason": reason,
        "actor": "watcher",
    })
