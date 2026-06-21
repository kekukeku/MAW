"""Tests for agent adapter registry and installer."""

import os
import tempfile
import shutil
import unittest

from adapters.installer import (
    list_agents,
    get_agent,
    install_adapters,
    render_template,
    load_registry,
)


class TestAdapters(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.workflow = os.path.join(self.tmp, "MAW_workflow")
        os.makedirs(self.workflow, exist_ok=True)
        with open(os.path.join(self.tmp, ".gitignore"), "w") as f:
            f.write("MAW_workflow/\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_registry_has_six_gui_agents(self):
        agents = list_agents()
        self.assertEqual(len(agents), 6)
        ids = {a["id"] for a in agents}
        self.assertIn("openwork", ids)
        self.assertIn("grok_build", ids)
        self.assertIn("custom", ids)
        for agent in agents:
            self.assertNotEqual(agent.get("kind"), "cli")

    def test_no_cli_agents_in_registry(self):
        raw = load_registry()
        kinds = {a.get("kind") for a in raw.get("agents", [])}
        self.assertNotIn("cli", kinds)

    def test_render_template_substitutes_vars(self):
        content = render_template(
            "templates/reviewer/mock_route.js.tpl",
            {"AGENT_ID": "grok_build", "AGENT_LABEL": "Grok Build"},
        )
        self.assertIn("grok_build", content)

    def test_install_adapters_writes_scripts(self):
        result = install_adapters(self.tmp, "antigravity", "grok_build")
        executor = os.path.join(self.workflow, "scripts", "trigger_executor.py")
        reviewer = os.path.join(self.workflow, "agent-runner", "trigger-review.js")
        router = os.path.join(self.workflow, "agent-runner", "route-review-decision.js")
        self.assertTrue(os.path.isfile(executor))
        self.assertTrue(os.path.isfile(reviewer))
        self.assertTrue(os.path.isfile(router))
        with open(executor, "r", encoding="utf-8") as f:
            body = f.read()
        self.assertIn("antigravity", body)
        self.assertEqual(result["executor"], "antigravity")

    def test_get_agent_unknown(self):
        self.assertIsNone(get_agent("claude_code"))


if __name__ == "__main__":
    unittest.main()