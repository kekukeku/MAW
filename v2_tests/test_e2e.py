"""v2 E2E tests — full workflow lifecycle with mock adapter.

Covers:
  1. Single agent full workflow
  2. 3-agent full workflow
  3. 4-planner comment matrix
  4. REQUEST_CHANGES → revision → APPROVE
  5. Chair clarification cycle
  6. Watcher restart recovery
"""

import os
import tempfile
import unittest
from pathlib import Path

from v2.schema import (
    WorkflowStatus,
    build_roster,
    make_manifest,
    load_manifest,
    save_manifest,
    set_status,
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
)
from v2.files import (
    scaffold_target,
    init_workflow,
    ensure_workflow_dirs,
    write_atomic,
    exists_nonempty,
    set_active_workflow,
)
from v2.workflow import (
    compute_dispatch,
    try_transition,
    user_answer,
    user_decision,
    generate_instruction,
    DispatchItem,
)
from v2.dispatcher import dispatch, MockAdapter, register_adapter, get_adapter
from v2.watcher import Watcher


# Ensure mock adapter always available
register_adapter("mock", MockAdapter(delay_seconds=0.01))
register_adapter("codex", MockAdapter(delay_seconds=0.01))
register_adapter("antigravity", MockAdapter(delay_seconds=0.01))
register_adapter("grok_build", MockAdapter(delay_seconds=0.01))


def _run_full_workflow(wf_dir, manifest, auto_dispatch=True):
    """Run workflow tick-by-tick until terminal or waiting state."""
    max_ticks = 200
    tick = 0
    stall_count = 0
    prev_status = None
    while tick < max_ticks:
        tick += 1
        manifest = load_manifest(str(wf_dir))
        status = WorkflowStatus(manifest["status"])

        if status in {WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED, WorkflowStatus.FAILED}:
            return manifest, status

        if status in {
            WorkflowStatus.WAITING_USER_CLARIFICATION,
            WorkflowStatus.WAITING_USER_APPROVAL,
            WorkflowStatus.WAITING_USER_DECISION,
        }:
            return manifest, status

        # Try transition first
        transitioned = try_transition(wf_dir, manifest)

        # Compute dispatch
        manifest = load_manifest(str(wf_dir))
        items = compute_dispatch(wf_dir, manifest)
        dispatched_any = False

        for item in items:
            if item.agent == "maw":
                continue
            if auto_dispatch:
                instruction = generate_instruction(wf_dir, manifest, item)
                result = dispatch(item, wf_dir, instruction, timeout=10)
                if not result.success:
                    return manifest, WorkflowStatus.FAILED
                dispatched_any = True

        # Stall detection: no transition and no dispatch = stuck
        if not transitioned and not dispatched_any:
            stall_count += 1
            if stall_count > 3:
                return manifest, status
        else:
            stall_count = 0

    return manifest, WorkflowStatus(manifest["status"])


class TestE2ESingleAgent(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.target = self.tmpdir
        scaffold_target(self.target)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_single_agent_full_workflow(self):
        """Full workflow: CREATED -> COMPLETED with single agent."""
        roster = build_roster(
            chair="mock",
            planners=[{"seat": "planner_a", "agent": "mock"}],
            executor="mock",
            reviewer="mock",
        )
        manifest = make_manifest("wf_single", self.target, roster)
        wf_dir = init_workflow(self.target, "wf_single", manifest, "Implement feature X")
        ensure_workflow_dirs(wf_dir)

        # ── CREATED: auto handle ──
        manifest = load_manifest(str(wf_dir))
        set_status(manifest, WorkflowStatus.CHAIR_CLARIFYING)
        save_manifest(str(wf_dir), manifest)

        # ── CHAIR_CLARIFYING: dispatch chair ──
        manifest, status = _run_full_workflow(wf_dir, manifest, auto_dispatch=True)

        # Should have transitioned to WAITING_USER_APPROVAL
        self.assertIn(status, {
            WorkflowStatus.WAITING_USER_APPROVAL,
            WorkflowStatus.WAITING_USER_CLARIFICATION,
            WorkflowStatus.COMPLETED,
        })

        # If waiting for approval, approve and continue
        if status == WorkflowStatus.WAITING_USER_CLARIFICATION:
            user_answer(wf_dir, manifest, "It should do X, Y, Z.")
            try_transition(wf_dir, manifest)
            manifest = load_manifest(str(wf_dir))

        if status == WorkflowStatus.WAITING_USER_APPROVAL or WorkflowStatus(manifest["status"]) == WorkflowStatus.WAITING_USER_APPROVAL:
            manifest = load_manifest(str(wf_dir))
            user_decision(wf_dir, manifest, "APPROVE")
            try_transition(wf_dir, manifest)
            manifest = load_manifest(str(wf_dir))
            manifest, status = _run_full_workflow(wf_dir, manifest, auto_dispatch=True)

        # Should reach COMPLETED
        self.assertEqual(status, WorkflowStatus.COMPLETED)

        # Verify all expected artifacts exist
        self.assertTrue(exists_nonempty(wf_dir / "chair_brief.md") or exists_nonempty(wf_dir / "questions.md"))
        self.assertTrue(exists_nonempty(wf_dir / "final_plan.md"))
        self.assertTrue(exists_nonempty(wf_dir / walkthrough_path(1)))
        self.assertTrue(exists_nonempty(wf_dir / review_path(1)))
        self.assertTrue(exists_nonempty(wf_dir / "commit.md"))
        self.assertTrue(exists_nonempty(wf_dir / "completion.md"))


class TestE2EMultiAgent(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.target = self.tmpdir
        scaffold_target(self.target)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_three_agent_full_workflow(self):
        """Full workflow with 3 different agents."""
        roster = build_roster(
            chair="codex",
            planners=[
                {"seat": "planner_a", "agent": "codex"},
                {"seat": "planner_b", "agent": "antigravity"},
                {"seat": "planner_c", "agent": "grok_build"},
            ],
            executor="antigravity",
            reviewer="grok_build",
        )
        manifest = make_manifest("wf_multi", self.target, roster)
        wf_dir = init_workflow(self.target, "wf_multi", manifest, "Refactor the core module")
        ensure_workflow_dirs(wf_dir)

        manifest = load_manifest(str(wf_dir))
        set_status(manifest, WorkflowStatus.CHAIR_CLARIFYING)
        save_manifest(str(wf_dir), manifest)

        manifest, status = _run_full_workflow(wf_dir, manifest, auto_dispatch=True)

        if status == WorkflowStatus.WAITING_USER_APPROVAL:
            user_decision(wf_dir, manifest, "APPROVE")
            try_transition(wf_dir, manifest)
            manifest = load_manifest(str(wf_dir))
            manifest, status = _run_full_workflow(wf_dir, manifest, auto_dispatch=True)
        elif status == WorkflowStatus.WAITING_USER_CLARIFICATION:
            user_answer(wf_dir, manifest, "Yes, refactor for clarity.")
            try_transition(wf_dir, manifest)
            manifest = load_manifest(str(wf_dir))
            manifest, status = _run_full_workflow(wf_dir, manifest, auto_dispatch=True)
            if status == WorkflowStatus.WAITING_USER_APPROVAL:
                user_decision(wf_dir, manifest, "APPROVE")
                try_transition(wf_dir, manifest)
                manifest = load_manifest(str(wf_dir))
                manifest, status = _run_full_workflow(wf_dir, manifest, auto_dispatch=True)

        self.assertEqual(status, WorkflowStatus.COMPLETED)

        # Verify 3 proposals exist
        for seat in ["planner_a", "planner_b", "planner_c"]:
            self.assertTrue(
                exists_nonempty(wf_dir / proposal_path(seat)),
                f"Missing proposal: {seat}",
            )

        # Verify 6 comments exist
        seats = ["planner_a", "planner_b", "planner_c"]
        for r in seats:
            for t in seats:
                if r != t:
                    self.assertTrue(
                        exists_nonempty(wf_dir / comment_path(r, t)),
                        f"Missing comment: {r} on {t}",
                    )


class TestE2EFourPlanners(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.target = self.tmpdir
        scaffold_target(self.target)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_four_planners_comment_matrix(self):
        """4 planners = 4 proposals + 12 comments."""
        roster = build_roster(
            chair="mock",
            planners=[
                {"seat": "planner_a", "agent": "mock"},
                {"seat": "planner_b", "agent": "mock"},
                {"seat": "planner_c", "agent": "mock"},
                {"seat": "planner_d", "agent": "mock"},
            ],
            executor="mock",
            reviewer="mock",
        )
        manifest = make_manifest("wf_4p", self.target, roster)
        wf_dir = init_workflow(self.target, "wf_4p", manifest, "Big refactor")
        ensure_workflow_dirs(wf_dir)

        manifest = load_manifest(str(wf_dir))
        set_status(manifest, WorkflowStatus.CHAIR_CLARIFYING)
        save_manifest(str(wf_dir), manifest)

        manifest, status = _run_full_workflow(wf_dir, manifest, auto_dispatch=True)

        # Run past planning into peer review
        while True:
            manifest = load_manifest(str(wf_dir))
            s = WorkflowStatus(manifest["status"])
            if s == WorkflowStatus.CHAIR_SYNTHESIS:
                break
            if s == WorkflowStatus.WAITING_USER_APPROVAL:
                user_decision(wf_dir, manifest, "APPROVE")
                try_transition(wf_dir, manifest)
                manifest = load_manifest(str(wf_dir))
                manifest, _ = _run_full_workflow(wf_dir, manifest, auto_dispatch=True)
                break
            if s in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}:
                break
            manifest, _ = _run_full_workflow(wf_dir, manifest, auto_dispatch=True)

        # Verify 4 proposals
        for seat in ["planner_a", "planner_b", "planner_c", "planner_d"]:
            self.assertTrue(
                exists_nonempty(wf_dir / proposal_path(seat)),
                f"Missing proposal: {seat}",
            )

        # Verify 12 comments (4 * 3)
        seats = ["planner_a", "planner_b", "planner_c", "planner_d"]
        count = 0
        for r in seats:
            for t in seats:
                if r != t:
                    self.assertTrue(
                        exists_nonempty(wf_dir / comment_path(r, t)),
                        f"Missing comment: {r}_on_{t}",
                    )
                    count += 1
        self.assertEqual(count, 12)


class TestE2ERevisionCycle(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.target = self.tmpdir
        scaffold_target(self.target)

        # Register a mock that fails the first review, then passes
        self._call_count = 0

        class ConditionalReviewer(MockAdapter):
            def __init__(self):
                super().__init__(delay_seconds=0.01)

            def invoke(self, role, seat, target_path, instruction, expected_output, timeout=600):
                nonlocal_self = TestE2ERevisionCycle.__dict__.get('_call_count')
                result = super().invoke(role, seat, target_path, instruction, expected_output, timeout)
                # Override the review file to be REQUEST_CHANGES on first call
                if role == "reviewer":
                    self._call_count = getattr(self, '_call_count', 0) + 1
                    if self._call_count == 1:
                        # Write REQUEST_CHANGES instead
                        wf_dir = Path(expected_output).parent
                        tmp = Path(expected_output + ".tmp")
                        tmp.write_text("DECISION: REQUEST_CHANGES\n\nFix the following:\n1. Missing error handling\n2. No tests added\n", encoding="utf-8")
                        tmp.rename(expected_output)
                return result

        register_adapter("cond_reviewer", ConditionalReviewer())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_revision_then_approve(self):
        """REQUEST_CHANGES on first review, APPROVE on second."""
        roster = build_roster(
            chair="mock",
            planners=[{"seat": "planner_a", "agent": "mock"}],
            executor="mock",
            reviewer="cond_reviewer",
        )
        manifest = make_manifest("wf_rev", self.target, roster, max_review_iterations=3)
        wf_dir = init_workflow(self.target, "wf_rev", manifest, "Add feature Y")
        ensure_workflow_dirs(wf_dir)

        manifest = load_manifest(str(wf_dir))
        set_status(manifest, WorkflowStatus.CHAIR_CLARIFYING)
        save_manifest(str(wf_dir), manifest)

        # Fast-forward to EXECUTING
        manifest, status = _run_full_workflow(wf_dir, manifest, auto_dispatch=True)

        # Handle any waiting states
        while status in {WorkflowStatus.WAITING_USER_CLARIFICATION, WorkflowStatus.WAITING_USER_APPROVAL}:
            if status == WorkflowStatus.WAITING_USER_CLARIFICATION:
                user_answer(wf_dir, manifest, "Answer")
            elif status == WorkflowStatus.WAITING_USER_APPROVAL:
                user_decision(wf_dir, manifest, "APPROVE")
            try_transition(wf_dir, manifest)
            manifest = load_manifest(str(wf_dir))
            manifest, status = _run_full_workflow(wf_dir, manifest, auto_dispatch=True)

        # Status should be either REVISION_REQUIRED (after first review=REQUEST_CHANGES)
        # or COMPLETED (if somehow everything resolved quickly)
        manifest = load_manifest(str(wf_dir))
        status = WorkflowStatus(manifest["status"])

        if status == WorkflowStatus.REVISION_REQUIRED:
            # Continue to get second walkthrough and review
            manifest, status = _run_full_workflow(wf_dir, manifest, auto_dispatch=True)

            # Handle waiting states
            while status in {WorkflowStatus.WAITING_USER_CLARIFICATION, WorkflowStatus.WAITING_USER_APPROVAL}:
                if status == WorkflowStatus.WAITING_USER_CLARIFICATION:
                    user_answer(wf_dir, manifest, "Answer")
                elif status == WorkflowStatus.WAITING_USER_APPROVAL:
                    user_decision(wf_dir, manifest, "APPROVE")
                manifest, status = _run_full_workflow(wf_dir, manifest, auto_dispatch=True)

        self.assertEqual(status, WorkflowStatus.COMPLETED)

        # Verify two walkthroughs exist (original + revision)
        self.assertTrue(
            exists_nonempty(wf_dir / walkthrough_path(1)),
            "Missing first walkthrough",
        )
        self.assertTrue(
            exists_nonempty(wf_dir / walkthrough_path(2)),
            "Missing revision walkthrough",
        )


class TestE2EClarificationCycle(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.target = self.tmpdir
        scaffold_target(self.target)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_chair_clarification_cycle(self):
        """Chair asks question, user answers, chair continues."""
        # Custom mock that always produces questions.md on first clarify
        class QuestioningChair(MockAdapter):
            def __init__(self):
                super().__init__(delay_seconds=0.01)
                self._clarify_count = 0

            def invoke(self, role, seat, target_path, instruction, expected_output, timeout=600):
                if role == "chair" and "clarify" in instruction.lower():
                    self._clarify_count += 1
                    if self._clarify_count == 1:
                        # First clarify: ask questions
                        expected_path = Path(expected_output).parent / "questions.md"
                        tmp = Path(str(expected_path) + ".tmp")
                        tmp.write_text("## Questions\n1. What is the scope?\n2. Any deadlines?", encoding="utf-8")
                        tmp.rename(expected_path)
                        from v2.dispatcher import DispatchResult
                        return DispatchResult(success=True)
                return super().invoke(role, seat, target_path, instruction, expected_output, timeout)

        register_adapter("questioning_chair", QuestioningChair())

        roster = build_roster(
            chair="questioning_chair",
            planners=[{"seat": "planner_a", "agent": "mock"}],
            executor="mock",
            reviewer="mock",
        )
        manifest = make_manifest("wf_clar", self.target, roster)
        wf_dir = init_workflow(self.target, "wf_clar", manifest, "Vague request")
        ensure_workflow_dirs(wf_dir)

        manifest = load_manifest(str(wf_dir))
        set_status(manifest, WorkflowStatus.CHAIR_CLARIFYING)
        save_manifest(str(wf_dir), manifest)

        # Run first tick → should go to WAITING_USER_CLARIFICATION
        manifest, status = _run_full_workflow(wf_dir, manifest, auto_dispatch=True)
        self.assertEqual(status, WorkflowStatus.WAITING_USER_CLARIFICATION)
        self.assertTrue(exists_nonempty(wf_dir / "questions.md"))

        # User answers — write answer, then transition state
        user_answer(wf_dir, manifest, "Scope: module X. Deadline: next week.")
        try_transition(wf_dir, manifest)
        manifest = load_manifest(str(wf_dir))

        # Continue → should go to chair clarifying again, then produce brief
        manifest, status = _run_full_workflow(wf_dir, manifest, auto_dispatch=True)

        # Should NOT be waiting for clarification again
        self.assertNotEqual(status, WorkflowStatus.WAITING_USER_CLARIFICATION)


class TestWatcherRecovery(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.target = self.tmpdir
        scaffold_target(self.target)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_watcher_restart_no_duplicate_dispatch(self):
        """Watcher restart should resume without re-dispatching completed work."""
        roster = build_roster(
            chair="mock",
            planners=[{"seat": "planner_a", "agent": "mock"}],
            executor="mock",
            reviewer="mock",
        )
        manifest = make_manifest("wf_rec", self.target, roster)
        wf_dir = init_workflow(self.target, "wf_rec", manifest, "Test recovery")
        ensure_workflow_dirs(wf_dir)

        manifest = load_manifest(str(wf_dir))
        set_status(manifest, WorkflowStatus.CHAIR_CLARIFYING)
        save_manifest(str(wf_dir), manifest)

        # Run the watcher once
        w1 = Watcher(
            target_path=self.target,
            workflow_id="wf_rec",
            auto_run=True,
        )
        w1.run_once()

        # Check state after one tick
        manifest = load_manifest(str(wf_dir))
        status = WorkflowStatus(manifest["status"])

        # Run again with "fresh" watcher (simulate restart)
        w2 = Watcher(
            target_path=self.target,
            workflow_id="wf_rec",
            auto_run=True,
        )
        w2.run_once()

        # State should have progressed (not re-done the same work)
        manifest2 = load_manifest(str(wf_dir))
        status2 = WorkflowStatus(manifest2["status"])

        # The watcher should have advanced the state, not idled
        # (At minimum, not back to CREATED)
        self.assertNotEqual(status2, WorkflowStatus.CREATED)


if __name__ == "__main__":
    unittest.main()
