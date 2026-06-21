"""E2E happy-path test with mock council and template target project."""

import os
import json
import unittest
import asyncio
import tempfile
import shutil
import subprocess
from unittest.mock import patch

import council.storage as storage_mod
import loop_orchestrator as orch_mod
from loop_orchestrator import LoopOrchestrator, WorkflowState


class TestE2EWorkflow(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.template = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template_target_project")
        self.target_path = os.path.join(self.test_dir, "target")
        shutil.copytree(self.template, self.target_path)

        self.conv_dir = os.path.join(self.test_dir, "conversations")
        os.makedirs(self.conv_dir, exist_ok=True)
        self.wf_path = os.path.join(self.test_dir, "workflows.json")

        storage_mod.CONVERSATIONS_DIR = self.conv_dir
        orch_mod.CONVERSATIONS_DIR = self.conv_dir
        orch_mod.WORKFLOWS_PATH = self.wf_path

        subprocess.run(["git", "init"], cwd=self.target_path, capture_output=True, check=False)
        subprocess.run(["git", "add", "-A"], cwd=self.target_path, capture_output=True, check=False)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.target_path, capture_output=True, check=False)
        subprocess.run(
            ["git", "config", "user.email", "test@maw.local"],
            cwd=self.target_path, capture_output=True, check=False,
        )
        subprocess.run(
            ["git", "config", "user.name", "MAW Test"],
            cwd=self.target_path, capture_output=True, check=False,
        )

        self.orch = LoopOrchestrator()
        self.orch._workflows = {}

        self.targets_config = {
            "default": "mock",
            "projects": {"mock": {"name": "Mock", "path": self.target_path}},
        }

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_full_happy_path(self):
        async def _run():
            with patch("export.load_targets", return_value=self.targets_config), \
                 patch("loop_orchestrator.load_targets", return_value=self.targets_config), \
                 patch("export.get_conversations_dir", return_value=self.conv_dir):
                wf = await self.orch.start_council(
                    prompt="Add hello world endpoint",
                    target_key="mock",
                    title="Hello World",
                    review_policy={
                        "mode": "AI",
                        "max_iterations": 3,
                        "allow_request_changes": True,
                        "require_pre_commit_approval": True,
                    },
                    mock=True,
                )

                conv_id = None
                for _ in range(100):
                    await asyncio.sleep(0.1)
                    for w in self.orch.list_workflows():
                        if w.get("conversation_id") and w["state"] == WorkflowState.COUNCIL_PENDING_APPROVAL.value:
                            conv_id = w["conversation_id"]
                            break
                    if conv_id:
                        break
                self.assertIsNotNone(conv_id)

                approved = await self.orch.approve_council(conv_id, title="Hello World")
                task_num = approved["task_num"]
                self.assertEqual(task_num, "001")

                task_file = os.path.join(self.target_path, "MAW_workflow", "TASKS", f"task_{task_num}.md")
                self.assertTrue(os.path.isfile(task_file))

                final_state = None
                for _ in range(100):
                    await asyncio.sleep(0.2)
                    status = self.orch.get_status(task_num)
                    final_state = status["state"]
                    if final_state in (
                        WorkflowState.COMMIT_PENDING_APPROVAL.value,
                        WorkflowState.FAILED.value,
                    ):
                        break

                self.assertEqual(final_state, WorkflowState.COMMIT_PENDING_APPROVAL.value)
                self.assertIsNotNone(status.get("pre_commit_report"))

                branch = f"task/task_{task_num}_hello-world"
                subprocess.run(["git", "checkout", "-b", branch], cwd=self.target_path, capture_output=True, check=False)
                with open(os.path.join(self.target_path, "feature.txt"), "w") as f:
                    f.write("feature")
                subprocess.run(["git", "add", "feature.txt"], cwd=self.target_path, capture_output=True, check=False)
                subprocess.run(["git", "commit", "-m", "feature"], cwd=self.target_path, capture_output=True, check=False)
                subprocess.run(["git", "checkout", "-"], cwd=self.target_path, capture_output=True, check=False)

                committed = await self.orch.approve_commit(task_num)
                self.assertIn(committed["state"], [
                    WorkflowState.COMPLETED.value,
                    WorkflowState.FINAL_REPORT_PRESENTED.value,
                ])
                final_report_path = os.path.join(
                    self.target_path, "MAW_workflow", "PLANNING", f"final_report_{task_num}.md"
                )
                self.assertTrue(os.path.isfile(final_report_path))

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()