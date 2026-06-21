"""Safety limits, crash recovery, and advanced-mode guard tests."""

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
from loop_orchestrator import LoopOrchestrator, WorkflowState, load_workflows


class TestSafety(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.template = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "template_target_project",
        )
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

    def _base_wf(self) -> dict:
        return {
            "workflow_id": "wf-001",
            "task_num": "001",
            "conversation_id": "conv-001",
            "state": WorkflowState.REVIEW_DECISION_PENDING.value,
            "title": "Safety test",
            "target_key": "mock",
            "review_policy": {
                "mode": "AI",
                "max_iterations": 3,
                "allow_request_changes": True,
                "require_pre_commit_approval": False,
                "auto_approve_council": False,
            },
            "review_iterations": 0,
            "logs": [],
            "export_result": {
                "targetPath": self.target_path,
                "workflowPath": os.path.join(self.target_path, "MAW_workflow"),
                "targetKey": "mock",
            },
            "mock": True,
        }

    def test_allow_auto_commit_guard_blocks_gate2_bypass(self):
        async def _run():
            wf = self._base_wf()
            self.orch._workflows["001"] = wf
            with patch.object(orch_mod, "ALLOW_AUTO_COMMIT", False):
                await self.orch._apply_review_decision(wf, "APPROVE")
            self.assertEqual(wf["state"], WorkflowState.COMMIT_PENDING_APPROVAL.value)
            self.assertTrue(any("Auto-commit blocked" in log["line"] for log in wf["logs"]))

        asyncio.run(_run())

    def test_allow_auto_commit_permits_auto_commit_when_enabled(self):
        async def _run():
            wf = self._base_wf()
            self.orch._workflows["001"] = wf
            with patch.object(orch_mod, "ALLOW_AUTO_COMMIT", True), \
                 patch.object(self.orch, "approve_commit", return_value={"state": WorkflowState.COMPLETED.value}) as mock_commit:
                await self.orch._apply_review_decision(wf, "APPROVE")
                mock_commit.assert_awaited_once_with("001")

        asyncio.run(_run())

    def test_request_changes_increments_iteration_and_respawns(self):
        async def _run():
            wf = self._base_wf()
            self.orch._workflows["001"] = wf
            with patch.object(self.orch, "_spawn_executor", return_value=None) as mock_spawn:
                await self.orch._apply_review_decision(wf, "REQUEST_CHANGES")
            self.assertEqual(wf["review_iterations"], 1)
            self.assertEqual(wf["state"], WorkflowState.EXECUTOR_RUNNING.value)
            mock_spawn.assert_awaited_once()

        asyncio.run(_run())

    def test_request_changes_exhausted_fails(self):
        async def _run():
            wf = self._base_wf()
            wf["review_iterations"] = 3
            wf["review_policy"]["max_iterations"] = 3
            self.orch._workflows["001"] = wf
            await self.orch._apply_review_decision(wf, "REQUEST_CHANGES")
            self.assertEqual(wf["state"], WorkflowState.FAILED.value)
            self.assertIn("exhausted", wf.get("reason", "").lower())

        asyncio.run(_run())

    def test_auto_approve_council_skips_gate1(self):
        async def _run():
            with patch("export.load_targets", return_value=self.targets_config), \
                 patch("loop_orchestrator.load_targets", return_value=self.targets_config), \
                 patch("project_context.load_targets", return_value=self.targets_config), \
                 patch("export.get_conversations_dir", return_value=self.conv_dir):
                await self.orch.start_council(
                    prompt="auto approve test",
                    target_key="mock",
                    review_policy={
                        "mode": "AI",
                        "max_iterations": 1,
                        "allow_request_changes": False,
                        "require_pre_commit_approval": True,
                        "auto_approve_council": True,
                        "allow_l0_auto_approve": True,
                    },
                    mock=True,
                )
                task_num = None
                for _ in range(100):
                    await asyncio.sleep(0.15)
                    for w in self.orch.list_workflows():
                        if w.get("task_num"):
                            task_num = w["task_num"]
                            break
                    if task_num:
                        break
                self.assertEqual(task_num, "001")
                status = self.orch.get_status(task_num)
                self.assertTrue(
                    any("Auto-approving council" in e["line"] for e in status.get("logs", [])),
                    "Expected auto-approve log for gate #1 skip",
                )

        asyncio.run(_run())

    def test_resume_unfinished_review_monitor(self):
        async def _run():
            reviews_dir = os.path.join(self.target_path, "MAW_workflow", "REVIEWS")
            os.makedirs(reviews_dir, exist_ok=True)
            with open(os.path.join(reviews_dir, "review_001.md"), "w", encoding="utf-8") as f:
                f.write("# Review\nAPPROVE\n")

            wf = {
                "workflow_id": "wf-resume",
                "task_num": "001",
                "conversation_id": "conv-resume",
                "state": WorkflowState.REVIEW_RUNNING.value,
                "title": "Resume test",
                "target_key": "mock",
                "review_policy": {
                    "mode": "AI",
                    "max_iterations": 1,
                    "allow_request_changes": False,
                    "require_pre_commit_approval": True,
                },
                "review_iterations": 0,
                "logs": [],
                "export_result": {
                    "targetPath": self.target_path,
                    "workflowPath": os.path.join(self.target_path, "MAW_workflow"),
                    "targetKey": "mock",
                },
                "mock": True,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
            self.orch._workflows["001"] = wf
            self.orch._persist()

            resumed = LoopOrchestrator()
            await resumed.resume_unfinished()

            final_state = None
            for _ in range(80):
                await asyncio.sleep(0.2)
                status = resumed.get_status("001")
                final_state = status["state"]
                if final_state in (
                    WorkflowState.COMMIT_PENDING_APPROVAL.value,
                    WorkflowState.FAILED.value,
                ):
                    break
            self.assertEqual(final_state, WorkflowState.COMMIT_PENDING_APPROVAL.value)

        asyncio.run(_run())

    def test_human_review_complete_advances_to_commit_gate(self):
        async def _run():
            with patch("export.load_targets", return_value=self.targets_config), \
                 patch("loop_orchestrator.load_targets", return_value=self.targets_config), \
                 patch("project_context.load_targets", return_value=self.targets_config), \
                 patch("export.get_conversations_dir", return_value=self.conv_dir):
                await self.orch.start_council(
                    prompt="human review test",
                    target_key="mock",
                    review_policy={
                        "mode": "Human",
                        "max_iterations": 0,
                        "allow_request_changes": False,
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
                await self.orch.approve_council(conv_id)

                for _ in range(80):
                    await asyncio.sleep(0.2)
                    status = self.orch.get_status("001")
                    if status["state"] == WorkflowState.REVIEW_PENDING.value:
                        break
                else:
                    self.fail("Did not reach REVIEW_PENDING for human mode")

                result = await self.orch.complete_human_review("001", "APPROVE")
                self.assertEqual(result["state"], WorkflowState.COMMIT_PENDING_APPROVAL.value)

        asyncio.run(_run())

    def test_workflows_persist_and_reload(self):
        wf = self._base_wf()
        wf["state"] = WorkflowState.COMMIT_PENDING_APPROVAL.value
        self.orch._workflows["001"] = wf
        self.orch._persist()

        data = load_workflows()
        self.assertIn("001", data["workflows"])
        self.assertEqual(data["workflows"]["001"]["state"], WorkflowState.COMMIT_PENDING_APPROVAL.value)

        reloaded = LoopOrchestrator()
        self.assertEqual(
            reloaded.get_workflow_by_task("001")["state"],
            WorkflowState.COMMIT_PENDING_APPROVAL.value,
        )


if __name__ == "__main__":
    unittest.main()