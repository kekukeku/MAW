"""v2 dispatcher tests — mock adapter dispatch behavior."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

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
    AntigravityAdapter,
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


class TestAntigravityAdapter(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.wf_dir = Path(self.tmpdir)
        ensure_workflow_dirs(self.wf_dir)
        self.adapter = AntigravityAdapter()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("subprocess.run")
    @patch("v2.dispatcher.discover_antigravity_credentials")
    @patch("os.path.isfile")
    def test_antigravity_invoke_success_already_exists(self, mock_isfile, mock_discover, mock_run):
        mock_isfile.return_value = True
        mock_discover.return_value = {
            "address": "localhost:49421",
            "csrf_token": "token-123",
            "project_id": "proj-123"
        }

        expected_out = str(self.wf_dir / "proposals" / "planner_a.md")
        Path(expected_out).parent.mkdir(parents=True, exist_ok=True)
        Path(expected_out).write_text("Test proposal contents", encoding="utf-8")

        result = self.adapter.invoke(
            role="planner",
            seat="planner_a",
            target_path=self.tmpdir,
            instruction="Test instructions",
            expected_output=expected_out,
            timeout=5
        )

        self.assertTrue(result.success)
        self.assertFalse(result.is_async)
        mock_run.assert_not_called()

    @patch("subprocess.run")
    @patch("v2.dispatcher.discover_antigravity_credentials")
    @patch("os.path.isfile")
    def test_antigravity_invoke_success_dispatch(self, mock_isfile, mock_discover, mock_run):
        from unittest.mock import MagicMock
        mock_isfile.return_value = True
        mock_discover.return_value = {
            "address": "localhost:49421",
            "csrf_token": "token-123",
            "project_id": "proj-123"
        }

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = '{"response": {"newConversation": {"conversationId": "conv-123"}}}'
        mock_run.return_value = mock_res

        # Write manifest.json so workflow directory search succeeds
        (self.wf_dir / "manifest.json").write_text("{}", encoding="utf-8")

        expected_out = str(self.wf_dir / "proposals" / "planner_a.md")
        Path(expected_out).parent.mkdir(parents=True, exist_ok=True)
        # We do NOT write expected_out

        result = self.adapter.invoke(
            role="planner",
            seat="planner_a",
            target_path=self.tmpdir,
            instruction="Test instructions",
            expected_output=expected_out,
            timeout=5
        )

        self.assertTrue(result.success)
        self.assertTrue(result.is_async)
        self.assertEqual(result.invocation_id, "conv-123")
        mock_run.assert_called_once()
        self.assertTrue(Path(expected_out + ".invocation").exists())

    @patch("v2.dispatcher.discover_antigravity_credentials")
    @patch("os.path.isfile")
    def test_antigravity_invoke_manual_fallback(self, mock_isfile, mock_discover):
        mock_isfile.return_value = False

        expected_out = str(self.wf_dir / "proposals" / "planner_a.md")
        Path(expected_out).parent.mkdir(parents=True, exist_ok=True)
        # We do NOT write expected_out

        # Write manifest.json so workflow directory search succeeds
        (self.wf_dir / "manifest.json").write_text("{}", encoding="utf-8")

        result = self.adapter.invoke(
            role="planner",
            seat="planner_a",
            target_path=self.tmpdir,
            instruction="Test instructions",
            expected_output=expected_out,
            timeout=5
        )

        self.assertTrue(result.success)
        self.assertTrue(result.is_async)
        self.assertEqual(result.invocation_id, "manual")
        self.assertTrue(Path(expected_out + ".invocation").exists())


class TestAntigravityDiscovery(unittest.TestCase):

    @patch("subprocess.check_output")
    @patch.dict("os.environ", {
        "ANTIGRAVITY_LS_ADDRESS": "localhost:9999",
        "ANTIGRAVITY_CSRF_TOKEN": "csrf-9999",
        "ANTIGRAVITY_PROJECT_ID": "proj-9999"
    })
    def test_discover_credentials_from_env(self, mock_ps):
        from v2.dispatcher import discover_antigravity_credentials
        res = discover_antigravity_credentials()
        self.assertEqual(res["address"], "localhost:9999")
        self.assertEqual(res["csrf_token"], "csrf-9999")
        self.assertEqual(res["project_id"], "proj-9999")
        mock_ps.assert_not_called()

    @patch("subprocess.run")
    @patch("subprocess.check_output")
    @patch("os.path.isfile")
    @patch.dict("os.environ", {}, clear=True)
    def test_discover_credentials_no_new_conversation_probe(self, mock_isfile, mock_ps, mock_run):
        from v2.dispatcher import discover_antigravity_credentials

        mock_isfile.return_value = True

        def ps_lsof_side_effect(args, **kwargs):
            if "ps" in args:
                return "kevin 1234 0.0 0.0 ... language_server --csrf_token my-csrf-token\n"
            if "lsof" in args:
                return "language_ 1234 kevin 6u IPv4 TCP 127.0.0.1:49421 (LISTEN)\n"
            return ""
        mock_ps.side_effect = ps_lsof_side_effect

        # Mock agentapi get-conversation-metadata response
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_res.stdout = '{"error": "rpc error: code = Unknown desc = trajectory not found: 00000000-0000-0000-0000-000000000000"}'
        mock_run.return_value = mock_res

        res = discover_antigravity_credentials()
        self.assertEqual(res["address"], "localhost:49421")
        self.assertEqual(res["csrf_token"], "my-csrf-token")

        # Verify that new-conversation was NOT called
        for call in mock_run.call_args_list:
            cmd = call[0][0]
            self.assertNotIn("new-conversation", cmd)
            self.assertIn("get-conversation-metadata", cmd)


if __name__ == "__main__":
    from unittest.mock import patch
    unittest.main()



if __name__ == "__main__":
    from unittest.mock import patch
    unittest.main()
