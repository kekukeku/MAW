import os
import json
import unittest
import asyncio
import tempfile
import shutil
from contextlib import ExitStack
from unittest.mock import patch

from loop_orchestrator import LoopOrchestrator, WorkflowState, parse_review_decision
import council.storage as storage_mod
import loop_orchestrator as orch_mod
from council.storage import load_conversation


class TestOrchestrator(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.template = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "template_target_project",
        )
        shutil.copytree(self.template, os.path.join(self.test_dir, "target"))

        self.conv_dir = tempfile.mkdtemp()
        self.wf_path = os.path.join(tempfile.mkdtemp(), "workflows.json")
        storage_mod.CONVERSATIONS_DIR = self.conv_dir
        orch_mod.WORKFLOWS_PATH = self.wf_path
        orch_mod.CONVERSATIONS_DIR = self.conv_dir

        self.orch = LoopOrchestrator()
        self.orch._workflows = {}

        self.targets = {
            "default": "test",
            "projects": {
                "test": {
                    "name": "Test Target",
                    "path": os.path.join(self.test_dir, "target"),
                }
            },
        }

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
        shutil.rmtree(self.conv_dir, ignore_errors=True)

    def _targets_patch(self):
        stack = ExitStack()
        stack.enter_context(patch("loop_orchestrator.load_targets", return_value=self.targets))
        stack.enter_context(patch("project_context.load_targets", return_value=self.targets))
        return stack

    def test_parse_decision(self):
        self.assertEqual(parse_review_decision("DECISION: APPROVE\n"), "APPROVE")
        self.assertEqual(parse_review_decision('{"decision": "REQUEST_CHANGES"}'), "REQUEST_CHANGES")
        self.assertEqual(parse_review_decision("REJECT"), "REJECT")
        self.assertEqual(parse_review_decision("DO NOT APPROVE this change"), "UNKNOWN")
        self.assertEqual(parse_review_decision("PRE-APPROVED WITH CHANGES"), "UNKNOWN")

    def test_state_transitions_mock_council(self):
        async def _run():
            with self._targets_patch(), patch("loop_orchestrator.export_to_target") as mock_export:
                mock_export.return_value = {
                    "taskNum": "001",
                    "targetPath": os.path.join(self.test_dir, "target"),
                    "workflowPath": os.path.join(self.test_dir, "target", "MAW_workflow"),
                    "targetKey": "test",
                }
                wf = await self.orch.start_council(
                    prompt="Test task",
                    target_key="test",
                    mock=True,
                )
                self.assertIn(wf["state"], [
                    WorkflowState.IDLE.value,
                    WorkflowState.COUNCIL_RUNNING.value,
                ])

                for _ in range(50):
                    await asyncio.sleep(0.1)
                    pending = [w for w in self.orch.list_workflows() if w.get("conversation_id")]
                    if pending and pending[0]["state"] == WorkflowState.COUNCIL_PENDING_APPROVAL.value:
                        conv_id = pending[0]["conversation_id"]
                        break
                else:
                    self.fail("Council did not complete in time")

                approved = await self.orch.approve_council(conv_id)
                self.assertEqual(approved["task_num"], "001")
                self.assertIn(approved["state"], [
                    WorkflowState.EXPORTED.value,
                    WorkflowState.EXECUTOR_RUNNING.value,
                ])

        asyncio.run(_run())

    def test_reject_council(self):
        async def _run():
            with self._targets_patch():
                await self.orch.start_council(prompt="Reject me", target_key="test", mock=True)
                for _ in range(50):
                    await asyncio.sleep(0.1)
                    for wf in self.orch.list_workflows():
                        if wf.get("conversation_id"):
                            result = await self.orch.reject_council(wf["conversation_id"])
                            self.assertEqual(result["state"], WorkflowState.FAILED.value)
                            return
                self.fail("Council did not complete")

        asyncio.run(_run())

    def test_request_changes_requires_commit_pending(self):
        async def _run():
            with self._targets_patch(), patch("loop_orchestrator.export_to_target") as mock_export:
                mock_export.return_value = {
                    "taskNum": "001",
                    "targetPath": os.path.join(self.test_dir, "target"),
                    "workflowPath": os.path.join(self.test_dir, "target", "MAW_workflow"),
                    "targetKey": "test",
                }
                await self.orch.start_council(prompt="x", target_key="test", mock=True)
                conv_id = None
                for _ in range(50):
                    await asyncio.sleep(0.1)
                    for w in self.orch.list_workflows():
                        if w.get("conversation_id"):
                            conv_id = w["conversation_id"]
                            break
                    if conv_id:
                        break
                await self.orch.approve_council(conv_id)
                with self.assertRaises(ValueError):
                    await self.orch.request_changes_at_commit("001")

        asyncio.run(_run())

    def test_reject_decision_fails_workflow(self):
        async def _run():
            with self._targets_patch(), \
                 patch("export.load_targets", return_value=self.targets), \
                 patch("export.get_conversations_dir", return_value=self.conv_dir), \
                 patch.dict(os.environ, {"MAW_MOCK_REVIEW_DECISION": "REJECT"}):
                await self.orch.start_council(
                    prompt="reject review",
                    target_key="test",
                    review_policy={"mode": "AI", "max_iterations": 1, "allow_request_changes": True, "require_pre_commit_approval": True},
                    mock=True,
                )
                conv_id = None
                for _ in range(50):
                    await asyncio.sleep(0.1)
                    for w in self.orch.list_workflows():
                        if w.get("conversation_id") and w["state"] == WorkflowState.COUNCIL_PENDING_APPROVAL.value:
                            conv_id = w["conversation_id"]
                            break
                    if conv_id:
                        break
                await self.orch.approve_council(conv_id)

                for _ in range(50):
                    await asyncio.sleep(0.2)
                    status = self.orch.get_status("001")
                    if status["state"] == WorkflowState.FAILED.value:
                        self.assertIn("reject", status.get("reason", "").lower())
                        return
                self.fail("Workflow did not fail on REJECT")

        asyncio.run(_run())

    def test_review_mode_none_skips_review(self):
        async def _run():
            with self._targets_patch(), \
                 patch("export.load_targets", return_value=self.targets), \
                 patch("export.get_conversations_dir", return_value=self.conv_dir):
                await self.orch.start_council(
                    prompt="skip review",
                    target_key="test",
                    review_policy={"mode": "None", "max_iterations": 0, "allow_request_changes": False, "require_pre_commit_approval": True},
                    mock=True,
                )
                conv_id = None
                for _ in range(50):
                    await asyncio.sleep(0.1)
                    for w in self.orch.list_workflows():
                        if w.get("conversation_id") and w["state"] == WorkflowState.COUNCIL_PENDING_APPROVAL.value:
                            conv_id = w["conversation_id"]
                            break
                    if conv_id:
                        break
                await self.orch.approve_council(conv_id)

                for _ in range(50):
                    await asyncio.sleep(0.2)
                    status = self.orch.get_status("001")
                    if status["state"] == WorkflowState.COMMIT_PENDING_APPROVAL.value:
                        self.assertEqual(status["pre_commit_report"]["review_decision"], "SKIPPED")
                        return
                self.fail("Did not reach COMMIT_PENDING_APPROVAL")

        asyncio.run(_run())

    def test_subprocess_timeout(self):
        async def _run():
            wf = {
                "task_num": "001",
                "state": WorkflowState.EXECUTOR_RUNNING.value,
                "logs": [],
                "export_result": {"targetPath": os.path.join(self.test_dir, "target")},
            }
            self.orch._workflows["001"] = wf
            with patch.object(orch_mod, "DEFAULT_EXECUTOR_TIMEOUT", 1):
                await self.orch._run_subprocess(
                    wf,
                    ["sleep", "5"],
                    cwd=self.test_dir,
                    timeout=1,
                    label="executor",
                )
            self.assertEqual(wf["state"], WorkflowState.FAILED.value)
            self.assertIn("timed out", wf.get("reason", ""))

        asyncio.run(_run())

    def test_context_pack_is_built_and_saved(self):
        async def _run():
            with self._targets_patch():
                wf = await self.orch.start_council(
                    prompt="Test task",
                    target_key="test",
                    mock=True,
                )
                self.assertIn(wf["state"], [
                    WorkflowState.IDLE.value,
                    WorkflowState.COUNCIL_RUNNING.value,
                ])

                for _ in range(50):
                    await asyncio.sleep(0.1)
                    pending = [w for w in self.orch.list_workflows() if w.get("conversation_id")]
                    if pending and pending[0]["state"] == WorkflowState.COUNCIL_PENDING_APPROVAL.value:
                        conv_id = pending[0]["conversation_id"]
                        break
                else:
                    self.fail("Council did not complete in time")

                conv = load_conversation(conv_id)
                self.assertIn("context_pack", conv)
                self.assertEqual(conv["context_pack"]["targetKey"], "test")
                self.assertEqual(conv["context_pack"]["summary"]["status"], "ready")

        asyncio.run(_run())

    def test_auto_approve_blocked_for_l0_only(self):
        async def _run():
            with self._targets_patch():
                await self.orch.start_council(
                    prompt="Test task",
                    target_key="test",
                    review_policy={
                        "mode": "AI",
                        "max_iterations": 3,
                        "allow_request_changes": True,
                        "require_pre_commit_approval": True,
                        "auto_approve_council": True,
                    },
                    mock=True,
                )

                conv_id = None
                for _ in range(50):
                    await asyncio.sleep(0.1)
                    for w in self.orch.list_workflows():
                        if w.get("conversation_id") and w["state"] == WorkflowState.COUNCIL_PENDING_APPROVAL.value:
                            conv_id = w["conversation_id"]
                            break
                    if conv_id:
                        break
                else:
                    self.fail("Council did not complete in time")

                # Workflow should be awaiting Gate #1, not already exported.
                wf = self.orch.get_workflow_by_conversation(conv_id)
                self.assertEqual(wf["state"], WorkflowState.COUNCIL_PENDING_APPROVAL.value)
                self.assertTrue(any("blocked" in log.get("line", "").lower() for log in wf.get("logs", [])))

        asyncio.run(_run())

    def test_auto_approve_allowed_with_l0_override(self):
        async def _run():
            with self._targets_patch(), patch("loop_orchestrator.export_to_target") as mock_export:
                mock_export.return_value = {
                    "taskNum": "001",
                    "targetPath": os.path.join(self.test_dir, "target"),
                    "workflowPath": os.path.join(self.test_dir, "target", "MAW_workflow"),
                    "targetKey": "test",
                }
                await self.orch.start_council(
                    prompt="Test task",
                    target_key="test",
                    review_policy={
                        "mode": "AI",
                        "max_iterations": 3,
                        "allow_request_changes": True,
                        "require_pre_commit_approval": True,
                        "auto_approve_council": True,
                        "allow_l0_auto_approve": True,
                    },
                    mock=True,
                )

                for _ in range(50):
                    await asyncio.sleep(0.1)
                    for w in self.orch.list_workflows():
                        if w.get("task_num"):
                            self.assertEqual(w["task_num"], "001")
                            return
                self.fail("Auto-approve did not export workflow")

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()