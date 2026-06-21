"""Tests for Panel 0 setup API helpers."""

import os
import json
import tempfile
import shutil
import unittest
import unittest.mock

import setup_api


class TestSetupAPI(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.workflow = os.path.join(self.tmp, "MAW_workflow")
        os.makedirs(os.path.join(self.workflow, "scripts"), exist_ok=True)
        os.makedirs(os.path.join(self.workflow, "agent-runner"), exist_ok=True)
        os.makedirs(os.path.join(self.workflow, "TASKS"), exist_ok=True)
        os.makedirs(os.path.join(self.workflow, "PLANNING"), exist_ok=True)
        os.makedirs(os.path.join(self.workflow, "REVIEWS"), exist_ok=True)
        with open(os.path.join(self.workflow, "AGENT_STATE.md"), "w") as f:
            f.write("| Task ID | State |\n| :--- | :--- |\n")
        with open(os.path.join(self.workflow, "scripts", "trigger_executor.py"), "w") as f:
            f.write("# ok")
        with open(os.path.join(self.workflow, "agent-runner", "trigger-review.js"), "w") as f:
            f.write("// ok")
        with open(os.path.join(self.workflow, "agent-runner", "route-review-decision.js"), "w") as f:
            f.write("// ok")
        with open(os.path.join(self.workflow, ".gitignore"), "w") as f:
            f.write("AGENT_STATE.md\nTASKS/\nPLANNING/\nREVIEWS/\n*.tmp\n.maw_export.lock\n")
        with open(os.path.join(self.tmp, ".gitignore"), "w") as f:
            f.write("MAW_workflow/\n")

        self.state_path = os.path.join(self.tmp, "setup_state.json")
        setup_api.SETUP_STATE_PATH = self.state_path

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_assess_health_green(self):
        health = setup_api.assess_health(self.tmp)
        self.assertEqual(health["lamp"], "green")
        self.assertTrue(health["valid"])

    def test_preflight_requires_llm_test(self):
        with open(self.state_path, "w") as f:
            json.dump({"llm_test_ok": False, "llm_provider": "litellm"}, f)

        env = {
            "LLM_PROVIDER": "litellm",
            "LITELLM_API_BASE": "http://localhost:4000",
            "MAW_MOCK_MODE": "0",
        }
        targets = {
            "default": "default",
            "projects": {
                "default": {
                    "path": self.tmp,
                    "agents": {"executor": "antigravity", "reviewer": "grok_build"},
                }
            },
        }
        with unittest.mock.patch("setup_api._read_env", return_value=env), \
             unittest.mock.patch("setup_api.load_targets", return_value=targets):
            result = setup_api.get_preflight(self.tmp)
        self.assertFalse(result["ready"])
        self.assertTrue(any("Test Connection" in i for i in result["issues"]))


if __name__ == "__main__":
    unittest.main()