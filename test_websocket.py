"""WebSocket log streaming tests."""

import os
import asyncio
import tempfile
import shutil
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import council.storage as storage_mod
import loop_orchestrator as orch_mod
import main as main_mod
from loop_orchestrator import LoopOrchestrator, WorkflowState
from main import app


class TestWebSocket(unittest.TestCase):

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

        self.orch = LoopOrchestrator()
        self.orch._workflows = {}
        orch_mod.orchestrator = self.orch
        main_mod.orchestrator = self.orch

        self.targets_config = {
            "default": "mock",
            "projects": {"mock": {"name": "Mock", "path": self.target_path}},
        }

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_websocket_streams_logs(self):
        async def _setup_workflow():
            with patch("export.load_targets", return_value=self.targets_config), \
                 patch("loop_orchestrator.load_targets", return_value=self.targets_config), \
                 patch("project_context.load_targets", return_value=self.targets_config), \
                 patch("export.get_conversations_dir", return_value=self.conv_dir):
                await self.orch.start_council(prompt="ws test", target_key="mock", mock=True)
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
                return "001"

        task_num = asyncio.run(_setup_workflow())
        status = self.orch.get_status(task_num)
        self.assertGreater(len(status.get("logs", [])), 0)

        client = TestClient(app)
        with client.websocket_connect(f"/ws/workflow/{task_num}") as ws:
            msg = ws.receive_json()
            self.assertEqual(msg["type"], "status")
            self.assertEqual(msg["workflow"]["task_num"], task_num)
            self.assertIn("logs", msg["workflow"])
            ws.close()

    def test_global_websocket_subscribe(self):
        async def _setup_workflow():
            with patch("export.load_targets", return_value=self.targets_config), \
                 patch("loop_orchestrator.load_targets", return_value=self.targets_config), \
                 patch("project_context.load_targets", return_value=self.targets_config), \
                 patch("export.get_conversations_dir", return_value=self.conv_dir):
                await self.orch.start_council(prompt="global ws test", target_key="mock", mock=True)
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
                return "001"

        task_num = asyncio.run(_setup_workflow())
        client = TestClient(app)
        with client.websocket_connect("/ws/maw") as ws:
            ws.send_json({"action": "subscribe", "task_num": task_num})
            msg = ws.receive_json()
            self.assertEqual(msg["type"], "status")
            self.assertEqual(msg["task_num"], task_num)
            self.assertEqual(msg["workflow"]["task_num"], task_num)
            self.assertIn("logs", msg["workflow"])

    def test_global_websocket_ping_pong(self):
        client = TestClient(app)
        with client.websocket_connect("/ws/maw") as ws:
            ws.send_json({"action": "ping"})
            msg = ws.receive_json()
            self.assertEqual(msg["type"], "pong")

    def test_global_alias_endpoint(self):
        client = TestClient(app)
        with client.websocket_connect("/ws/workflow/global") as ws:
            ws.send_json({"action": "ping"})
            msg = ws.receive_json()
            self.assertEqual(msg["type"], "pong")


if __name__ == "__main__":
    unittest.main()