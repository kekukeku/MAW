import os
import unittest
import json
import tempfile
import shutil
import time
from datetime import datetime
import re

# Import target functions to test
from export import (
    slugify_title, allocate_task_num, validate_target, acquire_export_lock,
    release_export_lock, append_registry_row_atomic, render_council_markdown,
    render_task_markdown, _derive_files_affected, _render_context_summary,
)
from maw_paths import WORKFLOW_DIR_NAME

class TestMAWExportAdapter(unittest.TestCase):

    def setUp(self):
        # Create a temporary directory for project simulation
        self.test_dir = tempfile.mkdtemp()
        
    def tearDown(self):
        # Remove the directory after test
        shutil.rmtree(self.test_dir)

    def test_slugify_title(self):
        """Verify deterministic slugify formatting."""
        # Simple English
        self.assertEqual(slugify_title("Implement MAW Council Export Adapter"), "implement-maw-council-export-adapter")
        # Unicode / Chinese
        self.assertEqual(slugify_title("新增五個頭像"), "council_synthesis")
        # Special characters & Collapse repeated dashes
        self.assertEqual(slugify_title("Fix bug #123!!!"), "fix-bug-123")
        # Truncate and strip trailing dash
        long_title = "a" * 100
        self.assertEqual(slugify_title(long_title), "a" * 48)
        # Empty title
        self.assertEqual(slugify_title(""), "council_synthesis")
        self.assertEqual(slugify_title(None), "council_synthesis")

    def _make_valid_target(self, base_dir):
        """Create a minimal MAW target project contract structure."""
        workflow = os.path.join(base_dir, WORKFLOW_DIR_NAME)
        for d in ("TASKS", "PLANNING", "REVIEWS", "scripts", "agent-runner"):
            os.makedirs(os.path.join(workflow, d), exist_ok=True)
        with open(os.path.join(workflow, "AGENT_STATE.md"), "w") as f:
            f.write("# Central Registry\n| Task ID | State | Linked PR |\n| :--- | :--- | :--- |\n")
        with open(os.path.join(workflow, "scripts", "trigger_executor.py"), "w") as f:
            f.write("# executor\n")
        with open(os.path.join(workflow, "agent-runner", "trigger-review.js"), "w") as f:
            f.write("// reviewer\n")
        with open(os.path.join(workflow, "agent-runner", "route-review-decision.js"), "w") as f:
            f.write("// router\n")
        with open(os.path.join(workflow, ".gitignore"), "w") as f:
            f.write("AGENT_STATE.md\nTASKS/\nPLANNING/\nREVIEWS/\n*.tmp\n.maw_export.lock\n")
        with open(os.path.join(base_dir, ".gitignore"), "w") as f:
            f.write(f"{WORKFLOW_DIR_NAME}/\n")

    def test_validate_target(self):
        """Verify project path validation requirements."""
        valid, issues = validate_target(os.path.join(self.test_dir, "nonexistent"))
        self.assertFalse(valid)
        self.assertTrue(len(issues) > 0)

        self._make_valid_target(self.test_dir)
        valid, issues = validate_target(self.test_dir)
        self.assertTrue(valid)
        self.assertEqual(len(issues), 0)

    def test_validate_target_missing_tmp_gitignore(self):
        """Reject targets missing *.tmp in workflow .gitignore."""
        self._make_valid_target(self.test_dir)
        workflow = os.path.join(self.test_dir, WORKFLOW_DIR_NAME)
        with open(os.path.join(workflow, ".gitignore"), "w") as f:
            f.write("AGENT_STATE.md\nTASKS/\nPLANNING/\nREVIEWS/\n.maw_export.lock\n")
        valid, issues = validate_target(self.test_dir)
        self.assertFalse(valid)
        self.assertTrue(any("*.tmp" in i for i in issues))

    def test_allocate_task_num(self):
        """Verify next task allocation scanner max + 1."""
        agent_state_path = os.path.join(self.test_dir, "AGENT_STATE.md")
        
        # Empty file
        with open(agent_state_path, "w") as f:
            f.write("")
        self.assertEqual(allocate_task_num(agent_state_path), "001")
        
        # Mock task table rows
        with open(agent_state_path, "w") as f:
            f.write("""
| Task ID | State | Linked PR |
| :--- | :--- | :--- |
| **TASK-001** | `MERGED` | [link](./TASKS/task_001.md) |
| **TASK-015** | `IN_PROGRESS` | [link](./TASKS/task_015.md) |
| **TASK-006** | `MERGED` | [link](./TASKS/task_006.md) |
            """)
        self.assertEqual(allocate_task_num(agent_state_path), "016")

    def test_lock_mechanism(self):
        """Verify file lock liveness checking and timeout reclaiming."""
        # Lock missing -> acquire successful
        workflow = os.path.join(self.test_dir, WORKFLOW_DIR_NAME)
        os.makedirs(workflow, exist_ok=True)
        acquired, err = acquire_export_lock(workflow, "test")
        self.assertTrue(acquired)
        self.assertIsNone(err)
        
        # Consecutive acquire with same PID (in python os.getpid() remains same, so it's live)
        # However, if PID is live, it should reject unless PID is dead or timed out
        # In our case, the current process PID is active, so trying to acquire lock again
        # from the same active PID: wait, the lock checks:
        # "if not is_pid_alive(lock_pid): reclaim else if older than 5 min: reclaim else: return 409"
        # Since os.getpid() IS alive, it should return 409!
        acquired, err = acquire_export_lock(workflow, "test")
        self.assertFalse(acquired)
        self.assertIn("locked by active process PID", err)

        release_export_lock(workflow)
        self.assertFalse(os.path.exists(os.path.join(workflow, ".maw_export.lock")))

    def test_append_registry_row_atomic(self):
        """Verify atomic insertion of task rows in central registry."""
        agent_state_path = os.path.join(self.test_dir, "AGENT_STATE.md")
        
        # Create base registry file
        with open(agent_state_path, "w") as f:
            f.write("""# Agent State
Some headers.

| Task ID | State | Linked PR / Branch | Last Updated |
| :--- | :--- | :--- | :--- |
| **TASK-001** | `MERGED` | [task/task_001_initialize_governance](./TASKS/task_001.md) | 2026-06-16 |
| **TASK-002** | `MERGED` | [task/task_002_provider_agnostic_agent_events](./TASKS/task_002.md) | 2026-06-16 |

---
## Footer
""")
            
        # Run append row
        date_str = "2026-06-20"
        append_registry_row_atomic(agent_state_path, "003", "test-slug", date_str)
        
        # Read back
        with open(agent_state_path, "r") as f:
            content = f.read()
            
        # Verify row exists in the right place
        self.assertIn("| **TASK-003** | `IN_PROGRESS` | [task/task_003_test-slug](./TASKS/task_003.md) | 2026-06-20 |", content)
        self.assertIn("## Footer", content)
        self.assertIn("| **TASK-002** | `MERGED` |", content)

    def test_render_council_markdown_includes_context(self):
        context_pack = {
            "version": 1,
            "targetKey": "test",
            "targetPath": "/tmp/test",
            "summary": {"status": "ready", "totalChars": 1000, "truncated": False, "includedFiles": 2, "excludedFiles": 5},
            "blueprint": {
                "tree": "test/\n└── src",
                "readme": "# Test",
                "dependencies": [{"path": "package.json", "source": "blueprint", "chars": 200, "truncated": False, "content": "{}"}],
            },
            "files": [],
            "accessIssues": [{"path": ".env", "reason": "excluded_secret:.env"}],
        }
        md = render_council_markdown(
            task_num="001",
            title="Test",
            date_str="2026-06-20",
            user_request="Do something",
            models_list={"council": ["m1"], "chairman": "c1"},
            stage1_text="stage1",
            stage2_text="stage2",
            aggregate_rankings_text="rank",
            stage3_text="stage3",
            export_options_text={"targetKey": "test", "filesAffected": "", "nonGoals": ""},
            context_pack=context_pack,
        )
        self.assertIn("## 3. Target Project Context", md)
        self.assertIn("package.json", md)
        self.assertIn(".env", md)
        self.assertIn("stage3", md)

    def test_derive_files_affected_with_user_input(self):
        context_pack = {
            "files": [{"path": "src/main.py"}],
            "blueprint": {"dependencies": []},
        }
        result = _derive_files_affected("src/custom.py", context_pack)
        self.assertEqual(result, "src/custom.py")

    def test_derive_files_affected_from_context_files(self):
        context_pack = {
            "files": [{"path": "src/main.py"}, {"path": "src/auth.py"}],
            "blueprint": {"dependencies": []},
        }
        result = _derive_files_affected("To be determined by executor after repository inspection", context_pack)
        self.assertIn("src/main.py", result)
        self.assertIn("src/auth.py", result)

    def test_derive_files_affected_l0_only(self):
        context_pack = {
            "files": [],
            "blueprint": {"dependencies": [{"path": "package.json"}]},
        }
        result = _derive_files_affected("To be determined by executor after repository inspection", context_pack)
        self.assertIn("project blueprint context", result)
        self.assertIn("package.json", result)

    def test_render_task_markdown_with_context(self):
        context_pack = {
            "files": [{"path": "src/main.py"}],
            "blueprint": {"dependencies": []},
        }
        md = render_task_markdown(
            task_num="001",
            title="Test",
            date_str="2026-06-20",
            slug="test",
            objective="Do it",
            files_affected="To be determined by executor after repository inspection",
            non_goals="None",
            conversation_id="conv-123",
            message_index=1,
            context_pack=context_pack,
        )
        self.assertIn("src/main.py", md)

if __name__ == "__main__":
    unittest.main()
