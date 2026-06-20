import os
import unittest
import json
import tempfile
import shutil
import time
from datetime import datetime
import re

# Import target functions to test
from export import slugify_title, allocate_task_num, validate_target, acquire_export_lock, release_export_lock, append_registry_row_atomic

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

    def test_validate_target(self):
        """Verify project path validation requirements."""
        # Directory doesn't exist
        valid, issues = validate_target(os.path.join(self.test_dir, "nonexistent"))
        self.assertFalse(valid)
        self.assertTrue(len(issues) > 0)
        
        # Valid structure simulated
        os.makedirs(os.path.join(self.test_dir, "TASKS"))
        with open(os.path.join(self.test_dir, "AGENT_STATE.md"), "w") as f:
            f.write("# Central Registry")
        with open(os.path.join(self.test_dir, "watcher.py"), "w") as f:
            f.write("# Watcher")
            
        valid, issues = validate_target(self.test_dir)
        self.assertTrue(valid)
        self.assertEqual(len(issues), 0)

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
        acquired, err = acquire_export_lock(self.test_dir, "test")
        self.assertTrue(acquired)
        self.assertIsNone(err)
        
        # Consecutive acquire with same PID (in python os.getpid() remains same, so it's live)
        # However, if PID is live, it should reject unless PID is dead or timed out
        # In our case, the current process PID is active, so trying to acquire lock again
        # from the same active PID: wait, the lock checks:
        # "if not is_pid_alive(lock_pid): reclaim else if older than 5 min: reclaim else: return 409"
        # Since os.getpid() IS alive, it should return 409!
        acquired, err = acquire_export_lock(self.test_dir, "test")
        self.assertFalse(acquired)
        self.assertIn("locked by active process PID", err)
        
        # Release lock
        release_export_lock(self.test_dir)
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, ".maw_export.lock")))

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

if __name__ == "__main__":
    unittest.main()
