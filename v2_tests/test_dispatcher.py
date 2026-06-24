"""v2 dispatcher tests — mock adapter dispatch behavior."""

import tempfile
import unittest
from pathlib import Path

from v2.schema import (
    build_roster,
    make_manifest,
    proposal_path,
    comment_path,
    walkthrough_path,
    review_path,
    ARTIFACT_CHAIR_BRIEF,
    ARTIFACT_FINAL_PLAN,
    ARTIFACT_COMMIT,
    ARTIFACT_COMPLETION,
    ARTIFACT_QUESTIONS,
)
from v2.files import init_workflow, ensure_workflow_dirs, exists_nonempty
from v2.dispatcher import (
    MockAdapter,
    DispatchResult,
    dispatch,
    get_adapter,
    register_adapter,
    list_adapters,
)
from v2.workflow import DispatchItem


class TestMockAdapter(unittest.TestCase):

    def setUp(self):
        self.adapter = MockAdapter(delay_seconds=0.01)
        self.tmpdir = tempfile.mkdtemp()
        self.wf_dir = Path(self.tmpdir)
        ensure_workflow_dirs(self.wf_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_mock_chair_clarify(self):
        result = self.adapter.invoke(
            role="chair", seat="chair",
            target_path="/tmp/test",
            instruction="Clarify this request",
            expected_output=str(self.wf_dir / ARTIFACT_CHAIR_BRIEF),
        )
        self.assertTrue(result.success)
        self.assertTrue(exists_nonempty(self.wf_dir / ARTIFACT_CHAIR_BRIEF))

    def test_mock_planner_proposal(self):
        result = self.adapter.invoke(
            role="planner", seat="planner_a",
            target_path="/tmp/test",
            instruction="Propose an approach",
            expected_output=str(self.wf_dir / proposal_path("planner_a")),
        )
        self.assertTrue(result.success)
        self.assertTrue(exists_nonempty(self.wf_dir / proposal_path("planner_a")))

    def test_mock_planner_comment(self):
        result = self.adapter.invoke(
            role="planner", seat="planner_a_on_b",
            target_path="/tmp/test",
            instruction="Review planner_b's proposal",
            expected_output=str(self.wf_dir / comment_path("planner_a", "planner_b")),
        )
        self.assertTrue(result.success)
        self.assertTrue(exists_nonempty(self.wf_dir / comment_path("planner_a", "planner_b")))

    def test_mock_chair_synthesis(self):
        result = self.adapter.invoke(
            role="chair", seat="chair",
            target_path="/tmp/test",
            instruction="Synthesize proposals into final plan",
            expected_output=str(self.wf_dir / ARTIFACT_FINAL_PLAN),
        )
        self.assertTrue(result.success)
        self.assertTrue(exists_nonempty(self.wf_dir / ARTIFACT_FINAL_PLAN))

    def test_mock_executor(self):
        result = self.adapter.invoke(
            role="executor", seat="executor",
            target_path="/tmp/test",
            instruction="Implement the plan",
            expected_output=str(self.wf_dir / walkthrough_path(1)),
        )
        self.assertTrue(result.success)
        self.assertTrue(exists_nonempty(self.wf_dir / walkthrough_path(1)))

    def test_mock_reviewer_approve(self):
        result = self.adapter.invoke(
            role="reviewer", seat="reviewer",
            target_path="/tmp/test",
            instruction="Review the work",
            expected_output=str(self.wf_dir / review_path(1)),
        )
        self.assertTrue(result.success)
        content = (self.wf_dir / review_path(1)).read_text()
        self.assertIn("APPROVE", content)

    def test_mock_executor_commit(self):
        result = self.adapter.invoke(
            role="executor", seat="executor",
            target_path="/tmp/test",
            instruction="Create commit",
            expected_output=str(self.wf_dir / ARTIFACT_COMMIT),
        )
        self.assertTrue(result.success)
        self.assertTrue(exists_nonempty(self.wf_dir / ARTIFACT_COMMIT))

    def test_mock_chair_final_check(self):
        result = self.adapter.invoke(
            role="chair", seat="chair",
            target_path="/tmp/test",
            instruction="Final inspection",
            expected_output=str(self.wf_dir / ARTIFACT_COMPLETION),
        )
        self.assertTrue(result.success)
        self.assertTrue(exists_nonempty(self.wf_dir / ARTIFACT_COMPLETION))

    def test_mock_failure(self):
        adapter = MockAdapter(fail_on={"planner_a"})
        result = adapter.invoke(
            role="planner", seat="planner_a",
            target_path="/tmp/test",
            instruction="Propose",
            expected_output=str(self.wf_dir / proposal_path("planner_a")),
        )
        self.assertFalse(result.success)

    def test_adapter_registry(self):
        register_adapter("fake", MockAdapter())
        self.assertIsNotNone(get_adapter("fake"))
        self.assertIn("fake", list_adapters())


class TestDispatchFunction(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.target = self.tmpdir
        from v2.files import scaffold_target
        scaffold_target(self.target)
        self.roster = build_roster(
            chair="mock",
            planners=[{"seat": "planner_a", "agent": "mock"}],
            executor="mock",
            reviewer="mock",
        )
        self.manifest = make_manifest("wf_disp", self.target, self.roster)
        self.wf_dir = init_workflow(self.target, "wf_disp", self.manifest, "Test")
        ensure_workflow_dirs(self.wf_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_dispatch_chair_clarify(self):
        item = DispatchItem("wf:clarify:chair:1", "chair", "chair", "mock", "clarify", 1)
        result = dispatch(item, self.wf_dir, "instruction", timeout=5)
        self.assertTrue(result.success)
        self.assertTrue(exists_nonempty(self.wf_dir / ARTIFACT_CHAIR_BRIEF))

    def test_dispatch_planner(self):
        item = DispatchItem("wf:proposal:planner_a:1", "planner", "planner_a", "mock", "proposal", 1)
        result = dispatch(item, self.wf_dir, "instruction", timeout=5)
        self.assertTrue(result.success)
        self.assertTrue(exists_nonempty(self.wf_dir / proposal_path("planner_a")))

    def test_dispatch_unknown_agent(self):
        item = DispatchItem("wf:proposal:planner_a:1", "planner", "planner_a", "nonexistent", "proposal", 1)
        result = dispatch(item, self.wf_dir, "instruction", timeout=5)
        self.assertFalse(result.success)
        self.assertIn("No adapter", result.error)

    def test_dispatch_executor(self):
        item = DispatchItem("wf:exec:executor:1", "executor", "executor", "mock", "execution", 1)
        result = dispatch(item, self.wf_dir, "instruction", timeout=5)
        self.assertTrue(result.success)
        self.assertTrue(exists_nonempty(self.wf_dir / walkthrough_path(1)))

    def test_dispatch_reviewer(self):
        item = DispatchItem("wf:rev:reviewer:1", "reviewer", "reviewer", "mock", "review", 1)
        result = dispatch(item, self.wf_dir, "instruction", timeout=5)
        self.assertTrue(result.success)
        content = (self.wf_dir / review_path(1)).read_text()
        self.assertIn("APPROVE", content)


if __name__ == "__main__":
    unittest.main()
