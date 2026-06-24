"""v2 workflow tests — state transitions, dispatch computation, user actions."""

import os
import tempfile
import unittest
from pathlib import Path

from v2.schema import (
    WorkflowStatus,
    build_roster,
    make_manifest,
    set_status,
    increment_review_iteration,
    load_manifest,
    save_manifest,
    proposal_path,
    comment_path,
    walkthrough_path,
    review_path,
    ARTIFACT_REQUEST,
    ARTIFACT_QUESTIONS,
    ARTIFACT_ANSWERS,
    ARTIFACT_CHAIR_BRIEF,
    ARTIFACT_FINAL_PLAN,
    ARTIFACT_USER_DECISION,
    ARTIFACT_COMMIT,
    ARTIFACT_COMPLETION,
    APPROVE,
    REQUEST_CHANGES,
    REJECT,
    CANCEL,
)
from v2.files import (
    init_workflow,
    write_atomic,
    write_json_atomic,
    append_event,
    ensure_workflow_dirs,
    exists_nonempty,
    scaffold_target,
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
)


class TestWorkflowTransitions(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.target = self.tmpdir
        scaffold_target(self.target)
        self.roster = build_roster(
            chair="codex",
            planners=[{"seat": "planner_a", "agent": "codex"}],
            executor="codex",
            reviewer="codex",
        )
        self.manifest = make_manifest("wf_test", self.target, self.roster)
        self.wf_dir = init_workflow(self.target, "wf_test", self.manifest, "Test request")
        ensure_workflow_dirs(self.wf_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _reload(self):
        return load_manifest(str(self.wf_dir))

    def _transition_and_reload(self):
        changed = try_transition(self.wf_dir, self.manifest)
        self.manifest = self._reload()
        return changed

    # ── CREATED → CHAIR_CLARIFYING ──

    def test_created_dispatches_chair(self):
        items = compute_dispatch(self.wf_dir, self.manifest)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].role, "chair")
        self.assertEqual(items[0].phase, "clarify")

    # ── CHAIR_CLARIFYING → PLANNING ──

    def test_chair_brief_triggers_planning(self):
        set_status(self.manifest, WorkflowStatus.CHAIR_CLARIFYING)
        save_manifest(str(self.wf_dir), self.manifest)
        write_atomic(self.wf_dir / ARTIFACT_CHAIR_BRIEF, "Brief content")
        self.assertTrue(self._transition_and_reload())
        self.assertEqual(self.manifest["status"], "PLANNING")

    # ── CHAIR_CLARIFYING → WAITING_USER_CLARIFICATION ──

    def test_questions_triggers_waiting(self):
        set_status(self.manifest, WorkflowStatus.CHAIR_CLARIFYING)
        save_manifest(str(self.wf_dir), self.manifest)
        write_atomic(self.wf_dir / ARTIFACT_QUESTIONS, "Q1: What is this?")
        self.assertTrue(self._transition_and_reload())
        self.assertEqual(self.manifest["status"], "WAITING_USER_CLARIFICATION")

    def test_answers_triggers_back_to_clarify(self):
        set_status(self.manifest, WorkflowStatus.WAITING_USER_CLARIFICATION)
        save_manifest(str(self.wf_dir), self.manifest)
        write_atomic(self.wf_dir / ARTIFACT_ANSWERS, "A1: This is it.")
        self.assertTrue(self._transition_and_reload())
        self.assertEqual(self.manifest["status"], "CHAIR_CLARIFYING")

    # ── PLANNING → PEER_REVIEW ──

    def test_all_proposals_triggers_peer_review(self):
        set_status(self.manifest, WorkflowStatus.PLANNING)
        save_manifest(str(self.wf_dir), self.manifest)
        for p in self.roster["planners"]:
            write_atomic(self.wf_dir / proposal_path(p["seat"]), f"Proposal by {p['seat']}")
        self.assertTrue(self._transition_and_reload())
        self.assertEqual(self.manifest["status"], "PEER_REVIEW")

    def test_missing_proposal_no_transition(self):
        set_status(self.manifest, WorkflowStatus.PLANNING)
        save_manifest(str(self.wf_dir), self.manifest)
        # Only write one of two proposals
        roster2 = build_roster(
            "c",
            [{"seat": "planner_a", "agent": "x"}, {"seat": "planner_b", "agent": "y"}],
            "e", "r",
        )
        m2 = make_manifest("wf_2p", self.target, roster2)
        wf2 = init_workflow(self.target, "wf_2p", m2, "req")
        set_status(m2, WorkflowStatus.PLANNING)
        save_manifest(str(wf2), m2)
        write_atomic(wf2 / proposal_path("planner_a"), "Proposal A")
        self.assertFalse(try_transition(wf2, m2))

    # ── CHAIR_SYNTHESIS ──

    def test_final_plan_triggers_waiting_approval(self):
        set_status(self.manifest, WorkflowStatus.CHAIR_SYNTHESIS)
        save_manifest(str(self.wf_dir), self.manifest)
        write_atomic(self.wf_dir / ARTIFACT_FINAL_PLAN, "Final plan content")
        self.assertTrue(self._transition_and_reload())
        self.assertEqual(self.manifest["status"], "WAITING_USER_APPROVAL")

    # ── USER APPROVAL ──

    def test_approve_triggers_executing(self):
        set_status(self.manifest, WorkflowStatus.WAITING_USER_APPROVAL)
        save_manifest(str(self.wf_dir), self.manifest)
        write_atomic(self.wf_dir / ARTIFACT_USER_DECISION, "APPROVE")
        self.assertTrue(self._transition_and_reload())
        self.assertEqual(self.manifest["status"], "EXECUTING")

    def test_request_changes_triggers_chair(self):
        set_status(self.manifest, WorkflowStatus.WAITING_USER_APPROVAL)
        save_manifest(str(self.wf_dir), self.manifest)
        write_atomic(self.wf_dir / ARTIFACT_USER_DECISION, "REQUEST_CHANGES")
        self.assertTrue(self._transition_and_reload())
        self.assertEqual(self.manifest["status"], "CHAIR_CLARIFYING")

    def test_cancel_triggers_cancelled(self):
        set_status(self.manifest, WorkflowStatus.WAITING_USER_APPROVAL)
        save_manifest(str(self.wf_dir), self.manifest)
        write_atomic(self.wf_dir / ARTIFACT_USER_DECISION, "CANCEL")
        self.assertTrue(self._transition_and_reload())
        self.assertEqual(self.manifest["status"], "CANCELLED")

    # ── EXECUTING → REVIEWING ──

    def test_walkthrough_triggers_reviewing(self):
        set_status(self.manifest, WorkflowStatus.EXECUTING)
        save_manifest(str(self.wf_dir), self.manifest)
        write_atomic(self.wf_dir / walkthrough_path(1), "# Walkthrough 1")
        self.assertTrue(self._transition_and_reload())
        self.assertEqual(self.manifest["status"], "REVIEWING")

    # ── REVIEWING → COMMITTING ──

    def test_review_approve_triggers_committing(self):
        set_status(self.manifest, WorkflowStatus.REVIEWING)
        save_manifest(str(self.wf_dir), self.manifest)
        write_atomic(self.wf_dir / review_path(1), "DECISION: APPROVE\n\nLooks good.")
        self.assertTrue(self._transition_and_reload())
        self.assertEqual(self.manifest["status"], "COMMITTING")

    def test_review_request_changes_triggers_revision(self):
        set_status(self.manifest, WorkflowStatus.REVIEWING)
        save_manifest(str(self.wf_dir), self.manifest)
        write_atomic(self.wf_dir / review_path(1), "DECISION: REQUEST_CHANGES\n\nFix the bug.")
        self.assertTrue(self._transition_and_reload())
        self.assertEqual(self.manifest["status"], "REVISION_REQUIRED")

    def test_review_reject_triggers_user_decision(self):
        set_status(self.manifest, WorkflowStatus.REVIEWING)
        save_manifest(str(self.wf_dir), self.manifest)
        write_atomic(self.wf_dir / review_path(1), "DECISION: REJECT\n\nCannot proceed as planned.")
        self.assertTrue(self._transition_and_reload())
        self.assertEqual(self.manifest["status"], "WAITING_USER_DECISION")

    # ── REVISION → REVIEWING ──

    def test_revision_new_walkthrough_triggers_reviewing(self):
        self.manifest = make_manifest(
            "wf_rev", self.target, self.roster, max_review_iterations=3
        )
        self.wf_dir = init_workflow(self.target, "wf_rev", self.manifest, "req")
        ensure_workflow_dirs(self.wf_dir)
        set_status(self.manifest, WorkflowStatus.REVISION_REQUIRED)
        increment_review_iteration(self.manifest)  # review_iteration = 1
        # Next walkthrough is iteration + 1 = 2
        write_atomic(self.wf_dir / walkthrough_path(2), "# Fixed walkthrough v2")
        save_manifest(str(self.wf_dir), self.manifest)
        self.assertTrue(self._transition_and_reload())
        self.assertEqual(self.manifest["status"], "REVIEWING")

    def test_revision_max_iterations_goes_to_user_decision(self):
        self.manifest = make_manifest(
            "wf_max", self.target, self.roster, max_review_iterations=1
        )
        self.wf_dir = init_workflow(self.target, "wf_max", self.manifest, "req")
        ensure_workflow_dirs(self.wf_dir)
        set_status(self.manifest, WorkflowStatus.REVISION_REQUIRED)
        self.manifest["review_iteration"] = 1  # iteration == max (boundary)
        save_manifest(str(self.wf_dir), self.manifest)
        self._transition_and_reload()
        self.assertEqual(self.manifest["status"], "WAITING_USER_DECISION")

    def test_revision_does_not_trigger_on_wrong_walkthrough(self):
        """walkthrough_001 must not trigger REVISION_REQUIRED -> REVIEWING when review_iteration=1."""
        self.manifest = make_manifest(
            "wf_rev2", self.target, self.roster, max_review_iterations=3
        )
        self.wf_dir = init_workflow(self.target, "wf_rev2", self.manifest, "req")
        ensure_workflow_dirs(self.wf_dir)
        set_status(self.manifest, WorkflowStatus.REVISION_REQUIRED)
        self.manifest["review_iteration"] = 1  # expect walkthrough_002
        save_manifest(str(self.wf_dir), self.manifest)
        write_atomic(self.wf_dir / walkthrough_path(1), "# Old walkthrough")
        self.assertFalse(self._transition_and_reload())
        self.assertEqual(self.manifest["status"], "REVISION_REQUIRED")

    # ── COMMITTING → CHAIR_FINAL_CHECK ──

    def test_commit_triggers_final_check(self):
        set_status(self.manifest, WorkflowStatus.COMMITTING)
        save_manifest(str(self.wf_dir), self.manifest)
        write_atomic(self.wf_dir / ARTIFACT_COMMIT, "Commit abc123")
        self.assertTrue(self._transition_and_reload())
        self.assertEqual(self.manifest["status"], "CHAIR_FINAL_CHECK")

    # ── CHAIR_FINAL_CHECK → COMPLETED ──

    def test_completion_clean_completes(self):
        set_status(self.manifest, WorkflowStatus.CHAIR_FINAL_CHECK)
        save_manifest(str(self.wf_dir), self.manifest)
        write_atomic(self.wf_dir / ARTIFACT_COMPLETION, "COMPLETED\nAll good.")
        self.assertTrue(self._transition_and_reload())
        self.assertEqual(self.manifest["status"], "COMPLETED")

    def test_completion_with_issues_goes_to_user_decision(self):
        set_status(self.manifest, WorkflowStatus.CHAIR_FINAL_CHECK)
        save_manifest(str(self.wf_dir), self.manifest)
        write_atomic(self.wf_dir / ARTIFACT_COMPLETION, "DECISION_NEEDED:\nSome issues remain.")
        self.assertTrue(self._transition_and_reload())
        self.assertEqual(self.manifest["status"], "WAITING_USER_DECISION")

    # ── User actions ──

    def test_user_answer(self):
        set_status(self.manifest, WorkflowStatus.WAITING_USER_CLARIFICATION)
        save_manifest(str(self.wf_dir), self.manifest)
        ok = user_answer(self.wf_dir, self.manifest, "My answer")
        self.assertTrue(ok)
        self.assertTrue(exists_nonempty(self.wf_dir / ARTIFACT_ANSWERS))

    def test_user_answer_wrong_state(self):
        set_status(self.manifest, WorkflowStatus.PLANNING)
        save_manifest(str(self.wf_dir), self.manifest)
        ok = user_answer(self.wf_dir, self.manifest, "Answer")
        self.assertFalse(ok)

    def test_user_decision_approve(self):
        set_status(self.manifest, WorkflowStatus.WAITING_USER_APPROVAL)
        save_manifest(str(self.wf_dir), self.manifest)
        ok = user_decision(self.wf_dir, self.manifest, "APPROVE")
        self.assertTrue(ok)
        content = (self.wf_dir / ARTIFACT_USER_DECISION).read_text().strip()
        self.assertEqual(content, "APPROVE")

    def test_user_decision_invalid_token(self):
        set_status(self.manifest, WorkflowStatus.WAITING_USER_APPROVAL)
        save_manifest(str(self.wf_dir), self.manifest)
        ok = user_decision(self.wf_dir, self.manifest, "MAYBE")
        self.assertFalse(ok)

    def test_user_cancel(self):
        set_status(self.manifest, WorkflowStatus.PLANNING)
        save_manifest(str(self.wf_dir), self.manifest)
        ok = user_cancel(self.wf_dir, self.manifest)
        self.assertTrue(ok)
        self.manifest = self._reload()
        self.assertEqual(self.manifest["status"], "CANCELLED")

    def test_mark_failed(self):
        set_status(self.manifest, WorkflowStatus.EXECUTING)
        save_manifest(str(self.wf_dir), self.manifest)
        ok = mark_failed(self.wf_dir, self.manifest, "Agent crashed")
        self.assertTrue(ok)
        self.manifest = self._reload()
        self.assertEqual(self.manifest["status"], "FAILED")


class TestDispatchComputation(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.target = self.tmpdir
        scaffold_target(self.target)
        self.roster = build_roster(
            chair="codex",
            planners=[
                {"seat": "planner_a", "agent": "codex"},
                {"seat": "planner_b", "agent": "antigravity"},
                {"seat": "planner_c", "agent": "grok_build"},
            ],
            executor="codex",
            reviewer="antigravity",
        )
        self.manifest = make_manifest("wf_dispatch", self.target, self.roster)
        self.wf_dir = init_workflow(self.target, "wf_dispatch", self.manifest, "Test")
        ensure_workflow_dirs(self.wf_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _set_state(self, state):
        set_status(self.manifest, state)
        save_manifest(str(self.wf_dir), self.manifest)
        self.manifest = load_manifest(str(self.wf_dir))

    def test_created_dispatches_chair(self):
        self._set_state(WorkflowStatus.CREATED)
        items = compute_dispatch(self.wf_dir, self.manifest)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].agent, "codex")
        self.assertEqual(items[0].role, "chair")

    def test_planning_dispatches_3_planners(self):
        self._set_state(WorkflowStatus.PLANNING)
        items = compute_dispatch(self.wf_dir, self.manifest)
        self.assertEqual(len(items), 3)
        agents = {i.agent for i in items}
        self.assertEqual(agents, {"codex", "antigravity", "grok_build"})

    def test_planning_skips_completed(self):
        self._set_state(WorkflowStatus.PLANNING)
        write_atomic(self.wf_dir / proposal_path("planner_a"), "Done")
        items = compute_dispatch(self.wf_dir, self.manifest)
        self.assertEqual(len(items), 2)
        seats = {i.seat for i in items}
        self.assertEqual(seats, {"planner_b", "planner_c"})

    def test_peer_review_dispatches_6_comments(self):
        self._set_state(WorkflowStatus.PEER_REVIEW)
        items = compute_dispatch(self.wf_dir, self.manifest)
        self.assertEqual(len(items), 6)  # 3*(3-1) = 6

    def test_peer_review_skips_completed_comments(self):
        self._set_state(WorkflowStatus.PEER_REVIEW)
        write_atomic(self.wf_dir / comment_path("planner_a", "planner_b"), "Good")
        write_atomic(self.wf_dir / comment_path("planner_a", "planner_c"), "OK")
        items = compute_dispatch(self.wf_dir, self.manifest)
        self.assertEqual(len(items), 4)

    def test_synthesis_dispatches_chair(self):
        self._set_state(WorkflowStatus.CHAIR_SYNTHESIS)
        items = compute_dispatch(self.wf_dir, self.manifest)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].role, "chair")
        self.assertEqual(items[0].phase, "synthesis")

    def test_executing_dispatches_executor(self):
        self._set_state(WorkflowStatus.EXECUTING)
        items = compute_dispatch(self.wf_dir, self.manifest)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].role, "executor")

    def test_reviewing_dispatches_reviewer(self):
        self._set_state(WorkflowStatus.REVIEWING)
        items = compute_dispatch(self.wf_dir, self.manifest)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].role, "reviewer")

    def test_revision_dispatches_executor_for_next_walkthrough(self):
        self._set_state(WorkflowStatus.REVISION_REQUIRED)
        self.manifest["review_iteration"] = 1
        items = compute_dispatch(self.wf_dir, self.manifest)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].role, "executor")
        self.assertEqual(items[0].iteration, 2)

    def test_revision_no_dispatch_when_max_reached(self):
        self._set_state(WorkflowStatus.REVISION_REQUIRED)
        self.manifest["review_iteration"] = 3
        self.manifest["max_review_iterations"] = 3
        items = compute_dispatch(self.wf_dir, self.manifest)
        self.assertEqual(len(items), 0)

    def test_revision_does_not_dispatch_when_walkthrough_exists(self):
        self._set_state(WorkflowStatus.REVISION_REQUIRED)
        self.manifest["review_iteration"] = 1
        write_atomic(self.wf_dir / walkthrough_path(2), "# Already done")
        items = compute_dispatch(self.wf_dir, self.manifest)
        self.assertEqual(len(items), 0)

    def test_committing_dispatches_executor(self):
        self._set_state(WorkflowStatus.COMMITTING)
        items = compute_dispatch(self.wf_dir, self.manifest)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].role, "executor")
        self.assertEqual(items[0].phase, "commit")

    def test_final_check_dispatches_chair(self):
        self._set_state(WorkflowStatus.CHAIR_FINAL_CHECK)
        items = compute_dispatch(self.wf_dir, self.manifest)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].role, "chair")
        self.assertEqual(items[0].phase, "final_check")

    def test_completed_no_dispatch(self):
        self._set_state(WorkflowStatus.COMPLETED)
        items = compute_dispatch(self.wf_dir, self.manifest)
        self.assertEqual(len(items), 0)

    def test_1_planner_no_comments(self):
        roster1 = build_roster("c", [{"seat": "planner_a", "agent": "x"}], "e", "r")
        m1 = make_manifest("wf_1p", self.target, roster1)
        wf1 = init_workflow(self.target, "wf_1p", m1, "req")
        set_status(m1, WorkflowStatus.PEER_REVIEW)
        save_manifest(str(wf1), m1)
        items = compute_dispatch(wf1, m1)
        self.assertEqual(len(items), 0)  # 1 planner = 0 comments

    def test_4_planners_12_comments(self):
        roster4 = build_roster("c", [
            {"seat": "planner_a", "agent": "a"},
            {"seat": "planner_b", "agent": "b"},
            {"seat": "planner_c", "agent": "c"},
            {"seat": "planner_d", "agent": "d"},
        ], "e", "r")
        m4 = make_manifest("wf_4p", self.target, roster4)
        wf4 = init_workflow(self.target, "wf_4p", m4, "req")
        ensure_workflow_dirs(wf4)
        set_status(m4, WorkflowStatus.PEER_REVIEW)
        save_manifest(str(wf4), m4)
        items = compute_dispatch(wf4, m4)
        self.assertEqual(len(items), 12)  # 4*3 = 12


class TestInstructionGeneration(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.target = self.tmpdir
        scaffold_target(self.target)
        self.roster = build_roster(
            chair="codex",
            planners=[{"seat": "planner_a", "agent": "codex"}],
            executor="antigravity",
            reviewer="grok_build",
        )
        self.manifest = make_manifest("wf_inst", self.target, self.roster)
        self.wf_dir = init_workflow(self.target, "wf_inst", self.manifest, "Test")
        ensure_workflow_dirs(self.wf_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_chair_clarify_instruction(self):
        item = DispatchItem("wf:clarify:chair:1", "chair", "chair", "codex", "clarify", 1)
        inst = generate_instruction(self.wf_dir, self.manifest, item)
        self.assertIn("Chair", inst)
        self.assertIn("chair_brief.md", inst)

    def test_planner_proposal_instruction(self):
        item = DispatchItem("wf:proposal:planner_a:1", "planner", "planner_a", "codex", "proposal", 1)
        inst = generate_instruction(self.wf_dir, self.manifest, item)
        self.assertIn("Planner", inst)
        self.assertIn("proposals/planner_a.md", inst)

    def test_planner_comment_instruction(self):
        item = DispatchItem("wf:comment:planner_a_on_planner_b:1", "planner", "planner_a_on_planner_b", "codex", "comment", 1)
        inst = generate_instruction(self.wf_dir, self.manifest, item)
        self.assertIn("review", inst.lower())

    def test_executor_instruction(self):
        item = DispatchItem("wf:execution:executor:1", "executor", "executor", "codex", "execution", 1)
        inst = generate_instruction(self.wf_dir, self.manifest, item)
        self.assertIn("Executor", inst)
        self.assertIn("walkthrough", inst)

    def test_reviewer_instruction(self):
        item = DispatchItem("wf:review:reviewer:1", "reviewer", "reviewer", "codex", "review", 1)
        inst = generate_instruction(self.wf_dir, self.manifest, item)
        self.assertIn("DECISION:", inst)

    def test_commit_instruction(self):
        item = DispatchItem("wf:commit:executor:1", "executor", "executor", "codex", "commit", 1)
        inst = generate_instruction(self.wf_dir, self.manifest, item)
        self.assertIn("commit", inst.lower())

    def test_final_check_instruction(self):
        item = DispatchItem("wf:final_check:chair:1", "chair", "chair", "codex", "final_check", 1)
        inst = generate_instruction(self.wf_dir, self.manifest, item)
        self.assertIn("Chair", inst)
        self.assertIn("final", inst.lower())


if __name__ == "__main__":
    unittest.main()
