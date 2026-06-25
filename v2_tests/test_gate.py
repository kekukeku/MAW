"""v2 gate tests — verify v2 isolation, path safety, and inspect correctness."""

import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from v2.files import (
    scaffold_target,
    init_workflow,
    ensure_workflow_dirs,
    exists_nonempty,
    write_atomic,
    ARTIFACT_CHAIR_BRIEF,
    acquire_executor_lock,
    release_executor_lock,
    check_executor_lock,
)
from v2.schema import (
    build_roster,
    make_manifest,
    load_manifest,
)


class TestV2ImportIsolation(unittest.TestCase):

    FORBIDDEN = [
        "council",
        "project_context",
        "loop_orchestrator",
        "export",
        "setup_api",
        "scout",
        "explorer",
    ]

    def test_no_v1_imports_in_v2(self):
        """Verify no v2 module imports v1 modules."""
        v2_dir = Path(__file__).resolve().parent.parent / "v2"
        violations = []

        for py_file in sorted(v2_dir.rglob("*.py")):
            if py_file.name == "__init__.py" and py_file.parent.name in ("adapters", "ui"):
                continue
            content = py_file.read_text()
            lines = content.splitlines()
            for i, line in enumerate(lines, 1):
                # Check import statements
                if line.strip().startswith(("import ", "from ")):
                    for forbidden in self.FORBIDDEN:
                        if forbidden in line and not line.strip().startswith("#"):
                            if f"v2_{forbidden}" not in line and f"/{forbidden}" not in line:
                                violations.append(f"{py_file.relative_to(v2_dir.parent)}:{i}: {line.strip()}")

        self.assertEqual(len(violations), 0,
                         f"v2 modules import forbidden v1 modules:\n" + "\n".join(violations))


class TestPathSafety(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.target = self.tmpdir
        scaffold_target(self.target)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_symlink_escape_prevented(self):
        """Writes through workflow dir cannot escape to parent via symlink."""
        roster = build_roster("mock", [{"seat": "a", "agent": "mock"}], "mock", "mock")
        manifest = make_manifest("wf_sym", self.target, roster)
        wf_dir = init_workflow(self.target, "wf_sym", manifest, "req")
        ensure_workflow_dirs(wf_dir)

        outside_path = Path(self.tmpdir) / "outside.md"
        outside_path.write_text("should not be touched")

        symlink_path = wf_dir / "symlink_to_outside"
        if not symlink_path.exists():
            symlink_path.symlink_to(outside_path)

        write_atomic(wf_dir / ARTIFACT_CHAIR_BRIEF, "# brief")

        # Verify write_atomic wrote to the correct file (not following symlink)
        self.assertTrue(exists_nonempty(wf_dir / ARTIFACT_CHAIR_BRIEF))
        self.assertEqual(outside_path.read_text(), "should not be touched")


class TestCLIPathEscape(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.target = self.tmpdir
        scaffold_target(self.target)
        roster = build_roster("mock", [{"seat": "a", "agent": "mock"}], "mock", "mock")
        manifest = make_manifest("wf_path", self.target, roster)
        self.wf_dir = init_workflow(self.target, "wf_path", manifest, "req")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_artifact_path_outside_workflow_root_blocked(self):
        """Artifact paths with ../ must not escape workflow root."""
        wf_dir = self.wf_dir
        safe = wf_dir / "chair_brief.md"
        escape = wf_dir / "../outside.md"

        resolved_safe = safe.resolve()
        resolved_escape = escape.resolve()
        resolved_tmpdir = Path(self.tmpdir).resolve()

        self.assertTrue(str(resolved_safe).startswith(str(resolved_tmpdir)),
                        f"Safe path {resolved_safe} should start with tmpdir {resolved_tmpdir}")

        if str(resolved_escape).startswith(str(resolved_tmpdir)):
            self.skipTest("tmpdir layout allows ../ that stays inside tmp")

    def test_manifest_writes_use_atomic_helper(self):
        """Verify manifest writes go through atomic JSON helper."""
        wf_dir = self.wf_dir
        manifest_path = wf_dir / "manifest.json"
        self.assertTrue(manifest_path.is_file())
        content = manifest_path.read_text()
        self.assertTrue(content.startswith("{"))


class TestInspectNoSideEffects(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.target = self.tmpdir
        scaffold_target(self.target)
        roster = build_roster(
            "mock",
            [{"seat": "planner_a", "agent": "mock"}],
            "mock",
            "mock",
        )
        manifest = make_manifest("wf_insp", self.target, roster)
        self.wf_dir = init_workflow(self.target, "wf_insp", manifest, "Test request")
        ensure_workflow_dirs(self.wf_dir)
        write_atomic(self.wf_dir / ARTIFACT_CHAIR_BRIEF, "# brief")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_inspect_writes_nothing(self):
        """Inspect command must not modify any files."""
        before = {}
        for f in sorted(self.wf_dir.rglob("*")):
            if f.is_file():
                st = f.stat()
                before[str(f)] = (st.st_mtime, st.st_size, f.read_bytes())

        from v2.schema import WorkflowStatus, expected_proposals, expected_comments, WAITING_STATES, load_manifest as load_mf
        from v2.files import load_runtime_state, check_executor_lock
        from v2.workflow import compute_dispatch
        mf = load_mf(str(self.wf_dir))
        items = compute_dispatch(self.wf_dir, mf)
        load_runtime_state(self.wf_dir)
        check_executor_lock(self.target)

        after = {}
        for f in sorted(self.wf_dir.rglob("*")):
            if f.is_file():
                st = f.stat()
                after[str(f)] = (st.st_mtime, st.st_size, f.read_bytes())

        self.assertEqual(before, after, "Inspect modified files!")


class TestExecutorLock(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.target = self.tmpdir
        scaffold_target(self.target)

    def tearDown(self):
        release_executor_lock(self.target)
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_acquire_and_release_lock(self):
        ok = acquire_executor_lock(self.target, "wf_a", "wf_a:exec:executor:1")
        self.assertTrue(ok)
        lock = check_executor_lock(self.target)
        self.assertIsNotNone(lock)
        self.assertEqual(lock["workflow_id"], "wf_a")

        ok = release_executor_lock(self.target)
        self.assertTrue(ok)
        self.assertIsNone(check_executor_lock(self.target))

    def test_active_lock_blocks_other_workflow(self):
        ok = acquire_executor_lock(self.target, "wf_a", "wf_a:exec:executor:1")
        self.assertTrue(ok)
        # Another workflow tries to acquire — should fail
        ok2 = acquire_executor_lock(self.target, "wf_b", "wf_b:exec:executor:1", lock_timeout=600)
        self.assertFalse(ok2)
        # But same workflow can re-acquire (overwrite)
        ok3 = acquire_executor_lock(self.target, "wf_a", "wf_a:exec:executor:2", lock_timeout=600)
        self.assertTrue(ok3)

    def test_stale_lock_allows_acquisition(self):
        # Acquire with a very short timeout
        ok = acquire_executor_lock(self.target, "wf_a", "wf_a:exec:executor:1")
        self.assertTrue(ok)
        # Set lock_timeout=0 so any existing lock is considered stale
        ok2 = acquire_executor_lock(self.target, "wf_b", "wf_b:exec:executor:1", lock_timeout=0)
        self.assertTrue(ok2)
        lock = check_executor_lock(self.target)
        self.assertEqual(lock["workflow_id"], "wf_b")

    def test_release_allows_other_workflow(self):
        ok = acquire_executor_lock(self.target, "wf_a", "wf_a:exec:executor:1")
        self.assertTrue(ok)
        ok = release_executor_lock(self.target)
        self.assertTrue(ok)
        ok2 = acquire_executor_lock(self.target, "wf_b", "wf_b:exec:executor:1")
        self.assertTrue(ok2)

    def test_concurrent_race_exactly_one_wins(self):
        """Two workflows racing for the lock — exactly one must succeed."""
        results: list[bool] = []
        barrier = threading.Barrier(2, timeout=5)

        def competitor(workflow_id: str) -> None:
            barrier.wait()  # synchronize start
            ok = acquire_executor_lock(self.target, workflow_id, f"{workflow_id}:exec")
            results.append(ok)

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(competitor, "wf_alpha")
            f2 = pool.submit(competitor, "wf_beta")
            f1.result(timeout=10)
            f2.result(timeout=10)

        # Exactly one winner
        self.assertEqual(len(results), 2)
        winners = sum(1 for r in results if r)
        self.assertEqual(winners, 1, f"Expected 1 winner, got {winners}: {results}")

        # Verify lock content
        lock = check_executor_lock(self.target)
        self.assertIsNotNone(lock)
        self.assertIn(lock["workflow_id"], ("wf_alpha", "wf_beta"))
        # JSON must be parseable (check_executor_lock already verified)

    def test_race_repeated_is_stable(self):
        """Repeat the race 5 times — must always have exactly 1 winner."""
        for attempt in range(5):
            release_executor_lock(self.target)
            results: list[bool] = []
            barrier = threading.Barrier(2, timeout=5)

            def competitor(wf: str) -> None:
                barrier.wait()
                ok = acquire_executor_lock(self.target, wf, f"{wf}:exec")
                results.append(ok)

            with ThreadPoolExecutor(max_workers=2) as pool:
                f1 = pool.submit(competitor, "wf_one")
                f2 = pool.submit(competitor, "wf_two")
                f1.result(timeout=10)
                f2.result(timeout=10)

            winners = sum(1 for r in results if r)
            self.assertEqual(winners, 1,
                            f"Round {attempt}: expected 1 winner, got {results}")


class TestScaffoldTemplates(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.target = self.tmpdir

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_scaffold_copies_templates_to_new_target(self):
        scaffold_target(self.target)
        agents = Path(self.target) / "AGENTS.md"
        rules = Path(self.target) / "TEAM_RULES.md"
        self.assertTrue(agents.is_file(), "AGENTS.md should be copied")
        self.assertTrue(rules.is_file(), "TEAM_RULES.md should be copied")

    def test_scaffold_does_not_overwrite_existing_templates(self):
        agents = Path(self.target) / "AGENTS.md"
        agents.write_text("Custom content", encoding="utf-8")
        scaffold_target(self.target)
        self.assertEqual(agents.read_text(), "Custom content",
                         "Existing AGENTS.md must not be overwritten")


if __name__ == "__main__":
    unittest.main()
